#!/usr/bin/env python3
"""Install memory-lifecycle v2.1: create dirs, add hot-list markers, register hook."""

import json
import os
import sys


MEMORY_INDEX_START = "<!-- memory-index:start -->"
MEMORY_INDEX_END = "<!-- memory-index:end -->"


def install_markers(filepath):
    """Append markers to file if missing. Returns True if added."""
    if not os.path.exists(filepath):
        print(f"  SKIP: {filepath} not found")
        return False
    with open(filepath, "r") as f:
        content = f.read()
    if MEMORY_INDEX_START in content and MEMORY_INDEX_END in content:
        return False
    with open(filepath, "a") as f:
        f.write(f"\n{MEMORY_INDEX_START}\n{MEMORY_INDEX_END}\n")
    return True


def register_hook():
    """Register PostToolUse hook in ~/.claude/settings.json."""
    settings_path = os.path.expanduser("~/.claude/settings.json")
    if not os.path.exists(settings_path):
        print("  SKIP: ~/.claude/settings.json not found, cannot register hook")
        return

    with open(settings_path, "r") as f:
        settings = json.load(f)

    hooks = settings.setdefault("hooks", {})
    post_hooks = hooks.setdefault("PostToolUse", [])

    hook_script = os.path.expanduser("~/.claude/skills/memory-lifecycle/scripts/memory-sync.py")
    for h in post_hooks:
        if "memory-sync" in h.get("command", "") or "memory-lifecycle" in h.get("command", ""):
            print("  PostToolUse hook already registered.")
            return

    new_hook = {
        "matcher": "Write|Edit|MultiEdit",
        "pathPattern": "**/.claude/**/memory/*.md",
        "hooks": [{"type": "command", "command": f"python3 {hook_script} sync"}]
    }
    post_hooks.append(new_hook)

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    print("  PostToolUse hook registered.")


def main():
    print("Installing memory-lifecycle v2.1...")

    global_mem = os.path.expanduser("~/.claude/global/memory")
    os.makedirs(global_mem, exist_ok=True)
    print(f"  Memory directory: {global_mem}")

    claude_md = os.path.expanduser("~/.claude/CLAUDE.md")
    if install_markers(claude_md):
        print(f"  Added memory-index markers to {claude_md}")

    register_hook()

    print("\nInstallation complete.")
    print("Project MEMORY.md markers will be added lazily on first sync-memory run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
