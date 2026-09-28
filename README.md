# customer-data-unifier

Scaffolding for the Maven AGI coding exercise: pull customer data from an
API or the files they give you, normalize it, load it into SQLite, and show
what came through and what did not.

Setup is already done. **The only file you should need to write in is
`src/unify/transform.py`.**

---

## Interview day: the six steps

### 1. Get the data into `data/raw/`

**If they give you files**, copy them in:

```bash
cp -r ~/Downloads/<their-data>/* data/raw/
```

Or leave them where they are and point at them instead:

```bash
export UNIFY_RAW_DIR=~/Downloads/<their-data>
```

**If they give you an API**, pull each endpoint into a JSONL file. Retries on
429/5xx and pagination are handled; the token never goes in code:

```bash
export UNIFY_API_BASE=https://api.example.com/v2
export UNIFY_API_TOKEN=...

uv run unify fetch /tickets --next-at next_page       # body has a next-page URL
uv run unify fetch /users --page-param page           # ?page=1,2,3… until empty
uv run unify fetch /orders --records-at data.items    # records nested in the body
```

Each writes `data/raw/<name>.jsonl`, so every step below treats API data and
file data the same way. Check the API docs for which pagination style it uses.
If the API is unusual (cursor tokens, Link headers), edit
`ApiClient.paginate` in `src/unify/client.py`.

### 2. Look at the data before writing anything (2 minutes)

```bash
uv run unify profile
```

Per file: record count, encoding, delimiter. Per field: null rate, value
shape (`epoch_s`, `iso8601`, `date_slash`, `email`, `money`, `html`, …),
distinct count, samples. At the bottom of each file it lists **enum
candidates** — the low-cardinality fields whose vocabularies you need to map.

Read this output out loud to the interviewer. It is the fastest way to show
you are making mapping decisions from evidence rather than guessing.

### 3. Write the mapping

Open `src/unify/transform.py`. There is a worked example (`tickets()`) using
every helper. Adapt it, then uncomment the line in `transform()` that calls it:

```python
def transform(raw_dir: Path) -> dict[str, list[dict]]:
    tables = {}
    tables["tickets"] = tickets(raw_dir)        # <- uncomment / rename
    if REJECTS:
        tables["rejects"] = REJECTS
    return tables
```

`transform()` returns `{table_name: rows}`. Anything you return gets a table.

### 4. Run it

```bash
uv run unify run
```

Prints a row count per table and flags rejects. Re-runnable: it drops and
rebuilds the tables, so a broken run is safe to just run again.

### 5. Check the result

```bash
uv run unify tables
uv run unify query "select status, count(*) from tickets group by 1 order by 2 desc"
uv run unify query "select reason, count(*) from rejects group by 1 order by 2 desc"
uv run pytest
```

### 6. Package it after the interview

All three options run the tests and lint first, and stop if anything fails.
Pick whichever one fits who's receiving it.

**A. Source zip (for a code reviewer).** Probably what they want, since
they'll be reading the code:

```bash
make test
git add -A && git commit -m "Maven AGI exercise"
git archive --format=zip HEAD -o ../maven-exercise.zip
```

They run `uv sync && uv run pytest && uv run unify run`. The zip includes
their data from `data/raw/` if you committed it; say so if it's confidential.

**B. Installable package (for someone with Python).**

```bash
make package                  # dist/customer_data_unifier-0.1.0-py3-none-any.whl
```

They install it once, and `unify` works from any directory:

```bash
uv tool install dist/customer_data_unifier-0.1.0-py3-none-any.whl
cd <folder with data/raw/> && unify run
```

Or run it without installing: `uvx --from dist/<wheel>.whl unify profile`.

**C. Standalone executable (no Python needed).**

```bash
make binary                   # dist/unify, one ~14 MB file
./dist/unify --help
```

Two caveats: it bundles `transform.py` as it is when you build, so rebuild
after any change; and it only runs on the OS and chip it was built on
(this Mac builds a macOS arm64 binary).

All three versions read `./data/raw` from the folder they're run in. Use
`UNIFY_RAW_DIR` to point somewhere else.

---

## What each file is

| File | Purpose |
|---|---|
| `src/unify/transform.py` | **Your code.** Maps raw records to canonical rows. Everything else supports this. |
| `src/unify/client.py` | API integration: auth header, retry/backoff on 429 and 5xx, pagination, writes JSONL. Powers `unify fetch`. |
| `src/unify/normalize.py` | Field cleaners: timestamps, emails, phones, money, HTML, enum mapping. |
| `src/unify/readers.py` | Loads CSV / TSV / JSON / JSONL. Detects encoding and delimiter, survives bad rows. |
| `src/unify/profile.py` | Powers `unify profile`. |
| `src/unify/load.py` | Writes rows to SQLite, infers columns, full reload each run. |
| `src/unify/cli.py` | The `unify` command. |
| `tests/` | Unit tests for the cleaners, readers and API client (mock server, no network), plus one end-to-end smoke test. |
| `Makefile` | `make test`, `make package`, `make binary`, `make clean`. |
| `data/raw/` | Empty. Their data goes here. |
| `out/warehouse.db` | Created by `unify run`. Gitignored. |

---

## Cheat sheet: `src/unify/normalize.py`

```python
from unify import normalize as n

n.clean("  N/A  ")                  # None — every spelling of null
n.strip_html("<p>hi &amp; bye</p>") # "hi & bye"
n.email(" Bob@ACME.com ")           # "bob@acme.com", None if implausible
n.email_key("bob+x@gmail.com")      # "bob@gmail.com"  <- use as the join key
n.phone("(617) 555-0142 ext. 9")    # "+16175550142"
n.name_key("O'Brien, Liam")         # "liam obrien"    <- fuzzy-match key
n.boolean("FALSE")                  # False

n.timestamp(1772000000)             # epoch s or ms, ISO, naive, slash dates
n.timestamp("03/05/2026", dayfirst=True)
n.iso(dt)                           # "2026-02-25T06:13:20Z" for storage

n.money_minor("$1,234.56")          # 123456 — integer cents, never float

status = n.mapper({"solved": "resolved", "3": "pending"})
status("Solved")                    # "resolved"
status.unmapped                     # {"escalated": 1} — values you missed
```

From `readers.py`: `read_any(path)`, `iter_files(dir)`, `pluck(row, "via.channel")`.

---

## Things to say out loud

The exercise asks you to note where more work is needed. These are already
true of this scaffold, so they are honest to raise:

- **Rejects, not drops.** Every record that fails normalization goes to the
  `rejects` table with a reason and the original payload, so nothing
  disappears silently and every rejection is replayable.
- **Full reload.** Tables are dropped and rebuilt each run, which is
  idempotent but cannot represent deletes and will not scale. Incremental sync
  needs a per-source high-water mark and upserts on `(source_system, source_id)`.
- **Timezones.** Naive timestamps are assumed to be `America/New_York`
  (`normalize.DEFAULT_TZ`). That assumption is declared in one place rather
  than buried, and it should really come from per-source config.
- **Money with no decimal point** is assumed to already be in minor units.
  If that is wrong, every whole-dollar amount is off by 100x and nothing in
  the data would reveal it — worth confirming with whoever owns the export.
- **Inverted scales.** A legacy `P1` is the *most* urgent; a helpdesk `low` is
  the *least*. Mapping them by ordinal position silently inverts urgency.
- **API sync is a full pull.** Incremental sync would store the max
  `updated_at` per endpoint and send it as a filter next time. OAuth token
  refresh isn't handled, so a 401 partway through a pull fails the run.
- **Identity resolution** is not built. If the exercise needs it: block on
  `email_key`, fall back to `phone`, then fuzzy `name_key` within a company,
  and record the match method and confidence for every link.

---

## One-time setup (already done)

Python 3.12 and [uv](https://docs.astral.sh/uv/) are installed and the venv is
built. To verify, or to rebuild on another machine:

```bash
uv sync
uv run pytest        # 65 tests, ~0.1s
```
