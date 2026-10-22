from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .env import Environment
from .index import ModuleEntry, Node
from .model import ApiKind

__all__ = ["IMPORT_WARNING", "introspect_module"]

IMPORT_WARNING = (
    "warning: --allow-import is enabled; ghostcall will import library code at "
    "runtime for modules it cannot read statically"
)

_INTROSPECT_SOURCE = r"""
import json, sys
name = sys.argv[1]
out = {"names": [], "error": None}
try:
    module = __import__(name, fromlist=["*"])
except BaseException as exc:  # noqa: BLE001 - report any import failure
    out["error"] = f"{type(exc).__name__}: {exc}"
else:
    names = []
    try:
        names = [n for n in dir(module) if not n.startswith("__")]
    except BaseException:  # noqa: BLE001
        names = []
    out["names"] = sorted(names)
    out["file"] = getattr(module, "__file__", None)
print(json.dumps(out))
"""


def introspect_module(env: Environment, dotted: str) -> ModuleEntry | None:
    """Import ``dotted`` in the target interpreter and record its public names."""
    try:
        proc = subprocess.run(
            [env.python, "-c", _INTROSPECT_SOURCE, dotted],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if data.get("error"):
        return None
    entry = ModuleEntry(
        name=dotted,
        origin="installed",
        filepath=Path(data["file"]) if data.get("file") else None,
        complete=True,
        dynamic=False,
    )
    for name in data.get("names") or []:
        entry.nodes[name] = Node(
            kind=ApiKind.ATTRIBUTE,
            name=name,
            path=f"{dotted}.{name}",
            members_complete=False,
        )
    entry.complete = False  # signatures are never available from dir()
    return entry
