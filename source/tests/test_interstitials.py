"""Consent-label ranking (pure, hermetic — no browser)."""

from agentify.interstitials import consent_label_rank


def test_reject_outranks_accept():
    assert consent_label_rank("Reject all") < consent_label_rank("Accept all")


def test_match_is_trimmed_and_case_insensitive():
    assert consent_label_rank("  ACCEPT ALL  ") == consent_label_rank("accept all")
    assert consent_label_rank("I Agree") is not None


def test_non_consent_labels_are_none():
    for t in ("Search", "Book a flight", "", None, "Accept the terms and continue"):
        assert consent_label_rank(t) is None
