"""test_text_locator_engine.py — getRectByText 必须用 findFirstText 那套引擎。

扩展是 JS，Python 单测跑不了它的运行时，所以这里做结构性校验（同 test_keepalive.py 的路子）。

背景：`getRectByText`（click_text / click("text:") 的落点来源）原来自己写了一遍朴素查找，
和 `find_text` 用的 findTextElements 是两套，真机实测出两个 bug：

  ① 无可见性检查：display:none 的元素排在前面就被选中，rect 是 0×0 → 点在 (0,0)、
     什么也没点到，却一路返回 ok:True（静默成功）
  ② 只认 childNodes.length===1 的纯文本叶节点：`保存 <b>*</b>` 这种文本与元素混排的
     按钮整个被跳过 → text not found

于是 find_text 找得到、click_text 点不到——两个本该配套使用的 helper 互相矛盾。
"""
import os

_EXT = os.path.join(os.path.dirname(__file__), "..", "extension", "background.js")


def _get_rect_by_text_body():
    src = open(_EXT, encoding="utf-8").read()
    i = src.index("case 'getRectByText'")
    j = src.index("case 'getRectByIndex'", i)
    return src[i:j]


def test_uses_the_shared_finder():
    body = _get_rect_by_text_body()
    assert "findFirstText(" in body, \
        "getRectByText 必须走 findFirstText（find_text 用的同一套引擎），别再自己写一遍"


def test_does_not_reintroduce_the_naive_scan():
    body = _get_rect_by_text_body()
    assert "querySelectorAll('*')" not in body, "朴素全量扫描回来了"
    assert "childNodes.length === 1" not in body, \
        "只认纯文本叶节点的判断回来了：`保存 <b>*</b>` 这类按钮会被跳过"


def test_still_scrolls_before_measuring():
    """rect 是视口坐标，视口外不滚就是点空还报 ok。"""
    body = _get_rect_by_text_body()
    assert "scrollIntoViewIfNeeded" in body, body
    assert body.index("scrollIntoViewIfNeeded") < body.index("getBoundingClientRect"), \
        "必须先滚再量，反了等于没滚"


def test_finder_itself_filters_invisible():
    """findFirstText 的价值全在 isVisible 上——它要是没了，①号 bug 就回来了。"""
    src = open(_EXT, encoding="utf-8").read()
    i = src.index("function findTextElements")
    j = src.index("function findFirstText")
    assert "isVisible(el)" in src[i:j], "findTextElements 丢了可见性过滤"


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
