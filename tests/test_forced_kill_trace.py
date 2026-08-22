"""test_forced_kill_trace.py — 强杀 daemon 时必须往它的日志里留一行。

为什么要有这条：优雅停会由 daemon 自己写下 `shutdown requested` + `daemon exiting`
两行；而 `stop_daemon()` 兜底走 `os.kill`（Windows 上就是 TerminateProcess）时，
被杀的进程**没有任何机会写日志**。

缺这一行的后果很实在：日志上「有人强制结束了它」和「它自己无声消失了」长得一模一样
——而后者是这个项目至今没定位的那个偶发问题。两轮独立复核都在这个签名上卡过：
`/exec 403 + /shutdown 403 + 无退出留痕` 现在既可能是已修的 `_run()` 老 bug，
也可能是 0.3.5 里故意的外部 `--stop` 强杀。混在一起就永远查不清。

这里不起真进程：`stop_daemon()` 的兜底 kill 需要「identify 认得出、pid 还活着、
优雅停没生效」这一整套世界，用假件搭出来比拉真 daemon 稳得多，也不会误杀本机上
真在跑的东西。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import lifecycle


def _isolated_data_dir():
    d = tempfile.mkdtemp(prefix="nekoro-kill-trace-")
    os.environ["NEKORO_DATA_DIR"] = d
    return d


def _fake_world(monkey, *, pid, still_alive=True):
    """让 stop_daemon 走到兜底 kill：认得出身份、pid 一直活着、优雅停不生效。"""
    monkey["identify"] = lifecycle.identify
    monkey["_alive_ping"] = lifecycle._alive_ping
    monkey["_pid_alive"] = lifecycle._pid_alive
    monkey["_request_shutdown"] = lifecycle._request_shutdown
    monkey["_process_start_time"] = lifecycle._process_start_time
    monkey["cleanup_pid"] = lifecycle.cleanup_pid

    lifecycle.identify = lambda timeout=2.0: pid
    lifecycle._alive_ping = lambda timeout=1.0: True
    lifecycle._pid_alive = lambda p: still_alive
    lifecycle._request_shutdown = lambda: None          # 优雅停不生效
    lifecycle._process_start_time = lambda p: 12345     # 身份指纹不变
    lifecycle.cleanup_pid = lambda: None


def _restore(monkey):
    for k, v in monkey.items():
        setattr(lifecycle, k, v)


def test_forced_kill_leaves_a_trace_in_the_log():
    _isolated_data_dir()
    monkey = {}
    _fake_world(monkey, pid=4242)
    real_kill = os.kill
    os.kill = lambda p, sig: None          # 别真杀本机进程
    try:
        lifecycle.stop_daemon()
        log = lifecycle.daemon_log_path()
        assert log.is_file(), "强杀之后日志文件都没建，等于什么都没留下"
        text = log.read_text(encoding="utf-8")
        assert "force-killed" in text, f"强杀没留痕，将来无法与「无声消失」区分: {text!r}"
        assert "4242" in text, f"留痕里没写是哪个 pid: {text!r}"
    finally:
        os.kill = real_kill
        _restore(monkey)


def test_trace_is_written_before_the_kill():
    """必须先写再杀。反过来的话，杀进程若连带把调用方也带走（或抛异常提前 return），
    这一行就永远写不出来——而那正是最需要它的时候。"""
    _isolated_data_dir()
    monkey = {}
    _fake_world(monkey, pid=777)
    order = []
    real_kill = os.kill
    real_note = lifecycle._note_forced_kill

    def note(pid):
        order.append("note")
        return real_note(pid)

    os.kill = lambda p, sig: order.append("kill")
    lifecycle._note_forced_kill = note
    try:
        lifecycle.stop_daemon()
        assert order == ["note", "kill"], f"顺序不对: {order}"
    finally:
        os.kill = real_kill
        lifecycle._note_forced_kill = real_note
        _restore(monkey)


def test_no_trace_when_the_daemon_exits_gracefully():
    """优雅停不该留这行——它是 daemon 自己写 shutdown/exiting 两行的场景。
    每次停都打一条「force-killed」等于把这个信号稀释成噪音。"""
    _isolated_data_dir()
    monkey = {}
    _fake_world(monkey, pid=999, still_alive=False)     # 优雅停生效，进程自己退了
    real_kill = os.kill
    os.kill = lambda p, sig: None
    try:
        lifecycle.stop_daemon()
        log = lifecycle.daemon_log_path()
        text = log.read_text(encoding="utf-8") if log.is_file() else ""
        assert "force-killed" not in text, f"优雅停也打了强杀留痕: {text!r}"
    finally:
        os.kill = real_kill
        _restore(monkey)


def test_note_never_breaks_stopping():
    """写日志失败（目录只读、盘满…）绝不能把停止本身搞失败。
    为了留痕让 --stop 失效，是本末倒置。"""
    _isolated_data_dir()
    monkey = {}
    _fake_world(monkey, pid=555)
    real_kill, real_path = os.kill, lifecycle.daemon_log_path
    killed = []
    os.kill = lambda p, sig: killed.append(p)

    def boom():
        raise OSError("disk full")

    lifecycle.daemon_log_path = boom
    try:
        lifecycle.stop_daemon()               # 不该抛
        assert killed == [555], "写日志失败把停止也带挂了"
    finally:
        os.kill = real_kill
        lifecycle.daemon_log_path = real_path
        _restore(monkey)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
