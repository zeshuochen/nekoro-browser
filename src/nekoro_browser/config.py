"""config.py — 端口解析。

端口原先硬编码在四个 Python 位置加 background.js 里，被占用就只能改源码。
这里给出通行的三层覆盖，daemon 与客户端的优先级略有不同：

  daemon  ：--port  >  NEKORO_PORT  >  28417
  客户端  ：--port  >  NEKORO_PORT  >  运行时端口文件  >  28417

客户端多一层「端口文件」：daemon 起在非默认端口后会把实际端口写进
`data_dir()/port`，这样后续 `echo ... | nekoro-browser` 不必重复传参。
文件由 daemon 写、停止时清；读不到就退回默认值，不报错。

扩展侧读不到环境变量，端口存在 `chrome.storage.local.nekoroPort`，
用扩展的选项页改（chrome://extensions → 扩展详情 → 扩展程序选项）。
"""

import os

from . import paths

DEFAULT_PORT = 28417  # 非标准/不常见端口，避开与 @jackwener/opencli（同类工具，19825）撞车
ENV_VAR = "NEKORO_PORT"


def _valid(port) -> int | None:
    """端口必须是 1-65535 的整数；0 只在测试里由代码直接传，不从配置来。"""
    try:
        p = int(port)
    except (TypeError, ValueError):
        return None
    return p if 1 <= p <= 65535 else None


def env_port() -> int | None:
    raw = os.environ.get(ENV_VAR)
    return _valid(raw) if raw not in (None, "") else None


def read_port_file() -> int | None:
    try:
        return _valid(paths.port_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_port_file(port: int) -> None:
    p = paths.port_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(port), encoding="utf-8")


def clear_port_file() -> None:
    try:
        paths.port_path().unlink()
    except OSError:
        pass


def daemon_port(explicit=None) -> int:
    """daemon 该监听哪个端口。不看端口文件——它自己就是写那个文件的人。"""
    return _valid(explicit) or env_port() or DEFAULT_PORT


def client_port(explicit=None) -> int:
    """客户端（CLI / MCP）该连哪个端口。"""
    return _valid(explicit) or env_port() or read_port_file() or DEFAULT_PORT


def client_url(explicit=None) -> str:
    return f"http://127.0.0.1:{client_port(explicit)}"
