"""test_mcp.py — MCP stdio server：schema 反射、参数编码、结果映射、协议往返。无需浏览器。

fake 的 /exec 响应严格照 daemon._on_exec 的真实返回形状写：
成功单表达式 → {"ok": True, "result": ..., "stdout": ""}，
异常 → {"ok": False, "error": traceback, "stdout": ""}。
形状写歪了测试会绿、真跑会炸。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import mcp_server as m


def _fake_exec(captured, response):
    """替 _post：记下 daemon 实际收到的代码，回一个 wire-faithful 响应。"""
    def _post(path, data="", timeout=30):
        captured.append((path, data))
        return response
    return _post


_REAL = (m._alive, m._post)


def _patch(monkey_alive=True, response=None, captured=None):
    m._alive = lambda: monkey_alive
    m._post = _fake_exec(captured if captured is not None else [],
                         response or {"ok": True, "result": None, "stdout": ""})


def _unpatch():
    """还原真实的 _alive/_post，免得测试顺序变了互相串（pytest 会打乱顺序）。"""
    m._alive, m._post = _REAL


def test_tools_reflect_helpers():
    tools = {t["name"]: t for t in m.build_tools()}
    # 反射来的常用 helper 都在
    for name in ("navigate", "js", "click_selector", "page_info", "capture_screenshot"):
        assert name in tools, name
    # daemon 参数被剥掉，不能出现在 schema 里
    assert "daemon" not in tools["navigate"]["inputSchema"]["properties"]
    # 必填 vs 可选按签名默认值分
    nav = tools["navigate"]["inputSchema"]
    assert nav["required"] == ["url"]
    assert nav["properties"]["url"]["type"] == "string"
    assert nav["properties"]["wait"]["type"] == "boolean"
    assert nav["properties"]["timeout"]["type"] == "number"
    # `tab: int = None`（联合/None 默认）仍推成 integer，不塌成 string
    assert tools["click_selector"]["inputSchema"]["properties"]["tab"]["type"] == "integer"
    # 私有开关不外露
    assert "_wrapped" not in tools["js"]["inputSchema"]["properties"]
    # 同步函数（list_helpers）不是工具；varargs 的 cdp_batch 被跳过
    assert "list_helpers" not in tools
    assert "cdp_batch" not in tools
    # 手写的两个在
    assert "cdp" in tools and "exec_python" in tools
    # 每个工具都有非空描述（MCP 客户端靠它选工具）
    assert all(t["description"].strip() for t in tools.values())


def test_code_encoding_quotes_and_unicode():
    # 引号、反斜杠、中文都得原样过去，不能被拼接截断
    code = m._code_for("fill_input", {"sel": "input[name='q']", "text": "he said \"hi\" 中文"})
    assert code.startswith("await fill_input(**")
    ns = {}
    args = eval(code[len("await fill_input(**"):-1], {}, ns)   # repr 必须能 round-trip
    assert args == {"sel": "input[name='q']", "text": "he said \"hi\" 中文"}
    # cdp 走 **params 展开
    assert m._code_for("cdp", {"method": "Page.navigate", "params": {"url": "u"}}) == \
        "await cdp('Page.navigate', **{'url': 'u'})"
    # exec_python 原样透传
    assert m._code_for("exec_python", {"code": "print(1)"}) == "print(1)"


def test_call_tool_success_and_daemon_error():
    cap = []
    # page_info 的真实返回**没有 ok 键**（helpers.page_info → {"title","url"}），
    # fake 里别自作主张补一个，否则测的是不存在的形状。
    _patch(response={"ok": True, "result": {"title": "T", "url": "u"},
                     "stdout": ""}, captured=cap)
    r = m.call_tool("page_info", {})
    assert cap[0][0] == "/exec" and cap[0][1] == "await page_info(**{})"
    assert not r.get("isError")
    assert json.loads(r["content"][0]["text"])["title"] == "T"

    # daemon 侧抛异常（/exec ok=False）→ isError
    _patch(response={"ok": False, "error": "Traceback...\nNameError: nope",
                     "stdout": ""})
    r = m.call_tool("page_info", {})
    assert r["isError"] and "NameError" in r["content"][0]["text"]
    _unpatch()


def test_page_info_empty_url_is_error():
    # daemon.get_page_info() 吞异常后返回两个空串，形状上还是「成功」。
    # SW 睡死时不能让 agent 拿着空 url 当真。
    _patch(response={"ok": True, "result": {"title": "", "url": ""}, "stdout": ""})
    r = m.call_tool("page_info", {})
    assert r["isError"] and "doctor" in r["content"][0]["text"]
    _unpatch()


def test_str_result_keeps_stdout():
    # http_get 返回裸 str；print 出来的东西不能因此被丢掉
    _patch(response={"ok": True, "result": "<html>hi</html>", "stdout": "note\n"})
    r = m.call_tool("http_get", {"url": "https://example.com"})
    texts = [c["text"] for c in r["content"]]
    assert texts == ["<html>hi</html>", "note\n"]
    # 纯语句（无 result）时 stdout 只出现一次，不重复
    _patch(response={"ok": True, "stdout": "printed\n"})
    r = m.call_tool("exec_python", {"code": "print('printed')"})
    assert [c["text"] for c in r["content"]] == ["printed\n"]
    _unpatch()


def test_helper_level_failure_is_error():
    # helper 自己返回 {"ok": false}（/exec 本身 ok=True）也必须标成错误，
    # 否则 agent 会把「没找到元素」当成点成功了
    _patch(response={"ok": True, "result": {"ok": False, "error": "not found"},
                     "stdout": ""})
    r = m.call_tool("click_selector", {"sel": "#nope"})
    assert r["isError"] and "not found" in r["content"][0]["text"]


def test_screenshot_returns_image_content():
    _patch(response={"ok": True,
                     "result": {"ok": True, "data": "QUJD", "format": "png"},
                     "stdout": ""})
    r = m.call_tool("capture_screenshot", {})
    assert r["content"][0] == {"type": "image", "data": "QUJD", "mimeType": "image/png"}
    _patch(response={"ok": True,
                     "result": {"ok": True, "data": "QUJD", "format": "jpeg"},
                     "stdout": ""})
    assert m.call_tool("capture_screenshot", {"format": "jpeg"})[
        "content"][0]["mimeType"] == "image/jpeg"


def test_http_timeout_covers_helper_timeout():
    # helper 自己等 90s 时，HTTP 侧不能 60s 就断（否则报成 daemon 挂了）
    seen = []

    def _post(path, data="", timeout=30):
        seen.append(timeout)
        return {"ok": True, "result": {"ok": True}, "stdout": ""}
    m._alive, m._post = lambda: True, _post
    m.call_tool("wait_selector", {"sel": "#x"})
    m.call_tool("wait_selector", {"sel": "#x", "timeout": 90})
    assert seen == [60.0, 105.0]


def test_no_daemon_is_actionable_error():
    _patch(monkey_alive=False)
    r = m.call_tool("page_info", {})
    assert r["isError"] and "nekoro-browser" in r["content"][0]["text"]


def test_unknown_tool_does_not_reach_daemon():
    cap = []
    _patch(captured=cap)
    resp = m.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                     "params": {"name": "definitely_not_a_tool", "arguments": {}}})
    assert resp["result"]["isError"]
    assert cap == []          # 没打到 daemon


def test_protocol_roundtrip_over_stdio():
    """跑一遍真实客户端会发的序列：initialize → initialized 通知 → tools/list → call。"""
    cap = []
    _patch(response={"ok": True, "result": {"ok": True, "url": "https://example.com"},
                     "stdout": ""}, captured=cap)
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": m.PROTOCOL_VERSION, "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},   # 无 id，不该回包
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "navigate", "arguments": {"url": "https://example.com"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "no/such/method"},
    ]
    out = io.StringIO()
    m.serve(io.StringIO("\n".join(json.dumps(x) for x in lines) + "\n"), out)
    resps = [json.loads(l) for l in out.getvalue().strip().split("\n")]

    assert [r["id"] for r in resps] == [1, 2, 3, 4]      # 通知没回包
    assert resps[0]["result"]["protocolVersion"] == m.PROTOCOL_VERSION
    assert resps[0]["result"]["serverInfo"]["name"] == "nekoro-browser"
    assert len(resps[1]["result"]["tools"]) > 20
    assert cap[-1][1] == "await navigate(**{'url': 'https://example.com'})"
    assert resps[3]["error"]["code"] == -32601           # 未知方法走 JSON-RPC error


def test_bad_json_line_does_not_kill_server():
    _patch()
    out = io.StringIO()
    m.serve(io.StringIO('not json\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n'), out)
    resps = [json.loads(l) for l in out.getvalue().strip().split("\n")]
    assert resps[0]["error"]["code"] == -32700
    assert resps[1]["id"] == 7 and resps[1]["result"] == {}
    _unpatch()


def test_non_object_json_does_not_kill_server():
    """合法 JSON 但不是 object：数字、字符串、顶层数组（2024-11-05 的 batch 形状）。
    这些若让 msg.get 抛出去，serve() 会连同后续所有请求一起死掉。"""
    _patch()
    lines = ['123', '"hi"',
             '[{"jsonrpc":"2.0","id":1,"method":"ping"}]',
             '{"jsonrpc":"2.0","id":8,"method":"ping"}']      # 前面几条不能影响这条
    out = io.StringIO()
    m.serve(io.StringIO("\n".join(lines) + "\n"), out)
    resps = [json.loads(l) for l in out.getvalue().strip().split("\n")]
    assert [r["error"]["code"] for r in resps[:3]] == [-32600, -32600, -32600]
    assert resps[3]["id"] == 8 and resps[3]["result"] == {}   # server 还活着
    _unpatch()


def test_protocol_version_is_echoed_when_known():
    _patch()
    for want, expect in (("2024-11-05", "2024-11-05"),      # 老客户端：回显它认识的
                         ("2025-06-18", "2025-06-18"),
                         ("1999-01-01", m.PROTOCOL_VERSION),  # 不认识：回自己的
                         (None, m.PROTOCOL_VERSION)):
        params = {"capabilities": {}}
        if want:
            params["protocolVersion"] = want
        r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": params})
        assert r["result"]["protocolVersion"] == expect, want
    _unpatch()


def test_screenshot_webp_mimetype():
    # CDP 的 format 原样透传，webp 合法；标成 png 客户端渲染不出来
    _patch(response={"ok": True, "result": {"ok": True, "data": "QUJD", "format": "webp"},
                     "stdout": ""})
    assert m.call_tool("capture_screenshot", {"format": "webp"})[
        "content"][0]["mimeType"] == "image/webp"
    _unpatch()


def test_sleep_seconds_extends_http_timeout():
    seen = []

    def _post(path, data="", timeout=30):
        seen.append(timeout)
        return {"ok": True, "result": {"ok": True}, "stdout": ""}
    m._alive, m._post = lambda: True, _post
    m.call_tool("sleep", {"seconds": 120})     # 等待参数叫 seconds，不叫 timeout
    assert seen == [135.0]
    _unpatch()


def test_upload_file_path_accepts_list():
    schema = {t["name"]: t for t in m.build_tools()}["upload_file"]
    path = schema["inputSchema"]["properties"]["path"]
    types = {s["type"] for s in path["anyOf"]}
    assert types == {"string", "array"}        # 多文件上传不该被 schema 挡在客户端


if __name__ == "__main__":
    test_tools_reflect_helpers()
    test_code_encoding_quotes_and_unicode()
    test_call_tool_success_and_daemon_error()
    test_page_info_empty_url_is_error()
    test_str_result_keeps_stdout()
    test_helper_level_failure_is_error()
    test_screenshot_returns_image_content()
    test_screenshot_webp_mimetype()
    test_http_timeout_covers_helper_timeout()
    test_sleep_seconds_extends_http_timeout()
    test_upload_file_path_accepts_list()
    test_no_daemon_is_actionable_error()
    test_unknown_tool_does_not_reach_daemon()
    test_protocol_roundtrip_over_stdio()
    test_protocol_version_is_echoed_when_known()
    test_bad_json_line_does_not_kill_server()
    test_non_object_json_does_not_kill_server()
    print("ALL OK")
