"""test_pipelining.py — CDP 流水线 + page_info 单次往返（无需浏览器）。

验证：
- cdp_batch 把 N 条命令并发在途（max in-flight == N），非串行；耗时 ≈ 1 个往返
- 单条失败被隔离，不拖垮其他
- get_page_info 只发一次 evaluate（title+url 合并），返回 {title,url}
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers
from nekoro_browser.daemon import Daemon


class FakeBridge:
    """记录并发在途数 + 发送次数；send 模拟一个往返延迟。"""
    def __init__(self, rtt=0.02, fail=None, value=None):
        self.inflight = 0
        self.max_inflight = 0
        self.calls = []
        self.rtt = rtt
        self.fail = fail          # method 名 → 抛错
        self.value = value        # evaluate 返回值

    async def send(self, method, params=None, **kw):
        self.calls.append((method, params))
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(self.rtt)
            if self.fail and method == self.fail:
                raise RuntimeError("boom")
            return {"result": {"value": self.value}} if self.value is not None else {"method": method}
        finally:
            self.inflight -= 1


class FakeDaemon:
    def __init__(self, bridge):
        self.bridge = bridge


async def run():
    # 1. cdp_batch 并发在途：5 条命令 max_inflight 应 == 5（流水线），非 1（串行）
    br = FakeBridge(rtt=0.02)
    d = FakeDaemon(br)
    cmds = [[f"M{i}", {"n": i}] for i in range(5)]
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    r = await helpers.cdp_batch(d, *cmds)
    elapsed = loop.time() - t0
    assert r["ok"] and len(r["results"]) == 5, r
    assert all(x["ok"] for x in r["results"]), r
    assert br.max_inflight == 5, br.max_inflight          # 全部同时在途（真并发的硬证据）
    # 只要求「明显快于串行」（串行是 5×rtt=100ms），不去卡 ≈1 个往返：
    # CI 的 Windows runner 定时器粒度 15.6ms + 负载抖动，卡 3×rtt 会偶发假红。
    # 真并发的硬证据是上面的 max_inflight == 5，这条只是兜底。
    assert elapsed < br.rtt * 4, elapsed
    # 省略 params 也能跑：[method] 形式
    r = await helpers.cdp_batch(d, ["Solo"])
    assert r["results"][0]["ok"] and br.calls[-1] == ("Solo", {}), br.calls[-1]

    # 2. 单条失败被隔离
    br2 = FakeBridge(fail="M1")
    d2 = FakeDaemon(br2)
    r = await helpers.cdp_batch(d2, ["M0"], ["M1"], ["M2"])
    oks = [x["ok"] for x in r["results"]]
    assert oks == [True, False, True], oks
    assert r["ok"] is False, r                            # 有失败 → 顶层 ok False
    assert "boom" in r["results"][1]["error"], r["results"][1]

    # 3. get_page_info 只发一次 evaluate（合并 title+url）
    br3 = FakeBridge(value={"title": "T", "url": "U"})
    dm = Daemon()
    dm.bridge = br3
    info = await dm.get_page_info()
    assert info == {"title": "T", "url": "U"}, info
    assert len(br3.calls) == 1, br3.calls                 # 一个往返，非两个
    assert br3.calls[0][0] == "Runtime.evaluate", br3.calls[0]

    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(run())
