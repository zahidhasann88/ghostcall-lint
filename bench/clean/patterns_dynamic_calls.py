"""Constructs that must never produce a finding, however tempting they look."""

import importlib
import os
from typing import TYPE_CHECKING, Any

import requests

# --- star-args: the call shape is not visible, so nothing may be reported. ----
def forward(*args: Any, **kwargs: Any) -> None:
    requests.get(*args, **kwargs)


def forward_both(extra: tuple[Any, ...], options: dict[str, Any]) -> None:
    requests.post("https://example.invalid", *extra, **options)


# --- double-star from a dict literal: keys are invisible to a static checker. -
payload = {"url": "https://example.invalid", "timeout": 5}
requests.get(**payload)
requests.get("https://example.invalid", **{"bogus_kwarg_that_is_invisible": 1})

# --- getattr / importlib: dynamic attribute access is not a call to flag. ----
method = getattr(requests, "get")
method("https://example.invalid")

module = importlib.import_module("requests")
module.get("https://example.invalid")
module.not_a_real_function("https://example.invalid")

# --- optional dependencies: guarded imports are skipped, not reported. -------
try:
    import pandas as pd  # noqa: F401
except ImportError:  # pragma: no cover
    pd = None  # type: ignore[assignment]

if pd is not None and hasattr(pd, "DataFrame"):
    print("pandas available")

# --- TYPE_CHECKING-only imports never exist at runtime; do not judge them. ---
if TYPE_CHECKING:
    import numpy as np  # noqa: F401

# --- platform / version guards for code paths the index cannot prove. --------
if os.name == "nt":
    import winreg  # noqa: F401

# --- local aliasing of a real API ------------------------------------------
from requests import get as http_get

http_get("https://example.invalid", timeout=1)

# --- calls on values whose type is unknown must stay silent -----------------
def apply(callback: Any, value: Any) -> Any:
    return callback(value, some_option=True)


def build() -> Any:
    factory = object
    return factory(unexpected=1)


# --- suppression comment: documented escape hatch --------------------------
requests.get("https://example.invalid", imaginary_option=1)  # ghostcall: ignore

# --- the project's own objects are out of scope ----------------------------
class LocalThing:
    def helper(self, value: int) -> int:
        return value

    def caller(self) -> int:
        return self.helper(1)


print(forward, forward_both, apply, build, LocalThing().caller, method, module)
