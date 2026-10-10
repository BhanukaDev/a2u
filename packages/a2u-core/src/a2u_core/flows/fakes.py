"""In-memory verifier and delegator for tests and evals."""

from dataclasses import dataclass, field

from pydantic import JsonValue

from a2u_core.workers.contract import Outcome, Result
from a2u_core.workers.runtime import WorkerContext
from a2u_core.workers.tools import JsonObject


@dataclass
class FakeVerifier:
    otp: str = "123456"
    facts: dict[str, JsonValue] = field(default_factory=lambda: {})
    sent: int = 0  # OTPs sent

    async def send_otp(self, ctx: WorkerContext) -> None:
        self.sent += 1

    async def check_otp(self, code: str, ctx: WorkerContext) -> bool:
        return code == self.otp

    async def check_fact(self, name: str, value: JsonValue, ctx: WorkerContext) -> bool:
        return name in self.facts and self.facts[name] == value


@dataclass
class FakeDelegator:
    """Replies with scripted outcomes per worker, in order; records every call."""

    outcomes: dict[str, list[Outcome]] = field(default_factory=lambda: {})
    calls: list[tuple[str, str, JsonValue]] = field(default_factory=lambda: [])  # (kind, worker, x)
    started: list[tuple[str, JsonObject, int]] = field(default_factory=lambda: [])

    def _next(self, worker: str) -> Outcome:
        queue = self.outcomes.get(worker, [])
        return queue.pop(0) if queue else Result(data={})

    async def run(
        self, worker: str, args: JsonObject, ctx: WorkerContext, *, key: str, depth: int
    ) -> Outcome:
        self.calls.append(("run", worker, args))
        return self._next(worker)

    async def resume(
        self, worker: str, answer: JsonValue, ctx: WorkerContext, *, key: str
    ) -> Outcome:
        self.calls.append(("resume", worker, answer))
        return self._next(worker)

    async def start(
        self, worker: str, args: JsonObject, ctx: WorkerContext, *, key: str, depth: int
    ) -> str:
        self.started.append((worker, args, depth))
        return f"task_{len(self.started)}"
