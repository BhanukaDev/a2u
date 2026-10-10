"""Scoped tool permissions: a worker calls only its tools; effects need confirm or approval."""

import asyncio

import pytest
from a2u_core.config import AgentConfig
from a2u_core.workers import (
    ScopedTools,
    ToolCallContext,
    ToolPermissionDenied,
    ToolScope,
    webhook_spec,
)
from a2u_core.workers.fakes import FakeToolBackend

CTX = ToolCallContext(
    tenant_id="ws_1", conversation_id="c_1", task_id="t_1", worker="products", step="1"
)


def _denied(scope: ToolScope, ref: str, *, confirmed: bool = False) -> str:
    with pytest.raises(ToolPermissionDenied) as err:
        scope.check(ref, confirmed=confirmed)
    assert err.value.ref == ref
    return err.value.reason


def test_llm_worker_may_call_its_listed_read_only_tools(config: AgentConfig) -> None:
    scope = ToolScope.for_worker(config, "products")
    scope.check("search_catalog")
    assert scope.refs == ("search_catalog", "crm.lookup_customer")


def test_tool_outside_the_worker_scope_is_denied(config: AgentConfig) -> None:
    scope = ToolScope.for_worker(config, "products")
    assert _denied(scope, "insurer_check") == "not_in_scope"
    assert _denied(scope, "issue_refund") == "not_in_scope"


def test_listing_one_mcp_operation_grants_only_that_one(config: AgentConfig) -> None:
    scope = ToolScope.for_worker(config, "products")
    assert _denied(scope, "crm.update_customer") == "not_in_scope"
    assert _denied(scope, "crm") == "not_in_scope"


def test_listing_an_mcp_server_grants_only_allowlisted_operations(config: AgentConfig) -> None:
    scope = ToolScope(config, ["crm"])
    assert _denied(scope, "crm.delete_customer") == "not_in_allowlist"
    assert _denied(scope, "crm") == "not_an_operation"


def test_unknown_tool_is_denied(config: AgentConfig) -> None:
    assert _denied(ToolScope(config, ["nope"]), "nope") == "unknown_tool"
    assert _denied(ToolScope(config, ["search_catalog"]), "search_catalog.x") == "not_an_operation"


def test_tool_worker_scope_is_its_one_tool(config: AgentConfig) -> None:
    scope = ToolScope.for_worker(config, "insurance")
    scope.check("insurer_check")
    assert _denied(scope, "search_catalog") == "not_in_scope"


def test_flow_scopes_come_from_the_flow_engine(config: AgentConfig) -> None:
    with pytest.raises(ValueError, match="flow"):
        ToolScope.for_worker(config, "bookings")
    with pytest.raises(KeyError):
        ToolScope.for_worker(config, "nobody")


def test_side_effecting_tool_needs_confirm(config: AgentConfig) -> None:
    config.policies.approval.pop("issue_refund")
    scope = ToolScope(config, ["issue_refund"])
    assert _denied(scope, "issue_refund") == "needs_confirm"
    scope.check("issue_refund", confirmed=True)


def test_approval_policy_gates_the_tool_even_after_confirm(config: AgentConfig) -> None:
    # The condition is evaluated by WP-1.5 and the pause is WP-5.1; until then, deny.
    scope = ToolScope(config, ["issue_refund"])
    assert _denied(scope, "issue_refund") == "needs_approval"
    assert _denied(scope, "issue_refund", confirmed=True) == "needs_approval"


def test_mcp_operations_count_as_side_effecting_until_declared(config: AgentConfig) -> None:
    # Connector and MCP effects come from the connector catalog (WP-1.10, D-032).
    scope = ToolScope.for_worker(config, "products")
    assert _denied(scope, "crm.lookup_customer") == "needs_approval"
    declared = ToolScope(
        config, ["crm.lookup_customer"], effects=lambda ref: ref != "crm.lookup_customer"
    )
    declared.check("crm.lookup_customer")


def test_allows_is_check_without_raising(config: AgentConfig) -> None:
    config.policies.approval.pop("issue_refund")
    scope = ToolScope(config, ["search_catalog", "issue_refund", "crm"])
    assert scope.allows("search_catalog")
    assert not scope.allows("issue_refund")
    assert scope.allows("issue_refund", confirmed=True)
    assert not scope.allows("crm.lookup_customer", confirmed=True)  # under approval


def test_scoped_tools_check_before_calling(config: AgentConfig, backend: FakeToolBackend) -> None:
    tools = ScopedTools(ToolScope.for_worker(config, "products"), backend)
    with pytest.raises(ToolPermissionDenied):
        asyncio.run(tools.call("issue_refund", {"order_id": "o1", "amount": 5}, CTX))
    assert backend.calls == []

    backend.replies = {"search_catalog": {"items": ["kettle"]}}
    out = asyncio.run(tools.call("search_catalog", {"query": "kettle"}, CTX))
    assert out == {"items": ["kettle"]}
    assert [(ref, args) for ref, args, _ in backend.calls] == [
        ("search_catalog", {"query": "kettle"})
    ]


def test_idempotency_key_is_task_and_step() -> None:
    assert CTX.idempotency_key == "t_1:1"


def test_webhook_spec_turns_declared_input_into_json_schema(config: AgentConfig) -> None:
    tool = config.tools["search_catalog"]
    assert tool.type == "webhook"
    spec = webhook_spec("search_catalog", tool)
    assert spec.ref == "search_catalog"
    assert spec.input_schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query", "limit"],
        "additionalProperties": False,
    }
