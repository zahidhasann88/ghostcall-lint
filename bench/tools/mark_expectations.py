"""Expand shorthand corpus markers into ``# ghostcall-expect: CODE LINE`` comments.

Corpus authors write the rule code as a trailing marker on the offending line::

    frame.append(extra)  #?GC003 medium

and this tool rewrites the file, inserting an explicit expectation comment above
that line with the *computed* line number::

    # ghostcall-expect: GC003 11 medium
    frame.append(extra)

Running it twice is harmless: files without ``#?`` markers are left untouched.

Usage::

    python bench/tools/mark_expectations.py bench/hallucinations
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_MARKER = re.compile(
    r"^(?P<code>.*?)\s*#\?\s*(?P<rule>[A-Z0-9]+)(?:\s+(?P<confidence>medium))?\s*$"
)
_PLACEHOLDER = "@LINE@"


def expand(path: Path) -> bool:
    """Rewrite one file in place; returns whether anything changed."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not any(_MARKER.match(line) for line in lines):
        return False

    expanded: list[str] = []
    pending: list[tuple[int, str, str]] = []  # (index of own comment, rule, confidence)
    for line in lines:
        match = _MARKER.match(line)
        if match is None:
            expanded.append(line)
            continue
        confidence = " medium" if match.group("confidence") else ""
        pending.append((len(expanded), match.group("rule").upper(), confidence))
        rule = match.group("rule").upper()
        expanded.append(f"# ghostcall-expect: {rule} {_PLACEHOLDER}{confidence}".rstrip())
        expanded.append(match.group("code").rstrip())

    rewritten = [
        (line.replace(_PLACEHOLDER, str(index + 2)) if _PLACEHOLDER in line else line)
        for index, line in enumerate(expanded)
    ]
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    roots = [Path(argument) for argument in argv] or [Path("bench/hallucinations")]
    changed = 0
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if expand(path):
                changed += 1
                print(f"marked {path}")
    print(f"{changed} file(s) updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
