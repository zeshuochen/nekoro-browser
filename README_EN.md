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
Lightweight browser automation CLI. Control your daily Chrome via extension — <b>keep cookies</b>, <b>zero ports</b>, <b>no banners</b>.<br>
<sub><a href="README.md">中文</a></sub>
</p>

---

## Why Not CDP WebSocket?

Chrome 136+ disables `--remote-debugging-port` for default profiles. nekoro-browser uses a custom extension + HTTP polling instead.

| | CDP WebSocket | playwright-cli | opencli | **nekoro-browser** |
|------|:--:|:--:|:--:|:--:|
| Approach | `--remote-debugging-port` | Playwright extension | OpenCLI extension | Custom extension + HTTP polling |
| Install | one flag | `npm i -g` (~200MB) | npm / desktop app | `pip install` (stdlib only) |
| Login state | ❌ fresh instance | ✅ | ✅ | ✅ |
| Modify extension | — | Edit Playwright source | Edit OpenCLI source | ✅ right in this repo |
| Self-healing | ❌ | ❌ | ❌ | ✅ Agent edits helpers.py at runtime |
| Scriptable flows | ❌ | ❌ | ❌ | ✅ Compose flows as helpers, one command |

## Install

```powershell
git clone https://github.com/zeshuochen/nekoro-browser
cd nekoro-browser
pip install -e .       # registers the `nekoro-browser` command
```

Load the Chrome extension:
1. Open `chrome://extensions/`, enable "Developer mode"
2. "Load unpacked" → select the `extension/` directory
3. Verify no errors

## Quick Start

> ⚠️ **Load the extension first, then start the daemon.** Wrong order = 60s timeout.

**Terminal 1** — start the daemon (keep it running):

```bash
nekoro-browser
```

**Terminal 2** — verify it works:

```bash
echo "page_info()" | nekoro-browser
# → {"ok": true, "result": {"title": "...", "url": "..."}}
```

## Example: Open Bilibili, Search, Like a Video

```bash
# 1. Search
echo "navigate('https://search.bilibili.com/all?keyword=some+creator')" | nekoro-browser
echo "sleep(3)" | nekoro-browser

# 2. Find first video link
echo "js(\"return document.querySelector('a[href*=\\\\"/video/BV\\\\\"]')?.href\")" | nekoro-browser
# → {"ok": true, "result": "https://www.bilibili.com/video/BV..."}

# 3. Open the video
echo "navigate('https://www.bilibili.com/video/...')" | nekoro-browser
echo "sleep(4)" | nekoro-browser

# 4. Click like
echo "js(\"document.querySelector('[class*=like]:not([class*=dislike])')?.click(); 'done'\")" | nekoro-browser
# → {"ok": true, "result": "done"}
```

## API

All 20 helpers documented in [SKILL.md](SKILL.md). Common ones:

| Category | Commands |
|----------|----------|
| Navigation | `navigate(url)`, `new_tab(url)` |
| Page info | `page_info()`, `page_html()`, `page_text()` |
| JavaScript | `js(code)` |
| Interaction | `click_selector(sel)`, `click_at_xy(x,y)`, `type_text(t)`, `press_key(k)` |
| Waiting | `wait_for_load()`, `wait_for_selector(sel)`, `sleep(s)` |
| Screenshots | `capture_screenshot()`, `capture_screenshot("jpeg", 90)` |

## Self-Healing

Edit `helpers.py` at runtime. Agent adds missing functions on failure — takes effect on next call, no restart needed.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Daemon not running` | Daemon not started | Run `nekoro-browser` in terminal 1 |
| CDP timeout | Extension not connected | Check `chrome://extensions` for errors |
| Page unchanged | Extension not attached to tab | Open a regular (non-chrome://) page, restart daemon |
| Port in use | Stale process | Kill the process on port 9230 |

---

## Acknowledgments

Inspired by (no code used):
- [browser-harness](https://github.com/nicholasgriffintn/browser-harness) — pipe mode
- [playwright-cli](https://github.com/microsoft/playwright-cli) / [opencli](https://github.com/jackwener/opencli) — extension + daemon architecture
- [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) — CDP docs
