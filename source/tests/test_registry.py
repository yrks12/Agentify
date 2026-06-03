"""Registry JSON roundtrip + OpenAI tool conversion."""


from agentify.recipe import Recipe
from agentify.registry import (
    AuthConfig,
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


def test_auth_roundtrip(tmp_path):
    reg = SiteRegistry(
        site="acme",
        base_url="https://acme.test",
        tools=[
            Recipe(
                name="login",
                description="Sign in.",
                parameters={
                    "type": "object",
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                    },
                    "required": ["username", "password"],
                },
                steps=[
                    {"op": "type", "target": {"role": "textbox", "name": "User"}, "text": "{{username}}"},
                    {"op": "type", "target": {"role": "textbox", "name": "Pass"}, "text": "{{password}}"},
                    {"op": "verify", "kind": "element_exists", "target": {"role": "link", "name": "Log out"}},
                ],
            )
        ],
        auth=AuthConfig(
            login_tool="login",
            check={"kind": "element_exists", "target": {"role": "link", "name": "Log out"}},
            storage_state="sessions/acme.json",
        ),
    )
    save(reg, recipes_dir=tmp_path)
    loaded = load("acme", recipes_dir=tmp_path)

    assert loaded.auth is not None
    assert loaded.auth.type == "form_login"
    assert loaded.auth.login_tool == "login"
    assert loaded.auth.storage_state == "sessions/acme.json"
    assert loaded.auth.check["kind"] == "element_exists"
    # The recipe parameterises credentials — no secret value is persisted.
    assert loaded.tools[0].steps[0]["text"] == "{{username}}"
    assert loaded.tools[0].steps[1]["text"] == "{{password}}"


def test_no_auth_key_when_absent(tmp_path):
    reg = SiteRegistry(site="plain", base_url="https://plain.test", tools=[])
    save(reg, recipes_dir=tmp_path)
    # `auth` must be omitted from JSON entirely for auth-free sites.
    raw = (tmp_path / "plain.tools.json").read_text()
    assert "\"auth\"" not in raw
    assert load("plain", recipes_dir=tmp_path).auth is None


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
