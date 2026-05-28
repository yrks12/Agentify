"""Rolling step history + extracted-data accumulator."""

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Step:
    n: int
    tool: str
    arguments: dict[str, Any]
    outcome: str  # short, plain text


@dataclass
class Memory:
    task: str
    history: deque[Step] = field(default_factory=lambda: deque(maxlen=10))
    extracted: dict[str, Any] = field(default_factory=dict)
    _last_action_key: tuple[str, Any] | None = None
    _repeat_count: int = 0

    def add(self, step: Step) -> None:
        self.history.append(step)
        key = (step.tool, step.arguments.get("element_id"))
        if key == self._last_action_key and step.tool not in ("extract", "wait", "scroll"):
            self._repeat_count += 1
        else:
            self._repeat_count = 0
            self._last_action_key = key

    def repeated_too_often(self, limit: int = 3) -> bool:
        return self._repeat_count >= limit

    def format_history(self) -> str:
        if not self.history:
            return "(no actions taken yet)"
        lines = []
        for s in self.history:
            args_short = ", ".join(f"{k}={v!r}" for k, v in s.arguments.items())
            lines.append(f"step {s.n}: {s.tool}({args_short}) -> {s.outcome}")
        return "\n".join(lines)
