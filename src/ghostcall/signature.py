from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .model import Parameter, ParameterKind, Signature

__all__ = ["CallShape", "Mismatch", "best_suggestion", "match_call"]

_SUGGESTION_CUTOFF = 72.0


@dataclass(frozen=True)
class CallShape:
    """The statically visible shape of a call expression."""

    positional: int
    keywords: tuple[str, ...]
    has_var_positional: bool
    has_var_keyword: bool

    @property
    def star_args(self) -> bool:
        return self.has_var_positional

    @property
    def star_kwargs(self) -> bool:
        return self.has_var_keyword

    @property
    def fully_visible(self) -> bool:
        """True when every argument is visible to the matcher."""
        return not self.has_var_positional and not self.has_var_keyword


@dataclass
class Mismatch:
    """A proven mismatch between a call and a signature."""

    code: str
    message: str
    suggestion: str | None = None
    detail: dict[str, str] = field(default_factory=dict)


def match_call(signatures: Sequence[Signature], call: CallShape) -> list[Mismatch]:
    """Return the mismatches of the best candidate signature.

    Returns an empty list when *any* candidate signature accepts the call, or
    when the call cannot be judged (unpacking hides arguments).
    """
    candidates = list(signatures)
    if not candidates:
        return []
    if call.star_args and call.star_kwargs:
        # ``f(*args, **kwargs)``: nothing about the call is visible.
        return []
    best: list[Mismatch] | None = None
    for signature in candidates:
        mismatches = _mismatches_for(signature, call)
        if not mismatches:
            return []
        if best is None or len(mismatches) < len(best):
            best = mismatches
    return best or []


def _mismatches_for(signature: Signature, call: CallShape) -> list[Mismatch]:
    out: list[Mismatch] = []

    if not call.star_kwargs and not signature.has_var_keyword:
        accepted = signature.accepted_keywords
        for keyword in call.keywords:
            if keyword in accepted:
                continue
            suggestion = best_suggestion(keyword, sorted(accepted))
            out.append(
                Mismatch(
                    code="GC004",
                    message=f"got an unexpected keyword argument {keyword!r}",
                    suggestion=suggestion,
                    detail={"keyword": keyword},
                )
            )

    if not call.star_args and not signature.has_var_positional:
        maximum = len(signature.positional)
        if call.positional > maximum:
            out.append(
                Mismatch(
                    code="GC005",
                    message=(
                        f"takes {maximum} positional argument"
                        f"{'s' if maximum != 1 else ''} but {call.positional} "
                        f"{'were' if call.positional != 1 else 'was'} given"
                    ),
                    detail={"expected": str(maximum), "given": str(call.positional)},
                )
            )

    missing = _missing_required(signature, call)
    if missing:
        rendered = ", ".join(repr(name) for name in missing)
        out.append(
            Mismatch(
                code="GC006",
                message=(
                    f"missing {'required argument' if len(missing) == 1 else 'required arguments'} "
                    f"{rendered}"
                ),
                detail={"missing": ", ".join(missing)},
            )
        )
    return out


def _missing_required(signature: Signature, call: CallShape) -> list[str]:
    """Required parameters that nothing in the call can satisfy."""
    keywords = set(call.keywords)
    missing: list[str] = []
    position = 0
    for parameter in signature.parameters:
        if parameter.kind is ParameterKind.VAR_POSITIONAL:
            continue
        if parameter.kind is ParameterKind.VAR_KEYWORD:
            continue
        if parameter.kind in (ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD):
            filled_positionally = position < call.positional
            position += 1
            if filled_positionally:
                continue
            if parameter.kind is ParameterKind.POSITIONAL_OR_KEYWORD and parameter.name in keywords:
                continue
            if call.star_args or call.star_kwargs:
                # ``f(*args)`` can fill this slot positionally and ``f(**kwargs)``
                # can fill it by name: nothing about the call is knowable.
                continue
            if parameter.is_required:
                missing.append(parameter.name)
            continue
        if parameter.kind is ParameterKind.KEYWORD_ONLY:
            if parameter.name in keywords:
                continue
            if call.star_kwargs:
                continue
            if parameter.is_required:
                missing.append(parameter.name)
    return missing


def best_suggestion(name: str, candidates: Sequence[str]) -> str | None:
    """Closest match for ``name`` among ``candidates`` (or ``None``).

    Public names win over private ones (``skip_blank_lines`` beats
    ``_skip_blank_lines``), and a suggestions is only returned when it is
    actually close, so that findings never carry a nonsense hint.
    """
    if not candidates:
        return None
    from rapidfuzz import fuzz, process

    pool = [c for c in candidates if not c.startswith("_")] or [
        c for c in candidates if not c.startswith("__")
    ]
    if not pool:
        return None
    match = process.extractOne(name, pool, scorer=fuzz.ratio, score_cutoff=_SUGGESTION_CUTOFF)
    return str(match[0]) if match is not None else None


def parameter_names(signature: Signature) -> list[str]:
    return [p.name for p in signature.parameters if p.kind is not ParameterKind.VAR_KEYWORD]


def required_of(signature: Signature) -> list[Parameter]:
    return [p for p in signature.parameters if p.is_required]
