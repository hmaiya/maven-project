"""unify — pull or inspect raw data, run the transform, query the result.

    unify fetch /tickets        pull an API endpoint into data/raw
    unify profile               what is in data/raw
    unify run                   transform + load into out/warehouse.db
    unify query "select ..."    ad hoc SQL
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .load import query as run_query
from .load import table_counts, write_all
from .profile import profile_dir

app = typer.Typer(add_completion=False, help=__doc__, no_args_is_help=True)
console = Console()

# Relative to where the command is run, not where the package is installed,
# so an installed wheel or a PyInstaller binary still finds ./data/raw.
# Override with env vars when the data lives elsewhere:
#   UNIFY_RAW_DIR=~/Downloads/exercise-data unify profile
ROOT = Path.cwd()
RAW_DIR = Path(os.environ.get("UNIFY_RAW_DIR", ROOT / "data" / "raw")).expanduser()
DB_PATH = Path(os.environ.get("UNIFY_DB", ROOT / "out" / "warehouse.db")).expanduser()


@app.command()
def fetch(
    endpoint: Annotated[str, typer.Argument(help="Path or full URL, e.g. /tickets")],
    name: Annotated[str | None, typer.Option(help="Output file stem (default: last path part)")] = None,
    records_at: Annotated[str | None, typer.Option(help="Dotted path to the record list")] = None,
    next_at: Annotated[str | None, typer.Option(help="Dotted path to the next-page URL")] = None,
    page_param: Annotated[str | None, typer.Option(help="Query param for page numbers")] = None,
) -> None:
    """Pull every page of an API endpoint into data/raw/<name>.jsonl.

    Auth and base URL come from UNIFY_API_TOKEN and UNIFY_API_BASE.
    """
    from .client import ApiClient, dump_jsonl

    stem = name or endpoint.rstrip("/").split("/")[-1].split("?")[0] or "records"
    out = RAW_DIR / f"{stem}.jsonl"
    client = ApiClient()
    try:
        count = dump_jsonl(
            client.paginate(endpoint, records_at=records_at, next_at=next_at, page_param=page_param), out
        )
    finally:
        client.close()
    console.print(f"[green]{count:,} records[/] -> {out}")


@app.command()
def profile(
    limit: Annotated[int, typer.Option(help="Max records per file, 0 = all")] = 0,
) -> None:
    """Inventory every file in data/raw: fields, null rates, shapes, enums."""
    profiles = profile_dir(RAW_DIR, limit=limit or None)
    if not profiles:
        console.print(f"[yellow]No readable files in {RAW_DIR}[/]  (csv, tsv, json, jsonl)")
        raise typer.Exit(0)

    for fp in profiles:
        header = f"{fp.path} · {fp.records:,} records · {fp.encoding}"
        if fp.delimiter:
            header += f" · delimiter {fp.delimiter!r}"
        console.rule(f"[bold]{header}")
        if fp.errors:
            console.print(f"[red]{len(fp.errors)} read error(s)[/]: {fp.errors[0]}")

        table = Table(header_style="bold")
        table.add_column("field", overflow="fold", max_width=36)
        table.add_column("null", justify="right")
        table.add_column("shape")
        table.add_column("distinct", justify="right")
        table.add_column("sample", overflow="fold", max_width=46)
        for entry in sorted(fp.fields.values(), key=lambda f: f.path):
            rate = entry.null_rate
            table.add_row(
                entry.path,
                f"[red]{rate:.0%}[/]" if rate > 0.25 else f"{rate:.0%}",
                entry.shape,
                f"{len(entry.distinct)}{'+' if entry.capped else ''}",
                " | ".join(entry.samples[:2]),
            )
        console.print(table)

        enums = [e for e in fp.fields.values() if e.enum_values]
        if enums:
            console.print("[bold]enum candidates[/] — each needs a mapping in transform.py:")
            for entry in sorted(enums, key=lambda f: f.path):
                console.print(f"  [cyan]{entry.path}[/]: {', '.join(entry.enum_values[:12])}")


@app.command()
def run() -> None:
    """Run transform() over data/raw and load the result into SQLite."""
    from .transform import transform

    tables = transform(RAW_DIR)
    if not tables:
        console.print("[yellow]transform() returned nothing.[/] Fill in src/unify/transform.py.")
        raise typer.Exit(0)

    counts = write_all(DB_PATH, tables)
    out = Table(title=f"loaded into {DB_PATH}", header_style="bold")
    out.add_column("table")
    out.add_column("rows", justify="right")
    for name, count in counts.items():
        style = "red" if name == "rejects" and count else ""
        out.add_row(f"[{style}]{name}[/]" if style else name, f"{count:,}")
    console.print(out)

    if rejects := counts.get("rejects"):
        console.print(f"[red]{rejects} rejected row(s)[/] — unify query \"select reason, count(*) "
                      'from rejects group by 1 order by 2 desc"')


@app.command(name="query")
def query_cmd(
    sql: Annotated[str, typer.Argument(help="SQL against out/warehouse.db")],
    limit: Annotated[int, typer.Option(help="Max rows to print")] = 25,
) -> None:
    """Ad hoc SQL against out/warehouse.db."""
    if not DB_PATH.exists():
        console.print(f"[red]No database at {DB_PATH}.[/] Run `unify run` first.")
        raise typer.Exit(1)
    rows = run_query(DB_PATH, sql)
    if not rows:
        console.print("[dim]0 rows[/]")
        return
    table = Table(header_style="bold")
    for column in rows[0]:
        table.add_column(str(column), overflow="fold")
    for row in rows[:limit]:
        table.add_row(*["" if v is None else str(v)[:120] for v in row.values()])
    console.print(table)
    console.print(f"[dim]{len(rows):,} rows, showing {min(limit, len(rows))}[/]")


@app.command()
def tables() -> None:
    """Row counts for everything in the database."""
    counts = table_counts(DB_PATH)
    if not counts:
        console.print(f"[yellow]No tables yet at {DB_PATH}.[/] Run `unify run`.")
        return
    console.print({name: f"{count:,}" for name, count in counts.items()})


if __name__ == "__main__":
    app()
