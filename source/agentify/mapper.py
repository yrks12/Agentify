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
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel

from .agent import Agent
from .ax_tree import AXElement
from .browser import Browser
from .llm import LLM, DEFAULT_MODEL
from .recipe import Recipe
from .recorder import RecordingBrowser
from .registry import SiteRegistry


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
        "properties": { "param1": {"type": "string", "description": "..."} },
        "required": ["param1"]
      }
    }
  ]
}

Rules:
- "action" tools perform some interaction (submit a form, click a button, etc.).
- "extract" tools just READ data from a page (top stories, article facts, ...).
- Reuse names visible in the page (e.g. "submit_contact_form" if the page has a Contact form).
- For "action" tools, the parameters should map 1:1 to the input fields you saw.
- For "extract" tools, include parameters like `n` (limit) or `query` (search term) if relevant.
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
        proposals.append(
            ToolProposal(
                name=t.get("name", "tool"),
                description=t.get("description", ""),
                parameters=t.get("parameters") or {"type": "object", "properties": {}},
                tool_type=t.get("tool_type", "action"),
                start_url=t.get("start_url") or survey.base_url,
            )
        )
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
    """Sentinel strings for each parameter."""
    out: dict[str, str] = {}
    for pname, pdef in (proposal.parameters.get("properties") or {}).items():
        ptype = (pdef or {}).get("type", "string")
        # Build a per-param sentinel that doesn't collide with real text.
        if ptype == "integer" or ptype == "number":
            out[pname] = "424242"  # unlikely-real-number sentinel
        elif (pdef or {}).get("enum"):
            # Use the first enum value as placeholder (must be valid for select).
            out[pname] = (pdef or {})["enum"][0]
        else:
            out[pname] = f"__W2A_{pname.upper()}__"
    return out


def _synthetic_task(proposal: ToolProposal, placeholders: dict[str, str]) -> str:
    bindings = "\n".join(f"  - {k}: {v!r}" for k, v in placeholders.items())
    return (
        f"You are recording a recipe for the tool `{proposal.name}`: "
        f"{proposal.description}\n"
        f"Use these EXACT placeholder values when filling fields:\n{bindings}\n"
        f"Perform the action end-to-end (navigate, fill all relevant fields, "
        f"submit). When the action is clearly complete, call done(success=true). "
        f"Do not call extract during this recording — it isn't needed."
    )


def record_action_recipe(
    proposal: ToolProposal, llm: LLM, headless: bool = True
) -> Recipe:
    placeholders = _placeholders_for(proposal)
    rec_browser = RecordingBrowser(placeholders=placeholders, headless=headless)

    with rec_browser:
        agent = Agent(browser=rec_browser, llm=llm, max_steps=20)
        agent.run(task=_synthetic_task(proposal, placeholders), start_url=proposal.start_url)

    steps = list(rec_browser.steps)
    # Final settle + verification step (best-effort).
    steps.append({"op": "wait", "ms": 1200})
    return Recipe(
        name=proposal.name,
        description=proposal.description,
        parameters=proposal.parameters,
        steps=steps,
        returns={},
    )


_EXTRACT_SYSTEM = """\
You produce a Playwright-compatible JavaScript expression that, when run in
the page via `page.evaluate(...)`, returns the data described by the tool.

Output JSON of the form:
{ "js_expr": "(() => { ... })()" }

Rules:
- Return ONLY the JS expression in `js_expr` (no markdown, no comments outside).
- The expression must be a self-contained arrow / IIFE that returns the value.
- If the tool has parameters, treat them as JS template literals already
  substituted at call time (e.g. `{{n}}` will be replaced with the int).
- Prefer plain `document.querySelectorAll` + `.map()` patterns.
- Cap results to {{n}} if relevant; default sensible (5).
"""


def record_extract_recipe(
    proposal: ToolProposal, llm: LLM, browser: Browser
) -> Recipe:
    # Visit the starting URL once so the LLM sees the actual structure.
    browser.goto(proposal.start_url)
    obs = browser.observe()
    user_msg = (
        f"Tool: {proposal.name}\n"
        f"Description: {proposal.description}\n"
        f"Parameters JSON Schema: {json.dumps(proposal.parameters)}\n\n"
        f"PAGE AT {obs.url}:\n{obs.text}\n"
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
    expr = expr.strip() or "() => ({ error: 'no expression generated' })"

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
    for p in approved:
        _console.print(f"  • recording {p.name} ({p.tool_type})...")
        try:
            if p.tool_type == "extract":
                # Need an open browser for the LLM to see the page once.
                with Browser(headless=headless) as b:
                    r = record_extract_recipe(p, llm, b)
            else:
                r = record_action_recipe(p, llm, headless=headless)
            recipes.append(r)
            _console.print(
                f"    -> {len(r.steps)} step(s) recorded"
            )
        except Exception as e:
            _console.print(f"    [red]failed: {e}[/]")

    registry = SiteRegistry(site=slug, base_url=url, tools=recipes)
    return registry
