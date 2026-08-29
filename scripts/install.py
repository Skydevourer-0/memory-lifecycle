#!/usr/bin/env python3
"""Compatibility entry point for memory-lifecycle installers.

Auto-detects the target environment and forwards:
  ~/.claude/settings.json exists  -> Claude Code installer (.claude/install.py)
  else $CODEX_HOME / ~/.codex     -> Codex installer (.codex/install.py)
  else ~/.zcode exists            -> ZCode installer (.zcode/install.py)
Use --claude / --codex / --zcode to force a specific end. With no target,
prints usage.
"""

import argparse
import os
import subprocess
import sys

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(installer_path):
    print(f"Forwarding to {installer_path}")
    return subprocess.call([sys.executable, installer_path])


def main():
    parser = argparse.ArgumentParser(description="memory-lifecycle installer (compat entry)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--claude", action="store_true", help="Force the Claude Code installer")
    group.add_argument("--codex", action="store_true", help="Force the Codex installer")
    group.add_argument("--zcode", action="store_true", help="Force the ZCode installer")
    args = parser.parse_args()

    claude_installer = os.path.join(SKILL_ROOT, ".claude", "install.py")
    codex_installer = os.path.join(SKILL_ROOT, ".codex", "install.py")
    zcode_installer = os.path.join(SKILL_ROOT, ".zcode", "install.py")

    if args.claude:
        return _run(claude_installer)
    if args.codex:
        return _run(codex_installer)
    if args.zcode:
        return _run(zcode_installer)

    if os.path.exists(os.path.expanduser("~/.claude/settings.json")):
        return _run(claude_installer)

    if os.path.isdir(common_codex_home()):
        return _run(codex_installer)

    if os.path.isdir(os.path.expanduser("~/.zcode")):
        return _run(zcode_installer)

    print("No target environment detected (no ~/.claude/settings.json, no Codex home, no ~/.zcode).")
    print("Usage:")
    print(f"  python {claude_installer}   # Claude Code")
    print(f"  python {codex_installer}    # Codex")
    print(f"  python {zcode_installer}    # ZCode")
    return 1


def common_codex_home():
    env = os.environ.get("CODEX_HOME")
    if env:
        return os.path.expanduser(env)
    return os.path.expanduser("~/.codex")


if __name__ == "__main__":
    sys.exit(main())