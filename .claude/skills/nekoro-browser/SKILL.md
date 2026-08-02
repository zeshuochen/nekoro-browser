---
name: nekoro-browser
description: 浏览器自动化——打开网页、搜索、点击、截图、执行 JS、填表、上传文件、处理对话框。通过 Chrome 扩展的 chrome.debugger API 操控用户日常浏览器，保留登录态，不开调试端口。触发词："浏览器"、"打开网页"、"搜索"、"截图"、"点击"、"填表"、"上传文件"、"自动化操作"。
allowed-tools: Bash(nekoro-browser:*) Bash(python:*) Read Edit Write
---

# nekoro-browser

通过 Chrome 扩展 + 持久 WebSocket 操控用户日常 Chrome 的 CLI 工具。不需要
`--remote-debugging-port`（Chrome 136 起该方式无法接默认 profile），保留真实登录态。

**完整命令参考、故障排查、领域技能见仓库根目录 [`SKILL.md`](../../../SKILL.md)——先读那份，本文件只补 Claude Code 场景的要点。**

## 前置条件

```bash
uv tool install nekoro-browser    # 已上架 PyPI
# 或从源码：uv pip install -e .（在 clone 出来的仓库根目录）
```

加载 Chrome 扩展：`chrome://extensions` → 开发者模式 → 加载已解压的扩展程序 → 选
`extension/` 目录（`nekoro-browser --extension-path` 打印绝对路径）。未打包扩展会被
Chrome 更新/重启后自动停用，`--doctor` 说 SW 不响应时先去那页确认它还开着。

## 快速开始

```bash
# 终端 1：启动 daemon（前台，保持打开）
nekoro-browser

# 终端 2：验证
nekoro-browser --doctor
echo "page_info()" | nekoro-browser
```

daemon 默认监听 `127.0.0.1:28417`（选此端口是为了不与同类工具 `@jackwener/opencli` 的
19825 撞车，若你机器上也装了 OpenCLI，两者可共存但同一时刻按需只启一个 daemon）。

改端口：Python 侧 `nekoro-browser --port 30500` 或环境变量 `NEKORO_PORT`，
扩展侧在扩展详情页的「扩展程序选项」里设成同一个。客户端不用重复传参——
daemon 把实际端口写进 `<数据目录>/port`，管道模式自己会读。

## 每次调用前

检查 daemon 是否存活（`nekoro-browser --doctor`），死了 `nekoro-browser --reload-ext`
或重启；扩展 service worker 偶尔需要在 `chrome://extensions` 手动重载（尤其 Chrome
刚重开时）。

## 自愈

缺少函数时编辑 `src/nekoro_browser/agent_helpers.py` 添加——**只有这个文件**每次
`/exec` 前会自动 reload，改完立即生效、无需重启 daemon。改 `helpers.py` 本身需要重启
daemon 才生效（daemon 启动时导入一次）。
