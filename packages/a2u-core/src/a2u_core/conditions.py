"""Conditions (spec §5.3): a CEL subset over bound values and customer traits.

Used by flow `if:` steps, router rules and approval policies. Supported:
literals (strings, numbers, `true`, `false`, `null`, lists), references
(`order.total`, `items.0.sku`), `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `&&`,
`||`, `!`, unary `-`, `has(ref)` and `str.startsWith(prefix)`. No function
definitions, no I/O.

Semantics follow CEL where it matters for builders: values of different types
are never equal, ordering needs two numbers or two strings, `&&` and `||`
need bools and short-circuit left to right, and a missing reference is an
error except inside `has()`. The value of a condition must be a bool.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

MAX_LENGTH = 1000
MAX_DEPTH = 50

Env = Mapping[str, Any]


class ConditionSyntaxError(ValueError):
    def __init__(self, message: str, column: int) -> None:
        super().__init__(f"column {column}: {message}")
        self.message = message
        self.column = column  # 1-based


class ConditionError(ValueError):
    """The condition is well formed but cannot be evaluated against these values."""


# --- Lexer --------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    (?P<space>\s+)
    | (?P<number>[0-9]+(\.[0-9]+)?)
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    | (?P<op>==|!=|<=|>=|&&|\|\||[<>!().,\[\]-])
    """,
    re.VERBOSE,
)
_ESCAPE = re.compile(r"\\(.)")


@dataclass(frozen=True)
class _Token:
    kind: str  # number, name, string, op, end
    text: str
    column: int


def _lex(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            if text[pos] in "'\"":
                raise ConditionSyntaxError("unterminated string", pos + 1)
            raise ConditionSyntaxError(f"unexpected {text[pos]!r}", pos + 1)
        kind = cast(str, m.lastgroup)
        if kind != "space":
            tokens.append(_Token(kind, m[0], pos + 1))
        pos = m.end()
    tokens.append(_Token("end", "", len(text) + 1))
    return tokens


# --- Syntax tree --------------------------------------------------------------


class _Node:
    def eval(self, env: Env) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class _Lit(_Node):
    value: Any

    def eval(self, env: Env) -> Any:
        return self.value


@dataclass(frozen=True)
class _List(_Node):
    items: tuple[_Node, ...]

    def eval(self, env: Env) -> Any:
        return [item.eval(env) for item in self.items]


MISSING = object()


def select_path(env: Env, path: Sequence[str]) -> Any:
    """Follow `order.items.0.sku` through maps and lists; MISSING if any part is absent."""
    value: Any = env
    for i, part in enumerate(path):
        if isinstance(value, Mapping) and part in value:
            value = cast(Mapping[str, Any], value)[part]
        elif i > 0 and isinstance(value, list) and part.isdigit():
            items = cast(list[Any], value)
            if int(part) >= len(items):
                return MISSING
            value = items[int(part)]
        else:
            return MISSING
    return value


@dataclass(frozen=True)
class _Ref(_Node):
    path: tuple[str, ...]

    def eval(self, env: Env) -> Any:
        value = select_path(env, self.path)
        if value is MISSING:
            raise ConditionError(f"{'.'.join(self.path)} is not bound")
        return value


@dataclass(frozen=True)
class _Has(_Node):
    ref: _Ref

    def eval(self, env: Env) -> Any:
        return select_path(env, self.ref.path) is not MISSING


@dataclass(frozen=True)
class _StartsWith(_Node):
    target: _Node
    prefix: _Node

    def eval(self, env: Env) -> Any:
        target, prefix = self.target.eval(env), self.prefix.eval(env)
        if not isinstance(target, str) or not isinstance(prefix, str):
            raise ConditionError("startsWith needs strings")
        return target.startswith(prefix)


@dataclass(frozen=True)
class _Unary(_Node):
    op: str
    operand: _Node

    def eval(self, env: Env) -> Any:
        value = self.operand.eval(env)
        if self.op == "!":
            if not isinstance(value, bool):
                raise ConditionError(f"! needs a bool, got {_type(value)}")
            return not value
        if not _is_number(value):
            raise ConditionError(f"- needs a number, got {_type(value)}")
        return -value


@dataclass(frozen=True)
class _Logic(_Node):
    op: str  # && or ||
    left: _Node
    right: _Node

    def eval(self, env: Env) -> Any:
        left = self._bool(self.left.eval(env))
        if left == (self.op == "||"):
            return left
        return self._bool(self.right.eval(env))

    def _bool(self, value: Any) -> bool:
        if not isinstance(value, bool):
            raise ConditionError(f"{self.op} needs bool operands, got {_type(value)}")
        return value


@dataclass(frozen=True)
class _Compare(_Node):
    op: str
    left: _Node
    right: _Node

    def eval(self, env: Env) -> Any:
        left, right = self.left.eval(env), self.right.eval(env)
        match self.op:
            case "==":
                return _equal(left, right)
            case "!=":
                return not _equal(left, right)
            case "in":
                if isinstance(right, list):
                    return any(_equal(left, item) for item in cast(list[Any], right))
                if isinstance(right, Mapping):
                    return isinstance(left, str) and left in right
                raise ConditionError(f"in needs a list or a map on the right, got {_type(right)}")
            case _:
                return _order(self.op, left, right)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if _is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, Mapping):
        return "map"
    return type(value).__name__


def _equal(left: Any, right: Any) -> bool:
    if _is_number(left) and _is_number(right):
        return left == right
    if _type(left) != _type(right):
        return False
    if isinstance(left, list):
        a, b = cast(list[Any], left), cast(list[Any], right)
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(left, Mapping):
        a, b = cast(Mapping[str, Any], left), cast(Mapping[str, Any], right)
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return bool(left == right)


def _order(op: str, left: Any, right: Any) -> bool:
    comparable = (_is_number(left) and _is_number(right)) or (
        isinstance(left, str) and isinstance(right, str)
    )
    if not comparable:
        raise ConditionError(f"cannot compare {_type(left)} with {_type(right)} using {op}")
    match op:
        case "<":
            return left < right
        case "<=":
            return left <= right
        case ">":
            return left > right
        case _:
            return left >= right


# --- Parser -------------------------------------------------------------------

_COMPARISONS = frozenset({"==", "!=", "<", "<=", ">", ">=", "in"})
_KEYWORDS = {"true": True, "false": False, "null": None}


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0
        self.roots: set[str] = set()

    @property
    def tok(self) -> _Token:
        return self.tokens[self.pos]

    def take(self) -> _Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def at(self, text: str) -> bool:
        return self.tok.kind in ("op", "name") and self.tok.text == text

    def expect(self, text: str) -> None:
        if not self.at(text):
            self.fail(f"expected {text!r}")
        self.pos += 1

    def fail(self, message: str, tok: _Token | None = None) -> None:
        tok = tok or self.tok
        raise ConditionSyntaxError(message, tok.column)

    def parse(self) -> _Node:
        if self.tok.kind == "end":
            self.fail("empty condition")
        node = self.logic("||")
        if self.tok.kind != "end":
            self.fail(f"unexpected {self.tok.text!r}")
        return node

    def logic(self, op: str) -> _Node:
        node = self.logic("&&") if op == "||" else self.comparison()
        while self.at(op):
            self.pos += 1
            right = self.logic("&&") if op == "||" else self.comparison()
            node = _Logic(op, node, right)
        return node

    def comparison(self) -> _Node:
        node = self.unary()
        if self.tok.text in _COMPARISONS and self.tok.kind in ("op", "name"):
            op = self.take().text
            node = _Compare(op, node, self.unary())
        return node

    def unary(self) -> _Node:
        if self.at("!") or self.at("-"):
            op = self.take().text
            return _Unary(op, self.nested(self.unary))
        return self.postfix()

    def nested(self, parse: Any) -> _Node:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            self.fail("condition is nested too deeply")
        node = cast(_Node, parse())
        self.depth -= 1
        return node

    def postfix(self) -> _Node:
        node = self.primary()
        while self.at("."):
            dot = self.take()
            name = self.take()
            if self.at("("):
                if name.text != "startsWith":
                    self.fail(f"unknown method {name.text!r}", name)
                args = self.arguments()
                if len(args) != 1:
                    self.fail("startsWith takes one argument", name)
                node = _StartsWith(node, args[0])
            elif isinstance(node, _Ref) and name.kind in ("name", "number"):
                if name.kind == "number" and not name.text.isdigit():
                    self.fail("a list index is a whole number", name)
                node = _Ref((*node.path, name.text))
            else:
                self.fail("expected a field name after '.'", name if name.kind != "end" else dot)
        return node

    def arguments(self) -> list[_Node]:
        self.expect("(")
        args: list[_Node] = []
        while not self.at(")"):
            args.append(self.nested(lambda: self.logic("||")))
            if not self.at(","):
                break
            self.pos += 1
        self.expect(")")
        return args

    def primary(self) -> _Node:
        tok = self.tok
        match tok.kind:
            case "number":
                self.pos += 1
                return _Lit(float(tok.text) if "." in tok.text else int(tok.text))
            case "string":
                self.pos += 1
                return _Lit(_ESCAPE.sub(r"\1", tok.text[1:-1]))
            case "name" if tok.text in _KEYWORDS:
                self.pos += 1
                return _Lit(_KEYWORDS[tok.text])
            case "name" if self.tokens[self.pos + 1].text == "(":
                self.pos += 1
                if tok.text != "has":
                    self.fail(f"unknown function {tok.text!r}", tok)
                args = self.arguments()
                if len(args) != 1 or not isinstance(args[0], _Ref):
                    self.fail("has() takes a reference, such as has(order.refund_id)", tok)
                return _Has(cast(_Ref, args[0]))
            case "name":
                self.pos += 1
                self.roots.add(tok.text)
                return _Ref((tok.text,))
            case "op" if tok.text == "(":
                self.pos += 1
                node = self.nested(lambda: self.logic("||"))
                self.expect(")")
                return node
            case "op" if tok.text == "[":
                self.pos += 1
                items: list[_Node] = []
                while not self.at("]"):
                    items.append(self.nested(lambda: self.logic("||")))
                    if not self.at(","):
                        break
                    self.pos += 1
                self.expect("]")
                return _List(tuple(items))
            case "end":
                self.fail("expected a value")
            case _:
                self.fail(f"unexpected {tok.text!r}")
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class Condition:
    text: str
    references: frozenset[str]  # root names the condition reads, such as order or customer
    _root: _Node

    def evaluate(self, env: Env) -> bool:
        """Evaluate against bound values; raise ConditionError if that is not possible."""
        value = self._root.eval(env)
        if not isinstance(value, bool):
            raise ConditionError(f"{self.text!r} must be true or false, got {_type(value)}")
        return value


def parse_condition(text: str) -> Condition:
    """Parse a condition; raise ConditionSyntaxError with a 1-based column if malformed."""
    if len(text) > MAX_LENGTH:
        raise ConditionSyntaxError(f"condition is longer than {MAX_LENGTH} characters", 1)
    parser = _Parser(_lex(text))
    root = parser.parse()
    return Condition(text, frozenset(parser.roots), root)
