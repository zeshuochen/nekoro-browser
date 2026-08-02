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
  example/
    search.md       ← knowledge  (surfaced on navigate)
    actions.py      ← workflows  (loaded into every /exec)
  another-site/
    notes.md
```

A directory matches when its name appears in the hostname, so `example/` covers
`www.example.com` and `admin.example.com` alike. If the domain has no natural word to key
on, name the directory after whatever fragment is distinctive.

## How it reaches the agent

`navigate()` and `new_tab()` add two fields when the site has material:

```python
{'ok': True, 'loaded': True,
 'notes':   ['example/search.md — Example — search results'],
 'actions': ['open_first_result(query) — search and open the top hit']}
```

`notes` lists **titles only**, never file contents — full text on every navigation would
turn a one-time write into a permanent read cost. Read the file when a title looks
relevant. `actions` lists callable functions; they are already in the `/exec` namespace,
so call them directly instead of rebuilding the flow. `list_site_actions()` shows
everything loaded plus any file that failed to load.

## Which one to write

| What repeats | Where it goes | Why |
|--------------|---------------|-----|
| The **site**, with a different task each time | `<site>/*.md` | Notes generalize across tasks; a function that performs one action is useless when you need a different one |
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
# Example — search results

## Opening a result

**Click the result card; don't navigate to the item URL directly.**
- Verify: the URL gains `?item=<id>` and the detail panel renders
- ~~Direct `/item/<id>` links~~ stopped working 2026-05: they render the logged-out page
  with no interactive UI. Don't retry that route.

## Reacting to an item

**Click the button and check the icon state, not the counter.**
- Verify: icon fill changes from the neutral colour to the active one
- Don't diff a displayed counter to confirm success — rounded counts (`12.3k`) don't move
  when you add one, so every popular item reports failure
```

Two rules earned the hard way:

**Always record how you verified it.** Dates are optional — you re-test on use anyway, and
age is a weak proxy for truth. What actually saves time is knowing what success looks like:
when a step fails, a verification signal tells you immediately whether the note is wrong or
your execution is.

**Mark dead facts, don't delete them.** Knowing a path is closed is worth as much as
knowing one is open — delete the line and the next agent burns the same minutes
rediscovering that it leads nowhere. This is the one case where a date earns its keep,
because it describes a change.

## What not to write

Anything visible from the page itself. Task-specific parameters — the search term, the ID
you happened to use, your own account's numbers. Anything you didn't verify.
