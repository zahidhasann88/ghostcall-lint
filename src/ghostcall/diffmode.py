from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .checks import FileResult

__all__ = ["DiffIndex", "GitError", "changed_lines", "parse_patch"]

_HUNK = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class GitError(RuntimeError):
    """Raised when git cannot answer a question (mapped to exit code 2)."""


@dataclass
class DiffIndex:
    """Mapping of absolute file path to the set of changed line numbers."""

    lines: dict[str, set[int]] = field(default_factory=dict)
    description: str = ""

    def add(self, path: str, line: int) -> None:
        self.lines.setdefault(path, set()).add(line)

    def files(self) -> set[str]:
        return set(self.lines)

    def includes(self, path: str | Path, line: int) -> bool:
        candidates = _path_candidates(path)
        for candidate in candidates:
            found = self.lines.get(candidate)
            if found and line in found:
                return True
        return False

    def includes_file(self, path: str | Path) -> bool:
        return any(candidate in self.lines for candidate in _path_candidates(path))

    @property
    def empty(self) -> bool:
        return not self.lines


def _path_candidates(path: str | Path) -> list[str]:
    text = str(path)
    normalized = text.replace("\\", "/")
    out = [normalized, text]
    absolute = Path(text).resolve()
    out.append(str(absolute).replace("\\", "/"))
    return list(dict.fromkeys(out))


def parse_patch(patch: str) -> DiffIndex:
    """Parse the output of ``git diff -U0`` into changed line numbers."""
    index = DiffIndex()
    current: str | None = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
                continue
            if target.startswith(("a/", "b/")):
                target = target[2:]
            current = target
            index.lines.setdefault(current, set())
            continue
        if line.startswith("--- "):
            continue
        match = _HUNK.match(line)
        if match and current is not None:
            start = int(match.group("new_start"))
            count = int(match.group("new_count") or 1)
            for offset in range(count):
                index.add(current, start + offset)
    return index


def changed_lines(
    ref: str,
    *,
    staged: bool = False,
    root: Path | None = None,
    paths: Sequence[Path | str] | None = None,
    include_untracked: bool = True,
) -> DiffIndex:
    """Changed lines in the working tree (or index) relative to ``ref``."""
    repo_root = _git_root(root)
    if ref == "HEAD" and not _has_commits(repo_root):
        # The very first commit: everything staged is new, so diff against the
        # empty tree instead of failing on a HEAD that does not exist yet.
        ref = _EMPTY_TREE
    command = ["git", "-C", str(repo_root), "diff", "-U0"]
    if staged:
        command.append("--cached")
    command.append(ref)
    command.append("--")
    command.extend([str(path) for path in paths] if paths else ["*.py", "*.pyi"])
    patch = _run_git(command)
    index = parse_patch(patch)
    if not staged and include_untracked:
        untracked = _run_git(
            ["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard"]
        )
        allowed = _allowed_paths(paths, repo_root)
        for relative in untracked.splitlines():
            if not relative.endswith((".py", ".pyi")):
                continue
            if allowed is not None and not _is_allowed(relative, allowed, repo_root):
                continue
            path = repo_root / relative
            try:
                count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                continue
            for line in range(1, count + 1):
                index.add(relative, line)
    absolute: dict[str, set[int]] = {}
    for relative, lines in index.lines.items():
        absolute[relative] = lines
        absolute[str((repo_root / relative).resolve()).replace("\\", "/")] = lines
    index.lines = absolute
    shown = "HEAD (no commits yet)" if ref == _EMPTY_TREE else ref
    index.description = f"{'staged ' if staged else ''}vs {shown}"
    return index


#: git's well-known hash of the empty tree, a valid diff base in any repository.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _has_commits(repo_root: Path) -> bool:
    try:
        _run_git(["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", "HEAD"])
    except GitError:
        return False
    return True


def _allowed_paths(paths: Sequence[Path | str] | None, root: Path) -> set[str] | None:
    """Absolute paths a ``--diff`` run is restricted to, or ``None`` for all."""
    if not paths:
        return None
    out: set[str] = set()
    for entry in paths:
        candidate = Path(entry)
        if not candidate.is_absolute():
            candidate = root / candidate
        out.add(str(candidate.resolve()).replace("\\", "/"))
    return out


def _is_allowed(relative: str, allowed: set[str], root: Path) -> bool:
    """Whether an untracked file matches the ``--diff`` path restriction."""
    absolute = str((root / relative).resolve()).replace("\\", "/")
    if absolute in allowed:
        return True
    return any(absolute.startswith(f"{entry.rstrip('/')}/") for entry in allowed)


def _git_root(root: Path | None) -> Path:
    start = str(root or Path.cwd())
    try:
        out = _run_git(["git", "-C", start, "rev-parse", "--show-toplevel"])
    except GitError as exc:
        raise GitError(f"not inside a git repository: {exc}") from exc
    return Path(out.strip())


def _run_git(command: list[str]) -> str:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitError(f"could not run git: {exc}") from exc
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise GitError(message[-1] if message else f"git {' '.join(command[1:])} failed")
    return proc.stdout


def filter_results(results: Sequence[FileResult], index: DiffIndex) -> int:
    """Drop findings that are not on changed lines; returns how many were dropped."""
    dropped = 0
    for result in results:
        kept = []
        for finding in result.findings:
            if index.includes(finding.path, finding.line):
                kept.append(finding)
            else:
                dropped += 1
        result.findings = kept
    return dropped
