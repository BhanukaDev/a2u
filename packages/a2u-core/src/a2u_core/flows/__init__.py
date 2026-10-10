"""Flows: the engine that runs config flows (spec §5.2-§5.4, architecture §4.4).

A flow pauses with `NeedsInput` and resumes from its `FlowState`; it ends with
`Result` (its `say` rendered by code for the channel, D-017), `Failed`, or a
handoff. Conditions live in `a2u_core.conditions`; templates and filters in
`render`; answer checks in `inputs`.
"""

from a2u_core.flows.engine import (
    DEFAULT_MAX_OPTIONS,
    MAX_DELEGATE_DEPTH,
    MAX_FORMAT_FAILURES,
    Delegator,
    FlowEngine,
    FlowRun,
    FlowState,
    Pending,
    Verifier,
    step_names,
)
from a2u_core.flows.inputs import BasicCapture, Capture, Invalid, Valid
from a2u_core.flows.render import Channel, RenderError, render_json, render_text

__all__ = [
    "DEFAULT_MAX_OPTIONS",
    "MAX_DELEGATE_DEPTH",
    "MAX_FORMAT_FAILURES",
    "BasicCapture",
    "Capture",
    "Channel",
    "Delegator",
    "FlowEngine",
    "FlowRun",
    "FlowState",
    "Invalid",
    "Pending",
    "RenderError",
    "Valid",
    "Verifier",
    "render_json",
    "render_text",
    "step_names",
]
