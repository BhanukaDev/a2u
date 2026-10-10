import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from a2u_core.config import AgentConfig, load_config, load_file
from a2u_core.flows import FlowEngine, FlowRun, FlowState
from a2u_core.flows.fakes import FakeDelegator, FakeVerifier
from a2u_core.flows.render import Channel
from a2u_core.workers import WorkerContext, declared_effects
from a2u_core.workers.fakes import FakeToolBackend
from pydantic import JsonValue

SUNRISE = Path(__file__).parents[1] / "config" / "fixtures" / "valid" / "sunrise_dental.yaml"
CTX = WorkerContext(tenant_id="ws_1", conversation_id="c_1", task_id="t_1")

CONFIG = """
agent: clinic
languages: { default: en, voice: [en], text: [en] }
front:
  instructions: You are the clinic's assistant.
  voice: { voices: { en: { voice_id: maya } } }
channels:
  web: { chat: true, voice: true }
  phone: { numbers: ["+94 11 234 5678"] }
  whatsapp: { account: clinic }

workers:
  intake:
    kind: flow
    steps:
      - collect: { name: person_name }
      - collect: { age: integer, retries: 1 }
      - collect: { nic: nic_lk, timeout: 1h }
      - result:
          data: { name: "${name}", age: "${age}", nic: "${nic}" }
          say: "Thanks ${name}, your NIC is ${nic | digits_spoken}."

  plans:
    kind: flow
    steps:
      - choose: { plan: [basic, plus, premium], max_options: 2 }
      - confirm: "Switch to ${plan}?"
      - call_tool: { name: switch_plan, args: { plan: "${plan}" }, as: switched }
      - result: { say: "You're on ${plan} now." }

  refunds:
    kind: flow
    steps:
      - collect: { order_no: { type: id, pattern: "\\\\d{8}" } }
      - call_tool: { name: order_lookup, args: { order_no: "${order_no}" }, as: order }
      - if: "order.status == 'damaged' && order.total <= 100"
        then:
          - if: "customer.tier == 'gold'"
            then:
              - call_tool:
                  name: issue_refund
                  args: { id: "${order.id}", amount: "${order.total}" }
                  as: refund
            else:
              - call_tool:
                  name: issue_refund
                  args: { id: "${order.id}", amount: 0 }
                  as: refund
        else: [ { handoff: human } ]
      - result: { say: "Refund ${refund.refund_id | digits_spoken} is on its way." }

  kyc:
    kind: flow
    steps:
      - verify: { knowledge: [dob, postcode] }
      - result: { say: "Thanks, you're verified." }

  menu_pick:
    kind: flow
    steps:
      - call_tool: { name: menu, as: menu }
      - choose: { dish: "choose(menu.dishes)", max_options: 5 }
      - result: { data: { dish: "${dish}" } }

  concierge_flow:
    kind: flow
    steps:
      - delegate: { worker: concierge, args: { topic: "${args.topic}" }, as: answer }
      - delegate:
          { worker: notify, args: { about: "${answer.summary}" }, background: true, as: job }
      - result: { data: { summary: "${answer.summary}", job: "${job.task_id}" } }

  greeting:
    kind: flow
    steps:
      - result: { say: "Hello ${args.name}." }

  concierge:
    kind: llm
    instructions: Answer questions about the clinic.
  notify:
    kind: tool
    tool: notify_tool

tools:
  calendar: { type: connector, connector: google_calendar, connection: clinic-gcal }
  switch_plan:
    type: webhook
    url: https://clinic.example.lk/a2u/plan
    input: { plan: string }
  order_lookup:
    type: webhook
    url: https://clinic.example.lk/a2u/orders
    input: { order_no: string }
    side_effects: false
  issue_refund:
    type: webhook
    url: https://clinic.example.lk/a2u/refunds
    input: { id: string, amount: number }
  menu:
    type: webhook
    url: https://clinic.example.lk/a2u/menu
    side_effects: false
  notify_tool:
    type: webhook
    url: https://clinic.example.lk/a2u/notify
    side_effects: false

policies:
  approval: { issue_refund: "amount > 50" }
"""

# Connector operations count as side-effecting until WP-1.10 declares them (D-032).
READ_ONLY = frozenset({"calendar.upcoming", "calendar.free_slots"})


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


class Flows:
    """Builds engines over one config and drives them like a task would."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.backend = FakeToolBackend(config)
        self.verifier = FakeVerifier()
        self.delegator = FakeDelegator()
        self.clock = Clock()

    def effects(self) -> Callable[[str], bool | None]:
        declared = declared_effects(self.config)
        return lambda ref: False if ref in READ_ONLY else declared(ref)

    def engine(self, name: str, **kw: Any) -> FlowEngine:
        options: dict[str, Any] = {
            "verifier": self.verifier,
            "delegator": self.delegator,
            "effects": self.effects(),
            "clock": self.clock,
        }
        return FlowEngine(self.config, name, self.backend, **{**options, **kw})

    def start(self, engine: FlowEngine, channel: Channel = "phone", **kw: Any) -> FlowRun:
        return asyncio.run(engine.start(CTX, channel=channel, **kw))

    def answer(self, engine: FlowEngine, run: FlowRun, value: JsonValue) -> FlowRun:
        # Round-trip the state through JSON, as a task does between turns.
        state = FlowState.model_validate_json(run.state.model_dump_json())
        return asyncio.run(engine.resume(state, value, CTX))

    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [(ref, args) for ref, args, _ in self.backend.calls]


@pytest.fixture
def flows() -> Flows:
    return Flows(load_config(CONFIG).config)


@pytest.fixture
def sunrise() -> Flows:
    return Flows(load_file(SUNRISE).config)
