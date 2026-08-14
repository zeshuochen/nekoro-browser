"""allowlist.py — 域名白名单闸门。

动机：这个工具操作的是用户日常那个 Chrome，所有登录态都在里面。开新浏览器权限太小
（没登录态，啥也干不了），用当前浏览器权限太大（所有站点的隐私全暴露）。白名单是这
两者之间唯一的中间地带。

**默认不限制**（fail-open）。不传 --allow-domains 就跟以前完全一样，升级无感。
安全收益只对主动配置的人生效 —— 这是刻意的取舍：这个包已经在 PyPI 上，默认收紧会
把所有现存脚本打挂，而"为了安全把能用的东西弄坏"没人会接受，最后只会被关掉。

匹配规则（照搬大家对 cookie domain / CORS 的直觉，不发明新语法）：
    example.com     → 精确匹配该主机
    *.example.com   → 匹配子域，**也匹配裸域** example.com 本身
    *               → 全放行（等价于不配置，但把意图写出来了）

只看主机名，不看 scheme/端口/路径：白名单是"能碰哪个站"，不是 URL 级 ACL。
"""

import os
from typing import Any
from urllib.parse import urlsplit

ENV_VAR = "NEKORO_ALLOW_DOMAINS"


def parse(raw: str | None) -> list[str] | None:
    """"a.com, *.b.com" → ["a.com", "*.b.com"]；空/None → None（表示不限制）。

    返回 None 和返回 [] 是两回事：None = 没配置 = 放行一切；[] = 配了但全是空项，
    按"没配置"处理而不是"全部拒绝" —— 拼错一个参数就把工具锁死，不是好设计。
    """
    if not raw:
        return None
    items = [x.strip().lower() for x in raw.replace(";", ",").split(",")]
    items = [x for x in items if x]
    return items or None


def from_env() -> list[str] | None:
    return parse(os.environ.get(ENV_VAR))


def host_of(url: str) -> str | None:
    """取 URL 主机名；about:blank / chrome:// 这类无主机的返回 None。"""
    try:
        h = (urlsplit(url).hostname or "").lower()
    except ValueError:                       # 畸形 URL（IPv6 括号不配对等）
        return None
    return h or None


def host_allowed(host: str | None, rules: list[str] | None) -> bool:
    """主机是否被规则放行。rules 为 None（未配置）时一律放行。"""
    if rules is None:
        return True
    if host is None:
        # 无主机的内部页（about:blank、chrome://newtab）不承载站点数据，放行。
        # 拦下它们只会让 new_tab() 这类基础操作在配了白名单后突然不能用。
        return True
    for r in rules:
        if r == "*":
            return True
        if r.startswith("*."):
            base = r[2:]
            if host == base or host.endswith("." + base):
                return True
        elif host == r:
            return True
    return False


def check(url: str | None, rules: list[str] | None) -> dict[str, Any] | None:
    """放行返回 None；拦截返回可直接当结果回传的 {ok:false,...}。

    拦截走返回值不走异常 —— 与 helpers 里其余错误路径保持一致，调用方不必为白名单
    单独加 try。
    """
    if url is None or host_allowed(host_of(url), rules):
        return None
    return {"ok": False, "kind": "domain_blocked",
            "error": f"domain not allowed: {host_of(url)} "
                     f"(允许的: {', '.join(rules or [])})"}
