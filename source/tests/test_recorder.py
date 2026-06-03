"""Non-text parameter binding at record time (hermetic — pure helpers)."""

from agentify.recorder import _match_param, parameterize_click_target

PH = {"size": "Small", "title": "Mr", "project_type": "Web App"}


def test_match_param_exact_case_insensitive_trimmed():
    assert _match_param("Small", PH) == "size"
    assert _match_param("  small  ", PH) == "size"
    assert _match_param("MR", PH) == "title"
    assert _match_param("web app", PH) == "project_type"


def test_match_param_requires_exact_not_substring():
    assert _match_param("Web", PH) is None          # must not hijack "Web App"
    assert _match_param("Small print", PH) is None
    assert _match_param("Mr.", PH) is None


def test_match_param_empty_or_nonstring():
    assert _match_param("", PH) is None
    assert _match_param("   ", PH) is None
    assert _match_param(None, PH) is None


def test_click_target_parameterized_on_match_drops_value_specific_fallbacks():
    # A radio whose label IS the placeholder value -> resolve by {{param}} name,
    # and the value-specific css/text fallbacks are dropped.
    target = {"role": "radio", "name": "Mr", "css": "input#id_gender1", "text": "Mr"}
    out = parameterize_click_target(target, "Mr", PH)
    assert out == {"role": "radio", "name": "{{title}}"}


def test_click_target_unchanged_for_navigation():
    target = {"role": "link", "name": "Book a Call", "css": "a.cta"}
    assert parameterize_click_target(target, "Book a Call", PH) == target


def test_parameterize_does_not_mutate_input():
    target = {"role": "radio", "name": "Mr", "css": "x"}
    parameterize_click_target(target, "Mr", PH)
    assert target == {"role": "radio", "name": "Mr", "css": "x"}
