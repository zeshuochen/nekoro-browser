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
import urllib.request
import urllib.error

from . import __version__

URL = "http://127.0.0.1:19825"


def _alive():
    try:
        with urllib.request.urlopen(f"{URL}/ping", timeout=2) as r:
            return r.status == 200
    except: return False


def _post(path, data="", timeout=30):
    try:
        req = urllib.request.Request(f"{URL}{path}", data=data.encode(),
                                     headers={"Content-Type": "text/plain"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()) if r.status == 200 else {"ok": False, "error": f"HTTP {r.status}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"Daemon not running: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    p = argparse.ArgumentParser(prog="nekoro-browser")
    p.add_argument("--version", action="version", version=f"nekoro-browser {__version__}")
    p.add_argument("--doctor", action="store_true")
    p.add_argument("-c", "--exec", type=str, default=None)
    args = p.parse_args()

    if args.doctor:
        _doctor(); return
    if args.exec:
        r = _post("/exec", args.exec); sys.stdout.write(json.dumps(r, default=str) + "\n"); return

    # Pipe mode
    if not sys.stdin.isatty():
        code = sys.stdin.read().strip()
        if code:
            if not _alive():
                sys.stderr.write("Daemon not running. Start: nekoro-browser\n"); sys.exit(1)
            r = _post("/exec", code); sys.stdout.write(json.dumps(r, default=str) + "\n")
        return

    # Daemon mode
    print(f"nekoro-browser v{__version__}", file=sys.stderr)
    asyncio.run(_run())


async def _run():
    from .daemon import Daemon
    d = Daemon()
    try:
        ok = await d.start()
        if not ok:
            print("ERROR: Extension not connected", file=sys.stderr); sys.exit(1)
        print("Ready. Pipe: echo 'page_info()' | nekoro-browser", file=sys.stderr)
        await d.wait_forever()
    except KeyboardInterrupt:
        pass
    finally:
        await d.stop()


def _doctor():
    print("nekoro-browser Doctor\n" + "=" * 40)
    import platform
    print(f"[PASS] Python 3.12+ : v{platform.python_version()}")
    try:
        import websockets; print(f"[PASS] websockets   : v{websockets.__version__}")
    except ImportError:
        print("[FAIL] websockets")
    if _alive():
        print(f"[PASS] Daemon       : running")
    else:
        print(f"[INFO] Daemon       : not running")
    print("=" * 40)


if __name__ == "__main__":
    main()
