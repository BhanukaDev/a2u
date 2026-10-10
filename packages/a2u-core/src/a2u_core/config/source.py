"""Parse YAML or JSON config text into plain data plus the source position of every key.

Plain scalars are resolved with the YAML 1.2 core schema, not PyYAML's YAML 1.1
rules, so `claim_check: on` and `filler: off` stay strings (D-031). Explicit
tags, merge keys and non-scalar keys are not supported. Alias expansion and
input size are bounded because configs arrive from tenants over the API.
"""

import json
import re
from dataclasses import dataclass, field

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from a2u_core.config.errors import ConfigError, InvalidConfigError

MAX_BYTES = 1_000_000
MAX_NODES = 50_000

Path = tuple[str | int, ...]

_STR = "tag:yaml.org,2002:str"
_SEQ = "tag:yaml.org,2002:seq"
_MAP = "tag:yaml.org,2002:map"

_NULL = re.compile(r"~|null|Null|NULL|")
_TRUE = re.compile(r"true|True|TRUE")
_FALSE = re.compile(r"false|False|FALSE")
_INT = re.compile(r"[-+]?[0-9]+")
_OCT = re.compile(r"0o[0-7]+")
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_FLOAT = re.compile(r"[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?")
_INF = re.compile(r"([-+]?)\.(inf|Inf|INF)")
_NAN = re.compile(r"\.(nan|NaN|NAN)")


@dataclass(frozen=True)
class Mark:
    line: int  # 1-based
    column: int  # 1-based


@dataclass
class Parsed:
    data: object
    marks: dict[Path, Mark] = field(default_factory=lambda: {})


class _Loader(yaml.SafeLoader):
    """Composes nodes without implicit tag resolution; `_resolve` does it per YAML 1.2."""


_Loader.yaml_implicit_resolvers = {}


class _YamlSyntaxError(Exception):
    def __init__(self, error: ConfigError) -> None:
        self.error = error


def render_path(path: Path) -> str:
    out = ""
    for segment in path:
        out += f"[{segment}]" if isinstance(segment, int) else f".{segment}" if out else segment
    return out


def parse(text: str | bytes, filename: str) -> Parsed:
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise _invalid("", f"config is too large (over {MAX_BYTES} bytes)", None, filename)
    if filename.endswith(".json"):
        return _parse_json(text, filename)
    try:
        return _parse_yaml(text, filename)
    except _YamlSyntaxError as e:
        raise InvalidConfigError([e.error]) from None


def _parse_json(text: str, filename: str) -> Parsed:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        error = ConfigError("", e.msg, e.lineno, e.colno, filename)
        raise InvalidConfigError([error]) from None
    # JSON is valid YAML 1.2; parse it again only to learn where each key is.
    # PyYAML rejects a few JSON layouts (tabs as indentation); keep the data without lines then.
    try:
        return Parsed(data, _parse_yaml(text, filename).marks)
    except _YamlSyntaxError:
        return Parsed(data)


def _parse_yaml(text: str, filename: str) -> Parsed:
    try:
        loader = _Loader(text)
        try:
            node = loader.get_single_node()
        finally:
            loader.dispose()
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        line, column = (mark.line + 1, mark.column + 1) if mark else (None, None)
        message = e.problem or e.context or "invalid YAML"
        raise _YamlSyntaxError(ConfigError("", message, line, column, filename)) from None
    if node is None:
        raise _invalid("", "config is empty", None, filename)
    return _Converter(filename).run(node)


def _invalid(path: str, message: str, mark: Mark | None, filename: str) -> InvalidConfigError:
    line, column = (mark.line, mark.column) if mark else (None, None)
    return InvalidConfigError([ConfigError(path, message, line, column, filename)])


def _mark(node: Node) -> Mark:
    return Mark(node.start_mark.line + 1, node.start_mark.column + 1)


def _resolve(value: str) -> object:
    if _NULL.fullmatch(value):
        return None
    if _TRUE.fullmatch(value):
        return True
    if _FALSE.fullmatch(value):
        return False
    if _INT.fullmatch(value):
        return int(value)
    if _OCT.fullmatch(value):
        return int(value[2:], 8)
    if _HEX.fullmatch(value):
        return int(value[2:], 16)
    if _FLOAT.fullmatch(value):
        return float(value)
    if m := _INF.fullmatch(value):
        return float(f"{m[1]}inf")
    if _NAN.fullmatch(value):
        return float("nan")
    return value


class _Converter:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.marks: dict[Path, Mark] = {}
        self.count = 0
        self.active: set[int] = set()

    def run(self, root: Node) -> Parsed:
        self.marks[()] = _mark(root)
        return Parsed(self.convert(root, ()), self.marks)

    def fail(self, path: Path, message: str, node: Node) -> InvalidConfigError:
        return _invalid(render_path(path), message, _mark(node), self.filename)

    def convert(self, node: Node, path: Path) -> object:
        self.count += 1
        if self.count > MAX_NODES:
            raise self.fail(path, f"config is too large (over {MAX_NODES} values)", node)
        if id(node) in self.active:
            raise self.fail(path, "recursive alias", node)
        if isinstance(node, ScalarNode):
            if node.tag != _STR:
                raise self.fail(path, f"unsupported tag {node.tag}", node)
            value: str = node.value
            return _resolve(value) if node.style is None else value
        self.active.add(id(node))
        try:
            if isinstance(node, SequenceNode) and node.tag == _SEQ:
                return self.sequence(node, path)
            if isinstance(node, MappingNode) and node.tag == _MAP:
                return self.mapping(node, path)
            raise self.fail(path, f"unsupported tag {node.tag}", node)
        finally:
            self.active.discard(id(node))

    def sequence(self, node: SequenceNode, path: Path) -> list[object]:
        items: list[object] = []
        for i, child in enumerate(node.value):
            self.marks[(*path, i)] = _mark(child)
            items.append(self.convert(child, (*path, i)))
        return items

    def mapping(self, node: MappingNode, path: Path) -> dict[str, object]:
        out: dict[str, object] = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode) or key_node.tag != _STR:
                raise self.fail(path, "keys must be plain strings", key_node)
            key: str = key_node.value
            child = (*path, key)
            if key in out:
                first = self.marks[child].line
                raise self.fail(child, f"duplicate key {key!r} (first at line {first})", key_node)
            self.marks[child] = _mark(key_node)
            out[key] = self.convert(value_node, child)
        return out
