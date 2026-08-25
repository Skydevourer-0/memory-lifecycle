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


def _hook_payload(hook):
    """Flatten command + args of a hook for matching. Handles both the new
    exec-form hooks and legacy command-string hooks."""
    if not isinstance(hook, dict):
        return ""
    return " ".join([hook.get("command", ""), *(hook.get("args") or [])])


def _is_ours(hook):
    return "memory-sync.py" in _hook_payload(hook)


POST_TOOLS = ("Write", "Edit", "MultiEdit")


def _build_hook(python_exe, script, *args):
    """Build a single Claude Code hook in exec (args) form: `command` is
    spawned directly with the argument list, never through a shell. On
    Windows a plain command string runs through Git Bash, which strips
    backslashes (D:\\scoop\\... -> D:scoop...), so Windows paths must never
    reach a shell parser. Args form also removes the need for quoting."""
    return {"type": "command", "command": python_exe, "args": [script, *args]}


def _build_hooks(python_exe, script, *args):
    """PostToolUse hooks: one entry per file tool, each gated by an `if`
    basename filter `*.md` so non-markdown writes never spawn the hook.
    Note: the `if` glob matches basenames only on Windows (directory globs
    fail against backslash paths), but `*.md` still excludes all code
    writes; the script guard remains the authoritative filter."""
    return [
        {"type": "command", "command": python_exe, "if": f"{tool}(*.md)", "args": [script, *args]}
        for tool in POST_TOOLS
    ]


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
            remaining = [h for h in hooks if not _is_ours(h)]
        else:
            remaining = hooks
        block["hooks"] = remaining
        if not remaining:
            continue  # block became empty (ours) -> drop
        # 旧 pathPattern 只属于本技能旧块;有本技能 hook 的块不再需要它
        if block.get("pathPattern") == OLD_PATH_PATTERN and any(
            _is_ours(h) for h in remaining
        ):
            block.pop("pathPattern", None)
        cleaned.append(block)
    return cleaned


def _ensure_hooks(blocks, new_hooks):
    """Append a new matcher block with the hooks, unless every hook already
    exists in some block (idempotent). Never merges into other skills'
    blocks."""
    for block in blocks:
        if not isinstance(block, dict):
            continue
        existing = block.get("hooks")
        if not isinstance(existing, list):
            continue
        missing = [
            h for h in new_hooks
            if not any(
                isinstance(x, dict)
                and x.get("command") == h["command"]
                and x.get("args") == h.get("args")
                and x.get("if") == h.get("if")
                for x in existing
            )
        ]
        if not missing:
            return False
    blocks.append({"matcher": None, "hooks": list(new_hooks)})
    return True


def _set_block_matcher(blocks, matcher, hooks):
    """Ensure a block with the given matcher + hooks exists."""
    if _ensure_hooks(blocks, hooks):
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
    post_hooks = _build_hooks(python_exe, SCRIPT, "sync-and-hint")
    session_hook = _build_hook(python_exe, SCRIPT, "session-start")
    _set_block_matcher(hooks["PostToolUse"], POST_MATCHER, post_hooks)
    _set_block_matcher(hooks["SessionStart"], SESSION_MATCHER, [session_hook])

    _atomic_write_json(settings_path, settings)

    print(f"  PostToolUse: {POST_MATCHER} -> sync-and-hint (no pathPattern)")
    print(f"  SessionStart: {SESSION_MATCHER} -> session-start")
    print("  autoMemoryEnabled left untouched (native auto-memory preserved).")
    print("Installation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
