#!/usr/bin/env python3
"""
memory-sync.py — Claude Code Memory Synchronization Script
Core utilities: frontmatter parser, hashing, TTL management.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def safe_read_text(filepath: Path) -> str | None:
    """Read a file as UTF-8, return None if encoding fails."""
    try:
        return filepath.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        print(f"Warning: cannot read {filepath} (not valid UTF-8), skipping", file=sys.stderr)
        return None


# ── Constants ────────────────────────────────────────────────────────────────

REQUIRED_FIELDS = {"name", "description"}
KEBAB_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
WIKI_LINK_PATTERN = re.compile(r"\[\[([a-z][a-z0-9-]*)\]\]")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# ── Hot-List Constants ──────────────────────────────────────────────────────────

HOT_BUDGET = 2000
MARKER_START = "<!-- memory-index:start -->"
MARKER_END = "<!-- memory-index:end -->"

# ── Frontmatter Parser ───────────────────────────────────────────────────────

def _parse_scalar(value: str):
    """Parse a single YAML-like scalar or inline list."""
    value = value.strip().strip("\"'")
    if not value or value.lower() in ("null", "none", "~"):
        return None
    if value == "[]":
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse v2 frontmatter from a memory .md file.

    v2 format — 4 fields at top level:
      name: kebab-case
      description: one sentence
      references: [] or list of slugs
      read-when: [] or list of natural-language phrases

    Returns (metadata_dict, body_string).
    If no frontmatter, returns ({}, text.strip()).
    """
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    end_idx = text.find("---", 3)
    if end_idx == -1:
        return {}, text

    raw_front = text[3:end_idx].strip()
    body = text[end_idx + 3:].strip()

    meta = {"references": [], "read_when": []}
    current_key = None
    current_list = None

    for line in raw_front.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        if stripped.startswith("- ") and current_list is not None:
            current_list.append(stripped[2:].strip().strip("\"'"))
            continue

        if ":" in stripped and indent == 0:
            key, _, raw_val = stripped.partition(":")
            key = key.strip()
            raw_val = raw_val.strip()

            if key == "references" or key == "read-when":
                current_key = key
                current_list = []
                meta["read_when" if key == "read-when" else "references"] = current_list
                val = _parse_scalar(raw_val)
                if isinstance(val, list):
                    current_list = val
                    meta["read_when" if key == "read-when" else "references"] = current_list
                    current_list = None
                elif val not in (None, ""):
                    current_list.append(val)
                    current_list = None
            else:
                current_key = None
                current_list = None
                meta[key] = _parse_scalar(raw_val)

    # Strip unknown fields — only keep v2 schema fields
    KNOWN = {"name", "description", "references", "read_when"}
    for key in list(meta.keys()):
        if key not in KNOWN:
            del meta[key]

    return meta, body


# ── Hashing ──────────────────────────────────────────────────────────────────

def file_hash(text: str) -> str:
    """Return SHA-256 of full file text (minus content_hash line), first 16 hex chars.

    The content_hash line is stripped before hashing to avoid a self-referential
    loop — otherwise writing a new hash would change the file, invalidating the
    hash on the next run.  This also means frontmatter metadata changes (tags,
    description, etc.) are detected as changes, not just body changes.
    """
    text_without_hash = re.sub(r'^\s*content_hash:.*\r?\n?', '', text, flags=re.MULTILINE)
    return hashlib.sha256(text_without_hash.encode("utf-8")).hexdigest()[:16]


# ── Path / Slug Utilities ───────────────────────────────────────────────────

def slug_from_path(filepath: Path) -> str:
    """Return the filename stem (no directory, no extension)."""
    return filepath.stem


# ── Index I/O ───────────────────────────────────────────────────────────────

def load_previous_index(index_json_path: Path) -> dict:
    """Load a JSON index file from disk.

    Returns the parsed dict on success, or {"files": {}} on any error
    (file not found, invalid JSON, permission error, etc.).
    """
    try:
        with open(index_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {"files": {}}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"files": {}}


# ── Validation Engine ─────────────────────────────────────────────────────────

def validate(meta: dict, body: str, stored_hash: str,
             known_slugs: set, filepath: Path) -> tuple[list, str]:
    """Run v2 validation — 4 checks.

    Returns (warnings_list, actual_hash).
    """
    warnings = []
    full_text = safe_read_text(filepath) if filepath.exists() else ""
    actual_hash = file_hash(full_text) if full_text else ""

    name = meta.get("name") if isinstance(meta.get("name"), str) else None
    description = meta.get("description") if isinstance(meta.get("description"), str) else None
    references = meta.get("references", []) if isinstance(meta.get("references"), list) else []

    # Check 1: missing required
    if not name or not description:
        warnings.append({
            "level": "error",
            "check": "missing-required",
            "detail": "name or description is missing",
            "suggestion": "Add both name and description fields"
        })

    # Check 2: invalid name
    if name and not KEBAB_PATTERN.match(name):
        warnings.append({
            "level": "error",
            "check": "invalid-name",
            "detail": f"name '{name}' is not kebab-case",
            "suggestion": "Use lowercase letters, digits, and hyphens only (e.g., 'my-memory')"
        })

    # Check 3: stale hash
    if stored_hash and stored_hash != actual_hash:
        warnings.append({
            "level": "warning",
            "check": "stale-hash",
            "detail": "stored hash does not match body hash",
            "suggestion": "Hash auto-updated in INDEX.json on next write"
        })

    # Check 4: broken references
    broken = [r for r in references if r not in known_slugs]
    if broken:
        warnings.append({
            "level": "error",
            "check": "broken-references",
            "detail": f"references to unknown slugs: {broken}",
            "suggestion": f"Remove broken references: {', '.join(broken)}"
        })

    # Check 5: weak or empty read-when
    rw = meta.get("read_when", []) or []
    if not rw:
        warnings.append({
            "level": "warning",
            "check": "empty-read-when",
            "detail": "read-when is empty — WARM recall will never find this memory",
            "suggestion": "Add 2-3 natural-language phrases describing when this memory is useful"
        })
    elif all(len(p.strip()) < 10 for p in rw):
        warnings.append({
            "level": "warning",
            "check": "weak-read-when",
            "detail": "all read-when phrases are very short (<10 chars), may match too broadly",
            "suggestion": "Use longer, more specific phrases like 'debugging tt statusline cost display'"
        })

    return warnings, actual_hash


# ── Graph Builder ─────────────────────────────────────────────────────────────

def build_graph(records):
    """Build citation graph from both body [[wiki-links]] and frontmatter references field.

    Returns {slug: [referenced_slug, ...]}.
    """
    graph = {}
    for record in records:
        slug = record["slug"]
        meta = record.get("metadata", {})
        body = record.get("body", "")
        references = meta.get("references", []) if isinstance(meta.get("references"), list) else []

        # Extract wiki links from body
        body_links = WIKI_LINK_PATTERN.findall(body) if body else []

        # Combine body links and explicit references, deduplicating
        seen = set()
        combined = []
        for slug_ref in body_links + references:
            if slug_ref != slug and slug_ref not in seen:
                seen.add(slug_ref)
                combined.append(slug_ref)

        graph[slug] = combined
    return graph


# ── Degree Computation ────────────────────────────────────────────────────────

def compute_degrees(graph):
    """Compute in-degree and out-degree for each slug in the graph.

    Only counts references to slugs that exist in the graph.
    Returns (in_degree_dict, out_degree_dict).
    """
    in_degree = {slug: 0 for slug in graph}
    out_degree = {slug: len(refs) for slug, refs in graph.items()}

    for slug, refs in graph.items():
        for ref in refs:
            if ref in graph:
                in_degree[ref] += 1

    return in_degree, out_degree


# ── Scoring ───────────────────────────────────────────────────────────────────

def compute_score(meta: dict, in_deg: int, out_deg: int, last_hit: str | None = None) -> float:
    """Compute v2 score: graph degree + freshness bonus + hit bonus.

    score = in_degree * 2.0 + out_degree * 0.5 + max(0, 10 - days_since_mtime) + (5.0 if hit within 30 days else 0)
    """
    days = 0
    filepath = meta.get("_filepath")
    if filepath and filepath.exists():
        mtime = filepath.stat().st_mtime
        days = (datetime.now(timezone.utc).timestamp() - mtime) / 86400
    days_bonus = max(0.0, 10.0 - days)
    hit_bonus = 0.0
    if last_hit:
        try:
            hit_dt = datetime.fromisoformat(last_hit)
            if (datetime.now(timezone.utc) - hit_dt).days < 30:
                hit_bonus = 5.0
        except (ValueError, TypeError):
            pass
    score = in_deg * 2.0 + out_deg * 0.5 + days_bonus + hit_bonus
    return round(score, 1)



# ── INDEX.md Generator ────────────────────────────────────────────────────────

def build_index_md(records: list, warnings: list, scores: dict,
                   in_deg: dict, out_deg: dict) -> str:
    """Generate v2 INDEX.md — flat list sorted by score descending."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Memory Index",
        f"*{now_str} · {len(records)} memories*",
        "",
    ]

    # Sort by score descending
    sorted_records = sorted(
        records, key=lambda r: scores.get(r["slug"], 0), reverse=True
    )

    for record in sorted_records:
        slug = record["slug"]
        meta = record.get("metadata", {})
        desc = meta.get("description", "")
        score = scores.get(slug, 0)
        in_d = in_deg.get(slug, 0)
        out_d = out_deg.get(slug, 0)

        # updated from stored _updated_str (set before writeback pops _filepath)
        updated_str = record.get("_updated_str", "")

        # read-when condensed
        rw = meta.get("read_when", []) or []
        rw_str = ", ".join(rw) if rw else "(none)"

        lines.append(f"- [{slug}]({slug}.md) — {desc}")
        lines.append(f"  read-when: {rw_str}")
        lines.append(f"  updated: {updated_str} · refs: in {in_d}, out {out_d} · score: {score}")
        lines.append("")

    # Warnings section
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in sorted(warnings, key=lambda w: {"error": 0, "warning": 1}.get(w.get("level", "info"), 2)):
            check = w.get("check", "")
            detail = w.get("detail", "")
            suggestion = w.get("suggestion", "")
            lines.append(f"- **{'ERR' if w.get('level') == 'error' else 'WARN'}** [{check}] {detail}")
            lines.append(f"  → {suggestion}")
        lines.append("")

    return "\n".join(lines)


# ── Hot-List Injection ──────────────────────────────────────────────────────────


def inject_hot_list(records: list, scores: dict, target_file: Path,
                    scope_label: str = "global") -> None:
    """Write Top-N entries into CLAUDE.md or MEMORY.md between markers.

    Auto-creates the target file and markers if they don't exist.
    """
    if target_file.exists():
        content = safe_read_text(target_file) or ""
    else:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        content = ""

    if MARKER_START not in content or MARKER_END not in content:
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"{MARKER_START}\n{MARKER_END}\n"

    # Build hot list entries with scope-aware relative paths
    sorted_records = sorted(
        records, key=lambda r: scores.get(r["slug"], 0), reverse=True
    )

    if scope_label == "global":
        path_prefix = "global/memory"
    else:
        path_prefix = f"projects/{scope_label}/memory"

    entries = []
    budget_remaining = HOT_BUDGET
    for record in sorted_records:
        slug = record["slug"]
        desc = record.get("metadata", {}).get("description", "")
        entry = f"- [{slug}]({path_prefix}/{slug}.md) — {desc}"
        if len(entry) <= budget_remaining:
            entries.append(entry)
            budget_remaining -= len(entry) + 1
        else:
            break

    new_block = f"{MARKER_START}\n{chr(10).join(entries)}{chr(10) if entries else ''}{MARKER_END}"

    start = content.find(MARKER_START)
    end = content.find(MARKER_END) + len(MARKER_END)
    new_content = content[:start] + new_block + content[end:]

    target_file.write_text(new_content, encoding="utf-8")


# ── INDEX.json Generator ──────────────────────────────────────────────────────

def build_index_json(project_name: str, memory_dir: Path, records: list,
                     warnings: list, scores: dict, in_deg: dict,
                     out_deg: dict, graph: dict, prev_index: dict | None = None) -> str:
    """Generate INDEX.json from v2 records."""
    prev_files = (prev_index or {}).get("files", {})
    files = {}
    for record in records:
        slug = record["slug"]
        meta = record.get("metadata", {})
        prev_entry = prev_files.get(slug, {})
        entry = {
            "path": f"memory/{slug}.md",
            "hash": record.get("stored_hash", ""),
            "name": meta.get("name", ""),
            "description": meta.get("description", ""),
            "references": meta.get("references", []),
            "read_when": meta.get("read_when", []),
            "in_degree": in_deg.get(slug, 0),
            "out_degree": out_deg.get(slug, 0),
            "score": scores.get(slug, 0),
            "sync_status": record.get("sync_status", ""),
        }
        # Preserve hit tracking fields from previous index
        if "hit_count" in prev_entry:
            entry["hit_count"] = prev_entry.get("hit_count", 0)
        if "last_hit" in prev_entry:
            entry["last_hit"] = prev_entry.get("last_hit")
        files[slug] = entry
    existing_slugs = {record["slug"] for record in records}
    filtered_graph = {
        slug: [r for r in refs if r in existing_slugs]
        for slug, refs in graph.items()
    }
    return json.dumps({
        "project": project_name,
        "memory_dir": str(memory_dir),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "warnings": warnings,
        "graph": filtered_graph,
    }, indent=2, ensure_ascii=False)


# ── Memory Directory Scanner ──────────────────────────────────────────────────

def _prev_to_meta(prev_entry: dict) -> dict:
    """Convert a flat INDEX.json entry back to v2 metadata format."""
    return {
        "name": prev_entry.get("name", ""),
        "description": prev_entry.get("description", ""),
        "references": prev_entry.get("references", []),
        "read_when": prev_entry.get("read_when", []),
    }


def scan_memory_dir(memory_dir, prev_index, target_files=None):
    """Scan memory directory and return (records, changed_slugs, affected_slugs).

    Phase 1 — Hash comparison: skip re-parsing when body hash is unchanged.
    Phase 2 — Reference propagation: track slugs whose refs changed.
    Phase 3 — Re-parse affected slugs that weren't in the original file set.
    """
    records = []
    changed_slugs = set()
    affected_slugs = set()
    prev_files = prev_index.get("files", {})

    # Determine files to scan
    if target_files:
        filepaths = [Path(f) for f in target_files if Path(f).exists()]
    else:
        filepaths = sorted([
            p for p in memory_dir.glob("*.md")
            if p.name not in ("INDEX.md", "MEMORY.md")
        ])

    # Always glob all .md files (skip INDEX.md, MEMORY.md) to know the
    # full slug universe — prevents --files mode from losing memories
    all_files = sorted([
        p for p in memory_dir.glob("*.md")
        if p.name not in ("INDEX.md", "MEMORY.md")
    ])

    # Phase 1 — Hash comparison
    parsed = {}
    body_map = {}
    text_map = {}
    hash_map = {}
    path_map = {}

    # Seed with all prev_index entries so --files mode doesn't drop
    # memories that weren't in the target set
    for slug, entry in prev_files.items():
        parsed[slug] = _prev_to_meta(entry)
        body_map[slug] = ""
        hash_map[slug] = entry.get("hash", "")

    # Also seed from glob (catches newly created files not in prev_index)
    for fp in all_files:
        slug = fp.stem
        if slug not in path_map:
            path_map[slug] = fp
        slug = slug_from_path(fp)
        text = safe_read_text(fp)
        if text is None:
            continue

        # Extract body for validation (hash uses full text below)
        if text.startswith("---"):
            end = text.find("---", 3)
            body = text[end + 3:].strip() if end != -1 else text.strip()
        else:
            body = text.strip()

        actual_hash = file_hash(text)
        prev_entry = prev_files.get(slug, {})
        prev_hash = prev_entry.get("hash", "")

        text_map[slug] = text
        body_map[slug] = body
        hash_map[slug] = actual_hash
        path_map[slug] = fp

        if actual_hash != prev_hash or target_files is not None or not prev_entry:
            # Changed or forced — full parse needed
            changed_slugs.add(slug)
            meta, _ = parse_frontmatter(text)
            parsed[slug] = meta
        else:
            # Unchanged — reuse previous metadata
            parsed[slug] = _prev_to_meta(prev_entry)

    # Phase 2 — Reference propagation
    for slug in changed_slugs:
        prev_entry = prev_files.get(slug, {})
        old_refs = set(prev_entry.get("references") or [])

        body = body_map.get(slug, "")
        meta = parsed.get(slug, {})
        body_links = WIKI_LINK_PATTERN.findall(body) if body else []
        new_refs = set(body_links + (meta.get("references") or []))

        affected_slugs.update(old_refs.symmetric_difference(new_refs))

    # Phase 3 — Re-parse affected slugs not yet processed
    for slug in list(affected_slugs):
        if slug not in parsed:
            af_file = memory_dir / f"{slug}.md"
            if af_file.exists():
                text = safe_read_text(af_file)
                if text is None:
                    continue
                if text.startswith("---"):
                    end = text.find("---", 3)
                    body = text[end + 3:].strip() if end != -1 else text.strip()
                else:
                    body = text.strip()
                meta, _ = parse_frontmatter(text)
                parsed[slug] = meta
                text_map[slug] = text
                body_map[slug] = body
                hash_map[slug] = file_hash(text)
                path_map[slug] = af_file

    # Build final records list — only include files that exist on disk
    for slug in parsed:
        if slug not in path_map:
            continue  # file was deleted, skip ghost entry
        prev_entry = prev_files.get(slug, {})
        stored_hash = prev_entry.get("hash", "")
        records.append({
            "slug": slug,
            "metadata": parsed[slug],
            "body": body_map.get(slug, ""),
            "stored_hash": stored_hash,
            "_actual_hash": hash_map.get(slug, ""),
            "_filepath": path_map.get(slug),
            "_text": text_map.get(slug, ""),
        })

    return records, list(changed_slugs), list(affected_slugs)


# ── Writeback ─────────────────────────────────────────────────────────────────

def writeback_content_hash(records, memory_dir):
    """Update stored_hash on records and clean up temp keys.

    v2: content_hash lives only in INDEX.json. This function does NOT
    modify .md files — the hash is a derived cache, not source of truth.
    """
    for record in records:
        actual_hash = record.get("_actual_hash")
        if actual_hash:
            record["stored_hash"] = actual_hash
        record.pop("_actual_hash", None)
        record.pop("_filepath", None)


# ── Fix Engine ────────────────────────────────────────────────────────────────

def _fix_references_on_disk(filepath, new_refs):
    """Replace the frontmatter references list with new_refs."""
    text = safe_read_text(filepath)
    if text is None:
        return
    lines = text.split("\n")

    # Locate the references block
    ref_key_idx = -1
    ref_indent = ""
    item_prefix = ""
    first_ref = -1
    last_ref = -1

    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if ref_key_idx < 0 and stripped.endswith("references:"):
            ref_key_idx = i
            ref_indent = line[: len(line) - len(line.lstrip())]
            item_prefix = ref_indent + "  "
        elif ref_key_idx >= 0:
            if line.startswith(item_prefix) and line.lstrip().startswith("- "):
                if first_ref < 0:
                    first_ref = i
                last_ref = i
            elif line.startswith(ref_indent) and not line.lstrip().startswith("- "):
                break  # next key at same indent

    if ref_key_idx < 0:
        return

    # Remove old reference items
    if first_ref >= 0 and last_ref >= first_ref:
        del lines[first_ref : last_ref + 1]
        # After deletion, the cursor is at first_ref
        insert_at = first_ref
    else:
        # references: [] or empty — insert after the key line
        # Replace `[]` on the same line if present
        if "[]" in lines[ref_key_idx]:
            lines[ref_key_idx] = lines[ref_key_idx].replace("[]", "")
        insert_at = ref_key_idx + 1
        # If the next line is empty, we insert there
        if insert_at < len(lines) and lines[insert_at].strip() == "":
            pass  # insert after the empty line? Actually, right after ref key

    # Insert new references
    if new_refs:
        new_lines = [f"{item_prefix}- {r}" for r in new_refs]
        for i, nl in enumerate(new_lines):
            lines.insert(insert_at + i, nl)

    filepath.write_text("\n".join(lines), encoding="utf-8")


def apply_fixes(records: list, known_slugs: set, memory_dir: Path,
                dry_run: bool = False) -> list:
    """Apply v2 fixes — only remove broken references."""
    fixes = []
    for record in records:
        slug = record["slug"]
        meta = record.get("metadata", {})
        references = meta.get("references") or []
        broken = [r for r in references if r not in known_slugs]
        if broken:
            new_refs = [r for r in references if r not in broken]
            fixes.append({
                "slug": slug,
                "check": "broken-references",
                "detail": f"Removed broken references: {broken}",
            })
            if not dry_run:
                _fix_references_on_disk(memory_dir / f"{slug}.md", new_refs)
                record["metadata"]["references"] = new_refs
    return fixes


def _extract_heading_terms(body: str) -> set[str]:
    """Extract kebab-case terms from all heading levels in body."""
    if not body:
        return set()
    headings = HEADING_PATTERN.findall(body)
    terms = set()
    for _level, heading_text in headings:
        if not re.search(r'[—:–]', heading_text):
            continue
        term = re.split(r'\s*[—:–]\s*', heading_text.strip())[0].strip()
        if term and re.match(r'^[a-z][a-z0-9-]*$', term.lower()):
            terms.add(term.lower())
    return terms


def audit_candidates(records):
    """Find knowledge memory pairs that share tags + headings.

    Returns list of candidate dicts with keys:
    a, b, shared_tags, shared_headings, updated_a, updated_b, suggestion
    """
    # v2: all memories are knowledge memories; type field removed
    knowledge = []
    for r in records:
        knowledge.append(r)

    candidates = []
    for i in range(len(knowledge)):
        for j in range(i + 1, len(knowledge)):
            a = knowledge[i]
            b = knowledge[j]

            meta_a = a.get("metadata", {})
            meta_b = b.get("metadata", {})

            # v2: use shared references instead of removed tags field
            refs_a = set(r.lower() for r in (meta_a.get("references") or []) if r)
            refs_b = set(r.lower() for r in (meta_b.get("references") or []) if r)
            shared_refs = sorted(refs_a & refs_b)
            if len(shared_refs) < 1:
                continue

            headings_a = _extract_heading_terms(a.get("body", ""))
            headings_b = _extract_heading_terms(b.get("body", ""))
            shared_headings = sorted(headings_a & headings_b)

            if not shared_headings:
                continue

            candidates.append({
                "a": a["slug"],
                "b": b["slug"],
                "shared_refs": shared_refs,
                "shared_headings": shared_headings,
                "updated_a": meta_a.get("updated", ""),
                "updated_b": meta_b.get("updated", ""),
                "suggestion": "Both discuss {0} — verify consistency".format(
                    ", ".join(shared_headings)),
            })

    return candidates


# ── Hit Tracking ──────────────────────────────────────────────────────────────


def _get_all_memory_dirs() -> list[tuple[Path, str]]:
    """Return all (memory_dir, scope_label) tuples."""
    dirs = []
    home = Path.home().resolve()
    global_dir = home / ".claude" / "global" / "memory"
    if global_dir.exists():
        dirs.append((global_dir, "global"))
    projects_base = home / ".claude" / "projects"
    if projects_base.exists():
        for proj_dir in sorted(projects_base.iterdir()):
            mem_dir = proj_dir / "memory"
            if mem_dir.is_dir():
                dirs.append((mem_dir, proj_dir.name))
    return dirs


def record_hit(slug: str) -> None:
    """Increment hit_count and set last_hit for a memory in its INDEX.json."""
    home = Path.home().resolve()
    # Search all scopes for this slug
    for memory_dir, _ in _get_all_memory_dirs():
        index_path = memory_dir / "INDEX.json"
        if not index_path.exists():
            continue
        data = json.loads(index_path.read_text(encoding="utf-8"))
        entry = data.get("files", {}).get(slug)
        if entry:
            entry["hit_count"] = entry.get("hit_count", 0) + 1
            entry["last_hit"] = datetime.now(timezone.utc).isoformat()
            index_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return
    print(f"Memory not found: {slug}", file=sys.stderr)


# ── Read-When Hints ─────────────────────────────────────────────────────────


def suggest_read_when(body: str) -> list[str]:
    """Extract heading terms that could serve as read-when phrases."""
    headings = HEADING_PATTERN.findall(body) if body else []
    suggestions = []
    for _, heading_text in headings:
        clean = heading_text.strip()
        if 10 <= len(clean) <= 80:
            suggestions.append(clean)
    return suggestions[:5]


# ── Scope Detection ───────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    """Sanitize a directory name to kebab-case for use as a project slug."""
    name = name.lower()
    # Collapse runs of hyphens
    name = re.sub(r'[-]+', '-', name)
    # Replace any non-alphanumeric (except hyphen) with hyphen
    name = re.sub(r'[^a-z0-9-]', '-', name)
    # Strip leading/trailing hyphens
    return name.strip('-')


def detect_scope(cwd=None, scope_from_file=None):
    """Detect memory directory and scope label.

    Returns (memory_dir, scope_label) where scope_label is 'global' or 'project'.
    - Global:  ~/.claude/global/memory/
    - Project: ~/.claude/projects/<project-slug>/memory/
    """
    if scope_from_file:
        fp = Path(scope_from_file).resolve()
        home = Path.home().resolve()
        fp_str = str(fp).replace("\\", "/")
        home_str = str(home).replace("\\", "/").rstrip("/") + "/"
        if fp_str.startswith(home_str + ".claude/global/memory/"):
            return home / ".claude" / "global" / "memory", "global"
        # Check if under ~/.claude/projects/<slug>/memory/
        m = re.match(r'.*\.claude/projects/([^/]+)/memory/.*', fp_str)
        if m:
            return home / ".claude" / "projects" / m.group(1) / "memory", m.group(1)
        # Fallback: walk up from file
        return _find_project_memory(fp.parent), "project"

    # Auto-detect from CWD
    cwd = cwd or Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            slug = _slugify(parent.name)
            return Path.home() / ".claude" / "projects" / slug / "memory", slug
    return Path.home() / ".claude" / "global" / "memory", "global"


def _find_project_memory(start):
    """Walk up from start to find .git, return ~/.claude/projects/<slug>/memory/."""
    home = Path.home().resolve()
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists():
            slug = _slugify(parent.name)
            return home / ".claude" / "projects" / slug / "memory"
    return home / ".claude" / "global" / "memory"


def find_hot_list_target(memory_dir, scope_label):
    """Return CLAUDE.md (global) or MEMORY.md (project) path."""
    home = Path.home().resolve()
    if scope_label == "global":
        return home / ".claude" / "CLAUDE.md"
    else:
        return home / ".claude" / "projects" / scope_label / "MEMORY.md"


# ── CLI Entry Point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Memory Graph v2 — sync engine")
    parser.add_argument("--fix", action="store_true", help="Remove broken references")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes, no writes")
    parser.add_argument("--audit", action="store_true", help="Find contradiction candidates")
    parser.add_argument("--json", action="store_true", help="Output INDEX.json to stdout")
    parser.add_argument("--hit", metavar="SLUG", help="Record a recall hit for a memory")
    parser.add_argument("--scope-from-file", metavar="PATH",
                        help="Detect scope from file path (for PostToolUse hook)")
    args = parser.parse_args()

    if args.hit:
        record_hit(args.hit)
        print(f"Hit recorded for: {args.hit}")
        return

    # Detect scope
    memory_dir, scope_label = detect_scope(
        cwd=Path.cwd(), scope_from_file=args.scope_from_file
    )
    if not memory_dir.exists():
        memory_dir.mkdir(parents=True, exist_ok=True)

    # Load previous state
    prev_index = load_previous_index(memory_dir / "INDEX.json")

    # Scan
    records, changed_slugs, affected_slugs = scan_memory_dir(memory_dir, prev_index)
    if not records:
        if not args.dry_run:
            (memory_dir / "INDEX.md").write_text("# Memory Index\n\n*0 memories*\n", encoding="utf-8")
            (memory_dir / "INDEX.json").write_text(json.dumps({"project": "memory", "files": {}, "warnings": [], "graph": {}}, indent=2), encoding="utf-8")
            hot_target = find_hot_list_target(memory_dir, scope_label)
            if hot_target:
                inject_hot_list([], {}, hot_target, scope_label)  # clears to empty list
        msg = json.dumps({"warnings": [], "message": "No memories found"}, indent=2) if args.json else "No memories found."
        print(msg)
        return

    # Build graph
    graph = build_graph(records)
    in_deg, out_deg = compute_degrees(graph)
    known_slugs = set(graph.keys())
    known_slugs.update(prev_index.get("files", {}).keys())

    # Validate + score
    all_warnings = []
    scores = {}
    for r in records:
        meta = r.get("metadata", {})
        body = r.get("body", "")
        stored_hash = r.get("stored_hash", "")
        slug = r["slug"]
        r["_filepath"] = memory_dir / f"{slug}.md"

        warns, actual_hash = validate(meta, body, stored_hash, known_slugs, r["_filepath"])
        all_warnings.extend(warns)
        r["_actual_hash"] = actual_hash
        r["sync_status"] = (
            "needs-review" if any(w["level"] == "error" for w in warns)
            else "stale" if any(w["level"] == "warning" for w in warns)
            else "synced"
        )
        meta["_filepath"] = r["_filepath"]
        prev_entry = prev_index.get("files", {}).get(slug, {})
        last_hit = prev_entry.get("last_hit")
        scores[slug] = compute_score(meta, in_deg.get(slug, 0), out_deg.get(slug, 0), last_hit)

    # --fix
    if args.fix:
        fixes = apply_fixes(records, known_slugs, memory_dir, dry_run=args.dry_run)
        if fixes:
            for f in fixes:
                print(f"  FIXED [{f['check']}] {f['slug']}: {f['detail']}")
            # Re-validate after fixes
            graph = build_graph(records)
            in_deg, out_deg = compute_degrees(graph)
            known_slugs = set(graph.keys()) | set(prev_index.get("files", {}).keys())
            all_warnings = []
            for r in records:
                warns, _ = validate(r["metadata"], r["body"], r.get("stored_hash", ""),
                                   known_slugs, r["_filepath"])
                all_warnings.extend(warns)
                r["metadata"]["_filepath"] = r["_filepath"]
                prev_entry_fix = prev_index.get("files", {}).get(r["slug"], {})
                last_hit_fix = prev_entry_fix.get("last_hit")
                scores[r["slug"]] = compute_score(r["metadata"], in_deg.get(r["slug"], 0),
                                                   out_deg.get(r["slug"], 0), last_hit_fix)

    # --audit
    if args.audit:
        candidates = audit_candidates(records)
        if args.json:
            print(json.dumps({"candidates": candidates}, indent=2, ensure_ascii=False))
            return
        if candidates:
            print("\nAudit Candidates:")
            for c in candidates:
                print(f"  {c['a']} ↔ {c['b']} — shared refs: {c['shared_refs']}, discuss: {c['shared_headings']}")
        else:
            print("No audit candidates found.")
        return

    # Print read-when hints for weak memories
    for r in records:
        rw = r.get("metadata", {}).get("read_when", []) or []
        if not rw or all(len(p.strip()) < 10 for p in rw):
            hints = suggest_read_when(r.get("body", ""))
            if hints:
                print(f"\n  Read-when hints for '{r['slug']}':")
                for h in hints:
                    print(f"    - {h}")

    # Store updated dates before writeback pops _filepath
    for r in records:
        fp = memory_dir / f"{r['slug']}.md"
        if fp.exists():
            r["_updated_str"] = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")

    # Writeback content_hash to INDEX.json (not to .md files)
    if not args.dry_run:
        writeback_content_hash(records, memory_dir)

    # Generate INDEX.md
    index_md = build_index_md(records, all_warnings, scores, in_deg, out_deg)
    index_json = build_index_json("memory", memory_dir, records, all_warnings, scores, in_deg, out_deg, graph, prev_index)

    if args.json:
        print(index_json)
        return

    if not args.dry_run:
        (memory_dir / "INDEX.md").write_text(index_md, encoding="utf-8")
        (memory_dir / "INDEX.json").write_text(index_json, encoding="utf-8")

        # Inject hot list (auto-creates target file + markers if needed)
        hot_target = find_hot_list_target(memory_dir, scope_label)
        if hot_target:
            inject_hot_list(records, scores, hot_target, scope_label)

        print(f"INDEX.md written ({len(records)} memories)")

    # Summary
    synced = sum(1 for r in records if r["sync_status"] == "synced")
    stale = sum(1 for r in records if r["sync_status"] == "stale")
    review = sum(1 for r in records if r["sync_status"] == "needs-review")
    errors_n = sum(1 for w in all_warnings if w["level"] == "error")
    warns_n = sum(1 for w in all_warnings if w["level"] == "warning")
    print(f"  synced: {synced}  |  stale: {stale}  |  needs-review: {review}")
    print(f"  errors: {errors_n}  |  warnings: {warns_n}")

    if all_warnings:
        print(f"\n{len(all_warnings)} issue(s):")
        for w in all_warnings:
            print(f"  [{w['level'].upper()}] [{w['check']}] {w['detail']}")


if __name__ == "__main__":
    main()
