nekoro-browser is a CLI that lets an agent drive the user's daily Chrome via a MV3
extension (`chrome.debugger` API) + a local daemon, keeping the real login state —
no `--remote-debugging-port` (Chrome 136+ blocks that on the default profile anyway).

# Code priorities
- Clarity
- Precision
- Low verbosity
- Never fabricate success — helpers return `{"ok": false, "error": ...}` on failure,
  not a silently-wrong `{"ok": true}`

# Overview
Two halves, one wire (HTTP + WebSocket, same port):
- `extension/` — MV3 extension. `background.js` is the service worker: WS transport to
  the daemon, `chrome.debugger` attach/CDP dispatch, dialog auto-handling, tab lifecycle.
  `keepalive.js` is a content script that gives the service worker an independent wake
  vector (MV3 workers get evicted; a plain WS/alarm keep-alive alone isn't reliable).
- `src/nekoro_browser/` — the daemon + CLI.
  - `daemon.py` — long-lived middleman process between the extension and the agent's `-c` code
  - `bridge.py` — the WS/HTTP transport
  - `lifecycle.py` — pid file, process fingerprint, stale-daemon self-heal
  - `helpers.py` — CDP wrapper functions auto-imported into `-c` scripts, each a thin
    (≤10 line) wrapper over one CDP capability
  - `cli.py` — the `nekoro-browser` command

`SKILL.md` tells agents how to use the CLI and lists every helper. `README.md` covers
install + quick start for humans.

An agent operating nekoro-browser edits two places:
- `src/nekoro_browser/agent_helpers.py` — task-specific browser helpers the agent adds
  at runtime; hot-reloaded via `reload_agent_helpers()`, no daemon restart needed
- `agent-workspace/domain-skills/` — site-specific playbooks the agent writes and reads

# Testing
`tests/*.py` are stdlib-style, not pytest: `assert` + a final `print("ALL OK")`, run via
`uv run python tests/test_X.py`. Extension JS has no unit-test surface — verify syntax
with `node --check`, behavior needs a live Chrome.

# Contributing
Consider what is really needed. Prefer the smallest diff that fixes the bug. Don't add
speculative config/flags for scenarios that aren't happening yet.
