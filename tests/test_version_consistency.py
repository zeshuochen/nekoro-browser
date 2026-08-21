"""test_version_consistency.py — 三处版本号必须一致。

版本号写在三个文件里：

    pyproject.toml                    打包元数据（PyPI 上显示的那个）
    src/nekoro_browser/__init__.py    --version 打印的那个
    extension/manifest.json           chrome://extensions 上显示的那个

**第三处最容易漏**：README 让人升级后重载扩展，manifest 版本号不跟着走，用户就
没法确认重载有没有生效。而前两处漏了同样有实害——真栽过一次：`git add` 只写了
`src / tests / extension`，`pyproject.toml` 没进去，于是 `__init__.py` 和
`manifest.json` 是新版、打包元数据还是旧版。那次是从工作区构建的 wheel 才碰巧正确，
而发布走的是 CI 从 commit 构建，真发就会拿旧版本号打包（PyPI 拒收重复版本，
属于响亮失败——但不该靠这个兜底）。

这条用例只读文件、不跑浏览器，是最便宜的一道发布门禁。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _pyproject_version():
    m = re.search(r'^version\s*=\s*"([^"]+)"', _read("pyproject.toml"), re.M)
    assert m, "pyproject.toml 里找不到 version"
    return m.group(1)


def _dunder_version():
    m = re.search(r'^__version__\s*=\s*"([^"]+)"',
                  _read("src", "nekoro_browser", "__init__.py"), re.M)
    assert m, "__init__.py 里找不到 __version__"
    return m.group(1)


def _manifest_version():
    return json.loads(_read("extension", "manifest.json"))["version"]


def test_all_three_version_strings_agree():
    py, dunder, mf = _pyproject_version(), _dunder_version(), _manifest_version()
    assert py == dunder == mf, (
        f"三处版本号不一致：pyproject={py} __init__={dunder} manifest={mf}。"
        "bump 的时候三个文件要一起改、一起提交。")


def test_version_looks_like_a_release_number():
    """形如 1.2.3。写错格式（尾随空格、v 前缀、四段）打包时才炸，太晚。"""
    v = _pyproject_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", v), f"版本号格式不对: {v!r}"


def test_installed_package_reports_the_same_version():
    """导入进来的那个包也要报同一个版本。

    守的是「改了文件但装的是另一棵树」——editable 安装指错地方、或 site-packages
    里躺着一份旧副本时，前面两条读文件的断言全过，而实际跑起来的是别的版本。
    """
    import nekoro_browser
    assert nekoro_browser.__version__ == _dunder_version(), (
        f"导入到的包版本 {nekoro_browser.__version__} 与源码树 {_dunder_version()} 不符"
        "——多半是装的不是这棵树")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
    print("ALL OK")
