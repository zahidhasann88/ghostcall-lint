"""Proof that the default path never imports the code it analyses.

Runtime introspection is the one thing ghostcall must not do silently: importing
a target library executes arbitrary code (``sitecustomize``-style side effects,
incomplete installs, wrong interpreter), and importing *project* code is worse.
These tests use a marker package that writes a file whenever it is imported, so
the assertion is not "we think we did not import it" but "the filesystem says so".
"""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from ghostcall.checks import Checker, Config
from ghostcall.env import Environment
from ghostcall.index import ApiIndex
from tests.conftest import BENCH_PYTHON, REPO_ROOT, needs_env

pytestmark = needs_env

MARKER_TEMPLATE = '''
"""A package that proves whether it was imported."""

import pathlib

MARKER = pathlib.Path({marker!r})
MARKER.write_text("imported by " + __name__, encoding="utf-8")


def sentinel(value):
    return value
'''


@pytest.fixture
def marker_env(env: Environment, tmp_path: Path) -> tuple[Environment, Path]:
    """A synthetic environment whose site-packages contains the marker package."""
    site = tmp_path / "site-packages"
    package = site / "marker_pkg"
    package.mkdir(parents=True)
    marker = tmp_path / "IMPORTED.txt"
    (package / "__init__.py").write_text(
        MARKER_TEMPLATE.format(marker=str(marker)), encoding="utf-8"
    )
    (package / "sub.py").write_text(
        MARKER_TEMPLATE.replace("marker_pkg", "marker_pkg.sub").format(marker=str(marker)),
        encoding="utf-8",
    )
    fake = replace(
        env,
        search_paths=(site,),
        site_packages=(site,),
        stdlib_paths=(),
        _local_modules={},
    )
    return fake, marker


def test_marker_package_is_not_imported_when_checking(marker_env: tuple[Environment, Path]) -> None:
    fake, marker = marker_env
    source = dedent(
        """
        import marker_pkg

        marker_pkg.sentinel(1)
        marker_pkg.sub.sentinel(2)
        """
    )
    checker = Checker(fake, Config())
    result = checker.check_source(source, filename="uses_marker.py")

    assert not marker.exists(), "ghostcall imported the analysed library"
    assert "marker_pkg" not in sys.modules, "ghostcall imported the analysed library"
    # The library exists in this environment, so nothing is reported about it.
    assert [finding.code for finding in result.findings] == []


def test_marker_package_is_not_imported_for_unknown_attributes(
    marker_env: tuple[Environment, Path],
) -> None:
    """Even a *missing* attribute must be answered statically, not by importing."""
    fake, marker = marker_env
    source = "import marker_pkg\n\nmarker_pkg.definitely_missing()\n"
    result = Checker(fake, Config()).check_source(source, filename="uses_marker.py")
    assert not marker.exists()
    assert "marker_pkg" not in sys.modules
    assert [finding.code for finding in result.findings] == ["GC003"]


def test_project_code_is_never_imported(tmp_path: Path) -> None:
    """A local module with an import side effect must not be executed either."""
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "PROJECT_IMPORTED.txt"
    (project / "localpkg.py").write_text(
        dedent(
            f"""
            import pathlib

            pathlib.Path({str(marker)!r}).write_text("imported", encoding="utf-8")

            def run(value):
                return value
            """
        ),
        encoding="utf-8",
    )
    script = project / "main.py"
    script.write_text(
        "import localpkg\n\nlocalpkg.run(1)\nlocalpkg.not_a_function(2)\n", encoding="utf-8"
    )
    fake = Environment(
        python=str(BENCH_PYTHON),
        version_info=(3, 12, 0),
        site_packages=(),
        search_paths=(project,),
        stdlib_paths=(),
        prefix="",
        base_prefix="",
        platform=sys.platform,
        _local_modules={"localpkg": project / "localpkg.py"},
    )
    result = Checker(fake, Config(), index=ApiIndex(fake)).check_file(script)
    assert not marker.exists(), "ghostcall executed project code"
    assert "localpkg" not in sys.modules
    assert [finding.code for finding in result.findings] == []


def test_real_libraries_are_not_imported(env: Environment) -> None:
    """A full check of real library code leaves ``sys.modules`` untouched."""
    source = dedent(
        """
        import numpy as np
        import pandas as pd
        import requests

        frame = pd.DataFrame({"a": [1]})
        frame.append({"a": 2})
        frame.map(str)
        values = np.array([1.0])
        np.linalg.norm(values)
        requests.get("https://example.invalid", timeout=5)
        """
    )
    before = set(sys.modules)
    Checker(env, Config(include_medium=True), index=ApiIndex(env)).check_source(source)
    loaded = {name.split(".")[0] for name in set(sys.modules) - before}
    assert loaded & {"numpy", "pandas", "requests"} == set()


def test_default_path_is_import_free_in_a_fresh_interpreter(tmp_path: Path) -> None:
    """The strongest form: a brand-new interpreter that checks *and* asserts."""
    script = tmp_path / "run_check.py"
    script.write_text(
        dedent(
            """
            import sys

            from ghostcall.checks import Checker, Config
            from ghostcall.env import Environment

            env = Environment.from_python(sys.argv[1])
            source = (
                "import pandas as pd\\n"
                "import requests\\n"
                "frame = pd.DataFrame({'a': [1]})\\n"
                "frame.append({'a': 2})\\n"
                "requests.get('https://x.invalid', retries=2)\\n"
            )
            result = Checker(env, Config(include_medium=True)).check_source(source)
            assert [f.code for f in result.findings] == ["GC003", "GC004"]
            leaked = sorted(
                name for name in sys.modules if name.split(".")[0] in {"pandas", "requests"}
            )
            assert leaked == [], f"imported {leaked}"
            print("clean")
            """
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script), str(BENCH_PYTHON)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "clean" in completed.stdout


def test_marker_template_is_valid_python() -> None:
    """Guard the guard: the marker package itself must be importable."""
    ast.parse(MARKER_TEMPLATE.format(marker="/tmp/x"))
