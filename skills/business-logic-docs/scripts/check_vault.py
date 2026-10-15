#!/usr/bin/env python3
"""Validate a business docs vault.

Usage: check_vault.py <docs-location> [--format gfm|obsidian] [--json]

The format defaults to the one in the manifest, else gfm.

Errors (must fix): missing frontmatter, missing tags/sources/updated, missing
TL;DR callout, broken links, missing entry note (README.md for gfm, Home.md for
obsidian) or Glossary.md.
  gfm:      relative [text](path.md) links must resolve; leftover Obsidian
            syntax ([[wikilinks]], [!tldr]/[!info]/... callouts) is an error.
  obsidian: [[wikilinks]] must resolve; note names must be unique.

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
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vaultlib import (  # noqa: E402
    EMBED_RE, ENTRY_NOTE, FORMATS, MDLINK_RE, WIKILINK_RE, as_list, iter_notes, read_format, read_note, strip_code,
)

TLDR_RE = {
    "obsidian": re.compile(r"^>\s*\[!(tldr|summary|abstract)\]", re.I | re.M),
    "gfm": re.compile(r"^>\s*\*\*TL;DR\*\*", re.M),
}
OBSIDIAN_CALLOUT_RE = re.compile(r"^>\s*\[!(tldr|summary|abstract|info|example|question|quote|todo|success|failure|bug|danger)\]", re.I | re.M)
EXTERNAL_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
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


def gfm_links(path: Path, prose: str, vault: Path) -> tuple[list[str], list[str]]:
    """Return (all relative link targets, broken ones) of a note, resolved from its folder."""
    targets, broken = [], []
    for raw in MDLINK_RE.findall(prose):
        target = raw.strip("<>").split("#", 1)[0]
        if not target or EXTERNAL_RE.match(target) or target.startswith("/"):
            continue
        targets.append(raw)
        resolved = (path.parent / unquote(target)).resolve()
        if not resolved.exists() or not resolved.is_relative_to(vault.resolve()):
            broken.append(raw)
    return targets, broken


def check(vault: Path, fmt: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, int] = defaultdict(int)

    notes = list(iter_notes(vault))
    names: dict[str, list[str]] = defaultdict(list)
    for path in notes:
        names[path.stem.lower()].append(str(path.relative_to(vault)))
    for stem, paths in names.items():
        if fmt == "obsidian" and len(paths) > 1:
            errors.append(f"duplicate note name '{stem}': {', '.join(paths)} (Obsidian links by file name)")

    for required in (ENTRY_NOTE[fmt], "Glossary.md"):
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
            if folder in {"Domains", "Workflows", "Entities", "Rules", "Glossary"} or rel in {ENTRY_NOTE[fmt], "Glossary.md"}:
                errors.append(f"{rel}: missing YAML frontmatter")
            continue
        if not managed:
            stats["human_notes"] += 1
            continue

        for key in REQUIRED_KEYS:
            if key not in fm:
                errors.append(f"{rel}: frontmatter missing '{key}'")
        if not TLDR_RE[fmt].search(body):
            expected = "> **TL;DR**" if fmt == "gfm" else "> [!tldr]"
            errors.append(f"{rel}: missing '{expected}' callout")

        mermaid = body.count("```mermaid")
        stats["mermaid_diagrams"] += mermaid
        if folder in {"Workflows", "Entities"} and mermaid == 0:
            warnings.append(f"{rel}: no mermaid diagram")

        prose = strip_code(body)
        if fmt == "gfm":
            links, broken = gfm_links(path, prose, vault)
            for target in broken:
                errors.append(f"{rel}: broken link ({target})")
            for target in WIKILINK_RE.findall(prose):
                errors.append(f"{rel}: Obsidian [[{target.strip()}]] link in a gfm vault, use [text](relative/path.md)")
            for callout in OBSIDIAN_CALLOUT_RE.findall(prose):
                errors.append(f"{rel}: Obsidian '[!{callout}]' callout in a gfm vault, see references/formats.md")
        else:
            links = WIKILINK_RE.findall(prose) + EMBED_RE.findall(prose)
            for target in links:
                target = target.strip()
                stem = Path(target).stem if target.endswith(".md") else Path(target).name
                if stem.lower() not in names:
                    errors.append(f"{rel}: broken link [[{target}]]")
        stats["links"] += len(links)
        if not links:
            warnings.append(f"{rel}: no outgoing links")

        for para in paragraphs(body):
            words = len(para.split())
            if words > MAX_PARAGRAPH_WORDS:
                warnings.append(f"{rel}: dense paragraph ({words} words): '{para[:70]}...'")

    stats["glossary_terms"] = sum(1 for p in notes if p.relative_to(vault).parts[0] == "Glossary" and p.parent != vault)
    return {"vault": str(vault), "format": fmt, "errors": errors, "warnings": warnings, "stats": dict(stats)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vault", type=Path)
    parser.add_argument("--format", choices=FORMATS, help="Markdown flavor (default: manifest's, else gfm)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    if not args.vault.is_dir():
        print(f"not a directory: {args.vault}", file=sys.stderr)
        return 2

    result = check(args.vault, read_format(args.vault, args.format))
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for e in result["errors"]:
            print(f"ERROR   {e}")
        for w in result["warnings"]:
            print(f"WARNING {w}")
        print(f"\nformat: {result['format']}")
        print(f"{len(result['errors'])} errors, {len(result['warnings'])} warnings")
        print("stats: " + ", ".join(f"{k}={v}" for k, v in sorted(result["stats"].items())))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
