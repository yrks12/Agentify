---
name: Agentify
description: Turn any website into an LLM-callable API. Phase 1 ("map") visits a site, proposes tool functions, and records deterministic Playwright recipes into recipes/<slug>.tools.json. Phase 2 ("call" or "run-mapped") replays a recipe directly or lets the LLM pick a tool from natural language — the page itself is never sent to the model. Use when the user wants to scrape, automate, or expose a website as tools.
argument-hint: [map|call|run-mapped] [...args]
allowed-tools: Bash, Read
---

# Agentify

Self-contained skill that bundles the `agentify` CLI, its Python venv, and the Playwright Chromium browser. Nothing installs at invocation time — just run the bundled interpreter.

## Run with the bundled venv (never `pip install` again)

The skill ships everything pre-installed at `${CLAUDE_SKILL_DIR}/venv/` and the source at `${CLAUDE_SKILL_DIR}/source/`. Always invoke through the venv's Python so dependencies resolve correctly:

```bash
~/.claude/skills/Agentify/venv/bin/python -m agentify.cli --help
```

The CLI's `_load_env` walks up from the package and finds `source/.env` automatically, so `OPENAI_API_KEY` is already wired up.

## Phase 1 — map a site (one-shot per site)

Crawls the landing page, asks the LLM to propose tool functions, prompts you to accept/reject each one, then drives the site with sentinel placeholders to record a deterministic recipe per tool. Output is written to `~/.claude/skills/Agentify/source/recipes/<name>.tools.json`.

```bash
~/.claude/skills/Agentify/venv/bin/python -m agentify.cli map \
  --url https://news.ycombinator.com --name hackernews
```

Flags:
- `--auto-approve` — skip the interactive accept/reject step
- `--no-headless` — show the browser while recording
- `--model gpt-4o-mini` — override `AGENTIFY_MODEL` from .env

## Phase 2a — call a single tool directly (no LLM)

Deterministic replay of a recorded recipe with explicit JSON args.

```bash
~/.claude/skills/Agentify/venv/bin/python -m agentify.cli call \
  --site hackernews --tool get_top_stories --args '{"n": 5}'
```

## Phase 2b — natural-language run (LLM picks the tool, never sees the page)

```bash
~/.claude/skills/Agentify/venv/bin/python -m agentify.cli run-mapped \
  --site hackernews \
  --task "Give me the top 3 stories right now"
```

## Where things live

| Path | Purpose |
|------|---------|
| `~/.claude/skills/Agentify/venv/` | Python venv with playwright, typer, openai, rich, python-dotenv |
| `~/.claude/skills/Agentify/source/` | Editable install of the `agentify` package |
| `~/.claude/skills/Agentify/source/recipes/` | Generated `<slug>.tools.json` registries |
| `~/.claude/skills/Agentify/source/.env` | `OPENAI_API_KEY`, `AGENTIFY_MODEL` |
| `~/Library/Caches/ms-playwright/chromium-*` | Bundled Chromium browser (managed by Playwright) |

## Recipe shape (for reference)

Each tool is `{name, description, parameters: JSON-Schema, steps: [...]}`. Step ops: `goto`, `click`, `type`, `select`, `press_enter`, `scroll`, `wait`, `extract`, `js_extract`, `verify`. Selectors try role+name → CSS → text in order.

## Updating the skill

The source under `source/` is an editable install — edit files there and changes apply on the next invocation. If `pyproject.toml` gains a new dependency, re-run:

```bash
~/.claude/skills/Agentify/venv/bin/python -m pip install -e ~/.claude/skills/Agentify/source
```

If Playwright is upgraded, reinstall the browser:

```bash
~/.claude/skills/Agentify/venv/bin/python -m playwright install chromium
```

## Full reference

See `~/.claude/skills/Agentify/source/README.md` for the design rationale, known limitations (no session persistence between `call` invocations, no iteration op, shallow crawler), and extension paths.
