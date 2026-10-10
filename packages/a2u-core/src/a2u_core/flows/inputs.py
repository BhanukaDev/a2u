"""Checking answers to `collect`, `choose` and `confirm` against their types.

Plain types are checked here. Entity types go through a `Capture` (D-026):
`BasicCapture` checks format and normalises; spoken-form parsers, per-language
number words and read-back arrive with WP-1.20, which replaces it.

A format failure (`Invalid(uses_retry=False)`) sends the agent back to the user
without using up a retry; anything else invalid uses one.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Protocol, cast

from pydantic import JsonValue

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
    ValueType,
)


@dataclass(frozen=True)
class Valid:
    value: JsonValue  # normalised; this is what gets bound


@dataclass(frozen=True)
class Invalid:
    reason: str
    uses_retry: bool = True


Check = Valid | Invalid


class Capture(Protocol):
    """Parses, validates and normalises an entity value (WP-1.20 provides the full one)."""

    def check(self, entity: EntityType, value: JsonValue) -> Check: ...


_YES = frozenset({"yes", "y", "true"})
_NO = frozenset({"no", "n", "false"})


def parse_yes_no(value: JsonValue) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _YES:
            return True
        if word in _NO:
            return False
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def check_plain(t: PlainType, value: JsonValue) -> Check:
    match t.type:
        case "string":
            if isinstance(value, str) and value.strip():
                return Valid(value.strip())
            return Invalid("expected some text")
        case "bool":
            answer = parse_yes_no(value)
            return Invalid("expected yes or no") if answer is None else Valid(answer)
        case "number" | "integer":
            number: Any = value
            if isinstance(value, str):
                try:
                    number = float(value.replace(",", "").strip())
                except ValueError:
                    return Invalid(f"expected a {t.type}")
            if not _is_number(number):
                return Invalid(f"expected a {t.type}")
            if t.type == "integer":
                if not float(number).is_integer():
                    return Invalid("expected a whole number")
                return Valid(int(number))
            return Valid(int(number) if float(number).is_integer() else number)


def input_schema(t: ValueType | list[str]) -> dict[str, Any]:
    """The JSON schema a `NeedsInput` carries for this type."""
    if isinstance(t, list | ChooseType):
        return {"type": "string"}  # the options carry the allowed values
    if isinstance(t, PlainType):
        return {"type": "boolean" if t.type == "bool" else t.type}
    schema: dict[str, Any] = {"type": "string", "entity": t.type}
    if isinstance(t, PatternEntity):
        schema["pattern"] = t.pattern
    return schema


# --- Basic entity checks (format only) ---------------------------------------

_SPACING = re.compile(r"[\s\-()]")
_NIC = re.compile(r"[0-9]{12}|[0-9]{9}[VX]")
_E164 = re.compile(r"\+[1-9][0-9]{6,14}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _type_name(entity: EntityType) -> str:
    return cast(Any, entity).type  # every concrete entity type declares `type`


def _format(reason: str) -> Invalid:
    return Invalid(reason, uses_retry=False)


class BasicCapture:
    """Format checks and normalisation for entity types, without spoken-form parsing."""

    def check(self, entity: EntityType, value: JsonValue) -> Check:
        match entity:
            case MoneyEntity():
                return self._money(entity, value)
            case AddressEntity():
                if (isinstance(value, dict) and value) or (
                    isinstance(value, str) and value.strip()
                ):
                    return Valid(value.strip() if isinstance(value, str) else value)
                return _format("expected an address")
            case _:
                pass
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str) or not value.strip():
            return _format(f"expected a {_type_name(entity)}")
        text = value.strip()
        match entity:
            case SimpleEntity(type="nic_lk"):
                nic = _SPACING.sub("", text).upper()
                return Valid(nic) if _NIC.fullmatch(nic) else _format("expected an NIC number")
            case SimpleEntity():
                return Valid(text)
            case PhoneEntity():
                phone = _SPACING.sub("", text)
                ok = _E164.fullmatch(phone)
                return Valid(phone) if ok else _format("expected a phone number with country code")
            case EmailEntity():
                return Valid(text) if _EMAIL.fullmatch(text) else _format("expected an email")
            case PatternEntity():
                if not re.fullmatch(entity.pattern, text):
                    return _format(f"expected {entity.type} matching {entity.pattern}")
                if entity.check_digit == "luhn" and not (text.isdigit() and _luhn(text)):
                    return _format("the check digit does not match")
                return Valid(text)
            case DateTimeEntity():
                return self._when(entity, text)
            case _:
                return _format(f"unsupported entity type {_type_name(entity)}")

    def _money(self, entity: MoneyEntity, value: JsonValue) -> Check:
        amount: Any = value
        currency = entity.currency
        if isinstance(value, dict):
            fields = cast(dict[str, Any], value)
            amount, currency = fields.get("amount"), fields.get("currency", currency)
        if not _is_number(amount):
            return _format("expected an amount")
        if entity.currency and currency != entity.currency:
            return Invalid(f"expected an amount in {entity.currency}")
        if entity.min is not None and amount < entity.min:
            return Invalid(f"the amount must be at least {entity.min:g}")
        if entity.max is not None and amount > entity.max:
            return Invalid(f"the amount must be at most {entity.max:g}")
        return Valid({"amount": amount, "currency": currency})

    def _when(self, entity: DateTimeEntity, text: str) -> Check:
        try:
            if entity.type == "time":
                return Valid(time.fromisoformat(text).isoformat(timespec="minutes"))
            if len(text) == 10:
                return Valid(date.fromisoformat(text).isoformat())
            return Valid(datetime.fromisoformat(text).isoformat())
        except ValueError:
            return _format(f"expected an ISO {entity.type}")
