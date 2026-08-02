<p align="center">
  <img src="extension/icons/icon-128.png" width="80" alt="nekoro-browser">
</p>

<h1 align="center">nekoro-browser</h1>

<p align="center">
  <a href="https://github.com/zeshuochen/nekoro-browser/actions/workflows/tests.yml"><img src="https://github.com/zeshuochen/nekoro-browser/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="#mcp-cursor--cline--claude-desktop"><img src="https://img.shields.io/badge/MCP-supported-8A2BE2" alt="MCP supported"></a>
</p>

<p align="center">
Lightweight browser automation CLI + MCP server. Drives your everyday Chrome through an extension — <b>keeps your login state</b>, <b>no debug port</b>, <b>no banners</b>.<br>
<sub><a href="README.zh-CN.md">中文</a></sub>
</p>

---

## Why Not `--remote-debugging-port`?

Since Chrome 136, `--remote-debugging-port` / `--remote-debugging-pipe` **refuse the default profile** — you must point Chrome at a non-default `--user-data-dir`, i.e. a clean instance with none of your logins. An extension's `chrome.debugger` is not subject to that restriction, which is why nekoro goes through an extension.

| | CDP WebSocket | playwright-cli | opencli | **nekoro-browser** |
|------|:--:|:--:|:--:|:--:|
| Approach | `--remote-debugging-port` | Playwright extension | OpenCLI extension | Custom extension + persistent WebSocket |
| Install | one flag | `npm i -g` (~200MB) | npm / desktop app | `pip install` (stdlib only, zero deps) |
| Login state | ❌ fresh instance | ✅ | ✅ | ✅ |
| Modify the extension | — | Edit Playwright source | Edit OpenCLI source | ✅ right in this repo |
| Self-healing | ❌ | ❌ | ❌ | ✅ Agent edits helpers at runtime |
| MCP | ❌ | ✅ (separate `@playwright/mcp`) | ❌ | ✅ built in, 45 tools via `nekoro-browser-mcp` |

## Quick Start

**1 — Install** (Python 3.12+, zero third-party dependencies)

```bash
git clone https://github.com/zeshuochen/nekoro-browser
cd nekoro-browser
pip install -e .
```

**2 — Load the extension**

```bash
nekoro-browser setup
```

`setup` copies the extension directory to your clipboard and then waits — up to three
minutes — until the extension actually connects, so you find out it worked instead of
guessing. Meanwhile you do the part Chrome reserves for humans: open `chrome://extensions/`,
turn on **Developer mode**, click **Load unpacked**, paste the directory.

**3 — Start the daemon** — give it its own terminal and **leave it open**; it runs in the
foreground and closing that window stops it

```bash
nekoro-browser
```

**4 — Drive the browser** from anywhere else

```bash
echo "page_info()" | nekoro-browser
# → {"ok": true, "result": {"title": "...", "url": "..."}}
```

That's it. If step 4 says the daemon isn't running, or a command times out, run
`nekoro-browser --doctor` — it checks the daemon, the extension and the service worker
separately and tells you which one is down.

## Examples

Send a multi-step flow in one shot with a heredoc. Every helper is a top-level `await`:

```bash
nekoro-browser <<'PY'
await new_tab("https://example.com")
print((await page_info())["title"])            # Example Domain
print((await get_markdown(max_chars=200))["result"])
print((await state(max_items=3))["result"])    # indexed interactive elements, model-ready
await close_tab()
PY
```

`state()` numbers the elements and `click_index(n)` clicks by number — the model never has to guess a CSS selector:

```bash
nekoro-browser <<'PY'
await navigate("https://github.com/search?q=browser+automation&type=repositories")
await wait_for_load()
print((await state(max_items=40))["result"])
await click_index(12)
PY
```

All helpers are documented in [SKILL.md](SKILL.md).

## MCP (Cursor / Cline / Claude Desktop)

Every function in `helpers.py` is reflected into an MCP tool (45 today) — no glue code:

```json
{
  "mcpServers": {
    "nekoro-browser": {
      "command": "nekoro-browser-mcp"
    }
  }
}
```

The daemon still has to be running in another terminal (`nekoro-browser`) — the MCP server just forwards tool calls to it over the same authenticated path as `echo ... | nekoro-browser`. Two escape hatches ship as tools: `cdp` (raw CDP command) and `exec_python` (arbitrary Python in the daemon namespace — a whole multi-step flow in one round trip).

Screenshots come back as image content so clients can render them. A helper's own failure (`{"ok": false}`) is surfaced as `isError` rather than being dressed up as success.

## API

| Category | Commands |
|----------|----------|
| Navigation | `navigate(url)`, `new_tab(url)`, `list_tabs()`, `switch_tab(id)`, `close_tab(id)` |
| Page info | `page_info()`, `page_html()`, `page_text()`, `get_markdown()`, `state()` |
| JavaScript | `js(code)`, `cdp(method, **p)`, `cdp_batch(*cmds)` |
| Interaction | `click_selector(sel)`, `click_index(n)`, `click_at_xy(x,y)`, `type_text(t)`, `fill_input(sel,t)`, `press_key(k)`, `upload_file(sel,path)` |
| Dialogs | `dialog_off()`, `get_last_dialog()` |
| Waiting | `wait_for_load()`, `wait_selector(sel)`, `wait_for_network_idle()`, `sleep(s)` |
| Screenshots | `capture_screenshot()`, `capture_screenshot("jpeg", 90)` |

## Architecture

```
Chrome extension (background.js) —— chrome.debugger / CDP
        ↕ persistent WebSocket
Python daemon (127.0.0.1:28417)
        ↕ HTTP /exec (token auth)
CLI (nekoro-browser)  ·  MCP server (nekoro-browser-mcp)
```

`helpers.py` (46 thin wrappers) → CDP commands, each ≤10 lines, none of them aware of any particular website.

`lifecycle.py` manages the daemon: pid file + process fingerprint (avoids killing a reused pid), self-heal on stale daemon (CDP probe fails → auto cleanup and restart), localhost requests bypass the system proxy.

The extension is hardened against MV3 service worker eviction: a `content_scripts` heartbeat (an independent wake vector living in the page, reconnects and wakes the SW even after it's killed) + `onStartup` (reconnects instantly on Chrome cold start) + reattaches the last-driven tab after a restart instead of drifting to a blank tab.

## CLI

| Command | What it does |
|---------|---------------|
| `nekoro-browser` | Start the daemon (foreground) |
| `nekoro-browser setup` | Guided install: extension path + opens chrome://extensions + waits for it to connect |
| `nekoro-browser --doctor` | End-to-end diagnostic (daemon + extension + SW all alive?) |
| `nekoro-browser --stop` | Stop the daemon |
| `nekoro-browser --restart` | Stop and restart (foreground) |
| `nekoro-browser --reload-ext` | Reload the extension's service worker — run before a batch job for a clean state |
| `nekoro-browser --extension-path` | Print the extension directory (for "Load unpacked") |
| `nekoro-browser --port N` | Run the daemon on port N (default 28417) |
| `nekoro-browser -c "code"` | Run one snippet, print the result |
| `nekoro-browser --timeout N` | Seconds to allow a snippet (default 120 — page loads are slow) |
| `echo "code" \| nekoro-browser` | Pipe mode (daemon must already be running) |

## Configuration

The daemon listens on **28417** by default. To change it:

| Side | How |
|------|-----|
| Python (daemon + CLI + MCP) | `nekoro-browser --port 30500`, or set `NEKORO_PORT=30500` |
| Extension | Extension details → **Extension options** → set the port → Save (reconnects immediately, no reload) |

Both sides must agree. Clients don't need the flag repeated: the daemon records its
actual port in `<data dir>/port`, so a plain `echo ... | nekoro-browser` finds a daemon
running on a non-default port. Precedence is `--port` > `NEKORO_PORT` > that file > default.

## Self-Healing

`src/nekoro_browser/agent_helpers.py` is editable at runtime and reloaded on every `/exec`. When an agent hits a gap, it appends the missing function there — effective on the next call, no daemon restart, no extension reload.

`domain-skills/` is where site knowledge goes (page structure, selectors, gotchas) — Markdown only, and empty by default since everyone automates different sites. Write a workflow against your notes, drop it into `agent_helpers.py`, same convention (`daemon` as first argument). See [`domain-skills/README.md`](domain-skills/README.md).

## Platform Support

| Platform | Status |
|----------|--------|
| Windows | Primary development platform, exercised end to end |
| Linux / macOS | The code has the branches (XDG dirs, `chmod 600` token, `/proc` and `ps` liveness probes) and CI runs the unit tests on all three, **but the full "Chrome + extension" loop has never been run on a real macOS/Linux box** — reports welcome |

## Known Limitations

- **Unpacked extensions get disabled by Chrome.** An extension installed via "Load unpacked" may be switched off automatically after a Chrome update or restart, or hidden behind the "Disable developer mode extensions" prompt. When `--doctor` reports Extension/SW not responding, re-enable it in `chrome://extensions/` first. This project is **not published to the Chrome Web Store**, so the limitation is not going away soon.
- **Service worker keepalive is not 100%.** MV3 eviction timing is Chrome's call. The heartbeat + `onStartup` + reattach cover the vast majority of cases, but unattended long-running cron jobs should still health-check with `--doctor` and retry.
- **One active tab at a time.** Tabs can be listed and switched (`list_tabs` / `switch_tab`), but commands always go to the current active tab — there are no parallel sessions.
- **The MCP server handles requests serially.** During a `wait_selector(timeout=90)` every other request on that connection (including `ping`) queues behind it. Open separate client connections if you need concurrency.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Daemon not running` | Daemon not started | Run `nekoro-browser` in terminal 1 |
| CDP timeout | Extension not connected / service worker asleep | `nekoro-browser --doctor` to diagnose; try `--reload-ext` or manually reload in `chrome://extensions` |
| Extension disabled by Chrome | Unpacked extension + Chrome update | Re-enable it in `chrome://extensions/`, then re-run `--doctor` |
| Page unchanged | Extension not attached to tab | Open a regular (non-chrome://) page, restart daemon |
| Port in use | Stale process | Kill the process on port 28417, or just run `nekoro-browser --stop` |

## Security

The daemon listens on `127.0.0.1` and `/exec` runs arbitrary Python, so the transport is guarded:

- **CLI / MCP → daemon** (`/exec`, `/raw`): a per-session token is written to a user-private file (`%LOCALAPPDATA%\nekoro-browser\token`, `chmod 600` on POSIX). Clients read it and send `X-Nekoro-Token`; missing/wrong token → `403`. Web pages and remote hosts can't read local files, so they can't obtain it. `/ping` stays open.
- **Extension → daemon** (`/ws`): the handshake `Origin` must be `chrome-extension://…`; a web page's `WebSocket` to localhost carries its own origin and is rejected.

Same-user local processes can read the token file — that boundary matches the OS user account, as with browser-harness's `chmod 600`.

## Feedback

Hit a problem, or missing a helper you need? Open an
[issue](https://github.com/zeshuochen/nekoro-browser/issues).
For bugs, include the output of `nekoro-browser --doctor`, your Chrome version and OS — saves a round trip.

PRs welcome. Run the tests first: `for f in tests/test_*.py; do python "$f"; done` (CI runs them on all three platforms too).

---

## Acknowledgments

Core architecture derived from:

- **[browser-harness](https://github.com/browser-use/browser-harness)** — thin-wrapper philosophy (each function is a CDP alias, ≤10 lines), pipe mode, self-healing `agent_helpers.py`, domain-skills directory structure, `cdp()` raw access
- **[browser-act](https://github.com/browser-act/skills)** — `state()` indexed element tree, `*[N]` change markers, `waitSelector()` state polling, `getMarkdown()` page extraction
- **[Playwright](https://github.com/microsoft/playwright)** — CDP `Input.dispatchMouseEvent` real mouse events (`isTrusted:true`), extension + daemon dual-path architecture
