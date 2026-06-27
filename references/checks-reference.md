# Validation Checks Reference

4 structural checks run during `sync-memory`. Level: E=error, W=warning.

| # | Check | Level | --fix | Detects |
|---|-------|-------|-------|---------|
| 1 | missing-required | E | — | name or description missing |
| 2 | invalid-name | E | — | name not kebab-case |
| 3 | stale-hash | W | — | content_hash mismatch (auto-updated in INDEX.json) |
| 4 | broken-references | E | yes | references point to non-existent slug |

## Scoring Formula

```
score = in_degree * 2.0 + out_degree * 0.5 + max(0, 10 - days_since_mtime)
```

Score determines INDEX.md sort order. It does NOT gate recall — read-when matching gates recall.

## Valid Types

N/A — type field removed in v2. All memories use the same 4-field metadata schema.
