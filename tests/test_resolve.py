"""Resolver behaviour: alias chains, re-exports, instance tracking, caching."""

from __future__ import annotations

from textwrap import dedent

from ghostcall.checks import Config
from ghostcall.env import Environment
from ghostcall.index import ApiIndex, ChainStatus
from tests.conftest import needs_env

pytestmark = needs_env


def chain(env: Environment, path: str) -> object:
    return ApiIndex(env).walk_chain(path)


def test_import_alias_resolves_to_the_same_object(env: Environment) -> None:
    resolved = chain(env, "pandas.DataFrame")
    assert resolved.status is ChainStatus.OK
    assert resolved.node is not None
    assert resolved.node.path == "pandas.core.frame.DataFrame"


def test_reexport_chain_is_followed(env: Environment) -> None:
    """``pandas.read_csv`` lives in ``pandas.io.parsers.readers``."""
    resolved = chain(env, "pandas.read_csv")
    assert resolved.status is ChainStatus.OK
    assert resolved.entry is not None
    assert resolved.entry.name.startswith("pandas.io")


def test_deep_module_path_resolves(env: Environment) -> None:
    resolved = chain(env, "requests.sessions.Session.send")
    assert resolved.status is ChainStatus.OK
    assert resolved.node is not None and resolved.node.kind.value == "function"


def test_missing_member_is_certain_only_when_the_module_is_complete(env: Environment) -> None:
    certain = chain(env, "requests.async_get")
    assert certain.status is ChainStatus.MISSING and certain.certain is True

    uncertain = chain(env, "pandas.DataFrame.read_table")
    assert uncertain.status is ChainStatus.MISSING and uncertain.certain is False


def test_unknown_module_is_reported_as_unknown_not_missing(env: Environment) -> None:
    resolved = chain(env, "no_such_module_at_all.thing")
    assert resolved.status is ChainStatus.UNKNOWN


def test_star_import_does_not_create_findings(check_medium) -> None:
    """``from pandas import *`` leaves names unresolvable: skip, never guess."""
    source = dedent(
        """
        from pandas import *

        frame = DataFrame({"a": [1]})
        print(frame, read_csv)
        """
    )
    assert check_medium(source).findings == []


def test_relative_imports_are_project_code(check_medium) -> None:
    source = dedent(
        """
        from . import sibling
        from ..package import module

        sibling.run()
        module.helper()
        """
    )
    assert check_medium(source).findings == []


def test_wildcard_local_module_is_not_flagged(env: Environment, tmp_path, make_checker) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("from .core import *\n", encoding="utf-8")
    (package / "core.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    script = tmp_path / "use.py"
    script.write_text("from pkg import run\n\nrun()\n", encoding="utf-8")
    result = make_checker().check_file(script)
    assert result.findings == []


def test_instance_from_constructor_is_tracked(check_medium) -> None:
    source = dedent(
        """
        import pandas as pd

        frame = pd.DataFrame({"a": [1]})
        frame.append({"a": 2})
        """
    )
    findings = check_medium(source).findings
    assert [f.code for f in findings] == ["GC003"]
    assert findings[0].confidence.value == "medium"
    assert findings[0].symbol == "pandas.core.frame.DataFrame.append"


def test_instance_tracking_can_be_switched_off(make_checker) -> None:
    source = "import pandas as pd\n\nframe = pd.DataFrame({'a': [1]})\nframe.append({'a': 2})\n"
    checker = make_checker(include_medium=True, instance_tracking=False)
    assert checker.check_source(source).findings == []


def test_reassigned_names_are_not_tracked(check_medium) -> None:
    """Two assignments to the same name means the type is unknowable."""
    source = dedent(
        """
        import pandas as pd

        frame = pd.DataFrame({"a": [1]})
        frame = something_else()
        frame.append({"a": 2})
        """
    )
    assert check_medium(source).findings == []


def test_parameter_names_are_not_tracked(check_medium) -> None:
    """A function argument shadows any tracked instance of the same name."""
    source = dedent(
        """
        import pandas as pd

        frame = pd.DataFrame({"a": [1]})

        def use(frame):
            frame.append({"a": 2})

        frame.reset_index()
        """
    )
    assert check_medium(source).findings == []


def test_classmethod_call_results_are_tracked(check_medium) -> None:
    source = dedent(
        """
        import pandas as pd

        frame = pd.DataFrame.from_records([{"a": 1}])
        frame.reindex_axis(["a"], axis=0)
        """
    )
    findings = check_medium(source).findings
    assert [f.code for f in findings] == ["GC003"]
    assert findings[0].confidence.value == "medium"


def test_chained_calls_on_call_results_are_tracked(check_medium) -> None:
    """``pd.DataFrame(...).append(...)`` must be judged like the two-line form."""
    source = "import pandas as pd\n\npd.DataFrame({'a': [1]}).append({'a': 2})\n"
    findings = check_medium(source).findings
    assert [f.code for f in findings] == ["GC003"]


def test_attribute_chain_through_a_module_alias(check_medium) -> None:
    source = dedent(
        """
        import pandas.core.frame as pcf

        pcf.DataFrame.from_dict({"a": [1]}).append({"a": 2})
        """
    )
    assert [f.code for f in check_medium(source).findings] == ["GC003"]


def test_signature_is_attached_for_bound_methods(check_medium) -> None:
    """``self`` is stripped: the user never passes it."""
    source = dedent(
        """
        import pandas as pd

        frame = pd.DataFrame({"a": [1]})
        frame.set_index("a", verify_integrityy=True)
        """
    )
    findings = check_medium(source).findings
    assert [f.code for f in findings] == ["GC004"]
    assert findings[0].signature and not findings[0].signature.startswith("self")
    assert findings[0].suggestion == "verify_integrity"


def test_unbound_access_on_a_class_is_skipped(check_medium) -> None:
    """``DataFrame.to_csv(path)`` would need an explicit ``self``: not judged."""
    source = "import pandas as pd\n\npd.DataFrame.to_csv('out.csv')\n"
    result = check_medium(source)
    assert result.findings == []
    assert any("unbound method" in item.reason for item in result.unresolved)


def test_property_access_is_not_treated_as_a_call(check_medium) -> None:
    source = "import pandas as pd\n\nframe = pd.DataFrame({'a': [1]})\nprint(frame.columns)\n"
    assert check_medium(source).findings == []


def test_calling_an_attribute_is_skipped(check_medium) -> None:
    source = "import pandas as pd\n\npd.__version__()\n"
    assert check_medium(source).findings == []


def test_index_is_cached_per_module(env: Environment) -> None:
    index = ApiIndex(env)
    first = index.get_module("requests")
    second = index.get_module("requests")
    assert first is second, "module indexes must be parsed once per run"


def test_local_modules_are_marked_local(env: Environment) -> None:
    index = ApiIndex(env)
    assert index.origin_of("requests") == "installed"
    assert index.origin_of("json") == "stdlib"
    assert index.origin_of("no_such_thing") == "unknown"


def test_ignored_modules_are_recognised(env: Environment) -> None:
    """Ignored modules are filtered when findings are produced, not when parsed."""
    index = ApiIndex(env, ignore_modules=("pandas",))
    assert index.is_ignored("pandas")
    assert not index.is_ignored("requests")


def test_runtime_introspection_requires_allow_import(env: Environment) -> None:
    """``--allow-import`` is the only path that can execute target code."""
    index = ApiIndex(env, allow_import=False)
    assert index.walk_chain("json.dumps").status is ChainStatus.OK

    with_import = ApiIndex(env, allow_import=True)
    assert with_import.allow_import is True


def test_checker_shares_one_index_across_files(make_checker) -> None:
    checker = make_checker()
    first = checker.check_source("import requests\n\nrequests.get('x', timeout=1)\n")
    second = checker.check_source("import httpx\n\nhttpx.get('x', retry=1)\n")
    assert first.findings == []
    assert [f.code for f in second.findings] == ["GC004"]


def test_config_defaults_are_conservative() -> None:
    config = Config()
    assert config.include_medium is False
    assert config.allow_import is False
    assert config.instance_tracking is True
    assert config.use_cache is True
