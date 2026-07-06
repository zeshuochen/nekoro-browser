"""test_netidle.py — wait_for_network_idle 只认活动标签的 Network 事件。
无需浏览器，用 fake daemon 喂脚本化事件流（每次 drain 返回一批）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


class FakeBridge:
    async def send(self, method, params=None):
        return {}


class FakeDaemon:
    def __init__(self, active, batches):
        self.bridge = FakeBridge()
        self.active_tab_id = active
        self._batches = list(batches)     # list[list[event]]，每次 drain 弹一批

    async def drain_events(self):
        return self._batches.pop(0) if self._batches else []


def req(rid, tab):
    return {"method": "Network.requestWillBeSent", "params": {"requestId": rid}, "tabId": tab}


def done(rid, tab):
    return {"method": "Network.loadingFinished", "params": {"requestId": rid}, "tabId": tab}


def run(coro):
    return asyncio.run(coro)


def test_background_tab_events_ignored():
    # 活动标签=1；后台标签=2 一直发请求且从不完成 → 过滤后仍应静默
    d = FakeDaemon(1, [[req("bg", 2)], [req("bg2", 2)], []])
    r = run(helpers.wait_for_network_idle(d, idle_time=0.1, timeout=3))
    assert r == {"ok": True}, r


def test_active_pending_blocks_idle():
    # 活动标签的请求未完成 → 不静默 → 超时
    d = FakeDaemon(1, [[req("a", 1)]])       # 之后一直空，但 a 从未 finished
    r = run(helpers.wait_for_network_idle(d, idle_time=0.1, timeout=1))
    assert r["ok"] is False and r["error"] == "timeout" and r["pending"] == 1, r


def test_active_request_completes_idle():
    # 活动标签请求发出后完成 → 静默
    d = FakeDaemon(1, [[req("a", 1)], [done("a", 1)], []])
    r = run(helpers.wait_for_network_idle(d, idle_time=0.1, timeout=3))
    assert r == {"ok": True}, r


def test_active_none_counts_all():
    # 无活动标签（active=None）→ 不过滤，任何标签的未完成请求都算 → 超时
    d = FakeDaemon(None, [[req("x", 2)]])
    r = run(helpers.wait_for_network_idle(d, idle_time=0.1, timeout=1))
    assert r["ok"] is False and r["pending"] == 1, r


if __name__ == "__main__":
    test_background_tab_events_ignored()
    test_active_pending_blocks_idle()
    test_active_request_completes_idle()
    test_active_none_counts_all()
    print("ALL OK")
