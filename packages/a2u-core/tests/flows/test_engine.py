"""The flow engine runs every step kind, pauses on NeedsInput and resumes from its state."""

import asyncio
from datetime import timedelta

import pytest
from a2u_core.config.models import CollectDefaults, FlowWorker
from a2u_core.flows import FlowEngine, FlowRun, step_names
from a2u_core.flows.engine import (
    DECLINED_MESSAGE,
    INVALID_MESSAGE,
    MAX_DELEGATE_DEPTH,
    MAX_FORMAT_FAILURES,
    NO_OPTIONS_MESSAGE,
    TIMEOUT_MESSAGE,
    VERIFY_FAILED_MESSAGE,
)
from a2u_core.workers import Failed, NeedsInput, Option, Result
from a2u_core.workers.tools import JsonObject

from .conftest import CTX, Flows


def asked(run: FlowRun, field: str) -> NeedsInput:
    assert isinstance(run.outcome, NeedsInput), run.outcome
    assert run.outcome.field == field
    assert not run.state.done
    return run.outcome


def failed(run: FlowRun, reason: str) -> Failed:
    assert isinstance(run.outcome, Failed), run.outcome
    assert run.outcome.reason == reason
    assert run.state.done
    assert run.state.pending is None
    return run.outcome


# --- The spec's booking flow (§5.4), end to end -----------------------------------

UPCOMING: JsonObject = {
    "items": [{"id": "apt_1", "label": "Cleaning with Dr Perera", "doctor": "dr_perera"}]
}
SLOT_TIMES = [
    "2026-10-16T15:30:00+05:30",
    "2026-10-21T09:00:00+05:30",
    "2026-10-22T11:00:00+05:30",
    "2026-10-23T11:00:00+05:30",  # beyond the 3 options offered
]
SLOTS: JsonObject = {"items": [*SLOT_TIMES]}


@pytest.mark.parametrize(
    ("channel", "say"),
    [
        ("phone", "Done. Your appointment is now on Wednesday the 21st at 9 am."),
        ("whatsapp", "Done. Your appointment is now on Wed 21 Oct, 9 am."),
    ],
)
def test_booking_flow_with_decline_and_pick_again(sunrise: Flows, channel: str, say: str) -> None:
    sunrise.backend.replies = {
        "calendar.upcoming": UPCOMING,
        "calendar.free_slots": SLOTS,
        "calendar.move": {"moved": True},
    }
    engine = sunrise.engine("bookings")

    run = sunrise.start(engine, channel=channel)  # type: ignore[arg-type]
    asked(run, "otp")
    assert sunrise.verifier.sent == 1

    run = sunrise.answer(engine, run, "123456")
    appointment = asked(run, "appointment")
    assert appointment.options == [Option(value="apt_1", label="Cleaning with Dr Perera")]
    assert run.state.verified

    run = sunrise.answer(engine, run, "apt_1")
    slot = asked(run, "slot")
    assert slot.options is not None
    assert [o.value for o in slot.options] == SLOT_TIMES[:3]
    first_label = "Friday the 16th at 3:30 pm" if channel == "phone" else "Fri 16 Oct, 3:30 pm"
    assert slot.options[0].label == first_label

    run = sunrise.answer(engine, run, "2026-10-16T15:30:00+05:30")
    confirm = asked(run, "confirm")
    assert confirm.hint == "Move your appointment to 2026-10-16T15:30:00+05:30?"
    assert confirm.options == [Option(value="yes", label="Yes"), Option(value="no", label="No")]

    # "No" goes back to the slot step (on_no: slot): the slots are offered again.
    run = sunrise.answer(engine, run, "no")
    asked(run, "slot")
    assert "slot" not in run.state.bindings
    assert run.state.visits["slot"] == 2  # step_reached: { step: slot, times: 2 }

    run = sunrise.answer(engine, run, "2026-10-21T09:00:00+05:30")
    asked(run, "confirm")
    run = sunrise.answer(engine, run, "yes")

    assert run.outcome == Result(
        data={"slot": "2026-10-21T09:00:00+05:30", "appointment_id": "apt_1"}, say=say
    )
    assert run.state.done
    assert run.handoff is None
    assert sunrise.calls() == [
        ("calendar.upcoming", {}),
        ("calendar.free_slots", {"doctor": "dr_perera"}),
        ("calendar.free_slots", {"doctor": "dr_perera"}),
        ("calendar.move", {"id": "apt_1", "to": "2026-10-21T09:00:00+05:30"}),
    ]
    keys = [ctx.idempotency_key for _, _, ctx in sunrise.backend.calls]
    assert keys == ["t_1:bookings:1", "t_1:bookings:2", "t_1:bookings:3", "t_1:bookings:4"]


def test_booking_flow_side_effect_needs_the_confirm(sunrise: Flows) -> None:
    # calendar.move is side-effecting: once the confirm is undone by on_no, it is not allowed.
    sunrise.backend.replies = {"calendar.upcoming": UPCOMING, "calendar.free_slots": SLOTS}
    engine = sunrise.engine("bookings")
    run = sunrise.start(engine)
    for answer in ("123456", "apt_1", SLOT_TIMES[0], "yes"):
        run = sunrise.answer(engine, run, answer)
    assert isinstance(run.outcome, Result)
    assert run.state.confirmed_at == [[3]]


def test_step_names_match_the_loader(sunrise: Flows) -> None:
    worker = sunrise.config.workers["bookings"]
    assert isinstance(worker, FlowWorker)
    assert [step_names(s) for s in worker.steps] == [
        ["verify"],
        ["collect", "appointment"],
        ["collect", "slot"],
        ["confirm"],
        ["call_tool", "moved"],
        ["result"],
    ]


# --- collect ------------------------------------------------------------------------


def test_collect_binds_checked_values_and_result_keeps_types(flows: Flows) -> None:
    engine = flows.engine("intake")
    run = flows.start(engine)
    request = asked(run, "name")
    assert request.input_schema == {"type": "string", "entity": "person_name"}
    run = flows.answer(engine, run, " Kamala ")
    assert asked(run, "age").input_schema == {"type": "integer"}
    run = flows.answer(engine, run, "42")
    run = flows.answer(engine, run, "2002 1770 1234")
    assert run.outcome == Result(
        data={"name": "Kamala", "age": 42, "nic": "200217701234"},
        say="Thanks Kamala, your NIC is 2 0 0 2 1 7 7 0 1 2 3 4.",
    )


def test_collect_reasks_then_fails_after_its_retries(flows: Flows) -> None:
    engine = flows.engine("intake")
    run = flows.answer(engine, flows.start(engine), "Kamala")
    run = flows.answer(engine, run, "forty")  # retries: 1, so one re-ask
    reask = asked(run, "age")
    assert reask.hint == (
        "Ask the user for age. The last answer was not accepted: expected a integer."
    )
    run = flows.answer(engine, run, "old")
    assert failed(run, "invalid_input").user_safe_message == INVALID_MESSAGE


def test_collect_retries_default_to_the_policy(flows: Flows) -> None:
    policies = flows.config.policies
    flows.config = flows.config.model_copy(
        update={"policies": policies.model_copy(update={"collect": CollectDefaults(retries=0)})}
    )
    engine = flows.engine("plans")
    run = flows.answer(engine, flows.start(engine), "gold")  # not an option, no retries left
    failed(run, "invalid_input")


def test_format_failures_do_not_use_up_retries(flows: Flows) -> None:
    engine = flows.engine("intake")
    run = flows.answer(engine, flows.start(engine), "Kamala")
    run = flows.answer(engine, run, 30)
    for _ in range(MAX_FORMAT_FAILURES):
        run = flows.answer(engine, run, "2002177")  # too short: a format failure
        asked(run, "nic")
    assert run.state.attempts == {}
    failed(flows.answer(engine, run, "2002177"), "invalid_input")


def test_collect_timeout(flows: Flows) -> None:
    engine = flows.engine("intake")
    run = flows.start(engine)
    pending = run.state.pending
    assert pending is not None
    assert pending.deadline == flows.clock.now + timedelta(hours=24)  # policies.collect.timeout

    run = flows.answer(engine, flows.answer(engine, run, "Kamala"), 30)
    asked(run, "nic")  # timeout: 1h on this step
    flows.clock.advance(timedelta(minutes=59))
    still = asyncio.run(engine.expire(run.state))
    assert still.outcome == run.outcome
    assert not still.state.done

    flows.clock.advance(timedelta(minutes=1))
    expired = asyncio.run(engine.expire(run.state))
    assert failed(expired, "timeout").user_safe_message == TIMEOUT_MESSAGE
    # An answer that arrives after the deadline is too late as well.
    failed(flows.answer(engine, run, "200217701234"), "timeout")


def test_reask_restarts_the_deadline(flows: Flows) -> None:
    engine = flows.engine("intake")
    run = flows.start(engine)
    flows.clock.advance(timedelta(hours=23))
    run = flows.answer(engine, run, "")  # a format failure
    assert run.state.pending is not None
    assert run.state.pending.deadline == flows.clock.now + timedelta(hours=24)


# --- choose, confirm, call_tool, result ---------------------------------------------


def test_choose_offers_at_most_max_options_and_needs_one_of_them(flows: Flows) -> None:
    engine = flows.engine("plans")
    run = flows.start(engine, channel="whatsapp")
    request = asked(run, "plan")
    assert request.options == [
        Option(value="basic", label="basic"),
        Option(value="plus", label="plus"),
    ]
    assert request.input_schema == {"type": "string", "enum": ["basic", "plus"]}
    run = flows.answer(engine, run, "premium")  # exists, but was not offered
    assert "expected one of the options: basic, plus" in asked(run, "plan").hint
    run = flows.answer(engine, run, "plus")
    assert asked(run, "confirm").hint == "Switch to plus?"


def test_confirm_yes_permits_the_side_effect_and_result_defaults_to_the_tool_data(
    flows: Flows,
) -> None:
    flows.backend.replies = {"switch_plan": {"plan": "plus", "from": "2026-11-01"}}
    engine = flows.engine("plans")
    run = flows.answer(engine, flows.start(engine), "plus")
    run = flows.answer(engine, run, "maybe")  # not yes or no: asked again
    assert "expected yes or no" in asked(run, "confirm").hint
    run = flows.answer(engine, run, True)
    assert run.outcome == Result(
        data={"plan": "plus", "from": "2026-11-01"}, say="You're on plus now."
    )
    assert flows.calls() == [("switch_plan", {"plan": "plus"})]


def test_confirm_no_without_on_no_declines(flows: Flows) -> None:
    engine = flows.engine("plans")
    run = flows.answer(engine, flows.start(engine), "basic")
    run = flows.answer(engine, run, "No")
    assert failed(run, "declined").user_safe_message == DECLINED_MESSAGE
    assert flows.calls() == []


def test_choose_from_a_bound_list(flows: Flows) -> None:
    flows.backend.replies = {
        "menu": {
            "dishes": [
                {"id": "d1", "name": "Kottu"},
                {"id": "d2", "label": "Hoppers (6)"},
                {"name": "Lamprais"},  # no id: every option is numbered instead
            ]
        }
    }
    engine = flows.engine("menu_pick")
    request = asked(flows.start(engine), "dish")
    assert request.options == [
        Option(value="1", label="Kottu"),
        Option(value="2", label="Hoppers (6)"),
        Option(value="3", label="Lamprais"),
    ]
    run = flows.answer(engine, flows.start(engine), "2")
    assert run.outcome == Result(data={"dish": {"id": "d2", "label": "Hoppers (6)"}})


def test_choose_with_nothing_to_offer_fails(flows: Flows) -> None:
    flows.backend.replies = {"menu": {"dishes": []}}
    run = flows.start(flows.engine("menu_pick"))
    assert failed(run, "no_options").user_safe_message == NO_OPTIONS_MESSAGE
    flows.backend.replies = {"menu": {"dishes": "kottu"}}
    failed(
        flows.start(flows.engine("menu_pick")),
        "choose_source: menu.dishes did not give a list of items",
    )


# --- if, handoff, approval ----------------------------------------------------------


def _refund(flows: Flows, order: JsonObject, customer: JsonObject) -> FlowRun:
    flows.backend.replies = {"order_lookup": order, "issue_refund": {"refund_id": "R-42"}}
    engine = flows.engine("refunds")
    return flows.answer(engine, flows.start(engine, customer=customer), "12345678")


def test_nested_if_takes_the_matching_branches(flows: Flows) -> None:
    order: JsonObject = {"id": "o_1", "status": "damaged", "total": 40}
    run = _refund(flows, order, {"tier": "gold"})
    assert run.outcome == Result(data={"refund_id": "R-42"}, say="Refund R 4 2 is on its way.")
    assert flows.calls()[-1] == ("issue_refund", {"id": "o_1", "amount": 40})

    run = _refund(flows, order, {"tier": "silver"})
    assert flows.calls()[-1] == ("issue_refund", {"id": "o_1", "amount": 0})
    assert run.state.cursor == [3]


def test_if_without_a_matching_branch_hands_off(flows: Flows) -> None:
    run = _refund(flows, {"id": "o_1", "status": "late", "total": 40}, {"tier": "gold"})
    assert run.outcome == Result(data={"id": "o_1", "status": "late", "total": 40})
    assert run.handoff == "human"
    assert run.state.done


def test_approval_condition_is_evaluated_with_the_call_arguments(flows: Flows) -> None:
    # policies.approval: { issue_refund: "amount > 50" }; approvals pause tasks in WP-5.1.
    run = _refund(flows, {"id": "o_1", "status": "damaged", "total": 80}, {"tier": "gold"})
    failed(run, "permission_denied: issue_refund: needs_approval")
    assert [ref for ref, _ in flows.calls()] == ["order_lookup"]


def test_condition_that_cannot_be_evaluated_fails_the_flow(flows: Flows) -> None:
    run = _refund(flows, {"id": "o_1", "total": 40}, {"tier": "gold"})
    failed(run, "condition_error: order.status is not bound")


def test_if_nesting_is_checked_when_the_engine_is_built(flows: Flows) -> None:
    deep = FlowWorker.model_validate(
        {
            "kind": "flow",
            "steps": [
                {"if": "true", "then": [{"if": "true", "then": [{"if": "true", "then": []}]}]}
            ],
        }
    )
    config = flows.config.model_copy(update={"workers": {**flows.config.workers, "deep": deep}})
    with pytest.raises(ValueError, match="nesting depth"):
        FlowEngine(config, "deep", flows.backend)


def test_only_flow_workers_run_in_the_engine(flows: Flows) -> None:
    with pytest.raises(ValueError, match="not a flow"):
        FlowEngine(flows.config, "concierge", flows.backend)


# --- verify ----------------------------------------------------------------------------


def test_verify_knowledge_asks_each_fact(flows: Flows) -> None:
    flows.verifier.facts = {"dob": "1990-01-02", "postcode": "00300"}
    engine = flows.engine("kyc")
    run = flows.start(engine)
    asked(run, "dob")
    run = flows.answer(engine, run, "1990-02-01")  # wrong: asked again
    asked(run, "dob")
    run = flows.answer(engine, run, "1990-01-02")
    asked(run, "postcode")
    run = flows.answer(engine, run, "00300")
    assert run.outcome == Result(data={}, say="Thanks, you're verified.")
    assert run.state.verified
    assert flows.verifier.sent == 0


def test_verify_fails_after_the_retries(sunrise: Flows) -> None:
    engine = sunrise.engine("bookings")
    run = sunrise.start(engine)
    for _ in range(2):  # policies.collect.retries: 2
        run = sunrise.answer(engine, run, "000000")
        asked(run, "otp")
    run = sunrise.answer(engine, run, "000000")
    assert failed(run, "verification_failed").user_safe_message == VERIFY_FAILED_MESSAGE
    assert sunrise.verifier.sent == 1  # a wrong code is asked again, not resent


def test_verify_without_a_verifier_fails(flows: Flows) -> None:
    failed(flows.start(flows.engine("kyc", verifier=None)), "verify_unavailable")


# --- delegate --------------------------------------------------------------------------


def test_delegate_passes_input_through_and_binds_the_result(flows: Flows) -> None:
    question = NeedsInput(field="when", hint="Ask when.", schema={"type": "string"})
    flows.delegator.outcomes = {"concierge": [question, Result(data={"summary": "Open at 9."})]}
    engine = flows.engine("concierge_flow")
    run = flows.start(engine, args={"topic": "hours"})
    assert run.outcome == question
    run = flows.answer(engine, run, "tomorrow")
    assert run.outcome == Result(data={"summary": "Open at 9.", "job": "task_1"})
    assert flows.delegator.calls == [
        ("run", "concierge", {"topic": "hours"}),
        ("resume", "concierge", "tomorrow"),
    ]
    assert flows.delegator.started == [("notify", {"about": "Open at 9."}, 1)]


def test_failed_delegate_fails_the_flow_with_its_message(flows: Flows) -> None:
    flows.delegator.outcomes = {
        "concierge": [Failed(reason="gave_up: no data", user_safe_message="I can't find that.")]
    }
    run = flows.start(flows.engine("concierge_flow"), args={"topic": "x"})
    assert failed(run, "delegate_failed: concierge: gave_up: no data").user_safe_message == (
        "I can't find that."
    )


def test_delegation_depth_is_capped(flows: Flows) -> None:
    run = flows.start(flows.engine("concierge_flow", depth=MAX_DELEGATE_DEPTH), args={"topic": "x"})
    failed(run, "delegate_too_deep")
    assert flows.delegator.calls == []


# --- state and errors ----------------------------------------------------------------


def test_resuming_the_same_state_twice_gives_the_same_run(flows: Flows) -> None:
    engine = flows.engine("plans")
    paused = flows.start(engine)
    first = flows.answer(engine, paused, "plus")
    second = flows.answer(engine, paused, "plus")
    assert first == second
    assert paused.state.pending is not None
    assert paused.state.bindings == {}  # resume never changes the state it was given


def test_render_error_fails_instead_of_saying_something_wrong(flows: Flows) -> None:
    run = flows.start(flows.engine("greeting"), args={})
    failed(run, "render_error: args.name is not bound")
    run = flows.start(flows.engine("greeting"), args={"name": "Kamala"})
    assert run.outcome == Result(data={}, say="Hello Kamala.")


def test_tool_errors_fail_the_flow(flows: Flows) -> None:
    flows.backend.replies = {"menu": TimeoutError()}
    failed(flows.start(flows.engine("menu_pick")), "tool_error: menu: TimeoutError")


def test_finished_flows_cannot_be_resumed(flows: Flows) -> None:
    engine = flows.engine("greeting")
    run = flows.start(engine, args={"name": "Kamala"})
    with pytest.raises(ValueError, match="not waiting"):
        asyncio.run(engine.resume(run.state, "hi", CTX))
    with pytest.raises(ValueError, match="not waiting"):
        asyncio.run(engine.expire(run.state))
