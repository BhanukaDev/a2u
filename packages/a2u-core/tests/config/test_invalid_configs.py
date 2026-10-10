"""Invalid configs produce the expected errors, at the expected lines.

Each file in fixtures/invalid marks the lines it expects errors on with a
trailing comment: `# error: <path> | <message substring>`. Several errors on
one line are separated by ` ; error: `. The test fails on any missing or
unexpected error.
"""

import re
from pathlib import Path

import pytest
from a2u_core.config import (
    ConfigError,
    Document,
    InvalidConfigError,
    Platform,
    load_config,
    load_file,
    validate_config,
)

FIXTURES = Path(__file__).parent / "fixtures" / "invalid"
SUNRISE = Path(__file__).parent / "fixtures" / "valid" / "sunrise_dental.yaml"

Expected = tuple[int, str, str]


def _expected(path: Path) -> list[Expected]:
    expected: list[Expected] = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        for marker in re.findall(r"error: (.*?)(?= ; error: |$)", line.partition("  # ")[2]):
            error_path, _, message = marker.partition(" | ")
            expected.append((number, "" if error_path == "<root>" else error_path, message))
    return expected


def _errors(
    path: Path,
    documents: dict[str, Document] | None = None,
    platform: Platform | None = None,
) -> list[ConfigError]:
    with pytest.raises(InvalidConfigError) as caught:
        load_file(path, documents=documents, platform=platform or Platform())
    return caught.value.errors


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.yaml")), ids=lambda p: p.stem)
def test_invalid_config(path: Path) -> None:
    expected = _expected(path)
    assert expected, f"{path.name} has no `# error:` markers"
    errors = _errors(path)
    unmatched = list(errors)
    missing: list[Expected] = []
    for line, error_path, message in expected:
        match = next(
            (
                e
                for e in unmatched
                if e.line == line and e.path == error_path and message in e.message
            ),
            None,
        )
        if match is None:
            missing.append((line, error_path, message))
        else:
            unmatched.remove(match)
    got = "\n".join(str(e) for e in errors)
    assert not missing, f"missing: {missing}\ngot:\n{got}"
    assert not unmatched, f"unexpected: {unmatched}\ngot:\n{got}"


def test_errors_are_sorted_and_formatted() -> None:
    errors = _errors(FIXTURES / "bad_values.yaml")
    lines = [e.line or 0 for e in errors]
    assert lines == sorted(lines)
    assert str(errors[0]).startswith("bad_values.yaml:3:")


def test_platform_voice_gate() -> None:
    errors = _errors(SUNRISE, platform=Platform(voice_languages=frozenset({"en"})))
    assert [(e.path, e.line) for e in errors] == [("languages.voice[1]", 8)]
    assert "not enabled for voice" in errors[0].message


def test_platform_text_gate() -> None:
    errors = _errors(SUNRISE, platform=Platform(text_languages=frozenset({"en", "ta"})))
    assert [(e.path, e.line) for e in errors] == [("languages.text[1]", 9)]
    assert "not enabled for text" in errors[0].message


def test_missing_and_mismatched_documents() -> None:
    documents = {
        "front_desk": Document(name="front_desk", kind="knowledge", version=1),
        "docs/faq.pdf": Document(name="docs/faq.pdf", kind="knowledge", version=1),
        "docs/prices.xlsx": Document(name="docs/prices.xlsx", kind="knowledge", version=1),
    }
    errors = _errors(SUNRISE, documents=documents)
    assert [(e.path, e.message) for e in errors] == [
        ("front.persona", "document 'front_desk' is a knowledge document, not a persona"),
        (
            "knowledge.refund_policy.sources[0]",
            "unknown document 'docs/refunds.pdf'",
        ),
    ]


def test_json_errors_have_lines() -> None:
    with pytest.raises(InvalidConfigError) as caught:
        load_config('{"agent": "x",\n "front": {}}', filename="agent.json")
    (error,) = caught.value.errors
    assert (error.path, error.line) == ("front", 2)


def test_plain_data_errors_have_paths_but_no_lines() -> None:
    with pytest.raises(InvalidConfigError) as caught:
        validate_config({"agent": "x", "front": {"instructions": "Hi.", "knowledge": ["faq"]}})
    (error,) = caught.value.errors
    assert (error.path, error.line) == ("front.knowledge[0]", None)


def test_top_level_must_be_a_mapping() -> None:
    with pytest.raises(InvalidConfigError) as caught:
        load_config("- a\n- b\n")
    assert "mapping" in caught.value.errors[0].message
