# nekoro-browser

> 轻量浏览器自动化 CLI。通过 Chrome 扩展操控你的日常浏览器——保留登录态，零端口，零弹窗。

## 与其他方案的区别

| 方案 | 原理 | 保留登录态？ | 需重启 Chrome？ |
|------|------|:--:|:--:|
| CDP WebSocket | `--remote-debugging-port` | ❌ 独立实例 | ✅ |
| playwright-cli `--extension` | Playwright 扩展 | ✅ | ❌ |
| opencli | OpenCLI 扩展 | ✅ | ❌ |
| **nekoro-browser** | 自建扩展 + HTTP polling | ✅ | ❌ |

Chrome 136+ 禁用了默认配置的远程调试端口。playwright-cli 和 opencli 也走了扩展路线，但它们依赖各自的专有扩展。nekoro-browser 把扩展源码一并给你，完全透明。

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

> ⚠️ **先加载扩展，再启动 daemon。**

**终端 1** — 启动 daemon（保持打开）：

```bash
nekoro-browser
# 看到 "ready" 表示扩展已连接
```

**终端 2** — 验证：

```bash
echo "page_info()" | nekoro-browser
# → {"ok": true, "result": {"title": "...", "url": "..."}}
```

## 实战：打开 B站，搜籽岷，给最新视频点赞

```bash
# 1. 导航
echo "navigate('https://search.bilibili.com/all?keyword=籽岷')" | nekoro-browser
echo "sleep(3)" | nekoro-browser

# 2. 找到第一个视频链接
echo "js(\"return document.querySelector('a[href*=\\\\"/video/BV\\\\\"]')?.href\")" | nekoro-browser
# → {"ok": true, "result": "https://www.bilibili.com/video/BV1HdjX6YErC/"}

# 3. 打开视频
echo "navigate('https://www.bilibili.com/video/BV1HdjX6YErC/')" | nekoro-browser
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
