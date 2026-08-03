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
    """Register PostToolUse hook in ~/.claude/settings.json.
    Ensures BOTH sync and hint hooks exist in the memory-lifecycle matcher block.
    Adds missing hooks individually rather than skipping on first match."""
    settings_path = os.path.expanduser("~/.claude/settings.json")
    if not os.path.exists(settings_path):
        print("  SKIP: ~/.claude/settings.json not found, cannot register hook")
        return

    with open(settings_path, "r") as f:
        settings = json.load(f)

    hooks = settings.setdefault("hooks", {})
    post_hooks = hooks.setdefault("PostToolUse", [])

    hook_script = os.path.expanduser("~/.claude/skills/memory-lifecycle/scripts/memory-sync.py")

    # Find or create the memory-lifecycle matcher block
    target_block = None
    for mblock in post_hooks:
        for h in mblock.get("hooks", []):
            if "memory-sync" in h.get("command", ""):
                target_block = mblock
                break
        if target_block:
            break

    if target_block is None:
        # No existing block — create new one
        target_block = {
            "matcher": "Write|Edit|MultiEdit",
            "pathPattern": "**/.claude/**/memory/*.md",
            "hooks": []
        }
        post_hooks.append(target_block)

    # Ensure pathPattern is set (may be missing after manual edit)
    if "pathPattern" not in target_block:
        target_block["pathPattern"] = "**/.claude/**/memory/*.md"

    # Collect existing command names
    existing_commands = set()
    for h in target_block.get("hooks", []):
        cmd = h.get("command", "")
        if cmd.endswith(" sync"):
            existing_commands.add("sync")
        elif cmd.endswith(" hint"):
            existing_commands.add("hint")

    if "sync" in existing_commands and "hint" in existing_commands:
        print("  PostToolUse hooks (sync + hint) already registered.")
        return

    added = []
    python_exe = sys.executable
    if "sync" not in existing_commands:
        target_block["hooks"].append({"type": "command", "command": f'"{python_exe}" "{hook_script}" sync'})
        added.append("sync")
    if "hint" not in existing_commands:
        target_block["hooks"].append({"type": "command", "command": f'"{python_exe}" "{hook_script}" hint'})
        added.append("hint")

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    print(f"  PostToolUse hooks registered: {', '.join(added)}")


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
