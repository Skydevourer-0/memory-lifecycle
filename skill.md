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
markers to CLAUDE.md, and registers a `PostToolUse` hook:

| Hook | Matcher | Purpose |
|------|---------|---------|
| `PostToolUse` | `Write\|Edit\|MultiEdit` + `pathPattern: **/.claude/**/memory/*.md` | Auto-sync after editing a memory file |

This is **best effort** — the hook may not fire depending on harness, tool variant, or Claude Code version.
Always follow the full lifecycle below: edit → sync → fix → verify. Do not assume the hook ran.

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

## Full Lifecycle: Write/Edit → Sync → Fix → Verify

### Step 1: Write or Edit the Memory File

Create or edit a `.md` file in the memory directory with valid frontmatter:

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

**Use the dedicated file tools** — `Write` (new file / full overwrite), `Edit` (surgical string replacement),
or `MultiEdit` (batch edits). Do NOT use shell commands (`Set-Content`, `Out-File`, `>>`) to write memory files —
those bypass the tool matcher and the auto-sync hook won't fire.

### Step 2: Run sync-memory

After writing/editing, run sync to validate and rebuild the INDEX:

```
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py
```

The `PostToolUse` hook does this automatically when you use `Write`, `Edit`, or `MultiEdit` on a
`**/.claude/**/memory/*.md` file. But this is **best effort** — the hook may not fire depending on
the harness, tool variant, or Claude Code version.

**Do not assume it ran.** After editing a memory file, always verify Step 3.

### Step 3: Check Sync Output for Errors and Warnings

The sync output tells you exactly what happened:

```
INDEX.md written (7 memories)
  synced: 7  |  stale: 0  |  needs-review: 0
  errors: 0  |  warnings: 0
```

| Field | Meaning | Action |
|-------|---------|--------|
| `errors > 0` | Broken references, missing files, invalid frontmatter | Fix immediately — INDEX may be incomplete |
| `warnings > 0` | Low-confidence links, deprecated patterns | Review and clean up |
| `stale > 0` | Memories with `expires` in the past or `needs-review: true` | Update or remove |
| `needs-review > 0` | Memories flagged for human review | Read and decide |

If the INDEX timestamp hasn't changed after your edit, the hook didn't fire — run sync manually.

### Step 4: Fix Issues and Re-sync

If sync reported errors or warnings, fix them immediately and re-sync. **Do NOT
ask the user whether to fix, which approach to take, or anything else that
pauses the loop.** Sync gives you the file path, line number, and a suggestion —
that is enough information to act.

Only escalate to the user if the fix requires a subjective judgment the model
cannot make (e.g., "this memory describes two unrelated topics — which one
should I keep?"). Mechanical fixes (broken refs, CJK characters in the wrong
language, missing frontmatter fields, stale content) are your responsibility.

Loop until clean:

1. Read the error output — file, line, check type, suggestion
2. Fix the file directly
3. Run sync again
4. Repeat until `errors: 0, warnings: 0`

Script-assisted fixes (run these before manual editing):

```
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --fix          # auto-remove broken references
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --fix --dry-run  # preview what --fix would do
```

### Step 5: Verify INDEX is Current

Check that the INDEX file was updated:

```
Get-ChildItem ~/.claude/global/memory/INDEX.md   # Windows
ls -la ~/.claude/global/memory/INDEX.md           # Unix
```

The timestamp should be within seconds of your last edit. If it's not, the whole pipeline
(edit → sync → fix → verify) didn't complete — go back to Step 2.

## Recalling a Memory

Before starting any non-trivial task, grep both INDEX files:

```
Grep "keyword" ~/.claude/global/memory/INDEX.md                       # global scope
Grep "keyword" ~/.claude/projects/*/memory/INDEX.md                   # all projects
```

If a `read-when` phrase matches, Read the linked `.md` file. Zero cost if nothing matches.

## Removing a Memory

```
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --delete <slug>
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --delete <slug> --yes     # skip confirmation
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --delete <slug> --dry-run # preview only
```

Deletes the `.md` file, cleans dangling references in other memories, rebuilds INDEX.
`remove-memory.py` is a thin wrapper that forwards to `--delete`. Either entry point works.

## Commands

Full paths (use `$env:USERPROFILE` on Windows instead of `~`):

```
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py              # validate + rebuild INDEX + update hot list
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --fix        # + remove broken references
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --fix --dry-run  # preview broken refs
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --dry-run    # validate only, no writes
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --audit      # contradiction candidates
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --json       # output INDEX.json
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --hit <slug>     # record recall hit (run after grep + Read)
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --delete <slug>  # safe delete + ref cleanup + rebuild INDEX
```
