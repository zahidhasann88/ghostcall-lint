from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .model import ApiKind, Parameter, ParameterKind, Signature

if TYPE_CHECKING:
    from .index import ModuleEntry

__all__ = [
    "DYNAMIC_SOURCE_PATTERNS",
    "AstModuleIndexer",
    "deprecation_note",
    "evaluate_condition",
]

_OVERLOAD_NAMES = frozenset({"overload", "typing.overload", "typing_extensions.overload"})
_DEPRECATED_NAMES = frozenset({"deprecated", "Deprecated"})
_PROPERTY_NAMES = frozenset({"property", "cached_property", "functools.cached_property"})
_STATIC_NAMES = frozenset({"staticmethod"})
_CLASSMETHOD_NAMES = frozenset({"classmethod"})
_DATACLASS_NAMES = frozenset({"dataclass", "dataclasses.dataclass"})
_NAMEDTUPLE_NAMES = frozenset({"NamedTuple", "typing.NamedTuple", "typing_extensions.NamedTuple"})

DYNAMIC_SOURCE_PATTERNS = (
    re.compile(r"^\s*setattr\s*\(\s*globals\s*\(", re.MULTILINE),
    re.compile(r"^\s*globals\s*\(\s*\)\s*\[", re.MULTILINE),
    re.compile(r"^\s*globals\s*\(\s*\)\s*\.update\s*\(", re.MULTILINE),
    re.compile(r"^\s*exec\s*\(", re.MULTILINE),
)

_MAX_DEPTH = 4
_MAX_NODES = 5000


def _leaf(name: str) -> str:
    return name.split("(", 1)[0].rsplit(".", 1)[-1]


class AstModuleIndexer:
    """Build a :class:`~ghostcall.index.ModuleEntry` from an AST."""

    def __init__(
        self,
        module_name: str,
        *,
        version: tuple[int, int] | None = None,
        platform: str | None = None,
        is_package: bool = False,
        origin: str = "installed",
        filepath: Path | None = None,
        source: str | None = None,
    ) -> None:
        self.module_name = module_name
        self.version = version
        self.platform = platform
        self.is_package = is_package
        self.origin = origin
        self.filepath = filepath
        self.source = source
        self.dynamic = False
        self.wildcards: list[str] = []
        self.truncated = False

    def build(self, tree: ast.Module) -> ModuleEntry:
        from .index import ModuleEntry

        entry = ModuleEntry(
            name=self.module_name,
            origin=self.origin,  # type: ignore[arg-type]
            filepath=self.filepath,
            is_package=self.is_package,
        )
        count = 0
        for statement in tree.body:
            count += self._statement(entry, statement, "", 0)
            if count > _MAX_NODES:
                entry.truncated = True
                break
        source = self.source
        if source is not None and any(p.search(source) for p in DYNAMIC_SOURCE_PATTERNS):
            self.dynamic = True
        entry.wildcards = tuple(self.wildcards)
        entry.dynamic = self.dynamic
        if self.dynamic:
            entry.complete = False
        return entry

    def _statement(self, entry: ModuleEntry, statement: ast.stmt, prefix: str, depth: int) -> int:

        added = 0
        if depth > _MAX_DEPTH:
            return 0
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            self._add_function(entry, statement, prefix)
            return 1
        if isinstance(statement, ast.ClassDef):
            added += self._add_class(entry, statement, prefix, depth)
            return added
        if isinstance(statement, ast.AnnAssign | ast.Assign):
            return self._add_assignment(entry, statement, prefix)
        if isinstance(statement, ast.Import | ast.ImportFrom):
            return self._add_import(entry, statement, prefix)
        if isinstance(statement, ast.Delete):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    key = f"{prefix}.{target.id}" if prefix else target.id
                    entry.nodes.pop(key, None)
            return 0
        if isinstance(statement, ast.If):
            verdict = evaluate_condition(statement.test, self.version, self.platform)
            if verdict is False:
                return sum(self._statement(entry, item, prefix, depth) for item in statement.orelse)
            if verdict is True:
                return sum(self._statement(entry, item, prefix, depth) for item in statement.body)
            total = sum(self._statement(entry, item, prefix, depth) for item in statement.body)
            return total + sum(
                self._statement(entry, item, prefix, depth) for item in statement.orelse
            )
        if isinstance(statement, ast.Try | ast.TryStar):
            total = sum(self._statement(entry, item, prefix, depth) for item in statement.body)
            for handler in statement.handlers:
                total += sum(self._statement(entry, item, prefix, depth) for item in handler.body)
            total += sum(self._statement(entry, item, prefix, depth) for item in statement.orelse)
            total += sum(
                self._statement(entry, item, prefix, depth) for item in statement.finalbody
            )
            return total
        if isinstance(statement, ast.With | ast.AsyncWith):
            return sum(self._statement(entry, item, prefix, depth) for item in statement.body)
        if isinstance(statement, ast.For | ast.AsyncFor | ast.While):
            total = sum(self._statement(entry, item, prefix, depth) for item in statement.body)
            return total + sum(
                self._statement(entry, item, prefix, depth) for item in statement.orelse
            )
        if isinstance(statement, ast.Match):
            return sum(
                self._statement(entry, item, prefix, depth)
                for case in statement.cases
                for item in case.body
            )
        return 0

    def _add_function(
        self, entry: ModuleEntry, statement: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str
    ) -> None:
        from .index import Node

        key = f"{prefix}.{statement.name}" if prefix else statement.name
        decorators = tuple(_decorator_name(item) for item in statement.decorator_list)
        is_overload = any(
            _leaf(item) == "overload" or item in _OVERLOAD_NAMES for item in decorators
        )
        obsolete = "deprecated" in {_leaf(name) for name in decorators}
        signature = _signature_from_args(
            statement.args,
            origin=f"{self.module_name}.{key}",
            is_stub=True,
            is_class_init=statement.name == "__init__" and bool(prefix),
            returns=_safe_unparse(statement.returns) or None,
        )
        existing = entry.nodes.get(key)
        if is_overload and existing is not None and existing.kind is ApiKind.FUNCTION:
            existing.overloads = (*existing.overloads, signature)
            return
        if is_overload:
            node = Node(
                kind=ApiKind.FUNCTION,
                name=statement.name,
                path=f"{self.module_name}.{key}",
                signature=None,
                overloads=(signature,),
                decorators=decorators,
                is_static=any(_leaf(item) in _STATIC_NAMES for item in decorators),
                is_classmethod=any(_leaf(item) in _CLASSMETHOD_NAMES for item in decorators),
                is_property=any(_leaf(item) in _PROPERTY_NAMES for item in decorators),
                deprecated=_deprecated_note(statement, decorators, obsolete),
            )
            entry.nodes[key] = node
            return
        node = Node(
            kind=ApiKind.FUNCTION,
            name=statement.name,
            path=f"{self.module_name}.{key}",
            signature=signature,
            decorators=decorators,
            is_static=any(_leaf(item) in _STATIC_NAMES for item in decorators),
            is_classmethod=any(_leaf(item) in _CLASSMETHOD_NAMES for item in decorators),
            is_property=any(_leaf(item) in _PROPERTY_NAMES for item in decorators),
            deprecated=_deprecated_note(statement, decorators, obsolete),
        )
        if existing is not None and existing.kind is ApiKind.FUNCTION:
            node.overloads = existing.overloads
        entry.nodes[key] = node
        if prefix == "" and statement.name == "__getattr__":
            self.dynamic = True

    def _add_class(
        self, entry: ModuleEntry, statement: ast.ClassDef, prefix: str, depth: int
    ) -> int:
        from .index import Node, metaclass_override

        key = f"{prefix}.{statement.name}" if prefix else statement.name
        decorators = tuple(_decorator_name(item) for item in statement.decorator_list)
        bases = tuple(_safe_unparse(base) for base in statement.bases)
        node = Node(
            kind=ApiKind.CLASS,
            name=statement.name,
            path=f"{self.module_name}.{key}",
            bases=bases,
            decorators=decorators,
            deprecated=_deprecated_note(statement, decorators, False),
        )
        entry.nodes[key] = node
        count = 1
        for item in statement.body:
            count += self._statement(entry, item, key, depth + 1)
        metaclass = next(
            (_safe_unparse(kw.value) for kw in statement.keywords if kw.arg == "metaclass"), None
        )
        node.constructor_override = metaclass_override(metaclass) or (
            "class defines __new__" if _defines_new(statement) else None
        )
        init = entry.nodes.get(f"{key}.__init__")
        if node.constructor_override is not None:
            node.init_skip_reason = node.constructor_override
        elif init is not None and init.signature is not None:
            node.signature = init.signature.strip_first()
            node.init_synthesized = False
        elif init is not None and init.overloads:
            node.overloads = tuple(sig.strip_first() for sig in init.overloads)
        else:
            leaf_decorators = {_leaf(name) for name in decorators}
            base_leaves = {base.rsplit(".", 1)[-1] for base in bases}
            if leaf_decorators & {"dataclass"} or base_leaves & {"NamedTuple"}:
                synthesized = _synthesize_generated_init(statement, key)
                if synthesized is not None:
                    node.signature = synthesized
                    node.init_synthesized = True
                else:
                    node.init_skip_reason = "generated __init__ could not be derived statically"
            else:
                node.init_skip_reason = "class has no statically-known constructor"
        return count

    def _add_assignment(self, entry: ModuleEntry, statement: ast.stmt, prefix: str) -> int:
        from .index import Node

        count = 0
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "__getattr__":
                    self.dynamic = True
                if isinstance(target, ast.Name) and target.id in {"__all__", "__path__"}:
                    continue
                if isinstance(target, ast.Name):
                    key = f"{prefix}.{target.id}" if prefix else target.id
                    annotation = _safe_unparse(statement.value)
                    entry.nodes[key] = Node(
                        kind=ApiKind.ATTRIBUTE,
                        name=target.id,
                        path=f"{self.module_name}.{key}",
                        annotation=annotation if _looks_like_annotation(annotation) else None,
                    )
                    count += 1
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name):
                key = f"{prefix}.{statement.target.id}" if prefix else statement.target.id
                if key.split(".")[-1] == "__getattr__":
                    self.dynamic = True
                else:
                    entry.nodes[key] = Node(
                        kind=ApiKind.ATTRIBUTE,
                        name=statement.target.id,
                        path=f"{self.module_name}.{key}",
                        annotation=_safe_unparse(statement.annotation),
                    )
                    count += 1
        return count

    def _add_import(
        self, entry: ModuleEntry, statement: ast.Import | ast.ImportFrom, prefix: str
    ) -> int:
        from .index import Node

        if prefix:
            return 0
        count = 0
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name == "__future__":
                    continue
                bound = alias.asname or alias.name.split(".", 1)[0]
                target = alias.name if alias.asname else alias.name.split(".", 1)[0]
                entry.nodes[bound] = Node(
                    kind=ApiKind.MODULE,
                    name=bound,
                    path=f"{self.module_name}.{bound}",
                    target=target,
                    members_complete=False,
                )
                count += 1
            return count
        module = statement.module or ""
        absolute = self._absolute_import(module, statement.level)
        if not absolute and statement.level == 0:
            return 0
        for alias in statement.names:
            if alias.name == "*":
                if absolute:
                    self.wildcards.append(absolute)
                else:
                    self.wildcards.append("*")
                continue
            target = f"{absolute}.{alias.name}" if absolute else alias.name
            bound = alias.asname or alias.name
            entry.nodes[bound] = Node(
                kind=ApiKind.MODULE,
                name=bound,
                path=f"{self.module_name}.{bound}",
                target=target,
                members_complete=False,
            )
            count += 1
        return count

    def _absolute_import(self, module: str, level: int) -> str:
        if level == 0:
            return module
        base = (
            self.module_name
            if self.is_package
            else self.module_name.rsplit(".", 1)[0]
            if "." in self.module_name
            else ""
        )
        parts = base.split(".") if base else []
        for _ in range(level - 1):
            if parts:
                parts.pop()
        if module:
            parts.extend(module.split("."))
        return ".".join(parts)


def _defines_new(statement: ast.ClassDef) -> bool:
    return any(
        isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == "__new__"
        for item in statement.body
    )


def _decorator_name(decorator: ast.expr) -> str:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return _safe_unparse(target)


#: Section headers (numpydoc + Google style) that can follow a ``.. deprecated::``
#: directive which documents a single *parameter* rather than the object itself.
_DOC_SECTIONS = (
    "Parameters",
    "Other Parameters",
    "Args",
    "Arguments",
    "Keyword Args",
    "Keyword Arguments",
    "Attributes",
    "Methods",
    "Returns",
    "Yields",
    "Receives",
    "Raises",
    "Warns",
    "Warnings",
    "See Also",
    "Notes",
    "Note",
    "References",
    "Examples",
    "Example",
)
_SECTION_HEADER = re.compile(
    r"^[ \t]*(" + "|".join(_DOC_SECTIONS) + r")[ \t]*\n[ \t]*[-=~^\"']{3,}[ \t]*$",
    re.MULTILINE,
)
_DEPRECATED_DIRECTIVE = re.compile(r"\.\.\s*deprecated::\s*(.*)")


def deprecation_note(docstring: str) -> str | None:
    """Return a note when *docstring* deprecates the object it documents.

    ``.. deprecated::`` also gets used inside ``Parameters`` sections to document a
    single deprecated argument (``pandas.NDFrame.fillna``, ``DataFrame.from_records``
    and friends do this).  Such a directive must never be reported as the object
    itself being deprecated, so only a directive that appears before the first
    section header counts -- the position pandas and NumPy use for object-level
    deprecations.
    """
    match = _DEPRECATED_DIRECTIVE.search(docstring)
    if match:
        header = _SECTION_HEADER.search(docstring)
        if header is None or match.start() < header.start():
            note = match.group(1).strip()
            return f"docstring: .. deprecated:: {note}".rstrip()
        return None
    if re.search(r"^\s*Deprecated:\s*(.*)$", docstring, re.MULTILINE):
        return "docstring: Deprecated:"
    return None


def _deprecated_note(
    statement: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    decorators: tuple[str, ...],
    obsolete_flag: bool,
) -> str | None:
    for name in decorators:
        if _leaf(name) in _DEPRECATED_NAMES:
            return f"decorated with @{name}"
    if obsolete_flag:
        return None
    return deprecation_note(ast.get_docstring(statement) or "")


def _safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _looks_like_annotation(text: str) -> bool:
    """A crude guard so ``x = 3`` in a stub does not become an annotation."""
    if not text:
        return False
    return bool(re.fullmatch(r"[\w\.\[\], \|\"']+", text)) and any(
        token in text for token in (".", "[", "|")
    )


def _synthesize_generated_init(statement: ast.ClassDef, key: str) -> Signature | None:
    parameters: list[Parameter] = []
    for item in statement.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            name = item.target.id
            if name.startswith("__"):
                continue
            annotation = _safe_unparse(item.annotation)
            if "ClassVar" in annotation:
                continue
            if item.value is not None:
                value_text = _safe_unparse(item.value)
                if re.search(r"field\s*\([^)]*init\s*=\s*False", value_text):
                    return None
            parameters.append(
                Parameter(
                    name=name,
                    kind=ParameterKind.POSITIONAL_OR_KEYWORD,
                    has_default=item.value is not None,
                    annotation=annotation or None,
                )
            )
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("__"):
                    parameters.append(
                        Parameter(
                            name=target.id,
                            kind=ParameterKind.POSITIONAL_OR_KEYWORD,
                            has_default=True,
                        )
                    )
    if not parameters:
        return None
    return Signature(parameters=tuple(parameters), origin=key, is_stub=True, is_class_init=True)


def _signature_from_args(
    args: ast.arguments,
    *,
    origin: str,
    is_stub: bool,
    is_class_init: bool = False,
    returns: str | None = None,
) -> Signature:
    parameters: list[Parameter] = []
    posonly = list(args.posonlyargs)
    positional = posonly + list(args.args)
    defaults = list(args.defaults)
    offset = len(positional) - len(defaults)
    for index, arg in enumerate(positional):
        parameters.append(
            Parameter(
                name=arg.arg,
                kind=(
                    ParameterKind.POSITIONAL_ONLY
                    if index < len(posonly)
                    else ParameterKind.POSITIONAL_OR_KEYWORD
                ),
                has_default=bool(defaults) and index >= offset,
                annotation=_safe_unparse(arg.annotation) or None,
            )
        )
    if args.vararg is not None:
        parameters.append(
            Parameter(
                name=args.vararg.arg,
                kind=ParameterKind.VAR_POSITIONAL,
                annotation=_safe_unparse(args.vararg.annotation) or None,
            )
        )
    for index, arg in enumerate(args.kwonlyargs):
        default = args.kw_defaults[index] if index < len(args.kw_defaults) else None
        parameters.append(
            Parameter(
                name=arg.arg,
                kind=ParameterKind.KEYWORD_ONLY,
                has_default=default is not None,
                annotation=_safe_unparse(arg.annotation) or None,
            )
        )
    if args.kwarg is not None:
        parameters.append(
            Parameter(
                name=args.kwarg.arg,
                kind=ParameterKind.VAR_KEYWORD,
                annotation=_safe_unparse(args.kwarg.annotation) or None,
            )
        )
    return Signature(
        parameters=tuple(parameters),
        origin=origin,
        is_stub=is_stub,
        is_class_init=is_class_init,
        return_annotation=returns,
    )


_VERSION_COMPARATORS = {
    "sys.version_info",
    "sys.version_info[:2]",
    "sys.version_info[0:2]",
    "sys.version_info[:3]",
    "sys.version_info[0:3]",
    "sys.version",
}

_PLATFORM_TEST = re.compile(r"sys\.platform\s*(==|!=|\.startswith)\s*\(?\s*['\"]([^'\"]+)['\"]")


def evaluate_condition(
    test: ast.expr,
    version: tuple[int, int] | None,
    platform: str | None,
) -> bool | None:
    """Evaluate a stub condition against the target interpreter.

    Returns ``True``/``False`` when the outcome is certain and ``None`` when it
    is not, in which case callers must keep both branches.
    """
    if isinstance(test, ast.BoolOp):
        verdicts = [evaluate_condition(value, version, platform) for value in test.values]
        if isinstance(test.op, ast.And):
            if any(v is False for v in verdicts):
                return False
            if all(v is True for v in verdicts):
                return True
            return None
        if any(v is True for v in verdicts):
            return True
        if all(v is False for v in verdicts):
            return False
        return None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = evaluate_condition(test.operand, version, platform)
        return None if inner is None else (not inner)
    if isinstance(test, ast.Compare):
        return _eval_compare(test, version, platform)
    if isinstance(test, ast.Name):
        if test.id == "TYPE_CHECKING":
            return True
        if test.id == "PY2":
            return False
        return None
    if isinstance(test, ast.Attribute):
        return None
    if isinstance(test, ast.Call):
        return _eval_call(test, platform)
    if isinstance(test, ast.Constant) and isinstance(test.value, bool):
        return test.value
    return None


def _eval_compare(
    test: ast.Compare, version: tuple[int, int] | None, platform: str | None
) -> bool | None:
    left = _safe_unparse(test.left)
    if left not in _VERSION_COMPARATORS and not left.startswith("sys.version_info"):
        text = _safe_unparse(test)
        match = _PLATFORM_TEST.search(text)
        if match and platform is not None:
            operator, value = match.group(1), match.group(2)
            actual = platform
            if operator == "==":
                return actual == value
            if operator == "!=":
                return actual != value
            return actual.startswith(value)
        if left in {"os.name", "sys.platform"} and platform is None:
            return None
        return None
    if version is None or len(test.ops) != 1 or len(test.comparators) != 1:
        return None
    right = test.comparators[0]
    if not isinstance(right, ast.Tuple):
        return None
    literal: list[int] = []
    for element in right.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, int):
            return None
        literal.append(element.value)
    sliced: tuple[int, ...] = version
    if "[" in left:
        inside = left[left.index("[") : left.index("]")]
        stop = inside.split(":")[-1].strip(" ]")
        if stop.isdigit():
            sliced = version[: int(stop)]
    target = tuple(literal[: len(sliced)])
    operation = test.ops[0]
    if isinstance(operation, ast.GtE):
        return sliced >= target
    if isinstance(operation, ast.Gt):
        return sliced > target
    if isinstance(operation, ast.LtE):
        return sliced <= target
    if isinstance(operation, ast.Lt):
        return sliced < target
    if isinstance(operation, ast.Eq):
        return sliced == target
    if isinstance(operation, ast.NotEq):
        return sliced != target
    return None


def _eval_call(test: ast.Call, platform: str | None) -> bool | None:
    text = _safe_unparse(test)
    match = _PLATFORM_TEST.search(text)
    if match and platform is not None:
        operator, value = match.group(1), match.group(2)
        if operator == ".startswith":
            return platform.startswith(value)
        if operator == "==":
            return platform == value
        if operator == "!=":
            return platform != value
    if text in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
        return True
    return None
