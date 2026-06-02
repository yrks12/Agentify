"""Typer CLI: map -> call / run-mapped."""

import json as _json
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from . import registry as registry_mod
from .browser import Browser
from .llm import DEFAULT_MODEL, LLM
from .mapper import map_site
from .recipe import Engine, RecipeFailure

app = typer.Typer(
    add_completion=False,
    help="Generate a deterministic SDK for any website, then use it via an LLM.",
)
_console = Console()


def _load_env() -> None:
    # Load from cwd or project root, whichever exists.
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)
            return
    load_dotenv(override=False)


@app.command()
def map(
    url: Annotated[str, typer.Option("--url", help="Site URL to map.")],
    name: Annotated[str, typer.Option("--name", help="Slug for the registry file.")],
    headless: Annotated[bool, typer.Option("--headless/--no-headless")] = True,
    interactive: Annotated[bool, typer.Option("--interactive/--auto-approve")] = True,
    model: Annotated[str, typer.Option("--model")] = DEFAULT_MODEL,
) -> None:
    """Phase 1: crawl, propose tools, get user approval, record recipes."""
    _load_env()
    registry = map_site(
        url=url, slug=name, headless=headless, interactive=interactive,
        llm=LLM(model=model),
    )
    if not registry.tools:
        _console.print("[red]No tools recorded.[/]")
        raise typer.Exit(code=1)
    path = registry_mod.save(registry)
    _console.print(f"[green]wrote {path}[/]")
    _console.print(f"[green]tools: {[t.name for t in registry.tools]}[/]")


@app.command()
def call(
    site: Annotated[str, typer.Option("--site", help="Registry slug.")],
    tool: Annotated[str, typer.Option("--tool", help="Tool name.")],
    args: Annotated[str, typer.Option("--args", help="JSON args.")] = "{}",
    headless: Annotated[bool, typer.Option("--headless/--no-headless")] = True,
) -> None:
    """Phase 2 (direct): execute one mapped tool with explicit args. No LLM."""
    _load_env()
    reg = registry_mod.load(site)
    recipe = reg.find(tool)
    if not recipe:
        _console.print(f"[red]tool {tool!r} not in registry {site!r}[/]")
        _console.print(f"available: {[t.name for t in reg.tools]}")
        raise typer.Exit(code=1)
    try:
        parsed_args = _json.loads(args)
    except _json.JSONDecodeError as e:
        _console.print(f"[red]--args is not valid JSON: {e}[/]")
        raise typer.Exit(code=1)

    _console.rule(f"[bold cyan]call {site}.{tool}")
    _console.print(Panel(_json.dumps(parsed_args, indent=2), title="args", border_style="dim"))

    with Browser(headless=headless) as browser:
        engine = Engine(browser)
        try:
            result = engine.execute(recipe, parsed_args)
        except RecipeFailure as e:
            _console.print(f"[red]RecipeFailure at step {e.step_index}: {e.reason}[/]")
            raise typer.Exit(code=1)

    _console.print(
        Panel(
            Syntax(_json.dumps(result, indent=2, default=str), "json"),
            title="result",
            border_style="green",
        )
    )


_RUN_MAPPED_SYSTEM = """\
You are a tool-using assistant for the site {site}. You do NOT see the
page. You only see the tools below. Pick the tool(s) needed to complete
the user's task. Fill arguments from the user's task text. When the task
is complete (or impossible), call `done` with a summary.
"""


@app.command(name="run-mapped")
def run_mapped(
    site: Annotated[str, typer.Option("--site", help="Registry slug.")],
    task: Annotated[str, typer.Option("--task", help="Natural-language task.")],
    headless: Annotated[bool, typer.Option("--headless/--no-headless")] = True,
    max_calls: Annotated[int, typer.Option("--max-calls")] = 5,
    model: Annotated[str, typer.Option("--model")] = DEFAULT_MODEL,
) -> None:
    """Phase 2 (NL): LLM picks tools (never sees the page) and engine replays."""
    from rich.table import Table
    from rich import box
    from rich.text import Text
    import time

    _load_env()
    reg = registry_mod.load(site)
    if not reg.tools:
        _console.print(f"[red]registry {site!r} has no tools[/]")
        raise typer.Exit(code=1)

    llm = LLM(model=model)
    tools = registry_mod.to_openai_tools(reg)
    tools.append({
        "type": "function",
        "function": {
            "name": "done",
            "description": "Terminate when the task is complete or impossible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "summary": {"type": "string"},
                },
                "required": ["success", "summary"],
            },
        },
    })

    messages: list[dict] = [
        {"role": "system", "content": _RUN_MAPPED_SYSTEM.format(site=site)},
        {"role": "user", "content": f"TASK: {task}"},
    ]

    # Beautiful header banner
    header_text = Text()
    header_text.append("Agentify ", style="bold violet")
    header_text.append("* ", style="dim")
    header_text.append("Autonomous Mode", style="bold cyan")
    
    info_table = Table(box=box.ROUNDED, border_style="violet", show_header=False)
    info_table.add_row("[bold magenta]Target Site:[/]", f"[cyan]{site}[/]")
    info_table.add_row("[bold magenta]User Task:[/]", f"[white]{task}[/]")
    info_table.add_row("[bold magenta]Available Tools:[/]", f"[dim]{', '.join([t['function']['name'] for t in tools])}[/]")
    
    _console.print(Panel(info_table, title=header_text, border_style="violet", padding=(1, 2)))
    _console.print("[dim italic](i) The AI agent operates strictly on the JSON SDK schema and never downloads or sees the raw website content.[/]")
    _console.print()

    aggregated: dict = {}

    with Browser(headless=headless) as browser:
        engine = Engine(browser)
        for call_n in range(1, max_calls + 1):
            _console.print(f"[bold yellow]Step {call_n} - Thinking...[/]")
            start_time = time.time()
            
            resp = llm.client.chat.completions.create(
                model=llm.model,
                messages=messages,
                tools=tools,
                tool_choice="required",
            )
            choice = resp.choices[0].message
            tool_calls = choice.tool_calls or []
            if not tool_calls:
                _console.print("[yellow]Model returned no tool call; stopping.[/]")
                break
            tc = tool_calls[0]
            tool_name = tc.function.name
            try:
                tool_args = _json.loads(tc.function.arguments or "{}")
            except _json.JSONDecodeError:
                tool_args = {}

            llm_duration = time.time() - start_time
            
            # Print decision
            decision_text = Text()
            decision_text.append("Agent Decided: ", style="bold green")
            decision_text.append(tool_name, style="bold cyan")
            decision_text.append(f"({_json.dumps(tool_args)})", style="dim")
            decision_text.append(f" [dim]({llm_duration:.1f}s)[/]")
            _console.print(decision_text)

            if tool_name == "done":
                success = tool_args.get("success", False)
                status_emoji = "SUCCESS" if success else "FAILED"
                status_style = "bold green" if success else "bold red"
                
                # Render beautiful summary card
                summary_panel = Panel(
                    f"[white]{tool_args.get('summary', '')}[/]",
                    title=f"[{status_style}]{status_emoji}[/]",
                    border_style="green" if success else "red",
                    padding=(1, 2)
                )
                _console.print()
                _console.print(summary_panel)
                
                # Render cost and performance stats
                stats_table = Table(box=box.SIMPLE, show_header=False, border_style="dim")
                stats_table.add_row("[bold cyan]Total Steps Executed:[/]", f"{call_n}")
                stats_table.add_row("[bold cyan]Total LLM Tokens Used:[/]", f"{llm.total_tokens}")
                
                _console.print(Panel(stats_table, title="[bold violet]Execution Stats[/]", border_style="violet"))
                return

            recipe = reg.find(tool_name)
            if not recipe:
                tool_result = {"error": f"unknown tool {tool_name}"}
            else:
                _console.print(f"  [cyan]Replaying recipe '{tool_name}' ({len(recipe.steps)} steps)...[/]")
                exec_start = time.time()
                try:
                    tool_result = engine.execute(recipe, tool_args)
                    aggregated[tool_name] = tool_result
                    _console.print(f"  [green][OK] Recipe executed successfully in {time.time() - exec_start:.1f}s[/]")
                except RecipeFailure as e:
                    tool_result = {"error": f"RecipeFailure step {e.step_index}: {e.reason}"}
                    _console.print(f"  [red][ERR] Recipe failed at step {e.step_index}: {e.reason}[/]")

            # Print tool result (try to format beautifully as Table if it contains a list of dicts)
            results_data = None
            if isinstance(tool_result, dict) and "result" in tool_result:
                results_data = tool_result["result"]
            elif isinstance(tool_result, list):
                results_data = tool_result
                
            if isinstance(results_data, list) and len(results_data) > 0 and isinstance(results_data[0], dict):
                # We have a list of dicts! Let's format as a table.
                res_table = Table(box=box.MINIMAL_DOUBLE_HEAD, border_style="cyan")
                # Filter out URL columns to fit the table nicely in standard terminal widths
                headers = [h for h in list(results_data[0].keys()) if "url" not in h.lower()]
                for h in headers:
                    # Title-case headers for beauty
                    res_table.add_column(h.replace("_", " ").title(), style="cyan" if h == "title" else "white")
                for item in results_data:
                    row_vals = []
                    for h in headers:
                        val = item.get(h, "")
                        val_str = str(val)
                        if h == "title" and len(val_str) > 40:
                            val_str = val_str[:37] + "..."
                        elif len(val_str) > 30:
                            val_str = val_str[:27] + "..."
                        row_vals.append(val_str)
                    res_table.add_row(*row_vals)
                _console.print(Panel(res_table, title=f"[bold cyan]Result: {tool_name}[/]", border_style="cyan"))
            else:
                # Fallback to standard Syntax JSON panel
                _console.print(Panel(
                    Syntax(_json.dumps(tool_result, indent=2, default=str)[:1500], "json"),
                    title=f"Result: {tool_name}", border_style="cyan",
                ))
            _console.print()

            # Feed result back to the model.
            messages.append({
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [{
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": tc.function.arguments},
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _json.dumps(tool_result, default=str),
            })

    _console.print(f"[yellow]hit max_calls={max_calls}[/]")


if __name__ == "__main__":
    app()
