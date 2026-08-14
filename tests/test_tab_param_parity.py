"""test_tab_param_parity.py — 页面级函数必须能指定 tab（纯内省，不碰浏览器）。

为什么要有这个门：daemon 有"当前活动标签"这个隐式状态，函数不收 tab= 就只能打它。
于是"截图 A、点击 B"这种错位调用**静默生效**——点到别的页面上，不报错、不抛异常，
排查时看不出任何线索。给页面级函数一律配上 tab=，调用方才有办法把意图写清楚。

三个桶，每个 helper 必须且只能落进一个：
  ROUTABLE  — 作用于某个具体页面，必须有 tab=
  AGNOSTIC  — 天然不针对单页（标签管理、纯 HTTP、进程控制），不该有 tab=
  PENDING   — 应该有但还没补上的欠账，明确列出来而不是假装不存在

新增 helper 不在任何桶里就会红。这是为了逼作者当场分类，而不是几个月后
再来一次"到底哪些函数能指定 tab"的考古。
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers

ROUTABLE = {
    "box_of", "capture_screenshot", "click", "click_at_xy", "click_index",
    "click_selector", "click_text", "close_tab", "dialog_off", "fill_input",
    "find_text", "get_markdown", "hover", "hover_index", "js", "navigate",
    "page_html", "page_info", "page_text", "press_key", "refs",
    "scroll_into_view", "state", "type_text", "wait_for_load", "wait_selector",
}

AGNOSTIC = {
    # 标签管理本身：tab 是它们的产出或入参语义，不是路由目标
    "list_tabs", "new_tab", "ensure_tab", "ensure_real_tab", "switch_tab",
    "close_tabs", "sweep_tabs",
    # 原始逃生舱：调用方自己在 params 里带 tab
    "cdp", "cdp_batch",
    # 不经过页面
    "http_get", "sleep", "drain_events", "list_site_actions",
    "reload_extension", "reload_agent_helpers",
    # 浏览器级 / 全局队列
    "get_last_dialog", "wait_for_download",
}

PENDING = {
    "click_ref", "get_cookies", "get_response_body", "iframe_target",
    "network_enable", "scroll_to", "scroll_wheel", "set_cookie",
    "upload_file", "wait_for_network_idle",
}


def main():
    names = {n for n in helpers.list_helpers()
             if inspect.iscoroutinefunction(getattr(helpers, n, None))}

    # 1. 分类完备：没有未归类的 helper，也没有指向已删函数的陈旧条目
    unclassified = names - ROUTABLE - AGNOSTIC - PENDING
    assert not unclassified, f"新 helper 未分类，请归入 ROUTABLE/AGNOSTIC/PENDING: {sorted(unclassified)}"
    stale = (ROUTABLE | AGNOSTIC | PENDING) - names
    assert not stale, f"分类表里有已不存在的函数: {sorted(stale)}"

    # 2. ROUTABLE 必须真的收 tab=，且默认 None（不传时保持打活动标签的旧行为）
    missing, bad_default = [], []
    for n in sorted(ROUTABLE):
        p = inspect.signature(getattr(helpers, n)).parameters.get("tab")
        if p is None:
            missing.append(n)
        elif p.default is not None:
            bad_default.append(f"{n}(tab={p.default!r})")
    assert not missing, f"页面级函数缺 tab= 参数: {missing}"
    assert not bad_default, f"tab 默认值必须是 None: {bad_default}"

    # 3. AGNOSTIC 不该有 tab=（有了说明分类错了，或者该挪进 ROUTABLE）
    surprising = [n for n in sorted(AGNOSTIC)
                  if "tab" in inspect.signature(getattr(helpers, n)).parameters]
    assert not surprising, f"标为 AGNOSTIC 却有 tab= 参数，分类需复核: {surprising}"

    print(f"ALL OK  (routable={len(ROUTABLE)} agnostic={len(AGNOSTIC)} pending={len(PENDING)})")


if __name__ == "__main__":
    main()
