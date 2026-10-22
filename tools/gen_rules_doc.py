from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ghostcall.checks import RULES, RuleDoc

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "rules.md"

HEADER = """# Rules

Run `ghostcall explain CODE` for the same information in the terminal.
Every rule skips anything it cannot prove.
"""


def render() -> str:
    """Render the whole catalogue."""
    lines: list[str] = [HEADER]
    lines.append("| code | title | severity |")
    lines.append("| --- | --- | --- |")
    for code in sorted(RULES, key=_order):
        doc = RULES[code]
        lines.append(f"| `{doc.code}` | {doc.title} | {doc.severity.value} |")
    lines.append("")
    for code in sorted(RULES, key=_order):
        lines.extend(_section(RULES[code]))
    return "\n".join(lines).rstrip() + "\n"


def _order(code: str) -> int:
    return int(code[2:]) if code[2:].isdigit() else 99


def _section(doc: RuleDoc) -> list[str]:
    return [
        f"## {doc.code} — {doc.title}",
        "",
        doc.summary,
        "",
        f"**Skipped when:** {doc.when_skipped}",
        "",
        "```python",
        doc.example_bad,
        "```",
        "",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    arguments = parser.parse_args()
    rendered = render()
    if arguments.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != rendered:
            print(f"{OUTPUT} is out of date; run: python tools/gen_rules_doc.py")
            return 1
        print(f"{OUTPUT} is up to date")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT} ({len(RULES)} rules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
