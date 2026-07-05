"""test_press_key.py — press_key 带 virtual key code + char 事件 + 快捷键分流。
无需浏览器，用 fake bridge 捕获 dispatchKeyEvent 序列。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nekoro_browser import helpers


class FakeBridge:
    def __init__(self):
        self.events = []                       # 每个 dispatchKeyEvent 的 params

    async def send(self, method, params=None):
        assert method == "Input.dispatchKeyEvent", method
        self.events.append(params)
        return {}


class FakeDaemon:
    def __init__(self):
        self.bridge = FakeBridge()


def run_press(key, modifiers=0):
    d = FakeDaemon()
    r = asyncio.run(helpers.press_key(d, key, modifiers))
    assert r == {"ok": True}, r
    return d.bridge.events


def test_special_key_enter():
    ev = run_press("Enter")
    assert [e["type"] for e in ev] == ["keyDown", "keyUp"], "特殊键无 char 事件"
    kd = ev[0]
    assert kd["windowsVirtualKeyCode"] == 13 and kd["nativeVirtualKeyCode"] == 13
    assert kd["code"] == "Enter" and kd["key"] == "Enter"
    assert kd["text"] == "\r", "Enter 的 text 挂在 keyDown"


def test_arrow_key_no_text():
    ev = run_press("ArrowLeft")
    assert [e["type"] for e in ev] == ["keyDown", "keyUp"]
    assert ev[0]["windowsVirtualKeyCode"] == 37 and ev[0]["code"] == "ArrowLeft"
    assert "text" not in ev[0], "ArrowLeft text='' → keyDown 不带 text"


def test_printable_char_emits_char_event():
    ev = run_press("a")
    assert [e["type"] for e in ev] == ["keyDown", "char", "keyUp"], "可打印字符补 char"
    assert "text" not in ev[0], "keyDown 不带 text（留给 char）"
    assert ev[1]["text"] == "a", "char 事件带字符"
    assert ev[0]["windowsVirtualKeyCode"] == ord("a")


def test_ctrl_shortcut_no_char():
    ev = run_press("a", modifiers=2)             # Ctrl+A
    # 关键：不发 char 事件（否则 Chrome 把 Ctrl+A 当打字符 'a'，select-all 失效）。
    # keyDown 仍带 text（对齐 harness），但有 char 才会真输入字符，此处无 char。
    assert [e["type"] for e in ev] == ["keyDown", "keyUp"], "快捷键不发 char"
    assert ev[0]["modifiers"] == 2
    assert ev[0]["text"] == "a", "keyDown 仍带 text（对齐 harness），但无 char 才不输入"


def test_space_emits_char():
    ev = run_press(" ")                          # 单字符且在 _KEYS 里 → 仍可打印
    assert [e["type"] for e in ev] == ["keyDown", "char", "keyUp"]
    assert ev[0]["windowsVirtualKeyCode"] == 32 and ev[0]["code"] == "Space"
    assert ev[1]["text"] == " "


def test_unknown_multichar_key_vk0():
    ev = run_press("F5")                          # 非 _KEYS、非单字符 → vk=0, text=""
    assert [e["type"] for e in ev] == ["keyDown", "keyUp"], "无 text → 无 char"
    assert ev[0]["windowsVirtualKeyCode"] == 0 and ev[0]["code"] == "F5"
    assert "text" not in ev[0]


def test_meta_shortcut_no_char():
    ev = run_press("a", modifiers=4)             # Cmd+A (macOS)
    assert [e["type"] for e in ev] == ["keyDown", "keyUp"]


def test_shift_still_printable():
    # Shift(8) 不属 Alt/Ctrl/Meta → 仍算可打印，补 char
    ev = run_press("A", modifiers=8)
    assert [e["type"] for e in ev] == ["keyDown", "char", "keyUp"]
    assert ev[1]["text"] == "A"


if __name__ == "__main__":
    test_special_key_enter()
    test_arrow_key_no_text()
    test_printable_char_emits_char_event()
    test_ctrl_shortcut_no_char()
    test_space_emits_char()
    test_unknown_multichar_key_vk0()
    test_meta_shortcut_no_char()
    test_shift_still_printable()
    print("ALL OK")
