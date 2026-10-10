"""Runtimes for `tool` and `llm` workers (spec §5.1). Flow workers run in the flow engine.

Every run ends in a contract outcome. Exceptions, permission denials, timeouts
and anything else a worker produces are turned into `Failed` by `run_guarded`.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai import (
    Agent,
    ModelMessage,
    ModelRequest,
    RunContext,
    Tool,
    ToolOutput,
    ToolReturnPart,
    UsageLimits,
)
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models import Model

from a2u_core.config.models import AgentConfig, LlmWorker, ToolWorker, is_document_name
from a2u_core.workers.contract import (
    Failed,
    NeedsInput,
    Option,
    Outcome,
    Progress,
    Result,
    to_outcome,
)
from a2u_core.workers.tools import (
    JsonObject,
    ScopedTools,
    ToolBackend,
    ToolCallContext,
    ToolCallError,
    ToolPermissionDenied,
    ToolScope,
    ToolSpec,
)

DEFAULT_SPECIALIST_MODEL = "anthropic:claude-sonnet-5-5"
MODEL_ALIASES = {"fast": "anthropic:claude-haiku-5-5"}
DEFAULT_USAGE_LIMITS = UsageLimits(request_limit=10)


@dataclass(frozen=True)
class WorkerContext:
    tenant_id: str
    conversation_id: str
    task_id: str
    report: Callable[[Progress], Awaitable[None]] | None = None  # None: progress is dropped

    async def progress(self, note: str) -> None:
        if self.report is not None:
            await self.report(Progress(note=note))

    def tool_call(self, worker: str, step: str) -> ToolCallContext:
        return ToolCallContext(
            tenant_id=self.tenant_id,
            conversation_id=self.conversation_id,
            task_id=self.task_id,
            worker=worker,
            step=step,
        )


async def run_guarded(work: Awaitable[object]) -> Outcome:
    """Await a worker and return a contract outcome whatever it did."""
    try:
        value = await work
    except ToolPermissionDenied as e:
        return Failed(reason=f"permission_denied: {e.ref}: {e.reason}")
    except ToolCallError as e:
        return Failed(reason=f"tool_error: {e.ref}: {type(e.__cause__).__name__}")
    except AgentRunError as e:
        return Failed(reason=f"model_error: {type(e).__name__}")
    except Exception as e:
        return Failed(reason=f"error: {type(e).__name__}")
    return to_outcome(value)


def resolve_model(ref: str | None) -> str:
    if ref is None:
        return DEFAULT_SPECIALIST_MODEL
    return MODEL_ALIASES.get(ref, ref)


# --- tool workers -------------------------------------------------------------


class ToolWorkerRunner:
    """Calls the worker's one tool with the delegated arguments; its output is the result."""

    def __init__(self, config: AgentConfig, name: str, backend: ToolBackend) -> None:
        worker = config.workers[name]
        if not isinstance(worker, ToolWorker):
            raise ValueError(f"{name!r} is not a tool worker")
        self.name = name
        self.worker = worker
        self.scope = ToolScope.for_worker(config, name)
        self.tools = ScopedTools(self.scope, backend)

    async def run(self, args: JsonObject, ctx: WorkerContext) -> Outcome:
        return await run_guarded(self._run(args, ctx))

    async def _run(self, args: JsonObject, ctx: WorkerContext) -> Outcome:
        limit = self.worker.timeout.total_seconds() if self.worker.timeout else None
        try:
            async with asyncio.timeout(limit):
                data = await self.tools.call(
                    self.worker.tool, args, ctx.tool_call(self.name, self.name)
                )
        except TimeoutError:
            return Failed(reason="timeout")
        return Result(data=data)


# --- llm workers --------------------------------------------------------------

_PREAMBLE = """\
You are a specialist worker behind a front agent. You never talk to the user; \
the front agent does. Use your tools, then end with exactly one output:
- finish: the data you found. Put exact values the reply must contain \
(amounts, dates, names) in must_say.
- ask_user: when you need something only the user can tell you.
- give_up: when you cannot do the task.
"""


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Finish(_Output):
    data: dict[str, JsonValue] = {}
    must_say: str | None = None


class _AskUser(_Output):
    field: str
    hint: str
    options: list[Option] | None = None


class _GiveUp(_Output):
    reason: str


_OUTPUTS: list[Any] = [
    ToolOutput(_Finish, name="finish", description="Return what you found."),
    ToolOutput(_AskUser, name="ask_user", description="Ask the user for one missing value."),
    ToolOutput(_GiveUp, name="give_up", description="Stop: the task cannot be done."),
]


def _to_contract(output: _Finish | _AskUser | _GiveUp) -> Outcome:
    match output:
        case _Finish():
            return Result(data=output.data, must_say=output.must_say)
        case _AskUser():
            return NeedsInput(
                field=output.field,
                hint=output.hint,
                schema={"type": "string"},
                options=output.options,
            )
        case _GiveUp():
            return Failed(reason=f"gave_up: {output.reason}")


@dataclass
class _RunState:
    ctx: WorkerContext
    calls: int  # tool calls made in this task so far; numbers the idempotency key


@dataclass(frozen=True)
class LlmRun:
    outcome: Outcome
    messages: list[ModelMessage] = field(default_factory=lambda: [])  # to resume after NeedsInput


class LlmWorkerRunner:
    """A Pydantic AI agent with the worker's permitted tools and the contract as its outputs."""

    def __init__(
        self,
        config: AgentConfig,
        name: str,
        backend: ToolBackend,
        *,
        documents: Mapping[str, str],  # instruction document text by name
        model: Model | str | None = None,  # overrides the worker's model
        usage_limits: UsageLimits = DEFAULT_USAGE_LIMITS,
    ) -> None:
        worker = config.workers[name]
        if not isinstance(worker, LlmWorker):
            raise ValueError(f"{name!r} is not an llm worker")
        instructions = worker.instructions
        if is_document_name(instructions):
            if instructions not in documents:
                raise ValueError(f"instructions document {instructions!r} was not supplied")
            instructions = documents[instructions]
        self.name = name
        self.scope = ToolScope.for_worker(config, name)
        self.tools = ScopedTools(self.scope, backend)
        self.usage_limits = usage_limits
        # Offer only what the scope allows now; every call is checked again when made.
        specs = [
            s for ref in self.scope.refs for s in backend.describe(ref) if self.scope.allows(s.ref)
        ]
        self._tool_names = {_tool_name(s.ref) for s in specs}
        self.agent = Agent[_RunState, Any](
            model or resolve_model(worker.model),
            output_type=_OUTPUTS,
            instructions=f"{_PREAMBLE}\n{instructions}",
            deps_type=_RunState,
            tools=[self._tool(s) for s in specs],
            defer_model_check=True,
        )

    async def run(self, args: JsonObject, ctx: WorkerContext) -> LlmRun:
        prompt = "Task arguments (JSON): " + json.dumps(
            args, ensure_ascii=False, separators=(",", ":")
        )
        return await self._run(prompt, [], ctx)

    async def resume(
        self, messages: Sequence[ModelMessage], field: str, value: JsonValue, ctx: WorkerContext
    ) -> LlmRun:
        """Continue after NeedsInput with the user's answer."""
        prompt = f"The user answered {field}: " + json.dumps(value, ensure_ascii=False)
        return await self._run(prompt, list(messages), ctx)

    async def _run(self, prompt: str, history: list[ModelMessage], ctx: WorkerContext) -> LlmRun:
        state = _RunState(ctx, calls=self._calls_in(history))
        messages = history

        async def work() -> Outcome:
            nonlocal messages
            result = await self.agent.run(
                prompt, deps=state, message_history=history, usage_limits=self.usage_limits
            )
            messages = result.all_messages()
            return _to_contract(result.output)

        return LlmRun(await run_guarded(work()), messages)

    def _calls_in(self, history: Sequence[ModelMessage]) -> int:
        return sum(
            isinstance(part, ToolReturnPart) and part.tool_name in self._tool_names
            for message in history
            if isinstance(message, ModelRequest)
            for part in message.parts
        )

    def _tool(self, spec: ToolSpec) -> Tool[_RunState]:
        async def call(rc: RunContext[_RunState], **args: JsonValue) -> JsonObject:
            rc.deps.calls += 1
            ctx = rc.deps.ctx.tool_call(self.name, f"{self.name}:{rc.deps.calls}")
            return await self.tools.call(spec.ref, args, ctx)

        return Tool[_RunState].from_schema(
            call,
            name=_tool_name(spec.ref),
            description=spec.description,
            json_schema=spec.input_schema,
            takes_ctx=True,
        )


def _tool_name(ref: str) -> str:
    # Model APIs allow [a-zA-Z0-9_-] in tool names; `crm.lookup` becomes `crm__lookup`.
    return ref.replace(".", "__")
