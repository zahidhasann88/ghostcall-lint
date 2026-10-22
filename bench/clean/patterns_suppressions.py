"""Suppression syntaxes the checker must respect, plus stdin-style snippets."""

import requests

# A plain ignore comment on the call's own line.
requests.get("https://example.invalid", imaginary=True)  # ghostcall: ignore

# An ignore comment above the call, scoped to the rule that would fire.
# ghostcall: ignore[GC004]
requests.get("https://example.invalid", imaginary=True)

# Multi-line calls: the comment may sit on any line of the statement.
requests.get(
    "https://example.invalid",
    imaginary=True,  # ghostcall: ignore[GC004]
    timeout=1,
)

# An ignore for a *different* rule does not silence this one, so the corpus
# files in bench/hallucinations/ are the ones that carry expectations; this file
# only exercises the parser's tolerance of the markers.
requests.get("https://example.invalid", timeout=1)  # ghostcall: ignore[GC001, GC002]
