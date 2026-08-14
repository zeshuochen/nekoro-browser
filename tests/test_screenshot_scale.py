"""test_screenshot_scale.py — capture_screenshot 的 CSS 尺寸出图（无需浏览器，用假 daemon）。

背景：系统缩放 125% 时裸 Page.captureScreenshot 给的是 1.25× 物理像素，而 click_at_xy
走 Input.dispatchMouseEvent 吃的是 CSS 像素。照着图读坐标会点偏，且偏得隐蔽——点到别的
元素上，不报错。修法是让 Chrome 按 clip.scale 直接渲成 CSS 尺寸。

fake 按真实 wire 造：evaluate 回 {result:{value:...}}，screenshot 回真 PNG 头（IHDR 里
写入按 clip 算出的实际尺寸），这样 _png_size 读到的是"产物尺寸"而不是我们自己算的数。
"""
import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


def fake_png(w: int, h: int) -> str:
    """造一个 IHDR 尺寸为 w×h 的最小 PNG（只有头，够 _png_size 读）。"""
    b = (b"\x89PNG\r\n\x1a\n"
         + (13).to_bytes(4, "big") + b"IHDR"
         + w.to_bytes(4, "big") + h.to_bytes(4, "big")
         + b"\x08\x06\x00\x00\x00" + b"\x00" * 16)
    return base64.b64encode(b).decode()


class FakeDaemon:
    """dpr/视口可配；screenshot 按有没有 clip 决定出图尺寸——模拟 Chrome 的真实行为。"""
    def __init__(self, dpr=1.0, css=(1600, 800)):
        self.dpr, self.css = dpr, css
        self.clips = []          # 记录每次下发的 clip，断言用
        self.tabs = []           # 记录 tab 透传

    async def evaluate(self, expr, tab=None):
        self.tabs.append(tab)
        w, h = self.css
        return {"result": {"value": {"d": self.dpr, "w": w, "h": h}}}

    async def screenshot(self, format="png", quality=80, clip=None, tab=None):
        self.clips.append(clip)
        self.tabs.append(tab)
        w, h = self.css
        if clip:
            # 复现 Chrome 真实行为：clip.scale 是**叠加在 dpr 之上**的倍率，
            # 出图 = clip.width × scale × dpr。早先这里写成直接返回 clip.width，
            # 把"scale=1 就能拿到 CSS 尺寸"这个错误假设编进了 fake —— 单测全绿、
            # 真机出来的仍是 2560 物理像素。fake 不忠于 wire，绿测就是自欺。
            s = clip.get("scale", 1)
            return fake_png(round(clip["width"] * s * self.dpr),
                            round(clip["height"] * s * self.dpr))
        return fake_png(round(w * self.dpr), round(h * self.dpr))   # 裸截 = 物理像素


async def run():
    # 1. dpr=1.25 + 默认 scale="css" → 出图必须等于 CSS 尺寸，坐标可直接喂 click_at_xy。
    #    断言落在**产物尺寸**上而不是 clip 参数上：clip 传对了没有，由出图说了算。
    d = FakeDaemon(dpr=1.25, css=(1600, 800))
    r = await helpers.capture_screenshot(d)
    assert r["ok"], r
    assert r["png_size"] == [1600, 800], r          # ← 关键：产物尺寸 == CSS 尺寸
    assert d.clips[-1]["scale"] == 1 / 1.25, d.clips  # scale 叠加在 dpr 上，必须是 1/dpr
    assert r["css_size"] == [1600, 800] and r["dpr"] == 1.25, r

    # 2. scale="device" → 不下发 clip，出图是物理像素（旧行为保留）
    d = FakeDaemon(dpr=1.25, css=(1600, 800))
    r = await helpers.capture_screenshot(d, scale="device")
    assert d.clips[-1] is None, d.clips
    assert r["png_size"] == [2000, 1000], r
    assert r["scale"] == "device", r

    # 3. dpr==1 → clip 是无操作，不该下发（少一个出错面）
    d = FakeDaemon(dpr=1.0, css=(1200, 900))
    r = await helpers.capture_screenshot(d)
    assert d.clips[-1] is None, d.clips
    assert r["png_size"] == [1200, 900], r

    # 4. tab= 透传到 evaluate 和 screenshot 两处，不能只传一半
    d = FakeDaemon(dpr=2.0, css=(800, 600))
    r = await helpers.capture_screenshot(d, tab=77)
    assert r["ok"] and set(d.tabs) == {77}, d.tabs

    # 4b. 视口 0×0（后台标签/窗口最小化）→ 当场说清原因，不去调那个注定超时的 CDP。
    #     真机上这条路径给的是 "CDP 'Page.captureScreenshot' timed out"，看不出所以然。
    d = FakeDaemon(dpr=1.25, css=(0, 0))
    r = await helpers.capture_screenshot(d)
    assert r["ok"] is False and r["kind"] == "not_rendered", r
    assert d.clips == [], "视口 0×0 时不该下发截图命令"

    # 5. 非法 scale 报错，不静默当默认值处理
    r = await helpers.capture_screenshot(FakeDaemon(), scale="cSS")
    assert r["ok"] is False and "scale must be" in r["error"], r

    # 6. daemon 抛异常 → ok:false，不冒泡
    class Boom(FakeDaemon):
        async def screenshot(self, format="png", quality=80, clip=None, tab=None):
            raise RuntimeError("cdp died")
    r = await helpers.capture_screenshot(Boom())
    assert r["ok"] is False and "cdp died" in r["error"], r

    # 7. 非 png 格式不谎报 png_size（IHDR 读不出来就别给这个键）
    d = FakeDaemon(dpr=1.25)
    r = await helpers.capture_screenshot(d, format="jpeg")
    assert "png_size" not in r, r

    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(run())
