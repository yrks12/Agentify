# Agentify

**Read a website once. Generate a JSON SDK. From then on, the LLM uses the
site like an API — without ever seeing the page.**

A mapper agent visits a site, proposes a list of tool functions
(`submit_contact_form`, `get_top_stories`, …), and records each one as a
deterministic recipe. At runtime, an LLM picks a tool from the schema and a
pure-replay engine executes it. No screenshots, no per-step LLM calls during
execution, no fragile prompting.

Built on Python + Playwright + OpenAI `gpt-5.4-mini`.

Agentify is an [Agent Skill](https://developers.openai.com/codex/skills) — the
single `SKILL.md` works in **both Claude Code and Codex** (and any tool that
reads the open SKILL.md standard). Only the install folder differs.

## Install

The skill bundles a Python venv + Playwright Chromium, so installing is: clone
into your tool's skills directory, then build the bundled environment. Paste the
matching prompt to your agent and it will do all of it for you.

### Claude Code

> Install the Agentify skill from `https://github.com/rivka2003/Agentify`.
> 1. `git clone https://github.com/rivka2003/Agentify ~/.claude/skills/Agentify`
> 2. `cd ~/.claude/skills/Agentify`
> 3. `python3 -m venv venv`
> 4. `venv/bin/pip install -e source`
> 5. `venv/bin/python -m playwright install chromium`
> 6. `cp source/.env.example source/.env` and set `OPENAI_API_KEY=` to my key.
>
> Then confirm the **Agentify** skill loads (`/skills`).

### Codex

> Install the Agentify skill from `https://github.com/rivka2003/Agentify`.
> 1. `git clone https://github.com/rivka2003/Agentify ~/.agents/skills/Agentify`
> 2. `cd ~/.agents/skills/Agentify`
> 3. `python3 -m venv venv`
> 4. `venv/bin/pip install -e source`
> 5. `venv/bin/python -m playwright install chromium`
> 6. `cp source/.env.example source/.env` and set `OPENAI_API_KEY=` to my key.
>
> Then confirm the **Agentify** skill loads (`/skills`).

The only difference is the target directory: Claude Code discovers skills in
`~/.claude/skills/`, Codex in `~/.agents/skills/`. The `SKILL.md` itself is
identical and path-independent (it resolves the install folder via `$SKILL_DIR`).

## Manual / dev setup

```bash
python -m venv venv && source venv/bin/activate
pip install -e source
playwright install chromium
cp source/.env.example source/.env   # OPENAI_API_KEY=...
```

### Phase 1 — generate an SDK for a site

```bash
agentify map --url https://news.ycombinator.com --name hackernews
```

What this does:
1. Crawls the landing page + a few same-origin nav links.
2. Sends the survey to the LLM; gets back proposed tools with JSON-schema
   parameters.
3. Shows you the proposals in the terminal — accept all or pick a subset.
4. For each accepted tool: drives the site once with sentinel placeholders
   (`__W2A_NAME__` → `{{name}}`), captures every browser action with a robust
   selector (role+name first, CSS fallback).
5. Writes `recipes/hackernews.tools.json`.

Add `--auto-approve` to skip the interactive approval step.

### Phase 2 — use the SDK

Two ways:

**Direct tool call** (no LLM, deterministic):
```bash
agentify call --site hackernews --tool get_top_stories --args '{"n": 5}'
```

**Natural language → tool pick** (LLM picks tool + arguments; never sees the page):
```bash
agentify run-mapped --site hackernews \
  --task "Give me the top 3 stories right now"
```

## How it works

```
PHASE 1 — MAP (one-shot per site)
─────────────────────────────────
        Crawler   ─→  surveys landing page + nav links
            ↓
        Proposer  ─→  LLM call: candidate tool list
            ↓
        Approver  ─→  CLI prompt: keep / drop
            ↓
        Recorder  ─→  drives site with placeholders, captures recipe
            ↓
   recipes/<slug>.tools.json

PHASE 2 — USE (every invocation)
─────────────────────────────────
   Registry   ─→  load recipes/<slug>.tools.json
        ↓
   Picker     ─→  LLM call: { tool_name, arguments }     (NO PAGE CONTENT)
        ↓
   Engine     ─→  deterministic replay of the recipe
```

### Recipe format

A site registry is `recipes/<slug>.tools.json`. Each tool has a name,
description, JSON-Schema `parameters`, and a list of `steps`:

```jsonc
{
  "name": "submit_contact_form",
  "description": "Submit a contact request.",
  "parameters": {
    "type": "object",
    "properties": {
      "name":  {"type": "string"},
      "email": {"type": "string"}
    },
    "required": ["name", "email"]
  },
  "steps": [
    {"op": "goto",   "url": "https://example.com/#contact"},
    {"op": "type",   "target": {"role": "textbox", "name": "Name *"},  "text": "{{name}}"},
    {"op": "type",   "target": {"role": "textbox", "name": "Email *"}, "text": "{{email}}"},
    {"op": "click",  "target": {"role": "button", "name": "Send"}},
    {"op": "wait",   "ms": 1500},
    {"op": "verify", "kind": "page_text_contains", "value": "thanks"}
  ]
}
```

### Engine op vocabulary (10 deterministic ops, zero LLM)

| op            | purpose                                                  |
|---------------|----------------------------------------------------------|
| `goto`        | navigate to a URL                                        |
| `click`       | click a Target                                           |
| `type`        | fill a textbox (supports `{{param}}` substitution)       |
| `select`      | pick an option in a combobox                             |
| `press_enter` | press Enter on a Target                                  |
| `scroll`      | scroll up / down / top / bottom                          |
| `wait`        | sleep N ms                                               |
| `extract`     | save text/value/attr from a Target into the result dict  |
| `js_extract`  | run arbitrary in-page JS for tricky extractions          |
| `verify`      | assert page state; failure raises `RecipeFailure`        |

### Target resolution (how recipes survive small DOM changes)

A `Target` records up to three strategies. The engine tries them in
priority order until one resolves to an element:

1. `role` + `name` — ARIA / accessibility tree (most stable).
2. `css` — recorded at map time from the element's id / name / data-testid
   / nth-of-type position.
3. `text` — visible text match.

## File-by-file

```
agentify/
├── cli.py             Typer commands: map, call, run-mapped, login
├── credentials.py     Interactive, masked credential prompting (never stored)
├── session.py         Runtime auth: load session, probe, lazy re-login, persist
├── browser.py         Playwright wrapper; actions keyed by element id
├── ax_tree.py         Injected JS that builds the numbered element list
│                      used during crawling and recording
├── selectors.py       Target dataclass + multi-strategy resolver
├── recipe.py          Recipe dataclass + deterministic Engine
├── registry.py        Load/save recipes/<slug>.tools.json + OpenAI conv.
├── recorder.py        Recording Browser subclass: tees every action
│                      into a recipe step list with placeholder binding
├── mapper.py          The full Phase-1 pipeline: Crawler + Proposer +
│                      Approver + Recorder (incl. login recording)
├── llm.py             OpenAI client + system prompts + tool schema
│                      (used by the Proposer, the recording driver,
│                      and the runtime Picker)
├── agent.py           Internal observe→think→act loop used by the
│                      Recorder to drive the site during mapping
└── memory.py          Step history used by the recording loop
```



## Multi-step flows (supported)

The mapper records arbitrary **linear multi-step** flows for any site, with no
per-site code, via four site-agnostic mechanisms:

- **Realistic example inputs** — the proposer attaches an `example` to each
  parameter (carried on `ToolProposal.examples`, used by `_placeholders_for`)
  so typeaheads/live-search respond during recording.
- **Autocomplete normalization** — `_normalize_autocomplete` rewrites
  `type {{param}} into a combobox → click a named suggestion` into
  `type {{param}} → verify an option exists → click the FIRST option`
  (`{"role": "option"}` resolves to `.first`), which is parameter-independent.
- **Auto result-extraction** — `record_action_recipe` appends a `js_extract`
  of the landing page so action tools return data.
- **Self-verifying record→replay** — `_record_verified_action` replays each
  freshly recorded recipe with the example args (`_verify_replay`) and
  re-records once with the failure as a hint if it doesn't replay.

What's still missing are *non-linear* shapes (loops, branches) and a few
binding edge cases — below.

## Sessions & login (auth)

Agentify maps and reuses **authenticated** sessions, so tools behind a login work.

**Mapping.** If a site has a sign-in form, `map` proposes a `login` tool. On
approval it **prompts you for credentials interactively** (secret fields masked
via `getpass`); they drive the form and are parameterised to `{{username}}` /
`{{password}}` in the recipe — **no credential is ever written to disk**. The
mapper derives a success probe (a Log out / Sign out control, else the post-login
URL), replay-verifies the login, and — only on success — saves the browser
session (cookies + localStorage + IndexedDB) to a gitignored
`source/sessions/<slug>.json`. The registry gains an `auth` block:
`{login_tool, check, storage_state}`. Login detection is deterministic — a tool
with a password field and a sign-in signal is treated as a login regardless of
the LLM's label; pure signup is excluded.

**Reuse — on by default.** `call` and `run-mapped` persist state by default in a
session named after the site, so cookies, localStorage and IndexedDB carry across
separate runs (the "continuous agent": do one action, then another, in the same
logged-in/stateful context). `--no-session` runs a fresh browser; `--session
<name>` selects a named/multi-account session. For sites with a login it verifies
you're still in with the probe and — only if the session is missing or expired —
re-runs the login (prompting once) and re-saves. That lazy re-auth is how a
session "stays logged in" across calls.

```bash
agentify call       --site shop --tool view_cart --args '{}'   # reuses sessions/shop.json
agentify run-mapped --site shop --task "what's in my cart?"
agentify call       --site shop --tool view_cart --args '{}' --no-session  # fresh

# Create/refresh a session explicitly:
agentify login --site shop --session shop
# MFA/CAPTCHA sites — sign in by hand in a visible browser, then it's captured:
agentify login --site shop --manual
```

Only the resulting `storage_state` is persisted (gitignored); credentials are
never stored.

## What this does NOT handle (and how you'd extend it)

The current system works well for **single-form submissions**,
**linear multi-step flows** (fill fields → pick suggestions → submit → read),
and **single-page data extraction**. Once you've internalised how it works,
these are the real seams where it falls over — listed with the concrete
fix each one needs:

### 1. ~~No session persistence between `call` invocations~~ — ✅ DONE
**Resolved** — see [Sessions & login](#sessions--login-auth) above. `map` records
a `login` tool (prompting for credentials, never storing them) and saves a
`storage_state` session; persistence is **on by default** for `call`/`run-mapped`
(per-site, `--no-session` to opt out), it re-authenticates lazily on expiry, and
persists on exit. **Cookies, localStorage and IndexedDB** are covered; only
`sessionStorage` (per-tab, ephemeral) and hardware-MFA remain out of scope (use
`login --manual` to bootstrap CAPTCHA/MFA sites by hand).

### 2. No iteration op (no pagination, no for-each)
Recipes are straight-line sequences. You can't say "for each story on
this list, open it and extract X." To paginate HN to page 5, you'd need
5 separate recipes or a `js_extract` that does all the work in one shot.

- **Why it matters:** scraping, batch processing, "show me all..." tasks.
- **Fix size:** medium. Add `for_each {items_target, sub_steps}` to the
  Engine op vocabulary; iterate `page.locator(items_target).all()` and
  run the sub-steps with a `{{_item}}` variable in scope. Mapper has to
  learn to *propose* such recipes for list-shaped sites.

### 3. No branching (`if_verify`)
`verify` either passes or raises `RecipeFailure`. There's no "if the
cart is empty, go shop; else go to checkout." Every conditional has to
live in the runtime LLM via tool composition, which is fine for top-level
decisions but awkward for "does this modal have a Cancel button or a
Close button?" micro-branches.

- **Why it matters:** any site with dialogs, optional flows, or
  validation errors that the recipe needs to react to.
- **Fix size:** medium. Add
  `if_verify {kind, value, then: [...steps], else: [...steps]}` to
  `recipe.py`'s Engine and recurse on the chosen branch.

### 4. Shallow Crawler → shallow tool proposals
The Crawler only visits the landing page + a few same-origin nav links.
Anything deep in a flow — logged-in pages, search results, multi-step
wizards, modals — is invisible to the Proposer, so no tools are proposed
for it. That's why Wikipedia got `search_wikipedia` but not
`get_article_facts(query)`: the Crawler never visited an article.

- **Why it matters:** the Proposer is the bottleneck on how rich the
  generated SDK is.
- **Fix size:** medium-large. Make the Crawler an agent itself —
  follow forms, interact with menus, capture state at each layer. This
  is real work because crawling becomes recursive and stateful (and may
  need login fixtures).

### 5. ~~Param binding fails on non-text actions~~ — ✅ DONE (mostly)
**Resolved** for the common cases. The Recorder now binds non-text selections
back to `{{param}}`: a native `<select>` whose chosen value/label equals a
placeholder, and a **click** on a radio / listbox-option / "open X by name" link
whose accessible name equals a placeholder value, are rewritten to resolve by
`{{param}}` (and the value-specific css fallback is dropped). Matching is exact
(case-insensitive, trimmed) so navigation clicks like "Book a Call" stay literal.
The mapper also tells the recording agent to pick the option whose visible label
is exactly the placeholder value. Replay needs no change — `recipe._substitute`
already templates `{{param}}` into a step's `target`.

- **Still out of scope:** boolean checkboxes (no text value to match), date
  pickers, file uploads, **custom (non-native) dropdown widgets** where the
  option isn't committed as a clean role+name, and two params sharing one value
  (first match wins). Inspect those recipes.

### Resilience (transient retries + optional steps) — built in

Replay is forgiving of real-site flakiness without sacrificing determinism:

- **Transient failures are retried.** A step that hits a Playwright timeout, a
  detached/re-rendered element, or a navigation/network race is retried a couple
  of times with a small backoff (`Engine(max_retries=…, backoff_s=…)`). A single
  blip no longer kills a tool that would succeed on the next try. Deterministic
  failures — a failed `verify`, an unknown op, a missing field — still **fail
  fast**, because retrying them only wastes time.
- **Non-critical steps can be marked `optional`.** An `"optional": true` step
  (and the presentational `scroll` / `wait` / `wait_for` ops) is logged and
  **skipped** on failure instead of aborting the recipe — handy for best-effort
  actions like dismissing a cookie banner.
- **Failures are one actionable line.** `RecipeFailure` carries the failing `op`
  and a de-noised, single-line reason (op + target + first error line) rather
  than a raw multi-line stack — better for you on `call` and for the LLM's
  recovery on `run-mapped`.

### Path forward

The cleanest design decision when extending is: **is the recipe a flat
sequence or a small language?**

- **Path X — keep recipes flat, push complexity to the LLM.**
  Add `Session` + improve param binding. Each tool stays a minimal
  atomic action (`login`, `view_cart`, `checkout_step_1`...). The
  runtime LLM composes them. Simple, predictable, more LLM calls.
- **Path Y — make recipes a small language.**
  Add `for_each`, `if_verify`, `subcall` to the Engine. One tool can
  encode "log in if needed, paginate to page N, extract everything,
  return." LLM only picks top-level tools. More expressive, more code
  to maintain.

The pragmatic order for this codebase: do (1) and (5) first — they're
small, high-value, and unlock most real multi-page cases. Add (2) only
when a concrete pagination task demands it. Defer (3) and (4) until
you've hit a wall with the simpler path.

## Example recipe

Recipes you generate with `agentify map` land in `recipes/` and are
git-ignored by default (they're yours, and may target private sites). A
neutral, public-site example ships in [`examples/`](examples/) so you can
see the format and try a replay without mapping anything first:

- `examples/hackernews.tools.json` — `get_top_stories` over
  [news.ycombinator.com](https://news.ycombinator.com)

## Tests

```bash
pip install -e "source[dev]"
pytest source   # hermetic: no Playwright / OpenAI key needed
```

## Configuration

`.env`:
```
OPENAI_API_KEY=sk-...
AGENTIFY_MODEL=gpt-5.4-mini     # any function-calling model
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md). Security issues: see
[SECURITY.md](SECURITY.md) — please report privately.

## License

[Apache-2.0](LICENSE). © Agentify contributors.
