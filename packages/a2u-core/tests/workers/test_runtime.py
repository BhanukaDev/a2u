"""The `tool` and `llm` worker runtimes return only contract types."""

import asyncio
from collections.abc import Sequence

import pytest
from a2u_core.config import AgentConfig
from a2u_core.workers import (
    Failed,
    LlmWorkerRunner,
    NeedsInput,
    Option,
    Progress,
    Result,
    ToolWorkerRunner,
    WorkerContext,
    run_guarded,
)
from a2u_core.workers.fakes import FakeToolBackend
from pydantic_ai import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

CTX = WorkerContext(tenant_id="ws_1", conversation_id="c_1", task_id="t_1")
DOCS = {"products": "You answer product questions from the catalogue."}


class Script:
    """A FunctionModel that replies with the next scripted parts and records what it was offered."""

    def __init__(self, *turns: Sequence[TextPart | ToolCallPart]) -> None:
        self.turns = list(turns)
        self.offered: list[str] = []
        self.outputs: list[str] = []
        self.seen: list[list[ModelMessage]] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.offered = [t.name for t in info.function_tools]
        self.outputs = [t.name for t in info.output_tools]
        self.seen.append(list(messages))
        return ModelResponse(parts=list(self.turns.pop(0)))

    @property
    def model(self) -> FunctionModel:
        return FunctionModel(self)


def call(name: str, args: dict[str, object], call_id: str = "c") -> ToolCallPart:
    return ToolCallPart(tool_name=name, args=args, tool_call_id=call_id)


# --- run_guarded ------------------------------------------------------------


def test_guarded_passes_contract_types_and_converts_the_rest() -> None:
    async def ok() -> object:
        return Result(data={"x": 1})

    async def chatty() -> object:
        return "I booked it for you!"

    async def broken() -> object:
        raise RuntimeError("boom")

    assert asyncio.run(run_guarded(ok())) == Result(data={"x": 1})
    assert asyncio.run(run_guarded(chatty())) == Failed(reason="contract_violation: returned str")
    failed = asyncio.run(run_guarded(broken()))
    assert isinstance(failed, Failed)
    assert failed.reason == "error: RuntimeError"


def test_context_reports_progress() -> None:
    seen: list[Progress] = []

    async def report(p: Progress) -> None:
        seen.append(p)

    ctx = WorkerContext(tenant_id="ws_1", conversation_id="c_1", task_id="t_1", report=report)
    asyncio.run(ctx.progress("checking the calendar"))
    asyncio.run(CTX.progress("nobody listening"))  # no reporter: dropped
    assert seen == [Progress(note="checking the calendar")]


# --- tool workers -------------------------------------------------------------


def test_tool_worker_returns_the_tool_output_as_result(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    backend.replies = {"insurer_check": {"covered": True, "percent": 80}}
    runner = ToolWorkerRunner(config, "insurance", backend)
    outcome = asyncio.run(runner.run({"policy_no": "P-1"}, CTX))
    assert outcome == Result(data={"covered": True, "percent": 80})
    [(ref, args, ctx)] = backend.calls
    assert (ref, args) == ("insurer_check", {"policy_no": "P-1"})
    assert ctx.worker == "insurance"
    assert ctx.idempotency_key == "t_1:insurance"


def test_tool_worker_times_out(config: AgentConfig, backend: FakeToolBackend) -> None:
    backend.delay = 5  # the worker's timeout is 1s
    outcome = asyncio.run(ToolWorkerRunner(config, "insurance", backend).run({}, CTX))
    assert outcome == Failed(reason="timeout")


def test_tool_worker_tool_error_is_failed(config: AgentConfig, backend: FakeToolBackend) -> None:
    backend.replies = {"insurer_check": ConnectionError("refused")}
    outcome = asyncio.run(ToolWorkerRunner(config, "insurance", backend).run({}, CTX))
    assert outcome == Failed(reason="tool_error: insurer_check: ConnectionError")


def test_tool_worker_permission_denied(config: AgentConfig, backend: FakeToolBackend) -> None:
    config.tools["insurer_check"] = config.tools["issue_refund"]  # now side-effecting
    outcome = asyncio.run(ToolWorkerRunner(config, "insurance", backend).run({}, CTX))
    assert outcome == Failed(reason="permission_denied: insurer_check: needs_confirm")
    assert backend.calls == []


def test_runner_rejects_a_worker_of_another_kind(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    with pytest.raises(ValueError, match="not a tool worker"):
        ToolWorkerRunner(config, "products", backend)
    with pytest.raises(ValueError, match="not an llm worker"):
        LlmWorkerRunner(config, "insurance", backend, documents=DOCS)


# --- llm workers --------------------------------------------------------------


def llm(
    config: AgentConfig, backend: FakeToolBackend, script: Script, worker: str = "products"
) -> LlmWorkerRunner:
    return LlmWorkerRunner(config, worker, backend, documents=DOCS, model=script.model)


def test_llm_worker_calls_its_tool_and_finishes(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    backend.replies = {"search_catalog": {"items": [{"name": "Kettle", "price": 4500}]}}
    script = Script(
        [call("search_catalog", {"query": "kettle", "limit": 3})],
        [call("finish", {"data": {"price": 4500}, "must_say": "4,500 rupees"})],
    )
    run = asyncio.run(llm(config, backend, script).run({"question": "kettle price?"}, CTX))
    assert run.outcome == Result(data={"price": 4500}, must_say="4,500 rupees")
    [(ref, args, ctx)] = backend.calls
    assert (ref, args, ctx.idempotency_key) == (
        "search_catalog",
        {"query": "kettle", "limit": 3},
        "t_1:products:1",
    )
    # Only permitted tools are offered: crm.lookup_customer has unknown effects (D-032).
    assert script.offered == ["search_catalog"]
    assert sorted(script.outputs) == ["ask_user", "finish", "give_up"]


def test_llm_worker_gets_the_document_text_and_args(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    script = Script([call("finish", {"data": {}})])
    asyncio.run(llm(config, backend, script).run({"question": "hi"}, CTX))
    first = str(script.seen[0])
    assert DOCS["products"] in first
    assert '"question":"hi"' in first


def test_llm_worker_document_must_be_supplied(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    with pytest.raises(ValueError, match="document 'products'"):
        LlmWorkerRunner(config, "products", backend, documents={})
    # inline instructions need no document
    LlmWorkerRunner(config, "refunds", backend, documents={}, model=Script().model)


def test_llm_worker_can_ask_and_resume(config: AgentConfig, backend: FakeToolBackend) -> None:
    script = Script(
        [
            call(
                "ask_user",
                {
                    "field": "size",
                    "hint": "Which size?",
                    "options": [{"value": "s", "label": "Small"}, {"value": "l", "label": "Large"}],
                },
            )
        ],
        [call("finish", {"data": {"size": "l"}})],
    )
    runner = llm(config, backend, script)
    first = asyncio.run(runner.run({"question": "do you have it?"}, CTX))
    assert first.outcome == NeedsInput(
        field="size",
        hint="Which size?",
        schema={"type": "string"},
        options=[Option(value="s", label="Small"), Option(value="l", label="Large")],
    )
    second = asyncio.run(runner.resume(first.messages, "size", "l", CTX))
    assert second.outcome == Result(data={"size": "l"})
    assert "size" in str(script.seen[-1][-1])


def test_llm_worker_can_give_up(config: AgentConfig, backend: FakeToolBackend) -> None:
    script = Script([call("give_up", {"reason": "no such product"})])
    run = asyncio.run(llm(config, backend, script).run({}, CTX))
    assert run.outcome == Failed(reason="gave_up: no such product")


def test_llm_worker_text_reply_is_retried_then_failed(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    # Workers never talk: text is not an output. The model gets retries, then the worker fails.
    script = Script(*([TextPart("Sure! The kettle costs 4,500.")],) * 10)
    run = asyncio.run(llm(config, backend, script).run({}, CTX))
    assert run.outcome == Failed(reason="model_error: UnexpectedModelBehavior")


def test_llm_worker_cannot_set_say(config: AgentConfig, backend: FakeToolBackend) -> None:
    # `say` is for flows only (D-017); an LLM worker's extra field is a validation error.
    script = Script(
        *([call("finish", {"data": {}, "say": "Refund approved."})],) * 10,
    )
    run = asyncio.run(llm(config, backend, script).run({}, CTX))
    assert isinstance(run.outcome, Failed)


def test_llm_worker_denied_tool_fails_the_worker(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    # issue_refund is listed but under an approval policy: not offered, and refused if called.
    script = Script(*([call("issue_refund", {"order_id": "o1", "amount": 50})],) * 10)
    run = asyncio.run(llm(config, backend, script, worker="refunds").run({}, CTX))
    assert "issue_refund" not in script.offered
    assert isinstance(run.outcome, Failed)
    assert backend.calls == []


def test_llm_worker_permission_check_runs_at_call_time(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    # Offered when the run starts, then the policy tightens: the call is still checked.
    script = Script([call("search_catalog", {"query": "x", "limit": 1})])
    runner = llm(config, backend, script)
    runner.scope.effects = lambda ref: True  # pyright: ignore[reportUnknownLambdaType]
    run = asyncio.run(runner.run({}, CTX))
    assert run.outcome == Failed(reason="permission_denied: search_catalog: needs_confirm")
    assert backend.calls == []


def test_llm_worker_tool_error_fails_the_worker(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    backend.replies = {"search_catalog": TimeoutError()}
    script = Script([call("search_catalog", {"query": "x", "limit": 1})])
    run = asyncio.run(llm(config, backend, script).run({}, CTX))
    assert run.outcome == Failed(reason="tool_error: search_catalog: TimeoutError")


def test_idempotency_keys_keep_counting_after_resume(
    config: AgentConfig, backend: FakeToolBackend
) -> None:
    search: dict[str, object] = {"query": "kettle", "limit": 1}
    script = Script(
        [call("search_catalog", search, "a")],
        [call("ask_user", {"field": "colour", "hint": "Which colour?"})],
        [call("search_catalog", search, "b")],
        [call("finish", {"data": {}})],
    )
    runner = llm(config, backend, script)
    first = asyncio.run(runner.run({}, CTX))
    asyncio.run(runner.resume(first.messages, "colour", "red", CTX))
    keys = [ctx.idempotency_key for _, _, ctx in backend.calls]
    assert keys == ["t_1:products:1", "t_1:products:2"]
