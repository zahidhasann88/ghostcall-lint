from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

__all__ = ["Binding", "FileScope", "Scope", "StarImport"]

BindingKind = Literal["module", "member"]


@dataclass(frozen=True)
class Binding:
    """A name bound in a scope, pointing at a canonical dotted target."""

    name: str
    target: str
    kind: BindingKind
    line: int
    col: int
    optional: bool = False
    """Imported under ``try/except ImportError``, ``TYPE_CHECKING`` or a guard."""
    relative: bool = False
    """Came from a relative import: project-local code, never checked."""
    module_target: bool = False
    """``import a.b`` (as opposed to ``from a import b``)."""
    doc: str = ""

    def __str__(self) -> str:
        return f"{self.name} -> {self.target}"


@dataclass(frozen=True)
class StarImport:
    name: str
    module: str
    line: int
    optional: bool = False


@dataclass
class Scope:
    """A lexical scope (module, function, class, comprehension)."""

    kind: Literal["module", "function", "class", "comprehension", "lambda"]
    start: int
    end: int
    parent: Scope | None = None
    bindings: dict[str, Binding] = field(default_factory=dict)
    stars: list[StarImport] = field(default_factory=list)
    assigned: set[str] = field(default_factory=set)
    """Names rebound by anything other than an import (shadowing candidates)."""
    deleted: set[str] = field(default_factory=set)
    parameters: set[str] = field(default_factory=set)
    globals_: set[str] = field(default_factory=set)
    name: str = "<module>"

    def contains(self, line: int) -> bool:
        return self.start <= line <= self.end

    def chain(self) -> Iterator[Scope]:
        scope: Scope | None = self
        while scope is not None:
            yield scope
            scope = scope.parent


@dataclass
class ShadowInfo:
    """Why a name cannot be resolved."""

    reason: str


class FileScope:
    """Import and scope information for one parsed module."""

    def __init__(self, tree: ast.Module, filename: str = "<unknown>") -> None:
        self.filename = filename
        self.tree = tree
        self.module_scope = Scope(kind="module", start=0, end=10**9, name="<module>")
        self.scopes: list[Scope] = [self.module_scope]
        self._build(tree.body, self.module_scope)

    def scope_at(self, line: int) -> Scope:
        """The innermost scope containing ``line``."""
        best = self.module_scope
        for scope in self.scopes:
            if scope is self.module_scope or not scope.contains(line):
                continue
            if best is self.module_scope or (scope.start, -scope.end) > (best.start, -best.end):
                best = scope
        return best

    def lookup(self, name: str, line: int) -> tuple[Binding | None, str]:
        """Resolve a name at ``line``.

        Returns ``(binding, reason)``: exactly one of the two is meaningful.
        """
        scope = self.scope_at(line)
        for current in scope.chain():
            if name in current.deleted:
                return None, "name was deleted"
            binding = current.bindings.get(name)
            if binding is not None:
                if name in current.assigned:
                    return None, "name is imported but also reassigned in the same scope"
                return binding, ""
            if name in current.assigned or name in current.parameters:
                return None, "name is rebound locally (shadowed)"
        for current in scope.chain():
            for star in current.stars:
                return None, f"name may come from 'from {star.module} import *'"
        return None, "name is not imported from a library"

    def star_imports(self, line: int) -> Sequence[StarImport]:
        scope = self.scope_at(line)
        out: list[StarImport] = []
        for current in scope.chain():
            out.extend(current.stars)
        return out

    def binding_at(self, name: str, line: int) -> Binding | None:
        """The import binding for ``name``, ignoring shadowing.

        Used to decide whether an import is *optional* (``try/except
        ImportError``, ``TYPE_CHECKING``, version guards) even when the name is
        later rebound by the analysed file.
        """
        scope = self.scope_at(line)
        for current in scope.chain():
            binding = current.bindings.get(name)
            if binding is not None:
                return binding
        return None

    def imports(self) -> list[Binding]:
        return [binding for scope in self.scopes for binding in scope.bindings.values()]

    def shadowed_names(self) -> set[str]:
        return set(self.module_scope.assigned) | set(self.module_scope.deleted)

    def _build(self, body: Sequence[ast.stmt], scope: Scope) -> None:
        for statement in body:
            self._statement(statement, scope)

    def _statement(self, statement: ast.stmt, scope: Scope) -> None:
        # Expressions hanging off a statement (``if (x := ...)``, ``while``
        # tests, ``for`` iterables, ``with`` items, decorators) can rebind
        # names through walrus/comprehension targets.
        for _, value in ast.iter_fields(statement):
            if isinstance(value, ast.expr):
                self._expression(value, scope)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.expr):
                        self._expression(item, scope)
                    elif isinstance(item, ast.withitem):
                        self._expression(item.context_expr, scope)
        if isinstance(statement, ast.Import):
            self._import(statement, scope, optional=False)
            return
        if isinstance(statement, ast.ImportFrom):
            self._import_from(statement, scope, optional=False)
            return
        if isinstance(statement, ast.Try | ast.TryStar):
            self._in_try(statement, scope)
            return
        if isinstance(statement, ast.If):
            optional = _is_type_checking(statement.test) or _is_version_guard(statement.test)
            self._body_optional(statement.body, scope, optional)
            self._body_optional(statement.orelse, scope, optional)
            return
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            scope.assigned.add(statement.name)
            self._function(statement, scope)
            return
        if isinstance(statement, ast.ClassDef):
            scope.assigned.add(statement.name)
            self._class(statement, scope)
            return
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                self._targets(target, scope)
            return
        if isinstance(statement, ast.AnnAssign | ast.AugAssign):
            self._targets(statement.target, scope)
            return
        if isinstance(statement, ast.Delete):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    for owner in self._owners(target.id, scope):
                        owner.deleted.add(target.id)
            return
        if isinstance(statement, ast.Global | ast.Nonlocal):
            scope.globals_.update(statement.names)
            return
        if isinstance(statement, ast.For | ast.AsyncFor):
            self._targets(statement.target, scope)
            self._body_optional(statement.body, scope, optional=False)
            self._body_optional(statement.orelse, scope, optional=False)
            return
        if isinstance(statement, ast.With | ast.AsyncWith):
            for item in statement.items:
                if item.optional_vars is not None:
                    self._targets(item.optional_vars, scope)
            self._body_optional(statement.body, scope, optional=False)
            return
        if isinstance(statement, ast.While):
            self._body_optional(statement.body, scope, optional=False)
            self._body_optional(statement.orelse, scope, optional=False)
            return
        if isinstance(statement, ast.Match):
            for case in statement.cases:
                for node in ast.walk(case.pattern):
                    captured = getattr(node, "name", None) or getattr(node, "rest", None)
                    if isinstance(captured, str):
                        self._assign(captured, scope)
                self._body_optional(case.body, scope, optional=False)
            return

    def _body_optional(self, body: Sequence[ast.stmt], scope: Scope, optional: bool) -> None:
        for statement in body:
            if optional and isinstance(statement, ast.Import | ast.ImportFrom):
                if isinstance(statement, ast.Import):
                    self._import(statement, scope, optional=True)
                else:
                    self._import_from(statement, scope, optional=True)
                continue
            self._statement(statement, scope)

    def _in_try(self, statement: ast.Try | ast.TryStar, scope: Scope) -> None:
        guarded = any(_is_import_error(handler.type) for handler in statement.handlers) or any(
            isinstance(item, ast.Import | ast.ImportFrom) for item in statement.body
        )
        self._body_optional(statement.body, scope, optional=guarded)
        for handler in statement.handlers:
            if handler.name:
                self._assign(handler.name, scope)
            self._body_optional(handler.body, scope, optional=guarded)
        self._body_optional(statement.orelse, scope, optional=False)
        self._body_optional(statement.finalbody, scope, optional=False)

    def _import(self, statement: ast.Import, scope: Scope, optional: bool) -> None:
        for alias in statement.names:
            if alias.name == "__future__":
                continue
            if alias.asname:
                bound, target, module_target = alias.asname, alias.name, True
            else:
                bound = alias.name.split(".", 1)[0]
                target = bound
                module_target = True
            self._bind(
                scope,
                bound,
                target,
                statement,
                kind="module",
                optional=optional,
                module_target=module_target,
            )

    def _import_from(self, statement: ast.ImportFrom, scope: Scope, optional: bool) -> None:
        module = statement.module or ""
        if any(alias.name == "*" for alias in statement.names):
            scope.stars.append(
                StarImport(name="*", module=module or ".", line=statement.lineno, optional=optional)
            )
            return
        if statement.level:
            absolute = _relative_target(module, statement.level)
            for alias in statement.names:
                bound = alias.asname or alias.name
                self._bind(
                    scope,
                    bound,
                    f"{absolute}.{alias.name}" if absolute else alias.name,
                    statement,
                    kind="member",
                    optional=optional,
                    relative=True,
                )
            return
        if not module:
            return
        for alias in statement.names:
            bound = alias.asname or alias.name
            self._bind(
                scope,
                bound,
                f"{module}.{alias.name}",
                statement,
                kind="member",
                optional=optional,
            )

    def _bind(
        self,
        scope: Scope,
        name: str,
        target: str,
        statement: ast.stmt,
        *,
        kind: BindingKind,
        optional: bool,
        relative: bool = False,
        module_target: bool = False,
    ) -> None:
        scope.bindings[name] = Binding(
            name=name,
            target=target,
            kind=kind,
            line=statement.lineno,
            col=statement.col_offset,
            optional=optional,
            relative=relative,
            module_target=module_target,
        )

    def _function(self, statement: ast.FunctionDef | ast.AsyncFunctionDef, parent: Scope) -> None:
        scope = Scope(
            kind="function",
            start=statement.lineno,
            end=getattr(statement, "end_lineno", statement.lineno) or statement.lineno,
            parent=parent,
            name=statement.name,
        )
        for arg in _arguments(statement.args):
            scope.parameters.add(arg.arg)
        self.scopes.append(scope)
        self._build_scope_body(statement.body, scope)

    def _class(self, statement: ast.ClassDef, parent: Scope) -> None:
        scope = Scope(
            kind="class",
            start=statement.lineno,
            end=getattr(statement, "end_lineno", statement.lineno) or statement.lineno,
            parent=parent,
            name=statement.name,
        )
        self.scopes.append(scope)
        self._build_scope_body(statement.body, scope)

    def _build_scope_body(self, body: Sequence[ast.stmt], scope: Scope) -> None:
        for statement in body:
            self._statement(statement, scope)

    def _owners(self, name: str, scope: Scope) -> list[Scope]:
        """Scopes whose binding of ``name`` an assignment in ``scope`` changes.

        ``global x`` makes the assignment rebind the module's ``x``; ``nonlocal
        x`` rebinds an enclosing function's.  Both count as shadowing there too,
        so an import of ``x`` is never trusted after such a rebinding.
        """
        if name not in scope.globals_:
            return [scope]
        owners = [scope]
        parent = scope.parent
        while parent is not None:
            owners.append(parent)
            parent = parent.parent
        return owners

    def _assign(self, name: str, scope: Scope) -> None:
        for owner in self._owners(name, scope):
            owner.assigned.add(name)

    def _targets(self, target: ast.expr, scope: Scope) -> None:
        if isinstance(target, ast.Name):
            self._assign(target.id, scope)
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._targets(element, scope)
        elif isinstance(target, ast.Starred):
            self._targets(target.value, scope)
        elif isinstance(target, ast.Attribute):
            self._expression(target.value, scope)

    def _expression(self, expression: ast.expr, scope: Scope) -> None:
        for node in ast.walk(expression):
            if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                self._assign(node.target.id, scope)
            elif isinstance(node, ast.Lambda):
                for arg in _arguments(node.args):
                    scope.assigned.add(arg.arg)
            elif isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
                for generator in node.generators:
                    self._targets(generator.target, scope)


def _arguments(args: ast.arguments) -> list[ast.arg]:
    out = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    if args.vararg is not None:
        out.append(args.vararg)
    if args.kwarg is not None:
        out.append(args.kwarg)
    return out


def _is_import_error(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in {"ImportError", "ModuleNotFoundError"}
    if isinstance(node, ast.Tuple):
        return any(_is_import_error(element) for element in node.elts)
    if isinstance(node, ast.Attribute):
        return node.attr in {"ImportError", "ModuleNotFoundError"}
    return False


def _is_type_checking(node: ast.expr) -> bool:
    text = _unparse(node)
    return text in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}


def _is_version_guard(node: ast.expr) -> bool:
    text = _unparse(node)
    return "version_info" in text or "sys.platform" in text or "impl" in text


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _relative_target(module: str, level: int) -> str:
    """Best-effort absolute target of a relative import (dots are elided)."""
    prefix = "." * level
    return f"{prefix}{module}" if module else prefix
