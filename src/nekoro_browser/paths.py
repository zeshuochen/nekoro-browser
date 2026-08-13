"""paths.py — 用户私有数据目录集中。

所有落盘位置（令牌、pid 文件）从这里派生，避免各模块各自拼
`%LOCALAPPDATA%\\nekoro-browser`，也免 lifecycle 反向依赖 auth 只为拿父目录。

每次调用都重读环境变量（不缓存），测试可 monkeypatch `NEKORO_DATA_DIR` 切目录。
"""

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """用户私有数据目录，其下的 `nekoro-browser`：

    - `NEKORO_DATA_DIR` —— 跨平台显式覆盖，优先于一切（测试与自定义部署用）
    - Windows: `%LOCALAPPDATA%`，缺失退到 `~`
    - macOS: `~/Library/Application Support`
    - 其余 POSIX: `XDG_CONFIG_HOME`，**未设时按 XDG 规范退到 `~/.config`**（不是 `~`）

    早先的实现三平台共用一条 or 链，`LOCALAPPDATA` → `XDG_CONFIG_HOME` → `~`。
    两个环境变量在 mac 上通常都没有，结果落在家目录下一个可见的 `~/nekoro-browser`；
    Linux 上多数发行版也不设 `XDG_CONFIG_HOME`，同样落到 `~` 而非规范要求的 `~/.config`。
    """
    override = os.environ.get("NEKORO_DATA_DIR")
    if override:
        return Path(override) / "nekoro-browser"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "nekoro-browser"


def token_path() -> Path:
    return data_dir() / "token"


def pid_path() -> Path:
    return data_dir() / "daemon.pid"


def port_path() -> Path:
    """daemon 实际监听的端口。daemon 启动时写、停止时清。

    存在的意义：daemon 用 `--port` 起在非默认端口后，后续的
    `echo ... | nekoro-browser` 不用再重复传参也能找到它。
    """
    return data_dir() / "port"
