from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "ApiKind",
    "ApiObject",
    "CallSite",
    "Confidence",
    "Finding",
    "Parameter",
    "ParameterKind",
    "ParameterKindValues",
    "ParseError",
    "Severity",
    "Signature",
    "Unresolved",
]


class Confidence(enum.StrEnum):
    """How sure ghostcall is that a finding is real.

    ``HIGH`` findings are emitted by default; ``MEDIUM`` findings require
    ``--include-medium`` (or ``include_medium = true`` in config).
    """

    HIGH = "high"
    MEDIUM = "medium"


class Severity(enum.StrEnum):
    """Severity of a finding, mirroring common linter conventions."""

    ERROR = "error"
    WARNING = "warning"


ParameterKindValues = Literal["posonly", "pos_kw", "var_pos", "kwonly", "var_kw"]

#: Kinds that can be filled by a value passed positionally in a call.
POSITIONAL_KINDS: frozenset[str] = frozenset({"posonly", "pos_kw"})


class ParameterKind(enum.StrEnum):
    """Mirror of :class:`griffe.ParameterKind` / the AST call conventions."""

    POSITIONAL_ONLY = "posonly"
    POSITIONAL_OR_KEYWORD = "pos_kw"
    VAR_POSITIONAL = "var_pos"
    KEYWORD_ONLY = "kwonly"
    VAR_KEYWORD = "var_kw"


@dataclass(frozen=True)
class Parameter:
    """A single parameter of a resolved signature."""

    name: str
    kind: ParameterKind
    has_default: bool = False
    annotation: str | None = None

    @property
    def is_required(self) -> bool:
        return not self.has_default and self.kind not in (
            ParameterKind.VAR_POSITIONAL,
            ParameterKind.VAR_KEYWORD,
        )

    def render(self) -> str:
        """Render this parameter the way it would appear in a signature."""
        name = self.name
        if self.annotation:
            name = f"{name}: {self.annotation}"
        if self.has_default:
            name = f"{name}=..."
        if self.kind is ParameterKind.POSITIONAL_ONLY:
            return name
        if self.kind is ParameterKind.VAR_POSITIONAL:
            return f"*{name}"
        if self.kind is ParameterKind.KEYWORD_ONLY:
            return name
        if self.kind is ParameterKind.VAR_KEYWORD:
            return f"**{name}"
        return name


@dataclass(frozen=True)
class Signature:
    """A callable signature, as recovered statically."""

    parameters: tuple[Parameter, ...]
    origin: str | None = None
    """Where this signature came from, e.g. ``os.path.join`` or a stub path."""
    is_stub: bool = False
    is_class_init: bool = False
    return_annotation: str | None = None
    """Rendered return annotation, used for conservative instance tracking."""

    @property
    def positional(self) -> tuple[Parameter, ...]:
        return tuple(p for p in self.parameters if p.kind in POSITIONAL_KINDS)

    @property
    def keyword_only(self) -> tuple[Parameter, ...]:
        return tuple(p for p in self.parameters if p.kind is ParameterKind.KEYWORD_ONLY)

    @property
    def has_var_positional(self) -> bool:
        return any(p.kind is ParameterKind.VAR_POSITIONAL for p in self.parameters)

    @property
    def has_var_keyword(self) -> bool:
        return any(p.kind is ParameterKind.VAR_KEYWORD for p in self.parameters)

    @property
    def accepted_keywords(self) -> frozenset[str]:
        return frozenset(
            p.name
            for p in self.parameters
            if p.kind
            in (
                ParameterKind.POSITIONAL_OR_KEYWORD,
                ParameterKind.KEYWORD_ONLY,
            )
        )

    @property
    def required_keywords(self) -> frozenset[str]:
        return frozenset(
            p.name
            for p in self.parameters
            if p.is_required
            and p.kind in (ParameterKind.POSITIONAL_OR_KEYWORD, ParameterKind.KEYWORD_ONLY)
        )

    @property
    def max_positional(self) -> int | None:
        """Number of positional arguments accepted, or ``None`` if unbounded."""
        if self.has_var_positional:
            return None
        return len(self.positional)

    def strip_first(self) -> Signature:
        """Return the signature of the underlying function (drop ``self``/``cls``)."""
        if not self.parameters:
            return self
        first = self.parameters[0]
        if first.kind in POSITIONAL_KINDS:
            return Signature(
                parameters=self.parameters[1:],
                origin=self.origin,
                is_stub=self.is_stub,
                is_class_init=self.is_class_init,
                return_annotation=self.return_annotation,
            )
        return self

    def render(self, max_width: int | None = None, full: bool = False) -> str:
        """Render the parameter list for display.

        The compact form (used in findings) keeps parameter names and default
        markers but drops annotations, so that a wide signature still shows
        the parameters a user needs to see.  ``full=True`` keeps annotations.
        """
        parts: list[str] = []
        star_added = False
        for param in self.parameters:
            if param.kind is ParameterKind.KEYWORD_ONLY and not star_added:
                parts.append("*")
                star_added = True
            rendered = param.name
            if param.kind is ParameterKind.VAR_POSITIONAL:
                rendered = f"*{param.name}"
            elif param.kind is ParameterKind.VAR_KEYWORD:
                rendered = f"**{param.name}"
            if full and param.annotation:
                rendered = f"{rendered}: {param.annotation}"
            if param.has_default:
                rendered = f"{rendered}=..." if not full else f"{rendered} = ..."
            parts.append(rendered)
        if max_width is None or full:
            return ", ".join(parts)
        if len(", ".join(parts)) <= max_width:
            return ", ".join(parts)
        kept: list[str] = []
        used = 0
        for piece in parts:
            extra = len(piece) + (2 if kept else 0)
            if used + extra > max_width - 4:
                break
            kept.append(piece)
            used += extra
        if not kept:
            first = parts[0]
            return f"{first[: max(0, max_width - 5)]}..., ..."
        return ", ".join(kept) + ", ..."


@dataclass(frozen=True)
class ApiObject:
    """A resolved element of a library's public API.

    Instances are produced by :mod:`ghostcall.index` and are immutable; member
    lookup goes back through the index (so that nothing is loaded eagerly).
    """

    kind: ApiKind
    path: str
    """Canonical dotted path, e.g. ``pandas.io.parsers.readers.read_csv``."""
    display: str
    """Path as the user would write it, e.g. ``pandas.read_csv``."""
    module: str
    """Top-level module this object belongs to (``pandas``)."""
    signature: Signature | None = None
    overloads: tuple[Signature, ...] = ()
    deprecated: str | None = None
    derived_from: str | None = None
    """Set when this object was produced by instance tracking (medium confidence)."""
    is_instance: bool = False

    @property
    def is_callable(self) -> bool:
        return self.kind in (ApiKind.FUNCTION, ApiKind.CLASS)

    @property
    def qualified_name(self) -> str:
        return self.display


class ApiKind(enum.StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    ATTRIBUTE = "attribute"
    INSTANCE = "instance"


@dataclass(frozen=True)
class CallSite:
    """A call expression observed in the analysed source."""

    path: str
    line: int
    col: int
    positional: int
    has_var_positional: bool
    has_var_keyword: bool
    keywords: tuple[str, ...]
    node: object = field(default=None, repr=False, compare=False)


@dataclass
class Finding:
    """A single reported problem."""

    code: str
    message: str
    line: int
    col: int
    confidence: Confidence = Confidence.HIGH
    severity: Severity = Severity.ERROR
    symbol: str = ""
    installed: str = ""
    signature: str = ""
    signature_full: str = ""
    suggestion: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
    path: str = ""

    @property
    def confidence_value(self) -> str:
        return self.confidence.value

    def render_line(self) -> str:
        return f"{self.path}:{self.line}:{self.col} {self.code} {self.message}"


@dataclass
class Unresolved:
    """An expression ghostcall refused to judge, with the reason why."""

    line: int
    col: int
    symbol: str
    reason: str
    path: str = ""

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.col} unresolved: {self.symbol} ({self.reason})"


@dataclass
class ParseError:
    """A file that could not be parsed (GC000)."""

    path: str
    line: int
    col: int
    message: str


RuleCatalogEntry = tuple[str, str, str]
RuleCatalog = Sequence[RuleCatalogEntry]
