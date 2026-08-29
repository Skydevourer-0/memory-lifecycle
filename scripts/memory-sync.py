#!/usr/bin/env python3
"""memory-lifecycle sync engine — v2.3 (Claude Code + Codex + ZCode).

Data SSOT: ~/.cc-switch/memory/{global,projects/<slug>}.
Hooks: PostToolUse sync-and-hint (soft hint, exit 0) + SessionStart session-start.
"""

import os
import sys
import time

# ── Fast pre-filter (PostToolUse hot path) ──────────────────────────────────────────────────────────────
# Windows hook schemas have no path-level filter (Claude Code `if` globs are
# basename-only; Codex has no `if`/pathPattern at all), so the PostToolUse hook
# fires on EVERY file edit. To keep the no-op cost near the Python interpreter
# floor instead of importing the whole engine, we read the payload here and
# exit before `import common` when it provably cannot be a memory write. The
# buffered bytes are handed to the engine afterwards (no double read).

_HOOK_PREFIX_BUF = None  # bytes already read from stdin by the fast pre-filter


def _read_hook_prefix():
    """Bounded non-blocking read of the full hook payload.

    Returns the bytes read (possibly b""), or None on I/O failure (fail-open).
    Reads until EOF or a short deadline; a real hook call has the payload fully
    written before the hook process starts, so EOF arrives immediately.
    """
    try:
        if sys.stdin.isatty():
            return None
        fd = sys.stdin.fileno()
        os.set_blocking(fd, False)
        buf = bytearray()
        deadline = time.monotonic() + 0.25
        try:
            while time.monotonic() < deadline:
                try:
                    chunk = os.read(fd, 1 << 16)
                except BlockingIOError:
                    time.sleep(0.001)
                    continue
                if not chunk:
                    break
                buf += chunk
        finally:
            try:
                os.set_blocking(fd, True)
            except OSError:
                pass
        return bytes(buf)
    except (BlockingIOError, OSError, ValueError):
        return None


# Fast path: for the PostToolUse hook command, skip the engine when the payload
# provably contains no memory path. Every path the precise guard accepts carries
# the literal dir segment "memory" (~/.cc-switch/memory/... or
# ~/.claude/projects/<slug>/memory/...), and the payload always includes the
# resolved path (or the cwd used to resolve it). Empty payload -> run the engine
# (its existing no-op path exits 0). Case-insensitive on purpose.
if len(sys.argv) >= 2 and sys.argv[1] == "sync-and-hint":
    _HOOK_PREFIX_BUF = _read_hook_prefix()
    if _HOOK_PREFIX_BUF and b"memory" not in _HOOK_PREFIX_BUF.lower():
        sys.exit(0)

import argparse
import json
import queue
import re
import shutil
import threading

# Ensure the scripts directory is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

INFRA_FILES = ("INDEX.md", "MEMORY.md", "README.md", "HOTLIST.md")
INFRA_SLUGS = ("INDEX", "MEMORY", "README", "HOTLIST")

HOOK_STDIN_TIMEOUT = 0.3
MAX_HINTS = 3

APPLY_PATCH_FILE_RE = re.compile(r"^\*\*\*\s*(Update|Add|Delete)\s+File:\s*(.+?)\s*$", re.MULTILINE)


def _reconfigure_utf8():
    """Force UTF-8 on stdout/stderr for ALL modes (Windows locale defaults to
    GBK, which breaks piped consumers and mixed-encoding output)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _read_pipe_stdin(timeout=HOOK_STDIN_TIMEOUT):
    """Read all bytes from the stdin pipe using a thread + short timeout.

    Thread is required because select.select does not work on Windows pipes.
    Returns bytes, or None when the pipe did not reach EOF in time (slow /
    incomplete write)."""
    result = queue.Queue()

    def _reader():
        try:
            result.put(sys.stdin.buffer.read())
        except Exception as exc:
            result.put(exc)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    value = result.get()
    if isinstance(value, Exception):
        return None
    return value


def _parse_payload_bytes(raw):
    """Parse hook payload bytes. Returns (payload, invalid).

    invalid=True means stdin carried bytes that are not a valid hook payload
    (incomplete JSON, wrong shape, no recognized hook keys) — callers must
    fail open (empty stdout, exit 0) and NEVER fall back to CWD scope.

    Key recognition is platform-tolerant: Claude Code always carries
    `hook_event_name`; Codex and ZCode payloads may differ in casing or omit
    it (ZCode emits Claude-style events but the input contract is not
    documented), so any dict carrying a known hook key
    (hook_event_name/hookEventName/tool_input/toolInput/tool_name/toolName/
    cwd/session_id/source) is accepted."""
    if not raw or not raw.strip():
        return None, False
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None, True
    if not isinstance(data, dict):
        return None, True
    known = (
        "hook_event_name", "hookEventName",
        "tool_input", "toolInput", "tool_name", "toolName",
        "cwd", "session_id", "source",
    )
    if not any(k in data for k in known):
        return None, True
    return data, False


def _load_hook_payload():
    """Read + parse hook payload from stdin (blocking, short timeout).

    Uses the bytes already buffered by the fast pre-filter when present, so the
    payload is never read twice."""
    if sys.stdin.isatty():
        return None, False
    global _HOOK_PREFIX_BUF
    if _HOOK_PREFIX_BUF is not None:
        raw = _HOOK_PREFIX_BUF
        _HOOK_PREFIX_BUF = None
    else:
        raw = _read_pipe_stdin()
    if raw is None:
        return None, True
    return _parse_payload_bytes(raw)


def _load_hook_payload_peek():
    """Read + parse hook payload only when data is already buffered.

    Used for dual-use commands (sync): manual invocations with an empty pipe
    must not stall on the timeout."""
    if sys.stdin.isatty():
        return None, False
    fd = None
    first = None
    try:
        fd = sys.stdin.fileno()
        os.set_blocking(fd, False)
        try:
            first = os.read(fd, 65536)
        except BlockingIOError:
            first = b""
    except Exception:
        first = None
    finally:
        if fd is not None:
            try:
                os.set_blocking(fd, True)
            except Exception:
                pass
    if first is None:
        return None, False
    if not first:
        return None, False
    rest = _read_pipe_stdin() or b""
    return _parse_payload_bytes(first + rest)


def _is_under_memory_dir(filepath):
    """Guard: only the new prefix ~/.cc-switch/memory/ counts.

    Native auto-memory territory (~/.claude/projects/<slug>/memory) is NOT
    matched here — it is routed to _import_native_memory instead. The old
    ~/.claude/global/memory layout is legacy skill territory (migrate only)."""
    expanded = common._normalized(filepath)
    root = common._normalized("~/.cc-switch/memory")
    return expanded.startswith(root + os.sep)


NATIVE_SOURCE = "native"


def _is_native_memory_path(filepath):
    """True for files under Claude Code's native auto-memory territory:
    ~/.claude/projects/<slug>/memory/. Only this form counts."""
    expanded = common._normalized(filepath)
    projects_root = common._normalized("~/.claude/projects")
    if not expanded.startswith(projects_root + os.sep):
        return False
    rel = expanded[len(projects_root + os.sep):]
    parts = rel.split(os.sep)
    return len(parts) >= 2 and parts[1] == "memory"


def _parse_native_memory(path):
    """Parse a native auto-memory file. Returns (slug, description, body) or None.

    Native format (frontmatter):
        ---
        name: <kebab-slug>
        description: <one-line summary>
        metadata:
          type: user|feedback|project|reference
        ---
        <body>
    Slug falls back to the filename when frontmatter omits it; files without
    frontmatter are treated as body-only."""
    basename = os.path.basename(path)
    if not basename.endswith(".md") or basename in INFRA_FILES:
        return None
    slug = basename[:-3]
    if not common.validate_slug(slug):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    description = ""
    body = raw
    text = raw.lstrip()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            front = text[3:end]
            body = text[end + 4:].lstrip("\n")
            for line in front.splitlines():
                if line.startswith("name:"):
                    slug = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
            if not common.validate_slug(slug):
                return None
    return slug, description, body


def _synthesize_read_when(slug, description):
    """Derive read_when phrases from a native description (native memories
    have no read_when field). Fallback: the slug itself (kebab slugs are
    >= 10 chars for real names)."""
    phrases = []
    for part in re.split(r"[.,;:!?()]+", description):
        part = part.strip()
        if len(part) < 10:
            continue
        if not any(w.lower() not in common.STOPWORDS for w in part.split()):
            continue
        phrases.append(part)
        if len(phrases) >= 2:
            break
    if not phrases and len(slug) >= 10:
        phrases.append(slug)
    if common.validate_read_when(phrases):
        return []
    return phrases


def _import_native_memory(scope_file, base_cwd):
    """One-way ingest of a native auto-memory file into the managed store.

    Scope comes from the session cwd (git root -> project store; no git root
    -> global store). The native file is left untouched. The managed copy is
    updated in place only while the memory is still native-owned: a memory
    the user has curated (no native marker, or metadata diverged from what
    we imported) is never overwritten. Returns (ok, message); message is
    None when nothing happened."""
    parsed = _parse_native_memory(scope_file)
    if parsed is None:
        return False, None
    slug, description, body = parsed
    mem_dir = common.get_memory_dir(common.detect_scope(cwd=base_cwd), cwd=base_cwd)
    os.makedirs(mem_dir, exist_ok=True)
    md_path = os.path.join(mem_dir, f"{slug}.md")
    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)
    existing = metadata.get(slug)

    if existing is not None:
        if existing.get("source") != NATIVE_SOURCE:
            return False, f"memory '{slug}' already managed manually; native copy left as-is"
        if existing.get("imported_description") and existing.get("description") != existing.get("imported_description"):
            return False, f"memory '{slug}' curated by user; native copy left as-is"

    common.atomic_write_text(md_path, body if body.endswith("\n") else body + "\n")
    entry = {
        "name": slug,
        "description": description,
        "read_when": _synthesize_read_when(slug, description),
        "references": (existing or {}).get("references", []),
        "source": NATIVE_SOURCE,
        "imported_description": description,
    }
    common.write_metadata(jsonl_path, slug, entry)
    cmd_sync(mem_dir)
    rel = os.path.relpath(mem_dir, os.path.expanduser("~"))
    return True, f"记忆 {slug}.md 已从原生 auto-memory 摄取 -> ~/{rel}"


def _native_memory_dir_for_cwd(cwd):
    """Locate the native auto-memory dir for a cwd by computing Claude Code's
    native slug form (sanitization: non-alnum -> '-', case kept, no folding)."""
    real = os.path.realpath(cwd)
    native_slug = re.sub(r"[^a-zA-Z0-9]", "-", real)
    return os.path.join(os.path.expanduser("~/.claude/projects"), native_slug, "memory")


def cmd_import_native():
    """Backfill native auto-memory for the current project into the managed
    store. Needed because auto-dream background writes never fire the
    PostToolUse hook (real-time ingestion only sees agent tool writes)."""
    cwd = os.getcwd()
    native_dir = _native_memory_dir_for_cwd(cwd)
    if not os.path.isdir(native_dir):
        print("No native auto-memory directory for this project.", file=sys.stderr)
        return 0
    imported = 0
    for fname in sorted(os.listdir(native_dir)):
        path = os.path.join(native_dir, fname)
        if not os.path.isfile(path):
            continue
        ok, note = _import_native_memory(path, cwd)
        if ok:
            imported += 1
        if note:
            print(note)
    if imported:
        print(f"Imported {imported} native memory file(s).")
    else:
        print("No native memories imported.", file=sys.stderr)
    return 0


def get_mem_dir(scope_from_file=None):
    """Resolve memory directory. Test override -> scope-from-file -> CWD detection."""
    test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
    if test_dir:
        return test_dir
    if scope_from_file:
        mem_dir = common.resolve_mem_dir_from_file(scope_from_file)
        if mem_dir:
            return mem_dir
        if common.detect_scope_from_file(scope_from_file) == "global":
            return common.get_memory_dir("global")
        return common.get_memory_dir(common.detect_scope(cwd=os.path.dirname(os.path.abspath(scope_from_file))))
    return common.get_memory_dir(common.detect_scope())


def get_jsonl_path(mem_dir):
    return os.path.join(mem_dir, "metadata.jsonl")


def get_index_path(mem_dir):
    return os.path.join(mem_dir, "INDEX.md")


def _refresh_hot_list(mem_dir, metadata):
    """Rebuild hot list targets for the scope of mem_dir.

    global -> inject/refresh marker blocks in CLAUDE.md + AGENTS.md
              (missing targets are lazily created with a marker skeleton).
    project -> standalone full write of HOTLIST.md (no markers, no header)."""
    if common._is_global_mem_dir(mem_dir):
        for target in common.get_hot_list_target("global"):
            if not os.path.exists(target):
                common.atomic_write_text(
                    target,
                    common.HOT_LIST_MARKER_START + "\n" + common.HOT_LIST_MARKER_END + "\n",
                )
                print(f"Created hot list skeleton at {target}")
            if common.inject_hot_list(target, metadata):
                print(f"Hot list updated in {target}")
            elif common.ensure_markers(target):
                print(f"Added memory-index markers to {target}.")
                common.inject_hot_list(target, metadata)
                print(f"Hot list updated in {target}")
            else:
                print(f"WARNING: No memory-index markers in {target}. Run install.py or add markers manually.")
        return
    target = os.path.join(mem_dir, "HOTLIST.md")
    common.inject_hot_list(target, metadata, standalone=True)
    print(f"HOTLIST.md written ({len(common.hot_list_lines(metadata))} entries) to {target}")


def cmd_sync(mem_dir, dry_run=False, scope_from_file=None):
    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)

    new_stubs = 0
    new_slugs = []
    for fname in sorted(os.listdir(mem_dir)):
        if fname.endswith(".md") and fname not in INFRA_FILES:
            slug = fname[:-3]
            if not common.validate_slug(slug):
                print(f"WARNING: '{fname}' -- invalid slug format, skipped")
                continue
            if slug not in metadata:
                metadata[slug] = {"name": slug, "description": "", "read_when": [], "references": []}
                new_stubs += 1
                new_slugs.append(slug)

    orphans = [name for name in metadata if not os.path.exists(os.path.join(mem_dir, f"{name}.md"))]
    for name in orphans:
        del metadata[name]
    if orphans:
        print(f"Removed {len(orphans)} orphan metadata entries (no .md file).")

    slug_set = set(metadata.keys())
    broken = []
    for name, entry in metadata.items():
        for ref in entry.get("references", []):
            clean = ref.replace("global:", "", 1)
            if clean not in slug_set and not ref.startswith("global:"):
                broken.append(f"  {name}: references unknown slug '{ref}'")
    if broken:
        print("Broken references (reported, not blocking sync):")
        for b in broken:
            print(b)

    for name, entry in metadata.items():
        dups = common.duplicate_read_when(entry.get("read_when", []))
        if dups:
            print(f"WARNING: '{name}' has duplicate read_when phrases: {', '.join(dups)} (not blocking)")

    if dry_run:
        if new_stubs:
            for slug in new_slugs:
                print(f"[DRY-RUN] Would add stub: {slug}")
            print(f"[DRY-RUN] Would add {new_stubs} stub(s)")
        if orphans:
            for name in orphans:
                print(f"[DRY-RUN] Would remove: {name}")
            print(f"[DRY-RUN] Would remove {len(orphans)} orphan(s)")
        print("[DRY-RUN] No changes written.")
        return 0

    if new_stubs or orphans:
        common.write_all_metadata(jsonl_path, metadata)

    index_path = get_index_path(mem_dir)
    index_md = common.generate_index_md(metadata)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_md)

    test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
    if test_dir:
        print("[TEST MODE] Skipping hot list injection (would write to real targets).")
    else:
        _refresh_hot_list(mem_dir, metadata)

    print(f"INDEX.md written ({len(metadata)} memories)")
    if new_stubs:
        print(f"{new_stubs} new memories awaiting metadata. Run $SM hint <slug> for each.")
    return 0


def _build_soft_hint(mem_dir, slug):
    """Soft, non-blocking hint text for a memory write. No emoji, no [REVIEW]."""
    md_path = os.path.join(mem_dir, f"{slug}.md")
    if not os.path.exists(md_path):
        return None
    metadata = common.read_metadata(get_jsonl_path(mem_dir))
    entry = metadata.get(slug)
    if entry is None or not (entry.get("description") or "").strip():
        return (
            f"记忆 {slug}.md 已写入,元数据待补。\n"
            f"运行 $SM set-metadata {slug} <<'EOF' 补充 description / read_when / references。"
        )
    rw = entry.get("read_when", [])
    rw_str = ", ".join(rw) if rw else "(空)"
    return (
        f"记忆 {slug}.md 已写入/更新。\n"
        f"read-when: {rw_str}\n"
        f"如正文有新增内容,运行 $SM set-metadata {slug} 同步元数据。"
    )


def _parse_hook_files(payload):
    """Extract (path, op) pairs from a hook payload.

    Claude Code / ZCode Write|Edit (ZCode aliases apply_patch onto Write/
    Edit matchers): tool_input.file_path (op='write').
    Codex apply_patch: tool_input.command lines '*** (Update|Add|Delete)
    File: <path>' (multiple files supported; relative paths resolved later
    against payload cwd). tool_input/toolInput both accepted — ZCode's input
    contract is not documented, camelCase is cheap insurance."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = payload.get("toolInput")
    if not isinstance(tool_input, dict):
        return []
    files = []
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str):
        file_path = tool_input.get("filePath")
    if isinstance(file_path, str) and file_path.strip():
        files.append((file_path.strip(), "write"))
    command = tool_input.get("command")
    if isinstance(command, str):
        for m in APPLY_PATCH_FILE_RE.finditer(command):
            files.append((m.group(2).strip(), m.group(1).lower()))
    return files


def cmd_sync_and_hint(payload):
    """PostToolUse hook: sync + soft hint. Always exit 0 (non-blocking).

    Stdout carries ONLY the hook JSON — sync/delete diagnostics are routed to
    stderr while their commands run."""
    files = _parse_hook_files(payload)
    if not files:
        return 0
    base = payload.get("cwd") or os.getcwd()
    hits = []
    import_notes = []
    for fp, op in files:
        path = fp if os.path.isabs(fp) else os.path.join(base, fp)
        if _is_native_memory_path(path):
            # Native auto-memory write -> one-way ingest into the managed store.
            _ok, note = _import_native_memory(path, base)
            if note:
                import_notes.append(note)
            continue
        if not _is_under_memory_dir(path):
            continue
        mem_dir = common.resolve_mem_dir_from_file(path)
        if not mem_dir:
            continue
        slug = os.path.splitext(os.path.basename(path))[0]
        if slug in INFRA_SLUGS:
            continue
        hits.append((mem_dir, slug, op))
    if not hits and not import_notes:
        return 0

    # Dedupe by (mem_dir, slug)
    seen = set()
    unique = []
    for h in hits:
        key = (h[0], h[1])
        if key not in seen:
            seen.add(key)
            unique.append(h)

    deletes = [h for h in unique if h[2] == "delete"]
    writes = [h for h in unique if h[2] != "delete"]

    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        for mem_dir, slug, _op in deletes:
            cmd_delete(mem_dir, slug)  # infra/unknown slugs already filtered; failures are no-ops
        for mem_dir in {h[0] for h in writes}:
            cmd_sync(mem_dir)
    finally:
        sys.stdout = real_stdout

    hints = []
    for note in import_notes:
        hints.append(note)
    for mem_dir, slug, _op in writes:
        hint = _build_soft_hint(mem_dir, slug)
        if hint:
            hints.append(hint)
        if len(hints) >= MAX_HINTS:
            break
    if hints:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(hints),
            }
        }, ensure_ascii=False))
    return 0


def _hook_hint(mem_dir, slug):
    """Legacy hint hook-mode: soft hint JSON, exit 0."""
    if slug in INFRA_SLUGS:
        return 0
    hint = _build_soft_hint(mem_dir, slug)
    if hint:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": hint,
            }
        }, ensure_ascii=False))
    return 0


def cmd_hint(mem_dir, slug, hook_mode=False):
    """Manual metadata hints. hook_mode routes diagnostics to stderr."""
    out = sys.stderr if hook_mode else sys.stdout
    if slug in INFRA_SLUGS:
        return 0  # silent skip for hot-list and infrastructure files
    md_path = os.path.join(mem_dir, f"{slug}.md")
    if not os.path.exists(md_path):
        print(f"{slug}: file not found", file=sys.stderr)
        return 1

    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)

    if slug not in metadata:
        print(f"{slug}: not yet registered in metadata. Run sync-memory sync first, then hint again.",
              file=out)
        return 1

    with open(md_path, "r", encoding="utf-8") as f:
        body = f.read()

    headings = common.extract_headings(body)
    entry = metadata[slug]
    existing_refs = entry.get("references", [])
    current_slugs = [n for n in metadata if n != slug]
    global_mem_dir = common.get_memory_dir("global")
    global_metadata = {}
    if os.path.exists(global_mem_dir) and mem_dir != global_mem_dir:
        global_metadata = common.read_metadata(os.path.join(global_mem_dir, "metadata.jsonl"))

    print(f"Metadata hints for '{slug}':", file=out)
    if headings:
        print("  Body headings (candidates for read_when):", file=out)
        for h in headings:
            print(f"    ## {h}", file=out)
    print(f"  Existing references: {len(existing_refs)}/10  [{', '.join(existing_refs)}]" if existing_refs else f"  Existing references: 0/10", file=out)
    if current_slugs:
        print(f"  Available slugs (current scope):  [{', '.join(current_slugs)}]", file=out)
    if global_metadata:
        global_names = list(global_metadata.keys())
        print(f"  Available slugs (global, with prefix):  [{', '.join(f'global:{n}' for n in global_names)}]", file=out)
    rw = entry.get("read_when", [])
    refs = entry.get("references", [])
    desc = entry.get("description", "")
    print("  Status:", file=out)
    if not desc.strip():
        print(f"    description  [MISSING]  required, min 20 chars", file=out)
    else:
        print(f"    description  (review)  {desc[:70]}", file=out)
    if not rw:
        print(f"    read_when    [MISSING]  required, min 1 phrase, max 8", file=out)
    else:
        print(f"    read_when    (review)  {rw[:3]}{'...' if len(rw) > 3 else ''}", file=out)
    if refs:
        print(f"    references   (review)  {refs}", file=out)
    else:
        print(f"    references   [empty]  optional, max 10", file=out)
    print(f"  Next:  $SM set-metadata {slug} <<'EOF' ...", file=out)
    return 0


def cmd_set_metadata(mem_dir, slug):
    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)

    if slug not in metadata:
        print(f"{slug}: no metadata entry. Run sync-memory sync first.", file=sys.stderr)
        return 1

    try:
        raw = sys.stdin.buffer.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode(sys.stdin.encoding or "utf-8", errors="replace")
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"invalid JSON: {e}", file=sys.stderr)
        return 2

    if not isinstance(data, dict):
        print("input must be a JSON object", file=sys.stderr)
        return 2

    current_slugs = set(metadata.keys())
    global_slugs = set()
    global_mem_dir = common.get_memory_dir("global")
    if os.path.exists(global_mem_dir) and mem_dir != global_mem_dir:
        global_metadata = common.read_metadata(os.path.join(global_mem_dir, "metadata.jsonl"))
        global_slugs = set(global_metadata.keys())

    result = common.validate_set_metadata_json(data, slug, current_slugs, global_slugs if global_slugs else None)
    if result.get("errors"):
        for err in result["errors"]:
            print(f"ERROR: {err}", file=sys.stderr)
        has_gate_failure = any("min" in e or "chars" in e or "required" in e or "expected" in e or "stopwords" in e or "blacklisted" in e or "generic" in e for e in result["errors"])
        has_unknown = any("unknown" in e for e in result["errors"])
        return 2 if has_gate_failure else 1 if has_unknown else 1

    if "read_when" in data:
        dups = common.duplicate_read_when(data["read_when"])
        if dups:
            print(f"WARNING: duplicate read_when phrases (warn only): {', '.join(dups)}", file=sys.stderr)

    entry = metadata[slug]
    if "description" in data:
        entry["description"] = data["description"]
    if "read_when" in data:
        entry["read_when"] = data["read_when"]
    if "references" in data:
        entry["references"] = data["references"]

    common.write_metadata(jsonl_path, slug, entry)
    print(f"Metadata written for '{slug}'.")
    return cmd_sync(mem_dir)


def cmd_delete(mem_dir, slug, dry_run=False, scope_from_file=None):
    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)

    if slug not in metadata:
        print(f"{slug}: not found", file=sys.stderr)
        return 1

    affected = []
    for name, entry in metadata.items():
        refs = entry.get("references", [])
        if slug in refs or f"global:{slug}" in refs:
            affected.append(name)

    if dry_run:
        print(f"[DRY-RUN] Would delete: {slug}.md")
        if affected:
            print(f"[DRY-RUN] Would clean dangling refs in: {', '.join(affected)}")
        return 0

    md_path = os.path.join(mem_dir, f"{slug}.md")
    if os.path.exists(md_path):
        os.unlink(md_path)

    for name in affected:
        entry = metadata[name]
        entry["references"] = [r for r in entry.get("references", []) if r not in (slug, f"global:{slug}")]
        common.write_metadata(jsonl_path, name, entry)

    common.remove_metadata(jsonl_path, slug)
    print(f"Deleted '{slug}'.")
    if affected:
        print(f"Cleaned dangling references in: {', '.join(affected)}")

    metadata = common.read_metadata(jsonl_path)
    index_path = get_index_path(mem_dir)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(common.generate_index_md(metadata))

    test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
    if not test_dir:
        if common._is_global_mem_dir(mem_dir):
            for target in common.get_hot_list_target("global"):
                if os.path.exists(target):
                    common.inject_hot_list(target, metadata)
        else:
            target = os.path.join(mem_dir, "HOTLIST.md")
            if os.path.exists(target):
                common.inject_hot_list(target, metadata, standalone=True)

    return 0


def cmd_audit(mem_dir):
    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)
    _, in_degree = common.compute_scores(metadata)

    print("=== Memory Graph Audit ===")
    orphans = [
        n for n, e in metadata.items()
        if in_degree.get(n, 0) == 0 and len(e.get("references", [])) == 0
    ]
    if orphans:
        print(f"\nOrphan nodes ({len(orphans)}):")
        for n in orphans:
            print(f"  [{n}] {metadata[n].get('description', '')}")
    else:
        print("\nNo orphans.")

    one_way = [
        n for n, e in metadata.items()
        if len(e.get("references", [])) > 0 and in_degree.get(n, 0) == 0
    ]
    if one_way:
        print(f"\nOne-way edges ({len(one_way)}):")
        for n in one_way:
            refs = metadata[n].get("references", [])
            print(f"  [{n}] -> {refs}")
    else:
        print("\nNo one-way edges.")

    return 0


def _display_graph(mem_dir, metadata, emit, no_mermaid=False):
    """Knowledge graph view: mermaid graph LR of slugs + references."""
    scores, in_degree = common.compute_scores(metadata)
    if no_mermaid:
        _graph_as_table(metadata, in_degree, emit)
        return

    nodes = sorted(metadata.keys(), key=lambda n: (-scores[n], n))
    emit("## 知识图谱")
    if len(nodes) > 50:
        emit(f"<!-- {len(nodes)} nodes, may render densely in Feishu -->")
    emit("```mermaid")
    emit("graph LR")
    for n in nodes:
        out_deg = len(metadata[n].get("references", []))
        if in_degree.get(n, 0) >= 3:
            emit(f'    {n}(["{n}"])')          # 枢纽:圆角矩形 + 加粗
        elif in_degree.get(n, 0) > 0 or out_deg > 0:
            emit(f'    {n}["{n}"]')            # 有连接:方框
        else:
            emit(f'    {n}("{n}")')            # 孤立:圆角
    emit()
    for a in nodes:
        for b in metadata[a].get("references", []):
            # b 已剔除 exclude 与自引用(在 cmd_display 中完成)
            # global: 前缀边跨 scope,标签原样输出含 global:B;本 scope 边无前缀。
            emit(f'    {a} --> {b}')
    emit("```")


def _graph_as_table(metadata, in_degree, emit):
    """--no-mermaid fallback: adjacency list table."""
    emit("## 知识图谱(邻接表形式)")
    emit()
    emit("| 节点 | 引用(出边) | 被引用(入边) |")
    emit("|------|------------|--------------|")
    for a in sorted(metadata.keys()):
        out = metadata[a].get("references", [])
        in_cnt = in_degree.get(a, 0)
        out_str = ", ".join(out) if out else "—"
        in_str = f"{in_cnt}" if in_cnt else "— (孤立)" if not out else "—"
        emit(f"| {a} | {out_str} | {in_str} |")


def _display_stats(mem_dir, metadata, emit):
    """Overview stats view: markdown table of counts + Top 5 hot list."""
    scores, in_degree = common.compute_scores(metadata)
    total = len(metadata)
    with_refs = [e for e in metadata.values() if e.get("references")]
    edges = sum(len(e.get("references", [])) for e in metadata.values())
    cross = sum(1 for e in metadata.values() for r in e.get("references", []) if r.startswith("global:"))
    hubs = sorted((n for n, d in in_degree.items() if d >= 3), key=lambda n: n)
    avg = f"{edges / total:.2f}" if total else "0.00"
    isolates = [n for n, e in metadata.items() if in_degree.get(n, 0) == 0 and not e.get("references")]
    # 预计算每个 slug 的引用集合(去除 global: 前缀),避免双向检查时 O(n²) 重建列表
    refs_clean = {
        n: {r.replace("global:", "", 1) for r in metadata[n].get("references", [])}
        for n in metadata
    }
    bidir = set()
    for a in metadata:
        for r in metadata[a].get("references", []):
            clean = r.replace("global:", "", 1)
            if clean in metadata and a in refs_clean.get(clean, set()):
                bidir.add(tuple(sorted((a, clean))))
    top = sorted(metadata.keys(), key=lambda n: (-scores[n], n))
    best = top[0] if top else None

    emit("## 全景统计")
    emit()
    emit("| 指标 | 数值 |")
    emit("|------|------|")
    emit(f"| 记忆总数 | {total} |")
    emit(f"| 有引用的记忆数 | {len(with_refs)} |")
    emit(f"| 引用边总数 | {edges} |")
    emit(f"| 跨 scope 边数 | {cross} |")
    emit(f"| 枢纽节点(入度≥3) | {len(hubs)} ({', '.join(hubs)}) |" if hubs else f"| 枢纽节点(入度≥3) | 0 |")
    emit(f"| 平均出度 | {avg} |")
    emit(f"| 孤立节点数 | {len(isolates)} |")
    emit(f"| 双向引用对数 | {len(bidir)} |")
    if best:
        emit(f"| 最高分记忆 | {best} ({scores[best]:.1f}) |")
    else:
        emit("| 最高分记忆 | — |")
    topics = _topic_groups(list(metadata.keys()))
    topic_str = ", ".join(f"{k} ({len(v)})" for k, v in topics.items()) if topics else "—"
    emit(f"| 覆盖技术主题 | {len(topics)} ({topic_str}) — 按 slug 前缀自动分组 |")

    emit()
    emit("### 热榜 Top 5(按引用分数排序)")
    emit()
    emit("| 排名 | 记忆 | 入度 | 出度 | 分数 |")
    emit("|------|------|------|------|------|")
    for i, n in enumerate(top[:5], 1):
        out = len(metadata[n].get("references", []))
        emit(f"| {i} | {n} | {in_degree.get(n, 0)} | {out} | {scores[n]:.1f} |")
    coverage = f"{len(with_refs) / total * 100:.0f}%" if total else "0%"
    emit()
    emit(f"**覆盖率**:{coverage} 的记忆建立了引用关系,知识网络已形成初步骨架。分数 = 入度×2 + 出度×0.5,引用越多越核心。")


def _topic_groups(slugs):
    """Coarse grouping by slug prefix. Returns dict topic -> list of slugs."""
    groups = {}
    for s in slugs:
        if s.startswith("onnx-"):
            groups.setdefault("ONNX", []).append(s)
        elif s.startswith(("cc-", "hook-")):
            groups.setdefault("Claude Code", []).append(s)
        elif s.startswith("archived-memory-"):
            groups.setdefault("archived-memory", []).append(s)
        elif s.startswith("git-"):
            groups.setdefault("git", []).append(s)
        elif s.startswith(("workflow-", "codex-")):
            groups.setdefault("workflow", []).append(s)
        else:
            groups.setdefault("other", []).append(s)
    return groups


def _display_timeline(mem_dir, metadata, emit, no_mermaid=False):
    """Accumulation timeline view: mermaid timeline by YYYY-MM buckets (mtime)."""
    from datetime import datetime, timezone
    buckets = {}  # YYYY-MM -> {day: [slug, ...]}
    for slug in metadata:
        path = os.path.join(mem_dir, f"{slug}.md")
        if not os.path.exists(path):
            continue
        ts = os.path.getmtime(path)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        key = dt.strftime("%Y-%m")
        day = f"{dt.month}月{dt.day}日"
        buckets.setdefault(key, {}).setdefault(day, []).append(slug)
    if not buckets:
        emit("## 积累时间线")
        emit()
        emit("> 记忆库为空,暂无时间线数据。")
        return

    if no_mermaid:
        _timeline_as_table(buckets, emit)
        return

    emit("## 积累时间线")
    emit("```mermaid")
    emit("timeline")
    emit("    title 记忆积累时间线")
    for month in sorted(buckets):
        emit(f"    section {month}")
        for day in sorted(buckets[month], key=lambda d: (int(d.split("月")[0]), int(d.split("月")[1].rstrip("日")))):
            slugs = sorted(buckets[month][day])
            for i, s in enumerate(slugs):
                prefix = f"        {day} :" if i == 0 else "              :"
                emit(f"{prefix} {s}")
    emit("```")


def _timeline_as_table(buckets, emit):
    """--no-mermaid fallback: month -> count -> slug list."""
    emit("## 积累时间线")
    emit()
    emit("| 月份 | 当月活跃记忆数 | 记忆列表 |")
    emit("|------|--------------|---------|")
    for month in sorted(buckets):
        day_slugs = sorted(s for day in buckets[month] for s in buckets[month][day])
        emit(f"| {month} | {len(day_slugs)} | {', '.join(day_slugs)} |")


def _resolve_hot_target_for_read(scope, cwd=None):
    """Resolve hot list file path(s) for reading.

    global -> [CLAUDE.md, AGENTS.md] (dual read, sources annotated in output).
    project -> [<mem_dir>/HOTLIST.md].
    In test mode redirect to the test dir mock file, symmetric with cmd_sync's
    test-mode skip."""
    test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
    if test_dir:
        if scope == "global":
            return [os.path.join(test_dir, "CLAUDE.md")]
        return [os.path.join(test_dir, "HOTLIST.md")]
    if scope == "global":
        return common.get_hot_list_target("global")
    if cwd:
        return [os.path.join(cwd, "HOTLIST.md")]
    return [common.get_hot_list_target("project", cwd=cwd)]


def _display_usage(mem_dir, metadata, emit, no_mermaid=False, args=None, scope=None):
    """Usage effect view: hot score bar chart + real hot list block + demo script."""
    scores, in_degree = common.compute_scores(metadata)
    top10 = sorted(metadata.keys(), key=lambda n: (-scores[n], n))[:10]

    if no_mermaid:
        emit("## 使用效果流")
        emit()
        emit("### 热榜分数分布(Top 10)")
        emit()
        emit("| 排名 | 记忆 | 分数 | 入度 | 出度 |")
        emit("|------|------|------|------|------|")
        for i, n in enumerate(top10, 1):
            out = len(metadata[n].get("references", []))
            emit(f"| {i} | {n} | {scores[n]:.1f} | {in_degree.get(n, 0)} | {out} |")
    else:
        _usage_bar_chart(top10, scores, emit)

    _usage_hotlist_block(mem_dir, metadata, emit, scope=scope)
    _usage_demo_script(emit)


def _usage_bar_chart(top10, scores, emit):
    """xychart-beta bar chart of top-10 scores."""
    emit("## 使用效果流")
    emit()
    emit("### 热榜分数分布(Top 10)")
    emit("```mermaid")
    emit('xychart-beta')
    emit('    title "热榜分数分布(Top 10)"')
    slugs = ", ".join(f'"{s}"' for s in top10)
    emit(f'    x-axis [{slugs}]')
    emit('    y-axis "分数" 0 --> 8')
    bars = ", ".join(f"{scores[n]:.1f}" for n in top10)
    emit(f"    bar [{bars}]")
    emit("```")


def _usage_hotlist_block(mem_dir, metadata, emit, scope=None):
    """Read real hot list blocks (CLAUDE.md + AGENTS.md, or HOTLIST.md),
    filter excluded slugs, annotate the source file."""
    if scope is None:
        test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
        if test_dir:
            scope = "global"
        else:
            scope = "global" if common._is_global_mem_dir(mem_dir) else "project"
    hot_targets = _resolve_hot_target_for_read(scope, cwd=mem_dir)
    emit("### 自动召回:双层机制")
    emit()
    emit("**热层(零操作自动加载)**")
    emit()
    emit("全局:Claude 自动加载 CLAUDE.md,Codex 自动加载 AGENTS.md,memory-index 块由 sync 注入。")
    emit("项目:SessionStart hook 注入 HOTLIST.md。")
    emit("当前热榜(按引用分数排序,自动写入):")
    emit()
    found_any = False
    for target in hot_targets:
        if not os.path.exists(target):
            continue
        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
        if scope == "global":
            start = content.find(common.HOT_LIST_MARKER_START)
            end = content.find(common.HOT_LIST_MARKER_END)
            if start == -1 or end == -1 or end <= start:
                continue
            block = content[start + len(common.HOT_LIST_MARKER_START):end]
        else:
            block = content
        emit(f"来源:{target}")
        matched = False
        for line in block.splitlines():
            # 只输出含 metadata 中 slug 的行(exclude 已从 metadata 剔除)
            if any(f"[{s}]" in line or f"({s})" in line for s in metadata):
                emit(line)
                matched = True
                found_any = True
        if not matched:
            emit("> 该来源热榜块中没有匹配的记忆条目。")
    if not found_any:
        emit("> 未找到 memory-index 热榜块,运行 $SM sync 后重试")
    emit()
    emit("**温层(按需 grep)**")
    emit()
    emit("当任务涉及特定主题时,模型主动 grep INDEX.md 的 read-when 字段匹配关键词,")
    emit("再按需读取对应记忆文件。例如任务提及\"ONNX 量化\",命中 onnx-qdq-quant-param-detection 的 read-when 短语。")


def _usage_demo_script(emit):
    """Fixed demo script text (not dynamic data)."""
    emit("### 演示脚本(可照着跑)")
    emit()
    emit("以下命令可在任何已安装 memory-lifecycle 的机器上复现:")
    emit()
    emit('1. 定义命令缩写:`SM="python $HOME/.cc-switch/skills/memory-lifecycle/scripts/memory-sync.py"`')
    emit("2. 查看全景统计:`$SM display --view stats`")
    emit("3. 生成知识图谱(贴 Feishu 自动渲染):`$SM display --view graph`")
    emit("4. 查看积累时间线:`$SM display --view timeline`")
    emit("5. 查看完整演示(四视图合一):`$SM display --view all`")
    emit("6. 脱敏:排除含内部细节的记忆:`$SM display --exclude codex-workflow,cc-memory-injection`")
    emit("7. 验证引用图健康度(对外展示前自查):`$SM audit`")
    emit()
    emit("> 截图说明:以上命令的终端输出截图由文档维护者人工补充。mermaid 代码块直接粘贴到 Feishu 即可自动渲染为图形。")


def cmd_display(mem_dir, args):
    """Read-only: output Markdown + Mermaid visualization for Feishu docs.
    Never writes to disk; never triggers sync; never touches the hot list."""
    if not os.path.isdir(mem_dir):
        print(f"ERROR: memory dir not found: {mem_dir}", file=sys.stderr)
        return 2
    views = args.view.split(",") if args.view != "all" else ["graph", "stats", "timeline", "usage"]
    exclude_set = set(args.exclude.split(",")) if args.exclude else set()
    out = sys.stdout
    if args.out:
        parent = os.path.dirname(os.path.abspath(args.out))
        try:
            os.makedirs(parent, exist_ok=True)
            out = open(args.out, "w", encoding="utf-8")
        except OSError as e:
            print(f"ERROR: cannot write to --out '{args.out}': {e}", file=sys.stderr)
            return 2

    def emit(text=""):
        print(text, file=out)

    try:
        jsonl_path = get_jsonl_path(mem_dir)
        metadata = common.read_metadata(jsonl_path)  # {} if missing

        # scope 解析(与 main() 的 --scope 覆盖一致):显式 > 路径推断 > 测试模式 global。
        # 传给 _display_usage → _usage_hotlist_block,统一热榜读取的 scope 来源。
        if getattr(args, "scope", "auto") in ("global", "project"):
            scope = args.scope
        else:
            test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
            if test_dir:
                scope = "global"
            else:
                scope = "global" if common._is_global_mem_dir(mem_dir) else "project"

        # Stale metadata detection: metadata has entries whose .md files were deleted.
        # Warn (read-only, no auto-cleanup) so the user knows to run sync.
        if metadata:
            md_files = {
                f[:-3] for f in os.listdir(mem_dir)
                if f.endswith(".md") and f not in INFRA_FILES
            }
            stale = [n for n in metadata if n not in md_files]
            if stale:
                print(
                    f"WARNING: metadata has {len(stale)} stale entr{'y' if len(stale) == 1 else 'ies'} "
                    f"({', '.join(sorted(stale))}) with no .md file. Run $SM sync to clean up.",
                    file=sys.stderr,
                )

        # exclude 过滤:剔除 slug + 剔除指向被排除 slug 的边(避免悬空边)
        for slug in list(exclude_set):
            if slug not in metadata:
                print(f"WARNING: exclude slug '{slug}' not found, ignored", file=sys.stderr)
        for slug in exclude_set:
            metadata.pop(slug, None)
        for name, entry in metadata.items():
            entry["references"] = [
                r for r in entry.get("references", [])
                if r.replace("global:", "", 1) not in exclude_set
            ]

        # 防御:拒绝自引用(metadata 校验已禁止,但脏数据时跳过)
        for name, entry in metadata.items():
            entry["references"] = [r for r in entry.get("references", []) if r.replace("global:", "", 1) != name]

        if not metadata:
            # 空库或全部被 exclude 排空 → 空状态占位
            emit("## 知识图谱\n\n> 记忆库为空,暂无节点与引用。先运行 $SM sync 初始化。\n")
            emit("## 全景统计\n\n| 指标 | 数值 |\n|------|------|\n| 记忆总数 | 0 |\n| 引用边总数 | 0 |\n")
            emit("## 积累时间线\n\n> 记忆库为空,暂无时间线数据。\n")
            emit("## 使用效果流\n\n> 记忆库为空。写入第一条记忆后重新运行 display 查看效果。")
            return 0

        for view in views:
            if view == "graph":
                _display_graph(mem_dir, metadata, emit, no_mermaid=args.no_mermaid)
            elif view == "stats":
                _display_stats(mem_dir, metadata, emit)
            elif view == "timeline":
                _display_timeline(mem_dir, metadata, emit, no_mermaid=args.no_mermaid)
            elif view == "usage":
                _display_usage(mem_dir, metadata, emit, no_mermaid=args.no_mermaid, args=args, scope=scope)

        return 0
    finally:
        # 确保文件句柄在任何路径(含异常)下都关闭;stdout 无需关闭。
        if args.out and out is not sys.stdout:
            out.close()


def _migrate_memory(old_dir, new_dir):
    """Copy old global memory .md files + merge metadata.jsonl. Skip existing."""
    if not os.path.isdir(old_dir):
        return None
    os.makedirs(new_dir, exist_ok=True)
    copied = skipped = 0
    for fname in sorted(os.listdir(old_dir)):
        src = os.path.join(old_dir, fname)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(new_dir, fname)
        if os.path.exists(dst):
            skipped += 1
            continue
        shutil.copy2(src, dst)
        copied += 1
    merged = common.read_metadata(os.path.join(new_dir, "metadata.jsonl"))
    old_meta = common.read_metadata(os.path.join(old_dir, "metadata.jsonl"))
    added = 0
    for name, entry in old_meta.items():
        if name not in merged:
            merged[name] = entry
            added += 1
    if added:
        common.write_all_metadata(os.path.join(new_dir, "metadata.jsonl"), merged)
    return copied, skipped, added


def _migrate_workflows(old_dir, new_dir):
    """Copy old workflows files + archived dir. Skip existing."""
    if not os.path.isdir(old_dir):
        return None
    os.makedirs(new_dir, exist_ok=True)
    copied = skipped = 0
    for fname in sorted(os.listdir(old_dir)):
        src = os.path.join(old_dir, fname)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(new_dir, fname)
        if os.path.exists(dst):
            skipped += 1
            continue
        shutil.copy2(src, dst)
        copied += 1
    old_archived = os.path.join(old_dir, "archived")
    new_archived = os.path.join(new_dir, "archived")
    if os.path.isdir(old_archived):
        os.makedirs(new_archived, exist_ok=True)
        for fname in sorted(os.listdir(old_archived)):
            src = os.path.join(old_archived, fname)
            dst = os.path.join(new_archived, fname)
            if not os.path.isfile(src):
                continue
            if os.path.exists(dst):
                skipped += 1
                continue
            shutil.copy2(src, dst)
            copied += 1
    return copied, skipped


def cmd_migrate():
    """Migrate old ~/.claude data into ~/.cc-switch. Idempotent, skip-existing.

    - ~/.claude/global/memory/*.md + metadata.jsonl -> ~/.cc-switch/memory/global/
    - ~/.claude/{global,projects/<slug>}/workflows -> ~/.cc-switch/workflows/...
    NOT migrated: ~/.claude/projects/<slug>/memory/ (native auto-memory territory)."""
    print("=== memory-lifecycle migrate ===")

    result = _migrate_memory(
        os.path.expanduser("~/.claude/global/memory"),
        common.get_memory_dir("global"),
    )
    if result is None:
        print(f"SKIP: old global memory dir not found: {os.path.expanduser('~/.claude/global/memory')}")
    else:
        copied, skipped, added = result
        print(f"global memory: copied {copied} file(s), skipped {skipped} (already exists), merged {added} metadata entry(ies)")

    old_dir = os.path.expanduser("~/.claude/global/workflows")
    result = _migrate_workflows(old_dir, os.path.expanduser("~/.cc-switch/workflows/global"))
    if result is None:
        print(f"SKIP: old global workflows dir not found: {old_dir}")
    else:
        copied, skipped = result
        print(f"global workflows: copied {copied} file(s), skipped {skipped} (already exists)")

    old_projects = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(old_projects):
        print(f"SKIP: old projects dir not found: {old_projects}")
    else:
        total_copied = total_skipped = 0
        for slug in sorted(os.listdir(old_projects)):
            src = os.path.join(old_projects, slug, "workflows")
            if not os.path.isdir(src):
                continue
            result = _migrate_workflows(src, os.path.join(os.path.expanduser("~/.cc-switch/workflows/projects"), slug))
            if result:
                copied, skipped = result
                total_copied += copied
                total_skipped += skipped
        print(f"projects workflows: copied {total_copied} file(s), skipped {total_skipped} (already exists)")

    print("migrate complete (idempotent, safe to re-run).")
    return 0


def cmd_session_start(payload):
    """SessionStart hook: inject project HOTLIST.md via additionalContext.

    Uses stdin JSON cwd (hook call) or os.getcwd() (manual call) to resolve the
    project slug. No git root or missing HOTLIST.md -> empty output, exit 0.
    Global hot list is NOT injected here (CLAUDE.md / AGENTS.md auto-load)."""
    if payload and payload.get("cwd"):
        cwd = payload["cwd"]
    else:
        cwd = os.getcwd()
    if common.detect_scope(cwd=cwd) != "project":
        return 0
    git_root = common._find_git_root(cwd)
    if not git_root:
        return 0
    mem_dir = common.get_memory_dir("project", cwd=git_root)
    hotlist_path = os.path.join(mem_dir, "HOTLIST.md")
    if not os.path.exists(hotlist_path):
        return 0
    with open(hotlist_path, "r", encoding="utf-8") as f:
        content = f.read()
    content = content[:common.HOTLIST_BUDGET]
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": content,
        }
    }, ensure_ascii=False))
    return 0


def _run_hook_command(fn, *args):
    """fail-open wrapper for hook commands: any exception -> stderr + empty
    stdout + exit 0."""
    try:
        return fn(*args)
    except Exception as exc:
        print(f"memory-sync hook error: {exc}", file=sys.stderr)
        return 0


def main():
    parser = argparse.ArgumentParser(prog="sync-memory", description="Memory lifecycle sync engine (Claude Code + Codex + ZCode)")
    sub = parser.add_subparsers(dest="command")

    sync_p = sub.add_parser("sync", help="Full sync: scan .md, update metadata, rebuild INDEX, update hot list")
    sync_p.add_argument("--dry-run", action="store_true", help="Validate only, no writes")
    sync_p.add_argument("--scope-from-file", type=str, help="Pin scope from file path")
    sub.add_parser("sync-and-hint", help="PostToolUse hook: sync + soft hint (exit 0, non-blocking)")
    hint_p = sub.add_parser("hint", help="Show metadata hints for a memory")
    hint_p.add_argument("slug", nargs="?", help="Memory slug")
    set_p = sub.add_parser("set-metadata", help="Batch write metadata from stdin JSON")
    set_p.add_argument("slug")
    del_p = sub.add_parser("delete", help="Delete a memory and clean dangling refs")
    del_p.add_argument("slug")
    del_p.add_argument("--dry-run", action="store_true", help="Validate only, no writes")
    del_p.add_argument("--scope-from-file", type=str, help="Pin scope from file path")
    sub.add_parser("audit", help="Structural audit of the memory graph")
    disp_p = sub.add_parser("display", help="Read-only: output Feishu-pasteable visualization (graph/stats/timeline/usage)")
    disp_p.add_argument("--view", default="all", choices=["graph", "stats", "timeline", "usage", "all"],
                        help="View to output (default: all)")
    disp_p.add_argument("--scope", default="auto", choices=["global", "project", "auto"],
                        help="Memory scope (default: auto-detect)")
    disp_p.add_argument("--exclude", default="", help="Comma-separated slugs to filter out")
    disp_p.add_argument("--out", default="", help="Write output to file (default: stdout)")
    disp_p.add_argument("--no-mermaid", action="store_true", help="Degrade mermaid blocks to markdown tables")
    sub.add_parser("session-start", help="SessionStart hook: inject project HOTLIST.md via additionalContext")
    sub.add_parser("migrate", help="Migrate old ~/.claude data into ~/.cc-switch (idempotent)")
    sub.add_parser("import-native", help="Backfill native auto-memory for the current project into the managed store")
    args = parser.parse_args()
    _reconfigure_utf8()  # 所有模式统一 UTF-8 输出,避免 Windows GBK locale 破坏管道消费者

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "migrate":
        return cmd_migrate()

    if args.command == "import-native":
        return cmd_import_native()

    # Hook payload parsing. Commands that run only as hooks block-read with a
    # short timeout; dual-use commands peek for already-buffered data.
    payload = None
    invalid = False
    if args.command in ("sync-and-hint", "session-start"):
        payload, invalid = _load_hook_payload()
    elif args.command == "sync":
        payload, invalid = _load_hook_payload_peek()
    elif args.command == "hint" and not args.slug:
        payload, invalid = _load_hook_payload()
    if invalid:
        # hook stdin 不完整 JSON -> fail open: empty stdout, exit 0.
        # NEVER fall back to CWD scope (prevents global memories being
        # synced into a project scope).
        return 0

    if args.command == "session-start":
        return _run_hook_command(cmd_session_start, payload)

    scope_file = getattr(args, "scope_from_file", None)
    if payload:
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict) and not scope_file:
            scope_file = tool_input.get("file_path") or None

    # Guard: hook fired for a non-memory file -> silent exit 0.
    # Native auto-memory files are ingested instead (see cmd_sync_and_hint).
    if payload and scope_file and not _is_under_memory_dir(scope_file) and not _is_native_memory_path(scope_file):
        return 0

    if args.command == "sync-and-hint":
        return _run_hook_command(cmd_sync_and_hint, payload)

    mem_dir = get_mem_dir(scope_from_file=scope_file) if scope_file else get_mem_dir()
    if args.command == "display":
        # --scope 显式覆盖 CWD 检测;测试模式(_MEMORY_SYNC_TEST_DIR)优先,保持现有测试行为。
        if getattr(args, "scope", "auto") in ("global", "project") and not os.environ.get("_MEMORY_SYNC_TEST_DIR"):
            mem_dir = common.get_memory_dir(args.scope)
    else:
        os.makedirs(mem_dir, exist_ok=True)

    dry_run = getattr(args, "dry_run", False)

    if args.command == "sync":
        return cmd_sync(mem_dir, dry_run=dry_run, scope_from_file=args.scope_from_file)
    elif args.command == "hint":
        fp = args.slug
        if not fp and payload:
            fp = payload.get("tool_input", {}).get("file_path", "")
        if not fp:
            print("hint: no file path. Run $SM hint <slug>.", file=sys.stderr)
            return 1
        slug = os.path.splitext(os.path.basename(fp))[0]
        if payload:
            return _run_hook_command(_hook_hint, mem_dir, slug)
        return cmd_hint(mem_dir, slug)
    elif args.command == "set-metadata":
        return cmd_set_metadata(mem_dir, args.slug)
    elif args.command == "delete":
        return cmd_delete(mem_dir, args.slug, dry_run=dry_run, scope_from_file=args.scope_from_file)
    elif args.command == "audit":
        return cmd_audit(mem_dir)
    elif args.command == "display":
        return cmd_display(mem_dir, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())