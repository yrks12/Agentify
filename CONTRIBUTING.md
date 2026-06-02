# Contributing to Agentify

Thanks for your interest in improving Agentify! This guide covers the dev setup
(the layout is a little unusual because Agentify ships as an agent skill), how to
run the tests, and what we look for in a pull request.

## Repository layout

```
Agentify/
├── SKILL.md            # The agent-skill manifest (Claude Code + Codex)
├── README.md           # User-facing docs
├── agents/openai.yaml  # Optional Codex UI metadata
├── examples/           # Hand-written example recipes (safe to read/run)
└── source/             # The actual Python package + tests live here
    ├── agentify/       # Package source
    ├── tests/          # pytest suite (no network/Playwright/OpenAI needed)
    ├── recipes/        # Generated recipes (git-ignored)
    └── pyproject.toml
```

Note the package and `pyproject.toml` live under `source/`, **not** the repo
root. All `pip`/`pytest` commands below reflect that.

## Dev setup

Requires Python 3.10+.

```bash
git clone https://github.com/rivka2003/Agentify
cd Agentify
python3 -m venv venv && source venv/bin/activate
pip install -e "source[dev]"      # editable install + dev deps
playwright install chromium        # only needed to run `map`/`call` live
cp source/.env.example source/.env # set OPENAI_API_KEY for live runs
```

## Running the tests

The suite is fully offline — no Playwright browser, no OpenAI key required.

```bash
pytest source            # run all tests
pytest source -q         # quiet
ruff check source        # lint (CI enforces this)
```

CI runs the same on every PR across Python 3.10–3.13. Please make sure
`pytest source` and `ruff check source` are green before opening a PR.

## How the pieces fit (for code changes)

- **Engine ops** live in `source/agentify/recipe.py` (`Engine.execute`). Adding a
  new step op means handling it there and, ideally, covering it in
  `tests/test_recipe_engine.py`.
- **Selector resolution** is in `selectors.py` (`Target` + multi-strategy
  resolver). Recipes survive small DOM changes via role+name → CSS → text.
- **Mapping** (Phase 1) is in `mapper.py` — the Crawler/Proposer/Approver/Recorder
  pipeline. The LLM prompts and tool schema are in `llm.py`.
- See the README's "What this does NOT handle" section for the known seams and
  the concrete fix each one needs — good first issues often live there.

## Pull request guidelines

1. Open an issue first for anything non-trivial so we can agree on direction.
2. Keep PRs focused; one logical change per PR.
3. Add or update tests for behavior changes.
4. Run `pytest source` and `ruff check source` locally.
5. Update `README.md` / `SKILL.md` if you change user-facing behavior.
6. By contributing, you agree your contributions are licensed under the
   project's [Apache-2.0 license](LICENSE).

## Recipes in PRs

Do **not** commit generated recipes from `source/recipes/` — they are
git-ignored and can bake in transient or private page content. If you want to
add an illustrative example, hand-write a minimal one under `examples/` (see
`examples/hackernews.tools.json`).

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating you agree to uphold it.
