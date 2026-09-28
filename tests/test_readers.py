"""Reader tests. Fixtures are written to tmp_path, never into data/raw."""

from __future__ import annotations

import json
from pathlib import Path

from unify.readers import iter_files, pluck, read_any, read_csv, read_json, read_jsonl


def test_semicolon_delimited_latin1_csv_is_read(tmp_path: Path) -> None:
    path = tmp_path / "legacy.csv"
    path.write_bytes("ID;NAME;CITY\n1;José Núñez;Málaga\n".encode("latin-1"))
    assert list(read_csv(path)) == [{"ID": "1", "NAME": "José Núñez", "CITY": "Málaga"}]


def test_ragged_rows_are_reported_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "ragged.csv"
    path.write_text("a,b\n1,2\n3,4,5\n")
    errors: list[str] = []
    assert len(list(read_csv(path, on_error=errors.append))) == 2
    assert errors and "ragged" in errors[0]


def test_truncated_final_jsonl_line_does_not_lose_the_file(tmp_path: Path) -> None:
    path = tmp_path / "conv.jsonl"
    path.write_text('{"id": 1}\n{"id": 2}\n{"id": 3, "created_at": 17')
    errors: list[str] = []
    assert [r["id"] for r in read_jsonl(path, on_error=errors.append)] == [1, 2]
    assert len(errors) == 1


def test_json_envelope_list_is_found_without_configuration(tmp_path: Path) -> None:
    path = tmp_path / "page.json"
    path.write_text(json.dumps({"next_page": None, "tickets": [{"id": 1}, {"id": 2}]}))
    assert [r["id"] for r in read_json(path)] == [1, 2]


def test_records_at_selects_a_nested_list(tmp_path: Path) -> None:
    path = tmp_path / "nested.json"
    path.write_text(json.dumps({"data": {"items": [{"id": 9}]}}))
    assert [r["id"] for r in read_json(path, records_at="data.items")] == [9]


def test_invalid_json_is_reported_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    errors: list[str] = []
    assert list(read_json(path, on_error=errors.append)) == []
    assert errors


def test_read_any_dispatches_on_suffix(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "b.jsonl").write_text('{"x": 1}\n')
    assert list(read_any(tmp_path / "a.csv")) == [{"x": "1"}]
    assert list(read_any(tmp_path / "b.jsonl")) == [{"x": 1}]


def test_iter_files_is_sorted_and_skips_unreadable_types(tmp_path: Path) -> None:
    for name in ["b.json", "a.json", ".gitkeep", "notes.txt"]:
        (tmp_path / name).write_text("[]" if name.endswith(".json") else "")
    assert [p.name for p in iter_files(tmp_path)] == ["a.json", "b.json"]


def test_pluck_walks_dicts_and_lists() -> None:
    payload = {"via": {"channel": "email"}, "comments": [{"body": "hi"}]}
    assert pluck(payload, "via.channel") == "email"
    assert pluck(payload, "comments.0.body") == "hi"
    assert pluck(payload, "via.missing.deep", "fallback") == "fallback"
