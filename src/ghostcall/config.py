from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .checks import Config

__all__ = ["FileConfig", "find_config_file", "load_config", "merge_config"]

CONFIG_FILENAME = "pyproject.toml"
_CONFIG_KEYS = {
    "python",
    "source-roots",
    "ignore",
    "select",
    "per-file-ignores",
    "ignore-modules",
    "extra-search-paths",
    "include-medium",
    "signature-width",
}


class ConfigError(Exception):
    """Invalid configuration (mapped to exit code 2)."""


@dataclass
class FileConfig:
    """Contents of ``[tool.ghostcall]``."""

    path: Path | None = None
    python: str | None = None
    source_roots: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    select: tuple[str, ...] = ()
    per_file_ignores: dict[str, tuple[str, ...]] = field(default_factory=dict)
    ignore_modules: tuple[str, ...] = ()
    extra_search_paths: tuple[str, ...] = ()
    include_medium: bool = False
    signature_width: int | None = None

    @property
    def root(self) -> Path:
        return self.path.parent if self.path else Path.cwd()


def find_config_file(start: Path | None = None) -> Path | None:
    """Walk upwards from ``start`` looking for a pyproject with a ghostcall table."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None = None, *, search_from: Path | None = None) -> FileConfig:
    """Load ``[tool.ghostcall]`` from an explicit path or the nearest pyproject."""
    explicit = path is not None
    if path is None:
        path = find_config_file(search_from)
    if path is None:
        return FileConfig()
    if not path.is_file():
        if explicit:
            raise ConfigError(f"config file not found: {path}")
        return FileConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
    table = data.get("tool", {}).get("ghostcall")
    if table is None:
        if explicit:
            raise ConfigError(f"{path} has no [tool.ghostcall] table")
        return FileConfig()
    if not isinstance(table, Mapping):
        raise ConfigError("[tool.ghostcall] must be a table")
    unknown = set(table) - _CONFIG_KEYS
    if unknown:
        raise ConfigError(f"unknown key(s) in [tool.ghostcall]: {', '.join(sorted(unknown))}")
    return FileConfig(
        path=path,
        python=_as_str(table.get("python")),
        source_roots=tuple(_as_list(table.get("source-roots"), "source-roots")),
        ignore=tuple(code.upper() for code in _as_list(table.get("ignore"), "ignore")),
        select=tuple(code.upper() for code in _as_list(table.get("select"), "select")),
        per_file_ignores=_as_mapping(table.get("per-file-ignores")),
        ignore_modules=tuple(_as_list(table.get("ignore-modules"), "ignore-modules")),
        extra_search_paths=tuple(_as_list(table.get("extra-search-paths"), "extra-search-paths")),
        include_medium=bool(table.get("include-medium", False)),
        signature_width=(int(table["signature-width"]) if "signature-width" in table else None),
    )


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError("python must be a string path")
    return value


def _as_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ConfigError(f"{key} must be a string or a list of strings")


def _as_mapping(value: Any) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError("per-file-ignores must be a table")
    out: dict[str, tuple[str, ...]] = {}
    for pattern, codes in value.items():
        out[str(pattern)] = tuple(code.upper() for code in _as_list(codes, "per-file-ignores"))
    return out


def merge_config(
    file_config: FileConfig,
    *,
    python: str | None = None,
    format: str = "text",
    select: Sequence[str] = (),
    ignore: Sequence[str] = (),
    include_medium: bool = False,
    allow_import: bool = False,
    verbose: bool = False,
    jobs: int = 1,
    use_cache: bool = True,
    ignore_modules: Sequence[str] = (),
    source_roots: Sequence[str] = (),
    extra_search_paths: Sequence[str] = (),
    full_signature: bool = False,
    signature_width: int | None = None,
) -> Config:
    """Combine CLI flags with file configuration (flags win)."""
    root = file_config.root
    resolved_python = python or file_config.python
    config = Config(
        python=Path(resolved_python) if resolved_python else None,
        format=format,
        select=tuple(code.upper() for code in select) or file_config.select,
        ignore=tuple(code.upper() for code in ignore) or file_config.ignore,
        include_medium=include_medium or file_config.include_medium,
        allow_import=allow_import,
        verbose=verbose,
        jobs=jobs,
        use_cache=use_cache,
        source_roots=tuple(
            Path(root / item) if not Path(item).is_absolute() else Path(item)
            for item in (source_roots or file_config.source_roots)
        ),
        ignore_modules=tuple(ignore_modules) or file_config.ignore_modules,
        extra_search_paths=tuple(
            Path(root / item) if not Path(item).is_absolute() else Path(item)
            for item in (extra_search_paths or file_config.extra_search_paths)
        ),
        per_file_ignores=file_config.per_file_ignores,
        full_signature=full_signature,
        signature_width=signature_width or file_config.signature_width or Config().signature_width,
        config_path=file_config.path,
        root=root,
    )
    if not config.source_roots:
        config = replace(config, source_roots=default_source_roots(root))
    return config


def default_source_roots(root: Path) -> tuple[Path, ...]:
    """Source roots used when the config does not name any."""
    roots: list[Path] = [root]
    for name in ("src", "lib", "python"):
        candidate = root / name
        if candidate.is_dir():
            roots.append(candidate)
    return tuple(roots)
