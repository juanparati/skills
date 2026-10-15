"""Shared helpers for reading an Obsidian-style business docs vault (stdlib only)."""

from __future__ import annotations

import re
from pathlib import Path

MANIFEST_NAME = ".business-docs.json"
WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")
EMBED_RE = re.compile(r"!\[\[([^\]|#^]+)")


def iter_notes(vault: Path):
    """Yield every Markdown note, skipping hidden folders (.obsidian, .git, ...)."""
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts):
            continue
        yield path


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_text, body). frontmatter_text is None when absent."""
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    return None, text


def _parse_scalar_list(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
    return [value.strip("'\"")] if value else []


def parse_frontmatter(fm: str) -> dict[str, object]:
    """Minimal YAML subset: `key: value`, `key: [a, b]` and block lists (`- item`)."""
    data: dict[str, object] = {}
    current_key = None
    for raw in fm.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if " #" in raw else raw.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("- ") and current_key is not None:
            existing = data.get(current_key)
            if not isinstance(existing, list):
                existing = []
            existing.append(stripped[2:].strip().strip("'\""))
            data[current_key] = existing
            continue
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key
            if value.startswith("["):
                data[key] = _parse_scalar_list(value)
            elif value:
                data[key] = value.strip("'\"")
            else:
                data[key] = []
    return data


def as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v)]
    return [str(value)] if str(value) else []


def read_note(path: Path) -> tuple[dict[str, object] | None, str]:
    text = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    return (parse_frontmatter(fm) if fm is not None else None), body


def strip_code(body: str) -> str:
    """Remove fenced code blocks and inline code so links inside them are ignored."""
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", body)
