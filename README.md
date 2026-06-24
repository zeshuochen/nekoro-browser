# nekoro-browser

> 轻量浏览器自动化 CLI，通过 Chrome 扩展的 `chrome.debugger` API 操控浏览器。

## 为什么不用 CDP WebSocket？

Chrome 136+ 禁用默认用户配置的 `--remote-debugging-port`。nekoro-browser 通过 Chrome 扩展 + HTTP 轮询绕开限制：

- **零端口**：不需要开远程调试端口
- **零弹窗**：没有 "DevTools is controlling this browser" 横幅
- **保留登录态**：操作你正常使用的 Chrome，Cookie 和扩展全在
- **无需重启**：不用关 Chrome 重新加参数

## 架构

```
nekoro-browser CLI (Python)
    │  HTTP POST /exec
    ▼
daemon ── HTTP polling (GET /poll, POST /result) ── 扩展 (background.js)
    │                                                    │
    │                                            chrome.debugger API
    │                                                    │
    └────────────── CDP 响应 ────────────────────── Chrome 浏览器
```

- **daemon**：Python asyncio HTTP 服务端（固定端口 9230）+ 命令队列
- **扩展**：Chrome Manifest V3 Service Worker，HTTP 轮询获取命令，`chrome.debugger` 执行
- **通信**：daemon 将 CDP 命令入队 → 扩展轮询获取 → 执行 → 结果回传

## 安装

```powershell
git clone https://github.com/zeshuochen/nekoro-browser
cd nekoro-browser
pip install -e .
```

然后加载 Chrome 扩展（**先做这一步，再启动 daemon**）：
1. 打开 `chrome://extensions/`
2. 开启「开发者模式」
3. 「加载已解压的扩展程序」→ 选择 `extension/` 目录
4. 确认扩展已加载、无报错

## 使用

**先加载扩展，再开两个终端**：

```bash
# 终端 1：启动 daemon（保持打开）
nekoro-browser

# 终端 2：执行命令
echo "page_info()" | nekoro-browser
echo "js('document.title')" | nekoro-browser
echo "capture_screenshot()" | nekoro-browser
```

> ⚠️ 如果 daemon 先启动、扩展后加载，daemon 会等 60 秒超时后打印 "Waiting for extension..."。正确顺序：扩展先加载，再启动 daemon。

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `Daemon not running` | daemon 没启动 | 终端 1 运行 `nekoro-browser` |
| CDP 命令超时 | 扩展未连接 | 检查 `chrome://extensions`，确认扩展已加载且未报错 |
| 页面没变化 | 扩展未 attach 到 tab | 打开一个普通网页（非 chrome://），重启 daemon |
| `address already in use` | 旧 daemon 占用端口 | `netstat -ano \| findstr 9230` 查 PID 杀掉 |

## 可用函数（20 个 helpers）

| 类别 | 函数 |
|------|------|
| Tab | `new_tab(url)`, `navigate(url)` |
| 页面 | `page_info()`, `page_html()`, `page_text()` |
| 截图 | `capture_screenshot()`, `capture_screenshot("jpeg", 90)` |
| JS | `js(code)` |
| 交互 | `click_at_xy(x,y)`, `click_selector(sel)`, `type_text(text)`, `press_key(key)` |
| 滚动 | `scroll_to(x,y)`, `scroll_bottom()` |
| 等待 | `wait_for_load()`, `wait_for_selector(sel)`, `sleep(seconds)` |
| Cookie | `get_cookies()`, `set_cookie(name,val)` |
| 网络 | `enable_network_monitoring()`, `get_response_body(id)` |

## 自愈机制

`helpers.py` 运行时随时可修改。Agent 操作失败时编辑此文件添加缺失函数，重新执行即生效——无需重启 daemon。
