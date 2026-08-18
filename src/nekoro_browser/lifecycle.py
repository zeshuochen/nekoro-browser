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
from typing import Any

from . import auth
from . import config
from . import paths

URL = config.client_url()        # 测试可 monkeypatch lifecycle.URL 指向本地测试服

LOG_MAX_BYTES = 1 << 20   # daemon.log 到 1MB 就重开，别无限长


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


def daemon_log_path() -> Path:
    """detached 起的 daemon 把 stdout/stderr 追加到这里。没有它，后台 daemon 起不来时
    屏幕上什么都没有，只剩一句"没在服务"。"""
    return paths.data_dir() / "daemon.log"


def spawn_detached(cmd, log_path=None) -> int:
    """起一个脱离当前终端的后台进程，返回子进程 pid。

    Windows: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP —— 不继承控制台，
    关掉发起它的终端也不会连带被杀。POSIX: start_new_session 同理。
    stdin 一律给 DEVNULL：后台进程没有可读的输入。

    日志是**起进程时**裁一次（超过 LOG_MAX_BYTES 就重开），不是持续封顶——
    一个长命 daemon 自己写多少没人拦。两个 daemon 共用同一个日志文件时，
    这一裁会连带丢掉对方还没落盘的那点输出（只发生在不同端口各起一个的情况）。
    「进程真的脱离了终端」这件事没法在单测里验（要另开会话/关终端才看得出来），
    这里如实写明，不假装有覆盖。
    """
    kw: dict[str, Any] = {"stdin": subprocess.DEVNULL, "close_fds": True}
    # 不继承调用方的工作目录：daemon 是长命进程，攥着别人的目录会在 Windows 上把它锁住。
    # 落到自己的数据目录里——但**必须先确保它存在**：全新安装、第一条命令就是 --ensure 时
    # 这个目录还没被建过，Popen 会直接抛 NotADirectoryError，Chrome 那步就以一句
    # 「launch failed: [WinError 267]」告吹（第二次跑又莫名其妙好了，因为 daemon 起过一次）。
    try:
        home = paths.data_dir()
        home.mkdir(parents=True, exist_ok=True)
        kw["cwd"] = str(home)
    except OSError:
        pass                     # 建不出来就不指定 cwd，别为了这点讲究把启动整个搞失败
    log = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.is_file() and log_path.stat().st_size > LOG_MAX_BYTES:
            log_path.write_bytes(b"")     # 只留新的：每次失败的冷启动都往里追加 traceback
        log = open(log_path, "ab")
        kw["stdout"] = kw["stderr"] = log
    else:
        kw["stdout"] = kw["stderr"] = subprocess.DEVNULL
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kw).pid
    finally:
        if log is not None:
            log.close()


def existing_daemon_pid():
    """存量 daemon 进程的 pid，确认没有则 None。**advisory，不是 kill-safe 的身份证明。**

    两条依据：先问 /pid（正在服务的那个，这条可信），再退回 pid 文件里那个仍活着的
    进程——后者覆盖「进程还在、HTTP 已不服务」的僵尸，spawn 前必须先认出它，
    否则新旧两个 daemon 抢同一个端口（实测踩过）。

    但第二条**没有指纹核验**：pid 号被别的进程复用时它会认错人。所以返回值只能用来
    回答「要不要现在就 spawn」，**绝不能拿去 kill**——真要杀走 stop_daemon()，
    那里有 identify() + _process_start_time 双重核验（见本模块开头）。
    """
    pid = identify()
    if pid is not None:
        return pid
    pid = read_pid_file()
    return pid if _pid_alive(pid) else None


def spawn_daemon(port=None, allow_domains=None) -> int:
    """detached 起一个 daemon，返回子进程 pid。

    调用方负责先确认没有存量实例（existing_daemon_pid()）——本函数只管起得干净。

    **端口取客户端口径**（`client_port`，会读端口文件）而不是原样透传：daemon 侧的
    `daemon_port()` 故意不读端口文件，父进程探活却读——上一个 daemon 死在非默认端口上、
    端口文件没来得及清时，两边会算出不同的端口，起了个自己都连不上的 daemon。

    `allow_domains` 必须显式带过去：它在父进程里是模块全局，子进程拿不到，
    漏传等于 `--allow-domains` 被静默丢掉、起一个完全不设防的 daemon。

    不走 `-m nekoro_browser.cli`：detached 进程的 stdin 不是 tty，CLI 会落进管道模式、
    读到空输入直接退出，daemon 根本起不来。直接调 cli._run()，绕开那层歧义。

    **前提是这个包装过**（uv tool / pip install -e .）。`-P` 与 `cwd=data_dir()` 都会
    切断「从源码树里直接跑」时那条靠 CWD 的隐式 import 路径，所以 `cd src &&
    python -m nekoro_browser.cli --ensure` 起不来 daemon —— 但它是**诚实地**失败：
    报「spawned but not serving」并指向 daemon.log，里面就是那条 ModuleNotFoundError。
    """
    resolved = config.client_port(port)
    code = ("import asyncio;from nekoro_browser import cli;"
            f"cli._ALLOW_DOMAINS={allow_domains!r};"
            f"asyncio.run(cli._run({resolved!r}))")
    # -P：不把工作目录塞进 sys.path[0]。少了它，从谁的目录里跑 --ensure，谁目录下的
    # nekoro_browser/ 或 config.py 就会被这个长命 daemon 抢先 import（在 src/ 下跑
    # 就是静默用了源码树而不是装好的包）。
    return spawn_detached([sys.executable, "-P", "-c", code], daemon_log_path())


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
