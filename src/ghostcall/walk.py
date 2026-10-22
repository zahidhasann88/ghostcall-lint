from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["DEFAULT_SKIP_DIRS", "PYTHON_SUFFIXES", "GitIgnore", "collect_files"]

PYTHON_SUFFIXES = (".py", ".pyi")

#: Directories that are never worth scanning, whatever the gitignore says.
DEFAULT_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        ".tox",
        ".nox",
        ".eggs",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".direnv",
        "__pycache__",
        "node_modules",
        "site-packages",
        "build",
        "dist",
        "venv",
        "env",
        "wheelhouse",
    }
)


def _compile_gitignore_pattern(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore glob into a regular expression.

    A trailing slash is *not* baked into the expression: ``logs$/`` can never
    match anything, so directory-only patterns are represented by the caller's
    ``dir_only`` flag instead (regression-tested in tests/test_config_walk.py).
    """
    translated = pattern
    anchored = translated.startswith("/")
    translated = translated.lstrip("/")
    translated = translated.rstrip("/")
    out: list[str] = []
    index = 0
    while index < len(translated):
        char = translated[index]
        if char == "*":
            if translated.startswith("**/", index):
                out.append("(?:.*/)?")
                index += 3
                continue
            if translated.startswith("**", index):
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        if char == "[":
            end = translated.find("]", index)
            if end != -1:
                out.append(translated[index : end + 1])
                index = end + 1
                continue
        out.append(re.escape(char))
        index += 1
    body = "".join(out)
    if anchored or "/" in translated:
        regex = f"^{body}$"
    else:
        regex = f"(?:^|/){body}$"
    return re.compile(regex)


@dataclass
class _Rule:
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool
    source: Path


@dataclass
class GitIgnore:
    """Gitignore rules collected from a directory tree."""

    rules: list[_Rule] = field(default_factory=list)

    def add_file(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            negated = line.startswith("!")
            pattern = line[1:] if negated else line
            pattern = pattern.strip()
            if not pattern:
                continue
            self.rules.append(
                _Rule(
                    regex=_compile_gitignore_pattern(pattern),
                    negated=negated,
                    dir_only=pattern.endswith("/"),
                    source=path,
                )
            )

    def is_ignored(self, relative: str, *, is_dir: bool) -> bool:
        """Whether a path relative to the scan root is ignored.

        Directory-only patterns (``build/``) are asked about the directory: the
        walker prunes it, so files inside are never tested individually -- which
        is exactly git's behaviour, including that ``!`` cannot resurrect a file
        from an excluded directory.
        """
        relative = relative.replace("\\", "/")
        if relative.startswith("./"):
            relative = relative[2:]
        relative = relative.strip("/")
        ignored = False
        for rule in self.rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.regex.search(relative):
                ignored = not rule.negated
        return ignored

    @property
    def empty(self) -> bool:
        return not self.rules


def collect_files(
    paths: Sequence[Path | str],
    *,
    respect_gitignore: bool = True,
    skip_dirs: Iterable[str] = DEFAULT_SKIP_DIRS,
) -> list[Path]:
    """Expand files and directories into a sorted list of Python files."""
    skip = set(skip_dirs)
    found: dict[str, Path] = {}
    for entry in paths:
        path = Path(entry)
        if path.is_file():
            found.setdefault(str(path.resolve()), path)
            continue
        if not path.is_dir():
            continue
        ignore = GitIgnore()
        if respect_gitignore:
            ignore.add_file(path / ".gitignore")
        for file_path in _walk(path, path, ignore, skip, respect_gitignore):
            found.setdefault(str(file_path.resolve()), file_path)
    return sorted(found.values())


def _walk(
    directory: Path,
    root: Path,
    ignore: GitIgnore,
    skip: set[str],
    respect_gitignore: bool,
) -> Iterator[Path]:
    if respect_gitignore:
        nested = directory / ".gitignore"
        if nested.is_file():
            local = GitIgnore()
            local.add_file(nested)
            inherited = GitIgnore(rules=[*ignore.rules, *local.rules])
            ignore = inherited
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        relative = str(entry.relative_to(root)).replace("\\", "/")
        if entry.is_dir():
            if entry.name in skip or entry.name.endswith(".egg-info"):
                continue
            if respect_gitignore and ignore.is_ignored(relative, is_dir=True):
                continue
            yield from _walk(entry, root, ignore, skip, respect_gitignore)
        elif entry.suffix in PYTHON_SUFFIXES:
            if respect_gitignore and ignore.is_ignored(relative, is_dir=False):
                continue
            yield entry
