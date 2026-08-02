"""cli.py — nekoro-browser CLI

用法:
    nekoro-browser             启动 daemon（前台）
    echo "code" | nekoro-browser  管道模式（需 daemon 已运行）
    nekoro-browser --doctor    诊断
    nekoro-browser --exec CODE   执行代码
"""

import argparse
import asyncio
import json
import logging
import sys
import time
import urllib.request
import urllib.error

from . import __version__
from . import auth
from . import config

# 显式 --port 的记忆位。None = 每次现算（读 NEKORO_PORT / daemon 写的端口文件），
# 这样 MCP server 先起、daemon 后起也能连上，不会锁死在导入时的那个值。
_EXPLICIT_PORT = None

# 执行代码的 HTTP 超时。30 秒是原来的写死值，而"开页面 + 等水合"这种再正常
# 不过的流程就要几十秒——第一条真实测试命令就撞上了。给个宽松默认值 + --timeout。
DEFAULT_EXEC_TIMEOUT = 120.0
_EXEC_TIMEOUT = DEFAULT_EXEC_TIMEOUT


def _url() -> str:
    return config.client_url(_EXPLICIT_PORT)

# 空代理 opener：系统/env 代理会拦 127.0.0.1 返 502，误判 daemon 死。localhost 直连。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _alive():
    try:
        with _OPENER.open(f"{_url()}/ping", timeout=2) as r:
            return r.status == 200
    except: return False


def _healthy(timeout=8):
    """端到端探活：一次真实 CDP 往返（page_info）。仅 /ping 200 不够——
    僵尸 daemon 端口还占着、ping 照样过，但扩展/SW 已死、CDP 往返失败。"""
    r = _post("/exec", "await page_info()", timeout=timeout)
    return bool(r.get("ok") and (r.get("result") or {}).get("url"))


def _post(path, data="", timeout=30):
    try:
        req = urllib.request.Request(
            f"{_url()}{path}", data=data.encode(), method="POST",
            headers={"Content-Type": "text/plain",
                     "X-Nekoro-Token": auth.read_token()})
        with _OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read()) if r.status == 200 else {"ok": False, "error": f"HTTP {r.status}"}
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return {"ok": False, "error": "Forbidden: bad/missing token (restart daemon?)"}
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"Daemon not running: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def extension_dir():
    """扩展目录的落地路径。

    从 PyPI 装的 wheel 里扩展在包内（`nekoro_browser/extension`）；
    从仓库 `pip install -e .` 装的则在仓库根（`src/nekoro_browser` 上两级）。
    两处都找不到返回 None——据实说没有，不猜一个不存在的路径给用户。
    """
    from pathlib import Path
    here = Path(__file__).resolve().parent
    for cand in (here / "extension", here.parent.parent / "extension"):
        if (cand / "manifest.json").is_file():
            return cand
    return None


def _copy_to_clipboard(text: str) -> bool:
    """尽力而为地复制到剪贴板。没有可用工具就返回 False，不报错——
    路径同时也打印在屏幕上，复制只是省一次手动选中。"""
    import subprocess
    if sys.platform == "win32":
        cands = [["clip"]]
    elif sys.platform == "darwin":
        cands = [["pbcopy"]]
    else:
        cands = [["wl-copy"], ["xclip", "-selection", "clipboard"],
                 ["xsel", "--clipboard", "--input"]]
    for cmd in cands:
        try:
            subprocess.run(cmd, input=text.encode(), timeout=5, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


async def _wait_for_extension(port, timeout: float = 180.0) -> bool:
    """临时起一个 bridge 等扩展连上来——扩展只有在有人监听时才连得上，
    所以「装完了没」这件事没法离线判断，必须真的监听一次。"""
    from .daemon import Daemon
    d = Daemon(port=port)
    await d.bridge.start()
    d.bridge.set_token(auth.issue_token())
    try:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            if await d.bridge.wait_for_extension(min(15.0, left)):
                return True
            print(f"      …still waiting ({int(deadline - time.monotonic())}s left)",
                  file=sys.stderr)
    finally:
        await d.bridge.stop()


def _poll_running_daemon(timeout: float = 180.0) -> bool:
    """端口已被现成的 daemon 占着时的等待路径。

    这时不能自己 bind 去等，只能反复问那个 daemon「扩展活了没」。
    必须轮询而不是探一次就走——用户正要去 Chrome 里加载扩展，一次性检查
    必然失败，等于没等（这是 setup 第一版的真实 bug）。
    """
    deadline = time.monotonic() + timeout
    next_note = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _healthy(timeout=5):
            return True
        if time.monotonic() >= next_note:
            print(f"      …still waiting ({int(deadline - time.monotonic())}s left)",
                  file=sys.stderr)
            next_note = time.monotonic() + 15
        time.sleep(2)
    return False


def _setup(port=None) -> int:
    """引导式安装：路径给到手 + 说清要点哪里 + 实时确认扩展是否连上。

    扩展这一步没法自动化——Chrome 既不提供把未打包扩展装进正在运行浏览器的接口，
    也不接受命令行传来的 chrome:// URL。能做的是把人工动作压到「粘路径、点一下」，
    其余全自动，并且**真的等到**扩展连上再下结论。
    """
    import platform
    print("nekoro-browser setup\n" + "=" * 46, file=sys.stderr)
    print(f"[1/4] Python {platform.python_version()} · "
          f"nekoro-browser {__version__} installed", file=sys.stderr)

    ext = extension_dir()
    if ext is None:
        print("[2/4] FAILED: extension directory not found in this install.\n"
              "      Reinstall from a clone: pip install -e .", file=sys.stderr)
        return 1
    copied = _copy_to_clipboard(str(ext))
    print(f"[2/4] Extension directory{' (copied to clipboard)' if copied else ''}:\n"
          f"      {ext}", file=sys.stderr)

    # 不自动开页面：Chrome 会拒绝命令行传来的 chrome:// URL，转给已运行实例后
    # 只会开出一个空白新标签——既没帮上忙，还得谎称"Opened"。直接说清要做什么。
    print("[3/4] In Chrome, open  chrome://extensions/\n"
          "      → turn on \"Developer mode\"  → \"Load unpacked\"  "
          "→ pick the directory above", file=sys.stderr)

    print("[4/4] Waiting for the extension to connect "
          f"(port {config.client_port(port)}, Ctrl-C to skip)…", file=sys.stderr)
    try:
        if _alive():
            # 已有 daemon 占着端口，不能再 bind；改成反复问它扩展活没活
            print("      (a daemon is already running — polling it)", file=sys.stderr)
            ok = _poll_running_daemon()
        else:
            ok = asyncio.run(_wait_for_extension(port))
    except KeyboardInterrupt:
        print("\n      Skipped.", file=sys.stderr)
        return 1

    print("=" * 46, file=sys.stderr)
    if ok:
        print("Extension connected. Setup complete.\n"
              "  1. Start the daemon:  nekoro-browser\n"
              "     It runs in the foreground — leave that terminal open.\n"
              "  2. From another terminal:  echo \"page_info()\" | nekoro-browser",
              file=sys.stderr)
        return 0
    print("Extension did not connect.\n"
          "  - Is it loaded AND enabled in chrome://extensions/ ?\n"
          "  - Unpacked extensions get disabled by Chrome after updates — re-enable it.\n"
          "  - If you changed the port, set the same one in the extension's options page.\n"
          "  Then re-run: nekoro-browser setup", file=sys.stderr)
    return 1


def _reload_ext() -> int:
    """--reload-ext：命扩展重载 service worker，拿干净状态（治 alive-stale）。
    据实返回退出码：无 daemon → 1（且不发 exec）；请求成功 → 0；失败 → 1。
    注意只治"SW 还在处理消息但状态腐坏"；truly-wedged（不处理消息）救不了，靠心跳唤醒。"""
    if not _alive():
        print("No daemon running.", file=sys.stderr)
        return 1
    r = _post("/exec", "await reload_extension()", timeout=_EXEC_TIMEOUT)
    if r.get("ok"):
        print("Extension reload requested.", file=sys.stderr)
        return 0
    print(f"reload failed: {r.get('error', '?')}", file=sys.stderr)
    return 1


def main():
    p = argparse.ArgumentParser(prog="nekoro-browser")
    p.add_argument("command", nargs="?", choices=["setup"], default=None,
                   help="setup：引导式安装（给出扩展目录、打开 chrome://extensions、"
                        "等扩展连上并当场确认）")
    p.add_argument("--version", action="version", version=f"nekoro-browser {__version__}")
    p.add_argument("--doctor", action="store_true")
    p.add_argument("--stop", action="store_true", help="停止正在运行的 daemon")
    p.add_argument("--restart", action="store_true", help="停止后重启（前台）")
    p.add_argument("--reload-ext", action="store_true",
                   help="重载扩展 service worker（自愈，治 alive-stale；跑任务前刷干净）")
    p.add_argument("--extension-path", action="store_true",
                   help="打印 Chrome 扩展目录（chrome://extensions 里「加载已解压的扩展」选它）")
    p.add_argument("--port", type=int, default=None,
                   help=f"daemon 端口（默认 {config.DEFAULT_PORT}；也可设环境变量 "
                        f"{config.ENV_VAR}）。扩展侧的端口在扩展选项页里改")
    p.add_argument("--timeout", type=float, default=None,
                   help=f"执行代码的超时秒数（默认 {DEFAULT_EXEC_TIMEOUT:.0f}）。"
                        "等待页面加载/水合的脚本可能需要更长")
    p.add_argument("-c", "--exec", type=str, default=None)
    args = p.parse_args()

    if args.timeout is not None and args.timeout > 0:
        global _EXEC_TIMEOUT
        _EXEC_TIMEOUT = args.timeout

    if args.port is not None:
        global _EXPLICIT_PORT
        _EXPLICIT_PORT = args.port
        from . import lifecycle
        lifecycle.set_port(args.port)   # stop/restart 要打到同一个 daemon

    if args.command == "setup":
        sys.exit(_setup(args.port))

    if args.extension_path:
        d = extension_dir()
        if d is None:
            print("Extension directory not found in this install.", file=sys.stderr)
            sys.exit(1)
        print(d)
        return

    if args.reload_ext:
        sys.exit(_reload_ext())

    if args.stop:
        from . import lifecycle
        if not _alive():
            print("No daemon running.", file=sys.stderr); return
        lifecycle.stop_daemon()
        for _ in range(15):                  # 复核最多 3s
            if not _alive(): break
            time.sleep(0.2)
        if _alive():
            # 据实上报，不谎报成功。多半是 pre-#10 daemon（无 /shutdown 路由、无 pid 文件），
            # 优雅停对它无效、又没安全 handle 可杀 → 让用户手动处理。
            print("Warning: daemon still responding after stop request.\n"
                  "  (likely a pre-#10 daemon without /shutdown; kill its PID manually)",
                  file=sys.stderr)
        else:
            print("Daemon stopped.", file=sys.stderr)
        return
    if args.restart:
        from . import lifecycle
        lifecycle.stop_daemon()
        for _ in range(25):                  # 最多 5s 等端口释放
            if not _alive(): break
            time.sleep(0.2)
        # 不 return：下方 pipe 分支被 `not args.restart` 跳过，直落 daemon 前台启动

    if args.doctor:
        _doctor(); return
    if args.exec:
        r = _post("/exec", args.exec, timeout=_EXEC_TIMEOUT)
        sys.stdout.write(json.dumps(r, default=str) + "\n")
        if not r.get("ok"):
            sys.exit(1)
        return

    # Pipe mode（--restart 强制走 daemon 启动，跳过管道）
    if not args.restart and not sys.stdin.isatty():
        code = sys.stdin.read().strip()
        if code:
            if not _alive():
                sys.stderr.write("Daemon not running. Start: nekoro-browser\n"); sys.exit(1)
            r = _post("/exec", code, timeout=_EXEC_TIMEOUT)
            sys.stdout.write(json.dumps(r, default=str) + "\n")
            if not r.get("ok"):
                sys.exit(1)
        return

    # Daemon mode
    print(f"nekoro-browser v{__version__}", file=sys.stderr)
    asyncio.run(_run(args.port))


async def _run(port=None):
    from .daemon import Daemon
    # 端口已占 = 多半已有 daemon 在跑。友好提示而非抛 bind 栈。
    from . import lifecycle
    if _alive():
        if _healthy():
            print("Daemon already running (healthy) on 127.0.0.1:28417.\n"
                  "Use it (echo ... | nekoro-browser), or restart: nekoro-browser --restart",
                  file=sys.stderr)
            sys.exit(1)
        # 端口占着但 CDP 不通 = 僵尸。自动清掉再启，免用户手动 taskkill。
        print("Stale daemon detected (port held, not serving). Cleaning up...",
              file=sys.stderr)
        lifecycle.stop_daemon()
        for _ in range(25):
            if not _alive(): break
            time.sleep(0.2)
    d = Daemon(port=port)
    try:
        ok = await d.start()
        if not ok:
            print("ERROR: Extension not connected", file=sys.stderr); sys.exit(1)
        print(f"Ready on 127.0.0.1:{d.port}. "
              f"Pipe: echo 'page_info()' | nekoro-browser", file=sys.stderr)
        await d.wait_forever()
    except RuntimeError as e:
        # 扩展没装/没启用时 start() 里 auto_attach 会抛 "extension not connected (WS)"。
        # 不兜住的话用户看到的是一坨 traceback，还得自己猜是扩展的问题。
        print(f"ERROR: {e}\n"
              "  扩展没连上。检查 chrome://extensions：扩展是否已加载并处于启用状态\n"
              "  （未打包扩展会被 Chrome 更新/重启后自动停用）。\n"
              f"  扩展目录：{extension_dir() or '未找到'}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        # 端口竞态：_alive 之后、bind 之前被别人占了
        print(f"Cannot start daemon (port in use?): {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await d.stop()


def _doctor():
    print("nekoro-browser Doctor\n" + "=" * 40)
    import platform
    print(f"[PASS] Python 3.12+ : v{platform.python_version()}")
    if not _alive():
        print(f"[INFO] Daemon       : not running on {_url()} (start: nekoro-browser)")
        print("=" * 40); return
    print(f"[PASS] Daemon       : running ({_url()})")
    # 端对端探活：一次真实 CDP 往返，证明扩展 + Service Worker 都活着，
    # 而不只是 Python 进程在。SW 被 Chrome 回收时这步会失败。
    r = _post("/exec", "await page_info()", timeout=8)
    info = (r.get("result") or {}) if r.get("ok") else {}
    url = info.get("url", "")
    if r.get("ok") and url:
        print(f"[PASS] Extension/SW : responding ({url})")
    elif "token" in r.get("error", "").lower():
        print(f"[FAIL] Token        : {r['error']}")
    else:
        # ok=True 但 url 空 = get_page_info 吞了异常（不能只信 ok，否则误报 PASS）；
        # 或往返直接失败。两者都说明 SW/扩展没响应。
        why = r.get("error") or "no response within 8s (SW asleep / extension not connected)"
        print(f"[FAIL] Extension/SW : {why}")
        print("        → 检查 chrome://extensions，或重开普通网页后重启 daemon")
    print("=" * 40)


if __name__ == "__main__":
    main()
