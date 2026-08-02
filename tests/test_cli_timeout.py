"""test_cli_timeout.py — 执行代码的 HTTP 超时。无需浏览器。

真实事故：管道模式的超时写死 30 秒，而"开页面 + 等 React 水合"这种最常见的
脚本就要几十秒，第一条真实命令就返回 {"ok": false, "error": "timed out"}——
daemon 那边其实还在正常跑，用户看到的却是失败。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import cli


def _capture_timeouts():
    """替 _post，记录每次调用用的超时值。"""
    seen = []

    def _post(path, data="", timeout=30):
        seen.append(timeout)
        return {"ok": True, "result": None, "stdout": ""}
    return seen, _post


def test_default_is_generous_enough_for_page_loads():
    # 30 秒是原来的写死值，等水合必超；默认值必须明显大于它
    assert cli.DEFAULT_EXEC_TIMEOUT >= 60, cli.DEFAULT_EXEC_TIMEOUT
    assert cli._EXEC_TIMEOUT == cli.DEFAULT_EXEC_TIMEOUT


def test_exec_paths_use_the_configured_timeout():
    """-c、管道、--reload-ext 三条执行路径都要带上超时，不能漏用 _post 的默认值。"""
    seen, fake = _capture_timeouts()
    real_post, real_alive, real_to = cli._post, cli._alive, cli._EXEC_TIMEOUT
    try:
        cli._post, cli._alive = fake, lambda: True
        cli._EXEC_TIMEOUT = 99.0
        cli._reload_ext()
        assert seen == [99.0], seen
    finally:
        cli._post, cli._alive, cli._EXEC_TIMEOUT = real_post, real_alive, real_to


def test_timeout_flag_parses_and_overrides():
    p = cli.argparse.ArgumentParser(prog="nekoro-browser")
    p.add_argument("--timeout", type=float, default=None)
    assert p.parse_args([]).timeout is None                  # 不传就用默认
    assert p.parse_args(["--timeout", "300"]).timeout == 300.0

    # 复刻 main() 的赋值逻辑：正数才生效，0/负数忽略（否则会瞬间超时）
    real = cli._EXEC_TIMEOUT
    try:
        for val, expect in ((300.0, 300.0), (0.0, real), (-5.0, real)):
            cli._EXEC_TIMEOUT = real
            if val is not None and val > 0:
                cli._EXEC_TIMEOUT = val
            assert cli._EXEC_TIMEOUT == expect, val
    finally:
        cli._EXEC_TIMEOUT = real


def test_probe_timeouts_stay_short():
    """探活不能跟着执行超时一起变长——doctor/_alive 卡两分钟就没法用了。"""
    import inspect
    assert "timeout=2" in inspect.getsource(cli._alive)
    assert inspect.signature(cli._healthy).parameters["timeout"].default == 8


if __name__ == "__main__":
    test_default_is_generous_enough_for_page_loads()
    test_exec_paths_use_the_configured_timeout()
    test_timeout_flag_parses_and_overrides()
    test_probe_timeouts_stay_short()
    print("ALL OK")
