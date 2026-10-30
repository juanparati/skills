# Django (5.x)

## Layout and naming

- One directory per app: `<app>/migrations/` with an `__init__.py`. The manifest's `table.app` is the app label; every table of an app is written into that app's directory, never elsewhere.
- File: `NNNN_<snake_name>.py`, four-digit sequence then a name that is a valid Python identifier. Class is always `Migration(migrations.Migration)`; the file name is the migration's identity (`('billing', '0003_invoice')`).
- Kept files reuse the original's file name **on purpose**: Django considers a file applied wherever a row `(app, name)` exists, so existing databases take the rewritten `0003_invoice.py` as already run. That is exactly why every database must have run every original first (see the precheck below): a database that never ran `0004_invoice_status` would never get the folded `status` field.
- New files (split tables, `deferred_foreign_keys`, `seed_reference_data`, `functions_and_triggers`, moved views) are numbered per app after that app's last original (last original `0017_invoice_meta` → `0018_payment.py`, `0019_deferred_foreign_keys.py`). `plan_order.py` names a split table file after the model (`0018_payment.py`) when the manifest table carries `model`, otherwise after the table; keep the plan's name.
- Tables are `<app>_<model>` by default; the manifest keeps `options.db_table` only when the originals set it.

## Replay order

Django orders by the `dependencies` graph, not by number; numbers are a convention for humans and for `makemigrations`. Each file's `dependencies` are the plan entry's `depends_on_files`: the previous file of the same app in plan order, kept or new (so the app stays linear), plus the files of other apps that hold what it reads. Add the dependencies on apps outside the consolidation (`auth`, third-party) that the folded operations need, and `swappable_dependency` where the originals had it. Drop every dependency on an original that disappeared: a dependency with no file behind it is a `NodeNotFoundError` at load time. A kept file may depend on a higher-numbered new file (a model split out of an earlier original into `0018_payment.py`); that is fine, the graph decides the order, not the number. Transcribe the originals in the order `showmigrations --plan` would list them (the ops dir is per file, so number order per app is enough when the ops carry table names).

## Reading originals into ops

Migration files are explicit about fields, so there is little hidden; what bites is naming and the join tables.

| Code | Ops |
| --- | --- |
| `migrations.CreateModel(name='Invoice', fields=[('id', models.BigAutoField(primary_key=True, ...)), ('author', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='accounts.customer')), ...])` | `create_table` with `name: "billing_invoice"`, `model: "Invoice"`, `app: "billing"`; Column `{name: "author_id", attr: "author", type: "models.ForeignKey(...)"}` plus ForeignKey `{name: null, columns: ["author_id"], ref_table: "accounts_customer", ref_columns: ["id"], on_delete: "cascade"}`. Keep the field expression verbatim in `type`; the writer copies it back. `db_column=` overrides the `_id` name. |
| `('tags', models.ManyToManyField(to='blog.tag'))` inside `CreateModel('Post')` | a second `create_table` for `blog_post_tags` with `id`, `post_id`, `tag_id`, a unique index on both and two FKs, and `implicit_m2m_of: "blog.Post.tags"`. No file is written for it later: the owning model's `CreateModel` creates it. `through=` makes it an ordinary model instead. |
| `to=settings.AUTH_USER_MODEL` plus `migrations.swappable_dependency(settings.AUTH_USER_MODEL)` | FK to the user table (resolve the concrete model from `settings.AUTH_USER_MODEL`, default `auth_user`); add `"table:<user table>"` to the table's `depends_on` so ordering is right, and remember to emit `swappable_dependency` when writing. |
| `options={'swappable': 'AUTH_USER_MODEL', 'db_table': 'users'}` on an `AbstractUser` model | `options` on the table; its fields are listed explicitly in the migration, and the `groups` / `user_permissions` M2M create `<app>_<model>_groups` and `<app>_<model>_user_permissions` join tables whose FKs to `auth_group` / `auth_permission` carry `"external": true` (those tables are outside the scope). Keep the `ManyToManyField(...)` expression on the join table as `m2m_field` so the writer can copy it back. |
| `migrations.AddIndex(model_name='invoice', index=models.Index(fields=['customer', 'status'], name='billing_inv_cust_status_idx'))` | `add_index` with the given name and `_id`-suffixed column names |
| `migrations.AddConstraint(model_name='invoice', constraint=models.CheckConstraint(condition=models.Q(amount_cents__gte=0), name='invoice_amount_nonnegative'))` | `add_check` with the `Q` kept as text in `expression`; `UniqueConstraint(fields=...)` is an `add_index` with `unique: true`; a `UniqueConstraint` with `condition=` or `expressions` keeps its definition in `raw`. Django 5.1 renamed `check=` to `condition=`. |
| `migrations.RunSQL("CREATE VIEW ...", "DROP VIEW ...")` | `create_view` (or `raw` for anything else); `RunPython` is a `data` op (`kind` by what it does) or `raw` |

`AlterField` / `RenameField` / `RenameModel` / `AlterModelTable` / `AlterUniqueTogether` are `alter_column` / `rename_column` / `rename_table` / `rename_table` / unique `add_index`+`drop_index`; `AlterModelOptions` is not schema. Index and FK names Django generates itself (`billing_invoice_customer_id_8f2a1c_idx`) are not in migration files; leave `name: null`.

## Writing a consolidated table migration

A kept file holds the `CreateModel`s its original created, each with its final fields, plus each model's own `AddIndex`/`AddConstraint` operations: all of them when kept together, only the model the file name refers to (for `0001_initial`, the first model created) when the user chose to split (the others get new files). It keeps the original's `initial` flag and has no `replaces`: a file cannot replace itself, and its name already has a row wherever the original ran.

`billing/migrations/0003_invoice.py` (the original's name, final content):

```python
# 0003_invoice.py: consolidated by consolidate-migrations on 2025-10-29.
# Final definition of Invoice (billing.invoices). Folded in: 0004_invoice_status,
# 0009_invoice_number_unique, 0012_invoice_check, 0017_invoice_meta.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),       # previous file of this app; was 0002_customer_email, which disappeared
        ("catalog", "0001_initial"),       # other app's file holding catalog.Tag
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Invoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.CharField(max_length=32, unique=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("paid", "Paid")], default="draft", max_length=20)),
                ("amount_cents", models.PositiveBigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="invoices", to="billing.customer")),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("tags", models.ManyToManyField(blank=True, to="catalog.tag")),   # creates billing_invoice_tags
            ],
            options={"db_table": "invoices", "db_table_comment": "One row per issued invoice"},
        ),
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(fields=["customer", "status"], name="invoice_customer_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="invoice",
            constraint=models.CheckConstraint(condition=models.Q(("amount_cents__gte", 0)), name="invoice_amount_nonnegative"),
        ),
    ]
```

There is no `down`: Django reverses `CreateModel`, `AddIndex` and `AddConstraint` itself. Column comments are `db_comment=` on the field (Django 4.2+), table comments `db_table_comment`.

A renamed model keeps the file of the original that created it (`0003_bill.py` now creating `Invoice`); the header says `Renamed from Bill`. An implicit M2M (`tags` above) is written in the file that created its join table: inside the owner's `CreateModel` when that is the owner's file, otherwise as `migrations.AddField(model_name="invoice", name="tags", field=models.ManyToManyField(...))` in the kept file that added the field.

## Writing a view migration

```python
class Migration(migrations.Migration):
    dependencies = [("billing", "0007_refund"), ("crm", "0004_account")]   # depends_on_files: previous file of the app + other apps' files the SELECT reads

    operations = [
        migrations.RunSQL(
            sql="""CREATE VIEW customer_summary AS
                   SELECT c.id AS customer_id, c.name, COUNT(i.id) AS invoice_count
                   FROM customers c LEFT JOIN invoices i ON i.customer_id = c.id GROUP BY c.id, c.name""",
            reverse_sql="DROP VIEW IF EXISTS customer_summary",
        ),
    ]
```

The view lives in the kept file of the original that created it when everything it reads exists by then; else in the file of its final definition, or a new view file after the app's last original. `RunSQL(sql, reverse_sql)` is the escape hatch; `migrations.RunSQL.noop` for a side that has nothing to do. A model with `managed = False` over the view, if the project has one, stays in `models.py` and needs no operation.

## The extra files

New files only for what no kept file can hold, numbered per app after that app's last original, `dependencies` from `depends_on_files`.

- Prerequisites have no file of their own: `RunSQL("CREATE EXTENSION IF NOT EXISTS citext", "DROP EXTENSION IF EXISTS citext")` goes first in `operations` of the kept file that created it, or of the earlier kept file the plan pulled it into (`moved_in`); for Postgres extensions prefer `django.contrib.postgres.operations.CITextExtension()` / `CreateExtension("postgis")` when the originals used them. `CreateCollation` likewise.
- Functions and triggers: one `RunSQL(raw, "DROP TRIGGER ... ON ...; DROP FUNCTION ...")` per object, in `raw` order, at the end of their kept file; `functions_and_triggers` only for those reading something created after every candidate original.
- `deferred_foreign_keys`: `migrations.AddField(model_name="customer", name="primary_invoice", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="billing.invoice"))`, with `dependencies` on the files of both models. Column and FK are one field, so the field is absent from the table's `CreateModel`. This is also how FK cycles between apps are broken.
- `seed_reference_data`: `migrations.RunPython(forwards, migrations.RunPython.noop)` with the original function body, using `apps.get_model("catalog", "Tag")`, never a direct model import.

## Schema snapshot to cross-check

`models.py` of each app is the source of truth, and Django can diff it against the migrations without a database: `python manage.py makemigrations --check --dry-run` exits non-zero and prints what it would generate if the consolidated migrations do not reproduce the current models (field by field, options, indexes, constraints). Run it from the project's environment after placing the files; a clean exit is the strongest check available. It opens a connection for the history consistency check but only warns when none is reachable. `python manage.py showmigrations --plan` prints the resulting order and dependency resolution. `sqlmigrate <app> <name>` shows the SQL but needs a live database.

## After placing the files

- Move the originals out of every `migrations/` package, never into a subpackage of it: the loader imports every module in the package. `--fake-initial` is not the tool here (it only checks table existence per `CreateModel`).
- Rewrite `dependencies` in any migration outside the consolidation that pointed at a removed original (packages or third-party apps with migrations that depend on yours) to the kept file that now creates that model, or the previous file of that app; otherwise `NodeNotFoundError`.
- `makemigrations --check --dry-run`, then `showmigrations --plan`.

## Tracking table and reconciliation

Table `django_migrations`: `id`, `app` (varchar 255), `name` (varchar 255), `applied` (datetime). A row means applied; there is no checksum.

- Row without file: lenient (kept in memory, skipped by consistency checks); `migrate --prune <app>` deletes such rows (needs the app label; keeps rows still named by a `replaces`). File without row: pending, runs on the next `migrate` whatever its number. An applied migration with unapplied dependencies raises `InconsistentMigrationHistory` and blocks `migrate` and `makemigrations`.

Kept files keep their rows and need nothing; `replaces` is not used. Precheck first (`precheck.sql`, must return no rows): the originals this database has not run. A database that is behind is migrated on the old history first.

```sql
SELECT m.a, m.v AS not_applied FROM (SELECT 'billing' AS a, '0001_initial' AS v UNION ALL SELECT 'billing' AS a, '0002_customer_email' AS v UNION ALL …) m
WHERE NOT EXISTS (SELECT 1 FROM django_migrations t WHERE t.app = m.a AND t.name = m.v);
```

Baseline (`baseline.sql`, what `baseline.py` emits), plain SQL in one transaction:

```sql
BEGIN;   -- MySQL: START TRANSACTION;
DELETE FROM django_migrations WHERE app = 'billing' AND name = '0002_customer_email';
DELETE FROM django_migrations WHERE app = 'billing' AND name = '0004_invoice_status';
INSERT INTO django_migrations (app, name, applied) VALUES ('billing', '0018_payment', CURRENT_TIMESTAMP);
INSERT INTO django_migrations (app, name, applied) VALUES ('billing', '0019_deferred_foreign_keys', CURRENT_TIMESTAMP);
COMMIT;
```

Then confirm with `python manage.py showmigrations`: every file `[X]`, nothing pending.

Never `migrate --fake` the new files. A kept file can depend on a new one: when `Payment` is split out of `0001_initial` into `0018_payment.py`, the plan places it right after `0001_initial`, and the next kept file of the app (`0003_invoice.py`, with its FK to `Payment`) depends on `0018_payment` instead of `0001_initial`. That is fine for Django, which orders by `dependencies`, not by number, but on an existing database `0003` is applied and `0018` is not, and `migrate` runs `check_consistent_history` before anything else, even with `--fake`, so it stops with `InconsistentMigrationHistory` until the INSERT is done. `python manage.py migrate --prune <app>` (Django 4.1+) is an optional alternative for the DELETEs only, and hits the same check, so run it after the INSERTs.

## Quirks

- Implicit ManyToMany join tables (`implicit_m2m_of` set) get no file of their own: they live in the file that created them, inside the owner's `CreateModel` or as `AddField` on the owner. The dependency order still has to put the target model's file before that file. For a self-referential or cyclic M2M, drop the field from `CreateModel` and add it in `deferred_foreign_keys` with `AddField`.
- Two consolidated files cannot both be `initial = True` and depend on each other across apps in a cycle; break cycles with `AddField` in `deferred_foreign_keys`, exactly as `makemigrations` does.
- The plan's `depends_on_files` lists the user table's file as a plain dependency; for a FK to `settings.AUTH_USER_MODEL` write `migrations.swappable_dependency(settings.AUTH_USER_MODEL)` instead of that hard dependency. `swappable_dependency` must stay in `dependencies` of every file that has a FK to `settings.AUTH_USER_MODEL`, and the FK must be written as `to=settings.AUTH_USER_MODEL`, not the concrete label; otherwise projects swapping the user model later break.
- With a custom user model, `auth`, `admin` and every app with a user FK resolve `swappable_dependency` to `(<your app>, "__first__")`, the app's first migration, normally `0001_initial`. That kept file must still create the user model (when the user chooses to split it, the user model must be the one that stays) and must create it on its own: FKs from it to tables created later go to `deferred_foreign_keys`. `showmigrations --plan` shows the resolved order, trust it over the numbers.
- `makemigrations --check` compares against models, so if the originals drifted from `models.py` (a manual `RunSQL` column nobody added to the model) the check fails for a reason unrelated to the consolidation; report it rather than editing models.
