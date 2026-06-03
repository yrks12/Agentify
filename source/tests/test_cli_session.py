"""Default-on session resolution for call / run-mapped (hermetic)."""

from agentify.cli import _resolve_session_name


def test_default_session_is_the_site_slug():
    # No flags → persistence on, named after the site (the "continuous agent").
    assert _resolve_session_name("demoqa", None, False) == "demoqa"


def test_explicit_session_name_overrides_slug():
    assert _resolve_session_name("demoqa", "account2", False) == "account2"


def test_no_session_disables_persistence():
    assert _resolve_session_name("demoqa", None, True) is None


def test_no_session_wins_over_explicit_name():
    assert _resolve_session_name("demoqa", "account2", True) is None
