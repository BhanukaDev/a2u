"""Fake ABC Bank backend for the voice spike: one customer, one card, typed tools.

The LLM decides *when* to call a tool; the code decides *what is true* and *what is allowed*:
card tools refuse until the caller is verified, verification locks after 3 failures, and
side-effecting tools refuse unless the caller has confirmed. This mirrors the product rule
"the LLM understands and speaks; code decides" in a few dozen lines.

Edit DEMO_CUSTOMERS to change the story. DEMO_MOBILE in .env pins the registered mobile
number; if unset, any valid Sri Lankan mobile (07XXXXXXXX) is accepted for the demo NIC.
"""

from __future__ import annotations

import copy
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from livekit.agents import RunContext, ToolError, function_tool

MAX_VERIFY_ATTEMPTS = 3

DEMO_CUSTOMERS: dict[str, dict[str, Any]] = {
    "200217701234": {
        "name": "Kasun Perera",
        "mobile": os.getenv("DEMO_MOBILE") or None,
        "account": {"type": "Savings", "available_balance_lkr": 84_250.00},
        "card": {
            "type": "Visa debit",
            "last4": "4821",
            "status": "active",  # active | blocked
            "expires": "08/2028",
            "chip_health": "no fault found, the card is working normally",
            "limits_lkr": {"atm_daily": 100_000, "pos_daily": 500_000},
            "recent_declines": [
                {
                    "when": "today 2:14 pm",
                    "where": "Keells Super, Kollupitiya (card terminal)",
                    "amount_lkr": 6_420.00,
                    "reason": "the terminal could not read the chip; the card itself has no fault (usually dirt or a scratch on the chip)",
                },
                {
                    "when": "today 2:31 pm",
                    "where": "ATM at Borella Junction (another bank's ATM)",
                    "amount_lkr": 10_000.00,
                    "reason": "the ATM could not read the chip; the card itself has no fault (usually dirt or a scratch on the chip)",
                },
            ],
        },
    },
}


@dataclass
class CallState:
    """Per-call state, held by the session (session.userdata)."""

    verify_attempts: int = 0
    # This call's own copy of the customer record, so blocking a card doesn't leak into later calls.
    record: dict[str, Any] | None = None
    actions: list[str] = field(default_factory=list)

    def customer(self) -> dict[str, Any]:
        if self.record is None:
            raise ToolError("The caller is not verified yet. Verify them with their NIC and mobile number first.")
        return self.record


def _normalise_nic(nic: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", nic).upper()


# New NIC: 12 digits (birth year, day of year, serial, check digit). Old NIC: 9 digits then V or X.
NIC_FORMAT = re.compile(r"\d{12}|\d{9}[VX]")


def _normalise_mobile(mobile: str) -> str:
    digits = re.sub(r"\D", "", mobile)
    if digits.startswith("94") and len(digits) == 11:  # +94 77 ... -> 077 ...
        digits = "0" + digits[2:]
    return digits


@function_tool
async def verify_customer(
    context: RunContext[CallState], nic: str, mobile: str, caller_confirmed_both: bool
) -> dict[str, Any]:
    """Verify the caller's identity. Call this only once you have both their NIC number and their
    registered mobile number, have read each one back digit by digit, and the caller said yes to each.

    Args:
        nic: The caller's NIC number: 12 digits (new), or 9 digits then V or X (old),
            e.g. "200217701234" or "853400937V".
        mobile: The caller's mobile number as digits, e.g. "0771234567".
        caller_confirmed_both: True only if you read back both numbers and the caller confirmed both.
    """
    state = context.userdata
    if state.record:
        return {"verified": True, "note": "already verified", "name": state.customer()["name"]}
    if not caller_confirmed_both:
        raise ToolError(
            "Read the NIC and the mobile number back to the caller digit by digit and get a yes "
            "for each before verifying."
        )
    nic_key, phone = _normalise_nic(nic), _normalise_mobile(mobile)
    # A misheard length is not a failed attempt: send the agent back to the caller without counting it.
    if not NIC_FORMAT.fullmatch(nic_key):
        raise ToolError(
            f"{nic_key} is not a valid NIC: it has {len(nic_key)} characters. A new NIC has 12 digits and "
            "an old one has 9 digits then V or X. Read back what you have one digit at a time and ask "
            "the caller for the missing part."
        )
    if state.verify_attempts >= MAX_VERIFY_ATTEMPTS:
        raise ToolError(
            "Verification is locked after 3 failed attempts. Tell the caller, kindly, to visit any "
            "ABC Bank branch with their NIC, or call back later."
        )
    state.verify_attempts += 1
    left = MAX_VERIFY_ATTEMPTS - state.verify_attempts

    record = DEMO_CUSTOMERS.get(nic_key)
    if record is None:
        raise ToolError(
            f"No customer found with NIC {nic_key}. Read those digits back one at a time so the caller "
            f"can spot the mistake, then ask them to say it slowly. Attempts left: {left}."
        )
    if not re.fullmatch(r"07\d{8}", phone):
        raise ToolError(f"That is not a valid Sri Lankan mobile number. Ask again. Attempts left: {left}.")
    if record["mobile"] and phone != record["mobile"]:
        raise ToolError(f"The mobile number does not match our records. Ask again. Attempts left: {left}.")

    state.record = copy.deepcopy(record)
    return {"verified": True, "name": record["name"]}


@function_tool
async def get_card_status(context: RunContext[CallState]) -> dict[str, Any]:
    """Look up the verified caller's debit card: status, recent declined transactions and their
    reasons, limits, and the account's available balance. Only works after verify_customer."""
    c = context.userdata.customer()
    return {"card": c["card"], "account": c["account"]}


@function_tool
async def order_replacement_card(context: RunContext[CallState], caller_confirmed: bool) -> dict[str, Any]:
    """Order a replacement debit card for the verified caller. Before calling, tell them it is free
    because the chip is faulty, arrives in 7 working days at their registered address, and the old
    card keeps working until the new one is activated, then ask if they want it.

    Args:
        caller_confirmed: True only if the caller clearly said yes to ordering the card.
    """
    state = context.userdata
    c = state.customer()
    if not caller_confirmed:
        raise ToolError("The caller has not confirmed. Ask them clearly before ordering.")
    ref = f"RC-{secrets.randbelow(900_000) + 100_000}"
    state.actions.append(f"replacement card {ref}")
    return {
        "ordered": True,
        "reference": ref,
        "fee_lkr": 0,
        "arrives": "within 7 working days",
        "card_last4": c["card"]["last4"],
    }


@function_tool
async def block_card(context: RunContext[CallState], reason: str, caller_confirmed: bool) -> dict[str, Any]:
    """Block the verified caller's debit card immediately. Only for a lost, stolen or misused card.
    Before calling, warn them the card will stop working at once and cannot be unblocked by phone,
    and ask if they want to go ahead.

    Args:
        reason: One of "lost", "stolen", "fraud".
        caller_confirmed: True only if the caller clearly said yes to blocking the card.
    """
    state = context.userdata
    c = state.customer()
    if not caller_confirmed:
        raise ToolError("The caller has not confirmed. Ask them clearly before blocking.")
    c["card"]["status"] = "blocked"
    state.actions.append(f"card blocked ({reason})")
    return {"blocked": True, "card_last4": c["card"]["last4"]}


TOOLS = [verify_customer, get_card_status, order_replacement_card, block_card]
