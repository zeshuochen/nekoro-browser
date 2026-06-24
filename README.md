# nekoro-browser

> 从零构建的轻量浏览器自动化 CLI 工具，通过 Chrome 扩展的 `chrome.debugger` API 操控浏览器。

## 为什么不用 CDP WebSocket？

Chrome 136+ 对默认用户配置禁用了 `--remote-debugging-port`。nekoro-browser 改走 Chrome 扩展的 `chrome.debugger` API：

- **零端口**：不需要开远程调试端口
- **零弹窗**：没有 "DevTools is controlling this browser" 横幅
- **保留登录态**：操作的是你的正常 Chrome，Cookie 和扩展全在
- **无需重启**：不需要关掉 Chrome 再加 `--remote-debugging-port` 参数

## 架构

```
nekoro-browser CLI (Python)
    │
    ▼
daemon ── 本地 WebSocket ── 扩展 (background.js)
    │                            │
    │                    chrome.debugger API
    │                            │
    └──────── CDP 响应 ──────── Chrome 浏览器
```

- **daemon**：Python asyncio 进程，在 `127.0.0.1:9230-9245` 范围内开 WebSocket 服务端
- **扩展**：Chrome Manifest V3 Service Worker，扫描端口连接 daemon
- **通信**：daemon 发送 CDP 命令 → 扩展调用 `chrome.debugger.sendCommand()` → 结果返回

## 安装

```powershell
.\install.ps1
```

脚本会：
1. 检查 Python 3.12+ 和 websockets 库
2. `pip install -e .`
3. 引导你加载 Chrome 扩展

## 使用

```bash
# 交互模式
nekoro-browser

# 管道模式 — 直接执行 Python 代码
echo "page_info()" | nekoro-browser
echo "js('document.title')" | nekoro-browser
echo "capture_screenshot()" | nekoro-browser

# 诊断
nekoro-browser --doctor
```

## 自愈机制

`helpers.py` 运行时随时可修改。Agent 操作失败时可以编辑此文件添加缺失的函数，重新执行即可使用——无需重启。
