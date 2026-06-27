# CLI Reference

## Synopsis

```
sync-memory [--global DIR] [--project DIR] [--fix] [--clean] [--audit]
            [--dry-run] [--json] [--files FILE ...]
```

## Flags

| Flag | Args | Purpose |
|------|------|---------|
| (none) | — | Sync memory directory, rebuild INDEX |
| --global | DIR | Sync global memory at DIR |
| --project | DIR | Sync project memory at DIR/memory/ |
| --fix | — | Auto-fix warnings (tags, references, context) |
| --clean | — | Delete task-complete tagged memories |
| --audit | — | Find memory pairs for semantic review |
| --audit --json | — | Audit output as structured JSON |
| --dry-run | — | Preview changes without writing |
| --json | — | Output INDEX.json instead of INDEX.md |
| --files | FILE... | Sync only specified files |
| --migrate-to-global | SRC DEST | Move .md files SRC→DEST, rebuild INDEX |

## Common Patterns

```bash
sync-memory                              # Quick sync
sync-memory --fix                        # Sync + auto-fix warnings
sync-memory --fix --clean                # Fix + remove completed tasks
sync-memory --audit                      # Check for contradictions
sync-memory --global ~/.claude/memory    # Sync global directory
sync-memory --migrate-to-global          # Migrate pseudo-project to global
  ~/.claude/projects/Old/memory
  ~/.claude/memory
```
