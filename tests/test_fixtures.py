"""Rules against a synthetic installed package, with no pinned environment needed.

The fixture package below is written into a temporary ``site-packages`` and the
target environment is ghostcall's own interpreter with that directory as its only
site directory.  Every expectation is therefore a fact about code in this file:
decorators (trusted and untrusted), generated constructors, metaclasses,
``__new__``, bound methods, module ``__getattr__``, namespace packages, shadowing
and conditional imports.
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from itertools import count
from pathlib import Path
from textwrap import dedent

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ghostcall.checks import Checker, Config, FileResult
from ghostcall.env import Environment
from ghostcall.index import ApiIndex
from ghostcall.report import Report, render_github, render_json, render_sarif

FIXTURE_PACKAGE = '''
"""A library whose every API shape is known to the tests."""

import dataclasses
import functools
from typing import Any, Callable, NamedTuple, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def passthrough(func: F) -> F:
    """Annotated identity, and the source returns the argument itself."""
    func.__doc__ = "documented"
    return func


def docs(text: str) -> Callable[[F], F]:
    """A decorator factory that only edits the docstring."""

    def decorate(func: F) -> F:
        func.__doc__ = text
        return func

    return decorate


def wrapping(func: F) -> F:
    """Annotated identity, but the source returns a wrapper that eats ``legacy=``."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        kwargs.pop("legacy", None)
        return func(*args, **kwargs)

    return wrapper


def vague(func: Callable[..., Any]) -> Callable[..., Any]:
    """Matching annotations that prove nothing about the parameters."""
    return func


def untyped(func):
    return func


class Stamp:
    """A class-based decorator: ``Stamp(...)(func)`` returns ``func``."""

    def __init__(self, label: str) -> None:
        self.label = label

    def __call__(self, func: F) -> F:
        func.__doc__ = self.label
        return func


@functools.lru_cache(maxsize=None)
def cached(a, b=1):
    return a


@passthrough
def plain(a, *, b=1):
    return a


@docs("hello")
def documented(a, *, b=1):
    return a


@Stamp("label")
def stamped(a):
    return a


@wrapping
def wrapped(a, *, b=1):
    return a


@vague
def loosely(a):
    return a


@untyped
def unknown(a):
    return a


def positional_only(a, b, /, c=1):
    return a


@dataclasses.dataclass
class Point:
    x: int
    y: int = 0


class Pair(NamedTuple):
    left: int
    right: int


class Meta(type):
    def __call__(cls, *args, **kwargs):
        return super().__call__()


class WithMeta(metaclass=Meta):
    def __init__(self, a):
        self.a = a


class WithNew:
    def __new__(cls, *args):
        return super().__new__(cls)

    def __init__(self, a):
        self.a = a


class Methods:
    def __init__(self) -> None:
        self.ready = True

    def run(self, a, *, flag=False):
        return a

    @classmethod
    def build(cls, a):
        return cls()

    @staticmethod
    def helper(a, b):
        return a

    @functools.cached_property
    def size(self):
        return 1


def make() -> Methods:
    return Methods()
'''

DYNAMIC_MODULE = """
def __getattr__(name):
    return name
"""

NAMESPACE_MODULE = """
def func(a):
    return a
"""


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("site-packages")
    package = root / "fixturelib"
    package.mkdir()
    (package / "__init__.py").write_text(FIXTURE_PACKAGE, encoding="utf-8")
    (package / "dynamic.py").write_text(DYNAMIC_MODULE, encoding="utf-8")
    namespace = root / "nspkg"
    namespace.mkdir()
    (namespace / "sub.py").write_text(NAMESPACE_MODULE, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def fixture_env(site: Path) -> Environment:
    """ghostcall's own interpreter, with the fixture directory as its site-packages."""
    real = Environment.from_python(sys.executable)
    return replace(
        real,
        site_packages=(site,),
        search_paths=(),
        _import_to_dist={"fixturelib": ("fixturelib",), "nspkg": ("nspkg",)},
        _dist_versions={"fixturelib": "1.0.0", "nspkg": "0.1.0"},
        _local_modules={},
        _editable_roots=(),
        extra_search_paths=(),
    )


@pytest.fixture(scope="module")
def fixture_index(fixture_env: Environment) -> ApiIndex:
    return ApiIndex(fixture_env)


@pytest.fixture
def run(fixture_env: Environment, fixture_index: ApiIndex) -> Callable[..., FileResult]:
    def check(source: str, *, filename: str = "snippet.py", **overrides: object) -> FileResult:
        config = Config(**overrides)  # type: ignore[arg-type]
        return Checker(fixture_env, config, index=fixture_index).check_source(
            dedent(source), filename=filename
        )

    return check


def codes(result: FileResult) -> list[str]:
    return sorted(finding.code for finding in result.findings)


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        pytest.param("fixturelib.cached(1, 2, 3)", ["GC005"], id="lru_cache-is-pass-through"),
        pytest.param("fixturelib.plain(1, c=2)", ["GC004"], id="identity-decorator"),
        pytest.param("fixturelib.documented(1, c=2)", ["GC004"], id="identity-factory"),
        pytest.param("fixturelib.stamped(1, 2)", ["GC005"], id="identity-class-decorator"),
    ],
)
def test_signature_preserving_decorators_are_trusted(run, call: str, expected: list[str]) -> None:
    assert codes(run(f"import fixturelib\n\n{call}\n")) == expected


@pytest.mark.parametrize(
    ("call", "decorator"),
    [
        pytest.param("fixturelib.wrapped(1, legacy=True)", "wrapping", id="returns-a-wrapper"),
        pytest.param("fixturelib.loosely(1, 2, 3)", "vague", id="vague-annotations"),
        pytest.param("fixturelib.unknown()", "untyped", id="unannotated"),
    ],
)
def test_signature_changing_decorators_are_skipped(run, call: str, decorator: str) -> None:
    result = run(f"import fixturelib\n\n{call}\n")
    assert codes(result) == []
    reasons = [item.reason for item in result.unresolved]
    assert any(f"@fixturelib.{decorator}" in reason for reason in reasons), reasons


def test_local_code_decorated_by_a_signature_changing_decorator(
    fixture_env: Environment, tmp_path: Path
) -> None:
    """``@click.command() def main(name)`` then ``main()`` is fine at runtime.

    Regression for the false positive the decorator rule exists to prevent: a
    project-local function whose decorator rewrites its signature.
    """
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        "from fixturelib import untyped\n\n\n@untyped\ndef main(name):\n    return name\n",
        encoding="utf-8",
    )
    script = package / "run.py"
    script.write_text("from app.cli import main\n\nmain()\n", encoding="utf-8")
    env = replace(fixture_env, _local_modules={"app": package})
    result = Checker(env, Config(), index=ApiIndex(env)).check_file(script)
    assert codes(result) == []


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        pytest.param("fixturelib.Point()", ["GC006"], id="dataclass-missing"),
        pytest.param("fixturelib.Point(1, 2, 3)", ["GC005"], id="dataclass-too-many"),
        pytest.param("fixturelib.Point(1, z=2)", ["GC004"], id="dataclass-keyword"),
        pytest.param("fixturelib.Point(1, y=2)", [], id="dataclass-valid"),
        pytest.param("fixturelib.Pair(1)", ["GC006"], id="namedtuple-missing"),
        pytest.param("fixturelib.Pair(1, 2)", [], id="namedtuple-valid"),
        pytest.param("fixturelib.WithMeta()", [], id="metaclass-skipped"),
        pytest.param("fixturelib.WithNew()", [], id="custom-new-skipped"),
        pytest.param("fixturelib.Methods.build()", ["GC006"], id="classmethod-strips-cls"),
        pytest.param("fixturelib.Methods.build(1)", [], id="classmethod-valid"),
        pytest.param("fixturelib.Methods.helper(1)", ["GC006"], id="staticmethod-keeps-all"),
        pytest.param("fixturelib.positional_only(1, b=2)", ["GC004", "GC006"], id="posonly"),
        pytest.param("fixturelib.positional_only(1, 2, c=3)", [], id="posonly-valid"),
    ],
)
def test_constructor_and_method_signatures(run, call: str, expected: list[str]) -> None:
    assert codes(run(f"import fixturelib\n\n{call}\n")) == expected


def test_bound_methods_through_instance_tracking_are_medium(run) -> None:
    source = """
        import fixturelib

        tool = fixturelib.Methods()
        tool.run(1, flagg=True)
        fixturelib.make().run()
        print(tool.size)
        """
    assert codes(run(source)) == []
    result = run(source, include_medium=True)
    assert codes(result) == ["GC004", "GC006"]
    assert {finding.confidence.value for finding in result.findings} == {"medium"}


def test_module_getattr_makes_absence_unprovable(run) -> None:
    result = run("import fixturelib.dynamic\n\nfixturelib.dynamic.anything_at_all()\n")
    assert codes(result) == []


def test_namespace_packages(run) -> None:
    assert codes(run("import nspkg.sub\n\nnspkg.sub.func(1)\n")) == []
    assert codes(run("import nspkg.sub\n\nnspkg.sub.missing()\n")) == ["GC003"]
    # The namespace itself has no file to read: absence there proves nothing.
    assert codes(run("import nspkg\n\nnspkg.other()\n")) == []


def test_missing_name_in_a_real_module(run) -> None:
    result = run("from fixturelib import plian\n")
    assert codes(result) == ["GC002"]
    assert result.findings[0].suggestion == "plain"
    assert result.findings[0].installed == "fixturelib 1.0.0"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """
            import fixturelib

            def reset():
                global fixturelib
                fixturelib = object()

            fixturelib.nope()
            """,
            id="global-rebinding",
        ),
        pytest.param(
            """
            import fixturelib

            if (fixturelib := object()):
                fixturelib.nope()
            """,
            id="walrus-in-if-test",
        ),
        pytest.param(
            """
            import fixturelib

            try:
                pass
            except Exception as fixturelib:
                fixturelib.nope()
            """,
            id="except-as",
        ),
        pytest.param(
            """
            import fixturelib

            match object():
                case fixturelib:
                    fixturelib.nope()
            """,
            id="match-capture",
        ),
        pytest.param(
            """
            import fixturelib

            for fixturelib in range(3):
                fixturelib.nope()
            """,
            id="loop-variable",
        ),
        pytest.param(
            """
            import fixturelib

            def use(fixturelib):
                fixturelib.nope()
            """,
            id="parameter",
        ),
        pytest.param(
            """
            import fixturelib

            del fixturelib
            fixturelib.nope()
            """,
            id="deleted",
        ),
    ],
)
def test_shadowed_imports_are_never_judged(run, source: str) -> None:
    assert codes(run(source, include_medium=True)) == []


def test_unshadowed_import_is_still_judged(run) -> None:
    assert codes(run("import fixturelib\n\nfixturelib.nope()\n")) == ["GC003"]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import not_installed_x\n",
            id="type-checking",
        ),
        pytest.param(
            "import sys\n\nif sys.version_info >= (3, 99):\n    import not_installed_x\n",
            id="version-guard",
        ),
        pytest.param(
            "try:\n    import not_installed_x\nexcept ImportError:\n    not_installed_x = None\n",
            id="try-import-error",
        ),
    ],
)
def test_optional_imports_are_not_gc001(run, source: str) -> None:
    assert codes(run(source)) == []


def test_unguarded_missing_module_is_gc001(run) -> None:
    assert codes(run("import not_installed_x\n")) == ["GC001"]


def test_trailing_suppression_does_not_leak_to_the_next_line(run) -> None:
    source = """
        import fixturelib

        fixturelib.plain(1, c=2)  # ghostcall: ignore
        fixturelib.plain(1, d=2)
        """
    result = run(source)
    assert codes(result) == ["GC004"]
    assert result.findings[0].line == 5


def test_comment_line_suppression_covers_the_line_below(run) -> None:
    source = """
        import fixturelib

        # ghostcall: ignore[GC004]
        fixturelib.plain(1, c=2)
        """
    assert codes(run(source)) == []


def test_parse_errors_reach_every_machine_format(run, fixture_env: Environment) -> None:
    broken = run("def broken(:\n", filename="broken.py")
    report = Report(results=[broken], env=fixture_env, files_scanned=1)
    assert report.findings == [], "GC000 must not count as a failing finding"

    payload = json.loads(render_json(report))
    assert [item["code"] for item in payload["findings"]] == ["GC000"]
    assert payload["summary"]["parse_errors"] == 1
    sarif = json.loads(render_sarif(report))
    assert [item["ruleId"] for item in sarif["runs"][0]["results"]] == ["GC000"]
    assert "title=GC000" in render_github(report)


def test_gc000_can_be_ignored(run) -> None:
    assert run("def broken(:\n", ignore=("GC000",)).parse_error is None


def test_parallel_runs_match_serial_runs(fixture_env: Environment) -> None:
    sources = [
        f"import fixturelib\n\nfixturelib.plain({index}, c{index}=1)\nfixturelib.Point()\n"
        for index in range(24)
    ]
    serial_checker = Checker(fixture_env, Config(), index=ApiIndex(fixture_env))
    serial = [serial_checker.check_source(s, f"f{i}.py") for i, s in enumerate(sources)]
    parallel_checker = Checker(fixture_env, Config(), index=ApiIndex(fixture_env))
    with ThreadPoolExecutor(max_workers=8) as pool:
        parallel = list(
            pool.map(
                lambda item: parallel_checker.check_source(item[1], f"f{item[0]}.py"),
                enumerate(sources),
            )
        )

    def summary(results: list[FileResult]) -> list[list[tuple[str, int, str]]]:
        return [[(f.code, f.line, f.message) for f in result.findings] for result in results]

    assert summary(parallel) == summary(serial)
    assert all(len(result.findings) == 2 for result in serial)


_identifiers = st.sampled_from(
    ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa"]
)


@st.composite
def parameter_lists(draw: st.DrawFn) -> dict[str, object]:
    """A random *legal* parameter list, as source text plus its structure."""
    names = draw(st.lists(_identifiers, max_size=6, unique=True))
    split_one = draw(st.integers(0, len(names)))
    split_two = draw(st.integers(split_one, len(names)))
    posonly, pos_kw, kwonly = names[:split_one], names[split_one:split_two], names[split_two:]
    positional = posonly + pos_kw
    # Python requires defaults to be trailing among positional parameters.
    first_default = draw(st.integers(0, len(positional)))
    required_kwonly = [name for name in kwonly if draw(st.booleans())]
    var_args = draw(st.booleans())
    var_kwargs = draw(st.booleans())

    def rendered(name: str, index: int) -> str:
        return f"{name}=None" if index >= first_default else name

    parts = [rendered(name, index) for index, name in enumerate(posonly)]
    if posonly:
        parts.append("/")
    parts += [rendered(name, index + len(posonly)) for index, name in enumerate(pos_kw)]
    if var_args:
        parts.append("*args")
    elif kwonly:
        parts.append("*")
    parts += [name if name in required_kwonly else f"{name}=None" for name in kwonly]
    if var_kwargs:
        parts.append("**kwargs")
    return {
        "source": f"def target({', '.join(parts)}):\n    return None\n",
        "required_positional": first_default,
        "positional": len(positional),
        "pos_kw": pos_kw,
        "required_kwonly": required_kwonly,
        "var_args": var_args,
        "var_kwargs": var_kwargs,
    }


@pytest.fixture(scope="module")
def property_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("property-site")


_module_numbers = count()


def _materialise(site: Path, env: Environment, source: str) -> tuple[str, Checker]:
    """Install ``source`` as a fresh module and return a checker that can see it."""
    name = f"propmod_{next(_module_numbers)}"
    ast.parse(source)  # the strategy must only produce valid Python
    (site / f"{name}.py").write_text(source, encoding="utf-8")
    target = replace(
        env,
        site_packages=(site,),
        _import_to_dist={name: (name,)},
        _dist_versions={name: "1.0"},
    )
    return name, Checker(target, Config(), index=ApiIndex(target))


def _call(module: str, positional: int, keywords: list[str]) -> str:
    arguments = [str(index) for index in range(positional)] + [f"{k}=1" for k in keywords]
    return f"import {module}\n\n{module}.target({', '.join(arguments)})\n"


def _codes_for(checker: Checker, source: str) -> list[str]:
    result = checker.check_source(source, "call.py")
    return sorted(finding.code for finding in result.findings)


@given(spec=parameter_lists())
@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_valid_calls_to_installed_functions_are_silent(
    spec: dict[str, object], property_site: Path, fixture_env: Environment
) -> None:
    module, checker = _materialise(property_site, fixture_env, str(spec["source"]))
    call = _call(module, int(spec["required_positional"]), list(spec["required_kwonly"]))  # type: ignore[call-overload]
    assert _codes_for(checker, call) == [], f"{spec['source']}\n{call}"


@given(spec=parameter_lists())
@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_invalid_calls_to_installed_functions_fire_the_right_rule(
    spec: dict[str, object], property_site: Path, fixture_env: Environment
) -> None:
    module, checker = _materialise(property_site, fixture_env, str(spec["source"]))
    required_positional = int(spec["required_positional"])  # type: ignore[call-overload]
    required_kwonly = list(spec["required_kwonly"])  # type: ignore[call-overload]

    if not spec["var_kwargs"]:
        call = _call(module, required_positional, [*required_kwonly, "not_a_parameter"])
        assert _codes_for(checker, call) == ["GC004"], f"{spec['source']}\n{call}"
    if not spec["var_args"]:
        too_many = int(spec["positional"]) + 1  # type: ignore[call-overload]
        call = _call(module, too_many, required_kwonly)
        assert "GC005" in _codes_for(checker, call), f"{spec['source']}\n{call}"
    if required_kwonly:
        call = _call(module, required_positional, required_kwonly[1:])
        assert _codes_for(checker, call) == ["GC006"], f"{spec['source']}\n{call}"
    if required_positional:
        call = _call(module, required_positional - 1, required_kwonly)
        assert _codes_for(checker, call) == ["GC006"], f"{spec['source']}\n{call}"
