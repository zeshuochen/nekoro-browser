"""test_port_config.py — 端口解析优先级 + daemon 端口文件。无需浏览器。

daemon ：--port > NEKORO_PORT > 默认
客户端 ：--port > NEKORO_PORT > daemon 写的端口文件 > 默认
"""
import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import config, paths


@contextlib.contextmanager
def _env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@contextlib.contextmanager
def _tmp_data_dir():
    with tempfile.TemporaryDirectory() as td, _env(NEKORO_DATA_DIR=td, NEKORO_PORT=None):
        yield td


def test_default_when_nothing_set():
    with _tmp_data_dir():
        assert config.daemon_port() == config.DEFAULT_PORT == 28417
        assert config.client_port() == 28417
        assert config.client_url() == "http://127.0.0.1:28417"


def test_priority_order():
    with _tmp_data_dir():
        config.write_port_file(30003)
        with _env(NEKORO_PORT="30002"):
            # 显式参数最大
            assert config.client_port(30001) == 30001
            assert config.daemon_port(30001) == 30001
            # 其次环境变量
            assert config.client_port() == 30002
            assert config.daemon_port() == 30002
        # 只剩端口文件：客户端认，daemon 不认（daemon 自己才是写文件的人）
        assert config.client_port() == 30003
        assert config.daemon_port() == config.DEFAULT_PORT


def test_invalid_values_fall_through():
    with _tmp_data_dir():
        for bad in ("abc", "0", "-1", "70000", "", "  "):
            with _env(NEKORO_PORT=bad):
                assert config.client_port() == config.DEFAULT_PORT, bad
                assert config.daemon_port() == config.DEFAULT_PORT, bad
        # 显式参数非法时也退回下一层，不能崩
        assert config.client_port("nope") == config.DEFAULT_PORT
        assert config.daemon_port(0) == config.DEFAULT_PORT


def test_port_file_roundtrip_and_cleanup():
    with _tmp_data_dir():
        assert config.read_port_file() is None          # 没写过
        config.write_port_file(31111)
        assert paths.port_path().is_file()
        assert config.read_port_file() == 31111
        config.clear_port_file()
        assert config.read_port_file() is None
        config.clear_port_file()                        # 重复清理不该抛
        # 文件内容损坏时当作没有，而不是崩在启动路径上
        paths.port_path().write_text("garbage", encoding="utf-8")
        assert config.read_port_file() is None
        assert config.client_port() == config.DEFAULT_PORT


def test_cli_and_lifecycle_follow_port():
    from nekoro_browser import cli, lifecycle
    with _tmp_data_dir():
        with _env(NEKORO_PORT="30777"):
            assert cli._url() == "http://127.0.0.1:30777"
        old_url = lifecycle.URL
        try:
            lifecycle.set_port(30888)
            assert lifecycle.URL == "http://127.0.0.1:30888"
        finally:
            lifecycle.URL = old_url


def test_extension_default_matches_python():
    """扩展和 Python 的默认端口必须同一个数，否则开箱即连不上。"""
    import pathlib
    import re
    js = (pathlib.Path(__file__).parent.parent / "extension" / "background.js").read_text(
        encoding="utf-8")
    m = re.search(r"const DEFAULT_PORT = (\d+);", js)
    assert m, "background.js 里找不到 DEFAULT_PORT"
    assert int(m.group(1)) == config.DEFAULT_PORT

    opts = (pathlib.Path(__file__).parent.parent / "extension" / "options.js").read_text(
        encoding="utf-8")
    m2 = re.search(r"const DEFAULT_PORT = (\d+);", opts)
    assert m2 and int(m2.group(1)) == config.DEFAULT_PORT, "options.js 默认端口不一致"


if __name__ == "__main__":
    test_default_when_nothing_set()
    test_priority_order()
    test_invalid_values_fall_through()
    test_port_file_roundtrip_and_cleanup()
    test_cli_and_lifecycle_follow_port()
    test_extension_default_matches_python()
    print("ALL OK")
