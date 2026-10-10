"""The flow engine (architecture §4.4): interprets a flow worker's steps (spec §5.2).

A flow runs until it needs the user or ends. `start` and `resume` return a
`FlowRun` with a contract outcome and the `FlowState` to keep: a paused flow is
nothing but that state, which is JSON, so a task can store it and resume it
hours later from a WhatsApp reply (DBOS durability is WP-1.11).

Code decides everything here: answers are checked against types, conditions
and templates are evaluated by code, side-effecting calls go through
`ScopedTools` after `confirm` or an approval condition, and `result.say` is
rendered for the channel and sent verbatim (D-017). Rules the spec left open
are recorded in D-033.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a2u_core.conditions import MISSING, Condition, ConditionError, parse_condition, select_path
from a2u_core.config.checks import MAX_IF_DEPTH
from a2u_core.config.models import (
    AgentConfig,
    CallToolStep,
    ChooseStep,
    ChooseType,
    CollectStep,
    ConfirmStep,
    DelegateStep,
    FlowWorker,
    HandoffStep,
    IfStep,
    PlainType,
    ResultStep,
    Step,
    ValueType,
    Verify,
    VerifyStep,
)
from a2u_core.flows.inputs import (
    BasicCapture,
    Capture,
    Check,
    Invalid,
    Valid,
    check_plain,
    input_schema,
    parse_yes_no,
)
from a2u_core.flows.render import Channel, RenderError, apply_filter, render_json, render_text
from a2u_core.workers.contract import Failed, NeedsInput, Option, Outcome, Result
from a2u_core.workers.runtime import WorkerContext, run_guarded
from a2u_core.workers.tools import JsonObject, ScopedTools, ToolBackend, ToolScope

DEFAULT_MAX_OPTIONS = 3  # for `collect: { x: choose(...) }`; `choose` steps set max_options
MAX_FORMAT_FAILURES = 5  # format failures do not use up retries (D-026), but they are capped
MAX_DELEGATE_DEPTH = 3

DECLINED_MESSAGE = "Okay, I won't go ahead with that."
TIMEOUT_MESSAGE = "Sorry, that took too long, so I've stopped. We can start again any time."
INVALID_MESSAGE = "Sorry, I couldn't get that right. Let me pass you to someone who can help."
VERIFY_FAILED_MESSAGE = "Sorry, I couldn't verify your identity."
NO_OPTIONS_MESSAGE = "Sorry, there's nothing available to choose from right now."

_YES_NO = [Option(value="yes", label="Yes"), Option(value="no", label="No")]

Cursor = list[int]  # step index, then (branch, index) pairs into `if` steps; branch 0 = then


# --- Hooks the engine calls ---------------------------------------------------


class Verifier(Protocol):
    """Identity checks for `verify`: an OTP over WhatsApp, or facts the customer knows."""

    async def send_otp(self, ctx: WorkerContext) -> None: ...

    async def check_otp(self, code: str, ctx: WorkerContext) -> bool: ...

    async def check_fact(self, name: str, value: JsonValue, ctx: WorkerContext) -> bool: ...


class Delegator(Protocol):
    """Runs another worker for `delegate`. `key` names the step within this task."""

    async def run(
        self, worker: str, args: JsonObject, ctx: WorkerContext, *, key: str, depth: int
    ) -> Outcome: ...

    async def resume(
        self, worker: str, answer: JsonValue, ctx: WorkerContext, *, key: str
    ) -> Outcome: ...

    async def start(
        self, worker: str, args: JsonObject, ctx: WorkerContext, *, key: str, depth: int
    ) -> str:
        """Start the worker as a background task; return its task ID."""
        ...


# --- State --------------------------------------------------------------------


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pending(_State):
    """The question a paused flow is waiting on."""

    kind: Literal["collect", "choose", "confirm", "verify", "delegate"]
    request: NeedsInput  # as sent; a re-ask updates its hint
    hint: str  # the step's own hint, before any re-ask note
    deadline: datetime | None = None
    choices: dict[str, JsonValue] | None = None  # option value -> the item it binds
    fact: int = 0  # verify knowledge: which fact is being asked


class FlowState(_State):
    worker: str
    channel: Channel
    args: dict[str, JsonValue] = Field(default_factory=lambda: {})
    customer: dict[str, JsonValue] = Field(default_factory=lambda: {})
    cursor: Cursor = Field(default_factory=lambda: [0])
    bindings: dict[str, JsonValue] = Field(default_factory=lambda: {})
    bound_at: dict[str, Cursor] = Field(default_factory=lambda: {})
    confirmed_at: list[Cursor] = Field(default_factory=lambda: [])
    attempts: dict[str, int] = Field(default_factory=lambda: {})  # step key -> answers refused
    format_failures: dict[str, int] = Field(default_factory=lambda: {})
    visits: dict[str, int] = Field(default_factory=lambda: {})  # step name -> times reached
    tool_calls: int = 0  # numbers idempotency keys
    last_result: dict[str, JsonValue] | None = None
    verified: bool = False
    pending: Pending | None = None
    handoff: str | None = None  # "human" or a worker, when the flow ended with handoff
    done: bool = False

    def env(self) -> dict[str, Any]:
        return {**self.bindings, "customer": self.customer, "args": self.args}


class FlowRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: Outcome
    state: FlowState

    @property
    def handoff(self) -> str | None:
        """Set when the flow ended with `handoff`; the turn loop (WP-1.7) performs it."""
        return self.state.handoff


class _Stop(Exception):  # noqa: N818 - ends the flow with Failed; not an error in the engine
    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(reason)
        self.failed = (
            Failed(reason=reason)
            if message is None
            else Failed(reason=reason, user_safe_message=message)
        )


def _key(cursor: Sequence[int]) -> str:
    return ".".join(map(str, cursor))


def _cursor(key: str) -> Cursor:
    return [int(part) for part in key.split(".")]


def step_names(step: Step) -> list[str]:
    """The names a step answers to in evals (`expect.step`, `step_reached`), as in the loader."""
    match step:
        case CollectStep(collect=c):
            return ["collect", c.name]
        case ChooseStep(choose=c):
            return ["choose", c.name]
        case CallToolStep(call_tool=c):
            return ["call_tool", *([c.as_] if c.as_ else [])]
        case DelegateStep(delegate=d):
            return ["delegate", *([d.as_] if d.as_ else [])]
        case ConfirmStep():
            return ["confirm"]
        case VerifyStep():
            return ["verify"]
        case HandoffStep():
            return ["handoff"]
        case IfStep():
            return ["if"]
        case ResultStep():
            return ["result"]


def _walk(steps: Sequence[Step], depth: int = 0) -> list[tuple[Step, int]]:
    out: list[tuple[Step, int]] = []
    for step in steps:
        out.append((step, depth))
        if isinstance(step, IfStep):
            out += _walk(step.then, depth + 1) + _walk(step.else_, depth + 1)
    return out


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Engine -------------------------------------------------------------------


class FlowEngine:
    """Runs one flow worker. Holds no run state: everything lives in `FlowState`."""

    def __init__(
        self,
        config: AgentConfig,
        name: str,
        backend: ToolBackend,
        *,
        verifier: Verifier | None = None,
        delegator: Delegator | None = None,
        capture: Capture | None = None,
        effects: Callable[[str], bool | None] | None = None,
        clock: Callable[[], datetime] = _utcnow,
        depth: int = 0,  # delegation depth: 0 for a flow the front agent started
    ) -> None:
        worker = config.workers[name]
        if not isinstance(worker, FlowWorker):
            raise ValueError(f"{name!r} is not a flow worker")
        self.config = config
        self.name = name
        self.worker = worker
        self.verifier = verifier
        self.delegator = delegator
        self.capture = capture or BasicCapture()
        self.clock = clock
        self.depth = depth
        self.conditions: dict[str, Condition] = {}
        refs: list[str] = []
        for step, nesting in _walk(worker.steps):
            match step:
                case IfStep() if nesting >= MAX_IF_DEPTH:
                    raise ValueError(f"{name}: if nesting depth is at most {MAX_IF_DEPTH}")
                case IfStep(condition=text):
                    self.conditions[text] = parse_condition(text)
                case CallToolStep(call_tool=call):
                    refs.append(call.name)
                case CollectStep(collect=c) if isinstance(c.type, ChooseType):
                    refs.append(c.type.source)
                case ChooseStep(choose=c) if isinstance(c.type, ChooseType):
                    refs.append(c.type.source)
                case _:
                    pass
        self.tools = ScopedTools(ToolScope(config, refs, effects=effects), backend)

    # --- Public API -----------------------------------------------------------

    async def start(
        self,
        ctx: WorkerContext,
        *,
        channel: Channel,
        args: JsonObject | None = None,
        customer: JsonObject | None = None,
    ) -> FlowRun:
        state = FlowState(
            worker=self.name, channel=channel, args=args or {}, customer=customer or {}
        )
        return await self._drive(state, ctx, None)

    async def resume(self, state: FlowState, answer: JsonValue, ctx: WorkerContext) -> FlowRun:
        """Continue a paused flow with the user's answer to `state.pending`."""
        if state.done or state.pending is None:
            raise ValueError("this flow is not waiting for input")
        return await self._drive(state.model_copy(deep=True), ctx, answer)

    async def expire(self, state: FlowState) -> FlowRun:
        """The task layer's timer fired: fail with `timeout` if the deadline has passed."""
        if state.done or state.pending is None:
            raise ValueError("this flow is not waiting for input")
        state = state.model_copy(deep=True)
        pending = cast(Pending, state.pending)
        if pending.deadline is not None and self.clock() >= pending.deadline:
            return self._finish(state, _Stop("timeout", TIMEOUT_MESSAGE).failed)
        return FlowRun(outcome=pending.request, state=state)

    # --- Driving --------------------------------------------------------------

    async def _drive(
        self, state: FlowState, ctx: WorkerContext, answer: JsonValue | None
    ) -> FlowRun:
        async def work() -> Outcome:
            try:
                if state.pending is not None:
                    outcome = await self._answer(state, state.pending, answer, ctx)
                    if outcome is not None:
                        return outcome
                while (step := self._step_at(state.cursor)) is not None:
                    outcome = await self._step(step, state, ctx)
                    if outcome is not None:
                        return outcome
                return Result(data=state.last_result or {})  # ran out of steps
            except _Stop as stop:
                return stop.failed
            except RenderError as e:
                return Failed(reason=f"render_error: {e}")
            except ConditionError as e:
                return Failed(reason=f"condition_error: {e}")

        return self._finish(state, await run_guarded(work()))

    def _finish(self, state: FlowState, outcome: Outcome) -> FlowRun:
        if not isinstance(outcome, NeedsInput):
            state.pending = None
            state.done = True
        return FlowRun(outcome=outcome, state=state)

    # --- Cursor ---------------------------------------------------------------

    def _list_at(self, cursor: Sequence[int]) -> list[Step]:
        """The step list that the last index of `cursor` points into."""
        steps = self.worker.steps
        for i in range(0, len(cursor) - 1, 2):
            branch = cast(IfStep, steps[cursor[i]])
            steps = branch.then if cursor[i + 1] == 0 else branch.else_
        return steps

    def _step_at(self, cursor: Sequence[int]) -> Step | None:
        steps = self._list_at(cursor)
        return steps[cursor[-1]] if cursor[-1] < len(steps) else None

    def _advance(self, state: FlowState) -> None:
        cursor = state.cursor
        cursor[-1] += 1
        while len(cursor) > 1 and cursor[-1] >= len(self._list_at(cursor)):
            del cursor[-2:]
            cursor[-1] += 1

    def _rewind(self, state: FlowState, to: Cursor) -> None:
        """Go back to an earlier step and forget everything decided from there on."""
        state.bindings = {k: v for k, v in state.bindings.items() if state.bound_at[k] < to}
        state.bound_at = {k: at for k, at in state.bound_at.items() if at < to}
        state.confirmed_at = [at for at in state.confirmed_at if at < to]
        state.attempts = {k: n for k, n in state.attempts.items() if _cursor(k) < to}
        state.format_failures = {k: n for k, n in state.format_failures.items() if _cursor(k) < to}
        state.cursor = list(to)
        state.pending = None

    def _bind(self, state: FlowState, name: str, value: JsonValue) -> None:
        state.bindings[name] = value
        state.bound_at[name] = list(state.cursor)

    # --- Steps ----------------------------------------------------------------

    async def _step(self, step: Step, state: FlowState, ctx: WorkerContext) -> Outcome | None:
        """Run a step from the start. None: carry on with the next step."""
        for name in step_names(step):
            state.visits[name] = state.visits.get(name, 0) + 1
        env = state.env()
        match step:
            case CollectStep(collect=c) if isinstance(c.type, ChooseType):
                return await self._offer(state, ctx, step, "collect", c.name, c.type)
            case CollectStep(collect=c):
                hint = f"Ask the user for {c.name}."
                request = NeedsInput(field=c.name, hint=hint, schema=input_schema(c.type))
                return self._ask(state, step, Pending(kind="collect", request=request, hint=hint))
            case ChooseStep(choose=c):
                return await self._offer(state, ctx, step, "choose", c.name, c.type)
            case ConfirmStep(confirm=c):
                text = render_text(c.text, env, state.channel)
                request = NeedsInput(
                    field="confirm", hint=text, schema={"type": "boolean"}, options=_YES_NO
                )
                return self._ask(state, step, Pending(kind="confirm", request=request, hint=text))
            case VerifyStep(verify=v):
                if self.verifier is None:
                    raise _Stop("verify_unavailable")
                if v == "otp":
                    await self.verifier.send_otp(ctx)
                return self._verify_ask(state, step, v, 0)
            case CallToolStep(call_tool=call):
                args = self._object(render_json(call.args, env, state.channel), "call_tool args")
                data = await self._call(state, ctx, call.name, args)
                state.last_result = data
                if call.as_ is not None:
                    self._bind(state, call.as_, data)
            case DelegateStep(delegate=d):
                if self.delegator is None:
                    raise _Stop("delegate_unavailable")
                if self.depth >= MAX_DELEGATE_DEPTH:
                    raise _Stop("delegate_too_deep")
                args = self._object(render_json(d.args, env, state.channel), "delegate args")
                key, depth = _key(state.cursor), self.depth + 1
                if not d.background:
                    outcome = await self.delegator.run(d.worker, args, ctx, key=key, depth=depth)
                    return self._delegated(state, d.worker, d.as_, outcome)
                task_id = await self.delegator.start(d.worker, args, ctx, key=key, depth=depth)
                if d.as_ is not None:
                    self._bind(state, d.as_, {"task_id": task_id})
            case HandoffStep(handoff=target):
                state.handoff = target
                return Result(data=state.last_result or {})
            case IfStep(condition=text, then=then, else_=else_):
                holds = self.conditions[text].evaluate(env)
                if then if holds else else_:
                    state.cursor += [0 if holds else 1, 0]
                    return None
            case ResultStep(result=r):
                data = state.last_result or {}
                if r.data is not None:
                    data = self._object(render_json(r.data, env, state.channel), "result data")
                say = render_text(r.say, env, state.channel) if r.say is not None else None
                return Result(data=data, say=say)
        self._advance(state)
        return None

    async def _answer(
        self, state: FlowState, pending: Pending, answer: JsonValue, ctx: WorkerContext
    ) -> Outcome | None:
        """Take the answer to the pending question. None: accepted, carry on."""
        if pending.deadline is not None and self.clock() >= pending.deadline:
            raise _Stop("timeout", TIMEOUT_MESSAGE)
        step = self._step_at(state.cursor)
        match step:
            case CollectStep(collect=c):
                check = (
                    self._pick(pending, answer) if pending.choices else self._check(c.type, answer)
                )
                return self._accept(state, step, pending, check, c.name, c.retries)
            case ChooseStep(choose=c):
                return self._accept(state, step, pending, self._pick(pending, answer), c.name)
            case ConfirmStep(confirm=c):
                yes = parse_yes_no(answer)
                if yes is None:
                    return self._reask(state, step, pending, Invalid("expected yes or no"))
                if yes:
                    state.confirmed_at.append(list(state.cursor))
                elif c.on_no is None:
                    raise _Stop("declined", DECLINED_MESSAGE)
                else:
                    self._rewind(state, state.bound_at[c.on_no])
                    return None
            case VerifyStep(verify=v):
                if not await self._verified(v, pending, answer, ctx):
                    return self._reask(
                        state,
                        step,
                        pending,
                        Invalid("that did not match"),
                        failure=("verification_failed", VERIFY_FAILED_MESSAGE),
                    )
                if v != "otp" and pending.fact + 1 < len(v.knowledge):
                    return self._verify_ask(state, step, v, pending.fact + 1)
                state.verified = True
            case DelegateStep(delegate=d):
                assert self.delegator is not None
                outcome = await self.delegator.resume(d.worker, answer, ctx, key=_key(state.cursor))
                return self._delegated(state, d.worker, d.as_, outcome)
            case _:
                raise ValueError(f"no step waiting for input at {_key(state.cursor)}")
        state.pending = None
        self._advance(state)
        return None

    # --- Asking ---------------------------------------------------------------

    def _ask(self, state: FlowState, step: Step, pending: Pending) -> NeedsInput:
        timeout = self.config.policies.collect.timeout
        if isinstance(step, CollectStep) and step.collect.timeout is not None:
            timeout = step.collect.timeout
        pending.deadline = self.clock() + timeout
        state.pending = pending
        return pending.request

    def _accept(
        self,
        state: FlowState,
        step: Step,
        pending: Pending,
        check: Check,
        name: str,
        retries: int | None = None,
    ) -> NeedsInput | None:
        if isinstance(check, Invalid):
            return self._reask(state, step, pending, check, retries=retries)
        self._bind(state, name, check.value)
        state.pending = None
        self._advance(state)
        return None

    def _reask(
        self,
        state: FlowState,
        step: Step,
        pending: Pending,
        invalid: Invalid,
        *,
        retries: int | None = None,
        failure: tuple[str, str] = ("invalid_input", INVALID_MESSAGE),
    ) -> NeedsInput:
        """Ask again, or fail once the retries are used up (`retries` re-asks, then fail)."""
        key = _key(state.cursor)
        if invalid.uses_retry:
            state.attempts[key] = state.attempts.get(key, 0) + 1
            limit = self.config.policies.collect.retries if retries is None else retries
            if state.attempts[key] > limit:
                raise _Stop(*failure)
        else:
            state.format_failures[key] = state.format_failures.get(key, 0) + 1
            if state.format_failures[key] > MAX_FORMAT_FAILURES:
                raise _Stop(*failure)
        hint = f"{pending.hint} The last answer was not accepted: {invalid.reason}."
        pending.request = pending.request.model_copy(update={"hint": hint})
        return self._ask(state, step, pending)

    def _check(self, t: ValueType, answer: JsonValue) -> Check:
        if isinstance(t, PlainType):
            return check_plain(t, answer)
        if isinstance(t, ChooseType):
            raise AssertionError("choose answers are picked from options")
        return self.capture.check(t, answer)

    def _pick(self, pending: Pending, answer: JsonValue) -> Check:
        choices = pending.choices or {}
        if isinstance(answer, str) and answer in choices:
            return Valid(choices[answer])
        return Invalid(f"expected one of the options: {', '.join(choices)}")

    async def _offer(
        self,
        state: FlowState,
        ctx: WorkerContext,
        step: Step,
        kind: Literal["collect", "choose"],
        name: str,
        source: ChooseType | list[str],
    ) -> NeedsInput:
        limit = step.choose.max_options if isinstance(step, ChooseStep) else DEFAULT_MAX_OPTIONS
        items = (await self._items(state, ctx, source))[:limit]
        if not items:
            raise _Stop("no_options", NO_OPTIONS_MESSAGE)
        options, choices = self._options(items, state.channel)
        hint = f"Ask the user to choose {name} from the options."
        request = NeedsInput(
            field=name,
            hint=hint,
            schema={"type": "string", "enum": list(choices)},
            options=options,
        )
        return self._ask(
            state, step, Pending(kind=kind, request=request, hint=hint, choices=choices)
        )

    async def _items(
        self, state: FlowState, ctx: WorkerContext, source: ChooseType | list[str]
    ) -> list[JsonValue]:
        """The options to offer: a literal list, a bound list, or the `items` a tool returns."""
        if isinstance(source, list):
            return list(source)
        env = state.env()
        value: Any
        if source.source.split(".")[0] in env:
            value = self._lookup(env, source.source)
        else:
            args = {k: self._lookup(env, ref) for k, ref in source.args.items()}
            value = await self._call(state, ctx, source.source, args)
        if isinstance(value, dict) and "items" in value:
            value = cast(JsonObject, value)["items"]
        if not isinstance(value, list):
            raise _Stop(f"choose_source: {source.source} did not give a list of items")
        return cast(list[JsonValue], value)

    def _options(
        self, items: list[JsonValue], channel: Channel
    ) -> tuple[list[Option], dict[str, JsonValue]]:
        values: list[str] = []
        labels: list[str] = []
        for item in items:
            if isinstance(item, dict):
                fields = cast(JsonObject, item)
                ident, label = fields.get("id"), fields.get("label", fields.get("name"))
                value = str(ident) if isinstance(ident, str | int) else ""
                labels.append(label if isinstance(label, str) else value)
            elif isinstance(item, str | int | float) and not isinstance(item, bool):
                value = str(item)
                labels.append(self._label(value, channel))
            else:
                raise _Stop("choose_source: options must be values or objects")
            values.append(value)
        if "" in values or len(set(values)) < len(values):
            values = [str(i + 1) for i in range(len(items))]  # no usable IDs: number them
        labels = [label or value for label, value in zip(labels, values, strict=True)]
        options = [Option(value=v, label=text) for v, text in zip(values, labels, strict=True)]
        return options, dict(zip(values, items, strict=True))

    def _label(self, value: str, channel: Channel) -> str:
        """Dates and times are labelled as they will be read; anything else as it is."""
        try:
            return apply_filter("date_spoken", value, channel)
        except RenderError:
            return value

    def _verify_ask(self, state: FlowState, step: Step, verify: Verify, fact: int) -> NeedsInput:
        if verify == "otp":
            field, hint = "otp", "Ask for the code we just sent on WhatsApp."
        else:
            field = verify.knowledge[fact]
            hint = f"To confirm who the user is, ask for their {field}."
        request = NeedsInput(field=field, hint=hint, schema={"type": "string"})
        return self._ask(state, step, Pending(kind="verify", request=request, hint=hint, fact=fact))

    async def _verified(
        self, verify: Verify, pending: Pending, answer: JsonValue, ctx: WorkerContext
    ) -> bool:
        assert self.verifier is not None
        if verify != "otp":
            return await self.verifier.check_fact(verify.knowledge[pending.fact], answer, ctx)
        if isinstance(answer, str | int) and not isinstance(answer, bool):
            return await self.verifier.check_otp(str(answer).strip(), ctx)
        return False

    # --- Tools and delegation -------------------------------------------------

    async def _call(
        self, state: FlowState, ctx: WorkerContext, ref: str, args: JsonObject
    ) -> JsonObject:
        state.tool_calls += 1
        tool_ctx = ctx.tool_call(self.name, f"{self.name}:{state.tool_calls}")
        return await self.tools.call(ref, args, tool_ctx, confirmed=bool(state.confirmed_at))

    def _delegated(
        self, state: FlowState, worker: str, as_: str | None, outcome: Outcome
    ) -> Outcome | None:
        match outcome:
            case NeedsInput():
                state.pending = Pending(kind="delegate", request=outcome, hint=outcome.hint)
                return outcome
            case Failed():
                raise _Stop(
                    f"delegate_failed: {worker}: {outcome.reason}", outcome.user_safe_message
                )
            case Result():
                state.last_result = outcome.data
                if as_ is not None:
                    self._bind(state, as_, outcome.data)
                state.pending = None
                self._advance(state)
                return None

    # --- Values ---------------------------------------------------------------

    def _lookup(self, env: dict[str, Any], ref: str) -> Any:
        value = select_path(env, ref.split("."))
        if value is MISSING:
            raise RenderError(f"{ref} is not bound")
        return value

    def _object(self, value: JsonValue, what: str) -> JsonObject:
        if not isinstance(value, dict):
            raise _Stop(f"render_error: {what} must be an object")
        return value


__all__ = [
    "DEFAULT_MAX_OPTIONS",
    "MAX_DELEGATE_DEPTH",
    "MAX_FORMAT_FAILURES",
    "Delegator",
    "FlowEngine",
    "FlowRun",
    "FlowState",
    "Pending",
    "Verifier",
    "step_names",
]
