from __future__ import annotations

import json
import sys
import textwrap
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, NoReturn

import click
import typer

from . import __version__
from .bench import BenchOutcome, format_report, run_bench
from .checks import RULES, Checker, Config, FileResult, rule_help
from .config import ConfigError, load_config, merge_config
from .diffmode import GitError, changed_lines, filter_results
from .env import Environment, EnvNotFound, EnvUnusable, discover
from .index import ApiCache, ApiIndex
from .report import Report, render
from .runtime import IMPORT_WARNING
from .walk import collect_files

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Find hallucinated or outdated library API usage by checking code against the "
        "library versions actually installed in the target environment."
    ),
)
cache_app = typer.Typer(help="Manage the on-disk API cache.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3

#: Typer swallows ``Exit`` codes when run with ``standalone_mode=False``, so the
#: commands record the intended exit status here and ``main`` honours it.
_EXIT_CODE = {"value": EXIT_CLEAN}


def _exit(code: int) -> NoReturn:
    """Record the exit status and stop the current command."""
    _EXIT_CODE["value"] = code
    raise typer.Exit(code)


class _CliError(Exception):
    """An error that should reach the user as a clean message plus exit code."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


#: Names of click/typer exception classes, by name rather than by import: typer
#: vendors its own copy of click (``typer._click``), so ``isinstance`` against
#: ``click.ClickException`` misses ``NoArgsIsHelpError`` and friends.
_CLICK_ERROR_NAMES = frozenset({"ClickException", "UsageError", "NoArgsIsHelpError", "Abort"})


def _is_click_error(exc: BaseException) -> bool:
    return any(cls.__name__ in _CLICK_ERROR_NAMES for cls in type(exc).__mro__)


def _fail(message: str, code: int) -> None:
    typer.secho(f"ghostcall: {message}", fg=typer.colors.RED, err=True)
    _exit(code)


def _load_env(config: Config) -> Environment:
    try:
        return discover(
            config.python,
            source_roots=config.source_roots,
            extra_search_paths=config.extra_search_paths,
            start=config.root,
        )
    except EnvNotFound as exc:
        _fail(
            f"{exc}\n"
            "  hint: pass --python /path/to/venv/bin/python, or run ghostcall from the "
            "project whose environment should be checked.",
            EXIT_ENVIRONMENT,
        )
    except EnvUnusable as exc:
        _fail(f"{exc}", EXIT_ENVIRONMENT)
    raise AssertionError("unreachable")


def _build_config(
    *,
    config_path: Path | None,
    python: str | None,
    format: str,
    select: Sequence[str],
    ignore: Sequence[str],
    include_medium: bool,
    allow_import: bool,
    verbose: bool,
    jobs: int,
    no_cache: bool,
    ignore_modules: Sequence[str],
    source_roots: Sequence[str],
    extra_search_paths: Sequence[str],
    full_signature: bool,
) -> Config:
    try:
        file_config = load_config(config_path)
    except ConfigError as exc:
        _fail(str(exc), EXIT_USAGE)
    for code in [*select, *ignore, *file_config.select, *file_config.ignore]:
        if code.upper() not in RULES:
            _fail(
                f"unknown rule {code!r}; run 'ghostcall explain' to list the rules",
                EXIT_USAGE,
            )
    return merge_config(
        file_config,
        python=python,
        format=format,
        select=select,
        ignore=ignore,
        include_medium=include_medium,
        allow_import=allow_import,
        verbose=verbose,
        jobs=jobs,
        use_cache=not no_cache,
        ignore_modules=ignore_modules,
        source_roots=source_roots,
        extra_search_paths=extra_search_paths,
        full_signature=full_signature,
    )


@app.command()
def check(
    paths: Annotated[
        list[str] | None,
        typer.Argument(
            help="Files or directories to scan.  Use '-' to read a single file from stdin."
        ),
    ] = None,
    python: Annotated[
        str | None, typer.Option("--python", help="Interpreter of the target environment.")
    ] = None,
    format: Annotated[
        str, typer.Option("--format", help="Output format: text, json, sarif or github.")
    ] = "text",
    select: Annotated[
        list[str] | None, typer.Option("--select", help="Only report these rule codes.")
    ] = None,
    ignore: Annotated[
        list[str] | None, typer.Option("--ignore", help="Never report these rule codes.")
    ] = None,
    include_medium: Annotated[
        bool, typer.Option("--include-medium", help="Also report medium-confidence findings.")
    ] = False,
    allow_import: Annotated[
        bool,
        typer.Option(
            "--allow-import",
            help="Allow runtime introspection (imports the analysed libraries) as a fallback.",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="List unresolved expressions with their reasons.")
    ] = False,
    jobs: Annotated[
        int, typer.Option("--jobs", help="Number of files to analyse in parallel.")
    ] = 1,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Ignore the on-disk API cache.")
    ] = False,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to a pyproject.toml with config.")
    ] = None,
    diff: Annotated[
        str | None,
        typer.Option("--diff", help="Only report findings on lines changed vs this git ref."),
    ] = None,
    staged: Annotated[
        bool,
        typer.Option(
            "--staged", help="Compare the staged index instead (against --diff, default HEAD)."
        ),
    ] = False,
    stdin_filename: Annotated[
        str, typer.Option("--stdin-filename", help="Filename used for stdin input.")
    ] = "-",
    full_signature: Annotated[
        bool, typer.Option("--full-signature", help="Print signatures without truncation.")
    ] = False,
    output_path: Annotated[
        Path | None,
        typer.Option("--output", help="Write the report to a file (for --format sarif/json)."),
    ] = None,
    ignore_modules: Annotated[
        list[str] | None, typer.Option("--ignore-modules", help="Never check these packages.")
    ] = None,
    source_roots: Annotated[
        list[str] | None, typer.Option("--source-roots", help="Project source roots.")
    ] = None,
    extra_search_paths: Annotated[
        list[str] | None, typer.Option("--extra-search-paths", help="Extra module search paths.")
    ] = None,
) -> None:
    """Scan Python files against the target environment."""
    if format not in {"text", "json", "sarif", "github"}:
        _fail(f"unknown --format {format!r}", EXIT_USAGE)
    if staged and not diff:
        diff = "HEAD"
    if jobs < 1:
        _fail("--jobs must be at least 1", EXIT_USAGE)

    config = _build_config(
        config_path=config_path,
        python=python,
        format=format,
        select=select or [],
        ignore=ignore or [],
        include_medium=include_medium,
        allow_import=allow_import,
        verbose=verbose,
        jobs=jobs,
        no_cache=no_cache,
        ignore_modules=ignore_modules or [],
        source_roots=source_roots or [],
        extra_search_paths=extra_search_paths or [],
        full_signature=full_signature,
    )
    if allow_import:
        typer.secho(IMPORT_WARNING, fg=typer.colors.YELLOW, err=True)

    targets = list(paths or [])
    use_stdin = "-" in targets
    if use_stdin:
        targets = [item for item in targets if item != "-"]
    if not targets and not use_stdin:
        targets = ["."]

    environment = _load_env(config)
    cache = ApiCache() if config.use_cache else None
    index = ApiIndex(
        environment,
        cache=cache,
        allow_import=config.allow_import,
        ignore_modules=config.ignore_modules,
    )
    checker = Checker(environment, config, index=index)

    results: list[FileResult] = []
    if use_stdin:
        source = sys.stdin.read()
        results.append(checker.check_source(source, filename=stdin_filename))
    if targets:
        files = collect_files(targets, respect_gitignore=True)
        if config.jobs > 1 and len(files) > 1:
            with ThreadPoolExecutor(max_workers=config.jobs) as pool:
                results.extend(pool.map(checker.check_file, files))
        else:
            results.extend(checker.check_file(path) for path in files)

    diff_description = None
    if diff:
        try:
            diff_index = changed_lines(diff, staged=staged, root=config.root)
        except GitError as exc:
            _fail(str(exc), EXIT_USAGE)
        dropped = filter_results(results, diff_index)
        diff_description = diff_index.description
        if config.verbose and dropped:
            typer.secho(f"ghostcall: {dropped} finding(s) outside the diff hidden", err=True)

    report = Report(
        results=results,
        env=environment,
        verbose=config.verbose,
        files_scanned=len(results),
        version=__version__,
        diff_description=diff_description,
        full_signature=config.full_signature,
    )
    output = render(report, config.format)
    text = output if output.endswith("\n") else output + "\n"
    if output_path is not None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            _fail(f"could not write {output_path}: {exc}", EXIT_USAGE)
        typer.secho(f"ghostcall: report written to {output_path}", err=True)
    elif output:
        sys.stdout.write(text)

    if report.findings:
        _exit(EXIT_FINDINGS)
    _exit(EXIT_CLEAN)


@app.command()
def explain(
    code: Annotated[str | None, typer.Argument(help="Rule code, e.g. GC004.")] = None,
) -> None:
    """Show documentation for one rule, or list every rule."""
    if code is None:
        _print_rule_table()
        _exit(EXIT_CLEAN)
    doc = rule_help(code)
    if doc is None:
        _fail(f"unknown rule {code!r}; run 'ghostcall explain' to list rules", EXIT_USAGE)
    assert doc is not None
    typer.echo(f"{doc.code}: {doc.title} [{doc.severity.value}]")
    typer.echo("")
    typer.echo(doc.summary)
    typer.echo("")
    typer.echo(f"Skipped when: {doc.when_skipped}")
    if doc.example_bad:
        typer.echo("")
        typer.echo("Reported:")
        for line in doc.example_bad.splitlines():
            typer.echo(f"    {line}")
    if doc.example_good:
        typer.echo("")
        typer.echo("Not reported:")
        for line in doc.example_good.splitlines():
            typer.echo(f"    {line}")
    _exit(EXIT_CLEAN)


@app.command()
def info(
    symbol: Annotated[str, typer.Argument(help="Dotted API path, e.g. pandas.read_csv.")],
    python: Annotated[
        str | None, typer.Option("--python", help="Interpreter of the target environment.")
    ] = None,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to a pyproject.toml with config.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Machine readable output.")] = False,
    show_overloads: Annotated[
        bool, typer.Option("--overloads", help="Print every overload, not just a count.")
    ] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Ignore the API cache.")] = False,
) -> None:
    """Print the resolved signature and version of an API, from the target env."""
    config = _build_config(
        config_path=config_path,
        python=python,
        format="text",
        select=[],
        ignore=[],
        include_medium=True,
        allow_import=False,
        verbose=True,
        jobs=1,
        no_cache=no_cache,
        ignore_modules=[],
        source_roots=[],
        extra_search_paths=[],
        full_signature=True,
    )
    environment = _load_env(config)
    index = ApiIndex(environment, cache=ApiCache() if config.use_cache else None)
    chain = index.walk_chain(symbol)
    from .index import ChainStatus

    payload: dict[str, object] = {"symbol": symbol, "status": chain.status.value}
    if chain.status is ChainStatus.UNKNOWN:
        payload["reason"] = chain.reason
    elif chain.status is ChainStatus.MISSING:
        payload["missing"] = f"{chain.missing_parent}.{chain.missing_name}"
        payload["certain"] = chain.certain
    else:
        node = chain.node
        payload["defined"] = chain.defined
        payload["kind"] = node.kind.value if node else "module"
        payload["installed"] = environment.label_for(chain.entry.name) if chain.entry else ""
        if node is not None and node.signature is not None:
            arguments = node.signature.render(full=True)
            payload["signature"] = f"{chain.display.split('.')[-1]}({arguments})"
        if node is not None and node.overloads:
            payload["overloads"] = [
                f"{chain.display.split('.')[-1]}({sig.render(full=True)})" for sig in node.overloads
            ]
        if node is not None and node.deprecated:
            payload["deprecated"] = node.deprecated
        if chain.hops:
            payload["aliases"] = chain.hops
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        _exit(EXIT_CLEAN)
    _print_symbol(payload, show_overloads=show_overloads)
    _exit(EXIT_CLEAN)


#: How many overloads ``ghostcall info`` prints before summarising the rest.
_INFO_OVERLOAD_LIMIT = 3


def _print_symbol(payload: Mapping[str, object], *, show_overloads: bool) -> None:
    """Print ``ghostcall info`` output, wrapped so signatures stay readable."""
    plain = ("symbol", "status", "missing", "certain", "defined", "kind", "installed")
    for key in plain:
        if key in payload:
            typer.echo(f"{key}: {payload[key]}")
    for key in ("signature", "deprecated"):
        if key in payload:
            _echo_wrapped(key, str(payload[key]))
    overloads = payload.get("overloads")
    if isinstance(overloads, list) and overloads:
        if not show_overloads:
            # Overloaded C-level APIs have dozens of near-identical signatures;
            # printing them all buries the answer the user asked for.
            typer.echo(f"overloads: {len(overloads)} (--overloads to print them, --json for all)")
            return
        shown = overloads[:_INFO_OVERLOAD_LIMIT]
        for item in shown:
            _echo_wrapped("overloads", str(item))
        hidden = len(overloads) - len(shown)
        if hidden:
            typer.echo(f"overloads: ... and {hidden} more (use --json for all)")
    aliases = payload.get("aliases")
    if isinstance(aliases, list):
        for item in aliases:
            typer.echo(f"aliases: {item}")


def _echo_wrapped(key: str, value: str, width: int = 100) -> None:
    """``key: value`` with continuation lines indented under the value."""
    indent = " " * (len(key) + 2)
    chunks = textwrap.wrap(value, width=width - len(indent), break_long_words=False) or [""]
    typer.echo(f"{key}: {chunks[0]}")
    for chunk in chunks[1:]:
        typer.echo(f"{indent}{chunk}")


@cache_app.command("clear")
def cache_clear() -> None:
    """Delete every cached API index."""
    removed = ApiCache().clear()
    typer.echo(f"removed {removed} cached module index file(s)")
    _exit(EXIT_CLEAN)


@cache_app.command("path")
def cache_path() -> None:
    """Print the cache directory."""
    typer.echo(str(ApiCache().root))
    _exit(EXIT_CLEAN)


def _print_bench_table(outcome: BenchOutcome, include_medium: bool) -> None:
    from rich.console import Console
    from rich.table import Table

    confidence_label = "high+medium" if include_medium else "high"
    table = Table(title=f"ghostcall bench ({confidence_label} confidence)")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("cases", str(outcome.cases))
    table.add_row("clean files", str(outcome.clean_files))
    table.add_row("expected findings", str(outcome.expectations))
    table.add_row("true positives", str(outcome.true_positives))
    table.add_row("false negatives", str(outcome.false_negatives))
    table.add_row("false positives", str(outcome.false_positives))
    table.add_row("precision", f"{outcome.precision:.3f}")
    table.add_row("recall", f"{outcome.recall:.3f}")
    Console().print(table)


@app.command()
def bench(
    corpus: Annotated[
        Path | None,
        typer.Option("--corpus", help="Directory holding the benchmark corpora."),
    ] = None,
    python: Annotated[
        str | None, typer.Option("--python", help="Interpreter of the target environment.")
    ] = None,
    include_medium: Annotated[
        bool, typer.Option("--include-medium", help="Also count medium-confidence findings.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Machine readable output.")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Ignore the API cache.")] = False,
) -> None:
    """Run the labeled benchmark corpora and print precision/recall."""
    corpus_root = _find_corpus(corpus)
    config = _build_config(
        config_path=None,
        python=python,
        format="text",
        select=[],
        ignore=[],
        include_medium=include_medium,
        allow_import=False,
        verbose=False,
        jobs=1,
        no_cache=no_cache,
        ignore_modules=[],
        source_roots=[],
        extra_search_paths=[],
        full_signature=False,
    )
    environment = _load_env(config)
    outcome = run_bench(environment, corpus_root, include_medium=include_medium)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "cases": outcome.cases,
                    "clean_files": outcome.clean_files,
                    "expectations": outcome.expectations,
                    "true_positives": outcome.true_positives,
                    "false_negatives": outcome.false_negatives,
                    "false_positives": outcome.false_positives,
                    "precision": round(outcome.precision, 4),
                    "recall": round(outcome.recall, 4),
                    "recall_by_rule": {
                        rule: round(value, 4) for rule, value in outcome.recall_by_rule.items()
                    },
                    "misses": outcome.misses,
                    "false_positive_details": outcome.false_positive_details,
                },
                indent=2,
            )
        )
    else:
        _print_bench_table(outcome, include_medium)
        typer.echo(format_report(outcome, include_medium))
    if outcome.false_negatives or outcome.false_positives:
        _exit(EXIT_FINDINGS)
    _exit(EXIT_CLEAN)


def _print_rule_table() -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title="ghostcall rules", show_lines=False)
    table.add_column("code", style="bold")
    table.add_column("severity")
    table.add_column("title")
    for doc in RULES.values():
        table.add_row(doc.code, doc.severity.value, doc.title)
    Console().print(table)


def _find_corpus(explicit: Path | None) -> Path:
    if explicit is not None:
        if not (explicit / "hallucinations").is_dir() and not (explicit / "clean").is_dir():
            _fail(f"{explicit} does not look like a corpus directory", EXIT_USAGE)
        return explicit
    candidates = [Path.cwd(), *Path.cwd().parents, Path(__file__).resolve().parents[2]]
    for candidate in candidates:
        if (candidate / "bench" / "hallucinations").is_dir():
            return candidate / "bench"
    _fail("could not find the benchmark corpus; pass --corpus PATH", EXIT_USAGE)
    raise AssertionError("unreachable")


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", help="Show the ghostcall version and exit.")
    ] = False,
) -> None:
    """Entry point used by the ``ghostcall`` console script."""
    if version:
        typer.echo(f"ghostcall {__version__}")
        _exit(EXIT_CLEAN)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        _exit(EXIT_USAGE)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the CLI, mapping internal errors onto exit codes."""
    args = list(argv if argv is not None else sys.argv[1:])
    _EXIT_CODE["value"] = EXIT_CLEAN
    try:
        app(args, standalone_mode=False)
    except typer.Exit as exc:
        raise SystemExit(exc.exit_code or _EXIT_CODE["value"]) from None
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(EXIT_USAGE) from exc
    # The specific handlers must come before the generic ``Exception`` one, or
    # they are unreachable and these errors escape as tracebacks.
    except (ConfigError, GitError) as exc:
        typer.secho(f"ghostcall: {exc}", fg=typer.colors.RED, err=True)
        raise SystemExit(EXIT_USAGE) from exc
    except (EnvNotFound, EnvUnusable) as exc:
        typer.secho(f"ghostcall: {exc}", fg=typer.colors.RED, err=True)
        raise SystemExit(EXIT_ENVIRONMENT) from exc
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        if not _is_click_error(exc):
            raise
        show = getattr(exc, "show", None)
        if callable(show):
            show()
        raise SystemExit(EXIT_USAGE) from exc
    raise SystemExit(_EXIT_CODE["value"])


if __name__ == "__main__":
    main()
