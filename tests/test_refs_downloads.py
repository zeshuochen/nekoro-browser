"""test_refs_downloads.py — refs()/click_ref()（backendNodeId 稳定句柄）与 wait_for_download()。

fake 严格照真 wire 形状来：
- bridge.send(method, params) 分发 CDP 命令（DOM.* / Runtime.* / Input.*），按 method 返回假数据
- drain_events() 返回预设的事件序列（Page.downloadWillBegin/Progress）
"""
import asyncio
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 指向空目录：否则 site_notes 会把本机真实站点笔记塞进返回值，断言随机器而变。
os.environ["NEKORO_DOMAIN_SKILLS"] = tempfile.mkdtemp(prefix="nekoro-empty-skills-")

from nekoro_browser import helpers


class FakeDaemon:
    def __init__(self, nodes=None, box_content=None, download_events=None,
                 box_error=None):
        """nodes: [{nodeId, backendNodeId, nodeName, text}] 供 refs() 查询。
        box_content: click_ref 的 getBoxModel content（8 点）。box_error: getBoxModel 抛异常。"""
        self.nodes = nodes or []
        self.box_content = box_content if box_content is not None else \
            [10, 20, 110, 20, 110, 70, 10, 70]
        self.box_error = box_error
        self.download_events = download_events or []
        self.calls = []
        self.mouse_events = []
        self.active_tab_id = 1
        self.evaluate_calls = []
        self.scrolled = []
        self.called_on = []
        # 注解不能省：不写的话 pyright 会把它们推成 None / dict[str, bool]，
        # 用例里改成字符串或加 reason 键就报类型错（CI 的 pyright 是门禁）。
        self.resolve_error: str | None = None
        self.click_result: dict[str, object] = {"clicked": True, "covered": False}
        self.texts_override: list[str] | None = None   # None = 照 nodes 的 text 回

    async def evaluate(self, expr, tab=None):
        # refs() 的文本走一条 Runtime.evaluate；真 daemon 带 returnByValue=True，
        # 所以拿到的是真数组而不是 objectId
        self.evaluate_calls.append(expr)
        if self.texts_override is not None:
            return {"result": {"value": list(self.texts_override)}}
        # 照浏览器的实际行为来：JS 里也有 .slice(0, N)，条数必须和 nodeIds 对齐，
        # 否则「条数对不上就不给文本」的守卫会被 fake 的偷懒假象盖掉
        m = re.search(r"slice\(0, (\d+)\)", expr)
        n = int(m.group(1)) if m else len(self.nodes)
        return {"result": {"value": [x.get("text", "") for x in self.nodes[:n]]}}

    async def drain_events(self):
        evts = self.download_events
        self.download_events = []          # 消费式，和真 daemon 一致
        return evts

    async def _send(self, method, params, tab=None, **kw):
        self.calls.append((method, params))
        if method == "DOM.enable":
            return {}
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelectorAll":
            return {"nodeIds": [n["nodeId"] for n in self.nodes]}
        if method == "DOM.describeNode":
            for n in self.nodes:
                if n["nodeId"] == params.get("nodeId"):
                    return {"node": {"backendNodeId": n["backendNodeId"],
                                     "nodeName": n["nodeName"]}}
            return {"node": {}}
        # DOM.resolveNode / Runtime.callFunctionOn 是 **click_ref 单个元素**的正当用法
        # （改走页内点击后靠它把 backendNodeId 解析成 JS 对象再就地派发）。
        # refs() 一旦退回「逐个元素 resolve + callFunctionOn」——3×N 次串行往返 +
        # 不释放的 RemoteObject——由 test_refs_round_trips_stay_constant 里那两条
        # 「not in kinds」断言拦住，那才是这条守卫真正的作用域。
        if method == "DOM.resolveNode":
            if self.resolve_error:
                raise RuntimeError(self.resolve_error)
            return {"object": {"objectId": f"obj-{params.get('backendNodeId')}"}}
        if method == "Runtime.callFunctionOn":
            self.called_on.append(params.get("objectId"))
            return {"result": {"value": dict(self.click_result)}}
        if method == "DOM.scrollIntoViewIfNeeded":
            self.scrolled.append(params.get("backendNodeId"))
            return {}
        if method == "DOM.getBoxModel":
            if self.box_error:
                raise RuntimeError(self.box_error)
            return {"model": {"content": list(self.box_content)}}
        if method == "Input.dispatchMouseEvent":
            self.mouse_events.append(params)
            return {}
        raise AssertionError(f"unexpected CDP method {method}")

    async def _send_scripting(self, params, timeout=30.0):
        raise AssertionError(f"unexpected scripting {params}")

    @property
    def bridge(self):
        return type("B", (), {"send": self._send, "send_scripting": self._send_scripting})()


def run(coro):
    return asyncio.run(coro)


def node(nid, bkid, name, text=""):
    return {"nodeId": nid, "backendNodeId": bkid, "nodeName": name, "text": text}


# ── refs() ────────────────────────────────────────────────────────────────────

def test_refs_returns_stable_handles():
    d = FakeDaemon(nodes=[node(1, 101, "BUTTON", "登录"), node(2, 102, "A", "文档")])
    r = run(helpers.refs(d))
    assert r["ok"] is True, r
    assert r["result"] == [{"ref": 101, "tag": "button", "text": "登录"},
                           {"ref": 102, "tag": "a", "text": "文档"}], r


def test_refs_skips_nodes_without_backend_node_id():
    d = FakeDaemon(nodes=[node(1, None, "DIV"), node(2, 102, "A", "x")])
    r = run(helpers.refs(d))
    assert r["ok"] is True and r["result"] == [{"ref": 102, "tag": "a", "text": "x"}], r


def test_refs_respects_max_items():
    d = FakeDaemon(nodes=[node(i, 100 + i, "BUTTON", f"b{i}") for i in range(1, 8)])
    r = run(helpers.refs(d, max_items=3))
    assert r["ok"] is True and len(r["result"]) == 3, r


def test_refs_custom_selector_passed_through():
    d = FakeDaemon(nodes=[node(1, 101, "BUTTON", "x")])
    run(helpers.refs(d, selector="button.primary"))
    assert any(m == "DOM.querySelectorAll" and p["selector"] == "button.primary"
               for m, p in d.calls), d.calls


class BoomDaemon(FakeDaemon):
    """桥直接抛。用子类覆盖而不是往实例上挂函数——后者能跑通纯属绑定规则的巧合
    （普通函数塞进 bridge 的类字典会变成绑定方法多吃一个 self，绑定方法则不会），
    签名对不上却照样绿，是最不该出现在 fake 里的东西。"""

    async def _send(self, method, params, tab=None, **kw):
        raise RuntimeError("bridge down")


def test_refs_error_surfaces():
    d = BoomDaemon(nodes=[node(1, 101, "BUTTON")])
    r = run(helpers.refs(d))
    assert r["ok"] is False and "bridge down" in r["error"], r
    assert r["kind"] == "transient", r      # 桥抖可重试，别让异常分支成为唯一没 kind 的


def test_refs_round_trips_stay_constant():
    """逐元素 describe→resolve→callFunctionOn 是 3×N 次串行往返，50 个元素 150 次。
    这里钉死：往返数不随元素数线性涨（describeNode 并发 + 文本一条 evaluate）。"""
    d = FakeDaemon(nodes=[node(i, 100 + i, "BUTTON", f"b{i}") for i in range(50)])
    r = run(helpers.refs(d))
    assert r["ok"] is True and len(r["result"]) == 50
    kinds = [m for m, _ in d.calls]
    assert kinds.count("DOM.describeNode") == 50            # 并发，不是串行
    assert "DOM.resolveNode" not in kinds, "又退回逐个 resolve 了"
    assert "Runtime.callFunctionOn" not in kinds, "RemoteObject 不释放会钉住游离 DOM"
    assert len(d.evaluate_calls) == 1, "文本只该走一条 Runtime.evaluate"
    assert len(d.calls) - 50 <= 4, f"非并发往返太多: {kinds}"


def test_refs_drops_text_when_counts_disagree():
    """两次查询之间 DOM 变了 → 条数对不上。宁可不给文本，也不能给错位的文本
    （把「登录」标到「注销」按钮上，比没有文本危险得多）。"""
    d = FakeDaemon(nodes=[node(1, 101, "BUTTON", "登录"), node(2, 102, "A", "文档")])
    d.texts_override = ["登录"]            # JS 侧只看到 1 个，DOM 侧 2 个
    r = run(helpers.refs(d))
    assert [x["ref"] for x in r["result"]] == [101, 102], r
    assert all(x["text"] == "" for x in r["result"]), r


# ── click_ref() ───────────────────────────────────────────────────────────────

def test_click_ref_dispatches_in_page_not_by_coordinates():
    """坐标点击打的是屏幕位置而不是元素：被遮挡或窗口没前台时返回 ok:true 而页面
    纹丝不动。改成把 backendNodeId 解析成 JS 对象后就地派发，句柄稳定性照旧。"""
    d = FakeDaemon(nodes=[])
    r = run(helpers.click_ref(d, 101))
    assert r["ok"] is True and r["via"] == "in-page", r
    assert d.called_on == ["obj-101"], d.called_on
    assert d.mouse_events == [], f"不该再有坐标点击: {d.mouse_events}"


def test_click_ref_reports_covered():
    d = FakeDaemon(nodes=[])
    d.click_result = {"clicked": True, "covered": True}
    r = run(helpers.click_ref(d, 101))
    assert r["ok"] is True and r["covered"] is True, r


def test_click_ref_stale_ref_is_transient():
    d = FakeDaemon(nodes=[])
    d.resolve_error = "Could not find node with given id"
    r = run(helpers.click_ref(d, 999))
    assert r["ok"] is False and r["kind"] == "transient", r
    assert "no longer valid" in r["error"]
    assert d.called_on == [], "失效句柄绝不能派发"


def test_click_ref_scrolls_into_view_first():
    """box model 是视口坐标：元素在视口外还照着点就是点空，还会返回 ok:True。"""
    d = FakeDaemon(nodes=[])
    r = run(helpers.click_ref(d, 101))
    assert r["ok"] is True, r
    assert d.scrolled == [101], d.calls
    kinds = [m for m, _ in d.calls]
    assert kinds.index("DOM.scrollIntoViewIfNeeded") < kinds.index("Runtime.callFunctionOn"), \
        "必须先滚再派发，反了等于没滚"


def test_click_ref_survives_scroll_failure():
    """滚不动不算失败——元素可能本来就在视口里，或页面根本不可滚。"""
    class NoScroll(FakeDaemon):
        async def _send(self, method, params, tab=None, **kw):
            if method == "DOM.scrollIntoViewIfNeeded":
                raise RuntimeError("Node does not have a layout object")
            return await super()._send(method, params)

    d = NoScroll(nodes=[])
    r = run(helpers.click_ref(d, 101))
    assert r["ok"] is True, r
    assert d.called_on == ["obj-101"], "滚动失败不该拦住点击"


def test_click_ref_unrendered_element_is_transient():
    """元素没有 box（未渲染）时页内 JS 回 clicked:false —— 必须转成 transient 失败，
    不能报成功。原来靠 getBoxModel 点数不足判断，现在判据挪到了 JS 的返回值上。"""
    d = FakeDaemon(nodes=[])
    d.click_result = {"clicked": False, "reason": "no box"}
    r = run(helpers.click_ref(d, 101))
    assert r["ok"] is False and r["kind"] == "transient", r
    assert "no box" in r["error"], r


# ── wait_for_download() ───────────────────────────────────────────────────────

DL_BEGIN = {"method": "Page.downloadWillBegin",
            "params": {"url": "https://x/f.zip", "suggestedFilename": "f.zip"}}
DL_PROG = {"method": "Page.downloadProgress", "params": {"state": "inProgress"}}
DL_DONE = {"method": "Page.downloadProgress",
           "params": {"state": "completed", "totalBytes": 12345}}
DL_CANCEL = {"method": "Page.downloadProgress", "params": {"state": "canceled"}}


def test_wait_for_download_completed():
    d = FakeDaemon(download_events=[DL_BEGIN, DL_PROG, DL_DONE])
    r = run(helpers.wait_for_download(d, timeout=5))
    assert r["ok"] is True, r
    assert r["filename"] == "f.zip" and r["url"] == "https://x/f.zip"
    assert r["bytes"] == 12345, r


def test_wait_for_download_canceled_is_permanent():
    d = FakeDaemon(download_events=[DL_BEGIN, DL_CANCEL])
    r = run(helpers.wait_for_download(d, timeout=5))
    assert r["ok"] is False and r["kind"] == "permanent", r
    assert "canceled" in r["error"]


def test_wait_for_download_timeout_no_events_is_transient():
    d = FakeDaemon(download_events=[])
    r = run(helpers.wait_for_download(d, timeout=0.2))
    assert r["ok"] is False and r["kind"] == "transient", r
    assert "no download detected" in r["error"]


def test_wait_for_download_timeout_after_begin_keeps_filename():
    d = FakeDaemon(download_events=[DL_BEGIN])     # 开始但没完成
    r = run(helpers.wait_for_download(d, timeout=0.2))
    assert r["ok"] is False and r["kind"] == "transient", r
    assert r["filename"] == "f.zip", r


def test_wait_for_download_events_after_call():
    """事件在 daemon 缓冲里，helper 调用后到达的事件也能等到（消费式 drain）。"""
    d = FakeDaemon(download_events=[])

    async def late_events():
        await asyncio.sleep(0.1)
        d.download_events = [DL_BEGIN, DL_DONE]

    async def scenario():
        await asyncio.sleep(0.05)
        t = asyncio.create_task(helpers.wait_for_download(d, timeout=3))
        await late_events()
        return await t

    r = run(scenario())
    assert r["ok"] is True and r["filename"] == "f.zip", r


def test_registered():
    for name in ("refs", "click_ref", "wait_for_download"):
        assert name in helpers.list_helpers(), name


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
