"""Workers: the contract they return (architecture §4.2) and the `tool` and `llm` runtimes.

Only the front agent talks. A worker ends with `Result`, `NeedsInput` or `Failed`,
reports `Progress` while it runs, and calls only the tools its scope permits.
"""

from a2u_core.workers.contract import (
    DEFAULT_USER_SAFE_MESSAGE,
    Failed,
    NeedsInput,
    Option,
    Outcome,
    Progress,
    Result,
    to_outcome,
)
from a2u_core.workers.runtime import (
    LlmRun,
    LlmWorkerRunner,
    ToolWorkerRunner,
    WorkerContext,
    resolve_model,
    run_guarded,
)
from a2u_core.workers.tools import (
    ScopedTools,
    ToolBackend,
    ToolCallContext,
    ToolCallError,
    ToolPermissionDenied,
    ToolScope,
    ToolSpec,
    declared_effects,
    webhook_spec,
)

__all__ = [
    "DEFAULT_USER_SAFE_MESSAGE",
    "Failed",
    "LlmRun",
    "LlmWorkerRunner",
    "NeedsInput",
    "Option",
    "Outcome",
    "Progress",
    "Result",
    "ScopedTools",
    "ToolBackend",
    "ToolCallContext",
    "ToolCallError",
    "ToolPermissionDenied",
    "ToolScope",
    "ToolSpec",
    "ToolWorkerRunner",
    "WorkerContext",
    "declared_effects",
    "resolve_model",
    "run_guarded",
    "to_outcome",
    "webhook_spec",
]
