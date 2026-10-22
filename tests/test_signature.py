"""Unit and property tests for the call/signature matcher.

``match_call`` is the piece that decides between "this is definitely wrong" and
"this cannot be proven", so it gets exhaustive-and-random treatment: hypothesis
checks that a call is never reported when the signature could accept it, however
odd the argument mix is.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ghostcall.model import Parameter, ParameterKind, Signature
from ghostcall.signature import CallShape, best_suggestion, match_call

POS_ONLY = ParameterKind.POSITIONAL_ONLY
POS_KW = ParameterKind.POSITIONAL_OR_KEYWORD
VAR_POS = ParameterKind.VAR_POSITIONAL
KW_ONLY = ParameterKind.KEYWORD_ONLY
VAR_KW = ParameterKind.VAR_KEYWORD


def param(
    name: str,
    kind: ParameterKind = POS_KW,
    *,
    default: bool = False,
    annotation: str | None = None,
) -> Parameter:
    return Parameter(name=name, kind=kind, has_default=default, annotation=annotation)


def sig(*parameters: Parameter) -> Signature:
    return Signature(parameters=parameters, origin="test.origin")


def shape(
    positional: int = 0,
    keywords: Sequence[str] = (),
    *,
    star_args: bool = False,
    star_kwargs: bool = False,
) -> CallShape:
    return CallShape(
        positional=positional,
        keywords=tuple(keywords),
        has_var_positional=star_args,
        has_var_keyword=star_kwargs,
    )


def codes(mismatches: Sequence[object]) -> list[str]:
    return [item.code for item in mismatches]


def test_unknown_keyword_is_reported_with_suggestion() -> None:
    signature = sig(param("filepath"), param("skip_blank_lines", default=True))
    mismatches = match_call([signature], shape(1, ["skip_blank"]))
    assert codes(mismatches) == ["GC004"]
    assert mismatches[0].suggestion == "skip_blank_lines"


def test_accepted_keyword_is_silent() -> None:
    signature = sig(param("filepath"), param("skip_blank_lines", default=True))
    assert match_call([signature], shape(1, ["skip_blank_lines"])) == []


def test_keyword_targeting_positional_only_is_reported() -> None:
    signature = sig(param("value", POS_ONLY), param("flag", KW_ONLY, default=True))
    assert codes(match_call([signature], shape(1, ["value"]))) == ["GC004"]


def test_var_keyword_absorbs_anything() -> None:
    signature = sig(param("name", POS_KW), param("kwargs", VAR_KW))
    assert match_call([signature], shape(1, ["totally_invented"])) == []


def test_star_star_call_is_never_judged() -> None:
    signature = sig(param("url"))
    assert match_call([signature], shape(0, [], star_kwargs=True)) == []


def test_too_many_positional_arguments() -> None:
    signature = sig(param("a"), param("b", default=True))
    mismatches = match_call([signature], shape(3))
    assert codes(mismatches) == ["GC005"]
    assert mismatches[0].detail == {"expected": "2", "given": "3"}


def test_var_positional_absorbs_extra_arguments() -> None:
    signature = sig(param("first"), param("rest", VAR_POS))
    assert match_call([signature], shape(9)) == []


def test_star_args_call_is_never_judged() -> None:
    signature = sig(param("a"))
    assert match_call([signature], shape(4, star_args=True)) == []


def test_missing_required_positional_and_keyword() -> None:
    signature = sig(param("url"), param("timeout", default=True), param("style", KW_ONLY))
    mismatches = match_call([signature], shape(0))
    assert codes(mismatches) == ["GC006"]
    assert set(mismatches[0].detail["missing"].split(", ")) == {"url", "style"}


def test_star_kwargs_withholds_missing_argument_findings() -> None:
    """``f(**options)`` may supply every required keyword: never guess."""
    signature = sig(param("url"), param("style", KW_ONLY))
    assert match_call([signature], shape(0, [], star_kwargs=True)) == []


def test_star_args_withholds_missing_argument_findings() -> None:
    signature = sig(param("url"))
    assert match_call([signature], shape(0, [], star_args=True)) == []


def test_defaults_satisfy_required_arguments() -> None:
    signature = sig(param("a"), param("b", default=True), param("c", KW_ONLY, default=True))
    assert match_call([signature], shape(1)) == []


def test_any_matching_overload_silences_the_call() -> None:
    first = sig(param("filepath"), param("sep", default=True))
    second = sig(param("filepath"), param("names", KW_ONLY))
    assert match_call([first, second], shape(2)) == []


def test_overloads_report_the_best_candidate() -> None:
    first = sig(param("a"), param("b"))
    second = sig(param("a"), param("b"), param("c"))
    mismatches = match_call([first, second], shape(2, ["d"]))
    assert codes(mismatches) == ["GC004"]
    assert len(mismatches) == 1, "only the closest overload should be reported"


def test_no_signature_means_no_opinion() -> None:
    assert match_call([], shape(3, ["whatever"])) == []


def test_suggestion_prefers_public_names() -> None:
    assert best_suggestion("skip_blank", ["skip_blank_lines", "_skip_blank"]) == "skip_blank_lines"


def test_suggestion_is_withheld_when_nothing_is_close() -> None:
    assert best_suggestion("zzz", ["filepath_or_buffer", "sep", "delimiter"]) is None


def test_suggestion_for_a_close_misspelling() -> None:
    assert best_suggestion("json_normalise", ["json_normalize"]) == "json_normalize"


def test_suggestion_with_no_candidates() -> None:
    assert best_suggestion("anything", []) is None


_names = st.text(alphabet="abcdefghijklmnop_", min_size=1, max_size=8)


@st.composite
def signatures(draw: st.DrawFn) -> Signature:
    """Random *legal* signatures: required parameters precede optional ones."""
    names = draw(st.lists(_names, min_size=0, max_size=5, unique=True))
    positional_count = draw(st.integers(min_value=0, max_value=len(names)))
    positional = names[:positional_count]
    keyword_only = names[positional_count:]
    required_positional = draw(st.integers(min_value=0, max_value=len(positional)))
    required_keyword_only = draw(st.integers(min_value=0, max_value=len(keyword_only)))
    parameters = [
        param(name, POS_KW, default=index >= required_positional)
        for index, name in enumerate(positional)
    ]
    if draw(st.booleans()):
        parameters.append(param("args", VAR_POS))
    parameters.extend(
        param(name, KW_ONLY, default=index >= required_keyword_only)
        for index, name in enumerate(keyword_only)
    )
    if draw(st.booleans()):
        parameters.append(param("kwargs", VAR_KW))
    return sig(*parameters)


def required_positional_count(signature: Signature) -> int:
    return sum(1 for parameter in signature.positional if parameter.is_required)


@given(signature=signatures(), extra=st.lists(_names, max_size=3, unique=True))
@settings(max_examples=300, deadline=None)
def test_satisfied_signatures_are_never_reported(signature: Signature, extra: list[str]) -> None:
    """A call that fills every required parameter and adds valid keywords is fine."""
    optional = sorted(signature.accepted_keywords - signature.required_keywords)
    keywords = sorted(
        set(signature.required_keywords) | {name for name in extra if name in optional}
    )
    call = shape(required_positional_count(signature), keywords)
    assert codes(match_call([signature], call)) == [], (
        f"signature {signature.parameters} rejected the valid call {call}"
    )


@given(signature=signatures())
@settings(max_examples=300, deadline=None)
def test_exactly_the_required_arguments_are_accepted(signature: Signature) -> None:
    call = shape(required_positional_count(signature), sorted(signature.required_keywords))
    assert codes(match_call([signature], call)) == []


@given(signature=signatures())
@settings(max_examples=300, deadline=None)
def test_unpacked_calls_are_never_reported(signature: Signature) -> None:
    """``f(*args, **kwargs)`` hides everything: the matcher must stay silent."""
    assert match_call([signature], shape(2, ["invented"], star_args=True, star_kwargs=True)) == []


@given(signature=signatures(), extra=st.integers(min_value=1, max_value=20))
@settings(max_examples=200, deadline=None)
def test_positional_overflow_is_reported_only_without_var_positional(
    signature: Signature, extra: int
) -> None:
    found = codes(match_call([signature], shape(len(signature.positional) + extra)))
    if signature.has_var_positional:
        assert "GC005" not in found
    else:
        assert "GC005" in found


@given(signature=signatures())
@settings(max_examples=200, deadline=None)
def test_no_mismatch_without_a_provable_reason(signature: Signature) -> None:
    """Every mismatch must carry a rule code, a message and structured detail."""
    for mismatch in match_call([signature], shape(999, ["definitely_invented_keyword_zzz"])):
        assert mismatch.code in {"GC004", "GC005", "GC006"}
        assert mismatch.message
        assert mismatch.detail


def test_compact_render_keeps_parameters_and_drops_annotations() -> None:
    """Displayed signatures must show the parameters, never ``...`` soup."""
    signature = sig(
        param("first", POS_KW, annotation="str"),
        param("second", POS_KW, default=True, annotation="int | None"),
        param("flag", KW_ONLY, default=True),
        param("extra", VAR_POS),
        param("options", VAR_KW),
    )
    compact = signature.render(200)
    assert compact == "first, second=..., *, flag=..., *extra, **options"
    assert "str" not in compact
    assert compact.count("...") == 2, "only defaults are elided in the compact form"


def test_full_render_keeps_annotations() -> None:
    signature = sig(param("first", POS_KW, annotation="str"))
    assert signature.render(200, full=True) == "first: str"


def test_render_truncates_at_the_requested_width() -> None:
    signature = sig(*(param(f"argument_number_{index}", default=True) for index in range(20)))
    assert signature.render(40) == "argument_number_0=..., ..."
    assert len(signature.render(40)) <= 40
    assert "argument_number_19" in signature.render(None, full=True)


def test_render_never_ends_with_a_dangling_separator() -> None:
    for width in (10, 20, 30, 40, 60):
        rendered = sig(param("a"), param("b", default=True)).render(width)
        assert not rendered.endswith((",", " ")), rendered


def test_signature_accepted_keywords_excludes_positional_only() -> None:
    signature = sig(param("value", POS_ONLY), param("other", POS_KW))
    assert signature.accepted_keywords == frozenset({"other"})
    # ``value`` is required but positional-only, so it can never be a keyword.
    assert signature.required_keywords == frozenset({"other"})
    assert [parameter.name for parameter in signature.positional] == ["value", "other"]


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ("skip_blank", "skip_blank_lines"),
        ("error_bad_line", "error_bad_lines"),
        ("delim_whitespac", "delim_whitespace"),
    ],
)
def test_suggestions_for_real_typos(missing: str, expected: str) -> None:
    pool = ["filepath_or_buffer", "skip_blank_lines", "error_bad_lines", "delim_whitespace"]
    assert best_suggestion(missing, pool) == expected
