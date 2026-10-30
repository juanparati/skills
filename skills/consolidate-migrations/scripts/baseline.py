#!/usr/bin/env python3
"""Emit what an EXISTING database needs so the consolidated migrations count as applied.

Usage:
    baseline.py <plan.json> [--dir <dir with the written files>] [-o baseline.sql|baseline.sh] [--batch N]

Why: every migration tool keeps a tracking table listing which files ran. The
consolidated files that keep an original's name are already recorded there, so they
need nothing (except a new checksum where the tool stores one: SQLx, Prisma, Flyway,
Liquibase). What changes is the rest: the originals that disappeared still have rows
(Knex, SQLx and Flyway refuse to run with those; Goose, Diesel, Rails, Sequelize and
TypeORM fail on rollback), and the new files (deferred FKs, seeds, split tables) have
none, so the tool would run them and fail on "already exists". The fix is bookkeeping
only. Fresh databases need nothing.

The bookkeeping is right only for a database that had run EVERY original: a kept name
looks applied even where the alters folded into it never ran, and that database would
silently miss those changes. So a precheck is written next to the baseline
(precheck.sql, or the tool's status command): it lists originals that database has not
run, and it must come back empty before the baseline is applied.

Frameworks whose tool has a proper command get a shell script; the others get SQL.
Checksums (SQLx, Prisma, Drizzle) are computed from the written files, so pass --dir.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schemalib import die, load_json  # noqa: E402


def q(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def stem(path: str) -> str:
    name = Path(path).name
    if name in ("migration.sql", "up.sql", "down.sql"):
        return Path(path).parent.name
    s = Path(name).stem
    return s[:-3] if s.endswith(".up") else s


def app_of(path: str) -> str:
    """Django: <app>/migrations/<name>.py -> <app> (the directory name stands for the app label)."""
    return Path(path).parent.parent.name


def typeorm_class(path: str) -> str:
    """TypeORM stores the class name, `CreateOrders1730800000000` for `1730800000000-CreateOrders.ts` by convention."""
    ver, _, name = stem(path).partition("-")
    return f"{name.replace('-', '')}{ver}" if name else stem(path)


def file_bytes(dir_: Path | None, entry: dict) -> bytes:
    if not dir_:
        die("--dir is required for this framework (a checksum of each written file is stored)")
    p = dir_ / entry["dir"] / entry["file"] if entry.get("dir") else dir_ / entry["file"]
    if not p.exists():
        die(f"written file not found: {p}")
    return p.read_bytes()


def precheck(table: str, col: str, values: list, numeric: bool = False, extra: str = "", like: str | None = None) -> str:
    """A query listing the originals this database has not run. It must return no rows."""
    rows = " UNION ALL ".join(f"SELECT {v if numeric else q(v)} AS v" for v in values)
    match = f"t.{col} LIKE {like}" if like else f"t.{col} = m.v"
    return f"SELECT m.v AS not_applied FROM ({rows}) m WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {match}{extra});"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--dir", help="directory holding the written consolidated files (for checksums)")
    ap.add_argument("-o", "--out")
    ap.add_argument("--batch", type=int, default=None, help="Laravel/Knex batch number to record (default: computed as MAX(batch)+1 in the SQL)")
    args = ap.parse_args()

    plan = load_json(args.plan)
    if "originals" not in plan:
        die("plan.json has no 'originals': re-run plan_order.py with this skill's version")
    fw = plan["framework"]
    dialect = plan.get("dialect", "postgres")
    originals = plan["originals"]
    removed = [o for o in originals if not o["kept"]]
    entries = plan["entries"]
    kept = [e for e in entries if e.get("kept")]
    new = [e for e in entries if not e.get("kept")]
    dir_ = Path(args.dir) if args.dir else None
    lines: list[str] = []
    check: list[str] = []      # precheck.sql statements (or commands, for shell frameworks)
    shell = False

    def head(text: str):
        lines.append(("# " if shell else "-- ") + text)

    def removed_in(values: list[str], col: str, table: str):
        if values:
            lines.append(f"DELETE FROM {table} WHERE {col} IN (" + ", ".join(values) + ");")

    head_note = (f"{len(kept)} consolidated file(s) keep an original's name and are already recorded; "
                 f"{len(removed)} original(s) disappeared; {len(new)} file(s) are new.")

    if fw == "laravel":
        head("Laravel: table `migrations` (id, migration, batch). " + head_note)
        head("Rows of removed originals are ignored by `migrate` but cleaned up here; new files without a row would run.")
        check.append(precheck("migrations", "migration", [stem(o["path"]) for o in originals]))
        lines.append("START TRANSACTION;" if dialect == "mysql" else "BEGIN;")
        removed_in([q(stem(o["path"])) for o in removed], "migration", "migrations")
        if new:
            batch = str(args.batch) if args.batch else "(SELECT COALESCE(MAX(b.batch), 0) + 1 FROM (SELECT batch FROM migrations) AS b)"
            rows = " UNION ALL ".join(f"SELECT {q(stem(e['file']))} AS migration" for e in new)
            lines.append(f"INSERT INTO migrations (migration, batch) SELECT m.migration, {batch} FROM ({rows}) AS m;")
        lines.append("COMMIT;")

    elif fw == "django":
        head("Django: table django_migrations (app, name, applied). " + head_note)
        head("Plain SQL rather than `migrate --fake`: a kept file may depend on a new one (a split-out model), and `migrate`")
        head("checks the history first (InconsistentMigrationHistory), even with --fake. Confirm with: python manage.py showmigrations")
        rows = " UNION ALL ".join(f"SELECT {q(app_of(o['path']))} AS a, {q(stem(o['path']))} AS v" for o in originals)
        check.append(f"SELECT m.a, m.v AS not_applied FROM ({rows}) m "
                     f"WHERE NOT EXISTS (SELECT 1 FROM django_migrations t WHERE t.app = m.a AND t.name = m.v);")
        lines.append("START TRANSACTION;" if dialect == "mysql" else "BEGIN;")
        for o in removed:
            lines.append(f"DELETE FROM django_migrations WHERE app = {q(app_of(o['path']))} AND name = {q(stem(o['path']))};")
        for e in new:
            app = e.get("app") or app_of((e.get("dir") or "") + "/" + e["file"])
            lines.append(f"INSERT INTO django_migrations (app, name, applied) VALUES ({q(app)}, {q(stem(e['file']))}, CURRENT_TIMESTAMP);")
        lines.append("COMMIT;")
        head("Alternative for the removed rows only (Django 4.1+): python manage.py migrate --prune <app>")

    elif fw in ("rails", "ecto"):
        head(f"{fw}: table `schema_migrations` (version). " + head_note)
        head("Rows of removed originals break rollbacks that reach them (Rails: NO FILE); new files without a row would run.")
        if fw == "rails":
            check.append(precheck("schema_migrations", "version", [str(o["version"]) for o in originals]))
            removed_in([q(o["version"]) for o in removed], "version", "schema_migrations")
            for e in new:
                lines.append(f"INSERT INTO schema_migrations (version) VALUES ({q(e['version'])});")
        else:
            check.append(precheck("schema_migrations", "version", [int(o["version"]) for o in originals], numeric=True))
            removed_in([str(int(o["version"])) for o in removed], "version", "schema_migrations")
            for e in new:
                lines.append(f"INSERT INTO schema_migrations (version, inserted_at) VALUES ({e['version']}, CURRENT_TIMESTAMP);")

    elif fw == "efcore":
        head("EF Core: table __EFMigrationsHistory (MigrationId, ProductVersion). The single consolidated migration keeps the first")
        head("original's id, whose row already exists; only the rows of the other originals go.")
        head("SQL Server: use [__EFMigrationsHistory] brackets; MySQL/SQLite: drop the double quotes.")
        check.append(precheck('"__EFMigrationsHistory"', '"MigrationId"', [stem(o["path"]) for o in originals]))
        removed_in([q(stem(o["path"])) for o in removed], '"MigrationId"', '"__EFMigrationsHistory"')

    elif fw == "doctrine":
        shell = True
        head("Doctrine: let the tool rewrite doctrine_migration_versions from the files on disk (kept files keep their version).")
        concat = "CONCAT('%', m.v)" if dialect == "mysql" else "'%' || m.v"
        check.append(precheck("doctrine_migration_versions", "version", [f"Version{o['version']}" for o in originals], like=concat))
        lines.append("php bin/console doctrine:migrations:version --delete --all --no-interaction")
        lines.append("php bin/console doctrine:migrations:version --add --all --no-interaction")
        lines.append("php bin/console doctrine:migrations:status")

    elif fw == "typeorm":
        head("TypeORM: table `migrations` (id, timestamp, name = class name). " + head_note)
        head("Rows of removed originals break `migration:revert`; new classes without a row would run. MySQL: backticks.")
        check.append(precheck('"migrations"', '"name"', [typeorm_class(o["path"]) for o in originals]))
        removed_in([q(typeorm_class(o["path"])) for o in removed], '"name"', '"migrations"')
        for e in new:
            lines.append(f'INSERT INTO "migrations" ("timestamp", "name") VALUES ({e["version"]}, {q(e.get("class") or typeorm_class(e["file"]))});')

    elif fw == "prisma":
        head("Prisma: table _prisma_migrations. " + head_note)
        head("Kept folders changed content, so their checksum (sha256 of migration.sql) is refreshed; otherwise `migrate dev` and")
        head("`migrate status` report them as modified and dev proposes a reset. New folders get a row, as `migrate resolve --applied` would.")
        check.append(precheck('"_prisma_migrations"', '"migration_name"', [stem(o["path"]) for o in originals],
                              extra=' AND t."finished_at" IS NOT NULL AND t."rolled_back_at" IS NULL'))
        removed_in([q(stem(o["path"])) for o in removed], '"migration_name"', '"_prisma_migrations"')
        for e in kept:
            digest = hashlib.sha256(file_bytes(dir_, e)).hexdigest()
            lines.append(f'UPDATE "_prisma_migrations" SET "checksum" = {q(digest)} WHERE "migration_name" = {q(stem(e["file"]))};')
        for e in new:
            digest = hashlib.sha256(file_bytes(dir_, e)).hexdigest()
            lines.append('INSERT INTO "_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "started_at", "applied_steps_count") '
                         f"VALUES ({q(str(uuid.uuid4()))}, {q(digest)}, CURRENT_TIMESTAMP, {q(stem(e['file']))}, '', CURRENT_TIMESTAMP, 1);")
        head("MySQL: backticks instead of double quotes. Then: npx prisma migrate status")

    elif fw == "flyway":
        head("Flyway: table flyway_schema_history. validate (run by migrate) FAILS on applied rows whose file is gone and on")
        head("checksum mismatches. Removed originals lose their rows, new files are inserted with checksum NULL, and")
        head("`flyway repair` then realigns every checksum (kept files changed content) with the files on disk.")
        check.append(precheck("flyway_schema_history", "script", [Path(o["path"]).name for o in originals], extra=" AND t.success = TRUE"))
        removed_in([q(Path(o["path"]).name) for o in removed], "script", "flyway_schema_history")
        for e in new:
            desc = stem(e["file"]).split("__", 1)[1].replace("_", " ") if "__" in stem(e["file"]) else stem(e["file"])
            lines.append("INSERT INTO flyway_schema_history (installed_rank, version, description, type, script, checksum, installed_by, installed_on, execution_time, success) "
                         f"VALUES ((SELECT COALESCE(MAX(h.installed_rank), 0) + 1 FROM flyway_schema_history h), {q(e['version'])}, {q(desc)}, 'SQL', "
                         f"{q(e['file'])}, NULL, 'consolidate', CURRENT_TIMESTAMP, 0, TRUE);")
        head("Then run: flyway repair   (realigns checksums with the files; needs the same `locations` as migrate)")
        head("MySQL: the self-referencing subquery needs a derived table: (SELECT COALESCE(MAX(r), 0) + 1 FROM (SELECT MAX(installed_rank) r FROM flyway_schema_history) x)")

    elif fw == "liquibase":
        shell = True
        head("Liquibase: kept changesets keep their id/author/filename, so their rows stay; their MD5SUM no longer matches the new")
        head("content, so clear the checksums (recomputed, not re-run, on the next update). changelog-sync then marks the new")
        head("changesets as applied without running them.")
        check.append("# before the swap, with the old changelog: liquibase status --verbose   (must report 0 changesets not applied)")
        lines.append("liquibase clear-checksums")
        lines.append("liquibase changelog-sync")
        lines.append("liquibase unexpected-changesets   # rows of removed changesets: harmless; delete them by hand if you want a clean table")
        lines.append("liquibase status --verbose")

    elif fw == "knex":
        head("Knex: table knex_migrations (id, name, batch, migration_time). Knex REFUSES to run when an applied row has no file:")
        head("'The migration directory is corrupt, the following files are missing'. The DELETE below is mandatory.")
        check.append(precheck("knex_migrations", "name", [Path(o["path"]).name for o in originals]))
        removed_in([q(Path(o["path"]).name) for o in removed], "name", "knex_migrations")
        if new:
            batch = str(args.batch) if args.batch else "(SELECT COALESCE(MAX(b.batch), 0) + 1 FROM (SELECT batch FROM knex_migrations) AS b)"
            rows = " UNION ALL ".join(f"SELECT {q(e['file'])} AS name" for e in new)
            lines.append(f"INSERT INTO knex_migrations (name, batch, migration_time) SELECT m.name, {batch}, CURRENT_TIMESTAMP FROM ({rows}) AS m;")

    elif fw == "sequelize":
        head('Sequelize: table "SequelizeMeta" (name). ' + head_note)
        head("Rows of removed originals break db:migrate:undo; new files without a row would run. MySQL: backticks.")
        check.append(precheck('"SequelizeMeta"', "name", [Path(o["path"]).name for o in originals]))
        removed_in([q(Path(o["path"]).name) for o in removed], "name", '"SequelizeMeta"')
        for e in new:
            lines.append(f'INSERT INTO "SequelizeMeta" (name) VALUES ({q(e["file"])});')

    elif fw == "drizzle":
        table = "drizzle.__drizzle_migrations" if dialect == "postgres" else "__drizzle_migrations"
        head(f"Drizzle: table {table} (id, hash, created_at). The migrator (journal layout) applies every journal entry whose")
        head("`when` is greater than the last created_at. Kept entries keep their `when`, so they are already behind it; the new")
        head("entries come last, so one row with the LAST entry's `when` and hash marks them applied. Rows are never deleted.")
        head("drizzle-kit 1.0 (folder per migration, no journal) matches by `name` instead: delete the rows of removed folders and")
        head("insert one row per new folder (hash, created_at = folder timestamp as UTC ms, name = folder name).")
        check.append(f"-- SELECT MAX(created_at) FROM {table};  must equal the `when` of the LAST entry of the old meta/_journal.json")
        if new:
            last = entries[-1]
            digest = hashlib.sha256(file_bytes(dir_, last)).hexdigest()
            when = last.get("when") or "<when value from meta/_journal.json for this entry>"
            lines.append(f"INSERT INTO {table} (hash, created_at) VALUES ({q(digest)}, {when});")
        else:
            head("No new files: nothing to record.")

    elif fw == "goose":
        head("Goose: table goose_db_version (id, version_id, is_applied, tstamp). " + head_note)
        head("down/redo fail when the highest row has no file, and new versions without a row would run.")
        check.append(precheck("goose_db_version", "version_id", [int(o["version"]) for o in originals], numeric=True))
        removed_in([str(int(o["version"])) for o in removed], "version_id", "goose_db_version")
        for e in new:
            lines.append(f"INSERT INTO goose_db_version (version_id, is_applied) VALUES ({int(e['version'])}, true);")
        head("MySQL/SQLite: `true` -> 1. Keep the version 0 row.")

    elif fw == "atlas":
        shell = True
        last = entries[-1]["version"]
        head("Atlas: rehash the directory (kept files changed content), then set the revision pointer to the last file.")
        head("Check that `migrate status` reports no hash drift for the kept revisions before the next deploy.")
        check.append('# before the swap: atlas migrate status --url "$DATABASE_URL"   (must report: Already at latest version)')
        lines.append("atlas migrate hash")
        lines.append(f'atlas migrate set {last} --url "$DATABASE_URL"')
        lines.append('atlas migrate status --url "$DATABASE_URL"')

    elif fw == "diesel":
        head("Diesel: table __diesel_schema_migrations (version, run_on). " + head_note)
        head("`migration run` ignores rows without a folder, `revert` needs the folder; new folders without a row would run.")
        check.append(precheck("__diesel_schema_migrations", "version", [str(o["version"]).replace("-", "") for o in originals]))
        removed_in([q(str(o["version"]).replace("-", "")) for o in removed], "version", "__diesel_schema_migrations")
        for e in new:
            lines.append(f"INSERT INTO __diesel_schema_migrations (version) VALUES ({q(e['version'].replace('-', ''))});")

    elif fw == "sqlx":
        head("SQLx: table _sqlx_migrations (version, description, installed_on, success, checksum, execution_time).")
        head("`migrate run` fails when an applied version has no file (unless --ignore-missing) and ALWAYS when a checksum differs,")
        head("so removed rows go, kept versions get the SHA-384 of their new content, and new files are inserted with theirs.")

        def chk(digest: str) -> str:
            if dialect == "postgres":
                return f"decode('{digest}', 'hex')"
            if dialect == "mysql":
                return f"UNHEX('{digest}')"
            return f"X'{digest}'"

        check.append(precheck("_sqlx_migrations", "version", [int(o["version"]) for o in originals], numeric=True, extra=" AND t.success = TRUE"))
        removed_in([str(int(o["version"])) for o in removed], "version", "_sqlx_migrations")
        for e in kept:
            lines.append(f"UPDATE _sqlx_migrations SET checksum = {chk(hashlib.sha384(file_bytes(dir_, e)).hexdigest())} WHERE version = {int(e['version'])};")
        for e in new:
            desc = stem(e["file"]).split("_", 1)[1].replace("_", " ") if "_" in stem(e["file"]) else stem(e["file"])
            lines.append(f"INSERT INTO _sqlx_migrations (version, description, success, checksum, execution_time) "
                         f"VALUES ({int(e['version'])}, {q(desc)}, true, {chk(hashlib.sha384(file_bytes(dir_, e)).hexdigest())}, 0);")
    else:
        die(f"no baseline recipe for framework '{fw}'")

    out = Path(args.out or Path(args.plan).with_name("baseline.sh" if shell else "baseline.sql"))
    pre = out.with_name("precheck.sh" if check[0].startswith("#") else "precheck.sql")
    if shell:
        lines.insert(0, "#!/usr/bin/env bash")
        lines.insert(1, "set -euo pipefail")
        lines.insert(2, f"# Run {pre.name} first: it must report nothing not applied.")
    else:
        lines.insert(0, f"-- Run {pre.name} first: it must return no rows.")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    comment = "#" if pre.suffix == ".sh" else "--"
    pre.write_text(f"{comment} Precondition: this database ran every original migration. Run against each existing database\n"
                   f"{comment} BEFORE {out.name}. Any row (or pending item) means that database is behind: migrate it on the old\n"
                   f"{comment} history first (restore the archived files), otherwise it would silently miss what the kept files now contain.\n"
                   + "\n".join(check) + "\n", encoding="utf-8")
    print(f"baseline written to {out} ({'shell' if shell else 'SQL'}), precheck to {pre}. Run the precheck, then the baseline, "
          f"against every existing database before the next deploy.")


if __name__ == "__main__":
    main()
