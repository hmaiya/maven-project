"""API client tests against an in-process mock server. No network."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from unify import client as c


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(c.time, "sleep", lambda _s: None)


def make_client(handler) -> c.ApiClient:
    return c.ApiClient("https://api.test", token="secret", transport=httpx.MockTransport(handler))


def test_next_url_pagination_follows_every_page_and_sends_auth() -> None:
    pages = {
        "/tickets": {"tickets": [{"id": 1}, {"id": 2}], "next_page": "https://api.test/tickets?page=2"},
        "/tickets?page=2": {"tickets": [{"id": 3}], "next_page": None},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        return httpx.Response(200, json=pages[key])

    ids = [r["id"] for r in make_client(handler).paginate("/tickets", next_at="next_page")]
    assert ids == [1, 2, 3]


def test_page_number_pagination_stops_on_an_empty_page() -> None:
    data = {1: [{"id": 1}], 2: [{"id": 2}], 3: []}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": data[int(request.url.params["page"])]})

    ids = [r["id"] for r in make_client(handler).paginate("/users", records_at="data", page_param="page")]
    assert ids == [1, 2]


def test_rate_limits_and_server_errors_are_retried() -> None:
    responses = iter([
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(503),
        httpx.Response(200, json=[{"id": 1}]),
    ])
    assert list(make_client(lambda _r: next(responses)).paginate("/x")) == [{"id": 1}]


def test_client_errors_are_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        list(make_client(handler).paginate("/missing"))
    assert len(calls) == 1


def test_dump_jsonl_dedupes_overlapping_pages(tmp_path: Path) -> None:
    out = tmp_path / "t.jsonl"
    assert c.dump_jsonl(iter([{"id": 1}, {"id": 2}, {"id": 1}]), out) == 2
    assert [json.loads(line)["id"] for line in out.read_text().splitlines()] == [1, 2]
