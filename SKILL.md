# nekoro-browser SKILL.md

## 概述

nekoro-browser 是浏览器自动化 CLI，通过 Chrome 扩展的 `chrome.debugger` API 操控 Chrome。

## 命令

### 启动和状态

```bash
nekoro-browser                 # 交互模式，从 stdin 读 Python 代码
nekoro-browser --doctor        # 诊断检查
nekoro-browser --version       # 版本
nekoro-browser --verbose       # 调试模式
```

### 管道模式

```bash
echo "code" | nekoro-browser   # 执行 Python 代码并返回 JSON 结果
```

## 可用函数 (helpers)

所有函数返回 `{"ok": True, ...}` 或 `{"ok": False, "error": "..."}`。

### Tab 管理

| 函数 | 用法 | 说明 |
|------|------|------|
| `new_tab(url)` | `new_tab("https://example.com")` | 新建标签页 |
| `navigate(url)` | `navigate("https://example.com")` | 当前标签导航 |

### 页面信息

| 函数 | 用法 | 说明 |
|------|------|------|
| `page_info()` | `page_info()` | 返回 `{title, url}` |
| `page_html()` | `page_html()` | 返回完整 HTML |
| `page_text()` | `page_text()` | 返回可见文本 |

### 截图

| 函数 | 用法 | 说明 |
|------|------|------|
| `capture_screenshot()` | `capture_screenshot()` | PNG 截图，返回 base64 |
| `capture_screenshot("jpeg", 90)` | 同上 | JPEG 截图，质量可调 |

### JavaScript

| 函数 | 用法 | 说明 |
|------|------|------|
| `js(code)` | `js("document.title")` | 执行 JS 并返回结果 |
| `js("return document.querySelectorAll('a').length")` | 同上 | 支持 return 语句 |

### 交互操作

| 函数 | 用法 | 说明 |
|------|------|------|
| `click_at_xy(x, y)` | `click_at_xy(100, 200)` | 坐标点击 |
| `click_selector(sel)` | `click_selector("#btn")` | CSS 选择器点击 |
| `type_text(text)` | `type_text("hello")` | 输入文本 |
| `press_key(key)` | `press_key("Enter")` | 按键 |
| `press_key("c", 2)` | 同上 | Ctrl+C（2=Ctrl, 1=Alt, 8=Shift, 4=Meta） |

### 滚动

| 函数 | 用法 | 说明 |
|------|------|------|
| `scroll_to(x, y)` | `scroll_to(0, 500)` | 滚动到坐标 |
| `scroll_bottom()` | `scroll_bottom()` | 滚动到底部 |

### 等待

| 函数 | 用法 | 说明 |
|------|------|------|
| `wait_for_load()` | `wait_for_load()` | 等待页面加载（30s 超时） |
| `wait_for_load(60)` | 同上 | 自定义超时 |
| `wait_for_selector(sel)` | `wait_for_selector("#content")` | 等待元素出现 |
| `sleep(seconds)` | `sleep(2)` | 暂停 |

### Cookie

| 函数 | 用法 | 说明 |
|------|------|------|
| `get_cookies()` | `get_cookies()` | 获取所有 cookie |
| `get_cookies("https://x.com")` | 同上 | 按 URL 过滤 |
| `set_cookie(name, val)` | `set_cookie("t", "abc", domain=".x.com")` | 设置 cookie |

### 网络监控

| 函数 | 用法 | 说明 |
|------|------|------|
| `enable_network_monitoring()` | `enable_network_monitoring()` | 启用请求监控 |
| `get_response_body(id)` | `get_response_body("123.5")` | 获取响应体 |

## 自愈机制

如果遇到缺失的功能，Agent 可以编辑 `helpers.py` 添加新函数。文件在每次调用前被重新加载，修改立即生效。

## 故障排查

| 问题 | 解决 |
|------|------|
| `Extension not connected` | 确保扩展已安装并在 chrome://extensions 中启用 |
| `No free port` | 检查端口 9230-9245 是否被占用 |
| CDP 命令超时 | 页面可能卡住，尝试刷新标签 |
