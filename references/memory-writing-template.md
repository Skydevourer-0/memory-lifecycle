# Memory Writing Guide

## Metadata Fields

| Field | Required | Format | Purpose |
|-------|----------|--------|---------|
| `name` | yes | kebab-case slug | File identity, matches filename |
| `description` | yes | one sentence | Summary, used in INDEX |
| `references` | no | list of slugs | Citation graph edges |
| `read-when` | no | list of phrases | Recall triggers for grep |

## Body Structure

Free-form. Recommended: `### entity-name — description` sections for each named concept.

No required sections. No auto-populated fields. No tags, context, priority, confidence, type, created, or ttl.

## Example

---
name: ssh-debugging-guide
description: Debugging techniques and known pitfalls for SSH connection issues
references:
  - networking-basics
read-when:
  - ssh connection timeout
  - permission denied publickey
  - debugging ssh issues
---

## Overview

Common SSH failures and how to diagnose them.

### ssh-connection-timeout — server unreachable or firewalled

Diagnostic steps for `Connection timed out` errors...

### ssh-permission-denied — public key authentication failures

Checklist for `Permission denied (publickey)` errors...
