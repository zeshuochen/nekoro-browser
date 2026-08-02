<p align="center">
  <img src="extension/icons/icon-128.png" width="80" alt="nekoro-browser">
</p>

<h1 align="center">nekoro-browser</h1>

<p align="center">
  <a href="https://github.com/zeshuochen/nekoro-browser/actions/workflows/tests.yml"><img src="https://github.com/zeshuochen/nekoro-browser/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="#mcp给-cursor--cline--claude-desktop-用"><img src="https://img.shields.io/badge/MCP-supported-8A2BE2" alt="MCP supported"></a>
</p>

<p align="center">
轻量浏览器自动化 CLI + MCP server。通过 Chrome 扩展操控日常浏览器 — <b>保留登录态</b>，<b>零端口</b>，<b>零弹窗</b>。<br>
<sub><a href="README_EN.md">English</a></sub>
</p>

---

## 为什么不用 `--remote-debugging-port`

Chrome 136 起，`--remote-debugging-port` / `--remote-debugging-pipe` **不再接受默认 profile**——必须指定一个非默认的 `--user-data-dir`，也就是一个没有你登录态的干净实例。扩展的 `chrome.debugger` 不受这条限制，所以 nekoro 走扩展。

| | CDP WebSocket | playwright-cli | opencli | **nekoro-browser** |
|------|:--:|:--:|:--:|:--:|
| 原理 | `--remote-debugging-port` | Playwright 扩展 | OpenCLI 扩展 | 自建扩展 + 持久 WebSocket |
| 安装 | 一行参数 | `npm i -g`（~200MB） | npm / 桌面应用 | `pip install`（纯标准库，零依赖） |
| 登录态 | ❌ 独立实例 | ✅ | ✅ | ✅ |
| 可修改扩展 | — | 需改 Playwright 源码 | 需改 OpenCLI 源码 | ✅ 扩展就在仓库里 |
| 自愈 | ❌ | ❌ | ❌ | ✅ Agent 运行时编辑 helpers |
| MCP | ❌ | ✅（另装 `@playwright/mcp`） | ❌ | ✅ 内置 45 个工具，`nekoro-browser-mcp` |

## 安装

```bash
git clone https://github.com/zeshuochen/nekoro-browser
cd nekoro-browser
pip install -e .       # 注册 nekoro-browser 命令；也可 ./install.sh 或 .\install.ps1
```

Python 3.12+，零第三方依赖（纯标准库）。

加载 Chrome 扩展：
1. 打开 `chrome://extensions/`，开启「开发者模式」
2. 「加载已解压的扩展程序」→ 选择 `extension/` 目录（`nekoro-browser --extension-path` 会打印它的绝对路径）
3. 确认扩展无报错

## 快速开始

> ⚠️ **先加载扩展，再启动 daemon。** 顺序反了 daemon 会等约 20 秒后报「扩展没连上」退出。

**终端 1** — 启动 daemon（保持打开）：

```bash
nekoro-browser
```

**终端 2** — 验证：

```bash
echo "page_info()" | nekoro-browser
# → {"ok": true, "result": {"title": "...", "url": "..."}}
```

## 示例

多步流程用 heredoc 一次发过去，所有 helper 都是顶层 `await`：

```bash
nekoro-browser <<'PY'
await new_tab("https://example.com")
print((await page_info())["title"])            # Example Domain
print((await get_markdown(max_chars=200))["result"])
print((await state(max_items=3))["result"])    # 带 index/box 的可交互元素，喂给模型
await close_tab()
PY
```

`state()` 给元素编号，`click_index(n)` 按编号点——模型不用猜 CSS 选择器：

```bash
nekoro-browser <<'PY'
await navigate("https://github.com/search?q=browser+automation&type=repositories")
await wait_for_load()
print((await state(max_items=40))["result"])
await click_index(12)
PY
```

全部 helper 见 [SKILL.md](SKILL.md)。

## MCP（给 Cursor / Cline / Claude Desktop 用）

`helpers.py` 里的函数会被反射成 MCP 工具（当前 45 个），不用改一行代码：

```json
{
  "mcpServers": {
    "nekoro-browser": {
      "command": "nekoro-browser-mcp"
    }
  }
}
```

daemon 仍需在另一个终端跑着（`nekoro-browser`）——MCP server 只是把工具调用转发给它，和 `echo ... | nekoro-browser` 是同一条路径、同一套令牌鉴权。工具里另有两个逃生口：`cdp`（原始 CDP 命令）和 `exec_python`（在 daemon 命名空间里跑任意 Python，多步流程一次往返）。

截图工具返回 image content，客户端能直接显示。helper 自己报的失败（`{"ok": false}`）会标成 `isError`，不会伪装成成功。

## 架构

```
Chrome 扩展 (background.js) —— chrome.debugger / CDP
        ↕ 持久 WebSocket
Python daemon (127.0.0.1:28417)
        ↕ HTTP /exec（令牌鉴权）
CLI (nekoro-browser)  ·  MCP server (nekoro-browser-mcp)
```

`helpers.py`（46 个）→ CDP 薄封装，每个 ≤10 行。厚逻辑在 `domain-skills/`。

`lifecycle.py` 管 daemon 生命周期：pid 文件 + 进程指纹防误杀、僵尸自愈（CDP 探活失败自动清理重启）、localhost 请求绕过系统代理。

扩展侧针对 MV3 service worker 会被 Chrome 回收这件事做了硬化：`content_scripts` 心跳（页面里的独立向量，SW 被杀也能重连唤醒）+ `onStartup`（Chrome 冷启动立即连 daemon）+ 断线后自动重挂上次操作的标签，不会漂到空白页。

## CLI

| 命令 | 作用 |
|------|------|
| `nekoro-browser` | 前台启动 daemon |
| `nekoro-browser --doctor` | 端到端诊断（daemon + 扩展 + SW 是否都活着） |
| `nekoro-browser --stop` | 停止 daemon |
| `nekoro-browser --restart` | 停止后重启（前台） |
| `nekoro-browser --reload-ext` | 命扩展重载 service worker，跑批量任务前刷干净状态 |
| `nekoro-browser --extension-path` | 打印扩展目录（加载已解压扩展时用） |
| `nekoro-browser -c "code"` | 执行一段代码并返回结果 |
| `echo "code" \| nekoro-browser` | 管道模式（需 daemon 已运行） |

## 自愈

`src/nekoro_browser/agent_helpers.py` 运行时随时可编辑，每次 `/exec` 自动 reload。Agent 操作失败时往里加缺失的函数，下次调用立即生效，不用重启 daemon、不用重装扩展。

`domain-skills/` 里的站点专属函数（如抖音的 `douyin_like`）**不会自动加载**——需要时把它们贴进 `agent_helpers.py`，签名约定一致（第一个参数是 `daemon`）。

## 平台支持

| 平台 | 状态 |
|------|------|
| Windows | 主力开发平台，全链路实测 |
| Linux / macOS | 代码有对应分支（XDG 目录、`chmod 600` 令牌、`/proc` 与 `ps` 存活探测），单测在 CI 上跑三平台；**但没有在真机上跑过「Chrome + 扩展」的完整链路**，欢迎反馈 |

## 已知限制

- **未打包的扩展会被 Chrome 停用。** 以「加载已解压的扩展程序」装的扩展，在 Chrome 更新或重启后可能被自动关掉、或弹出「停用开发者模式扩展程序」的提示。`--doctor` 报 Extension/SW 不响应时，先去 `chrome://extensions/` 把它重新打开。本项目目前**不发 Chrome 应用商店**，这条限制短期内不会消失。
- **Service Worker 保活不是 100%。** MV3 的回收时机由 Chrome 决定。心跳 + `onStartup` + 自动重挂能覆盖绝大多数情况，但无人值守的长时 cron 任务仍建议先 `--doctor` 健康检查再重试。
- **同时只驱动一个「活动标签」。** 多标签可以列举和切换（`list_tabs` / `switch_tab`），但命令总是发往当前活动标签，不做并行会话。
- **MCP server 串行处理请求。** 一次 `wait_selector(timeout=90)` 期间，同一连接上的其他请求（含 `ping`）会排队等它做完。要并发就开多个客户端连接。

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `Daemon not running` | daemon 没启动 | 终端 1 运行 `nekoro-browser` |
| CDP 命令超时 | 扩展未连接 / service worker 睡死 | `nekoro-browser --doctor` 定位；必要时 `--reload-ext` 或 `chrome://extensions` 手动重载 |
| 扩展被 Chrome 停用 | 未打包扩展 + Chrome 更新 | `chrome://extensions/` 重新启用，再 `--doctor` 复验 |
| 页面没变化 | 扩展未 attach | 打开普通网页（非 chrome://），重启 daemon |
| 端口占用 | 旧进程残留 | 杀掉占用 28417 的进程，或直接 `nekoro-browser --stop` |

## 安全

daemon 监听 `127.0.0.1`，`/exec` 会执行任意 Python，故传输层加了守卫：

- **CLI / MCP → daemon**（`/exec`、`/raw`）：每会话签发令牌，写入用户私有文件（`%LOCALAPPDATA%\nekoro-browser\token`，POSIX 上 `chmod 600`）。客户端读取后带在 `X-Nekoro-Token` 头里；缺失/错误 → `403`。网页和远程主机读不到本地文件，拿不到令牌。`/ping` 免令牌。
- **扩展 → daemon**（`/ws`）：握手 `Origin` 必须是 `chrome-extension://…`；网页对 localhost 发起的 `WebSocket` 带自己的域名 Origin，会被拒。

同用户的本地进程能读令牌文件——这条边界等于操作系统账户，与 browser-harness 的 `chmod 600` 一致。

## 反馈

用着有问题、或者想要的 helper 没有，开个 [issue](https://github.com/zeshuochen/nekoro-browser/issues)。
提 bug 时带上 `nekoro-browser --doctor` 的输出、Chrome 版本和操作系统，能省一轮来回。

PR 欢迎。改动前先跑一遍测试：`for f in tests/test_*.py; do python "$f"; done`（三平台 CI 也会跑）。

---

## 致谢

架构核心来源于以下项目：

- **[browser-harness](https://github.com/browser-use/browser-harness)** — helpers.py 的薄封装哲学（每个函数是 CDP 命令的别名，≤10 行）、管道模式、自愈 `agent_helpers.py`、domain-skills 目录结构、`cdp()` 原始访问接口
- **[browser-act](https://github.com/browser-act/skills)** — `state()` 索引元素树、`*[N]` 增量标记、`waitSelector()` 状态等待、`getMarkdown()` 页面提取
- **[Playwright](https://github.com/microsoft/playwright)** — CDP `Input.dispatchMouseEvent` 真实鼠标事件（`isTrusted:true`）、扩展+daemon 双路径架构
