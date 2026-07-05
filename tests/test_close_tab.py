"""test_close_tab.py — close_tab helper。无需浏览器，用 fake daemon/bridge。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


class FakeBridge:
    def __init__(self):
        self.scripting = []                    # 记录 send_scripting 的 params

    async def send_scripting(self, params, timeout=30.0):
        self.scripting.append(params)
        return {"closed": params.get("target")}


class FakeDaemon:
    def __init__(self, active=None):
        self.bridge = FakeBridge()
        self.active_tab_id = active


def run(coro):
    return asyncio.run(coro)


def test_close_explicit_tab():
    d = FakeDaemon(active=1)
    r = run(helpers.close_tab(d, 123))
    assert r == {"ok": True, "closed": 123}, r
    assert d.bridge.scripting == [{"action": "close_tab", "target": 123}]


def test_close_active_tab_default():
    d = FakeDaemon(active=42)
    r = run(helpers.close_tab(d))                 # 省略 tab → 关活动标签
    assert r == {"ok": True, "closed": 42}, r
    assert d.bridge.scripting == [{"action": "close_tab", "target": 42}]


def test_close_no_tab():
    d = FakeDaemon(active=None)                   # 无活动标签且未传 tab
    r = run(helpers.close_tab(d))
    assert r["ok"] is False and "No tab" in r["error"]
    assert d.bridge.scripting == [], "无标签不该发命令"


def test_close_error():
    class RaisingBridge(FakeBridge):
        async def send_scripting(self, params, timeout=30.0):
            raise RuntimeError("No tab with given id")
    d = FakeDaemon(active=1)
    d.bridge = RaisingBridge()
    r = run(helpers.close_tab(d, 999))
    assert r["ok"] is False and "No tab with given id" in r["error"], r


def test_registered():
    assert "close_tab" in helpers.list_helpers()


if __name__ == "__main__":
    test_close_explicit_tab()
    test_close_active_tab_default()
    test_close_no_tab()
    test_close_error()
    test_registered()
    print("ALL OK")
