"""Documentation that must not drift.

Two files quote facts that this project generates: the rule catalogue and the
benchmark tables in the README. Both are regenerated here, so a change in
behaviour fails the suite instead of leaving the documentation confidently wrong.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from types import ModuleType

import pytest

from ghostcall.checks import RULES
from tests.conftest import REPO_ROOT, needs_env

TOOLS = REPO_ROOT / "tools"


def _load(name: str) -> ModuleType:
    """Import a script from ``tools/`` without making it a package."""
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_rule_catalogue_is_current() -> None:
    module = _load("gen_rules_doc")
    documented = (REPO_ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    assert documented == module.render(), "run: python tools/gen_rules_doc.py"


def test_rule_catalogue_documents_every_rule() -> None:
    documented = (REPO_ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    for code in RULES:
        assert f"## {code} —" in documented, f"{code} is missing from docs/rules.md"


def test_readme_links_exist() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for target in ("docs/rules.md", "docs/architecture.md", "CHANGELOG.md", "LICENSE"):
        assert target in readme, f"README does not mention {target}"
        assert (REPO_ROOT / target).exists(), f"README links to missing {target}"


def test_readme_mentions_every_rule() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    documented = (REPO_ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    for code in RULES:
        assert code in documented
        if code != "GC000":  # GC000 is a parse warning, not a rule users select
            assert code in readme or code in documented


@needs_env
def test_readme_benchmark_block_is_current() -> None:
    module = _load("gen_bench_table")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    start = module.START
    end = module.END
    documented = readme[readme.index(start) + len(start) : readme.index(end)].strip()
    assert documented == module.render().strip(), "run: python tools/gen_bench_table.py"


def test_changelog_has_an_entry_for_the_current_version() -> None:
    from ghostcall import __version__

    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{__version__}]" in changelog


def test_pyproject_and_module_versions_agree() -> None:

    from ghostcall import __version__

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
    assert pyproject["project"]["name"] == "ghostcall-lint"


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_workflows_are_valid_yaml(workflow: str) -> None:
    yaml = pytest.importorskip("yaml")
    path = REPO_ROOT / ".github" / "workflows" / workflow
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data.get("jobs")


def test_composite_action_is_declared() -> None:
    """``uses: zahidhasann88/ghostcall-lint@v...`` needs a valid composite ``action.yml``."""
    yaml = pytest.importorskip("yaml")
    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    assert {"python", "requirements", "paths", "diff", "sarif"} <= set(action["inputs"])
    scripts = "\n".join(step.get("run", "") for step in action["runs"]["steps"])
    assert "ghostcall-lint" in scripts, "the action must install the published distribution"
    assert "--format github" in scripts


def test_pre_commit_hooks_are_declared() -> None:
    yaml = pytest.importorskip("yaml")
    hooks = yaml.safe_load((REPO_ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))
    ids = {hook["id"] for hook in hooks}
    assert {"ghostcall", "ghostcall-high-confidence"} <= ids
    for hook in hooks:
        assert hook["language"] == "python"
        assert hook["entry"].startswith("ghostcall check")
        assert hook["pass_filenames"] is True
        # A commit is judged on the lines it adds, never on pre-existing code.
        assert "--staged" in hook["entry"]


def test_readme_states_the_limitations_plainly() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Limitations" in readme
    # The most important promise: nothing is imported unless asked for.
    assert "--allow-import" in readme
    assert "never imported" in readme or "never import" in readme or "Static by default" in readme


def test_py_typed_marker_is_shipped() -> None:
    """The ``Typing :: Typed`` classifier is only true if the marker ships."""
    marker = REPO_ROOT / "src" / "ghostcall" / "py.typed"
    assert marker.is_file(), "src/ghostcall/py.typed is missing"
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "Typing :: Typed" in project["project"]["classifiers"]
    packages = project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/ghostcall" in packages, "py.typed would not reach the wheel"
