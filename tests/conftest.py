"""Shared fixtures: a real pinned environment, and a fast way to check snippets.

The tests deliberately exercise the *real* interpreter of ``bench/envs/main`` so
that every assertion about "the installed version" is a fact, not a mock.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ghostcall.checks import Checker, Config, FileResult
from ghostcall.env import Environment
from ghostcall.index import ApiIndex

REPO_ROOT = Path(__file__).resolve().parent.parent
#: venvs put the interpreter in ``Scripts/python.exe`` on Windows, ``bin/python`` elsewhere.
BENCH_PYTHON = (
    REPO_ROOT / "bench" / "envs" / "main" / "Scripts" / "python.exe"
    if os.name == "nt"
    else REPO_ROOT / "bench" / "envs" / "main" / "bin" / "python"
)
BENCH_CORPUS = REPO_ROOT / "bench"

pytestmark = pytest.mark.filterwarnings("error::DeprecationWarning")

#: Test names that need the pinned environment skip cleanly instead of failing --
#: unless ``GHOSTCALL_REQUIRE_ENV`` is set (CI), where a missing environment means
#: the important half of the suite would silently not run.
_REQUIRE_ENV = os.environ.get("GHOSTCALL_REQUIRE_ENV") == "1"

if _REQUIRE_ENV and not BENCH_PYTHON.exists():
    raise RuntimeError(
        "GHOSTCALL_REQUIRE_ENV=1 but the pinned benchmark environment is missing: "
        "run `uv venv bench/envs/main --python 3.12` and "
        f"`uv pip install --python {BENCH_PYTHON} -r bench/requirements.txt`"
    )

needs_env = pytest.mark.skipif(
    not BENCH_PYTHON.exists(),
    reason="pinned bench environment missing; run: uv venv bench/envs/main && uv pip install ...",
)


@pytest.fixture(scope="session")
def env() -> Environment:
    """The pinned plotting/data environment every corpus fact was verified against."""
    if not BENCH_PYTHON.exists():
        pytest.skip("pinned bench environment missing")
    return Environment.from_python(BENCH_PYTHON)


@pytest.fixture(scope="session")
def index(env: Environment) -> ApiIndex:
    """One shared API index for the whole session (parsing is the slow part)."""
    return ApiIndex(env)


@pytest.fixture
def make_checker(index: ApiIndex, env: Environment) -> Callable[..., Checker]:
    """Factory for checkers with tweaked configs, sharing the session index."""

    def factory(**overrides: object) -> Checker:
        config = Config(**overrides)  # type: ignore[arg-type]
        if config.ignore_modules:
            # Module filtering lives on the index, so a private one is needed.
            return Checker(env, config)
        return Checker(env, config, index=index)

    return factory


@pytest.fixture
def check(make_checker: Callable[..., Checker]) -> Callable[..., FileResult]:
    """``check(source)`` -> findings, high confidence only by default."""

    def run(source: str, *, filename: str = "snippet.py", **overrides: object) -> FileResult:
        overrides.setdefault("instance_tracking", True)
        return make_checker(**overrides).check_source(source, filename=filename)

    return run


@pytest.fixture
def check_medium(make_checker: Callable[..., Checker]) -> Callable[..., FileResult]:
    """``check_medium(source)`` -> findings including medium confidence."""

    def run(source: str, *, filename: str = "snippet.py", **overrides: object) -> FileResult:
        overrides.setdefault("include_medium", True)
        return make_checker(**overrides).check_source(source, filename=filename)

    return run


@pytest.fixture
def codes() -> Callable[[FileResult], list[str]]:
    """Sorted rule codes of a result, for terse assertions."""

    def run(result: FileResult) -> list[str]:
        return sorted(finding.code for finding in result.findings)

    return run


@pytest.fixture
def tmp_project(tmp_path: Path) -> Iterator[Path]:
    """A temporary project directory that tests can fill with files."""
    project = tmp_path / "project"
    project.mkdir()
    yield project
