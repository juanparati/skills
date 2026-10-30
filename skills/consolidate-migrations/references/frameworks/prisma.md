# Prisma (5 / 6)

## Layout and naming

- `prisma/migrations/` (next to `schema.prisma`; `prisma/schema.prisma` can be moved with `--schema`, the folder follows it). One folder per migration: `{YYYYMMDDHHMMSS}_{name}/migration.sql`. The name part is whatever was passed to `prisma migrate dev --name`.
- `prisma/migrations/migration_lock.toml` holds `provider = "postgresql"` (or `mysql`, `sqlite`, ...). Keep it; `migrate deploy` errors when the provider changed.
- Folder names sort as strings; the 14-digit UTC timestamp keeps them chronological. Any prefix works (`0_init` is the documented baseline trick), only the sort matters.
- There is no `down`. Rollback is a new forward migration.
- NestJS projects wrap the client in a `PrismaService`; migrations are still plain `prisma` CLI runs.
- Kept folders keep their exact name (`20230110100000_init/`); only `migration.sql` is rewritten. `migration_lock.toml` is not touched. New folders are numbered after the last original, one second apart in plan order (split-out tables with no room, `functions_and_triggers`, moved views, `deferred_foreign_keys`, `seed_reference_data`): with only deferred FKs after a last original `20240502083000_…`, that is `20240502083001_deferred_foreign_keys/`.
- Prisma's own first migration (`init`) usually creates every table, so it is the multi-table keep/split question: kept together, `20230110100000_init/migration.sql` holds the final schema of every table it created; split, the table its name refers to keeps it (with no such table, as for `init`, the first one created) and each other table gets a folder at the next free second, `20230110100001_create_post/`, or after the last original when no second is free.

## Replay order

- `prisma migrate deploy` lists folders, sorts them by name, and applies every folder whose name has no non-rolled-back row in `_prisma_migrations`, in that order. No dependency metadata.
- `prisma migrate dev` additionally replays the whole folder on a shadow database and diffs the result against `schema.prisma`; divergence from the real database makes it propose a reset.

## Reading originals into ops

`migration.sql` is plain dialect SQL, grouped under comments that `migrate dev` wrote (`-- CreateTable`, `-- AlterTable`, `-- CreateIndex`, `-- AddForeignKey`, `-- DropForeignKey`, `-- CreateEnum`, `-- RedefineTables`). Use them as section markers; the SQL itself is what counts.

- `CREATE TABLE "User" ("id" SERIAL NOT NULL, "email" TEXT NOT NULL, "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP, "role" "Role" NOT NULL DEFAULT 'USER', CONSTRAINT "User_pkey" PRIMARY KEY ("id"));` -> `create_table` with `primary_key: {name: "User_pkey", columns: ["id"]}` and `role` carrying `type_ref: "type:Role"`.
- `CREATE UNIQUE INDEX "User_email_key" ON "User"("email");` -> `add_index {unique: true}`; `CREATE INDEX "Post_authorId_idx" ON "Post"("authorId");` -> `add_index`.
- `ALTER TABLE "Post" ADD CONSTRAINT "Post_authorId_fkey" FOREIGN KEY ("authorId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;` -> `add_foreign_key` (Prisma's default actions: `RESTRICT` for required relations, `SET NULL` for optional ones, always `ON UPDATE CASCADE`).
- `CREATE TABLE "_CategoryToPost" ("A" INTEGER NOT NULL, "B" INTEGER NOT NULL);` plus `CREATE UNIQUE INDEX "_CategoryToPost_AB_unique" ON "_CategoryToPost"("A", "B");`, `CREATE INDEX "_CategoryToPost_B_index" ON "_CategoryToPost"("B");` and two `_A_fkey` / `_B_fkey` constraints `ON DELETE CASCADE ON UPDATE CASCADE` -> a real `create_table` (do **not** set `implicit_m2m_of`: that flag is for Django-style join tables that get no file of their own; Prisma join tables are ordinary tables and stay in the folder that created them, or get their own folder when that folder is split). Newer Prisma versions may emit `CONSTRAINT "_CategoryToPost_AB_pkey" PRIMARY KEY ("A","B")` instead of the unique index (which version switched is unverified): copy whatever the originals or `migrate diff` produce.
- SQLite `-- RedefineTables` block: `PRAGMA defer_foreign_keys=ON; PRAGMA foreign_keys=OFF; CREATE TABLE "new_Post" (...); INSERT INTO "new_Post" ("id", "title") SELECT "id", "title" FROM "Post"; DROP TABLE "Post"; ALTER TABLE "new_Post" RENAME TO "Post"; PRAGMA foreign_keys=ON; PRAGMA defer_foreign_keys=OFF;` -> a redefinition of `Post`: compare the `new_Post` column list with the table's current state and emit `add_column` / `drop_column` / `alter_column` ops. Never a `drop_table`.
- MySQL: `` CREATE TABLE `User` (`id` INTEGER NOT NULL AUTO_INCREMENT, `email` VARCHAR(191) NOT NULL, UNIQUE INDEX `User_email_key`(`email`), PRIMARY KEY (`id`)) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; `` -> `create_table` with `options.charset` / `collation`.
- Hand-edited data statements (`INSERT`, `UPDATE`) inside a folder -> `data` op.

## Writing a consolidated table migration

Get exact per-table SQL without a database: `npx prisma migrate diff --from-empty --to-schema-datamodel prisma/schema.prisma --script > /tmp/full.sql` prints the whole current schema in Prisma's own style (enums first, then tables with indexes, all foreign keys at the end). Cut the statements of the tables a kept folder holds out of it. Prisma 7 renamed the flag to `--to-schema`.

`prisma/migrations/20230215093000_add_orders/migration.sql`, the kept folder that created `Order`:

```sql
-- Consolidated: final schema of "Order" (created here); folded: 20230305100100_order_status
-- (enum OrderStatus moved in), 20230601100000_order_indexes, 20231120100000_order_customer_fk

-- CreateEnum
CREATE TYPE "OrderStatus" AS ENUM ('DRAFT', 'PAID', 'CANCELLED');

-- CreateTable
CREATE TABLE "Order" (
    "id" SERIAL NOT NULL,
    "customerId" INTEGER NOT NULL,
    "number" VARCHAR(32) NOT NULL,
    "status" "OrderStatus" NOT NULL DEFAULT 'DRAFT',
    "amountCents" BIGINT NOT NULL DEFAULT 0,
    "note" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Order_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "Order_amountCents_check" CHECK ("amountCents" >= 0)
);

-- CreateIndex
CREATE UNIQUE INDEX "Order_number_key" ON "Order"("number");
CREATE INDEX "Order_customerId_createdAt_idx" ON "Order"("customerId", "createdAt");

COMMENT ON COLUMN "Order"."amountCents" IS 'Total in minor units';

-- AddForeignKey
ALTER TABLE "Order" ADD CONSTRAINT "Order_customerId_fkey" FOREIGN KEY ("customerId") REFERENCES "Customer"("id") ON DELETE CASCADE ON UPDATE CASCADE;
```

`OrderStatus` was created by `20230305100100_order_status`, whose only other change added `status` to `Order`; the column now lives in this earlier folder, which needs the enum, so the enum moved in and `order_status` disappears. The inline FK works because `Customer` is created by an earlier folder; otherwise it would be in `deferred_foreign_keys`. A table that was renamed keeps its creating folder; name the former table in the header.

Check constraints and comments are not expressible in `schema.prisma`; they only survive because they sit in migration SQL, so carry them over verbatim. `@updatedAt` has no database default (the client sets it): do not invent one.

## Writing a view migration

Prisma Migrate does not create views (the `views` preview feature only maps them), so the view is raw SQL, rewritten in the folder that created it, `20230701100000_customer_summary/migration.sql` (or, when it reads something created later, a new folder after the last original, `<version>_create_customer_summary_view/`):

```sql
-- Consolidated: final view "CustomerSummary" (created here); folded: 20230915100000_customer_summary_v2
DROP VIEW IF EXISTS "CustomerSummary";
CREATE VIEW "CustomerSummary" AS SELECT c."id", count(o."id") AS "orders" FROM "Customer" c LEFT JOIN "Order" o ON o."customerId" = c."id" GROUP BY c."id";
```

## The extra files

New folders, after the last original, only when the plan lists them:

- `functions_and_triggers`: `CREATE OR REPLACE FUNCTION ... $$ LANGUAGE plpgsql;` and `CREATE TRIGGER ...` statements as they were, for the ones no original can hold (they read something created after every candidate). The rest stay in the folder that created them.
- `deferred_foreign_keys`: `ALTER TABLE "User" ADD CONSTRAINT "User_currentTeamId_fkey" FOREIGN KEY ("currentTeamId") REFERENCES "Team"("id") ON DELETE SET NULL ON UPDATE CASCADE;`.
- `seed_reference_data`: `INSERT INTO "Role" ("name") VALUES ('admin'), ('user');` (Prisma's own seeding lives in `prisma/seed.ts`, outside migrations; only inserts found in migration folders go here).

Prerequisites get no folder of their own: `CREATE EXTENSION IF NOT EXISTS "citext";`, `CREATE SCHEMA IF NOT EXISTS "billing";` (multiSchema), enums and sequences stay in the folder that created them (`init`, usually), or move into an earlier kept folder whose table needs them.

## Schema snapshot to cross-check

`prisma/schema.prisma` is the source of truth; the migrations must reproduce it exactly. Static check: run `prisma migrate diff --from-empty --to-schema-datamodel prisma/schema.prisma --script` and compare each `CREATE TABLE` / `CREATE INDEX` / `ADD CONSTRAINT` with the manifest. Mapping rules while reading the datamodel: `@@map` / `@map` give the real names; `Int @id @default(autoincrement())` -> `SERIAL NOT NULL` + `_pkey`; `String` -> `TEXT` (Postgres) / `VARCHAR(191)` (MySQL); `DateTime` -> `TIMESTAMP(3)` / `DATETIME(3)`; `@unique` -> `{Table}_{col}_key`; `@@index` -> `{Table}_{cols}_idx`; `@@unique` -> `{Table}_{cols}_key`; relation scalar -> `{Table}_{col}_fkey`; `@db.VarChar(32)` and other native attributes set the type. Anything in the manifest that the diff output lacks (checks, comments, triggers) is migration-only and must be in the consolidated SQL anyway.

## After placing the files

- The archived originals go outside `prisma/migrations/` (every subfolder with a `migration.sql` is a migration, and kept folders share their names with the archived ones). `migration_lock.toml` stays untouched; in sibling mode copy it into `migrations.consolidated/` so the directory is drop-in.
- Full check when a throwaway database is available: `prisma migrate diff --from-migrations prisma/migrations --to-schema-datamodel prisma/schema.prisma --shadow-database-url "$SHADOW_URL" --exit-code` returns 0 when the folder reproduces the schema. `prisma migrate dev` on a developer database replays the folder on the shadow database and should print that the schema is in sync.
- `prisma generate` is unaffected; nothing to rebuild.
- Every existing database (developers' too) needs the precheck and the baseline below, or `migrate dev` offers a reset.

## Tracking table and reconciliation

- `_prisma_migrations` (Postgres): `id VARCHAR(36) PRIMARY KEY`, `checksum VARCHAR(64) NOT NULL`, `finished_at TIMESTAMPTZ`, `migration_name VARCHAR(255) NOT NULL`, `logs TEXT`, `rolled_back_at TIMESTAMPTZ`, `started_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `applied_steps_count INTEGER NOT NULL DEFAULT 0`. MySQL uses `DATETIME(3)`, SQLite `TEXT` / `DATETIME`.
- `checksum` = SHA-256 of the `migration.sql` text, lower-case hex, 64 characters (comparison also accepts the file with `\n` / `\r\n` swapped, and un-padded legacy checksums).
- `migrate deploy` only compares `migration_name` (ignoring rows with `rolled_back_at`) and refuses to run while a row has neither `finished_at` nor `rolled_back_at` (a failed migration). Applied rows without a folder and edited folders do not stop it: lenient. `migrate dev` and `migrate status` do report folders missing or edited (checksum mismatch), and `dev` proposes a reset.
- Kept folders already have their row, but their `migration.sql` changed, so their checksum must be refreshed; removed folders lose their rows; new folders get one. `baseline.py --dir prisma/migrations` (after the files are final) writes `precheck.sql` and a pure-SQL `baseline.sql`:

```sql
-- precheck.sql: must return no rows (any row = that database has not run every original; migrate it on the old history first)
SELECT m.v AS not_applied FROM (SELECT '20230110100000_init' AS v UNION ALL SELECT '20230215093000_add_orders' AS v UNION ALL ...) m
WHERE NOT EXISTS (SELECT 1 FROM "_prisma_migrations" t WHERE t."migration_name" = m.v AND t."finished_at" IS NOT NULL AND t."rolled_back_at" IS NULL);
```
```sql
-- baseline.sql
DELETE FROM "_prisma_migrations" WHERE "migration_name" IN ('20230305100100_order_status', '20230601100000_order_indexes', ...);  -- removed folders
UPDATE "_prisma_migrations" SET "checksum" = '<sha256 hex of the new migration.sql>' WHERE "migration_name" = '20230215093000_add_orders';  -- one per kept folder
INSERT INTO "_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "started_at", "applied_steps_count")
VALUES ('<uuid>', '<sha256 hex of migration.sql>', CURRENT_TIMESTAMP, '20240502083001_deferred_foreign_keys', '', CURRENT_TIMESTAMP, 1);  -- one per new folder
```

MySQL: backticks. Without the `UPDATE`s, `migrate dev` and `migrate status` report the kept folders as modified and `dev` proposes a reset; `deploy` would not notice. The `INSERT` is what `npx prisma migrate resolve --applied 20240502083001_deferred_foreign_keys` does (row with the file's checksum, `started_at` = `finished_at` = now, empty `logs`, `applied_steps_count` 1), so the CLI is an alternative **for new folders only**, once they are in `prisma/migrations/` (after the swap, in sibling mode); it cannot refresh a kept folder, which is already recorded as applied (P3008).

## Quirks

- Identifiers are quoted and case-sensitive (`"User"`, `"createdAt"`); unquoted names in hand-written SQL fold to lower case on Postgres and silently miss.
- SQLite cannot `ALTER COLUMN` or `ADD CONSTRAINT`; everything beyond add-column arrives as a `-- RedefineTables` rebuild.
- Implicit many-to-many tables (`_AToB`) are real tables with real foreign keys; they stay in the folder that created them, and an FK to a side created in a later folder goes to `deferred_foreign_keys`.
- `prisma migrate dev` resets the database when the history diverges; never point it at shared data. `migrate deploy` never resets.
- Prisma 7 moved connection settings to `prisma.config.ts` and renamed diff flags (`--to-schema`, `--from-config-datasource`); the folder layout and `_prisma_migrations` are unchanged.
- Each migration runs in its own transaction on Postgres; MySQL commits DDL statement by statement, so a half-failed file leaves a partial table and a failed row that blocks `deploy` until `migrate resolve --rolled-back`.
- Editing a `migration.sql` after the baseline (or `resolve --applied`) changes its checksum; `migrate dev` then flags it. Finalize files first, then run `baseline.py --dir`.
- A database that ran `20230215093000_add_orders` but not yet `20230305100100_order_status` sees the rewritten `add_orders` as applied and never gets `status`; nothing fails. That is what the precheck catches.
