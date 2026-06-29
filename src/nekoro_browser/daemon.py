"""daemon.py — CDP 路由 + helper 执行"""

import asyncio
import io
import contextlib
import ast
import json
import logging

from .bridge import ExtensionBridge

logger = logging.getLogger(__name__)


class Daemon:
    def __init__(self):
        self.bridge = ExtensionBridge()
        self._tab_id = None
        self._event_queue: asyncio.Queue = asyncio.Queue()

    async def start(self) -> bool:
        self.bridge.set_exec_handler(self._on_exec)
        self.bridge.on_event(self._queue_event)  # 全局事件收集
        await self.bridge.start()
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

    async def _on_exec(self, code: str) -> dict:
        from functools import partial
        import importlib
        from . import helpers as h
        from . import agent_helpers as ah
        importlib.reload(ah)  # 每次 exec 拿最新 agent_helpers
        v = {"daemon": self, "tab": self._tab_id}
        for name in h.list_helpers():
            v[name] = partial(getattr(h, name), self)
        for name, obj in vars(ah).items():
            if not name.startswith("_") and callable(obj):
                v[name] = partial(obj, self)
        stdout_buf = io.StringIO()
        try:
            compiled = compile(code, "<nekoro-script>", "exec",
                               flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            with contextlib.redirect_stdout(stdout_buf):
                coro = eval(compiled, {"__builtins__": __builtins__}, v)
                if asyncio.iscoroutine(coro):
                    await coro
            return {"ok": True, "stdout": stdout_buf.getvalue()}
        except Exception:
            import traceback
            return {"ok": False, "error": traceback.format_exc(),
                    "stdout": stdout_buf.getvalue()}

    # ── API ───────────────────────────────────────────────────────────────
    async def navigate(self, url): return await self.bridge.send("Page.navigate", {"url": url})
    async def evaluate(self, expr): return await self.bridge.send("Runtime.evaluate", {"expression":expr,"returnByValue":True})
    async def screenshot(self, f="png", q=80):
        r = await self.bridge.send("Page.captureScreenshot", {"format":f,"quality":q})
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
    async def type_text(self, t): return await self.bridge.send("Input.insertText", {"text":t})
    async def get_page_info(self):
        try:
            t = await self.bridge.send("Runtime.evaluate", {"expression":"document.title","returnByValue":True})
            u = await self.bridge.send("Runtime.evaluate", {"expression":"location.href","returnByValue":True})
            return {"title":t["result"].get("value",""),"url":u["result"].get("value","")}
        except: return {"title":"","url":""}
    async def wait_for_load(self, to=30):
        ev = asyncio.Event()
        def cb(m,p,s):
            if m=="Page.loadEventFired": ev.set()
        self.bridge.on_event(cb)
        try: await asyncio.wait_for(ev.wait(), to); return True
        except asyncio.TimeoutError: return False

    # ── Event Queue ────────────────────────────────────────────────────────

    def _queue_event(self, method, params, session_id=None):
        """Push CDP event into buffer — consumed by drain_events()."""
        try:
            self._event_queue.put_nowait({
                "method": method, "params": params, "sessionId": session_id
            })
        except asyncio.QueueFull:
            pass

    async def drain_events(self) -> list[dict]:
        """Pull all buffered CDP events since last drain."""
        events = []
        while not self._event_queue.empty():
            events.append(self._event_queue.get_nowait())
        return events

    @property
    def active_tab_id(self): return self._tab_id

    async def stop(self): await self.bridge.stop()
    async def wait_forever(self): await asyncio.Event().wait()
