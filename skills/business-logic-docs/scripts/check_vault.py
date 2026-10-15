#!/usr/bin/env python3
"""Validate a business docs vault.

Usage: check_vault.py <docs-location> [--json]

Errors (must fix): missing frontmatter, missing tags/sources/updated, missing
TL;DR callout, broken [[wikilinks]], duplicate note names, missing Home.md or
Glossary.md.

Warnings (should fix): dense paragraphs, workflow/entity notes without a
mermaid diagram, notes with no outgoing links.

Only notes tagged `business-docs` are checked strictly; other notes are treated
as human-written and only used to resolve links.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vaultlib import WIKILINK_RE, EMBED_RE, as_list, iter_notes, read_note, strip_code  # noqa: E402

TLDR_RE = re.compile(r"^>\s*\[!(tldr|summary|abstract)\]", re.I | re.M)
MAX_PARAGRAPH_WORDS = 60
REQUIRED_KEYS = ("tags", "sources", "updated")


def paragraphs(body: str):
    """Yield plain prose paragraphs (not lists, headings, tables, callouts or code)."""
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    for block in re.split(r"\n\s*\n", body):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        first = lines[0].lstrip()
        if first.startswith(("#", "-", "*", "|", ">", "<!--")) or re.match(r"\d+\.", first):
            continue
        yield " ".join(lines)


def check(vault: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, int] = defaultdict(int)

    notes = list(iter_notes(vault))
    names: dict[str, list[str]] = defaultdict(list)
    for path in notes:
        names[path.stem.lower()].append(str(path.relative_to(vault)))
    for stem, paths in names.items():
        if len(paths) > 1:
            errors.append(f"duplicate note name '{stem}': {', '.join(paths)} (Obsidian links by file name)")

    for required in ("Home.md", "Glossary.md"):
        if not (vault / required).exists():
            errors.append(f"missing {required}")

    for path in notes:
        rel = str(path.relative_to(vault))
        fm, body = read_note(path)
        tags = [t.lower() for t in as_list(fm.get("tags"))] if fm else []
        managed = "business-docs" in tags
        folder = rel.split("/", 1)[0] if "/" in rel else ""
        stats[f"notes:{folder or 'root'}"] += 1

        if fm is None:
            if folder in {"Domains", "Workflows", "Entities", "Rules", "Glossary"} or rel in {"Home.md", "Glossary.md"}:
                errors.append(f"{rel}: missing YAML frontmatter")
            continue
        if not managed:
            stats["human_notes"] += 1
            continue

        for key in REQUIRED_KEYS:
            if key not in fm:
                errors.append(f"{rel}: frontmatter missing '{key}'")
        if not TLDR_RE.search(body):
            errors.append(f"{rel}: missing '> [!tldr]' callout")

        mermaid = body.count("```mermaid")
        stats["mermaid_diagrams"] += mermaid
        if folder in {"Workflows", "Entities"} and mermaid == 0:
            warnings.append(f"{rel}: no mermaid diagram")

        prose = strip_code(body)
        links = WIKILINK_RE.findall(prose) + EMBED_RE.findall(prose)
        stats["wikilinks"] += len(links)
        if not links:
            warnings.append(f"{rel}: no outgoing [[links]]")
        for target in links:
            target = target.strip()
            stem = Path(target).stem if target.endswith(".md") else Path(target).name
            if stem.lower() not in names:
                errors.append(f"{rel}: broken link [[{target}]]")

        for para in paragraphs(body):
            words = len(para.split())
            if words > MAX_PARAGRAPH_WORDS:
                warnings.append(f"{rel}: dense paragraph ({words} words): '{para[:70]}...'")

    stats["glossary_terms"] = sum(1 for p in notes if p.relative_to(vault).parts[0] == "Glossary" and p.parent != vault)
    return {"vault": str(vault), "errors": errors, "warnings": warnings, "stats": dict(stats)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vault", type=Path)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    if not args.vault.is_dir():
        print(f"not a directory: {args.vault}", file=sys.stderr)
        return 2

    result = check(args.vault)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for e in result["errors"]:
            print(f"ERROR   {e}")
        for w in result["warnings"]:
            print(f"WARNING {w}")
        print(f"\n{len(result['errors'])} errors, {len(result['warnings'])} warnings")
        print("stats: " + ", ".join(f"{k}={v}" for k, v in sorted(result["stats"].items())))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
