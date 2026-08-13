"""test_auth.py — 本地传输令牌 + WS Origin 校验（无需浏览器）。

验证：
- auth 令牌签发/读取/恒定时间比较
- /exec 无令牌→403、带令牌→200；/ping 免令牌
- WS 升级：网页 Origin→403、chrome-extension Origin→101
"""
import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 令牌文件指向临时目录，别碰真实用户目录
os.environ["NEKORO_DATA_DIR"] = tempfile.mkdtemp(prefix="nekoro-auth-")

from nekoro_browser import auth
from nekoro_browser.bridge import ExtensionBridge


async def http_post(port, path, headers, body=""):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    req = (f"POST {path} HTTP/1.1\r\nHost: x\r\n"
           f"Content-Length: {len(body)}\r\n")
    for k, v in headers.items():
        req += f"{k}: {v}\r\n"
    req += "\r\n" + body
    w.write(req.encode())
    await w.drain()
    line = await r.readline()
    w.close()
    return line.decode()


async def ws_handshake(port, origin):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    req = ("GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
           "Connection: Upgrade\r\n"
           "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
           f"Origin: {origin}\r\n\r\n")
    w.write(req.encode())
    await w.drain()
    line = await r.readline()
    w.close()
    return line.decode()


async def run():
    # 1. auth 基本行为
    tok = auth.issue_token()
    assert tok and auth.read_token() == tok, tok
    assert auth.token_eq(tok, tok)
    assert not auth.token_eq(tok, "wrong")
    assert not auth.token_eq("", "")          # 空判否
    assert not auth.token_eq(tok, "")
    assert not auth.token_eq("x", None)       # None 判否（daemon 未签发时）
    assert not auth.token_eq(tok, "�" * 32)  # 非 ASCII 不炸，判否

    # 2. 未 set_token 的 bridge：/exec 应 403（fail-closed，self.token 为 None）
    b0 = ExtensionBridge(port=0)
    b0.set_exec_handler(lambda code: {"ok": True})
    await b0.start()
    s = await http_post(b0.port, "/exec",
                        {"Content-Type": "text/plain", "X-Nekoro-Token": tok}, "1+1")
    assert "403" in s, s
    await b0.stop()

    bridge = ExtensionBridge(port=0)
    bridge.set_token(tok)

    async def exec_handler(code):
        return {"ok": True, "echo": code}
    bridge.set_exec_handler(exec_handler)
    await bridge.start()
    port = bridge.port

    # 3. /exec 无令牌 → 403
    s = await http_post(port, "/exec", {"Content-Type": "text/plain"}, "1+1")
    assert "403" in s, s
    # 4. /exec 令牌错误 → 403
    s = await http_post(port, "/exec",
                        {"Content-Type": "text/plain", "X-Nekoro-Token": "bad"}, "1+1")
    assert "403" in s, s
    # 5. /exec 令牌正确 → 200
    s = await http_post(port, "/exec",
                        {"Content-Type": "text/plain", "X-Nekoro-Token": tok}, "1+1")
    assert "200" in s, s
    # 6. /raw 同样受令牌保护：无令牌 → 403，带令牌 → 200
    s = await http_post(port, "/raw", {"Content-Type": "text/plain"},
                        '{"method":"X","params":{}}')
    assert "403" in s, s
    s = await http_post(port, "/raw",
                        {"Content-Type": "text/plain", "X-Nekoro-Token": tok},
                        '{"method":"X","params":{}}')
    assert "200" in s, s
    # 7. /ping 免令牌 → 200
    s = await http_post(port, "/ping", {})
    assert "200" in s, s

    # 8. WS：网页 Origin → 403；空 / null Origin → 403
    for bad in ("https://evil.com", "", "null"):
        s = await ws_handshake(port, bad)
        assert "403" in s, (bad, s)
    # 9. WS：扩展 Origin → 101 Switching Protocols
    s = await ws_handshake(port, "chrome-extension://abcdefghabcdefghabcdefghabcdefgh")
    assert "101" in s, s

    await bridge.stop()
    print("ALL OK")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    finally:
        shutil.rmtree(os.environ["NEKORO_DATA_DIR"], ignore_errors=True)
