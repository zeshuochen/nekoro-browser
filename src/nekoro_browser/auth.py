"""auth.py — 本地传输的共享令牌

威胁：daemon 监听 127.0.0.1，任意本地进程都能连；/exec 在 daemon 里跑任意 Python，
等于本地 RCE，还能被网页 drive-by（fetch/WebSocket 到 localhost）。

两类客户端两种防线：
- CLI（一次性 HTTP /exec /raw）：带 X-Nekoro-Token 头，令牌存在用户私有目录的文件里。
  网页/远程读不到本地文件 → 无令牌 → 403。同用户恶意进程能读文件——但它本就已控你，
  与 harness 的 POSIX chmod600 同等边界。
- 扩展（WS /ws）：Chrome 自动带 Origin: chrome-extension://<id>，daemon 校验来源。
  网页发起的 ws 带自己域名的 Origin → 拒。（扩展读不到文件，故走 Origin 而非令牌。）

令牌文件放 paths.data_dir()/token —— Windows %LOCALAPPDATA%、macOS
~/Library/Application Support、其余 POSIX XDG_CONFIG_HOME 或 ~/.config，
可用 NEKORO_DATA_DIR 覆盖。POSIX 上 chmod 600；Windows 靠 profile 目录 ACL
（无第三方依赖，os.chmod 只能切只读位）。
"""

import hmac
import os
import secrets
from pathlib import Path

from . import paths


def token_path() -> Path:
    """令牌文件路径（派生自 paths.data_dir()）。"""
    return paths.token_path()


def issue_token() -> str:
    """生成新令牌并写入私有文件，返回令牌。"""
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_hex(16)
    # 原子地以 0600 新建，避免「先写后 chmod」的可读窗口（POSIX TOCTOU）。
    # mode 只在创建时生效，故先删旧文件防残留宽权限。Windows 忽略 mode，靠目录 ACL。
    try:
        p.unlink()
    except OSError:
        pass
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, tok.encode())
    finally:
        os.close(fd)
    return tok


def read_token() -> str:
    """读令牌文件；不存在返回空串。"""
    try:
        return token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def token_eq(a: str | None, b: str | None) -> bool:
    """恒定时间比较；任一为空判否（None 也算空——daemon 还没签发令牌时就是 None）。转 bytes 比较，避免非 ASCII 令牌头触发 TypeError。"""
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8", "replace"))
