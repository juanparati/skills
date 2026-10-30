# Drizzle Kit (0.2x / 1.x)

## Layout and naming

Two layouts exist; the migrator rejects the wrong one, so detect first.

- **Journal layout (drizzle-kit 0.2x up to 0.31):** `drizzle.config.ts` sets `out: './drizzle'`, `schema`, `dialect`. The folder holds `NNNN_adjective_noun.sql` (4-digit index plus a random two-word name, or `--name=`), `meta/_journal.json` and `meta/NNNN_snapshot.json`. Journal: `{ "version": "7", "dialect": "postgresql", "entries": [{ "idx": 0, "version": "7", "when": 1700000000000, "tag": "0000_fancy_wolverine", "breakpoints": true }] }` (`version` is 7 for postgresql, 6 for sqlite/turso, 5 for mysql). `migrations.prefix` in the config switches the tag prefix: `index` (default), `timestamp` (`20241105120000_name`), `unix`, `supabase`, `none`.
- **Folder layout (drizzle-kit 1.0):** `drizzle/{YYYYMMDDHHMMSS}_{name}/migration.sql` plus `snapshot.json` in the same folder. No journal; the 14-digit UTC prefix is the version and folders sort by name. `drizzle-kit up` converts a journal layout to this one, and `migrate()` throws `We detected that you have old drizzle-kit migration folders` when it still finds `meta/_journal.json`.
- Statements inside a `.sql` file are separated by a line `--> statement-breakpoint` (config `breakpoints: true`, the default; required by drivers that cannot run multi-statement strings).
- `drizzle-kit generate --custom --name=create_users` writes an empty migration (`-- Custom SQL migration file, put your code below! --`) with its journal entry / folder and a snapshot of the current TS schema. It always appends (next `idx`, new `when`, new tag), so it cannot recreate a kept original's tag or `when`.
- Consolidated files: a kept original keeps its tag (`0003_create_orders`, or `0003_brave_wolverine`), its `when` and its folder name in 1.x, and holds the final definition of what it created; originals that only altered or dropped things disappear from the folder and the journal. New files are numbered after the last original (`0009_*` last -> `0010_functions_and_triggers`), never from `0000` again.

## Replay order

- Journal layout: entries in journal order; the runtime migrator (`migrate()` from `drizzle-orm/<driver>/migrator`, also used by `drizzle-kit migrate`) reads the last tracking row and runs every entry whose `when` is greater than that row's `created_at`. Nothing else is compared: not the tag, not the hash.
- Folder layout: folders sorted by name; a folder runs when its name is not in the `name` column (rows with `name` NULL are legacy). Out-of-order folders are allowed.
- All pending migrations run inside one transaction on Postgres.

## Reading originals into ops

Files are dialect SQL in drizzle-kit's style. Names follow drizzle's conventions and also live in the snapshot, so keep them.

- `CREATE TABLE "users" ("id" serial PRIMARY KEY NOT NULL, "email" text NOT NULL, "created_at" timestamp DEFAULT now() NOT NULL, CONSTRAINT "users_email_unique" UNIQUE("email"));` -> `create_table` with `id` `{type: "serial", primary: true, auto_increment: true}` and index `{name: "users_email_unique", unique: true}`. Composite keys appear as `CONSTRAINT "t_a_b_pk" PRIMARY KEY("a","b")`.
- `ALTER TABLE "posts" ADD CONSTRAINT "posts_author_id_users_id_fk" FOREIGN KEY ("author_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;` -> `add_foreign_key` (older 0.2x wrapped this in `DO $$ BEGIN ... EXCEPTION WHEN duplicate_object THEN null; END $$;`; unwrap it).
- `CREATE INDEX "posts_author_idx" ON "posts" USING btree ("author_id");` -> `add_index {method: "btree"}`; `CREATE TYPE "public"."status" AS ENUM('draft', 'published');` -> `create_type`.
- `ALTER TABLE "users" ADD COLUMN "name" text;` / `ALTER TABLE "users" ALTER COLUMN "name" SET NOT NULL;` / `ALTER TABLE "users" DROP COLUMN "name";` -> `add_column` / `alter_column` / `drop_column`.
- SQLite: `PRAGMA foreign_keys=OFF;` `CREATE TABLE \`__new_users\` (...);` `INSERT INTO \`__new_users\`(...) SELECT ... FROM \`users\`;` `DROP TABLE \`users\`;` `ALTER TABLE \`__new_users\` RENAME TO \`users\`;` `PRAGMA foreign_keys=ON;` is one redefinition of `users`: diff the new column list against the current state and emit column ops, never a `drop_table`.
- MySQL: `` CREATE TABLE `users` (`id` serial AUTO_INCREMENT NOT NULL, ..., CONSTRAINT `users_id` PRIMARY KEY(`id`)) `` -> `create_table`.
- A custom file with `INSERT` / `UPDATE` statements -> `data` op; a `CREATE VIEW` -> `create_view` (drizzle-kit generates `CREATE VIEW "x" AS (select ...)` for `pgView` definitions).

## Writing a consolidated table migration

The TS schema is the source of truth; the migrations must bring an empty database to exactly what the latest snapshot describes. Only the hand-written path works: `generate --custom --name` assigns a new index, tag and `when`, which cannot keep the originals' names.

1. Move the original `.sql` files and `meta/` (or the original folders) to the archive location.
2. Write each planned file under its plan name: kept files with the original tag (`drizzle/0003_create_orders.sql`), new files after the last original. Take the SQL from the manifest, or from `npx drizzle-kit generate` output of a throwaway run against an empty `out` (the full `CREATE` script for the whole schema), cut per file.
3. Rewrite `meta/_journal.json` and the snapshots (see After placing the files).
4. `npx drizzle-kit generate` must print `No schema changes, nothing to migrate 😴`; `npx drizzle-kit check` validates the folder.

`drizzle/0003_create_orders.sql`, the kept original (folder layout: `drizzle/20240310093000_create_orders/migration.sql`):

```sql
-- Consolidated by consolidate-migrations: final definition of orders.
-- Folded: 0003_create_orders (this file), 0005_add_order_status, 0007_add_order_indexes, 0009_order_customer_fk
CREATE TYPE "public"."order_status" AS ENUM('draft', 'paid', 'cancelled');
--> statement-breakpoint
CREATE TABLE "orders" (
	"id" serial PRIMARY KEY NOT NULL,
	"customer_id" integer NOT NULL,
	"number" varchar(32) NOT NULL,
	"status" "order_status" DEFAULT 'draft' NOT NULL,
	"amount_cents" bigint DEFAULT 0 NOT NULL,
	"note" text,
	"created_at" timestamp DEFAULT now() NOT NULL,
	"updated_at" timestamp DEFAULT now() NOT NULL,
	CONSTRAINT "orders_number_unique" UNIQUE("number"),
	CONSTRAINT "orders_amount_cents_check" CHECK ("orders"."amount_cents" >= 0)
);
--> statement-breakpoint
ALTER TABLE "orders" ADD CONSTRAINT "orders_customer_id_customers_id_fk" FOREIGN KEY ("customer_id") REFERENCES "public"."customers"("id") ON DELETE cascade ON UPDATE no action;
--> statement-breakpoint
CREATE INDEX "orders_customer_created_idx" ON "orders" USING btree ("customer_id","created_at");
--> statement-breakpoint
COMMENT ON COLUMN "orders"."amount_cents" IS 'Total in minor units';
```

There is no `down`; drizzle-kit has no rollback. Comments are not in the TS schema, so they are reproduced from the originals only. The enum came from `0005_add_order_status`; it moved up because `orders` needs it, and `0005` disappeared. A kept multi-table original (`0000_*`, not split) holds every table it created, in FK order, separated by breakpoints.

## Writing a view migration

Views defined with `pgView(...).as(...)` are part of the schema and come out of `generate`; views created by hand stay raw SQL, in their creating file when everything they read exists by then, otherwise in a new file after the last original (`0011_create_customer_summary_view.sql`):

```sql
CREATE VIEW "public"."customer_summary" AS (select c.id, count(o.id) as orders from customers c left join orders o on o.customer_id = c.id group by c.id);
```

## The extra files

Hand-written new files, appended to the journal after the last original in plan order: `0010_functions_and_triggers.sql` (`CREATE OR REPLACE FUNCTION ... $$ LANGUAGE plpgsql;` then `CREATE TRIGGER ...`, separated by breakpoints; only what could not stay in its creating file), moved views, `0012_deferred_foreign_keys.sql` (`ALTER TABLE "users" ADD CONSTRAINT "users_current_team_id_teams_id_fk" FOREIGN KEY ... ON DELETE set null;`; SQLite keeps forward FKs inline), `0013_seed_reference_data.sql` (`INSERT INTO "roles" ("name") VALUES ('admin'), ('user');`). Prerequisites get no file: `CREATE EXTENSION IF NOT EXISTS "citext";` `--> statement-breakpoint` `CREATE SCHEMA "billing";` and enums stay in the kept original that created them, or move to the top of an earlier kept file that needs them.

## Schema snapshot to cross-check

Two sources, both static: the TS schema files (`pgTable('orders', { id: serial().primaryKey(), customerId: integer('customer_id').notNull().references(() => customers.id, { onDelete: 'cascade' }), ... }, (t) => [index('orders_customer_created_idx').on(t.customerId, t.createdAt), check('orders_amount_cents_check', sql`...`)])`) and the last snapshot JSON (`tables`, `enums`, `schemas`, each column with `type`, `primaryKey`, `notNull`, `default`; `indexes`, `foreignKeys`, `uniqueConstraints`, `checkConstraints` keyed by name). Every table, column, index and FK name in the latest snapshot must be in the manifest and vice versa; the snapshot is the easier of the two to diff.

## After placing the files

- Journal (`meta/_journal.json`): keep the entries of kept originals with their `tag`, `when` and `breakpoints`; remove the entries of removed originals; append one entry per new file (`tag` = file stem, `when` in ms greater than every original's `when`, `breakpoints: true`). `idx`: the migrator does not read it; whether drizzle-kit accepts gaps is unverified. Renumbering 0..n-1 (keeping `tag` and `when`) removes the gaps, but the next `generate` appears to take the last entry's `idx` + 1 as its tag prefix and snapshot name (index prefix mode), so after renumbering it would write e.g. `0008_*` after `0013_*` and its snapshot would sort before the newest one. Keeping `idx` equal to each tag's number (new entries continue from the last) avoids that. Either way, verify with `drizzle-kit check` and a `generate` that reports no changes. Folder layout: kept folders keep their names, removed folders go, new folders are named after the last original's timestamp.
- Snapshots (`meta/NNNN_snapshot.json`, chained by `id` / `prevId`): the newest original snapshot describes the final schema; keep it as the snapshot of the LAST journal entry (renamed to that entry's prefix, replacing its own). Delete the snapshots of removed entries and re-chain `prevId` over the ones left (the first points at `00000000-0000-0000-0000-000000000000`); when a copy is needed give it a fresh UUID `id`, because drizzle-kit reports snapshots that share a parent as a collision. Intermediate kept snapshots no longer match their SQL; only the latest is diffed. Folder layout: the same with `<folder>/snapshot.json`. Warn the user that the chain is hand-made and must be verified with `drizzle-kit check` and `drizzle-kit generate` reporting no changes.
- Archive outside `out`; a leftover `meta/` or stray `.sql` next to the new files breaks the journal or shows up as an unknown folder.
- Expo / durable-sqlite bundles: `migrations.js` imports one module per journal entry; update it to the kept and new entries (its keys follow `idx`; verify against the bundled migrator), or re-run `generate` and check that it rewrote the file.
- Final gate: `drizzle-kit generate` silent, `drizzle-kit check` clean.

## Tracking table and reconciliation

- Postgres: `drizzle.__drizzle_migrations` (`migrations.schema` / `migrations.table` in the config, `migrationsSchema` / `migrationsTable` in `migrate()`), columns `id SERIAL PRIMARY KEY`, `hash text NOT NULL`, `created_at bigint`; drizzle-kit 1.0 adds `name text` and `applied_at timestamptz DEFAULT now()` (the migrator upgrades the table on first run). MySQL: `__drizzle_migrations` in the database, `created_at bigint`; SQLite: `__drizzle_migrations`, `created_at numeric`.
- `hash` = SHA-256 hex of the whole `.sql` file text (breakpoint lines included, line endings as on disk). It is stored and never compared, in both versions.
- 0.2x decides by `created_at` only: rows without files are irrelevant (lenient), and any entry whose `when` is newer than the last row runs. 1.x decides by `name`: orphan rows are ignored (lenient), folders without a row run.
- Baseline an existing database (journal layout). Kept entries keep their `when`, so they are already behind the last row; the new entries come last, so one row with the LAST entry's `when` and hash marks them all applied. Rows are never deleted. No new files: nothing to record.

```sql
-- precheck: SELECT MAX(created_at) FROM drizzle.__drizzle_migrations;
--           must equal the `when` of the LAST entry of the old meta/_journal.json
INSERT INTO drizzle.__drizzle_migrations (hash, created_at) VALUES ('<sha256 of 0013_seed_reference_data.sql>', 1731000000004);
```

Folder layout (1.x, matched by `name`): delete the rows of removed folders and insert one row per new folder, `created_at` = the folder prefix as UTC milliseconds: `DELETE FROM drizzle.__drizzle_migrations WHERE name IN ('20240402100000_add_order_status', ...);` `INSERT INTO drizzle.__drizzle_migrations (hash, created_at, name) VALUES ('<sha256>', 1731492001000, '20241113100001_deferred_foreign_keys');`.

## Quirks

- In 0.2x a hand-written journal with `when` values older than the last applied row never runs on that database, and a new file with an older `when` than the last row is skipped silently; `when` must keep increasing. That is why kept entries keep their `when` (they count as applied) and new entries get one above every original's.
- `drizzle-kit push` applies schema diffs directly and records nothing; projects that used `push` in development have databases with no tracking rows at all.
- The whole pending set runs in one Postgres transaction; one broken consolidated file rolls back everything. MySQL and SQLite commit per statement.
- Random tag names (`0003_brave_wolverine`) carry no meaning, but a kept file keeps its tag anyway; only new files get descriptive names.
- Snapshot ids must be unique and chained through `prevId`; duplicating one snapshot file verbatim trips `drizzle-kit check`.
- `drizzle-kit up` on a journal layout rewrites it into folders; run it before consolidating if the project is on 1.0 but still has `meta/`.
- Custom migrations carry `-- Custom SQL migration file` and often data; those are the `data` ops.
- Enum values and `pgSchema` definitions are in the TS schema too; dropping them from the SQL makes `generate` recreate them.
