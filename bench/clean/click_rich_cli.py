"""Valid click 8.1 + rich 13.9 code in the style of a real CLI."""

import json
from pathlib import Path
from typing import Any

import click
import rich
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

console = Console(stderr=True, force_terminal=False, width=100)
err_console = Console()


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, invoke_without_command=False)
@click.option("--verbose", "-v", count=True, help="increase verbosity")
@click.version_option(version="1.0.0", prog_name="clean-cli")
@click.pass_context
def cli(context: click.Context, verbose: int) -> None:
    """A well-formed click group."""
    context.ensure_object(dict)
    context.obj["verbose"] = verbose


@cli.command("scan")
@click.argument("paths", nargs=-1, type=click.Path(exists=False, path_type=Path))
@click.option("--format", "output_format", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.option("--limit", type=click.IntRange(min=1, max=100), default=10)
@click.option("--color/--no-color", default=None, help="force colour")
@click.option("--tag", multiple=True)
def scan(paths: tuple[Path, ...], output_format: str, limit: int, color: bool | None, tag: tuple[str, ...]) -> None:
    """Scan files and print a table."""
    click.echo(f"scanning {len(paths)} path(s)", nl=True, err=False, color=color)
    payload: dict[str, Any] = {"format": output_format, "limit": limit, "tags": list(tag)}
    if output_format == "json":
        click.echo(json.dumps(payload, indent=2))
    else:
        click.secho("ok", fg="green", bold=True, err=True)

    table = Table(title="results", show_lines=False, box=None)
    table.add_column("path", no_wrap=True)
    table.add_column("status", justify="right")
    table.add_row(Text("a.py", style="bold"), Text("clean", style="green"))
    console.print(table, soft_wrap=True)

    with Progress(TextColumn("[progress.description]{task.description}"), BarColumn(), TimeElapsedColumn()) as progress:
        task = progress.add_task(description="working", total=limit)
        while not progress.finished:
            progress.advance(task_id=task, advance=1)

    rich.print("[bold]done[/bold]", sep=" ", end="\n", flush=True, file=err_console.file)

    if not paths:
        raise click.ClickException("no paths given")
    if limit < 0:
        raise click.UsageError("negative limit")
    raise click.exceptions.Exit(0)


@cli.command("serve")
@click.option("--host", default="127.0.0.1", envvar="HOST")
@click.option("--port", type=click.IntRange(1, 65535), default=8000, metavar="PORT")
@click.option("--reload", is_flag=True, default=False)
def serve(host: str, port: int, reload: bool) -> None:
    """Start a server (not actually started in the benchmark)."""
    click.echo(f"{host}:{port} reload={reload}")


if __name__ == "__main__":
    cli(prog_name="clean-cli")
