"""mcp_server.py — Model Context Protocol server over stdio.

为什么要有这层：CLI + SKILL.md 只覆盖「会读文件的 agent」。而 MCP 是通用协议，
Claude Desktop / Cursor / opencode / Codex / VS Code 等一大批客户端都只认它。
这个模块把 helpers.py 原样暴露成 MCP tools，让它们不用改一行 nekoro 代码就能
驱动同一个 daemon。

实现取向与项目一致：**纯标准库**，不依赖 `mcp` SDK。stdio 上是行分隔的
JSON-RPC 2.0，协议面只需 initialize / tools/list / tools/call / ping 四个方法。

工具清单不是手写的——`inspect.signature` 扫 helpers.py 里第一个参数是 `daemon`
的协程函数，自动生成 JSON Schema。helpers 增删了这里自动跟着变，不会漂。

执行不在本进程：tools/call 把 `await name(**kwargs)` 发给正在跑的 daemon 的
HTTP `/exec`（复用 CLI 那套令牌鉴权），与 `echo ... | nekoro-browser` 同一条路径。
"""

import inspect
import json
import sys

from . import __version__
from .cli import _alive, _post

PROTOCOL_VERSION = "2025-06-18"

# 客户端请求这些版本时原样回显——本 server 没用任何版本独有特性，回显纯赚兼容性。
# 锁死在老版本的客户端见到不认识的 protocolVersion 有权直接断开。
_SUPPORTED_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18"})

# 手写 schema 的例外：签名是 **params / *cmds，签名反射表达不了。
# cdp 值得留（原始 CDP 逃生口），cdp_batch 的 [method, params] 列表结构对
# MCP 客户端不友好，让它们发多次 tools/call 即可。
_SKIP = {"cdp", "cdp_batch"}

_MANUAL_TOOLS = {
    "cdp": {
        "description": "Raw CDP command, e.g. method='Page.navigate', "
                       "params={'url': 'https://example.com'}.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "CDP method name"},
                "params": {"type": "object", "description": "CDP params object"},
            },
            "required": ["method"],
        },
    },
    "exec_python": {
        "description": "Escape hatch: run arbitrary Python in the daemon namespace "
                       "(all helpers pre-bound, top-level await allowed). Use when no "
                       "single tool fits — e.g. multi-step flows in one round trip.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source"},
            },
            "required": ["code"],
        },
    },
}

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean",
               dict: "object", list: "array"}

# 反射表达不了的单个参数：签名没注解、但实际接受多种形态。
_PARAM_OVERRIDES = {
    ("upload_file", "path"): {
        "description": "File path, or a list of paths for multi-file inputs",
        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
    },
    # 裸 {"type": "array"} 没有 items，严格校验的客户端（Gemini 系函数调用）会拒收。
    ("close_tabs", "tabs"): {
        "description": "Tab ids to close, e.g. from sweep_tabs()",
        "type": "array", "items": {"type": "integer"},
    },
    # description 只取 docstring 第一段，reuse 的说明落在后面段落里到不了客户端。
    ("new_tab", "reuse"): {
        "description": "Reuse an existing tab for the same site instead of opening a new one "
                       "(its login and page state are still there); falls back to opening "
                       "one when there is nothing reusable",
        "type": "boolean",
    },
}


def _param_schema(param: inspect.Parameter) -> dict:
    """注解 → JSON Schema 片段。拿不准一律 string（宽松好过报错拒绝调用）。"""
    ann = param.annotation
    if ann is inspect.Parameter.empty:
        # 无注解但有默认值 → 按默认值的类型推
        if param.default not in (inspect.Parameter.empty, None):
            return {"type": _JSON_TYPES.get(type(param.default), "string")}
        return {"type": "string"}
    if ann in _JSON_TYPES:
        return {"type": _JSON_TYPES[ann]}
    # `int | None` / `str | None` 这类联合：取第一个认识的具体类型
    for arg in getattr(ann, "__args__", ()):
        if arg in _JSON_TYPES:
            return {"type": _JSON_TYPES[arg]}
    return {"type": "string"}


def build_tools() -> list[dict]:
    """反射 helpers.py 生成 MCP tool 清单。"""
    from . import helpers as h

    tools = []
    for name in sorted(h.list_helpers()):
        if name in _SKIP:
            continue
        fn = getattr(h, name)
        if not inspect.iscoroutinefunction(fn):
            continue                       # list_helpers 之类同步工具函数，MCP 侧无意义
        params = list(inspect.signature(fn).parameters.values())
        if not params or params[0].name != "daemon":
            continue                       # 不吃 daemon 的不是 helper
        props, required = {}, []
        for p in params[1:]:
            if p.name.startswith("_"):
                continue                   # 私有开关（如 js 的 _wrapped）不外露
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            props[p.name] = _PARAM_OVERRIDES.get((name, p.name)) or _param_schema(p)
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
        doc = inspect.getdoc(fn) or name
        tools.append({
            "name": name,
            "description": doc.split("\n\n")[0].strip(),
            "inputSchema": {"type": "object", "properties": props,
                            "required": required},
        })
    for name, spec in _MANUAL_TOOLS.items():
        tools.append({"name": name, **spec})
    return tools


def _code_for(name: str, args: dict) -> str:
    """把 tools/call 的参数编成一句发给 /exec 的表达式。

    走 `repr` 而不是字符串拼接：JSON 解出来的值只有 str/num/bool/None/list/dict，
    这些的 repr 都是合法 Python 字面量，引号和转义由 repr 负责，不会被内容截断。
    """
    if name == "exec_python":
        return args.get("code", "")
    if name == "cdp":
        method = args.get("method", "")
        params = args.get("params") or {}
        return f"await cdp({method!r}, **{params!r})"
    return f"await {name}(**{args!r})"


def _timeout_for(args: dict) -> float:
    """HTTP 超时要盖过 helper 自己的等待，否则 `wait_selector(timeout=60)`
    会在客户端这边先断，daemon 那边还在等——报错还会误导成 daemon 死了。"""
    base = 60.0
    # sleep 的等待时长参数叫 seconds 不叫 timeout，只认 timeout 会漏掉它
    inner = args.get("timeout") if "timeout" in args else args.get("seconds")
    if isinstance(inner, (int, float)) and not isinstance(inner, bool) and inner > 0:
        return max(base, float(inner) + 15.0)
    return base


def call_tool(name: str, args: dict) -> dict:
    """执行一个工具，返回 MCP 的 CallToolResult。"""
    if not _alive():
        return _err("Daemon not running. Start it first: `nekoro-browser` "
                    "(foreground, keep it open).")
    r = _post("/exec", _code_for(name, args), timeout=_timeout_for(args))
    if not r.get("ok"):
        return _err(r.get("error", "unknown error"))

    result = r.get("result")
    # 截图走 image content，MCP 客户端能直接显示；文本通道塞 base64 是浪费。
    if (name == "capture_screenshot" and isinstance(result, dict)
            and result.get("ok") and result.get("data")):
        # CDP 的 format 原样透传，webp 也是合法值——标错 mimeType 客户端渲染不出来
        fmt = str(result.get("format", "png")).lower()
        mime = {"jpeg": "image/jpeg", "jpg": "image/jpeg",
                "webp": "image/webp"}.get(fmt, "image/png")
        return {"content": [{"type": "image", "data": result["data"],
                             "mimeType": mime}]}

    # helper 自身报的失败（{"ok": false, ...}）也算工具错误，别让 agent 当成功
    is_error = isinstance(result, dict) and result.get("ok") is False
    # page_info 是个例外：daemon.get_page_info() 吞异常返回空串，形状上仍是「成功」。
    # 扩展/SW 睡死时这会变成一次静默的空结果——CLI 的 --doctor 专门防了这点，
    # MCP 侧同样不能让 agent 拿着空 url 往下走。
    if (name == "page_info" and isinstance(result, dict)
            and not result.get("url")):
        return _err("page_info returned an empty url — the extension or its "
                    "service worker is probably not responding. Check "
                    "chrome://extensions, then `nekoro-browser --doctor`.")

    stdout = r.get("stdout") or ""
    payload = result if result is not None else stdout
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, default=str)
    out = {"content": [{"type": "text", "text": text}]}
    # print() 出来的东西也要带上（exec_python 常见），但别把 stdout-only 的响应重复两遍
    if stdout and stdout != payload:
        out["content"].append({"type": "text", "text": stdout})
    if is_error:
        out["isError"] = True
    return out


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def handle(msg) -> dict | None:
    """处理一条 JSON-RPC 消息。返回 None = 通知（notification），不回包。

    msg 不保证是 object：合法 JSON 也可能是数字、字符串，或顶层数组
    （2024-11-05 的 batch 形状）。这些必须当协议错误挡在这里，不能让
    `msg.get` 抛 AttributeError 冲穿 serve() 把进程带走。
    """
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600,
                          "message": "Invalid Request: expected a JSON object "
                                     "(JSON-RPC batching is not supported)"}}
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        want = ((msg.get("params") or {}).get("protocolVersion")
                if isinstance(msg.get("params"), dict) else None)
        return _ok(mid, {
            "protocolVersion": want if want in _SUPPORTED_VERSIONS else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "nekoro-browser", "version": __version__},
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": build_tools()})
    if method == "tools/call":
        p = msg.get("params") or {}
        name = p.get("name", "")
        known = {t["name"] for t in build_tools()}
        if name not in known:
            return _ok(mid, _err(f"Unknown tool: {name}"))
        # 工具执行失败走 isError 的结果体，不走 JSON-RPC error——
        # 协议要求前者，好让模型看到失败原因并自己重试。
        return _ok(mid, call_tool(name, p.get("arguments") or {}))
    if mid is None:
        return None                        # 未知通知，静默丢弃
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def _ok(mid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=None, stdout=None) -> None:
    """读 stdin 的行分隔 JSON-RPC，逐条应答。EOF 即退出。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "Parse error"}}
        else:
            try:
                resp = handle(msg)
            except Exception as e:                     # 单条崩了别拖垮整个 server
                # id 也要防御地取——msg 可能不是 dict，处理器自己再抛就真死了
                mid = msg.get("id") if isinstance(msg, dict) else None
                resp = {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32603, "message": f"Internal error: {e}"}}
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


def main() -> None:
    # stdout 是协议通道，任何日志都必须走 stderr，否则会污染 JSON-RPC 流。
    #
    # 编码必须钉成 UTF-8：MCP 的 stdio 传输规定 UTF-8，但 Python 默认跟随
    # locale——中文 Windows（ACP=936）上 `page_text()` 抓到一个 emoji 就
    # UnicodeEncodeError 把 server 打死，而中文本身 gbk 编得动，症状是间歇性崩。
    # newline="" 防 Windows 把 \n 写成 \r\n（行分隔的协议帧里那是脏字节）。
    for stream, kw in ((sys.stdin, {}), (sys.stdout, {"newline": ""})):
        try:
            stream.reconfigure(encoding="utf-8", **kw)
        except (AttributeError, ValueError):
            pass          # 被替换成非 TextIOWrapper 的流（测试/嵌入场景），跳过
    serve()


if __name__ == "__main__":
    main()
