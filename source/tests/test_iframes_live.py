"""Iframe-aware capture + resolve — real Chromium, self-skips.

A `data:` page whose `<iframe srcdoc>` holds a custom ARIA combobox autocomplete
(role=combobox + role=option, NOT native <select>). Confirms Agentify now sees,
resolves, and operates a control that lives inside an iframe. No network.
"""

import urllib.parse

import pytest

from agentify.browser import Browser
from agentify.recipe import Engine, Recipe
from agentify.selectors import Target, resolve

pytest.importorskip("playwright.sync_api")

_INNER = (
    "<!doctype html><body>"
    "<input role=combobox aria-label=Fruit id=cb autocomplete=off>"
    "<ul role=listbox id=lb></ul>"
    "<script>"
    'var fruits=["Apple","Apricot","Banana","Blueberry","Cherry","Mango"];'
    'var cb=document.getElementById("cb"),lb=document.getElementById("lb");'
    'cb.addEventListener("input",function(){var v=cb.value.toLowerCase();lb.innerHTML="";'
    "fruits.filter(function(f){return f.toLowerCase().indexOf(v)===0;}).forEach(function(f){"
    'var li=document.createElement("li");li.setAttribute("role","option");li.textContent=f;'
    "li.onclick=function(){cb.value=f;lb.innerHTML=\"\";};lb.appendChild(li);});});"
    "</script></body>"
)
_PARENT = "<!doctype html><body><h1>Parent</h1><iframe srcdoc='" + _INNER + "'></iframe></body>"
_URL = "data:text/html," + urllib.parse.quote(_PARENT)


def _browser():
    b = Browser(headless=True)
    try:
        b.start()
    except Exception as e:
        try:
            b.stop()
        except Exception:
            pass
        pytest.skip(f"Chromium unavailable: {e}")
    return b


def test_observe_sees_control_inside_iframe():
    b = _browser()
    try:
        b.goto(_URL)
        roles = [(e.role, e.name) for e in b.observe().elements]
    finally:
        b.stop()
    assert ("combobox", "Fruit") in roles  # lives in the child frame


def test_resolve_finds_a_role_only_in_a_child_frame():
    b = _browser()
    try:
        b.goto(_URL)
        loc = resolve(b.page, Target(role="combobox", name="Fruit"))  # no raise
        assert loc.count() > 0
    finally:
        b.stop()


def test_recipe_operates_an_iframed_combobox():
    b = _browser()
    try:
        b.goto(_URL)
        recipe = Recipe(
            name="t", description="", parameters={},
            steps=[
                {"op": "click", "target": {"role": "combobox", "name": "Fruit"}},
                {"op": "type", "target": {"role": "combobox", "name": "Fruit"}, "text": "{{q}}"},
                {"op": "wait", "ms": 200},
                {"op": "verify", "kind": "element_exists", "target": {"role": "option"}},
                {"op": "click", "target": {"role": "option"}},
                {"op": "extract", "key": "chosen", "target": {"role": "combobox", "name": "Fruit"}, "attr": "value"},
            ],
        )
        result = Engine(b).execute(recipe, {"q": "Ba"})
    finally:
        b.stop()
    assert result == {"chosen": "Banana"}
