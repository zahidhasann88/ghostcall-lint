from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from .checks import Checker, Config, FileResult
from .env import Environment
from .model import Confidence

__all__ = ["BenchOutcome", "Case", "Expectation", "format_report", "run_bench"]

#: Library names used to group cases in the report (longest match wins below).
_LIBRARIES = (
    "pandas",
    "numpy",
    "sqlalchemy",
    "requests",
    "httpx",
    "pydantic",
    "fastapi",
    "click",
    "rich",
    "polars",
    "stdlib",
    "typing",
)

_EXPECT = re.compile(r"#\s*ghostcall-expect:\s*([A-Z0-9]+)\s+(\d+)(?:\s+(medium))?")


@dataclass(frozen=True)
class Expectation:
    """One expected finding: rule code, 1-based line, and confidence.

    ``medium`` marks a finding that only appears with ``--include-medium``; the
    runner therefore compares against the mode it is running in, instead of
    counting every medium finding as a miss in high-confidence mode.
    """

    code: str
    line: int
    medium: bool = False

    def __str__(self) -> str:
        return f"{self.code}@{self.line}"


@dataclass
class Case:
    """One corpus file."""

    path: Path
    expectations: tuple[Expectation, ...]
    corpus: str
    library: str = ""

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class BenchOutcome:
    """Result of a benchmark run."""

    cases: int = 0
    expectations: int = 0
    true_positives: int = 0
    false_negatives: int = 0
    false_positives: int = 0
    misses: list[str] = field(default_factory=list)
    false_positive_details: list[str] = field(default_factory=list)
    per_rule: dict[str, tuple[int, int]] = field(default_factory=dict)
    """rule -> (true positives, total expected)."""
    medium_findings: int = 0
    clean_files: int = 0

    @property
    def recall(self) -> float:
        if self.expectations == 0:
            return 1.0
        return self.true_positives / self.expectations

    @property
    def precision(self) -> float:
        denominator = self.true_positives + self.false_positives
        if denominator == 0:
            return 1.0
        return self.true_positives / denominator

    @property
    def false_positive_rate(self) -> float:
        if self.clean_files == 0:
            return 0.0
        return self.false_positives / self.clean_files

    @property
    def recall_by_rule(self) -> dict[str, float]:
        return {
            rule: (found / total if total else 1.0)
            for rule, (found, total) in sorted(self.per_rule.items())
        }


def read_expectations(path: Path) -> tuple[Expectation, ...]:
    out: list[Expectation] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        for match in _EXPECT.finditer(line):
            out.append(
                Expectation(
                    match.group(1).upper(),
                    int(match.group(2)),
                    medium=bool(match.group(3)),
                )
            )
    return tuple(out)


def load_cases(corpus_root: Path) -> list[Case]:
    """Load every ``.py`` file of both corpora."""
    cases: list[Case] = []
    for corpus in ("hallucinations", "clean"):
        directory = corpus_root / corpus
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            if path.name.startswith("_"):
                continue
            cases.append(
                Case(
                    path=path,
                    expectations=read_expectations(path),
                    corpus=corpus,
                    library=_library_of(path),
                )
            )
    return cases


def _library_of(path: Path) -> str:
    """Guess the library a case exercises from its path, for the grouped report."""
    haystack = "/".join(path.parts).lower()
    for candidate in _LIBRARIES:
        if candidate in haystack:
            return candidate
    return "misc"


def run_bench(
    env: Environment,
    corpus_root: Path,
    *,
    include_medium: bool = False,
    config: Config | None = None,
    jobs: int = 1,
) -> BenchOutcome:
    """Run both corpora and compute precision/recall."""
    config = replace(config or Config(), include_medium=include_medium, verbose=False)
    checker = Checker(env, config)
    outcome = BenchOutcome()
    for case, result in _evaluated(checker, load_cases(corpus_root), jobs):
        expected = {
            (item.code, item.line)
            for item in case.expectations
            if include_medium or not item.medium
        }
        actual = {(finding.code, finding.line) for finding in result.findings}
        outcome.cases += 1
        outcome.medium_findings += sum(
            1 for finding in result.findings if finding.confidence is Confidence.MEDIUM
        )
        if case.corpus == "clean":
            outcome.clean_files += 1
            for finding in result.findings:
                outcome.false_positives += 1
                outcome.false_positive_details.append(
                    f"{case.path.name}:{finding.line} {finding.code} "
                    f"[{finding.confidence.value}] {finding.message}"
                )
            continue
        outcome.expectations += len(expected)
        for code, line in sorted(expected, key=lambda item: (item[1], item[0])):
            found, total = outcome.per_rule.get(code, (0, 0))
            if (code, line) in actual:
                outcome.true_positives += 1
                found += 1
            else:
                outcome.false_negatives += 1
                outcome.misses.append(
                    f"{case.path.name}:{line} expected {code} but got "
                    + (
                        ", ".join(f"{finding.code}@{finding.line}" for finding in result.findings)
                        or "nothing"
                    )
                )
            outcome.per_rule[code] = (found, total + 1)
        for finding in result.findings:
            if (finding.code, finding.line) not in expected:
                outcome.false_positives += 1
                outcome.false_positive_details.append(
                    f"{case.path.name}:{finding.line} {finding.code} "
                    f"[{finding.confidence.value}] {finding.message}"
                )
    return outcome


def _evaluated(
    checker: Checker, cases: Sequence[Case], jobs: int
) -> Iterator[tuple[Case, FileResult]]:
    """Yield ``(case, result)`` pairs in corpus order, analysing in parallel if asked.

    The shared :class:`~ghostcall.index.ApiIndex` is thread-safe, so worker threads
    only need their own per-file resolver state -- which ``Checker`` already creates
    per file.
    """
    if jobs <= 1 or len(cases) <= 1:
        for case in cases:
            yield case, _analyse_case(checker, case)
        return
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_analyse_case, checker, case): case for case in cases}
        for future in futures:  # iteration order is submission order
            case = futures[future]
            yield case, future.result()


def _analyse_case(checker: Checker, case: Case) -> FileResult:
    return checker.check_file(case.path, case.path.read_text(encoding="utf-8", errors="replace"))


def format_report(outcome: BenchOutcome, include_medium: bool) -> str:
    """Render the benchmark result as markdown (for the README) or plain text."""
    lines: list[str] = []
    mode = "high confidence" if not include_medium else "high + medium confidence"
    lines.append(f"mode: {mode}")
    lines.append(f"cases: {outcome.cases} ({outcome.clean_files} clean)")
    lines.append(f"expected findings: {outcome.expectations}")
    lines.append(f"true positives: {outcome.true_positives}")
    lines.append(f"false negatives (misses): {outcome.false_negatives}")
    lines.append(f"false positives: {outcome.false_positives}")
    lines.append(f"precision: {outcome.precision:.3f}")
    lines.append(f"recall:    {outcome.recall:.3f}")
    lines.append(
        f"false positives per clean file: {outcome.false_positive_rate:.3f} "
        f"({outcome.false_positives} / {outcome.clean_files})"
    )
    lines.append("")
    lines.append("| rule | found | expected | recall |")
    lines.append("| --- | --- | --- | --- |")
    for rule, (found, total) in sorted(outcome.per_rule.items()):
        lines.append(f"| {rule} | {found} | {total} | {found / total:.2f} |")
    if outcome.misses:
        lines.append("")
        lines.append("misses:")
        lines.extend(f"  - {item}" for item in outcome.misses)
    if outcome.false_positive_details:
        lines.append("")
        lines.append("false positives:")
        lines.extend(f"  - {item}" for item in outcome.false_positive_details)
    return "\n".join(lines)
