"""The worker contract: four types, and anything else becomes Failed."""

import pytest
from a2u_core.workers import (
    DEFAULT_USER_SAFE_MESSAGE,
    Failed,
    NeedsInput,
    Option,
    Progress,
    Result,
    to_outcome,
)
from pydantic import ValidationError


def test_result_carries_data_and_one_of_say_or_must_say() -> None:
    assert Result(data={"slot": "10:00"}, say="Booked for 10:00.").say == "Booked for 10:00."
    assert Result(data={"covered": True}, must_say="80%").must_say == "80%"
    assert Result(data={}).say is None
    with pytest.raises(ValidationError, match="not both"):
        Result(data={}, say="Booked.", must_say="Booked.")


def test_result_data_must_be_json() -> None:
    with pytest.raises(ValidationError):
        Result(data={"when": object()})  # pyright: ignore[reportArgumentType]


def test_needs_input_serialises_schema_under_its_contract_name() -> None:
    ask = NeedsInput(
        field="slot",
        hint="Which time suits you?",
        schema={"type": "string"},
        options=[Option(value="s1", label="10:00"), Option(value="s2", label="11:30")],
    )
    dumped = ask.model_dump(by_alias=True)
    assert dumped["schema"] == {"type": "string"}
    assert dumped["prefer_channel"] == "same"
    assert NeedsInput.model_validate(dumped) == ask


def test_needs_input_rejects_unknown_channel() -> None:
    with pytest.raises(ValidationError):
        NeedsInput(field="x", hint="?", schema={}, prefer_channel="sms")  # pyright: ignore[reportArgumentType]


def test_progress_and_failed() -> None:
    assert Progress(note="halfway").note == "halfway"
    failed = Failed(reason="timeout")
    assert failed.user_safe_message == DEFAULT_USER_SAFE_MESSAGE


@pytest.mark.parametrize(
    "outcome",
    [
        Result(data={"a": 1}),
        NeedsInput(field="day", hint="Which day?", schema={"type": "string"}),
        Failed(reason="declined", user_safe_message="No problem, nothing was booked."),
    ],
    ids=["result", "needs_input", "failed"],
)
def test_terminal_outcomes_pass_through(outcome: Result | NeedsInput | Failed) -> None:
    assert to_outcome(outcome) is outcome


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (Progress(note="still going"), "Progress"),  # progress is reported, never returned
        ("Your booking is done!", "str"),
        ({"data": {}}, "dict"),
        (None, "NoneType"),
    ],
    ids=["progress", "text", "dict", "none"],
)
def test_anything_else_becomes_failed(value: object, kind: str) -> None:
    outcome = to_outcome(value)
    assert isinstance(outcome, Failed)
    assert outcome.reason == f"contract_violation: returned {kind}"
    assert outcome.user_safe_message == DEFAULT_USER_SAFE_MESSAGE
