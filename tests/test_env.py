from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from ghostcall.checks import Checker, Config
from ghostcall.env import Environment, EnvNotFound, discover, find_python
from ghostcall.index import ApiCache, ApiIndex
from tests.conftest import REPO_ROOT

PACKAGE_SOURCE = "def greet(name, *, loud=False):\n    return name\n"


@dataclass
class TargetVenv:
    python: Path
    site: Path
    marker: Path
    editable_src: Path

    def set_version(self, version: str) -> None:
        """Re-label the installed distribution, keeping its source untouched."""
        metadata = self.site / "fixturedist-1.2.3.dist-info" / "METADATA"
        metadata.write_text(
            f"Metadata-Version: 2.1\nName: fixturedist\nVersion: {version}\n", encoding="utf-8"
        )


def _site_packages(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Lib" / "site-packages"
    return next((venv / "lib").glob("python*/site-packages"))


@pytest.fixture(scope="module")
def target(tmp_path_factory: pytest.TempPathFactory) -> TargetVenv:
    root = tmp_path_factory.mktemp("target")
    venv = root / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = _site_packages(venv)

    package = site / "fixturepkg"
    package.mkdir()
    (package / "__init__.py").write_text(PACKAGE_SOURCE, encoding="utf-8")
    dist_info = site / "fixturedist-1.2.3.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fixturedist\nVersion: 1.2.3\n", encoding="utf-8"
    )
    (dist_info / "RECORD").write_text(
        "fixturepkg/__init__.py,,\nfixturedist-1.2.3.dist-info/METADATA,,\n", encoding="utf-8"
    )

    # An editable install: the source lives outside site-packages.
    editable_src = root / "editable-src"
    (editable_src / "editablepkg").mkdir(parents=True)
    (editable_src / "editablepkg" / "__init__.py").write_text(
        "def run(value):\n    return value\n", encoding="utf-8"
    )
    (site / "__editable__.editablepkg-0.1.pth").write_text(f"{editable_src}\n", encoding="utf-8")

    # A .pth file with an import line: ``site`` would execute it at startup.
    marker = root / "PTH_EXECUTED.txt"
    (site / "zzz_hostile.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')\n", encoding="utf-8"
    )
    return TargetVenv(python=python, site=site, marker=marker, editable_src=editable_src)


def test_discovery_reads_a_foreign_venv_without_running_pth_files(target: TargetVenv) -> None:
    env = discover(target.python)
    assert not target.marker.exists(), "environment discovery executed a .pth import line"
    assert Path(env.prefix).resolve() == target.python.parent.parent.resolve()
    assert env.is_venv
    assert [path.resolve() for path in env.site_packages] == [target.site.resolve()]
    assert env.version_of_import("fixturepkg") == "1.2.3"
    assert env.label_for("fixturepkg") == "fixturedist 1.2.3"
    assert env.distribution_for("griffe") is None


def test_findings_name_the_target_version(target: TargetVenv) -> None:
    env = discover(target.python)
    source = "import fixturepkg\n\nfixturepkg.greet('x', loudly=True)\nfixturepkg.wave()\n"
    result = Checker(env, Config()).check_source(source)
    assert sorted(finding.code for finding in result.findings) == ["GC003", "GC004"]
    assert {finding.installed for finding in result.findings} == {"fixturedist 1.2.3"}
    assert not target.marker.exists()


def test_editable_installs_count_as_local_code(target: TargetVenv) -> None:
    env = discover(target.python)
    assert target.editable_src.resolve() in {path.resolve() for path in env._editable_roots}
    result = Checker(env, Config()).check_source(
        "import editablepkg\n\neditablepkg.run(1)\neditablepkg.not_defined()\n"
    )
    assert result.findings == [], [finding.message for finding in result.findings]


def test_cache_is_invalidated_when_the_installed_version_changes(
    target: TargetVenv, tmp_path: Path
) -> None:
    cache = ApiCache(tmp_path / "cache")
    try:
        env = discover(target.python)
        assert ApiIndex(env, cache=cache).get_module("fixturepkg") is not None
        first = set(cache.root.rglob("*.json"))
        assert len(first) == 1, "the first run must write the cache (miss)"

        assert ApiIndex(env, cache=cache).get_module("fixturepkg") is not None
        assert set(cache.root.rglob("*.json")) == first, "an unchanged install must hit"

        source = target.site / "fixturepkg" / "__init__.py"
        stat = source.stat()
        target.set_version("2.0.0")
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        bumped = discover(target.python)
        assert bumped.version_of_import("fixturepkg") == "2.0.0"
        assert ApiIndex(bumped, cache=cache).get_module("fixturepkg") is not None
        assert len(set(cache.root.rglob("*.json"))) == 2, "a new version must miss the cache"
    finally:
        target.set_version("1.2.3")


def test_explicit_missing_interpreter_is_env_not_found(tmp_path: Path) -> None:
    with pytest.raises(EnvNotFound):
        find_python(tmp_path / "no-such-python")


def test_venv_directory_is_accepted_as_python(target: TargetVenv) -> None:
    """``--python .venv`` means that venv's interpreter."""
    venv = target.python.parent.parent
    assert Path(find_python(venv)).resolve() == target.python.resolve()


def test_nearest_dot_venv_is_found(tmp_path: Path) -> None:
    project = tmp_path / "project"
    interpreter = project / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    interpreter.mkdir(parents=True)
    executable = interpreter / ("python.exe" if os.name == "nt" else "python")
    executable.write_text("", encoding="utf-8")
    assert Path(find_python(start=project)) == executable


def test_environment_object_is_immutable(target: TargetVenv) -> None:
    env = Environment.from_python(target.python)
    with pytest.raises(AttributeError):
        env.python = "other"  # type: ignore[misc]


def _cli(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ghostcall", *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=600,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )


def test_cli_missing_environment_exits_three(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("import json\n", encoding="utf-8")
    run = _cli("check", "--python", str(tmp_path / "missing" / "python"), "app.py", cwd=tmp_path)
    assert run.returncode == 3, run.stdout + run.stderr
    assert "no such interpreter" in run.stderr


def test_cli_allow_import_prints_a_warning(target: TargetVenv, tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "import fixturepkg\n\nfixturepkg.greet('x')\n", encoding="utf-8"
    )
    quiet = _cli("check", "--python", str(target.python), "app.py", cwd=tmp_path)
    assert quiet.returncode == 0, quiet.stdout + quiet.stderr
    assert "allow-import" not in quiet.stderr

    loud = _cli("check", "--allow-import", "--python", str(target.python), "app.py", cwd=tmp_path)
    assert loud.returncode == 0, loud.stdout + loud.stderr
    assert "warning: --allow-import is enabled" in loud.stderr
