# Knex (3)

## Layout and naming

- `knexfile.js` / `knexfile.ts` / `knexfile.mjs` (per environment) sets `migrations: { directory: './migrations', tableName: 'knex_migrations', extension: 'ts', loadExtensions: ['.ts'], schemaName, disableTransactions, disableMigrationsListValidation, sortDirsSeparately, stub }`. Default directory `./migrations`, default extension `js`.
- `knex migrate:make create_users` writes `{YYYYMMDDHHMMSS}_create_users.js` (`yyyymmddhhmmss()` helper, local time, 14 digits, underscore).
- Modules export `up` and `down` (CJS `exports.up = function (knex) {}`, ESM `export async function up(knex) {}`, TS `export async function up(knex: Knex): Promise<void>`), optionally `exports.config = { transaction: false }`.
- `directory` can be an array; files from all directories are merged and sorted together unless `sortDirsSeparately: true`.
- Consolidated files: a kept original keeps its file name, extension included (`20230110100000_create_orders.ts`), and holds the final definition of what it created; originals that only altered or dropped things disappear. New files are numbered from one second after the last original, one second apart (`20231120100001_functions_and_triggers.ts`, ...), except a table split out of a multi-table original, which takes a free second right after it when there is one.

## Replay order

- Files are read with `readdir`, filtered by `loadExtensions`, sorted by file name (plain string sort). No dependency metadata; the timestamp prefix is the order.
- `migrate:latest` runs every file without a tracking row, in that order, as one batch; `migrate:rollback` undoes the last batch, `migrate:up` / `migrate:down` one file. A new file with an older prefix than applied ones is simply run next time.

## Reading originals into ops

The schema builder expands into DDL with knex's own default names; record them so the consolidated files produce identical constraints.

- `table.increments('id')` -> column `{name: "id", type: "increments", primary: true, auto_increment: true}` (Postgres `serial` + DB-named primary key, MySQL `int unsigned AUTO_INCREMENT`).
- `table.timestamps(true, true)` -> `created_at` and `updated_at`, `{type: "timestamp", nullable: false, default: "CURRENT_TIMESTAMP"}`; bare `table.timestamps()` gives nullable `datetime` columns without defaults.
- `table.integer('user_id').unsigned().notNullable().references('id').inTable('users').onDelete('CASCADE')` -> `add_column` plus `add_foreign_key {name: "posts_user_id_foreign", on_delete: "cascade"}`; `table.foreign('user_id').references('users.id')` is the same FK without the column.
- `table.unique(['email'])` -> index `{name: "users_email_unique", unique: true}`; `table.index(['a', 'b'])` -> `{name: "posts_a_b_index"}`; `table.unique(['a'], { indexName: 'x' })` keeps `x`.
- `table.enu('status', ['draft', 'paid'])` -> Postgres: `text` column plus `add_check` (`status IN ('draft','paid')`, DB-named); MySQL: `enum_values`; with `{ useNative: true, enumName: 'order_status' }` -> `create_type` and `type_ref`.
- `table.string('name')` is `varchar(255)`; `table.decimal('amount', 12, 2)`, `table.boolean('active').defaultTo(false)`, `table.uuid('id').primary().defaultTo(knex.fn.uuid())`, `table.jsonb('meta')`, `table.text('body')` map one to one.
- `table.check('amount >= 0', [], 'orders_amount_nonneg')` -> `add_check`; `table.comment('...')` / `col.comment('...')` -> `set_comment`.
- `knex.schema.alterTable('users', t => { t.string('name', 100).alter(); t.renameColumn('a', 'b'); t.dropColumn('c'); })` -> `alter_column`, `rename_column`, `drop_column`.
- `knex.schema.raw('CREATE VIEW ...')` / `knex.schema.createView(...)` -> `create_view`; `knex('roles').insert([...])` -> `data`.
- `knex.schema.withSchema('billing').createTable(...)` sets `schema`.

## Writing a consolidated table migration

`migrations/20230110100000_create_orders.ts`, the kept original:

```ts
import type { Knex } from "knex";

// Consolidated by consolidate-migrations: final definition of orders.
// Folded: 20230110100000_create_orders (this file), 20230305100100_add_order_status,
// 20230601100000_add_order_indexes, 20231120100000_order_customer_fk
export async function up(knex: Knex): Promise<void> {
  await knex.schema.createTable("orders", (table) => {
    table.increments("id");
    table.integer("customer_id").unsigned().notNullable()
      .references("id").inTable("customers").onDelete("CASCADE");
    table.string("number", 32).notNullable();
    table.enu("status", ["draft", "paid", "cancelled"], { useNative: true, enumName: "order_status" })
      .notNullable().defaultTo("draft");
    table.bigInteger("amount_cents").notNullable().defaultTo(0).comment("Total in minor units");
    table.text("note").nullable();
    table.timestamps(true, true);

    table.unique(["number"]);                                  // orders_number_unique
    table.index(["customer_id", "created_at"]);                // orders_customer_id_created_at_index
    table.check("amount_cents >= 0", [], "orders_amount_cents_check");
    table.comment("Customer orders");
  });
}

export async function down(knex: Knex): Promise<void> {
  await knex.schema.dropTable("orders");
  await knex.schema.raw('DROP TYPE IF EXISTS "order_status"');   // Postgres native enum only
}
```

Use CJS `exports.up = async function (knex) {}` when the originals do; knex loads whichever extensions `loadExtensions` lists. A kept file keeps its extension even if the project has since moved to `.ts`: the extension is part of the recorded name. A kept multi-table original (not split) creates all its tables in FK order, and `down` drops them in reverse.

## Writing a view migration

The builder has `createView` / `createViewOrReplace` / `createMaterializedView`, but `knex.schema.raw` keeps the SELECT body verbatim, which is what the manifest holds. The view stays in its creating file when everything it reads exists by then, otherwise it goes to a new file after the last original (`20231120100002_create_customer_summary_view.ts`):

```ts
export async function up(knex: Knex): Promise<void> {
  await knex.schema.raw(`CREATE VIEW customer_summary AS SELECT c.id, count(o.id) AS orders FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id`);
}
export async function down(knex: Knex): Promise<void> {
  await knex.schema.dropViewIfExists("customer_summary");
}
```

## The extra files

New files, in plan order after the last original:

- `functions_and_triggers` (`20231120100001_functions_and_triggers.ts`, only what could not stay in its creating file): `knex.schema.raw(\`CREATE OR REPLACE FUNCTION ... $$ LANGUAGE plpgsql\`)` then `knex.schema.raw('CREATE TRIGGER ...')`.
- `deferred_foreign_keys` (`20231120100003_deferred_foreign_keys.ts`): `knex.schema.alterTable('users', t => { t.foreign('current_team_id').references('teams.id').onDelete('SET NULL'); })`; down `t.dropForeign(['current_team_id'])`. SQLite keeps forward FKs inline.
- `seed_reference_data` (`20231120100004_seed_reference_data.ts`): `await knex('roles').insert([{ name: 'admin' }, { name: 'user' }])`; down deletes by the same keys.

Prerequisites get no file: `await knex.schema.raw('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')`, `knex.schema.createSchemaIfNotExists('billing')`, `CREATE TYPE` stay in the kept original that created them, or move to the top of the `up` of an earlier kept file that needs them.

## Schema snapshot to cross-check

None. Knex keeps no schema file; Objection or Bookshelf models, when present, declare relations but not columns, so the manifest is only checked against the originals.

## After placing the files

- Archive outside `migrations.directory` (a subfolder is skipped because its name has no loaded extension, but keep it outside anyway for clarity). `knex migrate:list` must show exactly the consolidated files (kept and new), and errors with the corrupt-directory message until the tracking table is reconciled.
- TypeScript projects that run compiled migrations in production: rebuild so `dist/migrations` contains only the consolidated files.
- Regenerate nothing else; there is no snapshot.

## Tracking table and reconciliation

- `knex_migrations`: `id` (auto-increment primary key), `name` varchar(255), `batch` integer, `migration_time` timestamp. `knex_migrations_lock`: `index` (auto-increment primary key), `is_locked` integer, one row. Both renameable through `tableName` (lock table is `{tableName}_lock`), placed in `schemaName` when set.
- `name` is the file name **with** extension (`20231120100000_order_customer_fk.js`).
- Strict: `latest`, `up`, `down`, `rollback`, `list` and `status` all call `validateMigrationList` and throw `The migration directory is corrupt, the following files are missing: ...` when an applied row has no file. `disableMigrationsListValidation: true` turns the check off, but the baseline below is the clean fix. Files without a row are pending.
- No checksum.
- Baseline an existing database (`precheck.sql` first; it must return no rows, i.e. every original has a row). Kept files keep their rows; the DELETE of removed originals is mandatory:

```sql
DELETE FROM knex_migrations WHERE name IN ('20230305100100_add_order_status.ts', '20230601100000_add_order_indexes.ts', ...);  -- removed originals, with extension
INSERT INTO knex_migrations (name, batch, migration_time)
SELECT m.name, (SELECT COALESCE(MAX(b.batch), 0) + 1 FROM (SELECT batch FROM knex_migrations) AS b), CURRENT_TIMESTAMP
FROM (SELECT '20231120100001_functions_and_triggers.ts' AS name UNION ALL SELECT '20231120100003_deferred_foreign_keys.ts' AS name) AS m;
```

The new rows get `MAX(batch) + 1` (`baseline.py --batch N` fixes it), so a later `migrate:rollback` reverts only the new files; kept rows keep their original batch, and rolling one of those back now drops the whole final table, not one alter.

## Quirks

- The stored name includes the extension, so a project that runs `.ts` in development and compiled `.js` in production records two different names for the same migration. Match the extension to how each environment runs migrations.
- On Postgres a batch runs in one transaction (unless `disableTransactions` or a file's `config.transaction = false`); MySQL commits DDL per statement.
- `increments()` on Postgres is `serial`, not `identity`; the primary key constraint is DB-named (`orders_pkey`).
- `enu()` without `useNative` is a check constraint on Postgres, so changing the values later needs `dropChecks` / raw SQL; with `useNative` the type outlives `dropTable`.
- `timestamps()` without arguments creates nullable columns with no default; most projects meant `timestamps(true, true)`.
- A stuck `is_locked = 1` after a crash is cleared with `knex migrate:unlock`.
- `sortDirsSeparately` changes replay order across multiple directories; transcribe in the order `migrate:list` prints.
