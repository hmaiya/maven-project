# customer-data-unifier

Scaffolding for the Maven AGI coding exercise: read customer data from
whatever files they give you, normalize it, load it into SQLite, and show what
came through and what did not.

Setup is already done. **The only file you should need to write in is
`src/unify/transform.py`.**

---

## Interview day: the six steps

### 1. Drop the data in

```bash
cp -r ~/Downloads/<their-data>/* data/raw/
```

Or leave it where it is and point at it instead:

```bash
export UNIFY_RAW_DIR=~/Downloads/<their-data>
```

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

### 6. Package it for them

```bash
uv run pytest && uv run ruff check .    # green before you send
uv build                                # dist/*.whl + dist/*.tar.gz
```

Or just zip the repo — it has no system dependencies beyond Python 3.12:

```bash
git add -A && git commit -m "Maven AGI exercise"
git archive --format=zip HEAD -o ../maven-exercise.zip
```

Whoever receives it runs `uv sync && uv run pytest && uv run unify run`.

---

## What each file is

| File | Purpose |
|---|---|
| `src/unify/transform.py` | **Your code.** Maps raw records to canonical rows. Everything else supports this. |
| `src/unify/normalize.py` | Field cleaners: timestamps, emails, phones, money, HTML, enum mapping. |
| `src/unify/readers.py` | Loads CSV / TSV / JSON / JSONL. Detects encoding and delimiter, survives bad rows. |
| `src/unify/profile.py` | Powers `unify profile`. |
| `src/unify/load.py` | Writes rows to SQLite, infers columns, full reload each run. |
| `src/unify/cli.py` | The `unify` command. |
| `tests/` | Unit tests for the cleaners and readers, plus one end-to-end smoke test. |
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
- **Identity resolution** is not built. If the exercise needs it: block on
  `email_key`, fall back to `phone`, then fuzzy `name_key` within a company,
  and record the match method and confidence for every link.

---

## One-time setup (already done)

Python 3.12 and [uv](https://docs.astral.sh/uv/) are installed and the venv is
built. To verify, or to rebuild on another machine:

```bash
uv sync
uv run pytest        # 60 tests, ~0.1s
```
