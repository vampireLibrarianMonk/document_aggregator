# JSON-alignment benchmark fixtures (self-generated, committed)

This is our **own** air-gap-clean benchmark for the deterministic JSON→golden-JSON
alignment slice. It is authored by us, contains no third-party data, and is safe
to commit and redistribute.

It deliberately exercises MaDI-style difficulty knobs (the *ideas* are not
copyrightable; the data/code are ours):

- **renamed keys** — `game_title` → `name`, `made_by` → `developer`
- **type changes** — year as a string `"2001"` → integer-pattern target
- **date formats** — `"10/09/2007"` → ISO / year
- **value aliases / controlled vocab** — `PC` → `Windows PC`, `K-A` → `E`
- **split/merge** — `"Shooter;Action"` → `["Shooter","Action"]`
- **irrelevant fields** — `internal_id`, `scraped_at` have no target
- **opaque names** — `hw` → `platform`

## Files

- `target_schema.json` — the golden target JSON Schema (what we align *to*)
- `source_records.json` — the source batch (one object per entity)
- `schema_match_gold.json` — gold `(source_path, target_field)` correspondences
  plus `unmapped_source_fields` (fields that correctly map to nothing)
- `normalization_gold.json` — `(target_field, raw_value, expected_value, category)`
  value-normalization expectations

Mirrors the shape of MaDI's `sm_mapping_gold.json` + `normalization/test.csv`
so the same scorers run against either (MaDI only as an optional external
comparison via `MADI_BENCH_PATH`; never committed).
