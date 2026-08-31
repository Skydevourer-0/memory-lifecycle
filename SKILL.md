---
name: memory-lifecycle
description: Use when writing or editing memory .md files, running sync-memory to validate the knowledge graph, or safely removing memories. TRIGGER on memory, save, persist, archive, remember, recall, knowledge graph.
---
# Memory Lifecycle

Pure Markdown body files, no frontmatter. Metadata lives in script-owned `metadata.jsonl`.
Model sets metadata through CLI gates; script validates and writes.
Data is shared between Claude Code, Codex, and ZCode (`~/.cc-switch/memory/`).

Define: `$SM = python $HOME/.cc-switch/skills/memory-lifecycle/scripts/memory-sync.py`

## Platforms

- Claude Code: `python $HOME/.cc-switch/skills/memory-lifecycle/.claude/install.py`
- Codex: `python $HOME/.cc-switch/skills/memory-lifecycle/.codex/install.py`
- ZCode: `python $HOME/.cc-switch/skills/memory-lifecycle/.zcode/install.py`
- Auto-detect (any end installed): `python $HOME/.cc-switch/skills/memory-lifecycle/scripts/install.py`

All three ends share the same data and the same commands. Codex requires `/hooks`
approval after install, and again after any reinstall (command paths are re-hashed).
ZCode has no trust gate — configuration-file hooks run once `hooks.enabled: true`
(the installer sets it).

## Storage

| Scope | Memory .md | Hot-list target |
|-------|-----------|-----------------|
| Global | `~/.cc-switch/memory/global/<slug>.md` | `~/.claude/CLAUDE.md` + `~/.codex/AGENTS.md` + `~/.zcode/AGENTS.md` (marker blocks; ZCode target only when `~/.zcode` exists) |
| Project | `~/.cc-switch/memory/projects/<project-slug>/<slug>.md` | `<mem-dir>/HOTLIST.md` (SessionStart hook injects it) |

`<slug>` (memory file): kebab-case only — `[a-z0-9]+(-[a-z0-9]+)*`. No underscores. Sync rejects invalid slugs.

`<project-slug>`: git root `os.path.realpath()` → lowercase → non-`[a-z0-9]` → `-` → fold consecutive `-`.
e.g. `C:\Users\a\proj` → `c-users-a-proj`, memories at
`~/.cc-switch/memory/projects/c-users-a-proj/`.

Scope auto-detect: walk upward from CWD to `.git` (a `.git` file counts, e.g.
worktrees/submodules) → project. Paths under `~/.claude/`, `~/.zcode/skills/`,
`~/.agents/skills/`, `~/.config/opencode/skills/`, and `~/.cc-switch/skills/` are
always global. No `.git` found → global.

## Recall

1. **HOT** — Top-scored links auto-written into `<!-- memory-index:start/end -->` managed blocks.
   Global → `~/.claude/CLAUDE.md` (Claude auto-loads), `~/.codex/AGENTS.md`
   (Codex auto-loads), and `~/.zcode/AGENTS.md` (ZCode auto-loads; created on
   first sync if missing, skipped entirely when no `~/.zcode` home exists).
   Project → `~/.cc-switch/memory/projects/<slug>/HOTLIST.md`, injected into the
   session by the SessionStart hook (`startup|resume|clear|compact`). No action needed.
2. **WARM** — Before non-trivial tasks, grep INDEX.md for `read-when` phrases. One file per scope.

Scoring is **Effective Importance (EI)**: `base(importance) x access(recall/hint
frequency) x decay(30-day half-life) x edge(graph connectivity)`. `importance`
(default 3) is the only field set by hand; access/decay/edges are tracked/derived
automatically. Entity edges auto-link on sync when two memories share a technical
entity (regex + dictionary extraction).

Known limits (Codex): if `~/.codex/AGENTS.override.md` exists it takes precedence over
`AGENTS.md` and the hot list is not loaded; AGENTS.md merging is capped at 32 KiB
(the hot list is ~1200 chars, appended at the end — impact negligible). After Codex
`/clear`, the global hot list returns on the next startup (project HOTLIST re-injects
via SessionStart).

Known limits (ZCode): the hot list lands in `~/.zcode/AGENTS.md` (user-scope
instructions, injected first, before any workspace `AGENTS.md`). ZCode hooks have
no per-path filter, so PostToolUse fires on EVERY Write|Edit call — the script's
fast pre-filter (payload must contain "memory") keeps no-ops at interpreter floor.
ZCode accepts no `if` field on hooks and drops `args` from `type: "command"`
entries, so the installer registers `type: "process"` (args vector, no shell).
Hook execution is recorded in the ZCode log (outcome, duration, error preview).

## Lifecycle

### 1. Write the .md file

Pure Markdown. No YAML frontmatter. Use `##` / `###` headings.

MUST use `Write` / `Edit` / `MultiEdit` (Claude) or `apply_patch` (Codex) so the
PostToolUse hook fires.

### 2. Hook: sync-and-hint

PostToolUse hook runs `$SM sync-and-hint` once per tool call. It parses the payload
(Claude/ZCode `tool_input.file_path` — ZCode aliases apply_patch onto the Write/Edit
matchers — or Codex `apply_patch` `*** (Update|Add|Delete) File:` lines), syncs the
affected memory scope, then emits a **soft, non-blocking additionalContext hint**
(exit 0, no emoji, no blocking review). Up to 3 hints per call.

Claude-side PostToolUse entries (one per Write/Edit/MultiEdit) carry an
`if: "<Tool>(*.md)"` filter so non-markdown writes never spawn the hook. The
`if` glob matches basenames only on Windows (directory globs fail against
backslash paths — verified empirically), so `*.md` is the strongest usable
filter; the script guard remains the authoritative per-path filter.

- Memory file written → hint shows `read-when` and reminds you to refresh metadata
  when the body changed.
- Metadata empty (fresh stub) → hint says the memory exists and metadata is pending.
- `INDEX.md` / `MEMORY.md` / `README.md` / `HOTLIST.md` → silently skipped.
- Delete branch → memory removed, refs cleaned, INDEX + hot list rebuilt. Infrastructure
  files or unknown slugs → no-op.
- **Native auto-memory write** (`~/.claude/projects/<slug>/memory/*.md`) → one-way
  ingested into the managed store (scope from session cwd; native file untouched;
  imported memories carry `source: "native"` and stop auto-updating once curated).
- Non-memory paths, legacy `~/.claude/global/memory` paths, or broken payloads →
  silent exit 0 (fail-open).

Manual use: `$SM hint <slug>` — shows headings, refs, slugs, required fields.

### 3. Set metadata

```
$SM hint <slug>            # shows headings, refs, slugs, required fields
$SM set-metadata <slug> <<'EOF'
{
  "description": "...",
  "read_when": ["...", "..."],
  "references": ["other-slug", "global:cross-scope-slug"],
  "importance": 3,
  "entities": ["react", "sqlite"],
  "tags": ["frontend", "storage"]
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
| `importance` | Int 1-5 (default 3). EI base weight: 5->1.0, 4->0.8, 3->0.5, 2->0.3, 1->0.15. | 2 |
| `entities` | List of strings (max 50). Auto-extracted on sync; set manually to override. Feeds entity-graph auto-link. | 2 |
| `tags` | List of strings (max 20). Free-form. | 2 |

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
python $HOME/.cc-switch/skills/memory-lifecycle/.zcode/install.py    # ZCode
```

Registers in `~/.claude/settings.json` / `<codex-home>/hooks.json` /
`~/.zcode/cli/config.json` (`hooks.enabled: true` + `hooks.events.*`):
- PostToolUse (`Write|Edit|MultiEdit` / `apply_patch`, no path filter) → `$SM sync-and-hint`
- SessionStart (`startup|resume|clear|compact`) → `$SM session-start`

The ZCode installer writes `type: "process"` entries (args vector, no shell) —
ZCode's `type: "command"` accepts no `args` field and drops mixed-field hooks.

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
$SM import-native                   # backfill native auto-memory for the current project (auto-dream writes never fire hooks)
```