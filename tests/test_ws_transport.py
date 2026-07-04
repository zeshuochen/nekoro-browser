"""WebSocket 传输层测试 — 无需 Chrome，纯用一个裸 WS 客户端驱动 bridge。

跑法:  python tests/test_ws_transport.py
覆盖:  握手、command→result 往返、event 分发、>64KB 分片/64 位长度（双向）。
"""
import asyncio
import base64
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from nekoro_browser.bridge import ExtensionBridge  # noqa: E402


# ── 裸 WS 客户端（客户端→服务端必须掩码）──────────────────────────────────

async def ws_connect(host, port, path="/ws"):
    reader, writer = await asyncio.open_connection(host, port)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write((
        f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    ).encode())
    await writer.drain()
    status = await reader.readline()
    assert b"101" in status, status
    while (await reader.readline()) not in (b"\r\n", b""):
        pass
    return reader, writer


def client_encode(payload: bytes, opcode=0x1, fin=True) -> bytes:
    n = len(payload)
    mask = os.urandom(4)
    header = bytearray([(0x80 if fin else 0) | opcode])
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack("!H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", n)
    header += mask
    masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    return bytes(header) + masked


async def client_read(reader):
    head = await reader.readexactly(2)
    opcode = head[0] & 0x0F
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    return opcode, await reader.readexactly(length)


async def send_json(writer, obj):
    writer.write(client_encode(json.dumps(obj).encode()))
    await writer.drain()


# ── 测试 ─────────────────────────────────────────────────────────────────

async def run():
    bridge = ExtensionBridge(port=0)  # 临时端口，避开正在运行的真实 daemon
    await bridge.start()
    reader, writer = await ws_connect("127.0.0.1", bridge.port)
    await asyncio.wait_for(bridge._ws_ready.wait(), 2)
    assert bridge.connected

    # 1. attached 上报
    await send_json(writer, {"type": "attached", "tabId": 42})
    await asyncio.wait_for(bridge.attached.wait(), 2)
    assert bridge.attached_tab_id == 42

    # 1b. 客户端 ping → 服务端 pong
    writer.write(client_encode(b"hi", opcode=0x9))
    await writer.drain()
    op, payload = await client_read(reader)
    assert op == 0xA and payload == b"hi", (op, payload)

    # 2. command → result 往返
    async def responder():
        _, payload = await client_read(reader)
        msg = json.loads(payload)
        assert msg["method"] == "Foo.bar", msg
        await send_json(writer, {"id": msg["id"],
                                 "result": {"ok": 1, "echo": msg["params"]}})
    t = asyncio.create_task(responder())
    res = await bridge.send("Foo.bar", {"a": 1})
    assert res == {"ok": 1, "echo": {"a": 1}}, res
    await t

    # 3. event 分发
    got = []
    bridge.on_event(lambda m, p, s: got.append((m, p)))
    await send_json(writer, {"type": "event", "method": "E", "params": {"x": 2}})
    await asyncio.sleep(0.1)
    assert got == [("E", {"x": 2})], got

    # 3b. 分片消息重组（首帧 fin=0 + 续帧 opcode=0x0 fin=1）
    ev = json.dumps({"type": "event", "method": "F", "params": {"n": 9}}).encode()
    writer.write(client_encode(ev[:10], opcode=0x1, fin=False))
    writer.write(client_encode(ev[10:], opcode=0x0, fin=True))
    await writer.drain()
    await asyncio.sleep(0.1)
    assert ("F", {"n": 9}) in got, got

    # 4. >64KB 双向（64 位长度 + 掩码重组）
    big = "z" * 70000
    async def responder2():
        _, payload = await client_read(reader)
        msg = json.loads(payload)
        assert msg["params"]["p"] == big  # 服务端→客户端 64 位长度 OK
        await send_json(writer, {"id": msg["id"], "result": {"big": big}})
    t2 = asyncio.create_task(responder2())
    res2 = await bridge.send("Big.cmd", {"p": big})
    assert res2["big"] == big and len(res2["big"]) == 70000
    await t2

    # 4b. send_request 控制命令往返（list_tabs 形状）
    async def responder_lt():
        _, payload = await client_read(reader)
        msg = json.loads(payload)
        assert msg["type"] == "list_tabs" and "id" in msg, msg
        await send_json(writer, {"id": msg["id"],
                                 "result": {"tabs": [{"tabId": 1}, {"tabId": 2}]}})
    tlt = asyncio.create_task(responder_lt())
    r = await bridge.send_request("list_tabs")
    assert r["tabs"] == [{"tabId": 1}, {"tabId": 2}], r
    await tlt

    # 4c. attach/detach 回调：attached 传 tabId，当前标签 detached 传 None，
    #     重连再 attached 恢复（自动重连的核心）
    seen = []
    bridge.set_attach_handler(lambda t: seen.append(t))
    await send_json(writer, {"type": "attached", "tabId": 7})
    await asyncio.sleep(0.05)
    await send_json(writer, {"type": "detached", "tabId": 99})  # 非活动，忽略
    await asyncio.sleep(0.05)
    await send_json(writer, {"type": "detached", "tabId": 7})   # 活动，清空
    await asyncio.sleep(0.05)
    await send_json(writer, {"type": "attached", "tabId": 8})   # 重连到新标签
    await asyncio.sleep(0.05)
    assert seen == [7, None, 8], seen
    assert bridge.attached_tab_id == 8
    bridge.set_attach_handler(None)

    # 4d. send_request 错误传播（switch_tab 失败复用顶层 {id,error}）
    async def responder_err():
        _, payload = await client_read(reader)
        msg = json.loads(payload)
        await send_json(writer, {"id": msg["id"], "error": {"message": "no tab"}})
    ter = asyncio.create_task(responder_err())
    try:
        await bridge.send_request("switch_tab", tabId=999)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "no tab" in str(e), e
    await ter

    # 4e. send_request 超时 → TimeoutError 且 _pending 不泄漏（无人应答）
    try:
        await bridge.send_request("list_tabs", timeout=0.3)
        assert False, "expected TimeoutError"
    except TimeoutError:
        pass
    assert bridge._pending == {}, bridge._pending
    await client_read(reader)  # 排掉那条无人应答的请求帧，别污染后续 client_read

    # 5. 顶层 error（扩展真实上报形状 post({id, error:{...}})）→ 异常
    async def responder3():
        _, payload = await client_read(reader)
        msg = json.loads(payload)
        await send_json(writer, {"id": msg["id"],
                                 "error": {"message": "boom"}})
    t3 = asyncio.create_task(responder3())
    try:
        await bridge.send("Bad.cmd")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "boom" in str(e), e
    await t3

    # 5b. 嵌套 result.error（原始 CDP 错误形状）→ 异常
    async def responder4():
        _, payload = await client_read(reader)
        msg = json.loads(payload)
        await send_json(writer, {"id": msg["id"],
                                 "result": {"error": {"message": "nested"}}})
    t4 = asyncio.create_task(responder4())
    try:
        await bridge.send("Bad2.cmd")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "nested" in str(e), e
    await t4

    # 5c. _emit 失败时 send 必须清掉 future，不泄漏 _pending（PR review #1）
    async def boom(_msg):
        raise RuntimeError("extension not connected (WS)")
    orig_emit = bridge._emit
    bridge._emit = boom
    try:
        await bridge.send("Wont.reach")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
    assert bridge._pending == {}, bridge._pending
    bridge._emit = orig_emit

    # 5d. 断开后 _ws_ready 被清
    writer.close()
    await asyncio.sleep(0.1)
    assert not bridge._ws_ready.is_set()

    await bridge.stop()
    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(run())
