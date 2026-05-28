"""OpenAI integration: system prompt, tool schema, one-call-per-turn loop."""

import json
import os
from typing import Any

from openai import OpenAI

DEFAULT_MODEL = os.environ.get("AGENTIFY_MODEL", "gpt-5.4-mini")

SYSTEM_PROMPT = """\
You are Agentify, an AI that drives a web browser to complete tasks on \
behalf of a user. You can ONLY perceive the page through a numbered list of \
interactive elements (the "AX tree"), and you can ONLY act through the tool \
calls provided. There are no screenshots.

Each turn you receive:
- The current URL.
- The numbered AX tree, e.g. `[3] button "Sign in"`.
- A short history of recent actions and their outcomes.

Rules:
1. Emit exactly ONE tool call per turn. Do not write prose to the user — \
the only channel back is tool calls.
2. Only reference elements by ids that appear in the CURRENT AX tree. \
Ids change every step.
3. When the task is complete (or impossible), call `done` with a clear \
summary. If you collected information for the user, call `extract` for \
each piece BEFORE calling `done`.
4. Prefer the most specific action available. To submit a form after \
typing, set `press_enter=true` on `type_text` rather than hunting for a \
submit button.
5. If you don't see what you need, try `scroll`, or `wait` for an SPA \
to render. Avoid repeating the same action when it didn't change the page.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click an element by its AX-tree id.",
            "parameters": {
                "type": "object",
                "properties": {"element_id": {"type": "integer"}},
                "required": ["element_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type into a text input by its AX-tree id. Replaces existing value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "integer"},
                    "text": {"type": "string"},
                    "press_enter": {"type": "boolean", "default": False},
                },
                "required": ["element_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_option",
            "description": "Choose an option in a <select> by visible label or value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "integer"},
                    "value": {"type": "string"},
                },
                "required": ["element_id", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "top", "bottom"],
                    }
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait for an SPA / async UI to settle. Max 5000ms.",
            "parameters": {
                "type": "object",
                "properties": {"ms": {"type": "integer"}},
                "required": ["ms"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "goto",
            "description": "Navigate to a URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "go_back",
            "description": "Navigate back in history.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract",
            "description": (
                "Record a piece of information collected for the user. "
                "Has no side effect on the browser."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Terminate the task. Call only after the task is fully complete or proven impossible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "summary": {"type": "string"},
                },
                "required": ["success", "summary"],
            },
        },
    },
]


class LLM:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        self.model = model
        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0

    def next_action(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Return {'name': <tool name>, 'arguments': <dict>}.

        Forces exactly one tool call per turn.
        """
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOLS,
            tool_choice="required",
        )
        usage = resp.usage

        if usage:
           self.total_prompt_tokens += usage.prompt_tokens
           self.total_completion_tokens += usage.completion_tokens
           self.total_tokens += usage.total_tokens

        choice = resp.choices[0]
        tool_calls = choice.message.tool_calls or []
        if not tool_calls:
            return {
                "name": "done",
                "arguments": {
                    "success": False,
                    "summary": "model returned no tool call",
                },
                "raw_id": None,
            }
        call = tool_calls[0]
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        return {
            "name": call.function.name,
            "arguments": args,
            "raw_id": call.id,
            "raw_message": choice.message,
        }
