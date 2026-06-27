"""Domain skill: Douyin (抖音) video liking workflow.

Load with: from agent-workspace.domain-skills.douyin.like import douyin_like
"""
import asyncio


async def douyin_like(daemon, username: str = "籽岷",
                      video_index: int = 0,
                      tab: int = None) -> dict:
    """Search a Douyin user and CDP-like their first video.

    Usage: douyin_like("籽岷")
    Usage: douyin_like("籽岷", video_index=2)
    """
    try:
        # Tab discovery
        t = tab or getattr(daemon, 'active_tab_id', None)
        if not t:
            try:
                r = await daemon.bridge.send_scripting(
                    {"action": "find_tab", "url": "douyin.com"}, 10)
                t = r.get("tabId")
            except Exception:
                pass
        if not t:
            return {"ok": False, "error": "No douyin tab available"}

        # Step 1: Navigate to search
        await daemon.bridge.send_scripting(
            {"action": "navigate", "target": t,
             "url": f"https://www.douyin.com/search/{username}?type=video"}, 15)
        await asyncio.sleep(4)

        # Step 2: Find Nth video link
        link = None
        for i in range(15):
            r = await daemon.bridge.send_scripting(
                {"action": "evaluate", "target": t,
                 "op": "findLink", "arg": "/video/"}, 10)
            link = r.get("value")
            if link:
                if video_index <= 0:
                    break
                video_index -= 1
            await daemon.bridge.send_scripting(
                {"action": "evaluate", "target": t,
                 "op": "scroll", "arg": 800}, 10)
            await asyncio.sleep(1.5)
        if not link:
            return {"ok": False, "error": "Video link not found"}

        # Step 3: Click video (preserves auth — opens as modal)
        vid = link.split("/video/")[1].split("?")[0].split("/")[0]
        await daemon.bridge.send_scripting(
            {"action": "evaluate", "target": t, "op": "click",
             "sel": f'a[href*="{vid}"]'}, 10)
        await asyncio.sleep(6)

        # Step 4: Get action bar position
        r = await daemon.bridge.send_scripting(
            {"action": "evaluate", "target": t, "op": "box",
             "sel": "[class*=action]"}, 10)
        box = r.get("value", {})
        if not isinstance(box, dict) or not box.get("visible"):
            return {"ok": False, "error": "Action bar not found"}
        before = box.get("text", "")

        # Step 5: CDP-click like button (ensure debugger is on this tab)
        await daemon.bridge.send_control("attach", tabId=t)
        await asyncio.sleep(1)
        cx = box["x"] + box["w"] // 2
        cy = box["y"] + 160  # Past avatar → like button
        await daemon.bridge.send("Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": cx, "y": cy,
             "button": "left", "clickCount": 1})
        await asyncio.sleep(0.05)
        await daemon.bridge.send("Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": cx, "y": cy,
             "button": "left", "clickCount": 1})
        await asyncio.sleep(1.5)

        # Step 6: Verify
        r2 = await daemon.bridge.send_scripting(
            {"action": "evaluate", "target": t, "op": "box",
             "sel": "[class*=action]"}, 10)
        box2 = r2.get("value", {})
        after = box2.get("text", "") if isinstance(box2, dict) else ""
        return {
            "ok": True,
            "result": {
                "liked": before != after,
                "before": before[:30],
                "after": after[:30],
                "video": link[:80]
            }
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
