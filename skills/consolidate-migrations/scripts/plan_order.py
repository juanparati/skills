#!/usr/bin/env python3
"""Decide which file every object of the manifest is written in, and name the files.

Usage:
    plan_order.py <manifest.json> [--framework KEY] [-o plan.json] [--base VERSION] [--start N]
                  [--ext EXT] [--mermaid] [--text]

Why a script: the consolidated set keeps the file names of the originals, so the run
order is already fixed by the history. What is left to decide is where each object
goes and which references would point forward, and that must come out the same on
every run (a re-run after a fix must not move objects between files). This script is
deterministic: ties are broken by creation order, then by name.

Rules:
    - an original that created a surviving object keeps its file name and holds the
      final definition of what it created; originals that created nothing that
      survives disappear
    - an original that created several tables/views stays one file, unless its ops
      file says "split": true; then the table named in the file name keeps it and the
      others get new files right after it (or after the last original when the
      version format leaves no room)
    - prerequisites (schemas, extensions, types, sequences, functions that read no
      table) move to an earlier kept file when an object there needs them
    - views, functions that read tables and triggers stay in their creating file when
      everything they read exists by then; else they go to the file of their final
      definition; else to a new file after the last original
    - a foreign key whose target table is created in a later file moves to a final
      deferred_foreign_keys migration. SQLite cannot ADD CONSTRAINT and does not
      validate FK targets at CREATE time, so there it stays inline
    - new files (split tables, functions_and_triggers, moved views, deferred FKs,
      seed data) are numbered after the last original

EF Core keeps a single migration, named after the first original, with one part per
object ordered by dependency (ranks: 0 schema 1 extension 2 type 3 sequence
4 function without table deps 5 table 6 function/trigger with table deps 7 view).
"""

from __future__ import annotations

import argparse
import heapq
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schemalib import (FRAMEWORKS, die, dump_json, file_name, format_version, framework_key,  # noqa: E402
                       load_json, now_version, parse_version, pascal, qualified, snake, step_version, version_between)

RANK = {"schema": 0, "extension": 1, "type": 2, "sequence": 3, "function": 4, "table": 5,
        "function_late": 6, "trigger": 6, "view": 7}


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}            # id -> {"kind","name","schema","rank","obj"}
        self.deps: dict[str, set[str]] = defaultdict(set)   # id -> ids it depends on

    def add(self, kind: str, name: str, schema: str | None, obj, rank: int | None = None, nid: str | None = None):
        nid = nid or f"{kind}:{qualified(name, schema)}"
        self.nodes[nid] = {"kind": kind, "name": name, "schema": schema, "obj": obj,
                           "rank": RANK[kind] if rank is None else rank}
        return nid

    def edge(self, frm: str, to: str):
        if frm != to:
            self.deps[frm].add(to)


def trigger_id(table: dict, trg: dict) -> str:
    return f"trigger:{qualified(table['name'], table.get('schema'))}.{trg['name']}"


def is_prereq(node: dict) -> bool:
    return node["rank"] <= RANK["function"]


def build_graph(m: dict, warnings: list[str]) -> Graph:
    g = Graph()
    for s in m.get("schemas", []):
        g.add("schema", s, None, s)
    for e in m.get("extensions", []):
        g.add("extension", e, None, e)
    for t in m.get("types", []):
        g.add("type", t["name"], t.get("schema"), t)
    for s in m.get("sequences", []):
        g.add("sequence", s["name"], None, s)
    for f in m.get("functions", []):
        late = any(d.startswith("table:") or d.startswith("view:") for d in f.get("depends_on", []))
        g.add("function", f["name"], None, f, RANK["function_late"] if late else RANK["function"])
    table_ids = {}
    for t in m.get("tables", []):
        tid = g.add("table", t["name"], t.get("schema"), t)
        table_ids[qualified(t["name"], t.get("schema"))] = tid
        for trg in t.get("triggers", []):
            g.add("trigger", trg["name"], t.get("schema"), {"table": t["name"], "schema": t.get("schema"), "trigger": trg},
                  nid=trigger_id(t, trg))
    for v in m.get("views", []):
        g.add("view", v["name"], v.get("schema"), v)

    # edges
    for f in m.get("functions", []):
        for d in f.get("depends_on", []):
            g.edge(f"function:{f['name']}", d)
    for t in m.get("tables", []):
        tid = table_ids[qualified(t["name"], t.get("schema"))]
        if t.get("schema"):
            g.edge(tid, f"schema:{t['schema']}")
        for c in t.get("columns", []):
            if c.get("type_ref"):
                g.edge(tid, c["type_ref"])
        for fk in t.get("foreign_keys", []):
            ref = qualified(fk["ref_table"], fk.get("ref_schema"))
            if fk.get("external") or f"table:{ref}" not in g.nodes:
                warnings.append(f"table {t['name']}: FK {fk.get('name') or fk['columns']} -> {ref} points outside the consolidated scope; no ordering edge")
                continue
            g.edge(tid, f"table:{ref}")
        for d in t.get("depends_on", []):
            g.edge(tid, d)
        for trg in t.get("triggers", []):
            trid = trigger_id(t, trg)
            g.edge(trid, tid)
            for fn in trg.get("functions", []):
                g.edge(trid, f"function:{fn}")
        if t.get("polymorphic"):
            warnings.append(f"table {t['name']}: polymorphic relation(s) {t['polymorphic']} have no FK, nothing to order")
    # Django-style implicit M2M tables: created by a ManyToManyField of the owning model,
    # so they get no file of their own; they come after their owner.
    for t in m.get("tables", []):
        owner = t.get("implicit_m2m_of")
        if owner:
            tid = table_ids[qualified(t["name"], t.get("schema"))]
            oid = table_ids.get(owner) or f"table:{owner}"
            if oid not in g.nodes:
                # accept the Django form "app.Model.field" or "app.Model"
                parts = owner.split(".")
                match = [x for x in m.get("tables", []) if len(parts) >= 2 and x.get("app") == parts[0]
                         and (x.get("model") or "").lower() == parts[1].lower()]
                if len(match) == 1:
                    oid = table_ids[qualified(match[0]["name"], match[0].get("schema"))]
                else:
                    die(f"table {t['name']} is implicit_m2m_of unknown table '{owner}' (use the owning table's name, or app.Model)")
            g.edge(tid, oid)
            g.nodes[tid]["owner"] = oid
    for v in m.get("views", []):
        vid = f"view:{qualified(v['name'], v.get('schema'))}"
        if v.get("schema"):
            g.edge(vid, f"schema:{v['schema']}")
        if not v.get("depends_on"):
            warnings.append(f"view {v['name']} declares no depends_on; check its SELECT reads nothing created after its file")
        for d in v.get("depends_on", []):
            g.edge(vid, d)

    missing = sorted({d for deps in g.deps.values() for d in deps if d not in g.nodes})
    if missing:
        die("dependencies on objects that are not in the manifest:\n  " + "\n  ".join(missing))
    return g


def tarjan_scc(nodes: list[str], deps: dict[str, set[str]]) -> list[list[str]]:
    index = {}
    low = {}
    stack, on_stack, result = [], set(), []
    counter = [0]

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(deps.get(v, ())):
            if w not in index:
                if w in nodes_set:
                    strong(w)
                    low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            result.append(sorted(comp))

    nodes_set = set(nodes)
    sys.setrecursionlimit(max(10000, len(nodes) * 4))
    for v in sorted(nodes):
        if v not in index:
            strong(v)
    return result


def break_cycles(g: Graph, tables: list[str], dialect: str, warnings: list[str]) -> list[dict]:
    """Remove FK edges inside cycles among `tables` (all written in one file); return the deferred constraints."""
    table_deps = {n: {d for d in g.deps[n] if d in tables} for n in tables}
    deferred = []
    for comp in tarjan_scc(tables, table_deps):
        if len(comp) < 2:
            continue
        names = ", ".join(g.nodes[c]["name"] for c in comp)
        remaining = set(comp)
        if dialect == "sqlite":
            warnings.append(f"FK cycle between {names}: left inline (SQLite does not validate FK targets at CREATE time)")
            # still cut edges so the file can be ordered; nothing is deferred
            while remaining:
                pick = min(remaining, key=lambda n: (_fk_score(g, n, remaining), g.nodes[n]["name"]))
                for d in list(g.deps[pick]):
                    if d in remaining:
                        g.deps[pick].discard(d)
                remaining.discard(pick)
            continue
        while remaining:
            pick = min(remaining, key=lambda n: (_fk_score(g, n, remaining), g.nodes[n]["name"]))
            t = g.nodes[pick]["obj"]
            for fk in t.get("foreign_keys", []):
                ref = f"table:{qualified(fk['ref_table'], fk.get('ref_schema'))}"
                if ref in remaining and ref != pick:
                    fk["deferred"] = True
                    deferred.append({"table": t["name"], "schema": t.get("schema"), "fk": fk})
                    g.deps[pick].discard(ref)
                    warnings.append(f"FK cycle between {names}: deferred {t['name']}.{','.join(fk['columns'])} -> {fk['ref_table']}"
                                    f" ({'nullable' if _nullable(t, fk) else 'NOT NULL'})")
            remaining.discard(pick)
    return deferred


def _nullable(t: dict, fk: dict) -> bool:
    cols = {c["name"]: c for c in t.get("columns", [])}
    return all(cols.get(c, {}).get("nullable", False) for c in fk.get("columns", []))


def _fk_score(g: Graph, nid: str, remaining: set[str]):
    t = g.nodes[nid]["obj"]
    non_null = total = 0
    for fk in t.get("foreign_keys", []):
        ref = f"table:{qualified(fk['ref_table'], fk.get('ref_schema'))}"
        if ref in remaining and ref != nid:
            total += 1
            if not _nullable(t, fk):
                non_null += 1
    return (non_null, total)


def kahn(g: Graph, nodes: list[str], key) -> list[str]:
    """Topological order of `nodes`, ignoring dependencies outside the set; ties broken by key()."""
    members = set(nodes)
    indeg = {n: 0 for n in nodes}
    rdeps = defaultdict(set)
    for n in nodes:
        for d in g.deps[n]:
            if d in members:
                indeg[n] += 1
                rdeps[d].add(n)
    heap = [(key(n), n) for n in nodes if indeg[n] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        _, n = heapq.heappop(heap)
        order.append(n)
        for x in rdeps[n]:
            indeg[x] -= 1
            if indeg[x] == 0:
                heapq.heappush(heap, (key(x), x))
    if len(order) != len(nodes):
        stuck = sorted(n for n in nodes if n not in order)
        die("unresolvable dependency cycle among:\n  " + "\n  ".join(f"{n} -> {sorted(d for d in g.deps[n] if d in stuck)}" for n in stuck))
    return order


def choose_keeper(o: dict, mains: list[str], g: Graph) -> str:
    """The object a split original keeps: the one its file name names (longest match), else the first created."""
    stem = snake(Path(o["path"]).parent.name if Path(o["path"]).name in ("migration.sql", "up.sql") else Path(o["path"]).stem)
    best, best_len = None, 0
    for n in mains:
        obj = g.nodes[n]["obj"]
        for name in [g.nodes[n]["name"], obj.get("model") or ""] + list(obj.get("renamed_from") or []):
            flat = snake(name)
            for cand in {flat, flat[:-1] if flat.endswith("s") else flat}:
                if cand and cand in stem and len(cand) > best_len:
                    best, best_len = n, len(cand)
    return best or mains[0]


def plan(m: dict, fw_key: str, args) -> dict:
    fw = FRAMEWORKS[fw_key]
    style = (m.get("source") or {}).get("versioning") or fw["version"]
    warnings: list[str] = []
    dialect = m.get("dialect", "unknown")
    originals = (m.get("source") or {}).get("originals")
    if not originals:
        die("manifest.source.originals is missing: re-run replay.py (it records which original created each object)")
    n_orig = len(originals)
    g = build_graph(m, warnings)

    index = {o["file"]: i for i, o in enumerate(originals)}
    created, order_key = {}, {}
    for i, o in enumerate(originals):
        for j, ref in enumerate(o.get("creates", [])):
            created[ref], order_key[ref] = i, (i, j)
    unknown = sorted(n for n in g.nodes if n not in created)
    if unknown:
        die("objects with no creating original in manifest.source.originals (re-run replay.py):\n  " + "\n  ".join(unknown))
    single = bool(fw.get("single_migration"))
    taken = {str(o["version"]) for o in originals if o.get("version")}

    def key(n):
        return (g.nodes[n]["rank"], order_key[n], g.nodes[n]["schema"] or "", g.nodes[n]["name"])

    def defined(n) -> int:
        obj = g.nodes[n]["obj"]
        obj = obj.get("trigger", obj) if isinstance(obj, dict) else {}
        return index.get(obj.get("defined_in"), created[n])

    def where(p) -> str:
        return originals[p[0]]["path"] if p[0] < n_orig else "a new file after the last original"

    pos: dict[str, tuple[int, int]] = {}
    moved: dict[str, str] = {}

    # 1. tables, and the tables/views split out of a multi-table original
    split_slot: dict[str, tuple[int, int] | None] = {}
    split_version: dict[str, str] = {}
    multi = []
    for i, o in enumerate(originals):
        mains = [r for r in o.get("creates", []) if r in g.nodes and (g.nodes[r]["kind"] == "view" or
                                                                       (g.nodes[r]["kind"] == "table" and not g.nodes[r].get("owner")))]
        if len(mains) < 2 or single:
            continue
        decision = o.get("split")
        multi.append({"file": o["path"], "objects": mains, "split": decision})
        if decision is None:
            warnings.append(f"{o['path']} creates {len(mains)} tables/views ({', '.join(g.nodes[r]['name'] for r in mains)}) "
                            f"and has no split decision: kept together. Ask the user, then set \"split\" in its ops file")
        if not decision:
            continue
        keeper = choose_keeper(o, mains, g)
        nxt = originals[i + 1].get("version") if i + 1 < n_orig else None
        k = 0
        for r in mains:
            if r == keeper:
                continue
            k += 1
            if fw.get("per_app") or fw_key == "liquibase":
                split_slot[r] = (i, k)          # order comes from dependencies / the master changelog
                continue
            v = version_between(style, fw_key, str(o["version"]), str(nxt) if nxt else None, k, taken) if o.get("version") else None
            split_slot[r] = (i, k) if v else None
            if v:
                taken.add(v)
                split_version[r] = v

    appended = []
    for n, d in g.nodes.items():
        if d["kind"] != "table" or d.get("owner"):
            continue
        if single:
            pos[n] = (0, 0)
        elif n in split_slot:
            if split_slot[n]:
                pos[n] = split_slot[n]
            else:
                appended.append(n)
        else:
            pos[n] = (created[n], 0)
    for j, n in enumerate(sorted(appended, key=lambda x: order_key[x])):
        pos[n] = (n_orig, j)
        moved[n] = f"split out of {originals[created[n]]['path']}; no free version right after it"
    for n, d in g.nodes.items():
        if d["kind"] == "table" and d.get("owner"):
            pos[n] = (0, 0) if single else max((created[n], 0), pos[d["owner"]])

    # 2. views, functions that read tables, triggers: as early as their dependencies allow
    others = [n for n in g.nodes if g.nodes[n]["kind"] != "table"]
    topo = kahn(g, others, key)
    view_tail = 0
    for n in topo:
        d = g.nodes[n]
        if is_prereq(d):
            continue
        if single:
            pos[n] = (0, 0)
            continue
        need_from = [x for x in g.deps[n] if not is_prereq(g.nodes[x])]
        need = max((pos[x] for x in need_from), default=(-1, 0))
        if n in split_slot:
            cands = [split_slot[n]] if split_slot[n] else []
        else:
            cands = sorted({(created[n], 0), (defined(n), 0)})
        chosen = next((c for c in cands if c >= need), None)
        if chosen is None:
            if d["kind"] == "view":
                chosen, view_tail = (n_orig + 2, view_tail), view_tail + 1
            else:
                chosen = (n_orig + 1, 0)
        if chosen != (created[n], 0) and n not in split_slot:
            late = max(need_from, key=lambda x: pos[x])
            moved[n] = f"reads {g.nodes[late]['kind']} {g.nodes[late]['name']}, created in {where(pos[late])}"
        pos[n] = chosen

    # 3. prerequisites: pulled into the earliest file that needs them
    rdeps = defaultdict(set)
    for a, ds in g.deps.items():
        for b in ds:
            rdeps[b].add(a)
    for n in reversed(topo):
        if not is_prereq(g.nodes[n]):
            continue
        if single:
            pos[n] = (0, 0)
            continue
        p = (created[n], 0)
        needer = None
        for x in sorted(rdeps[n]):
            px = pos.get(x)
            if px and px[0] < n_orig and (px[0], 0) < p:
                p, needer = (px[0], 0), x
        if needer:
            moved[n] = f"needed by {g.nodes[needer]['kind']} {g.nodes[needer]['name']} in {where(p)}"
        pos[n] = p
    for n, d in g.nodes.items():
        if d["kind"] == "table":
            for x in g.deps[n]:
                if g.nodes[x]["rank"] == RANK["function_late"] and pos[x] > pos[n]:
                    warnings.append(f"table {d['name']} depends on function {g.nodes[x]['name']}, which reads tables and is written later "
                                    f"({where(pos[x])}); move that function or the default that calls it by hand")

    # 4. foreign keys: forward references are deferred, cycles inside one file are broken
    deferred = []
    for n, d in sorted(g.nodes.items()):
        if d["kind"] != "table":
            continue
        t = d["obj"]
        for fk in t.get("foreign_keys", []):
            ref = f"table:{qualified(fk['ref_table'], fk.get('ref_schema'))}"
            if ref == n or ref not in g.nodes or fk.get("external") or pos[ref] <= pos[n]:
                continue
            g.deps[n].discard(ref)
            label = f"{t['name']}.{','.join(fk['columns'])} -> {fk['ref_table']}"
            if dialect == "sqlite":
                warnings.append(f"FK {label}: {fk['ref_table']} is created later ({where(pos[ref])}); left inline "
                                f"(SQLite does not validate FK targets at CREATE time)")
                continue
            fk["deferred"] = True
            deferred.append({"table": t["name"], "schema": t.get("schema"), "fk": fk})
            warnings.append(f"FK {label} deferred: {fk['ref_table']} is created later ({where(pos[ref])})"
                            + (" — an implicit M2M: write its AddField in the deferred file" if d.get("owner") else ""))
    by_pos: dict[tuple[int, int], list[str]] = defaultdict(list)
    for n, p in pos.items():
        by_pos[p].append(n)
    for p in sorted(by_pos):
        tables = sorted(n for n in by_pos[p] if g.nodes[n]["kind"] == "table")
        if len(tables) > 1:
            deferred += break_cycles(g, tables, dialect, warnings)

    # 5. entries
    entries: list[dict] = []
    for p in sorted(by_pos):
        members = kahn(g, by_pos[p], key)
        if single:
            entries += ef_parts(members, g, originals[0])
        elif p[0] < n_orig and p[1] == 0:
            o = originals[p[0]]
            entries.append(dict(kind="kept", name=Path(o["path"]).stem if Path(o["path"]).name not in ("migration.sql", "up.sql")
                                else Path(o["path"]).parent.name, kept=True, original=o["path"], members=members))
        elif p[0] == n_orig + 1:
            entries.append(dict(kind="functions", name="functions_and_triggers", kept=False, original=None, members=members))
        else:
            # one new file per object; an implicit join table rides along with its owner
            for x in members:
                if g.nodes[x].get("owner") in members:
                    continue
                own = [x] + [y for y in members if g.nodes[y].get("owner") == x]
                entries.append(dict(kind=g.nodes[x]["kind"], name=g.nodes[x]["name"], schema=g.nodes[x]["schema"], kept=False,
                                    original=None, members=own, version=split_version.get(x)))
    if deferred and not single:
        entries.append(dict(kind="deferred_fks", name="deferred_foreign_keys", kept=False, original=None, members=[]))
    seeds = [d for d in m.get("data_migrations", []) if d.get("decision") == "keep"]
    pending = [d for d in m.get("data_migrations", []) if d.get("decision") not in ("keep", "drop")]
    if pending:
        warnings.append(f"{len(pending)} data migration(s) still have decision 'pending'; set keep or drop in their ops files")
    if seeds and not single:
        entries.append(dict(kind="seed", name="seed_reference_data", kept=False, original=None, members=[]))
    if single:
        entries = [e for e in entries if (e["kind"] != "deferred_fks" or deferred) and (e["kind"] != "seed" or seeds)]

    table_app = {t["name"]: t.get("app") for t in m.get("tables", [])}
    entry_of: dict[str, dict] = {}
    for seq, e in enumerate(entries, 1):
        e["seq"] = seq
        describe(e, g, pos, moved)
        for x in e["members"]:
            entry_of[x] = e
        if e["kind"] == "deferred_fks":
            e["constraints"] = deferred
            e["app"] = table_app.get(deferred[0]["table"])
        if e["kind"] == "seed":
            e["data"] = seeds
            e["app"] = next((table_app.get(tn) for d in seeds for tn in d.get("tables", []) if table_app.get(tn)), None)
        if e["kind"] == "functions" and not single:
            e["app"] = next((x.get("app") for x in entries if x.get("tables")), None)

    assign_names(entries, originals, m, fw_key, fw, style, args, warnings)
    link_files(entries, entry_of, g, fw_key, fw, deferred)

    kept_paths = {e["original"] for e in entries if e.get("kept")}
    for e in entries:
        for t in e.get("tables", []):
            if t.get("renamed_from") and e.get("kept") and not single:
                warnings.append(f"table {t['name']} (renamed from {', '.join(t['renamed_from'])}) keeps the file of the original that created it: {e['original']}")
    for n, why in sorted(moved.items()):
        warnings.append(f"{g.nodes[n]['kind']} {g.nodes[n]['name']} is not written in its creating file ({originals[created[n]]['path']}): {why}")

    mermaid = ["graph LR"]
    for n, d in sorted(g.nodes.items()):
        if d["kind"] in ("table", "view"):
            for x in sorted(g.deps[n]):
                if g.nodes[x]["kind"] in ("table", "view"):
                    mermaid.append(f"  {_mid(x.partition(':')[2])} --> {_mid(n.partition(':')[2])}")
    return {
        "framework": fw_key,
        "dialect": dialect,
        "entries": entries,
        "originals": [{"path": o["path"], "version": o.get("version"), "kept": o["path"] in kept_paths} for o in originals],
        "removed": [o["path"] for o in originals if o["path"] not in kept_paths],
        "multi_table_originals": multi,
        "warnings": warnings,
        "mermaid": "\n".join(mermaid),
    }


def ef_parts(members: list[str], g: Graph, first: dict) -> list[dict]:
    """EF Core: one migration (the first original's id), one partial-class part per object."""
    late = {g.nodes[n]["name"] for n in members if g.nodes[n]["rank"] == RANK["function_late"]}
    parts: list[dict] = []
    prereqs = [n for n in members if is_prereq(g.nodes[n])]
    if prereqs:
        parts.append(dict(kind="prerequisites", name="prerequisites", members=prereqs))
    funcs = []
    for n in members:
        d = g.nodes[n]
        if d["kind"] == "table":
            parts.append(dict(kind="table", name=d["name"], schema=d["schema"], members=[n]))
        elif d["kind"] == "trigger":
            fns = d["obj"]["trigger"].get("functions", [])
            home = next((p for p in parts if p["kind"] == "table" and p["name"] == d["obj"]["table"]
                         and p.get("schema") == d["obj"]["schema"]), None)
            if home and not any(fn in late for fn in fns):
                home["members"].append(n)
            else:
                funcs.append(n)
        elif d["rank"] == RANK["function_late"]:
            funcs.append(n)
    if funcs:
        parts.append(dict(kind="functions", name="functions_and_triggers", members=funcs))
    for n in members:
        if g.nodes[n]["kind"] == "view":
            parts.append(dict(kind="view", name=g.nodes[n]["name"], schema=g.nodes[n]["schema"], members=[n]))
    parts.append(dict(kind="deferred_fks", name="deferred_foreign_keys", members=[]))
    parts.append(dict(kind="seed", name="seed_reference_data", members=[]))
    for p in parts:
        p.update(kept=True, original=first["path"])
    return parts


def describe(e: dict, g: Graph, pos: dict, moved: dict):
    """Fill the per-kind details the writer and check_output.py read."""
    members = e["members"]
    tables, views, triggers, functions, prereqs = [], [], [], [], []
    for n in members:
        d = g.nodes[n]
        if d["kind"] == "table":
            t = d["obj"]
            owner = d.get("owner")
            if owner and owner in members:
                continue            # written inside the owner's model (implicit_tables)
            tables.append({
                "name": t["name"], "schema": t.get("schema"), "app": t.get("app"), "model": t.get("model"),
                "renamed_from": t.get("renamed_from", []),
                "depends_on": sorted(x for x in g.deps[n] if not g.nodes[x].get("owner")),
                "deferred_fks": [fk.get("name") or ",".join(fk["columns"]) for fk in t.get("foreign_keys", []) if fk.get("deferred")],
                "implicit_tables": [g.nodes[x]["name"] for x in members if g.nodes[x].get("owner") == n],
                "implicit_of": g.nodes[owner]["name"] if owner else None,
            })
        elif d["kind"] == "view":
            views.append({"name": d["name"], "schema": d["schema"], "materialized": bool(d["obj"].get("materialized")),
                          "depends_on": sorted(g.deps[n])})
        elif d["kind"] == "trigger":
            triggers.append(d["obj"])
        elif d["kind"] == "function" and not is_prereq(d):
            functions.append(d["name"])
        else:
            prereqs.append(n)
    e.update(tables=tables, views=views, triggers=triggers, functions=functions, prerequisites=prereqs,
             moved_in=[n for n in members if n in moved])
    e.setdefault("app", next((t["app"] for t in tables if t.get("app")), None))


def _mid(name: str) -> str:
    return name.replace(".", "_").replace("-", "_")


def _rel(path: str, base: str | None) -> str:
    if base and path.startswith(base.rstrip("/") + "/"):
        return path[len(base.rstrip("/")) + 1:]
    return Path(path).name


def assign_names(entries, originals, m, fw_key, fw, style, args, warnings):
    pad = fw.get("seq_pad", 0)
    if style == "seq":
        # keep the project's zero padding (0001_, 00001_, 001-, V1__)
        prefix = re.match(r"[Vv]?(\d+)", Path(originals[-1]["path"]).name if Path(originals[-1]["path"]).name not in ("migration.sql", "up.sql")
                          else Path(originals[-1]["path"]).parent.name)
        if prefix:
            pad = len(prefix.group(1)) if prefix.group(1).startswith("0") else 0
    ext = args.ext or fw["ext"]
    last = (m.get("source") or {}).get("last_version")
    by_path = {o["path"]: o for o in originals}
    app_dirs = {}
    for o in originals:
        if o.get("dir") and fw.get("per_app"):
            app_dirs.setdefault(Path(o["dir"]).parent.name or Path(o["dir"]).name, o["dir"])

    def first_new():
        """Where numbering of NEW files starts: right after the last original."""
        if style == "seq":
            if args.start is not None:
                return args.start
            parsed = parse_version("seq", last or "")
            if parsed is None:
                warnings.append(f"no last_version in manifest.source; new files are numbered from {fw.get('seq_start', 1)}")
                return fw.get("seq_start", 1)
            return parsed + 1
        if args.base:
            b = parse_version(style, args.base)
            if b is None:
                die(f"--base '{args.base}' does not match the {fw['label']} version format")
            return b
        parsed = parse_version(style, last or "")
        if parsed is not None:
            return step_version(style, parsed, 1)
        warnings.append("no last_version in manifest.source; new files are numbered from the current time")
        return now_version(style)

    base = None
    per_app_next: dict[str, int] = {}
    if fw.get("per_app"):
        for o in originals:
            app = Path(o["dir"]).parent.name if o.get("dir") else "app"
            v = parse_version("seq", str(o.get("version") or ""))
            if v is not None and args.start is None:
                per_app_next[app] = max(per_app_next.get(app, 0), v + 1)
    counter = 0

    first = originals[0]
    container_id = Path(first["path"]).stem
    container = container_id.split("_", 1)[1] if "_" in container_id else container_id

    for e in entries:
        if e.get("kept"):
            o = by_path[e["original"]]
            e["version"] = str(o.get("version") or "")
            if fw.get("single_migration"):
                e["dir"] = None
                e["version"] = e["version"] or container_id.split("_", 1)[0]
                kind = {"table": "table", "view": "view"}.get(e["kind"], "misc")
                e["file"] = file_name(fw, kind, e["name"], e["version"], ext, e.get("app"), None, container=container)
            elif fw.get("per_app"):
                e["dir"] = o.get("dir") or str(Path(o["path"]).parent)
                e["file"] = _rel(o["path"], e["dir"])
                e["app"] = e.get("app") or Path(e["dir"]).parent.name
            else:
                e["dir"] = None
                e["file"] = _rel(o["path"], o.get("dir"))
        else:
            if fw.get("per_app"):
                app = e.get("app") or next(iter(app_dirs), "app")
                e["app"] = app
                if app not in per_app_next:
                    per_app_next[app] = first_new()
                v = per_app_next[app]
                per_app_next[app] = v + 1
                e["dir"] = app_dirs.get(app, f"{app}/migrations")
                e["version"] = format_version(style, v, pad)
            else:
                e["dir"] = None
                if not e.get("version"):
                    base = first_new() if base is None else base
                    e["version"] = format_version(style, step_version(style, base, counter), pad)
                    counter += 1
            kind = {"table": "table", "view": "view"}.get(e["kind"], "misc")
            model = next((t.get("model") for t in e.get("tables", [])), None)
            e["file"] = file_name(fw, kind, e["name"], e["version"], ext, e.get("app"), model, container=container)
        if fw["folder"] == "up.sql+down.sql":
            e["down_file"] = e["file"].replace("up.sql", "down.sql")
        if fw_key == "typeorm":
            stem = Path(e["file"]).stem
            ver, _, name = stem.partition("-")
            e["class"] = f"{name.replace('-', '')}{ver}" if name else stem
        if fw_key == "doctrine":
            e["class"] = f"Version{e['version']}"
            e["description"] = {"table": f"create {e['name']} table", "view": f"create {e['name']} view"}.get(e["kind"], e["name"].replace("_", " "))
    if fw.get("single_migration"):
        for e in entries:
            e["container"] = fw["single_migration"].format(container_id=container_id, ext=ext)
            e["method"] = "Up" + Path(e["file"]).stem.split(".")[-1]


def link_files(entries, entry_of, g, fw_key, fw, deferred):
    """Django & Liquibase: each entry depends on the previous file of its app and on the
    files holding what it reads in other apps, so the tool's own ordering agrees with the plan."""
    if not (fw.get("per_app") or fw_key == "liquibase"):
        return
    prev: dict = {}
    for e in entries:
        app = e.get("app")
        need = set()
        for x in e["members"]:
            need |= g.deps[x]
        if e["kind"] == "deferred_fks":
            for c in deferred:
                need.add(f"table:{qualified(c['table'], c.get('schema'))}")
                need.add(f"table:{qualified(c['fk']['ref_table'], c['fk'].get('ref_schema'))}")
        files = [prev[app]] if app in prev else []
        for x in sorted(need):
            target = entry_of.get(x)
            if target and target is not e and target.get("app") != app:
                path = (target["dir"] + "/" if target.get("dir") else "") + target["file"]
                if path not in files:
                    files.append(path)
        e["depends_on_files"] = files
        prev[app] = (e["dir"] + "/" if e.get("dir") else "") + e["file"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest")
    ap.add_argument("--framework", help="override manifest.framework")
    ap.add_argument("-o", "--out", help="write plan.json here (default: next to the manifest)")
    ap.add_argument("--base", help="first version for NEW files (timestamp styles); default: manifest last_version + 1s")
    ap.add_argument("--start", type=int, help="first number for NEW files (sequential styles)")
    ap.add_argument("--ext", help="file extension override for new files (knex ts/js, liquibase xml/yaml/sql)")
    ap.add_argument("--mermaid", action="store_true", help="print the dependency graph as mermaid")
    ap.add_argument("--text", action="store_true", help="print the ordered list")
    args = ap.parse_args()

    m = load_json(args.manifest)
    fw_key = framework_key(args.framework or m.get("framework") or "")
    result = plan(m, fw_key, args)
    out = args.out or str(Path(args.manifest).with_name("plan.json"))
    dump_json(result, out)
    kept = sum(1 for e in result["entries"] if e.get("kept"))
    print(f"plan written to {out}: {len(result['entries'])} files ({kept} keep an original's name, "
          f"{len(result['entries']) - kept} new); {len(result['removed'])} originals disappear")
    if args.text or not args.mermaid:
        for e in result["entries"]:
            what = [x.partition(":")[2] for x in e["members"]]
            if e["kind"] == "deferred_fks":
                what = [f"{c['table']}.{','.join(c['fk']['columns'])}" for c in e["constraints"]]
            if e["kind"] == "seed":
                what = [d["file"] for d in e["data"]]
            loc = (e["dir"] + "/" if e.get("dir") else "") + e["file"]
            print(f"  {e['seq']:>3}. {'kept' if e.get('kept') else 'new ':<4}  {loc}  ({', '.join(what)})")
        if result["removed"]:
            print(f"  disappear (folded into the files above): {', '.join(Path(p).name if Path(p).name not in ('migration.sql', 'up.sql') else Path(p).parent.name for p in result['removed'])}")
    for w in result["warnings"]:
        print(f"  warning: {w}")
    if args.mermaid:
        print(result["mermaid"])


if __name__ == "__main__":
    main()
