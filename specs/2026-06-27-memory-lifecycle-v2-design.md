# memory-lifecycle v2 Design Spec

*2026-06-27*

## 1. Problem

memory-lifecycle v1 is hard to use and hard to trust.

**Metadata bloat.** The template has 12 fields (`name`, `description`, `type`, `tags`, `context`, `references`, `confidence`, `priority`, `created`, `updated`, `content_hash`, `ttl`). `context` auto-balloons to 40+ entries via `--fix`, most of them generic noise (`model`, `None`, `vscode`). `tags`, `priority`, `created`, `ttl` are hand-written but never meaningfully used for recall or scoring. `references` duplicates `[[wiki-links]]` in body. `content_hash` leaks an implementation detail into user-facing files.

**Recall is push-based.** The skill tells the model "run this hook, load INDEX, inject matched memories." That burns tokens on every session even when no memory is needed.

**Sync checks are noisy.** 14 checks, 7 produce info-level noise. `--fix` auto-adds 40 inline-code terms to `context` and 20 heading terms to `tags`. Output is hard to read.

**CLI has too many options.** 8 flags (`--global`, `--project`, `--fix`, `--clean`, `--audit`, `--dry-run`, `--json`, `--files`, `--migrate-to-global`). The model must choose the right combination and regularly picks wrong.

**No safe delete.** Memories can only be removed by hand-deleting `.md` files, which leaves broken `references` in surviving files.

## 2. Design

### 2.1 Metadata — 4 fields

All user-written. No auto-populated noise.

```yaml
---
name: tt-statusline-pricing-fix
description: DeepSeek pricing for tt statusline
references:
  - superpowers-hook
read-when:
  - debugging tt cost display
  - statusline shows wrong cost
---
```

| Field | Type | Purpose |
|-------|------|---------|
| `name` | kebab-case slug | File identity, matches filename |
| `description` | one sentence | Human summary, used in INDEX |
| `references` | list of slugs | Citation graph edges |
| `read-when` | list of natural-language phrases | Recall trigger — model greps INDEX.md for these |

Fields removed and why:

| Removed | Reason |
|---------|--------|
| `context` | Auto-noise, replaced by body-text grep |
| `tags` | Used for recall (replaced by `read-when`) and audit (replaced by shared `references`) |
| `priority` | Redundant — scoring already uses graph degree + freshness |
| `confidence` | Only `deprecated` mattered; deleted memories instead of deprecating |
| `type` | All checks that triggered on type are gone |
| `created` | Only used with `ttl` which nobody set |
| `ttl` | Memories are persistent knowledge, not expired data |
| `content_hash` | Implementation detail, lives in INDEX.json only |
| `updated` | Auto-extracted from file mtime by sync |
| `node_type` | Claude Code internal, already ignored by v1 |
| `originSessionId` | Claude Code internal, already ignored by v1 |

### 2.2 Recall — two-tier

**v1 (push):** SessionStart hook injects matched memories into system prompt. Burns tokens every session.

**v2 (tiered):** Hot memories are always visible; the rest are grep-discoverable.

```
Layer 1: HOT  — Top-N high-score links auto-written into CLAUDE.md / MEMORY.md
               Always in context. No recall step needed.
               Global memory → CLAUDE.md, project memory → MEMORY.md

Layer 2: WARM — Everything else. Grep INDEX.md for read-when phrases.
               Model proactively checks before non-trivial tasks.
               One file, one grep, zero cost if nothing matches.
```

**Layer 1 detail — dynamic Top-N.** Sync writes a marker-delimited block into CLAUDE.md (or MEMORY.md for project memories). Character budget: max 2000 chars for the section. Fill from highest score down until budget is exhausted. Roughly 12–16 entries at ~120 chars each.

Marker format so sync never touches user content outside the block:

```markdown
<!-- memory-index:start -->
- [tt-statusline-pricing-fix](~/.claude/memory/tt-statusline-pricing-fix.md) — DeepSeek pricing for tt statusline
- [powershell-alias-patterns](~/.claude/memory/powershell-alias-patterns.md) — PowerShell function wrappers
<!-- memory-index:end -->
```

**Layer 2 detail.** INDEX.md already contains condensed `read-when` phrases for every memory. One grep covers everything:

```
Grep "cost" ~/.claude/memory/INDEX.md → match → Read the linked .md file
```

Zero token cost when nothing matches.

**Sync flow (one target per run):**

```
sync-memory (auto-detect scope)
  → scan .md files
  → validate (4 checks)
  → score (graph degree + freshness)
  → write INDEX.md (full list, sorted)
  → write hot-list block (Top-N, dynamic budget):
      global scope  → ~/.claude/CLAUDE.md
      project scope → <git-root>/MEMORY.md
```

If the target file (CLAUDE.md or MEMORY.md) doesn't exist or has no `<!-- memory-index:start -->` marker, sync skips hot-list injection with a warning: "No memory-index marker in CLAUDE.md. Add `<!-- memory-index:start --><!-- memory-index:end -->` to enable hot list."

### 2.3 Sync checks — 14 → 4

| # | Check | Level | `--fix` |
|---|-------|-------|---------|
| 1 | `name` or `description` missing | error | — |
| 2 | `name` not kebab-case | error | — |
| 4 | `content_hash` mismatch (file changed) | warning | — (always auto-updated in INDEX.json during sync) |
| 6 | `references` point to non-existent slug | error | ✅ (delete broken entries) |

Checks removed: TTL expired, orphan confirmed, upgradable speculative, missing context, empty tags, heading-not-in-tags, inline-code-not-in-context, feedback-missing-structure, reference-missing-entity-headings, invalid type.

### 2.4 `--fix` — 4 actions → 1

| v1 | v2 |
|----|-----|
| Add heading terms to tags | Removed |
| Add inline code to context | Removed |
| Sync body `[[wiki-links]]` to `references` | Removed (`[[wiki-links]]` no longer used) |
| Remove broken references | **Kept** |

The sole `--fix` action: delete `references` entries that point to slugs that don't exist in the memory directory.

### 2.5 Scoring

```
score = in_degree × 2.0 + out_degree × 0.5 + max(0, 10 - days_since_mtime)
```

- `in_degree`: incoming references from other memories
- `out_degree`: outgoing references to other memories
- `days_since_mtime`: freshness bonus, decays over 10 days

`updated` is auto-extracted from `.md` file modification time. No user input.

Score determines INDEX.md sort order only. It does NOT gate recall — `read-when` matching gates recall.

### 2.6 Scope detection

Default: auto-detect from CWD.

```
walk from CWD upward → find .git directory
  → found: project scope, memory dir = <git-root>/.claude/memory/
  → not found: global scope, memory dir = ~/.claude/memory/
```

For PostToolUse hook: `--scope-from-file <path>` derives scope from the file
path itself (not CWD). `~/.claude/memory/` prefix → global; any other
`.claude/memory/` under a git root → project.

`--global` and `--project` flags are removed. No model decision needed.

### 2.7 CLI

```
sync-memory              # auto-detect scope, validate, rebuild INDEX + hot list
sync-memory --fix        # + remove broken references
sync-memory --fix --dry-run  # preview broken refs, no writes
sync-memory --dry-run    # validate only, report issues, no writes
sync-memory --audit      # find contradiction candidates
sync-memory --json       # output INDEX.json to stdout (for scripts, future embedding recall)
remove-memory <slug>     # delete <slug>.md, cleanup refs, rebuild INDEX + hot list
remove-memory <slug> --dry-run  # preview what would be deleted, no changes
remove-memory <slug> --yes      # skip confirmation, auto-remove dangling refs
```

Removed: `--global DIR`, `--project DIR`, `--files FILE...`, `--clean`, `--migrate-to-global`.

`--json` kept: outputs INDEX.json to stdout. Needed for programmatic consumers (future embedding recall, debugging, integration scripts).

### 2.8 `remove-memory.py`

```
remove-memory <slug> [--yes] [--dry-run]

Actions:
  1. Verify <slug>.md exists
  2. Scan all .md files in memory dir for references: ["<slug>"]
  3. Print: "These files reference <slug>: [list]"
  4. --dry-run: stop here, no changes
  5.  --yes: delete <slug>.md, auto-remove dangling refs, rebuild INDEX + hot list
      no --yes: confirm each step interactively
```

### 2.8a `--audit`

Finds contradiction candidates: memory pairs sharing ≥1 `references` target
and ≥1 `### entity` heading term. Without tags, the signal is "both cite the
same memory AND discuss the same named concept" — a tighter match than v1's
"shared tags."

```
sync-memory --audit
  → load all .md files
  → for each pair (A, B):
      shared_refs = A.references ∩ B.references
      shared_headings = A.heading_terms ∩ B.heading_terms
      if shared_refs ≥ 1 and shared_headings ≥ 1:
        → candidate: "A and B both reference X and discuss Y — verify consistency"
  → print candidates sorted by overlap count
```

### 2.9 INDEX.md format

Flat list sorted by score descending. No tier groups.

```markdown
# Memory Index
*2026-06-27 12:00 UTC · 4 memories*

- [tt-statusline-pricing-fix](tt-statusline-pricing-fix.md) — DeepSeek pricing for tt statusline
  read-when: debugging tt cost, statusline wrong cost, deepseek pricing
  updated: 2026-06-27 · refs: in 1, out 0 · score: 12.0

- [powershell-alias-patterns](powershell-alias-patterns.md) — PowerShell function wrappers
  read-when: powershell functions, command wrappers, ndd
  updated: 2026-06-20 · refs: in 1, out 0 · score: 11.5

- [superpowers-hook](superpowers-hook.md) — ParserError and UTF-8 fixes
  read-when: powershell hooks, settings.json hooks, garbled errors
  updated: 2026-06-27 · refs: in 0, out 1 · score: 10.5

## Warnings

- **ERR** [broken-references] tt-statusline-pricing-fix: references unknown slug 'old-thing'
  → Remove the broken reference or create the missing memory
```

Each entry on 2–3 lines:
- Line 1: link + description
- Line 2: `read-when` keywords (for grep), or `read-when: (none)` if empty
- Line 3 (optional): updated date + refs + score

### 2.10 `memory-lifecycle` skill SKILL.md

Updated to describe v2 usage:

```markdown
# Memory Graph

Persistent knowledge stored as markdown files. A sync engine validates structure,
scores memories, and maintains a recall index.

## Two-Tier Recall

1. **HOT** — Top-scored links auto-written into CLAUDE.md (global) or MEMORY.md (project).
   Always in context. No action needed.
2. **WARM** — Grep INDEX.md for `read-when` phrases before non-trivial tasks:
     Grep "keyword" .claude/memory/INDEX.md (project) or ~/.claude/memory/INDEX.md (global)
   One file covers all memories. If nothing matches, move on. Zero cost.

## Writing a Memory

Create `.claude/memory/<slug>.md` (project) or `~/.claude/memory/<slug>.md` (global):

---
name: my-slug
description: One-line summary
references: []
read-when:
  - specific scenario this memory helps with
  - natural phrase you'd type when stuck
---

Body: any format. Recommended: ### entity-name — description sections.

After writing, run `sync-memory` to rebuild the index and update the hot list.

## Removing a Memory

  remove-memory <slug>     # deletes .md, cleans dangling refs, rebuilds index

## Commands

  sync-memory              # validate + rebuild INDEX + update hot list
  sync-memory --fix        # + remove broken references
  sync-memory --audit      # find contradiction candidates
  remove-memory <slug>     # safe delete with ref cleanup
```

### 2.11 PostToolUse auto-sync

The model may write a memory file (Write/Edit tool on `memory/*.md`) but
forget to run `sync-memory`. A PostToolUse hook catches this:

```
PostToolUse hook:
  matcher: Write|Edit on **/memory/*.md
  → python sync-memory.py --scope-from-file <modified-file-path>
  → validates frontmatter, rebuilds INDEX, updates hot list
  → reports errors/warnings inline so model can fix immediately
```

**Scope-from-file:** Instead of auto-detecting from CWD (which can misidentify
scope when model writes global memory from a project directory), the hook
passes the modified file path. Sync infers:

```
path under ~/.claude/memory/     → global scope
path under **/.claude/memory/    → project scope (walk up to find .git)
```

If frontmatter is malformed (missing `name`, bad kebab-case, broken
`references`), the hook output makes it visible in the tool result or
as a system reminder — model corrects the file and re-sync triggers.

### 2.12 Migration from v1

Existing v1-format files (~/.claude/memory/*.md, currently 4 files) are migrated manually during implementation:

1. Strip all frontmatter except `name`, `description`, `references`
2. Add empty `read-when: []` — user fills in later
3. Rebuild INDEX

No migration command needed. The new sync engine handles v2 format only; v1-format files without `read-when` will fail check #1 (missing required field).

## 3. Files Changed

| File | Action |
|------|--------|
| `skills/memory-lifecycle/skill.md` | Rewrite for v2 |
| `skills/memory-lifecycle/scripts/memory-sync.py` | Rewrite: new metadata schema, 4 checks, simplified `--fix`, auto-detect scope, hot-list injection |
| `skills/memory-lifecycle/scripts/remove-memory.py` | **New** — safe delete + ref cleanup |
| `skills/memory-lifecycle/templates/memory-template.md` | Rewrite: 4-field template |
| `skills/memory-lifecycle/references/checks-reference.md` | Rewrite: 4 checks |
| `skills/memory-lifecycle/references/memory-writing-template.md` | Rewrite: simplified guide |
| `skills/memory-lifecycle/references/cli-reference.md` | Rewrite: simplified CLI |
| `~/.claude/CLAUDE.md` | Sync writes `<!-- memory-index -->` block (global Top-N) |
| `MEMORY.md` (per-project) | Sync writes `<!-- memory-index -->` block (project Top-N) |
| `~/.claude/settings.json` | Add PostToolUse hook: Write/Edit on `memory/*.md` → auto-run sync |
| `~/.claude/memory/*.md` | Migrate existing 4 files to v2 format |
