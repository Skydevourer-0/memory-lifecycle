# memory-lifecycle v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite memory-lifecycle from 12-field metadata + push recall + 14 noisy checks into 4-field metadata + two-tier pull recall + 4 focused checks.

**Architecture:** `memory-sync.py` scans `.md` files in `~/.claude/memory/` (global) or `<git>/.claude/memory/` (project), parses 4-field frontmatter, validates (4 checks), scores (graph degree + freshness), writes INDEX.md (flat, sorted) and injects Top-N hot links into CLAUDE.md/MEMORY.md. `remove-memory.py` safely deletes a memory and cleans dangling references. A PostToolUse hook auto-syncs on Write/Edit.

**Tech Stack:** Python 3, stdlib only (json, re, pathlib, hashlib, datetime, argparse, subprocess)

## Global Constraints

- All memory content in English; CLI output may be in user language
- `name` must be kebab-case, matches filename
- Memory dir: global = `~/.claude/memory/`, project = `<git-root>/.claude/memory/`
- Hot-list character budget: 2000 chars per marker block
- `content_hash` lives in INDEX.json only, not in .md files
- `updated` auto-extracted from file mtime, not user-written

---

### Task 1: Update templates and reference docs

**Files:**
- Create: `skills/memory-lifecycle/templates/memory-template.md` (overwrite)
- Create: `skills/memory-lifecycle/references/checks-reference.md` (overwrite)
- Create: `skills/memory-lifecycle/references/memory-writing-template.md` (overwrite)
- Create: `skills/memory-lifecycle/references/cli-reference.md` (overwrite)

**Interfaces:**
- Consumes: nothing
- Produces: reference docs that Tasks 2-4 refer to for format specs

- [ ] **Step 1: Write `templates/memory-template.md`**

```markdown
---
name: example-slug
description: One-line summary of what this memory contains
references: []
read-when:
  - phrase you would grep to find this memory
  - another scenario this helps with
---

## Overview

Brief context about this memory.

### entity-name — brief description

Body content about the entity.
```

- [ ] **Step 2: Write `references/checks-reference.md`**

```markdown
# Validation Checks Reference

4 structural checks run during `sync-memory`. Level: E=error, W=warning.

| # | Check | Level | --fix | Detects |
|---|-------|-------|-------|---------|
| 1 | missing-required | E | — | name or description missing |
| 2 | invalid-name | E | — | name not kebab-case |
| 3 | stale-hash | W | — | content_hash mismatch (auto-updated in INDEX.json) |
| 4 | broken-references | E | yes | references point to non-existent slug |

## Scoring Formula

```
score = in_degree * 2.0 + out_degree * 0.5 + max(0, 10 - days_since_mtime)
```

Score determines INDEX.md sort order. It does NOT gate recall — read-when matching gates recall.

## Valid Types

N/A — type field removed in v2. All memories use the same 4-field metadata schema.
```

- [ ] **Step 3: Write `references/memory-writing-template.md`**

```markdown
# Memory Writing Guide

## Metadata Fields

| Field | Required | Format | Purpose |
|-------|----------|--------|---------|
| `name` | yes | kebab-case slug | File identity, matches filename |
| `description` | yes | one sentence | Summary, used in INDEX |
| `references` | no | list of slugs | Citation graph edges |
| `read-when` | no | list of phrases | Recall triggers for grep |

## Body Structure

Free-form. Recommended: `### entity-name — description` sections for each named concept.

No required sections. No auto-populated fields. No tags, context, priority, confidence, type, created, or ttl.

## Example

---
name: ssh-debugging-guide
description: Debugging techniques and known pitfalls for SSH connection issues
references:
  - networking-basics
read-when:
  - ssh connection timeout
  - permission denied publickey
  - debugging ssh issues
---

## Overview

Common SSH failures and how to diagnose them.

### ssh-connection-timeout — server unreachable or firewalled

Diagnostic steps for `Connection timed out` errors...

### ssh-permission-denied — public key authentication failures

Checklist for `Permission denied (publickey)` errors...
```

- [ ] **Step 4: Write `references/cli-reference.md`**

```markdown
# CLI Reference

## Synopsis

```
sync-memory [--fix] [--dry-run] [--audit] [--json]
remove-memory <slug> [--yes] [--dry-run]
```

## Commands

| Command | Purpose |
|---------|---------|
| `sync-memory` | Auto-detect scope, validate, rebuild INDEX + hot list |
| `sync-memory --fix` | + remove broken references |
| `sync-memory --fix --dry-run` | Preview broken refs, no writes |
| `sync-memory --dry-run` | Validate only, report issues, no writes |
| `sync-memory --audit` | Find contradiction candidates |
| `sync-memory --json` | Output INDEX.json to stdout |
| `remove-memory <slug>` | Safe delete + ref cleanup + rebuild |
| `remove-memory <slug> --dry-run` | Preview deletion, no changes |
| `remove-memory <slug> --yes` | Skip confirmation |

## Scope Detection

Sync auto-detects scope by walking CWD upward to find `.git`:
- Found: project scope, memory dir = `<git-root>/.claude/memory/`
- Not found: global scope, memory dir = `~/.claude/memory/`

PostToolUse hook uses `--scope-from-file <path>` instead.
```

- [ ] **Step 5: Commit**

```bash
git add skills/memory-lifecycle/templates/ skills/memory-lifecycle/references/
git commit -m "docs: update memory-lifecycle templates and references for v2"
```

---

### Task 2: Rewrite `memory-sync.py` — frontmatter parser

**Files:**
- Modify: `skills/memory-lifecycle/scripts/memory-sync.py` (full rewrite, this task does the parser)

**Interfaces:**
- Consumes: nothing
- Produces: `parse_frontmatter(text: str) -> tuple[dict, str]` returning `{name, description, references, read-when}` and body

- [ ] **Step 1: Write the parser function**

Replace the entire `parse_frontmatter` and `_parse_yaml_value` functions. The v2 parser is simpler — only 4 fields, no nested `metadata:` block, no tags/context/confidence/priority.

```python
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

    return meta, body


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
```

- [ ] **Step 2: Write parser tests**

Create `skills/memory-lifecycle/tests/test_parser.py`:

```python
import sys
sys.path.insert(0, "skills/memory-lifecycle/scripts")
from memory_sync import parse_frontmatter

def test_minimal_frontmatter():
    text = """---
name: my-memory
description: A test memory
---
Body content."""
    meta, body = parse_frontmatter(text)
    assert meta["name"] == "my-memory"
    assert meta["description"] == "A test memory"
    assert meta["references"] == []
    assert meta["read_when"] == []
    assert body == "Body content."

def test_full_frontmatter():
    text = """---
name: pricing-fix
description: DeepSeek pricing for tt
references:
  - superpowers-hook
  - powershell-alias
read-when:
  - debugging tt cost
  - statusline wrong cost
---
Fix details."""
    meta, body = parse_frontmatter(text)
    assert meta["name"] == "pricing-fix"
    assert meta["description"] == "DeepSeek pricing for tt"
    assert meta["references"] == ["superpowers-hook", "powershell-alias"]
    assert meta["read_when"] == ["debugging tt cost", "statusline wrong cost"]

def test_empty_references():
    text = """---
name: test
description: test
references: []
read-when: []
---
Body."""
    meta, _ = parse_frontmatter(text)
    assert meta["references"] == []
    assert meta["read_when"] == []

def test_no_frontmatter():
    text = "Just body content."
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == "Just body content."
```

- [ ] **Step 3: Run test to verify parser**

```bash
python -m pytest skills/memory-lifecycle/tests/test_parser.py -v
```
Expected: 4 PASS

- [ ] **Step 4: Commit**

```bash
git add skills/memory-lifecycle/scripts/memory-sync.py skills/memory-lifecycle/tests/test_parser.py
git commit -m "feat: v2 frontmatter parser — 4 fields, no metadata nesting"
```

---

### Task 3: Rewrite `memory-sync.py` — validation + scoring

**Files:**
- Modify: `skills/memory-lifecycle/scripts/memory-sync.py` (add validate_v2, compute_score)
- Create: `skills/memory-lifecycle/tests/test_validate.py`

**Interfaces:**
- Consumes: `parse_frontmatter()` from Task 2
- Produces: `validate(meta, body, stored_hash, known_slugs, filepath) -> (list, str)`, `compute_score(meta, in_deg, out_deg) -> float`

- [ ] **Step 1: Write the v2 validate function**

```python
REQUIRED_FIELDS = {"name", "description"}
KEBAB_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def validate(meta: dict, body: str, stored_hash: str,
             known_slugs: set, filepath: Path) -> tuple[list, str]:
    """Run v2 validation — 4 checks.

    Returns (warnings_list, actual_hash).
    """
    warnings = []
    full_text = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
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

    return warnings, actual_hash
```

- [ ] **Step 2: Write the v2 scoring function**

```python
def compute_score(meta: dict, in_deg: int, out_deg: int) -> float:
    """Compute v2 score: graph degree + freshness bonus.

    score = in_degree * 2.0 + out_degree * 0.5 + max(0, 10 - days_since_mtime)
    """
    days = 0
    filepath = meta.get("_filepath")
    if filepath and filepath.exists():
        mtime = filepath.stat().st_mtime
        days = (datetime.now(timezone.utc).timestamp() - mtime) / 86400
    days_bonus = max(0.0, 10.0 - days)
    score = in_deg * 2.0 + out_deg * 0.5 + days_bonus
    return round(score, 1)
```

- [ ] **Step 3: Write validation tests**

```python
from pathlib import Path
import tempfile
from memory_sync import validate

def test_missing_name():
    meta = {"description": "test"}
    fp = Path(tempfile.mktemp(suffix=".md"))
    fp.write_text("---\ndescription: test\n---\nBody")
    warnings, _ = validate(meta, "Body", "", set(), fp)
    assert any(w["check"] == "missing-required" for w in warnings)
    fp.unlink()

def test_bad_kebab():
    meta = {"name": "Bad Name!", "description": "test"}
    fp = Path(tempfile.mktemp(suffix=".md"))
    fp.write_text("---\nname: Bad Name!\ndescription: test\n---\nBody")
    warnings, _ = validate(meta, "Body", "", set(), fp)
    assert any(w["check"] == "invalid-name" for w in warnings)
    fp.unlink()

def test_broken_references():
    meta = {"name": "test", "description": "test", "references": ["ghost"]}
    fp = Path(tempfile.mktemp(suffix=".md"))
    fp.write_text("---\nname: test\ndescription: test\nreferences:\n  - ghost\n---\nBody")
    warnings, _ = validate(meta, "Body", "", {"real-slug"}, fp)
    assert any(w["check"] == "broken-references" for w in warnings)
    fp.unlink()

def test_clean_memory():
    meta = {"name": "test", "description": "test", "references": []}
    fp = Path(tempfile.mktemp(suffix=".md"))
    fp.write_text("---\nname: test\ndescription: test\n---\nBody")
    warnings, _ = validate(meta, "Body", "", {"test"}, fp)
    assert len(warnings) == 0
    fp.unlink()
```

- [ ] **Step 4: Run validate tests**

```bash
python -m pytest skills/memory-lifecycle/tests/test_validate.py -v
```
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add skills/memory-lifecycle/scripts/memory-sync.py skills/memory-lifecycle/tests/test_validate.py
git commit -m "feat: v2 validation (4 checks) + scoring (graph degree + freshness)"
```

---

### Task 4: Rewrite `memory-sync.py` — INDEX.md + hot-list generation

**Files:**
- Modify: `skills/memory-lifecycle/scripts/memory-sync.py` (add build_index_md, inject_hot_list)
- Create: `skills/memory-lifecycle/tests/test_index.py`

**Interfaces:**
- Consumes: `validate()`, `compute_score()`, graph from `build_graph()` / `compute_degrees()`
- Produces: `build_index_md(records, warnings, scores, in_deg, out_deg) -> str`, `inject_hot_list(records, scores, target_file) -> bool`

- [ ] **Step 1: Write `build_index_md()`**

```python
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

        # updated from mtime
        fp = record.get("_filepath")
        updated_str = ""
        if fp and fp.exists():
            mtime = fp.stat().st_mtime
            updated_str = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")

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
```

- [ ] **Step 2: Write `inject_hot_list()`**

```python
HOT_BUDGET = 2000
MARKER_START = "<!-- memory-index:start -->"
MARKER_END = "<!-- memory-index:end -->"


def inject_hot_list(records: list, scores: dict, target_file: Path) -> bool:
    """Write Top-N entries into CLAUDE.md or MEMORY.md between markers.

    Returns True if injection succeeded, False if markers not found.
    """
    if not target_file.exists():
        return False

    content = target_file.read_text(encoding="utf-8")
    if MARKER_START not in content or MARKER_END not in content:
        return False

    # Build hot list entries
    sorted_records = sorted(
        records, key=lambda r: scores.get(r["slug"], 0), reverse=True
    )

    entries = []
    budget_remaining = HOT_BUDGET
    for record in sorted_records:
        slug = record["slug"]
        desc = record.get("metadata", {}).get("description", "")
        # Build link entry: path depends on scope
        entry = f"- [{slug}]({slug}.md) — {desc}"
        if len(entry) <= budget_remaining:
            entries.append(entry)
            budget_remaining -= len(entry) + 1  # +1 for newline
        else:
            break

    new_block = MARKER_START + "\n" + "\n".join(entries) + "\n" + MARKER_END

    start = content.find(MARKER_START)
    end = content.find(MARKER_END) + len(MARKER_END)
    new_content = content[:start] + new_block + content[end:]

    target_file.write_text(new_content, encoding="utf-8")
    return True
```

- [ ] **Step 3: Write INDEX.md tests**

```python
import tempfile
from pathlib import Path
from memory_sync import build_index_md, inject_hot_list

def test_build_index_md():
    records = [{
        "slug": "test-memory",
        "metadata": {
            "name": "test-memory",
            "description": "A test memory",
            "read_when": ["debugging test", "test failure"],
        },
        "_filepath": Path(tempfile.mktemp(suffix=".md")),
    }]
    # Touch file so mtime works
    records[0]["_filepath"].write_text("---\nname: test-memory\ndescription: test\n---\nBody")

    scores = {"test-memory": 12.0}
    in_deg = {"test-memory": 1}
    out_deg = {"test-memory": 0}

    result = build_index_md(records, [], scores, in_deg, out_deg)
    assert "test-memory" in result
    assert "debugging test, test failure" in result
    assert "score: 12.0" in result

    records[0]["_filepath"].unlink()

def test_empty_read_when_shows_none():
    records = [{
        "slug": "no-rw",
        "metadata": {"name": "no-rw", "description": "No read-when", "read_when": []},
        "_filepath": Path(tempfile.mktemp(suffix=".md")),
    }]
    records[0]["_filepath"].write_text("---\nname: no-rw\ndescription: test\n---\nBody")

    result = build_index_md(records, [], {"no-rw": 5.0}, {"no-rw": 0}, {"no-rw": 0})
    assert "read-when: (none)" in result

    records[0]["_filepath"].unlink()

def test_hot_list_injection():
    records = [{
        "slug": "hot-memory",
        "metadata": {"name": "hot-memory", "description": "Hot memory description"},
    }]
    scores = {"hot-memory": 15.0}

    tf = Path(tempfile.mktemp(suffix=".md"))
    tf.write_text("Some content\n<!-- memory-index:start -->\n<!-- memory-index:end -->\nMore content")

    result = inject_hot_list(records, scores, tf)
    assert result == True
    new_content = tf.read_text()
    assert "hot-memory" in new_content
    assert "Hot memory description" in new_content
    assert "Some content" in new_content  # not touched
    assert "More content" in new_content  # not touched

    tf.unlink()

def test_hot_list_no_markers():
    tf = Path(tempfile.mktemp(suffix=".md"))
    tf.write_text("No markers here")

    result = inject_hot_list([], {}, tf)
    assert result == False

    tf.unlink()
```

- [ ] **Step 4: Run INDEX tests**

```bash
python -m pytest skills/memory-lifecycle/tests/test_index.py -v
```
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add skills/memory-lifecycle/scripts/memory-sync.py skills/memory-lifecycle/tests/test_index.py
git commit -m "feat: v2 INDEX.md generation + hot-list injection"
```

---

### Task 5: Rewrite `memory-sync.py` — scope detection + CLI + main

**Files:**
- Modify: `skills/memory-lifecycle/scripts/memory-sync.py` (add detect_scope, rewrite main())
- Create: `skills/memory-lifecycle/tests/test_scope.py`

**Interfaces:**
- Consumes: all functions from Tasks 2-4
- Produces: runnable `memory-sync.py` CLI

- [ ] **Step 1: Write scope detection**

```python
def detect_scope(cwd: Path = None, scope_from_file: str = None) -> tuple[Path, str]:
    """Detect memory directory and scope label.

    Returns (memory_dir, scope_label) where scope_label is 'global' or '<project-slug>'.
    """
    if scope_from_file:
        fp = Path(scope_from_file).resolve()
        if str(Path.home().resolve()) in str(fp) and "/.claude/memory/" in str(fp).replace("\\", "/"):
            # Global: ~/.claude/memory/...
            return Path.home() / ".claude" / "memory", "global"
        else:
            # Project: walk up from file
            return _find_project_memory(fp.parent), "project"

    # Auto-detect from CWD
    cwd = cwd or Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            return parent / ".claude" / "memory", "project"
    return Path.home() / ".claude" / "memory", "global"


def _find_project_memory(start: Path) -> Path:
    """Walk up from start to find .git, return <git>/.claude/memory/."""
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists():
            return parent / ".claude" / "memory"
    return Path.home() / ".claude" / "memory"  # fallback


def find_hot_list_target(memory_dir: Path, scope_label: str) -> Path | None:
    """Return CLAUDE.md (global) or MEMORY.md (project) path."""
    if scope_label == "global":
        return Path.home() / ".claude" / "CLAUDE.md"
    else:
        # Walk up from memory_dir to find git root, return <root>/MEMORY.md
        for parent in [memory_dir.parent] + list(memory_dir.parent.parents):
            if (parent / ".git").exists():
                return parent / "MEMORY.md"
        return None
```

- [ ] **Step 2: Write scope detection tests**

```python
from pathlib import Path
import tempfile, os
from memory_sync import detect_scope

def test_scope_from_global_file():
    mem_dir, label = detect_scope(
        scope_from_file=str(Path.home() / ".claude" / "memory" / "test.md")
    )
    assert label == "global"
    assert mem_dir == Path.home() / ".claude" / "memory"

def test_auto_detect_no_git(monkeypatch):
    # CWD is home — no .git there
    import os
    monkeypatch.chdir(str(Path.home()))
    # Skip if home is a git repo
    if (Path.home() / ".git").exists():
        return
    mem_dir, label = detect_scope()
    assert label == "global"
```

- [ ] **Step 3: Run scope tests**

```bash
python -m pytest skills/memory-lifecycle/tests/test_scope.py -v
```
Expected: 2 PASS

- [ ] **Step 4: Rewrite `main()` — stitch everything together**

```python
def main():
    parser = argparse.ArgumentParser(description="Memory Graph v2 — sync engine")
    parser.add_argument("--fix", action="store_true", help="Remove broken references")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes, no writes")
    parser.add_argument("--audit", action="store_true", help="Find contradiction candidates")
    parser.add_argument("--json", action="store_true", help="Output INDEX.json to stdout")
    parser.add_argument("--scope-from-file", metavar="PATH",
                        help="Detect scope from file path (for PostToolUse hook)")
    args = parser.parse_args()

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
        scores[slug] = compute_score(meta, in_deg.get(slug, 0), out_deg.get(slug, 0))

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
                scores[r["slug"]] = compute_score(r["metadata"], in_deg.get(r["slug"], 0),
                                                   out_deg.get(r["slug"], 0))

    # --audit
    if args.audit:
        candidates = audit_candidates(records)
        if args.json:
            print(json.dumps({"candidates": candidates}, indent=2, ensure_ascii=False))
            return
        if candidates:
            print("\nAudit Candidates:")
            for c in candidates:
                print(f"  {c['a']} ↔ {c['b']} — both ref {c['shared_refs']}, discuss {c['shared_headings']}")
        else:
            print("No audit candidates found.")
        return

    # Writeback content_hash to INDEX.json (not to .md files)
    if not args.dry_run:
        writeback_content_hash(records, memory_dir)

    # Generate INDEX.md
    index_md = build_index_md(records, all_warnings, scores, in_deg, out_deg)
    index_json = build_index_json("memory", memory_dir, records, all_warnings, scores, in_deg, out_deg, graph)

    if args.json:
        print(index_json)
        return

    if not args.dry_run:
        (memory_dir / "INDEX.md").write_text(index_md, encoding="utf-8")
        (memory_dir / "INDEX.json").write_text(index_json, encoding="utf-8")

        # Inject hot list
        hot_target = find_hot_list_target(memory_dir, scope_label)
        if hot_target:
            success = inject_hot_list(records, scores, hot_target)
            if not success:
                all_warnings.append({
                    "level": "warning",
                    "check": "no-memory-index-marker",
                    "detail": f"No markers in {hot_target.name}. Add <!-- memory-index:start --><!-- memory-index:end -->",
                    "suggestion": f"Add the marker pair to {hot_target}"
                })

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
```

- [ ] **Step 5: Commit**

```bash
git add skills/memory-lifecycle/scripts/memory-sync.py skills/memory-lifecycle/tests/test_scope.py
git commit -m "feat: v2 scope detection + CLI + main orchestration"
```

---

### Task 6: Remove legacy code from `memory-sync.py`

**Files:**
- Modify: `skills/memory-lifecycle/scripts/memory-sync.py`

**Interfaces:**
- Consumes: all v2 functions from Tasks 2-5
- Produces: clean file, no dead code

- [ ] **Step 1: Remove v1-only functions and constants**

Delete these functions (their logic is replaced or removed):
- `_parse_yaml_value` → replaced by `_parse_scalar`
- `parse_ttl` / `is_expired` → TTL removed
- `_days_since` → inline in `compute_score`
- `_extract_inline_terms` → context removed
- `score_tier` → no tiers
- `_extract_heading_terms` → keep for audit only
- `_tag_insert_positions` → no tags
- `_fix_tags_on_disk` → no tags
- `_fix_context_on_disk` → no context
- `_fix_references_on_disk` → keep for --fix
- `_prev_to_meta` → rewrite for v2 schema

Delete these constants:
- `VALID_TYPES` → type removed
- `VALID_CONFIDENCES` / `CONFIDENCE_MAP` → confidence removed
- `TTL_PATTERN` / `TTL_MULTIPLIER` → TTL removed
- `HEADING_PATTERN` / `INLINE_CODE_PATTERN` / `FENCED_BLOCK_PATTERN` → keep what's needed

Update `_prev_to_meta()` for v2 schema — only 4 fields, no nesting:

```python
def _prev_to_meta(prev_entry: dict) -> dict:
    """Convert a flat INDEX.json entry back to v2 metadata format."""
    return {
        "name": prev_entry.get("name", ""),
        "description": prev_entry.get("description", ""),
        "references": prev_entry.get("references", []),
        "read_when": prev_entry.get("read_when", []),
    }
```

Update `scan_memory_dir()` to use the v2 `_prev_to_meta` and strip references to removed fields (`tags`, `context`, `confidence`, `priority`, `ttl`, `created`, `type`).

Update `build_index_json()` to output only v2 fields:

```python
def build_index_json(project_name, memory_dir, records, warnings, scores, in_deg, out_deg, graph):
    files = {}
    for record in records:
        slug = record["slug"]
        meta = record.get("metadata", {})
        files[slug] = {
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
    existing_slugs = {record["slug"] for record in records}
    filtered_graph = {slug: [r for r in refs if r in existing_slugs] for slug, refs in graph.items()}
    return json.dumps({
        "project": project_name,
        "memory_dir": str(memory_dir),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "warnings": warnings,
        "graph": filtered_graph,
    }, indent=2, ensure_ascii=False)
```

Update `apply_fixes()` to only do one thing — remove broken references:

```python
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
```

- [ ] **Step 2: Verify the file has no syntax errors**

```bash
python -c "import sys; sys.path.insert(0, 'skills/memory-lifecycle/scripts'); import memory_sync; print('OK')"
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest skills/memory-lifecycle/tests/ -v
```
Expected: all PASS (10+ tests)

- [ ] **Step 4: Commit**

```bash
git add skills/memory-lifecycle/scripts/memory-sync.py
git commit -m "refactor: remove v1 dead code — TTL, tags, context, confidence, priority, type, tiers"
```

---

### Task 7: Create `remove-memory.py`

**Files:**
- Create: `skills/memory-lifecycle/scripts/remove-memory.py`
- Create: `skills/memory-lifecycle/tests/test_remove.py`

**Interfaces:**
- Consumes: scope detection from memory-sync (duplicated inline for standalone script)
- Produces: `remove-memory <slug> [--yes] [--dry-run]` CLI

- [ ] **Step 1: Write `remove-memory.py`**

```python
#!/usr/bin/env python3
"""Remove a memory file and clean up dangling references."""
import argparse
import sys
from pathlib import Path


def find_memory_dir() -> Path:
    """Auto-detect memory directory from CWD."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            return parent / ".claude" / "memory"
    return Path.home() / ".claude" / "memory"


def find_referencing_files(memory_dir: Path, slug: str) -> list[Path]:
    """Find .md files that reference the given slug."""
    refs = []
    for md in sorted(memory_dir.glob("*.md")):
        if md.stem == slug or md.name in ("INDEX.md", "MEMORY.md"):
            continue
        content = md.read_text(encoding="utf-8")
        # Look for "- slug" in references block
        if f"- {slug}" in content or f"[{slug}]" in content:
            refs.append(md)
    return refs


def remove_reference_line(filepath: Path, slug: str) -> bool:
    """Remove '  - slug' line from references block. Returns True if changed."""
    lines = filepath.read_text(encoding="utf-8").split("\n")
    new_lines = []
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped == f"- {slug}":
            changed = True
            continue
        new_lines.append(line)
    if changed:
        filepath.write_text("\n".join(new_lines), encoding="utf-8")
    return changed


def main():
    parser = argparse.ArgumentParser(description="Remove a memory and clean up references")
    parser.add_argument("slug", help="Memory slug to remove")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    args = parser.parse_args()

    slug = args.slug
    memory_dir = find_memory_dir()
    md_file = memory_dir / f"{slug}.md"

    if not md_file.exists():
        print(f"Memory not found: {md_file}", file=sys.stderr)
        sys.exit(1)

    # Find files referencing this slug
    refs = find_referencing_files(memory_dir, slug)
    if refs:
        print(f"These files reference '{slug}':")
        for rf in refs:
            print(f"  {rf.name}")
    else:
        print(f"No files reference '{slug}'.")

    if args.dry_run:
        print(f"\nWould delete: {md_file}")
        if refs:
            print(f"Would remove dangling references from {len(refs)} file(s).")
        return

    # Confirm
    if not args.yes:
        response = input(f"\nDelete '{slug}.md'? [y/N] ").strip().lower()
        if response != "y":
            print("Aborted.")
            return

    # Delete the file
    md_file.unlink()
    print(f"Deleted: {md_file}")

    # Clean up references in other files
    if refs and (args.yes or input("Remove dangling references? [y/N] ").strip().lower() == "y"):
        for rf in refs:
            changed = remove_reference_line(rf, slug)
            if changed:
                print(f"  Cleaned: {rf.name}")

    # Rebuild INDEX by running sync
    import subprocess
    sync_script = Path(__file__).parent / "memory-sync.py"
    subprocess.run([sys.executable, str(sync_script)], check=False)
    print("INDEX rebuilt.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write tests for `remove-memory.py`**

```python
import tempfile
from pathlib import Path
from remove_memory import find_referencing_files, remove_reference_line

def test_find_referencing_files(tmp_path):
    # Create two .md files, one referencing 'target'
    (tmp_path / "target.md").write_text("---\nname: target\ndescription: test\n---\nBody")
    (tmp_path / "other.md").write_text("---\nname: other\ndescription: test\nreferences:\n  - target\n---\nBody")
    (tmp_path / "unrelated.md").write_text("---\nname: unrelated\ndescription: test\nreferences:\n  - something-else\n---\nBody")

    refs = find_referencing_files(tmp_path, "target")
    assert len(refs) == 1
    assert refs[0].stem == "other"

def test_remove_reference_line(tmp_path):
    md = tmp_path / "test.md"
    md.write_text("references:\n  - target\n  - other\n")
    remove_reference_line(md, "target")
    content = md.read_text()
    assert "target" not in content
    assert "other" in content
```

- [ ] **Step 3: Run remove-memory tests**

```bash
python -m pytest skills/memory-lifecycle/tests/test_remove.py -v
```
Expected: 2 PASS

- [ ] **Step 4: Commit**

```bash
git add skills/memory-lifecycle/scripts/remove-memory.py skills/memory-lifecycle/tests/test_remove.py
git commit -m "feat: remove-memory.py — safe delete + ref cleanup + INDEX rebuild"
```

---

### Task 8: Rewrite `skill.md`

**Files:**
- Modify: `skills/memory-lifecycle/skill.md` (overwrite)

**Interfaces:**
- Consumes: spec section 2.10
- Produces: skill loaded by Claude Code

- [ ] **Step 1: Write the new SKILL.md**

```markdown
---
name: memory-lifecycle
description: Use when writing or editing memory files, recalling past fixes/patterns, running sync-memory to validate the knowledge graph, or safely removing memories
---

# Memory Graph

Persistent knowledge stored as markdown files. A sync engine validates structure,
scores memories, and maintains a recall index.

## Two-Tier Recall

1. **HOT** — Top-scored links auto-written into CLAUDE.md (global) or MEMORY.md (project).
   Always in context. No action needed.
2. **WARM** — Grep INDEX.md for `read-when` phrases before non-trivial tasks:
   ```
   Grep "keyword" .claude/memory/INDEX.md              # project
   Grep "keyword" ~/.claude/memory/INDEX.md             # global
   ```
   One file covers all memories. If nothing matches, move on. Zero cost.

## Writing a Memory

Create `.claude/memory/<slug>.md` (project) or `~/.claude/memory/<slug>.md` (global):

```yaml
---
name: my-slug
description: One-line summary
references: []
read-when:
  - specific scenario this memory helps with
  - natural phrase you would type when stuck
---
```

Body: any format. Recommended: `### entity-name — description` sections.

After writing, run `sync-memory` to rebuild the index and update the hot list.
(A PostToolUse hook auto-runs sync on Write/Edit, but manually running it
gives immediate feedback on errors.)

## Recalling a Memory

Before starting any non-trivial task, grep INDEX.md for `read-when` phrases matching the current situation. If a match is found, Read the linked `.md` file.

## Removing a Memory

```
remove-memory <slug>         # safe delete + ref cleanup + INDEX rebuild
remove-memory <slug> --yes   # skip confirmation
```

## Commands

```
sync-memory              # validate + rebuild INDEX + update hot list
sync-memory --fix        # + remove broken references
sync-memory --audit      # find contradiction candidates
sync-memory --dry-run    # validate only, no writes
remove-memory <slug>     # safe delete with ref cleanup
```

- [ ] **Step 2: Commit**

```bash
git add skills/memory-lifecycle/skill.md
git commit -m "docs: rewrite skill.md for memory-lifecycle v2"
```

---

### Task 9: Migrate existing memories + configure hooks

**Files:**
- Modify: `~/.claude/memory/tt-statusline-pricing-fix.md`
- Modify: `~/.claude/memory/powershell-alias-patterns.md`
- Modify: `~/.claude/memory/superpowers-sessionstart-hook-parsererror.md`
- Modify: `~/.claude/memory/memory-sync-development.md`
- Modify: `~/.claude/settings.json` (add PostToolUse hook)
- Modify: `~/.claude/CLAUDE.md` (add memory-index markers)

- [ ] **Step 1: Migrate each v1 memory to v2 format**

For each file in `~/.claude/memory/*.md`:
a. Read the file
b. Extract `name`, `description`, `references` from frontmatter
c. Write new frontmatter with only those 3 fields + `read-when: []`
d. Keep body unchanged

```python
# Run this as a one-off migration script
import re
from pathlib import Path

MEM_DIR = Path.home() / ".claude" / "memory"

for md in sorted(MEM_DIR.glob("*.md")):
    if md.name in ("INDEX.md", "MEMORY.md", "INDEX.json"):
        continue
    content = md.read_text(encoding="utf-8")
    if "read-when:" in content:
        print(f"  SKIP {md.name} — already v2")
        continue

    print(f"  MIGRATE {md.name}")
    # Extract from v1 frontmatter: name, description, references
    body_start = content.find("---", 3)
    frontmatter = content[3:body_start].strip() if body_start > 0 else ""
    body = content[body_start + 3:].strip() if body_start > 0 else content

    # Parse name from frontmatter
    name_match = re.search(r'^name:\s*(.+)$', frontmatter, re.MULTILINE)
    desc_match = re.search(r'^description:\s*(.+)$', frontmatter, re.MULTILINE)
    name = name_match.group(1).strip() if name_match else md.stem
    desc = desc_match.group(1).strip() if desc_match else ""

    new_fm = f"---\nname: {name}\ndescription: {desc}\nreferences: []\nread-when: []\n---"
    md.write_text(new_fm + "\n\n" + body, encoding="utf-8")

print("Migration complete.")
```

- [ ] **Step 2: Add `<!-- memory-index -->` markers to CLAUDE.md**

Append to `~/.claude/CLAUDE.md`:

```markdown
<!-- memory-index:start -->
<!-- memory-index:end -->
```

- [ ] **Step 3: Add PostToolUse hook to `settings.json`**

In `~/.claude/settings.json`, under `"hooks"`, add:

```json
"PostToolUse": [
  {
    "matcher": "Write|Edit",
    "pathPattern": "**/memory/*.md",
    "hooks": [
      {
        "type": "command",
        "command": "python C:\\Users\\skyde\\.claude\\skills\\memory-lifecycle\\scripts\\memory-sync.py",
        "async": true
      }
    ]
  }
]
```

Note: `--scope-from-file` is omitted here because the PostToolUse hook's
path-matching variable name is CC-version-dependent. During implementation,
check the CC docs for the correct variable. Fallback: rely on CWD auto-detection
(the hook runs in the session's CWD, which is usually correct). If scope
misdetection becomes a problem, add `--scope-from-file` with the confirmed
variable name.

- [ ] **Step 4: Run first sync to build INDEX + hot list**

```bash
python C:\Users\skyde\.claude\skills\memory-lifecycle\scripts\memory-sync.py
```
Expected: INDEX.md written, hot list injected into CLAUDE.md

- [ ] **Step 5: Verify hot list in CLAUDE.md**

```bash
Select-String "memory-index" ~/.claude/CLAUDE.md -Context 0,10
```
Expected: Top memories listed between markers

- [ ] **Step 6: Commit**

```bash
git add ~/.claude/memory/*.md ~/.claude/CLAUDE.md ~/.claude/settings.json
git commit -m "migrate: v1 memories to v2 format, add hot-list marker and PostToolUse hook"
```

---

### Task 10: End-to-end integration test

- [ ] **Step 1: Create a test memory**

```bash
cat > ~/.claude/memory/e2e-test.md << 'EOF'
---
name: e2e-test
description: End-to-end test memory for v2
references:
  - tt-statusline-pricing-fix
read-when:
  - e2e testing memory graph
  - integration test recall
---

## Overview

This is a test memory for verifying the v2 pipeline.
EOF
```

- [ ] **Step 2: Run sync and verify INDEX.md**

```bash
python C:\Users\skyde\.claude\skills\memory-lifecycle\scripts\memory-sync.py
Select-String "e2e-test" ~/.claude/memory/INDEX.md
```
Expected: e2e-test appears in INDEX.md with read-when phrases

- [ ] **Step 3: Verify broken reference detection**

```bash
# e2e-test references 'tt-statusline-pricing-fix' which exists — should be clean
python C:\Users\skyde\.claude\skills\memory-lifecycle\scripts\memory-sync.py
```
Expected: 0 errors

- [ ] **Step 4: Test remove-memory (dry-run first)**

```bash
python C:\Users\skyde\.claude\skills\memory-lifecycle\scripts\remove-memory.py e2e-test --dry-run
```
Expected: Shows what would be deleted, no files changed

- [ ] **Step 5: Test actual remove**

```bash
python C:\Users\skyde\.claude\skills\memory-lifecycle\scripts\remove-memory.py e2e-test --yes
```
Expected: File deleted, INDEX rebuilt, no broken refs

- [ ] **Step 6: Verify clean state**

```bash
python C:\Users\skyde\.claude\skills\memory-lifecycle\scripts\memory-sync.py
```
Expected: 0 errors, synced: 4

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "test: e2e integration test for memory-lifecycle v2 pipeline"
```
