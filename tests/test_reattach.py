"""test_reattach.py — SW-4 结构性校验：autoAttach 记忆上次标签。
扩展 JS 无运行时单测面，这里静态断言：manifest 有 storage 权限 + background 在 attach
落点写 lastTab + autoAttach 优先读 lastTab 并含 tabs.get 守卫/组恢复/回退。"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

EXT = os.path.join(os.path.dirname(__file__), "..", "extension")


def _read(name):
    with open(os.path.join(EXT, name), encoding="utf-8") as f:
        return f.read()


def test_manifest_storage_permission():
    m = json.loads(_read("manifest.json"))          # 合法 JSON
    assert "storage" in m["permissions"], "chrome.storage.session 需 storage 权限"


def test_remembers_tab_on_attach():
    src = _read("background.js")
    # rememberTab 得真定义（缺定义 = ReferenceError，node --check 查不出）
    assert "function rememberTab" in src, "rememberTab 未定义"
    # 写 lastTab 到 session storage
    assert "chrome.storage.session.set" in src and "lastTab" in src, "缺 lastTab 持久化"
    # 两处 attach 落点都调 rememberTab（switch_tab 指针切换 + tryAttach 真 attach）
    assert src.count("rememberTab(id)") >= 2, "attach 落点应都记 lastTab（>=2 处）"


def test_canceled_by_user_clears_lasttab():
    # 用户点调试横幅取消 → 清被取消标签的 lastTab，否则后续 autoAttach 重挂它又弹横幅
    src = _read("background.js")
    assert "canceled_by_user" in src and "chrome.storage.session.remove('lastTab')" in src, \
        "canceled_by_user 应清 lastTab"


def test_autoattach_prefers_last_tab():
    src = _read("background.js")
    assert "chrome.storage.session.get('lastTab')" in src, "autoAttach 应先读 lastTab"
    # tabs.get 守卫：避免对已关标签白跑重试（.catch(()=>null) 是死标签安全的关键）
    assert "chrome.tabs.get(lastTab).catch(() => null)" in src, "tabs.get 应 .catch(()=>null) 兜死标签"
    # 恢复 managedGroupId，避免重挂后又新建组
    assert "managedGroupId = t.groupId" in src, "重挂后应恢复 managedGroupId"


def test_autoattach_falls_back():
    # 优先块必须在原有"找组/建组"逻辑之前，且失败能落到原逻辑（结构上原逻辑仍在）
    src = _read("background.js")
    i_prefer = src.find("session.get('lastTab')")
    i_group = src.find("Find existing nekoro group first")
    assert 0 <= i_prefer < i_group, "lastTab 优先块应在'找组'逻辑之前，失败回退到它"


if __name__ == "__main__":
    test_manifest_storage_permission()
    test_remembers_tab_on_attach()
    test_canceled_by_user_clears_lasttab()
    test_autoattach_prefers_last_tab()
    test_autoattach_falls_back()
    print("ALL OK")
