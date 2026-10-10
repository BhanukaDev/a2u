"""Pydantic models for the agent config spec (docs/agent-config-spec.md, v0.3).

These models check shape: types, required fields, enums and rules inside one
object. Rules that span objects (references, scopes, channels, languages) live
in `checks.py` and run once the shape is valid.

Unions are tagged with prefixed names (`step:if`, `tool:connector`) so that a
tag never collides with a key in the document when errors are mapped to lines.
"""

import re
from collections.abc import Callable, Collection
from datetime import timedelta
from typing import Annotated, Any, Literal, Self, cast
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    NonNegativeInt,
    PositiveInt,
    Tag,
    ValidationInfo,
    field_validator,
    model_validator,
)

# --- Scalars ---------------------------------------------------------------


def _matches(pattern: str, what: str) -> Callable[[str], str]:
    compiled = re.compile(pattern)

    def check(value: str) -> str:
        if not compiled.fullmatch(value):
            raise ValueError(f"{value!r} is not {what}")
        return value

    return check


def _not_reserved(reserved: Collection[str]) -> Callable[[str], str]:
    def check(value: str) -> str:
        if value in reserved:
            raise ValueError(f"{value!r} is reserved")
        return value

    return check


Slug = Annotated[
    str, AfterValidator(_matches(r"[a-z0-9][a-z0-9-]{0,62}", "a lowercase slug (a-z, 0-9 and -)"))
]
Name = Annotated[
    str, AfterValidator(_matches(r"[a-z][a-z0-9_]{0,62}", "a name (a-z, 0-9 and _, from a-z)"))
]
DOC_NAME = re.compile(r"[a-z][a-z0-9_-]{0,62}")
DocName = Annotated[str, AfterValidator(_matches(DOC_NAME.pattern, "a document name"))]
# Names every flow can read without binding them.
FLOW_ROOTS = frozenset({"customer", "args"})
BindingName = Annotated[Name, AfterValidator(_not_reserved(FLOW_ROOTS))]
WorkerName = Annotated[Name, AfterValidator(_not_reserved({"human", "front"}))]
LanguageCode = Annotated[
    str,
    AfterValidator(_matches(r"[a-z]{2,3}(-[A-Z]{2})?", "a language code such as en, si or en-LK")),
]
CountryCode = Annotated[
    str, AfterValidator(_matches(r"[A-Z]{2}", "an ISO country code such as LK"))
]
CurrencyCode = Annotated[
    str, AfterValidator(_matches(r"[A-Z]{3}", "an ISO currency code such as LKR"))
]
PhoneNumber = Annotated[
    str,
    AfterValidator(
        _matches(r"\+[1-9][0-9 ()-]{6,24}", "a phone number in international format (+94 ...)")
    ),
]
VendorRef = Annotated[
    str, AfterValidator(_matches(r"[a-z0-9_-]+(:[A-Za-z0-9._-]+)?", "a vendor or vendor:model"))
]
ToolRef = Annotated[
    str,
    AfterValidator(
        _matches(r"[a-z][a-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)?", "a tool name or tool.operation")
    ),
]
REFERENCE = re.compile(r"[a-z_][a-z0-9_]*(\.[A-Za-z0-9_]+)*")
Reference = Annotated[
    str, AfterValidator(_matches(REFERENCE.pattern, "a reference such as order.id"))
]
EventName = Annotated[
    str,
    AfterValidator(
        _matches(r"[a-z0-9_]+(\.[a-z0-9_]+)*", "an event name such as hubspot.new_lead")
    ),
]

MODEL_ALIASES = frozenset({"fast"})
_MODEL_ID = re.compile(r"[a-z0-9-]+:[A-Za-z0-9._-]+")


def _model_ref(value: str) -> str:
    if value not in MODEL_ALIASES and not _MODEL_ID.fullmatch(value):
        aliases = ", ".join(sorted(MODEL_ALIASES))
        raise ValueError(f"{value!r} is not a model alias ({aliases}) or provider:model")
    return value


ModelRef = Annotated[str, AfterValidator(_model_ref)]


def _https_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError(f"{value!r} must be an https:// URL")
    return value


HttpsUrl = Annotated[str, AfterValidator(_https_url)]

_DURATION = re.compile(r"([0-9]+)(ms|s|m|h|d)")
_UNITS = {
    "ms": timedelta(milliseconds=1),
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
}


def _duration(value: object) -> object:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, str) and (m := _DURATION.fullmatch(value)) and int(m[1]) > 0:
        return int(m[1]) * _UNITS[m[2]]
    raise ValueError(f"{value!r} is not a duration such as 30s, 10m, 2h or 365d")


Duration = Annotated[timedelta, BeforeValidator(_duration)]


def _filler(value: object) -> object:
    if value in ("auto", "off"):
        return value
    if isinstance(value, list):
        phrases = cast(list[object], value)
        if phrases and all(isinstance(p, str) and p.strip() for p in phrases):
            return phrases
    raise ValueError("filler must be auto, off or a list of phrases")


Filler = Annotated[Literal["auto", "off"] | list[str], BeforeValidator(_filler)]


def _key(value: object, field: str) -> object:
    if isinstance(value, dict):
        return cast(dict[str, object], value).get(field)
    return getattr(value, field, None)


def _first_key(value: Any, kinds: tuple[str, ...], prefix: str) -> str | None:
    if isinstance(value, BaseModel):
        keys: Collection[str] = type(value).model_fields
    elif isinstance(value, dict):
        keys = cast(dict[str, object], value)
    else:
        return None
    return next((f"{prefix}:{kind}" for kind in kinds if kind in keys), None)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- Languages, front agent, channels, memory (§1-§4) ----------------------


class Languages(_Spec):
    default: LanguageCode = "en"
    voice: list[LanguageCode] = Field(default_factory=lambda: [])
    text: list[LanguageCode] = Field(default_factory=lambda: ["en"])


ProfileChannel = Literal["web_chat", "web_voice", "phone", "whatsapp"]


class Profile(_Spec):
    model: ModelRef | None = None
    max_reply_chars: PositiveInt | None = None


class VoiceChoice(_Spec):
    voice_id: str
    tts: VendorRef | None = None
    stt: VendorRef | None = None


class Voice(_Spec):
    mode: Literal["cascaded", "speech_to_speech"] = "cascaded"
    stt: VendorRef | None = None  # None: the platform default
    tts: VendorRef | None = None
    voices: dict[LanguageCode, VoiceChoice] = Field(default_factory=lambda: {})


class Front(_Spec):
    persona: DocName | None = None
    instructions: str | None = None
    model: ModelRef | None = None
    profiles: dict[ProfileChannel, Profile] = Field(default_factory=lambda: {})
    voice: Voice | None = None
    knowledge: list[Name] = Field(default_factory=lambda: [])
    filler: Filler = "auto"
    handoff_number: PhoneNumber | None = None

    @model_validator(mode="after")
    def _one_prompt(self) -> Self:
        if (self.persona is None) == (self.instructions is None):
            raise ValueError("front needs exactly one of persona or instructions")
        return self


class WebChannel(_Spec):
    chat: bool = False
    voice: bool = False
    mode: Literal["anonymous", "verified", "both"] = "anonymous"


class PhoneChannel(_Spec):
    numbers: list[PhoneNumber] = Field(default_factory=lambda: [])
    recording: bool = False


class WhatsAppChannel(_Spec):
    account: str


class Channels(_Spec):
    web: WebChannel | None = None
    phone: PhoneChannel | None = None
    whatsapp: WhatsAppChannel | None = None

    def profile_channels(self) -> set[str]:
        """Configured channels, named as in `front.profiles` and `evals[].channel`."""
        out: set[str] = set()
        if self.web and self.web.chat:
            out.add("web_chat")
        if self.web and self.web.voice:
            out.add("web_voice")
        if self.phone:
            out.add("phone")
        if self.whatsapp:
            out.add("whatsapp")
        return out


class Memory(_Spec):
    identify_by: list[Literal["phone", "whatsapp", "web_user"]] = Field(
        default_factory=lambda: ["phone", "whatsapp", "web_user"]
    )
    link_web_to_phone: Literal["verified_only"] = "verified_only"
    summary_after_turns: PositiveInt = 10
    facts: bool = True
    fact_sensitivity_default: Literal["low", "high"] = "low"
    retention: Duration = timedelta(days=365)


# --- Value types for collect and choose (§5.2, §5.5) ------------------------

PLAIN_TYPES = ("string", "number", "integer", "bool")
ENTITY_TYPES = (
    "nic_lk",
    "phone",
    "person_name",
    "address",
    "money",
    "date",
    "time",
    "email",
    "account_no",
    "id",
)
_TYPE_TAGS = {
    **dict.fromkeys(PLAIN_TYPES, "plain"),
    "nic_lk": "simple",
    "person_name": "simple",
    "phone": "phone",
    "address": "address",
    "money": "money",
    "date": "datetime",
    "time": "datetime",
    "email": "email",
    "account_no": "pattern",
    "id": "pattern",
    "choose": "choose",
}
_CHOOSE = re.compile(r"choose\((.*)\)", re.DOTALL)

Readback = Literal["digits", "spell", "summary", "none"]


def _parse_choose(inner: str) -> dict[str, Any]:
    source, *rest = (part.strip() for part in inner.split(","))
    args: dict[str, str] = {}
    for part in rest:
        name, sep, value = part.partition("=")
        if not sep:
            raise ValueError(f"choose arguments are name=value, got {part!r}")
        args[name.strip()] = value.strip()
    return {"type": "choose", "source": source, "args": args}


def _value_type(value: object) -> object:
    if isinstance(value, str):
        if m := _CHOOSE.fullmatch(value.strip()):
            return _parse_choose(m[1])
        value = {"type": value}
    t = _key(value, "type")
    if isinstance(t, str) and t not in _TYPE_TAGS:
        known = ", ".join((*PLAIN_TYPES, *ENTITY_TYPES))
        raise ValueError(f"unknown type {t!r}; expected one of {known} or choose(...)")
    return value


def _type_tag(value: Any) -> str | None:
    t = _key(value, "type")
    return f"type:{_TYPE_TAGS[t]}" if isinstance(t, str) and t in _TYPE_TAGS else None


class PlainType(_Spec):
    type: Literal["string", "number", "integer", "bool"]


class EntityType(_Spec):
    """Base for entity types: parsed, validated and read back by code (D-026)."""


class SimpleEntity(EntityType):
    type: Literal["nic_lk", "person_name"]


class PhoneEntity(EntityType):
    type: Literal["phone"]
    region: CountryCode | None = None  # None: the workspace region


class AddressEntity(EntityType):
    type: Literal["address"]
    country: CountryCode | None = None
    geocode: bool = False


class MoneyEntity(EntityType):
    type: Literal["money"]
    currency: CurrencyCode | None = None
    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, gt=0)


class DateTimeEntity(EntityType):
    type: Literal["date", "time"]
    min: str | None = None  # bounds are interpreted by entity capture (WP-1.20)
    max: str | None = None


class EmailEntity(EntityType):
    type: Literal["email"]
    mx_check: bool = False


class PatternEntity(EntityType):
    type: Literal["account_no", "id"]
    pattern: str
    check_digit: Literal["luhn"] | None = None

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as e:
            raise ValueError(f"invalid pattern: {e}") from e
        return value


class ChooseType(_Spec):
    type: Literal["choose"]
    source: Reference  # a tool or tool.operation, or a bound list
    args: dict[Name, Reference] = Field(default_factory=lambda: {})


ValueType = Annotated[
    Annotated[PlainType, Tag("type:plain")]
    | Annotated[SimpleEntity, Tag("type:simple")]
    | Annotated[PhoneEntity, Tag("type:phone")]
    | Annotated[AddressEntity, Tag("type:address")]
    | Annotated[MoneyEntity, Tag("type:money")]
    | Annotated[DateTimeEntity, Tag("type:datetime")]
    | Annotated[EmailEntity, Tag("type:email")]
    | Annotated[PatternEntity, Tag("type:pattern")]
    | Annotated[ChooseType, Tag("type:choose")],
    Discriminator(_type_tag, custom_error_type="invalid_type", custom_error_message="unknown type"),
    BeforeValidator(_value_type),
]


def _split_binding(data: Any, options: tuple[str, ...], step: str) -> Any:
    """`{ slot: date, retries: 3 }` -> `{ name: slot, type: date, retries: 3 }`."""
    if not isinstance(data, dict):
        return data
    data = cast(dict[str, Any], data)
    names = [k for k in data if k not in options]
    if len(names) != 1:
        found = ", ".join(names) or "none"
        raise ValueError(
            f"{step} binds exactly one name, e.g. {{ slot: date }}; found {len(names)}: {found}"
        )
    (name,) = names
    split = {k: v for k, v in data.items() if k in options}
    return {"name": name, "type": data[name], **split}


# --- Flow steps (§5.2) -----------------------------------------------------


class Collect(_Spec):
    name: BindingName
    type: ValueType
    retries: NonNegativeInt | None = None  # None: policies.collect.retries
    timeout: Duration | None = None  # None: policies.collect.timeout
    readback: Readback | None = None  # None: the entity type's default

    @model_validator(mode="before")
    @classmethod
    def _from_binding(cls, data: Any) -> Any:
        return _split_binding(data, ("retries", "timeout", "readback"), "collect")

    @model_validator(mode="after")
    def _readback_needs_entity(self) -> Self:
        if self.readback is not None and not isinstance(self.type, EntityType):
            raise ValueError("readback applies only to entity types")
        return self


def _choose_options(value: object) -> object:
    if isinstance(value, str):
        if m := _CHOOSE.fullmatch(value.strip()):
            return _parse_choose(m[1])
        raise ValueError("choose takes choose(tool_or_list) or a list of options")
    return value


class Choose(_Spec):
    name: BindingName
    type: Annotated[ChooseType | list[str], BeforeValidator(_choose_options)]
    max_options: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="before")
    @classmethod
    def _from_binding(cls, data: Any) -> Any:
        return _split_binding(data, ("max_options",), "choose")


class Confirm(_Spec):
    text: str
    on_no: Name | None = None

    @model_validator(mode="before")
    @classmethod
    def _bare(cls, data: Any) -> Any:
        return {"text": data} if isinstance(data, str) else data


class VerifyKnowledge(_Spec):
    knowledge: list[Name] = Field(min_length=1)


def _verify_tag(value: Any) -> str:
    return "verify:otp" if isinstance(value, str) else "verify:knowledge"


Verify = Annotated[
    Annotated[Literal["otp"], Tag("verify:otp")]
    | Annotated[VerifyKnowledge, Tag("verify:knowledge")],
    Discriminator(_verify_tag),
]


class CallTool(_Spec):
    name: ToolRef
    args: dict[str, JsonValue] = Field(default_factory=lambda: {})
    as_: BindingName | None = Field(default=None, alias="as")


class Delegate(_Spec):
    worker: Name
    args: dict[str, JsonValue] = Field(default_factory=lambda: {})
    as_: BindingName | None = Field(default=None, alias="as")
    background: bool = False


class ResultSpec(_Spec):
    data: dict[str, JsonValue] | None = None  # None: the last tool result
    say: str | None = None


class CollectStep(_Spec):
    collect: Collect


class ChooseStep(_Spec):
    choose: Choose


class ConfirmStep(_Spec):
    confirm: Confirm


class VerifyStep(_Spec):
    verify: Verify


class CallToolStep(_Spec):
    call_tool: CallTool


class DelegateStep(_Spec):
    delegate: Delegate


class HandoffStep(_Spec):
    handoff: Name  # "human" or a worker


class IfStep(_Spec):
    condition: str = Field(alias="if")  # CEL subset (spec §5.3), see a2u_core.conditions
    then: list["Step"]
    else_: list["Step"] = Field(default_factory=lambda: [], alias="else")


class ResultStep(_Spec):
    result: ResultSpec


STEP_KINDS = (
    "collect",
    "choose",
    "confirm",
    "verify",
    "call_tool",
    "delegate",
    "handoff",
    "if",
    "result",
)


def _step_tag(value: Any) -> str | None:
    if isinstance(value, IfStep):
        return "step:if"
    return _first_key(value, STEP_KINDS, "step")


Step = Annotated[
    Annotated[CollectStep, Tag("step:collect")]
    | Annotated[ChooseStep, Tag("step:choose")]
    | Annotated[ConfirmStep, Tag("step:confirm")]
    | Annotated[VerifyStep, Tag("step:verify")]
    | Annotated[CallToolStep, Tag("step:call_tool")]
    | Annotated[DelegateStep, Tag("step:delegate")]
    | Annotated[HandoffStep, Tag("step:handoff")]
    | Annotated[IfStep, Tag("step:if")]
    | Annotated[ResultStep, Tag("step:result")],
    Discriminator(
        _step_tag,
        custom_error_type="invalid_step",
        custom_error_message=f"each step must be one of: {', '.join(STEP_KINDS)}",
    ),
]

IfStep.model_rebuild()


# --- Workers (§5.1) --------------------------------------------------------


class _Worker(_Spec):
    description: str | None = None  # used by routing
    filler: Filler | None = None  # None: front.filler


class LlmWorker(_Worker):
    kind: Literal["llm"]
    instructions: str  # a persona document name, or inline text
    model: ModelRef | None = None
    tools: list[ToolRef] = Field(default_factory=lambda: [])
    knowledge: list[Name] = Field(default_factory=lambda: [])


class ToolWorker(_Worker):
    kind: Literal["tool"]
    tool: ToolRef
    background: bool = False
    timeout: Duration | None = None


class FlowWorker(_Worker):
    kind: Literal["flow"]
    steps: list[Step] = Field(min_length=1)


def _worker_tag(value: Any) -> str | None:
    kind = _key(value, "kind")
    return f"worker:{kind}" if kind in ("llm", "tool", "flow") else None


Worker = Annotated[
    Annotated[LlmWorker, Tag("worker:llm")]
    | Annotated[ToolWorker, Tag("worker:tool")]
    | Annotated[FlowWorker, Tag("worker:flow")],
    Discriminator(
        _worker_tag,
        custom_error_type="invalid_worker",
        custom_error_message="kind must be llm, tool or flow",
    ),
]


def is_document_name(instructions: str) -> bool:
    """`instructions: products` names a document; anything else is inline text."""
    return DOC_NAME.fullmatch(instructions) is not None


# --- Router (§6) -----------------------------------------------------------


class RouterRule(_Spec):
    condition: str = Field(alias="if")
    prefer: Name


class RouterExample(_Spec):
    text: str
    worker: Name


class Router(_Spec):
    mode: Literal["handoff", "classifier", "rules_then_classifier"] = "handoff"
    model: ModelRef | None = None
    fallback: Name = "front"  # "front" or a worker
    rules: list[RouterRule] = Field(default_factory=lambda: [])
    examples: list[RouterExample] = Field(default_factory=lambda: [])

    @field_validator("rules")
    @classmethod
    def _rules_need_mode(cls, rules: list[RouterRule], info: ValidationInfo) -> list[RouterRule]:
        if rules and info.data.get("mode") != "rules_then_classifier":
            raise ValueError("rules are used only by rules_then_classifier")
        return rules


# --- Triggers (§7) ---------------------------------------------------------


class InboundTrigger(_Spec):
    inbound: list[Literal["phone", "whatsapp", "web"]] = Field(min_length=1)


class ApiTrigger(_Spec):
    api: Name


class WebhookTrigger(_Spec):
    webhook: EventName
    action: Literal["call", "whatsapp"]
    to: str
    within: Duration | None = None
    context: dict[str, str] = Field(default_factory=lambda: {})
    goal: str | None = None


class _ScheduleTrigger(_Spec):
    @model_validator(mode="before")
    @classmethod
    def _after_v1(cls, data: Any) -> Any:
        raise ValueError("schedule triggers are after v1 (D-022)")


TRIGGER_KINDS = ("inbound", "api", "webhook", "schedule")

Trigger = Annotated[
    Annotated[InboundTrigger, Tag("trigger:inbound")]
    | Annotated[ApiTrigger, Tag("trigger:api")]
    | Annotated[WebhookTrigger, Tag("trigger:webhook")]
    | Annotated[_ScheduleTrigger, Tag("trigger:schedule")],
    Discriminator(
        lambda v: _first_key(v, TRIGGER_KINDS, "trigger"),
        custom_error_type="invalid_trigger",
        custom_error_message="each trigger must be one of: inbound, api, webhook",
    ),
]


# --- Tools (§8) ------------------------------------------------------------

FieldType = Literal["string", "number", "integer", "bool", "object", "array"]


class WebhookTool(_Spec):
    type: Literal["webhook"]
    url: HttpsUrl
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    input: dict[str, FieldType] = Field(default_factory=lambda: {})
    output: dict[str, FieldType] = Field(default_factory=lambda: {})
    timeout: Duration = timedelta(seconds=8)
    retries: int = Field(default=2, ge=0, le=5)
    side_effects: bool = True  # assume effects unless the builder says otherwise


class ConnectorTool(_Spec):
    type: Literal["connector"]
    connector: Name
    connection: Slug


class McpTool(_Spec):
    type: Literal["mcp"]
    server: HttpsUrl
    allow: list[str] = Field(min_length=1)


def _tool_tag(value: Any) -> str | None:
    t = _key(value, "type")
    return f"tool:{t}" if t in ("webhook", "connector", "mcp") else None


Tool = Annotated[
    Annotated[WebhookTool, Tag("tool:webhook")]
    | Annotated[ConnectorTool, Tag("tool:connector")]
    | Annotated[McpTool, Tag("tool:mcp")],
    Discriminator(
        _tool_tag,
        custom_error_type="invalid_tool",
        custom_error_message="type must be webhook, connector or mcp",
    ),
]


# --- Knowledge (§9) --------------------------------------------------------


class KnowledgeBase(_Spec):
    sources: list[str] = Field(min_length=1)  # document names or http(s) URLs
    refresh: Literal["daily", "weekly", "monthly"] | None = None


def is_url(source: str) -> bool:
    return source.startswith(("https://", "http://"))


# --- Policies (§10) --------------------------------------------------------


class CollectDefaults(_Spec):
    retries: NonNegativeInt = 2
    timeout: Duration = timedelta(hours=24)


def _no_sms(value: object) -> object:
    if value == "sms":
        raise ValueError("sms is not a channel in v1 (D-013)")
    return value


DeliveryStep = Annotated[Literal["live", "whatsapp", "whatsapp_template"], BeforeValidator(_no_sms)]


class TaskDelivery(_Spec):
    order: list[DeliveryStep] = Field(
        default_factory=lambda: ["live", "whatsapp", "whatsapp_template"], min_length=1
    )
    consent: Literal["required", "assert_by_client"] = "required"
    ask_consent: str | None = None


class Idle(_Spec):
    prompt_after: Duration = timedelta(seconds=30)
    hangup_after: Duration = timedelta(seconds=45)


class RestrictedTopic(_Spec):
    sources: list[Name] = Field(default_factory=lambda: [])
    otherwise: str = "handoff"  # "handoff" or a sentence to say instead


class Speech(_Spec):
    claim_check: Literal["on", "off"] = "on"
    restricted_topics: dict[Name, RestrictedTopic] = Field(default_factory=lambda: {})
    never_say: list[str] = Field(default_factory=lambda: [])
    fallback: str = "Let me confirm that for you."


class Watcher(_Spec):
    name: Name
    kind: Literal["llm_judge", "classifier", "rules"]
    model: ModelRef | None = None
    prompt: str | None = None
    labels: list[str] | None = None
    when: str | None = None
    action: Literal["steer", "escalate", "flag", "remediate"]
    note: str | None = None

    @model_validator(mode="after")
    def _kind_fields(self) -> Self:
        if self.kind == "llm_judge" and not self.prompt:
            raise ValueError("llm_judge needs a prompt")
        if self.kind == "classifier" and not self.labels:
            raise ValueError("classifier needs labels")
        if self.action == "steer" and not self.note:
            raise ValueError("steer needs a note for the front agent")
        return self


class Policies(_Spec):
    pii: Literal["mask", "off"] = "mask"
    approval: dict[ToolRef, str] = Field(default_factory=lambda: {})  # tool -> condition
    collect: CollectDefaults = CollectDefaults()
    task_delivery: TaskDelivery = TaskDelivery()
    max_call_minutes: PositiveInt = 20
    idle: Idle = Idle()
    recording_disclosure: bool = True
    speech: Speech = Speech()
    watch: list[Watcher] = Field(default_factory=lambda: [])


# --- Evals (§11) -----------------------------------------------------------

EvalChannel = Literal["web_chat", "web_voice", "phone", "whatsapp"]
VOICE_CHANNELS = frozenset({"web_voice", "phone"})


class EvalCustomer(_Spec):
    phone: PhoneNumber | None = None
    traits: dict[str, JsonValue] = Field(default_factory=lambda: {})


class UserLine(_Spec):
    user: str


class Expect(_Spec):
    worker: Name
    step: str | None = None


class ExpectLine(_Spec):
    expect: Expect


ScriptLine = Annotated[
    Annotated[UserLine, Tag("script:user")] | Annotated[ExpectLine, Tag("script:expect")],
    Discriminator(
        lambda v: _first_key(v, ("user", "expect"), "script"),
        custom_error_type="invalid_script_line",
        custom_error_message="each script line must be user or expect",
    ),
]


class Simulate(_Spec):
    goal: str
    persona: str | None = None


class ToolCalled(_Spec):
    tool_called: ToolRef


class Said(_Spec):
    said: str


class NotSaid(_Spec):
    not_said: str


class LatencyP95(_Spec):
    latency_p95_ms: PositiveInt


class NoUnsupportedClaims(_Spec):
    no_unsupported_claims: bool


class Captured(_Spec):
    captured: dict[Name, str | int]


class StepReachedSpec(_Spec):
    worker: Name
    step: str
    times: PositiveInt = 1


class StepReached(_Spec):
    step_reached: StepReachedSpec


class ClaimClassSpec(_Spec):
    contains: Literal["conversation", "information", "commitment"]
    from_: Literal["result.say", "must_say"] | None = Field(default=None, alias="from")


class ClaimClass(_Spec):
    claim_class: ClaimClassSpec


class WatchFlaggedSpec(_Spec):
    watcher: Name
    expect: bool = True


class WatchFlagged(_Spec):
    watch_flagged: WatchFlaggedSpec


ASSERTION_KINDS = (
    "tool_called",
    "said",
    "not_said",
    "latency_p95_ms",
    "no_unsupported_claims",
    "captured",
    "step_reached",
    "claim_class",
    "watch_flagged",
)

Assertion = Annotated[
    Annotated[ToolCalled, Tag("assert:tool_called")]
    | Annotated[Said, Tag("assert:said")]
    | Annotated[NotSaid, Tag("assert:not_said")]
    | Annotated[LatencyP95, Tag("assert:latency_p95_ms")]
    | Annotated[NoUnsupportedClaims, Tag("assert:no_unsupported_claims")]
    | Annotated[Captured, Tag("assert:captured")]
    | Annotated[StepReached, Tag("assert:step_reached")]
    | Annotated[ClaimClass, Tag("assert:claim_class")]
    | Annotated[WatchFlagged, Tag("assert:watch_flagged")],
    Discriminator(
        lambda v: _first_key(v, ASSERTION_KINDS, "assert"),
        custom_error_type="invalid_assertion",
        custom_error_message=f"each assertion must be one of: {', '.join(ASSERTION_KINDS)}",
    ),
]


class Eval(_Spec):
    name: str
    channel: EvalChannel
    customer: EvalCustomer | None = None
    script: list[ScriptLine] = Field(default_factory=lambda: [])
    simulate: Simulate | None = None
    assert_: list[Assertion] = Field(default_factory=lambda: [], alias="assert")

    @model_validator(mode="after")
    def _has_input(self) -> Self:
        if not self.script and self.simulate is None:
            raise ValueError("an eval needs a script or simulate")
        return self


# --- Top level (§1) --------------------------------------------------------


class AgentConfig(_Spec):
    agent: Slug
    description: str | None = None
    languages: Languages = Languages()
    front: Front
    channels: Channels = Channels()
    memory: Memory = Memory()
    router: Router = Router()
    workers: dict[WorkerName, Worker] = Field(default_factory=lambda: {})
    triggers: list[Trigger] = Field(default_factory=lambda: [])
    tools: dict[Name, Tool] = Field(default_factory=lambda: {})
    knowledge: dict[Name, KnowledgeBase] = Field(default_factory=lambda: {})
    policies: Policies = Policies()
    evals: list[Eval] = Field(default_factory=lambda: [])
