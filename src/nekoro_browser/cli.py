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


def _reload_ext() -> int:
    """--reload-ext：命扩展重载 service worker，拿干净状态（治 alive-stale）。
    据实返回退出码：无 daemon → 1（且不发 exec）；请求成功 → 0；失败 → 1。
    注意只治"SW 还在处理消息但状态腐坏"；truly-wedged（不处理消息）救不了，靠心跳唤醒。"""
    if not _alive():
        print("No daemon running.", file=sys.stderr)
        return 1
    r = _post("/exec", "await reload_extension()")
    if r.get("ok"):
        print("Extension reload requested.", file=sys.stderr)
        return 0
    print(f"reload failed: {r.get('error', '?')}", file=sys.stderr)
    return 1


def main():
    p = argparse.ArgumentParser(prog="nekoro-browser")
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
    p.add_argument("-c", "--exec", type=str, default=None)
    args = p.parse_args()

    if args.port is not None:
        global _EXPLICIT_PORT
        _EXPLICIT_PORT = args.port
        from . import lifecycle
        lifecycle.set_port(args.port)   # stop/restart 要打到同一个 daemon

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
        r = _post("/exec", args.exec)
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
            r = _post("/exec", code)
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
