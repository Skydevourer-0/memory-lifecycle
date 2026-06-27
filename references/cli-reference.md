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
