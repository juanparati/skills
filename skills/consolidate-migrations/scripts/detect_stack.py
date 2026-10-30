#!/usr/bin/env python3
"""Detect the migration framework(s) of a repository and list the migrations in replay order.

Usage:
    detect_stack.py <repo> [-o stack.json]

Prints (and optionally writes) JSON:
{
  "candidates": [
    { "framework": "laravel", "confidence": "high", "migrations_dirs": [...], "dialect": "mysql",
      "versioning": "laravel_ts", "files": [{"path": ..., "version": ...}], "first_version": ..., "last_version": ...,
      "snapshot": "database/schema/mysql-schema.sql" | null, "data_files": [...],
      "multi_create_files": [{"path": ..., "creates": 3}], "tracking_table": "migrations", "notes": [...] }
  ]
}

The replay order is what matters most: the ops files are folded in this order. For
most tools it is the version prefix; Django follows each migration's `dependencies`,
Liquibase its master changelog, Drizzle its journal. Heuristics are deliberately
simple (file names and a few greps), so confirm the result with the user when the
confidence is not high or when several candidates are listed.

multi_create_files lists the originals that seem to create more than one table: the
consolidated files keep the originals' names, so the user decides whether such a file
stays one migration or is split into one per table. It is a hint for that question;
the plan (after the transcription) gives the exact list.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schemalib import FRAMEWORKS, dump_json, parse_version, format_version  # noqa: E402

SKIP_DIRS = {"node_modules", "vendor", ".git", "venv", ".venv", "site-packages", "dist", "build", "target", "bin", "obj",
             "__pycache__", ".idea", ".vscode", "deps", "_build", ".next", "coverage"}
DATA_RE = re.compile(r"\b((?<!new_)INSERT\s+INTO\s+(?![\"`]?new_)|(?<!ON\s)UPDATE\s+(?!ON\b)[\"`\w]|DELETE\s+FROM|DB::table|DB::insert|DB::update|RunPython|RunSQL|update_all|"
                     r"insert_all|execute\s*\(\s*['\"]\s*(INSERT|UPDATE|DELETE)|bulk_create|queryInterface\.bulkInsert|"
                     r"Repo\.insert|Repo\.update|\.insert\(|migrationBuilder\.InsertData|migrationBuilder\.Sql\(\s*\"\s*(INSERT|UPDATE))",
                     re.IGNORECASE)
# One table creation, in any supported framework's vocabulary. SQLite rebuild blocks
# (CREATE TABLE "new_X" ... RENAME) recreate a table, so they do not count.
CREATE_RE = re.compile(r"Schema::create\s*\(|CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?(?![\"`\[]?new_)|migrations\.CreateModel\s*\(|"
                       r"\bcreate_table\s*[(:]|\bcreate\s+table\s*\(|\.CreateTable\s*\(|\bcreateTable\s*[(:]|<createTable\b",
                       re.IGNORECASE)


def walk(root: Path):
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def grep(path: Path, pattern: str, flags=re.IGNORECASE) -> re.Match | None:
    try:
        return re.search(pattern, path.read_text(encoding="utf-8", errors="ignore"), flags)
    except OSError:
        return None


def rel(root: Path, p: Path) -> str:
    return str(p.relative_to(root))


def data_files(root: Path, files: list[Path]) -> list[str]:
    out = []
    for f in files:
        try:
            if DATA_RE.search(f.read_text(encoding="utf-8", errors="ignore")):
                out.append(rel(root, f))
        except OSError:
            pass
    return out


def multi_create_files(root: Path, files: list[Path]) -> list[dict]:
    out = []
    for f in files:
        try:
            n = len(CREATE_RE.findall(f.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
        if n > 1:
            out.append({"path": rel(root, f), "creates": n})
    return out


def dialect_from_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.lower()
    for key, d in (("postgres", "postgres"), ("pgsql", "postgres"), ("psql", "postgres"), ("mysql", "mysql"), ("mariadb", "mysql"),
                   ("sqlite", "sqlite"), ("sqlserver", "mssql"), ("mssql", "mssql"), ("file:", "sqlite")):
        if key in u:
            return d
    return None


def env_value(root: Path, key: str) -> str | None:
    for name in (".env", ".env.local", ".env.example"):
        p = root / name
        if p.exists():
            m = grep(p, rf"^\s*{key}\s*=\s*['\"]?([^'\"\n]+)", re.MULTILINE)
            if m:
                return m.group(1).strip()
    return None


def sort_by_version(root: Path, files: list[Path], style: str) -> list[dict]:
    def key(p: Path):
        v = parse_version(style, p.name if style != "seq" else p.name.split("_")[0].split("-")[0].split("__")[0])
        return (v is None, v if v is not None else 0, p.name)
    out = []
    for p in sorted(files, key=key):
        v = parse_version(style, p.name if style != "seq" else p.name.split("_")[0].split("-")[0].split("__")[0])
        out.append({"path": rel(root, p), "version": format_version(style, v, 0) if v is not None else None})
    return out


def candidate(fw: str, root: Path, dirs: list[Path], files: list[Path], dialect, snapshot, confidence="high", notes=None, ordered=None):
    style = FRAMEWORKS[fw]["version"]
    ordered = ordered or sort_by_version(root, files, style)
    versions = [f["version"] for f in ordered if f.get("version")]
    return {
        "framework": fw, "confidence": confidence,
        "migrations_dirs": sorted({rel(root, d) for d in dirs}),
        "dialect": dialect, "versioning": style,
        "files": ordered, "count": len(ordered),
        "first_version": versions[0] if versions else None, "last_version": versions[-1] if versions else None,
        "snapshot": rel(root, snapshot) if isinstance(snapshot, Path) else snapshot,
        "data_files": data_files(root, [root / f["path"] for f in ordered]),
        "multi_create_files": multi_create_files(root, [root / f["path"] for f in ordered]),
        "tracking_table": FRAMEWORKS[fw]["tracking_table"],
        "notes": notes or [],
    }


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------

def detect_laravel(root, all_files):
    if not (root / "artisan").exists():
        return None
    files = [p for p in all_files if p.suffix == ".php" and "database/migrations" in str(p.parent) and parse_version("laravel_ts", p.name)]
    if not files:
        return None
    dialect = dialect_from_url(env_value(root, "DB_CONNECTION")) or "mysql"
    snaps = list((root / "database" / "schema").glob("*-schema.sql")) if (root / "database" / "schema").exists() else []
    return candidate("laravel", root, {p.parent for p in files}, files, dialect, snaps[0] if snaps else None,
                     notes=["Laravel's own squash is `php artisan schema:dump` (one SQL dump, not per table)"])


def detect_django(root, all_files):
    if not (root / "manage.py").exists():
        return None
    files = [p for p in all_files if p.suffix == ".py" and p.parent.name == "migrations" and re.match(r"\d{4}_", p.name)]
    if not files:
        return None
    dialect = None
    for p in all_files:
        if p.name == "settings.py" or (p.suffix == ".py" and "settings" in p.parts):
            m = grep(p, r"django\.db\.backends\.(postgresql|mysql|sqlite3|oracle)")
            if m:
                dialect = {"postgresql": "postgres", "mysql": "mysql", "sqlite3": "sqlite"}.get(m.group(1), m.group(1))
                break
    # replay order from dependencies
    nodes = {}
    for p in files:
        app = p.parent.parent.name
        name = p.stem
        text = p.read_text(encoding="utf-8", errors="ignore")
        deps = []
        for m in re.finditer(r"\(\s*['\"]([\w.]+)['\"]\s*,\s*['\"]([\w]+)['\"]\s*\)", text.split("operations")[0]):
            deps.append((m.group(1), m.group(2)))
        nodes[(app, name)] = {"path": p, "deps": [d for d in deps if d in nodes or True]}
    order, seen = [], set()

    def visit(key, stack=()):
        if key in seen or key not in nodes:
            return
        if key in stack:
            return
        for d in nodes[key]["deps"]:
            visit(d, stack + (key,))
        seen.add(key)
        order.append(key)

    for key in sorted(nodes):
        visit(key)
    ordered = [{"path": rel(root, nodes[k]["path"]), "version": k[1].split("_")[0], "app": k[0]} for k in order]
    apps = sorted({k[0] for k in nodes})
    models = [rel(root, root / a / "models.py") for a in apps if (root / a / "models.py").exists()]
    return candidate("django", root, {p.parent for p in files}, files, dialect, ", ".join(models) or None, ordered=ordered,
                     notes=[f"apps with migrations: {', '.join(apps)}", "replay order follows each migration's dependencies"])


def detect_rails(root, all_files):
    files = [p for p in all_files if p.suffix == ".rb" and p.parent.name == "migrate" and p.parent.parent.name == "db"]
    if not files:
        return None
    dialect = None
    dbyml = root / "config" / "database.yml"
    if dbyml.exists():
        m = grep(dbyml, r"adapter:\s*(\w+)")
        dialect = dialect_from_url(m.group(1)) if m else None
    snap = next((root / "db" / n for n in ("schema.rb", "structure.sql") if (root / "db" / n).exists()), None)
    return candidate("rails", root, {p.parent for p in files}, files, dialect, snap,
                     notes=["Rails' own answer to squashing is db/schema.rb + db:schema:load"])


def detect_ecto(root, all_files):
    files = [p for p in all_files if p.suffix == ".exs" and p.parent.name == "migrations" and "priv" in p.parts]
    if not files:
        return None
    dialect = None
    for p in all_files:
        if p.suffix == ".exs" and p.parent.name == "config":
            m = grep(p, r"Ecto\.Adapters\.(Postgres|MyXQL|SQLite3|Tds)")
            if m:
                dialect = {"Postgres": "postgres", "MyXQL": "mysql", "SQLite3": "sqlite", "Tds": "mssql"}[m.group(1)]
                break
    snap = next((p for p in all_files if p.name == "structure.sql" and "priv" in p.parts), None)
    return candidate("ecto", root, {p.parent for p in files}, files, dialect, snap)


def detect_efcore(root, all_files):
    snaps = [p for p in all_files if p.name.endswith("ModelSnapshot.cs")]
    if not snaps:
        return None
    files = [p for p in all_files if p.suffix == ".cs" and re.match(r"\d{14}_", p.name) and not p.name.endswith(".Designer.cs")]
    if not files:
        return None
    dialect = None
    for p in all_files:
        if p.suffix == ".csproj":
            t = p.read_text(encoding="utf-8", errors="ignore")
            for key, d in (("Npgsql", "postgres"), ("Pomelo", "mysql"), ("MySql", "mysql"), ("Sqlite", "sqlite"), ("SqlServer", "mssql")):
                if key in t:
                    dialect = d
                    break
    return candidate("efcore", root, {p.parent for p in files}, files, dialect, snaps[0],
                     notes=["EF Core: the C# model is the source of truth; migrations carry a Designer and a shared ModelSnapshot"])


def detect_doctrine(root, all_files):
    comp = root / "composer.json"
    if not comp.exists() or not grep(comp, r"doctrine/(doctrine-)?migrations"):
        return None
    files = [p for p in all_files if p.suffix == ".php" and re.match(r"Version\d{14}\.php", p.name)]
    if not files:
        return None
    dialect = dialect_from_url(env_value(root, "DATABASE_URL"))
    return candidate("doctrine", root, {p.parent for p in files}, files, dialect, None)


def detect_typeorm(root, all_files):
    files = [p for p in all_files if p.suffix in (".ts", ".js") and re.match(r"\d{13}-", p.name) and grep(p, r"MigrationInterface")]
    if not files:
        return None
    dialect = None
    for p in all_files:
        if p.suffix in (".ts", ".js", ".json") and re.search(r"(data-source|ormconfig|typeorm)", p.name, re.IGNORECASE):
            m = grep(p, r"type['\"]?\s*:\s*['\"](postgres|mysql|mariadb|sqlite|better-sqlite3|mssql)['\"]")
            if m:
                dialect = dialect_from_url(m.group(1))
                break
    return candidate("typeorm", root, {p.parent for p in files}, files, dialect, None,
                     notes=["NestJS projects use TypeORM through @nestjs/typeorm; the DataSource file lists the migrations glob"])


def detect_prisma(root, all_files):
    locks = [p for p in all_files if p.name == "migration_lock.toml"]
    if not locks:
        return None
    mig_dir = locks[0].parent
    files = sorted(p for p in mig_dir.glob("*/migration.sql"))
    if not files:
        return None
    m = grep(locks[0], r"provider\s*=\s*['\"](\w+)['\"]")
    dialect = dialect_from_url(m.group(1)) if m else None
    schema = next((p for p in all_files if p.name == "schema.prisma"), None)
    ordered = [{"path": rel(root, p), "version": (parse_version("ts14", p.parent.name) and format_version("ts14", parse_version("ts14", p.parent.name)))}
               for p in sorted(files, key=lambda p: p.parent.name)]
    return candidate("prisma", root, [mig_dir], files, dialect, schema, ordered=ordered,
                     notes=["schema.prisma is the source of truth; `prisma migrate diff --from-empty --to-schema-datamodel` gives exact SQL"])


def detect_flyway(root, all_files):
    files = [p for p in all_files if p.suffix == ".sql" and re.match(r"[VRU]\d*__", p.name) and ("migration" in p.parts or "db" in p.parts)]
    if not files:
        return None
    dialect = _spring_dialect(root, all_files)
    versioned = [p for p in files if p.name.startswith("V")]
    repeat = [p for p in files if p.name.startswith("R")]

    def vkey(p):
        return [int(x) for x in re.findall(r"\d+", p.name.split("__")[0])]
    ordered = [{"path": rel(root, p), "version": p.name.split("__")[0][1:]} for p in sorted(versioned, key=vkey)]
    ordered += [{"path": rel(root, p), "version": None, "repeatable": True} for p in sorted(repeat, key=lambda p: p.name)]
    return candidate("flyway", root, {p.parent for p in files}, files, dialect, None, ordered=ordered,
                     notes=["repeatable R__ migrations run after all versioned ones, ordered by description"])


def _spring_dialect(root, all_files):
    for p in all_files:
        if p.name.startswith("application") and p.suffix in (".properties", ".yml", ".yaml"):
            m = grep(p, r"jdbc:(postgresql|mysql|mariadb|sqlite|sqlserver|h2)")
            if m:
                return dialect_from_url(m.group(1)) or m.group(1)
    return None


def detect_liquibase(root, all_files):
    masters = [p for p in all_files if re.match(r"db\.changelog-master\.(xml|ya?ml|json|sql)", p.name) or p.name in ("changelog.xml", "master.xml")]
    if not masters:
        return None
    master = masters[0]
    text = master.read_text(encoding="utf-8", errors="ignore")
    includes = re.findall(r"(?:file\s*[:=]\s*['\"]?)([^'\"\s,>]+)", text)
    ordered = []
    for inc in includes:
        p = (master.parent / Path(inc).name)
        cand = next((f for f in all_files if f.name == Path(inc).name), p)
        ordered.append({"path": rel(root, cand) if cand.exists() else inc, "version": None})
    if "includeAll" in text:
        files = sorted(p for p in master.parent.rglob("*") if p.is_file() and p != master and p.suffix in (".xml", ".yml", ".yaml", ".json", ".sql"))
        ordered = [{"path": rel(root, p), "version": None} for p in files]
    return candidate("liquibase", root, [master.parent], [root / o["path"] for o in ordered if (root / o["path"]).exists()],
                     _spring_dialect(root, all_files), None, ordered=ordered,
                     notes=[f"master changelog: {rel(root, master)}; order follows its includes"])


def detect_knex(root, all_files):
    kf = next((p for p in all_files if p.name.startswith("knexfile.")), None)
    if not kf:
        return None
    m = grep(kf, r"directory\s*:\s*['\"]([^'\"]+)['\"]")
    mig_dir = (root / m.group(1)).resolve() if m else root / "migrations"
    files = [p for p in all_files if p.suffix in (".js", ".ts", ".mjs", ".cjs") and mig_dir in p.parents and parse_version("ts14", p.name)]
    if not files:
        return None
    c = grep(kf, r"client\s*:\s*['\"]([\w-]+)['\"]")
    dialect = dialect_from_url(c.group(1)) if c else None
    return candidate("knex", root, [mig_dir], files, dialect, None,
                     notes=["Knex refuses to run when an applied row has no file: the baseline step is mandatory"])


def detect_sequelize(root, all_files):
    rc = root / ".sequelizerc"
    files = [p for p in all_files if p.suffix in (".js", ".ts", ".cjs") and p.parent.name == "migrations" and grep(p, r"queryInterface")]
    if not files:
        return None
    dialect = None
    cfg = next((p for p in all_files if p.name in ("config.json", "config.js") and p.parent.name == "config"), None)
    if cfg:
        m = grep(cfg, r"dialect['\"]?\s*:\s*['\"](\w+)['\"]")
        dialect = dialect_from_url(m.group(1)) if m else None
    return candidate("sequelize", root, {p.parent for p in files}, files, dialect, None,
                     confidence="high" if rc.exists() else "medium")


def detect_drizzle(root, all_files):
    cfg = next((p for p in all_files if p.name.startswith("drizzle.config.")), None)
    journals = [p for p in all_files if p.name == "_journal.json" and p.parent.name == "meta"]
    if not cfg and not journals:
        return None
    out_dir = journals[0].parent.parent if journals else root / "drizzle"
    ordered = []
    if journals:
        try:
            j = json.loads(journals[0].read_text(encoding="utf-8"))
            for e in j.get("entries", []):
                ordered.append({"path": rel(root, out_dir / f"{e['tag']}.sql"), "version": str(e.get("idx")).zfill(4), "when": e.get("when")})
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    files = [root / o["path"] for o in ordered if (root / o["path"]).exists()]
    dialect, snapshot = None, None
    if cfg:
        m = grep(cfg, r"dialect\s*:\s*['\"](\w+)['\"]")
        dialect = dialect_from_url(m.group(1)) if m else None
        s = grep(cfg, r"schema\s*:\s*['\"]([^'\"]+)['\"]")
        snapshot = s.group(1) if s else None
    return candidate("drizzle", root, [out_dir], files, dialect, snapshot, ordered=ordered,
                     notes=["the TypeScript schema is the source of truth; meta/_journal.json drives the order"])


def detect_goose(root, all_files):
    files = [p for p in all_files if p.suffix in (".sql", ".go") and grep(p, r"\+goose Up")]
    if not files:
        return None
    dialect = None
    for src in (root / "Makefile", root / ".env", root / "go.mod"):
        if src.exists():
            t = src.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"GOOSE_DRIVER\s*[=:]\s*(\w+)", t)
            if m:
                dialect = dialect_from_url(m.group(1))
                break
            if src.name == "go.mod":
                for key, d in (("lib/pq", "postgres"), ("jackc/pgx", "postgres"), ("go-sql-driver/mysql", "mysql"), ("mattn/go-sqlite3", "sqlite"), ("modernc.org/sqlite", "sqlite")):
                    if key in t:
                        dialect = d
                        break
    sql = [p for p in files if p.suffix == ".sql"]
    style = "seq" if sql and all(re.match(r"\d{1,8}_", p.name) and not parse_version("ts14", p.name) for p in sql) else "ts14"
    ordered = sort_by_version(root, files, style)
    c = candidate("goose", root, {p.parent for p in files}, files, dialect, None, ordered=ordered,
                  notes=["goose refuses versions lower than the current max unless -allow-missing; numbering continues after the last one"])
    c["versioning"] = style
    return c


def detect_atlas(root, all_files):
    sums = [p for p in all_files if p.name == "atlas.sum"]
    if not sums:
        return None
    mig_dir = sums[0].parent
    files = sorted(p for p in mig_dir.glob("*.sql"))
    dialect = None
    hcl = next((p for p in all_files if p.name == "atlas.hcl"), None)
    if hcl:
        m = grep(hcl, r"(?:url|dev)\s*=\s*['\"]([^'\"]+)['\"]")
        dialect = dialect_from_url(m.group(1)) if m else None
    snap = next((p for p in all_files if p.name in ("schema.hcl", "schema.sql") and p.parent != mig_dir), None)
    return candidate("atlas", root, [mig_dir], files, dialect, snap, notes=["run `atlas migrate hash` after writing files"])


def detect_diesel(root, all_files):
    if not (root / "diesel.toml").exists() and not (root / "migrations").exists():
        return None
    ups = [p for p in all_files if p.name == "up.sql" and p.parent.parent.name == "migrations"]
    if not ups:
        return None
    dialect = None
    cargo = root / "Cargo.toml"
    if cargo.exists():
        t = cargo.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"diesel[^\n]*features\s*=\s*\[([^\]]*)\]", t)
        if m:
            dialect = dialect_from_url(m.group(1))
    ordered = [{"path": rel(root, p), "version": re.sub(r"\D", "", p.parent.name.split("_")[0])} for p in sorted(ups, key=lambda p: p.parent.name)]
    snap = root / "src" / "schema.rs"
    return candidate("diesel", root, {p.parent.parent for p in ups}, ups, dialect, snap if snap.exists() else None, ordered=ordered,
                     notes=["keep 00000000000000_diesel_initial_setup as is; run `diesel print-schema` afterwards"])


def detect_sqlx(root, all_files):
    cargo = root / "Cargo.toml"
    if not cargo.exists() or "sqlx" not in cargo.read_text(encoding="utf-8", errors="ignore"):
        return None
    files = [p for p in all_files if p.suffix == ".sql" and p.parent.name == "migrations" and re.match(r"\d+_", p.name)]
    if not files:
        return None
    t = cargo.read_text(encoding="utf-8", errors="ignore")
    dialect = dialect_from_url(env_value(root, "DATABASE_URL")) or next((d for k, d in (("postgres", "postgres"), ("mysql", "mysql"), ("sqlite", "sqlite")) if k in t), None)
    ups = [p for p in files if not p.name.endswith(".down.sql")]
    style = "ts14" if all(parse_version("ts14", p.name) for p in ups) else "seq"
    c = candidate("sqlx", root, {p.parent for p in files}, ups, dialect, None, ordered=sort_by_version(root, ups, style),
                  notes=["reversible pairs: .up.sql / .down.sql", "sqlx migrate run fails on missing applied versions and on checksum mismatch"])
    c["versioning"] = style
    return c


DETECTORS = [detect_laravel, detect_django, detect_rails, detect_ecto, detect_efcore, detect_doctrine, detect_typeorm, detect_prisma,
             detect_flyway, detect_liquibase, detect_knex, detect_sequelize, detect_drizzle, detect_goose, detect_atlas, detect_diesel, detect_sqlx]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("-o", "--out", help="write stack.json here (default: print only)")
    args = ap.parse_args()
    root = Path(args.repo).resolve()
    if not root.is_dir():
        sys.exit(f"error: {root} is not a directory")
    all_files = [p for p in walk(root) if p.is_file()]
    candidates = []
    for det in DETECTORS:
        try:
            c = det(root, all_files)
        except Exception as e:  # a detector must never take the others down
            sys.stderr.write(f"warning: {det.__name__} failed: {e}\n")
            c = None
        if c:
            candidates.append(c)
    # Goose files also match sqlx/flyway patterns occasionally; prefer the more specific one
    names = {c["framework"] for c in candidates}
    if "goose" in names:
        candidates = [c for c in candidates if c["framework"] not in ("sqlx", "flyway") or c["files"] != next(x for x in candidates if x["framework"] == "goose")["files"]]
    result = {"repo": str(root), "candidates": candidates}
    if not candidates:
        result["notes"] = ["no supported migration framework detected; ask the user where the migrations live and which tool runs them"]
    dump_json(result, args.out)
    if args.out:
        for c in candidates:
            print(f"{c['framework']:<10} {c['confidence']:<6} {c['count']:>4} files  dialect={c['dialect']}  dirs={', '.join(c['migrations_dirs'])}")
            for mf in c["multi_create_files"]:
                print(f"           creates {mf['creates']} tables: {mf['path']}")
        if not candidates:
            print("no supported migration framework detected")


if __name__ == "__main__":
    main()
