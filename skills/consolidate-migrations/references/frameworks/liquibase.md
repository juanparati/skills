# Liquibase (4.x)

## Layout and naming

- A **master changelog** (Spring Boot default `classpath:/db/changelog/db.changelog-master.yaml`, property `spring.liquibase.change-log`; CLI `--changelog-file`) that `include`s or `includeAll`s child changelogs. Formats: XML (`.xml`), YAML (`.yaml`), JSON, formatted SQL (`.sql` starting with `--liquibase formatted sql`). Keep the project's format for every file you write.
- Child files are usually `db/changelog/changes/<nnn>-<description>.<ext>` or `<version>/<description>.<ext>`; there is no mandated version format. A changeset is identified by `id` + `author` + file path, not by file name.
- Consolidated files: the unit kept is the **creating changeset**. The child changelog whose changeset created a surviving object keeps its path, and that changeset keeps its `id` and `author` (and the file its `logicalFilePath`, equal to the FILENAME recorded in `DATABASECHANGELOG`); it now holds the final definition (a table with every later column, index, check and FK folded in), and the file's other changesets (alters) disappear. Files whose changesets only altered or dropped things disappear. A file with several `createTable` changesets is a multi-table original (keep together or split).
- New files (split tables, moved views, `functions_and_triggers`, `deferred_foreign_keys`, `seed_reference_data`) are numbered after the highest existing prefix in the same style, e.g. `changes/017-deferred-foreign-keys.yaml`, each holding one changeset `id: consolidated-deferred-foreign-keys`, `author: consolidate-migrations`.

## Replay order

The master changelog's order: `include`s run in the order written; `includeAll path=...` runs every file in the directory alphabetically (`resourceComparator` can change it). Inside a file, changesets run top to bottom. Nothing is sorted by id or date. So transcribe originals by walking the master changelog recursively, not by listing the directory. Kept files keep their place; new files sort last by number, which is plan order, except a table split out of a multi-table original: the plan puts it right after its original, which only explicit `include`s can express.

## Reading originals into ops

Change types map almost one-to-one; the implicit parts:

- `createTable` with `column: {name: id, type: bigint, autoIncrement: true, constraints: {primaryKey: true, primaryKeyName: pk_orders, nullable: false}}` -> `create_table` with `auto_increment: true`, `primary_key: {name: "pk_orders", columns: ["id"]}`. A column `constraints: {unique: true, uniqueConstraintName: uq_orders_number}` -> Index `{unique: true}`; `constraints: {foreignKeyName: fk_orders_user, references: "users(id)", deleteCascade: true}` -> `add_foreign_key` with `on_delete: "cascade"` (the `references` string holds table and columns); `checkConstraint: "total >= 0"` -> `add_check` (the database names it).
- `addForeignKeyConstraint baseTableName/baseColumnNames/referencedTableName/referencedColumnNames/onDelete: CASCADE` -> `add_foreign_key`. `addUniqueConstraint` -> Index `unique: true`. `createIndex` with `unique: true` -> same.
- `addColumn` -> `add_column`; `modifyDataType` -> `alter_column {type}`; `addNotNullConstraint` / `dropNotNullConstraint` -> `alter_column {nullable}`; `addDefaultValue` -> `alter_column {default}`; `renameColumn` / `renameTable` -> the rename ops.
- `sql` / `sqlFile` / formatted-SQL changesets -> parse the SQL; `createView` with `selectQuery` -> `create_view`, `replaceIfExists` is the CREATE OR REPLACE case.
- `insert` / `loadData` (CSV) -> `data` op, `kind: "seed"`, the change as `code`; `loadData` references a file, keep the path in `code`.
- `changeSet` attributes that are not schema: `context`, `labels`, `dbms`, `runOnChange`, `runAlways`, `preConditions`. Note `dbms` on the table op when a changeset is dialect-specific; `runOnChange` changesets are usually views or procedures (treat like Flyway repeatables).
- Note the `id`, `author` and attributes of each creating changeset (and the file's `logicalFilePath`, if any): that changeset is what a kept file keeps.

## Writing a consolidated table migration

`changes/003-create-orders.yaml`, the kept original (YAML shown; the XML form is the same tree with `<changeSet>`, `<createTable>`, `<column>`, `<constraints>` elements):

```yaml
# Consolidated by consolidate-migrations: changeset create-orders now creates the final orders table.
# Folded: 003-create-orders.yaml (this file), 007-add-order-status.yaml, 009-order-total-check.yaml, 014-order-notes.yaml
databaseChangeLog:
  - logicalFilePath: db/changelog/changes/003-create-orders.yaml   # = FILENAME already in DATABASECHANGELOG
  - changeSet:
      id: create-orders          # the original's id and author, unchanged
      author: jsmith
      comment: Customer orders (consolidated)
      changes:
        - createTable:
            tableName: orders
            schemaName: billing
            remarks: Customer orders
            columns:
              - column: { name: id, type: bigint, autoIncrement: true,
                          constraints: { primaryKey: true, primaryKeyName: pk_orders, nullable: false } }
              - column: { name: user_id, type: bigint, constraints: { nullable: false } }
              - column: { name: number, type: varchar(32), constraints: { nullable: false } }
              - column: { name: status, type: varchar(16), defaultValue: draft, constraints: { nullable: false } }
              - column: { name: total, type: "decimal(12,2)", defaultValueNumeric: 0,
                          constraints: { nullable: false, checkConstraint: "total >= 0" } }
              - column: { name: notes, type: text }
              - column: { name: created_at, type: timestamp, defaultValueComputed: CURRENT_TIMESTAMP,
                          constraints: { nullable: false } }
        - addUniqueConstraint:
            tableName: orders
            schemaName: billing
            columnNames: number
            constraintName: uq_orders_number
        - createIndex:
            tableName: orders
            schemaName: billing
            indexName: ix_orders_user_id_status
            columns:
              - column: { name: user_id }
              - column: { name: status }
        - addForeignKeyConstraint:
            baseTableName: orders
            baseTableSchemaName: billing
            baseColumnNames: user_id
            referencedTableName: users
            referencedColumnNames: id
            constraintName: fk_orders_user_id
            onDelete: CASCADE
      rollback:
        - dropTable: { tableName: orders, schemaName: billing }
```

`createTable`, `createIndex`, `addForeignKeyConstraint` and `addUniqueConstraint` all auto-rollback, so the explicit `rollback` is optional here; `sql` changes need one. Use `defaultValue` (string), `defaultValueNumeric`, `defaultValueBoolean`, `defaultValueComputed` (raw expression) as the original did; quoting the type `"decimal(12,2)"` avoids YAML parsing the comma. Formatted SQL projects get the same content under the original header, `--changeset jsmith:create-orders`, followed by plain `CREATE TABLE ...;` and `--rollback DROP TABLE billing.orders;`. A kept multi-table file (not split) keeps each creating changeset, each with its final table, in FK order. The new content changes the changeset's MD5SUM; the baseline clears it.

## Writing a view migration

`createView` is the DSL; `fullDefinition: true` lets the SQL carry the whole `CREATE VIEW` header for dialect-specific options. A view stays in its creating changeset (id and author kept) when everything it reads exists by then; otherwise it is a new file after the last original, `changes/016-create-customer-summary-view.yaml`:

```yaml
databaseChangeLog:
  - logicalFilePath: db/changelog/changes/016-create-customer-summary-view.yaml
  - changeSet:
      id: consolidated-customer-summary-view
      author: consolidate-migrations
      changes:
        - createView:
            viewName: customer_summary
            replaceIfExists: true
            selectQuery: SELECT u.id AS user_id, COUNT(o.id) AS order_count FROM users u LEFT JOIN orders o ON o.user_id = u.id GROUP BY u.id
      rollback:
        - dropView: { viewName: customer_summary }
```

If the original used `runOnChange: true` on the view changeset (so edits redeploy it), keep that attribute. Raw SQL escape hatch for anything else: `- sql: { sql: "CREATE MATERIALIZED VIEW ...", splitStatements: false }` or `- sqlFile: { path: views/customer_summary.sql, relativeToChangelogFile: true }`.

## The extra files

New files, numbered after the last original (`014-` here) in plan order, one changeset each, `id: consolidated-<name>`, `author: consolidate-migrations`:

- `functions_and_triggers` (`015-functions-and-triggers.yaml`, only what could not stay in its creating changeset): `- createProcedure` / `- sql` with `splitStatements: false` and `endDelimiter: ""` (or `stripComments: false`) so `$$` bodies are sent whole; `rollback: - sql: DROP FUNCTION ...`.
- `deferred_foreign_keys` (`017-deferred-foreign-keys.yaml`): one `addForeignKeyConstraint` per FK (`onDelete: SET NULL`), auto-rollback drops them.
- `seed_reference_data` (`018-seed-reference-data.yaml`): `- insert: { tableName: countries, columns: [ { column: { name: id, valueNumeric: 1 } }, { column: { name: name, value: US } } ] }` per row, or `loadData` with a CSV next to the changelog; `rollback: - delete: { tableName: countries, where: "id IN (1,2)" }`.

Prerequisites get no file: `- sql: { dbms: postgresql, sql: "CREATE EXTENSION IF NOT EXISTS citext" }`, `- sql: CREATE SCHEMA IF NOT EXISTS billing` (there is no `createSchema` change), `- createSequence: { sequenceName: invoice_numbers, startValue: 1000 }`, enum types via `sql` stay in the changeset that created them, or move to the top of the creating changeset of an earlier kept file that needs them.

## Schema snapshot to cross-check

None generated. For Spring projects with JPA entities the same entity-vs-manifest check as Flyway applies (`@Table`, `@Column(nullable, length)`, `@JoinColumn`, implicit `@ManyToMany` join tables), as a note rather than an error. `liquibase diff-changelog` / `diff` against a live database is the user's own check afterwards.

## After placing the files

- Rewrite the master changelog with the kept and new files in plan order: drop the `include`s of removed files, add the new ones (a split-out table right after its original). `includeAll` matches plan order only when there is no split-out table. With `includeAll`, the archive must live outside the included path, e.g. `db/changelog-archive/`, because `includeAll` is recursive to `maxDepth`.
- Set `logicalFilePath` on each consolidated changelog root (`databaseChangeLog: - logicalFilePath: db/changelog/changes/003-create-orders.yaml` or the `<databaseChangeLog logicalFilePath="...">` attribute) so the FILENAME recorded in the database no longer depends on how the file is referenced (classpath vs relative vs `relativeToChangelogFile`). On a kept file it must equal the FILENAME already recorded for its changesets (`SELECT DISTINCT FILENAME FROM DATABASECHANGELOG`; an existing `logicalFilePath` stays verbatim), otherwise the kept changeset is a new identity and runs again. New files: their own path.
- Java projects: rebuild, changelogs are classpath resources. Nothing else to regenerate.
- User sanity check without touching data: `liquibase validate` (parses every changelog), `liquibase status --verbose` (lists what would run), `liquibase update-sql` (prints the SQL). Before the swap, `liquibase status --verbose` with the old changelog is the precheck: every existing database must report no changeset not applied.

## Tracking table and reconciliation

- Tables `DATABASECHANGELOG` and `DATABASECHANGELOGLOCK` (Spring `spring.liquibase.database-change-log-table` / `database-change-log-lock-table`; CLI `--database-changelog-table-name`). Columns of `DATABASECHANGELOG`: `ID VARCHAR(255)`, `AUTHOR VARCHAR(255)`, `FILENAME VARCHAR(255)`, `DATEEXECUTED DATETIME`, `ORDEREXECUTED INT`, `EXECTYPE VARCHAR(10)` (`EXECUTED`, `FAILED`, `SKIPPED`, `RERAN`, `MARK_RAN`), `MD5SUM VARCHAR(35)`, `DESCRIPTION VARCHAR(255)`, `COMMENTS VARCHAR(255)`, `TAG VARCHAR(255)`, `LIQUIBASE VARCHAR(20)`, `CONTEXTS VARCHAR(255)`, `LABELS VARCHAR(255)`, `DEPLOYMENT_ID VARCHAR(10)`. No primary key; identity is `(ID, AUTHOR, FILENAME)`.
- Checksum: `MD5SUM` = `9:` + MD5 hex of the concatenation of each change's own checksum (`change.generateCheckSum()`, a normalized serialization of the change's fields, not the file bytes) joined by `:`, plus SQL-visitor checksums joined by `;` (version 9 since 4.22; older rows upgrade on the next `update`/`status`). Whitespace inside SQL bodies is normalized. The exact normalization is Liquibase-internal; treat it as not reproducible outside Liquibase and let the tool write it.
- Applied row with no changeset in the changelog: **lenient**, `update` ignores it (`liquibase unexpected-changesets --verbose` lists them). A changeset in the changelog with no row runs on the next `update`. A row whose MD5SUM differs from the current changeset fails validation ("Validation Failed: ... changesets check sum") unless the changeset has `runOnChange`, a matching `validCheckSum`, or the MD5SUM is set to NULL (then it is recomputed and stored, not re-run).
- Baseline for an existing database, the tool's own commands (`baseline.sh`, run by the user after the master changelog is rewritten). Kept changesets keep their rows, but their MD5SUM no longer matches the new content; new changesets have no row:

```
# precheck, before the swap, with the old changelog:
liquibase status --verbose              # must report 0 changesets not applied
# baseline.sh:
liquibase clear-checksums               # MD5SUM set to NULL; recomputed and stored (not re-run) on the next update
liquibase changelog-sync                # marks every pending changeset (the new files) EXECUTED without running it
liquibase unexpected-changesets         # rows of removed changesets: harmless, ignored by update; delete by hand for a clean table
liquibase status --verbose
```

`liquibase changelog-sync-sql` prints the sync as SQL for review. When the CLI cannot reach the database, the same by hand: NULL the MD5SUM of the kept changesets and insert the new ones with `MD5SUM NULL`, which Liquibase fills on the next `update`:

```sql
UPDATE DATABASECHANGELOG SET MD5SUM = NULL WHERE FILENAME IN ('db/changelog/changes/003-create-orders.yaml', ...);
INSERT INTO DATABASECHANGELOG (ID, AUTHOR, FILENAME, DATEEXECUTED, ORDEREXECUTED, EXECTYPE, MD5SUM, DESCRIPTION, LIQUIBASE, DEPLOYMENT_ID)
VALUES ('consolidated-deferred-foreign-keys', 'consolidate-migrations', 'db/changelog/changes/017-deferred-foreign-keys.yaml', CURRENT_TIMESTAMP,
        (SELECT COALESCE(MAX(o.ORDEREXECUTED),0)+1 FROM DATABASECHANGELOG o), 'EXECUTED', NULL, 'addForeignKeyConstraint baseTableName=users, ...', '4.29.2', 'consolid1');
```

## Quirks

- Identity is `id/author/filename`; the FILENAME is the path *as referenced*, so the same file included as `classpath:db/changelog/x.yaml` and as `changes/x.yaml` are two different changesets. `logicalFilePath` pins it: on kept files equal to the FILENAME already recorded, on new files set before the first `changelog-sync`.
- `includeAll` is alphabetical and recursive: a `010-` file sorts before `2-`; zero-pad, and keep the archive out of the directory.
- Checksum mismatch stops `update` for the whole run, including unrelated changesets. Every kept changeset is in that state after the swap, hence `clear-checksums` in the baseline; after editing a consolidated file that has been synced, use `validCheckSum` or `clear-checksums`, never edit MD5SUM by hand except to NULL.
- YAML: quote types with commas (`"decimal(12,2)"`) and default values that look like booleans or numbers; `defaultValue: draft` is fine, `defaultValue: 0` becomes a number (use `defaultValueNumeric`).
- Formatted SQL: the header must be exactly `--liquibase formatted sql` with no leading space; each `--changeset author:id` needs its own `--rollback` line or rollback is impossible for that changeset.
- Spring Boot defaults (verified in `LiquibaseProperties`): `spring.liquibase.enabled=true`, `change-log=classpath:/db/changelog/db.changelog-master.yaml`, `database-change-log-table=DATABASECHANGELOG`, `database-change-log-lock-table=DATABASECHANGELOGLOCK`, `drop-first=false`; `contexts`, `default-schema` and `liquibase-schema` unset.
- Liquibase runs at application start in Spring Boot; a stale `DATABASECHANGELOGLOCK` row (`LOCKED = true` after a crash) blocks every start until `liquibase release-locks`.
