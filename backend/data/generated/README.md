`wind_fields.jsonl` lists Wind `(table, field)` identifiers only. The current
snapshot has 7,487 rows and 7,404 case-insensitive unique `(table, field)` pairs;
83 rows are duplicate identifier pairs retained from the source catalogue. The
search layer de-duplicates identifiers before returning candidates.

`wind_metadata.jsonl` is generated locally from the licensed dictionary and
must not be committed (Chinese names and descriptions).
