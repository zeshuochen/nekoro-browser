# nekoro-browser SKILL.md

## 概述

nekoro-browser 是浏览器自动化 CLI，通过 Chrome 扩展的 `chrome.debugger` API 操控 Chrome。

## 命令

### 启动和状态

```bash
nekoro-browser setup           # 引导式安装（首次用；给路径+开页面+等扩展连上）
nekoro-browser                 # 前台启动 daemon
nekoro-browser --doctor        # 端到端诊断（daemon + 扩展 + SW 是否都活着）
nekoro-browser --version       # 版本
nekoro-browser --stop          # 停止 daemon
nekoro-browser --restart       # 停止后重启（前台）
nekoro-browser --reload-ext    # 命扩展重载 service worker（跑批量任务前刷干净状态）
nekoro-browser --extension-path # 打印扩展目录（chrome://extensions 加载已解压扩展时用）
nekoro-browser --port 30500     # 换端口（默认 28417；等价于设 NEKORO_PORT）
nekoro-browser --timeout 300    # 单次执行超时（默认 120s；等页面加载/水合可能要更久）
```

端口两侧都要改：Python 侧用 `--port` / `NEKORO_PORT`，扩展侧在扩展选项页里设。
客户端不必重复传参——daemon 把实际端口写进 `<数据目录>/port`，管道模式会自己读。

### MCP（非 CLI 客户端）

不读文件的 agent 客户端（Cursor / Cline / Claude Desktop）走 MCP：
配 `{"mcpServers": {"nekoro-browser": {"command": "nekoro-browser-mcp"}}}`。
helpers 自动反射成工具，外加 `cdp`（原始 CDP）和 `exec_python`（任意 Python，
多步流程一次往返）。daemon 仍要在另一个终端跑着。

### 管道模式

```bash
# 单行
nekoro-browser -c "print(await page_info())"

# 多步 (heredoc)
nekoro-browser <<'PY'
await new_tab("https://example.com")
await wait_for_load()
print(await page_info())
PY

# stdin 管道 (兼容旧用法)
echo "await page_info()" | nekoro-browser
```

## 可用函数 (helpers)

绝大多数函数返回 `{"ok": True, ...}` 或 `{"ok": False, "error": "..."}`。`list_helpers()`
列出当前全部可用函数名。

**三个例外，别对它们判 `["ok"]`（会 KeyError）：**

| 函数 | 实际返回 |
|------|---------|
| `page_info()` | `{"title": ..., "url": ...}`，无 `ok` 键；扩展/SW 没响应时是两个空串（不是报错） |
| `drain_events()` | 裸 list |
| `http_get()` | 裸 str |

### Tab 管理

| 函数 | 用法 | 说明 |
|------|------|------|
| `new_tab(url)` | `new_tab("https://example.com")` | 新建标签页 |
| `navigate(url)` | `navigate("https://example.com")` | 当前标签导航（默认等加载完成） |
| `list_tabs()` | `list_tabs()` | 列托管组标签 `[{tabId,url,title,active,attached}]` |
| `switch_tab(id)` | `switch_tab(123)` | 切换活动标签（后续命令发往该标签） |
| `ensure_real_tab()` | `ensure_real_tab()` | 自动从 chrome:// 等内部页导航到 about:blank |
| `iframe_target(url_substr)` | `iframe_target("player")` | 获取 iframe 的 CDP targetId |
| `close_tab(tab=None)` | `close_tab(123)` | 关闭标签；省略则关当前 attached tab |

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
| `type_text(text)` | `type_text("hello")` | CDP Input.insertText（往当前焦点插字符） |
| `fill_input(sel, text)` | `fill_input("#email", "a@b.com")` | 框架感知填值：原生 setter + input/change，React/Vue 受控组件能收到 onChange |
| `press_key(key)` | `press_key("Enter")` | 按键（带 virtual key code + char 事件，特殊键/单字符都真实触发） |
| `press_key("c", 2)` | 同上 | Ctrl+C（1=Alt, 2=Ctrl, 4=Meta, 8=Shift） |
| `upload_file(sel, path)` | `upload_file("input[type=file]", r"C:\a.png")` | CDP 设置文件输入框的文件（str/Path 或其 list） |

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
| `wait_for_load()` | `wait_for_load()` | 等待页面加载（默认 15s 超时） |
| `wait_for_load(60)` | 同上 | 自定义超时 |
| `wait_selector(sel, state)` | `wait_selector(".modal", "visible", 15)` | 等待元素状态（visible/hidden/attached/detached） |
| `wait_for_network_idle(idle_time, timeout)` | `wait_for_network_idle(0.5, 15)` | 等待【当前活动标签】Network 请求静默 |
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

### 对话框与事件

| 函数 | 用法 | 说明 |
|------|------|------|
| `dialog_off()` | `dialog_off()` | JS 层覆盖 alert/confirm/prompt 为自动关闭（需在触发前调用） |
| `get_last_dialog()` | `get_last_dialog()` | 取最近一次被扩展自动处置的原生对话框，读后清；扩展会自动处置所有原生对话框（beforeunload 放行、其余取消），防止页面冻结 |
| `drain_events()` | `drain_events()` | 拉取自上次 drain 后缓存的 CDP 事件 |

### 自愈

| 函数 | 用法 | 说明 |
|------|------|------|
| `reload_extension()` | `reload_extension()` | 强制重载 Chrome 扩展 service worker |
| `reload_agent_helpers()` | `reload_agent_helpers()` | 重新加载 agent_helpers.py，无需重启 daemon |

## 领域技能 (domain-skills)

站点知识和固化流程都放 `<skills 根>/<site>/`（`NEKORO_DOMAIN_SKILLS` 指定，
默认回落到仓库内的 `domain-skills/`）。**仓库里默认是空的** —— 每个人自动化的站点不同。

```
<skills 根>/douyin/
    video-interaction.md    ← 知识：navigate 时自动送标题
    actions.py              ← 流程：每次 /exec 自动载入命名空间
```

### 路由：先看有没有现成的，再决定怎么做

`navigate()` / `new_tab()` 命中站点时，返回值里会多两个字段：

```python
{'ok': True, 'loaded': True,
 'notes':   ['douyin/video-interaction.md — 抖音 — 视频页交互'],
 'actions': ['douyin_like(username, video_index) — 搜索用户并给第一个视频点赞']}
```

**看到 `actions` 就直接调那个函数**（已在命名空间里），别从零推导一遍。
**看到 `notes` 里有相关标题就先读那份文件**再动手 —— `notes` 只给标题不给正文，
按需读，避免每次导航都付一遍全文的 token。`list_site_actions()` 可查全部已载入
函数及载入失败的文件。

### 写哪儿

| 重复的是 | 去处 |
|---|---|
| 同一个**站点**，任务每次不同 | `<site>/*.md` 知识笔记 |
| 同一个**任务**反复跑 | `<site>/*.py` 函数（第一个参数 `daemon`，改完立即生效） |

默认写笔记；同一任务出现 2~3 次再固化成函数，且必须写明验证信号。
与核心 helper 同名的站点函数会被跳过（不静默替换），冲突原因见 `list_site_actions()`。

### 什么时候提议沉淀

**只在这次运行确实学到东西时才开口**，至少命中一条：试了 ≥2 次才点中、
`wait_for_load()` 不够要额外等、直连失败改点击、发现快捷键、
**既有笔记被证伪**（最高优先级）。一把过就什么都别提 —— 那种运行没有产生
"哪一步是关键"的证据，写下来的必然是猜测。

开口时**先把草稿写好**，让用户只需回答 y/n，别问"要不要沉淀经验"。

### 格式

结论先行；**必须写验证方式**（怎么算成功）；文件拆小、标题写实（标题是 navigate
时唯一被送出的部分）；**失效的条目标注失效、不要删**（知道哪条路走不通同样值钱，
这类条目值得带上日期，因为它描述的是一个变化）。日期不强制 —— 反正每次都会先试，
年龄是有效性的弱代理，验证方式才是。详见 `domain-skills/README.md`。

## 自愈机制

缺功能时编辑 `src/nekoro_browser/agent_helpers.py` 添加——**只有这个文件**在每次
`/exec` 前自动 reload，改完立即生效。改 `helpers.py` 本身要重启 daemon（启动时导入一次）。

## 故障排查

| 问题 | 解决 |
|------|------|
| `Extension not connected` | 确保扩展已安装并在 chrome://extensions 中启用 |
| `Address already in use` | 端口被占：先 `nekoro-browser --stop`，或杀掉占用进程，或换端口 `--port N`（扩展侧也要在选项页改成同一个） |
| CDP 命令超时 | 扩展 service worker 可能睡死/卡住；`nekoro-browser --doctor` 定位，`--reload-ext` 或 chrome://extensions 手动重载 |
