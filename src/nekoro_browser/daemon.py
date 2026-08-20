"""daemon.py — CDP 路由 + helper 执行"""

import asyncio
import io
import contextlib
import contextvars
import ast
import json
import logging
from typing import Any

from . import allowlist
from . import auth
from . import config
from . import lifecycle
from .bridge import ExtensionBridge

logger = logging.getLogger(__name__)

# 每个 exec 任务自己的 stdout 缓冲。**不能用 contextlib.redirect_stdout**：那是改
# 进程全局的 sys.stdout，而 daemon 会并发跑多条 exec。实测两条并发时，先发起的那条
# 在客户端超时后仍在跑，它后来的 print 落进了**另一条请求**的响应里，而后者自己的
# 输出反倒丢到 daemon 控制台去了（A 超时 → B 拿到 "B_START\nA_LATE"，B_END 不见了）。
# ContextVar 在 asyncio 里按任务隔离，各写各的缓冲，互不串台。
_EXEC_STDOUT: contextvars.ContextVar[io.StringIO | None] = \
    contextvars.ContextVar("nekoro_exec_stdout", default=None)


class _TaskRoutedStdout:
    """把 print 路由到「当前任务自己的缓冲」，没有缓冲时落回真正的 stdout。

    装一次就长期占着 sys.stdout。落回分支保证 daemon 自己的启动横幅、以及任何不在
    exec 上下文里的输出照常可见——不是把 stdout 吞掉。
    """

    def __init__(self, real):
        self._real = real

    def _target(self):
        return _EXEC_STDOUT.get() or self._real

    def write(self, s):
        return self._target().write(s)

    def flush(self):
        try:
            self._target().flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def writable(self):
        return True

    @property
    def encoding(self):
        return getattr(self._real, "encoding", "utf-8")


def install_task_routed_stdout():
    """把 sys.stdout 换成按任务路由的代理。重复调用无副作用。"""
    import sys
    if not isinstance(sys.stdout, _TaskRoutedStdout):
        sys.stdout = _TaskRoutedStdout(sys.stdout)


def _auto_await_code(code: str):
    """Transpile bare async-call expression statements to await for backward compat.
    navigate("url") → await navigate("url")
    print("x") → left as-is (builtin)
    x = js("...") → left as-is (not a bare call expression)
    """
    import ast as _ast
    _PY_BUILTINS = frozenset({
        "print", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
        "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
        "min", "max", "sum", "abs", "round", "type", "isinstance", "hasattr",
        "getattr", "setattr", "any", "all", "next", "iter",
        "open", "input", "id", "dir", "vars", "repr", "format", "chr", "ord",
        "hex", "oct", "bin", "pow", "divmod", "super", "object",
    })
    try:
        tree = _ast.parse(code, mode="exec")
        for node in tree.body:
            if isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Call):
                func = node.value.func
                # Only auto-await bare name calls that aren't Python builtins
                if isinstance(func, _ast.Name) and func.id not in _PY_BUILTINS:
                    node.value = _ast.Await(value=node.value)
        _ast.fix_missing_locations(tree)
        return compile(tree, "<nekoro-script>", "exec",
                       flags=_ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except SyntaxError:
        return compile(code, "<nekoro-script>", "exec",
                       flags=_ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


EVENT_BUFFER_MAX = 2000  # CDP 事件缓冲上限；满了丢最旧，防没人 drain 时无界增长


class Daemon:
    def __init__(self, port=None, allow_domains=None):
        self.bridge = ExtensionBridge(config.daemon_port(port))
        # None = 未配置 = 不限制（fail-open，见 allowlist.py 的取舍说明）。
        # 闸门放在 daemon 侧而不是 CLI 侧：CLI 只是客户端之一，MCP server 走的是
        # 同一个 daemon，拦在这里两个入口才都受管。
        self.allow_domains: list[str] | None = allow_domains or allowlist.from_env()
        self._tab_id = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=EVENT_BUFFER_MAX)
        self._site_errors: list[str] = []

    async def start(self) -> bool:
        self.bridge.set_exec_handler(self._on_exec)
        self.bridge.on_event(self._queue_event)  # 全局事件收集
        # 扩展重连/换标签时同步活动 tab_id（含用户关标签后的自动重连）
        self.bridge.set_attach_handler(self._on_attach)
        await self.bridge.start()
        # 令牌在成功 bind 之后再签发：否则端口已被占用（已有 daemon 在跑）时，
        # issue_token 会先覆盖共享令牌文件，把仍在运行的旧 daemon 弄成 403。
        # bind 到签发之间 self.token 为 None，token_eq 判否 → /exec 拒，失败关闭。
        self.bridge.set_token(auth.issue_token())
        lifecycle.write_pid()  # 令牌签发后写 pid，供 CLI --stop/--restart 与自愈用
        # 记下实际端口：客户端不带 --port 时靠这个文件找到非默认端口上的 daemon
        config.write_port_file(self.bridge.port)
        logger.info("Waiting for extension...")
        try:
            await asyncio.wait_for(self.bridge.attached.wait(), timeout=10)
            self._tab_id = self.bridge.attached_tab_id
            logger.info(f"Tab {self._tab_id} auto-attached")
        except asyncio.TimeoutError:
            logger.warning("No auto-attach, sending auto_attach command")
            await self.bridge.send_control("auto_attach")
            try:
                await asyncio.wait_for(self.bridge.attached.wait(), timeout=10)
                self._tab_id = self.bridge.attached_tab_id
                logger.info(f"Tab {self._tab_id} attached via command")
            except asyncio.TimeoutError:
                logger.error("Failed to attach tab after retry")
        return True

    def _on_attach(self, tab_id):
        """扩展 attach 状态变化时更新活动标签（重连自动换标签后仍指向可用 tab）。"""
        self._tab_id = tab_id

    async def _on_exec(self, code: str) -> dict[str, Any]:
        from functools import partial
        import importlib
        import ast as _ast
        from . import helpers as h
        from . import agent_helpers as ah
        # 注入核心 helper 后再 reload：agent_helpers 的文件头示例写的就是
        # `await js(daemon, ...)` 这种直接调用，不注入就是 NameError。
        for _n in h.list_helpers():
            ah.__dict__.setdefault(_n, getattr(h, _n))
        importlib.reload(ah)  # 每次 exec 拿最新 agent_helpers
        v = {"daemon": self, "tab": self._tab_id}
        for name in h.list_helpers():
            fn = getattr(h, name)
            # 只有吃 daemon 的（协程 helper）才绑；list_helpers 这类同步工具函数
            # 不吃 daemon，硬 partial 上去调用时会 TypeError: takes 0 positional
            # arguments but 1 was given。
            v[name] = partial(fn, self) if asyncio.iscoroutinefunction(fn) else fn
        # 用户目录里按站点固化的函数（<skills 根>/<site>/*.py）。放在 agent_helpers
        # 之前：agent_helpers 是草稿纸，草稿应当能盖过已固化的版本。
        # 同名不覆盖 helpers.py 的核心函数——被站点脚本悄悄改掉 click_selector 的
        # 语义是最难查的一类 bug。
        from . import site_notes
        site_ns, site_errors = site_notes.load_functions(h)
        self._site_errors = site_errors
        core = set(h.list_helpers())
        for name, obj in site_ns.items():
            if name in core:
                self._site_errors.append(f"{name}: 与核心 helper 同名，已跳过")
                continue
            v[name] = partial(obj, self) if asyncio.iscoroutinefunction(obj) else obj
        for name, obj in vars(ah).items():
            if name.startswith("_") or not callable(obj):
                continue
            # 只绑 agent_helpers 自己定义的：注入进去的核心 helper 上面已经绑过，
            # 再绑一次会把 list_helpers 这类同步函数也塞上 daemon → TypeError。
            # （M3 就是这个错，别用注入把它复活。）
            if getattr(obj, "__module__", None) != ah.__name__:
                continue
            v[name] = partial(obj, self) if asyncio.iscoroutinefunction(obj) else obj
        # **globals 和 locals 必须是同一个映射。**分开传时，脚本里 `import math` /
        # `x = 42` 绑进的是 locals，而模块级 `def` 的函数体只查 globals（那是个只有
        # __builtins__ 的空壳）——于是 `import math; def f(): return math.pi; f()`
        # 报 NameError: name 'math' is not defined。这直接废掉了「多步流程一次发过去」
        # 和 agent 自己写脚本这两件事，只能把代码全展平、或在每个函数内部重新 import。
        # v 是每次 exec 新建的，共用一个映射不会跨请求串。
        v["__builtins__"] = __builtins__
        stdout_buf = io.StringIO()
        install_task_routed_stdout()
        tok = _EXEC_STDOUT.set(stdout_buf)
        try:
            # 单表达式 → eval（返回 result 字段，兼容旧脚本）
            try:
                _ast.parse(code, mode="eval")
                compiled = compile(code, "<nekoro-expr>", "eval",
                                   flags=_ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                result = eval(compiled, v)
                if asyncio.iscoroutine(result):
                    result = await result
                return {"ok": True, "result": result,
                        "stdout": stdout_buf.getvalue()}
            except SyntaxError:
                pass
            # 多行语句 → auto-await + exec
            compiled = _auto_await_code(code)
            coro = eval(compiled, v)
            if asyncio.iscoroutine(coro):
                await coro
            return {"ok": True, "stdout": stdout_buf.getvalue()}
        except Exception:
            import traceback
            return {"ok": False, "error": traceback.format_exc(),
                    "stdout": stdout_buf.getvalue()}
        finally:
            _EXEC_STDOUT.reset(tok)

    # ── API ───────────────────────────────────────────────────────────────
    async def list_tabs(self):
        """列出 nekoro 托管组里的标签及 attach 状态。"""
        return await self.bridge.send_request("list_tabs")
    async def switch_tab(self, tab_id):
        """把活动标签切到 tab_id（未 attach 则 attach）。_tab_id 由 attach 回调同步。"""
        return await self.bridge.send_request("switch_tab", tabId=tab_id)
    async def navigate(self, url, tab=None): return await self.bridge.send("Page.navigate", {"url": url}, tab=tab)
    async def evaluate(self, expr, tab=None): return await self.bridge.send("Runtime.evaluate", {"expression":expr,"returnByValue":True}, tab=tab)
    async def screenshot(self, format="png", quality=80, clip=None, tab=None):
        # clip 带 scale 字段可直接让 Chrome 按 CSS 尺寸出图（见 helpers.capture_screenshot），
        # 不必走 Emulation.setDeviceMetricsOverride —— 那会真改视口、影响响应式布局。
        p = {"format":format,"quality":quality}
        if clip: p["clip"] = clip
        r = await self.bridge.send("Page.captureScreenshot", p, tab=tab)
        return r.get("data","")
    async def get_document(self): return await self.bridge.send("DOM.getDocument", {"depth":-1})
    async def query_selector(self, sel):
        d = await self.get_document()
        return (await self.bridge.send("DOM.querySelector", {"nodeId":d["root"]["nodeId"],"selector":sel})).get("nodeId")
    async def click_element(self, nid):
        b = await self.bridge.send("DOM.getBoxModel", {"nodeId":nid})
        c = b["model"]["content"]
        return await self.click_at((c[0]+c[4])/2, (c[1]+c[5])/2)
    async def click_at(self, x, y):
        await self.bridge.send("Input.dispatchMouseEvent", {"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1})
        return await self.bridge.send("Input.dispatchMouseEvent", {"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1})
    async def type_text(self, t, tab=None): return await self.bridge.send("Input.insertText", {"text":t}, tab=tab)
    async def get_page_info(self, tab=None):
        # 单次 evaluate 返回 {title,url}，省一个往返（原来 title/href 两次串行）。
        try:
            r = await self.bridge.send("Runtime.evaluate", {
                "expression": "({title: document.title, url: location.href})",
                "returnByValue": True}, tab=tab)
            v = r["result"].get("value") or {}
            return {"title": v.get("title", ""), "url": v.get("url", "")}
        except: return {"title":"","url":""}
    # wait_for_load 的真实实现在 helpers.py（轮询 readyState，无 listener 泄漏）。
    # 曾有的 daemon 版依赖 Page.loadEventFired 但从没 Page.enable → 永不触发、
    # 每次调用还漏一个事件监听，已删。

    # ── Event Queue ────────────────────────────────────────────────────────

    def _queue_event(self, method, params, session_id=None, tab_id=None):
        """Push CDP event into buffer — consumed by drain_events()。
        缓冲满时丢最旧（环形），保证没人 drain 也不会无界占内存。
        tab_id 用于按标签过滤（如 wait_for_network_idle 只认活动标签）。"""
        ev = {"method": method, "params": params, "sessionId": session_id, "tabId": tab_id}
        try:
            self._event_queue.put_nowait(ev)
        except asyncio.QueueFull:
            try:
                self._event_queue.get_nowait()  # 丢最旧
            except asyncio.QueueEmpty:
                pass
            try:
                self._event_queue.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    async def drain_events(self) -> list[dict[str, Any]]:
        """Pull all buffered CDP events since last drain."""
        events: list[dict[str, Any]] = []
        while not self._event_queue.empty():
            events.append(self._event_queue.get_nowait())
        return events

    @property
    def active_tab_id(self): return self._tab_id

    @property
    def port(self) -> int:
        return self.bridge.port

    async def stop(self):
        await self.bridge.stop()
        lifecycle.cleanup_pid()
        config.clear_port_file()

    async def wait_forever(self):
        # /shutdown 命中或 Ctrl-C 时返回，交给 _run 的 finally 清理。
        await self.bridge.shutdown_requested.wait()
