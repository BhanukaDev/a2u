"""Parsing YAML and JSON into plain data with source positions."""

import pytest
from a2u_core.config import InvalidConfigError
from a2u_core.config.source import parse


def test_plain_scalars_follow_yaml_1_2_core_schema() -> None:
    # YAML 1.1 would turn on/off/yes/no into booleans; the spec uses `claim_check: on`.
    parsed = parse(
        "a: on\nb: off\nc: no\nd: yes\ne: true\nf: False\ng: 10\nh: 1.5\ni: null\nj: ~\n"
        "k:\nl: 0x1F\nm: 2h\nn: '10'\no: +94\n",
        "agent.yaml",
    )
    assert parsed.data == {
        "a": "on",
        "b": "off",
        "c": "no",
        "d": "yes",
        "e": True,
        "f": False,
        "g": 10,
        "h": 1.5,
        "i": None,
        "j": None,
        "k": None,
        "l": 31,
        "m": "2h",
        "n": "10",
        "o": 94,
    }


def test_keys_are_always_strings() -> None:
    parsed = parse("200: ok\ntrue: yes\n", "agent.yaml")
    assert parsed.data == {"200": "ok", "true": "yes"}


def test_positions_point_at_keys_and_items() -> None:
    parsed = parse("front:\n  knowledge:\n    - faq\n    - prices\n", "agent.yaml")
    assert parsed.marks[()].line == 1
    assert parsed.marks[("front",)].line == 1
    assert parsed.marks[("front", "knowledge")].line == 2
    assert parsed.marks[("front", "knowledge", 1)].line == 4
    assert parsed.marks[("front", "knowledge", 1)].column == 7


def test_syntax_error_has_a_line() -> None:
    with pytest.raises(InvalidConfigError) as caught:
        parse("a: [1, 2\nb: 3\n", "agent.yaml")
    (error,) = caught.value.errors
    assert error.line is not None
    assert error.path == ""


def test_duplicate_keys_are_rejected() -> None:
    with pytest.raises(InvalidConfigError) as caught:
        parse("a: 1\nb: 2\na: 3\n", "agent.yaml")
    (error,) = caught.value.errors
    assert (error.path, error.line) == ("a", 3)
    assert "first at line 1" in error.message


def test_multiple_documents_are_rejected() -> None:
    with pytest.raises(InvalidConfigError):
        parse("a: 1\n---\nb: 2\n", "agent.yaml")


def test_empty_document_is_rejected() -> None:
    with pytest.raises(InvalidConfigError) as caught:
        parse("# nothing\n", "agent.yaml")
    assert "empty" in caught.value.errors[0].message


def test_custom_tags_are_rejected() -> None:
    with pytest.raises(InvalidConfigError) as caught:
        parse("a: !!python/object:os.system x\n", "agent.yaml")
    assert "tag" in caught.value.errors[0].message


def test_alias_expansion_is_bounded() -> None:
    bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n"
    for i, prev in zip("bcdefgh", "abcdefg", strict=True):
        bomb += f"{i}: &{i} [*{prev}, *{prev}, *{prev}, *{prev}, *{prev}, *{prev}, *{prev}]\n"
    with pytest.raises(InvalidConfigError) as caught:
        parse(bomb, "agent.yaml")
    assert "too large" in caught.value.errors[0].message


def test_oversized_input_is_rejected() -> None:
    with pytest.raises(InvalidConfigError) as caught:
        parse("a: " + "x" * 2_000_000, "agent.yaml")
    assert "too large" in caught.value.errors[0].message


def test_json_parses_with_positions() -> None:
    parsed = parse('{\n  "agent": "x",\n  "front": {"filler": "off"}\n}\n', "agent.json")
    assert parsed.data == {"agent": "x", "front": {"filler": "off"}}
    assert parsed.marks[("front", "filler")].line == 3


def test_json_syntax_error_has_a_line() -> None:
    with pytest.raises(InvalidConfigError) as caught:
        parse('{\n  "agent": "x",\n}\n', "agent.json")
    (error,) = caught.value.errors
    assert error.line == 3


def test_json_with_tabs_still_parses() -> None:
    parsed = parse('{\n\t"agent": "x"\n}', "agent.json")
    assert parsed.data == {"agent": "x"}
