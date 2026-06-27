---
name: memory-graph
description: Use when writing or editing memory files, running sync-memory to validate the knowledge graph, auditing for semantic contradictions, or setting up persistent knowledge infrastructure
---

# Memory Graph

Build and maintain a persistent knowledge graph as validated markdown files.
The `sync-memory` script enforces 14 structural checks and can auto-fix 4
classes of warnings.

## Core Commands

```bash
sync-memory                       # sync + rebuild INDEX
sync-memory --fix                 # sync + auto-fix warnings
sync-memory --audit               # find contradiction candidates
sync-memory --global <dir>        # sync a global memory directory
sync-memory --migrate-to-global S D  # move .md files S→D
```

Full CLI reference: `references/cli-reference.md`

## Writing a Memory File

Quick rules. Full guide: `references/memory-writing-template.md`

### Type Selection

| `type:` | For | Requires |
|---------|-----|----------|
| `reference` | Knowledge, patterns, debugging | `### entity — description` headings |
| `feedback` | User preferences, corrections | `**Why:**` + `**How to apply:**` |
| `project` | Project state, goals | `**Why:**` + `**How to apply:**` |
| `task` | Work tracking | `skill:` field recommended |
| `user` | Identity, role | Free-form |

### Body Structure

- `## Section Labels` organize the document. They are NOT auto-tagged.
- `### entity-name — description` marks each named entity. These ARE
  auto-tagged by check #11. Every `###` heading must include a separator
  (`—`, `:`, or `–`). Plain `### Problem` or `### Overview` headings
  are section labels — use `##` or bold markers instead.

### Language

All memory content — descriptions, body, headings — is written in English.
Tags use kebab-case. CLI output to the terminal may be in the user's language.

### Template

Copy `templates/memory-template.md` as a starting point for new `type: reference`
memories. It has the frontmatter skeleton and body structure stubs.

## Responding to Sync Output

After running `sync-memory`:

| Level | Action |
|-------|--------|
| `[ERROR]` | Report. Do not continue without fixing. |
| `[WARNING]` | Fix structurally (add tags, fix refs). Re-run sync. |
| `[INFO]` | Report to user. Offer to fix. |

After `--fix` adds inline-code terms to `context`: review the additions.
Remove noise terms (single generic keywords). Keep meaningful entity
references (command names, file paths, tool names).

All 14 checks: `references/checks-reference.md`

## Task Memory Format

Task memories (`type: task`) are managed by the `workflow-lifecycle` skill.
See `~/.claude/skills/workflow-lifecycle/SKILL.md` for the full lifecycle
protocol (pause, resume, completion, recovery).

Task memories live in `memory/tasks/` — separate from knowledge memories.
The sync engine does not scan them (flat `memory/*.md` glob only).

## Audit

`sync-memory --audit` finds memory pairs sharing ≥2 tags and ≥1 heading
entity. These are candidates for semantic review — the model should read
both, compare for factual consistency, and report contradictions or
confirm agreement.

## Proactive Maintenance

Memory not recalled despite relevance → check `tags`, `description`, and
`context` cover the terms used. Fix gaps, re-run sync.

Memory contradicts current knowledge → run `sync-memory --audit`, flag
the contradiction, ask which version is authoritative.
