# Diesel (2.x)

## Layout and naming

- `migrations/` at the crate root; found via `--migration-dir`, `MIGRATION_DIRECTORY`, `[migrations_directory] dir` in `diesel.toml`, or by searching upward from the current directory.
- One folder per migration: `YYYY-MM-DD-HHMMSS_name/` with `up.sql` and `down.sql`, optionally `metadata.toml` containing `run_in_transaction = false`. The version is everything before the first `_` with dashes removed (`2024-01-05-123000_x` is version `20240105123000`); it is stored and compared as a string, so keep 14 digits.
- `00000000000000_diesel_initial_setup/` is created by `diesel setup` on Postgres and defines `diesel_manage_updated_at(regclass)` and `diesel_set_updated_at()`. Keep it as is: version 0 sorts first, other migrations call `SELECT diesel_manage_updated_at('t')`, and `diesel setup` recreates it if missing. MySQL and SQLite projects have no initial setup.
- Any subfolder with an `up.sql` is a migration; a folder without one fails with `UnknownMigrationFormat`, so the archive must live outside `migrations/`.
- Kept folders keep their exact name; `up.sql` and `down.sql` are rewritten, `metadata.toml` stays if the original had one. `00000000000000_diesel_initial_setup/` is a kept entry in the plan (it created the two helper functions): copy it untouched. New folders (`deferred_foreign_keys`, `seed_reference_data`, `functions_and_triggers`, moved views, split-out tables) are numbered after the last original, e.g. `2023-11-20-100001_deferred_foreign_keys/`; a split-out table gets the next free second after its original when that sorts before the next one.

## Replay order

Ascending by version string. Pending migrations are the folders whose version has no row in `__diesel_schema_migrations`; nothing else decides order. Transcribe in folder order. The consolidated set keeps that order: kept folders at their original versions, new ones after.

## Reading originals into ops

Plain SQL per backend (`diesel.toml` and the `DATABASE_URL` scheme say which). Idioms worth expanding:

- `id SERIAL PRIMARY KEY` -> Column `{ "type": "serial", "primary": true, "auto_increment": true }`; `BIGSERIAL` likewise with `"type": "bigserial"`
- `user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE` -> Column + ForeignKey `{ "name": null, "columns": ["user_id"], "ref_table": "users", "ref_columns": ["id"], "on_delete": "cascade" }`
- `SELECT diesel_manage_updated_at('posts');` -> `create_trigger` on `posts` with `{ "name": "set_updated_at", "raw": "SELECT diesel_manage_updated_at('posts')", "functions": ["diesel_manage_updated_at", "diesel_set_updated_at"] }`; the functions themselves come from migration 0, so record them with `create_function` when transcribing that folder
- `CREATE TYPE status AS ENUM (...)` -> `create_type`; the column that uses it gets `"type_ref": "type:status"`
- SQLite "rebuild" migrations (`CREATE TABLE new_x ...; INSERT INTO new_x SELECT ...; DROP TABLE x; ALTER TABLE new_x RENAME TO x`) -> the net effect as `alter_column` / `drop_column` ops on `x`, plus a `data` op of kind `fix` if the copy transforms values
- `down.sql` is not transcribed; use it to confirm what `up.sql` created

## Writing a consolidated table migration

`migrations/2023-01-10-100000_create_invoices/up.sql`, the kept folder that created the table:

```sql
-- Consolidated: final invoices (created here); folded: 2023-03-05-100100_add_invoice_status,
--   2023-08-01-120000_invoice_customer_fk, 2023-11-20-100000_invoice_indexes
CREATE TABLE invoices (
    id            BIGSERIAL PRIMARY KEY,
    customer_id   BIGINT NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
    number        VARCHAR(32) NOT NULL,
    status        invoice_status NOT NULL DEFAULT 'draft',
    amount_cents  BIGINT NOT NULL DEFAULT 0,
    notes         TEXT,
    issued_on     DATE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT invoices_number_key UNIQUE (number),
    CONSTRAINT invoices_amount_nonnegative CHECK (amount_cents >= 0)
);
CREATE INDEX invoices_customer_id_status_idx ON invoices (customer_id, status);
COMMENT ON TABLE invoices IS 'One row per issued invoice';
COMMENT ON COLUMN invoices.amount_cents IS 'Total in minor units';
SELECT diesel_manage_updated_at('invoices');
```

`down.sql`:

```sql
DROP TABLE invoices;
```

Dropping the table drops its trigger, indexes and constraints. `customers` and `invoice_status` come from earlier kept folders (an FK to a table created in a later folder goes to `deferred_foreign_keys`); a renamed table keeps the folder of the original that created it, with the former name in the header. Never rename a kept folder: a different version is a new, unapplied migration (dashes or not, Diesel reduces the name to the same 14 digits). Add `metadata.toml` with `run_in_transaction = false` only when a folded original had it.

## Writing a view migration

`2023-06-01-090000_customer_summary_view/up.sql`, the kept folder that created the view (a new `<version>_create_customer_summary_view/` after the last original only when even the original holding its final definition comes too early):

```sql
-- Consolidated: final view customer_summary (created here); folded: 2023-09-15-110000_fix_customer_summary
CREATE VIEW customer_summary AS
SELECT c.id, c.name, count(i.id) AS invoice_count
FROM customers c LEFT JOIN invoices i ON i.customer_id = c.id
GROUP BY c.id, c.name;
```

`down.sql`: `DROP VIEW customer_summary;`. No escape hatch: Diesel migrations are SQL.

## The extra files

New folders, after the last original, only when the plan lists them:

- `functions_and_triggers`: project functions and triggers that no original can hold (they read something created after every candidate). `diesel_manage_updated_at` calls are triggers: they stay in the folder that made them. Diesel splits nothing, so `$$` bodies are safe.
- `deferred_foreign_keys`: `ALTER TABLE a ADD CONSTRAINT a_b_id_fkey FOREIGN KEY (b_id) REFERENCES b (id);` per deferred FK (target created in a later folder, or a cycle edge); `down.sql` drops the constraint. On SQLite there is no `ADD CONSTRAINT`: the plan leaves forward FKs and cycles inline, and the report notes them.
- `seed_reference_data`: the kept `INSERT` statements; `down.sql` deletes the same keys.

Prerequisites get no folder of their own: `CREATE EXTENSION IF NOT EXISTS citext; CREATE TYPE invoice_status AS ENUM ('draft', 'sent', 'paid');` stay in the folder that created them (the helper functions in `diesel_initial_setup`), or move into an earlier kept folder that needs them, with the reverse in that folder's `down.sql`.

## Schema snapshot to cross-check

`src/schema.rs` (path from `[print_schema] file` in `diesel.toml`) is generated by `diesel print-schema` and is the Rust view of the live schema. Compare without a database:

- one `diesel::table! { invoices (id) { ... } }` per manifest table; the tuple after the name is the primary key
- columns in ordinal order, with `Nullable<T>` for nullable ones; `#[max_length = 32]` carries `varchar(32)`; `with_docs = true` turns `COMMENT ON` into doc comments
- Postgres enums appear as `sql_types::InvoiceStatus` with `#[diesel(postgres_type(name = "invoice_status"))]`
- `diesel::joinable!(invoices -> customers (customer_id));` per single-column FK, and `allow_tables_to_appear_in_same_query!` lists every table
- type names: `Int4`, `Int8`, `Varchar`, `Text`, `Bool`, `Timestamp`, `Timestamptz`, `Date`, `Numeric`, `Jsonb`, `Uuid`, `Array<...>`

Tables excluded by `filter.except_tables` / `only_tables` or a `patch_file` explain legitimate differences. Views appear in `schema.rs` only when `print-schema --include-views` (or `include_views` under `[print_schema]`) is set; otherwise views are checked only against `check_output.py`.

## After placing the files

- `diesel print-schema` needs a database; when the user has one, `diesel migration run` on a fresh database followed by `git diff src/schema.rs` is the real proof the consolidation is faithful (an empty diff). `diesel.toml` with `[print_schema] file` regenerates the file automatically after `migration run`, `revert` and `redo`.
- `embed_migrations!()` compiles the directory into the binary: rebuild, and make sure `build.rs` has `println!("cargo:rerun-if-changed=migrations");` or the stale set stays embedded.
- Keep the archive outside `migrations/` (archived folders share their names with the kept ones), and keep `00000000000000_diesel_initial_setup/`.
- `diesel migration list` against any database shows `[X]` for applied folders and `[ ]` for pending; `diesel migration pending` exits true when something would run.

## Tracking table and reconciliation

Table `__diesel_schema_migrations` with `version VARCHAR(50) PRIMARY KEY` and `run_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP`. Versions are stored without dashes.

- Row without a folder: ignored by `run`, `list` and `pending` (lenient); `revert` and `redo` fail with `UnknownMigrationVersion` when the newest row has no folder.
- Folder without a row: pending, runs on the next `migration run` regardless of its position.

Kept folders already have their row; removed originals still have theirs; new folders have none. `baseline.py` writes `precheck.sql` (run first) and `baseline.sql`:

```sql
-- precheck.sql: must return no rows (any row = that database has not run every original; migrate it on the old history first)
SELECT m.v AS not_applied FROM (SELECT '00000000000000' AS v UNION ALL SELECT '20230110100000' AS v UNION ALL ...) m
WHERE NOT EXISTS (SELECT 1 FROM __diesel_schema_migrations t WHERE t.version = m.v);
```
```sql
-- baseline.sql
DELETE FROM __diesel_schema_migrations WHERE version IN ('20230305100100', '20230801120000', '20231120100000');  -- removed originals
INSERT INTO __diesel_schema_migrations (version) VALUES ('20231120100001');  -- one per new folder, 14 digits, no dashes
INSERT INTO __diesel_schema_migrations (version) VALUES ('20231120100002');
```

The `DELETE` keeps `revert` from hitting a version without a folder; kept rows stay untouched. There is no checksum. Then `diesel print-schema` against that database: `src/schema.rs` must come out unchanged (the baseline is bookkeeping only). Fresh databases just run `diesel migration run` (or `diesel setup`, which also creates the database and the table).

## Quirks

- Each migration runs inside a transaction unless `metadata.toml` says otherwise; on MySQL DDL auto-commits, so a failed `up.sql` leaves partial objects and no row.
- `diesel migration redo` / `revert` go newest-first by version; a consolidated set reverts cleanly because its order is the history's (a folder's inline FKs point only at tables of the same or earlier folders) and every forward FK sits in `deferred_foreign_keys`, which is among the newest folders and reverts before the tables it links.
- A database that ran `2023-01-10-100000_create_invoices` but not yet `2023-03-05-100100_add_invoice_status` sees the rewritten folder as applied and never gets `status`; nothing fails. The precheck catches it.
- `diesel database reset` drops and recreates the database and replays everything; a stale `schema.rs` after that means the consolidated files diverge from the originals.
- SQLite: no `ALTER TABLE ... ADD CONSTRAINT`, no `DROP COLUMN` before 3.35, no `COMMENT`; `print-schema` maps `INTEGER PRIMARY KEY` to `Int4` unless `sqlite_integer_primary_key_is_bigint = true`.
- A folder named with dashes and one without that reduce to the same 14 digits are the same version; the harness errors on duplicates.
- `print-schema` orders columns by `ordinal_position` by default (`column_sorting = "name"` is the alternative), so the consolidated `CREATE TABLE` must list columns in the manifest's order to reproduce the file byte for byte.
