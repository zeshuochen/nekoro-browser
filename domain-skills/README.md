# domain-skills

**Empty on purpose — this is where your own site notes go.**

Everyone automates different sites, so nekoro ships no site knowledge. The convention is
the useful part: page structure, selectors and quirks live here as Markdown an agent reads
before acting, and never as executable code. The daemon's namespace is only
`helpers.py` + `agent_helpers.py`; nothing in this directory is imported.

That split is what keeps `helpers.py` thin — every helper is one CDP call in ≤10 lines,
and none of them know about any particular website. When you need a workflow for a site,
write it against your notes and drop it into `src/nekoro_browser/agent_helpers.py`, which
hot-reloads on the next `/exec` (no daemon restart, no extension reload).

## Adding a site

Create `domain-skills/<site>/<topic>.md` and write down what cost you an hour to figure
out. Skip what is obvious from looking at the page. Things worth recording:

- URLs that behave differently than expected (e.g. a direct link rendering logged-out)
- Selectors for elements with no stable text or test id
- How long the app takes to hydrate, and what to wait on instead of a fixed sleep
- Keyboard shortcuts the site implements — usually more reliable than clicking
- Response shapes for the XHR/fetch endpoints you care about

Then point your agent at the directory. `SKILL.md` already tells agents to check
`domain-skills/<site>/` before rediscovering known quirks.
