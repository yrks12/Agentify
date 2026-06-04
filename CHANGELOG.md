# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-04

### Added
- **JS-heavy sites: iframe-aware capture & resolve.** `ax_tree.snapshot()` now
  collects interactive elements from the main frame and every child `<iframe>`
  (each element's locator bound to its own frame), and `selectors.resolve()` falls
  through to each frame when the top page has no match — so a control inside an
  iframe is both seen (`map`) and operated (`call`) with the same frame-agnostic
  role+name target. `agentify crawl --elements` prints the observed controls per
  page (including framed ones). Closed shadow DOM remains out of scope.
- **JS-heavy sites, phase 1: settle + consent walls.** `Browser.observe()` now
  waits for the page to render (polls until the interactive-element count is
  non-zero and stable, retrying on an empty snapshot) instead of firing at
  `domcontentloaded` — client-rendered SPAs (Booking, X) no longer come back with
  zero elements. The crawler dismisses common cookie/consent interstitials
  (new `interstitials.py`, reject-first then accept) so `crawl`/`map` reach the
  real app instead of a "before you continue" wall (Google Flights/Maps, LinkedIn).
- **JS-heavy sites: custom ARIA widgets confirmed operable.** Verified end-to-end
  (incl. inside iframes) that `role=combobox`/`listbox`/`option` autocompletes and
  `role=gridcell` date grids — the div-based widgets that JS sites use instead of
  native `<select>` — are driven by the existing `click`/`type`/`verify` +
  first-option ops plus the autocomplete normaliser; no new op was needed. Added an
  example to the test suite.
- **JS-heavy sites — known limits.** What these changes deliberately do **not**
  solve, found while testing Google Flights / Kayak / Booking / LinkedIn / X /
  YouTube / Airbnb / Maps: hard **CAPTCHA / anti-bot** challenges (e.g. Cloudflare,
  Booking's interstitial — the remaining blocker to full flight-search automation),
  **canvas/WebGL** apps with no semantic DOM (Maps, Figma), **closed** shadow DOM
  (unreadable by design), and **auth walls** (a login wall is not a cookie wall —
  use the `--session`/`login` path). Relevance-ranking very dense pages was
  evaluated and dropped — real pages rarely exceed the snapshot cap of *visible*
  controls.
- **Fail-soft recovery.** When a tool fails partway, `RecipeFailure` now carries
  `partial` (data extracted before the failure) and `url` (where the browser
  landed). `call` prints the salvaged partial + URL instead of discarding them;
  `run-mapped` feeds the LLM a `{error, failed_step, op, url, partial}` payload
  and nudges it to switch tools when the same one fails repeatedly. Mid-recipe
  session-expiry detection and rollback/resume remain out of scope (recipes
  aren't transactional; deterministic replay from step 0 is the recovery model).
- **Deeper, configurable crawl.** The map crawler is now a depth-aware, budgeted,
  content-aware BFS: `--max-pages` / `--max-depth` (on `map`, defaults 10 / 2)
  control breadth and hops, and link selection prefers action pages and deeper
  *content* over repeated nav chrome, so the survey reaches article/item/product
  pages the old 4-page skim never saw. Interacting during the crawl (submitting
  searches, opening modals) remains out of scope.
- **`agentify crawl` command.** Previews the crawl with no LLM — lists the pages
  `map` would survey (url · title · depth) so you can tune budget before paying
  for proposals/recording. `--session NAME` loads a saved `storage_state` so the
  crawl can see authenticated pages.
- **Conditional branching (`if_verify`).** A new Engine op,
  `{"op": "if_verify", "check": {kind, value, …}, "then": [...steps], "else": […]}`,
  evaluates a probe once (same kinds as `verify`) and runs the chosen branch.
  Branch sub-steps run through the same per-step path, so they keep transient
  retries + the `optional` flag, and `if_verify` can nest. Enables "if a modal is
  open, dismiss it; else carry on" without pushing every micro-branch up to the
  LLM. Native JS dialogs and map-time auto-proposal of branches remain out of
  scope.
- **Runtime robustness for replay.** The `Engine` now retries **transient**
  step failures (Playwright timeouts, detached elements, navigation/network
  races) with a small bounded backoff — real-site flakiness no longer kills a
  tool that would succeed on a second try. Deterministic failures (a failed
  `verify`, an unknown op, a missing field) still fail fast. Retry knobs are
  `Engine(max_retries=…, backoff_s=…)`.
- **`optional: true` step flag.** A step marked optional (and the presentational
  `scroll`/`wait`/`wait_for` ops) is logged and **skipped** when it fails instead
  of aborting the whole recipe — useful for best-effort steps like dismissing a
  banner.

### Changed
- **Actionable failure messages.** `RecipeFailure` now carries the failing `op`
  and a single de-noised line (op + target + first error line) instead of a raw
  multi-line Playwright stack — improving both the `call` error output and the
  `{"error": …}` payload `run-mapped` feeds back to the LLM.

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

[Unreleased]: https://github.com/rivka2003/Agentify/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rivka2003/Agentify/releases/tag/v0.2.0
[0.1.0]: https://github.com/rivka2003/Agentify/releases/tag/v0.1.0
