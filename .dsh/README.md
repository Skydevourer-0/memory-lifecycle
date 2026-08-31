DSH adaptation (native hook)

Uses the DSH native Cordis extension point agent/session-start to do what the
Claude Code / Codex SessionStart hook does: run memory-sync.py session-start on
session start and inject the project HOTLIST as context.

Does NOT use a hook bridge and does NOT change scripts/memory-sync.py.

Install (replace the path with this directory's absolute path):
  dsh plugin add link:C:/Users/ruanletian/.cc-switch/skills/memory-lifecycle/.dsh
