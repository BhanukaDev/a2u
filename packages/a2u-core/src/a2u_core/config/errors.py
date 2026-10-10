"""Config errors with a path into the document and, when parsed from text, a line."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigError:
    path: str  # dotted, with [i] for list items; "" is the document root
    message: str
    line: int | None = None  # 1-based
    column: int | None = None  # 1-based
    filename: str | None = None

    def __str__(self) -> str:
        where = self.filename or "<config>"
        if self.line is not None:
            where += f":{self.line}:{self.column or 1}"
        return f"{where}: {self.path or '<root>'}: {self.message}"


class InvalidConfigError(ValueError):
    """Raised with every error found, sorted by position."""

    def __init__(self, errors: list[ConfigError]) -> None:
        self.errors = sorted(errors, key=lambda e: (e.line or 0, e.column or 0, e.path))
        super().__init__("\n".join(str(e) for e in self.errors))
