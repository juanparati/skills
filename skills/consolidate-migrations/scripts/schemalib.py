"""Shared helpers for the consolidate-migrations scripts. Standard library only.

Holds the per-framework naming table (where migrations live, how versions look,
how files are named) and small utilities used by the other scripts.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Framework table
#
# version styles:
#   laravel_ts  2023_11_20_100000            (YYYY_MM_DD_HHMMSS)
#   ts14        20231120100000               (YYYYMMDDHHMMSS)
#   diesel_ts   2023-11-20-100000            (YYYY-MM-DD-HHMMSS)
#   ms13        1700474400000                (unix milliseconds)
#   seq         12 / 0012 / V12              (integer, zero padded with seq_pad)
#
# file patterns use {version}, {table}, {view}, {name}, {ext}, {pascal}, {app}, {container}
#
# These patterns name only the NEW files (deferred FKs, seeds, tables split out of a
# multi-table original). Every original that created a surviving object keeps its own
# file name; plan_order.py takes those from manifest.source.originals.
# ---------------------------------------------------------------------------

FRAMEWORKS: dict[str, dict] = {
    "laravel": dict(
        label="Laravel", version="laravel_ts", ext="php", default_dir="database/migrations",
        table_file="{version}_create_{table}_table.{ext}", view_file="{version}_create_{view}_view.{ext}",
        misc_file="{version}_{name}.{ext}", folder=None, tracking_table="migrations",
    ),
    "django": dict(
        label="Django", version="seq", seq_pad=4, seq_start=1, ext="py", default_dir="<app>/migrations",
        table_file="{version}_{model}.{ext}", view_file="{version}_{view}_view.{ext}",
        misc_file="{version}_{name}.{ext}", folder=None, per_app=True, tracking_table="django_migrations",
    ),
    "rails": dict(
        label="Rails Active Record", version="ts14", ext="rb", default_dir="db/migrate",
        table_file="{version}_create_{table}.{ext}", view_file="{version}_create_{view}_view.{ext}",
        misc_file="{version}_{name}.{ext}", folder=None, tracking_table="schema_migrations",
    ),
    "ecto": dict(
        label="Phoenix Ecto", version="ts14", ext="exs", default_dir="priv/repo/migrations",
        table_file="{version}_create_{table}.{ext}", view_file="{version}_create_{view}_view.{ext}",
        misc_file="{version}_{name}.{ext}", folder=None, tracking_table="schema_migrations",
    ),
    "efcore": dict(
        label="EF Core (ASP.NET)", version="ts14", ext="cs", default_dir="Migrations",
        table_file="{container}.{pascal}.{ext}", view_file="{container}.{pascal}View.{ext}",
        misc_file="{container}.{pascal}.{ext}", folder=None, single_migration="{container_id}.{ext}",
        tracking_table="__EFMigrationsHistory",
    ),
    "doctrine": dict(
        label="Doctrine Migrations (Symfony)", version="ts14", ext="php", default_dir="migrations",
        table_file="Version{version}.{ext}", view_file="Version{version}.{ext}", misc_file="Version{version}.{ext}",
        folder=None, tracking_table="doctrine_migration_versions",
    ),
    "typeorm": dict(
        label="TypeORM", version="ms13", ext="ts", default_dir="src/migrations",
        table_file="{version}-Create{pascal}.{ext}", view_file="{version}-Create{pascal}View.{ext}",
        misc_file="{version}-{pascal}.{ext}", folder=None, tracking_table="migrations",
    ),
    "prisma": dict(
        label="Prisma Migrate", version="ts14", ext="sql", default_dir="prisma/migrations",
        table_file="{version}_create_{table}/migration.sql", view_file="{version}_create_{view}_view/migration.sql",
        misc_file="{version}_{name}/migration.sql", folder="migration.sql", tracking_table="_prisma_migrations",
    ),
    "flyway": dict(
        label="Flyway (Spring)", version="seq", seq_pad=0, seq_start=1, ext="sql", default_dir="src/main/resources/db/migration",
        table_file="V{version}__create_{table}.{ext}", view_file="V{version}__create_{view}_view.{ext}",
        misc_file="V{version}__{name}.{ext}", folder=None, tracking_table="flyway_schema_history",
    ),
    "liquibase": dict(
        label="Liquibase (Spring)", version="seq", seq_pad=3, seq_start=1, ext="xml", default_dir="src/main/resources/db/changelog",
        table_file="{version}-create-{table_dash}.{ext}", view_file="{version}-create-{view_dash}-view.{ext}",
        misc_file="{version}-{name_dash}.{ext}", folder=None, tracking_table="DATABASECHANGELOG",
    ),
    "knex": dict(
        label="Knex", version="ts14", ext="js", default_dir="migrations",
        table_file="{version}_create_{table}.{ext}", view_file="{version}_create_{view}_view.{ext}",
        misc_file="{version}_{name}.{ext}", folder=None, tracking_table="knex_migrations",
    ),
    "sequelize": dict(
        label="Sequelize", version="ts14", ext="js", default_dir="migrations",
        table_file="{version}-create-{table_dash}.{ext}", view_file="{version}-create-{view_dash}-view.{ext}",
        misc_file="{version}-{name_dash}.{ext}", folder=None, tracking_table="SequelizeMeta",
    ),
    "drizzle": dict(
        label="Drizzle Kit", version="seq", seq_pad=4, seq_start=0, ext="sql", default_dir="drizzle",
        table_file="{version}_create_{table}.{ext}", view_file="{version}_create_{view}_view.{ext}",
        misc_file="{version}_{name}.{ext}", folder=None, tracking_table="__drizzle_migrations",
    ),
    "goose": dict(
        label="Goose", version="seq", seq_pad=5, seq_start=1, ext="sql", default_dir="db/migrations",
        table_file="{version}_create_{table}.{ext}", view_file="{version}_create_{view}_view.{ext}",
        misc_file="{version}_{name}.{ext}", folder=None, tracking_table="goose_db_version",
    ),
    "atlas": dict(
        label="Atlas", version="ts14", ext="sql", default_dir="migrations",
        table_file="{version}_create_{table}.{ext}", view_file="{version}_create_{view}_view.{ext}",
        misc_file="{version}_{name}.{ext}", folder=None, tracking_table="atlas_schema_revisions",
    ),
    "diesel": dict(
        label="Diesel", version="diesel_ts", ext="sql", default_dir="migrations",
        table_file="{version}_create_{table}/up.sql", view_file="{version}_create_{view}_view/up.sql",
        misc_file="{version}_{name}/up.sql", folder="up.sql+down.sql", tracking_table="__diesel_schema_migrations",
    ),
    "sqlx": dict(
        label="SQLx", version="ts14", ext="sql", default_dir="migrations",
        table_file="{version}_create_{table}.{ext}", view_file="{version}_create_{view}_view.{ext}",
        misc_file="{version}_{name}.{ext}", folder=None, tracking_table="_sqlx_migrations",
    ),
}

ALIASES = {
    "ef-core": "efcore", "ef_core": "efcore", "entityframework": "efcore", "aspnet": "efcore",
    "symfony": "doctrine", "nestjs": "typeorm", "spring": "flyway", "activerecord": "rails",
    "phoenix": "ecto", "drizzle-kit": "drizzle", "prisma-migrate": "prisma", "sequelize-cli": "sequelize",
}

DIALECTS = ("postgres", "mysql", "sqlite", "mssql")


def framework_key(name: str) -> str:
    key = name.strip().lower().replace(" ", "")
    key = ALIASES.get(key, key)
    if key not in FRAMEWORKS:
        die(f"unknown framework '{name}'. Known: {', '.join(sorted(FRAMEWORKS))}")
    return key


# ---------------------------------------------------------------------------
# Version arithmetic
# ---------------------------------------------------------------------------

_VERSION_RE = {
    "laravel_ts": re.compile(r"(\d{4})_(\d{2})_(\d{2})_(\d{6})"),
    "ts14": re.compile(r"(\d{14})"),
    "diesel_ts": re.compile(r"(\d{4})-?(\d{2})-?(\d{2})-?(\d{6})"),
    "ms13": re.compile(r"(\d{13})"),
    "seq": re.compile(r"[Vv]?(\d+)"),
}


def parse_version(style: str, text: str):
    """Return the version as a datetime (timestamp styles) or int, or None."""
    m = _VERSION_RE[style].search(text or "")
    if not m:
        return None
    try:
        if style == "laravel_ts" or style == "diesel_ts":
            y, mo, d, hms = m.groups()
            return _dt.datetime(int(y), int(mo), int(d), int(hms[:2]), int(hms[2:4]), int(hms[4:6]))
        if style == "ts14":
            s = m.group(1)
            return _dt.datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]), int(s[10:12]), int(s[12:14]))
        if style == "ms13":
            return int(m.group(1))
        return int(m.group(1))
    except ValueError:
        return None


def format_version(style: str, value, pad: int = 0) -> str:
    if style == "laravel_ts":
        return value.strftime("%Y_%m_%d_%H%M%S")
    if style == "ts14":
        return value.strftime("%Y%m%d%H%M%S")
    if style == "diesel_ts":
        return value.strftime("%Y-%m-%d-%H%M%S")
    if style == "ms13":
        return str(int(value))
    return str(int(value)).zfill(pad) if pad else str(int(value))


def step_version(style: str, value, n: int):
    """The version n steps after value: one second (or one ms*1000, or +1) per step."""
    if style in ("laravel_ts", "ts14", "diesel_ts"):
        return value + _dt.timedelta(seconds=n)
    if style == "ms13":
        return int(value) + 1000 * n
    return int(value) + n


def version_between(style: str, fw_key: str, prev: str, nxt: str | None, k: int, taken: set[str]) -> str | None:
    """The k-th version right after original `prev` that still sorts before the next original `nxt`.

    Used for tables split out of a multi-table original: they go right after it when the
    version format leaves room (a free second, or a Flyway dotted version), so the
    order of the history is kept. None when there is no room; the caller then appends
    the file after the last original. Never returns a version in `taken`.
    """
    if style == "seq":
        if fw_key != "flyway":
            return None
        cand = f"{prev}.{k}"
        return None if cand in taken else cand
    p, n = parse_version(style, prev), parse_version(style, nxt) if nxt else None
    if p is None:
        return None
    v = step_version(style, p, k)
    if n is not None and v >= n:
        return None
    cand = format_version(style, v)
    return None if cand in taken else cand


def now_version(style: str):
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    if style == "ms13":
        return int(now.timestamp() * 1000)
    if style == "seq":
        return 1
    return now


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def snake(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.strip("_").lower()


def pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^0-9a-zA-Z]+", name) if p)


def qualified(name: str, schema: str | None) -> str:
    return f"{schema}.{name}" if schema else name


def split_qualified(name: str, schema: str | None = None) -> tuple[str | None, str]:
    if schema:
        return schema, name
    if "." in name:
        s, n = name.split(".", 1)
        return s, n
    return None, name


def file_name(fw: dict, kind: str, name: str, version: str, ext: str | None = None, app: str | None = None, model: str | None = None,
              container: str = "") -> str:
    """Build the file (or folder/file) name for one new plan entry."""
    ext = ext or fw["ext"]
    flat = snake(name)
    model_flat = snake(model) if model else flat
    pattern = {"table": fw["table_file"], "view": fw["view_file"]}.get(kind, fw["misc_file"])
    return pattern.format(
        version=version, ext=ext, table=flat, view=flat, name=flat, pascal=pascal(name), model=model_flat,
        table_dash=flat.replace("_", "-"), view_dash=flat.replace("_", "-"), name_dash=flat.replace("_", "-"),
        app=app or "", container=container,
    )


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def load_json(path: str | Path):
    p = Path(path)
    if not p.exists():
        die(f"file not found: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{p}: invalid JSON: {e}")


def dump_json(data, path: str | Path | None):
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def die(msg: str, code: int = 1):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(code)


def warn(msg: str):
    sys.stderr.write(f"warning: {msg}\n")


def node_id(kind: str, name: str, schema: str | None = None) -> str:
    return f"{kind}:{qualified(name, schema)}"


def table_columns_names(table: dict) -> list[str]:
    return [c["name"] for c in table.get("columns", [])]
