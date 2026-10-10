"""Templates and filters (spec §5.4): rendered by code, per channel."""

from typing import Any

import pytest
from a2u_core.flows.render import RenderError, apply_filter, render_json, render_text
from pydantic import JsonValue

ENV: dict[str, Any] = {
    "slot": "2026-10-16T15:30:00+05:30",
    "day": "2026-10-16",
    "order": {"id": "o_17", "total": 1500.5, "items": ["kettle"], "paid": True},
    "fee": {"amount": 2500, "currency": "LKR"},
    "nic": "200217701234",
    "count": 3,
    "nothing": None,
}


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        ("phone", "Done. Your appointment is now on Friday the 16th at 3:30 pm."),
        ("web_voice", "Done. Your appointment is now on Friday the 16th at 3:30 pm."),
        ("web_chat", "Done. Your appointment is now on Fri 16 Oct, 3:30 pm."),
        ("whatsapp", "Done. Your appointment is now on Fri 16 Oct, 3:30 pm."),
    ],
)
def test_spec_say_renders_per_channel(channel: Any, expected: str) -> None:
    say = "Done. Your appointment is now on ${slot | date_spoken}."
    assert render_text(say, ENV, channel) == expected


@pytest.mark.parametrize(
    ("value", "voice", "text"),
    [
        ("2026-10-16", "Friday the 16th", "Fri 16 Oct"),
        ("2026-10-01T09:00", "Thursday the 1st at 9 am", "Thu 1 Oct, 9 am"),
        ("2026-10-02T00:05", "Friday the 2nd at 12:05 am", "Fri 2 Oct, 12:05 am"),
        ("2026-10-03T12:00", "Saturday the 3rd at 12 pm", "Sat 3 Oct, 12 pm"),
        ("2026-10-11T18:45", "Sunday the 11th at 6:45 pm", "Sun 11 Oct, 6:45 pm"),
        ("2026-10-22T10:00", "Thursday the 22nd at 10 am", "Thu 22 Oct, 10 am"),
    ],
)
def test_date_spoken(value: str, voice: str, text: str) -> None:
    assert apply_filter("date_spoken", value, "phone") == voice
    assert apply_filter("date_spoken", value, "web_chat") == text


@pytest.mark.parametrize(
    ("value", "expected"),
    [("15:30", "3:30 pm"), ("09:00:00", "9 am"), ("2026-10-16T15:30:00+05:30", "3:30 pm")],
)
def test_time_spoken(value: str, expected: str) -> None:
    assert apply_filter("time_spoken", value, "phone") == expected
    assert apply_filter("time_spoken", value, "whatsapp") == expected


@pytest.mark.parametrize(
    ("value", "voice", "text"),
    [
        ({"amount": 2500, "currency": "LKR"}, "2,500 rupees", "LKR 2,500.00"),
        ({"amount": 12.5, "currency": "USD"}, "12 dollars and 50 cents", "USD 12.50"),
        ({"amount": 1, "currency": "GBP"}, "1 pound", "GBP 1.00"),
        ({"amount": 99.99, "currency": "XYZ"}, "99.99 XYZ", "XYZ 99.99"),
        (1500.5, "1,500.50", "1,500.50"),
        (100000, "100,000", "100,000.00"),
    ],
)
def test_money(value: Any, voice: str, text: str) -> None:
    assert apply_filter("money", value, "phone") == voice
    assert apply_filter("money", value, "web_chat") == text


def test_digits_spoken_reads_each_digit_on_voice_only() -> None:
    assert apply_filter("digits_spoken", "200217701234", "phone") == ("2 0 0 2 1 7 7 0 1 2 3 4")
    assert apply_filter("digits_spoken", "AB-12 3", "web_voice") == "A B 1 2 3"
    assert apply_filter("digits_spoken", "200217701234", "whatsapp") == "200217701234"
    assert apply_filter("digits_spoken", 4021, "phone") == "4 0 2 1"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("date_spoken", "next thursday"),
        ("date_spoken", 20261016),
        ("time_spoken", "half three"),
        ("money", "a lot"),
        ("money", {"amount": "5", "currency": "LKR"}),
        ("money", -5),
        ("money", True),
        ("digits_spoken", 1.5),
        ("digits_spoken", {"id": 1}),
        ("weekday", "2026-10-16"),
    ],
)
def test_filters_refuse_values_they_cannot_render(name: str, value: Any) -> None:
    with pytest.raises(RenderError):
        apply_filter(name, value, "phone")


def test_plain_values_render_without_a_filter() -> None:
    template = "${order.id}: ${count} item, total ${order.total}, paid ${order.paid}"
    assert render_text(template, ENV, "web_chat") == "o_17: 3 item, total 1500.5, paid yes"
    assert render_text("${ order.items.0 }", ENV, "web_chat") == "kettle"
    assert render_text("no templates here", ENV, "phone") == "no templates here"


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("${missing}", "missing is not bound"),
        ("${order.nope}", "order.nope is not bound"),
        ("${nothing}", "nothing is empty"),
        ("${order}", "order is a map"),
        ("${order.items}", "order.items is a list"),
        ("${order.id | weekday}", "unknown filter 'weekday'"),
    ],
)
def test_render_errors(template: str, message: str) -> None:
    with pytest.raises(RenderError, match=message):
        render_text(template, ENV, "phone")


def test_json_keeps_the_type_of_a_whole_value_reference() -> None:
    args: JsonValue = {
        "id": "${order.id}",
        "order": "${order}",
        "count": "${count}",
        "nested": ["${slot}", {"label": "order ${order.id}"}],
        "when": "${slot | date_spoken}",
        "literal": 7,
    }
    assert render_json(args, ENV, "web_chat") == {
        "id": "o_17",
        "order": ENV["order"],
        "count": 3,
        "nested": ["2026-10-16T15:30:00+05:30", {"label": "order o_17"}],
        "when": "Fri 16 Oct, 3:30 pm",
        "literal": 7,
    }


def test_json_copies_bound_values() -> None:
    rendered = render_json({"order": "${order}"}, ENV, "web_chat")
    assert isinstance(rendered, dict)
    rendered["order"]["id"] = "changed"  # type: ignore[index]
    assert ENV["order"]["id"] == "o_17"
