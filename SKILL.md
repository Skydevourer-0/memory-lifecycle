---
name: memory-lifecycle
description: Use when writing or editing memory .md files, running sync-memory to validate the knowledge graph, or safely removing memories. TRIGGER on memory, save, persist, archive, remember, recall, knowledge graph.
---
# Memory Lifecycle

Pure Markdown body files, no frontmatter. Metadata lives in script-owned `metadata.jsonl`.
Model sets metadata through CLI gates; script validates and writes.
Data is shared between Claude Code and Codex (`~/.cc-switch/memory/`).

Define: `$SM = python $HOME/.cc-switch/skills/memory-lifecycle/scripts/memory-sync.py`

## Platforms

- Claude Code: `python $HOME/.cc-switch/skills/memory-lifecycle/.claude/install.py`
- Codex: `python $HOME/.cc-switch/skills/memory-lifecycle/.codex/install.py`
- Auto-detect (any end installed): `python $HOME/.cc-switch/skills/memory-lifecycle/scripts/install.py`

Both ends share the same data and the same commands. Codex requires `/hooks`
approval after install, and again after any reinstall (command paths are re-hashed).

## Storage

| Scope | Memory .md | Hot-list target |
|-------|-----------|-----------------|
| Global | `~/.cc-switch/memory/global/<slug>.md` | `~/.claude/CLAUDE.md` + `~/.codex/AGENTS.md` (marker blocks, dual-end) |
| Project | `~/.cc-switch/memory/projects/<project-slug>/<slug>.md` | `<mem-dir>/HOTLIST.md` (SessionStart hook injects it) |

`<slug>` (memory file): kebab-case only — `[a-z0-9]+(-[a-z0-9]+)*`. No underscores. Sync rejects invalid slugs.

`<project-slug>`: git root `os.path.realpath()` → lowercase → non-`[a-z0-9]` → `-` → fold consecutive `-`.
e.g. `C:\Users\a\proj` → `c-users-a-proj`, memories at
`~/.cc-switch/memory/projects/c-users-a-proj/`.

Scope auto-detect: walk upward from CWD to `.git` (a `.git` file counts, e.g.
worktrees/submodules) → project. Paths under `~/.claude/` and
`~/.cc-switch/skills/` are always global. No `.git` found → global.

## Recall

1. **HOT** — Top-scored links auto-written into `<!-- memory-index:start/end -->` managed blocks.
   Global → both `~/.claude/CLAUDE.md` (Claude auto-loads) and `~/.codex/AGENTS.md`
   (Codex auto-loads; created on first sync if missing). Project →
   `~/.cc-switch/memory/projects/<slug>/HOTLIST.md`, injected into the session by the
   SessionStart hook (`startup|resume|clear|compact`). No action needed.
2. **WARM** — Before non-trivial tasks, grep INDEX.md for `read-when` phrases. One file per scope.

Known limits (Codex): if `~/.codex/AGENTS.override.md` exists it takes precedence over
`AGENTS.md` and the hot list is not loaded; AGENTS.md merging is capped at 32 KiB
(the hot list is ~1200 chars, appended at the end — impact negligible). After Codex
`/clear`, the global hot list returns on the next startup (project HOTLIST re-injects
via SessionStart).

## Lifecycle

### 1. Write the .md file

Pure Markdown. No YAML frontmatter. Use `##` / `###` headings.

MUST use `Write` / `Edit` / `MultiEdit` (Claude) or `apply_patch` (Codex) so the
PostToolUse hook fires.

### 2. Hook: sync-and-hint

PostToolUse hook runs `$SM sync-and-hint` once per tool call. It parses the payload
(Claude `tool_input.file_path` or Codex `apply_patch` `*** (Update|Add|Delete) File:`
lines), syncs the affected memory scope, then emits a **soft, non-blocking
additionalContext hint** (exit 0, no emoji, no blocking review). Up to 3 hints per call.

- Memory file written → hint shows `read-when` and reminds you to refresh metadata
  when the body changed.
- Metadata empty (fresh stub) → hint says the memory exists and metadata is pending.
- `INDEX.md` / `MEMORY.md` / `README.md` / `HOTLIST.md` → silently skipped.
- Delete branch → memory removed, refs cleaned, INDEX + hot list rebuilt. Infrastructure
  files or unknown slugs → no-op.
- Non-memory paths, native auto-memory paths (Claude legacy project memory dirs),
  or broken payloads → silent exit 0 (fail-open).

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
Duplicate `read_when` phrases warn but do not block.

| Field | Gate | Exit |
|-------|------|------|
| `description` | >= 20 non-whitespace chars. NOT in blacklist (TBD, TODO, placeholder, WIP, draft, 待补充, 记住, 记一下, 重要, 备忘, 笔记, 总结, 概述, 相关信息). NOT boilerplate (EN/中文模板). | 2 |
| `read_when` | 1–8 phrases. Each: >= 2 words OR >= 10 chars. No stopword-only. No blacklisted. | 2 |
| `references` | Max 10. No self-ref. Every target must exist. `global:` prefix for cross-scope. Duplicates silently deduped. | 1 |

## Remove

```
$SM delete <slug>           # delete .md + clean dangling refs + rebuild
$SM delete <slug> --dry-run # preview only
```

## Migrate

`$SM migrate` — optional one-time migration from the old Claude-only layout into
`~/.cc-switch` (idempotent, skip-existing):

- `~/.claude/global/memory/*` → `~/.cc-switch/memory/global/`
- `~/.claude/{global,projects/<slug>}/workflows` → `~/.cc-switch/workflows/...`
- NOT migrated: `~/.claude/projects/<slug>/memory/` (native auto-memory territory).

## Setup

Once per end:

```
python $HOME/.cc-switch/skills/memory-lifecycle/.claude/install.py   # Claude Code
python $HOME/.cc-switch/skills/memory-lifecycle/.codex/install.py    # Codex (+ /hooks trust)
```

Registers in `~/.claude/settings.json` / `<codex-home>/hooks.json`:
- PostToolUse (`Write|Edit|MultiEdit` / `apply_patch`, no pathPattern) → `$SM sync-and-hint`
- SessionStart (`startup|resume|clear|compact`) → `$SM session-start`

Creates `~/.cc-switch/memory/global/` and `projects/`. Global hot-list skeletons
(CLAUDE.md / AGENTS.md) are created lazily on the first global sync.
`autoMemoryEnabled` is left untouched — native auto-memory keeps working in parallel.

## Audit

`$SM audit` — structural graph audit (orphans, one-way edges). No semantic judgment.

MUST NOT run `audit` during normal writes, syncs, or recalls.
Run ONLY when user explicitly asks to review, organize, clean up, or audit the memory graph.

## Commands

```
$SM sync                            # full sync
$SM sync-and-hint                   # PostToolUse hook: sync + soft hint (exit 0)
$SM hint [slug]                     # metadata hints (hook: slug from stdin; manual: slug from CLI arg)
$SM set-metadata <slug> <<'EOF'     # batch write metadata (stdin JSON)
$SM delete <slug>                   # delete + cleanup
$SM audit                           # structural audit
$SM display [--view graph|stats|timeline|usage|all] [--scope global|project|auto]
            [--exclude slug1,slug2] [--out <file>] [--no-mermaid]
            # read-only: emit paste-into-Feishu visual artifacts (knowledge graph / stats / timeline / usage)
$SM session-start                   # SessionStart hook: inject project HOTLIST.md
$SM migrate                         # one-time migration from old ~/.claude layout (idempotent)
```