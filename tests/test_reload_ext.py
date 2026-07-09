"""test_reload_ext.py — CLI --reload-ext 三态 + main() 路由（SW-2）。
无需浏览器：monkeypatch cli._alive / cli._post，验退出码 + 是否发 exec。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import cli

_ORIG = {"alive": cli._alive, "post": cli._post, "reload": cli._reload_ext}


def _restore():
    cli._alive, cli._post, cli._reload_ext = _ORIG["alive"], _ORIG["post"], _ORIG["reload"]


def _patch(alive, post_result, recorder=None):
    cli._alive = lambda: alive
    def _post(path, data="", timeout=30):
        if recorder is not None:
            recorder.append((path, data))
        return post_result
    cli._post = _post


def test_no_daemon_exits_1_no_exec():
    posted = []
    _patch(alive=False, post_result={"ok": True}, recorder=posted)
    try:
        assert cli._reload_ext() == 1
        assert posted == [], "无 daemon 不该发 /exec"
    finally:
        _restore()


def test_ok_exits_0_sends_reload():
    posted = []
    _patch(alive=True, post_result={"ok": True, "result": "reloading"}, recorder=posted)
    try:
        assert cli._reload_ext() == 0
        assert posted == [("/exec", "await reload_extension()")], "应发 reload_extension exec"
    finally:
        _restore()


def test_fail_exits_1_honest():
    _patch(alive=True, post_result={"ok": False, "error": "boom"})
    try:
        assert cli._reload_ext() == 1
    finally:
        _restore()


def test_main_routes_to_reload_ext():
    # 真 wiring：--reload-ext → main() 调 _reload_ext → sys.exit 带其退出码；不落其它分支
    called = []
    cli._reload_ext = lambda: (called.append(True), 3)[1]
    argv = sys.argv
    sys.argv = ["nekoro-browser", "--reload-ext"]
    try:
        try:
            cli.main()
            assert False, "--reload-ext 应 sys.exit"
        except SystemExit as e:
            assert e.code == 3, f"退出码应来自 _reload_ext，got {e.code}"
        assert called == [True], "main() 应路由到 _reload_ext"
    finally:
        sys.argv = argv
        _restore()


if __name__ == "__main__":
    test_no_daemon_exits_1_no_exec()
    test_ok_exits_0_sends_reload()
    test_fail_exits_1_honest()
    test_main_routes_to_reload_ext()
    print("ALL OK")
