"""test_click_loc.py — 统一 locator click()：语法解析、transient/permanent 错误分类。

fake 严格照真 wire 形状来：`evaluate` 回 {result:{value:...}}（CDP Runtime.evaluate 形状，
定位 JS 的返回值），`send_scripting` 回页内点击 op 的 {value:'clicked:…'|'clicked'|'not-found'|None}，
`send` 记录 Input.dispatchMouseEvent —— 现在只有 click_at_xy 还该产生它，
其余 click_* 一律走页内点击，所以「没有坐标事件」本身就是一条断言。
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 指向空目录：否则 site_notes 会把本机真实站点笔记塞进返回值，断言随机器而变。
os.environ["NEKORO_DOMAIN_SKILLS"] = tempfile.mkdtemp(prefix="nekoro-empty-skills-")

from nekoro_browser import helpers


class _FakeBridge:
    """真 daemon 的 bridge 是实例属性且带 send/send_scripting——用真实方法而非
    运行时挂动态属性，让 pyright 能静态解析（fake 严格照真 wire 形状来）。"""

    def __init__(self, owner):
        self.owner = owner

    async def send_scripting(self, params, timeout=30.0):
        return await self.owner._on_scripting(params)

    async def send(self, method, params, tab=None, **kw):
        return await self.owner._on_cdp(method, params, tab)


class FakeDaemon:
    def __init__(self, evaluate_value=None, script_value=None):
        self.evaluate_calls = []
        self.script_calls = []
        self.mouse_events = []
        self.cdp_tabs = []
        self.evaluate_value = evaluate_value
        self.script_value = script_value
        self.active_tab_id = 1          # _find_tab 用它，避免走 find_tab op
        self.bridge = _FakeBridge(self)

    async def evaluate(self, expr):
        self.evaluate_calls.append(expr)
        if callable(self.evaluate_value):
            return self.evaluate_value(expr)
        return {"result": {"value": self.evaluate_value}}

    async def _on_scripting(self, params):
        self.script_calls.append(params)
        if callable(self.script_value):
            return self.script_value(params)
        return {"value": self.script_value}

    async def _on_cdp(self, method, params, tab=None):
        self.cdp_tabs.append(tab)
        if method == "Runtime.evaluate":
            # 带 tab 的 click 走原始 CDP 而不是 daemon.evaluate，wire 形状同样是
            # {result:{value:...}}——fake 必须两条路都照真形状回
            self.evaluate_calls.append(params["expression"])
            if callable(self.evaluate_value):
                return self.evaluate_value(params["expression"])
            return {"result": {"value": self.evaluate_value}}
        self.mouse_events.append((method, params))


def make_fake(evaluate_value=None, script_value=None):
    return FakeDaemon(evaluate_value, script_value)


def run(coro):
    return asyncio.run(coro)


# 新 wire：定位 JS **就地派发**并回 clicked，不再把坐标交回 Python 走 CDP 坐标点击
# （坐标点击打的是屏幕位置而不是元素，被遮挡或窗口没前台时静默空点）。
OK = {"kind": "ok", "clicked": True}
OK_COVERED = {"kind": "ok", "clicked": True, "covered": True}


# ── _parse_loc ─────────────────────────────────────────────────────────────────

def test_parse_loc_forms():
    assert helpers._parse_loc("css:.btn") == ("css", ".btn", None)
    assert helpers._parse_loc(".btn") == ("css", ".btn", None)     # 裸 CSS 兜底
    assert helpers._parse_loc("text:登录") == ("text", "登录", None)
    assert helpers._parse_loc("text=登录") == ("text", "登录", None)
    assert helpers._parse_loc("index:3") == ("index", "3", None)
    assert helpers._parse_loc("xpath://button") == ("xpath", "//button", None)
    assert helpers._parse_loc("placeholder:关键词") == ("placeholder", "关键词", None)
    assert helpers._parse_loc("nth:2;css:.btn") == ("css", ".btn", 2)
    assert helpers._parse_loc("nth:0;xpath://a") == ("xpath", "//a", 0)
    assert helpers._parse_loc("") == ("css", "", None)


# ── css ────────────────────────────────────────────────────────────────────────

def test_click_css_single_match():
    d = make_fake(evaluate_value=OK)
    r = run(helpers.click(d, "css:.btn"))
    assert r == {"ok": True, "via": "in-page"}, r
    # 定位 JS 自己派发，绝不再回到 CDP 坐标点击那条路
    assert "dispatchEvent" in d.evaluate_calls[0], d.evaluate_calls[0]
    assert d.mouse_events == [], f"还在走坐标点击: {d.mouse_events}"
    # 定位表达式确实带了多匹配检测
    assert "ambiguous" in d.evaluate_calls[0]


def test_click_css_not_found_is_transient():
    d = make_fake(evaluate_value={"kind": "not-found"})
    r = run(helpers.click(d, "css:.missing"))
    assert r["ok"] is False and r["kind"] == "transient", r
    assert "not found" in r["error"]
    # 新实现里没有坐标事件，断 mouse_events 恒真=永远不会红。真正要守的是：
    # 定位 JS 必须在 not-found 时**提前 return**、走不到派发那几行。
    js = d.evaluate_calls[0]
    assert js.index("not-found") < js.index("dispatchEvent"), \
        "not-found 分支排在派发之后，等于没找到也会点"


def test_click_css_ambiguous_is_permanent():
    """多匹配是歧义不是没找到：重试无用，必须改定位。ego-lite 明确反对静默点第一个。"""
    d = make_fake(evaluate_value={"kind": "ambiguous", "count": 3})
    r = run(helpers.click(d, "css:.btn"))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert "matched 3 elements" in r["error"] and "nth:N" in r["error"], r
    assert d.evaluate_calls[0].index("ambiguous") < d.evaluate_calls[0].index("dispatchEvent"),         "歧义分支排在派发之后，等于多匹配也会点第一个"


def test_click_css_invalid_selector_is_permanent():
    d = make_fake(evaluate_value={
        "kind": "invalid", "error": "invalid selector: 'foo[' is not a valid selector"})
    r = run(helpers.click(d, "css:foo["))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert "invalid selector" in r["error"]


def test_click_css_nth_picks_the_asked_one():
    d = make_fake(evaluate_value=OK)
    r = run(helpers.click(d, "nth:2;css:.btn"))
    assert r["ok"] is True
    # nth 指定时不要求唯一，不注入多匹配检测
    assert "ambiguous" not in d.evaluate_calls[0]
    assert "vis[2]" in d.evaluate_calls[0]      # 下标落在「可见的那批」上


def test_click_bare_css_fallback():
    d = make_fake(evaluate_value=OK)
    r = run(helpers.click(d, ".legacy"))
    assert r["ok"] is True, r


# ── text / index（走扩展 op，与 click_text / click_index 行为一致）─────────

def test_click_text_via_op():
    d = make_fake(script_value="clicked:登录")
    r = run(helpers.click(d, "text:登录"))
    assert r["ok"] is True and r["via"] == "in-page", r
    assert d.script_calls[0]["op"] == "clickText", d.script_calls[0]
    assert d.script_calls[0]["arg"] == "登录"
    assert d.mouse_events == [], "text 定位也不该再走坐标点击"


def test_click_text_reports_covered():
    """**这条守的是我真踩过的坑。**

    给 clickText op 补 covered 之后它开始回 `clicked-covered:`，而 Python 侧
    click_text 当时只认 `clicked:` 前缀——于是目标被盖住时，明明点成功了却返回
    ok:false。症状是「假失败」：页面确实动了，返回值却说没成。

    独立复核指出：出事的正是这个函数，而 clicked-covered 的用例只覆盖了
    click_index 和 click_selector，唯独它没有。补上。
    """
    d = make_fake(script_value="clicked-covered:喜欢")
    r = run(helpers.click_text(d, "喜欢"))
    assert r["ok"] is True, f"点成功了却报失败: {r}"
    assert r["covered"] is True, r


def test_click_loc_text_branch_reports_covered():
    """click(loc) 的 text/index 分支同样要认双前缀——它是文档推荐的统一入口，
    这里回退的话面更广。"""
    d = make_fake(script_value="clicked-covered:登录")
    r = run(helpers.click(d, "text:登录"))
    assert r["ok"] is True, f"点成功了却报失败: {r}"
    assert r["covered"] is True, r


def test_click_loc_index_branch_reports_covered():
    d = make_fake(script_value="clicked-covered:3")
    r = run(helpers.click(d, "index:3"))
    assert r["ok"] is True and r["covered"] is True, r


def test_click_text_not_found_is_transient():
    d = make_fake(script_value=None)
    r = run(helpers.click(d, "text:不存在的东西"))
    assert r["ok"] is False and r["kind"] == "transient", r


def test_click_index_via_op():
    d = make_fake(script_value="clicked:3")
    r = run(helpers.click(d, "index:3"))
    assert r["ok"] is True and r["via"] == "in-page", r
    assert d.script_calls[0]["op"] == "clickIndex" and d.script_calls[0]["arg"] == 3
    assert d.mouse_events == [], "index 定位也不该再走坐标点击"


def test_click_index_not_a_number_is_permanent():
    d = make_fake()
    r = run(helpers.click(d, "index:abc"))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert "not a number" in r["error"]


def test_click_index_not_found_is_transient():
    d = make_fake(script_value=None)
    r = run(helpers.click(d, "index:99"))
    assert r["ok"] is False and r["kind"] == "transient", r


def test_click_nth_with_text_is_permanent_not_silently_ignored():
    """扩展 op 只回第一个匹配，兑现不了 nth。默默忽略 = 调用方以为点了第 2 个、
    实际点了第 1 个——正是 click() 要消灭的那类静默错误。"""
    d = make_fake(script_value="clicked:登录")
    r = run(helpers.click(d, "nth:2;text:登录"))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert "nth" in r["error"] and "text" in r["error"], r
    assert d.script_calls == [], "兑现不了就不能发出去让扩展点第一个"
    assert d.evaluate_calls == [], "也不能退回内联 JS 那条路"


def test_click_nth_with_index_is_permanent():
    d = make_fake(script_value="clicked:3")
    r = run(helpers.click(d, "nth:2;index:3"))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert d.script_calls == [], "兑现不了就不能发出去"


def test_click_exception_carries_transient_kind():
    """桥抖不该是唯一没有 kind 的失败分支。"""
    def boom(_expr):
        raise RuntimeError("bridge down")

    d = make_fake(evaluate_value=boom)
    r = run(helpers.click(d, "css:.btn"))
    assert r["ok"] is False and r["kind"] == "transient", r
    assert "bridge down" in r["error"]


# ── xpath / placeholder（内联 JS + 多匹配检测）──────────────────────────────

def test_click_xpath_ok():
    d = make_fake(evaluate_value=OK)
    r = run(helpers.click(d, "xpath://button[contains(.,'登录')]"))
    assert r["ok"] is True
    assert "document.evaluate" in d.evaluate_calls[0]


def test_click_xpath_ambiguous_is_permanent():
    d = make_fake(evaluate_value={"kind": "ambiguous", "count": 2})
    r = run(helpers.click(d, "xpath://button"))
    assert r["ok"] is False and r["kind"] == "permanent", r


def test_click_placeholder_ok():
    d = make_fake(evaluate_value=OK)
    r = run(helpers.click(d, "placeholder:关键词"))
    assert r["ok"] is True
    assert "querySelectorAll('input[placeholder]" in d.evaluate_calls[0]


def test_click_placeholder_ambiguous_is_permanent():
    d = make_fake(evaluate_value={"kind": "ambiguous", "count": 4})
    r = run(helpers.click(d, "placeholder:搜索"))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert d.evaluate_calls[0].index("ambiguous") < d.evaluate_calls[0].index("dispatchEvent")


def test_ambiguity_is_judged_on_visible_matches_only():
    """移动端+桌面端各留一份导航是常态：命中 2 个但只有 1 个可见，不该报歧义。
    JS 在浏览器里跑，单测能验的是「歧义判定挂在过滤后的集合上」这个不变量。"""
    for loc in ("css:.btn", "xpath://button", "placeholder:关键词"):
        d = make_fake(evaluate_value=OK)
        run(helpers.click(d, loc))
        js = d.evaluate_calls[0]
        assert "getComputedStyle(el).visibility !== 'hidden'" in js, (loc, js)
        assert "if (vis.length > 1) return {kind: 'ambiguous', count: vis.length};" in js, \
            f"{loc}: 歧义必须按可见集合判，不是按全部命中"
        assert "hits.length > 1" not in js, f"{loc}: 还在拿全部命中判歧义"


def test_locator_scrolls_into_view_before_dispatching():
    """元素在视口外时 rect 为空，派发等于点空——还会返回 ok。必须先滚、再量、再派发。

    锚点从「取坐标那行」换成「派发那行」：定位 JS 现在就地派发，不再返回坐标。
    """
    d = make_fake(evaluate_value=OK)
    run(helpers.click(d, "css:.btn"))
    js = d.evaluate_calls[0]
    assert "scrollIntoViewIfNeeded" in js, js
    # 锚到**测量**那一行：可见性过滤里也有一次 getBoundingClientRect，
    # 直接 index() 取到的是它，比 scroll 更靠前，断言会假红。
    measure = js.index("const r = el.getBoundingClientRect")
    assert js.index("scrollIntoViewIfNeeded") < measure, "必须先滚再量，反了等于没滚"
    assert measure < js.index("dispatchEvent"), "必须先量再派发"


def test_invalid_xpath_is_permanent_too():
    """非法 xpath 以前会把整个 evaluate 掀翻 → 笼统 transient；应与非法 css 一致。"""
    d = make_fake(evaluate_value={"kind": "invalid", "error": "invalid xpath: bad token"})
    r = run(helpers.click(d, "xpath://["))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert "catch (e) { return {kind: 'invalid'" in d.evaluate_calls[0]


def test_click_value_is_json_safe():
    """定位值里带引号/反斜杠不能炸掉生成的 JS。"""
    d = make_fake(script_value="clicked:登录", evaluate_value=OK)
    r = run(helpers.click(d, "text:say \"hi\""))
    assert r["ok"] is True, r
    r2 = run(helpers.click(d, "css:[data-x='a\\\\b']"))
    assert r2["ok"] is True, r2
    r3 = run(helpers.click(d, "xpath://a[@title='it\\'s']"))
    assert r3["ok"] is True, r3


# ── 注册 ───────────────────────────────────────────────────────────────────────

def test_registered():
    assert "click" in helpers.list_helpers()


# ── 批次 2/3：旧 click_* 判空修正 + 错误分类（借鉴 ego-lite 的 box-model 守卫）────

def test_click_at_xy_zero_x_is_clickable():
    """x==0 是合法坐标，不该被 `not x` 这类判空误判成没找到。

    这条原来挂在 click_selector / click_text 上——那时它们先取坐标再走 CDP 坐标点击。
    现在整条 click_* 家族都改走页内点击（坐标点击打的是屏幕位置而不是元素，被遮挡
    或窗口没前台时静默空点），**只剩 click_at_xy 还吃坐标**，所以守卫挪到它身上。
    留在原处的话就是一条永远不会红的用例：那两个函数根本不再看 x。
    """
    d = make_fake()
    r = run(helpers.click_at_xy(d, 0, 200))
    assert r["ok"] is True, r
    assert d.mouse_events and d.mouse_events[-1][1]["x"] == 0


def test_click_selector_uses_the_in_page_op():
    d = make_fake(script_value="clicked")
    r = run(helpers.click_selector(d, ".btn"))
    assert r["ok"] is True and r["via"] == "in-page", r
    assert d.script_calls[0]["op"] == "click", d.script_calls[0]
    assert d.mouse_events == [], "click_selector 不该再走坐标点击"


def test_click_selector_reports_covered():
    d = make_fake(script_value="clicked-covered")
    r = run(helpers.click_selector(d, ".btn"))
    assert r["ok"] is True and r["covered"] is True, r


def test_click_text_uses_the_in_page_op():
    d = make_fake(script_value="clicked:喜欢")
    r = run(helpers.click_text(d, "喜欢"))
    assert r["ok"] is True and r["via"] == "in-page", r
    assert d.script_calls[0]["op"] == "clickText", d.script_calls[0]
    assert d.mouse_events == [], "click_text 不该再走坐标点击"


def test_click_index_zero_is_clickable():
    """index 0 也是 falsy —— 别在哪一步 `if not index` 把第一个元素判成没找到。

    这条原来守的是「`x == 0` 被当成没找到」：那时 click_index 先取中心坐标再走
    CDP 坐标点击。现在它改走页内点击（坐标点击会静默空点：被遮挡、或窗口没前台时
    返回 ok:true 却什么都没发生），不再碰坐标，`x` 的 falsy 问题对它不复存在——
    但 index 本身的 falsy 风险还在，所以这条守住新 wire 形状继续测。
    `x == 0` 那条由 click_text / click_selector 的同名用例继续守着。
    """
    d = make_fake(script_value="clicked:0")
    r = run(helpers.click_index(d, 0))
    assert r["ok"] is True, r
    assert r["via"] == "in-page", r


def test_click_selector_not_found_has_kind():
    d = make_fake(script_value=None)
    r = run(helpers.click_selector(d, ".missing"))
    assert r["ok"] is False and r["kind"] == "transient", r


def test_click_text_not_found_has_kind():
    d = make_fake(script_value=None)
    r = run(helpers.click_text(d, "不存在"))
    assert r["ok"] is False and r["kind"] == "transient", r


def test_click_index_not_found_has_kind():
    d = make_fake(script_value=None)
    r = run(helpers.click_index(d, 99))
    assert r["ok"] is False and r["kind"] == "transient", r


def test_wait_selector_timeout_has_transient_kind():
    d = make_fake(script_value="timeout:.modal")
    r = run(helpers.wait_selector(d, ".modal"))
    assert r["ok"] is True and r["result"] == "timeout:.modal", r
    assert r["kind"] == "transient", r       # 超时=可能还没渲染完，可重试


def test_wait_selector_no_selector_is_permanent():
    d = make_fake(script_value="no-selector")
    r = run(helpers.wait_selector(d, ""))
    assert r["ok"] is True and r["result"] == "no-selector", r
    assert r["kind"] == "permanent", r       # 调用方没给选择器，重试无用


def test_wait_selector_visible_has_no_kind():
    """正常状态（visible）不是错误，不该带 kind 噪音。"""
    d = make_fake(script_value="visible")
    r = run(helpers.wait_selector(d, ".modal"))
    assert r["ok"] is True and "kind" not in r, r


# ── tab 路由（扩展侧按 msg.tabId 分发；真机验过跨标签点击）─────────────────

def test_click_with_tab_routes_locate_and_click_to_that_tab():
    """定位在 A、点击落 B 是最难查的一类错。

    改走页内点击之后，定位与派发在**同一次** Runtime.evaluate 里完成，这类错在结构上
    不再可能——所以这里守的是那个结构本身：只有一次 evaluate、带着 tab=77、且那段 JS
    里同时有定位和派发。原来断 `d.mouse_events`（点击落在 77）在新实现下恒假，
    留着就是条永远会红/永远无意义的用例。
    """
    d = make_fake(evaluate_value=OK)
    r = run(helpers.click(d, "css:.btn", tab=77))
    assert r == {"ok": True, "via": "in-page"}, r
    assert len(d.evaluate_calls) == 1, "定位与点击必须在同一次往返里"
    assert "dispatchEvent" in d.evaluate_calls[0], "那段 JS 里没有派发"
    assert set(d.cdp_tabs) == {77}, f"每条命令都得带 tab=77: {d.cdp_tabs}"
    assert d.mouse_events == [], "不该再有独立的坐标点击落到别的标签"


def test_click_without_tab_keeps_pointer_semantics():
    """不传 tab 就打活动指针——原有行为，绝不能被新参数改掉。"""
    d = make_fake(evaluate_value=OK)
    r = run(helpers.click(d, "css:.btn"))
    assert r == {"ok": True, "via": "in-page"}, r
    assert d.cdp_tabs == [None] * len(d.cdp_tabs), d.cdp_tabs


def test_click_text_with_tab_passes_target_to_extension_op():
    """text/index 走扩展 op，tab 要变成 op 的 target 字段。"""
    d = make_fake(script_value="clicked:登录")
    r = run(helpers.click(d, "text:登录", tab=88))
    assert r["ok"] is True and r["via"] == "in-page", r
    assert d.script_calls[0]["op"] == "clickText", d.script_calls[0]
    assert d.script_calls[0]["target"] == 88, d.script_calls[0]


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
