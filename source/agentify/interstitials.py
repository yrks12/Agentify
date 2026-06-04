"""Dismiss cookie-consent / "before you continue" interstitial walls.

JS-heavy sites (Google, LinkedIn, many EU sites) serve a consent/cookie wall
*instead of* the app, so the crawler would otherwise snapshot the wall and never
reach the real page. This clicks the wall away, best-effort and bounded.

Reject-style buttons are preferred (privacy-preserving, and on Google's consent
page "Reject all" still proceeds to the app); accept-style are the fallback to
guarantee passage. Driving is kept thin so the label logic stays unit-testable.
"""

from __future__ import annotations

import re

# Ordered by preference (reject first, then accept). Matched against a button's
# accessible name, exact (trimmed, case-insensitive).
_CONSENT_LABELS: tuple[str, ...] = (
    "reject all",
    "decline all",
    "reject",
    "accept all cookies",
    "accept all",
    "i accept",
    "i agree",
    "agree",
    "allow all",
    "got it",
    "accept",
)

# Known one-click cookie-banner controls (e.g. OneTrust) as a selector fallback.
_KNOWN_SELECTORS: tuple[str, ...] = (
    "#onetrust-reject-all-handler",
    "#onetrust-accept-btn-handler",
    "button[aria-label='Reject all']",
    "button[aria-label='Accept all']",
)


def consent_label_rank(text: str | None) -> int | None:
    """Priority of a button label as a consent action (lower = higher priority),
    or None if it isn't one. Exact match, trimmed and case-insensitive. Pure."""
    t = (text or "").strip().lower()
    for i, label in enumerate(_CONSENT_LABELS):
        if t == label:
            return i
    return None


def dismiss(page, *, timeout_ms: int = 2000) -> bool:
    """Click the highest-priority consent/cookie button visible on `page`.

    Returns True if something was clicked (the page may then navigate). Best
    effort and bounded — never raises.
    """
    for label in _CONSENT_LABELS:
        try:
            loc = page.get_by_role(
                "button", name=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)
            )
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    for sel in _KNOWN_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False
