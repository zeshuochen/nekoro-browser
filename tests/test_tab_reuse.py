"""test_tab_reuse.py — 标签复用提示（new_tab 的 existing / reuse）与 sweep_tabs / close_tabs。

fake 严格照真 wire 形状来：`list_tabs` 回 {tabs:[{tabId,url,title,active,attached}], grouped},
扩展 navigate action 回 {tabId, load:'complete'|'timeout'}，close_tab action 回 {closed}。
形状对不上的假对象会让绿测掩盖真 bug（历史上栽过两次）。
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 指向空目录：否则 site_notes 会把本机真实站点笔记塞进返回值，断言随机器而变。
os.environ["NEKORO_DOMAIN_SKILLS"] = tempfile.mkdtemp(prefix="nekoro-empty-skills-")

from nekoro_browser import helpers


class FakeBridge:
    def __init__(self, owner):
        self.owner = owner
        self.scripting = []

    async def send_scripting(self, params, timeout=30.0):
        self.scripting.append(params)
        action = params.get("action")
        if action == "navigate":
            # 带 target = 复用已有标签（扩展走 chrome.tabs.update(active:true)）；
            # 不带 target 才是 chrome.tabs.create 开新标签
            if params.get("target"):
                return {"navigated": params.get("url"), "tabId": params["target"],
                        "load": "complete"}
            self.owner.next_tab_id += 1
            return {"navigated": params.get("url"), "tabId": self.owner.next_tab_id,
                    "load": "complete"}
        if action == "close_tab":
            t = params["target"]
            if t in self.owner.unclosable:
                raise RuntimeError("No tab with given id")
            self.owner.tabs = [x for x in self.owner.tabs if x["tabId"] != t]
            return {"closed": t}
        raise AssertionError(f"unexpected action {action}")


class FakeDaemon:
    """够真的 daemon：托管标签清单 + switch/navigate/evaluate。

    `grouped=None` 复现**老版本扩展**的 wire：它的 list_tabs 响应压根没有这个键。"""

    def __init__(self, tabs, active=None, grouped=True, attach_fail=()):
        self.tabs = list(tabs)
        self.active_tab_id = active
        self.grouped = grouped
        self.attach_fail = set(attach_fail)     # 这些标签 attach 不上（如被 DevTools 占）
        self.unclosable = set()
        self.next_tab_id = 900
        self.bridge = FakeBridge(self)
        self.switched = []
        self.nav = []

    async def list_tabs(self):
        out = {"tabs": [dict(t) for t in self.tabs]}
        if self.grouped is not None:          # 老扩展：整个键都不存在
            out["grouped"] = self.grouped
        return out

    async def switch_tab(self, tab_id):
        if tab_id in self.attach_fail:
            return {"attached": False, "tabId": tab_id}
        self.switched.append(tab_id)
        self.active_tab_id = tab_id
        return {"attached": True, "tabId": tab_id}

    async def navigate(self, url):
        self.nav.append(url)
        return {"frameId": "1"}

    async def evaluate(self, code):
        return {"result": {"value": "complete"}}


def tab(tid, url, title="t", active=False, attached=True):
    return {"tabId": tid, "url": url, "title": title, "active": active, "attached": attached}


DUO = "https://www.duolingo.com/lesson"


def run(coro):
    return asyncio.run(coro)


# ── 判重键 ────────────────────────────────────────────────────────────────────

def test_tab_key():
    assert helpers._tab_key(DUO) == "www.duolingo.com/lesson"
    # 同 host 不同首段 → 不同键（/lesson 与 /practice 不算重复）
    assert helpers._tab_key("https://www.duolingo.com/practice") != helpers._tab_key(DUO)
    # 首段相同、后续不同仍算同一处
    assert helpers._tab_key("https://www.duolingo.com/lesson?x=1") == helpers._tab_key(DUO)
    # 没有 hostname 的一律不参与判重
    for u in ("about:blank", "", "chrome://newtab"):
        assert helpers._tab_key(u) == "", u


# ── new_tab 的 existing 提示 ──────────────────────────────────────────────────

def test_hint_lists_existing_same_site_tabs():
    d = FakeDaemon([tab(1, DUO), tab(2, DUO)], active=2)
    r = run(helpers.new_tab(d, DUO))
    assert r["ok"] and r["tabId"] == 901, r
    ex = r["existing"]
    assert [t["tabId"] for t in ex["tabs"]] == [1, 2], ex
    assert "switch_tab" in ex["hint"]
    assert "more" not in ex          # 只有两张，没有被截断


def test_hint_excludes_the_tab_just_opened():
    """新开的那张自己不能出现在「已有」清单里——fake 让它导航后即入列表。"""
    d = FakeDaemon([tab(1, DUO)], active=1)
    orig = d.bridge.send_scripting

    async def spy(params, timeout=30.0):
        res = await orig(params, timeout)
        if params.get("action") == "navigate":
            d.tabs.append(tab(res["tabId"], params["url"]))    # 扩展真会把新标签入组
        return res

    d.bridge.send_scripting = spy
    r = run(helpers.new_tab(d, DUO))
    assert [t["tabId"] for t in r["existing"]["tabs"]] == [1], r["existing"]


def test_hint_caps_at_three_and_reports_rest():
    d = FakeDaemon([tab(i, DUO) for i in range(1, 9)], active=8)     # 8 张（你机器上的真实场景）
    r = run(helpers.new_tab(d, DUO))
    ex = r["existing"]
    assert len(ex["tabs"]) == 3 and ex["more"] == 5, ex


def test_no_hint_when_nothing_matches():
    d = FakeDaemon([tab(1, "https://other.example/x"), tab(2, "about:blank")], active=1)
    r = run(helpers.new_tab(d, DUO))
    assert "existing" not in r, r


def test_hint_failure_never_breaks_new_tab():
    """list_tabs 挂了只是没提示，开标签本身必须照常成功。"""
    d = FakeDaemon([tab(1, DUO)], active=1)

    async def boom():
        raise RuntimeError("bridge down")

    d.list_tabs = boom
    r = run(helpers.new_tab(d, DUO))
    assert r["ok"] and r["tabId"] == 901 and "existing" not in r, r


# ── reuse=True ────────────────────────────────────────────────────────────────

def test_reuse_switches_instead_of_opening():
    d = FakeDaemon([tab(1, DUO), tab(2, DUO)], active=2)
    r = run(helpers.new_tab(d, DUO, reuse=True))
    assert r == {"ok": True, "tabId": 1, "reused": True, "loaded": True}, r
    assert d.switched == [1]
    # 复用必须走带 target 的扩展 navigate action：只有它会 chrome.tabs.update(active:true)
    # 把标签带到前台（托管组是 collapsed 建的，CDP Page.navigate 不会激活标签）
    assert d.bridge.scripting == [{"action": "navigate", "url": DUO, "target": 1}], \
        d.bridge.scripting
    assert d.nav == [], "复用不该走 CDP Page.navigate（那样标签留在折叠组里不可见）"


def test_reuse_falls_back_to_new_tab_when_attach_fails():
    """旧标签被 DevTools 占着 attach 不上 → 如实开新标签，不硬报错。"""
    d = FakeDaemon([tab(1, DUO)], active=1, attach_fail={1})
    r = run(helpers.new_tab(d, DUO, reuse=True))
    assert r["ok"] and r["tabId"] == 901 and "reused" not in r, r
    assert r["reuse"] == "no reusable tab", r
    assert d.bridge.scripting[-1] == {"action": "navigate", "url": DUO}


def test_reuse_without_candidates_opens_new():
    d = FakeDaemon([], active=None)
    r = run(helpers.new_tab(d, DUO, reuse=True))
    assert r["ok"] and r["tabId"] == 901 and "reused" not in r, r
    assert r["reuse"] == "no reusable tab", r


def test_reuse_reports_lookup_failure_instead_of_silently_opening():
    """查不成 ≠ 没有同站标签。桥抖一下就静默开新标签正是这个 feature 要消灭的东西。"""
    d = FakeDaemon([tab(1, DUO)], active=1)

    async def boom():
        raise RuntimeError("bridge down")

    d.list_tabs = boom
    r = run(helpers.new_tab(d, DUO, reuse=True))
    assert r["ok"] and r["tabId"] == 901 and "reused" not in r, r
    assert "lookup failed" in r["reuse"] and "bridge down" in r["reuse"], r


def test_reuse_falls_back_when_candidate_vanishes_mid_flight():
    """查到和导航之间用户手动关掉了那张 → 试下一张，都不成就开新的，别判死整个调用。"""
    d = FakeDaemon([tab(1, DUO), tab(2, DUO)], active=2)
    orig = d.bridge.send_scripting

    async def flaky(params, timeout=30.0):
        if params.get("target") == 1:          # 1 号已经没了
            raise RuntimeError("No tab with given id")
        return await orig(params, timeout)

    d.bridge.send_scripting = flaky
    r = run(helpers.new_tab(d, DUO, reuse=True))
    assert r["ok"] and r["tabId"] == 2 and r["reused"] is True, r


def test_reuse_reports_vanished_tab_when_nothing_else_left():
    d = FakeDaemon([tab(1, DUO)], active=1)
    orig = d.bridge.send_scripting

    async def flaky(params, timeout=30.0):
        if params.get("target") == 1:
            raise RuntimeError("No tab with given id")
        return await orig(params, timeout)

    d.bridge.send_scripting = flaky
    r = run(helpers.new_tab(d, DUO, reuse=True))
    assert r["ok"] and r["tabId"] == 901 and "reused" not in r, r
    assert "gone" in r["reuse"] and "No tab with given id" in r["reuse"], r


def test_reuse_is_keyword_only():
    """reuse 比 timeout 晚加进来，占位置会让老的 new_tab(url, 20) 静默变成 reuse=20。"""
    import inspect
    p = inspect.signature(helpers.new_tab).parameters
    assert p["reuse"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(p) == ["daemon", "url", "timeout", "reuse"], list(p)
    # 老调用形态：第三个位置参数仍然是 timeout，不该触发复用
    d = FakeDaemon([tab(1, DUO)], active=1)
    r = run(helpers.new_tab(d, DUO, 20))
    assert r["tabId"] == 901 and "reused" not in r, r


# ── sweep_tabs ────────────────────────────────────────────────────────────────

def test_sweep_dry_run_reports_without_closing():
    d = FakeDaemon([tab(1, DUO), tab(2, DUO), tab(3, DUO, active=True),
                    tab(4, "about:blank"), tab(5, "https://other.example/a")], active=3)
    r = run(helpers.sweep_tabs(d))
    assert r["ok"] and r["total"] == 5
    dup = [c for c in r["candidates"] if c["reason"] == "duplicate"]
    assert len(dup) == 1, r["candidates"]
    assert dup[0]["keep"] == 3, "活动标签是被保留的那张"
    assert dup[0]["close"] == [1, 2], dup
    assert [b["tabId"] for b in r["blank"]] == [4]
    assert "dry_run" in r["note"]
    assert d.bridge.scripting == [], "dry_run 绝不能关任何标签"


def test_sweep_never_touches_active_tab():
    d = FakeDaemon([tab(1, DUO), tab(2, DUO, active=True)], active=2)
    r = run(helpers.sweep_tabs(d))
    ids = [i for c in r["candidates"] for i in c["close"]]
    assert 2 not in ids and ids == [1], r


def test_sweep_single_tab_per_site_is_not_a_candidate():
    d = FakeDaemon([tab(1, DUO), tab(2, "https://other.example/a")], active=None)
    r = run(helpers.sweep_tabs(d))
    assert r["candidates"] == [] and r["blank"] == [], r


def test_sweep_execute_closes_duplicates_and_blanks():
    d = FakeDaemon([tab(1, DUO), tab(2, DUO), tab(3, DUO), tab(4, "about:blank")], active=None)
    r = run(helpers.sweep_tabs(d, dry_run=False))
    assert r["result"]["closed"] == [1, 2, 4], r["result"]
    assert [t["tabId"] for t in d.tabs] == [3], d.tabs


def test_sweep_refuses_to_close_without_managed_group():
    """没有托管组时清单里混着用户自己的标签 → 只报不关。"""
    d = FakeDaemon([tab(1, DUO), tab(2, DUO)], active=None, grouped=False)
    r = run(helpers.sweep_tabs(d, dry_run=False))
    assert r["ok"] and "拒绝真关" in r["note"], r
    assert d.bridge.scripting == [] and len(d.tabs) == 2


def test_sweep_refuses_when_extension_is_too_old_to_say():
    """老扩展不发 grouped：它同样会在没建组时列出所有窗口的标签，而 None 是最危险的
    未知——fail-closed，不能因为「没说不安全」就放行。"""
    d = FakeDaemon([tab(1, "https://mail.google.com/u/0"),
                    tab(2, "https://mail.google.com/u/1")], active=None, grouped=None)
    r = run(helpers.sweep_tabs(d, dry_run=False))
    assert r["ok"] and "拒绝真关" in r["note"] and "chrome://extensions" in r["note"], r
    assert d.bridge.scripting == [] and len(d.tabs) == 2, "一张用户自己的标签都不许关"


def test_sweep_puts_non_http_pages_in_other_and_never_closes_them():
    """file:/data:/chrome-extension: 也是 agent 自己开的工作页（scripts/smoke.py 就开
    data:text/html），不能因为「不是 http」就当游离标签扫掉。"""
    d = FakeDaemon([tab(1, "file:///E:/report.html"),
                    tab(2, "data:text/html,<input id=q>"),
                    tab(3, "chrome-extension://abc/viewer.html"),
                    tab(4, "about:blank"),
                    tab(5, "")], active=None)
    r = run(helpers.sweep_tabs(d, dry_run=False))
    assert [o["tabId"] for o in r["other"]] == [1, 2, 3], r["other"]
    assert [b["tabId"] for b in r["blank"]] == [4, 5], r["blank"]
    assert r["result"]["closed"] == [4, 5], r["result"]
    assert [t["tabId"] for t in d.tabs] == [1, 2, 3], "非 http 工作页必须原样留着"


def test_sweep_execute_keeps_active_tab():
    """真关路径此前只在 active=None 下跑过——有活动标签时它必须被保留。"""
    d = FakeDaemon([tab(1, DUO), tab(2, DUO, active=True), tab(3, DUO)], active=2)
    r = run(helpers.sweep_tabs(d, dry_run=False))
    assert r["result"]["closed"] == [1, 3], r["result"]
    assert [t["tabId"] for t in d.tabs] == [2], d.tabs


def test_sweep_protects_active_tab_reported_only_by_extension():
    """daemon 指针停在旧值/None 时，扩展 wire 上的 active 才是权威——两者取并集。"""
    d = FakeDaemon([tab(1, DUO, active=True), tab(2, DUO)], active=None)
    r = run(helpers.sweep_tabs(d))
    dup = r["candidates"][0]
    assert dup["keep"] == 1 and dup["close"] == [2], dup


def test_sweep_never_closes_any_active_id_when_sources_disagree():
    """daemon 指针和扩展说法分歧时，两个都得保住，不能保一个关一个。"""
    d = FakeDaemon([tab(1, DUO, active=True), tab(2, DUO), tab(3, DUO)], active=3)
    r = run(helpers.sweep_tabs(d))
    ids = [i for c in r["candidates"] for i in c["close"]]
    assert ids == [2], ids


def test_port_is_part_of_the_key():
    """localhost:3000（前端）和 localhost:8080（后端面板）不是同一处。"""
    assert helpers._tab_key("http://localhost:3000/app") != helpers._tab_key(
        "http://localhost:8080/app")
    # 默认端口写不写都是同一处
    assert helpers._tab_key("https://x.com:443/a") == helpers._tab_key("https://x.com/a")
    d = FakeDaemon([tab(1, "http://localhost:3000/app"),
                    tab(2, "http://localhost:8080/app")], active=None)
    r = run(helpers.sweep_tabs(d))
    assert r["candidates"] == [], "端口不同不算重复"
    r2 = run(helpers.new_tab(d, "http://localhost:8080/app", reuse=True))
    assert r2["tabId"] == 2 and r2["reused"] is True, r2


def test_sweep_list_tabs_error_is_reported():
    d = FakeDaemon([], active=None)

    async def boom():
        raise RuntimeError("bridge down")

    d.list_tabs = boom
    r = run(helpers.sweep_tabs(d))
    assert r["ok"] is False and "bridge down" in r["error"], r


# ── close_tabs ────────────────────────────────────────────────────────────────

def test_close_tabs_partial_failure():
    d = FakeDaemon([tab(1, DUO), tab(2, DUO), tab(3, DUO)], active=None)
    d.unclosable = {2}                       # 已被用户手动关掉
    r = run(helpers.close_tabs(d, [1, 2, 3]))
    assert r["ok"] is False, r
    assert r["closed"] == [1, 3] and r["failed"][0]["tabId"] == 2, r
    assert [t["tabId"] for t in d.tabs] == [2], "一张失败不该拖累其余"


def test_close_tabs_empty():
    d = FakeDaemon([], active=None)
    r = run(helpers.close_tabs(d, []))
    assert r == {"ok": True, "closed": [], "failed": []}, r


def test_list_tabs_passes_grouped_through():
    d = FakeDaemon([tab(1, DUO)], active=1)
    r = run(helpers.list_tabs(d))
    assert r["ok"] and r["grouped"] is True and r["active"] == 1, r


def test_registered():
    for name in ("sweep_tabs", "close_tabs", "new_tab"):
        assert name in helpers.list_helpers(), name


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
