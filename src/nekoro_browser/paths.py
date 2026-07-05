"""paths.py — 用户私有数据目录集中。

所有落盘位置（令牌、pid 文件）从这里派生，避免各模块各自拼
`%LOCALAPPDATA%\\nekoro-browser`，也免 lifecycle 反向依赖 auth 只为拿父目录。

每次调用都重读环境变量（不缓存），测试可 monkeypatch `LOCALAPPDATA` 切目录。
"""

import os
from pathlib import Path


def data_dir() -> Path:
    """用户私有数据目录：Windows `%LOCALAPPDATA%` / POSIX `XDG_CONFIG_HOME` /
    退化到 `~`，其下的 `nekoro-browser`。"""
    base = (os.environ.get("LOCALAPPDATA")
            or os.environ.get("XDG_CONFIG_HOME")
            or str(Path.home()))
    return Path(base) / "nekoro-browser"


def token_path() -> Path:
    return data_dir() / "token"


def pid_path() -> Path:
    return data_dir() / "daemon.pid"
