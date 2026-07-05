"""test_upload.py — upload_file helper。无需浏览器，用 fake daemon/bridge。"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


class FakeBridge:
    def __init__(self):
        self.sends = []                       # 记录 (method, params)

    async def send(self, method, params=None):
        self.sends.append((method, params))
        return {}


class FakeDaemon:
    def __init__(self, nid=7):
        self.bridge = FakeBridge()
        self._nid = nid                        # query_selector 返回值（0=找不到）

    async def query_selector(self, sel):
        return self._nid


def test_upload_success():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
        path = f.name
    try:
        d = FakeDaemon(nid=7)
        r = asyncio.run(helpers.upload_file(d, "input[type=file]", path))
        assert r["ok"] is True, r
        assert r["files"] == [path]
        # 发了正确的 CDP 命令 + nodeId
        assert d.bridge.sends == [("DOM.setFileInputFiles",
                                   {"nodeId": 7, "files": [path]})]
    finally:
        os.unlink(path)


def test_upload_multiple():
    files = []
    for _ in range(2):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            files.append(f.name)
    try:
        d = FakeDaemon(nid=9)
        r = asyncio.run(helpers.upload_file(d, "#f", files))
        assert r["ok"] is True and r["files"] == files
        assert d.bridge.sends[0][1] == {"nodeId": 9, "files": files}
    finally:
        for p in files:
            os.unlink(p)


def test_upload_file_missing():
    d = FakeDaemon(nid=7)
    r = asyncio.run(helpers.upload_file(d, "#f", "Z:\\nope\\nope.png"))
    assert r["ok"] is False and "not found" in r["error"]
    assert d.bridge.sends == [], "文件不存在时不该发 CDP"


def test_upload_element_not_found():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        path = f.name
    try:
        d = FakeDaemon(nid=0)                  # querySelector 未命中
        r = asyncio.run(helpers.upload_file(d, "#nope", path))
        assert r["ok"] is False and "element not found" in r["error"]
        assert d.bridge.sends == [], "找不到元素不该发 CDP"
    finally:
        os.unlink(path)


def test_upload_cdp_error():
    # 元素非 file input：真实 CDP 会报错，setFileInputFiles 抛 → catch 转 ok:false
    class RaisingBridge(FakeBridge):
        async def send(self, method, params=None):
            raise RuntimeError("Node is not a file input element")

    with tempfile.NamedTemporaryFile(delete=False) as f:
        path = f.name
    try:
        d = FakeDaemon(nid=7)
        d.bridge = RaisingBridge()
        r = asyncio.run(helpers.upload_file(d, "#notfile", path))
        assert r["ok"] is False and "file input" in r["error"]
    finally:
        os.unlink(path)


def test_upload_pathlike():
    from pathlib import Path
    with tempfile.NamedTemporaryFile(delete=False) as f:
        path = f.name
    try:
        d = FakeDaemon(nid=3)
        r = asyncio.run(helpers.upload_file(d, "#f", Path(path)))   # 单个 Path
        assert r["ok"] is True and r["files"] == [path]
        d2 = FakeDaemon(nid=4)
        r2 = asyncio.run(helpers.upload_file(d2, "#f", [Path(path)]))  # list of Path
        assert r2["ok"] is True and r2["files"] == [path]
    finally:
        os.unlink(path)


def test_upload_registered():
    assert "upload_file" in helpers.list_helpers()


if __name__ == "__main__":
    test_upload_success()
    test_upload_multiple()
    test_upload_file_missing()
    test_upload_element_not_found()
    test_upload_cdp_error()
    test_upload_pathlike()
    test_upload_registered()
    print("ALL OK")
