"""Diff mode: report only the findings a change introduced.

Uses a real temporary git repository, because the whole feature is about what git
says changed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ghostcall.checks import Checker, Config
from ghostcall.diffmode import GitError, changed_lines, filter_results
from ghostcall.env import Environment
from tests.conftest import BENCH_PYTHON, needs_env

pytestmark = needs_env

PRELUDE = (
    "import pandas as pd\n\n\ndef load(path):\n    return pd.read_csv(path, skip_blank=True)\n"
)


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            "GIT_AUTHOR_NAME": "Ghost",
            "GIT_AUTHOR_EMAIL": "ghost@example.invalid",
            "GIT_COMMITTER_NAME": "Ghost",
            "GIT_COMMITTER_EMAIL": "ghost@example.invalid",
            "HOME": str(repo),
            # Inherit PATH so git is found on every platform (Windows has no /usr/bin).
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        },
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text(PRELUDE, encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


def test_only_changed_lines_are_reported(repo: Path, env: Environment) -> None:
    """The pre-existing bug on line 4 must not be re-reported after a change."""
    (repo / "app.py").write_text(
        PRELUDE + '\ndata = pd.read_csv("other.csv", skip_blank=True)\n', encoding="utf-8"
    )
    index = changed_lines("HEAD", root=repo)
    assert index.includes("app.py", 7)
    assert not index.includes("app.py", 4)

    checker = Checker(env, Config())
    result = checker.check_file(repo / "app.py")
    assert len(result.findings) == 2

    dropped = filter_results([result], index)
    assert dropped == 1
    assert [finding.line for finding in result.findings] == [7]


def test_staged_mode_sees_the_index(repo: Path, env: Environment) -> None:
    (repo / "app.py").write_text(
        PRELUDE + '\ndata = pd.read_csv("other.csv", skip_blank=True)\n', encoding="utf-8"
    )
    git(repo, "add", "app.py")
    index = changed_lines("HEAD", staged=True, root=repo)
    assert index.includes("app.py", 7)


def test_untracked_files_are_included(repo: Path, env: Environment) -> None:
    (repo / "new.py").write_text("import requests\n\nrequests.get(timeout=3)\n", encoding="utf-8")
    index = changed_lines("HEAD", root=repo)
    assert index.includes("new.py", 3)


def test_untracked_files_can_be_excluded(repo: Path, env: Environment) -> None:
    (repo / "new.py").write_text("import requests\n", encoding="utf-8")
    index = changed_lines("HEAD", root=repo, include_untracked=False)
    assert not index.includes("new.py", 1)


def test_context_lines_are_included(repo: Path) -> None:
    """A one-line edit also marks the lines around it, so a fix is visible."""
    (repo / "app.py").write_text(PRELUDE.replace("def load", "def load_data"), encoding="utf-8")
    index = changed_lines("HEAD", root=repo)
    assert index.includes("app.py", 4)


def test_paths_filter_narrows_the_diff(repo: Path) -> None:
    """Regression: the path restriction used to be ignored for untracked files."""
    (repo / "app.py").write_text(PRELUDE + "\nX = 1\n", encoding="utf-8")
    (repo / "other.py").write_text("Y = 2\n", encoding="utf-8")
    index = changed_lines("HEAD", root=repo, paths=(repo / "app.py",))
    assert index.includes("app.py", 6)
    assert not index.includes("other.py", 1)


def test_git_errors_are_reported(tmp_path: Path) -> None:
    """Not a repository: the CLI turns this into exit code 2."""
    with pytest.raises(GitError):
        changed_lines("HEAD", root=tmp_path)


def test_diff_filters_every_file_independently(repo: Path, env: Environment) -> None:
    (repo / "app.py").write_text(
        PRELUDE + '\ndata = pd.read_csv("other.csv", skip_blank=True)\n', encoding="utf-8"
    )
    (repo / "second.py").write_text(
        'import requests\n\n\nrequests.get("https://x.invalid", retries=2)\n', encoding="utf-8"
    )
    index = changed_lines("HEAD", root=repo)
    checker = Checker(env, Config())
    results = [checker.check_file(repo / name) for name in ("app.py", "second.py")]
    filter_results(results, index)
    assert [finding.line for finding in results[0].findings] == [7]
    assert [finding.line for finding in results[1].findings] == [4]


def test_cli_diff_mode_only_fails_on_new_findings(repo: Path) -> None:
    """End-to-end: ``--diff HEAD`` exits 0 when the change is clean."""
    fixed = PRELUDE.replace("skip_blank=True", "skip_blank_lines=True")
    (repo / "app.py").write_text(fixed, encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ghostcall",
            "check",
            "--python",
            str(BENCH_PYTHON),
            "--diff",
            "HEAD",
            "--include-medium",
            ".",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_cli_diff_mode_fails_on_a_new_hallucination(repo: Path) -> None:
    (repo / "app.py").write_text(
        PRELUDE + '\nbroken = pd.read_csv("x.csv", skip_blank=True)\n', encoding="utf-8"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ghostcall",
            "check",
            "--python",
            str(BENCH_PYTHON),
            "--diff",
            "HEAD",
            "--include-medium",
            ".",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 1
    assert "GC004" in completed.stdout
    assert "app.py:7" in completed.stdout


def _cli_check(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ghostcall", "check", "--python", str(BENCH_PYTHON), *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def test_staged_alone_means_staged_against_head(repo: Path) -> None:
    """``ghostcall check --staged`` is the pre-commit hook's question."""
    (repo / "app.py").write_text(
        PRELUDE + '\nbroken = pd.read_csv("x.csv", skip_blank=True)\n', encoding="utf-8"
    )
    git(repo, "add", "app.py")
    completed = _cli_check(repo, "--staged", "app.py")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "app.py:7" in completed.stdout
    assert "app.py:5" not in completed.stdout, "the committed bug must not be re-reported"


def test_staged_works_before_the_first_commit(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "first.py").write_text(
        "import requests\n\nrequests.get('x', retries=1)\n", encoding="utf-8"
    )
    git(tmp_path, "add", "first.py")
    completed = _cli_check(tmp_path, "--staged", "first.py")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "first.py:3" in completed.stdout
    assert "no commits yet" in completed.stdout
