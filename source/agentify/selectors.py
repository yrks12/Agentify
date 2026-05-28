"""Robust element targets: capture at map time, resolve at replay time.

A `Target` is a description of *which* element to act on, recorded once
during mapping and re-resolved every time the recipe runs. The order of
strategies (role+name first, css fallback, text fallback) is what makes
recipes survive small DOM tweaks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from playwright.sync_api import Locator, Page


@dataclass
class Target:
    role: Optional[str] = None
    name: Optional[str] = None
    css: Optional[str] = None
    text: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "Target":
        return cls(
            role=d.get("role"),
            name=d.get("name"),
            css=d.get("css"),
            text=d.get("text"),
        )


import time

def resolve(page: Page, target: Target, timeout_ms: int = 3000) -> Locator:
    """Pick a Locator for target. Tries strategies in priority order, polling until one is found or timeout."""
    start_time = time.time()
    while True:
        if target.role:
            loc = page.get_by_role(target.role, name=target.name) if target.name else page.get_by_role(target.role)
            try:
                if loc.count() > 0:
                    return loc.first
            except Exception:
                pass
        if target.css:
            loc = page.locator(target.css)
            try:
                if loc.count() > 0:
                    return loc.first
            except Exception:
                pass
        if target.text:
            loc = page.get_by_text(target.text, exact=False)
            try:
                if loc.count() > 0:
                    return loc.first
            except Exception:
                pass
        
        if (time.time() - start_time) * 1000 > timeout_ms:
            break
        time.sleep(0.03)

    raise LookupError(f"could not resolve target: {target.to_dict()}")



# JS that, given a data-w2a-id, returns a stable CSS fallback for it.
# We try id, name, data-testid, then build a tag-based path. The result
# is a Playwright-compatible CSS selector.
_CAPTURE_CSS_JS = r"""
(w2aId) => {
  const el = document.querySelector(`[data-w2a-id="${w2aId}"]`);
  if (!el) return null;
  const tag = el.tagName.toLowerCase();
  if (el.id) return `${tag}#${CSS.escape(el.id)}`;
  const testid = el.getAttribute('data-testid');
  if (testid) return `[data-testid="${testid}"]`;
  if (el.name) return `${tag}[name="${CSS.escape(el.name)}"]`;
  if (tag === 'input' && el.type) {
    // narrow by type and surrounding form, if possible
    const form = el.closest('form');
    if (form && form.id) {
      return `#${CSS.escape(form.id)} input[type="${el.type}"]`;
    }
    return `input[type="${el.type}"]`;
  }
  // Fallback: tag + nth-of-type within its parent
  const parent = el.parentElement;
  if (!parent) return tag;
  const siblings = Array.from(parent.children).filter(c => c.tagName === el.tagName);
  const idx = siblings.indexOf(el) + 1;
  return `${tag}:nth-of-type(${idx})`;
}
"""


def capture_target(
    page: Page,
    w2a_id: str,
    role: Optional[str],
    name: Optional[str],
) -> Target:
    """Build a Target from an element tagged with data-w2a-id during mapping.

    Records role+name (already known from the AX tree) AND a CSS fallback
    derived from the element's stable attributes.
    """
    css: Optional[str] = None
    try:
        css = page.evaluate(_CAPTURE_CSS_JS, w2a_id)
    except Exception:
        css = None
    return Target(role=role or None, name=name or None, css=css)
