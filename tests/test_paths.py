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
    with tempfile.TemporaryDirectory() as td, _env(LOCALAPPDATA=td):
        base = Path(td) / "nekoro-browser"
        assert paths.data_dir() == base
        assert paths.token_path() == base / "token"
        assert paths.pid_path() == base / "daemon.pid"


def test_localappdata_wins_over_xdg():
    # 两者都设时，LOCALAPPDATA 该赢（锁 or-链顺序）
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
            _env(LOCALAPPDATA=a, XDG_CONFIG_HOME=b):
        assert paths.data_dir() == Path(a) / "nekoro-browser"


def test_auth_lifecycle_delegate():
    with tempfile.TemporaryDirectory() as td, _env(LOCALAPPDATA=td):
        # auth / lifecycle 都从 paths 派生，token 与 pid 同目录
        assert auth.token_path() == paths.token_path()
        assert lifecycle.pid_path() == paths.pid_path()
        assert lifecycle.pid_path().parent == auth.token_path().parent


def test_xdg_fallback():
    # 无 LOCALAPPDATA → 退到 XDG_CONFIG_HOME
    with tempfile.TemporaryDirectory() as td, _env(LOCALAPPDATA=None, XDG_CONFIG_HOME=td):
        assert paths.data_dir() == Path(td) / "nekoro-browser"


if __name__ == "__main__":
    test_data_dir_and_files()
    test_localappdata_wins_over_xdg()
    test_auth_lifecycle_delegate()
    test_xdg_fallback()
    print("ALL OK")
