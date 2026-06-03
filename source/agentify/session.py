"""Runtime session handling: load a saved login, keep it valid, persist it.

Stage 1 (mapping) records a login recipe and saves a `storage_state` file. This
module is the stage-2 counterpart: at call time it loads that session, checks it
with the probe derived during mapping, and — if it has expired or is missing —
re-runs the login (prompting interactively, exactly as at map time) and re-saves.
That lazy re-auth is what lets a session "stay logged in" across calls without
re-entering credentials every time.

Credentials are still never written to disk; only the refreshed `storage_state`
is persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .credentials import prompt_credentials
from .recipe import Engine, RecipeFailure, wait_for_condition
from .registry import SiteRegistry


@dataclass
class SessionStatus:
    """Outcome of an auth attempt, for the CLI to report."""

    needed: bool            # did the site require a login at all?
    authenticated: bool     # are we logged in now?
    relogged: bool = False  # did we have to (re)run the login this time?
    detail: str = ""

    def __str__(self) -> str:
        if not self.needed:
            return "no login required"
        if self.authenticated:
            return "re-logged in" if self.relogged else "session valid"
        return f"not authenticated ({self.detail})" if self.detail else "not authenticated"


def _login_start_url(registry: SiteRegistry) -> str:
    """The URL the login flow starts from (its first goto), else the site root."""
    login = registry.find(registry.auth.login_tool) if registry.auth else None
    if login:
        for step in login.steps:
            if step.get("op") == "goto" and step.get("url"):
                return step["url"]
    return registry.base_url


def ensure_authenticated(
    browser,
    registry: SiteRegistry,
    *,
    session_file: Path,
    interactive: bool = True,
    prompt_fn: Callable[..., dict] = prompt_credentials,
    check_timeout_s: float = 3.0,
) -> SessionStatus:
    """Make sure `browser` holds a valid session for `registry`.

    Assumes `browser` was started with `storage_state=session_file` (so any saved
    session is already loaded). Navigates to a page that reveals auth state, runs
    the mapping-time probe, and on failure re-runs the login recipe and re-saves.
    """
    auth = registry.auth
    if auth is None:
        return SessionStatus(needed=False, authenticated=True, detail="site needs no login")

    # Land somewhere that reveals auth state, then run the probe.
    browser.goto(registry.base_url)
    if auth.check and wait_for_condition(browser.page, auth.check, check_timeout_s):
        return SessionStatus(needed=True, authenticated=True, relogged=False)

    # Session missing/expired — re-login if we can.
    login = registry.find(auth.login_tool)
    if login is None:
        return SessionStatus(needed=True, authenticated=False,
                             detail=f"login tool {auth.login_tool!r} not in registry")
    if not interactive:
        return SessionStatus(needed=True, authenticated=False,
                             detail="session invalid and login needs an interactive prompt")

    creds = prompt_fn(list((login.parameters.get("properties") or {}).keys()),
                      tool_name=login.name)
    try:
        Engine(browser).execute(login, creds)
    except RecipeFailure as e:
        return SessionStatus(needed=True, authenticated=False, relogged=True,
                             detail=f"login replay failed: {e.reason}")

    browser.save_storage_state(session_file)
    ok = not auth.check or wait_for_condition(browser.page, auth.check, check_timeout_s)
    return SessionStatus(needed=True, authenticated=ok, relogged=True,
                         detail="" if ok else "probe still failing after login")


def manual_bootstrap(
    browser,
    registry: SiteRegistry,
    *,
    session_file: Path,
    wait_fn: Callable[[str], str] = input,
) -> SessionStatus:
    """Capture a session from a human login (MFA/CAPTCHA escape hatch).

    Intended for a non-headless browser: open the login page, let the user sign
    in by hand, then persist whatever session the site set. No credentials are
    prompted or stored.
    """
    browser.goto(_login_start_url(registry))
    wait_fn("Log in in the opened browser window, then press Enter here to capture the session... ")
    browser.save_storage_state(session_file)
    auth = registry.auth
    ok = not (auth and auth.check) or wait_for_condition(browser.page, auth.check)
    return SessionStatus(needed=True, authenticated=ok, relogged=True,
                         detail="" if ok else "no auth probe matched after manual login")
