#!/usr/bin/env python3
"""Install memory-lifecycle v2.2 for ZCode.

- Registers PostToolUse (Write|Edit|MultiEdit) -> sync-and-hint
- Registers SessionStart (startup|resume|clear|compact) -> session-start
- Merges into ~/.zcode/cli/config.json (the ZCode user configuration file);
  never clobbers other skills' hooks or unrelated keys
- Sets hooks.enabled = true (ZCode configuration-file hooks are disabled by
  default; without this flag no hook runs)
- Creates ~/.cc-switch/memory/{global,projects}

ZCode hook notes:
- Hook entries use type "process" (command + args, no shell): a "command"
  hook accepts only command/shell/timeout/timeoutMs — an args field would be
  dropped — and a plain command string would run Windows paths through a
  shell parser that strips backslashes.
- Unlike Codex there is no /hooks trust gate: with enabled=true these hooks
  run unconditionally.
- ZCode has no per-path `if` filter, so the PostToolUse hook fires on EVERY
  Write|Edit call; memory-sync.py's fast pre-filter (payload must contain
  "memory") keeps the no-op cost at interpreter floor.
"""

import json
import os
import sys
import tempfile

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SKILL_ROOT, "scripts", "memory-sync.py")

POST_MATCHER = "Write|Edit|MultiEdit"
SESSION_MATCHER = "startup|resume|clear|compact"


def _config_path():
    """ZCode user configuration file. $ZCODE_HOME is honored for parity with
    CODEX_HOME; the documented default is ~/.zcode/cli/config.json."""
    home = os.environ.get("ZCODE_HOME") or os.path.expanduser("~/.zcode")
    return os.path.join(os.path.expanduser(home), "cli", "config.json")


def _atomic_write_json(path, data):
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except Exception:
        os.unlink(tmp_path)
        raise
    os.replace(tmp_path, path)


def _hook_payload(hook):
    """Flatten command + args of a hook for matching (process and command
    types)."""
    if not isinstance(hook, dict):
        return ""
    return " ".join([str(hook.get("command", "")), *(str(a) for a in (hook.get("args") or []))])


def _is_ours(hook):
    return "memory-sync.py" in _hook_payload(hook)


def _clean_event_groups(events):
    """Remove our hooks from every event's matcher groups; drop emptied
    groups. Other skills' hooks are never touched."""
    for name, groups in events.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            group["hooks"] = [h for h in group["hooks"] if not _is_ours(h)]
        events[name] = [g for g in groups if isinstance(g, dict) and g.get("hooks")]
    return {k: v for k, v in events.items() if v}


def _build_hook(python_exe, script, *args):
    """ZCode hook in process form: an argument vector run without a shell —
    the documented most-portable choice, and the only type that accepts
    args[]. Windows paths must never reach a shell parser (backslash
    stripping), and quoting rules differ per consumer; args form removes
    both problems."""
    return {"type": "process", "command": python_exe, "args": [script, *args]}


def _set_group(events, event, matcher, hooks):
    """Ensure a matcher group with exactly our hooks exists (idempotent)."""
    groups = events.setdefault(event, [])
    if isinstance(groups, list) and any(
        isinstance(g, dict) and g.get("matcher") == matcher and g.get("hooks") == hooks
        for g in groups
    ):
        return False
    groups.append({"matcher": matcher, "hooks": list(hooks)})
    return True


def main():
    config_path = _config_path()
    print(f"Installing memory-lifecycle v2.2 for ZCode ({config_path})...")

    for d in (
        os.path.expanduser("~/.cc-switch/memory/global"),
        os.path.expanduser("~/.cc-switch/memory/projects"),
    ):
        os.makedirs(d, exist_ok=True)
        print(f"  Memory directory: {d}")

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {}
        print(f"  Creating {config_path}")

    hooks = config.setdefault("hooks", {})
    events = hooks.setdefault("events", {})
    events = _clean_event_groups(events)

    python_exe = sys.executable
    post_hook = _build_hook(python_exe, SCRIPT, "sync-and-hint")
    session_hook = _build_hook(python_exe, SCRIPT, "session-start")
    _set_group(events, "PostToolUse", POST_MATCHER, [post_hook])
    _set_group(events, "SessionStart", SESSION_MATCHER, [session_hook])
    hooks["events"] = events

    # Configuration-file hooks are disabled by default in ZCode; without
    # enabled=true nothing runs. Never downgrade an existing true.
    hooks["enabled"] = True

    _atomic_write_json(config_path, config)

    print(f"  PostToolUse: {POST_MATCHER} -> sync-and-hint")
    print(f"  SessionStart: {SESSION_MATCHER} -> session-start")
    print("  hooks.enabled set to true (required for configuration hooks).")
    print("  No /hooks approval needed (unlike Codex). Hook runs are logged")
    print("  in the ZCode log (outcome, duration, error preview).")
    print("Installation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
