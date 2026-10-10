"""Load an agent config from YAML or JSON, validate it and report errors with lines."""

from collections.abc import Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path as FilePath
from typing import cast

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from a2u_core.config.checks import Document, Platform, check
from a2u_core.config.errors import ConfigError, InvalidConfigError
from a2u_core.config.models import AgentConfig
from a2u_core.config.source import Mark, Path, parse, render_path

DEFAULT_PLATFORM = Platform()
# Keys that sit next to the binding name in `collect` and `choose` steps.
_BINDING_OPTIONS = frozenset({"retries", "timeout", "readback", "max_options"})


@dataclass(frozen=True)
class LoadedConfig:
    config: AgentConfig
    documents: Mapping[str, Document]  # the documents the config references, by name


def load_file(
    path: str | PathLike[str],
    *,
    documents: Mapping[str, Document] | None = None,
    platform: Platform = DEFAULT_PLATFORM,
) -> LoadedConfig:
    file = FilePath(path)
    return load_config(
        file.read_text(encoding="utf-8"), filename=file.name, documents=documents, platform=platform
    )


def load_config(
    text: str | bytes,
    *,
    filename: str = "agent.yaml",
    documents: Mapping[str, Document] | None = None,
    platform: Platform = DEFAULT_PLATFORM,
) -> LoadedConfig:
    """Parse YAML (or JSON, by `.json` filename) and validate it.

    `documents` are the workspace's documents by name; None skips document
    resolution. Raises InvalidConfigError with every error found.
    """
    parsed = parse(text, filename)
    return _validate(parsed.data, parsed.marks, filename, documents, platform)


def validate_config(
    data: object,
    *,
    documents: Mapping[str, Document] | None = None,
    platform: Platform = DEFAULT_PLATFORM,
) -> LoadedConfig:
    """Validate already-parsed data, e.g. `agent_versions.spec`. Errors have paths but no lines."""
    return _validate(data, {}, None, documents, platform)


def _validate(
    data: object,
    marks: Mapping[Path, Mark],
    filename: str | None,
    documents: Mapping[str, Document] | None,
    platform: Platform,
) -> LoadedConfig:
    if not isinstance(data, dict):
        mark = marks.get(())
        raise InvalidConfigError(
            [_error("", "the config must be a mapping at the top level", mark, filename)]
        )
    data = cast(dict[str, object], data)
    try:
        config = AgentConfig.model_validate(data)
    except ValidationError as e:
        errors = {_from_pydantic(err, data, marks, filename) for err in e.errors()}
        raise InvalidConfigError(list(errors)) from None
    problems, used = check(config, documents, platform)
    if problems:
        raise InvalidConfigError(
            [
                _error(render_path(p.path), p.message, _locate(p.path, data, marks)[1], filename)
                for p in problems
            ]
        )
    return LoadedConfig(config, used)


def _error(path: str, message: str, mark: Mark | None, filename: str | None) -> ConfigError:
    line, column = (mark.line, mark.column) if mark else (None, None)
    return ConfigError(path, message, line, column, filename)


def _from_pydantic(
    err: ErrorDetails, data: object, marks: Mapping[Path, Mark], filename: str | None
) -> ConfigError:
    loc = err["loc"]
    path, mark = _locate(loc, data, marks)
    message = err["msg"].removeprefix("Value error, ")
    last = loc[-1] if loc else None
    if err["type"] == "missing" and isinstance(last, str):
        path = (*path, last)
        message = f"missing required field {last!r}"
    elif err["type"] == "extra_forbidden" and isinstance(last, str):
        message = f"unknown field {last!r}"
    return _error(render_path(path), message, mark, filename)


def _binding_key(node: dict[str, object]) -> str | None:
    names = [k for k in node if k not in _BINDING_OPTIONS]
    return names[0] if len(names) == 1 else None


def _locate(
    loc: tuple[str | int, ...], data: object, marks: Mapping[Path, Mark]
) -> tuple[Path, Mark | None]:
    """Follow an error location through the raw data.

    Pydantic locations include union tags and fields renamed by validators;
    segments that are not in the data are skipped. A collect step's `type` is
    the value under its binding name.
    """
    path: Path = ()
    node = data
    for segment in loc:
        if isinstance(node, dict):
            mapping = cast(dict[str, object], node)
            if isinstance(segment, str) and segment in mapping:
                key = segment
            elif segment == "type" and (binding := _binding_key(mapping)) is not None:
                key = binding
            else:
                continue
            node = mapping[key]
            path = (*path, key)
        elif isinstance(node, list):
            items = cast(list[object], node)
            if isinstance(segment, int) and 0 <= segment < len(items):
                node = items[segment]
                path = (*path, segment)
    for depth in range(len(path), -1, -1):
        if (mark := marks.get(path[:depth])) is not None:
            return path, mark
    return path, None
