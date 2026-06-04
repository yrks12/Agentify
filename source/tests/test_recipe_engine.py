"""Recipe Engine tests with a fake Browser. No Playwright, no network."""

from dataclasses import dataclass, field

from agentify.recipe import Engine, Recipe, RecipeFailure, _substitute


def test_substitute_templates_into_target_name_and_value():
    # The replay side of #3: {{param}} inside a step's target resolves the right
    # element for the caller's argument — no engine change needed.
    steps = [
        {"op": "click", "target": {"role": "radio", "name": "{{title}}"}},
        {"op": "select", "target": {"role": "combobox", "name": "Month"}, "value": "{{month}}"},
    ]
    out = _substitute(steps, {"title": "Mrs", "month": "March"})
    assert out[0]["target"]["name"] == "Mrs"
    assert out[1]["value"] == "March"


@dataclass
class FakePage:
    url: str = "https://example.com/"
    body_text: str = ""
    locator_count: int = 1
    # Make `click` fail the first N times (for retry tests); the error raised is
    # `click_error` if set, else a Playwright-style TimeoutError (transient).
    click_fail_times: int = 0
    click_error: object = None
    click_calls: int = 0

    def locator(self, *args, **kwargs):
        return self

    def evaluate(self, expr: str, *args, **kwargs):
        if "innerText" in expr:
            return self.body_text
        if "(()=>" in expr.replace(" ", ""):
            return {"stub": True}
        return None

    # Locator-ish methods
    def scroll_into_view_if_needed(self, timeout=None): pass
    def click(self, timeout=None):
        self.click_calls += 1
        if self.click_fail_times > 0:
            self.click_fail_times -= 1
            raise self.click_error or TimeoutError("Timeout 4000ms exceeded")
        self.last_click = True
    def fill(self, value, timeout=None):
        self.last_fill = value
    def press(self, key):
        self.last_press = key
    def select_option(self, value=None, label=None, timeout=None):
        self.last_select = value or label
    def inner_text(self, timeout=None):
        return "extracted-text"
    def input_value(self, timeout=None):
        return "extracted-value"
    def get_attribute(self, name, timeout=None):
        return f"attr-{name}"
    def count(self):
        return self.locator_count
    @property
    def first(self):
        return self
    def get_by_role(self, role, name=None):
        return self
    def get_by_text(self, text, exact=False):
        return self


@dataclass
class FakeBrowser:
    page: FakePage = field(default_factory=FakePage)
    actions: list = field(default_factory=list)

    def goto(self, url, wait_ms=0):
        self.page.url = url
        self.actions.append(("goto", url))

    def wait(self, ms):
        self.actions.append(("wait", ms))
        return f"waited {ms}"

    def scroll(self, direction):
        self.actions.append(("scroll", direction))
        return f"scrolled {direction}"

    def press_key(self, key):
        self.actions.append(("press_key", key))
        return f"pressed {key}"


def test_substitution_in_recipe_steps():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t",
        description="d",
        parameters={"type": "object", "properties": {"name": {"type": "string"}}},
        steps=[
            {"op": "goto", "url": "https://e.com/?name={{name}}"},
            {"op": "type", "target": {"role": "textbox", "name": "Name"}, "text": "{{name}}"},
        ],
    )
    Engine(browser).execute(recipe, {"name": "Jane"})
    assert browser.actions[0] == ("goto", "https://e.com/?name=Jane")
    assert browser.page.last_fill == "Jane"


def test_verify_passes_when_text_present():
    browser = FakeBrowser(page=FakePage(body_text="Thanks for your submission"))
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "verify", "kind": "page_text_contains", "value": "thanks", "case_insensitive": True}],
    )
    Engine(browser).execute(recipe, {})  # no exception


def test_verify_fails_loudly():
    browser = FakeBrowser(page=FakePage(body_text="something else"))
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "verify", "kind": "page_text_contains", "value": "thanks"}],
    )
    try:
        Engine(browser).execute(recipe, {})
    except RecipeFailure as e:
        assert e.step_index == 0
        return
    raise AssertionError("should have raised RecipeFailure")


def test_unknown_op_fails():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "teleport", "destination": "moon"}],
    )
    try:
        Engine(browser).execute(recipe, {})
    except RecipeFailure as e:
        assert "teleport" in e.reason
        return
    raise AssertionError("should have raised RecipeFailure")


def test_extract_stores_into_result():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[
            {"op": "extract", "key": "title", "target": {"role": "heading"}, "attr": "text"},
        ],
    )
    result = Engine(browser).execute(recipe, {})
    assert result == {"title": "extracted-text"}


def test_press_op_on_target():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "press", "key": "ArrowDown", "target": {"role": "combobox"}}],
    )
    Engine(browser).execute(recipe, {})
    assert browser.page.last_press == "ArrowDown"


def test_press_op_without_target_uses_keyboard():
    browser = FakeBrowser()
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "press", "key": "Enter"}],
    )
    Engine(browser).execute(recipe, {})
    assert ("press_key", "Enter") in browser.actions


# ---------------------------------------------------------- error handling (#7)

_CLICK = {"op": "click", "target": {"role": "button", "name": "Go"}}


class _CountingEngine(Engine):
    """Records which op each `_run_op` call ran, to prove retry vs no-retry."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.op_calls = []

    def _run_op(self, i, step, op, args, returned):
        self.op_calls.append(op)
        return super()._run_op(i, step, op, args, returned)


def test_transient_click_failure_is_retried_then_succeeds():
    # A click that raises a transient TimeoutError once then succeeds: with at
    # least one retry the recipe completes and the action runs twice.
    browser = FakeBrowser(page=FakePage(click_fail_times=1))
    engine = _CountingEngine(browser, max_retries=2, backoff_s=0)
    engine.execute(Recipe(name="t", description="", parameters={}, steps=[_CLICK]), {})
    assert browser.page.last_click is True
    assert engine.op_calls.count("click") == 2  # first failed, retry succeeded


def test_no_retry_when_max_retries_zero():
    # Same transient failure, but retries disabled -> fails fast, one attempt.
    browser = FakeBrowser(page=FakePage(click_fail_times=1))
    engine = _CountingEngine(browser, max_retries=0, backoff_s=0)
    try:
        engine.execute(Recipe(name="t", description="", parameters={}, steps=[_CLICK]), {})
    except RecipeFailure as e:
        assert e.op == "click"
        assert engine.op_calls.count("click") == 1
        return
    raise AssertionError("should have raised RecipeFailure")


def test_non_transient_error_is_not_retried():
    # A ValueError is deterministic — retrying can't help, so it fails on the
    # first attempt even with retries enabled.
    browser = FakeBrowser(page=FakePage(click_fail_times=1, click_error=ValueError("boom")))
    engine = _CountingEngine(browser, max_retries=3, backoff_s=0)
    try:
        engine.execute(Recipe(name="t", description="", parameters={}, steps=[_CLICK]), {})
    except RecipeFailure:
        assert engine.op_calls.count("click") == 1
        return
    raise AssertionError("should have raised RecipeFailure")


def test_verify_failure_is_not_retried():
    # A failed assertion is deterministic: it raises immediately and is evaluated
    # exactly once, never retried, even with retries enabled.
    browser = FakeBrowser(page=FakePage(body_text="something else"))
    engine = _CountingEngine(browser, max_retries=2, backoff_s=0)
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[{"op": "verify", "kind": "page_text_contains", "value": "thanks"}],
    )
    try:
        engine.execute(recipe, {})
    except RecipeFailure as e:
        assert e.op == "verify"
        assert engine.op_calls.count("verify") == 1
        return
    raise AssertionError("should have raised RecipeFailure")


def test_optional_step_failure_is_skipped_and_execution_continues():
    # An optional step whose action throws is logged and skipped; later steps
    # still run and their results are returned.
    warnings = []
    browser = FakeBrowser(page=FakePage(click_fail_times=1))
    engine = Engine(browser, max_retries=0, backoff_s=0, on_warn=warnings.append)
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[
            {**_CLICK, "optional": True},
            {"op": "extract", "key": "title", "target": {"role": "heading"}, "attr": "text"},
        ],
    )
    result = engine.execute(recipe, {})
    assert result == {"title": "extracted-text"}  # step after the skip still ran
    assert warnings and "skipped" in warnings[0]


def test_optional_recipe_failure_is_also_skipped():
    # `optional` swallows deterministic failures too (here an unknown op).
    warnings = []
    browser = FakeBrowser()
    engine = Engine(browser, on_warn=warnings.append)
    recipe = Recipe(
        name="t", description="", parameters={},
        steps=[
            {"op": "teleport", "optional": True},
            {"op": "goto", "url": "https://e.com/"},
        ],
    )
    engine.execute(recipe, {})
    assert browser.actions[-1] == ("goto", "https://e.com/")
    assert warnings and "skipped" in warnings[0]


def test_failure_reason_is_a_single_actionable_line():
    # Multi-line Playwright dumps are collapsed to one line carrying the op and
    # the target, so neither the user nor the run-mapped LLM sees a stack trace.
    err = ValueError("Locator.click: Timeout\n  at line 1\n  at line 2")
    browser = FakeBrowser(page=FakePage(click_fail_times=1, click_error=err))
    try:
        Engine(browser, max_retries=0).execute(
            Recipe(name="t", description="", parameters={}, steps=[_CLICK]), {}
        )
    except RecipeFailure as e:
        assert e.op == "click"
        assert "\n" not in e.reason
        assert "click on button='Go'" in e.reason
        assert "at line 2" not in e.reason  # stack tail dropped
        return
    raise AssertionError("should have raised RecipeFailure")
