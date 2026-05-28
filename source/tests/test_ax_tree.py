"""Unit tests for ax_tree formatting (no Playwright needed)."""

from agentify.ax_tree import AXElement, format_tree


def test_format_tree_basic():
    els = [
        AXElement(id=1, role="textbox", name="Email", tag="input", input_type="email"),
        AXElement(id=2, role="textbox", name="Password", tag="input", input_type="password"),
        AXElement(id=3, role="button", name="Sign in", tag="button"),
    ]
    out = format_tree(els, "https://example.com/login")
    assert "URL: https://example.com/login" in out
    assert '[1] textbox "Email"' in out
    assert '[3] button "Sign in"' in out


def test_format_tree_with_page_text():
    out = format_tree([], "https://e.com", page_text="Hello world", title="Example")
    assert "Title: Example" in out
    assert "Page text" in out
    assert "Hello world" in out


def test_format_tree_empty():
    out = format_tree([], "https://example.com")
    assert "(none visible)" in out


def test_format_tree_truncated_marker():
    out = format_tree([AXElement(id=1, role="button", name="Go")], "https://e.com", truncated=True)
    assert "truncated" in out


def test_axelement_format_with_value():
    el = AXElement(id=7, role="textbox", name="Search", value="cats", tag="input", input_type="search")
    s = el.format()
    assert s.startswith("[7] textbox")
    assert "Search" in s
    assert "cats" in s
