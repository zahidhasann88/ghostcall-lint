"""End-to-end CLI behaviour: stdout, exit codes, formatters, stdin, config.

The CLI is driven as a subprocess, because the exit-code plumbing is exactly what
is being tested (typer with ``standalone_mode=False`` swallows ``typer.Exit``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import pytest

from tests.conftest import BENCH_PYTHON, REPO_ROOT, needs_env

pytestmark = needs_env

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3

BAD_SNIPPET = dedent(
    """\
    import pandas as pd
    import requests

    frame = pd.DataFrame({"a": [1]})
    frame.append({"a": 2})
    data = pd.read_csv("input.csv", skip_blank=True)
    response = requests.get()
    print(frame, data, response)
    """
)

CLEAN_SNIPPET = dedent(
    """\
    import pandas as pd

    frame = pd.DataFrame({"a": [1]})
    frame.map(str)
    print(frame.mean(numeric_only=True))
    """
)


@dataclass
class Run:
    """Result of one CLI invocation."""

    code: int
    stdout: str
    stderr: str


@pytest.fixture(scope="session")
def cli() -> object:
    def run(*arguments: str, stdin: str | None = None, cwd: Path | None = None) -> Run:
        completed = subprocess.run(
            [sys.executable, "-m", "ghostcall", *arguments],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=str(cwd or REPO_ROOT),
            timeout=600,
            check=False,
        )
        return Run(completed.returncode, completed.stdout, completed.stderr)

    return run


@pytest.fixture
def bad_file(tmp_path: Path) -> Path:
    path = tmp_path / "app.py"
    path.write_text(BAD_SNIPPET, encoding="utf-8")
    return path


@pytest.fixture
def clean_file(tmp_path: Path) -> Path:
    path = tmp_path / "clean.py"
    path.write_text(CLEAN_SNIPPET, encoding="utf-8")
    return path


def test_findings_exit_one(cli, bad_file: Path, tmp_path: Path) -> None:
    run = cli("check", "--python", str(BENCH_PYTHON), str(bad_file), cwd=tmp_path)
    assert run.code == EXIT_FINDINGS
    assert "GC004" in run.stdout
    assert "skip_blank_lines" in run.stdout


def test_clean_code_exits_zero(cli, clean_file: Path, tmp_path: Path) -> None:
    run = cli("check", "--python", str(BENCH_PYTHON), str(clean_file), cwd=tmp_path)
    assert run.code == EXIT_CLEAN, run.stdout + run.stderr
    assert "0 finding" in run.stdout or "no findings" in run.stdout.lower()


def test_unknown_rule_is_a_usage_error(cli, bad_file: Path, tmp_path: Path) -> None:
    run = cli(
        "check",
        "--python",
        str(BENCH_PYTHON),
        "--select",
        "GC999",
        str(bad_file),
        cwd=tmp_path,
    )
    assert run.code == EXIT_USAGE
    assert "GC999" in run.stderr + run.stdout


def test_missing_interpreter_is_an_environment_error(cli, bad_file: Path, tmp_path: Path) -> None:
    run = cli("check", "--python", str(tmp_path / "nope"), str(bad_file), cwd=tmp_path)
    assert run.code == EXIT_ENVIRONMENT
    assert run.stderr


def test_invalid_config_is_a_usage_error(cli, bad_file: Path, tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ghostcall]\nnot-a-real-key = 1\n", encoding="utf-8"
    )
    run = cli("check", "--python", str(BENCH_PYTHON), str(bad_file), cwd=tmp_path)
    assert run.code == EXIT_USAGE
    assert "not-a-real-key" in run.stderr


def test_missing_path_is_a_usage_error(cli, tmp_path: Path) -> None:
    run = cli("check", "--python", str(BENCH_PYTHON), cwd=tmp_path)
    assert run.code in (EXIT_CLEAN, EXIT_USAGE)


def test_stdin_mode_reports_findings_and_exits_one(cli, tmp_path: Path) -> None:
    run = cli(
        "check",
        "--python",
        str(BENCH_PYTHON),
        "--stdin-filename",
        "hook.py",
        "-",
        stdin=BAD_SNIPPET,
        cwd=tmp_path,
    )
    assert run.code == EXIT_FINDINGS
    assert "hook.py" in run.stdout
    assert "GC004" in run.stdout


def test_stdin_mode_accepts_clean_code(cli, tmp_path: Path) -> None:
    run = cli(
        "check",
        "--python",
        str(BENCH_PYTHON),
        "--stdin-filename",
        "hook.py",
        "-",
        stdin=CLEAN_SNIPPET,
        cwd=tmp_path,
    )
    assert run.code == EXIT_CLEAN, run.stdout + run.stderr


def test_json_format_is_machine_readable(cli, bad_file: Path, tmp_path: Path) -> None:
    run = cli(
        "check", "--python", str(BENCH_PYTHON), "--format", "json", str(bad_file), cwd=tmp_path
    )
    assert run.code == EXIT_FINDINGS
    payload = json.loads(run.stdout)
    findings = payload["findings"] if isinstance(payload, dict) else payload
    assert {finding["code"] for finding in findings} == {"GC004", "GC006"}
    sample = next(finding for finding in findings if finding["code"] == "GC004")
    assert sample["installed"] == "pandas 2.2.3"
    assert sample["suggestion"] == "skip_blank_lines"
    assert sample["line"] == 6


def test_sarif_format_is_valid_sarif(cli, bad_file: Path, tmp_path: Path) -> None:
    run = cli(
        "check", "--python", str(BENCH_PYTHON), "--format", "sarif", str(bad_file), cwd=tmp_path
    )
    payload = json.loads(run.stdout)
    assert payload["version"] == "2.1.0"
    run_object = payload["runs"][0]
    assert run_object["tool"]["driver"]["name"] == "ghostcall"
    rules = {rule["id"] for rule in run_object["tool"]["driver"]["rules"]}
    assert {"GC004", "GC006"} <= rules
    results = run_object["results"]
    assert {result["ruleId"] for result in results} == {"GC004", "GC006"}
    assert results[0]["locations"][0]["physicalLocation"]["region"]["startLine"] >= 1


def test_github_format_uses_workflow_commands(cli, bad_file: Path, tmp_path: Path) -> None:
    run = cli(
        "check", "--python", str(BENCH_PYTHON), "--format", "github", str(bad_file), cwd=tmp_path
    )
    lines = [line for line in run.stdout.splitlines() if line.startswith("::")]
    assert lines, run.stdout
    assert any("GC004" in line for line in lines)
    assert all("file=" in line for line in lines)


def test_output_flag_writes_the_report_to_a_file(cli, bad_file: Path, tmp_path: Path) -> None:
    "" """CI reads SARIF from a file, not from mixed-in stdout.""" ""
    sarif = tmp_path / "reports" / "ghostcall.sarif"
    run = cli(
        "check",
        "--python",
        str(BENCH_PYTHON),
        "--format",
        "sarif",
        "--output",
        str(sarif),
        str(bad_file),
        cwd=tmp_path,
    )
    assert run.code == EXIT_FINDINGS
    assert run.stdout.strip() == ""
    assert "ghostcall.sarif" in run.stderr
    payload = json.loads(sarif.read_text(encoding="utf-8"))
    assert payload["version"] == "2.1.0"
    assert {result["ruleId"] for result in payload["runs"][0]["results"]} == {"GC004", "GC006"}


def test_quiet_clean_run_prints_nothing_to_stdout(cli, clean_file: Path, tmp_path: Path) -> None:
    run = cli(
        "check",
        "--python",
        str(BENCH_PYTHON),
        "--format",
        "json",
        str(clean_file),
        cwd=tmp_path,
    )
    payload = json.loads(run.stdout)
    findings = payload["findings"] if isinstance(payload, dict) else payload
    assert findings == []


def test_explain_prints_rule_documentation(cli) -> None:
    run = cli("explain", "GC004")
    assert run.code == EXIT_CLEAN
    assert "GC004" in run.stdout
    assert "keyword" in run.stdout.lower()


def test_explain_without_arguments_lists_every_rule(cli) -> None:
    run = cli("explain")
    assert run.code == EXIT_CLEAN
    for number in range(1, 8):
        assert f"GC00{number}" in run.stdout


def test_explain_unknown_rule_is_a_usage_error(cli) -> None:
    run = cli("explain", "GC999")
    assert run.code == EXIT_USAGE


def test_info_reports_a_missing_attribute(cli) -> None:
    run = cli("info", "pandas.DataFrame.append", "--python", str(BENCH_PYTHON))
    assert run.code == EXIT_CLEAN
    output = run.stdout + run.stderr
    assert "missing" in output.lower() or "no attribute" in output.lower()


def test_info_json_lists_a_real_signature(cli) -> None:
    run = cli("info", "pandas.read_csv", "--python", str(BENCH_PYTHON), "--json")
    payload = json.loads(run.stdout)
    assert payload["status"] == "ok"
    assert "filepath_or_buffer" in payload["signature"]


def test_info_text_mode_summarises_overloads(cli) -> None:
    """Dozens of overloads must not bury the answer in text mode."""
    run = cli("info", "pandas.read_csv", "--python", str(BENCH_PYTHON))
    assert run.code == EXIT_CLEAN
    lines = run.stdout.splitlines()
    assert any(line.startswith("signature: read_csv(") for line in lines)
    assert not any(line.startswith("overloads: read_csv(") for line in lines)
    assert any("overloads" in line and "--overloads" in line for line in lines)

    verbose = cli("info", "pandas.read_csv", "--python", str(BENCH_PYTHON), "--overloads")
    assert any(line.startswith("overloads: read_csv(") for line in verbose.stdout.splitlines())


def test_cache_path_and_clear(cli) -> None:
    path = cli("cache", "path")
    assert path.code == EXIT_CLEAN
    assert path.stdout.strip()
    cleared = cli("cache", "clear")
    assert cleared.code == EXIT_CLEAN


def test_version_flag(cli) -> None:
    run = cli("--version")
    assert run.code == EXIT_CLEAN
    assert "ghostcall" in run.stdout


def test_no_arguments_prints_help(cli) -> None:
    run = cli()
    assert run.code == EXIT_USAGE
    assert "Usage" in run.stdout or "usage" in run.stdout


def test_verbose_lists_unresolved_expressions(cli, tmp_path: Path) -> None:
    path = tmp_path / "dynamic.py"
    snippet = 'import importlib\n\nmodule = importlib.import_module("json")\nmodule.loads("{}")\n'
    path.write_text(snippet, encoding="utf-8")
    run = cli("check", "--python", str(BENCH_PYTHON), "--verbose", str(path), cwd=tmp_path)
    assert run.code == EXIT_CLEAN
    assert "unresolved" in run.stdout


def test_include_medium_shows_medium_findings(cli, bad_file: Path, tmp_path: Path) -> None:
    default = cli("check", "--python", str(BENCH_PYTHON), str(bad_file), cwd=tmp_path)
    medium = cli(
        "check", "--python", str(BENCH_PYTHON), "--include-medium", str(bad_file), cwd=tmp_path
    )
    assert "GC003" not in default.stdout
    assert "GC003" in medium.stdout
    assert "append" in medium.stdout
