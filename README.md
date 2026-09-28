# customer-data-unifier

A small, testable pipeline that pulls customer-support data from source
systems (REST APIs or file exports), normalizes it into canonical tables, and
loads it into SQLite, with a full record of anything that could not be
normalized.

```
  API endpoints ──► unify fetch ──┐
                                  ├─► data/raw/ ──► unify profile   (inspect)
  CSV / TSV / JSON / JSONL ───────┘        │
                                           ▼
                                   transform.py  (map + normalize)
                                           │
                                           ▼
                             out/warehouse.db ◄── unify query / tables
                             ├── <entity tables>
                             └── rejects  (reason + original payload)
```

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest                       # unit + end-to-end tests

# put source files in data/raw/ (or: export UNIFY_RAW_DIR=/path/to/data)
uv run unify profile                # what is in the raw data
uv run unify run                    # transform + load into out/warehouse.db
uv run unify tables                 # row counts
uv run unify query "select status, count(*) from tickets group by 1"
uv run unify query "select reason, count(*) from rejects group by 1"
```

Pulling from an API (token is read from the environment, never from code):

```bash
export UNIFY_API_BASE=https://api.example.com/v2
export UNIFY_API_TOKEN=...
uv run unify fetch /tickets --next-at next_page     # next-URL pagination
uv run unify fetch /users --page-param page         # ?page=N pagination
uv run unify fetch /orders --records-at data.items  # nested record list
```

## Commands

| Command | What it does |
|---|---|
| `unify fetch <endpoint>` | Pages through an API endpoint into `data/raw/<name>.jsonl`. Handles retries with backoff on 429/5xx (honours `Retry-After`) and dedupes on `id`. |
| `unify profile` | Per file: record count, encoding, delimiter. Per field: null rate, value shape (`epoch_s`, `iso8601`, `email`, `money`, `html`, and mixed shapes such as `epoch_s+iso8601`), distinct count, samples, and low-cardinality enum candidates. |
| `unify run` | Runs `transform()` and reloads SQLite. Reports row counts, rejected records, and enum values that no mapping covered. |
| `unify query "<sql>"` / `unify tables` | Ad hoc inspection of the output. |

## Project layout

| Path | Responsibility |
|---|---|
| `src/unify/transform.py` | Source-to-canonical mapping. One function per entity. |
| `src/unify/normalize.py` | Pure field cleaners: timestamps, emails, phones, names, money, HTML, booleans, enum mapping. |
| `src/unify/readers.py` | CSV/TSV/JSON/JSONL readers with encoding and delimiter detection. Bad rows are reported, not fatal. |
| `src/unify/client.py` | HTTP client: bearer auth, retry/backoff, pagination, JSONL output. |
| `src/unify/profile.py` | Data profiling behind `unify profile`. |
| `src/unify/load.py` | SQLite writer with inferred schema and full reload. |
| `src/unify/cli.py` | The `unify` command-line interface. |
| `tests/` | Cleaners, readers, API client (mocked transport, no network) and an end-to-end smoke test. |

## Design decisions

- **Lineage on every row.** Each canonical row carries `source_system` and
  `source_id`, so any output can be traced back to the record it came from.
- **Reject, never drop.** A record that fails normalization goes to the
  `rejects` table with a reason and its original payload. One bad field
  rejects one record, never the whole run.
- **Unmapped values are visible.** Enum vocabularies go through `n.mapper()`.
  Values it doesn't recognise are counted and reported after each run instead
  of silently becoming `unknown`.
- **Canonical formats.** Timestamps are stored as UTC ISO-8601 (`...Z`). Epoch
  seconds and milliseconds, offsets, naive and slash dates are all accepted.
  Money is stored as integer minor units, never floats. Emails are
  lowercased, with a separate `email_key` (+tags and Gmail dots removed) as
  the join key.
- **Idempotent loads.** Each run drops and rebuilds its tables, so re-running
  is always safe.

## Assumptions and known limitations

- Naive timestamps are assumed to be `America/New_York`
  (`normalize.DEFAULT_TZ`). This should come from per-source configuration.
- A money value with no decimal separator is assumed to already be in minor
  units. This needs confirming against each source.
- Source priority scales can be inverted (legacy `P1` is most urgent, helpdesk
  `low` is least urgent). Mappings are explicit per source, never positional.
- **Full reload, full pull.** This can't represent deletes and won't scale to
  large volumes. See next steps.
- OAuth token refresh isn't handled; a 401 part-way through a pull fails the
  run.

## Next steps

1. **Incremental sync:** store a per-endpoint `updated_at` high-water mark, fetch
   only changes, and upsert on `(source_system, source_id)`.
2. **Identity resolution** across systems: block on `email_key`, fall back to
   phone, then fuzzy `name_key` within a company. Record the match method and
   confidence on each link.
3. **Per-source config** (timezone, date order, currency) instead of code
   constants.
4. **Warehouse target:** replace SQLite with Postgres or the team's warehouse
   and add explicit schemas and constraints.
5. **Scheduling:** run `unify fetch … && unify run` as a scheduled job. A
   `Dockerfile` is included as the starting point.

Open items in the code are marked with `# TODO:`.

## Packaging

```bash
make test       # pytest + ruff
make package    # wheel + sdist in dist/  ->  uv tool install dist/*.whl
make binary     # single-file executable at dist/unify (built for the host OS/arch)
docker build -t unify . && \
  docker run --rm -v "$PWD/data:/work/data" -v "$PWD/out:/work/out" unify run
```
