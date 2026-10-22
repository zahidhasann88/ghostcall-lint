"""ghostcall - find hallucinated and outdated library API usage.

Library usage::

    from ghostcall import check_source, discover, Config

    env = discover("path/to/venv/bin/python")
    findings = check_source(source, env, Config(include_medium=True))
    for finding in findings:
        print(finding.render_line())

The default code path never imports the code under analysis, and never imports
the libraries being checked; their APIs are read statically from source or
stubs.  Runtime introspection is only used when ``Config(allow_import=True)``
is requested explicitly.
"""

from __future__ import annotations

from pathlib import Path

from .checks import RULES, Checker, Config, FileResult, RuleDoc, rule_help
from .diffmode import DiffIndex, changed_lines, filter_results
from .env import (
    Distribution,
    EnvError,
    Environment,
    EnvNotFound,
    EnvUnusable,
    discover,
)
from .index import ApiCache, ApiIndex
from .model import (
    ApiKind,
    ApiObject,
    Confidence,
    Finding,
    ParseError,
    Severity,
    Signature,
    Unresolved,
)
from .report import Report, render
from .resolve import Resolution, ResolutionStatus, Resolver
from .scope import Binding, FileScope
from .walk import collect_files

__version__ = "0.1.0"

__all__ = [
    "RULES",
    "ApiCache",
    "ApiIndex",
    "ApiKind",
    "ApiObject",
    "Binding",
    "Checker",
    "Confidence",
    "Config",
    "DiffIndex",
    "Distribution",
    "EnvError",
    "EnvNotFound",
    "EnvUnusable",
    "Environment",
    "FileResult",
    "FileScope",
    "Finding",
    "ParseError",
    "Report",
    "Resolution",
    "ResolutionStatus",
    "Resolver",
    "RuleDoc",
    "Severity",
    "Signature",
    "Unresolved",
    "__version__",
    "changed_lines",
    "check_file",
    "check_source",
    "check_source_detailed",
    "collect_files",
    "discover",
    "filter_results",
    "render",
    "rule_help",
]


def check_source(source: str, env: Environment, config: Config | None = None) -> list[Finding]:
    """Analyse ``source`` against the APIs installed in ``env``.

    This is the primary library entry point.  It never executes the code it
    analyses (see ``--allow-import`` for the explicit opt-in escape hatch) and
    never raises for malformed input: an unparsable file yields a single
    ``GC000`` finding.
    """
    checker = Checker(env, config or Config())
    return checker.check_source(source).findings


def check_source_detailed(
    source: str, env: Environment, config: Config | None = None, filename: str = "<stdin>"
) -> FileResult:
    """Like :func:`check_source`, but also returns unresolved expressions."""
    checker = Checker(env, config or Config())
    return checker.check_source(source, filename=filename)


def check_file(path: str | Path, env: Environment, config: Config | None = None) -> FileResult:
    """Analyse one file from disk."""
    checker = Checker(env, config or Config())
    return checker.check_file(path)
