"""test_click_index.py — click_index 必须走页内点击，不能走 CDP 坐标点击。

守的 bug：`click_index` 以前取元素中心坐标再走 `Input.dispatchMouseEvent`。坐标点击
打的是**屏幕位置而不是元素**，于是两种情况都会「返回 ok:true 而页面纹丝不动」：

1. 目标被浮层 / sticky 头 / cookie 横幅盖住 —— 事件落在遮挡物上；
2. 窗口没在前台 —— 坐标算得完全正确、`elementFromPoint` 也确实命中目标，
   输入注入本身就是不生效。

第 2 种连命中检测都拦不住（真机实测：hit 为真、坐标无误，照样什么都没发生），
所以问题不在「验准不准」，而在这条路本身不可靠。没有报错、没有线索。

库里其它点击（click / click_text / click_selector / click_ref）本来就都是页内点击，
`click_index` 是唯一的例外，也正是唯一会静默空点的那个。

fake 严格照真 wire 形状：`send_scripting` 回 `{"value": "clicked:N"}` /
`"clicked-covered:N"` / `"no-element"`；`send` 记录 Input.dispatchMouseEvent，
用来断言**根本没发生过**坐标点击。
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ["NEKORO_DOMAIN_SKILLS"] = tempfile.mkdtemp(prefix="nekoro-empty-skills-")

from nekoro_browser import helpers


class _FakeBridge:
    def __init__(self, owner):
        self.owner = owner

    async def send_scripting(self, params, timeout=30.0):
        self.owner.script_calls.append(params)
        return {"value": self.owner.script_value}

    async def send(self, method, params, tab=None, **kw):
        self.owner.cdp_calls.append((method, params))
        return {}


class FakeDaemon:
    def __init__(self, script_value):
        self.script_value = script_value
        self.script_calls = []
        self.cdp_calls = []
        self.active_tab_id = 1
        self.bridge = _FakeBridge(self)


def _ops(d):
    return [c.get("op") for c in d.script_calls]


def test_click_index_uses_the_in_page_op():
    d = FakeDaemon("clicked:3")
    r = asyncio.run(helpers.click_index(d, 3))
    assert r["ok"] is True, r
    assert r["via"] == "in-page", r
    assert "clickIndex" in _ops(d), _ops(d)


def test_click_index_never_dispatches_coordinate_input():
    """**这条是核心。** 只要还发 Input.dispatchMouseEvent，就还留着那条会静默
    空点的路 —— 坐标对不对都不算数，窗口没前台时它就是不生效。"""
    d = FakeDaemon("clicked:3")
    asyncio.run(helpers.click_index(d, 3))
    mouse = [m for m, _ in d.cdp_calls if "dispatchMouseEvent" in m]
    assert mouse == [], f"仍在走坐标点击: {d.cdp_calls}"
    assert "getRectByIndex" not in _ops(d), \
        "还在取坐标——说明坐标那条路没拆干净"


def test_covered_is_reported_when_the_centre_is_blocked():
    """遮挡只作诊断：页内点击照样点得到，但调用方发现页面没反应时，这一位能省掉
    一轮排查。必须由扩展在**点击前**测好带回——点完页面一跳 DOM 就变了，事后问不到。"""
    d = FakeDaemon("clicked-covered:3")
    r = asyncio.run(helpers.click_index(d, 3))
    assert r["ok"] is True and r["covered"] is True, r


def test_not_covered_key_absent_when_unobstructed():
    """没被遮挡时不该带这个键——诊断信息只在有意义时出现。"""
    d = FakeDaemon("clicked:3")
    r = asyncio.run(helpers.click_index(d, 3))
    assert "covered" not in r, r


def test_missing_element_is_an_error_not_a_silent_ok():
    for v in ("no-element", "invalid-index", None):
        d = FakeDaemon(v)
        r = asyncio.run(helpers.click_index(d, 9999))
        assert r["ok"] is False, (v, r)
        assert "9999" in r["error"], (v, r)
        assert r["kind"] == "transient", (v, r)


def test_unexpected_op_result_is_not_reported_as_success():
    """op 回了个没见过的字符串 → 必须是失败。默认成功正是这一版在清理的那类 bug。"""
    d = FakeDaemon("something-unexpected")
    r = asyncio.run(helpers.click_index(d, 1))
    assert r["ok"] is False and "something-unexpected" in r["error"], r


def test_extension_measures_coverage_before_dispatching():
    """扩展侧：`elementFromPoint` 必须排在派发事件之前。排在之后的话，点击一旦
    触发导航，量到的就是新页面（甚至量不到），诊断位永远是错的。"""
    ext = os.path.join(os.path.dirname(__file__), "..", "extension", "background.js")
    with open(ext, encoding="utf-8") as f:
        src = f.read()
    start = src.index("case 'clickIndex'")
    end = src.index("case '", start + 10)
    block = src[start:end]
    assert "hitAt(" in block, "clickIndex 没测量遮挡"
    assert block.index("hitAt(") < block.index("dispatchEvent"), \
        "遮挡测量排在派发之后，量到的会是点击后的 DOM"
    assert "clicked-covered:" in block, "没有把遮挡结果带回给调用方"


def _op_block(name):
    ext = os.path.join(os.path.dirname(__file__), "..", "extension", "background.js")
    with open(ext, encoding="utf-8") as f:
        src = f.read()
    start = src.index("case '%s'" % name)
    end = src.index("case '", start + 10)
    return src[start:end]


def test_click_op_dispatches_to_the_element_not_the_thing_covering_it():
    """**这条是补的空档。** 扩展 `click` op 原来用
    `document.elementFromPoint(cx, cy) || el` 当派发目标——目标被浮层盖住时，
    事件就落在遮挡物上，调用方拿到 ok:true 而页面纹丝不动。

    实测过：用旧写法时 click_selector / click(css:) 在遮挡下正是「返回成功却不导航」。
    而把它改回去时**38 个测试文件一个都不红**——静默空点能原样复活而 CI 全绿。
    所以这里直接钉住那行的形状。
    """
    block = _op_block("click")
    assert "hitAt(" in block, "click op 没走统一的命中判定"
    assert "elementFromPoint(cx, cy) || el" not in block,         "又退回「盖住就派发给遮挡物」的写法了"
    assert "covered ? el :" in block,         "被盖住时必须直接派发给 el 本身，而不是中心点上那个元素"


def test_every_click_op_reports_coverage():
    """四个点击 op 的诊断位要一致：调用方拿到的信息不该随走哪条路而变。
    clickText 一度是唯一不报 covered 的，纯粹是漏了。

    **断言不能只查 "covered" 这个子串**——它在注释里也出现，把整个计算掏空、只留
    一句含该字样的注释，用例照样绿（独立复核实测：39 个文件一个都不红）。
    所以查两样只可能出现在真代码里的东西：调用了 hitAt()，以及返回值里那个字面量。
    """
    for name in ("click", "clickIndex", "clickText"):
        block = _op_block(name)
        assert "hitAt(" in block, f"{name} op 没有真的去算遮挡"
        assert "clicked-covered" in block, f"{name} op 没有把遮挡结果带回给调用方"


def test_coverage_is_not_claimed_when_the_viewport_is_zero():
    """视口 0×0 时 elementFromPoint 恒为 null —— 那不是「被盖住」，是判据不成立。

    不加这层判断的话，窗口最小化时每一次点击都挂着一个假的 covered，
    比没有诊断更糟：它会把排查引向根本不存在的遮挡物。
    """
    ext = os.path.join(os.path.dirname(__file__), "..", "extension", "background.js")
    with open(ext, encoding="utf-8") as f:
        src = f.read()
    start = src.index("function hitAt(")
    block = src[start:start + 600]
    assert "innerWidth" in block and "innerHeight" in block,         "hitAt 没有排除视口 0×0，最小化时会假报 covered"
    assert block.index("innerWidth") < block.index("elementFromPoint"),         "视口判断必须在 elementFromPoint 之前"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
