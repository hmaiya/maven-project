"""End-to-end proof that read -> transform -> SQLite works.

Uses the example mapper in transform.py. When you replace that with the real
mapping, point this test at yours: it is the fastest way to know the whole
path still runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unify import transform as t
from unify.load import query, table_counts, write_all

RAW = {
    "tickets": [
        {
            "id": 1,
            "subject": "Refund  please",
            "status": "Solved",
            "priority": "P1",
            "created_at": 1772000000,
            "updated_at": "2026-03-05T14:22:00Z",
            "requester": {"email": " Bob+x@Gmail.com "},
            "description": "<p>hi &amp; bye</p>",
        },
        {"id": 2, "subject": "N/A", "status": "open", "created_at": "not-a-date"},
        {"subject": "no id at all", "created_at": 1772000000},
        {"id": 4, "subject": "Escalation", "status": "escalated", "created_at": 1772000000},
    ]
}


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    (tmp_path / "tickets.json").write_text(json.dumps(RAW))
    t.REJECTS.clear()
    t.STATUS.unmapped.clear()
    t.PRIORITY.unmapped.clear()
    return tmp_path


def test_good_rows_are_normalized(raw_dir: Path) -> None:
    rows = t.tickets(raw_dir)
    assert len(rows) == 2
    row = rows[0]
    assert row["status"] == "resolved"
    assert row["priority"] == "urgent"           # P1 is the most urgent, not the least
    assert row["subject"] == "Refund please"     # whitespace collapsed
    assert row["customer_email"] == "bob+x@gmail.com"
    assert row["customer_email_key"] == "bob@gmail.com"   # +tag and gmail dots folded
    assert row["body"] == "hi & bye"
    assert row["created_at"] == "2026-02-25T06:13:20Z"


def test_bad_rows_are_rejected_with_a_reason_not_dropped(raw_dir: Path) -> None:
    t.tickets(raw_dir)
    reasons = [r["reason"] for r in t.REJECTS]
    assert len(reasons) == 2
    assert any("missing id" in r for r in reasons)
    assert any("unparseable timestamp" in r for r in reasons)


def test_unmapped_enum_values_are_visible(raw_dir: Path) -> None:
    t.tickets(raw_dir)
    assert "escalated" in t.STATUS.unmapped


def test_rows_load_into_sqlite_and_reload_is_idempotent(raw_dir: Path, tmp_path: Path) -> None:
    db = tmp_path / "warehouse.db"
    tables = {"tickets": t.tickets(raw_dir), "rejects": t.REJECTS}

    assert write_all(db, tables)["tickets"] == 2
    write_all(db, tables)
    assert table_counts(db)["tickets"] == 2      # full reload, not an append

    loaded = query(db, "select customer_email_key from tickets where status = 'resolved'")
    assert loaded == [{"customer_email_key": "bob@gmail.com"}]
