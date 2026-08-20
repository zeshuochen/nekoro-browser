"""test_get_markdown.py — getMarkdown 的标签名大小写必须自洽。

扩展 JS 没有运行时单测面，所以这里做结构性断言：把 `getMarkdown` 那段取出来，
读出 `tag` 被归一成哪种大小写，再要求**所有拿 tag 去比的字面量都是同一种**。

守的是一个真出过的 bug：`tag` 被 `.toLowerCase()` 归一成小写，而下面每一条规则
（`/^H[1-6]$/`、`tag === 'A'`、`'LI'`、`'P'`、块级元素表…）都按大写写。于是从标题
到链接到列表，整个格式化层一条都命不中，全部掉进最后那段纯文本递归 ——
`get_markdown()` 产出的是没有任何换行、所有文字粘在一起的纯文本，比 `page_text()`
还差。而它是 README 首页示例里的函数，也是一个 MCP 工具。

断言方向做成「自洽」而不是「必须大写」：以后谁想改成全小写也行，只要一起改；
只改一半就红。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

EXT = os.path.join(os.path.dirname(__file__), "..", "extension")


def _background():
    with open(os.path.join(EXT, "background.js"), encoding="utf-8") as f:
        return f.read()


def _get_markdown_block():
    src = _background()
    start = src.index("case 'getMarkdown'")
    # 下一个 case 就是块的结尾——别用花括号配对，正则和字符串里也有花括号
    end = src.index("case '", start + 10)
    block = src[start:end]
    assert "function toMd" in block, "取到的不是 getMarkdown 那段"
    return block


def _norm_case(block):
    """tag 被归一成哪种大小写 → 'UPPER' / 'LOWER'。"""
    m = re.search(r"const\s+tag\s*=\s*\(node\.tagName\s*\|\|\s*''\)\.to(Upper|Lower)Case\(\)", block)
    assert m, "没找到 tag 的归一化那行——改了写法就要同步这个用例"
    return m.group(1).upper()


def _literals_compared_to_tag(block):
    """所有拿 tag 去比的标签名字面量。"""
    out = []
    out += re.findall(r"tag\s*===\s*'([A-Za-z][A-Za-z0-9]*)'", block)
    for arr in re.findall(r"\[([^\]]+)\]\.includes\(tag\)", block):
        out += re.findall(r"'([A-Za-z][A-Za-z0-9]*)'", arr)
    # 标题那条是正则：/^H[1-6]$/ 里的字母也必须跟着归一化走
    for pat in re.findall(r"/\^([A-Za-z])\[1-6\]\$/\.test\(tag\)", block):
        out.append(pat)
    assert len(out) >= 10, f"只抽到 {len(out)} 条比较，正则多半没跟上代码改动"
    return out


def test_tag_comparisons_match_the_normalized_case():
    block = _get_markdown_block()
    want = _norm_case(block)
    bad = [lit for lit in _literals_compared_to_tag(block)
           if (lit.upper() if want == "UPPER" else lit.lower()) != lit]
    assert not bad, (
        f"tag 归一成 {want}，但这些字面量是另一种大小写，永远命不中：{sorted(set(bad))}")


def test_skip_list_uses_the_normalized_variable():
    """跳过 STYLE/SCRIPT 那条曾经直接比 node.tagName（大写），而 tag 是小写——
    结果它是整段里唯一还能工作的规则，掩盖了「其余全是死代码」这件事。
    统一走 tag，别再留这种一半一半的状态。"""
    block = _get_markdown_block()
    assert "includes(node.tagName)" not in block, \
        "跳过表还在直接比 node.tagName，应该跟其它规则一样用归一化后的 tag"


def test_no_stray_case_conversion_inside_tomd():
    """toMd 里只该有一处大小写归一化。多一处就是又在给自己埋不一致。"""
    block = _get_markdown_block()
    n = len(re.findall(r"\.to(?:Upper|Lower)Case\(\)", block))
    assert n == 1, f"toMd 里有 {n} 处大小写转换，应当只有 tag 那一处"


if __name__ == "__main__":
    test_tag_comparisons_match_the_normalized_case()
    test_skip_list_uses_the_normalized_variable()
    test_no_stray_case_conversion_inside_tomd()
    print("ALL OK")
