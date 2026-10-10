import pytest
from a2u_core.config import AgentConfig, load_config
from a2u_core.workers.fakes import FakeToolBackend

CONFIG = """
agent: shop
languages: { default: en, text: [en] }
front:
  instructions: You are the shop's assistant.
channels:
  web: { chat: true }

workers:
  products:
    kind: llm
    instructions: products
    tools: [search_catalog, crm.lookup_customer]
  refunds:
    kind: llm
    instructions: Handle refund questions.
    tools: [issue_refund, crm]
  insurance:
    kind: tool
    tool: insurer_check
    timeout: 1s
  bookings:
    kind: flow
    steps:
      - result: { say: "Done." }

tools:
  search_catalog:
    type: webhook
    url: https://shop.example.lk/a2u/search
    input: { query: string, limit: integer }
    output: { items: array }
    side_effects: false
  insurer_check:
    type: webhook
    url: https://shop.example.lk/a2u/insurance
    input: { policy_no: string }
    side_effects: false
  issue_refund:
    type: webhook
    url: https://shop.example.lk/a2u/refund
    input: { order_id: string, amount: number }
  crm:
    type: mcp
    server: https://mcp.example.lk
    allow: [lookup_customer, update_customer]

policies:
  approval: { issue_refund: "amount > 100", crm: "true" }
"""


@pytest.fixture
def config() -> AgentConfig:
    return load_config(CONFIG).config


@pytest.fixture
def backend(config: AgentConfig) -> FakeToolBackend:
    return FakeToolBackend(config)
