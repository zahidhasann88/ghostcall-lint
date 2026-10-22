from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .checks import RULES, FileResult
from .env import Environment
from .model import Finding, Severity

__all__ = ["Report", "render", "render_github", "render_json", "render_sarif", "render_text"]

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
TOOL_NAME = "ghostcall"


@dataclass
class Report:
    """Everything a formatter needs, independent of the CLI."""

    results: Sequence[FileResult]
    env: Environment | None = None
    verbose: bool = False
    files_scanned: int = 0
    version: str = "0.0.0"
    diff_description: str | None = None
    full_signature: bool = False

    @property
    def findings(self) -> list[Finding]:
        return [finding for result in self.results for finding in result.findings]

    @property
    def parse_warnings(self) -> list[Finding]:
        """GC000 for every file that could not be parsed.

        Kept apart from :attr:`findings` because an unparsable file is reported,
        not failed: GC000 never changes the exit code.
        """
        out: list[Finding] = []
        for result in self.results:
            error = result.parse_error
            if error is None:
                continue
            out.append(
                Finding(
                    code="GC000",
                    message=f"could not parse file: {error.message}",
                    line=error.line,
                    col=error.col,
                    severity=Severity.WARNING,
                    path=error.path,
                )
            )
        return out

    @property
    def reported(self) -> list[Finding]:
        """Everything a machine-readable format should list: GC000 first, then findings."""
        return [*self.parse_warnings, *self.findings]

    @property
    def unresolved(self) -> list[str]:
        return [unresolved.render() for result in self.results for unresolved in result.unresolved]

    @property
    def calls_checked(self) -> int:
        return sum(result.checked for result in self.results)

    @property
    def high(self) -> list[Finding]:
        return [f for f in self.findings if f.confidence.value == "high"]

    @property
    def medium(self) -> list[Finding]:
        return [f for f in self.findings if f.confidence.value == "medium"]

    def counts_by_rule(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.code] = counts.get(finding.code, 0) + 1
        return dict(sorted(counts.items()))


def render(report: Report, fmt: str = "text") -> str:
    if fmt == "json":
        return render_json(report)
    if fmt == "sarif":
        return render_sarif(report)
    if fmt == "github":
        return render_github(report)
    return render_text(report)


_BOLD = "\033[1m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def render_text(report: Report, color: bool | None = None) -> str:
    """Render the human readable report.

    Colour is applied only when ``color`` is true (default: stdout is a TTY), so
    that redirected output and snapshots stay stable.
    """
    if color is None:
        color = sys.stdout.isatty()
    painter = _Painter(bool(color))
    lines: list[str] = []
    for result in report.results:
        if result.parse_error is not None:
            error = result.parse_error
            lines.append(
                painter.paint(
                    f"{error.path}:{error.line}:{error.col} GC000 could not parse file: "
                    f"{error.message}",
                    _YELLOW,
                )
            )
        for finding in result.findings:
            lines.extend(_finding_lines(finding, painter, report.full_signature))
        if report.verbose:
            for unresolved in result.unresolved:
                lines.append(
                    painter.paint(
                        f"  unresolved: {unresolved.line}:{unresolved.col} "
                        f"{unresolved.symbol} ({unresolved.reason})",
                        _DIM,
                    )
                )

    summary = (
        f"{report.files_scanned} file(s) scanned, {report.calls_checked} call(s) resolved, "
        f"{len(report.findings)} finding(s) "
        f"({len(report.high)} high, {len(report.medium)} medium)"
    )
    if report.diff_description:
        summary = f"{summary} [diff: {report.diff_description}]"
    lines.append("")
    lines.append(painter.paint(summary, _BOLD))
    counts = report.counts_by_rule()
    if counts:
        lines.append(
            painter.paint(
                "  " + ", ".join(f"{code} x{count}" for code, count in counts.items()), _DIM
            )
        )
    return "\n".join(lines) + "\n"


def _finding_lines(finding: Finding, painter: _Painter, full_signature: bool = False) -> list[str]:
    colour = _RED if finding.severity is Severity.ERROR else _YELLOW
    lines = [
        painter.paint(f"{finding.path}:{finding.line}:{finding.col} {finding.code}", colour)
        + " "
        + finding.message
    ]
    details: list[str] = []
    if finding.installed:
        details.append(f"installed: {finding.installed}")
    signature = finding.signature
    if full_signature and finding.signature_full:
        signature = finding.signature_full
    if signature:
        details.append(f"signature: {signature}")
    if details:
        lines.append(painter.paint("    " + "   ".join(details), _DIM))
    if finding.extra.get("defined"):
        lines.append(painter.paint(f"    defined at: {finding.extra['defined']}", _DIM))
    if finding.suggestion:
        lines.append("    did you mean: " + painter.paint(finding.suggestion, _GREEN))
    if finding.confidence.value == "medium":
        lines.append(painter.paint("    confidence: medium (show with --include-medium)", _DIM))
    return lines


class _Painter:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def paint(self, text: str, colour: str) -> str:
        if not self.enabled or not colour:
            return text
        return f"{colour}{text}{_RESET}"


def finding_to_dict(finding: Finding) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": finding.code,
        "message": finding.message,
        "severity": finding.severity.value,
        "confidence": finding.confidence.value,
        "file": finding.path,
        "line": finding.line,
        "col": finding.col,
        "symbol": finding.symbol,
    }
    if finding.installed:
        payload["installed"] = finding.installed
    if finding.signature:
        payload["signature"] = finding.signature
    if finding.signature_full:
        payload["signature_full"] = finding.signature_full
    if finding.suggestion:
        payload["suggestion"] = finding.suggestion
    if finding.extra:
        payload["extra"] = dict(finding.extra)
    return payload


def render_json(report: Report) -> str:
    payload: dict[str, Any] = {
        "tool": {"name": TOOL_NAME, "version": report.version},
        "environment": _environment_dict(report.env),
        "summary": {
            "files_scanned": report.files_scanned,
            "calls_resolved": report.calls_checked,
            "findings": len(report.findings),
            "parse_errors": len(report.parse_warnings),
            "by_rule": report.counts_by_rule(),
        },
        "findings": [finding_to_dict(f) for f in report.reported],
    }
    if report.diff_description:
        payload["diff"] = report.diff_description
    if report.verbose:
        payload["unresolved"] = report.unresolved
    return json.dumps(payload, indent=2, sort_keys=False)


def _environment_dict(env: Environment | None) -> dict[str, Any]:
    if env is None:
        return {}
    return {
        "python": env.python,
        "version": env.version_string,
        "site_packages": [str(path) for path in env.site_packages],
    }


def render_sarif(report: Report) -> str:
    rules = [
        {
            "id": doc.code,
            "name": doc.title.replace(" ", "_"),
            "shortDescription": {"text": f"{doc.code}: {doc.title}"},
            "fullDescription": {"text": doc.summary},
            "help": {"text": f"{doc.summary}\n\nSkipped when: {doc.when_skipped}"},
            "defaultConfiguration": {"level": _sarif_level(doc.severity)},
            "properties": {"precision": "high"},
        }
        for doc in RULES.values()
    ]
    results: list[dict[str, Any]] = []
    for finding in report.reported:
        results.append(
            {
                "ruleId": finding.code,
                "level": _sarif_level(finding.severity),
                "message": {"text": finding.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": finding.path.replace("\\", "/")},
                            "region": {
                                "startLine": max(1, finding.line),
                                "startColumn": max(1, finding.col + 1),
                            },
                        }
                    }
                ],
                "partialFingerprints": {
                    "ghostcall/v1": f"{finding.code}:{finding.symbol}:{finding.line}"
                },
                "properties": {
                    "confidence": finding.confidence.value,
                    "installed": finding.installed,
                    "signature": finding.signature,
                    "suggestion": finding.suggestion or "",
                },
            }
        )
    payload = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": report.version,
                        "informationUri": "https://github.com/zahidhasann88/ghostcall-lint",
                        "rules": rules,
                    }
                },
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "toolExecutionNotifications": [
                            {
                                "level": "note",
                                "message": {"text": entry},
                            }
                            for entry in (report.unresolved if report.verbose else [])
                        ],
                    }
                ],
                "results": results,
            }
        ],
    }
    return json.dumps(payload, indent=2)


def _sarif_level(severity: Severity) -> str:
    return "error" if severity is Severity.ERROR else "warning"


def render_github(report: Report) -> str:
    lines: list[str] = []
    for finding in report.reported:
        level = "error" if finding.severity is Severity.ERROR else "warning"
        message = finding.message
        if finding.suggestion:
            message += f" (did you mean {finding.suggestion}?)"
        if finding.confidence.value == "medium":
            message += " [confidence: medium]"
        lines.append(
            f"::{level} file={finding.path},line={finding.line},"
            f"col={max(1, finding.col + 1)},title={finding.code}::{_escape_annotation(message)}"
        )
    if report.verbose:
        for entry in report.unresolved:
            lines.append(f"::notice::{_escape_annotation(entry)}")
    return "\n".join(lines)


def _escape_annotation(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def iter_findings_by_code(report: Report) -> Iterable[tuple[str, Finding]]:
    for finding in report.findings:
        yield finding.code, finding
