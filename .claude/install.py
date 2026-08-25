#!/usr/bin/env python3
"""Install memory-lifecycle v2.2 for Claude Code.

- Registers PostToolUse (Write|Edit|MultiEdit, no pathPattern) -> sync-and-hint
- Registers SessionStart (startup|resume|clear|compact) -> session-start
- Removes legacy memory-lifecycle hooks (sync / hint / old sync-and-hint)
- Creates ~/.cc-switch/memory/{global,projects}
- Never touches autoMemoryEnabled or other skills' hook blocks
"""

import json
import os
import sys
import tempfile

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SKILL_ROOT, "scripts", "memory-sync.py")

POST_MATCHER = "Write|Edit|MultiEdit"
SESSION_MATCHER = "startup|resume|clear|compact"
OLD_PATH_PATTERN = "**/.claude/**/memory/*.md"


def _atomic_write_json(path, data):
    """Atomic write (os.replace) with explicit UTF-8."""
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except Exception:
        os.unlink(tmp_path)
        raise
    os.replace(tmp_path, path)


def _is_ours(command):
    return "memory-sync.py" in (command or "")


def _fmt_cmd(python_exe, script, *args):
    """Build a hook command. Quote a path only when it contains spaces:
    Windows cmd / CreateProcess fails when the executable name itself is
    quoted (e.g. Codex hooks), while a quoted path with spaces works on
    Claude Code. Unquoted paths work everywhere."""
    def _q(p):
        return f'"{p}"' if " " in p else p
    return " ".join([_q(python_exe), _q(script), *args])


def _cleanup_blocks(blocks):
    """Remove our hooks from blocks; drop empty blocks; drop our old pathPattern.

    Other skills' hooks are never touched."""
    cleaned = []
    for block in blocks:
        if not isinstance(block, dict):
            cleaned.append(block)
            continue
        hooks = block.get("hooks")
        if isinstance(hooks, list):
            remaining = [h for h in hooks if not (isinstance(h, dict) and _is_ours(h.get("command", "")))]
        else:
            remaining = hooks
        block["hooks"] = remaining
        if not remaining:
            continue  # block became empty (ours) -> drop
        # 旧 pathPattern 只属于本技能旧块;有本技能 hook 的块不再需要它
        if block.get("pathPattern") == OLD_PATH_PATTERN and any(
            isinstance(h, dict) and _is_ours(h.get("command", "")) for h in remaining
        ):
            block.pop("pathPattern", None)
        cleaned.append(block)
    return cleaned


def _ensure_command(blocks, command):
    """Append a new matcher block with the command, unless an identical command
    already exists (idempotent). Never merges into other skills' blocks."""
    for block in blocks:
        if not isinstance(block, dict):
            continue
        hooks = block.get("hooks")
        if isinstance(hooks, list) and any(
            isinstance(h, dict) and h.get("command") == command for h in hooks
        ):
            return False
    blocks.append({"matcher": None, "hooks": [{"type": "command", "command": command}]})
    return True


def _set_block_matcher(blocks, matcher, command):
    """Ensure a block with the given matcher + command exists."""
    if _ensure_command(blocks, command):
        blocks[-1]["matcher"] = matcher
    return True


def main():
    print("Installing memory-lifecycle v2.2 for Claude Code...")

    for d in (
        os.path.expanduser("~/.cc-switch/memory/global"),
        os.path.expanduser("~/.cc-switch/memory/projects"),
    ):
        os.makedirs(d, exist_ok=True)
        print(f"  Memory directory: {d}")

    settings_path = os.path.expanduser("~/.claude/settings.json")
    if os.path.exists(settings_path):
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    else:
        settings = {}
        print(f"  Creating {settings_path}")

    hooks = settings.setdefault("hooks", {})
    post = hooks.setdefault("PostToolUse", [])
    session = hooks.setdefault("SessionStart", [])
    hooks["PostToolUse"] = _cleanup_blocks(post)
    hooks["SessionStart"] = _cleanup_blocks(session)

    python_exe = sys.executable
    post_cmd = _fmt_cmd(python_exe, SCRIPT, "sync-and-hint")
    session_cmd = _fmt_cmd(python_exe, SCRIPT, "session-start")
    _set_block_matcher(hooks["PostToolUse"], POST_MATCHER, post_cmd)
    _set_block_matcher(hooks["SessionStart"], SESSION_MATCHER, session_cmd)

    _atomic_write_json(settings_path, settings)

    print(f"  PostToolUse: {POST_MATCHER} -> sync-and-hint (no pathPattern)")
    print(f"  SessionStart: {SESSION_MATCHER} -> session-start")
    print("  autoMemoryEnabled left untouched (native auto-memory preserved).")
    print("Installation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
