"""Target dataclass and resolver priority (no real Playwright page)."""

from dataclasses import dataclass

from agentify.selectors import Target, resolve


@dataclass
class Stub:
    def __init__(self, **strategies_present):
        self.strategies_present = strategies_present
        self.role_calls: list = []
        self.css_calls: list = []
        self.text_calls: list = []

    def get_by_role(self, role, name=None):
        self.role_calls.append((role, name))
        return _Loc(self.strategies_present.get("role", 0))

    def locator(self, css):
        self.css_calls.append(css)
        return _Loc(self.strategies_present.get("css", 0))

    def get_by_text(self, text, exact=False):
        self.text_calls.append(text)
        return _Loc(self.strategies_present.get("text", 0))


class _Loc:
    def __init__(self, count): self._count = count
    def count(self): return self._count
    @property
    def first(self): return self


def test_role_strategy_wins_when_present():
    page = Stub(role=2, css=1, text=1)
    loc = resolve(page, Target(role="button", name="OK", css=".btn", text="OK"))
    assert page.role_calls == [("button", "OK")]
    assert page.css_calls == []  # never reached
    assert loc is not None


def test_falls_through_to_css_when_role_misses():
    page = Stub(role=0, css=3, text=1)
    resolve(page, Target(role="button", name="OK", css=".btn"))
    assert page.role_calls == [("button", "OK")]
    assert page.css_calls == [".btn"]


def test_falls_through_to_text_when_css_misses():
    page = Stub(role=0, css=0, text=2)
    resolve(page, Target(role=None, name=None, css=".btn", text="Click me"))
    assert page.css_calls == [".btn"]
    assert page.text_calls == ["Click me"]


def test_raises_when_all_strategies_fail():
    page = Stub(role=0, css=0, text=0)
    try:
        resolve(page, Target(role="button", name="OK", css=".btn", text="Click me"))
    except LookupError:
        return
    raise AssertionError("should have raised LookupError")


def test_target_dict_roundtrip():
    t = Target(role="textbox", name="Email", css="input[name=email]")
    d = t.to_dict()
    assert "text" not in d  # None fields are stripped
    t2 = Target.from_dict(d)
    assert t2 == t
