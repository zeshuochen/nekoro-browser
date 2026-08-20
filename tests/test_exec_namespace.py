"""test_exec_namespace.py — /exec 的命名空间与 stdout 隔离（无需浏览器）。

守两个真出过的 bug：

1. **globals/locals 分开传** → 脚本里 `import math` / `x = 42` 绑进 locals，而模块级
   `def` 的函数体只查 globals（一个只有 __builtins__ 的空壳），于是
   `import math; def f(): return math.pi; f()` 报 NameError。这直接废掉了
   「多步流程一次发过去」和 agent 自己写脚本。

2. **contextlib.redirect_stdout 改的是进程全局 sys.stdout** → daemon 并发跑多条 exec
   时输出串台。实测：A 客户端超时后协程仍在跑，它后来的 print 落进了 B 的响应里，
   而 B 自己的输出反倒丢到 daemon 控制台（B 拿到 "B_START\\nA_LATE"，B_END 不见）。
"""
import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser.daemon import Daemon


async def test_imports_are_visible_inside_defs():
    d = Daemon()
    r = await d._on_exec("import math\ndef f(): return round(math.pi, 3)\nprint(f())")
    assert r["ok"], r
    assert r["stdout"].strip() == "3.142", r


async def test_variables_are_visible_inside_defs():
    d = Daemon()
    r = await d._on_exec("x = 42\ndef g(): return x * 2\nprint(g())")
    assert r["ok"], r
    assert r["stdout"].strip() == "84", r


async def test_helpers_still_reachable_from_a_def():
    """把 v 同时当 globals 用之后，注入的 helper 也必须还能在函数体里看到——
    否则修好了 import 却把 helper 弄丢，是把 bug 换了个地方。"""
    d = Daemon()
    r = await d._on_exec("def n(): return len(list_helpers())\nprint(n() > 40)")
    assert r["ok"], r
    assert r["stdout"].strip() == "True", r


async def test_single_expression_still_returns_result():
    """单表达式那条老路（返回 result 字段）不能被 globals 改动带坏。"""
    d = Daemon()
    r = await d._on_exec("2 + 3")
    assert r["ok"] and r["result"] == 5, r


async def test_concurrent_execs_do_not_cross_stdout():
    """两条并发 exec，各自的 print 只能落进自己的响应。

    慢的那条故意跨过快的那条的整个生命周期——正是线上串台的形状。
    """
    d = Daemon()
    slow = "print('A_START')\nawait sleep(0.45)\nprint('A_LATE')"
    fast = "print('B_START')\nawait sleep(0.1)\nprint('B_END')"
    a, b = await asyncio.gather(d._on_exec(slow), d._on_exec(fast))
    assert a["ok"] and b["ok"], (a, b)
    assert a["stdout"] == "A_START\nA_LATE\n", a["stdout"]
    # B 既不能收到 A 的输出，也不能丢掉自己的 B_END
    assert b["stdout"] == "B_START\nB_END\n", b["stdout"]


async def test_stdout_outside_exec_still_reaches_the_real_stream():
    """代理装上之后，不在 exec 上下文里的 print 必须照常可见——
    daemon 的启动横幅走的就是这条路，吞掉它等于把日志弄丢。"""
    from nekoro_browser.daemon import install_task_routed_stdout
    d = Daemon()
    await d._on_exec("print('inside')")      # 触发安装
    install_task_routed_stdout()
    real = sys.stdout
    sink = io.StringIO()
    try:
        sys.stdout = sink                     # 代理的落回目标
        install_task_routed_stdout()
        print("outside")
        assert "outside" in sink.getvalue(), sink.getvalue()
    finally:
        sys.stdout = real


async def run():
    await test_imports_are_visible_inside_defs()
    await test_variables_are_visible_inside_defs()
    await test_helpers_still_reachable_from_a_def()
    await test_single_expression_still_returns_result()
    await test_concurrent_execs_do_not_cross_stdout()
    await test_stdout_outside_exec_still_reaches_the_real_stream()
    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(run())
