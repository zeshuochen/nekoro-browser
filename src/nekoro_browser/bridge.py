"""bridge.py — HTTP 轮询服务端

GET  /ping    → "pong" (health check)
GET  /poll    → 下一个 CDP 命令（JSON），无命令时返回空
POST /result  → 扩展返回 CDP 执行结果
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

HTTP_PORT = 19825
_cmd_counter = 0


def _next_id() -> int:
    global _cmd_counter
    _cmd_counter += 1
    return _cmd_counter


class ExtensionBridge:
    def __init__(self):
        self._pending: dict[int, asyncio.Future] = {}
        self._outbox: asyncio.Queue = asyncio.Queue()
        self._event_handlers: list = []
        self._connected = False
        self._server = None
        self._exec_handler = None
        self.attached = asyncio.Event()
        self.attached_tab_id: int | None = None
        self.port = HTTP_PORT

    def set_exec_handler(self, handler):
        """Set callback for /exec endpoint. handler(code: str) -> dict"""
        self._exec_handler = handler

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", HTTP_PORT,
            reuse_address=True
        )
        logger.info(f"HTTP on http://127.0.0.1:{HTTP_PORT}")

    async def wait_for_extension(self, timeout: float = 30.0) -> bool:
        deadline = asyncio.get_event_loop().time() + timeout
        while not self._connected:
            if asyncio.get_event_loop().time() > deadline:
                return False
            await asyncio.sleep(0.2)
        return True

    def on_event(self, handler):
        self._event_handlers.append(handler)

    async def send(self, method: str, params: dict | None = None,
                   session_id: str | None = None, timeout: float = 30.0) -> dict:
        cmd_id = _next_id()
        f = asyncio.get_event_loop().create_future()
        self._pending[cmd_id] = f
        msg = {"id": cmd_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        await self._outbox.put(msg)
        try:
            return await asyncio.wait_for(f, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise TimeoutError(f"CDP '{method}' timed out")

    async def send_control(self, msg_type: str, **kwargs):
        await self._outbox.put({"type": msg_type, **kwargs})

    # ── HTTP ─────────────────────────────────────────────────────────────

    async def _handle(self, reader, writer):
        try:
            req = (await asyncio.wait_for(reader.readline(), 10)).decode().strip()
            parts = req.split(" ")
            if len(parts) < 2:
                return
            method, path = parts[0], parts[1]

            headers = {}
            while True:
                line = (await reader.readline()).decode().strip()
                if not line:
                    break
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            cl = int(headers.get("content-length", 0))
            body = (await reader.readexactly(cl)).decode() if cl > 0 else ""

            if path == "/ping":
                self._connected = True
                await _resp(writer, 200, "pong")

            elif path == "/poll":
                self._connected = True
                await self._handle_poll(writer)

            elif path == "/result":
                self._connected = True
                await self._handle_result(writer, body)

            elif path == "/exec":
                self._connected = True
                await self._handle_exec(writer, body)

            elif path == "/raw":
                self._connected = True
                await self._handle_raw(writer, body)

            else:
                await _resp(writer, 404, "Not Found")

        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_poll(self, writer):
        try:
            msg = self._outbox.get_nowait()
            tp = msg.get("type", "cdp")
            mid = msg.get("method", msg.get("id", "?"))
            logger.info(f"POLL → [{tp}] {mid}")
            await _resp(writer, 200, json.dumps(msg),
                        "Content-Type: application/json")
        except asyncio.QueueEmpty:
            if not hasattr(self, '_first_poll'):
                self._first_poll = True
                logger.info("POLL ← extension connected!")
            await _resp(writer, 200, "")

    async def _handle_result(self, writer, body: str):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            await _resp(writer, 400, "Invalid JSON")
            return

        await _resp(writer, 200, "ok")

        tp = data.get("type")

        if tp == "event":
            for h in self._event_handlers:
                try:
                    h(data["method"], data.get("params", {}), data.get("sessionId"))
                except Exception as e:
                    logger.error(f"Event error: {e}")

        elif tp == "attached":
            self.attached_tab_id = data.get("tabId")
            self.attached.set()
            logger.info(f"Tab {self.attached_tab_id} attached")

        elif tp == "detached":
            logger.info(f"Tab {data.get('tabId')} detached")

        elif "id" in data:
            f = self._pending.get(data["id"])
            if f and not f.done():
                if "error" in data.get("result", {}):
                    err = data["result"]["error"]
                    f.set_exception(RuntimeError(
                        f"CDP error: {err.get('message','?')}"))
                else:
                    f.set_result(data.get("result", {}))

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def connected(self) -> bool:
        return self._connected

    async def _handle_exec(self, writer, body: str):
        if not self._exec_handler:
            await _resp(writer, 500, "No exec handler")
            return
        try:
            result = await self._exec_handler(body)
            await _resp(writer, 200, json.dumps(result, default=str),
                        "Content-Type: application/json")
        except Exception as e:
            await _resp(writer, 500, json.dumps({"ok": False, "error": str(e)}))

    async def _handle_raw(self, writer, body: str):
        try:
            cmd = json.loads(body)
            result = await self.send(cmd["method"], cmd.get("params", {}),
                                     timeout=cmd.get("timeout", 10))
            await _resp(writer, 200, json.dumps({"ok": True, "result": result}),
                        "Content-Type: application/json")
        except Exception as e:
            await _resp(writer, 200, json.dumps({"ok": False, "error": str(e)}))


async def _resp(writer, status, body, extra_header=""):
    b = body.encode()
    h = f"HTTP/1.1 {status} OK\r\nContent-Length: {len(b)}\r\nConnection: close\r\n"
    if extra_header:
        h += extra_header + "\r\n"
    h += "\r\n"
    writer.write(h.encode() + b)
    await writer.drain()
