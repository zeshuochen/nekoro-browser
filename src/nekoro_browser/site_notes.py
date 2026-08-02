"""site_notes.py — 导航到某站点时，把已有的站点笔记推到 agent 面前。

为什么需要这层：`SKILL.md` 里那句「遇到特定网站先查 domain-skills/<site>/」
是指望 agent 自觉，没有任何机制。实际上它多半不会主动去 ls——尤其 context 紧张时。
沉淀了却没人读 = 白沉淀。所以改成**知识主动送达**：`navigate` / `new_tab`
命中域名时在返回值里带一个 `notes` 字段。

只返回**文件清单 + 标题**，不返回正文。正文动辄几十行，每次导航都塞进去就是
把一次性写入成本变成永久读取成本（这正是不该自动沉淀一切的同一个理由）。
Agent 看到清单后自己决定读哪一份。

匹配规则：`domain-skills/` 下的目录名只要出现在 hostname 里就算命中，
所以 `example/` 能同时覆盖 `www.example.com` 和 `admin.example.com`。
域名里没有合适的词可用时，把目录命名成域名的任一可辨识片段即可。
"""

import os
from pathlib import Path

MAX_FILES = 8          # 清单上限，防某个站点笔记堆太多把返回值撑爆
ENV_VAR = "NEKORO_DOMAIN_SKILLS"


def skills_dir() -> Path | None:
    """domain-skills 目录。环境变量优先，其次包内，最后仓库根。"""
    override = os.environ.get(ENV_VAR)
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    here = Path(__file__).resolve().parent
    for cand in (here / "domain-skills",          # wheel 里（force-include）
                 here.parent.parent / "domain-skills"):   # 仓库 clone
        if cand.is_dir():
            return cand
    return None


def _hostname(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _title_of(md: Path) -> str:
    """取第一个 Markdown 标题当摘要；没有就用文件名。"""
    try:
        with md.open(encoding="utf-8", errors="replace") as f:
            for _ in range(20):                    # 只看开头几行，不读整个文件
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if line.startswith("#"):
                    return line.lstrip("#").strip()[:80]
    except OSError:
        pass
    return md.stem


def _dirs_for(url: str):
    """URL 命中的站点目录。目录名出现在 hostname 里即算命中。"""
    host = _hostname(url)
    root = skills_dir()
    if not host or root is None:
        return []
    return [d for d in sorted(root.iterdir())
            if d.is_dir() and not d.name.startswith(".") and d.name.lower() in host]


def _signatures(py: Path) -> list[str]:
    """用 ast 读函数签名——不 import，避免为了列个清单就执行用户代码。"""
    import ast
    out = []
    try:
        tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return out
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        args = [a.arg for a in node.args.args if a.arg != "daemon"]
        doc = (ast.get_docstring(node) or "").split("\n")[0].strip()
        sig = f"{node.name}({', '.join(args)})"
        out.append(f"{sig} — {doc}" if doc else sig)
    return out


def actions_for(url: str) -> list[str]:
    """该站点已固化的函数清单。路由用：agent 看到就该直接调，而不是重新推导一遍。"""
    try:
        out = []
        for d in _dirs_for(url):
            for py in sorted(d.glob("*.py")):
                for sig in _signatures(py):
                    out.append(sig)
                    if len(out) >= MAX_FILES * 2:
                        return out
        return out
    except Exception:
        return []


def _inject_helpers(mod) -> None:
    """把核心 helper 塞进站点脚本的全局命名空间。

    站点脚本是独立模块，默认拿不到 `navigate` / `page_info` 这些——但约定和
    agent_helpers.py 的示例都写着直接调（`await navigate(daemon, url)`），
    不注入就是 NameError。在 exec_module **之前**注入，模块顶层代码也能用；
    脚本自己定义的同名函数会自然覆盖注入值。
    注入进来的名字不会被当成站点函数导出（导出按 __module__ 过滤）。
    """
    from . import helpers as h
    for name in h.list_helpers():
        mod.__dict__.setdefault(name, getattr(h, name))


def load_functions():
    """把所有站点目录下的 *.py 载入，返回 ({name: func}, [错误])。

    每次 /exec 都重新载入，所以改完立即生效（与 agent_helpers 同样的语义）。
    单个文件出错只跳过它并记下原因——一个写坏的站点脚本不能让整个 exec 挂掉，
    但也不能静默吞掉，否则用户会对着"函数怎么不存在"抓瞎。
    """
    import importlib.util
    ns, errors = {}, []
    root = skills_dir()
    if root is None:
        return ns, errors
    try:
        dirs = [d for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        return ns, errors
    for d in dirs:
        for py in sorted(d.glob("*.py")):
            mod_name = f"_nekoro_site_{d.name}_{py.stem}".replace("-", "_").replace(".", "_")
            try:
                spec = importlib.util.spec_from_file_location(mod_name, py)
                mod = importlib.util.module_from_spec(spec)
                _inject_helpers(mod)   # 让站点脚本能直接写 await navigate(daemon, url)
                spec.loader.exec_module(mod)
            except Exception as e:
                errors.append(f"{d.name}/{py.name}: {type(e).__name__}: {e}")
                continue
            for name, obj in vars(mod).items():
                if not name.startswith("_") and callable(obj) \
                        and getattr(obj, "__module__", None) == mod_name:
                    ns[name] = obj
    return ns, errors


def notes_for(url: str) -> list[str]:
    """返回该 URL 命中的笔记清单，形如 `example/search.md — Example — 搜索结果页`。

    任何异常都吞掉返回空列表：笔记查询失败绝不能连累导航本身。
    """
    try:
        hits = []
        for sub in _dirs_for(url):
            for md in sorted(sub.glob("*.md")):
                hits.append(f"{sub.name}/{md.name} — {_title_of(md)}")
                if len(hits) >= MAX_FILES:
                    return hits
        return hits
    except Exception:
        return []


def attach(result: dict, url: str) -> dict:
    """命中才加 `notes` 键——没笔记的站点不该多出一个空字段来占位。

    这里再兜一层异常：`notes_for` 自己虽然吞了，但 attach 是接在 navigate/new_tab
    返回路径上的，一个附赠功能绝不能把导航本身搞挂。
    """
    try:
        notes = notes_for(url)
        if notes:
            result["notes"] = notes
        acts = actions_for(url)
        if acts:
            # 路由信号：这个站点已经有现成函数了，别再从零推导
            result["actions"] = acts
    except Exception:
        pass
    return result
