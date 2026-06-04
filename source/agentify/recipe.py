"""Recipe = a deterministic action sequence. Engine = runs it. No LLM."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .selectors import Target, resolve

_log = logging.getLogger(__name__)


class RecipeFailure(Exception):
    def __init__(self, step_index: int, reason: str, op: Optional[str] = None):
        super().__init__(f"step {step_index}: {reason}")
        self.step_index = step_index
        self.reason = reason
        self.op = op  # the failing step's op, for actionable error surfaces


@dataclass
class Recipe:
    name: str
    description: str
    parameters: dict  # JSON Schema
    steps: list[dict] = field(default_factory=list)
    returns: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            parameters=d.get("parameters", {"type": "object", "properties": {}}),
            steps=list(d.get("steps", [])),
            returns=d.get("returns", {}),
        )


_PARAM_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _substitute(value: Any, args: dict) -> Any:
    """Replace {{param}} placeholders inside any string field, recursively."""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            key = m.group(1)
            if key not in args or args[key] is None:
                # Optional param omitted: resolve to empty rather than leaving
                # the literal "{{param}}", which would otherwise be typed into
                # fields or passed to select_option as a bogus option value.
                return ""
            return str(args[key])
        return _PARAM_RE.sub(repl, value)
    if isinstance(value, list):
        return [_substitute(v, args) for v in value]
    if isinstance(value, dict):
        # `expr` is JS source for js_extract — args are passed natively via
        # page.evaluate(expr, args), so we must NOT template into it (would be
        # JS injection and would lose type information).
        return {k: (v if k == "expr" else _substitute(v, args)) for k, v in value.items()}
    return value


# --------------------------------------------------------------- conditions

def evaluate_condition(page, check: dict) -> bool:
    """Evaluate a `{kind, ...}` probe against `page` once. No raising, no polling.

    Shared by the Engine's `verify` op and the session-validity check so the two
    can never drift. Supported kinds: `page_text_contains` (+ `case_insensitive`),
    `url_contains`, `element_exists` (with a `target`). Unknown kinds are False.
    """
    kind = check.get("kind", "page_text_contains")
    expected = str(check.get("value", ""))
    if kind == "page_text_contains":
        body_text = page.evaluate("() => document.body.innerText || ''") or ""
        if check.get("case_insensitive"):
            return expected.lower() in body_text.lower()
        return expected in body_text
    if kind == "url_contains":
        return expected in page.url
    if kind == "element_exists":
        try:
            loc = resolve(page, Target.from_dict(check.get("target") or {}), timeout_ms=100)
            return loc.count() > 0
        except Exception:
            return False
    return False


def wait_for_condition(page, check: dict, timeout_s: float = 3.0) -> bool:
    """Poll `evaluate_condition` until it passes or `timeout_s` elapses."""
    start = time.time()
    while True:
        if evaluate_condition(page, check):
            return True
        if (time.time() - start) > timeout_s:
            return False
        time.sleep(0.05)


# --------------------------------------------------------- error handling

# Substrings that mark an error a re-attempt could plausibly fix: flaky DOM
# (element re-rendered mid-action), navigation races, transient network/timeout.
_TRANSIENT_NEEDLES = (
    "detached",
    "not attached",
    "execution context was destroyed",
    "target closed",
    "target page, context or browser has been closed",
    "navigation failed",
    "net::err",
    "timeout",
    "timed out",
    "intercepts pointer events",
)

# Presentational / best-effort ops: a failure here must never abort a recipe (a
# failed scroll or settle shouldn't lose an extraction). `wait_for` already
# swallows its own timeout internally; listing it keeps the intent in one place.
_NONFATAL_OPS = frozenset({"scroll", "wait", "wait_for"})


def _is_transient(exc: Exception) -> bool:
    """True for errors worth retrying; deterministic failures fail fast.

    A failed `verify`, an unknown op, or a missing field is deterministic —
    retrying only wastes time. Playwright timeouts, detached elements, and
    navigation races are the flaky cases retry is meant to absorb.
    """
    if isinstance(exc, RecipeFailure):
        return False
    if type(exc).__name__ in ("TimeoutError", "LookupError"):
        # Playwright TimeoutError, or selectors.resolve giving up on a
        # (possibly slow-to-appear) element.
        return True
    msg = str(exc).lower()
    return any(n in msg for n in _TRANSIENT_NEEDLES)


def _target_summary(step: dict) -> str:
    tgt = step.get("target")
    if not isinstance(tgt, dict):
        return ""
    role, name = tgt.get("role"), tgt.get("name")
    if name:
        return f" on {role or 'element'}={name!r}"
    if role:
        return f" on {role}"
    return ""


def _one_line_reason(op: Optional[str], step: dict, exc: Exception) -> str:
    """A single de-noised line — never a multi-line Playwright stack dump.

    Feeds both the `call` error message and the `{"error": ...}` payload that
    `run-mapped` hands back to the LLM, so neither sees raw tracebacks.
    """
    raw = str(exc).strip()
    first = raw.splitlines()[0].strip() if raw else type(exc).__name__
    return f"{op}{_target_summary(step)} failed: {type(exc).__name__}: {first}"


class Engine:
    """Runs a Recipe against a Browser. No LLM contact."""

    def __init__(self, browser, *, max_retries: int = 2, backoff_s: float = 0.3,
                 on_warn=None):  # browser: agentify.browser.Browser
        self.browser = browser
        # Transient failures (flaky DOM/network) get bounded retries; set both to
        # 0 in tests for fast, deterministic runs.
        self.max_retries = max(0, int(max_retries))
        self.backoff_s = max(0.0, float(backoff_s))
        # Invoked with a human string when an optional / non-fatal step is
        # skipped. Defaults to a module logger so recipe.py carries no UI
        # dependency; the CLI passes a callback that prints to its rich console.
        self._on_warn = on_warn or (lambda msg: _log.warning(msg))

    def execute(self, recipe: Recipe, args: Optional[dict] = None) -> dict:
        args = args or {}
        returned: dict[str, Any] = {}
        steps = _substitute(recipe.steps, args)

        for i, step in enumerate(steps):
            op = step.get("op")
            # A step may opt out of being fatal (`optional: true`), and the
            # presentational ops are implicitly non-fatal.
            skippable = bool(step.get("optional")) or op in _NONFATAL_OPS
            attempt = 0
            while True:
                try:
                    self._run_op(i, step, op, args, returned)
                    break
                except RecipeFailure as rf:
                    # Deterministic (verify / unknown op): never retried.
                    if skippable:
                        self._on_warn(f"step {i} ({op}) skipped — {rf.reason}")
                        break
                    raise
                except Exception as e:
                    if attempt < self.max_retries and _is_transient(e):
                        attempt += 1
                        if self.backoff_s:
                            time.sleep(self.backoff_s)
                        continue
                    reason = _one_line_reason(op, step, e)
                    if skippable:
                        self._on_warn(f"step {i} ({op}) skipped — {reason}")
                        break
                    raise RecipeFailure(i, reason, op=op) from e

        return returned

    def _run_op(self, i: int, step: dict, op: Optional[str], args: dict,
                returned: dict) -> None:
        """Execute a single step. Raises on failure; retry/optional handling
        lives in `execute`. May raise raw exceptions (wrapped by the caller) or a
        `RecipeFailure` for deterministic errors (verify / unknown op)."""
        if op == "goto":
            self.browser.goto(step["url"])
        elif op == "wait":
            ms = int(step.get("ms", 500))
            # Scale down wait times by a factor of 5 (e.g. 1000ms -> 200ms) for speed, with 50ms min
            scaled_ms = max(50, int(ms * 0.2)) if ms > 0 else 0
            self.browser.wait(scaled_ms)
        elif op == "wait_for":
            # Wait for a selector to appear — robust against variable
            # render/network time, unlike the scaled fixed `wait`.
            # Swallows timeout so extraction can still run on partial pages.
            sel = step.get("selector")
            if sel:
                try:
                    self.browser.page.wait_for_selector(
                        sel, timeout=int(step.get("ms", 8000))
                    )
                except Exception:
                    pass
        elif op == "scroll":
            self.browser.scroll(step.get("direction", "down"))
        elif op == "click":
            loc = resolve(self.browser.page, Target.from_dict(step["target"]))
            loc.scroll_into_view_if_needed(timeout=2000)
            loc.click(timeout=4000)
        elif op == "type":
            text = step.get("text", "")
            # An optional param that resolved to empty leaves nothing to
            # type — skip rather than clobbering any default field value.
            if text == "" and not step.get("press_enter"):
                return
            loc = resolve(self.browser.page, Target.from_dict(step["target"]))
            loc.scroll_into_view_if_needed(timeout=2000)
            loc.fill(text, timeout=4000)
            if step.get("press_enter"):
                loc.press("Enter")
        elif op == "press_enter":
            loc = resolve(self.browser.page, Target.from_dict(step["target"]))
            loc.press("Enter")
        elif op == "press":
            # Generic key press. With a target, press the key on that
            # element; otherwise press it on whatever is focused. Used
            # for autocomplete commit sequences (ArrowDown, Enter).
            key = step.get("key", "Enter")
            if step.get("target"):
                loc = resolve(self.browser.page, Target.from_dict(step["target"]))
                loc.press(key)
            else:
                self.browser.press_key(key)
        elif op == "select":
            value = step.get("value", "")
            # Optional category/filter omitted -> empty value. Skip so
            # the recipe falls back to the control's default selection.
            if value == "":
                return
            loc = resolve(self.browser.page, Target.from_dict(step["target"]))
            try:
                loc.select_option(value=value, timeout=2000)
            except Exception:
                loc.select_option(label=value, timeout=2000)
        elif op == "extract":
            loc = resolve(self.browser.page, Target.from_dict(step["target"]))
            attr = step.get("attr", "text")
            if attr == "text":
                val = loc.inner_text(timeout=2000).strip()
            elif attr == "value":
                val = loc.input_value(timeout=2000)
            else:
                val = loc.get_attribute(attr, timeout=2000)
            returned[step["key"]] = val
        elif op == "js_extract":
            # Custom JS for tricky extraction. Deterministic — no LLM.
            # Parameters are passed natively as the second arg to
            # page.evaluate, bound to `args` inside an `(args) => {...}`
            # function. Legacy IIFE expressions ignore the extra arg.
            expr = step["expr"]
            val = self.browser.page.evaluate(expr, args)
            returned[step["key"]] = val
        elif op == "verify":
            if not wait_for_condition(self.browser.page, step, timeout_s=3.0):
                kind = step.get("kind", "page_text_contains")
                expected = str(step.get("value", ""))
                raise RecipeFailure(i, f"verify failed: {kind}={expected!r}", op="verify")
        else:
            raise RecipeFailure(i, f"unknown op {op!r}", op=op)
