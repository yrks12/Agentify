---
name: Agentify
description: Turn any website into an LLM-callable API. Phase 1 ("map") visits a site, proposes tool functions, and records deterministic Playwright recipes into recipes/<slug>.tools.json. Phase 2 ("call" or "run-mapped") replays a recipe directly or lets the LLM pick a tool from natural language — the page itself is never sent to the model. Use when the user wants to scrape, automate, or expose a website as tools.
argument-hint: "[map|call|run-mapped|login] [...args]"
allowed-tools: Bash, Read
---

# Agentify

Self-contained skill that bundles the `agentify` CLI, its Python venv, and the Playwright Chromium browser. Nothing installs at invocation time — just run the bundled interpreter.

> **Skill location (`$SKILL_DIR`).** This same skill runs in both Claude Code
> and Codex; only the install folder differs. Every command below uses
> `$SKILL_DIR` for that folder — set it to whichever applies to your tool:
>
> ```bash
> export SKILL_DIR=~/.claude/skills/Agentify   # Claude Code
> export SKILL_DIR=~/.agents/skills/Agentify   # Codex
> ```

## Run with the bundled venv (never `pip install` again)

The skill ships everything pre-installed at `$SKILL_DIR/venv/` and the source at `$SKILL_DIR/source/`. Always invoke through the venv's Python so dependencies resolve correctly:

```bash
"$SKILL_DIR/venv/bin/python" -m agentify.cli --help
```

The CLI's `_load_env` walks up from the package and finds `source/.env` automatically, so `OPENAI_API_KEY` is already wired up.

## Phase 1 — map a site (one-shot per site)

Crawls the landing page, asks the LLM to propose tool functions, prompts you to accept/reject each one, then drives the site to record a deterministic recipe per tool. Output is written to `$SKILL_DIR/source/recipes/<name>.tools.json`.

```bash
"$SKILL_DIR/venv/bin/python" -m agentify.cli map \
  --url https://news.ycombinator.com --name hackernews
```

Flags:
- `--auto-approve` — skip the interactive accept/reject step
- `--no-headless` — show the browser while recording
- `--model gpt-4o-mini` — override `AGENTIFY_MODEL` from .env
- `--max-pages N` / `--max-depth D` — how far the crawler explores (default 10 / 2)

### Preview the crawl (no LLM)

See exactly which pages `map` will survey — handy for tuning `--max-pages`/`--max-depth` before paying for proposals + recording. Read-only; no model call.

```bash
"$SKILL_DIR/venv/bin/python" -m agentify.cli crawl \
  --url https://news.ycombinator.com --max-pages 8 --max-depth 2
```

Add `--session NAME` to crawl with a saved login (`storage_state`) so authenticated pages are visited too.

### Multi-step flows (any site, no per-site code)

`map` records arbitrary linear multi-step flows — fill several fields, pick
autocomplete suggestions, submit, read the result page — using four
site-agnostic mechanisms:

1. **Realistic example inputs.** The proposer supplies a real value per field
   (`"TLV"`, not `"xxx"`), so dynamic widgets (typeaheads, live search) respond
   while recording. Without this, dropdowns never open and the flow can't be
   captured.
2. **Autocomplete normalization.** Any `type into a combobox → click a
   suggestion` is rewritten to "type `{{param}}` → wait for an option → click
   the **first** option," which is input-independent and replays for any value.
3. **Auto result-extraction.** Whatever page the flow lands on, the mapper
   generates a `js_extract` so the tool returns data.
4. **Self-verifying record→replay.** After the LLM records the steps, the
   mapper deterministically replays the recipe with the example values to prove
   it works; on failure it re-records once with the failure fed back as a hint.
   The console prints a `replay check passed/failed` line per tool.

Recording costs LLM calls once, at map time. Replay (`call`) is pure Playwright
with **zero** LLM calls — typically a few seconds for a multi-step flow, versus
an LLM round-trip per step in a general agentic browser.

### Logins & sessions (mapped automatically)

If the landing page has a sign-in form, `map` proposes a `login` tool. When you
approve it, the CLI **prompts you for the credentials interactively** (secret
fields are masked via `getpass`). They drive the form and are parameterised to
`{{username}}`/`{{password}}` in the recipe — **no credential is ever written to
disk**. The mapper derives a success probe, replay-verifies the login, and saves
the resulting browser session (cookies + localStorage + IndexedDB) to a gitignored
`$SKILL_DIR/source/sessions/<slug>.json`. The registry also gains an `auth` block
describing how to re-authenticate. (Login detection is deterministic — a tool
with a password field and a sign-in signal is treated as a login regardless of
how the LLM labelled it; pure signup is excluded.)

## Phase 2a — call a single tool directly (no LLM)

Deterministic replay of a recorded recipe with explicit JSON args.

```bash
"$SKILL_DIR/venv/bin/python" -m agentify.cli call \
  --site hackernews --tool get_top_stories --args '{"n": 5}'
```

## Phase 2b — natural-language run (LLM picks the tool, never sees the page)

```bash
"$SKILL_DIR/venv/bin/python" -m agentify.cli run-mapped \
  --site hackernews \
  --task "Give me the top 3 stories right now"
```

## Sessions — on by default

`call` and `run-mapped` **persist state by default**, in a session named after the
site (`sessions/<slug>.json`), so cookies, **localStorage and IndexedDB** carry
across separate runs — that's what makes a "continuous agent" (do one action, then
another, in the same logged-in/stateful context). Flags:

- (nothing) — use/maintain the per-site session automatically.
- `--no-session` — fresh browser; load and save nothing.
- `--session <name>` — use a named session (multi-account, or a shared one).

On start it loads the session; for sites with a login it checks you're still
logged in with the mapped probe and — only if the session is missing or expired —
**re-runs the login (prompting once)** and re-saves. State is persisted again on
exit. Credentials are only ever prompted, never stored.

```bash
# Reuses sessions/shop.json automatically (no flag needed):
"$SKILL_DIR/venv/bin/python" -m agentify.cli call \
  --site shop --tool view_cart --args '{}'
```

## Phase 2c — manage a session directly (`login`)

```bash
# Auto: replay the recorded login (prompts once), then cache the session.
"$SKILL_DIR/venv/bin/python" -m agentify.cli login --site shop --session shop

# Manual bootstrap for MFA/CAPTCHA sites: opens a VISIBLE browser, you sign in
# by hand, and whatever session the site sets is captured.
"$SKILL_DIR/venv/bin/python" -m agentify.cli login --site shop --manual
```

## Where things live

| Path | Purpose |
|------|---------|
| `$SKILL_DIR/venv/` | Python venv with playwright, typer, openai, rich, python-dotenv |
| `$SKILL_DIR/source/` | Editable install of the `agentify` package |
| `$SKILL_DIR/source/recipes/` | Generated `<slug>.tools.json` registries |
| `$SKILL_DIR/source/sessions/` | Saved login sessions (`storage_state`), gitignored — never contains credentials |
| `$SKILL_DIR/source/.env` | `OPENAI_API_KEY`, `AGENTIFY_MODEL` |
| `~/Library/Caches/ms-playwright/chromium-*` | Bundled Chromium browser (managed by Playwright) |

## Recipe shape (for reference)

Each tool is `{name, description, parameters: JSON-Schema, steps: [...]}`. Step ops: `goto`, `click`, `type`, `select`, `press_enter`, `press`, `scroll`, `wait`, `wait_for`, `extract`, `js_extract`, `verify`, `if_verify`. Selectors try role+name → CSS → text in order; a target of `{"role": "option"}` resolves to the first match, which is how autocomplete suggestions are selected parameter-independently. `press` sends a key (e.g. `{"op": "press", "key": "Enter"}`) to a target, or to whatever is focused if no target is given. Any step may carry `"optional": true` to be skipped (rather than abort the recipe) if it fails; transient failures are retried automatically. `if_verify` branches on a probe — `{"op": "if_verify", "check": {kind, value}, "then": [...steps], "else": [...steps]}` — running one sub-step list; branches may nest. A site that has a login also carries a top-level `auth` block — `{login_tool, check, storage_state}` — naming the login recipe, its success probe, and the gitignored session path.

## Updating the skill

The source under `source/` is an editable install — edit files there and changes apply on the next invocation. If `pyproject.toml` gains a new dependency, re-run:

```bash
"$SKILL_DIR/venv/bin/python" -m pip install -e "$SKILL_DIR/source"
```

If Playwright is upgraded, reinstall the browser:

```bash
"$SKILL_DIR/venv/bin/python" -m playwright install chromium
```

## Full reference

See `$SKILL_DIR/README.md` for the design rationale, sessions/login, remaining limitations (no iteration op, no interactive crawling), and extension paths.
