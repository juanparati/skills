# Rails (Active Record 7.2 / 8.x)

## Layout and naming

- Directory: `db/migrate/` (`config.paths["db/migrate"]` or `migrations_paths` can add more; multi-database apps have `db/<name>_migrate/`). Check `stack.json`.
- File: `YYYYMMDDHHMMSS_<snake_name>.rb` (14 digits, UTC); the regex is `/\A([0-9]+)_([_a-z0-9]*)\.?([_a-z0-9]*)?\.rb\z/`. The class must be the CamelCase of the name part: `20251029120000_create_invoices.rb` defines `CreateInvoices`. Class names must be unique across the directory (`DuplicateMigrationNameError`).
- Consolidated files: a kept original keeps its version and its class name (`20230110100000_create_invoices.rb` still defines `CreateInvoices`) and holds the final definition of what it created; originals that only altered or dropped things disappear. Every kept file shares version and class with its archived copy, so the archive must be outside the `db/migrate/**` glob. New files are numbered from one second after the last original, one second apart (`20240211100001_functions_and_triggers.rb`, class `FunctionsAndTriggers`); a table split out of a multi-table original takes a free second right after it when there is one.
- Every class inherits `ActiveRecord::Migration[x.y]`. Use the project's current tag (what the newest original or `bin/rails generate migration` produces: `[7.2]`, `[8.0]`, `[8.1]`), also in a kept original that carried an older one; the tag selects compatibility behaviour, it is not a Rails requirement to match the installed version.

## Replay order

Rails runs every file in `db/migrate/**/[0-9]*_*.rb` whose integer version has no row in `schema_migrations`, sorted by version; an older unapplied file still runs. With `validate_migration_timestamps` on (default for `load_defaults 7.2+`), a version must be a real `YYYYMMDDHHMMSS` smaller than tomorrow's UTC, so `plan_order.py` must number new files with legal seconds/minutes (no `…120060`) and never date into the future. Transcribe in version order.

## Reading originals into ops

What the `Migration[x.y]` tag changes matters while reading: under `[5.0]` `t.references` made an `integer` column, from `[5.1]` a `bigint`; from `[7.0]` `t.timestamps` has precision 6 and `datetime` columns default to precision 6. Record the resolved type, and cross-check `db/schema.rb`, which has the truth.

| Code | Ops |
| --- | --- |
| `create_table :invoices do \|t\| … end` | `create_table` with an implicit Column `{name: "id", type: "bigint", primary: true, auto_increment: true}`; `id: :uuid` → `uuid` default `gen_random_uuid()`; `id: false` → none; `primary_key: [:a, :b]` → composite `primary_key` |
| `t.references :customer, null: false, foreign_key: { on_delete: :cascade }` | Column `customer_id bigint not null` + Index `{name: "index_invoices_on_customer_id"}` + ForeignKey `{name: null, columns: ["customer_id"], ref_table: "customers", ref_columns: ["id"], on_delete: "cascade"}` (`belongs_to` is the same; `index: false` / `foreign_key: true` plain / `to_table:` override) |
| `t.references :commentable, polymorphic: true` | Columns `commentable_type string` + `commentable_id bigint` + Index `{name: "index_comments_on_commentable", columns: ["commentable_type", "commentable_id"]}`; table `polymorphic: ["commentable"]`, no FK |
| `t.timestamps` | `created_at`, `updated_at` `datetime`, `nullable: false`, `precision: 6` (`[7.0]`+) |
| `t.string :number, limit: 32, null: false, comment: "…"`; `t.decimal :amount, precision: 12, scale: 2, default: 0` | Columns, verbatim type names |
| `add_index :invoices, [:customer_id, :status], unique: true, where: "deleted_at IS NULL"` | Index `{name: "index_invoices_on_customer_id_and_status", unique: true, where: "…"}` |
| `add_foreign_key :invoices, :customers, column: :buyer_id, on_delete: :nullify` | `add_foreign_key`, name null (Rails names it `fk_rails_<first 10 hex of SHA256("invoices_buyer_id_fk")>`) |
| `add_check_constraint :invoices, "amount_cents >= 0", name: "invoice_amount_nonnegative"` | `add_check` (unnamed: `chk_rails_<hash>`) |
| `create_enum :invoice_status, %w[draft paid]`; `t.enum :status, enum_type: :invoice_status` | `create_type` kind enum + Column with `type_ref: "type:invoice_status"` |
| `execute "CREATE VIEW …"`, `create_view :customer_summaries` (Scenic) | `create_view`; for Scenic the SELECT is in `db/views/customer_summaries_v0N.sql` (highest `version:` referenced wins) |
| `change_column_null`, `change_column_default`, `change_column`, `rename_column`, `remove_reference` | `alter_column`, `rename_column`, `drop_column` + `drop_index` + `drop_foreign_key` |

## Writing a consolidated table migration

`db/migrate/20230110100000_create_invoices.rb`, the kept original (same version, same class, current tag):

```ruby
# Consolidated by consolidate-migrations on 2025-10-29: final definition of invoices.
# Folded: 20230110100000_create_invoices (this file), 20230402100000_add_status_to_invoices,
#         20230901100000_add_number_index_to_invoices, 20240211100000_add_amount_check_to_invoices.
class CreateInvoices < ActiveRecord::Migration[8.0]
  def change
    create_table :invoices, comment: "One row per issued invoice" do |t|
      t.references :customer, null: false, foreign_key: { on_delete: :cascade }
      t.string :number, limit: 32, null: false
      t.string :status, limit: 20, null: false, default: "draft", comment: "draft|paid|void"
      t.decimal :amount, precision: 12, scale: 2, null: false, default: 0
      t.bigint :amount_cents, null: false
      t.jsonb :meta
      t.date :due_on
      t.datetime :deleted_at
      t.timestamps

      t.index :number, unique: true                                     # index_invoices_on_number
      t.index [:customer_id, :status], name: "index_invoices_on_customer_and_status"
      t.check_constraint "amount_cents >= 0", name: "invoice_amount_nonnegative"
    end
  end
end
```

`change` reverses `create_table` (including its indexes, FKs and checks) by dropping the table; no `down` needed. Only when something is not reversible (`execute`, `change_column`) use `reversible do |dir| dir.up { … }; dir.down { … } end` or explicit `up`/`down`. A kept multi-table original (not split) holds one `create_table` per table it created, in FK order.

## Writing a view migration

A view stays in its creating file (class unchanged) when everything it reads exists by then; otherwise it goes to a new file after the last original (`20240211100002_create_customer_summaries_view.rb`, class `CreateCustomerSummariesView`). Without Scenic, `execute` is the escape hatch and needs both directions:

```ruby
class CreateCustomerSummaries < ActiveRecord::Migration[8.0]
  def up
    execute <<~SQL
      CREATE VIEW customer_summaries AS
      SELECT c.id AS customer_id, c.name, COUNT(i.id) AS invoice_count
      FROM customers c LEFT JOIN invoices i ON i.customer_id = c.id
      GROUP BY c.id, c.name
    SQL
  end

  def down
    execute "DROP VIEW IF EXISTS customer_summaries"
  end
end
```

When `db/views/` exists the project uses Scenic: write the final SELECT to `db/views/customer_summaries_v01.sql` (archive the older `_v0N.sql` with the originals) and the migration becomes `def change; create_view :customer_summaries; end` (`materialized: true` for materialized views; it is reversible). Scenic dumps views into `schema.rb` as `create_view "customer_summaries", sql_definition: <<-SQL`.

## The extra files

New files, in plan order after the last original:

- `functions_and_triggers` (`20240211100001_functions_and_triggers.rb`, only what could not stay in its creating file): `reversible do |dir| dir.up { execute raw }; dir.down { execute "DROP TRIGGER … ON …; DROP FUNCTION …" } end`, one block per object.
- `deferred_foreign_keys` (`20240211100003_deferred_foreign_keys.rb`): `add_foreign_key :customers, :invoices, column: :primary_invoice_id, on_delete: :nullify` in `change` (reversible). SQLite keeps forward FKs inline.
- `seed_reference_data` (`20240211100004_seed_reference_data.rb`): `reversible` with `dir.up { execute "INSERT INTO tags …" }` or the original `Tag.insert_all(...)` guarded by a local model class (`class Tag < ApplicationRecord; end` inside the migration) so later model changes cannot break it.

Prerequisites get no file: `enable_extension "citext"` / `disable_extension` (reversible), `create_enum :invoice_status, %w[draft paid]` (reversible, Postgres), `create_schema :billing` (7.1+), `execute "CREATE SEQUENCE …"` inside `reversible` stay in the kept original that created them, or move to the top of an earlier kept file that needs them.

## Schema snapshot to cross-check

`db/schema.rb` (`config.active_record.schema_format = :ruby`, the default) or `db/structure.sql` (`:sql`). Both are generated from a real database after every `db:migrate`, so they describe the schema the originals produced and are the best static cross-check: every `create_table` block, `t.index`, `t.check_constraint`, `add_foreign_key` line and `create_enum` in `schema.rb` should have an equal in the manifest (same column types, `null`, `default`, `precision`, index names and `where:`). Differences point at a transcription error or at a column added outside migrations. `structure.sql` is `pg_dump`/`mysqldump` output and ends with `INSERT INTO "schema_migrations" (version) VALUES (...)` rows.

## After placing the files

- Archive outside `db/migrate/` (and outside any configured `migrations_paths`); Rails globs subdirectories too (`**/[0-9]*_*.rb`), and a globbed archive duplicates every kept version and class (`DuplicateMigrationVersionError` / `DuplicateMigrationNameError`).
- Update `db/schema.rb` `define(version: …)` to the highest version among the kept and new files (or run `bin/rails db:schema:dump` against a migrated database, which rewrites the file and the version). `db:schema:load` calls `assume_migrated_upto_version(version)`, which inserts rows for every file in `db/migrate` with a version ≤ that number; with the old version, the new files would be reported pending after a `db:schema:load`.
- `bin/rails db:migrate:status` lists versions and files; archived originals show as `********** NO FILE **********` until their rows are removed.
- Nothing to rebuild; `db:prepare` and `db:test:prepare` work off the schema file.

## Tracking table and reconciliation

`schema_migrations`: one column `version` (string, primary key; `ActiveRecord::Base.schema_migrations_table_name` to rename). `ar_internal_metadata`: `key`, `value`, `created_at`, `updated_at` with rows `environment` and `schema_sha1`; leave it alone.

- Row without file: lenient. `db:migrate` ignores it, `db:migrate:status` prints `NO FILE`; only `db:migrate:down VERSION=` / a rollback that reaches it raises `UnknownMigrationVersionError`.
- File without row: pending, runs on the next `db:migrate` regardless of being older than the current version.
- No checksum.

Baseline for an existing database (what `baseline.py` emits, after `precheck.sql` returns no rows). Kept versions keep their rows; removed ones go, because a rollback that reaches an orphan raises:

```sql
DELETE FROM schema_migrations WHERE version IN ('20230402100000', '20230901100000', '20240211100000');   -- removed originals
INSERT INTO schema_migrations (version) VALUES ('20240211100001');   -- one per new file
INSERT INTO schema_migrations (version) VALUES ('20240211100003');
```

Equivalent for the INSERTs, once the originals are out of `db/migrate`: `bin/rails runner 'ActiveRecord::Base.connection.assume_migrated_upto_version(20240211100004)'` inserts every version found in `db/migrate` up to that number (it raises on duplicate versions and leaves the rows of removed originals in place, so the DELETE is still needed).

## Quirks

- The `Migration[x.y]` tag changes defaults silently: integer vs bigint references before `[5.1]`, `null: true` timestamps before `[5.0]`, precision-less datetimes before `[7.0]`. When the originals carried mixed tags (or a kept original moves from an old tag to the current one), write the resolved types explicitly in the consolidated file (`t.bigint`, `precision: 6`) rather than relying on the helper's default under the new tag; `schema.rb` tells you which it was.
- Index names are capped at 62 bytes on Postgres; Rails falls back to `idx_on_<columns>_<hash>` for long defaults, so pass `name:` explicitly when the manifest has such a name.
- `t.references … foreign_key: true` in `create_table` fails if the referenced table does not exist yet; the file order is the history's, so `plan_order.py` moves FKs to a table created in a later file (cycles included) to `deferred_foreign_keys`: write the `t.references` without `foreign_key:` there and the `add_foreign_key` in the deferred file. Self-references are fine inline.
- `t.timestamps` without `null: false` under `[8.x]` is already `null: false`; do not add `null: true` unless the manifest says the originals were nullable.
- `execute` statements are not reversible and `db:rollback` of a `change` method containing one raises `IrreversibleMigration`; always use `up`/`down` or `reversible` around them.
- MySQL: `comment:` on tables and columns works; SQLite ignores comments and `add_check_constraint` requires a table rebuild (Rails does it, slowly).
- `config.active_record.dump_schema_after_migration = false` in some production configs means the schema file was never regenerated there; trust the development one.
- Scenic `update_view` versions: the manifest keeps only the final `definition`, so write it as `_v01.sql` and drop the other versions; `revert_to_version:` is only needed in `down` of `update_view`, not here.
