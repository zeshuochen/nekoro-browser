"""test_allowlist.py — 域名白名单闸门（纯逻辑 + 假 daemon，不碰浏览器）。

关键取舍是 **fail-open**：不配置就不限制。这不是疏忽，是因为包已经在 PyPI 上，
默认收紧会把所有现存脚本打挂。所以第一组用例就锁住"未配置 = 放行一切"——
哪天有人"顺手改成默认安全"，这里必须红。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import allowlist, helpers


class FakeDaemon:
    def __init__(self, rules=None):
        self.allow_domains = rules
        self.navigated = []

    async def navigate(self, url, tab=None):
        self.navigated.append(url)
        return {"frameId": "1"}

    async def evaluate(self, code, tab=None):
        return {"result": {"value": "complete"}}


def test_parse():
    assert allowlist.parse("jd.com, *.taobao.com") == ["jd.com", "*.taobao.com"]
    assert allowlist.parse("A.COM") == ["a.com"]           # 大小写归一
    assert allowlist.parse("a.com;b.com") == ["a.com", "b.com"]   # 分号也认
    # 空 / 全空项 → None（当没配置），而不是 []（当全部拒绝）。
    # 拼错一个参数就把工具锁死不是好设计。
    assert allowlist.parse("") is None
    assert allowlist.parse(None) is None
    assert allowlist.parse(" , , ") is None


def test_matching():
    # 未配置 = 放行一切（fail-open 的核心断言，别随手改）
    assert allowlist.host_allowed("anything.com", None) is True

    assert allowlist.host_allowed("jd.com", ["jd.com"]) is True
    assert allowlist.host_allowed("x.jd.com", ["jd.com"]) is False      # 精确不含子域
    assert allowlist.host_allowed("taobao.com", ["*.taobao.com"]) is True   # 通配含裸域
    assert allowlist.host_allowed("a.b.taobao.com", ["*.taobao.com"]) is True
    assert allowlist.host_allowed("anything", ["*"]) is True

    # 后缀不能误判：evil-jd.com / notjd.com 不属于 jd.com
    assert allowlist.host_allowed("evil-jd.com", ["*.jd.com"]) is False
    assert allowlist.host_allowed("notjd.com", ["jd.com"]) is False
    assert allowlist.host_allowed("jd.com.evil.com", ["*.jd.com"]) is False

    # 无主机的内部页放行——拦了会让 new_tab() 在配了白名单后突然不能用
    assert allowlist.host_of("about:blank") is None
    assert allowlist.host_allowed(None, ["jd.com"]) is True


def test_check_shape():
    assert allowlist.check("https://jd.com/a", ["jd.com"]) is None
    r = allowlist.check("https://evil.com/a", ["jd.com"])
    assert r is not None, "站外域必须被拦"
    assert r["ok"] is False and r["kind"] == "domain_blocked"
    assert "evil.com" in r["error"] and "jd.com" in r["error"]   # 说清拦了谁、允许谁


async def test_navigate_gate():
    # 配了白名单：站外域被拦，且**没有真的下发导航**
    d = FakeDaemon(["jd.com"])
    r = await helpers.navigate(d, "https://evil.com/x")
    assert r["ok"] is False and r["kind"] == "domain_blocked", r
    assert d.navigated == [], "被拦的 URL 不该下发到 CDP"

    # 站内域正常放行
    d = FakeDaemon(["jd.com"])
    r = await helpers.navigate(d, "https://jd.com/ok", wait=False)
    assert r["ok"] and d.navigated == ["https://jd.com/ok"], (r, d.navigated)

    # 未配置（含 daemon 压根没这个属性的老桩）→ 一律放行
    for daemon in (FakeDaemon(None), type("Bare", (FakeDaemon,), {})(None)):
        daemon.allow_domains = None
        r = await helpers.navigate(daemon, "https://whatever.com", wait=False)
        assert r["ok"], r


async def run():
    test_parse()
    test_matching()
    test_check_shape()
    await test_navigate_gate()
    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(run())
