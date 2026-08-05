"""test_daemon_call_signatures.py — helpers 对 daemon.* 的每一处调用都能真的绑上。

`capture_screenshot` 坏了不知道多久：helpers 写 `daemon.screenshot(format=..., quality=...)`，
daemon 的签名却是 `screenshot(self, f="png", q=80)`——关键字按名匹配，直接 TypeError。
而 `capture_screenshot` 外面裹着 try/except 转成 `{"ok": false, "error": ...}`，
**失败是静默的**，日常流程走 get_markdown()/state() 又碰不到截图，于是从第一个 commit
一路潜到现在。

单个 fake 挡不住这类问题（fake 想写成什么签名就是什么签名，`**kwargs` 一收更是全绿），
所以这里不 fake：**从 helpers.py 的 AST 里捞出所有 `daemon.X(...)` 调用，拿真 Daemon 类的
真签名去 bind**。少一个参数、多一个关键字、名字对不上，全在这里断。
"""
import ast
import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ["NEKORO_DOMAIN_SKILLS"] = tempfile.mkdtemp(prefix="nekoro-empty-skills-")

from nekoro_browser.daemon import Daemon

_HELPERS = os.path.join(os.path.dirname(__file__), "..", "src", "nekoro_browser", "helpers.py")


def _daemon_calls(path):
    """[(方法名, 位置参个数, [关键字名], 行号)] — helpers 里所有 `daemon.X(...)`。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        recv = node.func.value
        if not (isinstance(recv, ast.Name) and recv.id == "daemon"):
            continue          # daemon.bridge.send(...) 之类不在这层，接收者不是 daemon
        kwargs = [k.arg for k in node.keywords if k.arg is not None]
        has_star = any(k.arg is None for k in node.keywords)
        out.append((node.func.attr, len(node.args), kwargs, has_star, node.lineno))
    return out


def test_found_the_call_sites():
    """先证明扫描本身没扫空——不然下面全是空转。"""
    calls = _daemon_calls(_HELPERS)
    assert len(calls) >= 10, f"只扫到 {len(calls)} 处 daemon.* 调用，扫描器坏了"
    assert any(c[0] == "screenshot" for c in calls), "screenshot 调用点没扫到"


def test_every_daemon_call_binds_to_the_real_signature():
    bad = []
    for name, npos, kwargs, has_star, lineno in _daemon_calls(_HELPERS):
        method = getattr(Daemon, name, None)
        if method is None:
            bad.append(f"helpers.py:{lineno} daemon.{name}(...) — Daemon 上没有这个方法")
            continue
        if has_star:
            continue          # **kwargs 展开，静态绑不了
        sig = inspect.signature(method)
        try:
            # self + 位置参各占一个坑；只验名字和数量，值无所谓
            sig.bind_partial(*[object()] * (npos + 1),
                             **{k: object() for k in kwargs})
        except TypeError as e:
            bad.append(f"helpers.py:{lineno} daemon.{name}"
                       f"({npos} 个位置参, kwargs={kwargs}) 绑不上 {sig}: {e}")
    assert not bad, "helpers 与 daemon 签名对不上：\n  " + "\n  ".join(bad)


def test_screenshot_specifically():
    """点名钉住出过事的那条：helpers 传的关键字必须在 daemon.screenshot 上真实存在。"""
    params = inspect.signature(Daemon.screenshot).parameters
    for kw in ("format", "quality"):
        assert kw in params, (
            f"Daemon.screenshot 没有 `{kw}` 参数（当前 {list(params)}），"
            f"而 helpers.capture_screenshot 就是用这个关键字调的 → TypeError")


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
