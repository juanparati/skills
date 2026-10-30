# Ops and manifest formats

Two JSON formats carry the schema through the workflow:

- **Ops files** (`.consolidate-migrations/ops/NNN_<original>.json`): one per original migration, written by you while reading it. Each is a *local* translation of that one file into normalized operations. They are order-independent to write, so several subagents can transcribe chunks in parallel.
- **The manifest** (`.consolidate-migrations/manifest.json`): the final schema, produced by `scripts/replay.py` folding the ops in migration order, plus which original created each object (the consolidated files keep those originals' names). `plan_order.py`, `check_output.py` and the writing step read only this file, so anything missing here is missing from the result.

Both use the same object shapes for tables, columns, indexes and so on. Field names are fixed; values for `type`, `default` and `raw` stay in the **framework's own vocabulary** (a Laravel column has `"type": "string"`, a Postgres SQL column has `"type": "varchar(255)"`), because the writing step must reproduce them in the same framework.

Every field is optional except where marked **(required)**. Omit fields you have no value for rather than inventing one; `null` and absent mean the same thing.

## Object shapes

### Table

```jsonc
{
  "name": "invoices",                     // (required) final table name, without schema
  "schema": "billing",                    // Postgres/MSSQL schema; null = default schema
  "app": null,                            // Django app label, Ecto repo, EF context: when output is split by directory
  "model": "Invoice",                     // Django/EF/Rails/TypeORM class name when the framework names migrations after it
  "comment": null,
  "options": { "engine": "InnoDB", "charset": "utf8mb4", "collation": null },   // only what the originals set
  "columns": [ /* Column */ ],            // (required) in the order they should appear in the file
  "primary_key": { "name": null, "columns": ["id"] },   // null when a column has "primary": true
  "indexes": [ /* Index */ ],
  "checks": [ /* Check */ ],
  "foreign_keys": [ /* ForeignKey */ ],
  "triggers": [ /* Trigger */ ],
  "depends_on": ["extension:citext"],     // extra dependencies not implied by FKs or type_ref
  "implicit_m2m_of": null,                // owning table ("blog_post", or Django "blog.Post.tags"): a join table created by the owner's migration
  "polymorphic": ["commentable"],         // column prefixes of polymorphic relations (no FK, nothing to order)
  "renamed_from": [],                     // filled by replay.py
  "folded_from": [],                      // filled by replay.py: every original file that touched this table
  "created_in": "..."                     // filled by replay.py: the original that created it (its file keeps that name)
}
```

### Column

```jsonc
{
  "name": "customer_id",                  // (required) column name in the database
  "attr": "customer",                     // model attribute when it differs (Django `author` -> column `author_id`)
  "type": "foreignId",                    // (required) framework vocabulary: "string", "bigIncrements", "varchar(255)", "models.CharField(max_length=80)"
  "length": 255, "precision": 12, "scale": 2,   // only when the type takes them and they are not in `type`
  "nullable": false,
  "default": "'draft'",                   // as written in the source; raw expressions as text, e.g. "now()"
  "primary": true,
  "auto_increment": true,
  "unsigned": true,
  "unique": false,                        // inline unique; prefer an Index with unique:true when the original named it
  "comment": null,
  "type_ref": "type:invoice_status",      // when the column uses a standalone type (enum, domain)
  "enum_values": ["draft", "published"],  // inline enums (MySQL ENUM, Laravel enum())
  "generated": null,                      // generated/computed expression
  "raw": null                             // verbatim DDL when the DSL cannot express the column
}
```

### Index

```jsonc
{ "name": "invoices_customer_id_idx", "columns": ["customer_id"], "unique": false,
  "method": "btree", "where": null, "raw": null }
```

Composite uniques are indexes with `"unique": true`. A `primary` index belongs in `primary_key`, not here.

### Check

```jsonc
{ "name": "invoices_amount_nonnegative", "expression": "amount_cents >= 0" }
```

### ForeignKey

```jsonc
{ "name": "invoices_customer_id_fkey",   // null when the original let the database name it
  "columns": ["customer_id"], "ref_table": "customers", "ref_schema": null, "ref_columns": ["id"],
  "on_delete": "cascade", "on_update": null, "deferrable": false,
  "external": false }                      // true when the target lives outside the consolidated scope (django.contrib.auth tables, another service's schema)
```

`ref_table` is the table name **as it is at the time of the op**; `replay.py` follows later renames. An `external` foreign key is written like any other but creates no ordering edge and is not checked for existence; the report lists them.

Objects may carry extra keys the scripts do not know (a Django join table's `m2m_field` expression, a Rails `t.references` option). They are passed through untouched for the writing step.

### Trigger

```jsonc
{ "name": "invoices_set_updated_at", "raw": "CREATE TRIGGER ... EXECUTE FUNCTION set_updated_at()",
  "functions": ["set_updated_at"] }
```

`replay.py` adds `created_in` (the original that created the trigger) and `defined_in` (the one holding its final definition); views and functions get the same two fields, types and sequences get `created_in`.

### View

```jsonc
{ "name": "customer_summary", "schema": null, "materialized": false,
  "definition": "SELECT ...",             // the SELECT body only
  "depends_on": ["table:customers", "table:billing.invoices", "view:other"],  // (required) everything the SELECT reads
  "indexes": [],                          // materialized views only
  "raw": null }                           // verbatim statement when the framework needs it
```

### Type, extension, sequence, function, schema

```jsonc
{ "name": "invoice_status", "schema": null, "kind": "enum", "values": ["draft", "paid", "void"], "raw": null }   // type: kind enum | domain | composite
"citext"                                                                                                          // extension: a string
{ "name": "invoice_number_seq", "raw": "CREATE SEQUENCE invoice_number_seq START 1000" }                           // sequence
{ "name": "set_updated_at", "raw": "CREATE OR REPLACE FUNCTION ...", "depends_on": [] }                           // function
"billing"                                                                                                         // schema: a string
```

### Dependency references

Typed strings, used in every `depends_on`: `table:name`, `table:schema.name`, `view:name`, `type:name`, `function:name`, `extension:name`, `sequence:name`, `schema:name`. Foreign keys and `type_ref` columns create their edges automatically; use `depends_on` only for what the scripts cannot infer (a default calling a function, a column of a domain type, a view reading a table).

## Ops file

```jsonc
{
  "file": "2023_03_05_100100_add_current_team_id_to_users_table.php",   // (required) the original, as listed in stack.json
  "split": null,                          // only for an original that creates several tables/views: the user's answer,
                                          // true = one file per table, false = keep them in this file
  "ops": [ /* in the order the file performs them */ ]
}
```

An empty `ops` list is valid (a no-op file, or a file whose only content was data you recorded with a `data` op). `split` is ignored for originals that create fewer than two surviving tables or views; one left out where it matters is treated as `false` and the plan warns. Each op is an object with an `"op"` key:

| op | payload | notes |
| --- | --- | --- |
| `create_table` | `table` (Table) | full table as created by this file |
| `drop_table` | `table`, `schema` | later ops on it are errors |
| `rename_table` | `table`, `to`, `schema` | state moves to the new name; FKs pointing at it are rewritten |
| `add_column` | `table`, `column` (Column), `after` | |
| `drop_column` | `table`, `column` | indexes/FKs that used it are dropped with a warning |
| `rename_column` | `table`, `column`, `to` | indexes/FKs/checks are rewritten |
| `alter_column` | `table`, `column`, `changes` (partial Column) | only the keys present change; `"nullable": true` to drop NOT NULL, `"default": null` clears a default only if you also pass `"clear_default": true` |
| `add_index` / `drop_index` | `table`, `index` (Index) / `name` or `columns` | |
| `add_foreign_key` / `drop_foreign_key` | `table`, `foreign_key` (ForeignKey) / `name` or `columns` | |
| `add_check` / `drop_check` | `table`, `check` (Check) / `name` | |
| `set_primary_key` | `table`, `primary_key` (or `null`) | |
| `set_comment` | `table`, `column` (optional), `text` | |
| `set_options` | `table`, `options` | merged |
| `set_table_meta` | `table`, `app`, `model`, `polymorphic`, `implicit_m2m_of`, `depends_on` | any subset |
| `create_view` | `view` (View) | creating an existing name replaces it (`CREATE OR REPLACE`) |
| `drop_view` | `name`, `schema` | |
| `create_type` / `drop_type` | `type` (Type) / `name` | |
| `alter_type` | `name`, `add_value` or `rename_value: {from, to}` or `rename: to` | |
| `create_extension` / `drop_extension` | `name` | |
| `create_sequence` / `drop_sequence` | `sequence` / `name` | |
| `create_function` / `drop_function` | `function` / `name` | create replaces an existing one |
| `create_trigger` / `drop_trigger` | `table`, `trigger` (Trigger) / `table`, `name` | |
| `create_schema` / `drop_schema` | `name` | |
| `data` | `kind` (`seed` / `backfill` / `fix`), `tables`, `summary`, `code`, `decision` | `code` is the statement(s) in the framework's own form; see `transcribing.md` for the kinds. Put the user's answer in `decision` (`keep` / `drop`) here, so a replay re-run keeps it |
| `raw` | `sql` or `code`, `note` | last resort; lands in `manifest.unresolved` and in the report |

Table references in ops (`table`, `ref_table`) use the name current at that point of the history. Schema-qualified names can be written as `"billing.invoices"` or with a separate `schema` key.

## Manifest

```jsonc
{
  "version": 1,
  "framework": "laravel",                 // (required) key from scripts/schemalib.py FRAMEWORKS
  "dialect": "mysql",                     // (required) postgres | mysql | sqlite | mssql
  "source": { "dirs": ["database/migrations"], "files": 14,
              "first_version": "2023_01_10_100000", "last_version": "2023_11_20_100000", "ops_dir": ".consolidate-migrations/ops",
              "originals": [                // every original in replay order, filled by replay.py
                { "file": "2023_01_10_100000_create_users_table.php", "path": "database/migrations/2023_01_10_100000_create_users_table.php",
                  "dir": "database/migrations", "version": "2023_01_10_100000",
                  "creates": ["table:users"],   // surviving objects it created, in creation order (typed references, triggers as trigger:<table>.<name>)
                  "split": null }
              ] },
  "schemas": ["billing"],
  "extensions": ["citext"],
  "types": [ /* Type */ ],
  "sequences": [ /* Sequence */ ],
  "functions": [ /* Function */ ],
  "tables": [ /* Table, with renamed_from / folded_from filled */ ],
  "views": [ /* View */ ],
  "data_migrations": [
    { "file": "2023_09_01_100000_backfill_post_status_and_seed_tags.php", "kind": "seed",
      "tables": ["tags"], "summary": "inserts the tags php, laravel, mysql", "code": "DB::table('tags')->insert([...])",
      "decision": "keep" }                // keep | drop, set from the user's answer
  ],
  "unresolved": [ { "file": "...", "note": "...", "raw": "..." } ],
  "warnings": [ "..." ]                   // what replay.py noticed (implicit drops, overwritten views)
}
```

`source.first_version` / `last_version` are the version prefixes of the first and last original. `source.originals[].creates` is what `plan_order.py` uses to give each object a file: an original with an empty `creates` disappears, the others keep their names. New files (deferred FKs, seeds, split tables) are numbered right after `last_version`, so they sort after the history and before any migration generated later.

## Worked example

`2023_03_05_100100_add_current_team_id_to_users_table.php` contains
`$table->foreignId('current_team_id')->nullable()->constrained('teams')->nullOnDelete();`

```json
{
  "file": "2023_03_05_100100_add_current_team_id_to_users_table.php",
  "ops": [
    { "op": "add_column", "table": "users",
      "column": { "name": "current_team_id", "type": "foreignId", "nullable": true } },
    { "op": "add_foreign_key", "table": "users",
      "foreign_key": { "name": "users_current_team_id_foreign", "columns": ["current_team_id"],
                       "ref_table": "teams", "ref_columns": ["id"], "on_delete": "set null" } }
  ]
}
```

Note the expansion: `constrained()` means a foreign key, so it becomes its own op. `transcribing.md` lists these implicit expansions per framework.
