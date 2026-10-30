#!/usr/bin/env python3
"""Fold per-migration ops files into the final schema manifest.

Usage:
    replay.py <workdir> [-o manifest.json] [--framework KEY] [--dialect D] [--stack stack.json]

<workdir> is the run's working directory (usually .consolidate-migrations/). It must
contain ops/*.json, one per original migration, named so that sorting them by file
name gives the replay order (NNN_<original>.json). If <workdir>/stack.json exists it
is used to fill manifest.source (framework, dialect, dirs, first/last version).

Every surviving object also records the original that created it (`created_in`), and
manifest.source.files lists the originals in replay order with what each one created:
the consolidated files keep the names of those originals, so plan_order.py needs to
know which original each object belongs to.

The fold is strict on purpose: an op that touches a table or column that does not
exist at that point of the history means a transcription mistake (a missed rename,
a skipped file, a typo), and finding it here is far cheaper than finding it after
the consolidated files are written. Errors stop the run with exit code 1; softer
findings become warnings in the manifest.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schemalib import (FRAMEWORKS, die, dump_json, format_version, framework_key, load_json,  # noqa: E402
                       parse_version, qualified, split_qualified)


class ReplayError(Exception):
    pass


class State:
    def __init__(self):
        self.tables: dict[str, dict] = {}       # qualified name -> table
        self.views: dict[str, dict] = {}
        self.types: dict[str, dict] = {}
        self.sequences: dict[str, dict] = {}
        self.functions: dict[str, dict] = {}
        self.extensions: list[str] = []
        self.schemas: list[str] = []
        self.data: list[dict] = []
        self.unresolved: list[dict] = []
        self.warnings: list[str] = []
        self.dropped_tables: list[str] = []
        self.ext_origin: dict[str, tuple[str, int]] = {}      # extension -> (creating file, seq)
        self.schema_origin: dict[str, tuple[str, int]] = {}   # schema -> (creating file, seq)
        self.seq = 0
        self.file = ""

    # -- helpers ------------------------------------------------------------
    def warn(self, msg: str):
        self.warnings.append(f"{self.file}: {msg}")

    def key(self, op: dict, field: str = "table") -> str:
        schema, name = split_qualified(op[field], op.get("schema") if field == "table" else None)
        return qualified(name, schema)

    def table(self, op: dict, field: str = "table") -> dict:
        k = self.key(op, field)
        if k not in self.tables:
            hint = ""
            if k in self.dropped_tables:
                hint = " (it was dropped earlier)"
            raise ReplayError(f"table '{k}' does not exist at this point{hint}")
        return self.tables[k]

    @staticmethod
    def column(table: dict, name: str) -> dict:
        for c in table.get("columns", []):
            if c["name"] == name:
                return c
        raise ReplayError(f"column '{name}' does not exist on table '{table['name']}'")

    @staticmethod
    def has_column(table: dict, name: str) -> bool:
        return any(c["name"] == name for c in table.get("columns", []))

    @staticmethod
    def normalize_table(t: dict) -> dict:
        t = copy.deepcopy(t)
        schema, name = split_qualified(t["name"], t.get("schema"))
        t["name"], t["schema"] = name, schema
        for key in ("columns", "indexes", "checks", "foreign_keys", "triggers", "depends_on"):
            t.setdefault(key, [])
        t.setdefault("options", {})
        t.setdefault("primary_key", None)
        if t["primary_key"] is None:
            pk = [c["name"] for c in t["columns"] if c.get("primary")]
            if pk:
                t["primary_key"] = {"name": None, "columns": pk}
        for fk in t["foreign_keys"]:
            rs, rn = split_qualified(fk["ref_table"], fk.get("ref_schema"))
            fk["ref_table"], fk["ref_schema"] = rn, rs
        t.setdefault("renamed_from", [])
        t.setdefault("folded_from", [])
        return t

    def touch(self, table: dict):
        if self.file not in table["folded_from"]:
            table["folded_from"].append(self.file)

    def born(self, obj: dict):
        """Mark obj as created by the current file; the seq keeps the creation order inside a file."""
        self.seq += 1
        obj["created_in"], obj["_seq"] = self.file, self.seq

    # -- ops ------------------------------------------------------------------
    def apply(self, op: dict):
        name = op.get("op")
        handler = getattr(self, f"op_{name}", None)
        if handler is None:
            raise ReplayError(f"unknown op '{name}'")
        handler(op)
        if op.get("note"):
            self.warnings.append(f"{self.file}: note on {name}: {op['note']}")

    def op_create_table(self, op):
        t = self.normalize_table(op["table"])
        k = qualified(t["name"], t["schema"])
        if k in self.tables:
            raise ReplayError(f"table '{k}' already exists")
        self.tables[k] = t
        self.touch(t)
        self.born(t)
        for trg in t["triggers"]:
            self.born(trg)
            trg["defined_in"] = self.file
        for fk in t["foreign_keys"]:
            ref = qualified(fk["ref_table"], fk.get("ref_schema"))
            if ref not in self.tables and not fk.get("external"):
                raise ReplayError(f"table '{k}' has a foreign key to unknown table '{ref}' (set \"external\": true if it lives outside the consolidated scope)")

    def op_drop_table(self, op):
        k = self.key(op)
        self.table(op)
        del self.tables[k]
        self.dropped_tables.append(k)
        for other in self.tables.values():
            kept = []
            for fk in other["foreign_keys"]:
                if qualified(fk["ref_table"], fk.get("ref_schema")) == k:
                    self.warn(f"foreign key {fk.get('name') or fk['columns']} on '{other['name']}' pointed at dropped table '{k}'; dropped it")
                else:
                    kept.append(fk)
            other["foreign_keys"] = kept
        for v in self.views.values():
            if f"table:{k}" in v.get("depends_on", []):
                self.warn(f"view '{v['name']}' depended on dropped table '{k}'")

    def op_rename_table(self, op):
        t = self.table(op)
        old = qualified(t["name"], t["schema"])
        new_schema, new_name = split_qualified(op["to"], op.get("to_schema") or t["schema"])
        new = qualified(new_name, new_schema)
        if new in self.tables:
            raise ReplayError(f"cannot rename '{old}' to '{new}': target exists")
        del self.tables[old]
        t["renamed_from"].append(t["name"])
        t["name"], t["schema"] = new_name, new_schema
        self.tables[new] = t
        self.touch(t)
        for other in self.tables.values():
            for fk in other["foreign_keys"]:
                if qualified(fk["ref_table"], fk.get("ref_schema")) == old:
                    fk["ref_table"], fk["ref_schema"] = new_name, new_schema
        for v in self.views.values():
            v["depends_on"] = [f"table:{new}" if d == f"table:{old}" else d for d in v.get("depends_on", [])]

    def op_add_column(self, op):
        t = self.table(op)
        col = copy.deepcopy(op["column"])
        if self.has_column(t, col["name"]):
            raise ReplayError(f"column '{col['name']}' already exists on '{t['name']}'")
        after = op.get("after")
        if after and self.has_column(t, after):
            idx = [c["name"] for c in t["columns"]].index(after) + 1
            t["columns"].insert(idx, col)
        else:
            t["columns"].append(col)
        if col.get("primary"):
            t["primary_key"] = {"name": None, "columns": [col["name"]]}
        self.touch(t)

    def op_drop_column(self, op):
        t = self.table(op)
        name = op["column"]
        self.column(t, name)
        t["columns"] = [c for c in t["columns"] if c["name"] != name]
        for kind in ("indexes", "foreign_keys"):
            kept = []
            for item in t[kind]:
                if name in item.get("columns", []):
                    self.warn(f"{kind[:-1]} {item.get('name') or item['columns']} on '{t['name']}' used dropped column '{name}'; dropped it")
                else:
                    kept.append(item)
            t[kind] = kept
        for chk in t["checks"]:
            if re.search(rf"\b{re.escape(name)}\b", chk.get("expression", "")):
                self.warn(f"check {chk.get('name')} on '{t['name']}' mentions dropped column '{name}'; review it")
        if t.get("primary_key") and name in t["primary_key"]["columns"]:
            t["primary_key"]["columns"].remove(name)
            if not t["primary_key"]["columns"]:
                t["primary_key"] = None
        self.touch(t)

    def op_rename_column(self, op):
        t = self.table(op)
        col = self.column(t, op["column"])
        old, new = col["name"], op["to"]
        if self.has_column(t, new):
            raise ReplayError(f"cannot rename column '{old}' to '{new}' on '{t['name']}': target exists")
        col["name"] = new
        for kind in ("indexes", "foreign_keys"):
            for item in t[kind]:
                item["columns"] = [new if c == old else c for c in item.get("columns", [])]
        if t.get("primary_key"):
            t["primary_key"]["columns"] = [new if c == old else c for c in t["primary_key"]["columns"]]
        for chk in t["checks"]:
            if re.search(rf"\b{re.escape(old)}\b", chk.get("expression", "")):
                chk["expression"] = re.sub(rf"\b{re.escape(old)}\b", new, chk["expression"])
                self.warn(f"rewrote check {chk.get('name')} on '{t['name']}' for column rename {old} -> {new}; review it")
        for other in self.tables.values():
            for fk in other["foreign_keys"]:
                if qualified(fk["ref_table"], fk.get("ref_schema")) == qualified(t["name"], t["schema"]):
                    fk["ref_columns"] = [new if c == old else c for c in fk.get("ref_columns", [])]
        self.touch(t)

    def op_alter_column(self, op):
        t = self.table(op)
        col = self.column(t, op["column"])
        changes = dict(op.get("changes", {}))
        if changes.pop("clear_default", False):
            col["default"] = None
        for k, v in changes.items():
            if k == "default" and v is None:
                continue
            col[k] = v
        self.touch(t)

    def op_add_index(self, op):
        t = self.table(op)
        idx = copy.deepcopy(op["index"])
        for c in idx.get("columns", []):
            if not self.has_column(t, c) and not idx.get("raw"):
                raise ReplayError(f"index {idx.get('name') or idx['columns']} on '{t['name']}' uses unknown column '{c}'")
        if idx.get("name") and any(i.get("name") == idx["name"] for i in t["indexes"]):
            self.warn(f"index '{idx['name']}' on '{t['name']}' created twice; keeping the later definition")
            t["indexes"] = [i for i in t["indexes"] if i.get("name") != idx["name"]]
        t["indexes"].append(idx)
        self.touch(t)

    def op_drop_index(self, op):
        t = self.table(op)
        self._drop_item(t, "indexes", op)

    def op_add_foreign_key(self, op):
        t = self.table(op)
        fk = copy.deepcopy(op["foreign_key"])
        rs, rn = split_qualified(fk["ref_table"], fk.get("ref_schema"))
        fk["ref_table"], fk["ref_schema"] = rn, rs
        for c in fk.get("columns", []):
            if not self.has_column(t, c):
                raise ReplayError(f"foreign key {fk.get('name') or fk['columns']} on '{t['name']}' uses unknown column '{c}'")
        ref = qualified(rn, rs)
        if ref not in self.tables and ref != qualified(t["name"], t["schema"]) and not fk.get("external"):
            raise ReplayError(f"foreign key on '{t['name']}' references unknown table '{ref}' (set \"external\": true if it lives outside the consolidated scope)")
        t["foreign_keys"].append(fk)
        self.touch(t)

    def op_drop_foreign_key(self, op):
        t = self.table(op)
        self._drop_item(t, "foreign_keys", op)

    def op_add_check(self, op):
        t = self.table(op)
        t["checks"].append(copy.deepcopy(op["check"]))
        self.touch(t)

    def op_drop_check(self, op):
        t = self.table(op)
        self._drop_item(t, "checks", op)

    def _drop_item(self, t: dict, kind: str, op: dict):
        name, cols = op.get("name"), op.get("columns")
        before = len(t[kind])
        if name:
            t[kind] = [i for i in t[kind] if i.get("name") != name]
        elif cols:
            t[kind] = [i for i in t[kind] if sorted(i.get("columns", [])) != sorted(cols)]
        else:
            raise ReplayError(f"drop_{kind[:-1] if kind != 'indexes' else 'index'} needs 'name' or 'columns'")
        if len(t[kind]) == before:
            raise ReplayError(f"no {kind[:-1]} named {name or cols} on '{t['name']}' to drop")
        self.touch(t)

    def op_set_primary_key(self, op):
        t = self.table(op)
        pk = op.get("primary_key")
        if pk:
            for c in pk.get("columns", []):
                self.column(t, c)
        t["primary_key"] = copy.deepcopy(pk)
        for c in t["columns"]:
            c["primary"] = bool(pk) and c["name"] in pk["columns"] and len(pk["columns"]) == 1
        self.touch(t)

    def op_set_comment(self, op):
        t = self.table(op)
        if op.get("column"):
            self.column(t, op["column"])["comment"] = op.get("text")
        else:
            t["comment"] = op.get("text")
        self.touch(t)

    def op_set_options(self, op):
        t = self.table(op)
        t["options"].update(op.get("options", {}))
        self.touch(t)

    def op_set_table_meta(self, op):
        t = self.table(op)
        for k in ("app", "model", "polymorphic", "implicit_m2m_of", "comment"):
            if k in op:
                t[k] = op[k]
        if "depends_on" in op:
            for d in op["depends_on"]:
                if d not in t["depends_on"]:
                    t["depends_on"].append(d)
        self.touch(t)

    def op_create_view(self, op):
        v = copy.deepcopy(op["view"])
        schema, name = split_qualified(v["name"], v.get("schema"))
        v["name"], v["schema"] = name, schema
        v.setdefault("depends_on", [])
        v.setdefault("materialized", False)
        v.setdefault("indexes", [])
        k = qualified(name, schema)
        if k in self.views:
            self.warn(f"view '{k}' redefined; keeping the later definition")
        for d in v["depends_on"]:
            kind, _, target = d.partition(":")
            if kind == "table" and target not in self.tables:
                raise ReplayError(f"view '{k}' depends on unknown table '{target}'")
            if kind == "view" and target not in self.views:
                raise ReplayError(f"view '{k}' depends on unknown view '{target}'")
        v.setdefault("folded_from", [])
        if k in self.views:
            v["folded_from"] = self.views[k]["folded_from"] + v["folded_from"]
            v["created_in"], v["_seq"] = self.views[k]["created_in"], self.views[k]["_seq"]
        else:
            self.born(v)
        v["folded_from"].append(self.file)
        v["defined_in"] = self.file
        self.views[k] = v

    def op_drop_view(self, op):
        schema, name = split_qualified(op["name"], op.get("schema"))
        k = qualified(name, schema)
        if k not in self.views:
            raise ReplayError(f"view '{k}' does not exist")
        del self.views[k]

    def op_create_type(self, op):
        t = copy.deepcopy(op["type"])
        if t["name"] in self.types:
            raise ReplayError(f"type '{t['name']}' already exists")
        t.setdefault("kind", "enum")
        self.born(t)
        self.types[t["name"]] = t

    def op_alter_type(self, op):
        t = self.types.get(op["name"])
        if t is None:
            raise ReplayError(f"type '{op['name']}' does not exist")
        if "add_value" in op:
            t.setdefault("values", [])
            if op["add_value"] not in t["values"]:
                t["values"].append(op["add_value"])
        if "rename_value" in op:
            rv = op["rename_value"]
            t["values"] = [rv["to"] if v == rv["from"] else v for v in t.get("values", [])]
        if "rename" in op:
            new = op["rename"]
            del self.types[t["name"]]
            for tbl in self.tables.values():
                for c in tbl["columns"]:
                    if c.get("type_ref") == f"type:{t['name']}":
                        c["type_ref"] = f"type:{new}"
                        if c.get("type") == t["name"]:
                            c["type"] = new
            t["name"] = new
            self.types[new] = t

    def op_drop_type(self, op):
        if op["name"] not in self.types:
            raise ReplayError(f"type '{op['name']}' does not exist")
        del self.types[op["name"]]

    def op_create_extension(self, op):
        if op["name"] not in self.extensions:
            self.extensions.append(op["name"])
            self.seq += 1
            self.ext_origin[op["name"]] = (self.file, self.seq)

    def op_drop_extension(self, op):
        if op["name"] in self.extensions:
            self.extensions.remove(op["name"])
            self.ext_origin.pop(op["name"], None)

    def op_create_schema(self, op):
        if op["name"] not in self.schemas:
            self.schemas.append(op["name"])
            self.seq += 1
            self.schema_origin[op["name"]] = (self.file, self.seq)

    def op_drop_schema(self, op):
        if op["name"] in self.schemas:
            self.schemas.remove(op["name"])
            self.schema_origin.pop(op["name"], None)

    def op_create_sequence(self, op):
        s = copy.deepcopy(op["sequence"])
        if s["name"] in self.sequences:
            s["created_in"], s["_seq"] = self.sequences[s["name"]]["created_in"], self.sequences[s["name"]]["_seq"]
        else:
            self.born(s)
        self.sequences[s["name"]] = s

    def op_drop_sequence(self, op):
        if op["name"] not in self.sequences:
            raise ReplayError(f"sequence '{op['name']}' does not exist")
        del self.sequences[op["name"]]

    def op_create_function(self, op):
        f = copy.deepcopy(op["function"])
        f.setdefault("depends_on", [])
        old = self.functions.get(f["name"])
        if old:
            self.warn(f"function '{f['name']}' redefined; keeping the later definition")
            f["created_in"], f["_seq"], f["folded_from"] = old["created_in"], old["_seq"], old["folded_from"]
        else:
            self.born(f)
            f["folded_from"] = []
        f["folded_from"].append(self.file)
        f["defined_in"] = self.file
        self.functions[f["name"]] = f

    def op_drop_function(self, op):
        if op["name"] not in self.functions:
            raise ReplayError(f"function '{op['name']}' does not exist")
        del self.functions[op["name"]]
        for t in self.tables.values():
            for trg in t["triggers"]:
                if op["name"] in trg.get("functions", []):
                    self.warn(f"trigger '{trg['name']}' on '{t['name']}' uses dropped function '{op['name']}'")

    def op_create_trigger(self, op):
        t = self.table(op)
        trg = copy.deepcopy(op["trigger"])
        trg.setdefault("functions", [])
        for fn in trg["functions"]:
            if fn not in self.functions:
                raise ReplayError(f"trigger '{trg['name']}' uses unknown function '{fn}'")
        old = next((x for x in t["triggers"] if x.get("name") == trg.get("name")), None)
        if old:
            trg["created_in"], trg["_seq"] = old["created_in"], old["_seq"]
        else:
            self.born(trg)
        trg["defined_in"] = self.file
        t["triggers"] = [x for x in t["triggers"] if x.get("name") != trg.get("name")]
        t["triggers"].append(trg)
        self.touch(t)

    def op_drop_trigger(self, op):
        t = self.table(op)
        before = len(t["triggers"])
        t["triggers"] = [x for x in t["triggers"] if x.get("name") != op["name"]]
        if len(t["triggers"]) == before:
            raise ReplayError(f"trigger '{op['name']}' does not exist on '{t['name']}'")
        self.touch(t)

    def op_data(self, op):
        entry = {k: op.get(k) for k in ("kind", "tables", "summary", "code")}
        entry["file"] = self.file
        entry["decision"] = op.get("decision", "pending")
        if entry["kind"] not in ("seed", "backfill", "fix"):
            self.warn(f"data op has kind '{entry['kind']}', expected seed | backfill | fix")
        self.data.append(entry)

    def op_raw(self, op):
        self.unresolved.append({"file": self.file, "note": op.get("note"), "raw": op.get("sql") or op.get("code")})
        self.warn("raw op recorded as unresolved; review it before writing files")


def describe_originals(originals: list[dict], state: State, cand: dict, fw: str) -> list[dict]:
    """One entry per original in replay order: its path and version, and what it created that still exists."""
    stack_files = cand.get("files", [])
    by_path = {f["path"]: f for f in stack_files}
    names: dict[str, list[dict]] = {}
    for f in stack_files:
        names.setdefault(Path(f["path"]).name, []).append(f)
    dirs = sorted(cand.get("migrations_dirs", []), key=len, reverse=True)

    created: dict[str, list[tuple[int, str]]] = {}

    def add(file: str, seq: int, ref: str):
        created.setdefault(file, []).append((seq, ref))

    for k, t in state.tables.items():
        add(t["created_in"], t.pop("_seq"), f"table:{k}")
        for trg in t["triggers"]:
            add(trg["created_in"], trg.pop("_seq"), f"trigger:{k}.{trg['name']}")
    for k, v in state.views.items():
        add(v["created_in"], v.pop("_seq"), f"view:{k}")
    for t in state.types.values():
        add(t["created_in"], t.pop("_seq"), f"type:{qualified(t['name'], t.get('schema'))}")
    for sq in state.sequences.values():
        add(sq["created_in"], sq.pop("_seq"), f"sequence:{sq['name']}")
    for f in state.functions.values():
        add(f["created_in"], f.pop("_seq"), f"function:{f['name']}")
    for name, (file, seq) in state.ext_origin.items():
        add(file, seq, f"extension:{name}")
    for name, (file, seq) in state.schema_origin.items():
        add(file, seq, f"schema:{name}")

    out = []
    for o in originals:
        f = o["file"]
        match = by_path.get(f) or next((x for x in stack_files if x["path"].endswith("/" + f)), None)
        if match is None and len(names.get(Path(f).name, [])) == 1:
            match = names[Path(f).name][0]
        path = match["path"] if match else f
        version = (match or {}).get("version")
        if version is None:
            style = FRAMEWORKS[fw]["version"]
            parsed = parse_version(style, Path(path).name if FRAMEWORKS[fw].get("folder") is None else path)
            version = format_version(style, parsed) if parsed is not None else None
        mdir = next((d for d in dirs if path.startswith(d.rstrip("/") + "/")), None)
        out.append({
            "file": f,
            "path": path,
            "dir": mdir,
            "version": version,
            "creates": [ref for _, ref in sorted(created.get(f, []))],
            "split": o["split"],
        })
    return out


def replay(workdir: Path, stack: dict | None, framework: str | None, dialect: str | None) -> dict:
    ops_dir = workdir / "ops"
    files = sorted(p for p in ops_dir.glob("*.json"))
    if not files:
        die(f"no ops files in {ops_dir}")
    state = State()
    errors = []
    originals = []
    for path in files:
        doc = load_json(path)
        state.file = doc.get("file") or path.stem
        split = doc.get("split")
        if split not in (None, True, False):
            errors.append(f"{path.name}: 'split' must be true or false, got {split!r}")
        originals.append({"file": state.file, "split": split})
        for i, op in enumerate(doc.get("ops", [])):
            try:
                state.apply(op)
            except ReplayError as e:
                errors.append(f"{path.name} op #{i + 1} ({op.get('op')}): {e}")
            except KeyError as e:
                errors.append(f"{path.name} op #{i + 1} ({op.get('op')}): missing field {e}")
    if errors:
        sys.stderr.write("replay failed:\n" + "\n".join(f"  - {e}" for e in errors) + "\n")
        sys.exit(1)

    stack = stack or {}
    cand = (stack.get("candidates") or [{}])[0] if "candidates" in stack else stack
    fw = framework or cand.get("framework")
    if not fw:
        die("framework unknown: pass --framework or provide stack.json")
    fw = framework_key(fw)
    source_files = describe_originals(originals, state, cand, fw)
    manifest = {
        "version": 1,
        "framework": fw,
        "dialect": dialect or cand.get("dialect") or "unknown",
        "source": {
            "dirs": cand.get("migrations_dirs", []),
            "files": len(files),
            "first_version": cand.get("first_version") or next((f["version"] for f in source_files if f["version"]), None),
            "last_version": cand.get("last_version") or next((f["version"] for f in reversed(source_files) if f["version"]), None),
            "ops_dir": str(ops_dir),
            "versioning": cand.get("versioning") or FRAMEWORKS[fw]["version"],
            "originals": source_files,
        },
        "schemas": state.schemas,
        "extensions": state.extensions,
        "types": sorted(state.types.values(), key=lambda t: t["name"]),
        "sequences": sorted(state.sequences.values(), key=lambda s: s["name"]),
        "functions": sorted(state.functions.values(), key=lambda f: f["name"]),
        "tables": sorted(state.tables.values(), key=lambda t: (t.get("schema") or "", t["name"])),
        "views": sorted(state.views.values(), key=lambda v: (v.get("schema") or "", v["name"])),
        "data_migrations": state.data,
        "unresolved": state.unresolved,
        "dropped_tables": state.dropped_tables,
        "warnings": state.warnings,
    }
    if manifest["dialect"] == "unknown":
        manifest["warnings"].append("dialect unknown: set it in the manifest before planning")
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workdir")
    ap.add_argument("-o", "--out", help="write the manifest here (default: <workdir>/manifest.json)")
    ap.add_argument("--framework")
    ap.add_argument("--dialect", choices=["postgres", "mysql", "sqlite", "mssql"])
    ap.add_argument("--stack", help="stack.json from detect_stack.py (default: <workdir>/stack.json)")
    args = ap.parse_args()

    workdir = Path(args.workdir)
    stack_path = Path(args.stack) if args.stack else workdir / "stack.json"
    stack = load_json(stack_path) if stack_path.exists() else None
    manifest = replay(workdir, stack, args.framework, args.dialect)
    out = args.out or workdir / "manifest.json"
    dump_json(manifest, out)
    t, v = len(manifest["tables"]), len(manifest["views"])
    print(f"manifest written to {out}: {t} tables, {v} views, {len(manifest['types'])} types, "
          f"{len(manifest['functions'])} functions, {len(manifest['data_migrations'])} data migrations, "
          f"{len(manifest['unresolved'])} unresolved, {len(manifest['warnings'])} warnings")
    for w in manifest["warnings"]:
        print(f"  warning: {w}")


if __name__ == "__main__":
    main()
