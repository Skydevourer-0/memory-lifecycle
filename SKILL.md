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

Scope auto-detect: walk upward from CWD to `.git` → project; no `.git` → global.

## Recall

1. **HOT** — Top-scored links auto-written into `<!-- memory-index:start/end -->` managed block.
   Global → `~/.claude/CLAUDE.md`, project → `~/.claude/projects/<project-slug>/memory/MEMORY.md`.
   CC auto-loads both. No action needed.
2. **WARM** — Before non-trivial tasks, grep INDEX.md for `read-when` phrases. One file per scope.

## Lifecycle

### 1. Write the .md file

Pure Markdown. No YAML frontmatter. Use `##` / `###` headings.

MUST use `Write` / `Edit` / `MultiEdit` tools (not shell commands) so the PostToolUse hook fires.

### 2. Sync

Hook auto-runs `$SM sync`. Check output for `INDEX.md written`. If absent, run `$SM sync` manually.

After body edits, run `$SM --hint <slug>` to review headings. Decide whether new headings
warrant updating `read_when` — not every heading needs a trigger phrase.

New files get a stub:
```
1 new memories awaiting metadata. Run $SM --hint <slug> for each.
```

### 3. Set metadata

```
$SM --hint <slug>          # shows headings, refs, slugs, required fields
$SM --set-metadata <slug> <<'EOF'
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
$SM --delete <slug>           # delete .md + clean dangling refs + rebuild
$SM --delete <slug> --dry-run # preview only
```

## Setup

Once: `python3 $HOME/.claude/skills/memory-lifecycle/scripts/install.py`

## Audit

`$SM --audit` — structural graph audit (orphans, one-way edges). No semantic judgment.

MUST NOT run `--audit` during normal writes, syncs, or recalls.
Run ONLY when user explicitly asks to review, organize, clean up, or audit the memory graph.

## Commands

```
$SM sync                            # full sync
$SM --hint <slug>                   # metadata hints
$SM --set-metadata <slug> <<'EOF'    # batch write metadata (stdin JSON)
$SM --delete <slug>                 # delete + cleanup
$SM --dry-run                       # read-only validate
$SM --audit                         # structural audit
```
