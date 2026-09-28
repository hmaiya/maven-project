"""HTTP client for pulling records out of a customer-style API.

Handles the parts every integration needs: auth header, retries with backoff
on 429/5xx (honouring Retry-After), and pagination. Records are written to
data/raw/ as JSONL so `unify profile` and transform.py treat API data exactly
like file data.

Configure with env vars so no secret lands in code:
    UNIFY_API_BASE   e.g. https://api.example.com/v2
    UNIFY_API_TOKEN  sent as "Authorization: Bearer <token>"
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from .readers import pluck

RETRY_STATUSES = {429, 500, 502, 503, 504}


class ApiClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        max_retries: int = 5,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Accept": "application/json"}
        token = token or os.environ.get("UNIFY_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.max_retries = max_retries
        self.http = httpx.Client(
            base_url=base_url or os.environ.get("UNIFY_API_BASE", ""),
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """GET and decode JSON, retrying transient failures with backoff."""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.http.get(url, params=params)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                time.sleep(min(2**attempt, 30))
                continue
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                time.sleep(_retry_after(response) or min(2**attempt, 30))
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("unreachable")

    def paginate(
        self,
        url: str,
        *,
        records_at: str | None = None,
        next_at: str | None = None,
        page_param: str | None = None,
        params: dict[str, Any] | None = None,
        max_pages: int = 10_000,
    ) -> Iterator[dict[str, Any]]:
        """Yield every record across pages.

        Pick the style the API uses:
          next_at="next_page"          body holds the next URL (or cursor links)
          page_param="page"            ?page=1,2,3… until a page comes back empty
          neither                      single request, no pagination

        `records_at` is the dotted path to the list in the body, e.g. "data" or
        "tickets". Without it, the first list of objects in the body is used.

        TODO: cursor-token APIs (a cursor value you send back as a param, not a
        URL) and Link-header pagination are not handled yet.
        """
        query: dict[str, Any] | None = dict(params or {})
        page = 1
        next_url: str | None = url
        for _ in range(max_pages):
            if page_param and query is not None:
                query[page_param] = page
            body = self.get(next_url, params=query or None)
            records = _records(body, records_at)
            yield from records

            if next_at:
                next_url = pluck(body, next_at)
                # The next URL carries its own query string. Passing any params,
                # even {}, makes httpx replace it and re-fetch page one forever.
                query = None
                if not next_url:
                    return
            elif page_param:
                if not records:
                    return
                page += 1
            else:
                return

    def close(self) -> None:
        self.http.close()


def _records(body: Any, records_at: str | None) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    node = pluck(body, records_at) if records_at else next(
        (v for v in body.values() if isinstance(v, list) and (not v or isinstance(v[0], dict))), None
    )
    return [r for r in node or [] if isinstance(r, dict)]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return min(float(value), 60.0) if value else None
    except ValueError:
        return None  # HTTP-date form; fall back to exponential backoff


def dump_jsonl(records: Iterator[dict[str, Any]], path: Path) -> int:
    """Write records to JSONL, deduped on `id` since paginated reads overlap."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[Any] = set()
    count = 0
    with path.open("w") as fh:
        for record in records:
            key = record.get("id")
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            fh.write(json.dumps(record, default=str) + "\n")
            count += 1
    return count


# TODO: every fetch is a full pull. For incremental sync, persist the max
# updated_at per endpoint and send it as a filter param on the next run.
# TODO: OAuth token refresh is not handled; a 401 mid-pull will fail the run.
