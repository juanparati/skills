# Goose (v3)

## Layout and naming

- One flat directory, usually `migrations/` or `db/migrations/`; the CLI takes `-dir` (or `GOOSE_MIGRATION_DIR`), there is no fixed default beyond the current directory. Check `stack.json` for the detected path.
- Files are `<version>_<name>.sql` or `<version>_<name>.go`. The version is any positive integer; `goose create` writes a UTC timestamp `YYYYMMDDHHMMSS` by default or, with `-s`, a zero-padded sequence `00001`, `00002`… Goose sorts numerically by that integer, so mixing both styles works but sorts every sequential file before every timestamp.
- Goose only globs `*.sql` and `*.go` at the top level, skips `_test.go` and ignores files without a numeric prefix, so `helpers.go` next to migrations is fine and nested directories are invisible.
- Kept files keep their exact name, integer prefix (and `.sql` / `.go` extension) included; only their content changes. New files (`deferred_foreign_keys`, `seed_reference_data`, `functions_and_triggers`, moved views, split-out tables) get numbers right after `source.last_version` in the project's style, which is also what `goose create` does (it continues after the highest existing version).
- A split multi-table original (the user's choice, per run): the table named in the file name keeps the file (the first one created when it names none). Versions are plain integers, so `plan_order.py` never slots the other tables between two originals; it appends them after the last original, and any FK pointing at them from the files in between moves to `deferred_foreign_keys`.

## Replay order

Goose applies files in ascending version order and records each one. Nothing else (no journal, no dependencies) decides order, so transcribe the originals sorted by their integer prefix. Duplicate versions are a hard error at collection time. The consolidated set keeps that order: kept files at their original versions, new files after.

## Reading originals into ops

Goose migrations are plain SQL for the target dialect; the annotations only shape parsing:

- `-- +goose Up` … `-- +goose Down`: transcribe the Up block only; the Down block is a consistency check.
- `-- +goose StatementBegin` / `StatementEnd`: one statement that contains semicolons (functions, triggers, `DO $$` blocks). It becomes a `create_function` / `create_trigger` op, or `raw` when it is procedural code that touches data.
- `-- +goose NO TRANSACTION`: no schema meaning; note it in the ops file (`"note": "no transaction"`) so the consolidated file for that table can carry it (needed for `CREATE INDEX CONCURRENTLY`, `ALTER TYPE ... ADD VALUE` on older Postgres).
- `-- +goose ENVSUB ON`: `${VAR}` placeholders in SQL. Keep them verbatim in `raw`/`default` strings; do not resolve them.
- A `.go` migration (`goose.AddMigrationContext(upFn, downFn)` in `init()`) runs Go code; read the SQL strings it executes. If it builds DDL dynamically, record a `raw` op with the function name so it lands in `unresolved`.

Typical expansions (Postgres):

- `id bigserial PRIMARY KEY` -> Column `{ "type": "bigserial", "primary": true, "auto_increment": true }`
- `customer_id bigint NOT NULL REFERENCES customers(id) ON DELETE CASCADE` -> Column + ForeignKey `{ "columns": ["customer_id"], "ref_table": "customers", "ref_columns": ["id"], "on_delete": "cascade", "name": null }`
- `CREATE UNIQUE INDEX users_email_idx ON users (lower(email))` -> Index `{ "name": "users_email_idx", "unique": true, "raw": "lower(email)" }`
- `ALTER TABLE t ADD CONSTRAINT t_amount_chk CHECK (amount >= 0)` -> `add_check`
- A StatementBegin block with `CREATE OR REPLACE FUNCTION set_updated_at()` -> `create_function`; the `CREATE TRIGGER ... EXECUTE FUNCTION set_updated_at()` that follows -> `create_trigger` on that table

## Writing a consolidated table migration

`20230110100000_create_invoices.sql`, the kept file that created the table:

```sql
-- Consolidated: final billing.invoices (created here); folded:
--   20230305100100_add_invoice_status.sql, 20230801120000_invoice_customer_fk.sql,
--   20231120100000_invoice_indexes.sql
-- +goose Up
CREATE TABLE billing.invoices (
    id            bigserial PRIMARY KEY,
    customer_id   bigint        NOT NULL,
    number        varchar(32)   NOT NULL,
    status        invoice_status NOT NULL DEFAULT 'draft',
    amount_cents  bigint        NOT NULL DEFAULT 0,
    notes         text,
    issued_on     date,
    created_at    timestamptz   NOT NULL DEFAULT now(),
    updated_at    timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT invoices_amount_nonnegative CHECK (amount_cents >= 0),
    CONSTRAINT invoices_customer_id_fkey FOREIGN KEY (customer_id)
        REFERENCES billing.customers (id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX invoices_number_key ON billing.invoices (number);
CREATE INDEX invoices_customer_id_status_idx ON billing.invoices (customer_id, status);
COMMENT ON TABLE billing.invoices IS 'One row per issued invoice';
COMMENT ON COLUMN billing.invoices.amount_cents IS 'Total in minor units';

-- +goose Down
DROP TABLE billing.invoices;
```

`invoice_status` and `billing.customers` come from earlier kept files (an FK to a table created in a later file goes to `deferred_foreign_keys`). A renamed table keeps the file of the original that created it; name the former table in the header. Dropping the table removes its indexes, constraints and comments, so `Down` is a single statement. MySQL and SQLite use their own column syntax but the same annotation skeleton; SQLite gets no `COMMENT` and no `ADD CONSTRAINT`, so put checks and FKs inline in `CREATE TABLE`.

## Writing a view migration

`20230601090000_customer_summary_view.sql`, the kept file that created the view (a new `<version>_create_customer_summary_view.sql` after the last original only when even the original holding its final definition comes too early):

```sql
-- Consolidated: final view customer_summary (created here); folded: 20230915110000_fix_customer_summary.sql
-- +goose Up
CREATE VIEW customer_summary AS
SELECT c.id, c.name, count(i.id) AS invoice_count
FROM billing.customers c
LEFT JOIN billing.invoices i ON i.customer_id = c.id
GROUP BY c.id, c.name;

-- +goose Down
DROP VIEW customer_summary;
```

No escape hatch is needed: the file is SQL already. Wrap a materialized view with a `WITH DATA` clause or an index in the same file.

## The extra files

New files, after the last original, only when the plan lists them:

- `functions_and_triggers`: only the functions that read tables, and triggers, that no original can hold (they read something created after every candidate). A plain trigger function like `set_updated_at()` is a prerequisite: it stays in the file that created it, and the trigger in the file that created the trigger (the entry's `triggers` in the plan). Every function body goes between `-- +goose StatementBegin` / `-- +goose StatementEnd` (it has semicolons); `Down` drops triggers then functions.
- Column order: write columns in manifest order (the history's order, with late `ADD COLUMN`s last), which is the physical order existing databases have; do not reorder for looks.
- `deferred_foreign_keys`: `ALTER TABLE a ADD CONSTRAINT a_b_id_fkey FOREIGN KEY (b_id) REFERENCES b (id);` per deferred FK (target created in a later file, a split-out table among them, or a cycle edge); `Down` is `ALTER TABLE a DROP CONSTRAINT a_b_id_fkey;`.
- `seed_reference_data`: the `INSERT ... ON CONFLICT DO NOTHING` statements the user chose to keep, copied verbatim from the `data` ops; `Down` deletes the same keys. Add `-- +goose NO TRANSACTION` only if an original had it.

Prerequisites get no file of their own: `CREATE SCHEMA billing; CREATE EXTENSION IF NOT EXISTS citext; CREATE TYPE invoice_status AS ENUM ('draft','sent','paid');` stay in the file that created them, or move into an earlier kept file that needs them, with the matching `DROP ...` in that file's `Down`.

## Schema snapshot to cross-check

None. Goose keeps no generated schema. If the repo has a `schema.sql` dump (`pg_dump --schema-only`), compare table by table with the manifest; otherwise `check_output.py` is the only static check.

## After placing the files

- Move archived originals outside the migration directory: any `.sql` or `.go` file with a numeric prefix in that directory is a migration.
- If migrations are embedded (`//go:embed migrations/*.sql` + `goose.SetBaseFS(embedMigrations)`), the binary must be rebuilt; `go vet ./...` confirms the embed pattern still matches.
- Go migrations: the archived `.go` files still compile if they stay in the package, and each would register its version in `init()` (a removed original's version, its row deleted by the baseline, would then count as missing). Move them out of the build (`archive/` outside the package, or a build tag) so the registry only contains versions that exist as files. A kept `.go` original stays `.go`: its `upFn` executes the final DDL.
- Run `goose -dir <dir> validate -v`: it parses every file, checks annotations and counts statements without a database. `goose fix` is only for converting timestamp names to sequential ones; do not run it unless the project already uses sequential versions and the consolidated files were numbered as timestamps by mistake.

## Tracking table and reconciliation

Table `goose_db_version` (override with `-table` / `GOOSE_TABLE`; schema-qualify it on Postgres when not in `public`). Columns: `id` (serial PK), `version_id bigint`, `is_applied boolean`, `tstamp` (default now). Goose inserts a `version_id = 0` row when it creates the table and refuses to work without it (`missing zero version migration`). The current version is the highest `version_id` present; `goose down` deletes that row (older releases inserted a second row with `is_applied = false` instead, so both patterns may exist in old databases).

- Row without a file: ignored by `up` and `status`; `down`/`redo` fail with `migration file not found for current version`.
- File without a row and version lower than the current version: `up` refuses (`found N missing migrations before current version V`) unless `-allow-missing`; higher versions are simply pending and run.

Kept files already have their row; removed originals still have theirs; new files have none. `baseline.py` writes `precheck.sql` (run first) and `baseline.sql`:

```sql
-- precheck.sql: must return no rows (any row = that database has not run every original; migrate it on the old history first)
SELECT m.v AS not_applied FROM (SELECT 20230110100000 AS v UNION ALL SELECT 20230305100100 AS v UNION ALL ...) m
WHERE NOT EXISTS (SELECT 1 FROM goose_db_version t WHERE t.version_id = m.v);
```
```sql
-- baseline.sql
DELETE FROM goose_db_version WHERE version_id IN (20230305100100, 20230801120000, 20231120100000);  -- removed originals; never version 0
INSERT INTO goose_db_version (version_id, is_applied) VALUES (20231120100001, true);  -- one per new file
INSERT INTO goose_db_version (version_id, is_applied) VALUES (20231120100002, true);
-- MySQL/SQLite: true -> 1
```

The `DELETE` is not cosmetic: `down`/`redo` walk back from the highest row and fail on one without a file. Kept rows stay untouched. There is no checksum. Fresh databases just run `goose up`.

## Quirks

- The "missing migrations" check compares the versions of files without a row against the DB max version. Kept files keep old versions but already have rows, so they are not missing. A *new* file numbered below the max would be reported as missing on existing databases (or run out of order with `-allow-missing`); new files always go after `last_version`.
- A database that ran `20230110100000_create_invoices.sql` but not yet `20230305100100_add_invoice_status.sql` sees the rewritten file as applied and never gets `status`; nothing fails. The precheck catches it.
- `goose create` decides timestamp-vs-sequential per call; a directory with both styles still orders every `0000N` file before every timestamp.
- A `-- +goose Up` annotation is mandatory; a file without it fails to parse. `Down` is optional but `goose validate` reports zero down statements.
- Statements with semicolons inside (functions, `DO` blocks, triggers with bodies) silently break into fragments unless wrapped in `StatementBegin`/`StatementEnd`.
- Each file runs in one transaction unless `NO TRANSACTION`; on MySQL DDL auto-commits, so a failed multi-statement file leaves a partial table with no row recorded.
- `goose up -no-versioning` runs files without touching the table; not useful for baselining.
- Goose can also be driven as a library (`goose.NewProvider`) with `goose.WithAllowMissing()`; the same rules apply.
