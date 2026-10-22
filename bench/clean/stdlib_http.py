"""Valid standard-library usage: http, urllib, json, pathlib, argparse, logging."""

import argparse
import configparser
import dataclasses
import json
import logging
import os
import pathlib
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.request import Request, urlopen

# json.dumps: skipkeys / indent / sort_keys all exist; ``skip_keys`` is not a
# real parameter.
serialised = json.dumps(
    {"b": 1, "a": 2},
    indent=2,
    sort_keys=True,
    skipkeys=True,
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
)
json.loads(serialised, parse_float=float, object_hook=lambda item: item)

query = urllib.parse.urlencode({"q": "ghostcall", "page": "2"}, doseq=True, quote_via=urllib.parse.quote)
parts = urllib.parse.urlsplit("https://example.invalid/search?q=1")
print(urllib.parse.urlunsplit(parts), urllib.parse.quote_plus("a b"), query)

request = Request("https://example.invalid", data=b"{}", headers={"Accept": "application/json"}, method="POST")
with urlopen(request, timeout=5) as handle:  # noqa: S310 - benchmark fixture
    body = handle.read()

root = pathlib.Path(__file__).resolve().parent
(root / "artifact.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
for path in sorted(root.glob("*.json"), key=lambda item: item.name):
    print(path.name, path.stat().st_size)

logger = logging.getLogger("bench")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
logger.log(logging.WARNING, "hello %s", "world")


@dataclass(slots=True, frozen=True)
class Config:
    """Dataclass features: slots, frozen, field defaults."""

    name: str
    retries: int = 3
    tags: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.name}/{self.retries}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clean-http", description="demo")
    parser.add_argument("--verbose", action="store_true", default=False, help="talk more")
    parser.add_argument("--level", type=int, default=3, choices=[1, 2, 3])
    parser.add_argument("paths", nargs="*", default=[])
    args = parser.parse_args(argv)

    parser.error("no") if False else None
    settings = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
    settings.read_dict({"section": {"key": "value"}})
    print(settings.get("section", "key", fallback=""))

    counts: dict[str, int] = defaultdict(int)
    for item in args.paths:
        counts[os.path.basename(str(item))] += 1

    done = subprocess.run(  # noqa: S603 - benchmark fixture
        [sys.executable, "-c", "print('ok')"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=root,
    )
    stamp = datetime.now(timezone.utc) + timedelta(days=1)
    print(Config(name="x").label, dataclasses.asdict(Config(name="y")), done.stdout, stamp.isoformat())
    del body, args
    return 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        payload: dict[str, Any] = {"path": self.path}
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    del server
    raise SystemExit(main())
