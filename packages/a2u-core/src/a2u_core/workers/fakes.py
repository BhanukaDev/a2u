"""An in-memory tool backend for tests and evals: canned replies, recorded calls."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from a2u_core.config.models import AgentConfig, McpTool, WebhookTool
from a2u_core.workers.tools import JsonObject, ToolCallContext, ToolSpec, webhook_spec


@dataclass
class FakeToolBackend:
    config: AgentConfig
    replies: Mapping[str, JsonObject | Exception] = field(default_factory=lambda: {})
    calls: list[tuple[str, JsonObject, ToolCallContext]] = field(default_factory=lambda: [])
    delay: float = 0.0  # seconds before each reply

    def describe(self, ref: str) -> Sequence[ToolSpec]:
        base, _, op = ref.partition(".")
        tool = self.config.tools.get(base)
        if isinstance(tool, WebhookTool):
            return [webhook_spec(base, tool)]
        ops = [op] if op else tool.allow if isinstance(tool, McpTool) else []
        return [ToolSpec(f"{base}.{o}", None, {"type": "object"}) for o in ops]

    async def call(self, ref: str, args: JsonObject, ctx: ToolCallContext) -> JsonObject:
        self.calls.append((ref, args, ctx))
        if self.delay:
            await asyncio.sleep(self.delay)
        reply = self.replies.get(ref, {})
        if isinstance(reply, Exception):
            raise reply
        return reply
