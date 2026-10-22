"""Every false positive ever fixed gets a test here, forever.

The corpus in ``bench/`` is the first line of defence, but a regression test
documents *why* the behaviour is what it is, and fails loudly if a later
"improvement" reintroduces the false positive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ghostcall.env import Environment
from tests.conftest import needs_env

pytestmark = needs_env


# FP 1 - ``f(**mapping)``: the keyword dict can supply required parameters
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import requests\n\n"
            'payload = {"url": "https://x.invalid", "timeout": 5}\n'
            "requests.get(**payload)\n",
            id="explicit-keyword-dict",
        ),
        pytest.param(
            'import requests\n\nrequests.get(**{"url": "https://x.invalid"})\n',
            id="inline-dict",
        ),
        pytest.param(
            "import requests\n\n\ndef wrapper(**kwargs):\n    return requests.post(**kwargs)\n",
            id="forwarded-kwargs",
        ),
        pytest.param(
            "import pandas as pd\n\n\ndef load(**options):\n    return pd.read_csv(**options)\n",
            id="forwarded-kwargs-pandas",
        ),
    ],
)
def test_star_kwargs_never_produce_missing_argument_findings(check_medium, source: str) -> None:
    result = check_medium(source)
    assert [f.code for f in result.findings] == [], [f.message for f in result.findings]


def test_missing_argument_is_still_reported_without_unpacking(check_medium) -> None:
    """The fix must not silence the real bug: ``requests.get()`` with no URL."""
    result = check_medium("import requests\n\nrequests.get(timeout=5)\n")
    assert [f.code for f in result.findings] == ["GC006"]


# FP 2 - modules obtained dynamically are typed by their annotation, not guessed
def test_importlib_import_module_result_is_not_typed(check_medium) -> None:
    """``importlib.import_module(...)`` returns ``types.ModuleType``: unknowable."""
    source = (
        "import importlib\n"
        "\n"
        'module = importlib.import_module("requests")\n'
        'module.get("https://x.invalid")\n'
        "module.definitely_not_a_real_function()\n"
    )
    result = check_medium(source)
    assert [f.code for f in result.findings] == [], [f.message for f in result.findings]


def test_supertype_annotations_are_not_tracked(check_medium) -> None:
    """A return annotation from another library describes a shape, not a class."""
    source = (
        "import functools\n"
        "import io\n"
        "\n"
        "buffer = io.StringIO()\n"
        "wrapper = functools.partial(print, 1)\n"
        "buffer.getvalue()\n"
        "wrapper(nonexistent_keyword=1)\n"
    )
    result = check_medium(source)
    assert [f.code for f in result.findings] == [], [f.message for f in result.findings]


def test_constructor_results_are_still_tracked(check_medium) -> None:
    """``df = pd.DataFrame(...)`` is typed by the constructor, and must stay so."""
    source = 'import pandas as pd\n\ndf = pd.DataFrame({"a": [1]})\ndf.append({"a": 2})\n'
    result = check_medium(source)
    assert [f.code for f in result.findings] == ["GC003"]
    assert result.findings[0].symbol == "pandas.core.frame.DataFrame.append"
    assert result.findings[0].message.startswith("pandas.DataFrame has no attribute")


def test_unambiguous_return_annotations_are_still_tracked(check_medium) -> None:
    """A single-class return annotation is exact, so it is tracked (medium)."""
    source = (
        "import sqlalchemy\n"
        "\n"
        'engine = sqlalchemy.create_engine("sqlite://")\n'
        'engine.execute("select 1")\n'
    )
    result = check_medium(source)
    assert [f.code for f in result.findings] == ["GC003"]
    assert result.findings[0].confidence.value == "medium"
    # The message must name the class, never the function that produced it.
    assert result.findings[0].message.startswith("sqlalchemy.engine.base.Engine has no attribute")


def test_union_return_annotations_are_skipped_to_stay_precise(check_medium) -> None:
    """``read_csv`` is annotated ``DataFrame | TextFileReader``: no guessing.

    Documented limitation: because the two return types are indistinguishable
    statically, ``df = pd.read_csv(...)`` is *not* typed, so a later
    ``df.append(...)`` stays unreported (it is listed under ``--verbose`` as
    unresolved instead).
    """
    source = 'import pandas as pd\n\ndf = pd.read_csv("data.csv")\ndf.append({"a": 2})\n'
    result = check_medium(source)
    assert [f.code for f in result.findings] == []


# FP 3 - siblings of the analysed file are project code, never findings
def test_sibling_package_import_is_project_local(make_checker, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    package = root / "myapp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "helpers.py").write_text("def load(): ...\n", encoding="utf-8")
    module = package / "main.py"
    module.write_text(
        "from myapp import helpers\nfrom myapp.helpers import load\n\nload()\n",
        encoding="utf-8",
    )
    checker = make_checker()
    result = checker.check_file(module)
    assert [f.code for f in result.findings] == [], [f.message for f in result.findings]


def test_module_import_of_namespace_directory_is_project_local(
    make_checker, tmp_path: Path
) -> None:
    """A directory without ``__init__.py`` that holds Python is still local."""
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "build.py").write_text("VALUE = 1\n", encoding="utf-8")
    module = root / "script.py"
    module.write_text("import tools.build\n\nprint(tools.build.VALUE)\n", encoding="utf-8")
    checker = make_checker()
    result = checker.check_file(module)
    assert [f.code for f in result.findings] == [], [f.message for f in result.findings]


def test_unknown_third_party_module_is_still_reported(make_checker, tmp_path: Path) -> None:
    """Being conservative about siblings must not disable GC001 itself."""
    module = tmp_path / "script.py"
    module.write_text("import polars\n\npolars.read_csv('data.csv')\n", encoding="utf-8")
    checker = make_checker()
    result = checker.check_file(module)
    assert [f.code for f in result.findings] == ["GC001"]


def test_empty_directory_is_not_a_package(make_checker, tmp_path: Path) -> None:
    """``import data`` where ``data/`` holds no Python at all is not local code."""
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "notes.txt").write_text("not python\n", encoding="utf-8")
    module = tmp_path / "script.py"
    module.write_text("import tables\n\ntables.load()\n", encoding="utf-8")
    checker = make_checker()
    result = checker.check_file(module)
    assert [f.code for f in result.findings] == ["GC001"]


# FP 4 - ``.. deprecated::`` inside a Parameters section documents a parameter
def test_parameter_deprecation_is_not_object_deprecation(check_medium) -> None:
    """``pandas.NDFrame.fillna`` mentions deprecations per *parameter* only.

    Before the fix, the whole method was reported as deprecated because the
    docstring contains a ``.. deprecated::`` directive somewhere.
    """
    source = (
        "import pandas as pd\n"
        "\n"
        "df = pd.DataFrame({'a': [1]})\n"
        "df.fillna(value=0)\n"
        "df.ffill()\n"
        "df.interpolate()\n"
    )
    result = check_medium(source)
    assert [f.code for f in result.findings] == [], [f.message for f in result.findings]


def test_object_level_deprecation_is_still_reported(check_medium) -> None:
    source = "import pandas as pd\n\ndf = pd.DataFrame({'a': [1]})\ndf.applymap(str)\n"
    result = check_medium(source)
    assert [f.code for f in result.findings] == ["GC007"]


# earlier resolver fixes: stale cache, alias chains, unresolved discipline
def test_cache_is_invalidated_when_the_file_changes(env: Environment, tmp_path: Path) -> None:
    """A stale on-disk index must never answer for a rewritten module.

    Regression for the worst bug found in development: the cache key ignored the
    file's mtime, so editing a module kept returning the previous API surface.
    """
    from dataclasses import replace

    from ghostcall.index import ApiCache
    from ghostcall.index import ApiIndex as Index

    module = tmp_path / "localpkg.py"
    module.write_text("def old_name(value): ...\n", encoding="utf-8")
    local_env = replace(
        env,
        search_paths=(tmp_path,),
        _local_modules={"localpkg": module},
    )
    cache = ApiCache(tmp_path / "cache")
    index = Index(local_env, cache=cache)

    first = index.get_module("localpkg")
    assert first is not None and "old_name" in first.nodes

    module.write_text("def new_name(value): ...\n", encoding="utf-8")
    # A new run (fresh index) shares the same on-disk cache: it must notice the edit.
    second = Index(local_env, cache=cache).get_module("localpkg")
    assert second is not None
    assert "new_name" in second.nodes, "cache returned a stale module index"
    assert "old_name" not in second.nodes


def test_cache_schema_version_is_part_of_the_key(env: Environment, tmp_path: Path) -> None:
    """Changing what a cached field means must not resurrect old entries."""
    from ghostcall.index import CACHE_SCHEMA, ApiCache

    cache = ApiCache(tmp_path / "cache")
    module = tmp_path / "pkg.py"
    module.write_text("X = 1\n", encoding="utf-8")
    assert CACHE_SCHEMA in cache.key("pkg", module)
    assert cache.key("pkg", module) != cache.key("pkg", module, extra="different")


# FP 5 - ``module.some_value.method``: an annotated attribute is a *value*,
# so its members are not looked up in the module that defines it
# (found by running ghostcall on itself: ``sys.stdout.write`` and
# ``os.environ.get`` were both reported as missing attributes)
@pytest.mark.parametrize(
    ("source", "chain", "annotation"),
    [
        pytest.param(
            "import sys\n\nsys.stdout.write('hello')\n",
            "sys.stdout.write",
            "TextIO",
            id="sys-stdout-write",
        ),
        pytest.param(
            "import sys\n\nline = sys.stdin.read()\n",
            "sys.stdin.read",
            "TextIO",
            id="sys-stdin-read",
        ),
        pytest.param(
            "import os\n\nvalue = os.environ.get('HOME')\n",
            "os.environ.get",
            "_Environ",
            id="os-environ-get",
        ),
    ],
)
def test_members_of_annotated_module_attributes_are_never_findings(
    check_medium, index, source: str, chain: str, annotation: str
) -> None:
    result = check_medium(source)
    assert [f.code for f in result.findings] == [], [f.message for f in result.findings]
    # The reason must be visible under --verbose instead of a silent skip.
    walked = index.walk_chain(chain)
    assert walked.status.value == "unknown", chain
    assert annotation in walked.reason


def test_unknown_value_members_do_not_hide_real_missing_attributes(check_medium) -> None:
    """The fix must not blanket-silence attribute lookup on modules."""
    result = check_medium("import os\n\nos.definitely_not_here()\n")
    assert [f.code for f in result.findings] == ["GC003"]
    assert "os (stdlib)" in result.findings[0].installed


# FP 6 - a decorator whose *annotations* claim an unchanged signature, while its
# source returns a wrapper that accepts more (``deprecate_kwarg``)
def test_annotated_wrapper_decorators_do_not_vouch_for_a_signature(check_medium) -> None:
    """``Rolling.quantile`` is ``@deprecate_kwarg(old_arg_name="quantile", ...)``.

    The decorator is typed ``Callable[[F], F]``, so trusting annotations alone
    reported ``quantile=`` as an unexpected keyword -- yet pandas 2.2.3 accepts it
    (with a FutureWarning) through the ``(*args, **kwargs)`` wrapper.
    """
    source = (
        "from pandas.core.window.rolling import Rolling\n"
        "import pandas as pd\n"
        "\n"
        "window = Rolling(pd.DataFrame({'a': [1.0, 2.0]}), 2)\n"
        "window.quantile(quantile=0.5)\n"
    )
    result = check_medium(source)
    assert [f.code for f in result.findings] == [], [f.message for f in result.findings]
    assert any("deprecate_kwarg" in item.reason for item in result.unresolved)


def test_docstring_decorators_keep_their_signature(check) -> None:
    """``@Appender``/``@doc`` only edit ``__doc__``: ``read_csv`` stays fully checked."""
    result = check("import pandas as pd\n\npd.read_csv('f.csv', skip_blank=True)\n")
    assert [f.code for f in result.findings] == ["GC004"]


def test_deprecation_is_reported_even_when_the_signature_is_not_trusted(check) -> None:
    """``DataFrame.to_gbq`` is wrapped by ``deprecate_nonkeyword_arguments``."""
    source = "import pandas as pd\n\npd.DataFrame({'a': [1]}).to_gbq('p.d.t')\n"
    result = check(source, include_medium=True)
    assert [f.code for f in result.findings] == ["GC007"]
