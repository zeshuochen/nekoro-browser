"""test_iframe_target.py — iframe_target 必须走 page-level 的 Page.getFrameTree。

原实现用 `Target.getTargets`，那是 browser-level 命令，chrome.debugger 的 tab attach
下恒返回 `Not allowed` —— 这个 helper 从第一个 commit 起没成功过一次。异常被吞成
`{"ok": false, "error": ...}`，读起来就像「这个 iframe 不存在」，所以一直没人发现。

同一堵墙上撞死的还有 Browser.setDownloadBehavior（自定义下载目录，已确认不可行）。
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ["NEKORO_DOMAIN_SKILLS"] = tempfile.mkdtemp(prefix="nekoro-empty-skills-")

from nekoro_browser import helpers


# 真 wire 形状：Page.getFrameTree 的嵌套 frameTree，根框架无 parentId
FRAME_TREE = {
    "frameTree": {
        "frame": {"id": "ROOT", "url": "http://127.0.0.1:8899/"},
        "childFrames": [
            {"frame": {"id": "F1", "parentId": "ROOT",
                       "url": "http://127.0.0.1:8899/frame.html"}},
            {"frame": {"id": "F2", "parentId": "ROOT",
                       "url": "https://cdn.example.com/player?v=1"},
             "childFrames": [
                 {"frame": {"id": "F3", "parentId": "F2",
                            "url": "https://ads.example.com/deep"}}]},
        ],
    }
}


class FakeDaemon:
    def __init__(self, tree=None, error=None):
        self.tree = FRAME_TREE if tree is None else tree
        self.error = error
        self.methods = []

    async def _send(self, method, params, tab=None, **kw):
        self.methods.append(method)
        if self.error:
            raise RuntimeError(self.error)
        if method == "Page.getFrameTree":
            return self.tree
        raise AssertionError(f"unexpected CDP method {method}")

    @property
    def bridge(self):
        return type("B", (), {"send": self._send})()


def run(coro):
    return asyncio.run(coro)


def test_uses_page_level_command_not_browser_level():
    """browser-level 的 Target.* 在 tab attach 下恒 'Not allowed'——一次都不许发。"""
    d = FakeDaemon()
    run(helpers.iframe_target(d, "frame.html"))
    assert d.methods == ["Page.getFrameTree"], d.methods
    assert not any(m.startswith(("Target.", "Browser.")) for m in d.methods), d.methods


def test_finds_child_frame_and_returns_frame_id():
    d = FakeDaemon()
    r = run(helpers.iframe_target(d, "player"))
    assert r["ok"] is True, r
    assert r["frameId"] == "F2" and "player" in r["url"], r


def test_finds_nested_frame():
    """iframe 里还能套 iframe，别只看第一层。"""
    d = FakeDaemon()
    r = run(helpers.iframe_target(d, "ads.example.com"))
    assert r["ok"] is True and r["frameId"] == "F3", r


def test_root_frame_never_matches():
    """根框架就是当前页；匹配上会把主页面当 iframe 返回，比报没找到更糟。"""
    d = FakeDaemon()
    r = run(helpers.iframe_target(d, "127.0.0.1:8899"))
    assert r["ok"] is True and r["frameId"] == "F1", r      # 命中子框架而非 ROOT
    r2 = run(helpers.iframe_target(d, "127.0.0.1:8899/"))   # 只有根框架以 / 结尾
    assert r2["frameId"] != "ROOT", r2


def test_no_match_is_transient():
    d = FakeDaemon()
    r = run(helpers.iframe_target(d, "nonexistent"))
    assert r["ok"] is False and r["kind"] == "transient", r


def test_bridge_error_surfaces_with_kind():
    d = FakeDaemon(error="Not allowed")
    r = run(helpers.iframe_target(d, "x"))
    assert r["ok"] is False and "Not allowed" in r["error"] and r["kind"] == "transient", r


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
