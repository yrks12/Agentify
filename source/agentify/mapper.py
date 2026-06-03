"""Phase 1: turn a website into a JSON SDK.

  Crawler  -> SiteSurvey
  Proposer -> [ToolProposal]   (LLM call #1)
  Approver -> [ToolProposal]   (CLI prompt)
  Recorder -> Recipe per tool  (action recipes: live agent + recorder;
                                extract recipes: LLM call #2 produces JS)

The output is a SiteRegistry written to recipes/<slug>.tools.json.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel

from .agent import Agent
from .browser import Browser
from .credentials import is_secret_field, prompt_credentials
from .llm import LLM
from .recipe import Engine, Recipe, RecipeFailure
from .recorder import RecordingBrowser
from .registry import AuthConfig, SiteRegistry, session_path


_console = Console()


# ---------------------------------------------------------------- survey

@dataclass
class PageSurvey:
    url: str
    title: str
    ax_tree_text: str
    page_text: str
    nav_links: list[str] = field(default_factory=list)


@dataclass
class SiteSurvey:
    base_url: str
    pages: list[PageSurvey] = field(default_factory=list)

    def as_text(self) -> str:
        chunks = [f"Site root: {self.base_url}", ""]
        for p in self.pages:
            chunks.append(f"--- PAGE: {p.url} ---")
            if p.title:
                chunks.append(f"Title: {p.title}")
            chunks.append(p.ax_tree_text)
            if p.page_text:
                chunks.append("Page text:")
                chunks.append(p.page_text[:1500])
            chunks.append("")
        return "\n".join(chunks)


def _same_origin(base: str, other: str) -> bool:
    try:
        b, o = urlparse(base), urlparse(other)
        return b.netloc == o.netloc
    except Exception:
        return False


def survey_site(browser: Browser, base_url: str, max_pages: int = 4) -> SiteSurvey:
    """Visit the landing page plus a few same-origin nav links."""
    site = SiteSurvey(base_url=base_url)
    visited: set[str] = set()
    queue: list[str] = [base_url]

    while queue and len(site.pages) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            browser.goto(url)
            obs = browser.observe()
        except Exception as e:
            _console.print(f"[yellow]skipped {url}: {e}[/]")
            continue

        # Find new same-origin nav links worth following.
        candidate_links: list[str] = []
        for el in obs.elements:
            if el.role != "link":
                continue
            href = ""
            try:
                href = browser.page.locator(
                    f'[data-w2a-id="{el.w2a_id}"]'
                ).get_attribute("href") or ""
            except Exception:
                href = ""
            if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
                continue
            if href.startswith("/"):
                # join with base origin
                pr = urlparse(base_url)
                href = f"{pr.scheme}://{pr.netloc}{href}"
            if not _same_origin(base_url, href):
                continue
            candidate_links.append(href)

        site.pages.append(
            PageSurvey(
                url=obs.url,
                title=obs.title,
                ax_tree_text=obs.text,
                page_text=obs.page_text,
                nav_links=candidate_links[:8],
            )
        )

        # Prioritize links whose name suggests an action page
        priority_keywords = ("contact", "form", "search", "book", "demo", "signup", "login", "post")
        sorted_links = sorted(
            candidate_links,
            key=lambda h: not any(k in h.lower() for k in priority_keywords),
        )
        for h in sorted_links:
            if h not in visited and h not in queue:
                queue.append(h)

    return site


# ---------------------------------------------------------------- propose

@dataclass
class ToolProposal:
    name: str
    description: str
    parameters: dict
    tool_type: str  # "action" or "extract"
    start_url: str
    # param name -> realistic example value. Used at RECORD time only, so
    # autocomplete/typeahead fields surface real suggestions while we record.
    examples: dict = field(default_factory=dict)


_PROPOSE_SYSTEM = """\
You are designing a JSON tool SDK for a website. Given a survey of the site
(pages, interactive elements, page text), propose 1 to 4 tool functions
that an AI agent would want to call.

Output JSON of the form:
{
  "tools": [
    {
      "name": "snake_case_name",
      "description": "Short verb phrase for what the tool does.",
      "tool_type": "action" | "extract",
      "start_url": "URL the tool starts from",
      "parameters": {
        "type": "object",
        "properties": {
          "param1": {"type": "string", "description": "...", "example": "a realistic value"}
        },
        "required": ["param1"]
      }
    }
  ]
}

Rules:
- "action" tools perform some interaction (fill a form, search, book, etc.).
  An action tool may chain SEVERAL steps (type into multiple fields, pick
  autocomplete suggestions, click a button) and may also return data from
  the page it lands on (e.g. search results). Use "action" — not "extract" —
  whenever fields must be filled before the useful data appears.
- "extract" tools just READ data from a page that is already showing it
  (top stories, article facts, ...) with no interaction needed.
- "login" tools sign the user in. Propose AT MOST ONE "login" tool, and only
  if the site has a sign-in form. Its parameters are the credential fields you
  saw (e.g. "username" or "email", and "password"). For a "login" tool you do
  NOT need to supply "example" values — credentials are prompted from the user
  at record time, never invented.
- Reuse names visible in the page (e.g. "submit_contact_form" if the page has a Contact form).
- For "action" tools, the parameters should map 1:1 to the input fields you saw.
- For "extract" tools, include parameters like `n` (limit) or `query` (search term) if relevant.
- Every parameter MUST include an "example": a realistic value that will be
  typed during recording. For autocomplete/typeahead fields (airports, cities,
  products) the example MUST be a real value that produces live suggestions
  (e.g. "TLV", "New York") — never a placeholder token like "xxx".
- Prefer fewer, higher-value tools over many similar ones.
- Keep names short (1-3 words snake_case) and descriptions one sentence.
"""


def propose_tools(llm: LLM, survey: SiteSurvey) -> list[ToolProposal]:
    msg = [
        {"role": "system", "content": _PROPOSE_SYSTEM},
        {"role": "user", "content": survey.as_text()},
    ]
    resp = llm.client.chat.completions.create(
        model=llm.model,
        messages=msg,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"tools": []}

    proposals: list[ToolProposal] = []
    for t in parsed.get("tools", []):
        params = t.get("parameters") or {"type": "object", "properties": {}}
        # Pull the per-param "example" out of the JSON Schema (it's a record-time
        # hint, not part of the runtime contract) and keep it on the proposal.
        examples: dict[str, str] = {}
        for pname, pdef in (params.get("properties") or {}).items():
            if isinstance(pdef, dict) and pdef.get("example") is not None:
                examples[pname] = str(pdef.pop("example"))
        proposals.append(
            ToolProposal(
                name=t.get("name", "tool"),
                description=t.get("description", ""),
                parameters=params,
                tool_type=t.get("tool_type", "action"),
                start_url=t.get("start_url") or survey.base_url,
                examples=examples,
            )
        )
    return _coerce_login_type(proposals)


_SIGNIN_RE = re.compile(r"log[\s_-]?in|sign[\s_-]?in", re.IGNORECASE)
_SIGNUP_RE = re.compile(r"sign[\s_-]?up|register|create.{0,12}account|new account", re.IGNORECASE)


def _coerce_login_type(proposals: list[ToolProposal]) -> list[ToolProposal]:
    """Deterministically tag sign-in tools as `login`, whatever the LLM labeled.

    The proposer is inconsistent about emitting ``tool_type: "login"``, so we
    don't rely on it: a tool that takes a secret (password) field AND reads as a
    sign-in (name/description, or a login-ish start_url) is a login. Account
    *creation* (signup/register) is explicitly excluded — it also has a password
    field and often shares the /login page, but must not be re-run to mint
    accounts.
    """
    for p in proposals:
        if p.tool_type == "login":
            continue
        props = p.parameters.get("properties") or {}
        has_secret = any(is_secret_field(name) for name in props)
        text = f"{p.name} {p.description}"
        # A sign-in word in the name/description is decisive (covers a merged
        # "signup_login" tool too — the recording task drives the login form).
        # A login-ish URL only counts when the tool isn't a pure signup, since
        # signup commonly shares the /login page.
        signin_in_text = bool(_SIGNIN_RE.search(text))
        signup_in_text = bool(_SIGNUP_RE.search(text))
        signin_in_url = bool(_SIGNIN_RE.search(p.start_url or ""))
        can_sign_in = signin_in_text or (signin_in_url and not signup_in_text)
        if has_secret and can_sign_in:
            p.tool_type = "login"
    return proposals


# ---------------------------------------------------------------- approve

def approve_proposals(
    proposals: list[ToolProposal], interactive: bool = True
) -> list[ToolProposal]:
    """Show proposals, let the user accept all or pick a subset."""
    if not proposals:
        _console.print("[red]No tools proposed.[/]")
        return []

    table_lines = []
    for i, p in enumerate(proposals, 1):
        params = ", ".join(p.parameters.get("properties", {}).keys())
        table_lines.append(
            f"[bold]{i}.[/bold] [cyan]{p.name}[/]  [dim]({p.tool_type})[/]\n"
            f"   {p.description}\n"
            f"   params: {params or '(none)'}\n"
            f"   start: {p.start_url}"
        )
    _console.print(
        Panel(
            "\n\n".join(table_lines),
            title="Proposed tools",
            border_style="cyan",
            title_align="left",
        )
    )

    if not interactive:
        return proposals

    answer = _console.input(
        "[bold]Keep all? [Y/n, or comma-separated indices to keep][/] "
    ).strip().lower()
    if answer in ("", "y", "yes"):
        return proposals
    if answer in ("n", "no"):
        return []
    keep: list[ToolProposal] = []
    for chunk in answer.split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            idx = int(chunk) - 1
            if 0 <= idx < len(proposals):
                keep.append(proposals[idx])
    return keep


# ---------------------------------------------------------------- record

def _placeholders_for(proposal: ToolProposal) -> dict[str, str]:
    """Per-parameter value typed during recording.

    Prefer the LLM-supplied realistic example so autocomplete fields surface
    live suggestions; fall back to sentinels for plain fields with no example.
    """
    out: dict[str, str] = {}
    for pname, pdef in (proposal.parameters.get("properties") or {}).items():
        ptype = (pdef or {}).get("type", "string")
        example = proposal.examples.get(pname)
        if example:
            out[pname] = example
        elif ptype == "integer" or ptype == "number":
            out[pname] = "424242"  # unlikely-real-number sentinel
        elif (pdef or {}).get("enum"):
            # Use the first enum value as placeholder (must be valid for select).
            out[pname] = (pdef or {})["enum"][0]
        else:
            out[pname] = f"__W2A_{pname.upper()}__"
    return out


def _synthetic_task(
    proposal: ToolProposal, placeholders: dict[str, str], hint: str = ""
) -> str:
    bindings = "\n".join(f"  - {k}: {v!r}" for k, v in placeholders.items())
    task = (
        f"You are recording a recipe for the tool `{proposal.name}`: "
        f"{proposal.description}\n"
        f"Use these EXACT placeholder values when filling fields:\n{bindings}\n"
        f"Perform the action end-to-end (navigate, fill all relevant fields, "
        f"submit). When you type into a field that pops up a dropdown of "
        f"autocomplete suggestions (airports, cities, products), click the "
        f"matching suggestion to commit it before moving to the next field. "
        f"For dropdowns (select), radio buttons, and option lists, choose the "
        f"option whose visible label is EXACTLY the placeholder value above — "
        f"do not substitute a different option — so the choice can be "
        f"parameterised. "
        f"When the action is clearly complete and the result page is showing, "
        f"call done(success=true). "
        f"Do not call extract during this recording — it isn't needed."
    )
    if hint:
        # Replay of the previous recording failed; steer the retry.
        task += (
            f"\n\nIMPORTANT: a previous attempt recorded steps that FAILED to "
            f"replay deterministically — {hint}. Make sure to complete every "
            f"required field and commit each dropdown selection so the flow "
            f"reaches the result page."
        )
    return task


_AUTOCOMPLETE_FIELD_ROLES = {"combobox", "searchbox", "textbox"}


def _normalize_autocomplete(steps: list[dict]) -> list[dict]:
    """Make recorded typeahead interactions parameter-independent.

    During recording the agent types a realistic example (e.g. "TLV") into a
    combobox and then clicks the suggestion the site offered — whose accessible
    name is the site's canonical label ("Tel Aviv, Israel TLV"), NOT the typed
    text. Replaying that literal click with a different argument ("JFK") would
    fail. So whenever a parameterised `type` into a typeahead field is followed
    by a click on a listbox `option`, we drop the captured option name and
    replace the pair with: type {{param}} -> wait for an option -> click the
    FIRST option. `resolve()` returns `.first`, so this picks the top suggestion
    for whatever value is passed at call time.
    """
    out: list[dict] = []
    i, n = 0, len(steps)
    while i < n:
        step = steps[i]
        out.append(step)
        is_param_type = (
            step.get("op") == "type"
            and (step.get("target") or {}).get("role") in _AUTOCOMPLETE_FIELD_ROLES
            and "{{" in str(step.get("text", ""))
        )
        if is_param_type:
            # Skip any settle waits the agent inserted, then look for the
            # suggestion click it recorded.
            j = i + 1
            while j < n and steps[j].get("op") == "wait":
                j += 1
            if (
                j < n
                and steps[j].get("op") == "click"
                and (steps[j].get("target") or {}).get("role") == "option"
            ):
                out.append({"op": "verify", "kind": "element_exists",
                            "target": {"role": "option"}})
                out.append({"op": "click", "target": {"role": "option"}})
                i = j + 1
                continue
        i += 1
    return out


def _generate_extract_expr(
    proposal: ToolProposal,
    llm: LLM,
    obs,
    placeholders: Optional[dict[str, str]] = None,
) -> str:
    """Ask the LLM for a JS extraction expression for the given page.

    `placeholders` is the per-parameter example value that was typed during
    recording. We surface it to the LLM as `args.<name> (example was 'X')` so
    the model has zero excuse to inline the literal — it knows the example was
    the recording-time value and the caller will pass something else.
    """
    placeholders = placeholders or {}
    if placeholders:
        bindings = "\n".join(
            f"  - args.{name}  (example used during recording: {val!r})"
            for name, val in placeholders.items()
        )
    else:
        bindings = "  (the tool takes no parameters)"
    user_msg = (
        f"Tool: {proposal.name}\n"
        f"Description: {proposal.description}\n\n"
        f"Parameter access pattern (use these EXACTLY in your expression):\n"
        f"{bindings}\n\n"
        f"Parameters JSON Schema (for type info): {json.dumps(proposal.parameters)}\n\n"
        f"Page URL: {obs.url}\n"
        f"Page title: {getattr(obs, 'title', '') or ''}\n\n"
        f"PAGE STRUCTURE (accessibility tree):\n{obs.text}\n"
    )
    resp = llm.client.chat.completions.create(
        model=llm.model,
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        expr = json.loads(raw).get("js_expr", "")
    except json.JSONDecodeError:
        expr = ""
    return expr.strip() or "() => ({ error: 'no expression generated' })"


def record_action_recipe(
    proposal: ToolProposal, llm: LLM, headless: bool = True, hint: str = ""
) -> Recipe:
    placeholders = _placeholders_for(proposal)
    rec_browser = RecordingBrowser(placeholders=placeholders, headless=headless)

    extract_expr = ""
    with rec_browser:
        agent = Agent(browser=rec_browser, llm=llm, max_steps=20)
        agent.run(task=_synthetic_task(proposal, placeholders, hint), start_url=proposal.start_url)
        # After the action lands on its result page, capture an extraction
        # expression so multi-step tools (search, booking lookup) return data.
        try:
            obs = rec_browser.observe()
            extract_expr = _generate_extract_expr(proposal, llm, obs, placeholders)
        except Exception as e:
            _console.print(f"    [yellow]extract step skipped: {e}[/]")

    steps = _normalize_autocomplete(list(rec_browser.steps))
    # Final settle before reading the page.
    steps.append({"op": "wait", "ms": 1200})
    returns: dict = {}
    if extract_expr:
        steps.append({"op": "js_extract", "expr": extract_expr, "key": "result"})
        returns = {"result": "object|array"}
    return Recipe(
        name=proposal.name,
        description=proposal.description,
        parameters=proposal.parameters,
        steps=steps,
        returns=returns,
    )


_EXTRACT_SYSTEM = """\
You produce a Playwright-compatible JavaScript ARROW FUNCTION that, when
called in the page via `page.evaluate(fn, args)`, returns the data described
by the tool.

Output JSON of the form:
{ "js_expr": "(args) => { ... return data; }" }

HARD RULES (the recipe is rejected if violated):
- `js_expr` MUST be a single arrow function whose only parameter is named
  `args`. Not an IIFE. Not a multi-statement block. One arrow function.
- Read every tool parameter via `args.<name>` — for example `args.query`,
  `args.n`. NEVER inline a literal example value. If the prompt tells you
  the example for `query` was "Foo Bar", you write `args.query`, NOT
  `'Foo Bar'`. The literal value is what was typed during recording; the
  caller will pass a DIFFERENT value at replay time.
- Anchor selectors under <main>, <article>, the search-results container,
  or a result-list landmark visible on the page. AVOID extracting from
  <nav>, <aside>, <header>, <footer>, breadcrumb trails, "main page /
  contents / about" sidebar menus, cookie banners, or footer link lists.
- If the tool has an `n` parameter, cap results to `args.n`. Otherwise
  default to a reasonable cap (5 or 10).
- Use plain `document.querySelector(All)` + `.map()`. Return a plain
  JSON-serializable value (array of objects, or a single object).
"""


def record_extract_recipe(
    proposal: ToolProposal, llm: LLM, browser: Browser
) -> Recipe:
    # Visit the starting URL once so the LLM sees the actual structure.
    browser.goto(proposal.start_url)
    obs = browser.observe()
    expr = _generate_extract_expr(proposal, llm, obs, _placeholders_for(proposal))

    steps = [
        {"op": "goto", "url": proposal.start_url},
        {"op": "wait", "ms": 800},
        {"op": "js_extract", "expr": expr, "key": "result"},
    ]
    return Recipe(
        name=proposal.name,
        description=proposal.description,
        parameters=proposal.parameters,
        steps=steps,
        returns={"result": "object|array"},
    )


# ------------------------------------------------------------ self-verify

def _verify_replay(
    recipe: Recipe, args: dict, headless: bool
) -> tuple[bool, str]:
    """Deterministically replay a freshly-recorded recipe to prove it works.

    Runs the Engine (no LLM) in a clean browser with the example args. A
    recipe is only trustworthy if it replays end-to-end — this is what lets
    the mapper handle arbitrary multi-step flows instead of hoping the
    recording was clean.
    """
    try:
        with Browser(headless=headless) as b:
            result = Engine(b).execute(recipe, args)
        keys = ", ".join(result.keys()) if result else "(no extract)"
        return True, f"replayed {len(recipe.steps)} steps -> {keys}"
    except RecipeFailure as e:
        return False, f"step {e.step_index}: {e.reason}"
    except Exception as e:  # browser/launch errors, etc.
        return False, f"{type(e).__name__}: {e}"


def _record_verified_action(
    proposal: ToolProposal, llm: LLM, headless: bool, max_attempts: int = 2
) -> Recipe:
    """Record an action recipe, then replay-verify it; re-record on failure.

    On a failed replay the failure reason is fed back to the recording agent
    as a hint so the retry can fix the missing/raced step.
    """
    args = _placeholders_for(proposal)
    hint = ""
    recipe: Optional[Recipe] = None
    for attempt in range(1, max_attempts + 1):
        recipe = record_action_recipe(proposal, llm, headless=headless, hint=hint)
        ok, msg = _verify_replay(recipe, args, headless)
        if ok:
            _console.print(f"    [green]replay check passed: {msg}[/]")
            return recipe
        _console.print(
            f"    [yellow]replay check failed (attempt {attempt}/{max_attempts}): {msg}[/]"
        )
        hint = msg
    _console.print(
        "    [red]recipe still fails to replay — saved anyway; inspect/repair the steps.[/]"
    )
    return recipe  # last attempt; let the user see/fix it


# ---------------------------------------------------------------- login

_LOGOUT_RE = re.compile(r"log\s?out|sign\s?out", re.IGNORECASE)


def _login_task(
    proposal: ToolProposal, credentials: dict[str, str], hint: str = ""
) -> str:
    bindings = "\n".join(f"  - {k}: {v!r}" for k, v in credentials.items())
    task = (
        f"You are recording a LOGIN recipe for the tool `{proposal.name}`: "
        f"{proposal.description}\n"
        f"Use these EXACT values when filling the sign-in fields:\n{bindings}\n"
        f"Navigate to the sign-in form, fill every credential field, and submit. "
        f"When the page shows you are logged in (an account menu, dashboard, or a "
        f"Log out control is visible), call done(success=true). "
        f"Do not call extract during this recording."
    )
    if hint:
        task += (
            f"\n\nIMPORTANT: a previous attempt FAILED to replay — {hint}. "
            f"Make sure every credential field is filled and the form is submitted "
            f"so the page reaches the logged-in state."
        )
    return task


def _derive_login_check(obs) -> dict:
    """A cheap probe proving we're logged in: `{kind, ...}` or `{}`.

    Prefers a Log out / Sign out control; falls back to the post-login URL path.
    Doubles as the recipe's final `verify` step and the stage-2 session-validity
    probe. Only the matched "log out"/"sign out" token is stored as the target
    name — preserving the site's real spelling (e.g. "Logout") so the probe
    actually resolves, while dropping any account identifier that might trail it
    (e.g. "Logout (alice@x)").
    """
    for el in obs.elements:
        if el.role in ("link", "button"):
            match = _LOGOUT_RE.search(el.name or "")
            if match:
                return {
                    "kind": "element_exists",
                    "target": {"role": el.role, "name": match.group(0)},
                }
    path = urlparse(obs.url).path
    if path and path not in ("", "/"):
        return {"kind": "url_contains", "value": path}
    return {}


def record_login_recipe(
    proposal: ToolProposal,
    llm: LLM,
    *,
    headless: bool,
    credentials: dict[str, str],
    hint: str = "",
) -> tuple[Recipe, dict]:
    """Record a parameterised login recipe and derive its success probe.

    The prompted credentials are used as the recorder placeholders, so the
    existing `_bind` machinery swaps each typed value back to `{{param}}` — the
    saved recipe holds `{{username}}`/`{{password}}`, never the secret itself.
    Returns `(recipe, check)` where `check` is the `{kind, ...}` probe.
    """
    rec_browser = RecordingBrowser(placeholders=credentials, headless=headless)
    check: dict = {}
    with rec_browser:
        agent = Agent(browser=rec_browser, llm=llm, max_steps=20)
        agent.run(task=_login_task(proposal, credentials, hint), start_url=proposal.start_url)
        try:
            check = _derive_login_check(rec_browser.observe())
        except Exception as e:
            _console.print(f"    [yellow]login check derivation skipped: {e}[/]")

    steps = _normalize_autocomplete(list(rec_browser.steps))
    steps.append({"op": "wait", "ms": 1200})
    if check:
        # Embed the probe so a broken login fails loudly at replay/verify time.
        steps.append({"op": "verify", **check})
    recipe = Recipe(
        name=proposal.name,
        description=proposal.description,
        parameters=proposal.parameters,
        steps=steps,
        returns={},
    )
    return recipe, check


def _verify_login_and_capture(
    recipe: Recipe, credentials: dict[str, str], headless: bool, session_file: Path
) -> tuple[bool, str]:
    """Replay the login with the real credentials; persist storage_state on success."""
    try:
        with Browser(headless=headless) as b:
            Engine(b).execute(recipe, credentials)
            b.save_storage_state(session_file)
        return True, f"login replayed -> session saved to {session_file.name}"
    except RecipeFailure as e:
        return False, f"step {e.step_index}: {e.reason}"
    except Exception as e:  # browser/launch errors, etc.
        return False, f"{type(e).__name__}: {e}"


def _record_verified_login(
    proposal: ToolProposal,
    llm: LLM,
    *,
    headless: bool,
    credentials: dict[str, str],
    session_file: Path,
    max_attempts: int = 2,
) -> tuple[Recipe, dict]:
    """Record a login recipe, replay-verify it, and capture the session.

    Mirrors `_record_verified_action`: a failed replay feeds its reason back to
    the recording agent as a hint for one retry.
    """
    hint = ""
    recipe: Optional[Recipe] = None
    check: dict = {}
    for attempt in range(1, max_attempts + 1):
        recipe, check = record_login_recipe(
            proposal, llm, headless=headless, credentials=credentials, hint=hint
        )
        ok, msg = _verify_login_and_capture(recipe, credentials, headless, session_file)
        if ok:
            _console.print(f"    [green]login replay check passed: {msg}[/]")
            return recipe, check
        _console.print(
            f"    [yellow]login replay failed (attempt {attempt}/{max_attempts}): {msg}[/]"
        )
        hint = msg
    _console.print(
        "    [red]login recipe still fails to replay — saved anyway; inspect the steps.[/]"
    )
    return recipe, check


# ---------------------------------------------------------------- top-level

def map_site(
    url: str,
    slug: str,
    headless: bool = True,
    interactive: bool = True,
    llm: Optional[LLM] = None,
) -> SiteRegistry:
    llm = llm or LLM()

    _console.rule(f"[bold cyan]Mapping {slug} — {url}")

    # 1. Survey
    _console.print("[bold]Phase 1/4:[/] crawling pages...")
    with Browser(headless=headless) as crawler:
        survey = survey_site(crawler, url)
    _console.print(f"  surveyed {len(survey.pages)} pages")

    # 2. Propose
    _console.print("[bold]Phase 2/4:[/] proposing tools via LLM...")
    proposals = propose_tools(llm, survey)
    _console.print(f"  got {len(proposals)} proposals")

    # 3. Approve
    _console.print("[bold]Phase 3/4:[/] approval...")
    approved = approve_proposals(proposals, interactive=interactive)
    if not approved:
        _console.print("[red]Nothing approved; aborting.[/]")
        return SiteRegistry(site=slug, base_url=url, tools=[])

    # 4. Record
    _console.print(f"[bold]Phase 4/4:[/] recording {len(approved)} recipe(s)...")
    recipes: list[Recipe] = []
    auth: Optional[AuthConfig] = None
    for p in approved:
        _console.print(f"  • recording {p.name} ({p.tool_type})...")
        try:
            if p.tool_type == "login":
                r, auth = _record_login_tool(p, llm, slug, headless, interactive)
                if r is None:
                    continue
            elif p.tool_type == "extract":
                # Need an open browser for the LLM to see the page once.
                with Browser(headless=headless) as b:
                    r = record_extract_recipe(p, llm, b)
                ok, msg = _verify_replay(r, _placeholders_for(p), headless)
                _console.print(
                    f"    [{'green' if ok else 'yellow'}]replay check: {msg}[/]"
                )
            else:
                # Action recipes are recorded AND replay-verified (retry on fail).
                r = _record_verified_action(p, llm, headless=headless)
            recipes.append(r)
            _console.print(
                f"    -> {len(r.steps)} step(s) recorded"
            )
        except Exception as e:
            _console.print(f"    [red]failed: {e}[/]")

    registry = SiteRegistry(site=slug, base_url=url, tools=recipes, auth=auth)
    return registry


def _record_login_tool(
    proposal: ToolProposal,
    llm: LLM,
    slug: str,
    headless: bool,
    interactive: bool,
) -> tuple[Optional[Recipe], Optional[AuthConfig]]:
    """Prompt for credentials, record+verify the login, and build its AuthConfig.

    Returns `(None, None)` when login can't be recorded (no interactive prompt
    available), so the caller skips it without aborting the rest of the map.
    """
    if not interactive:
        _console.print(
            "    [yellow]skipped login tool: credentials need an interactive "
            "prompt (omit --auto-approve).[/]"
        )
        return None, None

    cred_fields = list((proposal.parameters.get("properties") or {}).keys())
    if not cred_fields:
        _console.print("    [yellow]skipped login tool: no credential fields found.[/]")
        return None, None

    credentials = prompt_credentials(cred_fields, tool_name=proposal.name)
    session_file = session_path(slug)
    recipe, check = _record_verified_login(
        proposal, llm, headless=headless, credentials=credentials, session_file=session_file
    )
    auth = AuthConfig(
        login_tool=recipe.name,
        check=check,
        storage_state=f"sessions/{slug}.json",
    )
    return recipe, auth
