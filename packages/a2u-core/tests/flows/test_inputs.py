"""Answers are checked against the collect type; format failures do not use up a retry."""

from typing import Any

import pytest
from a2u_core.config.models import (
    AddressEntity,
    ChooseType,
    DateTimeEntity,
    EmailEntity,
    EntityType,
    MoneyEntity,
    PatternEntity,
    PhoneEntity,
    PlainType,
    SimpleEntity,
)
from a2u_core.flows.inputs import (
    BasicCapture,
    Invalid,
    Valid,
    check_plain,
    input_schema,
    parse_yes_no,
)


@pytest.mark.parametrize(
    ("t", "answer", "expected"),
    [
        ("string", "  Nimal ", Valid("Nimal")),
        ("string", "", Invalid("expected some text")),
        ("string", 5, Invalid("expected some text")),
        ("number", "1,500.5", Valid(1500.5)),
        ("number", 3, Valid(3)),
        ("number", "3.0", Valid(3)),
        ("number", True, Invalid("expected a number")),
        ("number", "lots", Invalid("expected a number")),
        ("integer", "42", Valid(42)),
        ("integer", 4.5, Invalid("expected a whole number")),
        ("bool", "Yes", Valid(True)),
        ("bool", False, Valid(False)),
        ("bool", "maybe", Invalid("expected yes or no")),
    ],
)
def test_plain_types(t: Any, answer: Any, expected: object) -> None:
    assert check_plain(PlainType(type=t), answer) == expected


def test_yes_no() -> None:
    assert parse_yes_no("y") is True
    assert parse_yes_no(" NO ") is False
    assert parse_yes_no("sure") is None
    assert parse_yes_no(1) is None


CAPTURE = BasicCapture()


@pytest.mark.parametrize(
    ("entity", "answer", "bound"),
    [
        (SimpleEntity(type="nic_lk"), "2002 1770 1234", "200217701234"),
        (SimpleEntity(type="nic_lk"), "851234567v", "851234567V"),
        (SimpleEntity(type="person_name"), " Kamala ", "Kamala"),
        (PhoneEntity(type="phone"), "+94 77 123-4567", "+94771234567"),
        (EmailEntity(type="email"), "a@b.lk", "a@b.lk"),
        (PatternEntity(type="id", pattern=r"\d{8}"), 12345678, "12345678"),
        (PatternEntity(type="account_no", pattern=r"\d{4}", check_digit="luhn"), "4242", "4242"),
        (
            MoneyEntity(type="money", currency="LKR", max=500),
            250,
            {"amount": 250, "currency": "LKR"},
        ),
        (DateTimeEntity(type="date"), "2026-10-16", "2026-10-16"),
        (DateTimeEntity(type="time"), "15:30:00", "15:30"),
        (DateTimeEntity(type="date"), "2026-10-16T15:30", "2026-10-16T15:30:00"),
        (AddressEntity(type="address"), "12 Galle Rd, Colombo 3", "12 Galle Rd, Colombo 3"),
    ],
)
def test_entities_are_normalised(entity: EntityType, answer: Any, bound: Any) -> None:
    assert CAPTURE.check(entity, answer) == Valid(bound)


@pytest.mark.parametrize(
    ("entity", "answer", "uses_retry"),
    [
        (SimpleEntity(type="nic_lk"), "2002177", False),
        (PhoneEntity(type="phone"), "0771234567", False),
        (EmailEntity(type="email"), "nobody", False),
        (PatternEntity(type="id", pattern=r"\d{8}"), "1234", False),
        (PatternEntity(type="account_no", pattern=r"\d{4}", check_digit="luhn"), "4243", False),
        (DateTimeEntity(type="date"), "next friday", False),
        (MoneyEntity(type="money"), "a lot", False),
        (AddressEntity(type="address"), "", False),
        # Well formed but not acceptable: these use up a retry.
        (MoneyEntity(type="money", currency="LKR", max=500), 900, True),
        (MoneyEntity(type="money", min=10), 5, True),
        (MoneyEntity(type="money", currency="LKR"), {"amount": 5, "currency": "USD"}, True),
    ],
)
def test_entity_failures(entity: EntityType, answer: Any, uses_retry: bool) -> None:
    result = CAPTURE.check(entity, answer)
    assert isinstance(result, Invalid)
    assert result.uses_retry is uses_retry


def test_input_schemas() -> None:
    assert input_schema(PlainType(type="bool")) == {"type": "boolean"}
    assert input_schema(PlainType(type="integer")) == {"type": "integer"}
    assert input_schema(PatternEntity(type="id", pattern=r"\d{8}")) == {
        "type": "string",
        "entity": "id",
        "pattern": r"\d{8}",
    }
    assert input_schema(ChooseType(type="choose", source="calendar.free_slots")) == {
        "type": "string"
    }
    assert input_schema(["a", "b"]) == {"type": "string"}
