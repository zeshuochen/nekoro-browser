"""test_new_tab.py — new_tab 走扩展 navigate action（分组+等 load）+ 切换。
无需浏览器，用 fake daemon/bridge 校验编排。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


class FakeBridge:
    def __init__(self, result):
        self._result = result
        self.scripting = []

    async def send_scripting(self, params, timeout=30.0):
        self.scripting.append(params)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeDaemon:
    def __init__(self, result):
        self.bridge = FakeBridge(result)
        self.switched = []

    async def switch_tab(self, tab_id):
        self.switched.append(tab_id)
        return {"attached": True, "tabId": tab_id}


def run(coro):
    return asyncio.run(coro)


def test_new_tab_success():
    # 扩展 waitTabLoad 返回字符串 'complete'（不是 Python bool）
    d = FakeDaemon({"navigated": "https://x.com", "tabId": 7, "load": "complete"})
    r = run(helpers.new_tab(d, "https://x.com"))
    assert r == {"ok": True, "tabId": 7, "loaded": True}, r
    # 走 navigate action（非裸 Target.createTarget）
    assert d.bridge.scripting == [{"action": "navigate", "url": "https://x.com"}]
    # 切到并 attach 新标签
    assert d.switched == [7]


def test_new_tab_default_blank():
    d = FakeDaemon({"tabId": 3, "load": "complete"})
    r = run(helpers.new_tab(d))
    assert r["ok"] is True and r["tabId"] == 3
    assert d.bridge.scripting[0]["url"] == "about:blank"
    assert d.switched == [3], "默认路径也应 switch 到新标签"


def test_new_tab_none_result():
    # 扩展返回 None（异常传输）→ (r or {}) 兜底，无 tabId → ok:false，不 switch
    d = FakeDaemon(None)
    r = run(helpers.new_tab(d, "x"))
    assert r["ok"] is False and "no tabId" in r["error"]
    assert d.switched == []


def test_new_tab_load_timeout():
    # 慢页面 waitTabLoad 超时 → load:'timeout'，仍算开成功但 loaded=False（不谎报加载完）
    d = FakeDaemon({"tabId": 9, "load": "timeout"})
    r = run(helpers.new_tab(d, "https://slow.example"))
    assert r == {"ok": True, "tabId": 9, "loaded": False}, r
    assert d.switched == [9]


def test_new_tab_no_tabid():
    d = FakeDaemon({"navigated": "x", "load": True})   # 扩展没回 tabId
    r = run(helpers.new_tab(d, "x"))
    assert r["ok"] is False and "no tabId" in r["error"]
    assert d.switched == [], "拿不到 tabId 不该 switch"


def test_new_tab_error():
    d = FakeDaemon(RuntimeError("create failed"))
    r = run(helpers.new_tab(d, "x"))
    assert r["ok"] is False and "create failed" in r["error"]
    assert d.switched == []


def test_registered():
    assert "new_tab" in helpers.list_helpers()


if __name__ == "__main__":
    test_new_tab_success()
    test_new_tab_default_blank()
    test_new_tab_none_result()
    test_new_tab_load_timeout()
    test_new_tab_no_tabid()
    test_new_tab_error()
    test_registered()
    print("ALL OK")
