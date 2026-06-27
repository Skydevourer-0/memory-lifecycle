#!/usr/bin/env python3
"""install.py — Cross-platform installer for memory-lifecycle v2.

Idempotent installer that:
1. Creates ~/.claude/global/memory/ directory if it doesn't exist
2. Adds memory-index markers to ~/.claude/CLAUDE.md if not present
3. Registers the PostToolUse hook (auto-sync on Write/Edit/MultiEdit of memory files)
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path


def get_claude_dir() -> Path:
    """Return the ~/.claude directory path."""
    return Path.home() / ".claude"


def get_skill_scripts_dir() -> Path:
    """Return the directory where this install script lives (same dir as memory-sync.py)."""
    return Path(__file__).resolve().parent


def get_memory_sync_path() -> Path:
    """Return the absolute path to memory-sync.py."""
    return get_skill_scripts_dir() / "memory-sync.py"


def step_create_memory_dir() -> bool:
    """Create ~/.claude/global/memory/ if it doesn't exist. Returns True if created."""
    memory_dir = get_claude_dir() / "global" / "memory"
    if memory_dir.is_dir():
        print(f"[OK] Directory already exists: {memory_dir}")
        return False
    memory_dir.mkdir(parents=True, exist_ok=True)
    print(f"[CREATED] Directory: {memory_dir}")
    return True


def step_add_memory_markers() -> bool:
    """Add memory-index markers to CLAUDE.md if not present. Returns True if added."""
    claude_md = get_claude_dir() / "CLAUDE.md"
    start_marker = "<!-- memory-index:start -->"
    end_marker = "<!-- memory-index:end -->"

    if not claude_md.is_file():
        content = f"{start_marker}\n{end_marker}\n"
        claude_md.write_text(content, encoding="utf-8")
        print(f"[CREATED] {claude_md} with memory-index markers")
        return True

    content = claude_md.read_text(encoding="utf-8")
    if start_marker in content and end_marker in content:
        print(f"[OK] Memory-index markers already present in {claude_md}")
        return False

    # Append markers at the end
    if not content.endswith("\n"):
        content += "\n"
    content += f"{start_marker}\n{end_marker}\n"
    claude_md.write_text(content, encoding="utf-8")
    print(f"[ADDED] Memory-index markers to {claude_md}")
    return True


def _build_hook_entry() -> dict:
    """Build the PostToolUse hook entry for this platform."""
    memory_sync = get_memory_sync_path()
    abs_path = str(memory_sync)

    if platform.system() == "Windows":
        command = (
            'powershell -NoProfile -ExecutionPolicy Bypass -Command '
            f'"[Console]::OutputEncoding=[Text.Encoding]::UTF8; & \'{sys.executable}\' \'{abs_path}\'"'
        )
    else:
        command = f"'{sys.executable}' '{abs_path}'"

    return {
        "matcher": "Write|Edit|MultiEdit",
        "pathPattern": "**/.claude/**/memory/*.md",
        "hooks": [
            {
                "type": "command",
                "command": command,
                "async": False,
            }
        ],
    }


def _hook_already_registered(existing_hooks: list, new_hook: dict) -> bool:
    """Check if an equivalent PostToolUse hook is already present.

    Checks for matching matcher + pathPattern, and a hooks list whose
    commands reference the memory-sync.py script.
    """
    for entry in existing_hooks:
        if not isinstance(entry, dict):
            continue
        if entry.get("matcher") != new_hook["matcher"]:
            continue
        if entry.get("pathPattern") != new_hook["pathPattern"]:
            continue
        hooks_list = entry.get("hooks", [])
        for h in hooks_list:
            if not isinstance(h, dict):
                continue
            cmd = h.get("command", "")
            if "memory-sync.py" in cmd and str(sys.executable) in cmd:
                return True
    return False


def step_register_hook() -> bool:
    """Register the PostToolUse hook in settings.json. Returns True if added."""
    settings_path = get_claude_dir() / "settings.json"
    new_hook = _build_hook_entry()

    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[ERROR] Failed to read {settings_path}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        settings = {}

    # Ensure nested structure exists
    if "hooks" not in settings:
        settings["hooks"] = {}
    if "PostToolUse" not in settings["hooks"]:
        settings["hooks"]["PostToolUse"] = []

    existing = settings["hooks"]["PostToolUse"]
    if _hook_already_registered(existing, new_hook):
        print(
            f"[OK] PostToolUse hook for memory-sync already registered in {settings_path}"
        )
        return False

    existing.append(new_hook)
    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"[ADDED] PostToolUse hook to {settings_path}")
    return True


def main():
    print("=== memory-lifecycle Installer ===")
    print(f"Platform: {platform.system()} ({platform.release()})")
    print(f"Scripts directory: {get_skill_scripts_dir()}")
    print(f"Memory-sync path:  {get_memory_sync_path()}")
    print(f"Claude directory:  {get_claude_dir()}")
    print()

    step_create_memory_dir()
    step_add_memory_markers()
    step_register_hook()

    print()
    print("=== Installation complete ===")
    print("Project-scope setup is automatic: sync creates MEMORY.md + markers on first run.")


if __name__ == "__main__":
    main()
