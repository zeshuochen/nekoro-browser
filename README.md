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

## 实战：打开 B站，搜籽岷，给最新视频点赞

```bash
# 1. 搜索
echo "navigate('https://search.bilibili.com/all?keyword=籽岷')" | nekoro-browser
echo "sleep(3)" | nekoro-browser

# 2. 找第一个视频链接
echo "js(\"return document.querySelector('a[href*=\\\\"/video/BV\\\\\"]')?.href\")" | nekoro-browser
# → {"ok": true, "result": "https://www.bilibili.com/video/BV..."}

# 3. 打开视频
echo "navigate('https://www.bilibili.com/video/...')" | nekoro-browser
echo "sleep(4)" | nekoro-browser

# 4. 点赞
echo "js(\"document.querySelector('[class*=like]:not([class*=dislike])')?.click(); 'done'\")" | nekoro-browser
# → {"ok": true, "result": "done"}
```

## API

全部 20 个 helpers 见 [SKILL.md](SKILL.md)。常用：

| 类别 | 命令 |
|------|------|
| 导航 | `navigate(url)`, `new_tab(url)` |
| 页面 | `page_info()`, `page_html()`, `page_text()` |
| JS | `js(code)` |
| 交互 | `click_selector(sel)`, `click_at_xy(x,y)`, `type_text(t)`, `press_key(k)` |
| 等待 | `wait_for_load()`, `wait_for_selector(sel)`, `sleep(s)` |
| 截图 | `capture_screenshot()`, `capture_screenshot("jpeg", 90)` |

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

受以下项目启发（未使用其代码）：
- [browser-harness](https://github.com/nicholasgriffintn/browser-harness) — 管道模式
- [playwright-cli](https://github.com/microsoft/playwright-cli) / [opencli](https://github.com/jackwener/opencli) — 扩展+daemon 架构
- [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) — CDP 文档
