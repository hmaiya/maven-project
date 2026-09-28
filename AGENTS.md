# Conventions

Time-boxed coding exercise: read messy customer data, normalize it, load it.

- Write mapping code in `src/unify/transform.py`. Do not add new modules unless
  a file is genuinely doing a different job.
- Reuse the helpers in `src/unify/normalize.py` instead of writing new parsing
  logic. If a helper is missing a case, extend it and add a test.
- Never drop a record silently. Call `reject(row, reason, source, source_id)`
  so it lands in the `rejects` table with its payload.
- Every canonical row carries `source_system` and `source_id`.
- Timestamps: store `n.iso(...)`, which is UTC with a trailing Z. Money:
  integer minor units, never float.
- Map enum vocabularies with `n.mapper(...)` so unmapped values are recorded
  rather than silently becoming "unknown".
- Comment where more work is needed with `# TODO:` and say what the next
  person should do, not what the code does.
- Keep comments to constraints and decisions a reader cannot infer. No
  narration.
- Before saying done: `uv run pytest && uv run ruff check .`
