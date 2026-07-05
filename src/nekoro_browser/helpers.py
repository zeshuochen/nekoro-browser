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

async def new_tab(daemon, url: str = "about:blank") -> dict:
    """new_tab("https://example.com")"""
    try:
        r = await daemon.bridge.send("Target.createTarget", {"url": url})
        return {"ok": True, "targetId": r.get("targetId", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def list_tabs(daemon) -> dict:
    """list_tabs() → nekoro 托管组里的标签 [{tabId,url,title,active,attached}]。"""
    try:
        r = await daemon.list_tabs()
        return {"ok": True, "tabs": r.get("tabs", []), "active": daemon.active_tab_id}
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
        return {"ok": True, "result": res, "loaded": loaded}
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


async def press_key(daemon, key: str, modifiers: int = 0) -> dict:
    """press_key("Enter") — 修饰键: Ctrl=2 Alt=1 Shift=8 Meta=4"""
    try:
        await daemon.bridge.send("Input.dispatchKeyEvent",
            {"type": "keyDown", "key": key, "modifiers": modifiers})
        await daemon.bridge.send("Input.dispatchKeyEvent",
            {"type": "keyUp", "key": key, "modifiers": modifiers})
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
    """wait_for_network_idle(0.5, 15) — 等待 Network 请求静默 idle_time 秒。"""
    import time
    try:
        await cdp(daemon, "Network.enable")
        deadline = time.time() + timeout
        pending: set = set()
        last_active = time.time()
        while time.time() < deadline:
            events = await drain_events(daemon)
            for ev in events:
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
    每个事件为 {method, params, sessionId}。"""
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
    """dialog_off() — 自动关闭 alert/confirm/prompt"""
    t = tab or await _find_tab(daemon)
    if not t: return {"ok": False, "error": "No tab"}
    try:
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t, "op": "dialogOff"}, 10)
        return {"ok": True, "result": r.get("value")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Self-heal
# ═══════════════════════════════════════════════════════════════════════════════

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
