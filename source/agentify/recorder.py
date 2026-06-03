"""Recording Browser: tees every action into a recipe-step list.

Wraps the regular Browser so the existing approach-A Agent loop can
drive it unchanged. As actions go through, the recorder captures a
Target (role+name + css fallback) for each element it touched and
substitutes sentinel placeholders for `{{param}}` markers.

Used only during the mapping phase, never at runtime.
"""

from __future__ import annotations

from typing import Any, Optional

from .browser import Browser
from .selectors import Target, capture_target


def _match_param(text: Any, placeholders: dict[str, str]) -> Optional[str]:
    """Param whose placeholder value EQUALS `text` (case-insensitive, trimmed).

    Exact match only — a placeholder value of "Web" must not hijack a "Web
    Design" link. This is what lets a non-text selection (radio/option/select)
    be tied back to the parameter it represents.
    """
    if not isinstance(text, str):
        return None
    needle = text.strip().lower()
    if not needle:
        return None
    for param, placeholder in placeholders.items():
        if isinstance(placeholder, str) and placeholder.strip().lower() == needle:
            return param
    return None


def parameterize_click_target(
    target: dict, ax_name: Any, placeholders: dict[str, str]
) -> dict:
    """If a click landed on the element that represents a parameter value
    (a radio/option/named link whose label IS the placeholder value), rewrite
    the target to resolve by `{{param}}` name and drop the value-specific css/
    text fallback — so a different argument resolves the right element at replay.
    Non-matching clicks (navigation, submit) are returned unchanged.
    """
    param = _match_param(ax_name, placeholders)
    if not param:
        return target
    out = dict(target)
    out["name"] = "{{" + param + "}}"
    out.pop("css", None)
    out.pop("text", None)
    return out


class RecordingBrowser(Browser):
    """A Browser that records every action it executes as a recipe step.

    `placeholders` maps parameter name -> sentinel value. The mapper
    feeds the agent a synthetic task instructing it to type these
    exact sentinel values into the relevant form fields. When the
    recorder sees them coming through, it swaps them back to
    `{{paramname}}` so the recipe is parameterised.
    """

    def __init__(self, placeholders: dict[str, str], **kw):
        super().__init__(**kw)
        self.placeholders = placeholders
        self.steps: list[dict] = []

    # --- recording helpers --------------------------------------------

    def _capture(self, element_id: int) -> Target:
        ax = self._ax_map.get(element_id)
        if ax is None:
            return Target()
        return capture_target(self.page, ax.w2a_id, ax.role, ax.name)

    def _bind(self, value: Any) -> Any:
        """Swap sentinel placeholders back to {{paramname}} in strings."""
        if not isinstance(value, str):
            return value
        for param, placeholder in self.placeholders.items():
            if placeholder in value:
                value = value.replace(placeholder, "{{" + param + "}}")
        return value

    # --- recorded actions ---------------------------------------------

    def goto(self, url: str, wait_ms: int = 800) -> None:
        super().goto(url, wait_ms)
        self.steps.append({"op": "goto", "url": url})

    def click(self, element_id: int) -> str:
        ax = self._ax_map.get(element_id)
        target = self._capture(element_id)
        out = super().click(element_id)
        target_dict = parameterize_click_target(
            target.to_dict(), ax.name if ax else None, self.placeholders
        )
        self.steps.append({"op": "click", "target": target_dict})
        return out

    def type_text(self, element_id: int, text: str, press_enter: bool = False) -> str:
        target = self._capture(element_id)
        out = super().type_text(element_id, text, press_enter)
        step: dict[str, Any] = {
            "op": "type",
            "target": target.to_dict(),
            "text": self._bind(text),
        }
        if press_enter:
            step["press_enter"] = True
        self.steps.append(step)
        return out

    def select_option(self, element_id: int, value: str) -> str:
        target = self._capture(element_id)
        out = super().select_option(element_id, value)
        # Prefer an exact placeholder match (the chosen option label/value IS the
        # parameter value) so the select is parameterised even when the option's
        # value attribute differs from the typed example; fall back to the
        # sentinel-substring swap for plain fields.
        param = _match_param(value, self.placeholders)
        bound = ("{{" + param + "}}") if param else self._bind(value)
        self.steps.append(
            {"op": "select", "target": target.to_dict(), "value": bound}
        )
        return out

    def scroll(self, direction: str) -> str:
        out = super().scroll(direction)
        self.steps.append({"op": "scroll", "direction": direction})
        return out

    def wait(self, ms: int) -> str:
        out = super().wait(ms)
        # Record a small post-action settle so replay matches map-time timing.
        self.steps.append({"op": "wait", "ms": ms})
        return out
