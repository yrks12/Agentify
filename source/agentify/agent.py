"""The observe -> think -> act loop."""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .browser import Browser, Observation
from .llm import LLM, SYSTEM_PROMPT
from .memory import Memory, Step


@dataclass
class RunResult:
    success: bool
    summary: str
    extracted: dict[str, Any]
    steps: int


@dataclass
class Agent:
    browser: Browser
    llm: LLM
    max_steps: int = 25
    on_step: Optional[Callable[[int, str, dict, str, Observation], None]] = None
    on_observation: Optional[Callable[[int, Observation], None]] = None

    def _build_messages(self, obs: Observation, memory: Memory) -> list[dict]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK:\n{memory.task}\n\n"
                    f"CURRENT OBSERVATION:\n{obs.text}\n\n"
                    f"RECENT HISTORY:\n{memory.format_history()}\n\n"
                    f"EXTRACTED SO FAR:\n{memory.extracted or '(empty)'}"
                ),
            },
        ]

    def run(self, task: str, start_url: str) -> RunResult:
        self.browser.goto(start_url)
        memory = Memory(task=task)

        for step_n in range(1, self.max_steps + 1):
            try:
                obs = self.browser.observe()
            except Exception as e:
                # Tell the model what went wrong instead of crashing.
                memory.add(Step(
                    n=step_n,
                    tool="observe_error",
                    arguments={},
                    outcome=f"{type(e).__name__}: {e}",
                ))
                continue
            if self.on_observation:
                self.on_observation(step_n, obs)

            messages = self._build_messages(obs, memory)
            action = self.llm.next_action(messages)
            name = action["name"]
            args = action["arguments"]

            outcome = self._dispatch(name, args, memory)
            memory.add(Step(n=step_n, tool=name, arguments=args, outcome=outcome))
            if self.on_step:
                self.on_step(step_n, name, args, outcome, obs)

            if name == "done":
                return RunResult(
                    success=bool(args.get("success", False)),
                    summary=str(args.get("summary", "")),
                    extracted=dict(memory.extracted),
                    steps=step_n,
                )

            if memory.repeated_too_often():
                return RunResult(
                    success=False,
                    summary=f"aborted: same action repeated {memory._repeat_count + 1}x",
                    extracted=dict(memory.extracted),
                    steps=step_n,
                )

        return RunResult(
            success=False,
            summary=f"hit max_steps={self.max_steps} without calling done",
            extracted=dict(memory.extracted),
            steps=self.max_steps,
        )

    def _dispatch(self, name: str, args: dict, memory: Memory) -> str:
        try:
            if name == "click":
                return self.browser.click(int(args["element_id"]))
            if name == "type_text":
                return self.browser.type_text(
                    int(args["element_id"]),
                    str(args.get("text", "")),
                    bool(args.get("press_enter", False)),
                )
            if name == "select_option":
                return self.browser.select_option(
                    int(args["element_id"]),
                    str(args.get("value", "")),
                )
            if name == "scroll":
                return self.browser.scroll(str(args.get("direction", "down")))
            if name == "wait":
                return self.browser.wait(int(args.get("ms", 500)))
            if name == "goto":
                self.browser.goto(str(args["url"]))
                return f"navigated to {args['url']}"
            if name == "go_back":
                self.browser.go_back()
                return "navigated back"
            if name == "extract":
                key = str(args.get("key", ""))
                value = args.get("value")
                memory.extracted[key] = value
                return f"recorded extract[{key!r}]"
            if name == "done":
                return f"done(success={args.get('success')})"
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"
        return f"unknown tool: {name}"
