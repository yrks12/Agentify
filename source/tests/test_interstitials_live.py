"""Settle-before-snapshot and consent dismissal — real Chromium, self-skips.

Drives the actual Browser against deterministic `data:` pages (no network):
content that appears late, and a fake consent wall. Skips if Chromium is
unavailable.
"""

import pytest

from agentify.browser import Browser
from agentify.interstitials import dismiss

pytest.importorskip("playwright.sync_api")

# Body is empty at domcontentloaded; a button is added ~300ms later. Only a
# settle-then-snapshot will see it.
_LATE_PAGE = (
    "data:text/html,<!doctype html><html><body><script>"
    "setTimeout(function(){var b=document.createElement('button');"
    "b.textContent='LateBtn';document.body.appendChild(b);},300)"
    "</script></body></html>"
)
_CONSENT_PAGE = (
    "data:text/html,<!doctype html><html><body>"
    "<button>Reject all</button><button>Other</button></body></html>"
)
_PLAIN_PAGE = (
    "data:text/html,<!doctype html><html><body><button>Search</button></body></html>"
)


def _browser():
    b = Browser(headless=True)
    try:
        b.start()
    except Exception as e:  # missing binary / sandbox
        try:
            b.stop()
        except Exception:
            pass
        pytest.skip(f"Chromium unavailable: {e}")
    return b


def test_observe_settles_for_late_rendered_content():
    b = _browser()
    try:
        b.goto(_LATE_PAGE)
        names = [e.name for e in b.observe().elements]
    finally:
        b.stop()
    assert "LateBtn" in names  # settle waited for the late button


def test_dismiss_clicks_consent_button():
    b = _browser()
    try:
        b.goto(_CONSENT_PAGE)
        clicked = dismiss(b.page)
    finally:
        b.stop()
    assert clicked is True


def test_dismiss_noop_when_no_consent():
    b = _browser()
    try:
        b.goto(_PLAIN_PAGE)
        clicked = dismiss(b.page)
    finally:
        b.stop()
    assert clicked is False
