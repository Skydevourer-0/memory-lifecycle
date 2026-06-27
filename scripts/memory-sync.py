"""
memory-sync.py — Claude Code Memory Synchronization Script
Core utilities: frontmatter parser, hashing, TTL management.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

REQUIRED_FIELDS = {"name", "description"}
VALID_TYPES = {"user", "feedback", "project", "reference"}
VALID_CONFIDENCES = {"confirmed", "speculative", "deprecated"}
CONFIDENCE_MAP = {"confirmed": 3, "speculative": 1, "deprecated": -5}
TTL_PATTERN = re.compile(r"^(\d+)\s*([dmy])$", re.IGNORECASE)
TTL_MULTIPLIER = {"d": 1, "m": 30, "y": 365}
KEBAB_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
WIKI_LINK_PATTERN = re.compile(r"\[\[([a-z][a-z0-9-]*)\]\]")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
INLINE_CODE_PATTERN = re.compile(r"`([^`\n]+?)`")
FENCED_BLOCK_PATTERN = re.compile(r"```.*?```", re.DOTALL)

# ── Frontmatter Parser ───────────────────────────────────────────────────────

def _parse_yaml_value(value: str):
    """Parse a single YAML-like scalar value."""
    value = value.strip().strip("\"'")
    if not value:
        return None
    if value == "[]":
        return []
    if value.lower() in ("null", "none", "~"):
        return None
    # List literal: [a, b, c]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
    # Boolean
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    # Integer
    try:
        return int(value)
    except ValueError:
        pass
    # Float
    try:
        return float(value)
    except ValueError:
        pass
    # Plain string
    return value


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-like frontmatter between --- delimiters.

    Handles both inline format (tags: [a, b]) and YAML block format
    (tags:\\n  - a\\n  - b). Also handles Claude Code's native format
    where extended fields are nested under 'metadata:'.

    Returns (metadata_dict, body_string).
    If no frontmatter is found, returns ({}, text.strip()).
    """
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    # Find the closing --- delimiter
    end_idx = text.find("---", 3)
    if end_idx == -1:
        return {}, text

    raw_front = text[3:end_idx].strip()
    body = text[end_idx + 3:].strip()

    meta: dict = {}
    lines = raw_front.split("\n")
    current_key: str | None = None
    current_list: list | None = None
    current_list_parent: str | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # YAML block list item: "- value"
        if stripped.startswith("- "):
            item_val = _parse_yaml_value(stripped[2:])
            if current_list is not None:
                current_list.append(item_val)
            continue

        if indent == 0:
            # Top-level key: value or key:
            current_list = None
            current_list_parent = None
            if ":" not in stripped:
                current_key = None
                continue
            key, _, raw_val = stripped.partition(":")
            key = key.strip()
            raw_val = raw_val.strip()
            current_key = key

            val = _parse_yaml_value(raw_val)
            if val is None and raw_val == "":
                # Bare key — may start a block list or nested dict
                meta[key] = {}
                current_list = None
            else:
                meta[key] = val

        elif indent > 0 and current_key is not None:
            if ":" in stripped:
                sub_key, _, raw_val = stripped.partition(":")
                sub_key = sub_key.strip()
                raw_val = raw_val.strip()

                # Ensure parent is a dict
                parent = meta.get(current_key)
                if not isinstance(parent, dict):
                    meta[current_key] = {}
                    parent = meta[current_key]

                val = _parse_yaml_value(raw_val)
                if val is None and raw_val == "":
                    # Bare sub-key — may start a list
                    parent[sub_key] = []
                    current_list = parent[sub_key]
                    current_list_parent = sub_key
                else:
                    parent[sub_key] = val
                    current_list = None
                    current_list_parent = None

    # Post-parse: flatten metadata.* to top level
    # Claude Code nests tags/context/references/confidence/priority/etc under 'metadata:'
    if isinstance(meta.get("metadata"), dict):
        inner = meta["metadata"]
        # Preserve type for validation (metadata.type is the canonical location)
        if "type" in inner and "metadata" not in meta:
            meta["metadata"] = {}
        # Flatten known fields to top level, skip internal fields
        for k, v in inner.items():
            if k in ("node_type", "originSessionId"):
                continue  # Claude Code internal fields — silently ignored
            if k == "type":
                meta.setdefault("metadata", {})["type"] = v
            elif k not in meta:
                meta[k] = v

    return meta, body


# ── Hashing ──────────────────────────────────────────────────────────────────

def file_hash(text: str) -> str:
    """Return SHA-256 of full file text (minus content_hash line), first 16 hex chars.

    The content_hash line is stripped before hashing to avoid a self-referential
    loop — otherwise writing a new hash would change the file, invalidating the
    hash on the next run.  This also means frontmatter metadata changes (tags,
    description, etc.) are detected as changes, not just body changes.
    """
    text_without_hash = re.sub(r'^\s*content_hash:.*\n?', '', text, flags=re.MULTILINE)
    return hashlib.sha256(text_without_hash.encode("utf-8")).hexdigest()[:16]


# ── TTL Utilities ────────────────────────────────────────────────────────────

def parse_ttl(ttl_str: str | None) -> timedelta | None:
    """Parse patterns like '90d', '6m', '1y' into timedelta.

    Returns None when ttl_str is None or does not match the expected pattern.
    """
    if ttl_str is None:
        return None
    match = TTL_PATTERN.match(ttl_str.strip())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    return timedelta(days=value * TTL_MULTIPLIER[unit])


def is_expired(created_str: str | None, ttl_str: str | None) -> bool:
    """Return True if the current time exceeds created + ttl.

    Handles date-only ("2026-06-20") and full ISO-format strings.
    Returns False when either argument is None or unparseable.
    """
    if created_str is None or ttl_str is None:
        return False
    ttl = parse_ttl(ttl_str)
    if ttl is None:
        return False
    try:
        created = datetime.fromisoformat(created_str)
    except (ValueError, TypeError):
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return now > (created + ttl)


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


# ── Helper: Days Since ────────────────────────────────────────────────────────

def _days_since(date_str):
    """Return days since given date string, or None if unparseable."""
    if not date_str:
        return None
    try:
        dt_str = str(date_str).replace("Z", "+00:00")
        if "T" not in dt_str:
            dt_str = dt_str + "T00:00:00+00:00"
        dt = datetime.fromisoformat(dt_str)
        return (datetime.now(timezone.utc) - dt).days
    except (ValueError, TypeError):
        return None


# ── Inline Code Extraction ──────────────────────────────────────────────────

def _extract_inline_terms(body: str) -> list[str]:
    """Extract inline-code entities from body text.

    Strips fenced code blocks first, then matches `...` patterns.
    Filters out code blocks, pure non-ASCII terms, and terms >64 chars.
    Deduplicates and returns a sorted list.
    """
    if not body:
        return []
    # Strip fenced code blocks
    clean = FENCED_BLOCK_PATTERN.sub("", body)
    matches = INLINE_CODE_PATTERN.findall(clean)
    seen = set()
    terms = []
    for m in matches:
        term = m.strip()
        # Skip empty, too long, or single-character terms (noise)
        if not term or len(term) > 64 or len(term) < 2:
            continue
        # Must contain at least one ASCII letter or digit
        if not re.search(r'[A-Za-z0-9]', term):
            continue
        # Skip markdown headers copied into backticks
        if term.startswith("#"):
            continue
        # Skip purely numeric terms
        if re.match(r'^[0-9]+$', term):
            continue
        if term not in seen:
            seen.add(term)
            terms.append(term)
    return sorted(terms, key=str.lower)


# ── Validation Engine ─────────────────────────────────────────────────────────

def validate(meta, body, stored_hash, known_slugs, in_degree, full_text=""):
    """Perform deterministic validation checks on a memory record.

    Returns (warnings_list, actual_hash).
    """
    warnings = []
    actual_hash = file_hash(full_text) if full_text else file_hash(body)

    name = meta.get("name") if isinstance(meta.get("name"), str) else None
    description = meta.get("description") if isinstance(meta.get("description"), str) else None
    meta_type = meta.get("metadata", {}).get("type") if isinstance(meta.get("metadata"), dict) else None
    confidence = meta.get("confidence")
    created = meta.get("created")
    ttl = meta.get("ttl")
    updated = meta.get("updated")
    references = meta.get("references", []) if isinstance(meta.get("references"), list) else []
    context = meta.get("context", []) if isinstance(meta.get("context"), list) else []
    tags = meta.get("tags", []) if isinstance(meta.get("tags"), list) else []

    # 1. Missing required (error)
    if not name or not description:
        warnings.append({
            "level": "error",
            "check": "missing-required",
            "detail": "name or description is missing",
            "suggestion": "Add both name and description fields"
        })

    # 2. Invalid name (error)
    if name and not KEBAB_PATTERN.match(name):
        warnings.append({
            "level": "error",
            "check": "invalid-name",
            "detail": "name '{0}' is not kebab-case".format(name),
            "suggestion": "Use lowercase letters, digits, and hyphens only (e.g., 'my-memory')"
        })

    # 3. Invalid metadata.type (error)
    if meta_type is not None and meta_type not in VALID_TYPES:
        warnings.append({
            "level": "error",
            "check": "invalid-type",
            "detail": "metadata.type '{0}' is not valid".format(meta_type),
            "suggestion": "Use one of: {0}".format(", ".join(sorted(VALID_TYPES)))
        })

    # 4. Stale hash (warning)
    if stored_hash and stored_hash != actual_hash:
        warnings.append({
            "level": "warning",
            "check": "stale-hash",
            "detail": "stored hash does not match body hash",
            "suggestion": "Update the content_hash field in the index"
        })

    # 5. TTL expired (warning)
    if is_expired(created, ttl):
        warnings.append({
            "level": "warning",
            "check": "ttl-expired",
            "detail": "memory has expired (created={0}, ttl={1})".format(created, ttl),
            "suggestion": "Review and update the memory or extend its TTL"
        })

    # 6. Broken references (error)
    body_links = WIKI_LINK_PATTERN.findall(body) if body else []
    ref_slugs = body_links + references
    broken = [slug for slug in ref_slugs if slug not in known_slugs]
    if broken:
        warnings.append({
            "level": "error",
            "check": "broken-references",
            "detail": "references to unknown slugs: {0}".format(broken),
            "suggestion": "Create memories for: {0}".format(", ".join(broken))
        })

    # 7. Orphan confirmed (warning)
    if confidence == "confirmed" and in_degree == 0:
        days = _days_since(updated)
        if days is not None and days > 180:
            warnings.append({
                "level": "warning",
                "check": "orphan-confirmed",
                "detail": "confirmed memory with no incoming references, last updated {0} days ago (>180)".format(days),
                "suggestion": "Consider deprecating or linking this memory to others"
            })

    # 8. Upgradable speculative (info)
    if confidence == "speculative" and in_degree >= 3:
        warnings.append({
            "level": "info",
            "check": "upgradable-speculative",
            "detail": "speculative memory has {0} incoming references (>=3)".format(in_degree),
            "suggestion": "Consider upgrading confidence to 'confirmed'"
        })

    # 9. Missing context (info)
    if not context:
        warnings.append({
            "level": "info",
            "check": "missing-context",
            "detail": "context array is empty or missing",
            "suggestion": "Add related memory slugs to the context field"
        })

    # 10. Empty tags (info)
    if not tags:
        warnings.append({
            "level": "info",
            "check": "empty-tags",
            "detail": "tags array is empty or missing",
            "suggestion": "Add relevant tags for discoverability"
        })

    # 11. Body headings not reflected in tags (warning)
    # Extract heading terms — named entities (commands, functions, components)
    # that should be discoverable via tag-based recall. Kebab-case filter
    # excludes noise (Chinese headings, generic prose).
    headings = HEADING_PATTERN.findall(body) if body else []
    heading_terms = []
    for level_str, heading_text in headings:
        # Only entity-style headings with separators (e.g., "### ndd — quick editor").
        # Plain labels like "Problem" or "Why" are section markers, not entities.
        if not re.search(r'[—:–]', heading_text):
            continue
        # Extract the first term before any separator (em-dash, en-dash, colon)
        # NOTE: hyphen (-) is NOT a separator — it is part of kebab-case names
        term = re.split(r'\s*[—:–]\s*', heading_text.strip())[0].strip()
        # Only flag if it looks like a taggable keyword (lowercase ascii,
        # alphanumeric with hyphens, no spaces)
        if term and re.match(r'^[a-z][a-z0-9-]*$', term.lower()):
            heading_terms.append(term.lower())

    tags_lower = [t.lower() for t in tags]
    missing_from_tags = [t for t in heading_terms if t not in tags_lower]
    if missing_from_tags:
        warnings.append({
            "level": "warning",
            "check": "heading-not-in-tags",
            "detail": "body headings found that are missing from metadata.tags: {0}".format(missing_from_tags),
            "suggestion": "Add these terms to the tags field so they are discoverable by the recall system: {0}".format(", ".join(missing_from_tags))
        })

    # 12. Inline code terms missing from context (info)
    inline_terms = _extract_inline_terms(body)
    context_lower = [str(c).lower() for c in context if c]
    missing_context = [t for t in inline_terms if t.lower() not in context_lower]
    if missing_context:
        warnings.append({
            "level": "info",
            "check": "inline-code-not-in-context",
            "detail": "inline `code` entities found that are missing from metadata.context: {0}".format(missing_context),
            "suggestion": "Add these terms to the context field for extended discoverability: {0}".format(", ".join(missing_context))
        })

    # 13. Feedback/project memories missing required structure (info)
    # Per memory spec: type: feedback and type: project must include
    # **Why:** and **How to apply:** sections in the body.
    if meta_type in ("feedback", "project"):
        missing_sections = []
        if "**Why:**" not in body and "**Why: **" not in body:
            missing_sections.append("Why")
        if "**How to apply:**" not in body and "**How to apply: **" not in body:
            missing_sections.append("How to apply")
        if missing_sections:
            warnings.append({
                "level": "info",
                "check": "feedback-missing-structure",
                "detail": "type: {0} memory is missing required sections: {1}".format(
                    meta_type, ", ".join(missing_sections)),
                "suggestion": "Add **Why:** and **How to apply:** sections to the body. This is a semantic requirement — the script cannot fix it automatically.",
            })

    # 14. Reference memories without entity-style headings (info)
    # Entity headings ("### ndd — description") enable automatic tag extraction.
    # Memories with only section labels ("## Overview") produce no taggable terms.
    if meta_type == "reference":
        headings = HEADING_PATTERN.findall(body) if body else []
        has_entity = any(re.search(r'[—:–]', h[1]) for h in headings)
        if not has_entity:
            warnings.append({
                "level": "info",
                "check": "reference-missing-entity-headings",
                "detail": "No entity-style headings found (e.g., '### name — description'). Section labels like '## Overview' are not taggable by check #11.",
                "suggestion": "Add at least one entity heading with a separator (—, :, or –) so key terms can be automatically extracted as tags.",
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
            if slug_ref not in seen:
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

def compute_score(meta, in_deg, out_deg):
    """Compute a memory score per spec section 4.

    score = priority + in_deg * 2.0 + out_deg * 0.5 + confidence_map + max(0, 10 - days_since_updated)
    Rounded to 1 decimal place.
    """
    # Priority: try int conversion, default 3
    priority_raw = meta.get("priority", 3)
    try:
        priority = int(priority_raw)
    except (ValueError, TypeError):
        priority = 3

    # Confidence map value
    confidence = meta.get("confidence", "speculative")
    confidence_val = CONFIDENCE_MAP.get(confidence, 1)

    # Days since updated
    updated = meta.get("updated")
    days = _days_since(updated)
    if days is None:
        days = 0
    days_bonus = max(0, 10 - days)

    score = priority + in_deg * 2.0 + out_deg * 0.5 + confidence_val + days_bonus
    return round(score, 1)


def score_tier(score):
    """Return the tier label for a given score."""
    if score >= 12:
        return "high"
    elif score >= 5:
        return "normal"
    else:
        return "low"


# ── INDEX.md Generator ────────────────────────────────────────────────────────

def build_index_md(records, warnings, scores, in_deg, out_deg, memory_dir):
    """Generate INDEX.md content from records and analysis data.

    Returns a markdown string with memories grouped by tier and warnings.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append("# Memory Index")
    lines.append(f"*{now_str} · {len(records)} memories · {len(warnings)} warnings*")
    lines.append("")

    # Group records by tier
    tier_records = {"high": [], "normal": [], "low": []}
    for record in records:
        slug = record["slug"]
        score = scores.get(slug, 0)
        tier = score_tier(score)
        tier_records[tier].append((score, record))

    # Sort each tier by score descending
    for tier in tier_records:
        tier_records[tier].sort(key=lambda x: x[0], reverse=True)

    # Tier display metadata
    tier_config = [
        ("high", "## R Priority (score >= 12)", "*Always load*"),
        ("normal", "## Y Normal (score 5–11)", "*Context-match*"),
        ("low", "## G Low (score < 5)", "*Exact-match only*"),
    ]

    for tier_key, heading, subtitle in tier_config:
        entries = tier_records[tier_key]
        if not entries:
            continue
        lines.append(heading)
        lines.append(subtitle)
        for score, record in entries:
            slug = record["slug"]
            meta = record.get("metadata", {})
            description = meta.get("description", "")
            updated = meta.get("updated", "")
            in_d = in_deg.get(slug, 0)
            out_d = out_deg.get(slug, 0)
            lines.append(f"- [{slug}]({slug}.md) — {description}  score:{score}  updated:{updated}  in:{in_d} out:{out_d}")
        lines.append("")

    # Warnings section
    if warnings:
        lines.append("## Warnings")
        lines.append("")

        level_order = {"error": 0, "warning": 1, "info": 2}
        level_prefix = {"error": "ERR", "warning": "WARN", "info": "INFO"}
        sorted_warnings = sorted(warnings, key=lambda w: level_order.get(w.get("level", "info"), 3))

        for w in sorted_warnings:
            level = w.get("level", "info")
            prefix = level_prefix.get(level, level.upper())
            check = w.get("check", "")
            detail = w.get("detail", "")
            suggestion = w.get("suggestion", "")
            lines.append(f"- **{prefix}** [{check}] {detail}")
            lines.append(f"  → {suggestion}")

        lines.append("")

    return "\n".join(lines)


# ── INDEX.json Generator ──────────────────────────────────────────────────────

def build_index_json(project_name, memory_dir, records, warnings, scores, in_deg, out_deg, graph):
    """Generate INDEX.json content from records and analysis data.

    Returns a JSON string with memory metadata, warnings, and citation graph.
    """
    files = {}
    for record in records:
        slug = record["slug"]
        meta = record.get("metadata", {})
        files[slug] = {
            "path": f"memory/{slug}.md",
            "hash": record.get("stored_hash", ""),
            "name": meta.get("name", ""),
            "description": meta.get("description", ""),
            "metadata_type": meta.get("metadata", {}).get("type", ""),
            "tags": meta.get("tags", []),
            "context": meta.get("context", []),
            "references": meta.get("references", []),
            "confidence": meta.get("confidence", ""),
            "priority": meta.get("priority", 0),
            "ttl": meta.get("ttl", ""),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
            "in_degree": in_deg.get(slug, 0),
            "out_degree": out_deg.get(slug, 0),
            "score": scores.get(slug, 0),
            "skill": (record.get("metadata", {}).get("metadata") or {}).get("skill", ""),
            "sync_status": record.get("sync_status", ""),
        }

    # Filter graph to only include targets that exist as keys
    existing_slugs = {record["slug"] for record in records}
    filtered_graph = {}
    for slug, refs in graph.items():
        filtered_refs = [ref for ref in refs if ref in existing_slugs]
        filtered_graph[slug] = filtered_refs

    result = {
        "project": project_name,
        "memory_dir": str(memory_dir),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "warnings": warnings,
        "graph": filtered_graph,
    }

    return json.dumps(result, indent=2, ensure_ascii=False)


# ── Memory Directory Scanner ──────────────────────────────────────────────────

def _prev_to_meta(prev_entry):
    """Convert a flat INDEX.json file entry back to nested metadata format."""
    meta = {
        "name": prev_entry.get("name", ""),
        "description": prev_entry.get("description", ""),
        "tags": prev_entry.get("tags", []),
        "context": prev_entry.get("context", []),
        "references": prev_entry.get("references", []),
        "confidence": prev_entry.get("confidence", "speculative"),
        "priority": prev_entry.get("priority", 3),
        "ttl": prev_entry.get("ttl"),
        "created": prev_entry.get("created"),
        "updated": prev_entry.get("updated"),
    }
    meta["metadata"] = {"type": prev_entry.get("metadata_type", "")}
    return meta


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
        text = fp.read_text(encoding="utf-8")

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
                text = af_file.read_text(encoding="utf-8")
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
    """Write updated content_hash values back into memory files.

    Only modifies the content_hash line in frontmatter; all other content
    is left untouched.  Sets record['stored_hash'] and cleans up temp keys.
    """
    for record in records:
        actual_hash = record.get("_actual_hash")
        filepath = record.get("_filepath")
        if not actual_hash or not filepath:
            continue

        content = filepath.read_text(encoding="utf-8")

        if content.startswith("---"):
            end_idx = content.find("---", 3)
            if end_idx == -1:
                # Malformed frontmatter — skip writeback for this file
                record["stored_hash"] = actual_hash
                record.pop("_actual_hash", None)
                record.pop("_filepath", None)
                continue

            frontmatter = content[3:end_idx]
            rest = content[end_idx:]

            if re.search(r"^\s*content_hash:", frontmatter, re.MULTILINE):
                # Replace existing hash value (preserving original indentation)
                new_frontmatter = re.sub(
                    r"^(\s*)content_hash:\s*\S*",
                    r"\1content_hash: {0}".format(actual_hash),
                    frontmatter,
                    flags=re.MULTILINE,
                )
                new_content = "---" + new_frontmatter + rest
            else:
                # Insert before closing ---
                new_content = content[:end_idx] + f"content_hash: {actual_hash}\n" + content[end_idx:]
        else:
            # No frontmatter — nothing to write
            new_content = content

        filepath.write_text(new_content, encoding="utf-8")
        record["stored_hash"] = actual_hash
        record.pop("_actual_hash", None)
        record.pop("_filepath", None)


# ── Fix Engine ────────────────────────────────────────────────────────────────

def _tag_insert_positions(lines):
    """Return (tags_key_idx, first_item_idx, last_item_idx, indent, item_prefix).

    tags_key_idx:  the line ending with 'tags:'
    first_item_idx: first indented '- tag' line, or -1 if no tags exist
    last_item_idx:  last indented '- tag' line, or -1
    indent:         whitespace prefix of the 'tags:' line
    item_prefix:    whitespace before '- tag' items (indent + 4 spaces)
    """
    tags_key_idx = -1
    first_item_idx = -1
    last_item_idx = -1
    indent = ""
    item_prefix = ""

    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if tags_key_idx < 0 and stripped.endswith("tags:"):
            tags_key_idx = i
            indent = line[: len(line) - len(line.lstrip())]
            item_prefix = indent + "  "
        elif tags_key_idx >= 0:
            if line.startswith(item_prefix) and line.lstrip().startswith("- "):
                if first_item_idx < 0:
                    first_item_idx = i
                last_item_idx = i
            elif line.startswith(indent) and not line.lstrip().startswith("- "):
                # Reached next same-indent key — end of tags block
                break

    return tags_key_idx, first_item_idx, last_item_idx, indent, item_prefix


def _fix_tags_on_disk(filepath, tags_to_add):
    """Append new tags to the frontmatter tags block."""
    text = filepath.read_text(encoding="utf-8")
    lines = text.split("\n")

    tags_key_idx, first_item_idx, last_item_idx, indent, item_prefix = (
        _tag_insert_positions(lines)
    )

    if tags_key_idx < 0:
        return  # no tags: key — shouldn't happen for valid files

    new_lines = [f"{item_prefix}- {t}" for t in tags_to_add]

    if last_item_idx >= 0:
        # Insert after last existing tag
        for i, nl in enumerate(new_lines):
            lines.insert(last_item_idx + 1 + i, nl)
    else:
        # No tags yet — insert after tags: line
        for i, nl in enumerate(new_lines):
            lines.insert(tags_key_idx + 1 + i, nl)

    filepath.write_text("\n".join(lines), encoding="utf-8")


def _fix_context_on_disk(filepath, items_to_add):
    """Append items to the frontmatter context block."""
    text = filepath.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Reuse tag insertion logic — context has identical YAML block structure
    ctx_key_idx = -1
    first_item_idx = -1
    last_item_idx = -1
    indent = ""
    item_prefix = ""

    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if ctx_key_idx < 0 and stripped.endswith("context:"):
            ctx_key_idx = i
            indent = line[: len(line) - len(line.lstrip())]
            item_prefix = indent + "  "
        elif ctx_key_idx >= 0:
            if line.startswith(item_prefix) and line.lstrip().startswith("- "):
                if first_item_idx < 0:
                    first_item_idx = i
                last_item_idx = i
            elif line.startswith(indent) and not line.lstrip().startswith("- "):
                break

    if ctx_key_idx < 0:
        return

    # Quote values that would break YAML parsing (contain ", [, ], :, #, etc.)
    yaml_safe = []
    for t in items_to_add:
        if re.search(r'["\[\]{}:#]', t):
            escaped = t.replace('"', '\\"')
            yaml_safe.append(f'"{escaped}"')
        else:
            yaml_safe.append(t)

    new_lines = [f"{item_prefix}- {v}" for v in yaml_safe]

    if last_item_idx >= 0:
        for i, nl in enumerate(new_lines):
            lines.insert(last_item_idx + 1 + i, nl)
    else:
        for i, nl in enumerate(new_lines):
            lines.insert(ctx_key_idx + 1 + i, nl)

    filepath.write_text("\n".join(lines), encoding="utf-8")


def _fix_references_on_disk(filepath, new_refs):
    """Replace the frontmatter references list with new_refs."""
    text = filepath.read_text(encoding="utf-8")
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


def apply_fixes(records, known_slugs, memory_dir, dry_run=False):
    """Apply automatic fixes for fixable warnings.

    Returns a list of fix descriptions applied.
    """
    fixes = []

    for record in records:
        slug = record["slug"]
        filepath = record.get("_filepath")
        if not filepath:
            continue

        meta = record.get("metadata", {})
        body = record.get("body", "")
        tags = meta.get("tags") or []
        references = meta.get("references") or []

        # Fix 1: Remove broken references
        broken = [r for r in references if r not in known_slugs]
        if broken:
            new_refs = [r for r in references if r not in broken]
            fixes.append({
                "slug": slug,
                "check": "broken-references",
                "detail": "Removed broken references: {0}".format(broken),
            })
            if not dry_run:
                _fix_references_on_disk(filepath, new_refs)
            record["metadata"]["references"] = new_refs

        # Fix 2: Add heading terms to tags
        headings = HEADING_PATTERN.findall(body) if body else []
        heading_terms = []
        for _level_str, heading_text in headings:
            if not re.search(r'[—:–]', heading_text):
                continue
            term = re.split(r'\s*[—:–]\s*', heading_text.strip())[0].strip()
            if term and re.match(r'^[a-z][a-z0-9-]*$', term.lower()):
                heading_terms.append(term.lower())

        tags_lower = [t.lower() for t in tags]
        missing = [t for t in heading_terms if t not in tags_lower]
        if missing:
            fixes.append({
                "slug": slug,
                "check": "heading-not-in-tags",
                "detail": "Added to tags: {0}".format(missing),
            })
            if not dry_run:
                _fix_tags_on_disk(filepath, missing)
            record["metadata"]["tags"] = tags + missing

        # Fix 3: Add inline code entities to context
        inline_terms = _extract_inline_terms(body)
        context_lower = [str(c).lower() for c in (meta.get("context") or []) if c]
        missing_context = [t for t in inline_terms if t.lower() not in context_lower]
        if missing_context:
            new_context = (meta.get("context") or []) + missing_context
            fixes.append({
                "slug": slug,
                "check": "inline-code-not-in-context",
                "detail": "Added to context: {0}".format(missing_context),
            })
            if not dry_run:
                _fix_context_on_disk(filepath, missing_context)
            record["metadata"]["context"] = new_context

        # Fix 4: Add body wiki-links to references when target exists
        body_links = WIKI_LINK_PATTERN.findall(body) if body else []
        existing_refs = set(references)
        new_ref_links = [
            l for l in body_links
            if l in known_slugs and l != slug and l not in existing_refs
        ]
        if new_ref_links:
            new_refs = references + new_ref_links
            fixes.append({
                "slug": slug,
                "check": "wiki-link-not-in-references",
                "detail": "Added body wiki-links to references: {0}".format(new_ref_links),
            })
            if not dry_run:
                _fix_references_on_disk(filepath, new_refs)
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
    # Only knowledge memories
    knowledge = []
    for r in records:
        mt = (r.get("metadata", {}).get("metadata") or {}).get("type")
        if mt != "task":
            knowledge.append(r)

    candidates = []
    for i in range(len(knowledge)):
        for j in range(i + 1, len(knowledge)):
            a = knowledge[i]
            b = knowledge[j]

            meta_a = a.get("metadata", {})
            meta_b = b.get("metadata", {})

            tags_a = set(t.lower() for t in (meta_a.get("tags") or []) if t)
            tags_b = set(t.lower() for t in (meta_b.get("tags") or []) if t)
            shared_tags = sorted(tags_a & tags_b)

            if len(shared_tags) < 2:
                continue

            headings_a = _extract_heading_terms(a.get("body", ""))
            headings_b = _extract_heading_terms(b.get("body", ""))
            shared_headings = sorted(headings_a & headings_b)

            if not shared_headings:
                continue

            candidates.append({
                "a": a["slug"],
                "b": b["slug"],
                "shared_tags": shared_tags,
                "shared_headings": shared_headings,
                "updated_a": meta_a.get("updated", ""),
                "updated_b": meta_b.get("updated", ""),
                "suggestion": "Both discuss {0} — verify consistency".format(
                    ", ".join(shared_headings)),
            })

    return candidates


# ── CLI Entry Point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Claude Code Memory Sync")
    parser.add_argument("--files", nargs="*", help="Specific memory files to sync")
    parser.add_argument("--project", help="Path to project .claude directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--dry-run", action="store_true", help="Don't write files")
    parser.add_argument("--fix", action="store_true", help="Auto-fix warnings in memory files")
    parser.add_argument("--global", dest="global_dir", metavar="DIR",
                        help="Sync global memory directory (e.g., ~/.claude/memory/)")
    parser.add_argument("--migrate-to-global", nargs=2, metavar=("SRC", "DEST"),
                        help="Migrate memories from pseudo-project to global directory")
    parser.add_argument("--audit", action="store_true",
                        help="Find memory pairs that may have conflicting information")
    args = parser.parse_args()

    # ── Migration mode ────────────────────────────────────────────────────
    if args.migrate_to_global:
        src_dir = Path(args.migrate_to_global[0])
        dest_dir = Path(args.migrate_to_global[1])
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not src_dir.exists():
            print(f"Source directory not found: {src_dir}", file=sys.stderr)
            sys.exit(1)

        count = 0
        for md_file in sorted(src_dir.glob("*.md")):
            if md_file.name in ("INDEX.md", "MEMORY.md"):
                continue
            dest_file = dest_dir / md_file.name
            dest_file.write_text(md_file.read_text(encoding="utf-8"), encoding="utf-8")
            md_file.unlink()
            count += 1
            print(f"  migrated: {md_file.name}")

        # Remove source dir if empty of .md files
        remaining = list(src_dir.glob("*.md"))
        if not remaining:
            try:
                src_dir.rmdir()
            except OSError:
                pass  # non-empty or permissions

        print(f"Migrated {count} memories: {src_dir} -> {dest_dir}")
        # Run sync on destination to rebuild INDEX
        memory_dir = dest_dir
        args.dry_run = False  # always write after migration

    # Determine memory directory
    elif args.global_dir:
        memory_dir = Path(args.global_dir)
    elif args.project:
        memory_dir = Path(args.project) / "memory"
    else:
        cwd = Path.cwd()
        project_slug = re.sub(r"[^A-Za-z0-9-]", "-", str(cwd).replace(":", "").replace("\\", "-"))
        memory_dir = Path.home() / ".claude" / "projects" / project_slug / "memory"

    if not memory_dir.exists() and args.migrate_to_global:
        pass  # just created by migration
    elif not memory_dir.exists():
        print(f"Memory directory not found: {memory_dir}", file=sys.stderr)
        sys.exit(1)

    # Load previous state
    prev_index = load_previous_index(memory_dir / "INDEX.json")

    # Scan
    records, changed_slugs, affected_slugs = scan_memory_dir(memory_dir, prev_index, args.files)

    if not records:
        msg = json.dumps({"warnings": [], "message": "No memories found"}, indent=2) if args.json else "No memories found."
        print(msg)
        return

    # Build graph, compute degrees
    graph = build_graph(records)
    in_deg, out_deg = compute_degrees(graph)
    # Include all known slugs from previous index (prevents false broken-ref
    # warnings in --files mode where only a subset of files are scanned)
    known_slugs = set(graph.keys())
    known_slugs.update(prev_index.get("files", {}).keys())

    # Validate and score
    all_warnings = []
    scores = {}
    for r in records:
        meta = r.get("metadata", {})
        body = r.get("body", "")
        stored_hash = r.get("stored_hash", "")
        actual_hash = r.get("_actual_hash", stored_hash)
        slug = r["slug"]
        ind = in_deg.get(slug, 0)

        warns, actual_hash = validate(meta, body, stored_hash, known_slugs, ind, r.get("_text", ""))
        all_warnings.extend(warns)

        # Determine sync_status
        if any(w["level"] == "error" for w in warns):
            r["sync_status"] = "needs-review"
        elif any(w["level"] == "warning" for w in warns):
            r["sync_status"] = "stale"
        else:
            r["sync_status"] = "synced"

        if not r.get("_actual_hash"):
            r["_actual_hash"] = actual_hash
        scores[slug] = compute_score(meta, ind, out_deg.get(slug, 0))

    project_name = memory_dir.parent.name

    # ── Fix engine ──────────────────────────────────────────────────────────
    fixes_applied = []
    if args.fix:
        fixes_applied = apply_fixes(records, known_slugs, memory_dir, dry_run=args.dry_run)

        if fixes_applied:
            # Re-validate after fixes (metadata changed in-memory and on-disk)
            all_warnings = []
            for r in records:
                meta = r.get("metadata", {})
                body = r.get("body", "")
                stored_hash = r.get("stored_hash", "")
                slug = r["slug"]
                ind = in_deg.get(slug, 0)

                warns, actual_hash = validate(meta, body, stored_hash, known_slugs, ind, r.get("_text", ""))
                all_warnings.extend(warns)

                if any(w["level"] == "error" for w in warns):
                    r["sync_status"] = "needs-review"
                elif any(w["level"] == "warning" for w in warns):
                    r["sync_status"] = "stale"
                else:
                    r["sync_status"] = "synced"

                if not r.get("_actual_hash"):
                    r["_actual_hash"] = actual_hash

            # Print fix summary
            for f in fixes_applied:
                print(f"  FIXED [{f['check']}] {f['slug']}: {f['detail']}")

    # Rebuild graph after fixes (references may have changed)
    if fixes_applied:
        graph = build_graph(records)
        in_deg, out_deg = compute_degrees(graph)
        known_slugs = set(graph.keys())
        known_slugs.update(prev_index.get("files", {}).keys())
        # Re-score
        for r in records:
            slug = r["slug"]
            meta = r.get("metadata", {})
            scores[slug] = compute_score(meta, in_deg.get(slug, 0), out_deg.get(slug, 0))

    # ── Audit candidates ───────────────────────────────────────────────────
    if args.audit:
        candidates = audit_candidates(records)
        if args.json:
            print(json.dumps({"candidates": candidates}, indent=2, ensure_ascii=False))
            return
        if candidates:
            print("\nAudit Candidates (model review suggested)")
            print("─────────────────────────────────────────")
            for c in candidates:
                print(f"\n{c['a']} ↔ {c['b']}")
                print(f"  shared tags:     {c['shared_tags']}")
                print(f"  shared headings: {c['shared_headings']}")
                print(f"  updated:         {c['updated_a']} / {c['updated_b']}")
                print(f"  suggestion:      {c['suggestion']}")
        else:
            print("\nNo audit candidates found.")

    # Writeback content_hash FIRST so INDEX.json has accurate hashes
    if not args.dry_run:
        writeback_content_hash(records, memory_dir)

    # Generate outputs (after writeback so stored_hash is populated)
    index_md = build_index_md(records, all_warnings, scores, in_deg, out_deg, memory_dir)
    index_json = build_index_json(project_name, memory_dir, records, all_warnings, scores, in_deg, out_deg, graph)

    if args.json:
        print(index_json)
        return

    if not args.dry_run:
        (memory_dir / "INDEX.md").write_text(index_md, encoding="utf-8")
        (memory_dir / "INDEX.json").write_text(index_json, encoding="utf-8")
        print(f"INDEX.md written ({len(records)} memories)")

    # Summary
    synced = sum(1 for r in records if r["sync_status"] == "synced")
    stale = sum(1 for r in records if r["sync_status"] == "stale")
    review = sum(1 for r in records if r["sync_status"] == "needs-review")
    errors_n = sum(1 for w in all_warnings if w["level"] == "error")
    warns_n = sum(1 for w in all_warnings if w["level"] == "warning")
    infos_n = sum(1 for w in all_warnings if w["level"] == "info")
    print(f"  synced: {synced}  |  stale: {stale}  |  needs-review: {review}")
    print(f"  errors: {errors_n}  |  warnings: {warns_n}  |  info: {infos_n}")

    if all_warnings:
        print(f"\n{len(all_warnings)} issue(s):")
        for w in all_warnings:
            print(f"  [{w['level'].upper()}] [{w['check']}] {w['detail']}")


if __name__ == "__main__":
    main()
