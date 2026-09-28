"""Write normalized rows to SQLite, and read them back.

Full reload each run: tables are dropped and rebuilt, so running twice gives
the same result and a failed run is safe to just re-run. Columns are inferred
from the rows, so no schema to maintain while the mapping is still moving.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SQL_TYPES = {int: "INTEGER", float: "REAL", bool: "INTEGER"}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def write_table(conn: sqlite3.Connection, name: str, rows: Sequence[dict[str, Any]]) -> int:
    """Replace `name` with `rows`. Columns are the union of all row keys."""
    if not rows:
        return 0

    columns: dict[str, str] = {}
    for row in rows:
        for key, value in row.items():
            if value is not None and columns.get(key, "TEXT") == "TEXT":
                columns[key] = SQL_TYPES.get(type(value), "TEXT")
            columns.setdefault(key, "TEXT")

    definition = ", ".join(f'"{column}" {sql_type}' for column, sql_type in columns.items())
    conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    conn.execute(f'CREATE TABLE "{name}" ({definition})')
    placeholders = ", ".join(["?"] * len(columns))
    conn.executemany(
        f'INSERT INTO "{name}" VALUES ({placeholders})',
        [tuple(_encode(row.get(c)) for c in columns) for row in rows],
    )
    conn.commit()
    return len(rows)


def write_all(db_path: Path, tables: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    conn = connect(db_path)
    try:
        return {name: write_table(conn, name, rows) for name, rows in tables.items()}
    finally:
        conn.close()


def _encode(value: Any) -> Any:
    """SQLite takes str, int, float, bytes and None. Everything else is JSON."""
    if isinstance(value, bool):
        return int(value)
    if value is None or isinstance(value, str | int | float | bytes):
        return value
    return json.dumps(value, default=str)


def query(db_path: Path, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def table_counts(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    conn = connect(db_path)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {n: conn.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


# TODO: full reload cannot represent deletes and will not scale past a few
# hundred thousand rows. If the exercise asks for incremental sync, add a
# per-source high-water mark table and upsert on (source_system, source_id).
