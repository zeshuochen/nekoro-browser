"""test_stop.py — bridge.stop() 必须在有活动连接时也能返回。无需浏览器。

真实事故：扩展连着时 stop() 永久挂起。asyncio 的 wait_closed()（3.12+）
要等所有已接受连接的处理协程结束，而扩展那条 WS 停在无限读循环里，
于是 setup 等到扩展后卡死在收尾、daemon 也关不掉（端口被僵尸占着）。

所以这里的 fake 必须是**真的 TCP 连接并保持不断开**——用假对象模拟
"连接存在"不会复现这个 bug，测出来是绿的、线上照挂。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser.bridge import ExtensionBridge

_WS_REQ = (
    "GET /ws HTTP/1.1\r\n"
    "Host: 127.0.0.1\r\n"
    "Upgrade: websocket\r\n"
    "Connection: Upgrade\r\n"
    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
    "Sec-WebSocket-Version: 13\r\n"
    "Origin: chrome-extension://abcdefghabcdefghabcdefghabcdefgh\r\n"
    "\r\n"
)


async def _stop_with_live_ws():
    b = ExtensionBridge(port=0)
    await b.start()
    r, w = await asyncio.open_connection("127.0.0.1", b.port)
    try:
        w.write(_WS_REQ.encode())
        await w.drain()
        await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)   # 握手响应
        assert await b.wait_for_extension(5), "握手后应视为已连接"
        # 连接故意不关：stop() 自己得把它断掉
        await asyncio.wait_for(b.stop(), 8)                   # 挂起的话这里超时
        return True
    finally:
        try:
            w.close()
        except Exception:
            pass


async def _stop_idle():
    b = ExtensionBridge(port=0)
    await b.start()
    await asyncio.wait_for(b.stop(), 5)      # 无连接时也要干净返回
    await b.stop()                           # 重复 stop 不该抛
    return True


async def _port_released_after_stop():
    """stop() 之后端口必须真的可以再 bind——否则重启 daemon 会撞 'address in use'。"""
    b = ExtensionBridge(port=0)
    await b.start()
    port = b.port
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(_WS_REQ.encode())
    await w.drain()
    await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
    await asyncio.wait_for(b.stop(), 8)
    try:
        w.close()
    except Exception:
        pass
    b2 = ExtensionBridge(port=port)
    await b2.start()                          # 占着就会抛 OSError
    await asyncio.wait_for(b2.stop(), 5)
    return True


def test_stop_with_live_extension_connection():
    assert asyncio.run(_stop_with_live_ws()) is True


def test_stop_without_connection_and_twice():
    assert asyncio.run(_stop_idle()) is True


def test_port_is_reusable_after_stop():
    assert asyncio.run(_port_released_after_stop()) is True


if __name__ == "__main__":
    test_stop_with_live_extension_connection()
    test_stop_without_connection_and_twice()
    test_port_is_reusable_after_stop()
    print("ALL OK")
