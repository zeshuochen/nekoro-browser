"""test_fill_input.py — 框架感知填值 helper（无需浏览器）。

验证：
- 主路径：走 scripting fillInput，成功返回 {ok, value}，payload op/sel/arg 正确
- 失败诚实上报：没找到 / 非可填元素 / 空结果 → ok:false，绝不伪造成功
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


class FakeBridge:
    """按 op 路由 send_scripting；send 记录 CDP 调用。"""
    def __init__(self, scripting):
        self.scripting = scripting        # op -> 返回值（{value: ...}）
        self.scripting_calls = []
        self.sends = []

    async def send_scripting(self, params, timeout=30.0):
        self.scripting_calls.append(params)
        op = params.get("op") or params.get("action")
        return self.scripting.get(op, {})

    async def send(self, method, params=None, **kw):
        self.sends.append((method, params))
        return {}


class FakeDaemon:
    def __init__(self, bridge):
        self.bridge = bridge
        self.active_tab_id = 1
        self.typed = []

    async def type_text(self, text):
        self.typed.append(text)
        return {}


async def run():
    # 1. 主路径成功
    br = FakeBridge({"fillInput": {"value": {"ok": True, "value": "a@b.com"}}})
    d = FakeDaemon(br)
    r = await helpers.fill_input(d, "#email", "a@b.com")
    assert r == {"ok": True, "value": "a@b.com"}, r
    p = br.scripting_calls[0]
    assert p["op"] == "fillInput" and p["sel"] == "#email" and p["arg"] == "a@b.com", p
    assert p["target"] == 1, p                      # 用了 active_tab_id
    assert d.typed == [] and br.sends == []         # 只走 scripting，不碰 CDP

    # 2. 没找到元素 → ok False，带原始 error，绝不回退伪造成功
    br = FakeBridge({"fillInput": {"value": {"ok": False, "error": "element not found: #x"}}})
    d = FakeDaemon(br)
    r = await helpers.fill_input(d, "#x", "hi")
    assert not r["ok"] and "not found" in r["error"], r
    assert d.typed == [] and br.sends == []         # 没有任何 CDP 兜底

    # 3. 非可填元素（div/select，扩展侧 guard 返回 not fillable）→ ok False
    br = FakeBridge({"fillInput": {"value": {"ok": False, "error": "not a fillable input: DIV"}}})
    d = FakeDaemon(br)
    r = await helpers.fill_input(d, "div.box", "x")
    assert not r["ok"] and "fillable" in r["error"], r

    # 4. evaluate 返回空结果（无 value 键）→ helpers 里 res={}，不能当成功，诚实报 fill failed。
    #    （页内抛异常现由 CDP exceptionDetails → send_scripting raise → 各 helper 的 try/except
    #     兜成 ok:False；此处直喂 {} 覆盖“拿到结果但缺 value”这条路径。）
    br = FakeBridge({"fillInput": {}})              # 无 value 键，模拟空结果
    d = FakeDaemon(br)
    r = await helpers.fill_input(d, "#weird", "x")
    assert not r["ok"] and "fill failed" in r["error"], r

    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(run())
