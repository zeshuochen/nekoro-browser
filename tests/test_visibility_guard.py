"""test_visibility_guard.py — 视口 0×0 时找元素类 op 必须报错，不能静默给空结果。

守的 bug：窗口最小化 / 标签没在渲染时 `innerWidth` 为 0，于是**所有块级元素**的
rect 宽度都是 0。`isVisible()` 据此判定「不可见」，而调用方拿到的是
`{"ok": true, "result": []}` —— 和「页面上真的没有这段文字」一模一样。实测
`find_text("Example Domain")` 返回空数组、`state()` 从 3 条缩到 1 条（只有行内
元素幸存），全都带着 ok:true。无人值守的 agent 会据此走错分支。

`capture_screenshot()` 对完全相同的条件早有 `kind: not_rendered` 防护并给出可行建议
（先 switch_tab / Page.bringToFront）；它的兄弟函数没有。这里守的是补齐后的口径。

防护放在 `isVisible` 而不是逐个 op 上：受影响的 op 是开放集合（findText / state /
dump / clickIndex / waitSelector…），枚举必漏，而它们的共同依赖只有这一处。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

EXT = os.path.join(os.path.dirname(__file__), "..", "extension")


def _is_visible_body():
    with open(os.path.join(EXT, "background.js"), encoding="utf-8") as f:
        src = f.read()
    start = src.index("function isVisible(el)")
    end = src.index("getComputedStyle(el)", start)
    return src[start:end]


def test_guard_exists_and_is_tagged():
    """必须真的抛，而且带一个稳定标记——调用方要能把「没渲染」和「没找到」分开。"""
    body = _is_visible_body()
    assert re.search(r"innerWidth|innerHeight", body), \
        "isVisible 里没有视口检查：最小化时会把「不可见」当成「不存在」"
    assert "throw" in body, "只是 return false 的话，调用方仍然分不清空结果的原因"
    m = re.search(r"throw new Error\('([^']+)'", body)
    assert m and m.group(1).startswith("not_rendered"), \
        "抛出的消息要以 not_rendered 打头，跟 capture_screenshot 的 kind 对齐"


def test_guard_runs_before_the_rect_is_trusted():
    """视口检查必须在读 getBoundingClientRect 之前——否则 0 宽的 rect 已经被当真了。"""
    body = _is_visible_body()
    guard = min((body.index(k) for k in ("innerWidth", "innerHeight") if k in body),
                default=-1)
    rect = body.index("getBoundingClientRect")
    assert 0 <= guard < rect, "视口检查排在了 rect 之后，等于没防"


def test_ops_that_ignore_visibility_are_untouched():
    """不看可见性的 op（title/url/text/html/getMarkdown）在最小化时仍应可用——
    防护只该拦「答案会是错的」那些，别顺手把还能用的也拦了。"""
    with open(os.path.join(EXT, "background.js"), encoding="utf-8") as f:
        src = f.read()
    for op in ("case 'title'", "case 'url'", "case 'text'", "case 'html'",
               "case 'getMarkdown'"):
        i = src.index(op)
        j = src.index("case '", i + 10)
        assert "isVisible(" not in src[i:j], f"{op} 意外依赖了 isVisible"


if __name__ == "__main__":
    test_guard_exists_and_is_tagged()
    test_guard_runs_before_the_rect_is_trusted()
    test_ops_that_ignore_visibility_are_untouched()
    print("ALL OK")
