"""Rules that span objects: references, flow scopes, channels, languages, documents.

Run on a config whose shape is already valid. Each problem carries a path into
the original document so the loader can attach a line to it.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import JsonValue

from a2u_core.conditions import ConditionSyntaxError, parse_condition
from a2u_core.config.models import (
    FLOW_ROOTS,
    REFERENCE,
    VOICE_CHANNELS,
    AgentConfig,
    CallToolStep,
    ChooseStep,
    ChooseType,
    CollectStep,
    ConfirmStep,
    DelegateStep,
    ExpectLine,
    FlowWorker,
    HandoffStep,
    IfStep,
    InboundTrigger,
    LatencyP95,
    LlmWorker,
    McpTool,
    NotSaid,
    ResultStep,
    Said,
    Step,
    StepReached,
    Tool,
    ToolCalled,
    ToolWorker,
    ValueType,
    VerifyStep,
    WatchFlagged,
    WebhookTool,
    WebhookTrigger,
    is_document_name,
    is_url,
)
from a2u_core.config.source import Path

FILTERS = ("date_spoken", "time_spoken", "money", "digits_spoken")
MAX_IF_DEPTH = 2
_TEMPLATE = re.compile(r"\$\{([^}]*)\}")


@dataclass(frozen=True)
class Platform:
    """What the platform has enabled. None skips that gate (e.g. offline linting)."""

    voice_languages: frozenset[str] | None = None  # the M0 speech gate (D-019)
    text_languages: frozenset[str] | None = None


@dataclass(frozen=True)
class Document:
    name: str
    kind: Literal["persona", "knowledge"]
    version: int


@dataclass(frozen=True)
class Problem:
    path: Path
    message: str


@dataclass
class _Scope:
    bound: set[str] = field(default_factory=lambda: set[str]())
    collects: set[str] = field(default_factory=lambda: set[str]())
    confirmed: bool = False

    def copy(self) -> "_Scope":
        return _Scope(set(self.bound), set(self.collects), self.confirmed)


def check(
    config: AgentConfig,
    documents: Mapping[str, Document] | None,
    platform: Platform,
) -> tuple[list[Problem], dict[str, Document]]:
    checker = _Checker(config, documents, platform)
    checker.run()
    return checker.problems, checker.used


class _Checker:
    def __init__(
        self,
        config: AgentConfig,
        documents: Mapping[str, Document] | None,
        platform: Platform,
    ) -> None:
        self.c = config
        self.documents = documents
        self.platform = platform
        self.problems: list[Problem] = []
        self.used: dict[str, Document] = {}
        self.delegates: list[tuple[str, str, Path]] = []
        self.step_names: dict[str, set[str]] = {}

    def error(self, path: Path, message: str) -> None:
        self.problems.append(Problem(path, message))

    def run(self) -> None:
        self.languages()
        self.front()
        for name, worker in self.c.workers.items():
            self.worker(name, worker)
        self.delegate_cycles()
        self.router()
        self.triggers()
        self.policies()
        self.evals()
        self.knowledge_documents()

    # --- References -------------------------------------------------------

    def tool(self, ref: str, path: Path) -> Tool | None:
        base, _, op = ref.partition(".")
        tool = self.c.tools.get(base)
        if tool is None:
            self.error(path, f"unknown tool {base!r}")
            return None
        if op and isinstance(tool, WebhookTool):
            self.error(path, f"webhook tool {base!r} has no operations; call it as {base!r}")
        elif op and isinstance(tool, McpTool) and op not in tool.allow:
            self.error(path, f"{op!r} is not in {base}.allow")
        return tool

    def has_side_effects(self, tool: Tool) -> bool:
        # Connector and MCP operations declare effects in the connector catalog (WP-1.10).
        return isinstance(tool, WebhookTool) and tool.side_effects

    def approved(self, ref: str) -> bool:
        approval = self.c.policies.approval
        return ref in approval or ref.partition(".")[0] in approval

    def knowledge_base(self, name: str, path: Path) -> None:
        if name not in self.c.knowledge:
            self.error(path, f"unknown knowledge base {name!r}")

    def worker_ref(self, name: str, path: Path) -> bool:
        if name not in self.c.workers:
            self.error(path, f"unknown worker {name!r}")
            return False
        return True

    def document(self, name: str, kind: Literal["persona", "knowledge"], path: Path) -> None:
        if self.documents is None:
            return
        doc = self.documents.get(name)
        if doc is None:
            self.error(path, f"unknown document {name!r}")
        elif doc.kind != kind:
            self.error(path, f"document {name!r} is a {doc.kind} document, not a {kind}")
        else:
            self.used[name] = doc

    def template(self, text: str, path: Path, roots: set[str] | frozenset[str]) -> None:
        for m in _TEMPLATE.finditer(text):
            expr, _, name = (part.strip() for part in m[1].partition("|"))
            if not REFERENCE.fullmatch(expr):
                self.error(path, f"{m[0]!r} is not a reference such as ${{order.id}}")
                continue
            if expr.split(".")[0] not in roots:
                self.error(path, f"'${{{expr}}}' is not bound here")
            if name and name not in FILTERS:
                self.error(path, f"unknown filter {name!r}; expected one of {', '.join(FILTERS)}")

    def condition(self, text: str, path: Path, roots: set[str] | None = None) -> None:
        """Parse a condition; with `roots`, also check that every name it reads is bound."""
        try:
            parsed = parse_condition(text)
        except ConditionSyntaxError as e:
            self.error(path, f"invalid condition: {e}")
            return
        for name in sorted(parsed.references - (roots if roots is not None else parsed.references)):
            self.error(path, f"{name!r} is not bound here")

    def json_templates(self, value: JsonValue, path: Path, roots: set[str]) -> None:
        if isinstance(value, str):
            self.template(value, path, roots)
        elif isinstance(value, dict):
            for key, child in value.items():
                self.json_templates(child, (*path, key), roots)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                self.json_templates(child, (*path, i), roots)

    # --- Languages and channels (§1.1, D-019) -------------------------------

    def languages(self) -> None:
        langs, channels, voice = self.c.languages, self.c.channels, self.c.front.voice
        if langs.default not in langs.voice and langs.default not in langs.text:
            self.error(
                ("languages", "default"), f"{langs.default!r} is not enabled for voice or text"
            )

        voice_on = [
            n
            for n, on in (("web", channels.web and channels.web.voice), ("phone", channels.phone))
            if on
        ]
        text_on = [
            n
            for n, on in (
                ("web chat", channels.web and channels.web.chat),
                ("whatsapp", channels.whatsapp),
            )
            if on
        ]
        if voice_on and not langs.voice:
            self.error(
                ("languages", "voice"),
                f"voice is enabled ({', '.join(voice_on)}) but languages.voice is empty",
            )
        if voice_on and voice is None:
            self.error(
                ("front", "voice"),
                f"voice is enabled ({', '.join(voice_on)}) but front.voice is not configured",
            )
        if text_on and not langs.text:
            self.error(
                ("languages", "text"),
                f"text is enabled ({', '.join(text_on)}) but languages.text is empty",
            )

        voices = voice.voices if voice else {}
        needs_voice = voice is not None and voice.mode == "cascaded"
        for i, lang in enumerate(langs.voice):
            gate = self.platform.voice_languages
            if gate is not None and lang not in gate:
                self.error(
                    ("languages", "voice", i), f"{lang!r} is not enabled for voice on this platform"
                )
            elif needs_voice and lang not in voices:
                self.error(
                    ("languages", "voice", i),
                    f"voice language {lang!r} has no voice in front.voice.voices",
                )
        for i, lang in enumerate(langs.text):
            gate = self.platform.text_languages
            if gate is not None and lang not in gate:
                self.error(
                    ("languages", "text", i), f"{lang!r} is not enabled for text on this platform"
                )
        for lang in voices:
            if lang not in langs.voice:
                self.error(
                    ("front", "voice", "voices", lang), f"{lang!r} is not in languages.voice"
                )

    # --- Front agent (§2) -------------------------------------------------

    def front(self) -> None:
        front = self.c.front
        configured = self.c.channels.profile_channels()
        for channel in front.profiles:
            if channel not in configured:
                self.error(("front", "profiles", channel), f"channel {channel!r} is not configured")
        for i, name in enumerate(front.knowledge):
            self.knowledge_base(name, ("front", "knowledge", i))
        if front.persona is not None:
            self.document(front.persona, "persona", ("front", "persona"))

    # --- Workers and flows (§5) -------------------------------------------

    def worker(self, name: str, worker: LlmWorker | ToolWorker | FlowWorker) -> None:
        path: Path = ("workers", name)
        if self.c.router.mode != "handoff" and not worker.description:
            self.error((*path, "description"), "classifier routing needs a description")
        if isinstance(worker, LlmWorker):
            for i, ref in enumerate(worker.tools):
                tool = self.tool(ref, (*path, "tools", i))
                if tool and self.has_side_effects(tool) and not self.approved(ref):
                    self.error(
                        (*path, "tools", i), f"side-effecting tool {ref!r} needs an approval policy"
                    )
            for i, kb in enumerate(worker.knowledge):
                self.knowledge_base(kb, (*path, "knowledge", i))
            if is_document_name(worker.instructions):
                self.document(worker.instructions, "persona", (*path, "instructions"))
        elif isinstance(worker, ToolWorker):
            tool = self.tool(worker.tool, (*path, "tool"))
            if tool and self.has_side_effects(tool) and not self.approved(worker.tool):
                self.error(
                    (*path, "tool"), f"side-effecting tool {worker.tool!r} needs an approval policy"
                )
        else:
            self.step_names[name] = set()
            self.steps(name, worker.steps, (*path, "steps"), _Scope(), 0)

    def bind(self, name: str, path: Path, scope: _Scope) -> None:
        if name in scope.bound:
            self.error(path, f"{name!r} is already bound")
        scope.bound.add(name)

    def value_type(self, value: ValueType | list[str], path: Path, scope: _Scope) -> None:
        if not isinstance(value, ChooseType):
            return
        if value.source.split(".")[0] not in scope.bound:
            self.tool(value.source, path)
        for ref in value.args.values():
            if ref.split(".")[0] not in scope.bound | FLOW_ROOTS:
                self.error(path, f"{ref!r} is not bound here")

    def steps(
        self, worker: str, steps: list[Step], path: Path, scope: _Scope, depth: int
    ) -> tuple[_Scope, bool]:
        """Walk steps in order; return the scope after them and whether they end the flow."""
        names = self.step_names[worker]
        for i, step in enumerate(steps):
            at: Path = (*path, i)
            roots = scope.bound | FLOW_ROOTS
            match step:
                case CollectStep(collect=collect):
                    names.update(("collect", collect.name))
                    self.value_type(collect.type, (*at, "collect", collect.name), scope)
                    self.bind(collect.name, (*at, "collect", collect.name), scope)
                    scope.collects.add(collect.name)
                case ChooseStep(choose=choose):
                    names.update(("choose", choose.name))
                    self.value_type(choose.type, (*at, "choose", choose.name), scope)
                    self.bind(choose.name, (*at, "choose", choose.name), scope)
                case ConfirmStep(confirm=confirm):
                    names.add("confirm")
                    self.template(confirm.text, (*at, "confirm", "text"), roots)
                    if confirm.on_no is not None and confirm.on_no not in scope.collects:
                        self.error(
                            (*at, "confirm", "on_no"),
                            f"on_no must name an earlier collect step, not {confirm.on_no!r}",
                        )
                    scope.confirmed = True
                case VerifyStep(verify=verify):
                    names.add("verify")
                    if verify == "otp" and self.c.channels.whatsapp is None:
                        self.error(
                            (*at, "verify"), "otp is sent over WhatsApp and needs channels.whatsapp"
                        )
                case CallToolStep(call_tool=call):
                    names.add("call_tool")
                    tool = self.tool(call.name, (*at, "call_tool", "name"))
                    if (
                        tool
                        and self.has_side_effects(tool)
                        and not scope.confirmed
                        and not self.approved(call.name)
                    ):
                        self.error(
                            (*at, "call_tool", "name"),
                            f"side-effecting tool {call.name!r} needs an earlier confirm"
                            " or an approval policy",
                        )
                    self.json_templates(call.args, (*at, "call_tool", "args"), roots)
                    if call.as_ is not None:
                        names.add(call.as_)
                        self.bind(call.as_, (*at, "call_tool", "as"), scope)
                case DelegateStep(delegate=delegate):
                    names.add("delegate")
                    if self.worker_ref(delegate.worker, (*at, "delegate", "worker")):
                        self.delegates.append(
                            (worker, delegate.worker, (*at, "delegate", "worker"))
                        )
                    self.json_templates(delegate.args, (*at, "delegate", "args"), roots)
                    if delegate.as_ is not None:
                        names.add(delegate.as_)
                        self.bind(delegate.as_, (*at, "delegate", "as"), scope)
                case HandoffStep(handoff=target):
                    names.add("handoff")
                    if target != "human":
                        self.worker_ref(target, (*at, "handoff"))
                    return self.unreachable(steps, path, i), True
                case IfStep():
                    names.add("if")
                    self.condition(step.condition, (*at, "if"), roots)
                    if depth >= MAX_IF_DEPTH:
                        self.error(at, f"if nesting depth is at most {MAX_IF_DEPTH}")
                        continue
                    branches = [
                        self.steps(worker, step.then, (*at, "then"), scope.copy(), depth + 1),
                        self.steps(worker, step.else_, (*at, "else"), scope.copy(), depth + 1),
                    ]
                    live = [s for s, ended in branches if not ended]
                    if not live:
                        return self.unreachable(steps, path, i), True
                    first, *rest = live
                    scope = _Scope(
                        first.bound.intersection(*(s.bound for s in rest)),
                        first.collects.intersection(*(s.collects for s in rest)),
                        all(s.confirmed for s in live),
                    )
                case ResultStep(result=result):
                    names.add("result")
                    if result.data is not None:
                        self.json_templates(result.data, (*at, "result", "data"), roots)
                    if result.say is not None:
                        self.template(result.say, (*at, "result", "say"), roots)
                    return self.unreachable(steps, path, i), True
        return scope, False

    def unreachable(self, steps: list[Step], path: Path, last: int) -> _Scope:
        if last + 1 < len(steps):
            self.error(
                (*path, last + 1), "unreachable: the flow already ended with result or handoff"
            )
        return _Scope()

    def delegate_cycles(self) -> None:
        edges: dict[str, list[str]] = {}
        for source, target, _ in self.delegates:
            edges.setdefault(source, []).append(target)

        def route(start: str, goal: str) -> list[str] | None:
            queue, seen = [[start]], {start}
            while queue:
                trail = queue.pop(0)
                if trail[-1] == goal:
                    return trail
                for nxt in edges.get(trail[-1], []):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append([*trail, nxt])
            return None

        for source, target, path in self.delegates:
            trail = route(target, source)
            if trail is not None:
                self.error(path, f"delegate cycle: {' -> '.join([source, *trail])}")

    # --- Router (§6) ------------------------------------------------------

    def router(self) -> None:
        router = self.c.router
        if router.fallback != "front":
            self.worker_ref(router.fallback, ("router", "fallback"))
        for i, rule in enumerate(router.rules):
            self.condition(rule.condition, ("router", "rules", i, "if"))
            self.worker_ref(rule.prefer, ("router", "rules", i, "prefer"))
        for i, example in enumerate(router.examples):
            self.worker_ref(example.worker, ("router", "examples", i, "worker"))

    # --- Triggers (§7) ----------------------------------------------------

    def triggers(self) -> None:
        channels = self.c.channels
        configured = {"web": channels.web, "phone": channels.phone, "whatsapp": channels.whatsapp}
        for i, trigger in enumerate(self.c.triggers):
            if isinstance(trigger, InboundTrigger):
                for j, channel in enumerate(trigger.inbound):
                    if configured[channel] is None:
                        self.error(
                            ("triggers", i, "inbound", j), f"channel {channel!r} is not configured"
                        )
            elif isinstance(trigger, WebhookTrigger):
                needs = "phone" if trigger.action == "call" else "whatsapp"
                if configured[needs] is None:
                    self.error(
                        ("triggers", i, "action"), f"{trigger.action} needs channels.{needs}"
                    )
                roots = {"event"}
                self.template(trigger.to, ("triggers", i, "to"), roots)
                for key, value in trigger.context.items():
                    self.template(value, ("triggers", i, "context", key), roots)

    # --- Policies (§10) ---------------------------------------------------

    def policies(self) -> None:
        policies = self.c.policies
        for ref, condition in policies.approval.items():
            self.tool(ref, ("policies", "approval", ref))
            # Approval conditions read the call's arguments, which are known only at call time.
            self.condition(condition, ("policies", "approval", ref))
        speech = policies.speech
        for topic, rule in speech.restricted_topics.items():
            for i, kb in enumerate(rule.sources):
                self.knowledge_base(
                    kb, ("policies", "speech", "restricted_topics", topic, "sources", i)
                )
        voice = self.c.front.voice
        if voice is not None and voice.mode == "speech_to_speech":
            # Speech-to-speech produces audio directly, so no sentence can be checked (PRD §6.9).
            for key, used in (
                ("restricted_topics", speech.restricted_topics),
                ("never_say", speech.never_say),
            ):
                if used:
                    self.error(
                        ("policies", "speech", key),
                        f"speech_to_speech has no claim check, so {key} cannot be enforced",
                    )
        seen: set[str] = set()
        for i, watcher in enumerate(policies.watch):
            if watcher.name in seen:
                self.error(("policies", "watch", i, "name"), f"duplicate watcher {watcher.name!r}")
            seen.add(watcher.name)

    # --- Evals (§11) ------------------------------------------------------

    def flow_step(self, worker: str, step: str, path: Path) -> None:
        names = self.step_names.get(worker)
        if names is None:
            self.error(path, f"{worker!r} is not a flow")
        elif step not in names:
            self.error(path, f"{worker!r} has no step {step!r}")

    def evals(self) -> None:
        configured = self.c.channels.profile_channels()
        watchers = {w.name for w in self.c.policies.watch}
        roots = {"result", "customer"}
        for i, ev in enumerate(self.c.evals):
            path: Path = ("evals", i)
            if ev.channel not in configured:
                self.error((*path, "channel"), f"channel {ev.channel!r} is not configured")
            for j, line in enumerate(ev.script):
                if isinstance(line, ExpectLine):
                    at: Path = (*path, "script", j, "expect")
                    if self.worker_ref(line.expect.worker, (*at, "worker")) and line.expect.step:
                        self.flow_step(line.expect.worker, line.expect.step, (*at, "step"))
            for j, assertion in enumerate(ev.assert_):
                at = (*path, "assert", j)
                match assertion:
                    case LatencyP95() if ev.channel not in VOICE_CHANNELS:
                        self.error(at, "latency_p95_ms is for voice evals only")
                    case ToolCalled(tool_called=ref):
                        self.tool(ref, (*at, "tool_called"))
                    case StepReached(step_reached=reached):
                        if self.worker_ref(reached.worker, (*at, "step_reached", "worker")):
                            self.flow_step(
                                reached.worker, reached.step, (*at, "step_reached", "step")
                            )
                    case WatchFlagged(watch_flagged=flagged) if flagged.watcher not in watchers:
                        self.error(
                            (*at, "watch_flagged", "watcher"),
                            f"unknown watcher {flagged.watcher!r}",
                        )
                    case Said(said=text):
                        self.template(text, (*at, "said"), roots)
                    case NotSaid(not_said=text):
                        self.template(text, (*at, "not_said"), roots)
                    case _:
                        pass

    # --- Documents (PRD §6.1) -----------------------------------------------

    def knowledge_documents(self) -> None:
        for name, kb in self.c.knowledge.items():
            for i, source in enumerate(kb.sources):
                if not is_url(source):
                    self.document(source, "knowledge", ("knowledge", name, "sources", i))
