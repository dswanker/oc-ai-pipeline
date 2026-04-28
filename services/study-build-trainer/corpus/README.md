# Corpus directory

**Gitignored — contents do not commit to the repo.**

This is where the trainer's local data lives:

- `embeddings.db` — SQLite + sqlite-vec database with all indexed pairs
- `embeddings.db-wal`, `embeddings.db-shm` — SQLite write-ahead-log files
- `cache/` — downloaded form designs and protocols, keyed by pair hash

Everything in here is regenerable:

- Vector data can be rebuilt from the raw files in `cache/` using
  `VectorStore.rebuild_index()`.
- Cache files can be re-downloaded from monday using each pair's
  `monday_item_id` and re-fetched from CT.gov by NCT ID.

Do **NOT** commit these files. They are large (1-10 MB per protocol PDF
× hundreds of pairs) and contain customer-derived data.

For backups, snapshot the cache directory and the DB file together —
they need to stay in sync.
