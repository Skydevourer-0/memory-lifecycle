#!/usr/bin/env python3
"""Install memory-lifecycle v2.2 for Codex.

- Registers PostToolUse (apply_patch) -> sync-and-hint
- Registers SessionStart (startup|resume|clear|compact) -> session-start
- Merges into $CODEX_HOME/hooks.json (default ~/.codex); never clobbers others
- Removes legacy memory-lifecycle entries
- Warns when config.toml contains an inline [hooks] section
- Prints /hooks trust guidance
"""

import json
import os
import re
import sys
import tempfile

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))
import common  # noqa: E402

SCRIPT = os.path.join(SKILL_ROOT, "scripts", "memory-sync.py")

POST_MATCHER = "apply_patch"
SESSION_MATCHER = "startup|resume|clear|compact"


def _atomic_write_json(path, data):
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix=".hooks-", suffix=".tmp")
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
            continue
        cleaned.append(block)
    return cleaned


def _set_block_matcher(blocks, matcher, command):
    for block in blocks:
        if not isinstance(block, dict):
            continue
        hooks = block.get("hooks")
        if isinstance(hooks, list) and any(
            isinstance(h, dict) and h.get("command") == command for h in hooks
        ):
            return False
    blocks.append({"matcher": matcher, "hooks": [{"type": "command", "command": command}]})
    return True


def _warn_inline_hooks(codex_home):
    config_path = os.path.join(codex_home, "config.toml")
    if not os.path.exists(config_path):
        return
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    if re.search(r"^\s*\[hooks\]\s*$", content, re.MULTILINE):
        print("  WARNING: config.toml contains an inline [hooks] section.")
        print("           Codex merges hooks.json with config.toml and may emit a startup warning.")
        print("           Prefer hooks.json (this installer) or remove the inline section.")


def main():
    codex_home = common.codex_home()
    print(f"Installing memory-lifecycle v2.2 for Codex (CODEX_HOME={codex_home})...")

    for d in (
        os.path.expanduser("~/.cc-switch/memory/global"),
        os.path.expanduser("~/.cc-switch/memory/projects"),
    ):
        os.makedirs(d, exist_ok=True)
        print(f"  Memory directory: {d}")

    hooks_path = os.path.join(codex_home, "hooks.json")
    if os.path.exists(hooks_path):
        with open(hooks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
        print(f"  Creating {hooks_path}")

    hooks = data.setdefault("hooks", {})
    post = hooks.setdefault("PostToolUse", [])
    session = hooks.setdefault("SessionStart", [])
    hooks["PostToolUse"] = _cleanup_blocks(post)
    hooks["SessionStart"] = _cleanup_blocks(session)

    python_exe = sys.executable
    post_cmd = _fmt_cmd(python_exe, SCRIPT, "sync-and-hint")
    session_cmd = _fmt_cmd(python_exe, SCRIPT, "session-start")
    _set_block_matcher(hooks["PostToolUse"], POST_MATCHER, post_cmd)
    _set_block_matcher(hooks["SessionStart"], SESSION_MATCHER, session_cmd)

    _atomic_write_json(hooks_path, data)
    _warn_inline_hooks(codex_home)

    print(f"  PostToolUse: {POST_MATCHER} -> sync-and-hint")
    print(f"  SessionStart: {SESSION_MATCHER} -> session-start")
    print("  Next: run /hooks in the Codex CLI and approve the registered commands.")
    print("  Note: reinstalling (command path changes) re-hashes commands -")
    print("        approve them again via /hooks after any reinstall.")
    print("Installation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
