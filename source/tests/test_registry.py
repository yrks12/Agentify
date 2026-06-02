"""Registry JSON roundtrip + OpenAI tool conversion."""


from agentify.recipe import Recipe
from agentify.registry import (
    SiteRegistry,
    load,
    save,
    to_openai_tools,
)


def test_roundtrip(tmp_path):
    reg = SiteRegistry(
        site="example",
        base_url="https://example.com",
        tools=[
            Recipe(
                name="submit_form",
                description="Submit the contact form.",
                parameters={
                    "type": "object",
                    "properties": {"email": {"type": "string"}},
                    "required": ["email"],
                },
                steps=[
                    {"op": "goto", "url": "https://example.com/#contact"},
                    {"op": "type", "target": {"role": "textbox", "name": "Email"}, "text": "{{email}}"},
                ],
                returns={},
            )
        ],
    )
    save(reg, recipes_dir=tmp_path)
    loaded = load("example", recipes_dir=tmp_path)
    assert loaded.site == "example"
    assert loaded.base_url == "https://example.com"
    assert len(loaded.tools) == 1
    assert loaded.tools[0].name == "submit_form"
    assert loaded.tools[0].steps[1]["text"] == "{{email}}"


def test_find_by_name():
    reg = SiteRegistry(site="x", base_url="https://x.com", tools=[
        Recipe(name="a", description="", parameters={}, steps=[]),
        Recipe(name="b", description="", parameters={}, steps=[]),
    ])
    assert reg.find("a").name == "a"
    assert reg.find("missing") is None


def test_to_openai_tools():
    reg = SiteRegistry(site="x", base_url="", tools=[
        Recipe(
            name="search",
            description="Search the site",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
            steps=[],
        ),
    ])
    tools = to_openai_tools(reg)
    assert len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "search"
    assert tools[0]["function"]["parameters"]["required"] == ["q"]
