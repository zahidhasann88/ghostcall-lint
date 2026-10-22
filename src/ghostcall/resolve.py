from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from .index import ApiIndex, Chain, ChainStatus, ClassInit, ModuleEntry, Node
from .model import ApiKind, Confidence, Signature
from .scope import Binding, FileScope

__all__ = ["InstanceTracker", "Resolution", "ResolutionStatus", "Resolver", "TrackedInstance"]


class ResolutionStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass
class Resolution:
    """The outcome of resolving an expression."""

    status: ResolutionStatus
    display: str = ""
    reason: str = ""
    kind: ApiKind | None = None
    entry: ModuleEntry | None = None
    relative: str = ""
    node: Node | None = None
    signature: Signature | None = None
    overloads: tuple[Signature, ...] = ()
    signature_reason: str = ""
    defined: str = ""
    module: str = ""
    missing_name: str = ""
    missing_parent: str = ""
    confidence: Confidence = Confidence.HIGH
    is_instance: bool = False
    parent_kind: ApiKind | None = None
    optional: bool = False
    hops: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is ResolutionStatus.OK

    @property
    def missing(self) -> bool:
        return self.status is ResolutionStatus.MISSING

    @property
    def unknown(self) -> bool:
        return self.status is ResolutionStatus.UNKNOWN

    @property
    def is_module(self) -> bool:
        return self.status is ResolutionStatus.OK and self.kind is ApiKind.MODULE

    @property
    def signatures(self) -> tuple[Signature, ...]:
        """Every acceptable signature (overloads plus the implementation)."""
        collected: list[Signature] = list(self.overloads)
        if self.signature is not None:
            collected.append(self.signature)
        return tuple(collected)

    @property
    def effective_signature(self) -> Signature | None:
        """The signature to display: implementation if known, else first overload."""
        if self.signature is not None:
            return self.signature
        return self.overloads[0] if self.overloads else None


@dataclass
class TrackedInstance:
    """A local variable whose type could be determined conservatively."""

    name: str
    entry: ModuleEntry
    class_relative: str
    display: str
    confidence: Confidence
    line: int


class Resolver:
    """Resolves expressions in one file against the API index."""

    def __init__(
        self,
        index: ApiIndex,
        file_scope: FileScope,
        *,
        instance_tracking: bool = True,
        max_depth: int = 6,
    ) -> None:
        self.index = index
        self.scope = file_scope
        self.instance_tracking = instance_tracking
        self.max_depth = max_depth
        self.current_line = 10**9
        self.cache_enabled = True
        self._cache: dict[tuple[int, int], Resolution] = {}
        self.instances: dict[str, TrackedInstance] = {}
        if instance_tracking:
            # The tracker probes resolutions before ``instances`` is known, so
            # its probes must not pollute the cache used by the real walk.
            self.instances = InstanceTracker(self).scan(file_scope.tree)
            self._cache.clear()

    def resolve(self, node: ast.expr, *, depth: int = 0) -> Resolution:
        if not self.cache_enabled:
            return self._resolve(node, depth)
        key = (id(node), depth)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._resolve(node, depth)
        self._cache[key] = result
        return result

    def resolve_callee(self, call: ast.Call) -> Resolution:
        return self.resolve(call.func)

    def _resolve(self, node: ast.expr, depth: int) -> Resolution:
        if depth > self.max_depth:
            return Resolution(ResolutionStatus.UNKNOWN, reason="expression is nested too deeply")
        if isinstance(node, ast.Call):
            return self._resolve_call_result(node, depth)
        chain = _flatten(node)
        if chain is None:
            return Resolution(
                ResolutionStatus.UNKNOWN,
                display=_unparse(node),
                reason="expression is not a plain import chain",
            )
        base, attrs = chain
        if isinstance(base, ast.Call):
            # ``httpx.Client().stream(...)``: resolve the attribute on whatever
            # the call returns, exactly as if it had been assigned first.
            return self._resolve_on_call_result(base, attrs, depth)
        assert isinstance(base, ast.Name)
        return self._resolve_name(base.id, attrs)

    def _resolve_on_call_result(
        self, call: ast.Call, attrs: Sequence[str], depth: int
    ) -> Resolution:
        """Attribute access on a call result: ``f().attr``."""
        base = self._resolve_call_result(call, depth + 1)
        if not base.ok or base.entry is None or not base.relative or not attrs:
            return Resolution(
                ResolutionStatus.UNKNOWN,
                display=_unparse(call),
                reason="call result has no known type",
            )
        tracked = TrackedInstance(
            name=_unparse(call),
            entry=base.entry,
            class_relative=base.relative,
            display=base.display,
            confidence=Confidence.MEDIUM,
            line=getattr(call, "lineno", 0),
        )
        return self._resolve_tracked_attribute(tracked, list(attrs))

    def _resolve_name(self, name: str, attrs: Sequence[str]) -> Resolution:
        binding, reason = self.scope.lookup(name, self.current_line)
        if binding is None:
            tracked = self.instances.get(name)
            if tracked is not None and attrs:
                return self._resolve_tracked_attribute(tracked, list(attrs))
            return Resolution(ResolutionStatus.UNKNOWN, display=name, reason=reason)
        if binding.relative:
            return self._resolve_relative(binding, attrs)
        canonical = ".".join([binding.target, *attrs]) if attrs else binding.target
        chain = self.index.walk_chain(canonical)
        resolution = self._from_chain(chain, _display(binding, attrs), binding)
        resolution.optional = binding.optional
        return resolution

    def _resolve_relative(self, binding: Binding, attrs: Sequence[str]) -> Resolution:
        """Relative imports point at project-local code: never a finding."""
        display = _display(binding, attrs)
        entry = self.index.get_module(".".join([binding.target, *attrs])) if attrs else None
        if entry is None:
            entry = self.index.get_module(binding.target)
        if entry is None:
            return Resolution(
                ResolutionStatus.UNKNOWN,
                display=display,
                reason="relative import of project-local code",
            )
        resolution = Resolution(
            ResolutionStatus.OK,
            display=display,
            kind=ApiKind.MODULE,
            entry=entry,
            relative=entry.name,
            reason="project-local module",
        )
        if not attrs:
            return resolution
        member = entry.nodes.get(".".join(attrs))
        if member is not None:
            resolution.kind = member.kind
            resolution.node = member
            resolution.relative = ".".join(attrs)
            blocker = self._signature_blocker(entry, member, ".".join(attrs))
            if blocker:
                resolution.signature_reason = blocker
            else:
                resolution.signature = member.signature
                resolution.overloads = member.overloads
        else:
            return Resolution(
                ResolutionStatus.UNKNOWN,
                display=display,
                reason="project-local code cannot be resolved statically",
            )
        return resolution

    def _from_chain(self, chain: Chain, display: str, binding: Binding | None) -> Resolution:
        module = (
            binding.target.split(".", 1)[0]
            if binding is not None
            else chain.display.split(".", 1)[0]
        )
        if chain.status is ChainStatus.UNKNOWN:
            resolution = Resolution(
                ResolutionStatus.UNKNOWN,
                display=display,
                reason=chain.reason or "could not resolve statically",
            )
            resolution.hops = list(chain.hops)
            return resolution
        if chain.status is ChainStatus.MISSING:
            resolution = Resolution(
                ResolutionStatus.MISSING,
                display=display,
                reason=(
                    "not present in the installed version"
                    if chain.certain
                    else "not present, but a base class could not be resolved statically"
                ),
                missing_name=chain.missing_name,
                missing_parent=chain.missing_parent,
                entry=chain.entry,
                relative=chain.relative,
                confidence=Confidence.HIGH if chain.certain else Confidence.MEDIUM,
                module=module,
            )
            resolution.hops = list(chain.hops)
            return resolution
        node = chain.node
        entry = chain.entry
        resolution = Resolution(
            ResolutionStatus.OK,
            display=display,
            kind=node.kind if node is not None else ApiKind.MODULE,
            entry=entry,
            relative=chain.relative,
            node=node,
            defined=chain.defined,
            module=module,
        )
        resolution.hops = list(chain.hops)
        self._apply_signature(resolution)
        return resolution

    def _apply_signature(self, resolution: Resolution) -> None:
        """Attach the effective signature, unless a decorator makes it untrustworthy."""
        self._attach_signature(resolution)
        node = resolution.node
        entry = resolution.entry
        if node is None or entry is None or not resolution.ok:
            return
        if resolution.signature is None and not resolution.overloads:
            return  # already skipped, with a more specific reason
        blocker = self._signature_blocker(entry, node, resolution.relative)
        if blocker:
            resolution.signature = None
            resolution.overloads = ()
            resolution.signature_reason = blocker

    def _attach_signature(self, resolution: Resolution) -> None:
        """Attach the effective signature, stripping ``self`` for bound methods."""
        node = resolution.node
        entry = resolution.entry
        if node is None or entry is None:
            return
        if node.kind is ApiKind.FUNCTION:
            if "." in resolution.relative:
                parent_relative = resolution.relative.rsplit(".", 1)[0]
                parent_node = entry.nodes.get(parent_relative)
                parent_kind = parent_node.kind if parent_node is not None else None
                resolution.parent_kind = parent_kind
                if node.is_static:
                    resolution.signature = node.signature
                    resolution.overloads = node.overloads
                elif node.is_classmethod:
                    resolution.signature = _strip(node.signature)
                    resolution.overloads = _strip_all(node.overloads)
                elif parent_kind is ApiKind.CLASS:
                    # ``Class.method`` reached without an instance: the call
                    # would have to pass ``self`` explicitly.  Skip rather than
                    # guess.
                    resolution.status = ResolutionStatus.UNKNOWN
                    resolution.reason = (
                        "unbound method reached on the class object; "
                        "its signature cannot be trusted"
                    )
                    resolution.signature = None
                    resolution.overloads = ()
                    return
                else:
                    resolution.signature = _strip(node.signature)
                    resolution.overloads = _strip_all(node.overloads)
            else:
                resolution.signature = node.signature
                resolution.overloads = node.overloads
            if resolution.signature is None and not resolution.overloads:
                resolution.signature_reason = "the installed API has no readable signature"
            return
        if node.kind is ApiKind.CLASS:
            init: ClassInit = self.index.init_of(entry, resolution.relative)
            if init.signature is not None:
                resolution.signature = init.signature
            else:
                resolution.signature_reason = init.reason or "constructor could not be determined"
            return
        if node.kind is ApiKind.ATTRIBUTE:
            resolution.signature_reason = "attribute is not callable"
            return
        resolution.signature_reason = "module objects cannot be called"

    def _signature_blocker(self, entry: ModuleEntry, node: Node, relative: str) -> str:
        """Why a decorator makes ``node``'s recovered signature untrustworthy, or ``""``.

        For a class, both class decorators and decorators on ``__init__`` count:
        either can replace what calling the class accepts.
        """
        if node.kind not in (ApiKind.FUNCTION, ApiKind.CLASS):
            return ""
        blocker = self.index.decorator_blocker(entry, node)
        if blocker is None and node.kind is ApiKind.CLASS:
            init = entry.nodes.get(f"{relative}.__init__")
            if init is not None:
                blocker = self.index.decorator_blocker(entry, init)
        if blocker is None:
            return ""
        return f"decorated with @{blocker}, which may change its signature"

    def _resolve_call_result(self, call: ast.Call, depth: int) -> Resolution:
        callee = self.resolve(call.func, depth=depth + 1)
        if not callee.ok:
            return Resolution(
                ResolutionStatus.UNKNOWN,
                display=callee.display,
                reason=f"call result of an unresolved call ({callee.reason})",
            )
        target = self.class_of(callee)
        if target is None:
            return Resolution(
                ResolutionStatus.UNKNOWN,
                display=_unparse(call.func),
                reason="return type could not be determined statically",
            )
        entry, relative = target
        return Resolution(
            ResolutionStatus.OK,
            # Name the class the call returns rather than the callee: findings on a
            # chained call must not read as "the function has no attribute ...".
            display=_class_display(callee, entry, relative),
            kind=ApiKind.INSTANCE,
            entry=entry,
            relative=relative,
            node=entry.nodes.get(relative),
            defined=f"{entry.name}.{relative}",
            is_instance=True,
            confidence=Confidence.MEDIUM,
            reason="instance tracked through a return annotation",
            module=entry.name.split(".", 1)[0],
        )

    def class_of(self, resolution: Resolution) -> tuple[ModuleEntry, str] | None:
        """The single class a resolution stands for, or ``None``."""
        node = resolution.node
        entry = resolution.entry
        if node is None or entry is None:
            return None
        if node.kind in (ApiKind.CLASS, ApiKind.FUNCTION) and self.index.changes_return(
            entry, node
        ):
            # A decorator may return something else entirely (a wrapper object,
            # a context manager): the class or annotation no longer describes it.
            return None
        if node.kind in (ApiKind.CLASS, ApiKind.INSTANCE):
            return entry, resolution.relative
        if node.kind is not ApiKind.FUNCTION:
            return None
        if node.signature is None and node.overloads:
            return None  # ambiguous return type
        annotation = node.signature.return_annotation if node.signature else None
        if annotation is None:
            return None
        return self._resolve_annotation(annotation, entry)

    def _resolve_annotation(
        self, annotation: str, entry: ModuleEntry
    ) -> tuple[ModuleEntry, str] | None:
        """Resolve an annotation to exactly one class, or give up."""
        text = annotation.strip().strip('"').strip("'")
        if not text or any(token in text for token in ("|", "Union", "Optional", "Callable")):
            return None
        try:
            parsed = ast.parse(text, mode="eval").body
        except SyntaxError:
            return None
        # ``NDArray[Any]`` describes the same class as ``NDArray``; a subscription
        # never changes *which* concrete class a call returns.
        while isinstance(parsed, ast.Subscript):
            parsed = parsed.value
        if not isinstance(parsed, ast.Name | ast.Attribute):
            return None
        flattened = _flatten(parsed)
        if flattened is None:
            return None
        root, attrs = flattened
        if not isinstance(root, ast.Name):
            return None
        local = entry.nodes.get(root.id)
        start = local.target if local is not None and local.target else f"{entry.name}.{root.id}"
        chain = self.index.walk_chain(".".join([start, *attrs]))
        if not chain.ok or chain.node is None or chain.entry is None:
            return None
        if chain.node.kind is not ApiKind.CLASS:
            return None
        if not _same_library(entry.name, chain.entry.name):
            # A return annotation naming another library describes a supertype
            # (``importlib.import_module(...) -> types.ModuleType``), never the
            # concrete object the call produces.
            return None
        return chain.entry, chain.relative

    def _resolve_tracked_attribute(self, tracked: TrackedInstance, attrs: list[str]) -> Resolution:
        entry = tracked.entry
        relative = tracked.class_relative
        display = f"{tracked.display}.{'.'.join(attrs)}"
        for position, attr in enumerate(attrs):
            found = self.index.class_member(entry, relative, attr)
            node, certain = found.node, found.certain
            if node is None:
                class_node = entry.nodes.get(relative)
                if class_node is not None and not class_node.members_complete:
                    return Resolution(
                        ResolutionStatus.UNKNOWN,
                        display=display,
                        reason="tracked class exposes attributes dynamically",
                    )
                if entry.dynamic or entry.truncated:
                    return Resolution(
                        ResolutionStatus.UNKNOWN,
                        display=display,
                        reason="tracked class could not be indexed completely",
                    )
                return Resolution(
                    ResolutionStatus.MISSING,
                    display=display,
                    kind=ApiKind.INSTANCE,
                    entry=entry,
                    relative=relative,
                    missing_name=attr,
                    missing_parent=f"{entry.name}.{relative}",
                    confidence=Confidence.MEDIUM,
                    is_instance=True,
                    reason=(
                        "instance attribute is not present on the tracked class"
                        if certain
                        else "instance attribute is not present, but a base class is dynamic"
                    ),
                    module=entry.name.split(".", 1)[0],
                )
            if found.entry is not None:
                entry = found.entry
            relative = found.relative or f"{relative}.{attr}"
            if position < len(attrs) - 1 and node.kind is not ApiKind.CLASS:
                if node.kind is ApiKind.INSTANCE:
                    continue
                return Resolution(
                    ResolutionStatus.UNKNOWN,
                    display=display,
                    reason="attribute chain continues through an object ghostcall cannot type",
                )
        node = entry.nodes.get(relative)
        resolution = Resolution(
            ResolutionStatus.OK,
            display=display,
            kind=ApiKind.INSTANCE,
            entry=entry,
            relative=relative,
            node=node,
            defined=f"{entry.name}.{relative}",
            is_instance=True,
            confidence=Confidence.MEDIUM,
            reason="instance tracked from an assignment",
            module=entry.name.split(".", 1)[0],
        )
        if node is not None:
            blocker = self._signature_blocker(entry, node, relative)
            if blocker:
                resolution.signature_reason = blocker
            elif node.kind is ApiKind.FUNCTION and not node.is_static:
                resolution.signature = _strip(node.signature)
                resolution.overloads = _strip_all(node.overloads)
            else:
                resolution.signature = node.signature
                resolution.overloads = node.overloads
        return resolution


class InstanceTracker:
    """Finds ``obj = Alias.Class(...)`` (and function-return) assignments.

    Only single-assignment names constructed directly from a resolution whose
    type is unambiguous are tracked, and the resulting findings stay at medium
    confidence.
    """

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

    def scan(self, tree: ast.Module) -> dict[str, TrackedInstance]:
        counts: dict[str, int] = {}
        disqualified: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Store):
                    counts[node.id] = counts.get(node.id, 0) + 1
                elif isinstance(node.ctx, ast.Del):
                    disqualified.add(node.id)
            elif isinstance(node, ast.arg):
                disqualified.add(node.arg)
            elif isinstance(node, ast.Global | ast.Nonlocal):
                disqualified.update(node.names)
            elif isinstance(node, ast.For | ast.AsyncFor) and isinstance(node.target, ast.Name):
                disqualified.add(node.target.id)
            elif isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
                disqualified.add(node.optional_vars.id)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                disqualified.add(node.name)
            elif isinstance(node, ast.alias):
                disqualified.add(node.asname or node.name.split(".", 1)[0])
            elif (isinstance(node, ast.ExceptHandler) and node.name) or (
                isinstance(node, ast.MatchAs | ast.MatchStar) and node.name
            ):
                disqualified.add(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                disqualified.add(node.rest)
        tracked: dict[str, TrackedInstance] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Call):
                continue
            name = target.id
            if name in disqualified or counts.get(name, 0) != 1:
                continue
            if _is_in_nested_scope(node, tree):
                continue
            previous = self.resolver.current_line
            previous_cache = self.resolver.cache_enabled
            self.resolver.current_line = node.lineno
            self.resolver.cache_enabled = False
            try:
                callee = self.resolver.resolve(node.value.func)
                target_class = self.resolver.class_of(callee) if callee.ok else None
            finally:
                self.resolver.current_line = previous
                self.resolver.cache_enabled = previous_cache
            if target_class is None:
                continue
            entry, relative = target_class
            tracked[name] = TrackedInstance(
                name=name,
                entry=entry,
                class_relative=relative,
                display=_class_display(callee, entry, relative),
                confidence=Confidence.MEDIUM,
                line=node.lineno,
            )
        return tracked


def _is_in_nested_scope(node: ast.AST, tree: ast.Module) -> bool:
    """Crude check that an assignment is not inside a comprehension/lambda."""
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    for parent in ast.walk(tree):
        if not isinstance(parent, comprehensions):
            continue
        if any(child is node for child in ast.walk(parent)):
            return True
    return False


def _flatten(node: ast.expr) -> tuple[ast.Name | ast.Call, list[str]] | None:
    """Turn ``a.b.c`` into ``(Name a, ["b", "c"])``.

    The base may also be a call (``Client().close``), which the resolver handles
    by typing the call result first.
    """
    attrs: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        attrs.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name | ast.Call):
        return None
    attrs.reverse()
    return current, attrs


def _display(binding: Binding, attrs: Iterable[str]) -> str:
    return ".".join([binding.target, *attrs])


#: Origins whose names describe *shapes* rather than the concrete class a call
#: returns, so tracking call results through them would only invent children.
_SUPERTYPE_ORIGINS = frozenset(
    {"builtins", "typing", "typing_extensions", "types", "abc", "collections", "enum", "io"}
)


def _same_library(callee_module: str, class_module: str) -> bool:
    """Whether a return annotation can describe the callee's concrete result."""
    class_root = class_module.split(".", 1)[0]
    if class_root in _SUPERTYPE_ORIGINS:
        return False
    return class_root == callee_module.split(".", 1)[0]


def _class_display(callee: Resolution, entry: ModuleEntry, relative: str) -> str:
    """How to name a tracked class in findings.

    When the call *is* the class (``pd.DataFrame(...)``) the user's own spelling
    is the clearest name; when the class came from a return annotation
    (``sqlalchemy.create_engine(...) -> Engine``) the canonical path is used,
    because naming the function there reads as "``create_engine`` has no
    attribute ...", which is nonsense.
    """
    node = callee.node
    canonical = f"{entry.name}.{relative}"
    if node is not None and node.kind is ApiKind.CLASS and node.path == canonical:
        return callee.display
    return canonical


def _strip(signature: Signature | None) -> Signature | None:
    return signature.strip_first() if signature is not None else None


def _strip_all(overloads: Sequence[Signature | None]) -> tuple[Signature, ...]:
    """Strip ``self`` from every overload that survived parsing."""
    stripped = (_strip(overload) for overload in overloads)
    return tuple(item for item in stripped if item is not None)


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<expression>"
