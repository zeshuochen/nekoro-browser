"""site_notes.py — 导航到某站点时，把已有的站点笔记推到 agent 面前。

为什么需要这层：`SKILL.md` 里那句「遇到特定网站先查 domain-skills/<site>/」
是指望 agent 自觉，没有任何机制。实际上它多半不会主动去 ls——尤其 context 紧张时。
沉淀了却没人读 = 白沉淀。所以改成**知识主动送达**：`navigate` / `new_tab`
命中域名时在返回值里带一个 `notes` 字段。

只返回**文件清单 + 标题**，不返回正文。正文动辄几十行，每次导航都塞进去就是
把一次性写入成本变成永久读取成本（这正是不该自动沉淀一切的同一个理由）。
Agent 看到清单后自己决定读哪一份。

匹配规则：`domain-skills/` 下的目录名只要出现在 hostname 里就算命中，
所以 `douyin/` 能覆盖 `www.douyin.com` 和 `creator.douyin.com`。
域名和目录名对不上时（如视频号 `channels.weixin.qq.com`），
直接把目录命名成域名片段即可。
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


def notes_for(url: str) -> list[str]:
    """返回该 URL 命中的笔记清单，形如 `douyin/creator-stats.md — 抖音创作者中心`。

    任何异常都吞掉返回空列表：笔记查询失败绝不能连累导航本身。
    """
    try:
        host = _hostname(url)
        if not host:
            return []
        root = skills_dir()
        if root is None:
            return []
        hits = []
        for sub in sorted(root.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            if sub.name.lower() not in host:
                continue
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
    except Exception:
        pass
    return result
