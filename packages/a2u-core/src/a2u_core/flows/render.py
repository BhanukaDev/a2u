"""Templates and filters (spec §5.4): `${ref}` and `${ref | filter}`, rendered by code.

Rendering depends on the channel: voice channels get forms written to be
spoken ("Friday the 16th at 3:30 pm", "2 0 0 2"), text channels get compact
written forms ("Fri 16 Oct, 3:30 pm"). Filters render English until
per-language rendering arrives with entity capture (WP-1.20, D-033).

A value that cannot be rendered is a `RenderError`, never a guess: a flow that
would say something wrong fails instead.
"""

import copy
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, time
from typing import Any, Literal, cast

from pydantic import JsonValue

from a2u_core.conditions import MISSING, Env, select_path

Channel = Literal["web_chat", "web_voice", "phone", "whatsapp"]
VOICE: frozenset[Channel] = frozenset({"web_voice", "phone"})

TEMPLATE = re.compile(r"\$\{([^}]*)\}")


class RenderError(ValueError):
    pass


def is_voice(channel: Channel) -> bool:
    return channel in VOICE


# --- Filters ------------------------------------------------------------------


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _clock(t: time) -> str:
    hour = t.hour % 12 or 12
    minutes = f":{t.minute:02d}" if t.minute else ""
    return f"{hour}{minutes} {'am' if t.hour < 12 else 'pm'}"


def _parse_when(value: Any) -> date | datetime:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
        except ValueError:
            pass
    raise RenderError(f"{value!r} is not an ISO date or date-time")


def date_spoken(value: Any, channel: Channel) -> str:
    when = _parse_when(value)
    if is_voice(channel):
        out = f"{when:%A} the {_ordinal(when.day)}"
        return f"{out} at {_clock(when.time())}" if isinstance(when, datetime) else out
    out = f"{when:%a} {when.day} {when:%b}"
    return f"{out}, {_clock(when.time())}" if isinstance(when, datetime) else out


def time_spoken(value: Any, channel: Channel) -> str:
    if isinstance(value, str):
        try:
            t = time.fromisoformat(value) if "T" not in value else datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            return _clock(t if isinstance(t, time) else t.time())
    raise RenderError(f"{value!r} is not an ISO time or date-time")


# Spoken names per currency: (one, many, one minor, many minor).
_CURRENCY_WORDS = {
    "LKR": ("rupee", "rupees", "cent", "cents"),
    "INR": ("rupee", "rupees", "paisa", "paise"),
    "USD": ("dollar", "dollars", "cent", "cents"),
    "EUR": ("euro", "euros", "cent", "cents"),
    "GBP": ("pound", "pounds", "penny", "pence"),
}


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def money(value: Any, channel: Channel) -> str:
    amount: Any = value
    currency: Any = None
    if isinstance(value, Mapping):
        fields = cast(Mapping[str, Any], value)
        amount, currency = fields.get("amount"), fields.get("currency")
    if not _is_number(amount) or (currency is not None and not isinstance(currency, str)):
        raise RenderError(f"{value!r} is not an amount or {{amount, currency}}")
    if amount < 0:
        raise RenderError(f"{value!r} is negative")
    whole, minor = divmod(round(amount * 100), 100)
    if not is_voice(channel):
        figure = f"{whole:,}.{minor:02d}"
        return f"{currency} {figure}" if currency else figure
    figure = f"{whole:,}.{minor:02d}" if minor else f"{whole:,}"
    words = _CURRENCY_WORDS.get(currency or "")
    if currency is None:
        return figure
    if words is None:
        return f"{figure} {currency}"
    one, many, one_minor, many_minor = words
    out = f"{whole:,} {one if whole == 1 else many}"
    return f"{out} and {minor} {one_minor if minor == 1 else many_minor}" if minor else out


def digits_spoken(value: Any, channel: Channel) -> str:
    if not isinstance(value, str | int) or isinstance(value, bool):
        raise RenderError(f"{value!r} is not an ID or a whole number")
    text = str(value)
    if not is_voice(channel):
        return text
    return " ".join(c for c in text if c.isalnum())


FILTERS: dict[str, Callable[[Any, Channel], str]] = {
    "date_spoken": date_spoken,
    "time_spoken": time_spoken,
    "money": money,
    "digits_spoken": digits_spoken,
}


def apply_filter(name: str, value: Any, channel: Channel) -> str:
    fn = FILTERS.get(name)
    if fn is None:
        raise RenderError(f"unknown filter {name!r}")
    return fn(value, channel)


# --- Templates ----------------------------------------------------------------


def _lookup(ref: str, env: Env) -> Any:
    value = select_path(env, ref.split("."))
    if value is MISSING:
        raise RenderError(f"{ref} is not bound")
    return value


def _plain(ref: str, value: Any) -> str:
    if value is None:
        raise RenderError(f"{ref} is empty")
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, str | int | float):
        return str(value)
    kind = "list" if isinstance(value, list) else "map"
    raise RenderError(f"{ref} is a {kind}; name a field, such as ${{{ref}.id}}")


def _render_one(expr: str, env: Env, channel: Channel) -> str:
    ref, _, name = (part.strip() for part in expr.partition("|"))
    value = _lookup(ref, env)
    return apply_filter(name, value, channel) if name else _plain(ref, value)


def render_text(template: str, env: Env, channel: Channel) -> str:
    """Render every `${...}` in a sentence; raise RenderError if any cannot be rendered."""
    return TEMPLATE.sub(lambda m: _render_one(m[1], env, channel), template)


def render_json(value: JsonValue, env: Env, channel: Channel) -> JsonValue:
    """Render templates inside JSON. A string that is exactly `${ref}` keeps the value's type."""
    if isinstance(value, str):
        m = TEMPLATE.fullmatch(value.strip())
        if m and "|" not in m[1]:
            return cast(JsonValue, copy.deepcopy(_lookup(m[1].strip(), env)))
        return render_text(value, env, channel)
    if isinstance(value, dict):
        return {k: render_json(v, env, channel) for k, v in value.items()}
    if isinstance(value, list):
        return [render_json(v, env, channel) for v in value]
    return value
