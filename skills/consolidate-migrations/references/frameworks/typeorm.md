# TypeORM (0.3)

## Layout and naming

- Migrations live wherever the DataSource's `migrations` glob points (`migrations: ['src/migrations/*.ts']` in dev, `['dist/migrations/*.js']` when running compiled code). `src/migrations/` is the usual choice; there is no default directory.
- `typeorm migration:create src/migrations/CreateUsers` writes `{ms}-CreateUsers.ts` where `{ms}` is a 13-digit `Date.now()`, and the class is `CreateUsers{ms} implements MigrationInterface`. `-o` writes `.js` with `module.exports = class ...`.
- The file name is decorative. What TypeORM uses is the **class name**: its last 13 characters must parse as a timestamp or loading fails with "migration name is wrong". Keep `{ms}-{Name}.ts` / `{Name}{ms}` in sync anyway.
- NestJS projects configure the runtime through `TypeOrmModule.forRoot(...)` and keep a separate `data-source.ts` for the CLI: `npx typeorm-ts-node-commonjs migration:generate -d src/data-source.ts src/migrations/Name`.
- Kept files keep the file name **and** the class name **and** the `name` property (`1700000000000-CreateOrders.ts`, `CreateOrders1700000000000`): the class name is the tracking key. The plan's `class` is derived by convention (`{Name}{timestamp}` from the file name); when the original's class name (or `name` property) differs, because it was renamed by hand or the file name does not follow `{ms}-{Name}`, copy it from the original.
- New files (`deferred_foreign_keys`, `seed_reference_data`, `functions_and_triggers`, moved views, split-out tables) are `{ms}-{Name}.ts` after the last original, one second (1000 ms) apart: `1700300001000-DeferredForeignKeys.ts`, class `DeferredForeignKeys1700300001000`. A split-out table gets the next free second after its original, when that sorts before the next one.

## Replay order

- Sorted by the numeric timestamp taken from the class name, not by file name and not by any dependency list.
- Pending = classes whose name has no row in the tracking table. Out-of-order timestamps are accepted (the check is commented out in `MigrationExecutor`), so a split-out table slotted right after its original is not rejected where later classes already ran (the baseline gives it a row there). On a fresh database the order is the history's: kept classes at their timestamps, new ones after the last original.
- Two classes with the same name abort with `Duplicate migrations: ...`; every kept class has the same name as its archived original, so an archived copy still matched by the glob triggers this.

## Reading originals into ops

Most files are generated and call `queryRunner.query()` with dialect SQL; `migration:generate` prints one statement per call. Read `up` only, in order. Generated constraint names are `PK_`/`FK_`/`UQ_`/`IDX_`/`CHK_`/`REL_` plus a 27-character hash of table and column names: record them verbatim, because `migration:generate` compares names and will emit drop/add pairs for renamed constraints.

- `CREATE TABLE "users" ("id" SERIAL NOT NULL, "email" character varying NOT NULL, CONSTRAINT "UQ_97672ac88f789774dd47f7c8be3" UNIQUE ("email"), CONSTRAINT "PK_a3ffb1c0c8416b9fc6f907b7433" PRIMARY KEY ("id"))` -> `create_table` with column `id` `{type: "SERIAL", primary: true, auto_increment: true}`, `primary_key.name = "PK_a3ff..."`, index `{name: "UQ_9767...", columns: ["email"], unique: true}`.
- `CREATE TYPE "public"."users_role_enum" AS ENUM('admin', 'user')` then `"role" "public"."users_role_enum" NOT NULL DEFAULT 'user'` -> `create_type` plus a column with `type_ref: "type:users_role_enum"` (TypeORM names enums `{table}_{column}_enum`).
- `ALTER TABLE "posts" ADD CONSTRAINT "FK_c5a322ad..." FOREIGN KEY ("authorId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE NO ACTION` -> `add_foreign_key` with `on_delete: "cascade"`.
- `CREATE INDEX "IDX_2d82eb2b..." ON "posts" ("authorId")` -> `add_index`; `COMMENT ON COLUMN "users"."email" IS 'login'` -> `set_comment`.
- `ALTER TABLE "users" ALTER COLUMN "name" DROP NOT NULL` -> `alter_column {nullable: true}`.
- SQLite: `CREATE TABLE "temporary_users" (...)`, `INSERT INTO "temporary_users"(...) SELECT ... FROM "users"`, `DROP TABLE "users"`, `ALTER TABLE "temporary_users" RENAME TO "users"` is one redefinition of `users`: diff the new column list against the current table state and emit `add_column` / `alter_column` / `drop_column` ops, not a drop and create.
- `INSERT INTO "typeorm_metadata"("database", "schema", "table", "type", "name", "value") VALUES (...)` with `type = 'VIEW'` accompanies every `CREATE VIEW` from a `@ViewEntity`; `type = 'GENERATED_COLUMN'` accompanies generated columns. Keep the `CREATE VIEW` as `create_view` and note the metadata insert in `view.raw` so the writer reproduces it.
- Hand-written files may use the builder: `queryRunner.createTable(new Table({ name, columns: [{ name: 'id', type: 'int', isPrimary: true, isGenerated: true, generationStrategy: 'increment' }], foreignKeys: [{ columnNames, referencedTableName, referencedColumnNames, onDelete }] }))` -> the same `create_table`.

## Writing a consolidated table migration

`src/migrations/1700000000000-CreateOrders.ts`, the kept original that created `orders` (Postgres):

```ts
import { MigrationInterface, QueryRunner } from "typeorm";

// Consolidated: final orders (created here); folded: 1700100000000-AddOrderStatus (enum moved in),
// 1700200000000-AddOrderIndexes, 1700300000000-OrderCustomerFk
export class CreateOrders1700000000000 implements MigrationInterface {
    name = 'CreateOrders1700000000000'

    public async up(queryRunner: QueryRunner): Promise<void> {
        await queryRunner.query(`CREATE TYPE "public"."orders_status_enum" AS ENUM('draft', 'paid', 'cancelled')`);
        await queryRunner.query(`CREATE TABLE "orders" (
            "id" SERIAL NOT NULL,
            "customerId" integer NOT NULL,
            "number" character varying(32) NOT NULL,
            "status" "public"."orders_status_enum" NOT NULL DEFAULT 'draft',
            "amountCents" bigint NOT NULL DEFAULT 0,
            "note" text,
            "createdAt" TIMESTAMP NOT NULL DEFAULT now(),
            "updatedAt" TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT "UQ_8e1c3d2d8a1f6c6e9e7b1a2c3d4" UNIQUE ("number"),
            CONSTRAINT "CHK_3f2e1d0c9b8a7f6e5d4c3b2a1" CHECK ("amountCents" >= 0),
            CONSTRAINT "PK_710e2d4957aa5878dfe94e4ac2f" PRIMARY KEY ("id")
        )`);
        await queryRunner.query(`CREATE INDEX "IDX_1a2b3c4d5e6f7a8b9c0d1e2f3" ON "orders" ("customerId", "createdAt")`);
        await queryRunner.query(`COMMENT ON COLUMN "orders"."amountCents" IS 'Total in minor units'`);
        await queryRunner.query(`ALTER TABLE "orders" ADD CONSTRAINT "FK_124456e1b5e1f5c2d3e4f5a6b7c" FOREIGN KEY ("customerId") REFERENCES "customers"("id") ON DELETE CASCADE ON UPDATE NO ACTION`);
    }

    public async down(queryRunner: QueryRunner): Promise<void> {
        await queryRunner.query(`ALTER TABLE "orders" DROP CONSTRAINT "FK_124456e1b5e1f5c2d3e4f5a6b7c"`);
        await queryRunner.query(`DROP INDEX "public"."IDX_1a2b3c4d5e6f7a8b9c0d1e2f3"`);
        await queryRunner.query(`DROP TABLE "orders"`);
        await queryRunner.query(`DROP TYPE "public"."orders_status_enum"`);
    }
}
```

The explicit `name` property is what the tracking row stores; copy it unchanged from the original (add it equal to the class name if the original had none: that avoids surprises when minifiers rename classes). `orders_status_enum` was created by `AddOrderStatus` together with the column; the column now lives in this earlier file, so the enum moved in and `AddOrderStatus` disappears. The inline FK works because `customers` is created by an earlier class; otherwise it goes to `deferred_foreign_keys`. A renamed table keeps its creating class; name the former table in the header. For MySQL use backticks, `int NOT NULL AUTO_INCREMENT`, `PRIMARY KEY (\`id\`)` inside the table and `ENGINE=InnoDB` from `options`.

## Writing a view migration

In the kept class that created the view, or a new `{ms}-CreateCustomerSummaryView.ts` after the last original when even the original holding its final definition comes too early:

```ts
public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`CREATE VIEW "customer_summary" AS SELECT c.id, count(o.id) AS orders FROM customers c LEFT JOIN orders o ON o."customerId" = c.id GROUP BY c.id`);
    // only when the original did it (a @ViewEntity): otherwise migration:generate thinks the view is new
    await queryRunner.query(`INSERT INTO "typeorm_metadata"("database", "schema", "table", "type", "name", "value") VALUES (DEFAULT, $1, DEFAULT, $2, $3, $4)`, ["public", "VIEW", "customer_summary", "SELECT c.id, ..."]);
}
public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DELETE FROM "typeorm_metadata" WHERE "type" = $1 AND "name" = $2 AND "schema" = $3`, ["VIEW", "customer_summary", "public"]);
    await queryRunner.query(`DROP VIEW "customer_summary"`);
}
```

## The extra files

New classes, after the last original, only when the plan lists them:

- `functions_and_triggers`: only what no original can hold (it reads something created after every candidate); one `queryRunner.query()` per `CREATE OR REPLACE FUNCTION` / `CREATE TRIGGER` statement (`$$` bodies go inside a template literal untouched).
- `deferred_foreign_keys`: `ALTER TABLE "users" ADD CONSTRAINT "FK_..." FOREIGN KEY ("currentTeamId") REFERENCES "teams"("id") ON DELETE SET NULL`; down drops the constraint.
- `seed_reference_data`: `await queryRunner.query(\`INSERT INTO "roles" ("name") VALUES ('admin'), ('user')\`)` or `queryRunner.manager.insert(Role, [...])` when the original used the entity manager.

Prerequisites get no class of their own: `await queryRunner.query(\`CREATE EXTENSION IF NOT EXISTS "uuid-ossp"\`)`, `CREATE SCHEMA IF NOT EXISTS "billing"`, enum types and sequences stay in the class that created them, or move into an earlier kept class that needs them.

## Schema snapshot to cross-check

The `@Entity` classes are the source of truth; `migration:generate` diffs them against a live database, so there is no file to diff statically. Walk the entities instead: `@PrimaryGeneratedColumn()` -> `SERIAL` + `PK_`; `@Column({ type, length, nullable, default, unique, comment })`; `@CreateDateColumn` / `@UpdateDateColumn` -> `TIMESTAMP NOT NULL DEFAULT now()`; `@DeleteDateColumn` nullable; `@VersionColumn`; `@ManyToOne` + `@JoinColumn()` -> column `{relation}Id` and an `FK_`; `@Index`, `@Unique`, `@Check` -> `IDX_`, `UQ_`, `CHK_`. Column names default to the property name (camelCase) unless a `namingStrategy` such as `SnakeNamingStrategy` is set in the DataSource. Every entity column should be in the manifest and vice versa.

## After placing the files

- The archive directory must fall outside the `migrations` glob, and outside the `dist/` glob too: delete `dist/` and rebuild, or stale compiled originals get loaded next to the new files. Kept classes share their names with the archived ones, so either leak aborts with `Duplicate migrations`.
- NestJS: `nest build`, then `migration:show -d dist/data-source.js` should list only the consolidated classes (kept and new).
- The user's sanity check (needs a database): `npx typeorm migration:generate -d src/data-source.ts src/migrations/Check --check` exits 0 with `No changes in database schema were found`. Exit 1 prints the drift as a would-be migration; fix the consolidated file, never commit the generated one.

## Tracking table and reconciliation

- Table `migrations` (option `migrationsTableName`), columns `id` (auto-increment primary key), `timestamp` (bigint, the class timestamp), `name` (varchar, the class name such as `CreateOrders1700000000000`). No checksum.
- Rows without a file are ignored by `migration:run` and `migration:show`, so the tool is lenient, but `migration:revert` takes the newest row and fails with `No migration X was found in the source code` when that row is an orphan. Files without a row run.
- Kept classes already have their row; removed originals still have theirs; new classes have none. `baseline.py` writes `precheck.sql` (run first) and `baseline.sql` (Postgres syntax; MySQL uses backticks):

```sql
-- precheck.sql: must return no rows (any row = that database has not run every original; migrate it on the old history first)
SELECT m.v AS not_applied FROM (SELECT 'CreateOrders1700000000000' AS v UNION ALL SELECT 'AddOrderStatus1700100000000' AS v UNION ALL ...) m
WHERE NOT EXISTS (SELECT 1 FROM "migrations" t WHERE t."name" = m.v);
```
```sql
-- baseline.sql
DELETE FROM "migrations" WHERE "name" IN ('AddOrderStatus1700100000000', 'AddOrderIndexes1700200000000', 'OrderCustomerFk1700300000000');  -- removed originals
INSERT INTO "migrations" ("timestamp", "name") VALUES (1700300001000, 'DeferredForeignKeys1700300001000');  -- one per new class
```

Kept rows stay untouched. `baseline.py` derives the original class names by convention (`{Name}{timestamp}` from the file name); where an original's real class name differs, correct the precheck and the `DELETE` by hand.

## Quirks

- The class name carries the version; a file renamed without renaming the class keeps its old order and tracking name. A kept class renamed or renumbered becomes a new, unapplied migration.
- A database that ran `CreateOrders1700000000000` but not yet `AddOrderStatus1700100000000` sees the rewritten class as applied and never gets `status`; nothing fails. The precheck catches it.
- `migrationsRun: true` (common in NestJS configs) applies pending migrations at boot; baseline every environment before deploying the consolidated set.
- `migrationsTransactionMode` defaults to `all`: on Postgres the whole pending set runs in one transaction, so one bad consolidated file rolls back all of them; MySQL DDL auto-commits and leaves partial state.
- `synchronize: true` must stay off, otherwise TypeORM alters tables behind the migrations' back.
- `typeorm_metadata` rows for views and generated columns are part of the schema from `migration:generate`'s point of view; omit them and the next generate recreates the view.
- Generated SQL quotes identifiers and schema-qualifies enum types (`"public"."x_enum"`); keep that style so generate stays silent.
- SQLite migrations rebuild tables through `temporary_*` copies; MySQL ones carry `ENGINE=InnoDB` and sometimes charset noise from `migration:generate`.
