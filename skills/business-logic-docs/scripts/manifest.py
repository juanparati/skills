#!/usr/bin/env python3
"""Manage the .business-docs.json manifest of a business docs vault.

Usage:
  manifest.py write   <docs-location> --repo <repo-root>
      Record the current HEAD commit and the note -> sources map (read from
      each managed note's `sources` frontmatter).

  manifest.py changed <docs-location> [--repo <repo-root>] [--max-commits N]
      Compare the recorded commit with HEAD and print JSON with the affected
      notes, changed files no note covers, deleted files and commit messages.

  manifest.py show    <docs-location>
      Print the manifest.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vaultlib import MANIFEST_NAME, as_list, iter_notes, read_note  # noqa: E402

IGNORED_PREFIXES = ("vendor/", "node_modules/", "public/build/", "storage/", ".git/")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def load(vault: Path) -> dict:
    path = vault / MANIFEST_NAME
    if not path.exists():
        raise SystemExit(f"no {MANIFEST_NAME} in {vault}")
    return json.loads(path.read_text(encoding="utf-8"))


def note_sources(vault: Path) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for path in iter_notes(vault):
        fm, _ = read_note(path)
        if not fm or "business-docs" not in [t.lower() for t in as_list(fm.get("tags"))]:
            continue
        mapping[str(path.relative_to(vault))] = [s.strip().lstrip("./") for s in as_list(fm.get("sources"))]
    return mapping


def covers(source: str, changed: str) -> bool:
    source = source.rstrip("/")
    return changed == source or changed.startswith(source + "/")


def cmd_write(args) -> int:
    repo = args.repo.resolve()
    vault = args.vault
    head = git(repo, "rev-parse", "HEAD").strip()
    notes = note_sources(vault)
    manifest = {
        "version": 1,
        "repo": str(repo),
        "commit": head,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": notes,
    }
    (vault / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    missing = sorted({s for srcs in notes.values() for s in srcs if not (repo / s).exists()})
    print(f"wrote {vault / MANIFEST_NAME}: commit {head[:10]}, {len(notes)} notes")
    if missing:
        print("warning: these sources don't exist in the repo (fix the notes' `sources`):")
        for s in missing:
            print(f"  - {s}")
    return 0


def cmd_changed(args) -> int:
    vault = args.vault
    manifest = load(vault)
    repo = (args.repo or Path(manifest["repo"])).resolve()
    base = manifest["commit"]
    head = git(repo, "rev-parse", "HEAD").strip()

    result: dict = {"from": base, "to": head, "affected_notes": {}, "unmapped_files": [], "deleted_files": [], "commits": []}
    if base != head:
        diff = git(repo, "diff", "--name-status", f"{base}..{head}")
        notes = note_sources(vault) or manifest.get("notes", {})
        for line in diff.splitlines():
            parts = line.split("\t")
            status, paths = parts[0], parts[1:]
            for changed in paths:
                if changed.startswith(IGNORED_PREFIXES):
                    continue
                if status.startswith("D"):
                    result["deleted_files"].append(changed)
                hit = False
                for note, sources in notes.items():
                    if any(covers(s, changed) for s in sources):
                        result["affected_notes"].setdefault(note, []).append(changed)
                        hit = True
                if not hit and not status.startswith("D"):
                    result["unmapped_files"].append(changed)
        log = git(repo, "log", "--no-merges", f"-n{args.max_commits}", "--format=%h %ad %s", "--date=short", f"{base}..{head}")
        result["commits"] = log.splitlines()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_show(args) -> int:
    print(json.dumps(load(args.vault), indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write")
    w.add_argument("vault", type=Path)
    w.add_argument("--repo", type=Path, required=True)
    w.set_defaults(func=cmd_write)

    c = sub.add_parser("changed")
    c.add_argument("vault", type=Path)
    c.add_argument("--repo", type=Path)
    c.add_argument("--max-commits", type=int, default=200)
    c.set_defaults(func=cmd_changed)

    s = sub.add_parser("show")
    s.add_argument("vault", type=Path)
    s.set_defaults(func=cmd_show)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
