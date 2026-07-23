"""`clone-demo` - the rep's entire interface.

Two commands do the work:

    clone-demo new https://brightsmiledental.com --prospect brightsmile
    clone-demo push brightsmile

`new` scrapes, extracts, writes `prospects/<id>.yaml`, and **stops**. The gate is
deliberate: scraped insurance lists are wrong often enough that one unreviewed
clone naming a carrier the practice dropped costs more than every hour the gate
ever saves. Nothing is provisioned until the rep runs `push`.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.panel import Panel
from rich.table import Table

from clone.console import console
from clone.extract import ExtractionError, extract_profile
from clone.kb_builder import render_knowledge_base, render_prompt, render_tools
from clone.profile import PracticeProfile
from clone.push import PushError, push_prospect
from clone.retell import RetellClient, RetellError
from clone.scrape import scrape_site
from clone.settings import PROSPECTS_DIR

app = typer.Typer(
    add_completion=False,
    help="Clone a prospect-branded dental demo agent from their website.",
    no_args_is_help=True,
)


def _path_for(prospect_id: str) -> Path:
    return PROSPECTS_DIR / f"{prospect_id}.yaml"


def _load(prospect_id: str) -> PracticeProfile:
    path = _path_for(prospect_id)
    if not path.is_file():
        console.print(f"[red]No profile at {path}.[/] Run `clone-demo list` to see what exists.")
        raise typer.Exit(1)
    return PracticeProfile.from_yaml(path.read_text(encoding="utf-8"))


def _save(profile: PracticeProfile) -> Path:
    PROSPECTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(profile.prospect_id)
    path.write_text(profile.to_yaml(), encoding="utf-8")
    return path


@app.command()
def new(
    url: str = typer.Argument(..., help="The prospect's website, e.g. brightsmiledental.com"),
    prospect: str = typer.Option(..., "--prospect", "-p", help="Short slug, e.g. brightsmile"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing profile."),
) -> None:
    """Scrape a practice site, extract a profile, and stop for review."""
    path = _path_for(prospect)
    if path.exists() and not force:
        console.print(f"[yellow]{path} already exists.[/] Re-run with --force to overwrite.")
        raise typer.Exit(1)

    with console.status(f"Reading {url} (60s budget)…"):
        scrape = scrape_site(url)
    console.print(f"Read [bold]{len(scrape.pages)}[/] page(s).")
    for err in scrape.errors:
        console.print(f"  [dim]skipped: {err}[/]")

    try:
        with console.status("Extracting the practice profile…"):
            profile = extract_profile(scrape, prospect)
    except ExtractionError as exc:
        console.print(f"[red]Extraction failed:[/] {exc}")
        console.print("Write the YAML by hand from `prospects/_showcase.yaml` and push that.")
        raise typer.Exit(1) from exc

    saved = _save(profile)
    _print_review(profile, saved)


def _print_review(profile: PracticeProfile, path: Path) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("Practice", profile.practice_name)
    table.add_row("Providers", ", ".join(p.name for p in profile.providers) or "[dim]none[/]")
    table.add_row(
        "Insurance",
        ", ".join(profile.insurance_accepted) or "[dim]none found[/]",
    )
    table.add_row("Services", ", ".join(profile.services) or "[dim]none found[/]")
    table.add_row("Open days", ", ".join(profile.hours.open_days()) or "[dim]none found[/]")
    table.add_row("Accent", profile.accent_color or "[dim]default[/]")
    console.print(Panel(table, title=f"[bold]{path.name}[/]", border_style="cyan"))

    console.print(
        "\n[bold yellow]Review before pushing.[/] Open the file and check the insurance "
        "list against what the practice actually accepts — that list is the one the "
        "prospect will fact-check live.\n"
    )
    console.print(f"  1. edit  [cyan]{path}[/]")
    console.print(f"  2. run   [cyan]clone-demo push {profile.prospect_id}[/]\n")


@app.command()
def push(
    prospect: str = typer.Argument(..., help="The prospect slug."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the payloads, send nothing."),
    skip_deploy: bool = typer.Option(False, "--skip-deploy", help="Provision the agent only."),
) -> None:
    """Create the agent, upload the KB, provision a number, deploy the page."""
    profile = _load(prospect)
    try:
        result = push_prospect(profile, dry_run=dry_run, skip_deploy=skip_deploy)
    except (PushError, RetellError) as exc:
        console.print(f"[red]Push failed:[/] {exc}")
        raise typer.Exit(1) from exc

    for step in result.steps:
        console.print(f"  [green]•[/] {step}")

    if not dry_run:
        _save(profile)
        console.print(
            Panel(
                f"[bold]{profile.practice_name}[/] is live.\n\n"
                f"Call:  [bold cyan]{profile.demo_number or 'no number'}[/]\n"
                f"Page:  {profile.demo_page_url or '[dim]not deployed[/]'}",
                border_style="green",
            )
        )


@app.command()
def preview(
    prospect: str = typer.Argument(...),
    what: str = typer.Option("prompt", "--what", help="prompt | kb | tools"),
) -> None:
    """Print exactly what this clone will say, without provisioning anything."""
    profile = _load(prospect)
    if what == "prompt":
        console.print(render_prompt(profile))
    elif what == "kb":
        console.print(render_knowledge_base(profile))
    elif what == "tools":
        for tool in render_tools():
            console.print(f"[bold]{tool['name']}[/] → {tool.get('url', tool['type'])}")
    else:
        console.print("[red]--what must be prompt, kb, or tools[/]")
        raise typer.Exit(1)


@app.command("list")
def list_prospects() -> None:
    """Show every clone and whether it is live."""
    PROSPECTS_DIR.mkdir(parents=True, exist_ok=True)
    table = Table("prospect", "practice", "number", "status")
    for path in sorted(PROSPECTS_DIR.glob("*.yaml")):
        try:
            profile = PracticeProfile.from_yaml(path.read_text(encoding="utf-8"))
        except Exception as exc:
            table.add_row(path.stem, f"[red]unreadable[/] ({type(exc).__name__})", "", "")
            continue
        live = "[green]live[/]" if profile.retell_agent_id else "[yellow]not pushed[/]"
        table.add_row(profile.prospect_id, profile.practice_name, profile.demo_number or "—", live)
    console.print(table)


@app.command()
def kill(prospect: str = typer.Argument(...)) -> None:
    """Take a clone offline. The number stops answering as the agent."""
    profile = _load(prospect)
    if not profile.demo_number:
        console.print("[yellow]No number on this profile — nothing to take offline.[/]")
        raise typer.Exit(1)
    try:
        with RetellClient() as client:
            client.take_offline(profile.demo_number)
    except RetellError as exc:
        console.print(f"[red]Could not take it offline:[/] {exc}")
        console.print("Fall back to the Retell dashboard: Phone Numbers → unbind the agent.")
        raise typer.Exit(1) from exc
    console.print(f"[green]{profile.demo_number} is offline.[/] Re-push to bring it back.")


if __name__ == "__main__":
    app()
