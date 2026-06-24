# nekoro-browser

> 轻量浏览器自动化 CLI。通过 Chrome 扩展操控你的日常浏览器——保留登录态，零端口，零弹窗。

## 与其他方案的区别

| 方案 | 需要重启 Chrome？ | 保留登录态？ | 有弹窗？ |
|------|:--:|:--:|:--:|
| CDP WebSocket (`--remote-debugging-port`) | ✅ 需要 | ❌ 独立实例 | ❌ 无 |
| nekoro-browser | ❌ 不需要 | ✅ 你的 Chrome | ❌ 无 |

Chrome 136+ 对默认用户配置禁用了远程调试端口。nekoro-browser 用扩展的 `chrome.debugger` API 绕过去。

## 安装

```powershell
git clone https://github.com/zeshuochen/nekoro-browser
cd nekoro-browser
pip install -e .       # 注册 nekoro-browser 命令
```

然后加载 Chrome 扩展：
1. 打开 `chrome://extensions/`，开启「开发者模式」
2. 「加载已解压的扩展程序」→ 选择 `extension/` 目录
3. 确认扩展列表中出现 nekoro-browser，无报错

## 快速开始

> ⚠️ 顺序：**先加载扩展，再启动 daemon**。

**终端 1** — 启动 daemon（保持打开）：

```bash
nekoro-browser
# 看到 "Ready" 表示扩展已连接
```

**终端 2** — 验证能工作：

```bash
echo "page_info()" | nekoro-browser
# → {"ok": true, "result": {"title": "...", "url": "..."}}
```

## 实战示例：打开 B站、搜索、截图

```bash
# 1. 导航到 B站
echo "navigate('https://www.bilibili.com')" | nekoro-browser

# 2. 搜索 UP 主
echo "navigate('https://search.bilibili.com/all?keyword=籽岷')" | nekoro-browser

# 3. 等页面加载后截图
echo "sleep(3)" | nekoro-browser
echo "capture_screenshot()" | nekoro-browser

# 4. 获取搜索结果标题
echo "page_info()" | nekoro-browser
```

## API

全部 20 个 helpers 见 [SKILL.md](SKILL.md)。常用：

| 类别 | 命令 |
|------|------|
| 导航 | `navigate(url)`, `new_tab(url)` |
| 信息 | `page_info()`, `page_html()`, `page_text()` |
| JS | `js(code)` |
| 交互 | `click_selector(sel)`, `click_at_xy(x,y)`, `type_text(t)`, `press_key(k)` |
| 截图 | `capture_screenshot()`, `capture_screenshot("jpeg", 90)` |
| 等待 | `wait_for_load()`, `wait_for_selector(sel)`, `sleep(s)` |

## 自愈

`helpers.py` 运行时随时可修改。编辑文件添加缺失函数，下次执行立即生效——无需重启。

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `Daemon not running` | daemon 没启动 | 终端 1 运行 `nekoro-browser` |
| CDP 命令超时 | 扩展未连接 | 检查 `chrome://extensions`，确认扩展已加载且无报错 |
| 页面没变化 | 扩展未 attach 到 tab | 打开一个普通网页（非 chrome://），重启 daemon |
| `address already in use` | 旧进程占用端口 | 杀掉占用 9230 的进程 |
