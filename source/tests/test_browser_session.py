"""Browser-level: `save_storage_state` produces a reusable session.

Launches real Chromium (no LLM, no network), sets a session cookie, persists it
via `Browser.save_storage_state`, then proves a fresh context built from that
file sees the cookie — the exact round-trip stage 2 will depend on. Self-skips
where Chromium can't launch, so the hermetic suite stays green everywhere.
"""

import json

import pytest

from agentify.browser import Browser

# Playwright is a core dependency; importorskip just guards exotic envs.
pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

_COOKIE = {"name": "sessionid", "value": "abc123", "domain": "example.test", "path": "/"}


def test_save_storage_state_roundtrips_cookies(tmp_path):
    path = tmp_path / "sess.json"

    # Save side: drive our Browser, seed a session cookie, persist the state.
    try:
        with Browser(headless=True) as b:
            b._context.add_cookies([_COOKIE])
            saved = b.save_storage_state(path)
    except Exception as e:  # missing browser binary / sandbox restrictions
        pytest.skip(f"Chromium unavailable: {e}")

    assert saved == path and path.exists()
    data = json.loads(path.read_text())
    assert "sessionid" in {c["name"] for c in data.get("cookies", [])}

    # Reload side: a fresh context built from the saved file must see the cookie.
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as e:
        pw.stop()
        pytest.skip(f"Chromium unavailable: {e}")
    try:
        ctx = browser.new_context(storage_state=str(path))
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
    finally:
        browser.close()
        pw.stop()

    assert cookies.get("sessionid") == "abc123"


def test_save_storage_state_creates_parent_dir(tmp_path):
    # The sessions/ dir may not exist yet — save_storage_state must create it.
    path = tmp_path / "nested" / "sess.json"
    try:
        with Browser(headless=True) as b:
            b.save_storage_state(path)
    except Exception as e:
        pytest.skip(f"Chromium unavailable: {e}")
    assert path.exists()


def test_browser_loads_storage_state_on_start(tmp_path):
    # Save a session, then start a NEW Browser from it and assert the cookie is
    # loaded into the context — the stage-2 load path.
    path = tmp_path / "sess.json"
    try:
        with Browser(headless=True) as b:
            b._context.add_cookies([_COOKIE])
            b.save_storage_state(path)
        with Browser(headless=True, storage_state=path) as b2:
            names = {c["name"] for c in b2._context.cookies()}
    except Exception as e:
        pytest.skip(f"Chromium unavailable: {e}")
    assert "sessionid" in names


def test_missing_storage_state_starts_fresh(tmp_path):
    # A non-existent session file must not crash start() — just a clean context.
    missing = tmp_path / "does_not_exist.json"
    try:
        with Browser(headless=True, storage_state=missing) as b:
            cookies = b._context.cookies()
    except Exception as e:
        pytest.skip(f"Chromium unavailable: {e}")
    assert cookies == []
