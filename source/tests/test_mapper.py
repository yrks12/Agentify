"""Mapper unit tests for the generic multi-step machinery.

No Playwright, no network — these cover the pure transforms that make the
mapper handle arbitrary multi-step flows: realistic example placeholders and
parameter-independent autocomplete normalization.
"""

from agentify.mapper import (
    ToolProposal,
    _link_specificity,
    _normalize_autocomplete,
    _normalize_link,
    _placeholders_for,
    _prioritize_links,
    _same_origin,
)


def _proposal(props, examples=None):
    return ToolProposal(
        name="t",
        description="",
        parameters={"type": "object", "properties": props},
        tool_type="action",
        start_url="https://example.com",
        examples=examples or {},
    )


def test_placeholders_prefer_examples():
    p = _proposal(
        {"frm": {"type": "string"}, "n": {"type": "integer"}},
        examples={"frm": "TLV"},
    )
    ph = _placeholders_for(p)
    assert ph["frm"] == "TLV"          # realistic example used for typeahead
    assert ph["n"] == "424242"          # numeric sentinel still used (no example)


def test_placeholders_fallback_sentinel_without_example():
    p = _proposal({"q": {"type": "string"}})
    assert _placeholders_for(p)["q"] == "__W2A_Q__"


def test_autocomplete_click_becomes_first_option():
    """type {{param}} into a combobox + click a named suggestion ->
    verify-an-option-exists + click the FIRST option (param-independent)."""
    steps = [
        {"op": "click", "target": {"role": "combobox", "name": "Where from?"}},
        {"op": "type", "target": {"role": "combobox", "name": "Where from?"}, "text": "{{frm}}"},
        {"op": "wait", "ms": 800},
        {"op": "click", "target": {"role": "option", "name": "Tel Aviv-Yafo TLV"}},
        {"op": "click", "target": {"role": "button", "name": "Search"}},
    ]
    out = _normalize_autocomplete(steps)
    # the literal-named option click is gone
    assert all(
        s.get("target", {}).get("name") != "Tel Aviv-Yafo TLV" for s in out
    )
    # replaced by a gate + a bare first-option click
    assert {"op": "verify", "kind": "element_exists", "target": {"role": "option"}} in out
    assert {"op": "click", "target": {"role": "option"}} in out
    # the trailing Search click is preserved
    assert out[-1] == {"op": "click", "target": {"role": "button", "name": "Search"}}


def test_autocomplete_leaves_plain_typing_untouched():
    """A type with no {{param}}, or with no following option click, is left alone."""
    steps = [
        {"op": "type", "target": {"role": "searchbox", "name": "q"}, "text": "{{query}}", "press_enter": True},
        {"op": "wait", "ms": 500},
    ]
    assert _normalize_autocomplete(steps) == steps


# --------------------------------------------------------- deep crawl (#9)

BASE = "https://site.example/"


def test_normalize_link_resolves_relative_and_strips_fragment():
    assert _normalize_link("/products/42", BASE) == "https://site.example/products/42"
    assert _normalize_link("about", BASE) == "https://site.example/about"
    assert _normalize_link("/x#section", BASE) == "https://site.example/x"
    # absolute same-origin passes through
    assert _normalize_link("https://site.example/y", BASE) == "https://site.example/y"


def test_normalize_link_drops_non_navigational_and_cross_origin():
    for bad in ("", "   ", "#", "#top", "javascript:void(0)", "mailto:a@b.c", "tel:+123"):
        assert _normalize_link(bad, BASE) is None
    assert _normalize_link("https://other.example/z", BASE) is None  # cross-origin


def test_same_origin():
    assert _same_origin(BASE, "https://site.example/deep/page")
    assert not _same_origin(BASE, "https://evil.example/")


def test_link_specificity_counts_path_depth_and_query():
    assert _link_specificity("https://site.example/") == 0
    assert _link_specificity("https://site.example/blog") == 1
    assert _link_specificity("https://site.example/blog/post/123") == 3
    assert _link_specificity("https://site.example/item?id=9") == 2  # 1 seg + query


def test_prioritize_puts_keyword_pages_first():
    links = [
        "https://site.example/about",
        "https://site.example/login",  # keyword
    ]
    assert _prioritize_links(links)[0] == "https://site.example/login"


def test_prioritize_prefers_deeper_content_over_shallow_nav():
    links = [
        "https://site.example/home",            # shallow nav chrome
        "https://site.example/blog/the-article",  # deeper content
    ]
    # Neither is a keyword link, so the deeper/more-specific one wins.
    assert _prioritize_links(links)[0] == "https://site.example/blog/the-article"


def test_prioritize_dedupes_preserving_first_seen_for_ties():
    links = [
        "https://site.example/a",
        "https://site.example/a",
        "https://site.example/b",
    ]
    out = _prioritize_links(links)
    assert out == ["https://site.example/a", "https://site.example/b"]
