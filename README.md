# customer-data-unifier

Scaffolding for the Maven AGI coding exercise: pull customer data from an
API or the files they give you, normalize it, load it into SQLite, and show
what came through and what did not.

Setup is already done. **We start by writing logic in this file
`src/unify/transform.py`.**

---

## Game plan when the question lands (60–90 min)

| Time | Do | Say out loud |
|---|---|---|
| 0–5 min | Read the prompt twice. Ask: which entities? what is the canonical output? is it files or an API? any known quirks (timezones, currencies, IDs shared across systems)? | Restate the goal in one sentence and confirm it. |
| 5–10 | Get data into `data/raw/` (step 1), then `uv run unify profile` (step 2). | Walk through nulls, mixed shapes, and enum candidates. "Here's what I'm designing for." |
| 10–15 | Write down the canonical schema as a comment at the top of `transform.py`: one table per entity, with `source_system`/`source_id` on each. | Explain your join key (usually `email_key`) and why. |
| 15–45 | One entity end-to-end first: mapper → `unify run` → `unify query`. **Then** the second entity. Commit after each one works. | Narrate each mapping decision. When unsure, pick one, leave a `# TODO:` and move on. |
| 45–55 | Look at rejects and the `unmapped …` lines from `unify run`. Close the easy gaps; add `# TODO:` for the rest. Point one smoke test at your mapper. | "Nothing is dropped silently; here is every reject and why." |
| 55–60 | `make test`, commit, walk them through the output and the TODOs. | Use "Things to say out loud" below as the "what's next" list. |

Rules of thumb: get something running end to end before polishing anything. Prompt Claude
with the profile output pasted in ("map these fields to this schema"), then
review what it writes; don't accept a mapping you can't explain.

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
    tables["rejects"] = REJECTS
    return tables
```

`transform()` returns `{table_name: rows}`. Anything you return gets a table.

For each new entity, copy the pattern:

- **Scope the files.** `iter_files(raw_dir, "**/*ticket*")` reads only the ticket files. With a
  bare `iter_files(raw_dir)`, `customers.csv` would be mapped as tickets too.
- **Keep the per-record `try/except ValueError`**, so one bad field rejects one record
  instead of crashing the run.
- **Put vocabularies in module-level `n.mapper(...)` constants.** `unify run` finds them
  and prints any values that fell through to `unknown`.
- **Declare per-source quirks in the mapper:** `n.timestamp(v, dayfirst=True)`,
  `n.timestamp(v, tz=ZoneInfo("Europe/London"))`, `n.money_minor(v, currency="EUR")`.

### 4. Run it

```bash
uv run unify run
```

Prints a row count per table, flags rejects, and lists any **unmapped enum
values** (for example `unmapped STATUS -> 'unknown': escalated (3)`). You can re-run it
safely: it drops and rebuilds every table it writes. After a clean run the
`rejects` table is dropped, so an old reject never shows up as a new one.

### 5. Check the result

```bash
uv run unify tables
uv run unify query "select status, count(*) from tickets group by 1 order by 2 desc"
uv run unify query "select reason, count(*) from rejects group by 1 order by 2 desc"
uv run pytest
```

### 6. Package it

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
`UNIFY_RAW_DIR` to point somewhere else, and `UNIFY_DB` to write the database
somewhere else.

**D. Container (deploy as a scheduled batch job).** Docker isn't installed on
this machine, so this file has not been built here:

```bash
docker build -t unify .
docker run --rm -v "$PWD/data:/work/data" -v "$PWD/out:/work/out" unify run
docker run --rm -e UNIFY_API_BASE -e UNIFY_API_TOKEN -v "$PWD/data:/work/data" unify fetch /tickets
```

To deploy it for real, run the same image as a scheduled job (ECS scheduled task,
Cloud Run job, K8s CronJob) running `unify fetch … && unify run`, with the token
supplied from a secret manager and SQLite replaced by the team's warehouse. If
they ask how you'd deploy it, give that answer. Don't build it during the
exercise.

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
| `Dockerfile` | Batch-job image for deployment. Entrypoint is `unify`. |
| `AGENTS.md` / `CLAUDE.md` | Conventions for the AI assistant: reject rather than drop, lineage on every row, UTC, integer cents. `CLAUDE.md` imports `AGENTS.md`. |
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

## Using Claude during the exercise

`CLAUDE.md` loads the repo conventions automatically. The data plugin skills that help
most here:

| When | Invoke |
|---|---|
| New file you don't understand yet | `/data:explore-data data/raw/<file>`, alongside `unify profile` |
| Writing checks against the loaded tables | `/data:write-query` (tell it the dialect is SQLite) |
| Before you demo the result | `/data:validate-data` to catch duplicate keys, bad joins, and suspicious counts |

A prompt that works well: *"Here is the `unify profile` output and the target schema.
Write `customers()` in transform.py following the `tickets()` pattern. Use
n.mapper for every enum, and add `# TODO:` for anything ambiguous."* Then read
the diff before running it.

---

## One-time setup (already done)

Python 3.12 and [uv](https://docs.astral.sh/uv/) are installed and the venv is
built. To verify, or to rebuild on another machine:

```bash
uv sync
uv run pytest        # 68 tests, ~0.1s
```
