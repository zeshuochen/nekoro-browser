"""test_paths.py — 数据目录集中 + auth/lifecycle 派生一致。无需浏览器。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import paths, auth, lifecycle


import contextlib


@contextlib.contextmanager
def _env(**kv):
    """临时设/删环境变量，退出还原（None 表示删），防泄漏到别的测试。"""
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_data_dir_and_files():
    with tempfile.TemporaryDirectory() as td, _env(NEKORO_DATA_DIR=td):
        base = Path(td) / "nekoro-browser"
        assert paths.data_dir() == base
        assert paths.token_path() == base / "token"
        assert paths.pid_path() == base / "daemon.pid"


def test_override_wins_over_platform_vars():
    # NEKORO_DATA_DIR 是跨平台显式覆盖，压过任何平台变量
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
            _env(NEKORO_DATA_DIR=a, LOCALAPPDATA=b, XDG_CONFIG_HOME=b):
        assert paths.data_dir() == Path(a) / "nekoro-browser"


def test_auth_lifecycle_delegate():
    with tempfile.TemporaryDirectory() as td, _env(NEKORO_DATA_DIR=td):
        # auth / lifecycle 都从 paths 派生，token 与 pid 同目录
        assert auth.token_path() == paths.token_path()
        assert lifecycle.pid_path() == paths.pid_path()
        assert lifecycle.pid_path().parent == auth.token_path().parent


def test_platform_default_without_override():
    """没有覆盖时，每个平台落在自己的惯例位置——**且都不是裸家目录**。

    旧实现在 mac 和「没设 XDG_CONFIG_HOME 的 Linux」上都会掉进 `~/nekoro-browser`，
    这个用例就是钉死那条回归。
    """
    home = Path.home()
    with tempfile.TemporaryDirectory() as td, \
            _env(NEKORO_DATA_DIR=None, LOCALAPPDATA=None, XDG_CONFIG_HOME=None):
        got = paths.data_dir()
        assert got.name == "nekoro-browser"
        if sys.platform == "win32":
            # Windows 上 LOCALAPPDATA 实际总是有的，这里被显式清掉才退到 ~
            assert got == home / "nekoro-browser"
        elif sys.platform == "darwin":
            assert got == home / "Library" / "Application Support" / "nekoro-browser"
        else:
            assert got == home / ".config" / "nekoro-browser"

        # 平台变量该只在自己的平台上生效
        with _env(XDG_CONFIG_HOME=td):
            xdg = paths.data_dir()
        with _env(LOCALAPPDATA=td):
            laa = paths.data_dir()
        tmp_based = Path(td) / "nekoro-browser"
        assert (xdg == tmp_based) is (sys.platform not in ("win32", "darwin"))
        assert (laa == tmp_based) is (sys.platform == "win32")


if __name__ == "__main__":
    test_data_dir_and_files()
    test_override_wins_over_platform_vars()
    test_auth_lifecycle_delegate()
    test_platform_default_without_override()
    print("ALL OK")
