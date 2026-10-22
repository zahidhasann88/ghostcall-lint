"""Streams, environment and paths: values whose members ghostcall must not guess.

``sys.stdout``, ``os.environ`` and ``sys.path`` are *values*, not namespaces.
Their types (``TextIO``, ``_Environ[str]``, ``list[str]``) come from annotations
ghostcall deliberately does not follow, so member access on them is skipped --
every line here is correct code in the pinned interpreter and must stay silent.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def report(name: str) -> None:
    # sys.stdout / sys.stderr are TextIO values.
    sys.stdout.write(f"hello {name}\n")
    sys.stderr.flush()
    sys.stdout.isatty()


def prompt() -> str:
    # sys.stdin is a TextIO value.
    line = sys.stdin.readline()
    return line.rstrip("\n")


def environment() -> dict[str, str]:
    # os.environ is a mapping value; os.environb is its bytes twin.
    return {
        "home": os.environ.get("HOME", ""),
        "path": os.environ["PATH"],
        "user": os.environ.get("USER", ""),
    }


def search_path() -> list[str]:
    # sys.path is a list value.
    sys.path.append(str(Path.cwd()))
    sys.argv.append("--verbose")
    return list(sys.path)


def logging_calls() -> None:
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.info("starting %s", "task")
    LOGGER.exception("failed")
    LOGGER.handlers.clear()
