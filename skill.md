---
name: memory-lifecycle
description: Use when writing or editing memory files, recalling past fixes/patterns, running sync-memory to validate the knowledge graph, or safely removing memories
---

# Memory Lifecycle

Persistent knowledge stored as markdown files. A sync engine validates structure,
scores memories, and maintains a recall index.

## Storage Paths

| Scope | Memory location | Index location | Hot-list target |
|-------|----------------|----------------|-----------------|
| Global | `~/.claude/global/memory/<slug>.md` | `~/.claude/global/memory/INDEX.md` | `~/.claude/CLAUDE.md` |
| Project | `~/.claude/projects/<project>/memory/<slug>.md` | same dir | `~/.claude/projects/<project>/MEMORY.md` |

`<project>` is the git root directory name, sanitized to kebab-case.
`<slug>` is the memory name (kebab-case, matches filename stem).

## Setup

Run once:

```
python ~/.claude/skills/memory-lifecycle/scripts/install.py
```
(On Windows: `python $env:USERPROFILE\.claude\skills\memory-lifecycle\scripts\install.py`)

This creates `~/.claude/global/memory/`, adds `<!-- memory-index:start -->` / `<!-- memory-index:end -->`
markers to CLAUDE.md, and registers a PostToolUse hook that auto-syncs on memory Write/Edit.

Project-scope setup is automatic — the first `sync-memory` run inside a git project
creates `~/.claude/projects/<project>/MEMORY.md` and the memory directory.

## Two-Tier Recall

1. **HOT** — Top-scored links auto-written into the hot-list target (see table above).
   Always in context. No action needed.
2. **WARM** — Grep the INDEX.md for `read-when` phrases before non-trivial tasks:
   ```
   Grep "keyword" ~/.claude/global/memory/INDEX.md              # global
   Grep "keyword" ~/.claude/projects/<project>/memory/INDEX.md   # project
   ```
   One file covers all memories. Zero cost if nothing matches.

## Writing a Memory

```yaml
---
name: my-topic
description: One-line summary of what this memory contains
references: []
read-when:
  - phrase you would grep to find this memory
  - another scenario this helps with
---
```

Body: any format. Recommended: `### entity-name — description` sections.

After writing, run sync:
```
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py
```
The PostToolUse hook runs this automatically on Write/Edit to any `**/.claude/**/memory/*.md` file.

## Recalling a Memory

Before starting any non-trivial task, grep both INDEX files:

```
Grep "keyword" ~/.claude/global/memory/INDEX.md                       # global scope
Grep "keyword" ~/.claude/projects/*/memory/INDEX.md                   # all projects
```

If a `read-when` phrase matches, Read the linked `.md` file. Zero cost if nothing matches.

## Removing a Memory

```
python ~/.claude/skills/memory-lifecycle/scripts/remove-memory.py <slug>
python ~/.claude/skills/memory-lifecycle/scripts/remove-memory.py <slug> --yes     # skip prompts
python ~/.claude/skills/memory-lifecycle/scripts/remove-memory.py <slug> --dry-run # preview only
```

Deletes the `.md` file, cleans dangling references in other memories, rebuilds INDEX.

## Commands

Full paths (use `$env:USERPROFILE` on Windows instead of `~`):

```
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py              # validate + rebuild INDEX + update hot list
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --fix        # + remove broken references
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --fix --dry-run  # preview broken refs
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --dry-run    # validate only, no writes
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --audit      # contradiction candidates
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --json       # output INDEX.json
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --hit <slug>   # record recall hit (run after grep + Read)
python ~/.claude/skills/memory-lifecycle/scripts/remove-memory.py <slug>     # safe delete + ref cleanup
```
