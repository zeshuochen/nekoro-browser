# domain-skills

Site-specific knowledge — page structure, selectors, quirks — for sites that took real
time to figure out. **Documentation only, no executable code**: the daemon's namespace is
`helpers.py` + `agent_helpers.py`, and nothing here is imported automatically.

The split is deliberate. `helpers.py` stays thin (every function is one CDP call, ≤10
lines) and site-specific workflows live here as notes an agent reads before acting, rather
than as a growing pile of per-site functions nobody else can use. When you need a
workflow, write it against these notes and drop it into
`src/nekoro_browser/agent_helpers.py` — it hot-reloads on the next `/exec`, no restart.

Current notes happen to cover Chinese platforms, because that is what the author
automates. They are examples of the convention as much as they are useful in themselves:

| File | What it covers |
|------|----------------|
| `douyin/video-interaction.md` | React RSC hydration timing, why direct `/video/` URLs render logged-out, like-button has no text label, keyboard shortcuts |
| `douyin/creator-stats.md` | Creator dashboard URLs, follower-count regexes, innerText layout, the ~6-day data lag |
| `wechat-channels/post-list.md` | Post list iframe structure, number formats |

Adding your own site is just a Markdown file. Write down what cost you an hour to
discover; skip what is obvious from the page.
