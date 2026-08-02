"""test_site_notes.py — 站点笔记随导航主动送达。无需浏览器。

设计要点在测试里钉住：
- 命中才加 `notes` 键（没笔记的站点不该多出空字段）
- 只给清单+标题，不给正文（否则每次导航都付一遍全文的 token）
- 查询出任何问题都必须静默降级，绝不能连累导航本身
"""
import asyncio
import contextlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers, site_notes


@contextlib.contextmanager
def _skills(tree):
    """临时 domain-skills 目录。tree = {"douyin/creator-stats.md": "# 抖音创作者中心"}"""
    with tempfile.TemporaryDirectory() as td:
        for rel, body in tree.items():
            p = Path(td) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        old = os.environ.get(site_notes.ENV_VAR)
        os.environ[site_notes.ENV_VAR] = td
        try:
            yield td
        finally:
            os.environ.pop(site_notes.ENV_VAR, None) if old is None else \
                os.environ.__setitem__(site_notes.ENV_VAR, old)


class FakeDaemon:
    def __init__(self):
        self.nav = []

    async def navigate(self, url):
        self.nav.append(url)
        return {"frameId": "1"}

    async def evaluate(self, code):
        return {"result": {"value": "complete"}}


def test_matches_subdomains_and_reports_title():
    with _skills({"douyin/creator-stats.md": "# 抖音创作者中心 — 数据统计\n正文很长" * 50}):
        for url in ("https://www.douyin.com/x", "https://creator.douyin.com/y"):
            n = site_notes.notes_for(url)
            assert n == ["douyin/creator-stats.md — 抖音创作者中心 — 数据统计"], (url, n)
        # 只给标题，不给正文：清单长度必须和文件大小无关
        assert all(len(x) < 120 for x in n)


def test_no_match_returns_empty():
    with _skills({"douyin/a.md": "# A"}):
        assert site_notes.notes_for("https://example.com/") == []
        assert site_notes.notes_for("not-a-url") == []
        assert site_notes.notes_for("") == []


def test_missing_dir_and_broken_input_never_raise():
    old = os.environ.get(site_notes.ENV_VAR)
    try:
        os.environ[site_notes.ENV_VAR] = "Z:/definitely/not/here"
        assert site_notes.notes_for("https://www.douyin.com/") == []
    finally:
        os.environ.pop(site_notes.ENV_VAR, None) if old is None else \
            os.environ.__setitem__(site_notes.ENV_VAR, old)


def test_cap_on_number_of_files():
    tree = {f"douyin/n{i}.md": f"# note {i}" for i in range(20)}
    with _skills(tree):
        assert len(site_notes.notes_for("https://www.douyin.com/")) == site_notes.MAX_FILES


def test_navigate_attaches_notes_only_on_hit():
    async def run():
        d = FakeDaemon()
        with _skills({"douyin/tips.md": "# 抖音要点"}):
            hit = await helpers.navigate(d, "https://www.douyin.com/discover")
            miss = await helpers.navigate(d, "https://example.com/")
        return hit, miss

    hit, miss = asyncio.run(run())
    assert hit["ok"] and hit["notes"] == ["douyin/tips.md — 抖音要点"]
    assert "notes" not in miss, miss        # 没笔记就不该多出这个键


def test_notes_lookup_failure_does_not_break_navigation():
    """笔记查询炸了也只能少一个字段，导航结果必须照常返回。"""
    real = site_notes.notes_for
    try:
        site_notes.notes_for = lambda url: (_ for _ in ()).throw(RuntimeError("boom"))
        r = site_notes.attach({"ok": True, "loaded": True}, "https://www.douyin.com/")
        assert r == {"ok": True, "loaded": True}
    except RuntimeError:
        raise AssertionError("attach 必须吞掉查询异常，不能向上抛")
    finally:
        site_notes.notes_for = real


# ── 站点脚本：载入、路由信号、错误可见性 ─────────────────────────────────

_ACTION_PY = '''
async def douyin_like(daemon, username, video_index=0):
    """搜索用户并给第 N 个视频点赞。"""
    return {"ok": True, "who": username}

def _private(daemon):
    return "should not be exported"
'''


def test_actions_are_listed_without_importing():
    """签名清单走 ast 解析——列个目录不该执行用户代码。"""
    with _skills({"douyin/actions.py": "raise RuntimeError('import 就炸')\n" + _ACTION_PY}):
        acts = site_notes.actions_for("https://www.douyin.com/")
        assert acts == ["douyin_like(username, video_index) — 搜索用户并给第 N 个视频点赞。"], acts


def test_navigate_surfaces_actions_for_routing():
    async def run():
        d = FakeDaemon()
        with _skills({"douyin/actions.py": _ACTION_PY, "douyin/notes.md": "# 抖音要点"}):
            return await helpers.navigate(d, "https://www.douyin.com/x")

    r = asyncio.run(run())
    assert r["notes"] == ["douyin/notes.md — 抖音要点"]
    assert r["actions"] and r["actions"][0].startswith("douyin_like(")


def test_load_functions_skips_private_and_reports_broken_file():
    with _skills({"douyin/actions.py": _ACTION_PY,
                  "broken/bad.py": "def oops(:\n"}):
        ns, errors = site_notes.load_functions()
        assert "douyin_like" in ns
        assert "_private" not in ns                    # 下划线开头不导出
        assert len(errors) == 1 and "broken/bad.py" in errors[0], errors
        # 一个坏文件不能连累其他站点的函数
        assert callable(ns["douyin_like"])


def test_load_functions_ignores_imported_names():
    """`from x import y` 带进来的名字不该被当成站点函数导出。"""
    with _skills({"douyin/a.py": "import json\nfrom pathlib import Path\n"
                                 "async def real(daemon):\n    return 1\n"}):
        ns, _ = site_notes.load_functions()
        assert set(ns) == {"real"}, set(ns)


if __name__ == "__main__":
    test_matches_subdomains_and_reports_title()
    test_no_match_returns_empty()
    test_missing_dir_and_broken_input_never_raise()
    test_cap_on_number_of_files()
    test_navigate_attaches_notes_only_on_hit()
    test_notes_lookup_failure_does_not_break_navigation()
    test_actions_are_listed_without_importing()
    test_navigate_surfaces_actions_for_routing()
    test_load_functions_skips_private_and_reports_broken_file()
    test_load_functions_ignores_imported_names()
    print("ALL OK")
