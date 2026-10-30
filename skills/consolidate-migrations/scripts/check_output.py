#!/usr/bin/env python3
"""Static check of the written migration files against the plan and the manifest.

Usage:
    check_output.py <plan.json> <manifest.json> <output-dir> [--strict-extra]

No database is involved. The check is textual: every object the manifest knows about
must be mentioned in the file the plan assigned to it, and no file may mention a table
whose file comes later in the order. It cannot prove the SQL is right, but it catches
the common consolidation mistakes: a column lost while merging alters, a foreign key
written before its target exists, an index forgotten, an extra or missing file, a kept
file that lost its original's name, a new file that reuses an original's version.

Exit code 1 when any error is found. Warnings do not fail the check.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schemalib import FRAMEWORKS, load_json, qualified  # noqa: E402


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# DSL helpers that create columns without naming them; the checker accepts the helper as a mention.
HELPERS = [
    (r"->(timestamps|timestampsTz|nullableTimestamps)\(", ["created_at", "updated_at"]),      # Laravel
    (r"->softDeletes(Tz)?\(", ["deleted_at"]),
    (r"->id\(\s*\)", ["id"]),
    (r"->(uuid|ulid)\(\s*\)", ["uuid", "ulid"]),
    (r"->rememberToken\(", ["remember_token"]),
    (r"t\.timestamps\b", ["created_at", "updated_at"]),                                      # Rails
    (r"\btimestamps\(\)", ["inserted_at", "updated_at", "created_at"]),                       # Ecto (and Laravel outside ->)
    (r"\bid\s*(bigserial|serial|INTEGER PRIMARY KEY|uuid)", ["id"]),
]


def helper_columns(text: str) -> set[str]:
    cols: set[str] = set()
    for pattern, names in HELPERS:
        if re.search(pattern, text):
            cols.update(names)
    for m in re.finditer(r"->(nullable)?(uuid|ulid)?[mM]orphs\(\s*['\"](\w+)['\"]", text):           # Laravel morphs
        cols.update({m.group(3) + "_type", m.group(3) + "_id"})
    for m in re.finditer(r"t\.references\s+:(\w+)|t\.belongs_to\s+:(\w+)", text):                 # Rails references
        cols.add((m.group(1) or m.group(2)) + "_id")
    return cols


def strip_comments(text: str) -> str:
    """Remove block and line comments so a header note never counts as DDL."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r'"""[^"]*?"""', lambda m: m.group(0) if "CREATE" in m.group(0).upper() or "SELECT" in m.group(0).upper() else "", text, flags=re.S)
    out = []
    for line in text.splitlines():
        if re.match(r"\s*(--|#|//)", line):
            continue
        out.append(re.sub(r"\s+(--|//|#)\s[^'\"]*$", "", line))
    return "\n".join(out)


def implied_fk(text: str, fk: dict) -> bool:
    """Laravel `foreignId('x_id')->constrained()` / Rails `t.references :x, foreign_key: true` name no table."""
    for col in fk.get("columns", []):
        if re.search(r"foreignId(For)?\(\s*['\"]" + re.escape(col) + r"['\"][^;]*->constrained\(\s*\)", text):
            return True
        base = col[:-3] if col.endswith("_id") else col
        if re.search(r"t\.(references|belongs_to)\s+:" + re.escape(base) + r"\b[^\n]*foreign_key", text):
            return True
    return False


def mentions(text: str, token: str) -> bool:
    if not token:
        return True
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, text) is not None or re.search(pattern, text, re.IGNORECASE) is not None


DIR_MAP: list = []


def entry_paths(entry: dict, out_dir: Path, fw: dict) -> list[Path]:
    d = entry.get("dir")
    if d:
        parts = d.split("/")
        for frm, to in DIR_MAP:
            parts = [to if x == frm else x for x in parts]
        d = "/".join(parts)
    base = out_dir / d if d else out_dir
    paths = [base / entry["file"]]
    if entry.get("down_file"):
        paths.append(base / entry["down_file"])
    return paths


def check_table(seq, rel, name, text, tables, fw, seq_of_table, deferred_by_table, sqlite, errors, warnings):
    t = tables.get(name)
    if t is None:
        errors.append(f"[{seq}] {rel}: table '{name}' is in the plan but not in the manifest")
        return
    model = t.get("model")
    if t.get("implicit_m2m_of"):
        # created by the owner's ManyToManyField (inside its CreateModel, or an AddField); only that field can be checked
        if not re.search(r"ManyToManyField|has_and_belongs_to_many|many_to_many", text):
            errors.append(f"[{seq}] {rel}: implicit join table '{t['name']}' but no ManyToManyField in this file")
        return
    if not (mentions(text, t["name"]) or (model and mentions(text, model))):
        errors.append(f"[{seq}] {rel}: does not mention table '{t['name']}'" + (f" or model '{model}'" if model else ""))
    implied = helper_columns(text)
    deferred_cols = {c for fk in t.get("foreign_keys", []) if fk.get("deferred") or (fk.get("name") or ",".join(fk["columns"])) in deferred_by_table.get(name, set()) for c in fk["columns"]}
    for c in t.get("columns", []):
        if c["name"] in deferred_cols and fw.get("per_app"):
            continue   # Django: column and FK are one AddField in the deferred file
        if not (mentions(text, c["name"]) or (c.get("attr") and mentions(text, c["attr"])) or c["name"] in implied):
            errors.append(f"[{seq}] {rel}: column '{t['name']}.{c['name']}' not found")
    for idx in t.get("indexes", []):
        token = idx.get("name")
        cols_ok = all(mentions(text, c) or c in implied for c in idx.get("columns", []))
        if token and not mentions(text, token) and not cols_ok:
            errors.append(f"[{seq}] {rel}: index '{token}' not found")
        elif token and not mentions(text, token):
            warnings.append(f"[{seq}] {rel}: index name '{token}' not found (columns are present; the framework may generate a different name)")
    for fk in t.get("foreign_keys", []):
        label = fk.get("name") or ",".join(fk["columns"])
        if fk.get("external"):
            continue   # target is outside the consolidated scope (auth tables, another service)
        if fk.get("deferred") or label in deferred_by_table.get(name, set()):
            if mentions(text, fk["ref_table"]) and fk.get("name") and mentions(text, fk["name"]):
                warnings.append(f"[{seq}] {rel}: deferred FK '{label}' seems to be written inline too")
            continue
        ref_model = tables.get(qualified(fk["ref_table"], fk.get("ref_schema")), {}).get("model")
        if not mentions(text, fk["ref_table"]) and not implied_fk(text, fk) and not (ref_model and mentions(text, ref_model)) \
                and not re.search(r"settings\.AUTH_USER_MODEL", text):
            errors.append(f"[{seq}] {rel}: foreign key '{label}' -> '{fk['ref_table']}' not found")
        ref_seq = seq_of_table.get(qualified(fk["ref_table"], fk.get("ref_schema")))
        if ref_seq is not None and ref_seq > seq:
            msg = f"[{seq}] {rel}: foreign key '{label}' references '{fk['ref_table']}' whose file comes later (#{ref_seq})"
            if sqlite:
                warnings.append(msg + " (fine on SQLite: FK targets are not validated at CREATE time)")
            else:
                errors.append(msg)
    for chk in t.get("checks", []):
        if chk.get("name") and not mentions(text, chk["name"]):
            expr_hint = (chk.get("expression") or "")[:30]
            if not (expr_hint and expr_hint.split(" ")[0] in text):
                errors.append(f"[{seq}] {rel}: check '{chk['name']}' not found")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("manifest")
    ap.add_argument("out_dir")
    ap.add_argument("--strict-extra", action="store_true", help="treat files not in the plan as errors instead of warnings")
    ap.add_argument("--dir-map", action="append", default=[], metavar="FROM=TO",
                    help="rewrite a path segment of the plan's per-entry dir, e.g. migrations=migrations.consolidated for per-app frameworks staged in a sibling directory")
    args = ap.parse_args()
    dir_map = [a.split("=", 1) for a in args.dir_map if "=" in a]

    DIR_MAP.extend(dir_map)
    plan = load_json(args.plan)
    m = load_json(args.manifest)
    out_dir = Path(args.out_dir)
    fw = FRAMEWORKS[plan["framework"]]
    sqlite = (m.get("dialect") or plan.get("dialect")) == "sqlite"
    errors, warnings = [], []

    tables = {qualified(t["name"], t.get("schema")): t for t in m.get("tables", [])}
    views = {qualified(v["name"], v.get("schema")): v for v in m.get("views", [])}
    entries = plan["entries"]
    if "originals" not in plan:
        print("error: plan.json has no 'originals': re-run plan_order.py with this skill's version")
        sys.exit(1)
    seq_of_table, seq_of_view, deferred_by_table = {}, {}, {}
    for e in entries:
        for t in e.get("tables", []):
            q = qualified(t["name"], t.get("schema"))
            seq_of_table[q] = e["seq"]
            deferred_by_table[q] = set(t.get("deferred_fks", []))
            for implicit in t.get("implicit_tables", []):
                seq_of_table[implicit] = e["seq"]
        for v in e.get("views", []):
            seq_of_view[qualified(v["name"], v.get("schema"))] = e["seq"]

    # names: a kept file must carry its original's exact name; a new file must not reuse one
    def norm(v):
        v = re.sub(r"[-_]", "", str(v))
        return str(int(v)) if v.isdigit() else v

    orig_versions = {norm(o["version"]) for o in plan["originals"] if o.get("version")}
    orig_names = {Path(o["path"]).name if Path(o["path"]).name not in ("migration.sql", "up.sql") else Path(o["path"]).parent.name
                  for o in plan["originals"]}
    for e in entries:
        loc = (e["dir"] + "/" if e.get("dir") else "") + e["file"]
        if fw.get("single_migration"):
            if e.get("container") and Path(e["container"]).stem != Path(e["original"]).stem:
                errors.append(f"[{e['seq']}] {loc}: the migration must keep the first original's id {Path(e['original']).stem}, plan says {e['container']}")
        elif e.get("kept"):
            if not e["original"].endswith("/" + loc) and e["original"] != loc and Path(e["original"]).name != Path(loc).name:
                errors.append(f"[{e['seq']}] {loc}: kept file is not named like its original {e['original']}")
        else:
            name = Path(e["file"]).name if Path(e["file"]).name not in ("migration.sql", "up.sql") else Path(e["file"]).parent.name
            if name in orig_names:
                errors.append(f"[{e['seq']}] {loc}: new file reuses the name of an original")
            if norm(e.get("version")) in orig_versions and not fw.get("per_app"):
                errors.append(f"[{e['seq']}] {loc}: new file reuses version {e['version']} of an original (it would look already applied)")

    planned_files = set()
    texts = {}
    for e in entries:
        paths = entry_paths(e, out_dir, fw)
        for p in paths:
            planned_files.add(p.resolve())
        main_path = paths[0]
        if not main_path.exists():
            errors.append(f"[{e['seq']}] missing file: {main_path}")
            continue
        for p in paths[1:]:
            if not p.exists():
                errors.append(f"[{e['seq']}] missing file: {p}")
        text = strip_comments("".join(read(p) for p in paths))
        texts[e["seq"]] = (e, text, main_path)
    containers = sorted({e["container"] for e in entries if e.get("container")})
    for c in containers:
        for p in (out_dir / c, out_dir / c.replace(".cs", ".Designer.cs")):
            planned_files.add(p.resolve())
            if not p.exists():
                errors.append(f"missing file: {p}")

    # per-member content checks
    for seq, (e, text, path) in texts.items():
        rel = path.relative_to(out_dir) if path.is_relative_to(out_dir) else path
        for t in e.get("tables", []):
            q = qualified(t["name"], t.get("schema"))
            for name in [q] + list(t.get("implicit_tables", [])):
                check_table(seq, rel, name, text, tables, fw, seq_of_table, deferred_by_table, sqlite, errors, warnings)
        for ve in e.get("views", []):
            v = views.get(qualified(ve["name"], ve.get("schema")))
            if v is None:
                errors.append(f"[{seq}] {rel}: view '{ve['name']}' is in the plan but not in the manifest")
            elif not mentions(text, v["name"]):
                errors.append(f"[{seq}] {rel}: does not mention view '{v['name']}'")
            for d in ve.get("depends_on", []):
                kind, _, target = d.partition(":")
                ref_seq = (seq_of_table if kind == "table" else seq_of_view if kind == "view" else {}).get(target)
                if ref_seq is not None and ref_seq > seq:
                    errors.append(f"[{seq}] {rel}: view reads '{target}' whose file comes later (#{ref_seq})")
        for member in e.get("prerequisites", []):
            _, _, name = member.partition(":")
            if not mentions(text, name.split(".")[-1]):
                errors.append(f"[{seq}] {rel}: prerequisite '{member}' not found")
        for fn in e.get("functions", []):
            if not mentions(text, fn):
                errors.append(f"[{seq}] {rel}: function '{fn}' not found")
        for trg in e.get("triggers", []):
            if not mentions(text, trg["trigger"]["name"]):
                errors.append(f"[{seq}] {rel}: trigger '{trg['trigger']['name']}' not found")
        if e["kind"] == "deferred_fks":
            for c in e.get("constraints", []):
                fk = c["fk"]
                tbl = tables.get(qualified(c["table"], c.get("schema")), {})
                ref = tables.get(qualified(fk["ref_table"], fk.get("ref_schema")), {})
                attrs = {col.get("attr") for col in tbl.get("columns", []) if col["name"] in fk["columns"] and col.get("attr")}
                tbl_ok = mentions(text, c["table"]) or (tbl.get("model") and mentions(text, tbl["model"]))
                ref_ok = mentions(text, fk["ref_table"]) or (ref.get("model") and mentions(text, ref["model"]))
                cols_ok = all(mentions(text, col) for col in fk["columns"]) or (attrs and all(mentions(text, a) for a in attrs))
                if not (tbl_ok and ref_ok and cols_ok):
                    errors.append(f"[{seq}] {rel}: deferred FK {c['table']}.{','.join(fk['columns'])} -> {fk['ref_table']} not found")
        if e["kind"] == "seed":
            for d in e.get("data", []):
                for tname in d.get("tables", []):
                    tmodel = tables.get(tname, {}).get("model")
                    if not (mentions(text, tname) or (tmodel and mentions(text, tmodel))):
                        errors.append(f"[{seq}] {rel}: seed for table '{tname}' (from {d.get('file')}) not found")

        if plan["framework"] == "goose" and path.suffix == ".sql":
            text = read(path)   # annotations are comments, so look at the raw file here
            if "+goose Up" not in text:
                errors.append(f"[{seq}] {rel}: missing '-- +goose Up' annotation")
            if "$$" in text and "+goose StatementBegin" not in text:
                errors.append(f"[{seq}] {rel}: a $$ body needs '-- +goose StatementBegin' / 'StatementEnd' around it")
            if text.count("+goose StatementBegin") != text.count("+goose StatementEnd"):
                errors.append(f"[{seq}] {rel}: unbalanced StatementBegin/StatementEnd")

        # mentions of tables created later (raw SQL and views are where this slips in)
        own = {qualified(t["name"], t.get("schema")) for t in e.get("tables", [])}
        fk_targets = {qualified(fk["ref_table"], fk.get("ref_schema")) for q in own for fk in tables.get(q, {}).get("foreign_keys", [])}
        for tname, tseq in seq_of_table.items():
            if tseq > seq and e["kind"] != "deferred_fks" and tname not in fk_targets:
                short = tname.split(".")[-1]
                if mentions(text, short) and not any(short in o for o in own) and short not in (e.get("name") or ""):
                    warnings.append(f"[{seq}] {rel}: mentions table '{short}' whose file comes later (#{tseq}); wrong if it is DDL")

    # manifest objects without a file
    for name in tables:
        if name not in seq_of_table:
            errors.append(f"manifest table '{name}' has no entry in the plan")
    for name in views:
        if name not in seq_of_view:
            errors.append(f"manifest view '{name}' has no entry in the plan")

    # extra files in the output dir
    scan_dirs = {entry_paths(e, out_dir, fw)[0].parent for e in entries}
    if fw.get("folder"):
        scan_dirs = {d.parent for d in scan_dirs}
    for scan in sorted(scan_dirs):
        for p in scan.rglob("*"):
            if p.is_file() and p.resolve() not in planned_files and p.name not in ("migration_lock.toml", "atlas.sum", ".gitkeep", "__init__.py") \
                    and "__pycache__" not in p.parts and p.suffix != ".pyc" \
                    and "/meta/" not in str(p) and "ModelSnapshot" not in p.name and "changelog-master" not in p.name:
                msg = f"file not in the plan: {p.relative_to(out_dir)}"
                (errors if args.strict_extra else warnings).append(msg)

    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s) across {len(entries)} planned files")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
