"""Conditions (spec §5.3): a CEL subset over bound values, parsed and evaluated by code."""

import re
from typing import Any

import pytest
from a2u_core.conditions import (
    ConditionError,
    ConditionSyntaxError,
    parse_condition,
)

ENV: dict[str, Any] = {
    "order": {"id": "o_1", "status": "damaged", "total": 80, "tags": ["gift"], "note": None},
    "customer": {"tier": "gold", "has_upcoming_appointment": True, "phone": "+94771234567"},
    "amount": 150.5,
    "count": 3,
    "items": [{"sku": "a"}, {"sku": "b"}],
}


def ev(text: str, env: dict[str, Any] | None = None) -> bool:
    return parse_condition(text).evaluate(ENV if env is None else env)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("order.status == 'damaged' && order.total <= 100", True),
        ('order.status == "damaged" && order.total > 100', False),
        ("customer.has_upcoming_appointment", True),
        ("!customer.has_upcoming_appointment", False),
        ("amount > 100", True),
        ("amount >= 150.5 && amount <= 150.5", True),
        ("count == 3.0", True),  # ints and floats compare as numbers
        ("count != 3", False),
        ("-count < 0", True),
        ("customer.tier in ['gold', 'platinum']", True),
        ("'silver' in ['gold', 'platinum']", False),
        ("'gift' in order.tags", True),
        ("'status' in order", True),  # map keys
        ("has(order.status)", True),
        ("has(order.refund_id)", False),
        ("has(order.note)", True),  # present, even though null
        ("has(missing)", False),
        ("has(missing.field)", False),
        ("customer.phone.startsWith('+94')", True),
        ("customer.phone.startsWith('+1')", False),
        ("order.note == null", True),
        ("items.1.sku == 'b'", True),
        ("true || order.missing == 1", True),  # short-circuit: the right side is not evaluated
        ("false && order.missing == 1", False),
        ("(count > 1 || amount < 0) && !(order.total > 100)", True),
        ("count == '3'", False),  # different types are never equal
        ("true == 1", False),  # bools are not numbers
        ("[1, 2] == [1, 2]", True),
    ],
)
def test_evaluates(text: str, expected: bool) -> None:
    assert ev(text) is expected


def test_and_binds_tighter_than_or() -> None:
    assert ev("true || false && false") is True
    assert ev("(true || false) && false") is False


def test_references_are_the_roots_a_condition_reads() -> None:
    cond = parse_condition("order.total > 1 && has(customer.tier) || 'x' in tags")
    assert cond.references == frozenset({"order", "customer", "tags"})
    assert parse_condition("true").references == frozenset()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("order.missing == 1", "order.missing is not bound"),
        ("nobody", "nobody is not bound"),
        ("order.status < 1", "cannot compare string with number"),
        ("order.status && true", "&& needs bool operands"),
        ("!order.total", "! needs a bool"),
        ("-order.status", "- needs a number"),
        ("order.status", "must be true or false"),  # a condition's value must be a bool
        ("1 in order.status", "in needs a list or a map"),
        ("order.total.startsWith('8')", "startsWith needs strings"),
        ("order.id.0 == 'o'", "order.id.0 is not bound"),
    ],
)
def test_evaluation_errors(text: str, message: str) -> None:
    with pytest.raises(ConditionError, match=message):
        ev(text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "empty condition"),
        ("order.status = 'x'", "unexpected '='"),
        ("order.status == 'x", "unterminated string"),
        ("a &&", "expected a value"),
        ("(a", "expected ')'"),
        ("len(a)", "unknown function 'len'"),
        ("a.endsWith('x')", "unknown method 'endsWith'"),
        ("has(1)", "has() takes a reference"),
        ("has(a, b)", "has() takes a reference"),
        ("a.startsWith()", "startsWith takes one argument"),
        ("a b", "unexpected 'b'"),
        ("a == b == c", "unexpected '=='"),  # comparisons do not chain
        ("a # b", "unexpected '#'"),
        ("x" * 1001, "longer than 1000"),
        ("(" * 60 + "a" + ")" * 60, "nested too deeply"),
    ],
)
def test_syntax_errors(text: str, message: str) -> None:
    with pytest.raises(ConditionSyntaxError, match=re.escape(message)):
        parse_condition(text)


def test_syntax_error_reports_the_column() -> None:
    with pytest.raises(ConditionSyntaxError) as err:
        parse_condition("order.total >> 1")
    assert err.value.column == 14
    assert "column 14" in str(err.value)


def test_condition_errors_are_not_python_errors() -> None:
    # Builder mistakes surface as ConditionError, never as a TypeError from Python.
    with pytest.raises(ConditionError):
        ev("order < order")
    with pytest.raises(ConditionError):
        ev("order.tags > 1")
