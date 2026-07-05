"""test_js_values.py — js() eval 健壮化：裸表达式 / 顶层 return 重试 /
不可序列化值解码 / 错误路径。无需浏览器，用 fake daemon 喂 CDP 响应。"""
import asyncio
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


class FakeDaemon:
    """evaluate(expr) 返回 canned CDP 响应。resp 可为 dict 或
    callable(expr)->dict（按是否已包函数分支）。记录收到的表达式。"""
    def __init__(self, resp):
        self._resp = resp
        self.exprs = []

    async def evaluate(self, expr):
        self.exprs.append(expr)
        return self._resp(expr) if callable(self._resp) else self._resp


def run(coro):
    return asyncio.run(coro)


def test_plain_value():
    d = FakeDaemon({"result": {"type": "string", "value": "Example"}})
    r = run(helpers.js(d, "document.title"))
    assert r == {"ok": True, "result": "Example"}, r
    # 裸表达式：不包函数直接 eval（否则会返 undefined）
    assert d.exprs == ["document.title"]


def test_undefined_returns_none():
    d = FakeDaemon({"result": {"type": "undefined"}})
    r = run(helpers.js(d, "void 0"))
    assert r == {"ok": True, "result": None}, r


def test_unserializable_decode():
    cases = {"Infinity": math.inf, "-Infinity": -math.inf, "NaN": math.nan,
             "-0": -0.0, "123n": 123}
    for raw, expect in cases.items():
        d = FakeDaemon({"result": {"type": "number", "unserializableValue": raw}})
        r = run(helpers.js(d, "x"))
        assert r["ok"] is True
        got = r["result"]
        if isinstance(expect, float) and math.isnan(expect):
            assert math.isnan(got), (raw, got)
        else:
            assert got == expect, (raw, got)
    # -0 要真的是负零
    d = FakeDaemon({"result": {"type": "number", "unserializableValue": "-0"}})
    assert math.copysign(1.0, run(helpers.js(d, "x"))["result"]) == -1.0


def test_falsy_values():
    # 用成员判断 "value" in res（非 .get 真值），falsy 值不能掉进 undefined→None 分支
    for jsval, py in [("false", False), ("0", 0), ("''", ""), ("null", None)]:
        res = {"type": "boolean", "value": py} if py is not None else \
              {"type": "object", "subtype": "null", "value": None}
        d = FakeDaemon({"result": res})
        r = run(helpers.js(d, jsval))
        assert r == {"ok": True, "result": py}, (jsval, r)


def test_illegal_return_retry():
    # 第一次裸 eval → Illegal return statement；第二次（已包函数）→ 返回值
    def resp(expr):
        if expr.startswith("(function()"):
            return {"result": {"type": "string", "value": "T"}}
        return {"result": {}, "exceptionDetails": {
            "text": "Uncaught SyntaxError: Illegal return statement"}}
    d = FakeDaemon(resp)
    r = run(helpers.js(d, "return document.title"))
    assert r == {"ok": True, "result": "T"}, r
    # 恰好两次：裸 + 包函数
    assert len(d.exprs) == 2
    assert d.exprs[0] == "return document.title"
    assert d.exprs[1] == "(function(){return document.title})()"


def test_illegal_return_no_infinite_loop():
    # 包函数后仍报 illegal return（不该发生，但要防死循环）→ 只重试一次即 ok:false
    d = FakeDaemon({"result": {}, "exceptionDetails": {
        "text": "Illegal return statement"}})
    r = run(helpers.js(d, "return 1"))
    assert r["ok"] is False and "Illegal return" in r["error"]
    assert len(d.exprs) == 2, "最多重试一次"


def test_exception_detail_error():
    d = FakeDaemon({"result": {}, "exceptionDetails": {
        "exception": {"description": "ReferenceError: foo is not defined"}}})
    r = run(helpers.js(d, "foo"))
    assert r["ok"] is False and "ReferenceError" in r["error"], r


def test_subtype_error():
    d = FakeDaemon({"result": {"type": "object", "subtype": "error",
                               "description": "TypeError: bad"}})
    r = run(helpers.js(d, "null.x"))
    assert r["ok"] is False and "TypeError" in r["error"], r


def test_decode_unit():
    assert helpers._decode_unserializable_js_value("Infinity") == math.inf
    assert math.isnan(helpers._decode_unserializable_js_value("NaN"))
    assert helpers._decode_unserializable_js_value("999999999999999999999n") == 999999999999999999999
    assert helpers._decode_unserializable_js_value("plain") == "plain"  # 非哨兵原样返回


if __name__ == "__main__":
    test_plain_value()
    test_undefined_returns_none()
    test_unserializable_decode()
    test_falsy_values()
    test_illegal_return_retry()
    test_illegal_return_no_infinite_loop()
    test_exception_detail_error()
    test_subtype_error()
    test_decode_unit()
    print("ALL OK")
