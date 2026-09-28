"""Answer "what is actually in this data" before writing any mapping.

Per file: record count, encoding, delimiter. Per field: null rate, value
shape, distinct count, samples, and — most useful — which fields are small
enough to be enums that need a vocabulary mapping.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .normalize import NULLISH
from .readers import detect_delimiter, detect_encoding, iter_files, read_any

MAX_DISTINCT = 60

SHAPES: list[tuple[str, re.Pattern[str]]] = [
    ("iso8601", re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")),
    ("date_ymd", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("date_slash", re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}")),
    ("epoch_s", re.compile(r"^\d{9,10}$")),
    ("epoch_ms", re.compile(r"^\d{12,13}$")),
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")),
    ("phone", re.compile(r"^\+?[\d\s().\-]{9,}$")),
    # Money needs a symbol, a currency code, or cents — otherwise a bare
    # integer id looks like an amount.
    ("money", re.compile(r"^[$€£¥]\s?[\d,]+(\.\d{1,2})?$|^[\d,]+\.\d{2}(\s?[A-Z]{3})?$")),
    ("html", re.compile(r"<[a-z/][^>]*>", re.I)),
    ("integer", re.compile(r"^-?\d+$")),
    ("decimal", re.compile(r"^-?\d+\.\d+$")),
]


@dataclass
class Field:
    path: str
    count: int = 0
    nulls: int = 0
    shapes: Counter[str] = field(default_factory=Counter)
    distinct: set[str] = field(default_factory=set)
    capped: bool = False
    samples: list[str] = field(default_factory=list)

    def observe(self, value: Any) -> None:
        self.count += 1
        if value is None or (isinstance(value, str) and value.strip().lower() in NULLISH):
            self.nulls += 1
            return
        text = str(value)
        self.shapes[_shape(text)] += 1
        if len(self.distinct) < MAX_DISTINCT:
            self.distinct.add(text[:120])
        else:
            self.capped = True
        if len(self.samples) < 3 and text not in self.samples:
            self.samples.append(text[:80])

    @property
    def null_rate(self) -> float:
        return self.nulls / self.count if self.count else 0.0

    @property
    def shape(self) -> str:
        return self.shapes.most_common(1)[0][0] if self.shapes else "-"

    @property
    def enum_values(self) -> list[str] | None:
        """Values in full when the field looks like a controlled vocabulary.

        Low cardinality, text or numeric codes, and not simply unique per row
        (which would make it an identifier rather than a vocabulary).
        """
        if self.capped or not 1 < len(self.distinct) <= 25:
            return None
        if self.shape not in {"text", "integer"}:
            return None
        if len(self.distinct) == self.count - self.nulls and self.count > 5:
            return None
        return sorted(self.distinct)


@dataclass
class FileProfile:
    path: str
    encoding: str
    delimiter: str | None
    records: int
    errors: list[str]
    fields: dict[str, Field]


def _shape(text: str) -> str:
    for name, pattern in SHAPES:
        if pattern.search(text) if name == "html" else pattern.match(text.strip()):
            return name
    return "text"


def flatten(payload: Any, prefix: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    """Nested structures to dotted paths. Lists show their length plus the
    shape of the first element, which is enough to know what is in there."""
    out = {} if out is None else out
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict | list):
                flatten(value, path, out)
            else:
                out[path] = value
    elif isinstance(payload, list):
        out[f"{prefix}[]"] = len(payload)
        if payload:
            flatten(payload[0], f"{prefix}[0]", out) if isinstance(payload[0], dict | list) else None
    else:
        out[prefix] = payload
    return out


def profile_file(path: Path, root: Path, limit: int | None = None) -> FileProfile:
    errors: list[str] = []
    encoding = detect_encoding(path)
    fields: dict[str, Field] = {}
    records = 0

    for payload in read_any(path, on_error=errors.append):
        records += 1
        for key, value in flatten(payload).items():
            fields.setdefault(key, Field(path=key)).observe(value)
        if limit and records >= limit:
            break

    return FileProfile(
        path=str(path.relative_to(root)),
        encoding=encoding,
        delimiter=detect_delimiter(path, encoding) if path.suffix.lower() in {".csv", ".tsv"} else None,
        records=records,
        errors=errors,
        fields=fields,
    )


def profile_dir(root: Path, limit: int | None = None) -> list[FileProfile]:
    return [profile_file(p, root, limit) for p in iter_files(root)]
