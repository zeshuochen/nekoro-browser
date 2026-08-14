"""test_navigate.py — navigate() 的加载等待逻辑（无需浏览器，用假 daemon）。

验证 helpers.navigate 在 wait=True 时轮询 document.readyState 直到 'complete'，
wait=False 立即返回，卡住不 complete 时按 timeout 返回 loaded=False。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


class FakeDaemon:
    """最小 daemon：navigate 记录 url；evaluate 按预设序列吐 readyState。"""
    def __init__(self, states, stuck=False):
        self.states = list(states)
        self.stuck = stuck          # True → 永远 'loading'，触发 timeout
        self.nav = []
        self.eval_calls = 0

    async def navigate(self, url, tab=None):
        self.nav.append(url)
        return {"frameId": "1"}

    async def evaluate(self, code, tab=None):
        self.eval_calls += 1
        if self.stuck:
            v = "loading"
        else:
            v = self.states.pop(0) if self.states else "complete"
        return {"result": {"value": v}}


async def run():
    # 1. wait=True：轮询到 complete 才返回，loaded=True
    d = FakeDaemon(["loading", "interactive", "complete"])
    r = await helpers.navigate(d, "https://x.test")
    assert r["ok"] and r["loaded"], r
    assert d.nav == ["https://x.test"], d.nav
    assert d.eval_calls == 3, d.eval_calls

    # 2. wait=False：立即返回，不查 readyState
    d = FakeDaemon(["loading"])
    r = await helpers.navigate(d, "https://y.test", wait=False)
    assert r["ok"] and r["loaded"] and d.eval_calls == 0, r

    # 3. 卡住不 complete → 按 timeout 返回 loaded=False（不抛异常）
    d = FakeDaemon([], stuck=True)
    t0 = asyncio.get_running_loop().time()
    r = await helpers.navigate(d, "https://z.test", timeout=0.6)
    dt = asyncio.get_running_loop().time() - t0
    assert r["ok"] and r["loaded"] is False, r
    assert dt >= 0.5, dt          # 确实等了 ~timeout
    assert d.eval_calls >= 2, d.eval_calls

    # 4. daemon.navigate 抛异常 → {ok: False}，不冒泡
    class Boom(FakeDaemon):
        async def navigate(self, url, tab=None):
            raise RuntimeError("nav failed")
    r = await helpers.navigate(Boom([]), "https://boom.test")
    assert r["ok"] is False and "nav failed" in r["error"], r

    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(run())
