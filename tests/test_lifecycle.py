"""test_lifecycle.py — daemon 生命周期（pid 文件 / 指纹 / identify / 停 / 端点）。无需浏览器。"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import lifecycle


def test_start_time_and_alive():
    cur = os.getpid()
    s1 = lifecycle._process_start_time(cur)
    assert s1 is not None, "当前进程应有启动时间"
    assert lifecycle._process_start_time(cur) == s1, "同进程两次读应一致"
    dead = 2_000_000_000                      # 几乎不可能存在的 pid
    assert lifecycle._process_start_time(dead) is None, "死 pid 应 None"
    assert lifecycle._process_start_time(-1) is None
    assert lifecycle._pid_alive(cur) is True
    assert lifecycle._pid_alive(dead) is False
    assert lifecycle._pid_alive(0) is False


def test_pid_file_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        os.environ["LOCALAPPDATA"] = td            # token_path/pid_path 用它
        assert lifecycle.read_pid_file() is None    # 未写
        lifecycle.write_pid()
        assert lifecycle.read_pid_file() == os.getpid()
        lifecycle.cleanup_pid()
        assert lifecycle.read_pid_file() is None    # 清掉


class _PidHandler(BaseHTTPRequestHandler):
    pid_value: object = 4242               # 类变量，测试可改（故意塞非 int）
    def log_message(self, format: str, *args: object):   # 静音
        pass
    def do_GET(self):
        if self.path == "/pid":
            body = json.dumps({"pid": self.pid_value}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        elif self.path == "/ping":
            self.send_response(200); self.send_header("Content-Length", "4"); self.end_headers()
            self.wfile.write(b"pong")
        else:
            self.send_response(404); self.end_headers()


def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_identify():
    srv, url = _serve(_PidHandler)
    old = lifecycle.URL
    try:
        lifecycle.URL = url
        _PidHandler.pid_value = 4242
        assert lifecycle.identify() == 4242
        _PidHandler.pid_value = -1                 # 非法 pid
        assert lifecycle.identify() is None
        _PidHandler.pid_value = "nope"             # 非 int
        assert lifecycle.identify() is None
        _PidHandler.pid_value = 2 ** 31            # 越上界（os.kill 拒）
        assert lifecycle.identify() is None
        _PidHandler.pid_value = True               # bool 不是合法 pid（type(True) is int → False）
        assert lifecycle.identify() is None
    finally:
        lifecycle.URL = old; srv.shutdown()
    _PidHandler.pid_value = 4242


def test_stop_daemon_no_daemon():
    # 指向死端口 + 落一个死 pid 的 pid 文件 → stop 只清文件、不杀任何东西
    with tempfile.TemporaryDirectory() as td:
        os.environ["LOCALAPPDATA"] = td
        lifecycle.write_pid()                       # 建目录，随后覆盖成死 pid
        lifecycle.pid_path().write_text("2000000000", encoding="utf-8")
        old = lifecycle.URL
        try:
            lifecycle.URL = "http://127.0.0.1:1"    # 无人监听
            lifecycle.stop_daemon()
        finally:
            lifecycle.URL = old
        assert lifecycle.read_pid_file() is None, "stop 应清掉 pid 文件"


def test_bridge_pid_and_shutdown():
    from nekoro_browser.bridge import ExtensionBridge
    # 空代理 opener：系统代理会拦 127.0.0.1 返 502，测试直连。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _get(url):
        with opener.open(url, timeout=3) as r:
            return r.status, r.read()

    def _post(url, token=None):
        h = {"X-Nekoro-Token": token} if token else {}
        req = urllib.request.Request(url, data=b"", method="POST", headers=h)
        with opener.open(req, timeout=3) as r:
            return r.status

    async def run():
        b = ExtensionBridge(port=0)
        b.set_token("secrettoken")
        await b.start()
        base = f"http://127.0.0.1:{b.port}"
        # 阻塞 HTTP 必须丢到线程：opener.open 若在本 loop 里同步跑，会卡住服务端 → 死锁。
        # /pid 免令牌，返回本进程 pid
        status, body = await asyncio.to_thread(_get, f"{base}/pid")
        assert status == 200 and json.loads(body)["pid"] == os.getpid()
        # /shutdown 无令牌 → 403，且不置位
        try:
            await asyncio.to_thread(_post, f"{base}/shutdown", None)
            assert False, "无令牌应 403"
        except urllib.error.HTTPError as e:
            assert e.code == 403
        assert not b.shutdown_requested.is_set()
        # /shutdown 带令牌 → 200 且置位
        assert await asyncio.to_thread(_post, f"{base}/shutdown", "secrettoken") == 200
        assert b.shutdown_requested.is_set()
        await b.stop()

    asyncio.run(run())


if __name__ == "__main__":
    test_start_time_and_alive()
    test_pid_file_roundtrip()
    test_identify()
    test_stop_daemon_no_daemon()
    test_bridge_pid_and_shutdown()
    print("ALL OK")
