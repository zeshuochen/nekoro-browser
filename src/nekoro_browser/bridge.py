"""bridge.py — 扩展 ↔ daemon 传输层

传输分两路，同一端口（默认 28417，可配，见 config.py）：

  1. 扩展 ↔ daemon：持久 WebSocket（GET /ws + Upgrade）。
     命令即时下发，结果/事件即时上报。零轮询、零 sleep 间隙。
     WebSocket 用纯 stdlib 实现（RFC6455），不引入依赖。
     附带好处：MV3 service worker 在活跃 WS 期间不会被杀。

  2. CLI ↔ daemon：一次性 HTTP。
     GET  /ping   → "pong"（健康检查）
     POST /exec   → 执行 Python 代码，返回 JSON
     POST /raw    → 单条原始 CDP 命令
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import struct

from . import auth
from . import config

logger = logging.getLogger(__name__)

HTTP_PORT = config.DEFAULT_PORT  # 可被 --port / NEKORO_PORT 覆盖，见 config.py
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_cmd_counter = 0


def _next_id() -> int:
    global _cmd_counter
    _cmd_counter += 1
    return _cmd_counter


# ── WebSocket 编解码（RFC6455，纯 stdlib）─────────────────────────────────

def _ws_accept(key: str) -> str:
    """Sec-WebSocket-Accept = base64(sha1(key + MAGIC))"""
    return base64.b64encode(
        hashlib.sha1((key + _WS_MAGIC).encode()).digest()).decode()


def _ws_encode(payload: bytes, opcode: int = 0x1) -> bytes:
    """服务端→客户端帧：FIN=1、不掩码、按长度选 7/16/64 位长度字段。"""
    n = len(payload)
    header = bytearray([0x80 | opcode])
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack("!H", n)
    else:
        header.append(127)
        header += struct.pack("!Q", n)
    return bytes(header) + payload


async def _ws_read_message(reader: asyncio.StreamReader):
    """读取一条完整消息（自动重组分片）。

    返回 (opcode, bytes)；opcode 为消息首帧的 opcode。
    控制帧（ping/pong/close）单独返回，不参与重组。
    EOF 时 readexactly 抛 IncompleteReadError，由调用方捕获。
    """
    frames = bytearray()
    msg_opcode = None
    while True:
        head = await reader.readexactly(2)
        fin = head[0] & 0x80
        opcode = head[0] & 0x0F
        masked = head[1] & 0x80
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await reader.readexactly(8))[0]
        mask = await reader.readexactly(4) if masked else b""
        payload = await reader.readexactly(length)
        if masked and length:
            # 向量化 XOR：大帧（如截图 base64）比逐字节循环快几个数量级
            full = (mask * ((length + 3) // 4))[:length]
            payload = (int.from_bytes(payload, "big")
                       ^ int.from_bytes(full, "big")).to_bytes(length, "big")
        # 控制帧（0x8 close / 0x9 ping / 0xA pong）立即返回，不参与重组
        if opcode >= 0x8:
            return opcode, payload
        if opcode != 0x0:  # 数据帧首帧
            msg_opcode = opcode
        frames += payload
        if fin:
            return msg_opcode, bytes(frames)


class ExtensionBridge:
    def __init__(self, port: int = HTTP_PORT):
        self._pending: dict[int, asyncio.Future] = {}
        self._event_handlers: list = []
        self._connected = False
        self._server = None
        self._exec_handler = None
        self._ext_writer: asyncio.StreamWriter | None = None
        self._ws_ready = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self.attached = asyncio.Event()
        self.attached_tab_id: int | None = None
        self._attach_handler = None
        self.token = None  # CLI /exec /raw 的共享令牌，daemon.start 里注入
        self.port = port  # 0 → OS 分配临时端口（测试用），实际端口在 start() 回填
        self.shutdown_requested = asyncio.Event()  # /shutdown 置位 → daemon.wait_forever 返回

    def set_exec_handler(self, handler):
        """/exec 回调。handler(code: str) -> dict"""
        self._exec_handler = handler

    def set_token(self, token):
        """注入 CLI HTTP 令牌（/exec /raw 校验用）。"""
        self.token = token

    def set_attach_handler(self, handler):
        """attach 状态变化回调。handler(tab_id) — attached 传 tab_id，detached 传 None。"""
        self._attach_handler = handler

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", self.port, reuse_address=True)
        # port=0 时回填 OS 实际分配的端口
        self.port = self._server.sockets[0].getsockname()[1]
        logger.info(f"listening on http://127.0.0.1:{self.port} (ws: /ws)")

    async def wait_for_extension(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._ws_ready.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def on_event(self, handler):
        self._event_handlers.append(handler)

    # ── 下发 ─────────────────────────────────────────────────────────────

    async def _emit(self, msg: dict, ready_timeout: float = 10.0):
        """把消息通过 WS 即时推给扩展。扩展未连时短暂等待。"""
        if not self._ws_ready.is_set():
            try:
                await asyncio.wait_for(self._ws_ready.wait(), ready_timeout)
            except asyncio.TimeoutError:
                raise RuntimeError("extension not connected (WS)")
        writer = self._ext_writer
        if writer is None:
            raise RuntimeError("extension not connected (WS)")
        frame = _ws_encode(json.dumps(msg).encode())
        async with self._write_lock:
            writer.write(frame)
            await writer.drain()

    async def send(self, method: str, params: dict | None = None,
                   session_id: str | None = None, timeout: float = 30.0,
                   tab: int | None = None) -> dict:
        """`tab` 指定目标标签（须已 attach）；不传就打扩展当前的活动指针——
        这是原有行为，绝大多数调用都该保持不传。"""
        cmd_id = _next_id()
        f = asyncio.get_running_loop().create_future()
        self._pending[cmd_id] = f
        msg = {"id": cmd_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        if tab is not None:
            msg["tabId"] = tab
        try:
            await self._emit(msg)
            return await asyncio.wait_for(f, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise TimeoutError(f"CDP '{method}' timed out")
        except Exception:
            # _emit 失败（如扩展未连）也要清掉 future，否则 _pending 泄漏
            self._pending.pop(cmd_id, None)
            raise

    async def send_control(self, msg_type: str, **kwargs):
        await self._emit({"type": msg_type, **kwargs})

    async def send_request(self, msg_type: str, timeout: float = 10.0, **kwargs) -> dict:
        """发一条带 id 的控制命令并等待扩展回 {id, result}（如 list_tabs/switch_tab）。"""
        cmd_id = _next_id()
        f = asyncio.get_running_loop().create_future()
        self._pending[cmd_id] = f
        try:
            await self._emit({"id": cmd_id, "type": msg_type, **kwargs})
            return await asyncio.wait_for(f, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise TimeoutError(f"control '{msg_type}' timed out")
        except Exception:
            self._pending.pop(cmd_id, None)
            raise

    async def send_scripting(self, params: dict, timeout: float = 30.0) -> dict:
        """发 scripting 命令（不走 CDP），等待响应。"""
        cmd_id = _next_id()
        f = asyncio.get_running_loop().create_future()
        self._pending[cmd_id] = f
        try:
            await self._emit({"id": cmd_id, "type": "scripting", "params": params})
            return await asyncio.wait_for(f, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise TimeoutError(f"Scripting '{params.get('action','?')}' timed out")
        except Exception:
            self._pending.pop(cmd_id, None)
            raise

    # ── 连接处理 ─────────────────────────────────────────────────────────

    async def _handle(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            req = line.decode(errors="replace").strip()
            parts = req.split(" ")
            if len(parts) < 2:
                return
            method, path = parts[0], parts[1]

            headers: dict[str, str] = {}
            while True:
                h = (await reader.readline()).decode(errors="replace").strip()
                if not h:
                    break
                if ":" in h:
                    k, v = h.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            # 扩展的 WebSocket 升级请求 → 校验来源后进入持久循环。
            # 只有 chrome-extension:// 来源放行，挡掉网页发起的 ws（带自身域名 Origin）。
            if headers.get("upgrade", "").lower() == "websocket":
                origin = headers.get("origin", "")
                if not origin.startswith("chrome-extension://"):
                    logger.warning(f"WS rejected, origin={origin!r}")
                    await _http_resp(writer, 403, "forbidden origin")
                    return
                await self._serve_ws(reader, writer, headers)
                return

            # CLI 的一次性 HTTP
            cl = int(headers.get("content-length", 0))
            body = (await reader.readexactly(cl)).decode(errors="replace") if cl > 0 else ""

            if path == "/ping":
                await _http_resp(writer, 200, "pong")  # 存活探测，无需令牌
            elif path == "/pid":
                # 自报 pid，供 lifecycle.identify 核验身份。免令牌（只读、无副作用）。
                await _http_resp(writer, 200, json.dumps({"pid": os.getpid()}),
                                 "Content-Type: application/json")
            elif path == "/shutdown":
                # 优雅停：置位 shutdown 事件让 daemon 主循环退出。必须 POST + 持令牌。
                if method != "POST":
                    await _http_resp(writer, 405, "method not allowed")
                elif not auth.token_eq(headers.get("x-nekoro-token", ""), self.token):
                    logger.warning("HTTP /shutdown rejected: bad token")
                    await _http_resp(writer, 403, "bad token")
                else:
                    await _http_resp(writer, 200, json.dumps({"ok": True}),
                                     "Content-Type: application/json")
                    self.shutdown_requested.set()
            elif path in ("/exec", "/raw"):
                # /exec 跑任意 Python、/raw 直发 CDP —— 必须持有令牌
                if not auth.token_eq(headers.get("x-nekoro-token", ""), self.token):
                    logger.warning(f"HTTP {path} rejected: bad token")
                    await _http_resp(writer, 403, "bad token")
                elif path == "/exec":
                    await self._handle_exec(writer, body)
                else:
                    await self._handle_raw(writer, body)
            else:
                await _http_resp(writer, 404, "Not Found")
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _serve_ws(self, reader, writer, headers):
        key = headers.get("sec-websocket-key", "")
        if not key:
            await _http_resp(writer, 400, "missing Sec-WebSocket-Key")
            return
        handshake = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {_ws_accept(key)}\r\n\r\n"
        )
        writer.write(handshake.encode())
        await writer.drain()

        self._ext_writer = writer
        self._connected = True
        self._ws_ready.set()
        logger.info("extension WS connected")
        hb = asyncio.create_task(self._heartbeat(writer))
        try:
            while True:
                opcode, payload = await _ws_read_message(reader)
                if opcode == 0x8:            # close
                    break
                if opcode == 0x9:            # ping → pong
                    async with self._write_lock:
                        writer.write(_ws_encode(payload, opcode=0xA))
                        await writer.drain()
                    continue
                if opcode == 0xA:            # pong
                    continue
                if opcode == 0x1:            # text
                    try:
                        self._dispatch(json.loads(payload.decode()))
                    except json.JSONDecodeError:
                        logger.warning("bad WS json from extension")
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            hb.cancel()
            if self._ext_writer is writer:
                self._ext_writer = None
                self._ws_ready.clear()
                self._connected = False
            logger.info("extension WS disconnected")

    async def _heartbeat(self, writer, interval: float = 20.0):
        """周期性 WS ping。浏览器自动回 pong，产生 socket 活动 →
        让 MV3 service worker 常驻；同时探测半开连接（write 失败即断开）。"""
        try:
            while True:
                await asyncio.sleep(interval)
                async with self._write_lock:
                    writer.write(_ws_encode(b"", opcode=0x9))
                    await writer.drain()
        except (asyncio.CancelledError, ConnectionResetError, RuntimeError, OSError):
            pass

    def _notify_attach(self, tab_id):
        if self._attach_handler:
            try:
                self._attach_handler(tab_id)
            except Exception as e:
                logger.error(f"attach handler error: {e}")

    def _dispatch(self, data: dict):
        """处理扩展上报：event / attached / result 等。"""
        tp = data.get("type")
        if tp == "event":
            for h in self._event_handlers:
                try:
                    h(data["method"], data.get("params", {}), data.get("sessionId"),
                      data.get("tabId"))
                except Exception as e:
                    logger.error(f"Event error: {e}")
        elif tp == "attached":
            self.attached_tab_id = data.get("tabId")
            self.attached.set()
            logger.info(f"Tab {self.attached_tab_id} attached")
            self._notify_attach(self.attached_tab_id)
        elif tp == "detached":
            logger.info(f"Tab {data.get('tabId')} detached")
            # 只在断开的是当前活动标签时清状态，避免关闭其它托管标签误清
            if data.get("tabId") == self.attached_tab_id:
                self.attached_tab_id = None
                self.attached.clear()
                self._notify_attach(None)
        elif tp == "attach_error":
            logger.error(f"ATTACH FAILED: tab={data.get('tabId')} "
                         f"detail={data.get('detail','?')}")
        elif "id" in data:
            f = self._pending.pop(data["id"], None)
            if f and not f.done():
                # 扩展的错误在顶层（background.js post({id, error:{...}})），
                # 原始 CDP 错误则嵌在 result.error 里，两种都要处理。
                # result 可能是 null（如 last_dialog 无对话框）→ 用 `or {}` 兜底，
                # 否则 None.get 抛 AttributeError 会一路拆掉 WS 桥。
                err = data.get("error") or (data.get("result") or {}).get("error")
                if err:
                    f.set_exception(RuntimeError(
                        f"CDP error: {err.get('message', '?')}"))
                else:
                    f.set_result(data.get("result", {}))

    async def _handle_exec(self, writer, body: str):
        if not self._exec_handler:
            await _http_resp(writer, 500, "No exec handler")
            return
        try:
            result = await self._exec_handler(body)
            await _http_resp(writer, 200, json.dumps(result, default=str),
                             "Content-Type: application/json")
        except Exception as e:
            await _http_resp(writer, 500, json.dumps({"ok": False, "error": str(e)}))

    async def _handle_raw(self, writer, body: str):
        try:
            cmd = json.loads(body)
            result = await self.send(cmd["method"], cmd.get("params", {}),
                                     timeout=cmd.get("timeout", 10))
            await _http_resp(writer, 200, json.dumps({"ok": True, "result": result}),
                             "Content-Type: application/json")
        except Exception as e:
            await _http_resp(writer, 200, json.dumps({"ok": False, "error": str(e)}))

    async def stop(self):
        """关闭监听并断开扩展。

        必须先主动断开扩展那条持久 WS：它的处理协程停在无限读循环里，而
        `wait_closed()`（Python 3.12+）要等所有已接受连接的处理协程结束——
        不先掐断就是**永久挂起**。这个坑同时会让 setup 等到扩展后卡在收尾、
        以及 daemon 关不掉变成占着端口的僵尸。
        """
        if not self._server:
            return
        self._server.close()
        w, self._ext_writer = self._ext_writer, None
        if w is not None:
            try:
                w.close()          # 读端拿到 EOF → 处理协程退出 → wait_closed 才可能返回
            except Exception:
                pass
        try:
            await asyncio.wait_for(self._server.wait_closed(), 3)
        except Exception:
            pass                   # 兜底：端口已 close()，剩下的交给进程退出
        self._server = None
        self._ws_ready.clear()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected


async def _http_resp(writer, status, body, extra_header=""):
    b = body.encode()
    h = f"HTTP/1.1 {status} OK\r\nContent-Length: {len(b)}\r\nConnection: close\r\n"
    if extra_header:
        h += extra_header + "\r\n"
    h += "\r\n"
    writer.write(h.encode() + b)
    await writer.drain()
