"""Recorder parameterises non-text selections — real Chromium, self-skips.

Drives the actual RecordingBrowser against deterministic `data:` pages (a native
<select> and a radio group) and asserts the recorded step is parameterised to
`{{param}}`, not frozen to the chosen value. No network.
"""

import pytest

from agentify.recorder import RecordingBrowser

pytest.importorskip("playwright.sync_api")

_SELECT_PAGE = (
    "data:text/html,<!doctype html><html><body>"
    "<select id=s><option>Red</option><option>Green</option><option>Blue</option></select>"
    "</body></html>"
)
_RADIO_PAGE = (
    "data:text/html,<!doctype html><html><body>"
    "<label><input type=radio name=t>Mr</label>"
    "<label><input type=radio name=t>Mrs</label>"
    "</body></html>"
)


def _drive(page_url, placeholders, action):
    rb = RecordingBrowser(placeholders=placeholders, headless=True)
    try:
        rb.start()
        rb.goto(page_url)
        obs = rb.observe()
        action(rb, obs)
    except Exception as e:  # missing browser binary / sandbox
        try:
            rb.stop()
        except Exception:
            pass
        pytest.skip(f"Chromium unavailable: {e}")
    rb.stop()
    return rb.steps


def test_native_select_value_is_parameterized():
    def act(rb, obs):
        combo = next(e for e in obs.elements if e.role == "combobox")
        rb.select_option(combo.id, "Green")

    steps = _drive(_SELECT_PAGE, {"color": "Green"}, act)
    sel = [s for s in steps if s["op"] == "select"][-1]
    assert sel["value"] == "{{color}}"


def test_radio_click_target_is_parameterized():
    def act(rb, obs):
        mrs = next(e for e in obs.elements if e.role == "radio" and e.name == "Mrs")
        rb.click(mrs.id)

    steps = _drive(_RADIO_PAGE, {"title": "Mrs"}, act)
    click = [s for s in steps if s["op"] == "click"][-1]
    assert click["target"]["name"] == "{{title}}"
    assert "css" not in click["target"]  # value-specific fallback dropped
