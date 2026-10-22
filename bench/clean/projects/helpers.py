"""Helper module used by ``clean_package.py``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")
MISSING_ATTRIBUTE_THAT_STILL_MUST_NOT_BE_FLAGGED = "project local"


@dataclass
class JsonStore:
    """Tiny JSON store: methods here are intentionally *not* indexed as APIs."""

    root: str = "."
    _cache: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return Path(self.root) / "store.json"

    def save(self, key: str, value: Any) -> None:
        self._cache[key] = value
        self.path.write_text(json.dumps(self._cache), encoding="utf-8")

    def load(self, key: str, default: Any = None, *, strict: bool = False) -> Any:
        return self._cache.get(key, default) if not strict else self._cache[key]


def load_settings(name: str) -> dict[str, str]:
    return {"name": name}


def with_retries(action: Callable[[], T], attempts: int = 3, delay: float = 0.0) -> T:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return action()
        except Exception as error:  # pragma: no cover
            last = error
        del delay
    raise RuntimeError(f"retries exhausted: {last}")
