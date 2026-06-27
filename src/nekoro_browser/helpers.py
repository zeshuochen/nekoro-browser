"""helpers.py — 自愈函数集

这些函数在 daemon 的上下文中执行，提供高级 DOM 操作能力。
Agent 在运行时遇到缺失的功能，可以直接编辑此文件添加新函数。
文件使用前会被重新加载（importlib.reload），所以修改立即生效。

所有函数接受 daemon 实例作为第一个参数。
"""

import asyncio
import base64
import json
import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Tab Management
# ═══════════════════════════════════════════════════════════════════════════════

async def new_tab(daemon, url: str = "about:blank") -> dict:
    """新建一个标签页并导航到指定 URL。返回 tab 信息。

    Usage: new_tab("https://example.com")
    """
    try:
        result = await daemon.bridge.send("Target.createTarget", {
            "url": url
        })
        target_id = result.get("targetId", "")
        return {"ok": True, "targetId": target_id, "url": url}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def navigate(daemon, url: str) -> dict:
    """导航当前标签到指定 URL。

    Usage: navigate("https://example.com")
    """
    try:
        result = await daemon.navigate(url)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Page Info
# ═══════════════════════════════════════════════════════════════════════════════

async def page_info(daemon) -> dict:
    """获取当前页面的标题和 URL。

    Usage: page_info()
    """
    return await daemon.get_page_info()


async def page_html(daemon) -> dict:
    """获取当前页面的完整 HTML 源码。

    Usage: page_html()
    """
    try:
        result = await daemon.evaluate("document.documentElement.outerHTML")
        html = result.get("result", {}).get("value", "")
        return {"ok": True, "html": html}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def page_text(daemon) -> dict:
    """获取当前页面的可见文本内容。

    Usage: page_text()
    """
    try:
        result = await daemon.evaluate("document.body.innerText")
        text = result.get("result", {}).get("value", "")
        return {"ok": True, "text": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Screenshots
# ═══════════════════════════════════════════════════════════════════════════════

async def capture_screenshot(daemon, format: str = "png", quality: int = 80) -> dict:
    """截取当前页面的截图，返回 base64 编码的图像数据。

    Usage: capture_screenshot()
    Usage: capture_screenshot("jpeg", 90)
    """
    try:
        data = await daemon.screenshot(format=format, quality=quality)
        return {"ok": True, "data": data, "format": format}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# JavaScript Execution
# ═══════════════════════════════════════════════════════════════════════════════

async def js(daemon, code: str) -> dict:
    """在当前页面执行 JavaScript 代码。

    Usage: js("document.title")
    Usage: js("return document.querySelectorAll('a').length")
    """
    try:
        # Wrap in function to support return statements
        wrapped = f"(function() {{ {code} }})()"
        result = await daemon.evaluate(wrapped)
        value = result.get("result", {})
        if value.get("type") == "object" and value.get("subtype") == "error":
            return {"ok": False, "error": value.get("description", "Unknown JS error")}
        return {"ok": True, "result": value.get("value", value)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Click / Interaction
# ═══════════════════════════════════════════════════════════════════════════════

async def click_at_xy(daemon, x: float, y: float) -> dict:
    """在屏幕坐标 (x, y) 处点击。

    Usage: click_at_xy(100, 200)
    """
    try:
        result = await daemon.click_at(x, y)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def click_selector(daemon, selector: str) -> dict:
    """点击匹配 CSS 选择器的第一个元素。

    Usage: click_selector("#submit-btn")
    Usage: click_selector("a.login")
    """
    try:
        node_id = await daemon.query_selector(selector)
        if node_id is None:
            return {"ok": False, "error": f"Element not found: {selector}"}
        result = await daemon.click_element(node_id)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def type_text(daemon, text: str) -> dict:
    """在当前聚焦的元素中输入文本。

    Usage: type_text("hello world")
    """
    try:
        result = await daemon.type_text(text)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def press_key(daemon, key: str, modifiers: int = 0) -> dict:
    """发送按键事件。

    修饰键（可组合）：Ctrl=2, Alt=1, Shift=8, Meta=4

    Usage: press_key("Enter")
    Usage: press_key("c", 2)  # Ctrl+C
    """
    try:
        await daemon.bridge.send("Input.dispatchKeyEvent", {
            "type": "keyDown",
            "key": key,
            "modifiers": modifiers,
        })
        await daemon.bridge.send("Input.dispatchKeyEvent", {
            "type": "keyUp",
            "key": key,
            "modifiers": modifiers,
        })
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Scrolling
# ═══════════════════════════════════════════════════════════════════════════════

async def scroll_to(daemon, x: float = 0, y: float = 0) -> dict:
    """滚动页面到指定位置。

    Usage: scroll_to(0, 500)
    """
    try:
        await daemon.evaluate(f"window.scrollTo({x}, {y})")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def scroll_bottom(daemon) -> dict:
    """滚动到页面底部。

    Usage: scroll_bottom()
    """
    try:
        await daemon.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Waiting
# ═══════════════════════════════════════════════════════════════════════════════

async def wait_for_load(daemon, timeout: float = 30.0) -> dict:
    """等待页面加载完成。

    Usage: wait_for_load()
    Usage: wait_for_load(60)
    """
    try:
        ok = await daemon.wait_for_load(timeout=timeout)
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def wait_for_selector(daemon, selector: str, timeout: float = 10.0) -> dict:
    """等待 CSS 选择器匹配的元素出现。

    Usage: wait_for_selector("#content-loaded")
    """
    try:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            node_id = await daemon.query_selector(selector)
            if node_id is not None:
                return {"ok": True, "nodeId": node_id}
            await asyncio.sleep(0.2)
        return {"ok": False, "error": f"Timeout waiting for selector: {selector}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def sleep(daemon, seconds: float) -> dict:
    """暂停指定秒数。

    Usage: sleep(2)
    """
    await asyncio.sleep(seconds)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Network / Cookies
# ═══════════════════════════════════════════════════════════════════════════════

async def get_cookies(daemon, url: str | None = None) -> dict:
    """获取当前页面的 cookies。

    Usage: get_cookies()
    Usage: get_cookies("https://example.com")
    """
    try:
        params = {}
        if url:
            params["urls"] = [url]
        result = await daemon.bridge.send("Network.getCookies", params)
        return {"ok": True, "cookies": result.get("cookies", [])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def set_cookie(daemon, name: str, value: str, url: str = "",
                     domain: str = "", path: str = "/") -> dict:
    """设置一个 cookie。

    Usage: set_cookie("token", "abc123", domain=".example.com")
    """
    try:
        params = {
            "name": name,
            "value": value,
            "path": path,
        }
        if url:
            params["url"] = url
        if domain:
            params["domain"] = domain
        await daemon.bridge.send("Network.setCookie", params)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Request Interception (for monitoring/extraction)
# ═══════════════════════════════════════════════════════════════════════════════

async def enable_network_monitoring(daemon) -> dict:
    """启用网络请求监控。后续所有请求/响应都会通过事件推送。

    Usage: enable_network_monitoring()
    """
    try:
        await daemon.bridge.send("Network.enable", {
            "maxTotalBufferSize": 10000000,
            "maxResourceBufferSize": 5000000,
        })
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_response_body(daemon, request_id: str) -> dict:
    """获取指定请求的响应体。

    Usage: get_response_body("1234.5")
    """
    try:
        result = await daemon.bridge.send("Network.getResponseBody", {
            "requestId": request_id,
        })
        body = result.get("body", "")
        base64_encoded = result.get("base64Encoded", False)
        if base64_encoded:
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        return {"ok": True, "body": body}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: list all available helpers (for SKILL.md / discovery)
# ═══════════════════════════════════════════════════════════════════════════════

def list_helpers() -> list[str]:
    """列出所有可用的 helper 函数名。"""
    import inspect
    return [
        name for name, obj in globals().items()
        if inspect.iscoroutinefunction(obj) and not name.startswith("_")
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Scripting Path Ops (chrome.scripting.executeScript — no CDP needed)
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_tab(daemon, url_hint: str = "") -> int | None:
    """Auto-discover a tab: try CDP-attached tab first, then search by URL."""
    tab = getattr(daemon, 'active_tab_id', None)
    if tab:
        return tab
    # Fallback: search tabs via scripting
    try:
        pattern = url_hint or "http"
        r = await daemon.bridge.send_scripting(
            {"action": "find_tab", "url": pattern}, 10)
        return r.get("tabId")
    except Exception:
        return None


async def script_op(daemon, op: str, sel: str = None, arg=None,
                    tab: int = None, timeout: float = 15.0) -> dict:
    """Run a pre-defined op via chrome.scripting.executeScript (scripting path).

    Usage: script_op("clickText", arg="喜欢")
    Usage: script_op("findText", arg={"text":"喜欢", "limit":5})
    Usage: script_op("dump")
    """
    try:
        t = tab or await _get_tab(daemon)
        if t is None:
            return {"ok": False, "error": "No tab available"}
        r = await daemon.bridge.send_scripting({
            "action": "evaluate", "target": t,
            "op": op, "sel": sel, "arg": arg
        }, timeout)
        val = r.get("value")
        err = r.get("error")
        if err:
            return {"ok": False, "error": str(err)}
        return {"ok": True, "result": val}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def click_text(daemon, text: str, tab: int = None) -> dict:
    """Click visible element by its text content. Uses improved TreeWalker engine.

    Usage: click_text("喜欢")
    """
    return await script_op(daemon, "clickText", arg=text, tab=tab)


async def find_text(daemon, text: str, exact: bool = False,
                    limit: int = 10, tab: int = None) -> dict:
    """Search for visible elements containing text. Returns metadata for each match.

    Usage: find_text("喜欢")
    Usage: find_text("喜欢", exact=True)
    """
    return await script_op(daemon, "findText",
                           arg={"text": text, "exact": exact, "limit": limit},
                           tab=tab)


async def wait_for_text(daemon, text: str, timeout: float = 15.0,
                        interval: float = 0.5, tab: int = None) -> dict:
    """Poll until visible text appears on the page. Great for React-hydrated UIs.

    Usage: wait_for_text("喜欢")
    Usage: wait_for_text("喜欢", timeout=30)
    """
    return await script_op(daemon, "waitForText",
                           arg={"text": text, "timeout": int(timeout * 1000),
                                "interval": int(interval * 1000)},
                           tab=tab, timeout=timeout + 5)


async def dump_dom(daemon, sel: str = None, depth: int = 4,
                   tab: int = None) -> dict:
    """Dump interactive DOM elements as a text tree. Useful for debugging.

    Usage: dump_dom()
    Usage: dump_dom(sel=".sidebar", depth=3)
    """
    return await script_op(daemon, "dump", sel=sel, arg=depth, tab=tab)


async def has_sel(daemon, sel: str, tab: int = None) -> dict:
    """Quick check if a CSS selector exists on the page.

    Usage: has_sel("*[class*=like]")
    """
    return await script_op(daemon, "has", sel=sel, tab=tab)


async def box_of(daemon, sel: str, tab: int = None) -> dict:
    """Get bounding box and visibility info for a CSS selector.

    Usage: box_of(".like-btn")
    """
    return await script_op(daemon, "box", sel=sel, tab=tab)


async def reload_extension(daemon) -> dict:
    """Force-reload the Chrome extension to pick up new code changes.
    After this call, the extension restarts automatically.

    Usage: reload_extension()
    """
    try:
        r = await daemon.bridge.send_scripting(
            {"action": "reload_extension"}, 5)
        return {"ok": True, "result": r}
    except Exception:
        # reload kills the connection, so timeout is expected
        return {"ok": True, "result": "reloading (connection lost as expected)"}
