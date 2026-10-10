"""The worker contract (architecture §4.2): what a worker may hand back to the front agent.

A worker ends with `Result`, `NeedsInput` or `Failed`. `Progress` is reported while
it runs, through `WorkerContext.progress`; it is never a return value. Anything
else a worker returns becomes `Failed` (see `to_outcome`).
"""

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

DEFAULT_USER_SAFE_MESSAGE = "Sorry, I couldn't finish that just now."


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Option(_Contract):
    value: str  # what the worker gets back when this option is picked
    label: str  # what the user sees or hears


class Result(_Contract):
    data: dict[str, JsonValue]
    # Set by the flow engine: already rendered, send verbatim (D-017).
    say: str | None = None
    # Set by LLM workers: values the front agent's paraphrase must contain.
    must_say: str | None = None

    @model_validator(mode="after")
    def _one_voice(self) -> Self:
        if self.say is not None and self.must_say is not None:
            raise ValueError("a result sets say (flows) or must_say (LLM workers), not both")
        return self


class NeedsInput(_Contract):
    field: str
    hint: str
    # `schema` would shadow BaseModel.schema(); the alias keeps the contract's name on the wire.
    input_schema: dict[str, Any] = Field(alias="schema")
    # For choose steps; renders as a WhatsApp list or spoken options.
    options: list[Option] | None = None
    prefer_channel: Literal["same", "whatsapp"] = "same"


class Progress(_Contract):
    note: str


class Failed(_Contract):
    reason: str  # internal, goes to traces
    user_safe_message: str = DEFAULT_USER_SAFE_MESSAGE


Outcome = Result | NeedsInput | Failed


def to_outcome(value: object) -> Outcome:
    """Pass a contract outcome through; anything else a worker returned becomes `Failed`."""
    if isinstance(value, Result | NeedsInput | Failed):
        return value
    return Failed(reason=f"contract_violation: returned {type(value).__name__}")
