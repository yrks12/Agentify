"""DOM-only perception: extract a numbered list of interactive elements.

For each snapshot we mark interactive elements in the page with a
`data-w2a-id` attribute so the Locator we hand to Playwright is uniquely
addressable and survives between observe and act within a single step.
Markers are cleared at the start of every snapshot, so IDs never leak
across steps.
"""

from dataclasses import dataclass

from playwright.sync_api import Locator, Page

# JS that runs in-page. Clears prior markers, then re-walks the DOM
# and tags every visible interactive element with a fresh data-w2a-id.
_COLLECT_JS = r"""
() => {
  // Wipe any markers from the previous snapshot so IDs are fresh.
  for (const el of document.querySelectorAll('[data-w2a-id]')) {
    el.removeAttribute('data-w2a-id');
  }

  const inputTypeRoles = {
    text: 'textbox', email: 'textbox', password: 'textbox',
    search: 'searchbox', tel: 'textbox', url: 'textbox',
    number: 'spinbutton', checkbox: 'checkbox', radio: 'radio',
    submit: 'button', button: 'button', reset: 'button',
    file: 'button',
  };

  const inferRole = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' && el.href) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') return inputTypeRoles[el.type] || 'textbox';
    if (el.isContentEditable) return 'textbox';
    return null;
  };

  const accessibleName = (el) => {
    let name = el.getAttribute('aria-label') || '';
    if (!name) {
      const labelledBy = el.getAttribute('aria-labelledby');
      if (labelledBy) {
        const refs = labelledBy.split(/\s+/).map(id => document.getElementById(id));
        name = refs.filter(Boolean).map(n => n.innerText).join(' ');
      }
    }
    if (!name && el.labels && el.labels.length) {
      name = el.labels[0].innerText;
    }
    if (!name) {
      const tag = el.tagName.toLowerCase();
      if (tag === 'input' && (el.type === 'submit' || el.type === 'button')) {
        name = el.value || '';
      } else if (tag === 'img') {
        name = el.alt || '';
      } else {
        name = el.innerText || '';
      }
    }
    if (!name) name = el.placeholder || el.title || '';
    return (name || '').trim().replace(/\s+/g, ' ').slice(0, 120);
  };

  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    if (rect.bottom < 0 || rect.top > window.innerHeight) return false;
    if (rect.right < 0 || rect.left > window.innerWidth) return false;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity) === 0) return false;
    return true;
  };

  const candidates = document.querySelectorAll(
    'a, button, input, select, textarea, [role], [tabindex], [contenteditable=true]'
  );
  const elements = [];
  let idx = 0;
  for (const el of candidates) {
    const role = inferRole(el);
    if (!role) continue;
    if (el.disabled) continue;
    if (!isVisible(el)) continue;

    const id = 'w2a-' + idx++;
    el.setAttribute('data-w2a-id', id);
    elements.push({
      id,
      role,
      name: accessibleName(el),
      value: ('value' in el ? (el.value || '') : '').slice(0, 80),
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
    });
  }

  // Also capture a page-text snippet so the agent can read content
  // (article bodies, JSON responses, search results, etc).
  let pageText = '';
  try {
    const main = document.querySelector('main, article, [role="main"]') || document.body;
    pageText = (main.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 2500);
  } catch (e) {
    pageText = '';
  }

  return { elements, pageText, title: document.title || '' };
}
"""


@dataclass
class AXElement:
    id: int
    role: str
    name: str
    value: str = ""
    tag: str = ""
    input_type: str = ""
    w2a_id: str = ""  # the data-w2a-id marker on the page

    def format(self) -> str:
        parts = [f"[{self.id}]", self.role]
        if self.name:
            parts.append(f'"{self.name}"')
        if self.value:
            parts.append(f"(value={self.value!r})")
        if self.tag == "input" and self.input_type:
            parts.append(f"<input type={self.input_type}>")
        return " ".join(parts)


def snapshot(
    page: Page, max_elements: int = 80
) -> tuple[list[AXElement], dict[int, Locator], bool, str, str]:
    """Return (elements, id->Locator, truncated, page_text, title).

    Collects from the main frame AND every child `<iframe>` so controls inside an
    iframe are seen (`_COLLECT_JS` only scans one frame's light DOM). Title and
    page text come from the main frame; each element's Locator is bound to its own
    frame so clicks land in the right place. `page.frames` lists the main frame
    first, so its elements (and the user-facing chrome) lead.
    """
    title = ""
    page_text = ""
    # (frame, item) pairs, main frame first.
    collected: list[tuple[object, dict]] = []
    for fi, frame in enumerate(page.frames):
        try:
            payload = frame.evaluate(_COLLECT_JS) or {}
        except Exception:
            continue  # cross-origin frame we can't script, or a detached frame
        if not isinstance(payload, dict):
            continue
        if fi == 0:
            title = payload.get("title", "")
            page_text = payload.get("pageText", "")
        for item in payload.get("elements", []):
            collected.append((frame, item))

    truncated = len(collected) > max_elements
    collected = collected[:max_elements]

    elements: list[AXElement] = []
    locator_map: dict[int, Locator] = {}
    for i, (frame, item) in enumerate(collected, start=1):
        elements.append(
            AXElement(
                id=i,
                role=item.get("role", ""),
                name=item.get("name", ""),
                value=item.get("value", ""),
                tag=item.get("tag", ""),
                input_type=item.get("type", ""),
                w2a_id=item.get("id", ""),
            )
        )
        # Bind to the owning frame so the locator resolves in the right document
        # (data-w2a-id values can repeat across frames; frame-scoping keeps them
        # unambiguous).
        locator_map[i] = frame.locator(f'[data-w2a-id="{item["id"]}"]')
    return elements, locator_map, truncated, page_text, title


def format_tree(
    elements: list[AXElement],
    url: str,
    truncated: bool = False,
    page_text: str = "",
    title: str = "",
) -> str:
    lines = [f"URL: {url}"]
    if title:
        lines.append(f"Title: {title}")
    lines.append(f"Interactive elements ({len(elements)}):")
    if not elements:
        lines.append("  (none visible)")
    for el in elements:
        lines.append("  " + el.format())
    if truncated:
        lines.append("  ... (truncated; scroll to reveal more)")
    if page_text:
        lines.append("")
        lines.append("Page text (truncated):")
        lines.append(page_text)
    return "\n".join(lines)
