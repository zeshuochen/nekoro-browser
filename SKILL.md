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
# 单行
nekoro-browser -c "print(await page_info())"

# 多步 (heredoc)
nekoro-browser <<'PY'
await new_tab("https://douyin.com")
await wait_for_load()
print(await page_info())
PY

# stdin 管道 (兼容旧用法)
echo "await page_info()" | nekoro-browser
```

## 可用函数 (helpers)

所有函数返回 `{"ok": True, ...}` 或 `{"ok": False, "error": "..."}`。

### Tab 管理

| 函数 | 用法 | 说明 |
|------|------|------|
| `new_tab(url)` | `new_tab("https://example.com")` | 新建标签页 |
| `navigate(url)` | `navigate("https://example.com")` | 当前标签导航（默认等加载完成） |
| `list_tabs()` | `list_tabs()` | 列托管组标签 `[{tabId,url,title,active,attached}]` |
| `switch_tab(id)` | `switch_tab(123)` | 切换活动标签（后续命令发往该标签） |
| `ensure_real_tab()` | `ensure_real_tab()` | 自动从 chrome:// 等内部页导航到 about:blank |
| `iframe_target(url_substr)` | `iframe_target("player")` | 获取 iframe 的 CDP targetId |

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
| `cdp(method, **params)` | `cdp("Page.navigate", url="...")` | 原始 CDP 命令 |
| `cdp_batch(*cmds)` | `cdp_batch(["DOM.getDocument"], ["Page.getLayoutMetrics"])` | 多条独立 CDP 命令并发（流水线，N 条 ~1 个往返） |

### 交互操作

| 函数 | 用法 | 说明 |
|------|------|------|
| `click_at_xy(x, y)` | `click_at_xy(100, 200)` | CDP 真实鼠标点击（isTrusted:true） |
| `click_selector(sel)` | `click_selector("#btn")` | CSS 选择器 → CDP 坐标点击 |
| `click_text("文字")` | `click_text("喜欢")` | 按可见文本 → CDP 坐标点击 |
| `type_text(text)` | `type_text("hello")` | CDP Input.insertText |
| `press_key(key)` | `press_key("Enter")` | 按键 |
| `press_key("c", 2)` | 同上 | Ctrl+C（2=Ctrl, 1=Alt, 8=Shift, 4=Meta） |

### 索引元素树（browser-act 风格）

| 函数 | 用法 | 说明 |
|------|------|------|
| `state()` | `state()` | 返回索引元素列表 `[{index, changed, tag, text, box}]` |
| `state(max_items=50)` | 同上 | 限制数量 |
| `state(sel=".sidebar")` | 同上 | 限定范围 |
| `click_index(idx)` | `click_index(3)` | 点击第 N 个元素（CDP isTrusted:true） |
| `hover(sel)` | `hover(".menu")` | CSS 选择器悬停 |
| `hover_index(idx)` | `hover_index(3)` | 悬停第 N 个元素 |

### 文本查找与提取

| 函数 | 用法 | 说明 |
|------|------|------|
| `find_text("关键词")` | `find_text("喜欢")` | 搜索可见文本元素 |
| `get_markdown()` | `get_markdown()` | 提取页面为 Markdown |
| `get_markdown(sel="article")` | 同上 | 限定区域提取 |
| `box_of(sel)` | `box_of(".btn")` | 获取元素包围盒 `{x,y,w,h,visible,tag,text}` |

### 等待与状态

| 函数 | 用法 | 说明 |
|------|------|------|
| `wait_for_load()` | `wait_for_load()` | 等待页面加载（30s 超时） |
| `wait_for_load(60)` | 同上 | 自定义超时 |
| `wait_selector(sel, state)` | `wait_selector(".modal", "visible", 15)` | 等待元素状态（visible/hidden/attached/detached） |
| `sleep(seconds)` | `sleep(2)` | 暂停 |

### 滚动

| 函数 | 用法 | 说明 |
|------|------|------|
| `scroll_to(x, y)` | `scroll_to(0, 500)` | 滚动视口到坐标（window.scrollTo） |
| `scroll_wheel(dx, dy)` | `scroll_wheel(0, 500)` | CDP compositor 鼠标滚轮（穿透 iframe/shadow DOM） |
| `scroll_into_view(sel)` | `scroll_into_view("#target")` | 滚动元素到可见区域 |

### 网络与 Cookie

| 函数 | 用法 | 说明 |
|------|------|------|
| `get_cookies()` | `get_cookies()` | 获取所有 cookie |
| `get_cookies("https://x.com")` | 同上 | 按 URL 过滤 |
| `set_cookie(name, val)` | `set_cookie("t", "abc", domain=".x.com")` | 设置 cookie |
| `network_enable()` | `network_enable()` | 启用 CDP 网络请求捕获 |
| `get_response_body(id)` | `get_response_body("123.5")` | 获取 CDP 网络响应体 |

### HTTP（不启浏览器）

| 函数 | 用法 | 说明 |
|------|------|------|
| `http_get(url)` | `http_get("https://example.com")` | 纯 HTTP GET，适合静态页/API |

### 其他

| 函数 | 用法 | 说明 |
|------|------|------|
| `dialog_off()` | `dialog_off()` | 自动关闭 alert/confirm/prompt |
| `reload_extension()` | `reload_extension()` | 强制重载 Chrome 扩展（自愈用） |

## 领域技能 (domain-skills)

遇到特定网站先查 `domain-skills/<site>/`，不要重新发现已知规律。

```bash
ls domain-skills/
cat domain-skills/douyin/creator-stats.md
cat domain-skills/wechat-channels/post-list.md
```

厚逻辑（站点工作流）放 `domain-skills/` 目录。

### 抖音 (douyin/creator-stats.md)

| 函数 | 用法 | 说明 |
|------|------|------|
| `douyin_like("用户名")` | `douyin_like("籽岷")` | 搜索用户并点赞第一个视频 |
| `douyin_press(key)` | `douyin_press("z")` | 按抖音键盘快捷键 |

抖音键盘快捷键：`z`=点赞 `x`=评论 `c`=收藏 `G`=关注 `f`=首页 `b`=弹幕 `esc`=退出

详细爬取指南见 `domain-skills/douyin/creator-stats.md`。

### 视频号 (wechat-channels/post-list.md)

作品列表和粉丝统计爬取指南见 `domain-skills/wechat-channels/post-list.md`。

## 自愈机制

如果遇到缺失的功能，Agent 可以编辑 `helpers.py` 添加新函数。文件在每次调用前被重新加载，修改立即生效。

## 故障排查

| 问题 | 解决 |
|------|------|
| `Extension not connected` | 确保扩展已安装并在 chrome://extensions 中启用 |
| `No free port` | 检查端口 9230-9245 是否被占用 |
| CDP 命令超时 | 页面可能卡住，尝试刷新标签 |
