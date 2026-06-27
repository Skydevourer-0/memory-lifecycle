# Validation Checks Reference

14 structural checks run during `sync-memory`. Level: E=error, W=warning, I=info.
--fix = auto-fixable. auto = fixed during writeback.

| # | Check | Level | --fix | Detects |
|---|-------|-------|-------|---------|
| 1 | missing-required | E | — | name or description missing |
| 2 | invalid-name | E | — | name not kebab-case |
| 3 | invalid-type | E | — | metadata.type not in VALID_TYPES |
| 4 | stale-hash | W | auto | content_hash mismatch (writeback fixes) |
| 5 | ttl-expired | W | — | created + ttl is in the past |
| 6 | broken-references | E | ✅ | references to unknown slugs |
| 7 | orphan-confirmed | W | — | confirmed, 0 incoming refs, >180d stale |
| 8 | upgradable-speculative | I | — | speculative with ≥3 incoming refs |
| 9 | missing-context | I | — | context array empty or missing |
| 10 | empty-tags | I | — | tags array empty or missing |
| 11 | heading-not-in-tags | W | ✅ | entity heading terms not in tags |
| 12 | inline-code-not-in-context | I | ✅ | inline `code` entities not in context |
| 13 | feedback-missing-structure | I | — | feedback/project missing **Why:**/**How to apply:** |
| 14 | reference-missing-entity-headings | I | — | reference has no `### entity — desc` headings |

## Valid Types

`user`, `feedback`, `project`, `reference`, `task`

## Scoring Formula

**Knowledge memories:** `score = priority + in×2.0 + out×0.5 + confidence_map + max(0, 10−days)`

**Task memories:** `score = priority + confidence_map + max(0, 10−days)` (no graph inputs — tasks are rendered in their own INDEX section regardless of score)

**Confidence map:** confirmed = 3, speculative = 1, deprecated = −5
