"""Rule-by-rule behaviour: what fires, what stays silent, and why."""

from __future__ import annotations

from textwrap import dedent

from tests.conftest import needs_env

pytestmark = needs_env


def test_gc001_reports_uninstalled_module_with_suggestion(check) -> None:
    result = check("import pandas as pd\n\nframe = pd.DataFrame()\n")
    assert [f.code for f in result.findings] == []
    result = check("import pandas as pandas_alias\nimport polars\n\nprint(polars, pandas_alias)\n")
    codes = {f.code for f in result.findings}
    assert codes == {"GC001"}
    finding = result.findings[0]
    assert finding.symbol == "polars"
    assert "not installed" in finding.message


def test_gc001_skips_optional_imports(check) -> None:
    """A guarded import is a documented absence, not a bug."""
    source = dedent(
        """
        try:
            import polars
        except ImportError:
            polars = None

        if polars is not None:
            polars.read_csv("data.csv")
        """
    )
    assert check(source).findings == []


def test_gc002_reports_missing_name_with_closest_match(check) -> None:
    result = check("from pandas import DataFram\n\nprint(DataFram)\n")
    assert [f.code for f in result.findings] == ["GC002"]
    finding = result.findings[0]
    assert finding.symbol == "pandas.DataFram"
    assert finding.suggestion == "DataFrame"


def test_gc003_attribute_on_instance_and_module(check_medium) -> None:
    source = dedent(
        """
        import pandas as pd

        pd.from_csv("data.csv")
        frame = pd.DataFrame({"a": [1]})
        frame.melt_axis()
        """
    )
    result = check_medium(source)
    assert [f.code for f in result.findings] == ["GC003", "GC003"]
    assert sorted(f.confidence.value for f in result.findings) == ["high", "medium"]


def test_gc003_reports_a_certain_absence(check) -> None:
    """``requests`` is read completely, so absence in it is meaningful."""
    result = check("import requests\n\nrequests.definitely_not_there()\n")
    assert [f.code for f in result.findings] == ["GC003"]


def test_gc003_on_a_class_object_is_medium_confidence(check_medium) -> None:
    """A missing method reached through the class is reported, but only as medium.

    ``pd.DataFrame.read_table`` does not exist in pandas 2.2.3; the index cannot
    follow every base class pandas builds at runtime, so the finding is hedged
    behind ``--include-medium`` instead of being claimed as proven.
    """
    source = "import pandas as pd\n\npd.DataFrame.read_table('data.tsv')\n"
    result = check_medium(source)
    assert [f.code for f in result.findings] == ["GC003"]
    assert result.findings[0].confidence.value == "medium"


def test_unbound_existing_method_is_not_judged(check_medium) -> None:
    """``DataFrame.to_csv('out.csv')`` passes the path as ``self``: not judgeable."""
    source = "import pandas as pd\n\npd.DataFrame.to_csv('out.csv')\n"
    result = check_medium(source)
    assert result.findings == []
    assert any("unbound method" in item.reason for item in result.unresolved)


def test_gc004_and_gc005_and_gc006_arity_rules(check) -> None:
    source = dedent(
        """
        import pandas as pd

        pd.read_csv("data.csv", skip_blank=True)
        pd.concat([], [], [], [], [], [], [], [], [], [], [], [])
        pd.read_csv()
        """
    )
    codes = [f.code for f in check(source).findings]
    assert codes == ["GC004", "GC005", "GC006"]


def test_gc007_deprecation_names_the_installed_object(check_medium) -> None:
    source = "import pandas as pd\n\nframe = pd.DataFrame({'a': [1]})\nframe.applymap(str)\n"
    result = check_medium(source)
    assert [f.code for f in result.findings] == ["GC007"]
    assert "deprecated" in result.findings[0].message


def test_gc000_reports_a_parse_error_without_stopping(check) -> None:
    result = check("def broken(:\n")
    assert result.parse_error is not None
    assert result.parse_error.message
    assert result.findings == []


def test_suppression_comment_removes_findings(check) -> None:
    source = (
        'import pandas as pd\n\npd.read_csv("data.csv", skip_blank=True)  # ghostcall: ignore\n'
    )
    assert check(source).findings == []


def test_scoped_suppression_only_silences_that_rule(check) -> None:
    source = dedent(
        """
        import pandas as pd

        # ghostcall: ignore[GC004]
        pd.read_csv("data.csv", skip_blank=True)
        """
    )
    assert check(source).findings == []

    other = dedent(
        """
        import pandas as pd

        # ghostcall: ignore[GC001]
        pd.read_csv("data.csv", skip_blank=True)
        """
    )
    assert [f.code for f in check(other).findings] == ["GC004"]


def test_select_and_ignore_are_honoured(make_checker) -> None:
    source = "import pandas as pd\n\npd.read_csv('data.csv', skip_blank=True)\npd.from_csv('x')\n"
    only_gc004 = make_checker(select=("GC004",)).check_source(source)
    assert {f.code for f in only_gc004.findings} == {"GC004"}
    without = make_checker(ignore=("GC004",)).check_source(source)
    assert {f.code for f in without.findings} == {"GC003"}


def test_per_file_ignores_are_honoured(make_checker) -> None:
    checker = make_checker(per_file_ignores={"legacy/*": ("GC004",)})
    source = "import pandas as pd\n\npd.read_csv('data.csv', skip_blank=True)\n"
    assert checker.check_source(source, filename="legacy/old.py").findings == []
    assert len(checker.check_source(source, filename="new/module.py").findings) == 1


def test_ignore_modules_skips_a_library_entirely(make_checker) -> None:
    checker = make_checker(ignore_modules=("pandas",))
    source = "import pandas as pd\n\npd.from_csv('data.csv')\n"
    assert checker.check_source(source).findings == []


def test_medium_findings_are_hidden_by_default(make_checker) -> None:
    source = "import pandas as pd\n\nframe = pd.DataFrame({'a': [1]})\nframe.append({'a': 2})\n"
    assert make_checker().check_source(source).findings == []
    medium = make_checker(include_medium=True).check_source(source)
    assert [f.code for f in medium.findings] == ["GC003"]
    assert medium.findings[0].confidence.value == "medium"


def test_every_finding_carries_installed_version_and_actionable_hint(check) -> None:
    source = "import pandas as pd\n\npd.read_csv('data.csv', skip_blank=True)\n"
    finding = check(source).findings[0]
    assert finding.installed == "pandas 2.2.3"
    assert finding.symbol == "pandas.read_csv"
    assert finding.suggestion == "skip_blank_lines"
    assert finding.signature and "filepath_or_buffer" in finding.signature
    assert finding.line == 3


def test_unresolved_reasons_are_recorded_for_verbose_output(check) -> None:
    """Every call ghostcall refuses to judge is explainable, never just dropped."""
    source = "some_unknown_variable.call_me(1)\n"
    result = check(source)
    assert result.findings == []
    assert result.unresolved, "unresolved calls must be explainable, not silently dropped"
    assert result.unresolved[0].symbol == "some_unknown_variable"
    assert "name is not imported from a library" in result.unresolved[0].reason


def test_star_args_are_never_judged(check) -> None:
    source = dedent(
        """
        import pandas as pd

        def load(*args, **kwargs):
            return pd.read_csv(*args, **kwargs)
        """
    )
    assert check(source).findings == []


def test_aliased_imports_resolve_to_the_same_object(check) -> None:
    source = dedent(
        """
        from pandas import read_csv as loader

        loader("data.csv", skip_blank=True)
        """
    )
    assert [f.code for f in check(source).findings] == ["GC004"]


def test_submodule_imports_are_checked(check) -> None:
    source = "import requests.adapters\n\nrequests.adapters.HTTPAdapter(pool_maxsizes=1)\n"
    assert [f.code for f in check(source).findings] == ["GC004"]
