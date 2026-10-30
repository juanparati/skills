# Laravel (11 / 12, Schema builder)

## Layout and naming

- Directory: `database/migrations/` (extra paths may be registered by packages via `loadMigrationsFrom()`; `php artisan migrate --path=` can point elsewhere). Check `stack.json` for every directory you found.
- File: `YYYY_MM_DD_HHMMSS_<snake_name>.php`, e.g. `2025_10_29_120000_create_invoices_table.php`. Only `.php`.
- Modern files return an anonymous class: `return new class extends Migration { ... };`. Older ones declare `class CreateInvoicesTable extends Migration`. Write anonymous classes, also when rewriting a kept original that declared a named class: the kept file and its archived original would otherwise both declare `CreateUsersTable`, which collides as soon as both are loaded (classmap, a stray `--path`).
- The migration name recorded in the database is the file name without `.php`, not the class, so a kept file (same name as its original) is already recorded wherever the original ran, and switching it to an anonymous class changes nothing there.

## Replay order

Laravel sorts the discovered files by name and runs every file whose name has no row in the `migrations` table, in that order. There is no dependency graph, so the version prefix *is* the order. Transcribe originals in file-name order. Kept files keep their names, so the consolidated set runs in the history's order; only new files (`deferred_foreign_keys`, `seed_reference_data`, `functions_and_triggers`, moved views) are numbered by `plan_order.py` right after `source.last_version` (`2023_11_20_100000` → `2023_11_20_100001_deferred_foreign_keys.php`), after the history and before anything generated later. A table split out of a multi-table original takes the next free second after it when that still sorts before the next original; Laravel 11's `0001_01_01_000000_create_users_table.php` (users, password_reset_tokens, sessions) has no room, because `0001_01_01_000001_create_cache_table.php` follows, so split tables there go after the last original.

## Reading originals into ops

Blueprint methods hide columns, indexes and foreign keys. Expand them; the default names below are what `createIndexName()` produces (`<table>_<columns joined by _>_<type>`, lowercase, dots and dashes replaced by `_`).

| Code | Ops |
| --- | --- |
| `$table->id();` | Column `{name: "id", type: "bigIncrements", primary: true, auto_increment: true, unsigned: true}` |
| `$table->timestamps();` | Columns `created_at`, `updated_at`, `type: "timestamp"`, `nullable: true` (not `NOT NULL`, a frequent misreading) |
| `$table->softDeletes();` | Column `deleted_at timestamp nullable` |
| `$table->rememberToken();` | Column `remember_token`, `type: "string"`, `length: 100`, `nullable: true` |
| `$table->foreignId('customer_id')->constrained()->cascadeOnDelete();` | Column `customer_id foreignId` (unsigned bigint) + ForeignKey `{name: "invoices_customer_id_foreign", columns: ["customer_id"], ref_table: "customers", ref_columns: ["id"], on_delete: "cascade"}`. `constrained('accounts', 'uuid')` overrides table/column. No index is created by `constrained()`; MySQL adds one implicitly, Postgres does not, so record only what the code says. |
| `$table->morphs('commentable');` | Columns `commentable_type string` + `commentable_id unsignedBigInteger` + Index `{name: "comments_commentable_type_commentable_id_index", columns: [type, id]}`; set `polymorphic: ["commentable"]` on the table, no FK. `nullableMorphs`, `uuidMorphs`, `ulidMorphs` differ only in nullability / id type. |
| `$table->string('email')->unique();` | Column + Index `{name: "users_email_unique", columns: ["email"], unique: true}` |
| `->comment('…')`, `$table->comment('…')`, `->engine('InnoDB')`, `->charset()`, `->collation()` | `comment` on the column / table, `options` on the table |
| `DB::statement('ALTER TABLE … ADD CONSTRAINT … CHECK …')` | `add_check`; the Blueprint has no `check()` method, so checks only ever appear as raw SQL |
| `DB::statement('CREATE [OR REPLACE] VIEW …')` | `create_view` with the SELECT body in `definition` |

`foreignIdFor(User::class)` is `user_id foreignId` (a FK only with `->constrained()`); `enum('status', [...])` is an inline `enum_values` column, no `create_type`; `->change()` is `alter_column`, `dropForeign(['customer_id'])` derives the name `x_customer_id_foreign`.

## Writing a consolidated table migration

```php
<?php

// 2023_01_10_100000_create_invoices_table.php: consolidated by consolidate-migrations on 2025-10-29.
// Final definition of `invoices`. Folded in: 2023_04_02_100000_add_status_to_invoices,
//         2023_09_01_100000_add_invoice_number_index, 2024_02_11_100000_invoice_amount_check.

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('invoices', function (Blueprint $table) {
            $table->comment('One row per issued invoice');
            $table->engine('InnoDB');                       // only when the originals set it

            $table->id();
            $table->foreignId('customer_id')->constrained()->cascadeOnDelete();
            $table->string('number', 32);
            $table->string('status', 20)->default('draft')->comment('draft|paid|void');
            $table->decimal('amount', 12, 2)->unsigned();
            $table->unsignedBigInteger('amount_cents');
            $table->timestamps();
            $table->softDeletes();

            $table->unique('number');                              // invoices_number_unique
            $table->index(['customer_id', 'status']);              // invoices_customer_id_status_index
        });

        // Blueprint has no check constraints; raw SQL is the only way (skip on SQLite).
        DB::statement('ALTER TABLE invoices ADD CONSTRAINT invoices_amount_nonnegative CHECK (amount_cents >= 0)');
    }

    public function down(): void
    {
        Schema::dropIfExists('invoices');   // drops its indexes, FKs and checks with it
    }
};
```

The file keeps the original's name (`2023_01_10_100000_create_invoices_table.php`); only its content changes. When the table was renamed later, the name still says the old one (`create_bills_table.php` now creates `invoices`) and the header adds `Renamed from bills (2023_06_01_100000_rename_bills_to_invoices)`. An original that created several tables holds the final definition of each, in plan order, one `Schema::create` per table, unless the user chose to split it; `down` drops them in reverse.

Keep the original index names when the originals passed explicit names; otherwise let Blueprint generate them, because the generated name is what later `dropIndex([...])` calls in the project expect.

## Writing a view migration

```php
return new class extends Migration
{
    public function up(): void
    {
        DB::statement(<<<'SQL'
            CREATE VIEW customer_summary AS
            SELECT c.id AS customer_id, c.name, COUNT(i.id) AS invoice_count
            FROM customers c LEFT JOIN invoices i ON i.customer_id = c.id
            GROUP BY c.id, c.name
        SQL);
    }

    public function down(): void
    {
        DB::statement('DROP VIEW IF EXISTS customer_summary');
    }
};
```

`DB::statement()` is the escape hatch. Use `DB::unprepared()` for bodies that the PDO driver cannot prepare (MySQL triggers and procedures with `BEGIN … END`, Postgres `$$` function bodies on some drivers).

## The extra files

Only what no kept file can hold gets a new file, numbered after the last original (e.g. `2023_11_20_100001_deferred_foreign_keys.php`).

- Prerequisites have no file of their own: `DB::statement('CREATE EXTENSION IF NOT EXISTS citext')`, `DB::statement("CREATE TYPE invoice_status AS ENUM ('draft','paid')")`, `DB::statement('CREATE SCHEMA billing')` stay at the top of `up()` in the kept file that created them, or of the earlier kept file the plan pulled them into (`moved_in`); `down` drops them last, in reverse.
- Functions and triggers sit at the end of `up()` in their kept file (one `DB::unprepared()` each, verbatim `raw`). Only those that read something created after every candidate original go to `functions_and_triggers`; `down` runs `DROP TRIGGER … ON <table>` then `DROP FUNCTION`.
- `deferred_foreign_keys`: `Schema::table('customers', fn (Blueprint $t) => $t->foreign('primary_invoice_id')->references('id')->on('invoices')->nullOnDelete());` and `dropForeign(['primary_invoice_id'])` in `down`.
- `seed_reference_data`: the `code` from the kept `data` ops, e.g. `DB::table('tags')->insert([...])`; `down` deletes the same rows by key.
- Split-out tables: `<version>_create_<table>_table.php` with the version from the plan, written like any table migration.

## Schema snapshot to cross-check

None generated by default. If `database/schema/<connection>-schema.sql` exists (from `schema:dump`), it is a raw SQL dump of a real database at dump time and a useful cross-check: every `CREATE TABLE` in it should match a manifest table column for column. `app/Models/*` only tell you `$fillable`/`$casts`, not types.

The built-in alternative, `php artisan schema:dump [--prune]`, is a different thing: it needs a live database plus `mysqldump` / `pg_dump` / `sqlite3` on the PATH, writes one dialect-specific SQL file (including the `migrations` rows) that Laravel loads only when the `migrations` table does not exist yet, and `--prune` deletes the migration files. The result is not readable per table, cannot be edited with Blueprint, and ties tests to that dialect (an in-memory SQLite test database cannot load a MySQL dump). This skill keeps everything in PHP, in the files of the originals that created each table.

## After placing the files

- Keep the archive directory outside `database/migrations/` (or any `--path`) and out of `composer.json` `classmap`, or Laravel globs it (the archived original and its kept file have the same name) / autoloads named classes twice.
- A `database/schema/*-schema.sql` from before the swap is stale: it carries the old `migrations` rows (removed originals, none for the new files), so a fresh database would load it and then run the new files on top of it and fail on "already exists". Delete it, or regenerate it with `php artisan schema:dump` from a database that has been baselined.
- `php artisan migrate --pretend` against any database prints the SQL the consolidated files would run, without executing it. `php artisan migrate:status` lists files and their `Ran`/`Pending` state (it iterates files only; database rows with no file are not shown).

## Tracking table and reconciliation

Table `migrations` (name from `config/database.php` → `migrations.table`, older apps `migrations` as a plain string): `id` (auto-increment), `migration` (string, file name without `.php`), `batch` (integer).

- Row without file: lenient (`migrate` ignores it, `migrate:rollback` prints `Migration not found` and skips, `migrate:status` does not list it). File without row: pending, runs on the next `migrate` whatever its date. No checksum.

Precheck first (`precheck.sql`): lists every original this database has not run; it must return no rows, otherwise the kept files look applied while their folded alters never ran.

```sql
SELECT m.v AS not_applied FROM (SELECT '2023_01_10_100000_create_invoices_table' AS v UNION ALL SELECT '…every original…' AS v) m
WHERE NOT EXISTS (SELECT 1 FROM migrations t WHERE t.migration = m.v);
```

Baseline for an existing database (what `baseline.py` emits), inside one transaction:

```sql
DELETE FROM migrations WHERE migration IN ('2023_04_02_100000_add_status_to_invoices', '…every removed original…');
INSERT INTO migrations (migration, batch) SELECT m.migration, 7 FROM (   -- --batch 7; default (SELECT COALESCE(MAX(batch), 0) + 1 …)
  SELECT '2023_11_20_100001_deferred_foreign_keys' AS migration UNION ALL
  SELECT '2023_11_20_100002_seed_reference_data' AS migration) AS m;
```

Kept files keep their rows and their original batch; nothing is written for them. The DELETE is tidy-up (`migrate` ignores those rows) but keeps `migrate:rollback` from stepping onto removed names; the INSERT is mandatory, otherwise the next `migrate` runs the new files and fails on objects that already exist. All new rows share one batch, so `migrate:rollback` of the last batch reverts only them.

## Quirks

- `timestamps()` columns are nullable; `softDeletes()` is nullable; `id()` is unsigned bigint. Write them with the helper, not expanded, so the project keeps its conventions.
- `constrained()` naming follows the *column* name (`customer_id` → table `customers`), not the referenced table; when the originals used `constrained('accounts')` keep the explicit argument.
- Check constraints, views, functions, triggers, Postgres enums and extensions all live in `DB::statement()`; they are not reversible automatically, so always write `down`.
- SQLite: `DB::statement('ALTER TABLE … ADD CONSTRAINT …')` fails; guard with `if (DB::getDriverName() !== 'sqlite')` when the project runs tests on SQLite. Column `->change()` on SQLite requires `doctrine/dbal` before Laravel 11; on 11+ it is native.
- A `foreignId()->constrained()` in `Schema::create` of table A referencing table B requires B to exist; `plan_order.py` handles that: a FK whose target's file comes later goes to `deferred_foreign_keys` (the column stays in A's file, as plain `foreignId()` without `constrained()`). Self-references (`parent_id` on the same table) are fine inline.
