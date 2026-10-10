"""Every example in the agent config spec loads."""

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from a2u_core.config import (
    ChooseType,
    CollectStep,
    Document,
    EntityType,
    FlowWorker,
    LlmWorker,
    load_config,
    load_file,
    validate_config,
)
from a2u_core.config.source import parse

FIXTURES = Path(__file__).parent / "fixtures" / "valid"
SPEC = Path(__file__).parents[4] / "docs" / "agent-config-spec.md"


def _stripped_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _contains(haystack: list[str], needle: list[str]) -> bool:
    return any(haystack[i : i + len(needle)] == needle for i in range(len(haystack)))


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.yaml")), ids=lambda p: p.stem)
def test_fixture_loads(path: Path) -> None:
    load_file(path)


def test_every_spec_example_is_in_a_fixture() -> None:
    blocks = re.findall(r"```yaml\n(.*?)```", SPEC.read_text(), flags=re.DOTALL)
    assert blocks
    fixtures = [_stripped_lines(p.read_text()) for p in FIXTURES.glob("*.yaml")]
    missing: list[str] = []
    for block in blocks:
        # Skeletons with placeholders are covered by the full fixtures, not verbatim.
        if "{...}" in block or "[...]" in block:
            continue
        if not any(_contains(fixture, _stripped_lines(block)) for fixture in fixtures):
            missing.append(block.splitlines()[0])
    assert not missing, f"spec examples not in any fixture: {missing}"


def test_sunrise_values() -> None:
    config = load_file(FIXTURES / "sunrise_dental.yaml").config
    assert config.agent == "sunrise-dental"
    assert config.policies.speech.claim_check == "on"
    assert config.memory.retention == timedelta(days=365)
    assert config.policies.collect.timeout == timedelta(hours=24)
    bookings = config.workers["bookings"]
    assert isinstance(bookings, FlowWorker)
    slot = bookings.steps[2]
    assert isinstance(slot, CollectStep)
    assert slot.collect.name == "slot"
    assert slot.collect.retries == 3
    assert isinstance(slot.collect.type, ChooseType)
    assert slot.collect.type.source == "calendar.free_slots"
    assert slot.collect.type.args == {"doctor": "appointment.doctor"}


def test_worker_kinds_values() -> None:
    config = load_file(FIXTURES / "worker_kinds.yaml").config
    returns = config.workers["returns"]
    assert isinstance(returns, LlmWorker)
    assert returns.filler == "off"
    assert config.front.filler == ["One moment."]


def test_entity_types_parse() -> None:
    config = load_file(FIXTURES / "steps_and_entities.yaml").config
    onboarding = config.workers["onboarding"]
    assert isinstance(onboarding, FlowWorker)
    nic = onboarding.steps[0]
    mobile = onboarding.steps[1]
    assert isinstance(nic, CollectStep)
    assert isinstance(mobile, CollectStep)
    assert isinstance(nic.collect.type, EntityType)
    assert nic.collect.type.model_dump() == {"type": "nic_lk"}
    assert mobile.collect.type.model_dump(exclude_none=True) == {"type": "phone", "region": "LK"}


def test_json_loads_the_same_config() -> None:
    path = FIXTURES / "worker_kinds.yaml"
    data = parse(path.read_text(), path.name).data
    from_json = load_config(json.dumps(data, indent=2), filename="agent.json").config
    assert from_json == load_file(FIXTURES / "worker_kinds.yaml").config


def test_validate_config_accepts_plain_data() -> None:
    loaded = validate_config(
        {"agent": "x", "front": {"instructions": "Hi."}, "channels": {"web": {"chat": True}}}
    )
    assert loaded.config.languages.default == "en"


def test_documents_are_resolved_by_name() -> None:
    documents = {
        "front_desk": Document(name="front_desk", kind="persona", version=3),
        "docs/faq.pdf": Document(name="docs/faq.pdf", kind="knowledge", version=1),
        "docs/prices.xlsx": Document(name="docs/prices.xlsx", kind="knowledge", version=2),
        "docs/refunds.pdf": Document(name="docs/refunds.pdf", kind="knowledge", version=1),
        "unused": Document(name="unused", kind="persona", version=1),
    }
    loaded = load_file(FIXTURES / "sunrise_dental.yaml", documents=documents)
    assert set(loaded.documents) == {
        "front_desk",
        "docs/faq.pdf",
        "docs/prices.xlsx",
        "docs/refunds.pdf",
    }
    assert loaded.documents["front_desk"].version == 3


def test_inline_instructions_are_not_document_references() -> None:
    loaded = load_file(
        FIXTURES / "worker_kinds.yaml",
        documents={"products": Document(name="products", kind="persona", version=1)},
    )
    assert set(loaded.documents) == {"products"}
