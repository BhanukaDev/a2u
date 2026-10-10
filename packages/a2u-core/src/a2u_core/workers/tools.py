"""Scoped tool permissions and the backend interface that executes tool calls.

A worker may call only the tools its config lists. A tool with side effects
needs a `confirm` earlier in the flow (the caller passes `confirmed=True`) and,
if it is under an approval policy, an approval. Approval conditions are
evaluated by the flow engine (WP-1.5) and approvals pause tasks in WP-5.1; until
then a call to a tool under an approval policy is denied.

Connector and MCP operations count as side-effecting until the connector
catalog declares their effects (WP-1.10, D-032); pass `effects` to supply them.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import JsonValue

from a2u_core.config.models import (
    AgentConfig,
    FieldType,
    FlowWorker,
    LlmWorker,
    McpTool,
    Tool,
    WebhookTool,
)

JsonObject = dict[str, JsonValue]

DenialReason = Literal[
    "not_in_scope",
    "unknown_tool",
    "not_in_allowlist",
    "not_an_operation",
    "needs_confirm",
    "needs_approval",
]


class ToolPermissionDenied(Exception):  # noqa: N818 - a denial, not a crash
    def __init__(self, ref: str, reason: DenialReason) -> None:
        super().__init__(f"{ref}: {reason}")
        self.ref = ref
        self.reason: DenialReason = reason


class ToolCallError(Exception):
    """The backend failed to complete a permitted call; the cause is chained."""

    def __init__(self, ref: str) -> None:
        super().__init__(ref)
        self.ref = ref


@dataclass(frozen=True)
class ToolSpec:
    """What a model is told about one callable tool or operation."""

    ref: str  # `name` or `name.operation`
    description: str | None
    input_schema: JsonObject


@dataclass(frozen=True)
class ToolCallContext:
    tenant_id: str
    conversation_id: str
    task_id: str
    worker: str
    step: str  # stable within the task, so a replayed call reuses its key

    @property
    def idempotency_key(self) -> str:
        return f"{self.task_id}:{self.step}"


class ToolBackend(Protocol):
    """Executes tool calls: webhooks, connectors and MCP. WP-1.10 provides the real one."""

    def describe(self, ref: str) -> Sequence[ToolSpec]:
        """The callable operations behind a scope entry (`crm` may expand to several)."""
        ...

    async def call(self, ref: str, args: JsonObject, ctx: ToolCallContext) -> JsonObject: ...


def declared_effects(config: AgentConfig) -> Callable[[str], bool | None]:
    """Side effects as the config declares them; None when it cannot say."""

    def effects(ref: str) -> bool | None:
        tool = config.tools.get(ref.partition(".")[0])
        return tool.side_effects if isinstance(tool, WebhookTool) else None

    return effects


class ToolScope:
    """The tools one worker may call, checked on every call."""

    def __init__(
        self,
        config: AgentConfig,
        refs: Iterable[str],
        *,
        effects: Callable[[str], bool | None] | None = None,
    ) -> None:
        self.config = config
        self.refs = tuple(refs)
        self.effects = effects or declared_effects(config)

    @classmethod
    def for_worker(cls, config: AgentConfig, name: str) -> "ToolScope":
        worker = config.workers[name]
        if isinstance(worker, FlowWorker):
            raise ValueError(f"{name!r} is a flow; its tool scope comes from the flow engine")
        refs = worker.tools if isinstance(worker, LlmWorker) else [worker.tool]
        return cls(config, refs)

    def check(self, ref: str, *, confirmed: bool = False) -> None:
        """Raise ToolPermissionDenied unless this scope may call `ref` now."""
        base, _, op = ref.partition(".")
        if not any(ref == r or base == r for r in self.refs):
            raise ToolPermissionDenied(ref, "not_in_scope")
        tool = self.config.tools.get(base)
        if tool is None:
            raise ToolPermissionDenied(ref, "unknown_tool")
        if not _addresses_one_operation(tool, op):
            raise ToolPermissionDenied(ref, "not_an_operation")
        if isinstance(tool, McpTool) and op not in tool.allow:
            raise ToolPermissionDenied(ref, "not_in_allowlist")
        if self.effects(ref) is False:
            return
        approval = self.config.policies.approval
        if ref in approval or base in approval:
            raise ToolPermissionDenied(ref, "needs_approval")
        if not confirmed:
            raise ToolPermissionDenied(ref, "needs_confirm")

    def allows(self, ref: str, *, confirmed: bool = False) -> bool:
        try:
            self.check(ref, confirmed=confirmed)
        except ToolPermissionDenied:
            return False
        return True


def _addresses_one_operation(tool: Tool, op: str) -> bool:
    if isinstance(tool, WebhookTool):
        return not op
    return bool(op)


class ScopedTools:
    """A backend behind a scope: every call is checked first."""

    def __init__(self, scope: ToolScope, backend: ToolBackend) -> None:
        self.scope = scope
        self.backend = backend

    async def call(
        self, ref: str, args: JsonObject, ctx: ToolCallContext, *, confirmed: bool = False
    ) -> JsonObject:
        self.scope.check(ref, confirmed=confirmed)
        try:
            return await self.backend.call(ref, args, ctx)
        except Exception as e:
            raise ToolCallError(ref) from e


_JSON_TYPES: dict[FieldType, str] = {
    "string": "string",
    "number": "number",
    "integer": "integer",
    "bool": "boolean",
    "object": "object",
    "array": "array",
}


def webhook_spec(name: str, tool: WebhookTool) -> ToolSpec:
    """A webhook's declared `input` as a JSON schema; every declared field is required."""
    return ToolSpec(
        ref=name,
        description=None,
        input_schema={
            "type": "object",
            "properties": {k: {"type": _JSON_TYPES[t]} for k, t in tool.input.items()},
            "required": list(tool.input),
            "additionalProperties": False,
        },
    )
