from __future__ import annotations

import ast as stdlib_ast
import enum
import hashlib
import json
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import griffe
import platformdirs
import typeshed_client

from .astindex import AstModuleIndexer, deprecation_note
from .env import Environment
from .model import ApiKind, Parameter, ParameterKind, Signature

__all__ = [
    "CACHE_SCHEMA",
    "GRIPPE_VERSION",
    "ApiCache",
    "ApiIndex",
    "Chain",
    "ChainStatus",
    "ClassInit",
    "MemberResult",
    "ModuleEntry",
    "Node",
]

GRIPPE_VERSION: str = getattr(griffe, "__version__", "2")

#: Bump whenever the meaning of a cached field changes, so old entries are ignored.
CACHE_SCHEMA: str = "3"

_ORIGIN = Literal["stdlib", "installed", "local", "unknown"]

_KIND_BY_PARAMETER = {
    "positional-only": ParameterKind.POSITIONAL_ONLY,
    "positional or keyword": ParameterKind.POSITIONAL_OR_KEYWORD,
    "keyword-only": ParameterKind.KEYWORD_ONLY,
    "variadic positional": ParameterKind.VAR_POSITIONAL,
    "variadic keyword": ParameterKind.VAR_KEYWORD,
}

_PROPERTY_DECORATORS = frozenset({"property", "cached_property", "functools.cached_property"})
_DEPRECATED_DECORATORS = frozenset({"deprecated", "Deprecated"})
_DATACLASS_DECORATORS = frozenset(
    {
        "dataclass",
        "dataclasses.dataclass",
    }
)
_NAMEDTUPLE_BASES = frozenset({"NamedTuple", "typing.NamedTuple", "typing_extensions.NamedTuple"})
_DYNAMIC_CLASS_BASES = frozenset(
    {
        "BaseModel",
        "DeclarativeBase",
        "DeclarativeMeta",
        "TypedDict",
        "typing.TypedDict",
        "typing_extensions.TypedDict",
        "Enum",
        "enum.Enum",
        "IntEnum",
        "enum.IntEnum",
        "StrEnum",
        "enum.StrEnum",
        "Protocol",
        "typing.Protocol",
    }
)
_DYNAMIC_CLASS_DECORATORS = frozenset(
    {
        "attr.s",
        "attr.attrs",
        "attr.define",
        "attr.frozen",
        "attr.mutable",
        "attrs.s",
        "attrs.define",
        "attrs.frozen",
        "attrs.mutable",
        "attrs.attrs",
        "pydantic.dataclasses.dataclass",
        "dataclass_transform",
    }
)

_IGNORED_BASE_LEAVES = frozenset(
    {
        "object",
        "Generic",
        "Protocol",
        "ABC",
        "ABCMeta",
        "EnumMeta",
        "type",
        "NamedTuple",
        "TypedDict",
        "Sized",
        "Container",
        "Iterable",
        "Iterator",
        "Mapping",
        "Sequence",
        "Set",
        "Callable",
        "Hashable",
        "SupportsIndex",
        "SupportsInt",
        "SupportsFloat",
        "SupportsBytes",
        "ContextManager",
        "AsyncContextManager",
        "Awaitable",
        "Coroutine",
    }
)

#: Decorators known to hand back a callable with the *same* call signature.  Any
#: other decorator must prove it through its annotations (see
#: :meth:`ApiIndex.decorator_blocker`), or the signature is not trusted.
_PASS_THROUGH_DECORATORS = frozenset(
    {
        "staticmethod",
        "classmethod",
        "property",
        "builtins.staticmethod",
        "builtins.classmethod",
        "builtins.property",
        "functools.wraps",
        "functools.cache",
        "functools.lru_cache",
        "functools.cached_property",
        "functools.total_ordering",
        "contextlib.contextmanager",
        "contextlib.asynccontextmanager",
        "abc.abstractmethod",
        "abc.abstractproperty",
        "abc.abstractclassmethod",
        "abc.abstractstaticmethod",
        "typing.final",
        "typing_extensions.final",
        "typing.override",
        "typing_extensions.override",
        "typing.overload",
        "typing_extensions.overload",
        "typing.no_type_check",
        "typing.type_check_only",
        "typing.runtime_checkable",
        "typing_extensions.runtime_checkable",
        "warnings.deprecated",
        "typing_extensions.deprecated",
        "dataclasses.dataclass",
        "enum.unique",
    }
)
#: Pass-through decorators that keep the parameters but change what a call
#: *returns*, so a return annotation no longer describes the result.
_RETURN_CHANGING_DECORATORS = frozenset({"contextmanager", "asynccontextmanager"})
_CALLABLE_NAMES = frozenset({"Callable", "typing.Callable", "collections.abc.Callable"})

_MAX_MEMBER_DEPTH = 4
_MAX_NODES_PER_MODULE = 4000
_MAX_WILDCARD_HOPS = 3


@dataclass
class Node:
    """One element of a module's API (function, class, attribute, alias)."""

    kind: ApiKind
    name: str
    path: str
    target: str | None = None
    """For names imported from elsewhere: the canonical dotted target."""
    signature: Signature | None = None
    """For functions: the call signature.  For classes: the constructor."""
    overloads: tuple[Signature, ...] = ()
    bases: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    deprecated: str | None = None
    members_complete: bool = True
    """False when we cannot know whether the member set is exhaustive."""
    init_synthesized: bool = False
    init_skip_reason: str | None = None
    constructor_override: str | None = None
    """Set when calling the class does not just run ``__init__`` (metaclass, ``__new__``)."""
    is_static: bool = False
    is_classmethod: bool = False
    is_property: bool = False
    annotation: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "path": self.path,
            "target": self.target,
            "signature": _signature_to_dict(self.signature),
            "overloads": [_signature_to_dict(s) for s in self.overloads],
            "bases": list(self.bases),
            "decorators": list(self.decorators),
            "deprecated": self.deprecated,
            "members_complete": self.members_complete,
            "init_synthesized": self.init_synthesized,
            "init_skip_reason": self.init_skip_reason,
            "constructor_override": self.constructor_override,
            "is_static": self.is_static,
            "is_classmethod": self.is_classmethod,
            "is_property": self.is_property,
            "annotation": self.annotation,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Node:
        return cls(
            kind=ApiKind(data["kind"]),
            name=data["name"],
            path=data["path"],
            target=data.get("target"),
            signature=_signature_from_dict(data.get("signature")),
            overloads=tuple(
                sig
                for sig in (_signature_from_dict(item) for item in data.get("overloads", []))
                if sig is not None
            ),
            bases=tuple(data.get("bases", ())),
            decorators=tuple(data.get("decorators", ())),
            deprecated=data.get("deprecated"),
            members_complete=bool(data.get("members_complete", True)),
            init_synthesized=bool(data.get("init_synthesized", False)),
            init_skip_reason=data.get("init_skip_reason"),
            constructor_override=data.get("constructor_override"),
            is_static=bool(data.get("is_static", False)),
            is_classmethod=bool(data.get("is_classmethod", False)),
            is_property=bool(data.get("is_property", False)),
            annotation=data.get("annotation"),
        )


def _signature_to_dict(signature: Signature | None) -> dict[str, Any] | None:
    if signature is None:
        return None
    return {
        "params": [
            [p.name, p.kind.value, p.has_default, p.annotation] for p in signature.parameters
        ],
        "origin": signature.origin,
        "is_stub": signature.is_stub,
        "is_class_init": signature.is_class_init,
        "returns": signature.return_annotation,
    }


def _signature_from_dict(data: Mapping[str, Any] | None) -> Signature | None:
    if not data:
        return None
    params = tuple(
        Parameter(
            name=str(item[0]),
            kind=ParameterKind(item[1]),
            has_default=bool(item[2]),
            annotation=item[3] if len(item) > 3 else None,
        )
        for item in data.get("params", ())
    )
    return Signature(
        parameters=params,
        origin=data.get("origin"),
        is_stub=bool(data.get("is_stub", False)),
        is_class_init=bool(data.get("is_class_init", False)),
        return_annotation=data.get("returns"),
    )


@dataclass
class ModuleEntry:
    """A parsed module: the unit of the API index."""

    name: str
    origin: _ORIGIN
    filepath: Path | None = None
    nodes: dict[str, Node] = field(default_factory=dict)
    wildcards: tuple[str, ...] = ()
    """Modules star-imported into this one."""
    complete: bool = True
    """False when absence of a member proves nothing (dynamic or unresolved)."""
    dynamic: bool = False
    """True for modules whose namespace is produced at runtime."""
    is_package: bool = False
    truncated: bool = False
    """True when member collection hit its budget."""
    _wildcards_expanded: bool = field(default=False, repr=False)

    def member(self, relative: str) -> Node | None:
        return self.nodes.get(relative)

    def top_level_names(self) -> set[str]:
        return {key.split(".", 1)[0] for key in self.nodes}

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "origin": self.origin,
            "filepath": str(self.filepath) if self.filepath else None,
            "nodes": {key: node.as_dict() for key, node in self.nodes.items()},
            "wildcards": list(self.wildcards),
            "complete": self.complete,
            "dynamic": self.dynamic,
            "is_package": self.is_package,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModuleEntry:
        return cls(
            name=str(data["name"]),
            origin=data["origin"],
            filepath=Path(data["filepath"]) if data.get("filepath") else None,
            nodes={key: Node.from_dict(value) for key, value in data["nodes"].items()},
            wildcards=tuple(data.get("wildcards", ())),
            complete=bool(data.get("complete", True)),
            dynamic=bool(data.get("dynamic", False)),
            is_package=bool(data.get("is_package", False)),
            truncated=bool(data.get("truncated", False)),
        )


@dataclass(frozen=True)
class ClassInit:
    """Outcome of resolving a class's constructor signature."""

    signature: Signature | None
    reason: str | None = None
    synthesized: bool = False

    @property
    def known(self) -> bool:
        return self.signature is not None


class ApiCache:
    """On-disk cache of parsed module indexes, keyed by file stat + version."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            Path(root) if root is not None else Path(platformdirs.user_cache_dir("ghostcall"))
        )

    def key(self, entry_name: str, filepath: Path | None, extra: str = "") -> str:
        parts: list[str] = [entry_name, extra, GRIPPE_VERSION, CACHE_SCHEMA]
        if filepath is not None:
            try:
                stat = filepath.stat()
                parts.extend([str(filepath), str(stat.st_mtime_ns), str(stat.st_size)])
            except OSError:
                parts.append(str(filepath))
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    def _path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> ModuleEntry | None:
        try:
            data = json.loads(self._path_for(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            return ModuleEntry.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def set(self, key: str, entry: ModuleEntry) -> None:
        path = self._path_for(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entry.as_dict()), encoding="utf-8")
        except OSError:
            return

    def clear(self) -> int:
        """Delete every cached entry, returning the number of files removed."""
        if not self.root.exists():
            return 0
        count = 0
        for path in sorted(self.root.rglob("*.json")):
            try:
                path.unlink()
                count += 1
            except OSError:
                continue
        for path in sorted(self.root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and path != self.root:
                try:
                    path.rmdir()
                except OSError:
                    continue
        return count


def _module_paths(base: Path, tail: Sequence[str], suffixes: Sequence[str]) -> list[Path]:
    """Candidate files for a module, in preference order (flat file, then package)."""
    out: list[Path] = []
    if tail:
        parent = base.joinpath(*tail[:-1])
        package = parent / tail[-1]
        for suffix in suffixes:
            out.append(parent / f"{tail[-1]}{suffix}")
        for suffix in suffixes:
            out.append(package / f"__init__{suffix}")
    else:
        for suffix in suffixes:
            out.append(base.with_name(base.name + suffix))
        for suffix in suffixes:
            out.append(base / f"__init__{suffix}")
    return out


_DYNAMIC_PATTERNS = (
    re.compile(r"^\s*setattr\s*\(\s*globals\s*\(", re.MULTILINE),
    re.compile(r"^\s*globals\s*\(\s*\)\s*\[", re.MULTILINE),
    re.compile(r"^\s*globals\s*\(\s*\)\s*\.update\s*\(", re.MULTILINE),
    re.compile(r"^\s*exec\s*\(", re.MULTILINE),
    re.compile(r"^\s*__path__\s*=", re.MULTILINE),
)


def _is_dynamic_module(module: griffe.Module, code: str) -> bool:
    """Whether a module's namespace is produced at runtime."""
    if "__getattr__" in module.members or "__dir__" in module.members:
        return True
    return any(pattern.search(code) for pattern in _DYNAMIC_PATTERNS)


def _decorator_path(decorator: griffe.Decorator) -> str:
    try:
        return decorator.callable_path
    except Exception:
        return str(decorator.value)


def _decorator_leaf(decorator: str) -> str:
    return decorator.split("(", 1)[0].rsplit(".", 1)[-1]


def _griffe_deprecated(member: object, decorators: Sequence[str]) -> str | None:
    for decorator in decorators:
        if _decorator_leaf(decorator) in _DEPRECATED_DECORATORS:
            return f"decorated with @{decorator}"
    docstring = getattr(member, "docstring", None)
    value = getattr(docstring, "value", None)
    if value:
        return deprecation_note(value)
    return None


def _is_stub_entry(entry: ModuleEntry) -> bool:
    """Stubs (typeshed, ``.pyi`` files) declare the signature callers see."""
    if entry.origin == "stdlib":
        return True
    return entry.filepath is not None and entry.filepath.suffix == ".pyi"


def _decorator_target(entry: ModuleEntry, name: str) -> str:
    """Canonical dotted path of a decorator expression written in ``entry``."""
    head, _, rest = name.partition(".")
    local = entry.nodes.get(head)
    if local is None:
        return name
    base = local.target or local.path
    return f"{base}.{rest}" if rest else base


def _normalise_annotation(text: str | None) -> str:
    return (text or "").replace(" ", "").strip("\"'")


def _is_identity_decorator(signature: Signature) -> bool:
    """``(func: F) -> F`` or ``(...) -> Callable[[F], F]``: the callable comes back as-is."""
    returns = _normalise_annotation(signature.return_annotation)
    if not returns:
        return False
    try:
        parsed = stdlib_ast.parse(returns, mode="eval").body
    except SyntaxError:
        return False
    positional = signature.positional
    if positional and _normalise_annotation(positional[0].annotation) == returns:
        return _names_one_callable(parsed)
    if not isinstance(parsed, stdlib_ast.Subscript):
        return False
    if stdlib_ast.unparse(parsed.value) not in _CALLABLE_NAMES:
        return False
    arguments = parsed.slice
    if not isinstance(arguments, stdlib_ast.Tuple) or len(arguments.elts) != 2:
        return False
    accepted, produced = arguments.elts
    if not isinstance(accepted, stdlib_ast.List) or len(accepted.elts) != 1:
        return False
    same = stdlib_ast.unparse(accepted.elts[0]) == stdlib_ast.unparse(produced)
    return same and _names_one_callable(produced)


def _returns_its_argument(
    definition: stdlib_ast.FunctionDef | stdlib_ast.AsyncFunctionDef, *, is_method: bool
) -> bool:
    """Whether a decorator's source hands back the very callable it was given.

    Accepts ``def deco(func): ...; return func`` (and a class ``__call__`` of that
    shape), and a factory ``def factory(...): def deco(func): ...; return func;
    return deco``.  Anything else -- a wrapper, a rebinding of the argument, no
    ``return`` at all -- is not proof.
    """
    if _returns_first_parameter(definition, skip_self=is_method):
        return True
    if is_method:
        return False
    nested = {
        statement.name: statement
        for statement in definition.body
        if isinstance(statement, stdlib_ast.FunctionDef | stdlib_ast.AsyncFunctionDef)
    }
    returned = _returned_values(definition)
    if not returned or not all(isinstance(value, stdlib_ast.Name) for value in returned):
        return False
    names = {value.id for value in returned if isinstance(value, stdlib_ast.Name)}
    return all(
        name in nested and _returns_first_parameter(nested[name], skip_self=False) for name in names
    )


def _returns_first_parameter(
    definition: stdlib_ast.FunctionDef | stdlib_ast.AsyncFunctionDef, *, skip_self: bool
) -> bool:
    parameters = [*definition.args.posonlyargs, *definition.args.args]
    if skip_self:
        parameters = parameters[1:]
    if not parameters:
        return False
    name = parameters[0].arg
    returned = _returned_values(definition)
    if not returned:
        return False
    if not all(isinstance(value, stdlib_ast.Name) and value.id == name for value in returned):
        return False
    # ``func = wrap(func); return func`` returns something else under the same name.
    return not any(
        isinstance(node, stdlib_ast.Name)
        and node.id == name
        and isinstance(node.ctx, stdlib_ast.Store | stdlib_ast.Del)
        for node in _own_nodes(definition)
    )


def _returned_values(
    definition: stdlib_ast.FunctionDef | stdlib_ast.AsyncFunctionDef,
) -> list[stdlib_ast.expr | None]:
    """Values of the ``return`` statements of ``definition`` itself (not nested defs)."""
    return [node.value for node in _own_nodes(definition) if isinstance(node, stdlib_ast.Return)]


def _own_nodes(
    definition: stdlib_ast.FunctionDef | stdlib_ast.AsyncFunctionDef,
) -> list[stdlib_ast.AST]:
    """Every node in a function body, without descending into nested scopes."""
    scopes = (
        stdlib_ast.FunctionDef,
        stdlib_ast.AsyncFunctionDef,
        stdlib_ast.ClassDef,
        stdlib_ast.Lambda,
    )
    out: list[stdlib_ast.AST] = []
    pending: list[stdlib_ast.AST] = list(definition.body)
    while pending:
        node = pending.pop()
        out.append(node)
        if isinstance(node, scopes):
            continue
        pending.extend(stdlib_ast.iter_child_nodes(node))
    return out


#: Types that say "some callable" without saying *which*: a decorator typed
#: ``(Callable[..., Any]) -> Callable[..., Any]`` proves nothing about parameters.
_VAGUE_TYPES = frozenset({"Callable", "Any", "object", "type", "function", "FunctionType"})


def _names_one_callable(annotation: stdlib_ast.expr) -> bool:
    """A TypeVar (``F``) or ``Callable[P, R]``: the type pins the exact callable."""
    if isinstance(annotation, stdlib_ast.Name | stdlib_ast.Attribute):
        leaf = stdlib_ast.unparse(annotation).rsplit(".", 1)[-1]
        return leaf not in _VAGUE_TYPES
    if isinstance(annotation, stdlib_ast.Subscript):
        if stdlib_ast.unparse(annotation.value) not in _CALLABLE_NAMES:
            return False
        arguments = annotation.slice
        if not isinstance(arguments, stdlib_ast.Tuple) or len(arguments.elts) != 2:
            return False
        parameters = arguments.elts[0]
        # ``Callable[P, R]`` (ParamSpec) is exact; ``Callable[..., R]`` is not.
        return isinstance(parameters, stdlib_ast.Name)
    return False


def _signature_from_griffe(func: griffe.Function, *, is_stub: bool) -> Signature:
    parameters: list[Parameter] = []
    try:
        raw_params = list(func.parameters)
    except Exception:
        raw_params = []
    for param in raw_params:
        kind = _KIND_BY_PARAMETER.get(str(param.kind.value), ParameterKind.POSITIONAL_OR_KEYWORD)
        annotation: str | None = None
        try:
            annotation = str(param.annotation) if param.annotation is not None else None
        except Exception:
            annotation = None
        has_default = param.default is not None and kind not in (
            ParameterKind.VAR_POSITIONAL,
            ParameterKind.VAR_KEYWORD,
        )
        parameters.append(
            Parameter(
                name=param.name,
                kind=kind,
                has_default=has_default,
                annotation=annotation,
            )
        )
    origin: str | None = None
    try:
        origin = func.canonical_path
    except Exception:
        origin = getattr(func, "name", None)
    returns: str | None = None
    try:
        returns = str(func.returns) if getattr(func, "returns", None) is not None else None
    except Exception:
        returns = None
    return Signature(
        parameters=tuple(parameters), origin=origin, is_stub=is_stub, return_annotation=returns
    )


def _class_needs_dynamic_init(member: griffe.Class) -> str | None:
    """Return a skip reason when a class's constructor cannot be trusted."""
    for base in member.bases:
        name = getattr(base, "canonical_path", None) or getattr(base, "name", "")
        leaf = str(name).rsplit(".", 1)[-1]
        if str(name) in _DYNAMIC_CLASS_BASES or leaf in _DYNAMIC_CLASS_BASES:
            return f"class inherits from {leaf} (framework model)"
    for decorator in member.decorators:
        path = _decorator_path(decorator)
        if path in _DYNAMIC_CLASS_DECORATORS:
            return f"class decorated with @{path} (framework model)"
    return _constructor_override(member)


class GriffeProvider:
    """Static loader for installed distributions, backed by griffe."""

    origin: _ORIGIN = "installed"

    def __init__(self, env: Environment, cache: ApiCache | None = None) -> None:
        self.env = env
        self.cache = cache
        self._chain: dict[str, griffe.Module] = {}

    def find_module_file(self, dotted: str) -> Path | None:
        parts = dotted.split(".")
        top, tail = parts[0], parts[1:]
        seen: set[str] = set()
        for root in self.env.all_search_paths:
            if str(root) in seen:
                continue
            seen.add(str(root))
            found = self._find_in_root(root, top, tail)
            if found is not None:
                return found
        return None

    def _find_in_root(self, root: Path, top: str, tail: Sequence[str]) -> Path | None:
        groups = (
            *(_module_paths(root / f"{top}-stubs", tail, (".pyi",))),
            *(_module_paths(root / top, tail, (".pyi",))),
            *(_module_paths(root / top, tail, (".py",))),
        )
        for candidate in groups:
            if candidate.is_file():
                return candidate
        return None

    def is_installed_dir(self, dotted: str) -> bool:
        parts = dotted.split(".")
        return any(root.joinpath(*parts).is_dir() for root in self.env.all_search_paths)

    def has_compiled_module(self, dotted: str) -> bool:
        parts = dotted.split(".")
        top, tail = parts[0], parts[1:]
        for root in self.env.all_search_paths:
            base = root.joinpath(*parts) if tail else root / top
            if base.with_suffix(".so").exists() or base.with_suffix(".pyd").exists():
                return True
            # Real extension modules carry an ABI tag:
            # ``foo.cpython-312-x86_64-linux-gnu.so``, ``foo.cp312-win_amd64.pyd``.
            if base.parent.is_dir() and (
                any(base.parent.glob(f"{base.name}.*.so"))
                or any(base.parent.glob(f"{base.name}.*.pyd"))
            ):
                return True
            if base.is_dir():
                has_extension = any(
                    child.suffix in {".so", ".pyd"} for child in base.glob("*") if child.is_file()
                )
                if has_extension:
                    return True
        return False

    def get_module(self, dotted: str, extra_key: str = "") -> ModuleEntry | None:
        filepath = self.find_module_file(dotted)
        if filepath is None:
            if self.has_compiled_module(dotted) or self.is_installed_dir(dotted):
                # Compiled extension or namespace package: installed, but its
                # API cannot be read statically.
                return ModuleEntry(
                    name=dotted,
                    origin=self.origin,
                    filepath=None,
                    complete=False,
                    dynamic=True,
                )
            return None
        key = ""
        if self.cache is not None:
            key = self.cache.key(dotted, filepath, extra_key)
            cached = self.cache.get(key)
            if cached is not None:
                return cached
        entry = self._parse(dotted, filepath)
        if self.cache is not None and key:
            self.cache.set(key, entry)
        return entry

    def _parse(self, dotted: str, filepath: Path) -> ModuleEntry:
        try:
            code = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ModuleEntry(name=dotted, origin=self.origin, filepath=filepath, complete=False)
        if filepath.suffix == ".pyi":
            # Stubs are indexed with ghostcall's own AST indexer: griffe drops
            # functions that only have ``@overload`` declarations, which is the
            # dominant style in typed stubs.
            return self._parse_stub(dotted, filepath, code)
        return self._parse_source(dotted, filepath, code)

    def _parse_stub(self, dotted: str, filepath: Path, code: str) -> ModuleEntry:
        try:
            tree = stdlib_ast.parse(code, filename=str(filepath))
        except (SyntaxError, ValueError):
            return ModuleEntry(
                name=dotted, origin=self.origin, filepath=filepath, complete=False, dynamic=True
            )
        indexer = AstModuleIndexer(
            dotted,
            origin=self.origin,
            filepath=filepath,
            is_package=filepath.name == "__init__.pyi",
            source=code,
        )
        return indexer.build(tree)

    def _parse_source(self, dotted: str, filepath: Path, code: str) -> ModuleEntry:
        module = self._visit(dotted, filepath, code)
        if module is None:
            return ModuleEntry(
                name=dotted, origin=self.origin, filepath=filepath, complete=False, dynamic=True
            )
        entry = ModuleEntry(
            name=dotted,
            origin=self.origin,
            filepath=filepath,
            is_package=filepath.name == "__init__.py",
        )
        entry.dynamic = _is_dynamic_module(module, code)
        _collect_members(entry, module, dotted, is_stub=False)
        entry.wildcards = tuple(
            member.wildcard
            for member in module.members.values()
            if isinstance(member, griffe.Alias) and member.wildcard
        )
        if entry.dynamic:
            entry.complete = False
        # Backstop: griffe can silently drop declarations (for example
        # functions declared only through ``@overload`` inside a .py source).
        # Anything the AST sees but griffe missed becomes an opaque node, which
        # is enough for existence checks and is ignored by signature checks.
        _augment_with_ast(entry, dotted, filepath, code, self.origin)
        return entry

    def _visit(self, dotted: str, filepath: Path, code: str) -> griffe.Module | None:
        """Visit a module with its parent chain, so relative imports resolve right."""
        try:
            parent, leaf = self._parent_chain(dotted)
            return griffe.visit(
                leaf,
                filepath=filepath,
                code=code,
                parent=parent,
                modules_collection=None,
                lines_collection=griffe.LinesCollection(),
            )
        except Exception:
            return None

    def _parent_chain(self, dotted: str) -> tuple[griffe.Module | None, str]:
        """Build (and cache) the griffe parent chain of a dotted module."""
        parts = dotted.split(".")
        parent: griffe.Module | None = None
        for index in range(1, len(parts)):
            name = ".".join(parts[:index])
            parent = self._parent_module(name, parent)
        return parent, parts[-1]

    def _parent_module(self, name: str, parent: griffe.Module | None) -> griffe.Module:
        cached = self._chain.get(name)
        if cached is not None:
            return cached
        leaf = name.rsplit(".", 1)[-1]
        real_filepath = self.find_module_file(name)
        filepath = real_filepath if real_filepath is not None else Path(f"<{name}>")
        code = ""
        if real_filepath is not None:
            try:
                code = real_filepath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                code = ""
        try:
            module = griffe.visit(
                leaf,
                filepath=filepath,
                code=code,
                parent=parent,
                modules_collection=None,
                lines_collection=griffe.LinesCollection(),
            )
        except Exception:
            module = griffe.Module(leaf, filepath=filepath, parent=parent)
        self._chain[name] = module
        return module


def _augment_with_ast(
    entry: ModuleEntry, dotted: str, filepath: Path, code: str, origin: str
) -> None:
    """Add names that the AST sees but the griffe parse missed."""
    try:
        tree = stdlib_ast.parse(code, filename=str(filepath))
    except (SyntaxError, ValueError):
        entry.complete = False
        return
    indexer = AstModuleIndexer(
        dotted,
        origin=origin,
        filepath=filepath,
        is_package=filepath.name == "__init__.py",
        source=None,
    )
    raw = indexer.build(tree)
    for key, node in raw.nodes.items():
        if key in entry.nodes:
            continue
        if "." in key:
            continue
        entry.nodes[key] = node
    for wildcard in raw.wildcards:
        if wildcard not in entry.wildcards:
            entry.wildcards = (*entry.wildcards, wildcard)


def _collect_members(
    entry: ModuleEntry, module: griffe.Module, dotted: str, *, is_stub: bool
) -> None:
    """Flatten a griffe module (including nested classes) into ``entry.nodes``."""
    budget = _MAX_NODES_PER_MODULE

    def walk(obj: griffe.Module | griffe.Class, prefix: str, depth: int) -> None:
        nonlocal budget
        if depth > _MAX_MEMBER_DEPTH:
            return
        for name, member in list(obj.members.items()):
            if name in {"__all__", "__doc__", "__file__", "__path__", "__spec__", "__builtins__"}:
                continue
            if budget <= 0:
                entry.truncated = True
                return
            budget -= 1
            relative = f"{prefix}.{name}" if prefix else name
            node = _node_from_griffe(relative, member, dotted, is_stub=is_stub)
            if node is None:
                continue
            entry.nodes[relative] = node
            if isinstance(member, griffe.Class):
                walk(member, relative, depth + 1)

    walk(module, "", 0)


def _node_from_griffe(relative: str, member: object, dotted: str, *, is_stub: bool) -> Node | None:
    full_path = f"{dotted}.{relative}"
    if isinstance(member, griffe.Alias):
        if member.wildcard:
            return None
        return Node(
            kind=ApiKind.MODULE,
            name=relative.rsplit(".", 1)[-1],
            path=full_path,
            target=member.target_path,
            members_complete=False,
        )
    if isinstance(member, griffe.Function):
        decorators = tuple(_decorator_path(d) for d in member.decorators)
        return Node(
            kind=ApiKind.FUNCTION,
            name=member.name,
            path=full_path,
            signature=_signature_from_griffe(member, is_stub=is_stub),
            overloads=tuple(
                _signature_from_griffe(o, is_stub=is_stub) for o in (member.overloads or ())
            ),
            decorators=decorators,
            deprecated=_griffe_deprecated(member, decorators),
            is_static=any(_decorator_leaf(d) == "staticmethod" for d in decorators),
            is_classmethod=any(_decorator_leaf(d) == "classmethod" for d in decorators),
            is_property=any(_decorator_leaf(d) in _PROPERTY_DECORATORS for d in decorators),
        )
    if isinstance(member, griffe.Class):
        return _class_node(member, full_path, is_stub=is_stub)
    if isinstance(member, griffe.Attribute):
        # griffe 2 represents ``@property`` as an Attribute without decorators.
        decorators = tuple(_decorator_path(d) for d in getattr(member, "decorators", ()))
        annotation: str | None = None
        try:
            annotation = str(member.annotation) if member.annotation is not None else None
        except Exception:
            annotation = None
        return Node(
            kind=ApiKind.ATTRIBUTE,
            name=member.name,
            path=full_path,
            decorators=decorators,
            deprecated=_griffe_deprecated(member, decorators),
            is_property=bool({_decorator_leaf(d) for d in decorators} & _PROPERTY_DECORATORS),
            annotation=annotation,
        )
    return None


def _class_node(member: griffe.Class, full_path: str, *, is_stub: bool) -> Node:
    decorators = tuple(_decorator_path(d) for d in member.decorators)
    bases = tuple(
        getattr(base, "canonical_path", None) or getattr(base, "name", None) or str(base)
        for base in member.bases
    )
    node = Node(
        kind=ApiKind.CLASS,
        name=member.name,
        path=full_path,
        bases=bases,
        decorators=decorators,
        deprecated=_griffe_deprecated(member, decorators),
        members_complete=not (
            "__getattr__" in member.members or "__getattribute__" in member.members
        ),
    )
    node.constructor_override = _constructor_override(member)
    node.signature, node.init_synthesized, node.init_skip_reason = _resolve_init(
        member, bases, decorators, is_stub=is_stub
    )
    return node


#: Metaclasses that do not change what calling the class accepts.
_BENIGN_METACLASSES = frozenset({"type", "ABCMeta"})


def metaclass_override(metaclass: str | None) -> str | None:
    """The skip reason for a class created by ``metaclass``, if it is not benign."""
    if not metaclass:
        return None
    if metaclass.rsplit(".", 1)[-1] in _BENIGN_METACLASSES:
        return None
    return f"class uses a custom metaclass ({metaclass})"


def _constructor_override(member: griffe.Class) -> str | None:
    """Why calling ``member`` may not be governed by its ``__init__``."""
    keywords = getattr(member, "keywords", None) or {}
    metaclass = keywords.get("metaclass") if isinstance(keywords, Mapping) else None
    reason = metaclass_override(str(metaclass) if metaclass is not None else None)
    if reason is not None:
        return reason
    if isinstance(member.members.get("__new__"), griffe.Function):
        return "class defines __new__"
    return None


def _resolve_init(
    member: griffe.Class,
    bases: tuple[str, ...],
    decorators: tuple[str, ...],
    *,
    is_stub: bool,
) -> tuple[Signature | None, bool, str | None]:
    """Work out the constructor signature of a class, or why we cannot."""
    override = _constructor_override(member)
    if override is not None:
        return None, False, override
    init = member.members.get("__init__")
    if isinstance(init, griffe.Function):
        signature = _signature_from_griffe(init, is_stub=is_stub).strip_first()
        return signature, False, None
    reason = _class_needs_dynamic_init(member)
    if reason is not None:
        return None, False, reason
    leaf_decorators = {_decorator_leaf(d) for d in decorators}
    is_dataclass = bool(leaf_decorators & {"dataclass"}) and any(
        d in _DATACLASS_DECORATORS or _decorator_leaf(d) == "dataclass" for d in decorators
    )
    base_leaves = {b.rsplit(".", 1)[-1] for b in bases}
    is_namedtuple = bool(base_leaves & {"NamedTuple"}) or any(b in _NAMEDTUPLE_BASES for b in bases)
    if is_dataclass or is_namedtuple:
        synthesized = _synthesize_generated_init(member)
        if synthesized is None:
            return None, False, "generated __init__ could not be derived statically"
        return synthesized, True, None
    return None, False, "class has no statically-known constructor"


def _synthesize_generated_init(member: griffe.Class) -> Signature | None:
    """Derive a dataclass / NamedTuple ``__init__`` from annotated attributes."""
    parameters: list[Parameter] = []
    for name, child in member.members.items():
        if name.startswith("__"):
            continue
        if not isinstance(child, griffe.Attribute):
            continue
        try:
            annotation = str(child.annotation) if child.annotation is not None else None
        except Exception:
            annotation = None
        if annotation is None:
            continue
        if "ClassVar" in annotation:
            continue
        value = None
        try:
            value = child.value
        except Exception:
            value = None
        if value is not None:
            text = str(value)
            if "field(" in text and "init=False" in text.replace(" ", ""):
                return None
        parameters.append(
            Parameter(
                name=name,
                kind=ParameterKind.POSITIONAL_OR_KEYWORD,
                has_default=value is not None,
                annotation=annotation,
            )
        )
    if not parameters:
        return None
    return Signature(parameters=tuple(parameters), origin=member.path, is_stub=False)


class TypeshedProvider:
    """Standard-library API provider backed by version-filtered typeshed stubs."""

    origin: _ORIGIN = "stdlib"

    def __init__(self, env: Environment) -> None:
        self.env = env
        self._context = typeshed_client.get_search_context(
            search_path=[],
            version=env.version,
            platform=env.platform,
            implementation_name=env.implementation_name,
            implementation_version=env.implementation_version,
        )
        self._cache: dict[str, ModuleEntry | None] = {}

    def get_module(self, dotted: str) -> ModuleEntry | None:
        if dotted not in self._cache:
            self._cache[dotted] = self._load(dotted)
        return self._cache[dotted]

    def _load(self, dotted: str) -> ModuleEntry | None:
        try:
            tree = typeshed_client.get_stub_ast(dotted, search_context=self._context)
            stub_path = typeshed_client.get_stub_file(dotted, search_context=self._context)
        except Exception:
            return None
        if tree is None:
            return None
        path = Path(stub_path) if stub_path else None
        indexer = AstModuleIndexer(
            dotted,
            version=self.env.version,
            platform=self.env.platform,
            origin=self.origin,
            filepath=path,
            is_package=bool(path and path.name == "__init__.pyi"),
        )
        return indexer.build(tree)


class LocalProvider:
    """Project-local modules, indexed only so we never flag the project's own code."""

    origin: _ORIGIN = "local"

    def __init__(self, env: Environment) -> None:
        self.env = env
        self._cache: dict[str, ModuleEntry | None] = {}

    def get_module(self, dotted: str) -> ModuleEntry | None:
        if dotted not in self._cache:
            self._cache[dotted] = self._load(dotted)
        return self._cache[dotted]

    def _load(self, dotted: str) -> ModuleEntry | None:
        parts = dotted.split(".")
        base = self.env.local_module_path(dotted)
        if base is None:
            return None
        rest = parts[1:]
        target: Path | None = None
        if base.is_file():
            if not rest:
                target = base
        else:
            if rest:
                for candidate in (
                    base.joinpath(*rest[:-1], f"{rest[-1]}.py"),
                    base.joinpath(*rest, "__init__.py"),
                ):
                    if candidate.is_file():
                        target = candidate
                        break
            else:
                candidate = base / "__init__.py"
                if candidate.is_file():
                    target = candidate
                else:
                    return ModuleEntry(
                        name=dotted,
                        origin=self.origin,
                        filepath=base,
                        complete=False,
                        dynamic=True,
                        is_package=True,
                    )
        if target is None or not target.is_file():
            return None
        try:
            code = target.read_text(encoding="utf-8", errors="replace")
            module = griffe.visit(
                dotted,
                filepath=target,
                code=code,
                modules_collection=None,
                lines_collection=griffe.LinesCollection(),
            )
        except Exception:
            return ModuleEntry(name=dotted, origin=self.origin, filepath=target, complete=False)
        entry = ModuleEntry(
            name=dotted,
            origin=self.origin,
            filepath=target,
            is_package=target.name == "__init__.py",
        )
        entry.dynamic = _is_dynamic_module(module, code)
        _collect_members(entry, module, dotted, is_stub=False)
        entry.wildcards = tuple(
            member.wildcard
            for member in module.members.values()
            if isinstance(member, griffe.Alias) and member.wildcard
        )
        if entry.dynamic or entry.wildcards:
            entry.complete = False
        return entry


class ChainStatus(enum.StrEnum):
    OK = "ok"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MemberResult:
    """Where a class member was found, and how sure we are about absence."""

    entry: ModuleEntry | None
    relative: str
    node: Node | None
    certain: bool

    @property
    def found(self) -> bool:
        return self.node is not None


@dataclass
class Chain:
    """Outcome of walking a dotted path through the API index."""

    status: ChainStatus
    display: str = ""
    entry: ModuleEntry | None = None
    relative: str = ""
    node: Node | None = None
    defined: str = ""
    missing_name: str = ""
    missing_parent: str = ""
    certain: bool = True
    reason: str = ""
    hops: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is ChainStatus.OK

    @property
    def is_module(self) -> bool:
        return self.status is ChainStatus.OK and not self.relative


class ApiIndex:
    """Single entry point for every API question the checks need to ask."""

    def __init__(
        self,
        env: Environment,
        *,
        cache: ApiCache | None = None,
        allow_import: bool = False,
        ignore_modules: Iterable[str] = (),
        prefer_typeshed_third_party: bool = False,
    ) -> None:
        self.env = env
        self.cache = cache
        self.allow_import = allow_import
        self.ignore_modules = frozenset(ignore_modules)
        self.prefer_typeshed_third_party = prefer_typeshed_third_party
        self._griffe = GriffeProvider(env, cache)
        self._typeshed = TypeshedProvider(env)
        self._local = LocalProvider(env)
        self._modules: dict[str, ModuleEntry | None] = {}
        self._runtime_cache: dict[str, ModuleEntry | None] = {}
        self._decorator_verdicts: dict[str, bool] = {}
        self._source_trees: dict[Path, stdlib_ast.Module] = {}
        self.warnings: list[str] = []
        # The index is shared across worker threads when --jobs > 1; every
        # mutation (module cache, wildcard expansion, disk cache writes) goes
        # through this re-entrant lock.
        self._lock = threading.RLock()

    def origin_of(self, top_level: str) -> _ORIGIN:
        if self.env.is_local_module(top_level):
            return "local"
        if self.env.is_stdlib(top_level):
            return "stdlib"
        if self.env.distribution_for(top_level) is not None:
            return "installed"
        if self._griffe.find_module_file(top_level) is not None:
            return "installed"
        if self._griffe.is_installed_dir(top_level):
            return "installed"
        return "unknown"

    def is_ignored(self, dotted: str) -> bool:
        return dotted.split(".", 1)[0] in self.ignore_modules

    def get_module(self, dotted: str) -> ModuleEntry | None:
        with self._lock:
            return self._get_module(dotted)

    def _get_module(self, dotted: str) -> ModuleEntry | None:
        if dotted in self._modules:
            return self._modules[dotted]
        top = dotted.split(".", 1)[0]
        origin = self.origin_of(top)
        entry: ModuleEntry | None
        if origin == "local":
            entry = self._local.get_module(dotted)
        elif origin == "stdlib":
            entry = self._typeshed.get_module(dotted)
        elif origin == "installed":
            dist = self.env.distribution_for(top)
            extra = f"{dist.name}=={dist.version}" if dist else "unknown"
            entry = self._griffe.get_module(dotted, extra)
            if entry is None and self.allow_import:
                entry = self._runtime_module(dotted)
        else:
            entry = None
        if entry is not None:
            self._expand_wildcards(entry)
        self._modules[dotted] = entry
        return entry

    def _expand_wildcards(self, entry: ModuleEntry, hops: int = 0) -> None:
        """Merge ``from x import *`` targets into the member set when safe."""
        if entry._wildcards_expanded or not entry.wildcards:
            entry._wildcards_expanded = True
            return
        entry._wildcards_expanded = True
        if hops > _MAX_WILDCARD_HOPS:
            entry.complete = False
            return
        for wildcard in entry.wildcards:
            target = self.get_module(wildcard)
            if target is None or not target.complete:
                entry.complete = False
                continue
            for key, node in target.nodes.items():
                if "." in key or key.startswith("_"):
                    continue
                entry.nodes.setdefault(key, node)

    def longest_module_prefix(self, dotted: str) -> tuple[str, ModuleEntry] | None:
        """Split ``dotted`` into the longest importable module prefix and the rest."""
        parts = dotted.split(".")
        for split in range(len(parts), 0, -1):
            candidate = ".".join(parts[:split])
            entry = self.get_module(candidate)
            if entry is not None:
                return candidate, entry
        return None

    def member_of(self, entry: ModuleEntry, relative: str) -> Node | None:
        return entry.nodes.get(relative)

    def class_member(
        self,
        entry: ModuleEntry,
        class_relative: str,
        name: str,
        _seen: frozenset[str] = frozenset(),
    ) -> MemberResult:
        """Look up ``name`` on a class, walking its bases.

        The returned :class:`MemberResult` says where the member was found and
        whether the lookup was *certain* -- a member that is genuinely absent
        can only be claimed when every base could be resolved statically.
        """
        direct = entry.nodes.get(f"{class_relative}.{name}")
        if direct is not None:
            return MemberResult(entry, f"{class_relative}.{name}", direct, True)
        class_node = entry.nodes.get(class_relative)
        if class_node is None or class_node.kind is not ApiKind.CLASS:
            return MemberResult(None, "", None, False)
        if not class_node.members_complete:
            return MemberResult(None, "", None, False)
        certain = True
        for base_expr in class_node.bases:
            status, resolved = self.base_status(entry, base_expr)
            if status == "ignored":
                # ``object``/``Generic``/``Protocol`` style bases add nothing
                # that a missing-member check has to care about.
                continue
            if status == "unknown" or resolved is None:
                certain = False
                continue
            base_entry, base_relative = resolved
            key = f"{base_entry.name}.{base_relative}"
            if key in _seen:
                certain = False
                continue
            found = self.class_member(base_entry, base_relative, name, _seen | {key})
            if found.node is not None:
                return found
            certain = certain and found.certain
        return MemberResult(None, "", None, certain)

    def base_status(
        self, entry: ModuleEntry, base_expr: str
    ) -> tuple[str, tuple[ModuleEntry, str] | None]:
        """Classify a base-class expression.

        ``("ok", (entry, relative))`` when it resolves to a class we can index,
        ``("ignored", None)`` for bases that cannot contribute members we check
        (``object``, ``Generic``, protocols, ABC helpers) and ``("unknown",
        None)`` when it cannot be resolved statically.
        """
        expr = base_expr.strip()
        if not expr:
            return "ignored", None
        if expr.endswith("]"):
            expr = expr.split("[", 1)[0].strip()
        leaf = expr.rsplit(".", 1)[-1]
        if leaf in _IGNORED_BASE_LEAVES:
            return "ignored", None
        resolved = self.resolve_base(entry, expr)
        if resolved is None:
            return "unknown", None
        return "ok", resolved

    def resolve_base(self, entry: ModuleEntry, base_expr: str) -> tuple[ModuleEntry, str] | None:
        """Resolve a base-class expression to ``(entry, relative_path)``."""
        expr = base_expr.strip()
        if not expr:
            return None
        if expr.endswith("]"):
            expr = expr.split("[", 1)[0].strip()
        leaf = expr.rsplit(".", 1)[-1]
        if leaf in _IGNORED_BASE_LEAVES:
            return None
        for candidate in (expr, leaf):
            node = entry.nodes.get(candidate)
            if node is None:
                continue
            if node.kind is ApiKind.CLASS and not node.target:
                return entry, candidate
            if node.target:
                chain = self.walk_chain(node.target)
                if chain.ok and chain.entry is not None and chain.relative:
                    return chain.entry, chain.relative
            return None
        chain = self.walk_chain(expr)
        if chain.ok and chain.entry is not None and chain.relative:
            return chain.entry, chain.relative
        return None

    def walk_chain(self, dotted: str, *, _depth: int = 0) -> Chain:
        """Resolve a canonical dotted path, following re-export aliases.

        Never guesses: when the answer cannot be established statically the
        result carries ``status=UNKNOWN`` and a human readable reason.
        """
        if _depth > 16:
            return Chain(ChainStatus.UNKNOWN, display=dotted, reason="alias cycle")
        prefix = self.longest_module_prefix(dotted)
        if prefix is None:
            return Chain(
                ChainStatus.UNKNOWN,
                display=dotted,
                reason=f"{dotted!r} is not a module ghostcall can read",
            )
        module_name, entry = prefix
        segments = dotted[len(module_name) + 1 :].split(".") if dotted != module_name else []
        relative = ""
        display = module_name
        hops: list[str] = []
        position = 0
        while position < len(segments):
            segment = segments[position]
            search = f"{relative}.{segment}" if relative else segment
            node = entry.nodes.get(search)
            if node is None and relative:
                current = entry.nodes.get(relative)
                if current is not None and current.kind is ApiKind.CLASS:
                    if not current.members_complete:
                        return Chain(
                            ChainStatus.UNKNOWN,
                            display=display,
                            reason=f"{display} produces attributes dynamically",
                        )
                    found = self.class_member(entry, relative, segment)
                    if found.node is not None and found.entry is not None:
                        entry = found.entry
                        relative = found.relative
                        node = found.node
                        display = f"{entry.name}.{relative}"
                        position += 1
                        continue
                    if found.certain and entry.complete and not entry.truncated:
                        return Chain(
                            ChainStatus.MISSING,
                            entry=entry,
                            relative=relative,
                            display=display,
                            missing_name=segment,
                            missing_parent=display,
                            certain=True,
                        )
                    return Chain(
                        ChainStatus.MISSING,
                        entry=entry,
                        relative=relative,
                        display=display,
                        missing_name=segment,
                        missing_parent=display,
                        certain=False,
                    )
                if current is None:
                    if entry.complete and not entry.dynamic and not entry.truncated:
                        return Chain(
                            ChainStatus.MISSING,
                            entry=entry,
                            relative=relative,
                            display=display,
                            missing_name=segment,
                            missing_parent=display,
                            certain=True,
                        )
                    return Chain(
                        ChainStatus.UNKNOWN,
                        display=display,
                        reason=f"{entry.name} is not statically readable",
                    )
            if node is None:
                if entry.dynamic:
                    return Chain(
                        ChainStatus.UNKNOWN,
                        display=display,
                        reason=f"{entry.name} builds its namespace at runtime",
                    )
                if not entry.complete or entry.truncated:
                    return Chain(
                        ChainStatus.UNKNOWN,
                        display=display,
                        reason=f"{entry.name} could not be indexed completely",
                    )
                return Chain(
                    ChainStatus.MISSING,
                    entry=entry,
                    relative=relative,
                    display=display or entry.name,
                    missing_name=segment,
                    missing_parent=display or entry.name,
                    certain=True,
                )
            if node.target:
                hops.append(node.target)
                rest = segments[position + 1 :]
                target = ".".join([node.target, *rest])
                sub = self.walk_chain(target, _depth=_depth + 1)
                if sub.status is ChainStatus.UNKNOWN:
                    sub.hops = [*hops, *sub.hops]
                return sub
            if position + 1 < len(segments) and node.kind is ApiKind.ATTRIBUTE:
                # ``sys.stdin.read``: ``stdin`` is a plain attribute annotated
                # ``TextIO | MaybeNone``.  The remaining segments belong to a
                # *value* whose type ghostcall cannot follow, so its members must
                # never be looked up in this module.
                return Chain(
                    ChainStatus.UNKNOWN,
                    display=".".join([display, *segments[position + 1 :]]),
                    reason=(
                        f"{display} is a value with a statically unknown type"
                        if not node.annotation
                        else f"{display} is annotated {node.annotation}, which ghostcall "
                        "does not follow"
                    ),
                )
            relative = search
            display = f"{entry.name}.{relative}"
            position += 1
        return Chain(
            ChainStatus.OK,
            entry=entry,
            relative=relative,
            node=entry.nodes.get(relative) if relative else None,
            display=display,
            defined=display,
            hops=list(hops),
        )

    def init_of(self, entry: ModuleEntry, class_relative: str) -> ClassInit:
        """Constructor signature of a class, including inherited ``__init__``."""
        node = entry.nodes.get(class_relative)
        if node is None or node.kind is not ApiKind.CLASS:
            return ClassInit(None, "not a class")
        inherited = self._inherited_override(entry, node, frozenset())
        if inherited is not None:
            return ClassInit(None, inherited)
        if node.signature is not None:
            return ClassInit(node.signature, None, node.init_synthesized)
        if node.init_skip_reason:
            return ClassInit(None, node.init_skip_reason)
        found = self.class_member(entry, class_relative, "__init__")
        if found.node is not None and found.node.signature is not None:
            return ClassInit(found.node.signature.strip_first(), None, False)
        return ClassInit(None, "class has no statically-known constructor")

    def decorator_blocker(self, entry: ModuleEntry, node: Node) -> str | None:
        """The first decorator of ``node`` that may change its call signature.

        ``None`` means the recovered signature can be trusted: every decorator is
        a known pass-through, or is annotated to return the callable it receives
        (``def deco(func: F) -> F``, ``def factory(...) -> Callable[[F], F]``, a
        class whose ``__call__`` is ``(self, func: T) -> T``).  Stubs describe
        the signature as exposed, so their decorators are always trusted.
        """
        if not node.decorators or _is_stub_entry(entry):
            return None
        for decorator in node.decorators:
            if not self._preserves_signature(entry, decorator):
                return decorator.split("(", 1)[0].strip()
        return None

    def changes_return(self, entry: ModuleEntry, node: Node) -> bool:
        """Whether a decorator makes the return annotation describe the wrong object.

        Weaker than :meth:`decorator_blocker` on purpose: a wrapper typed
        ``Callable[[F], F]`` that forwards ``*args, **kwargs`` may accept other
        arguments, but it still returns what the wrapped function returns.
        """
        if not node.decorators or _is_stub_entry(entry):
            return False
        for decorator in node.decorators:
            if _decorator_leaf(decorator) in _RETURN_CHANGING_DECORATORS:
                return True
            if not self._decorator_verdict(entry, decorator, proof="annotation"):
                return True
        return False

    def _preserves_signature(self, entry: ModuleEntry, decorator: str) -> bool:
        return self._decorator_verdict(entry, decorator, proof="implementation")

    def _decorator_verdict(
        self, entry: ModuleEntry, decorator: str, *, proof: Literal["annotation", "implementation"]
    ) -> bool:
        name = decorator.split("(", 1)[0].strip()
        if not name:
            return False
        if "." in name and name.rsplit(".", 1)[-1] in {"setter", "getter", "deleter"}:
            return True  # ``@value.setter``: a property accessor
        target = _decorator_target(entry, name)
        if name in _PASS_THROUGH_DECORATORS or target in _PASS_THROUGH_DECORATORS:
            return True
        key = f"{proof}|{entry.name}|{target}"
        # The whole computation holds the re-entrant lock: the provisional
        # ``False`` that stops a self-decorating decorator from recursing must
        # never be visible to another thread as if it were the answer.
        with self._lock:
            cached = self._decorator_verdicts.get(key)
            if cached is not None:
                return cached
            self._decorator_verdicts[key] = False
            verdict = self._decorator_is_identity(target, proof)
            self._decorator_verdicts[key] = verdict
            return verdict

    def _decorator_is_identity(
        self, target: str, proof: Literal["annotation", "implementation"]
    ) -> bool:
        """Whether the decorator at ``target`` hands back the callable it receives.

        ``annotation`` asks only that its types say so.  ``implementation`` also
        requires the source to return that very argument: annotations alone are
        not proof, because ``pandas.util._decorators.deprecate_kwarg`` is typed
        ``Callable[[F], F]`` yet returns a ``(*args, **kwargs)`` wrapper that
        still accepts the deprecated keyword.
        """
        chain = self.walk_chain(target)
        node = chain.node
        decorator_entry = chain.entry
        if not chain.ok or node is None or decorator_entry is None:
            return False
        # Where the decorator is *defined*, after following re-exports.
        target = f"{decorator_entry.name}.{chain.relative}"
        if node.kind is ApiKind.FUNCTION:
            if self.decorator_blocker(decorator_entry, node) is not None:
                return False
            signatures = [node.signature] if node.signature is not None else list(node.overloads)
            if not signatures or not all(_is_identity_decorator(s) for s in signatures):
                return False
        elif node.kind is ApiKind.CLASS:
            found = self.class_member(decorator_entry, chain.relative, "__call__")
            call = found.node
            if call is None or call.kind is not ApiKind.FUNCTION or found.entry is None:
                return False
            signatures = [call.signature] if call.signature is not None else list(call.overloads)
            if not signatures or not all(
                _is_identity_decorator(s.strip_first()) for s in signatures
            ):
                return False
            decorator_entry, target = found.entry, f"{found.entry.name}.{found.relative}"
        else:
            return False
        if proof == "annotation":
            return True
        definition = self._definition(decorator_entry, target)
        return definition is not None and _returns_its_argument(
            definition, is_method=node.kind is ApiKind.CLASS
        )

    def _definition(
        self, entry: ModuleEntry, dotted: str
    ) -> stdlib_ast.FunctionDef | stdlib_ast.AsyncFunctionDef | None:
        """The ``def`` statement behind ``dotted``, read from ``entry``'s source."""
        filepath = entry.filepath
        if filepath is None or filepath.suffix != ".py" or not dotted.startswith(entry.name):
            return None
        tree = self._source_trees.get(filepath)
        if tree is None:
            try:
                tree = stdlib_ast.parse(filepath.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError, ValueError):
                return None
            with self._lock:
                self._source_trees[filepath] = tree
        body: Sequence[stdlib_ast.stmt] = tree.body
        found: stdlib_ast.stmt | None = None
        for part in dotted[len(entry.name) + 1 :].split("."):
            found = next(
                (
                    statement
                    for statement in body
                    if isinstance(
                        statement,
                        stdlib_ast.FunctionDef | stdlib_ast.AsyncFunctionDef | stdlib_ast.ClassDef,
                    )
                    and statement.name == part
                ),
                None,
            )
            if found is None:
                return None
            body = found.body
        if isinstance(found, stdlib_ast.FunctionDef | stdlib_ast.AsyncFunctionDef):
            return found
        return None

    def _inherited_override(
        self, entry: ModuleEntry, node: Node, seen: frozenset[str]
    ) -> str | None:
        """A metaclass or ``__new__`` inherited from a base class ghostcall can read."""
        for base_expr in node.bases:
            status, resolved = self.base_status(entry, base_expr)
            if status != "ok" or resolved is None:
                continue
            base_entry, base_relative = resolved
            base = base_entry.nodes.get(base_relative)
            if base is None or base.path in seen:
                continue
            if base.constructor_override:
                return f"base class {base.path}: {base.constructor_override}"
            deeper = self._inherited_override(base_entry, base, seen | {base.path})
            if deeper is not None:
                return deeper
        return None

    def _runtime_module(self, dotted: str) -> ModuleEntry | None:
        if dotted not in self._runtime_cache:
            from .runtime import introspect_module

            self._runtime_cache[dotted] = introspect_module(self.env, dotted)
        return self._runtime_cache[dotted]

    def stub_is_complete(self, entry: ModuleEntry) -> bool:
        """Whether absence of a name in ``entry`` is meaningful."""
        return not (entry.dynamic or not entry.complete or entry.truncated)
