"""YOUR CODE GOES HERE. This is the only file the exercise should need.

`transform()` takes the raw data directory and returns table name -> rows.
Everything else in the project (reading files, loading SQLite, reporting) is
already wired up, so you can spend the hour on the mapping itself.

Pattern for each source file:

    1. read it        read_any(path) or read_csv / read_json / read_jsonl
    2. pull fields    pluck(row, "nested.field")
    3. clean them     n.clean / n.email / n.phone / n.timestamp / n.money_minor
    4. keep lineage   source_system + source_id on every row
    5. reject, never drop silently   reject(row, "why", source)
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from . import normalize as n
from .readers import iter_files, pluck, read_any

# Rows that could not be normalized. Written to the `rejects` table so nothing
# disappears without a reason attached.
REJECTS: list[dict[str, Any]] = []


def reject(row: Any, reason: str, source: str = "", source_id: Any = None) -> None:
    REJECTS.append(
        {"source": source, "source_id": str(source_id), "reason": reason, "payload": repr(row)[:2000]}
    )


def _reject_read_error(source: str, message: str) -> None:
    reject(None, message, source)


def transform(raw_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Return {table_name: rows}. Add one entry per canonical entity."""
    tables: dict[str, list[dict[str, Any]]] = {}

    # Uncomment and adapt once you have seen `unify profile` output:
    # tables["tickets"] = tickets(raw_dir)
    # tables["customers"] = customers(raw_dir)

    if REJECTS:
        tables["rejects"] = REJECTS
    return tables


# --- worked example, copy and adapt ---------------------------------------
# Deliberately not called by transform(). Delete it once your real mappers
# exist, or rename it and wire it in.

STATUS = n.mapper({
    "new": "new", "open": "open", "pending": "pending",
    "solved": "resolved", "closed": "closed",
    # numeric legacy codes, inverted scales and vendor synonyms go here too
    "1": "new", "2": "open", "3": "pending", "4": "resolved",
})

PRIORITY = n.mapper({
    "low": "low", "normal": "normal", "high": "high", "urgent": "urgent",
    # careful: P1 is the *most* urgent, the opposite end from "low"
    "p1": "urgent", "p2": "high", "p3": "normal", "p4": "low",
})


def tickets(raw_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in iter_files(raw_dir):
        source = path.stem
        on_error = partial(_reject_read_error, source)
        for raw in read_any(path, on_error=on_error):
            source_id = pluck(raw, "id")
            if source_id is None:
                reject(raw, "missing id", source)
                continue
            try:
                created = n.timestamp(pluck(raw, "created_at"))
            except ValueError as exc:
                reject(raw, str(exc), source, source_id)
                continue

            rows.append({
                "source_system": source,
                "source_id": str(source_id),
                "subject": n.clean(pluck(raw, "subject")),
                "status": STATUS(pluck(raw, "status")),
                "priority": PRIORITY(pluck(raw, "priority")),
                "customer_email": n.email(pluck(raw, "requester.email")),
                "customer_email_key": n.email_key(pluck(raw, "requester.email")),
                "body": n.strip_html(pluck(raw, "description")),
                "created_at": n.iso(created),
                "updated_at": n.iso(n.timestamp(pluck(raw, "updated_at"))),
            })
    return rows
