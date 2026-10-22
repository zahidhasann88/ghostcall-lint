"""Configuration loading and file discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghostcall.config import (
    ConfigError,
    default_source_roots,
    find_config_file,
    load_config,
    merge_config,
)
from ghostcall.walk import GitIgnore, collect_files


def test_load_config_reads_tool_ghostcall(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
        [tool.ghostcall]
        python = ".venv/bin/python"
        source-roots = ["src", "app"]
        ignore = ["GC007"]
        include-medium = true
        signature-width = 80
        ignore-modules = ["numpy"]
        extra-search-paths = ["vendor"]
        """,
        encoding="utf-8",
    )
    config = load_config(tmp_path / "pyproject.toml")
    assert config.python == ".venv/bin/python"
    assert config.source_roots == ("src", "app")
    assert config.ignore == ("GC007",)
    assert config.include_medium is True
    assert config.signature_width == 80
    assert config.ignore_modules == ("numpy",)
    assert config.extra_search_paths == ("vendor",)


def test_unknown_config_key_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ghostcall]\nignore-me-plz = true\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as error:
        load_config(tmp_path / "pyproject.toml")
    assert "ignore-me-plz" in str(error.value)


def test_missing_config_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_find_config_walks_upwards(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.ghostcall]\nignore = []\n", encoding="utf-8")
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)
    assert find_config_file(nested) == tmp_path / "pyproject.toml"
    assert find_config_file(tmp_path) == tmp_path / "pyproject.toml"


def test_merge_config_lets_cli_win(tmp_path: Path) -> None:
    from ghostcall.config import FileConfig

    base = FileConfig(path=tmp_path / "pyproject.toml", ignore=("GC001",), include_medium=False)
    merged = merge_config(base, ignore=("GC004",), include_medium=True)
    assert merged.ignore == ("GC004",)
    assert merged.include_medium is True
    assert merged.root == tmp_path
    assert merged.config_path == tmp_path / "pyproject.toml"


def test_default_source_roots_detects_src_layout(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "lib").mkdir()
    roots = default_source_roots(tmp_path)
    assert roots[0] == tmp_path
    assert tmp_path / "src" in roots
    assert tmp_path / "lib" in roots


def test_merge_config_keeps_unset_values() -> None:
    from ghostcall.config import FileConfig

    base = FileConfig(ignore=("GC001",), signature_width=80, select=("GC004",))
    merged = merge_config(base, signature_width=None)
    assert merged.signature_width == 80
    assert merged.ignore == ("GC001",)
    assert merged.select == ("GC004",)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_collect_files_respects_gitignore(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "build/\n*.generated.py\n!keep.generated.py\n")
    keep = _write(tmp_path / "app.py", "import os\n")
    generated = _write(tmp_path / "skip.generated.py", "import os\n")
    _write(tmp_path / "keep.generated.py", "import os\n")
    _write(tmp_path / "build" / "out.py", "import os\n")
    found = set(collect_files([tmp_path]))
    assert keep in found
    assert tmp_path / "keep.generated.py" in found
    assert generated not in found
    assert tmp_path / "build" / "out.py" not in found


def test_collect_files_skips_default_directories(tmp_path: Path) -> None:
    _write(tmp_path / "app.py", "")
    for name in (".venv", "node_modules", "__pycache__", ".git"):
        _write(tmp_path / name / "inside.py", "")
    assert collect_files([tmp_path]) == [tmp_path / "app.py"]


def test_collect_files_is_sorted_and_deduplicated(tmp_path: Path) -> None:
    second = _write(tmp_path / "b.py", "")
    first = _write(tmp_path / "a.py", "")
    assert collect_files([tmp_path, tmp_path]) == [first, second]


def test_collect_files_honours_nested_gitignore(tmp_path: Path) -> None:
    _write(tmp_path / "sub" / ".gitignore", "private.py\n")
    _write(tmp_path / "sub" / "public.py", "")
    _write(tmp_path / "sub" / "private.py", "")
    found = collect_files([tmp_path])
    assert found == [tmp_path / "sub" / "public.py"]


def test_gitignore_negation_and_directory_patterns(tmp_path: Path) -> None:
    rules = tmp_path / ".gitignore"
    rules.write_text("logs/\n*.tmp\n!keep.tmp\n", encoding="utf-8")
    ignore = GitIgnore()
    ignore.add_file(rules)
    # Directories are pruned while walking, so the walker asks about the directory
    # itself; files inside never get a chance to be checked (as in git).
    assert ignore.is_ignored("logs", is_dir=True)
    assert ignore.is_ignored("other.tmp", is_dir=False)
    assert not ignore.is_ignored("keep.tmp", is_dir=False)
    assert not ignore.is_ignored("main.py", is_dir=False)


def test_collect_files_accepts_stubs_and_python_files(tmp_path: Path) -> None:
    _write(tmp_path / "module.py", "")
    _write(tmp_path / "module.pyi", "")
    _write(tmp_path / "notes.txt", "")
    assert [path.name for path in collect_files([tmp_path])] == ["module.py", "module.pyi"]


def test_directory_only_gitignore_rules_prune_the_directory(tmp_path: Path) -> None:
    """``logs/`` must exclude the directory, which silently did nothing before.

    The rule used to be compiled as ``(?:^|/)logs$/`` -- an anchor followed by a
    slash, which cannot match any string.
    """
    _write(tmp_path / ".gitignore", "logs/\n")
    _write(tmp_path / "logs" / "app.log", "")
    _write(tmp_path / "logs" / "nested" / "deeper.py", "")
    _write(tmp_path / "main.py", "")
    assert collect_files([tmp_path]) == [tmp_path / "main.py"]


def test_directory_pattern_does_not_hide_a_file_of_the_same_name(tmp_path: Path) -> None:
    """``logs/`` excludes directories named logs, not a file named ``logs``."""
    _write(tmp_path / ".gitignore", "logs/\n")
    like_file = _write(tmp_path / "logs", "print('a file, not a directory')\n") if False else None
    # ``logs`` cannot be both a file and a directory here, so use a subdirectory.
    _write(tmp_path / "sub" / "logs" / "inside.py", "")
    _write(tmp_path / "sub" / "keep.py", "")
    found = collect_files([tmp_path])
    assert found == [tmp_path / "sub" / "keep.py"]
    assert like_file is None


def test_dotfiles_are_matched_by_name(tmp_path: Path) -> None:
    """``lstrip("./")`` used to mangle names such as ``.env``."""
    rules = tmp_path / ".gitignore"
    rules.write_text(".env\n", encoding="utf-8")
    ignore = GitIgnore()
    ignore.add_file(rules)
    assert ignore.is_ignored(".env", is_dir=False)
    assert ignore.is_ignored("sub/.env", is_dir=False)
    assert not ignore.is_ignored("environment.py", is_dir=False)


def test_gitignore_negation_within_a_kept_directory(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "*.py\n!keep.py\n")
    keep = _write(tmp_path / "keep.py", "")
    _write(tmp_path / "other.py", "")
    assert collect_files([tmp_path]) == [keep]
