"""test_keepalive.py — SW-3 content-script 心跳的结构性校验。
扩展 JS 无运行时单测面，这里静态断言：manifest 合法 + content_scripts schema +
引用文件存在 + keepalive.js 含重连/心跳且不碰 DOM + background 含 onConnect 处理。"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

EXT = os.path.join(os.path.dirname(__file__), "..", "extension")


def _read(name):
    with open(os.path.join(EXT, name), encoding="utf-8") as f:
        return f.read()


def test_manifest_content_scripts_schema():
    m = json.loads(_read("manifest.json"))            # 合法 JSON（改错会在此抛）
    cs = m.get("content_scripts")
    assert isinstance(cs, list) and len(cs) == 1, "content_scripts 应恰一条"
    e = cs[0]
    assert e["matches"] == ["<all_urls>"], e.get("matches")
    assert e["js"] == ["keepalive.js"], e.get("js")
    assert e["run_at"] == "document_idle", e.get("run_at")
    assert e["all_frames"] is False, e.get("all_frames")


def test_referenced_files_exist():
    m = json.loads(_read("manifest.json"))
    for f in m["content_scripts"][0]["js"]:
        assert os.path.exists(os.path.join(EXT, f)), f"content_scripts 引用文件不存在: {f}"
    assert os.path.exists(os.path.join(EXT, m["background"]["service_worker"]))


def test_keepalive_reconnect_and_heartbeat():
    src = _read("keepalive.js")
    assert "chrome.runtime.connect" in src and "'keepalive'" in src, "缺具名 keepalive Port"
    assert "onDisconnect" in src, "缺 onDisconnect（断线不重连 = 达不成唤醒目标）"
    assert "setTimeout" in src, "onDisconnect 内要 setTimeout backoff 重连"
    assert "postMessage" in src, "缺心跳 ping"


def test_keepalive_no_dom_access():
    # 极简可审计：只 connect+postMessage，零 DOM（过商店审查、降隐私顾虑）
    src = _read("keepalive.js")
    for banned in ["document", "window.", "innerHTML", "querySelector", "location"]:
        assert banned not in src, f"心跳脚本不应出现 {banned!r}（应零 DOM/页面访问）"


def test_keepalive_iife_and_invalidated_bail():
    src = _read("keepalive.js")
    assert "(function" in src and "})();" in src, "应为 IIFE 包裹（零全局泄漏）"
    # 扩展重载后孤儿脚本要 bail，不死循环
    assert "chrome.runtime.id" in src, "缺 context 失效检测（重载后会 1/秒空转）"


def test_background_onconnect_keepalive():
    src = _read("background.js")
    assert "onConnect" in src, "background 缺 onConnect 处理心跳 Port"
    assert "'keepalive'" in src, "onConnect 应辨识 keepalive Port"
    assert "!== 'keepalive'" in src, "onConnect 应有 name 守卫，别处理其它 Port"
    # 唤醒后补 WS
    assert "connect()" in src


if __name__ == "__main__":
    test_manifest_content_scripts_schema()
    test_referenced_files_exist()
    test_keepalive_reconnect_and_heartbeat()
    test_keepalive_no_dom_access()
    test_keepalive_iife_and_invalidated_bail()
    test_background_onconnect_keepalive()
    print("ALL OK")
