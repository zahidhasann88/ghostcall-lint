"""The benchmark is part of the contract: it must stay at zero false positives.

If a change makes ghostcall noisier, this file fails before the README table can
go stale.  It also checks the corpus itself, so a case cannot silently rot.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ghostcall.bench import run_bench
from ghostcall.env import Environment
from ghostcall.index import ApiIndex
from tests.conftest import BENCH_CORPUS, needs_env

pytestmark = needs_env

#: Facts in the corpus were verified against exactly these versions.  Bumping the
#: pinned environment means re-verifying every case before changing this list.
PINNED_VERSIONS = {
    "pandas": "2.2.3",
    "numpy": "2.2.2",
    "requests": "2.32.3",
    "httpx": "0.28.1",
    "sqlalchemy": "2.0.36",
    "pydantic": "2.10.4",
    "fastapi": "0.115.6",
    "click": "8.1.8",
    "rich": "13.9.4",
}


def _corpus_files(corpus: str) -> list[Path]:
    return sorted((BENCH_CORPUS / corpus).rglob("*.py"))


def test_pinned_environment_versions(env: Environment) -> None:
    """The corpus facts are only valid for the versions they were checked against."""
    for name, expected in PINNED_VERSIONS.items():
        assert env.version_of_import(name) == expected, (
            f"{name} is {env.version_of_import(name)!r}, but the corpus was verified against "
            f"{expected!r}: re-verify bench/hallucinations before updating this list"
        )


def test_clean_corpus_produces_no_findings_in_high_confidence_mode(env: Environment) -> None:
    outcome = run_bench(env, BENCH_CORPUS, include_medium=False)
    assert outcome.clean_files >= 10, "the clean corpus shrank unexpectedly"
    assert outcome.false_positives == 0, outcome.false_positive_details
    assert outcome.false_positive_rate == 0.0
    assert outcome.precision == 1.0


def test_medium_mode_is_also_false_positive_free(env: Environment) -> None:
    """Medium confidence is allowed to find more, never to be wrong."""
    outcome = run_bench(env, BENCH_CORPUS, include_medium=True)
    assert outcome.false_positives == 0, outcome.false_positive_details
    assert outcome.precision == 1.0


def test_every_expected_finding_is_reported(env: Environment) -> None:
    outcome = run_bench(env, BENCH_CORPUS, include_medium=True)
    assert outcome.false_negatives == 0, outcome.misses
    assert outcome.recall == 1.0
    assert outcome.true_positives == outcome.expectations >= 30


def test_all_rules_are_covered_by_the_corpus(env: Environment) -> None:
    """Every rule GC000+ has at least one verifying case (GC000 is a parse error)."""
    outcome = run_bench(env, BENCH_CORPUS, include_medium=True)
    covered = {code for code, (found, total) in outcome.per_rule.items() if found}
    assert covered == {f"GC00{n}" for n in range(1, 8)}, sorted(covered)


def test_clean_corpus_files_are_valid_python() -> None:
    for path in _corpus_files("clean"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_hallucination_corpus_files_are_valid_python() -> None:
    """Even the wrong code has to parse, or the case proves nothing."""
    for path in _corpus_files("hallucinations"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_corpus_expectations_point_at_real_lines() -> None:
    for path in _corpus_files("hallucinations"):
        lines = path.read_text(encoding="utf-8").splitlines()
        markers = [line for line in lines if "ghostcall-expect" in line]
        assert markers, f"{path.name} has no expectations"
        for number, line in enumerate(lines, start=1):
            assert "#?" not in line, (
                f"{path.name}:{number} still has a shorthand marker; run "
                "python bench/tools/mark_expectations.py bench/hallucinations"
            )
            if "ghostcall-expect" not in line:
                continue
            parts = line.split()
            target = int(parts[-2] if parts[-1] == "medium" else parts[-1])
            assert 1 <= target <= len(lines), f"{path.name}:{number} points at line {target}"
            assert lines[target - 1].strip(), f"{path.name}:{number} points at a blank line"


def test_library_helper_matches_the_directory_layout() -> None:
    """``_library_of`` powers the grouped report; keep it honest."""
    from ghostcall.bench import _library_of

    assert _library_of(Path("bench/hallucinations/pandas_bad_kwargs.py")) == "pandas"
    assert _library_of(Path("bench/hallucinations/numpy_removed_aliases.py")) == "numpy"
    assert _library_of(Path("bench/clean/stdlib_typing.py")) == "stdlib"
    assert _library_of(Path("bench/clean/patterns_suppressions.py")) == "misc"


def test_index_shares_one_graph_for_the_whole_corpus(env: Environment) -> None:
    """A sanity check that the bench uses a real index, not an empty one."""
    index = ApiIndex(env)
    assert index.get_module("pandas") is not None
    assert index.get_module("definitely_not_installed_anywhere") is None
