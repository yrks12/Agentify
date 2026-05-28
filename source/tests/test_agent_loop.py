"""Agent loop with mocked browser + LLM — asserts dispatch + termination."""

from dataclasses import dataclass, field
from typing import Any

from agentify.agent import Agent
from agentify.ax_tree import AXElement
from agentify.browser import Observation


class FakeBrowser:
    def __init__(self):
        self.actions: list[tuple[str, tuple, dict]] = []
        self.url = "https://example.com"

    def goto(self, url: str, wait_ms: int = 0):
        self.url = url
        self.actions.append(("goto", (url,), {}))

    def go_back(self):
        self.actions.append(("go_back", (), {}))

    def observe(self, max_elements: int = 80) -> Observation:
        els = [
            AXElement(id=1, role="textbox", name="Search"),
            AXElement(id=2, role="button", name="Go"),
        ]
        return Observation(
            url=self.url,
            elements=els,
            truncated=False,
            text=f"URL: {self.url}\n[1] textbox 'Search'\n[2] button 'Go'",
        )

    def click(self, element_id: int) -> str:
        self.actions.append(("click", (element_id,), {}))
        return f"clicked [{element_id}]"

    def type_text(self, element_id: int, text: str, press_enter: bool = False) -> str:
        self.actions.append(("type_text", (element_id, text, press_enter), {}))
        return f"typed into [{element_id}]"

    def select_option(self, element_id: int, value: str) -> str:
        self.actions.append(("select_option", (element_id, value), {}))
        return "selected"

    def scroll(self, direction: str) -> str:
        self.actions.append(("scroll", (direction,), {}))
        return f"scrolled {direction}"

    def wait(self, ms: int) -> str:
        self.actions.append(("wait", (ms,), {}))
        return f"waited {ms}ms"

    def press_key(self, key: str) -> str:
        self.actions.append(("press_key", (key,), {}))
        return f"pressed {key}"


@dataclass
class FakeLLM:
    """Yields a scripted sequence of tool calls."""

    script: list[dict[str, Any]] = field(default_factory=list)
    _idx: int = 0
    seen_messages: list[list[dict]] = field(default_factory=list)

    def next_action(self, messages):
        self.seen_messages.append(messages)
        if self._idx >= len(self.script):
            return {"name": "done", "arguments": {"success": False, "summary": "script exhausted"}}
        call = self.script[self._idx]
        self._idx += 1
        return call


def test_agent_completes_when_done_called():
    browser = FakeBrowser()
    llm = FakeLLM(script=[
        {"name": "type_text", "arguments": {"element_id": 1, "text": "cats", "press_enter": True}},
        {"name": "click", "arguments": {"element_id": 2}},
        {"name": "extract", "arguments": {"key": "result", "value": "ok"}},
        {"name": "done", "arguments": {"success": True, "summary": "done it"}},
    ])
    agent = Agent(browser=browser, llm=llm, max_steps=10)  # type: ignore[arg-type]
    result = agent.run(task="search for cats", start_url="https://example.com")

    assert result.success is True
    assert result.steps == 4
    assert result.extracted == {"result": "ok"}
    # type_text + click reached the browser
    assert ("type_text", (1, "cats", True), {}) in browser.actions
    assert ("click", (2,), {}) in browser.actions


def test_agent_aborts_on_repeated_action():
    browser = FakeBrowser()
    # Same click 4 times -> guard triggers after 3 repeats (count >= 3 means 4 total).
    llm = FakeLLM(script=[{"name": "click", "arguments": {"element_id": 2}}] * 6)
    agent = Agent(browser=browser, llm=llm, max_steps=10)  # type: ignore[arg-type]
    result = agent.run(task="x", start_url="https://example.com")
    assert result.success is False
    assert "repeated" in result.summary


def test_agent_hits_max_steps():
    browser = FakeBrowser()
    # Loop of safe actions that don't trigger the repetition guard.
    llm = FakeLLM(script=[
        {"name": "scroll", "arguments": {"direction": "down"}},
        {"name": "wait", "arguments": {"ms": 100}},
    ] * 5)
    agent = Agent(browser=browser, llm=llm, max_steps=3)  # type: ignore[arg-type]
    result = agent.run(task="x", start_url="https://example.com")
    assert result.success is False
    assert "max_steps" in result.summary
    assert result.steps == 3
