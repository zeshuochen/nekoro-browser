# domain-skills

**Empty on purpose — this is where your own site knowledge goes.**

Everyone automates different sites, so nekoro ships none. What it ships is the convention,
plus the machinery that makes it pay off: notes and functions you put here are **pushed to
the agent at the moment it navigates**, instead of waiting to be discovered.

## Where it lives

Put your own material in a directory you own, outside the package, and point nekoro at it:

```bash
NEKORO_DOMAIN_SKILLS=/path/to/my/skills      # or %LOCALAPPDATA%\nekoro-browser\skills
```

Without the variable, nekoro falls back to this directory inside the repo. That works, but
`pip install -U` can overwrite it and `git pull` will conflict with your edits — keep
anything you care about outside.

One directory per site, holding both kinds of material:

```
<skills root>/
  douyin/
    video-interaction.md    ← knowledge  (surfaced on navigate)
    actions.py              ← workflows  (loaded into every /exec)
  github/
    notes.md
```

A directory matches when its name appears in the hostname, so `douyin/` covers
`www.douyin.com` and `creator.douyin.com` alike. If the domain doesn't contain a natural
word, name the directory after a piece of the domain instead.

## How it reaches the agent

`navigate()` and `new_tab()` add two fields when the site has material:

```python
{'ok': True, 'loaded': True,
 'notes':   ['douyin/video-interaction.md — Douyin — video page'],
 'actions': ['douyin_like(username, video_index) — like a creator's first video']}
```

`notes` lists **titles only**, never file contents — full text on every navigation would
turn a one-time write into a permanent read cost. Read the file when a title looks
relevant. `actions` lists callable functions; they are already in the `/exec` namespace,
so call them directly. `list_site_actions()` shows everything loaded plus any file that
failed to load.

## Which one to write

| What repeats | Where it goes | Why |
|--------------|---------------|-----|
| The **site**, with a different task each time | `<site>/*.md` | Notes generalize across tasks; a function that likes a video is useless when you need to comment |
| The **task**, run over and over | `<site>/*.py` | Executable, no re-derivation |

Write notes by default. Promote to a function once the same task has come up **two or
three times** — and only with a real verification signal (see below). Functions take
`daemon` as their first argument, exactly like the built-in helpers, and are reloaded on
every `/exec`, so edits take effect immediately. A function whose name collides with a
built-in helper is skipped, not silently substituted.

`src/nekoro_browser/agent_helpers.py` is scratch paper for a quick experiment. Move
anything worth keeping into your skills directory — the package file gets overwritten by
upgrades.

## When to write anything at all

Only when the run actually taught you something. Concretely, at least one of:

- it took two or more attempts to hit the right element
- `wait_for_load()` wasn't enough and you had to wait on something else
- direct navigation failed and you had to click instead
- you found a keyboard shortcut the site implements
- **an existing note turned out to be wrong** — highest priority of all

If the task worked first try, write nothing. Not because it has no value, but because a
first-try run produces no evidence of *which step mattered*; anything you write down is a
guess, and guesses read like facts once they're in a file.

Ask before writing. Propose the finished text so the answer is yes or no, not an
invitation to author documentation.

## Format that survives

Lead with the conclusion — the agent reads this before acting, not afterwards. State how
to verify success. Keep files small and give them honest titles: **the title is the only
part surfaced on navigation**, so it is the index.

```markdown
# Douyin — video page

## Liking

**Click the heart in the action bar; don't use the keyboard shortcut.**
- Verify: heart fill changes from `currentColor` to `rgb(254,44,85)`
- Don't diff the like count: `1.8万` is rounded, so +1 leaves the text unchanged and
  every high-count video reports failure
- ~~Shortcut `z`~~ stopped working 2026-08: the tooltip still advertises it, but nothing
  happens while `activeElement` is `BODY`; it needs the player focused. Don't retry it.

## Opening a video

**Click the card from search or profile pages; never navigate to the URL.**
- A direct `/video/<id>` renders the logged-out page with no interactive UI
- After clicking, the URL becomes `?modal_id=<id>` and the session is preserved
```

Two rules earned the hard way:

**Always record how you verified it.** Dates are optional — you re-test on use anyway, and
age is a weak proxy for truth. What actually saves time is knowing what success looks like:
when a note fails, a verification signal tells you instantly whether the note is wrong or
your execution is.

**Mark dead facts, don't delete them.** Knowing a path is closed is worth as much as
knowing one is open — without the note above, the next agent burns the same minutes
rediscovering that `z` does nothing. This is the one case where a date earns its keep,
because it describes a change.

## What not to write

Anything visible from the page itself. Task-specific parameters — the search term, the
ID you happened to use. Anything you didn't verify.
