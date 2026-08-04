"""helpers.py — 浏览器操作函数集

架构来源于 browser-harness (https://github.com/browser-use/browser-harness)
每个函数是 CDP 命令的薄封装，不超过 10 行。厚逻辑（站点工作流）放 domain-skills/。

Agent 运行时缺功能，直接编辑此文件添加。文件用前 reload，修改立即生效。
"""

import asyncio
import base64
import json
import logging
import math

from . import site_notes

logger = logging.getLogger(__name__)


def _decode_unserializable_js_value(value):
    """CDP 对 JSON 无法表示的返回值（Infinity/NaN/-0/BigInt）不给 value，只给
    unserializableValue 字符串。这里解回 Python 值（移植 browser-harness）。"""
    if value == "NaN":
        return math.nan
    if value == "Infinity":
        return math.inf
    if value == "-Infinity":
        return -math.inf
    if value == "-0":
        return -0.0
    if isinstance(value, str) and value.endswith("n"):   # BigInt 字面量 "123n"
        try:
            return int(value[:-1])
        except ValueError:
            return value
    return value


def _is_illegal_return_error(desc: str) -> bool:
    return "Illegal return statement" in (desc or "")


# ═══════════════════════════════════════════════════════════════════════════════
# Tab
# ═══════════════════════════════════════════════════════════════════════════════

# 托管组里的旧标签是资产不是垃圾：同一张标签，登录态和页面状态都还在，能直接接着用。
# 但 agent 不会主动 list_tabs() 去查——和 site_notes 那条同源的教训：约定不执行自己。
# 所以把「同站已经开着」这条信息挂到 new_tab 的返回值上，那正是即将造出重复标签的时刻。
_HINT_TABS = 3          # 提示里最多列几张，其余只报数量——给索引不给正文


def _tab_key(url: str) -> str:
    """判重键 = hostname + 首段 path。`/lesson` 与 `/practice` 算两处，够粗也够用。
    只认 http/https：about:blank 没 hostname，而 `chrome://newtab` 的 hostname 是
    'newtab'——不按 scheme 卡住就会把内部页当成正经站点参与判重。"""
    from urllib.parse import urlparse
    try:
        u = urlparse(url or "")
    except ValueError:
        return ""
    if u.scheme not in ("http", "https"):
        return ""
    host = (u.hostname or "").lower()
    if not host:
        return ""
    seg = [s for s in (u.path or "").split("/") if s]
    return f"{host}/{seg[0]}" if seg else host


async def _tabs_like(daemon, url: str, exclude=()) -> list[dict]:
    """托管组里与 url 同站同段的标签。附赠功能——任何异常都吞成空列表，绝不连累导航本身。"""
    key = _tab_key(url)
    if not key:
        return []
    try:
        r = await daemon.list_tabs()
        out = []
        for t in ((r or {}).get("tabs") or []):
            tid = t.get("tabId")
            if tid is None or tid in exclude:
                continue
            if _tab_key(t.get("url", "")) == key:
                out.append({"tabId": tid, "title": (t.get("title") or "")[:80]})
        return out
    except Exception:
        return []


async def new_tab(daemon, url: str = "about:blank", reuse: bool = False,
                  timeout: float = 15.0) -> dict:
    """new_tab("https://example.com") — 开新标签，加入 nekoro 托管组，切过去并 attach。
    走扩展 navigate action：chrome.tabs.create + 分组 + waitTabLoad 等**真实 load 事件**，
    尽量等到加载完（不像原来裸 Target.createTarget 直接返回、后续 wait 撞 about:blank
    stale-complete 竞态）；超时则 loaded=False。返回 {tabId, loaded}，之后 helper 直接
    作用于新标签。注意：load 等待上限由扩展内部硬编码（~10s），`timeout` 只界定这条 RPC
    的传输上限（取 max(timeout,10)+5，保证不早于扩展的 load 等待就超时），并不缩短扩展
    侧的 load 等待预算。

    `reuse=True` 时若托管组里已有同站标签，就切过去导航而不新开，返回值多 `reused: True`
    （那张标签 attach 不上——比如被 DevTools 占着——则如实回落到开新标签）。
    默认 False：函数叫 new_tab，默认开新才是诚实的。默认路径下若发现同站已有标签，
    返回值里带 `existing`（清单+提示），标签照开、行为不变，只是让 agent 知道有得复用。"""
    try:
        if reuse:
            for cand in await _tabs_like(daemon, url):
                sw = await switch_tab(daemon, cand["tabId"])
                if not sw.get("ok"):
                    continue                     # 这张 attach 不上，试下一张
                nav = await navigate(daemon, url, timeout=timeout)
                res = {"ok": bool(nav.get("ok")), "tabId": cand["tabId"], "reused": True,
                       "loaded": bool(nav.get("loaded"))}
                if not res["ok"]:
                    res["error"] = nav.get("error", "reuse: navigate failed")
                return site_notes.attach(res, url)
            # 一张都复用不上 → 照常开新标签，不硬报错

        r = await daemon.bridge.send_scripting({"action": "navigate", "url": url},
                                               max(timeout, 10) + 5)
        tab_id = (r or {}).get("tabId")
        if not tab_id:
            return {"ok": False, "error": "new_tab: no tabId from extension"}
        # 扩展 waitTabLoad 返回字符串 'complete'|'timeout'|'no-tab'，只有 complete 算真加载完
        loaded = ((r or {}).get("load") == "complete")
        await daemon.switch_tab(tab_id)          # attach debugger + 切活动指针
        res = {"ok": True, "tabId": tab_id, "loaded": loaded}
        if not reuse:
            others = await _tabs_like(daemon, url, exclude={tab_id})
            if others:
                res["existing"] = {
                    "hint": "同站已有标签；switch_tab(id) 可复用，或 new_tab(url, reuse=True)",
                    "tabs": others[:_HINT_TABS],
                }
                if len(others) > _HINT_TABS:
                    res["existing"]["more"] = len(others) - _HINT_TABS
        return site_notes.attach(res, url)
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def list_tabs(daemon) -> dict:
    """list_tabs() → nekoro 托管组里的标签 [{tabId,url,title,active,attached}]。
    `grouped: False` 表示托管组还没建起来，这份清单其实是「所有非 chrome:// 标签」，
    里面混着用户自己的标签——别拿它当「nekoro 开的标签」用（老版本扩展不带该字段）。"""
    try:
        r = await daemon.list_tabs()
        out = {"ok": True, "tabs": r.get("tabs", []), "active": daemon.active_tab_id}
        if "grouped" in (r or {}):
            out["grouped"] = r["grouped"]
        return out
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def switch_tab(daemon, tab_id: int) -> dict:
    """switch_tab(123) — 把后续命令切到该标签（未 attach 则先 attach）。"""
    try:
        r = await daemon.switch_tab(tab_id)
        return {"ok": bool(r.get("attached", False)), "tabId": r.get("tabId", tab_id)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def close_tab(daemon, tab: int = None) -> dict:
    """close_tab(123) — 关掉指定标签；tab 省略则关当前 attached tab。
    走扩展 close_tab action（chrome.tabs.remove）。关掉活动标签后扩展会自动重连到
    另一可用标签、经 attach 回调同步 active_tab_id。无标签可关 → ok:false。"""
    t = tab if tab is not None else daemon.active_tab_id
    if not t:
        return {"ok": False, "error": "No tab"}     # 与兄弟 helper 错误串一致
    try:
        r = await daemon.bridge.send_scripting({"action": "close_tab", "target": t}, 10)
        return {"ok": True, "closed": (r or {}).get("closed", t)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def close_tabs(daemon, tabs: list) -> dict:
    """close_tabs([1,2,3]) — 批量关标签，逐个走 close_tab，返回 {closed, failed}。
    一张关不掉不影响其余（标签可能已被用户手动关掉）。"""
    closed, failed = [], []
    for t in (tabs or []):
        r = await close_tab(daemon, t)
        (closed if r.get("ok") else failed).append(
            t if r.get("ok") else {"tabId": t, "error": r.get("error", "")})
    return {"ok": not failed, "closed": closed, "failed": failed}


async def sweep_tabs(daemon, dry_run: bool = True) -> dict:
    """sweep_tabs() — 列出可清理的标签候选，**默认只报不关**。

    关不关是用户偏好，工具没资格替他定：有人就是要一直留着标签。所以这里只给证据
    （哪些是同站重复、哪些是游离 about:blank），由 agent/用户判断，`dry_run=False`
    才真关。当前活动标签永远不进候选。

    候选口径：
    - `duplicate` — 同一判重键（host + 首段 path）下多于一张，保留活动标签，
      没有活动标签则保留最后一张（通常是最新开的），其余进 close 列表
    - `blank` — url 为空或 about:blank 的游离标签（站点自己弹出来的那种）

    ⚠️ 扩展还没建起托管组时，`list_tabs` 会退化成「所有非 chrome:// 标签」，候选里
    就可能混进你自己日常的标签。这种情况下拒绝真关（强制 dry_run），只报候选。"""
    try:
        r = await daemon.list_tabs()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    tabs = ((r or {}).get("tabs") or [])
    grouped = (r or {}).get("grouped")       # 老版本扩展不带这个字段 → None = 未知
    active = getattr(daemon, "active_tab_id", None)

    groups, blank = {}, []
    for t in tabs:
        tid = t.get("tabId")
        if tid is None:
            continue
        url = t.get("url", "") or ""
        key = _tab_key(url)
        if not key:
            if tid != active:                 # 活动标签永不进候选
                blank.append({"tabId": tid, "url": url or "about:blank"})
            continue
        # 活动标签要参与分组（否则「另一张与它重复」这种最常见的情况会数成 1 张、
        # 判不出重复），只是永远落在 keep 那一侧。
        groups.setdefault(key, []).append({"tabId": tid, "url": url})

    candidates = []
    for key, members in groups.items():
        if len(members) < 2:                  # 同站只有一张 = 不算重复
            continue
        keep = next((m for m in members if m["tabId"] == active), members[-1])
        close = [m["tabId"] for m in members if m["tabId"] != keep["tabId"]]
        candidates.append({"reason": "duplicate", "key": key, "url": keep["url"],
                           "keep": keep["tabId"], "close": close})

    ids = [i for c in candidates for i in c["close"]] + [b["tabId"] for b in blank]
    out = {"ok": True, "total": len(tabs), "candidates": candidates, "blank": blank}
    if not dry_run and grouped is False:
        out["note"] = ("拒绝真关：扩展没有托管组，候选里可能混进你自己的标签。"
                       "确认后请用 close_tabs([...]) 逐个指定。")
        return out
    if dry_run:
        out["note"] = (f"dry_run — 未关任何标签。sweep_tabs(dry_run=False) 或 "
                       f"close_tabs({ids}) 执行" if ids else "dry_run — 没有可清理的候选")
        return out
    out["result"] = await close_tabs(daemon, ids)
    return out


async def navigate(daemon, url: str, wait: bool = True, timeout: float = 15.0) -> dict:
    """navigate("https://example.com") — 默认等 readyState==='complete' 再返回。
    wait=False 立即返回（Page.navigate 一发出就走）。loaded 标记是否等到加载完成。"""
    try:
        res = await daemon.navigate(url)
        loaded = True
        if wait:
            # 先给导航一点提交时间，避免 readyState 读到上一页残留的 complete
            # （stale-complete 竞态）。150ms 远小于旧的硬编码 3s。
            await asyncio.sleep(0.15)
            loaded = (await wait_for_load(daemon, timeout)).get("ok", False)
        # 有该站点的笔记就顺手带上——指望 agent 自己去 ls domain-skills 是不现实的
        return site_notes.attach({"ok": True, "result": res, "loaded": loaded}, url)
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Page
# ═══════════════════════════════════════════════════════════════════════════════

async def page_info(daemon) -> dict:
    """page_info() → {title, url}"""
    return await daemon.get_page_info()


async def page_html(daemon) -> dict:
    """page_html() → 完整 HTML"""
    try:
        r = await daemon.evaluate("document.documentElement.outerHTML")
        return {"ok": True, "html": r.get("result", {}).get("value", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def page_text(daemon) -> dict:
    """page_text() → 可见文本"""
    try:
        r = await daemon.evaluate("document.body.innerText")
        return {"ok": True, "text": r.get("result", {}).get("value", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Screenshot
# ═══════════════════════════════════════════════════════════════════════════════

async def capture_screenshot(daemon, format: str = "png", quality: int = 80) -> dict:
    """capture_screenshot() → base64 PNG"""
    try:
        data = await daemon.screenshot(format=format, quality=quality)
        return {"ok": True, "data": data, "format": format}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# JavaScript (Runtime.evaluate via CDP)
# ═══════════════════════════════════════════════════════════════════════════════

async def js(daemon, code: str, _wrapped: bool = False) -> dict:
    """js("document.title") — 在当前页面执行 JS，返回完成值。
    先按裸表达式/脚本 eval（`document.title` 直接返回标题）；顶层 `return` 触发
    "Illegal return statement" 时自动包进函数重试（`return x` 也能用）。
    不可 JSON 序列化的值（Infinity/NaN/-0/BigInt）解码回 Python 值，不再吐原始 dict。
    页内异常 / eval 出错 → ok:false，不伪造成功。"""
    try:
        expr = _wrap_js_function(code) if _wrapped else code
        r = await daemon.evaluate(expr)
        det = r.get("exceptionDetails")
        res = r.get("result", {})
        if det:
            desc = ((det.get("exception") or {}).get("description")
                    or det.get("text") or "eval error")
            # 顶层 return 非法 → 包函数重试一次（只重试一次，防死循环）
            if not _wrapped and _is_illegal_return_error(desc):
                return await js(daemon, code, _wrapped=True)
            return {"ok": False, "error": desc}
        if res.get("subtype") == "error":
            return {"ok": False, "error": res.get("description", "?")}
        if "value" in res:
            return {"ok": True, "result": res["value"]}
        if "unserializableValue" in res:
            return {"ok": True,
                    "result": _decode_unserializable_js_value(res["unserializableValue"])}
        return {"ok": True, "result": None}          # undefined 返回
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _wrap_js_function(code: str) -> str:
    return f"(function(){{{code}}})()"


async def cdp(daemon, method: str, **params) -> dict:
    """cdp("Page.navigate", url="...") — 原始 CDP 命令"""
    try:
        return {"ok": True, "result": await daemon.bridge.send(method, params)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def cdp_batch(daemon, *cmds) -> dict:
    """cdp_batch(["DOM.getDocument",{}], ["Page.getLayoutMetrics"]) —
    多条【互不依赖】的 CDP 命令一次性并发下发。命令是 id-keyed 的，
    N 条同时在途 → 只花 ~1 个往返延迟，而非串行 N 个。
    每条 = [method] 或 [method, params]。后条要用前条结果时别用（有依赖）。"""
    async def _one(c):
        method = c[0]
        params = c[1] if len(c) > 1 else {}
        try:
            return {"ok": True, "result": await daemon.bridge.send(method, params)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    results = list(await asyncio.gather(*[_one(c) for c in cmds]))
    # 顶层 ok 反映「全部成功」，与本文件其他 helper 的 ok 语义一致
    return {"ok": all(r["ok"] for r in results), "results": results}


# ═══════════════════════════════════════════════════════════════════════════════
# Click / Input
# ═══════════════════════════════════════════════════════════════════════════════

async def click_at_xy(daemon, x: float, y: float) -> dict:
    """click_at_xy(100, 200) — CDP 完整鼠标点击序列 (isTrusted:true)"""
    try:
        # Step 1: mouseMoved — 关键！让 React pointer/hover 系统初始化
        await daemon.bridge.send("Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 0, "modifiers": 0})
        await asyncio.sleep(0.03)
        # Step 2: mousePressed
        await daemon.bridge.send("Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1, "modifiers": 0})
        await asyncio.sleep(0.05)
        # Step 3: mouseReleased
        await daemon.bridge.send("Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1, "modifiers": 0})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def type_text(daemon, text: str) -> dict:
    """type_text("hello") — CDP Input.insertText"""
    try:
        return {"ok": True, "result": await daemon.type_text(text)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def fill_input(daemon, sel: str, text: str, tab: int = None) -> dict:
    """fill_input("#email", "a@b.com") — 框架感知填值。
    走 scripting：原生 value setter 写值 + 派发 input/change，React/Vue 受控组件
    能收到 onChange（type_text 的 Input.insertText 常绕不过框架 setter）。
    非 input/textarea/contenteditable 或找不到元素 → ok:false，不伪造成功。
    自定义组件要真实键入用 click_selector + type_text。"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "fillInput",
            "sel": sel, "arg": text}, 10)
        res = (r.get("value") if r else None) or {}
        if res.get("ok"):
            return {"ok": True, "value": res.get("value")}
        return {"ok": False, "error": res.get("error", f"fill failed: {sel}")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def upload_file(daemon, sel: str, path) -> dict:
    """upload_file("input[type=file]", r"C:\\a.png") — 给文件输入框设文件。
    走 CDP DOM.setFileInputFiles（触发 change 事件，框架能收到）；path 为绝对路径，
    单个 str 或多个 list。文件不存在 / 找不到元素 / 元素非 file input → ok:false，不伪造成功。
    注意：作用于当前 attached tab，nodeId 经 daemon.query_selector 解析。"""
    import os
    # str / pathlib.Path 当单个；否则视为可迭代（list/tuple of str|Path）。os.fspath 归一。
    if isinstance(path, (str, os.PathLike)):
        files = [os.fspath(path)]
    else:
        files = [os.fspath(p) for p in path]
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        return {"ok": False, "error": f"file not found: {missing}"}
    try:
        nid = await daemon.query_selector(sel)
        if not nid:
            return {"ok": False, "error": f"element not found: {sel}"}
        # 元素非 <input type=file> 时 CDP 报错，catch 统一转 ok:false
        await daemon.bridge.send("DOM.setFileInputFiles",
                                 {"nodeId": nid, "files": files})
        return {"ok": True, "files": files}
    except Exception as e:
        return {"ok": False, "error": str(e)}


_KEYS = {  # key → (windowsVirtualKeyCode, code, text)
    "Enter": (13, "Enter", "\r"), "Tab": (9, "Tab", "\t"), "Backspace": (8, "Backspace", ""),
    "Escape": (27, "Escape", ""), "Delete": (46, "Delete", ""), " ": (32, "Space", " "),
    "ArrowLeft": (37, "ArrowLeft", ""), "ArrowUp": (38, "ArrowUp", ""),
    "ArrowRight": (39, "ArrowRight", ""), "ArrowDown": (40, "ArrowDown", ""),
    "Home": (36, "Home", ""), "End": (35, "End", ""),
    "PageUp": (33, "PageUp", ""), "PageDown": (34, "PageDown", ""),
}


async def press_key(daemon, key: str, modifiers: int = 0) -> dict:
    """press_key("Enter") / press_key("a") — 修饰键位: Alt=1 Ctrl=2 Meta=4 Shift=8。
    特殊键（Enter/Tab/Arrow*/Backspace…）带 virtual key code，监听 e.keyCode/e.which/e.key
    的页面（表单、老站）都能触发；单字符可打印键补发 char 事件（直接进 input，不走 insertText）；
    Alt/Ctrl/Meta 修饰时不发 char（让 Ctrl+A 走快捷键而非打出字符 'a'）。
    移植 browser-harness press_key。"""
    try:
        vk, code, text = _KEYS.get(
            key, (ord(key[0]) if len(key) == 1 else 0, key, key if len(key) == 1 else ""))
        base = {"key": key, "code": code, "modifiers": modifiers,
                "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
        shortcut_mods = modifiers & (1 | 2 | 4)          # Alt/Ctrl/Meta 把单键变快捷键
        printable = len(key) == 1 and bool(text) and not shortcut_mods
        # keyDown：特殊键（有 text 但非可打印，如 Enter="\r"）在此带 text；可打印字符的 text
        # 留给 char 事件，keyDown 不带（否则重复输入）。
        kd = dict(base)
        if text and not printable:
            kd["text"] = text
        await daemon.bridge.send("Input.dispatchKeyEvent", {"type": "keyDown", **kd})
        if printable:
            await daemon.bridge.send("Input.dispatchKeyEvent",
                                     {"type": "char", "text": text, **base})
        await daemon.bridge.send("Input.dispatchKeyEvent", {"type": "keyUp", **base})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Scroll / Wheel
# ═══════════════════════════════════════════════════════════════════════════════

async def scroll_wheel(daemon, dx: float = 0, dy: float = 300,
                       x: float = 500, y: float = 300) -> dict:
    """scroll_wheel(0, 500) — CDP compositor 级 mouseWheel（能穿透 iframe/shadow DOM）。
    fire-and-forget 修复后 CDP Input.dispatchMouseEvent 不再超时。"""
    try:
        await daemon.bridge.send("Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": x, "y": y, "deltaX": dx, "deltaY": dy})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Tab safety & iframe
# ═══════════════════════════════════════════════════════════════════════════════

async def ensure_real_tab(daemon) -> dict:
    """ensure_real_tab() — 当前 tab 是 chrome:// 等内部页时自动导航到 about:blank。
    返回 {url, title}。"""
    INTERNAL = ("chrome://", "chrome-untrusted://", "devtools://")
    try:
        cur = await daemon.get_page_info()
        url = cur.get("url", "")
        if url and not any(url.startswith(p) for p in INTERNAL):
            return {"ok": True, "result": cur}
        # 内部页 → 导航到 about:blank
        await daemon.navigate("about:blank")
        await asyncio.sleep(0.5)
        return {"ok": True, "result": await daemon.get_page_info()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def iframe_target(daemon, url_substr: str) -> dict:
    """iframe_target("player") → 返回第一个 URL 含 url_substr 的 iframe targetId。"""
    try:
        r = await daemon.bridge.send("Target.getTargets", {})
        targets = r.get("targetInfos", []) if r else []
        for t in targets:
            if t.get("type") == "iframe" and url_substr in t.get("url", ""):
                return {"ok": True, "targetId": t["targetId"], "url": t["url"]}
        return {"ok": False, "error": f"no iframe matching '{url_substr}'"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP (no browser)
# ═══════════════════════════════════════════════════════════════════════════════

async def http_get(daemon, url: str, timeout: float = 20.0) -> str:
    """http_get("https://example.com") → 纯 HTTP GET 返回 HTML 字符串。用于静态页/API。"""
    import urllib.request, urllib.error, gzip
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Encoding": "gzip",
    }
    def _sync():
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {url}") from e
        except Exception as e:
            raise RuntimeError(f"http_get failed: {e}") from e
    return await asyncio.get_running_loop().run_in_executor(None, _sync)


async def scroll_to(daemon, x: float = 0, y: float = 0) -> dict:
    """scroll_to(0, 500) — 滚动页面视口到坐标 (window.scrollTo)"""
    try:
        await daemon.evaluate(f"window.scrollTo({x}, {y})")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Wait
# ═══════════════════════════════════════════════════════════════════════════════

async def wait_for_load(daemon, timeout: float = 15.0) -> dict:
    """wait_for_load(15) — poll document.readyState (无 listener 泄漏)。"""
    import time
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = await js(daemon, "document.readyState")
            if r.get("result") == "complete":
                return {"ok": True}
            await asyncio.sleep(0.3)
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def wait_for_network_idle(daemon, idle_time: float = 0.5,
                                 timeout: float = 15.0) -> dict:
    """wait_for_network_idle(0.5, 15) — 等待【当前活动标签】的 Network 请求静默 idle_time 秒。
    只算 active_tab_id 的事件：其它 attached 标签（后台轮询/SSE 页）的 Network 事件也进全局
    缓冲，不过滤会一直把 idle 窗口顶开、永不静默。"""
    import time
    try:
        await cdp(daemon, "Network.enable")
        active = daemon.active_tab_id     # 只抓一次：等待期间即使 switch_tab 也继续认起始标签
        deadline = time.time() + timeout
        pending: set = set()
        last_active = time.time()
        while time.time() < deadline:
            events = await drain_events(daemon)
            for ev in events:
                # 只认当前活动标签的事件（后台标签的 Network 事件会污染 idle 判定）
                if active is not None and ev.get("tabId") != active:
                    continue
                m = ev.get("method", "")
                p = ev.get("params", {})
                if m == "Network.requestWillBeSent":
                    pending.add(p.get("requestId", ""))
                    last_active = time.time()
                elif m in ("Network.loadingFinished", "Network.loadingFailed"):
                    pending.discard(p.get("requestId", ""))
            if not pending and time.time() - last_active >= idle_time:
                return {"ok": True}
            await asyncio.sleep(0.1)
        return {"ok": False, "error": "timeout", "pending": len(pending)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def drain_events(daemon):
    """drain_events() → list — 拉取自上次 drain 后所有缓存的 CDP 事件。
    每个事件为 {method, params, sessionId, tabId}（tabId 用于按标签过滤）。"""
    return await daemon.drain_events()


async def sleep(daemon, seconds: float) -> dict:
    """sleep(2)"""
    await asyncio.sleep(seconds)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Network / Cookies
# ═══════════════════════════════════════════════════════════════════════════════

async def get_cookies(daemon, url: str = None) -> dict:
    """get_cookies("https://example.com")"""
    try:
        r = await daemon.bridge.send("Network.getCookies", {"urls": [url]} if url else {})
        return {"ok": True, "cookies": r.get("cookies", [])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def set_cookie(daemon, name: str, value: str,
                     url: str = "", domain: str = "", path: str = "/") -> dict:
    """set_cookie("token", "abc", domain=".example.com")"""
    try:
        p = {"name": name, "value": value, "path": path}
        if url: p["url"] = url
        if domain: p["domain"] = domain
        await daemon.bridge.send("Network.setCookie", p)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def network_enable(daemon) -> dict:
    """network_enable() — 启用 CDP 网络请求捕获"""
    try:
        await daemon.bridge.send("Network.enable", {
            "maxTotalBufferSize": 10000000, "maxResourceBufferSize": 5000000})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_response_body(daemon, request_id: str) -> dict:
    """get_response_body("1234.5") → CDP 网络响应体"""
    try:
        r = await daemon.bridge.send("Network.getResponseBody",
            {"requestId": request_id})
        body = r.get("body", "")
        if r.get("base64Encoded", False):
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        return {"ok": True, "body": body}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Extension self-reload
# ═══════════════════════════════════════════════════════════════════════════════

async def reload_extension(daemon) -> dict:
    """reload_extension() — 强制重载扩展（自愈用）"""
    try:
        await daemon.bridge.send_scripting({"action": "reload_extension"}, 5)
        return {"ok": True, "result": "reloading"}
    except Exception:
        return {"ok": True, "result": "reloading (connection lost as expected)"}


# ═══════════════════════════════════════════════════════════════════════════════
# Scripting path ops — CDP 被占时的保底路径
# 这些函数直接调 daemon.bridge.send_scripting，无中间层
# 对应的 op 定义在 extension/background.js 的 runOp() 里
# ═══════════════════════════════════════════════════════════════════════════════

async def _find_tab(daemon, url_hint: str = "http") -> int | None:
    """找匹配 URL 的 tab，优先 CDP-attached tab"""
    tab = getattr(daemon, 'active_tab_id', None)
    if tab: return tab
    try:
        r = await daemon.bridge.send_scripting(
            {"action": "find_tab", "url": url_hint}, 8)
        return r.get("tabId")
    except Exception:
        return None


async def click_selector(daemon, sel: str, tab: int = None) -> dict:
    """click_selector(".btn") — CDP 真实坐标点击 (isTrusted:true)"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        # JS 获取元素坐标（不派发事件）
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "getRect", "sel": sel}, 10)
        rect = r.get("value") if r else None
        if not rect or not rect.get("x"):
            return {"ok": False, "error": f"element not found: {sel}"}
        # CDP 真实鼠标点击
        return await click_at_xy(daemon, rect["x"], rect["y"])
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def state(daemon, max_items: int = 80, sel: str = None,
                tab: int = None) -> dict:
    """state() → [{index, changed, tag, text, box}] — 索引元素树"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "state",
            "sel": sel, "arg": max_items}, 10)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def find_text(daemon, text: str, exact: bool = False,
                    limit: int = 10, tab: int = None) -> dict:
    """find_text("喜欢") → [{text, tag, match, w, h}]"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "findText",
            "arg": {"text": text, "exact": exact, "limit": limit}}, 10)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def click_text(daemon, text: str, tab: int = None) -> dict:
    """click_text("喜欢") — CDP 真实坐标点击 (isTrusted:true)"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "getRectByText", "arg": text}, 10)
        rect = r.get("value") if r else None
        if not rect or not rect.get("x"):
            return {"ok": False, "error": f"text not found: {text}"}
        return await click_at_xy(daemon, rect["x"], rect["y"])
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def click_index(daemon, index: int, tab: int = None) -> dict:
    """click_index(3) — CDP 真实坐标点击 (isTrusted:true)"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "getRectByIndex", "arg": index}, 10)
        rect = r.get("value") if r else None
        if not rect or not rect.get("x"):
            return {"ok": False, "error": f"index not found: {index}"}
        return await click_at_xy(daemon, rect["x"], rect["y"])
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def hover(daemon, sel: str, tab: int = None) -> dict:
    """hover(".menu") — CSS 选择器悬停"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "hover", "sel": sel}, 10)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def hover_index(daemon, index: int, tab: int = None) -> dict:
    """hover_index(3) — 悬停 state() 列表的第 N 个元素"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "hoverIndex", "arg": index}, 10)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def scroll_into_view(daemon, sel: str = None, tab: int = None) -> dict:
    """scroll_into_view("#target") — 滚动到可见"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "scrollIntoView",
            "sel": sel}, 10)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def wait_selector(daemon, sel: str, state: str = "visible",
                        timeout: float = 10.0, tab: int = None) -> dict:
    """wait_selector(".modal", "visible", 15) — 等待元素状态"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "waitSelector",
            "arg": {"sel": sel, "state": state, "timeout": int(timeout * 1000)}},
            timeout + 5)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_markdown(daemon, sel: str = None, max_chars: int = 8000,
                       tab: int = None) -> dict:
    """get_markdown() → 页面内容转 Markdown"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "getMarkdown",
            "sel": sel, "arg": max_chars}, 10)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def box_of(daemon, sel: str, tab: int = None) -> dict:
    """box_of(".btn") → {x, y, w, h, visible, tag, text}"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "box", "sel": sel}, 10)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def dialog_off(daemon, tab: int = None) -> dict:
    """dialog_off() — JS 层覆盖 window.alert/confirm/prompt 为自动关闭（需在触发前注入，
    盖不住 beforeunload / 已开的原生对话框）。想要兜底防挂用扩展的 CDP 层处置（见
    get_last_dialog）——那个 attach 后一直生效、覆盖 beforeunload。"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "dialogOff"}, 10)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_last_dialog(daemon) -> dict:
    """get_last_dialog() → {dialog} — 取最近一次被扩展自动处置的原生对话框，读后清。
    attach 后扩展 Page.enable + 拦 Page.javascriptDialogOpening 立即处置（beforeunload
    放行、alert/confirm/prompt 取消），防原生对话框冻结页面 JS 线程导致 evaluate 系
    helper 挂死。dialog = {kind, message, url, defaultPrompt} 或 None（期间无对话框）。
    仅事后观测，不能代答：需 confirm()===true 或特定 prompt 字符串的流程会被无条件取消。"""
    try:
        r = await daemon.bridge.send_request("last_dialog", timeout=5)
        return {"ok": True, "dialog": r}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Self-heal
# ═══════════════════════════════════════════════════════════════════════════════

async def list_site_actions(daemon) -> dict:
    """list_site_actions() → 已固化的站点函数 + 载入失败的文件。

    路由用：想知道"这事有没有现成的"就查这里。载入错误必须能看见——
    否则用户对着"我明明写了函数怎么不存在"抓瞎。
    """
    try:
        ns, errors = site_notes.load_functions()
        root = site_notes.skills_dir()
        return {"ok": True, "root": str(root) if root else None,
                "actions": sorted(ns), "errors": errors}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def reload_agent_helpers(daemon) -> dict:
    """reload_agent_helpers() — 重新加载 agent_helpers.py，无需重启 daemon。"""
    import importlib
    from . import agent_helpers as ah
    importlib.reload(ah)
    names = [k for k in vars(ah) if not k.startswith("_") and callable(vars(ah)[k])]
    return {"ok": True, "reloaded": names}


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: list all available helpers (for SKILL.md / discovery)
# ═══════════════════════════════════════════════════════════════════════════════

def list_helpers() -> list[str]:
    """列出所有可用的 helper 函数名（含同步函数）。"""
    import inspect
    return [
        name for name, obj in globals().items()
        if callable(obj) and not name.startswith("_")
        and (inspect.iscoroutinefunction(obj) or inspect.isfunction(obj))
    ]
