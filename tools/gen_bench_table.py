from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ghostcall.bench import BenchOutcome, run_bench  # noqa: E402
from ghostcall.env import Environment  # noqa: E402

README = ROOT / "README.md"
START = "<!-- bench:start -->"
END = "<!-- bench:end -->"


def render(python: Path | None = None, corpus: Path | None = None) -> str:
    """Render both benchmark modes as one compact markdown table."""
    env = Environment.from_python(python or _default_python())
    root = corpus or ROOT / "bench"
    default = run_bench(env, root, include_medium=False)
    medium = run_bench(env, root, include_medium=True)
    versions = " · ".join(
        f"{name} {env.version_of_import(name)}"
        for name in ("pandas", "numpy", "requests", "httpx", "sqlalchemy", "click", "rich")
    )

    def cell(outcome: BenchOutcome, rule: str) -> str:
        found, total = outcome.per_rule.get(rule, (0, 0))
        return f"{found}/{total}"

    lines = [
        f"Python {'.'.join(map(str, env.version))} · {versions}",
        "",
        "| rule | default | `--include-medium` |",
        "| --- | --- | --- |",
    ]
    for rule in sorted(set(default.per_rule) | set(medium.per_rule)):
        lines.append(f"| {rule} | {cell(default, rule)} | {cell(medium, rule)} |")
    lines.append(
        f"| **found** | **{default.true_positives}/{default.expectations}** "
        f"| **{medium.true_positives}/{medium.expectations}** |"
    )
    lines.append(
        f"| **false positives** ({medium.clean_files} clean files) "
        f"| **{default.false_positives}** | **{medium.false_positives}** |"
    )
    problems = [*medium.misses, *medium.false_positive_details]
    if problems:
        lines.append("")
        lines.extend(f"- {item}" for item in problems)
    return "\n".join(lines)


def _default_python() -> Path:
    """The pinned benchmark interpreter (``Scripts/python.exe`` on Windows)."""
    base = ROOT / "bench" / "envs" / "main"
    return base / "Scripts" / "python.exe" if os.name == "nt" else base / "bin" / "python"


def replace_block(readme: str, block: str) -> str:
    """Swap the generated block between the markers."""
    start = readme.index(START) + len(START)
    end = readme.index(END)
    return f"{readme[:start]}\n{block}\n{readme[end:]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    parser.add_argument("--python", type=Path, default=None, help="target interpreter")
    arguments = parser.parse_args()

    readme = README.read_text(encoding="utf-8")
    if START not in readme or END not in readme:
        print(f"README.md is missing the {START} / {END} markers")
        return 1
    updated = replace_block(readme, render(arguments.python))
    if arguments.check:
        if updated != readme:
            print("README.md benchmark block is out of date; run: python tools/gen_bench_table.py")
            return 1
        print("README.md benchmark block is up to date")
        return 0
    README.write_text(updated, encoding="utf-8", newline="\n")
    print("updated README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
