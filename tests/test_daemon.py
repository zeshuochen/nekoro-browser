"""test_daemon.py — daemon 事件缓冲有界性（无需浏览器）。

验证 _queue_event 满了丢最旧（环形），drain_events 保持 FIFO 顺序。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser.daemon import Daemon, EVENT_BUFFER_MAX


async def run():
    d = Daemon()

    # 灌入超上限的事件；应只保留最近 EVENT_BUFFER_MAX 个
    total = EVENT_BUFFER_MAX + 5
    for i in range(total):
        d._queue_event(f"E{i}", {"n": i})

    events = await d.drain_events()
    assert len(events) == EVENT_BUFFER_MAX, len(events)
    # 丢掉最旧 5 个（E0..E4），保留 E5..E{total-1}，FIFO 顺序不变
    assert events[0]["method"] == "E5", events[0]
    assert events[-1]["method"] == f"E{total - 1}", events[-1]
    assert events[0]["params"] == {"n": 5}

    # drain 后清空
    assert await d.drain_events() == []

    await _exec_namespace_binding(d)
    await _site_functions_in_namespace(d)
    print("ALL OK")


async def _site_functions_in_namespace(d):
    """用户目录里按站点固化的函数要能直接在 /exec 里调到，且不能顶掉核心 helper。"""
    import os
    import tempfile
    import textwrap
    from pathlib import Path
    from nekoro_browser import site_notes

    # 第二个函数故意和核心 helper 重名：必须被跳过，
    # 不能让站点脚本悄悄改掉 page_info 的语义（那是最难查的一类 bug）
    body = textwrap.dedent("""
        async def my_site_flow(daemon, who):
            return {'ok': True, 'who': who}

        async def page_info(daemon):
            return 'HIJACKED'
    """)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "example" / "flow.py"
        f.parent.mkdir(parents=True)
        f.write_text(body, encoding="utf-8")
        old = os.environ.get(site_notes.ENV_VAR)
        os.environ[site_notes.ENV_VAR] = td
        try:
            r = await d._on_exec("my_site_flow('nekoro')")
            assert r["ok"] and r["result"] == {"ok": True, "who": "nekoro"}, r
            # 同名冲突被拒，并且记了原因（不能静默）
            assert any("page_info" in e for e in d._site_errors), d._site_errors
        finally:
            if old is None:
                os.environ.pop(site_notes.ENV_VAR, None)
            else:
                os.environ[site_notes.ENV_VAR] = old


async def _exec_namespace_binding(d):
    """exec 命名空间：协程 helper 绑 daemon，同步的不绑。

    曾经无差别 partial(fn, daemon)，于是 SKILL.md 里写着的 `list_helpers()`
    一调就 TypeError: takes 0 positional arguments but 1 was given。
    这条不碰浏览器——list_helpers 纯本地。
    """
    r = await d._on_exec("list_helpers()")
    assert r["ok"], r
    assert isinstance(r["result"], list) and "page_info" in r["result"], r
    # 协程 helper 仍然是绑好 daemon 的（调用方不传 daemon）
    r = await d._on_exec("sleep(0)")
    assert r["ok"], r


if __name__ == "__main__":
    asyncio.run(run())
