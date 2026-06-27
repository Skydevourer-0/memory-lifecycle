# memory-lifecycle

Claude Code skill for persistent knowledge management across sessions.

## Install

```bash
python ~/.claude/skills/memory-lifecycle/scripts/install.py
```

Idempotent — safe to run multiple times.

## How it works

**Write** a memory file with 4 metadata fields:

```yaml
---
name: my-topic
description: One-line summary
references: []
read-when:
  - phrase you would grep to find this memory
---
```

**Recall** is two-tier:

- **HOT** — Top-scored links auto-injected into CLAUDE.md. Always visible.
- **WARM** — Grep `~/.claude/global/memory/INDEX.md` for `read-when` phrases. Zero cost if nothing matches.

Run `sync-memory --hit <slug>` after recalling to boost the memory's score.

**Remove** safely:

```bash
python ~/.claude/skills/memory-lifecycle/scripts/remove-memory.py <slug>
```

## Commands

```
sync-memory              # validate + rebuild INDEX + update hot list
sync-memory --fix        # + remove broken references
sync-memory --audit      # find contradiction candidates
sync-memory --hit <slug> # record a recall hit
remove-memory <slug>     # safe delete + ref cleanup
```

## Requirements

Python 3.7+, no pip dependencies.
