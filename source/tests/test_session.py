"""Session orchestration logic (hermetic — fake browser/page, no Chromium)."""

from pathlib import Path

from agentify.recipe import Recipe, evaluate_condition
from agentify.registry import AuthConfig, SiteRegistry
from agentify.session import (
    SessionStatus,
    _login_start_url,
    ensure_authenticated,
)


class FakePage:
    def __init__(self, url="", body=""):
        self.url = url
        self._body = body

    def evaluate(self, expr, *args):
        return self._body


class FakeBrowser:
    """Minimal stand-in: records goto/save, exposes a settable page."""

    def __init__(self, page):
        self.page = page
        self.saved: list = []

    def goto(self, url, *a, **k):
        self.page.url = url

    def save_storage_state(self, path):
        self.saved.append(path)


def _login_recipe():
    return Recipe(
        name="login",
        description="Sign in.",
        parameters={"type": "object", "properties": {"email": {}, "password": {}}},
        steps=[{"op": "goto", "url": "https://x.test/login"}],
    )


# --- evaluate_condition (the shared check) --------------------------------

def test_evaluate_url_contains():
    assert evaluate_condition(FakePage(url="https://x.test/app/today"),
                              {"kind": "url_contains", "value": "/app/today"})
    assert not evaluate_condition(FakePage(url="https://x.test/login"),
                                  {"kind": "url_contains", "value": "/app/today"})


def test_evaluate_page_text_contains_case_insensitive():
    page = FakePage(body="Welcome, you are logged in")
    assert evaluate_condition(page, {"kind": "page_text_contains", "value": "LOGGED IN",
                                     "case_insensitive": True})
    assert not evaluate_condition(page, {"kind": "page_text_contains", "value": "LOGGED IN"})


# --- _login_start_url ------------------------------------------------------

def test_login_start_url_uses_first_goto():
    reg = SiteRegistry(site="x", base_url="https://x.test", tools=[_login_recipe()],
                       auth=AuthConfig(login_tool="login"))
    assert _login_start_url(reg) == "https://x.test/login"


def test_login_start_url_falls_back_to_base():
    reg = SiteRegistry(site="x", base_url="https://x.test", tools=[], auth=None)
    assert _login_start_url(reg) == "https://x.test"


# --- ensure_authenticated --------------------------------------------------

def test_no_login_required():
    reg = SiteRegistry(site="x", base_url="https://x.test", tools=[])  # auth is None
    status = ensure_authenticated(object(), reg, session_file=Path("/none.json"))
    assert status.needed is False and status.authenticated is True


def test_valid_session_passes_probe_without_relogin():
    reg = SiteRegistry(
        site="x", base_url="https://x.test/app/today", tools=[_login_recipe()],
        auth=AuthConfig(login_tool="login", check={"kind": "url_contains", "value": "/app/today"}),
    )
    browser = FakeBrowser(FakePage())
    status = ensure_authenticated(browser, reg, session_file=Path("/none.json"))
    assert status.authenticated and not status.relogged
    assert browser.saved == []  # nothing re-saved when the session is already valid


def test_invalid_session_without_interactive_prompt_fails_cleanly():
    reg = SiteRegistry(
        site="x", base_url="https://x.test/login", tools=[_login_recipe()],
        auth=AuthConfig(login_tool="login", check={"kind": "url_contains", "value": "/dashboard"}),
    )
    browser = FakeBrowser(FakePage())
    status = ensure_authenticated(
        browser, reg, session_file=Path("/none.json"),
        interactive=False, check_timeout_s=0.0,
    )
    assert status.needed and not status.authenticated
    assert "interactive" in status.detail


# --- SessionStatus rendering ----------------------------------------------

def test_status_str_variants():
    assert str(SessionStatus(needed=False, authenticated=True)) == "no login required"
    assert str(SessionStatus(needed=True, authenticated=True)) == "session valid"
    assert str(SessionStatus(needed=True, authenticated=True, relogged=True)) == "re-logged in"
    assert "not authenticated" in str(SessionStatus(needed=True, authenticated=False, detail="x"))
