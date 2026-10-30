# Sequelize (6) with sequelize-cli

## Layout and naming

- `.sequelizerc` at the project root sets `config` (default `config/config.json` or a `.js` exporting per-environment objects), `migrations-path` (default `migrations`), `seeders-path`, `models-path`, `env`, `url`.
- `npx sequelize-cli migration:generate --name create-users` writes `{YYYYMMDDHHMMSS}-create-users.js` (14-digit UTC timestamp, hyphen separator). Files matching `.js`, `.cjs`, `.ts`, `.cts` (not `.d.ts`) are loaded.
- Module shape: `'use strict'; /** @type {import('sequelize-cli').Migration} */ module.exports = { async up(queryInterface, Sequelize) {}, async down(queryInterface, Sequelize) {} };` (`Sequelize` is the library, giving `Sequelize.DataTypes`, `Sequelize.literal`, `Sequelize.Op`).

## Replay order

- Umzug v2 under the hood: all files in `migrations-path`, sorted by file name; pending = files whose name is not in the tracking table. No dependency metadata.
- `db:migrate` runs pending in name order, `db:migrate:status` prints `up` / `down` per file, `db:migrate:undo` reverts the most recently executed row (needs its file), `db:migrate:undo:all`, `db:migrate --to <name>`.
- Nothing wraps a migration in a transaction unless the file does it itself.
- Kept files keep the original's file name, so they sort where the original did and an existing database already has their row. New files (`deferred-foreign-keys`, `seed-reference-data`, `functions-and-triggers`, moved views, split tables without a free second) are numbered after the last original: `20231120100000-…` → `20231120100001-deferred-foreign-keys.js`.

## Reading originals into ops

Table and column names are whatever the strings say; Sequelize's conventions (PascalCase plural tables, camelCase columns, `createdAt` / `updatedAt`) are common but nothing is implicit in a migration.

- `queryInterface.createTable('Users', { id: { allowNull: false, autoIncrement: true, primaryKey: true, type: Sequelize.INTEGER }, email: { type: Sequelize.STRING, allowNull: false, unique: true }, createdAt: { allowNull: false, type: Sequelize.DATE } })` -> `create_table`; `STRING` is `varchar(255)`, `DATE` is `timestamptz` (Postgres) / `datetime` (MySQL), `BOOLEAN` is `tinyint(1)` on MySQL; `unique: true` -> inline `unique`.
- `status: { type: Sequelize.ENUM('draft', 'paid') }` -> Postgres `create_type` named `enum_{table}_{column}` with `type_ref`; MySQL `enum_values`.
- `customerId: { type: Sequelize.INTEGER, references: { model: 'Customers', key: 'id' }, onDelete: 'CASCADE', onUpdate: 'CASCADE' }` -> column plus `add_foreign_key {name: null}` (database-named); `references.model` may be `{ tableName, schema }`.
- `queryInterface.addConstraint('Orders', { fields: ['customerId'], type: 'foreign key', name: 'orders_customer_fk', references: { table: 'Customers', field: 'id' }, onDelete: 'cascade' })` -> named `add_foreign_key`; `type: 'unique'` -> `add_index {unique: true}`; `type: 'check', where: { amountCents: { [Sequelize.Op.gte]: 0 } }` -> `add_check` with expression `"amountCents" >= 0`; `type: 'primary key'` -> `set_primary_key`.
- `queryInterface.addIndex('Orders', ['customerId', 'createdAt'], { name: 'orders_customer_created', unique: false })` -> `add_index` (without `name` Sequelize derives `{table}_{fields joined by _}`; exact casing unverified, record what `addIndex` was given).
- `addColumn` / `changeColumn('Orders', 'note', { type: Sequelize.TEXT, allowNull: true })` / `renameColumn` / `removeColumn` / `renameTable` / `dropTable` -> the matching ops; `queryInterface.sequelize.query('CREATE VIEW ...')` -> `create_view`; `queryInterface.bulkInsert('Roles', [...])` -> `data`.
- `defaultValue: Sequelize.NOW`, `Sequelize.UUIDV4`, `Sequelize.UUIDV1` are client-side defaults that the query generator skips when creating the table: record no `default` for them (and warn). `Sequelize.literal('CURRENT_TIMESTAMP')` / `Sequelize.fn('now')` are real defaults.

## Writing a consolidated table migration

`migrations/20230110100000-create-orders.js` (the original's name, final content):

```js
'use strict';

// 20230110100000-create-orders.js: consolidated by consolidate-migrations on 2024-11-05.
// Final definition of "Orders". Folded in: 20230305100100-add-order-status,
// 20230601100000-add-order-indexes, 20231120100000-order-customer-fk.

/** @type {import('sequelize-cli').Migration} */
module.exports = {
  async up(queryInterface, Sequelize) {
    await queryInterface.sequelize.transaction(async (transaction) => {
      await queryInterface.createTable('Orders', {
        id: { type: Sequelize.INTEGER, autoIncrement: true, primaryKey: true, allowNull: false },
        customerId: {
          type: Sequelize.INTEGER, allowNull: false,
          references: { model: 'Customers', key: 'id' }, onDelete: 'CASCADE', onUpdate: 'CASCADE',
        },
        number: { type: Sequelize.STRING(32), allowNull: false, unique: true },
        status: { type: Sequelize.ENUM('draft', 'paid', 'cancelled'), allowNull: false, defaultValue: 'draft' },
        amountCents: { type: Sequelize.BIGINT, allowNull: false, defaultValue: 0, comment: 'Total in minor units' },
        note: { type: Sequelize.TEXT, allowNull: true },
        createdAt: { type: Sequelize.DATE, allowNull: false, defaultValue: Sequelize.literal('CURRENT_TIMESTAMP') },
        updatedAt: { type: Sequelize.DATE, allowNull: false, defaultValue: Sequelize.literal('CURRENT_TIMESTAMP') },
      }, { comment: 'Customer orders', transaction });

      await queryInterface.addIndex('Orders', ['customerId', 'createdAt'], { name: 'orders_customer_created', transaction });
      await queryInterface.addConstraint('Orders', {
        type: 'check', name: 'orders_amount_cents_check',
        fields: ['amountCents'], where: { amountCents: { [Sequelize.Op.gte]: 0 } }, transaction,
      });
    });
  },

  async down(queryInterface, Sequelize) {
    await queryInterface.sequelize.transaction(async (transaction) => {
      await queryInterface.dropTable('Orders', { transaction });
      await queryInterface.sequelize.query('DROP TYPE IF EXISTS "enum_Orders_status";', { transaction });  // Postgres
    });
  },
};
```

The transaction is optional but cheap on Postgres; the CLI gives none. Named foreign keys go through `addConstraint` instead of `references`. A renamed table keeps the file of the original that created it (`…-create-bills.js` now creates `"Invoices"`); the header says `Renamed from "Bills"`. An original that created several tables holds all of them, one `createTable` each in plan order, unless the user chose to split it; `down` drops them in reverse.

## Writing a view migration

The escape hatch is `queryInterface.sequelize.query`:

```js
module.exports = {
  async up(queryInterface) {
    await queryInterface.sequelize.query(`CREATE VIEW "CustomerSummary" AS SELECT c."id", count(o."id") AS "orders" FROM "Customers" c LEFT JOIN "Orders" o ON o."customerId" = c."id" GROUP BY c."id";`);
  },
  async down(queryInterface) {
    await queryInterface.sequelize.query('DROP VIEW IF EXISTS "CustomerSummary";');
  },
};
```

## The extra files

New files only for what no kept file can hold, numbered after the last original.

- Prerequisites have no file of their own: `queryInterface.sequelize.query('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')`, `queryInterface.createSchema('billing')`, shared enum types via raw `CREATE TYPE` go first in `up` of the kept file that created them, or of the earlier kept file the plan pulled them into (`moved_in`).
- Functions and triggers: raw `CREATE OR REPLACE FUNCTION` / `CREATE TRIGGER` through `sequelize.query`, at the end of their kept file; `functions-and-triggers` only for those reading something created after every candidate original.
- `deferred_foreign_keys`: `queryInterface.addConstraint('Users', { type: 'foreign key', name: 'users_current_team_fk', fields: ['currentTeamId'], references: { table: 'Teams', field: 'id' }, onDelete: 'set null' })`; down `removeConstraint('Users', 'users_current_team_fk')`.
- `seed_reference_data`: `queryInterface.bulkInsert('Roles', [{ name: 'admin' }, { name: 'user' }])`; down `bulkDelete('Roles', { name: ['admin', 'user'] })`.

## Schema snapshot to cross-check

None that is authoritative. Models in `models/` (`Model.init` / `sequelize.define`) describe the same columns and are worth a read: `timestamps: true` (default) implies `createdAt` / `updatedAt`, `underscored: true` turns them into `created_at` / `updated_at`, `paranoid: true` adds `deletedAt`, `tableName` / `field` override names. A column in a model but not in the manifest is a bug in one of the two; `sync()` is not used by migration-based projects, so the migrations win.

## After placing the files

- Archive outside `migrations-path` (every loadable file there is a migration).
- TypeScript projects: rebuild from a clean output directory so it holds only the consolidated files (a stale compiled copy of a removed original has no row after the baseline and would run again), and keep the `.js` names in the baseline.
- `npx sequelize-cli db:migrate:status` should list only the consolidated files, all `up` after the baseline.

## Tracking table and reconciliation

- `SequelizeMeta` with one column `name` (`varchar(255)`, primary key, unique, not null). Table name via `migrationStorageTableName`, schema via `migrationStorageTableSchema` in the config; `migrationStorage: 'json'` writes `sequelize-meta.json` instead. Older projects may have a three-column variant with `createdAt` / `updatedAt`.
- `name` is the file basename **with** extension (`20231120100000-order-customer-fk.js`).
- Lenient for `db:migrate` (pending = files minus executed), but `db:migrate:undo` picks the newest row and fails when its file is gone: breaks-rollback-only. No checksum.
- Precheck (`precheck.sql`, must return no rows): every original this database has not run. A database that is behind would take the kept files as applied and miss their folded changes.

```sql
SELECT m.v AS not_applied FROM (SELECT '20230110100000-create-orders.js' AS v UNION ALL SELECT '…every original…' AS v) m
WHERE NOT EXISTS (SELECT 1 FROM "SequelizeMeta" t WHERE t.name = m.v);
```

- Baseline an existing database (Postgres quoting; MySQL uses backticks). Kept files keep their rows; removed originals lose theirs (a row without a file breaks `db:migrate:undo` when it reaches it); new files get one:

```sql
DELETE FROM "SequelizeMeta" WHERE name IN ('20230305100100-add-order-status.js', ...);  -- every removed original
INSERT INTO "SequelizeMeta" (name) VALUES ('20231120100001-deferred-foreign-keys.js');
INSERT INTO "SequelizeMeta" (name) VALUES ('20231120100002-seed-reference-data.js');
```

## Quirks

- Mixed-case identifiers (`"Orders"`, `"customerId"`, `"SequelizeMeta"`) need quotes on Postgres; unquoted SQL in raw statements folds to lower case and misses.
- `Sequelize.NOW` / `UUIDV4` defaults never reach the database from a migration; write `Sequelize.literal(...)` for real defaults.
- No automatic transaction: a consolidated file that fails halfway leaves a partial table on every dialect.
- `dropTable` on Postgres leaves the `enum_{table}_{column}` type behind; the `down` has to drop it.
- `references` inside `createTable` creates inline, database-named foreign keys; names from `addConstraint` are the only ones you control.
- The stored name includes the extension, so `.ts` in development and compiled `.js` in production record different names; pick one per environment and baseline accordingly.
- `migration:generate` writes a skeleton only; nothing is derived from models.
