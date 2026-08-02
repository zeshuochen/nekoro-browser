"""lifecycle.py — daemon 进程生命周期：pid 文件 + 身份指纹 + 安全清僵尸。

移植 browser-harness B 树（4d75f11）admin.py:_process_start_time / restart_daemon
+ _ipc.identify 的概念，适配 nekoro 的 HTTP 传输（socket IPC → HTTP /pid /shutdown）。

僵尸场景（本项目实测坑）：旧 daemon 进程占着 28417、/ping 仍 200，但扩展/SW 已死，
CDP 往返失败 → CLI 既不能用、又启动不了（bind 撞 port in use）。此模块提供自报身份、
指纹防误杀、优雅停 + 兜底 kill、清 pid 文件。
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import auth
from . import config
from . import paths

URL = config.client_url()        # 测试可 monkeypatch lifecycle.URL 指向本地测试服


def set_port(port) -> None:
    """CLI 传了 --port 时改写本模块的目标地址（stop/restart 要打到对的 daemon）。"""
    global URL
    URL = config.client_url(port)

# 显式空代理 opener：系统/env 代理（如 wandayun）会拦截 127.0.0.1 并返 502，
# 把健康的本地 daemon 误判成僵尸。localhost 一律直连、绕过任何代理。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def pid_path() -> Path:
    """pid 文件路径（与令牌同目录，都派生自 paths.data_dir()）。"""
    return paths.pid_path()


def write_pid():
    p = pid_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()), encoding="utf-8")


def read_pid_file():
    try:
        return int(pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def cleanup_pid():
    try:
        os.unlink(pid_path())
    except FileNotFoundError:
        pass


def _process_start_time(pid):
    """进程启动时间指纹，或 None。两次读得同一非 None 值 = 仍是同一进程；
    值变了 = pid 被复用。restart 用它在 IPC 已拆、进程慢退时仍能安全兜底 kill，
    而不必回退到「盲信 pid 文件」（会重开 pid 复用漏洞）。

    Linux: /proc/<pid>/stat 字段 22。macOS: ps -o lstart=。
    Windows: GetProcessTimes via ctypes（FILETIME 创建时间，1601 起 100ns）。
    其它平台: None（restart 退化为严格 identify-only 检查，仍比无检查安全）。
    """
    if type(pid) is not int or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                raw = f.read().decode("ascii", errors="replace")
        except (FileNotFoundError, PermissionError, OSError):
            return None
        try:
            tail = raw[raw.rindex(")") + 2:].split()
            return tail[19]
        except (ValueError, IndexError):
            return None
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["ps", "-o", "lstart=", "-p", str(pid)],
                stderr=subprocess.DEVNULL, timeout=2,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        s = out.decode("ascii", errors="replace").strip()
        return s or None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
        except ImportError:
            return None
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
            ]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
        except (OSError, AttributeError):
            return None
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_ft = wintypes.FILETIME()
            kernel_ft = wintypes.FILETIME()
            user_ft = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                h, ctypes.byref(creation), ctypes.byref(exit_ft),
                ctypes.byref(kernel_ft), ctypes.byref(user_ft),
            )
            if not ok:
                return None
            return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        finally:
            kernel32.CloseHandle(h)
    return None


def _pid_alive(pid) -> bool:
    """跨平台存活探测。Windows 用 OpenProcess（_process_start_time 成功=活），
    绝不用 os.kill(pid,0)——Windows 上那会 TerminateProcess 杀掉目标。"""
    if type(pid) is not int or pid <= 0:
        return False
    if sys.platform == "win32":
        return _process_start_time(pid) is not None
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True          # 存在但无权限发信号，仍算活
    except OSError:
        return False


def _alive_ping(timeout=1.0) -> bool:
    try:
        with _OPENER.open(f"{URL}/ping", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def identify(timeout=2.0):
    """daemon 经 /pid 自报 pid；拿不到 / 非法 → None（防 stale 端口被别的进程占用误判）。"""
    try:
        with _OPENER.open(f"{URL}/pid", timeout=timeout) as r:
            if r.status != 200:
                return None
            pid = json.loads(r.read()).get("pid")
    except Exception:
        return None
    if type(pid) is not int or pid <= 0 or pid >= 2 ** 31:
        return None
    return pid


def _request_shutdown(timeout=5.0) -> bool:
    """请 daemon 优雅停（令牌保护）。成功与否都不抛。"""
    try:
        req = urllib.request.Request(
            f"{URL}/shutdown", data=b"", method="POST",
            headers={"X-Nekoro-Token": auth.read_token()})
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def stop_daemon():
    """优雅停 daemon 并清 pid 文件。身份经 identify() 核验后才兜底 kill，
    stale pid（号被别的进程复用）绝不被 SIGTERM。够不到就只清文件、不升级到 kill。"""
    daemon_pid = identify()
    alive = daemon_pid is not None or _alive_ping()
    # 先快照启动时间：IPC/端点可能在进程真退前就没了，identify 中途变 None 不等于进程已死；
    # kill 前用启动时间复核，既恢复慢退场景的兜底 kill，又不重开 pid 复用漏洞。
    daemon_start = _process_start_time(daemon_pid) if daemon_pid else None

    if alive:
        _request_shutdown()

    if daemon_pid is not None:
        for _ in range(75):              # 最长 ~15s 等优雅退出
            if not _pid_alive(daemon_pid):
                break
            time.sleep(0.2)
        else:
            # 仍活 → 复核身份再兜底 kill：identify 仍同 pid（IPC 活、进程 wedged），
            # 或启动时间指纹未变（同进程慢退，IPC 已没）。都不成立则 pid 可能被复用，跳过。
            same_process = identify() == daemon_pid or (
                daemon_start is not None
                and _process_start_time(daemon_pid) == daemon_start
            )
            if same_process:
                # POSIX：SIGTERM 优雅信号，daemon 可自清；Windows：os.kill 映射到
                # TerminateProcess 硬杀（不跑自身清理）——但进程已 wedged，且 pid 文件
                # 由下方 cleanup_pid() 统一清，无碍。
                try:
                    os.kill(daemon_pid, signal.SIGTERM)
                except (ProcessLookupError, OSError, SystemError, OverflowError):
                    pass

    cleanup_pid()
