@AGENTS.md

# Working with Claude in this repo

- Start every new dataset with `uv run unify profile` and read the output
  before proposing a mapping. Base enum mappings on the observed values it
  lists, not on guesses.
- After `uv run unify run`, check the rejects table and the "unmapped" lines it
  prints. Each one is either a mapping gap to close or a documented `# TODO:`.
- Useful skills: `/data:explore-data` (profile a new file),
  `/data:validate-data` (sanity-check the loaded tables before demoing),
  `/data:write-query` (SQL against out/warehouse.db, SQLite dialect).
