# Douyin (抖音) Domain Skill

## Architecture

Douyin uses React RSC (server components) with client hydration.
The page loads in stages:
1. Shell HTML with inline scripts → 2. RSC flight data → 3. React hydration (10-15s)

Content scripts in ISOLATED world see only the shell. MUST use MAIN world injection.

## Critical Patterns

### Navigation: Click don't navigate
```
❌ navigate("https://www.douyin.com/video/123")  → unauthenticated page
✅ click video link on search results page       → modal overlay, preserves auth
```

Direct URL navigation to `/video/` pages shows logged-out version without interactive UI.
Always click video cards from search/user pages — they open as modals.

### Like Button Detection
- **No text labels** — all icons are SVGs, numbers only
- **Location**: right-side action bar, `[class*=action]` div
- **Selector**: `[class*=action] > div:nth-of-type(1)` (first div after profile `<a>`)
- **Text matching fails** — must use coordinate or structural selector click

### Click Mechanism
- **Synthetic events don't work** — React checks `event.isTrusted`
- **CDP required**: `Input.dispatchMouseEvent` via `cdp_click_at(x, y)`
- Like button offset from action bar: `y = action_box.y + 160`

### Action Bar Layout
```
Right sidebar: x≈1216, y≈150, w=66, h≈485
├── <a> — Profile avatar/link
├── div — Like button (heart icon + count)
├── div — Comment button
├── div — Share button
└── div — Collect/bookmark button
```

## Gotchas
- `chrome.runtime.reload()` kills Service Worker — Chrome doesn't auto-restart. Must manually toggle in `chrome://extensions`.
- `send_scripting` times out if extension SW is dead — check port 19825 first
- CDP attach may fail if another debugger (Playwright, DevTools) is on the tab
- Modal videos: URL shows `?modal_id=...` not `/video/...`
- React hydration timeout: wait 8-12s after clicking video card before interacting
