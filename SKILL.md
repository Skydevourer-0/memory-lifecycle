---
name: memory-lifecycle
description: Use when writing or editing memory .md files, running sync-memory to validate the knowledge graph, or safely removing memories. TRIGGER on memory, save, persist, archive, remember, recall, knowledge graph.
---
# Memory Lifecycle

Pure Markdown body files, no frontmatter. Metadata lives in script-owned `metadata.jsonl`.
Model sets metadata through CLI gates; script validates and writes.

Define: `$SM = python3 $HOME/.claude/skills/memory-lifecycle/scripts/memory-sync.py`

## Storage

| Scope | Memory .md | Hot-list target |
|-------|-----------|-----------------|
| Global | `~/.claude/global/memory/<slug>.md` | `~/.claude/CLAUDE.md` |
| Project | `~/.claude/projects/<project-slug>/memory/<slug>.md` | `~/.claude/projects/<project-slug>/memory/MEMORY.md` |

`<slug>`: kebab-case only — `[a-z0-9]+(-[a-z0-9]+)*`. No underscores. Sync rejects invalid slugs.

`<project-slug>`: git root absolute path, lowercased, `/` → `-`.
e.g. `/home/user/code/my-project` → `-home-user-code-my-project` →
memories at `~/.claude/projects/-home-user-code-my-project/memory/`,
hot list at `~/.claude/projects/-home-user-code-my-project/memory/MEMORY.md` (CC auto-loads).

Scope auto-detect: walk upward from CWD to `.git` → project. Paths under `~/.claude/`
are always global (covers skill development, configs, etc.). No `.git` found → global.

## Recall

1. **HOT** — Top-scored links auto-written into `<!-- memory-index:start/end -->` managed block.
   Global → `~/.claude/CLAUDE.md`, project → `~/.claude/projects/<project-slug>/memory/MEMORY.md`.
   CC auto-loads both. No action needed.
2. **WARM** — Before non-trivial tasks, grep INDEX.md for `read-when` phrases. One file per scope.

## Lifecycle

### 1. Write the .md file

Pure Markdown. No YAML frontmatter. Use `##` / `###` headings.

MUST use `Write` / `Edit` / `MultiEdit` tools (not shell commands) so the PostToolUse hook fires.

### 2. Sync & Hint

PostToolUse hook runs TWO separate commands: `$SM sync` then `$SM hint`.
Each gets its own independent stdin pipe with the tool-result JSON payload.
Sync rebuilds the index; hint extracts the slug from stdin and checks metadata
completeness. MEMORY.md, INDEX.md, and README.md are silently skipped.

When hint detects missing or stale metadata (description, read_when), it
injects an `additionalContext` message into your next turn:
```
⚠️ Metadata stale for 'data-movement'. Run $SM set-metadata data-movement ...
```
You MUST respond by running `$SM set-metadata <slug>` with updated
description, read_when, and references. Do not ignore stale-metadata warnings.

New files get a stub:
```
1 new memories awaiting metadata.
```

Manual use: `$SM hint <slug>` — shows headings, refs, slugs, required fields.

### 3. Set metadata

```
$SM hint <slug>            # shows headings, refs, slugs, required fields
$SM set-metadata <slug> <<'EOF'
{
  "description": "...",
  "read_when": ["...", "..."],
  "references": ["other-slug", "global:cross-scope-slug"]
}
EOF
```

Fields present **replace** existing values; absent stay. `[]` clears refs; empty
description/read_when is REJECTED. Failure writes nothing; success auto-runs sync.

| Field | Gate | Exit |
|-------|------|------|
| `description` | >= 20 non-whitespace chars. NOT in blacklist (TBD, TODO, placeholder, WIP, draft, 待补充). NOT boilerplate. | 2 |
| `read_when` | 1–8 phrases. Each: >= 2 words OR >= 10 chars. No stopword-only. No blacklisted. | 2 |
| `references` | Max 10. No self-ref. Every target must exist. `global:` prefix for cross-scope. Duplicates silently deduped. | 1 |

## Remove

```
$SM delete <slug>           # delete .md + clean dangling refs + rebuild
$SM delete <slug> --dry-run # preview only
```

## Setup

Once: `python3 $HOME/.claude/skills/memory-lifecycle/scripts/install.py`

Registers TWO PostToolUse hooks in `~/.claude/settings.json`:
- `$SM sync` — rebuilds INDEX.md and hot-list from .md files
- `$SM hint` — checks metadata freshness, injects `additionalContext` when stale

Creates `~/.claude/global/memory/`. Adds memory-index markers to `~/.claude/CLAUDE.md`.
Project MEMORY.md markers are added lazily on first sync.

## Audit

`$SM audit` — structural graph audit (orphans, one-way edges). No semantic judgment.

MUST NOT run `audit` during normal writes, syncs, or recalls.
Run ONLY when user explicitly asks to review, organize, clean up, or audit the memory graph.

## Commands

```
$SM sync                            # full sync
$SM hint [slug]                     # metadata hints (hook: slug from stdin; manual: slug from CLI arg)
$SM set-metadata <slug> <<'EOF'     # batch write metadata (stdin JSON)
$SM delete <slug>                   # delete + cleanup
$SM audit                           # structural audit
$SM sync-and-hint                   # DEPRECATED — use sync + hint as separate hooks
```
