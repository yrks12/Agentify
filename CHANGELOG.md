# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Parameter binding for non-text actions.** Native `<select>` values and
  clicks on radios / listbox-options / named links are now parameterised to
  `{{param}}` (matched exactly against the placeholder value), instead of being
  frozen to whatever the mapping agent picked. Replay is unchanged
  (`_substitute` already templates into a step's `target`). Boolean checkboxes,
  date pickers, file uploads, and custom non-native dropdown widgets remain out
  of scope.

### Added
- **Sessions & login.** `map` records a `login` tool (credentials prompted
  interactively and never written to disk; parameterised to `{{...}}` in the
  recipe), derives a success probe, and saves the browser `storage_state` to a
  gitignored `sessions/<slug>.json`; the registry gains an `auth` block.
- `--session <name>` on `call` and `run-mapped`: loads a saved session, verifies
  it with the probe, lazily re-logs-in (prompting once) when missing/expired, and
  persists it on exit.
- **Session persistence on by default**, named after the site, so cookies,
  localStorage and IndexedDB carry across separate `call`/`run-mapped` runs (the
  "continuous agent"). `--no-session` opts out (fresh browser); `--session <name>`
  selects a named/multi-account session.
- **IndexedDB capture** (`storage_state(indexed_db=True)`) so SPA/Firebase-style
  logins that keep their token in IndexedDB survive across runs.
- `agentify login` command: `--auto` (replay the recorded login) or `--manual`
  (visible-browser bootstrap for MFA/CAPTCHA sites).
- Deterministic login detection (password field + sign-in signal; pure signup
  excluded), independent of the LLM's labelling.

## [0.1.0] - 2026-06-02

Initial public release.

### Added
- **Map** phase: a mapper agent visits a site, proposes tool functions, and
  records each as a deterministic Playwright recipe in `recipes/<slug>.tools.json`.
- **Run** phase: a pure-replay engine executes a recorded recipe; an LLM picks
  a tool from the JSON schema without ever seeing the page.
- `agentify` CLI (`map`, `run`, `call`, `list`).
- `SKILL.md` working in both Claude Code and Codex, plus optional native
  `agents/openai.yaml` metadata for Codex.
- Bundled Python venv + Playwright Chromium for one-step install.
- Test suite (hermetic: no network, Playwright, or OpenAI key required).
- Example recipe under `examples/`.

[Unreleased]: https://github.com/rivka2003/Agentify/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rivka2003/Agentify/releases/tag/v0.1.0
