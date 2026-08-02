"""test_setup.py — `nekoro-browser setup` 的可离线验证部分。无需浏览器。

真正的"等扩展连上"要有 Chrome 参与，这里覆盖的是它周围会静默坏掉的部分：
参数解析、扩展目录缺失时的退出码、副作用（剪贴板/开页面）失败时不能崩、
以及 bridge 的等待接口在超时/连上两种情况下的返回值。
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
    real_dir, real_copy, real_open, real_alive = (
        cli.extension_dir, cli._copy_to_clipboard, cli._open_extensions_page, cli._alive)
    try:
        cli.extension_dir = lambda: None
        called = []
        cli._copy_to_clipboard = lambda t: called.append("copy") or True
        cli._open_extensions_page = lambda: called.append("open") or True
        cli._alive = lambda: False
        assert cli._setup() == 1
        # 扩展目录都没有就该立刻停，不该继续去开浏览器、更不该傻等 180 秒
        assert called == []
    finally:
        (cli.extension_dir, cli._copy_to_clipboard,
         cli._open_extensions_page, cli._alive) = (real_dir, real_copy, real_open, real_alive)


def test_side_effects_never_raise():
    """剪贴板/浏览器在无头环境（CI、服务器）里必然失败，只能返回 False，不能抛。"""
    assert _copy_result_is_bool()
    assert isinstance(cli._open_extensions_page(), bool)


def _copy_result_is_bool():
    r = cli._copy_to_clipboard("nekoro-browser-selftest")
    return isinstance(r, bool)


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


def test_setup_uses_running_daemon_instead_of_stealing_port():
    """已有 daemon 在跑时，setup 必须走它的健康检查，绝不能再 bind 一次端口。"""
    real = (cli.extension_dir, cli._copy_to_clipboard, cli._open_extensions_page,
            cli._alive, cli._healthy)
    try:
        cli.extension_dir = lambda: __import__("pathlib").Path(".")
        cli._copy_to_clipboard = lambda t: False
        cli._open_extensions_page = lambda: False
        cli._alive = lambda: True
        cli._healthy = lambda: True
        # 走到这里若去 bind 端口就会实际起服务；返回 0 说明用的是 _healthy 分支
        assert cli._setup() == 0
        cli._healthy = lambda: False
        assert cli._setup() == 1
    finally:
        (cli.extension_dir, cli._copy_to_clipboard, cli._open_extensions_page,
         cli._alive, cli._healthy) = real


if __name__ == "__main__":
    test_setup_parses_as_positional_without_breaking_flags()
    test_setup_fails_cleanly_without_extension_dir()
    test_side_effects_never_raise()
    test_bridge_wait_for_extension_timeout_and_success()
    test_setup_uses_running_daemon_instead_of_stealing_port()
    print("ALL OK")
