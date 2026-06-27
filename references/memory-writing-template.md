# Memory Writing Template

## Type Selection

| `type:` | For | Required Structure |
|---------|-----|-------------------|
| `reference` | Technical knowledge, patterns, debugging notes, how-to guides | ≥1 `### entity — description` heading |
| `feedback` | User preferences, corrections, guidance | `**Why:**` + `**How to apply:**` |
| `project` | Ongoing project state, goals, constraints | `**Why:**` + `**How to apply:**` |
| `task` | Work tracking with skill field | Recovery instructions section |
| `user` | User identity and role | Free-form |

## Naming Conventions

- `name:` — kebab-case slug matching the filename (e.g., `powershell-alias-patterns`)
- `description:` — single English sentence summarizing what this memory contains
- `tags:` — all named entities (commands, tools, patterns) from `### entity — description` headings
- `context:` — related entity references found in inline code or body text

## Body Structure

- **`## Section Labels`** — for document organization. These are NOT auto-tagged.
- **`### entity-name — description`** — for each named entity. These ARE auto-tagged by check #11.
- Every `###` heading must include a separator character (`—`, `:`, or `–`)
- Hyphen (`-`) is NOT a separator — it is part of kebab-case names
- Avoid bare `### Problem` or `### Overview` — use `##` or bold markers instead

## Minimum Requirements by Type

| `type:` | Requirement | Enforced by |
|---------|-------------|-------------|
| `reference` | ≥1 entity heading | check #14 |
| `feedback` | `**Why:**` + `**How to apply:**` | check #13 |
| `project` | `**Why:**` + `**How to apply:**` | check #13 |
| `task` | `skill:` field recommended | — |

## Example

```markdown
---
name: ssh-debugging-guide
description: Debugging techniques and known pitfalls for SSH connection issues
metadata:
  type: reference
  tags: [ssh, debugging, networking]
  context: [openssh, connection-timeout, permission-denied]
  references: []
  confidence: confirmed
  priority: 4
---

## Overview

Common SSH failures and how to diagnose them.

### ssh-connection-timeout — server unreachable or firewalled

Diagnostic steps for `Connection timed out` errors...

### ssh-permission-denied — public key authentication failures

Checklist for `Permission denied (publickey)` errors...
```
