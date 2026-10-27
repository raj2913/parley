import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from parley.config import get_settings

app = typer.Typer(help="Parley: a policy-bound collections agent.", no_args_is_help=True)
db_app = typer.Typer(help="Database setup.", no_args_is_help=True)
eval_app = typer.Typer(help="Release-gate evals.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(eval_app, name="eval")

console = Console()


def _database():
    from parley.db import Database

    return Database(get_settings().database_url)


def _agent(db):
    from parley.agent import Agent
    from parley.llm import make_model_client
    from parley.policy import load_policy

    settings = get_settings()
    return Agent(db, make_model_client(settings), load_policy(settings.policy_path), settings)


@db_app.command("init")
def db_init(reset: bool = typer.Option(False, help="Drop all tables first.")) -> None:
    """Create tables and seed the demo accounts."""
    from parley.seed import seed_demo_accounts

    async def go() -> None:
        db = _database()
        if reset:
            await db.drop_all()
        await db.create_all()
        created = await seed_demo_accounts(db, reset=True)
        await db.dispose()
        console.print(f"[green]Database ready[/] ({created} demo accounts created)")

    asyncio.run(go())


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run("parley.app:app", host=host, port=port, reload=reload)


def _print_run(run, results) -> None:
    table = Table(title=f"Eval run {run.id[:8]}  ({run.kind})", show_lines=False)
    table.add_column("scenario")
    table.add_column("result")
    table.add_column("failed metrics")
    table.add_column("secs", justify="right")
    for r in results:
        failing = [
            f"{m['name']}: {m['reason']}" for m in r.metrics if m["gating"] and not m["success"]
        ]
        if r.error:
            failing.insert(0, r.error)
        table.add_row(
            r.scenario_id[:40],
            "[green]PASS[/]" if r.passed else "[red]FAIL[/]",
            "\n".join(failing)[:300],
            f"{r.duration_ms / 1000:.1f}",
        )
    console.print(table)
    s = run.summary
    colour = "green" if run.passed else "red"
    console.print(
        f"[{colour}]{'PASSED' if run.passed else 'FAILED'}[/] "
        f"{s['passed']}/{s['total']} calls  ·  policy {run.policy_version}  ·  "
        f"prompt {run.agent_prompt_ref}  ·  model {run.agent_model}"
    )


@eval_app.command("run")
def eval_run(
    tag: list[str] = typer.Option(None, help="Only scenarios with this tag (repeatable)."),
    scenario: list[str] = typer.Option(None, help="Only these scenario ids (repeatable)."),
    label: str = typer.Option(None, help="Label stored with the run."),
    judges: bool = typer.Option(True, help="Run the LLM-judged metrics."),
    concurrency: int = typer.Option(
        None, min=1, max=16, help="Parallel calls (default: 2 on Gemini's free tier, else 4)."
    ),
) -> None:
    """Run the scenario suite. Exits non-zero if any gating metric fails."""
    from parley.evals.runner import EvalRunner
    from parley.evals.scenarios import load_scenarios

    settings = get_settings()
    concurrency = concurrency or (2 if settings.provider == "gemini" else 4)
    scenarios = load_scenarios(settings.scenarios_dir, tags=tag or None)
    if scenario:
        scenarios = [s for s in scenarios if s.id in scenario]
    if not scenarios:
        console.print("[yellow]No scenarios selected.[/]")
        raise typer.Exit(2)
    console.print(f"Running {len(scenarios)} scenario(s)…")

    async def go():
        db = _database()
        await db.create_all()
        runner = EvalRunner(db, _agent(db), settings, judges=judges, concurrency=concurrency)
        run, results = await runner.run_suite(scenarios, label=label)
        await db.dispose()
        return run, results

    run, results = asyncio.run(go())
    _print_run(run, results)
    raise typer.Exit(0 if run.passed else 1)


@eval_app.command("replay")
def eval_replay(
    conversation: list[str] = typer.Option(None, help="Conversation ids to replay."),
    last: int = typer.Option(20, help="Otherwise, replay the most recent N ended calls."),
    label: str = typer.Option(None),
    judges: bool = typer.Option(True),
    concurrency: int = typer.Option(4, min=1, max=16),
) -> None:
    """Replay recorded calls against the current agent and run the release checks."""
    from parley.evals.runner import EvalRunner, replayable_conversations

    settings = get_settings()

    async def go():
        db = _database()
        ids = conversation or await replayable_conversations(db, last)
        if not ids:
            await db.dispose()
            return None, []
        console.print(f"Replaying {len(ids)} call(s)…")
        runner = EvalRunner(db, _agent(db), settings, judges=judges, concurrency=concurrency)
        run, results = await runner.run_replay(ids, label=label)
        await db.dispose()
        return run, results

    run, results = asyncio.run(go())
    if run is None:
        console.print("[yellow]No recorded calls to replay yet.[/]")
        raise typer.Exit(0)
    _print_run(run, results)
    raise typer.Exit(0 if run.passed else 1)


@eval_app.command("capture")
def eval_capture(
    conversation_id: str,
    scenario_id: str = typer.Option(..., "--id", help="Id for the new scenario."),
    tag: list[str] = typer.Option(None),
) -> None:
    """Turn a recorded call into a scripted regression scenario."""
    from parley.evals.runner import capture_scenario

    settings = get_settings()

    async def go() -> Path:
        db = _database()
        try:
            return await capture_scenario(
                db, conversation_id, scenario_id, settings.scenarios_dir, tag or []
            )
        finally:
            await db.dispose()

    path = asyncio.run(go())
    console.print(f"[green]Wrote[/] {path}. Edit its description and expectations, then commit it.")


@eval_app.command("list")
def eval_list() -> None:
    """List scenarios in the suite."""
    from parley.evals.scenarios import load_scenarios

    table = Table()
    for col in ("id", "caller", "tags", "description"):
        table.add_column(col)
    for s in load_scenarios(get_settings().scenarios_dir):
        table.add_row(s.id, s.caller.mode, ", ".join(s.tags), s.description[:70])
    console.print(table)
