"""Load raw files into dicts. Knows about file formats, not about the data.

Defensive on purpose: a bad row is reported through `on_error` instead of
raising, so one broken line at the end of an export does not cost you the rest.
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

ErrorSink = Callable[[str], None]

# latin-1 never fails, so it is the catch-all and must come last.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def detect_encoding(path: Path) -> str:
    raw = path.read_bytes()[:65536]
    for encoding in ENCODINGS:
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def detect_delimiter(path: Path, encoding: str) -> str:
    with path.open(encoding=encoding, errors="replace", newline="") as fh:
        sample = fh.read(16384)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def read_csv(path: Path, *, on_error: ErrorSink | None = None) -> Iterator[dict[str, Any]]:
    """Rows as dicts, with encoding and delimiter detected per file."""
    encoding = detect_encoding(path)
    with path.open(encoding=encoding, errors="replace", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=detect_delimiter(path, encoding), restkey="_extra")
        for lineno, row in enumerate(reader, start=2):
            if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
                continue
            if "_extra" in row and on_error:
                on_error(f"{path.name}: ragged row at line {lineno}")
            yield {k: v for k, v in row.items() if k is not None}


def read_jsonl(path: Path, *, on_error: ErrorSink | None = None) -> Iterator[dict[str, Any]]:
    """One JSON object per line. A truncated last line is reported, not fatal."""
    with path.open(encoding=detect_encoding(path), errors="replace") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not (line := line.strip()):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                if on_error:
                    on_error(f"{path.name}: bad JSON at line {lineno}: {exc.msg}")
                continue
            if isinstance(obj, dict):
                yield obj


def read_json(
    path: Path, *, records_at: str | None = None, on_error: ErrorSink | None = None
) -> Iterator[dict[str, Any]]:
    """A list of records, or an envelope around one.

    `records_at` is a dotted path, e.g. "data.tickets". Without it the reader
    takes the first list-of-objects at the top level, which is right for the
    usual {"tickets": [...], "next_page": ...} shape.
    """
    try:
        doc = json.loads(path.read_text(encoding=detect_encoding(path), errors="replace"))
    except json.JSONDecodeError as exc:
        if on_error:
            on_error(f"{path.name}: invalid JSON: {exc.msg}")
        return

    node: Any = doc
    if records_at:
        for part in records_at.split("."):
            node = node.get(part) if isinstance(node, dict) else None
    elif isinstance(doc, dict):
        node = next((v for v in doc.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)

    if node is None:
        if on_error:
            on_error(f"{path.name}: no record list found (records_at={records_at!r})")
        return
    for item in [node] if isinstance(node, dict) else node:
        if isinstance(item, dict):
            yield item


READERS = {
    ".csv": read_csv,
    ".tsv": read_csv,
    ".jsonl": read_jsonl,
    ".ndjson": read_jsonl,
    ".json": read_json,
}


def read_any(path: Path, *, on_error: ErrorSink | None = None, **kwargs: Any) -> Iterator[dict[str, Any]]:
    """Dispatch on file suffix."""
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        if on_error:
            on_error(f"{path.name}: no reader for {path.suffix!r}")
        return iter(())
    if reader is read_json:
        return reader(path, on_error=on_error, **kwargs)
    return reader(path, on_error=on_error)


def iter_files(root: Path, pattern: str = "**/*") -> list[Path]:
    """Readable files under root, sorted so page order is deterministic."""
    return sorted(
        p for p in root.glob(pattern)
        if p.is_file() and p.suffix.lower() in READERS and not p.name.startswith(".")
    )


def pluck(payload: Any, path: str, default: Any = None) -> Any:
    """Read a dotted path out of nested dicts and lists.

        pluck(t, "via.channel")      pluck(t, "comments.0.body")
    """
    node = payload
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return default
    return default if node is None else node


# TODO: Excel, gzip and Parquet are not handled. Add them here if the exercise
# ships one. Large JSON files are read whole; switch to ijson above ~1GB.
