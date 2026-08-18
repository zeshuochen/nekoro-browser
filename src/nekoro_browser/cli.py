"""cli.py — nekoro-browser CLI

用法:
    nekoro-browser             启动 daemon（前台）
    echo "code" | nekoro-browser  管道模式（需 daemon 已运行）
    nekoro-browser --doctor    诊断
    nekoro-browser --ensure    自愈式就绪检查（缺什么起什么）
    nekoro-browser --exec CODE   执行代码
"""

import argparse
import asyncio
import json
import logging
import os
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
_ALLOW_DOMAINS = None   # 域名白名单；None = 不限制（见 allowlist.py）

# 执行代码的 HTTP 超时。30 秒是原来的写死值，而"开页面 + 等水合"这种再正常
# 不过的流程就要几十秒——第一条真实测试命令就撞上了。给个宽松默认值 + --timeout。
DEFAULT_EXEC_TIMEOUT = 120.0
_EXEC_TIMEOUT = DEFAULT_EXEC_TIMEOUT


def _make_output_encoding_safe():
    """让输出在非 UTF-8 控制台上也不会把整条命令打断。

    Windows 的控制台代码页默认不是 UTF-8（英文机 cp1252、中文机 cp936），而本 CLI 的
    输出里有 `→` 和中文。**输出被重定向时** Python 按 locale 编码写，撞上编不了的字符
    就 UnicodeEncodeError —— 而「用管道抓输出」正是 agent 调这个 CLI 的常规姿势
    （`nekoro-browser --ensure` 的结果本来就是给程序读的）。本机 ACP=65001 所以永远
    撞不到，是 CI 的 cp1252 runner 把它照出来的。

    重定向时钉 UTF-8：读的是程序，解得开。真控制台保持原编码（Windows 控制台走
    WriteConsoleW，本来就显示得了这些字符），只加 errors=replace 兜底。
    两种情况都宁可字符降级，也不让一条诊断命令死在编码上。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            # getattr 而非直接调：sys.stdout 的静态类型是 TextIO，没有 reconfigure
            # （那是 TextIOWrapper 的方法）。mcp_server.py 里也是这么绕的。
            kw = {} if stream.isatty() else {"encoding": "utf-8"}
            getattr(stream, "reconfigure")(errors="replace", **kw)
        except (AttributeError, OSError, ValueError):
            pass      # 测试里换成了 StringIO、或早期 Python：不值得为这个失败


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


def _post(path, data="", timeout: float = 30):
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
    d = Daemon(port=port, allow_domains=_ALLOW_DOMAINS)
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


# ── --ensure：自愈式就绪检查 ────────────────────────────────────────────────
# 等待预算。冷启动（刚拉起 Chrome）要给 service worker 连上来的时间，热路径不必。
# 模块级常量而非命令行参数：这是内部节奏，不是用户要调的东西（测试会改小它们）。
ENSURE_CHROME_WAIT = 20.0
ENSURE_DAEMON_WAIT = 25.0
ENSURE_DAEMON_GRACE = 4.0        # 存量进程还没开始服务时，判它"僵尸"之前给的宽限
ENSURE_EXT_WAIT = 10.0
ENSURE_EXT_WAIT_COLD = 45.0
ENSURE_RELOAD_WAIT = 20.0


def _wait(pred, timeout: float, interval: float = 0.5, note: str = "") -> bool:
    """轮询 pred 直到为真或超时。至少探一次（timeout=0 也会探）。

    超过 15 秒就周期性报还要等多久（同 `_poll_running_daemon`）：全部修复路径叠满
    能跑到三分钟，中间一声不吭的话，跑 `--ensure` 的人分不清是在等还是卡死了。
    """
    deadline = time.monotonic() + timeout
    next_note = time.monotonic() + 15
    while True:
        if pred():
            return True
        if time.monotonic() >= deadline:
            return False
        if note and time.monotonic() >= next_note:
            print(f"       …{note} ({int(deadline - time.monotonic())}s left)")
            next_note = time.monotonic() + 15
        time.sleep(interval)


def _step(tag: str, name: str, msg: str):
    print(f"[{tag:<4}] {name:<12}: {msg}")


def chrome_path():
    """Chrome 可执行文件路径，找不到返回 None（不猜一个不存在的路径）。"""
    import shutil
    from pathlib import Path
    cands = []
    if sys.platform == "win32":
        cands = [Path(os.environ[v]) / "Google" / "Chrome" / "Application" / "chrome.exe"
                 for v in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")
                 if os.environ.get(v)]
    elif sys.platform == "darwin":
        cands = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for c in cands:
        if c.is_file():
            return c
    for n in ("google-chrome", "google-chrome-stable", "chrome", "chromium"):
        w = shutil.which(n)
        if w:
            return Path(w)
    return None


def _chrome_profile_dir(exe=None):
    """默认 profile 目录，不存在则 None。

    显式传一个**不存在**的 --user-data-dir 会让 Chrome 新建一个空 profile ——
    登录态全丢。所以只在目录确实存在时才传；否则省掉这个参数，Chrome 自己会用默认的。

    POSIX 上还要看**将要启动的是哪个浏览器**：Chromium 与 Google Chrome 的 profile
    不通用，把 `~/.config/google-chrome` 传给 chromium 轻则被拒、重则跨渠道写坏
    用户真正的 Chrome profile。
    """
    from pathlib import Path
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        p = Path(base) / "Google" / "Chrome" / "User Data" if base else None
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    elif exe is not None and "chromium" in Path(exe).name.lower():
        p = Path.home() / ".config" / "chromium"
    else:
        p = Path.home() / ".config" / "google-chrome"
    return p if p is not None and p.is_dir() else None


def _chrome_proc_pattern():
    """认「Chrome 进程」用的名字/模式，跟着 chrome_path() 实际解析到的可执行文件走。

    写死 "chrome" 是错的：`chromium` 不含子串 `chrome`，只装了 Chromium 的机器上
    永远扫不到自己刚拉起的浏览器 → 每次跑都 FAIL，还每次多开一个窗口。
    """
    exe = chrome_path()
    if sys.platform == "win32":
        return exe.name if exe is not None else "chrome.exe"
    if sys.platform == "darwin":
        return "Chromium" if exe is not None and "chromium" in exe.name.lower() \
            else "Google Chrome"
    # POSIX：pgrep -f 吃 ERE，匹配的是整条命令行。锚到路径分隔符/行首与词尾，
    # 否则 chromedriver、开着 chrome.md 的编辑器、别的 agent 命令行里那句
    # --load-extension 都算「Chrome 在跑」，于是该拉起的时候不拉起。
    # google-chrome 的实际进程 cmdline 是 /opt/google/chrome/chrome，
    # 所以两种写法都要覆盖，不能只匹配 which 到的那个名字。
    stem = "chromium" if exe is not None and "chromium" in exe.name.lower() \
        else "chrom(e|ium)"
    return f"(^|/){stem}( |$)"


def _chrome_running() -> bool:
    """Chrome 进程在不在。

    扩展只在 Chrome 里活着：Chrome 没开时 daemon 起得来、扩展永远连不上，
    报错却指向扩展（实测踩过的坑）。探测失败一律当作"没在跑"——
    多开一个窗口无害（Chrome 会转交给已有实例）。

    **这个判断只用来决定要不要拉起浏览器，不参与最终就绪结论**：扩展的一次真实 CDP
    往返是比进程扫描强得多的证据（见 `_ensure`）。误报只会浪费一次启动，不会误判就绪。
    """
    import subprocess
    pat = _chrome_proc_pattern()
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {pat}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=15).stdout
            return pat.lower() in (out or "").lower()
        return subprocess.run(["pgrep", "-f", pat], capture_output=True,
                              timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _launch_chrome(ext_dir=None) -> str | None:
    """拉起 Chrome（默认 profile，保留登录态）。detached，不阻塞。
    成功返回 None，失败返回原因字符串。

    **扩展不是这里装上去的**：Chrome 137 起 `--load-extension` 被停用，实测 151
    上连一个最小合法 MV3 扩展也不会被加载（干净 profile 里只剩内置扩展）。
    扩展靠的是它已经以「加载已解压」的方式常驻在 profile 里 —— 所以即使
    `ext_dir` 是 None，把 Chrome 拉起来仍然是对的修复，别因为没有扩展目录就不启动。
    旧版 Chrome / Chromium 上这个开关还有效，有目录就顺手带上，没有也不影响。
    """
    from . import lifecycle
    exe = chrome_path()
    if exe is None:
        return "chrome executable not found"
    args = [str(exe)]
    if ext_dir is not None:
        args.append(f"--load-extension={ext_dir}")
    prof = _chrome_profile_dir(exe)
    if prof is not None:
        args.append(f"--user-data-dir={prof}")
    try:
        lifecycle.spawn_detached(args)
    except OSError as e:
        return str(e)
    return None


def _ensure_chrome():
    """返回 (进程扫到了吗, 是否是我们刚拉起来的)。

    第一个返回值**不进最终结论**（见 `_ensure`）：扩展答得出 CDP 往返就证明 Chrome 在跑，
    比进程扫描强。所以这里失败一律记 WARN 不记 FAIL——否则「扫不到但扩展通了」会打出
    自相矛盾的成绩单（FAIL Chrome + PASS Extension + Not ready），把好好的环境判死。
    """
    if _chrome_running():
        _step("PASS", "Chrome", "running")
        return True, False
    # 扩展目录缺席也照样启动：扩展是从 profile 里加载的，不是命令行带进去的
    # （见 _launch_chrome）。为此不启动等于白白少修一件能修的事。
    err = _launch_chrome(extension_dir())
    if err:
        _step("WARN", "Chrome", f"not detected; launch failed: {err}")
        return False, False
    if not _wait(_chrome_running, ENSURE_CHROME_WAIT, note="waiting for Chrome"):
        _step("WARN", "Chrome", "launched but no matching process appeared")
        return False, True
    _step("FIX", "Chrome", "was not running → launched (default profile)")
    return True, True


def _port_in_use(port=None) -> bool:
    """端口上有没有人占着。**用 bind 试，不用 connect。**

    这比「/ping 有没有 200」强：在忙的 daemon（一条 exec 就能占住事件循环好几分钟，
    默认超时 120 秒）ping 不应答，但端口确实还占着。分不清这两者就会往一个活得
    好好的 daemon 上再叠一个。

    connect 式探测在这里会**反过来失灵**：内核把连接直接塞进 accept 队列，应用根本
    没 accept 也算连接成功；队列一满，connect 就失败，于是「有人占着」被读成
    「没人占」。而队列填满恰恰发生在我们最需要认出它的那两种状态——wedged 和阻塞在
    长 exec 的 daemon 都不 accept。实测（backlog=5，从不 accept）第 6 次探测就翻车；
    真 daemon 的 backlog 是 100，一次 --ensure 要吃掉十几个槽位，**连跑七八次这道闸门
    就整个失效**，重新退回「两个 daemon 抢一个端口」。

    探测的地址与 daemon 完全一致（`127.0.0.1`，AF_INET）：判据必须是「daemon 能不能
    bind 上去」，别改成绑通配地址——那样别人占着 0.0.0.0 时会误判，而 daemon 其实
    绑得上环回。

    **SO_REUSEADDR 按平台分：POSIX 设、Windows 不设。** 两边语义相反：
    - POSIX：`Connection: close` 让 daemon 每次都先关连接，于是 28417 上会留下一串
      TIME_WAIT。裸 bind 撞上它们直接 EADDRINUSE —— 于是 `--stop` 之后最长一分钟内，
      明明没人监听也被判成「占着」，`--ensure` 拒绝启动，而 daemon 自己（`bridge` 传了
      `reuse_address=True`）本来是起得来的。设了 SO_REUSEADDR 就跳过这些尸体，
      同时**仍然盖不过活着的 LISTEN**（那要 SO_REUSEPORT），所以判真占用者照样准。
    - Windows：TIME_WAIT 不挡裸 bind（实测），而设了 SO_REUSEADDR 反倒能**抢占**别人
      正在监听的端口 → 占用者被读成不存在。所以这边必须素身。
      （SO_EXCLUSIVEADDRUSE 对结果没有影响：实测带不带都正确判出 WSAEADDRINUSE。
      它保护的是「自己长期持有的 socket 不被别人抢」，而探测用完立刻就关。）

    bind 失败一律按「占着」处理（EADDRINUSE / EACCES 都轮不到我们启动 daemon；
    其它异常宁可拦下，也好过往一个可能活着的 daemon 上再叠一个）。
    """
    import socket
    s = None
    try:
        s = socket.socket()
        if sys.platform != "win32":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", config.client_port(port)))
        return False
    except OSError:
        return True
    finally:
        if s is not None:
            s.close()


def _port_bind_denied(port=None) -> bool:
    """端口不是被人占着，而是**这台机器不让绑**（保留段 / 特权端口）。

    Windows 上 `netsh int ipv4 show excludedportrange` 里的段（Hyper-V、Docker 会动态
    占走一批）、POSIX 上非 root 绑 <1024，都会给 EACCES 而不是 EADDRINUSE。这时叫人
    「去找占着端口的进程」是白费力气——根本没有那个进程，换端口才是出路。
    """
    import errno
    import socket
    s = None
    try:
        s = socket.socket()
        if sys.platform != "win32":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", config.client_port(port)))
        return False
    except OSError as e:
        return e.errno == errno.EACCES
    finally:
        if s is not None:
            s.close()


def _pid_file_is_ours(port=None) -> bool:
    """pid/端口/令牌文件都是单槽的。端口文件记着别的端口时，那份 pid 文件属于
    另一个 daemon —— 与这次 --ensure 无关，绝不能去清它、更不能把它的 pid 报成
    「占着这个端口的那个」（清了就把人家的生命周期机制卸了，而人家还在跑）。

    端口文件缺失 = **不知道**那份 pid 属于谁，一律按「不是我的」处理。
    这种状态是真会出现的：`Daemon.stop()` 无条件清端口文件，所以 daemon B 退出
    就把 daemon A 写的那份抹掉了 —— 此时在默认端口上跑 --ensure，会去删还在跑的
    A 的 pid 文件。不清的代价则几乎为零：端口空着照样 spawn，新 daemon 起来会把
    pid 文件重写一遍，清理本来就只是顺手。
    """
    recorded = config.read_port_file()
    if recorded is None:
        return False
    return recorded == config.client_port(port)


def _ensure_daemon(port=None) -> bool:
    from . import lifecycle
    # lifecycle.URL 是导入时算好的，而 _alive()/_port_in_use() 每次现算。端口文件
    # 在这中间被改写（别的 daemon 起停都会重写它）时，identify() 问的就是另一个
    # 端口上的 daemon —— 拿它的 pid 去说「占着我这个端口」又是一次张冠李戴。
    lifecycle.set_port(config.client_port(port))
    if _alive():
        _step("PASS", "Daemon", f"running ({_url()})")
        return True
    if _port_bind_denied(port):
        # 不是被占，是这台机器不让绑。拒绝启动是对的（daemon 自己也绑不上），
        # 但绝不能让人去找一个根本不存在的占用者。
        _step("FAIL", "Daemon", f"the OS refuses to bind port "
                                f"{config.client_port(port)} (reserved or privileged)")
        print("       → 没有占用者可找。换个端口："
              "nekoro-browser --ensure --port <N>（扩展选项页里要改成同一个）")
        return False
    if _port_in_use(port):
        # 有人监听但不应答。可能正在启动、可能正忙着跑一条长 exec、也可能真 wedged——
        # **从外面分不清**。而猜错的代价严重不对称：猜"死了"就去 spawn，结果是两个
        # daemon 抢同一个端口（Windows 的 SO_REUSEADDR 允许第二个绑上去，不会报错），
        # 新 daemon 还会轮换共享令牌，把原来那个打成 403 —— 正是这条命令要防的事。
        # 所以只等，不动手；等不到就据实报，把处置权交回去。
        # 占着端口的到底是不是「我们这个端口的 daemon」，决定了等多久、以及怎么措辞。
        # identify() 是问 /pid，天然按端口走（--port 会经 set_port 改写 lifecycle.URL），
        # 可信；pid 文件那条是 advisory 且不认端口，只有确认属于本端口时才敢用。
        verified = lifecycle.identify()          # 它自己应答的，可信
        held = verified
        if held is None and _pid_file_is_ours(port):
            held = lifecycle.existing_daemon_pid()   # advisory，只够用来决定等多久
        # 认得出是自家 daemon 时给足耐心：它多半只是在跑一条长 exec（默认超时 120 秒），
        # 这时「有个健康 daemon 在」的证据最强，4 秒就判死是本末倒置。
        budget = ENSURE_DAEMON_WAIT if held is not None else ENSURE_DAEMON_GRACE
        if _wait(_alive, budget, note="waiting for the daemon to answer"):
            # 「还在启动」和「刚才忙着」两种都会走到这里，措辞别把话说死
            _step("PASS", "Daemon", f"running ({_url()}, was busy or still starting)")
            return True
        # 措辞要跟证据强度对齐：identify() 是那个进程自己应答的 /pid，可以点名；
        # pid 文件只是块石头上的记号（不认端口、不防 pid 复用），只能存疑地提一句。
        if verified is not None:
            who = f"nekoro daemon (pid {verified}) "
        elif held is not None:
            who = f"something (pid file says {held}, unverified) "
        else:
            who = "something "
        _step("FAIL", "Daemon", f"{who}holds {_url()} but isn't answering "
                                f"— refusing to start a second daemon on the same port")
        if verified is not None:
            print("       → 多半是它正忙着跑一条长 exec：等它跑完再来一次。"
                  "确认是真僵了再 nekoro-browser --stop")
        else:
            # 认不出身份时别乱指方向：--stop 在这个状态下只会回一句「No daemon running.」，
            # 把人堵死。据实说清占着端口的可能不是 nekoro，并给出真的走得通的那条路。
            print("       → 刚跑过 --stop/--restart 的话，等一两秒再试一次"
                  "（socket 释放有延迟）。")
            print(f"         否则占用者未必是 nekoro，也可能是彻底僵死的 daemon："
                  f"查一下谁占着 {config.client_port(port)} 并处理掉，"
                  f"或者换个端口 nekoro-browser --ensure --port <N>"
                  f"（扩展选项页里要改成同一个）")
        return False
    # 端口没人监听 = 没有活着的 daemon。这时 pid 文件必然是陈的，可以清——
    # 但只清属于这个端口的那份。
    stale = lifecycle.existing_daemon_pid() if _pid_file_is_ours(port) else None
    if stale is not None:
        lifecycle.cleanup_pid()
    try:
        # _ALLOW_DOMAINS 必须显式带上：子进程读不到父进程的模块全局，漏传就是
        # `--allow-domains` 被静默丢掉、起一个不设防的 daemon 却报绿。
        pid = lifecycle.spawn_daemon(port, _ALLOW_DOMAINS)
    except OSError as e:
        _step("FAIL", "Daemon", f"spawn failed: {e}")
        return False
    if not _wait(_alive, ENSURE_DAEMON_WAIT, note="waiting for the daemon"):
        _step("FAIL", "Daemon", f"spawned (pid {pid}) but not serving {_url()} — "
                                f"see {lifecycle.daemon_log_path()}")
        return False
    # 措辞按实际发生的说：那个 pid 早就不在了（端口无人监听是前提），
    # 我们清掉的只是它留下的文件，没杀任何东西。
    cleaned = f"cleared stale pid file (pid {stale}), " if stale is not None else ""
    # 报**在服务的那个** pid，不报 Popen 拿到的：venv 里的 python.exe 常是个
    # trampoline，它 spawn 出真解释器后自己留在中间，两个 pid 不是一个进程。
    # 打印的 pid 要是能拿去 kill 的那个。
    served = lifecycle.identify() or pid
    _step("FIX", "Daemon", f"was down → {cleaned}started detached "
                           f"(pid {served}, {_url()})")
    return True


def _ext_hint():
    print(f"       → chrome://extensions：确认扩展已加载且已启用"
          f"（未打包扩展会被 Chrome 更新后自动停用）\n"
          f"       → 扩展目录：{extension_dir() or '未找到'}；"
          f"端口两侧要一致（扩展选项页）")


def _ensure_extension(port=None, cold=False) -> bool:
    budget = ENSURE_EXT_WAIT_COLD if cold else ENSURE_EXT_WAIT
    if _wait(lambda: _healthy(timeout=5), budget, note="waiting for the extension"):
        _step("PASS", "Extension/SW", "responding")
        return True
    why = ""
    if _alive():
        r = _post("/exec", "await reload_extension()", timeout=ENSURE_RELOAD_WAIT)
        err = r.get("error", "") if not r.get("ok") else ""
        # 只认 _post 为 403 造的那句原话，不做宽泛的 "token" 子串匹配——
        # daemon 侧任何提到 token 的 traceback 都会被误判成令牌问题。
        if "bad/missing token" in err:
            # 令牌对不上 = daemon 侧的事，扩展是无辜的。多半是又起了一个 daemon
            # 把共享令牌轮换了。这里指错方向的代价很实在：人会跑去 chrome://extensions
            # 反复重载一个根本没坏的扩展。--doctor 有这条分支，ensure 不能比它还差。
            _step("FAIL", "Daemon", f"token mismatch: {err}")
            print("       → 多半是有第二个 daemon 轮换了共享令牌。"
                  "先 nekoro-browser --stop，再重跑 --ensure")
            return False
        if err:
            why = f"; reload failed: {err}"
        elif _wait(lambda: _healthy(timeout=5), ENSURE_RELOAD_WAIT,
                   note="waiting for the reloaded worker"):
            _step("FIX", "Extension/SW", "was not responding → reloaded service worker")
            return True
    # daemon 等不到扩展会自己退出：start() 先等 10s auto-attach，再 send_control 撞上
    # bridge 自己的 10s ready_timeout 才抛 RuntimeError ——**约 20 秒**，比热路径的
    # 探活预算长。所以它可能死在上面任何一步之后，这个检查必须放在最后而不是最前。
    if not _alive():
        if not _ensure_daemon(port):
            _step("SKIP", "Extension/SW", "daemon gone and could not be restarted")
            return False
        if _wait(lambda: _healthy(timeout=5), budget, note="waiting for the extension"):
            _step("FIX", "Extension/SW", "responding after daemon respawn")
            return True
    _step("FAIL", "Extension/SW", f"still not responding{why}")
    _ext_hint()
    return False


def _ensure(port=None) -> int:
    """--ensure：逐项检查、缺什么修什么，最后据实报状态。

    与 --doctor 的分工：doctor 只诊断不动手；ensure 会真的拉起 Chrome / daemon /
    重载扩展。修不好的照样报 FAIL + 人工提示，不假装绿。
    """
    print("nekoro-browser Ensure\n" + "=" * 46)
    if _alive() and _healthy(timeout=5):
        # 端到端已通 → Chrome、daemon、扩展三者必然都活着，无须逐项探（也省掉进程扫描）
        _step("PASS", "Chrome", "running (CDP round-trip ok)")
        _step("PASS", "Daemon", f"running ({_url()})")
        _step("PASS", "Extension/SW", "responding")
        print("=" * 46 + "\n[PASS] Ready.")
        return 0
    chrome_ok, cold = _ensure_chrome()
    daemon_ok = _ensure_daemon(port)
    if daemon_ok:
        ext_ok = _ensure_extension(port, cold=cold)
    else:
        ext_ok = False
        _step("SKIP", "Extension/SW", "no daemon to ask")
    print("=" * 46)
    # 结论只看 daemon + 扩展：扩展答得出一次真实 CDP 往返，就已经证明 Chrome 在跑，
    # 而且是比进程扫描更强的证据。把进程扫描算进结论只会制造假红——扫不到的原因
    # 可能只是 tasklist 被拦、或那台机器上浏览器叫 chromium。
    if daemon_ok and ext_ok:
        if not chrome_ok:
            print("       (Chrome 进程没扫到，但扩展答了 CDP 往返 → 它确实在跑)")
        print("[PASS] Ready.")
        return 0
    print("[FAIL] Not ready — see the failing line above.")
    return 1


def main():
    _make_output_encoding_safe()
    p = argparse.ArgumentParser(prog="nekoro-browser")
    p.add_argument("command", nargs="?", choices=["setup"], default=None,
                   help="setup：引导式安装（给出扩展目录、打开 chrome://extensions、"
                        "等扩展连上并当场确认）")
    p.add_argument("--version", action="version", version=f"nekoro-browser {__version__}")
    p.add_argument("--doctor", action="store_true", help="纯诊断，不动手")
    p.add_argument("--ensure", action="store_true",
                   help="自愈式就绪检查：Chrome 没开就带扩展拉起、daemon 没跑就后台起、"
                        "扩展没响应就重载。全绿退出码 0，修不好非 0")
    p.add_argument("--stop", action="store_true", help="停止正在运行的 daemon")
    p.add_argument("--restart", action="store_true", help="停止后重启（前台）")
    p.add_argument("--reload-ext", action="store_true",
                   help="重载扩展 service worker（自愈，治 alive-stale；跑任务前刷干净）")
    p.add_argument("--extension-path", action="store_true",
                   help="打印 Chrome 扩展目录（chrome://extensions 里「加载已解压的扩展」选它）")
    p.add_argument("--port", type=int, default=None,
                   help=f"daemon 端口（默认 {config.DEFAULT_PORT}；也可设环境变量 "
                        f"{config.ENV_VAR}）。扩展侧的端口在扩展选项页里改")
    p.add_argument("--allow-domains", type=str, default=None,
                   metavar="LIST",
                   help="daemon 只允许操作这些域，逗号分隔（如 'jd.com,*.taobao.com'）。"
                        "不传则不限制；也可用 NEKORO_ALLOW_DOMAINS 环境变量")
    p.add_argument("--timeout", type=float, default=None,
                   help=f"执行代码的超时秒数（默认 {DEFAULT_EXEC_TIMEOUT:.0f}）。"
                        "等待页面加载/水合的脚本可能需要更长")
    p.add_argument("-c", "--exec", type=str, default=None)
    args = p.parse_args()

    if args.timeout is not None and args.timeout > 0:
        global _EXEC_TIMEOUT
        _EXEC_TIMEOUT = args.timeout

    if args.allow_domains:
        from . import allowlist
        global _ALLOW_DOMAINS
        _ALLOW_DOMAINS = allowlist.parse(args.allow_domains)

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

    if args.ensure:
        sys.exit(_ensure(args.port))
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
    d = Daemon(port=port, allow_domains=_ALLOW_DOMAINS)
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
