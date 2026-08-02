"""test_setup.py — `nekoro-browser setup` 的可离线验证部分。无需浏览器。

真正的"等扩展连上"要有 Chrome 参与，这里覆盖的是它周围会静默坏掉的部分：
参数解析、扩展目录缺失时的退出码、剪贴板在无头环境失败时不能崩、
不许谎称打开了扩展页、以及两条等待路径（自己 bind / 轮询现成 daemon）
都必须真的重试而不是探一次就走。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import cli
from nekoro_browser.bridge import ExtensionBridge


def _run(fn, *a, **kw):
    return asyncio.run(fn(*a, **kw))


def test_setup_parses_as_positional_without_breaking_flags():
    p = cli.argparse.ArgumentParser(prog="nekoro-browser")
    # 复刻 main() 里的两个关键参数，确认 `setup` 与 `--port` 能共存
    p.add_argument("command", nargs="?", choices=["setup"], default=None)
    p.add_argument("--port", type=int, default=None)
    assert p.parse_args(["setup"]).command == "setup"
    assert p.parse_args([]).command is None                      # 管道模式不受影响
    ns = p.parse_args(["setup", "--port", "30500"])
    assert ns.command == "setup" and ns.port == 30500


def test_setup_fails_cleanly_without_extension_dir():
    real_dir, real_copy, real_alive = (cli.extension_dir, cli._copy_to_clipboard, cli._alive)
    try:
        cli.extension_dir = lambda: None
        called = []
        cli._copy_to_clipboard = lambda t: called.append("copy") or True
        cli._alive = lambda: False
        assert cli._setup() == 1
        # 扩展目录都没有就该立刻停，不该继续复制路径、更不该傻等 180 秒
        assert called == []
    finally:
        (cli.extension_dir, cli._copy_to_clipboard, cli._alive) = (
            real_dir, real_copy, real_alive)


def test_clipboard_never_raises():
    """剪贴板工具在无头环境（CI、服务器）里必然缺席，只能返回 False，不能抛。"""
    assert isinstance(cli._copy_to_clipboard("nekoro-browser-selftest"), bool)


def test_never_claims_it_opened_the_extensions_page():
    """不许声称『已打开 chrome://extensions』——Chrome 会拒收命令行传来的
    chrome:// URL，只开出一个空白新标签，那句话就是谎报（第一版的真实 bug）。
    断言实际输出而不是源码文本：注释里出现这个词不该让测试变红。"""
    import contextlib
    import io
    real = (cli.extension_dir, cli._copy_to_clipboard, cli._alive, cli._poll_running_daemon)
    try:
        cli.extension_dir = lambda: __import__("pathlib").Path(".")
        cli._copy_to_clipboard = lambda t: True
        cli._alive = lambda: True
        cli._poll_running_daemon = lambda *a, **k: True
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cli._setup()
        out = buf.getvalue()
    finally:
        (cli.extension_dir, cli._copy_to_clipboard, cli._alive,
         cli._poll_running_daemon) = real
    assert "chrome://extensions/" in out          # 该告诉用户去哪
    assert "Opened" not in out                    # 但别说是自己打开的
    assert not hasattr(cli, "_open_extensions_page")


def test_bridge_wait_for_extension_timeout_and_success():
    async def timeout_case():
        b = ExtensionBridge(port=0)
        await b.start()
        try:
            return await b.wait_for_extension(0.2)      # 没有扩展 → False
        finally:
            await b.stop()

    async def success_case():
        b = ExtensionBridge(port=0)
        await b.start()
        try:
            # 模拟扩展 WS 接入：_ws_ready 是 setup 判定"连上了"的唯一依据
            b._ws_ready.set()
            return await b.wait_for_extension(0.2)
        finally:
            await b.stop()

    assert _run(timeout_case) is False
    assert _run(success_case) is True


def test_setup_polls_running_daemon_instead_of_stealing_port():
    """已有 daemon 在跑时：不能再 bind 端口，且必须**轮询**而不是探一次就走。

    第一版就是探一次——用户正要去 Chrome 里加载扩展，那一次必然失败，
    于是 setup 秒退并报『扩展没连上』，等于完全没等。
    """
    real = (cli.extension_dir, cli._copy_to_clipboard, cli._alive,
            cli._healthy, cli._poll_running_daemon)
    try:
        cli.extension_dir = lambda: __import__("pathlib").Path(".")
        cli._copy_to_clipboard = lambda t: False
        cli._alive = lambda: True
        used = []
        cli._poll_running_daemon = lambda *a, **k: used.append(1) or True
        assert cli._setup() == 0
        assert used == [1], "已有 daemon 时必须走轮询分支"
    finally:
        (cli.extension_dir, cli._copy_to_clipboard, cli._alive,
         cli._healthy, cli._poll_running_daemon) = real


def test_poll_running_daemon_keeps_retrying():
    """扩展是在轮询过程中才被加载的——第一次探活失败不能就此放弃。"""
    real_healthy = cli._healthy
    try:
        calls = []
        cli._healthy = lambda timeout=8: (calls.append(1), len(calls) >= 3)[1]
        assert cli._poll_running_daemon(timeout=30) is True
        assert len(calls) == 3, calls          # 前两次失败后仍在重试
    finally:
        cli._healthy = real_healthy


def test_poll_running_daemon_gives_up_at_timeout():
    real_healthy = cli._healthy
    try:
        cli._healthy = lambda timeout=8: False
        assert cli._poll_running_daemon(timeout=0.1) is False   # 不会无限等
    finally:
        cli._healthy = real_healthy


if __name__ == "__main__":
    test_setup_parses_as_positional_without_breaking_flags()
    test_setup_fails_cleanly_without_extension_dir()
    test_clipboard_never_raises()
    test_never_claims_it_opened_the_extensions_page()
    test_bridge_wait_for_extension_timeout_and_success()
    test_setup_polls_running_daemon_instead_of_stealing_port()
    test_poll_running_daemon_keeps_retrying()
    test_poll_running_daemon_gives_up_at_timeout()
    print("ALL OK")
