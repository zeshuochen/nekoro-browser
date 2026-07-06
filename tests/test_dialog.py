"""test_dialog.py — get_last_dialog helper 编排（Python 侧）。
扩展的 CDP 层拦截/处置是 JS，无法单测，只测 helper 对 send_request 的编排。
无需浏览器，用 fake bridge。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


class FakeBridge:
    def __init__(self, result):
        self._result = result
        self.requests = []

    async def send_request(self, msg_type, timeout=10.0, **kwargs):
        self.requests.append((msg_type, kwargs))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeDaemon:
    def __init__(self, result):
        self.bridge = FakeBridge(result)


def run(coro):
    return asyncio.run(coro)


def test_dialog_present():
    dialog = {"kind": "confirm", "message": "Delete?", "url": "https://x.com",
              "defaultPrompt": ""}
    d = FakeDaemon(dialog)
    r = run(helpers.get_last_dialog(d))
    assert r == {"ok": True, "dialog": dialog}, r
    assert d.bridge.requests == [("last_dialog", {})], "走 last_dialog control 查询"


def test_dialog_none():
    d = FakeDaemon(None)                          # 期间无对话框 → 扩展返 null
    r = run(helpers.get_last_dialog(d))
    assert r == {"ok": True, "dialog": None}, r


def test_dialog_error():
    d = FakeDaemon(TimeoutError("control 'last_dialog' timed out"))
    r = run(helpers.get_last_dialog(d))
    assert r["ok"] is False and "last_dialog" in r["error"]


def test_registered():
    assert "get_last_dialog" in helpers.list_helpers()


# ── 真路径回归：last_dialog 无对话框返回 {result:null}，_dispatch 不能拆桥 ──
# （前一版 fake 直接返 None 绕过了 _dispatch，掩盖了 None.get 的 AttributeError bug）

def test_dispatch_null_result_resolves_none():
    from nekoro_browser.bridge import ExtensionBridge

    async def go():
        b = ExtensionBridge(port=0)
        f = asyncio.get_running_loop().create_future()
        b._pending[42] = f
        b._dispatch({"id": 42, "result": None})   # last_dialog 无对话框
        return await asyncio.wait_for(f, timeout=1)

    assert run(go()) is None, "null result 应解析成 None，而非抛异常拆桥"


def test_dispatch_cdp_error_still_raises():
    from nekoro_browser.bridge import ExtensionBridge

    async def go():
        b = ExtensionBridge(port=0)
        f = asyncio.get_running_loop().create_future()
        b._pending[7] = f
        b._dispatch({"id": 7, "result": {"error": {"message": "boom"}}})
        return await asyncio.wait_for(f, timeout=1)

    try:
        run(go())
        assert False, "result.error 仍应抛"
    except RuntimeError as e:
        assert "boom" in str(e)


if __name__ == "__main__":
    test_dialog_present()
    test_dialog_none()
    test_dialog_error()
    test_registered()
    test_dispatch_null_result_resolves_none()
    test_dispatch_cdp_error_still_raises()
    print("ALL OK")
