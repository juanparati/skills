# Ecto (ecto_sql 3.12)

## Layout and naming

- Directory: `priv/<repo>/migrations/` where `<repo>` is the underscored last segment of the repo module (`MyApp.Repo` → `priv/repo/migrations`; `MyApp.AnalyticsRepo` → `priv/analytics_repo/migrations`). Umbrella apps keep it under `apps/<app>/priv/…`. The manifest's `table.app` carries the repo when a project has several; write each table into its repo's directory. `--migrations-path` can point elsewhere; check `stack.json`.
- File: `<version>_<snake_name>.exs`, version is any integer that `Integer.parse` reads from the file name; by convention `YYYYMMDDHHMMSS` (`mix ecto.gen.migration`). Both the version and the name must be unique in the directory (`Ecto.MigrationError … is duplicated`).
- Module: `<Repo>.Migrations.<CamelName>`, e.g. `MyApp.Repo.Migrations.CreateInvoices`. The runner finds the module by `use Ecto.Migration`, not by name, but two files defining the same module overwrite each other at compile time. A kept file keeps its original's file name **and** module name, so it and its archived original define the same module with the same version and name: the archive must never be reachable by the runner. New files take module names no original used.

## Replay order

Ecto sorts by the integer version and runs every file whose version has no row in `schema_migrations`, so an older unapplied file still runs. No dependency graph. Transcribe in version order. Kept files keep their versions, so the history's order holds; `plan_order.py` numbers only the new files, right after `source.last_version` (14-digit timestamps, increment legally: `20240211100000` → `20240211100001_deferred_foreign_keys.exs`). A table split out of a multi-table original takes the next free second after it when that sorts before the next original; it is older than applied versions, which Ecto runs anyway, so its row has to come from the baseline.

## Reading originals into ops

The DSL is explicit about most things; what it hides is the primary key, timestamp types and the fact that `references` creates no index.

| Code | Ops |
| --- | --- |
| `create table(:invoices) do … end` | `create_table` with an implicit Column `{name: "id", type: "bigserial", primary: true}` unless `primary_key: false` or the repo configures `migration_primary_key` (e.g. `[name: :id, type: :binary_id]`; read `config/config.exs`). `prefix: "billing"` → `schema`. `comment:`, `engine:`, `options:` → `comment` / `options`. |
| `add :customer_id, references(:customers, on_delete: :delete_all, type: :binary_id), null: false` | Column `customer_id` (type from `type:`, default `:bigserial` → column `bigint`; `migration_foreign_key` config can change it) + ForeignKey `{name: "invoices_customer_id_fkey", columns: ["customer_id"], ref_table: "customers", ref_columns: ["id"], on_delete: "cascade"}` (`:nilify_all` → `set null`, `:restrict`, `:nothing`; `column:` / `name:` / `prefix:` override). No index. |
| `timestamps()` | Columns `inserted_at`, `updated_at`, `type: "naive_datetime"`, `nullable: false` (`type: :utc_datetime` or renamed via `migration_timestamps` config; `timestamps(inserted_at: :created_at)` inline) |
| `add :status, :string, size: 20, default: "draft", null: false, comment: "…"`; `add :amount, :decimal, precision: 12, scale: 2` | Columns, Ecto type atoms as `type` (`:string` → `varchar(255)` on Postgres; keep the atom) |
| `create index(:invoices, [:customer_id, :status])`, `create unique_index(:invoices, [:number])` | Index `{name: "invoices_customer_id_status_index"}` / `{name: "invoices_number_index", unique: true}`; `where:`, `using:`, `concurrently:`, `name:` map to `where`, `method`, `name` |
| `create constraint(:invoices, :invoice_amount_nonnegative, check: "amount_cents >= 0")` | `add_check` (name is mandatory in Ecto; `exclude:` constraints go to `raw`) |
| `execute "CREATE VIEW …", "DROP VIEW …"` / `execute "CREATE EXTENSION …"` / `execute &fun/0` | `create_view` / `create_extension` / `raw` with the function source in `code` |
| `alter table(:invoices) do modify :status, :string, null: true; remove :legacy; end`; `rename table(:x), to: table(:y)`; `rename table(:x), :a, to: :b` | `alter_column`, `drop_column`, `rename_table`, `rename_column` |
| `create_if_not_exists`, `drop_if_exists`, `@disable_ddl_transaction true`, `@disable_migration_lock true` | same ops; note the attributes in the ops file `note` when an index used `concurrently: true`, so the writer keeps them |

## Writing a consolidated table migration

```elixir
# 20230110100000_create_invoices.exs: consolidated by consolidate-migrations on 2025-10-29.
# Final definition of invoices. Folded in: 20230402100000_add_status_to_invoices,
#         20230901100000_add_number_index_to_invoices, 20240211100000_add_amount_check_to_invoices.
defmodule MyApp.Repo.Migrations.CreateInvoices do
  use Ecto.Migration

  def change do
    create table(:invoices, comment: "One row per issued invoice") do
      add :customer_id, references(:customers, on_delete: :delete_all), null: false
      add :number, :string, size: 32, null: false
      add :status, :string, size: 20, null: false, default: "draft", comment: "draft|paid|void"
      add :amount, :decimal, precision: 12, scale: 2, null: false, default: 0
      add :amount_cents, :bigint, null: false
      add :meta, :map
      add :due_on, :date
      add :deleted_at, :naive_datetime

      timestamps()
    end

    create unique_index(:invoices, [:number])                              # invoices_number_index
    create index(:invoices, [:customer_id, :status])                       # invoices_customer_id_status_index
    create constraint(:invoices, :invoice_amount_nonnegative, check: "amount_cents >= 0")
  end
end
```

File and module are the original's (`20230110100000_create_invoices.exs`, `MyApp.Repo.Migrations.CreateInvoices`); only the body is final. A renamed table keeps the file and module of the original that created it (`…_create_bills.exs`, `CreateBills`, now creating `:invoices`); the header says `Renamed from bills`. An original that created several tables holds each final `create table` in plan order, unless the user chose to split it.

`change/0` reverses `create table`, `create index` and `create constraint` by itself (`mix ecto.rollback` drops them in reverse order). A table with a non-default key: `create table(:invoices, primary_key: false) do add :id, :binary_id, primary_key: true … end`. When the repo configures `migration_primary_key`, write plain `create table(:invoices)` so the config keeps applying.

## Writing a view migration

```elixir
defmodule MyApp.Repo.Migrations.CreateCustomerSummaryView do
  use Ecto.Migration

  def change do
    execute(
      """
      CREATE VIEW customer_summary AS
      SELECT c.id AS customer_id, c.name, COUNT(i.id) AS invoice_count
      FROM customers c LEFT JOIN invoices i ON i.customer_id = c.id
      GROUP BY c.id, c.name
      """,
      "DROP VIEW IF EXISTS customer_summary"
    )
  end
end
```

`execute/2` (up SQL, down SQL) is the escape hatch and stays reversible inside `change/0`; `execute/1` only works in `up/0` + `down/0`. SQL in `execute` is queued and run when the callback ends; call `flush()` when a later statement in the same migration needs the view to exist already.

## The extra files

New files only for what no kept file can hold, numbered after the last original.

- Prerequisites have no file of their own: `execute "CREATE EXTENSION IF NOT EXISTS citext", "DROP EXTENSION IF EXISTS citext"`; `execute "CREATE TYPE invoice_status AS ENUM ('draft','paid')", "DROP TYPE invoice_status"`; `execute "CREATE SCHEMA billing", "DROP SCHEMA billing"` (then `create table(:invoices, prefix: "billing")`) go first in `change/0` of the kept file that created them, or of the earlier kept file the plan pulled them into (`moved_in`).
- Functions and triggers: one `execute(raw, "DROP TRIGGER … ON …")` / `execute(raw, "DROP FUNCTION …")` per object, in `raw` order, at the end of their kept file; `$$` bodies are fine in `execute`. `functions_and_triggers` only for those reading something created after every candidate original.
- `deferred_foreign_keys`: `alter table(:customers) do modify :primary_invoice_id, references(:invoices, on_delete: :nilify_all), from: :bigint end` (`from:` is what makes `modify` reversible in `change/0`) or, when the originals did it that way, `execute "ALTER TABLE … ADD CONSTRAINT …", "ALTER TABLE … DROP CONSTRAINT …"`.
- `seed_reference_data`: `execute "INSERT INTO tags (…) VALUES (…)", "DELETE FROM tags WHERE …"`; for the original `Repo.insert_all` code keep it in `up/0` with `flush()` first and write a `down/0`.

## Schema snapshot to cross-check

None by default. `priv/<repo>/structure.sql`, if present, is the output of `mix ecto.dump` (`pg_dump --schema-only --no-acl --no-owner` plus `INSERT INTO public."schema_migrations" (version) VALUES (…)` lines; `mysqldump` for MySQL) and reflects the database at dump time; compare its `CREATE TABLE` / `CREATE INDEX` / `ALTER TABLE … FOREIGN KEY` statements with the manifest. Ecto schemas (`lib/**/schema.ex`, `field :x, :type`) are not a source of truth for the DDL (no sizes, nullability or indexes) but confirm column names and the `@primary_key` / `@foreign_key_type` conventions.

`mix ecto.dump` is the built-in alternative and differs from this skill: it needs a live database and the `pg_dump`/`mysqldump` binary, produces dialect SQL that only `mix ecto.load` consumes (the default `ecto.setup` alias and the test sandbox keep running `ecto.migrate`, so the migrations stay), and the migration files are not reduced by it. This skill keeps the DSL and the files of the originals that created the tables.

## After placing the files

- Archive outside `priv/<repo>/migrations/`; the runner globs `**/*.{ex,exs}` recursively, so a subdirectory would still be picked up.
- `mix compile` is not affected (migrations are `.exs`, evaluated at run time); a quick static check is `elixir -e 'Code.compile_file("priv/repo/migrations/20230110100000_create_invoices.exs")'` per file or `mix format --check-formatted priv/repo/migrations/*.exs`.
- `mix ecto.migrations` lists the state per version (`up` / `down`; before the baseline, `** FILE NOT FOUND **` for removed originals that still have rows and `down` for the new files).
- Releases: migrations run from `Application.app_dir(:my_app, "priv/repo/migrations")`, i.e. whatever is in `priv/` at build time; rebuild the release after placing the files.
- If `structure.sql` exists, it is stale: its `INSERT INTO public."schema_migrations" (version) VALUES (…)` lines list the old versions, so `mix ecto.load` would record the removed originals and not the new files, and the next `ecto.migrate` would run the new files on top of the loaded schema. Regenerate it with `mix ecto.dump` from a baselined database, or delete it.

## Tracking table and reconciliation

Table `schema_migrations` (renamed via `migration_source`, kept in `migration_default_prefix` or `migration_repo` when configured): `version` (bigint, primary key), `inserted_at` (naive_datetime). No checksum, no name.

- Row without file: lenient. `mix ecto.migrate` ignores it; `mix ecto.migrations` shows `** FILE NOT FOUND **`; `mix ecto.rollback` only rolls back versions that have a file (`--to` a missing version does nothing for it).
- File without row: pending, runs on the next `mix ecto.migrate`, even if older than applied versions.

Precheck first (`precheck.sql`, must return no rows): the originals this database has not run. A database that is behind would see the kept versions as applied and miss their folded changes.

```sql
SELECT m.v AS not_applied FROM (SELECT 20230110100000 AS v UNION ALL SELECT 20230402100000 AS v UNION ALL …) m
WHERE NOT EXISTS (SELECT 1 FROM schema_migrations t WHERE t.version = m.v);
```

Baseline for an existing database (what `baseline.py` emits). Kept versions keep their rows; removed ones go (tidy-up: they only show as `** FILE NOT FOUND **`); new ones are inserted (mandatory, or they run):

```sql
DELETE FROM schema_migrations WHERE version IN (20230402100000, 20230901100000, 20240211100000);
INSERT INTO schema_migrations (version, inserted_at) VALUES (20240211100001, CURRENT_TIMESTAMP);
INSERT INTO schema_migrations (version, inserted_at) VALUES (20240211100002, CURRENT_TIMESTAMP);
```

Versions are integers, not strings. There is no `mix` command that records a version without running it (unverified: none in ecto_sql 3.12's task list), so the SQL is the way; `mix ecto.migrate --to <version>` would execute the file.

## Quirks

- `references/2` creates the foreign key only; the index you expect on `customer_id` exists only if an original had `create index`. Do not add one while consolidating.
- The implicit `id :bigserial` primary key is easy to miss when transcribing and easy to double when writing (`add :id, …` plus the default). One or the other.
- `timestamps()` is `NOT NULL` `naive_datetime` by default; projects that switched to `:utc_datetime` usually did it via `migration_timestamps` config rather than per file, so check the repo config before writing the helper.
- `create constraint` requires a name; the manifest's check may have `name: null` when it came from raw SQL; invent `<table>_<column>_check`-style names and record them in the report.
- `@disable_ddl_transaction true` is required for `concurrently: true` indexes and makes the whole file non-transactional. A concurrent index folded into a kept table file would drag the whole file out of its transaction; the table is new and empty when that file runs, so write the index without `concurrently:` (same index) and mention it in the report. Keep the attributes only when the kept original itself had them.
- MySQL: `comment:` works, `prefix:` means database, and `modify` of a FK column needs the constraint dropped first; SQLite: `alter table` is rebuilt, FKs are not enforced by default.
- The migration lock (`pg_advisory_lock` or table lock) is taken by `mix ecto.migrate` even for a no-op run; the SQL baseline above avoids it entirely.
- `Ecto.Migrator.with_repo/2` in release tasks reads the same directory; nothing else to update.
