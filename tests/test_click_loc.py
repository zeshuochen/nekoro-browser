"""test_click_loc.py — 统一 locator click()：语法解析、transient/permanent 错误分类。

fake 严格照真 wire 形状来：`evaluate` 回 {result:{value:...}}（CDP Runtime.evaluate 形状，
定位 JS 的返回值），`send_scripting` 回 getRectByText/getRectByIndex 的 {value:{x,y}|null}，
`send` 记录 Input.dispatchMouseEvent（click_at_xy 的落点）。
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

    async def send(self, method, params):
        return await self.owner._on_cdp(method, params)


class FakeDaemon:
    def __init__(self, evaluate_value=None, script_value=None):
        self.evaluate_calls = []
        self.script_calls = []
        self.mouse_events = []
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

    async def _on_cdp(self, method, params):
        self.mouse_events.append((method, params))


def make_fake(evaluate_value=None, script_value=None):
    return FakeDaemon(evaluate_value, script_value)


def run(coro):
    return asyncio.run(coro)


OK = {"kind": "ok", "x": 100, "y": 200}


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
    assert r == {"ok": True}, r
    assert d.mouse_events and d.mouse_events[0][0] == "Input.dispatchMouseEvent"
    assert d.mouse_events[-1][1]["x"] == 100 and d.mouse_events[-1][1]["y"] == 200
    # 定位表达式确实带了多匹配检测
    assert "ambiguous" in d.evaluate_calls[0]


def test_click_css_not_found_is_transient():
    d = make_fake(evaluate_value={"kind": "not-found"})
    r = run(helpers.click(d, "css:.missing"))
    assert r["ok"] is False and r["kind"] == "transient", r
    assert "not found" in r["error"]
    assert d.mouse_events == [], "没找到绝不能点"


def test_click_css_ambiguous_is_permanent():
    """多匹配是歧义不是没找到：重试无用，必须改定位。ego-lite 明确反对静默点第一个。"""
    d = make_fake(evaluate_value={"kind": "ambiguous", "count": 3})
    r = run(helpers.click(d, "css:.btn"))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert "matched 3 elements" in r["error"] and "nth:N" in r["error"], r
    assert d.mouse_events == []


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
    d = make_fake(script_value={"x": 55, "y": 66})
    r = run(helpers.click(d, "text:登录"))
    assert r["ok"] is True
    assert d.script_calls[0]["op"] == "getRectByText"
    assert d.script_calls[0]["arg"] == "登录"
    assert d.mouse_events[-1][1]["x"] == 55


def test_click_text_not_found_is_transient():
    d = make_fake(script_value=None)
    r = run(helpers.click(d, "text:不存在的东西"))
    assert r["ok"] is False and r["kind"] == "transient", r


def test_click_index_via_op():
    d = make_fake(script_value={"x": 11, "y": 22})
    r = run(helpers.click(d, "index:3"))
    assert r["ok"] is True
    assert d.script_calls[0]["op"] == "getRectByIndex" and d.script_calls[0]["arg"] == 3


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
    d = make_fake(script_value={"x": 1, "y": 2})
    r = run(helpers.click(d, "nth:2;text:登录"))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert "nth" in r["error"] and "text" in r["error"], r
    assert d.mouse_events == [], "兑现不了就不能点"
    assert d.script_calls == [], "更不能发出去让扩展点第一个"


def test_click_nth_with_index_is_permanent():
    d = make_fake(script_value={"x": 1, "y": 2})
    r = run(helpers.click(d, "nth:2;index:3"))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert d.mouse_events == []


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
    assert d.mouse_events == []


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


def test_locator_scrolls_into_view_before_taking_coords():
    """box/rect 是视口坐标：元素在视口外还照着点就是点空——而且会返回 ok。"""
    d = make_fake(evaluate_value=OK)
    run(helpers.click(d, "css:.btn"))
    js = d.evaluate_calls[0]
    assert "scrollIntoViewIfNeeded" in js, js
    assert js.index("scrollIntoViewIfNeeded") < js.index("getBoundingClientRect();\n"
                                                        "  return {kind: 'ok'"), \
        "必须先滚再取坐标，反了等于没滚"


def test_invalid_xpath_is_permanent_too():
    """非法 xpath 以前会把整个 evaluate 掀翻 → 笼统 transient；应与非法 css 一致。"""
    d = make_fake(evaluate_value={"kind": "invalid", "error": "invalid xpath: bad token"})
    r = run(helpers.click(d, "xpath://["))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert "catch (e) { return {kind: 'invalid'" in d.evaluate_calls[0]


def test_click_value_is_json_safe():
    """定位值里带引号/反斜杠不能炸掉生成的 JS。"""
    d = make_fake(script_value={"x": 1, "y": 2}, evaluate_value=OK)
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

def test_click_selector_zero_x_is_clickable():
    """x==0 是合法坐标，不该被 `not rect.get('x')` 误判为未找到。"""
    d = make_fake(script_value={"x": 0, "y": 200})
    r = run(helpers.click_selector(d, ".btn"))
    assert r["ok"] is True, r
    assert d.mouse_events and d.mouse_events[-1][1]["x"] == 0


def test_click_text_zero_x_is_clickable():
    d = make_fake(script_value={"x": 0, "y": 200})
    r = run(helpers.click_text(d, "喜欢"))
    assert r["ok"] is True, r


def test_click_index_zero_x_is_clickable():
    d = make_fake(script_value={"x": 0, "y": 200})
    r = run(helpers.click_index(d, 0))
    assert r["ok"] is True, r


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


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
