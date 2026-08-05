"""test_refs_downloads.py — refs()/click_ref()（backendNodeId 稳定句柄）与 wait_for_download()。

fake 严格照真 wire 形状来：
- bridge.send(method, params) 分发 CDP 命令（DOM.* / Runtime.* / Input.*），按 method 返回假数据
- drain_events() 返回预设的事件序列（Page.downloadWillBegin/Progress）
"""
import asyncio
import os
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

    async def evaluate(self, expr):
        return {"result": {"value": "complete"}}

    async def drain_events(self):
        evts = self.download_events
        self.download_events = []          # 消费式，和真 daemon 一致
        return evts

    async def _send(self, method, params):
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
        if method == "DOM.resolveNode":
            for n in self.nodes:
                if n["backendNodeId"] == params.get("backendNodeId"):
                    return {"object": {"objectId": f"obj-{params['backendNodeId']}"}}
            return {"object": {}}
        if method == "Runtime.callFunctionOn":
            for n in self.nodes:
                if f"obj-{n['backendNodeId']}" == params.get("objectId"):
                    return {"result": {"value": n.get("text", "")}}
            return {"result": {"value": ""}}
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


def test_refs_error_surfaces():
    d = FakeDaemon(nodes=[node(1, 101, "BUTTON")])

    async def boom(self, method, params):
        raise RuntimeError("bridge down")

    d._send = boom
    r = run(helpers.refs(d))
    assert r["ok"] is False and "bridge down" in r["error"], r


# ── click_ref() ───────────────────────────────────────────────────────────────

def test_click_ref_clicks_center():
    d = FakeDaemon(nodes=[])
    r = run(helpers.click_ref(d, 101))
    assert r["ok"] is True, r
    last = d.mouse_events[-1]
    assert last["x"] == 60 and last["y"] == 45, last   # content 8 点的几何中心


def test_click_ref_stale_ref_is_transient():
    d = FakeDaemon(box_error="Could not find node with given id")
    r = run(helpers.click_ref(d, 999))
    assert r["ok"] is False and r["kind"] == "transient", r
    assert "no longer valid" in r["error"]
    assert d.mouse_events == [], "失效句柄绝不能点"


def test_click_ref_no_box_model_is_transient():
    d = FakeDaemon(box_content=[10, 20])          # 不足 8 点 → 未渲染
    r = run(helpers.click_ref(d, 101))
    assert r["ok"] is False and r["kind"] == "transient", r
    assert d.mouse_events == []


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
