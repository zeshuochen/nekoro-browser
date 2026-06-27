<p align="center">
  <img src="extension/icons/icon-128.png" width="80" alt="nekoro-browser">
</p>

<h1 align="center">nekoro-browser</h1>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://github.com/zeshuochen/nekoro-browser"><img src="https://img.shields.io/badge/repo-github-black" alt="GitHub"></a>
</p>

<p align="center">
轻量浏览器自动化 CLI。通过 Chrome 扩展操控日常浏览器 — <b>保留登录态</b>，<b>零端口</b>，<b>零弹窗</b>。<br>
<sub><a href="README_EN.md">English</a></sub>
</p>

---

## 与其他方案的区别

| | CDP WebSocket | playwright-cli | opencli | **nekoro-browser** |
|------|:--:|:--:|:--:|:--:|
| 原理 | `--remote-debugging-port` | Playwright 扩展 | OpenCLI 扩展 | 自建扩展 + HTTP polling |
| 安装 | 一行参数 | `npm i -g`（~200MB） | npm / 桌面应用 | `pip install`（纯标准库） |
| 登录态 | ❌ 独立实例 | ✅ | ✅ | ✅ |
| 可修改扩展 | — | 需改 Playwright 源码 | 需改 OpenCLI 源码 | ✅ 扩展就在仓库里 |
| 自愈 | ❌ | ❌ | ❌ | ✅ Agent 运行时编辑 helpers.py |
| 流程沉淀 | ❌ | ❌ | ❌ | ✅ 复合流程写成函数一条命令复用 |

## 安装

```powershell
git clone https://github.com/zeshuochen/nekoro-browser
cd nekoro-browser
pip install -e .       # 注册 nekoro-browser 命令
```

加载 Chrome 扩展：
1. 打开 `chrome://extensions/`，开启「开发者模式」
2. 「加载已解压的扩展程序」→ 选择 `extension/` 目录
3. 确认扩展无报错

## 快速开始

> ⚠️ **先加载扩展，再启动 daemon。** 顺序反了会等 60 秒超时。

**终端 1** — 启动 daemon（保持打开）：

```bash
nekoro-browser
```

**终端 2** — 验证：

```bash
echo "page_info()" | nekoro-browser
# → {"ok": true, "result": {"title": "...", "url": "..."}}
```

## 实战：抖音搜索籽岷，给第一个视频点赞

```bash
echo "douyin_like('籽岷')" | nekoro-browser
```

抖音键盘快捷键：`z`=点赞 `x`=评论 `c`=收藏 `G`=关注

全部 helpers 和 domain skills 见 [SKILL.md](SKILL.md)。

## 架构

`helpers.py`（30 个）→ CDP 薄封装，每个 ≤10 行。厚逻辑在 `domain-skills/`。

## 自愈

`helpers.py` 运行时随时可编辑。Agent 操作失败时编辑此文件添加缺失函数，下次执行立即生效。

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `Daemon not running` | daemon 没启动 | 终端 1 运行 `nekoro-browser` |
| CDP 命令超时 | 扩展未连接 | 检查 `chrome://extensions` |
| 页面没变化 | 扩展未 attach | 打开普通网页（非 chrome://），重启 daemon |
| 端口占用 | 旧进程残留 | 杀掉占用 9230 的进程 |

---

## 致谢

架构核心来源于以下项目：

- **[browser-harness](https://github.com/browser-use/browser-harness)** — helpers.py 的薄封装哲学（每个函数是 CDP 命令的别名，≤10 行）、管道模式、自愈 `agent_helpers.py`、domain-skills 目录结构、`cdp()` 原始访问接口
- **[browser-act](https://github.com/browser-act/skills)** — `state()` 索引元素树、`*[N]` 增量标记、`waitSelector()` 状态等待、`getMarkdown()` 页面提取
- **[Playwright](https://github.com/microsoft/playwright)** — CDP `Input.dispatchMouseEvent` 真实鼠标事件（`isTrusted:true`）、扩展+daemon 双路径架构
