#!/usr/bin/env python3
"""memory-lifecycle sync engine — v2.1 metadata.jsonl-based."""

import argparse
import json
import os
import sys

# Ensure the scripts directory is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def get_mem_dir(scope_from_file=None):
    """Resolve memory directory. Test override -> scope-from-file -> CWD detection.
    When scope_from_file is given, derive mem_dir directly from the file path
    (the file already lives under the correct memory dir, no need for git discovery)."""
    test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
    if test_dir:
        return test_dir
    if scope_from_file:
        expanded = os.path.expanduser(scope_from_file)
        project_prefix = os.path.expanduser("~/.claude/projects/")
        global_mem = os.path.expanduser("~/.claude/global/memory")
        if expanded.startswith(global_mem):
            return global_mem
        if expanded.startswith(project_prefix):
            # Extract ~/.claude/projects/<slug>/memory from the file path
            rel = expanded[len(project_prefix):]
            parts = rel.split(os.sep, 1)
            if parts:
                slug = parts[0]
                return os.path.join(project_prefix, slug, "memory")
        # Fallback: scope detection from CWD
        return common.get_memory_dir("project", cwd=os.path.dirname(expanded))
    return common.get_memory_dir(common.detect_scope())


def get_jsonl_path(mem_dir):
    return os.path.join(mem_dir, "metadata.jsonl")


def get_index_path(mem_dir):
    return os.path.join(mem_dir, "INDEX.md")


def cmd_sync(mem_dir, dry_run=False, scope_from_file=None):
    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)

    new_stubs = 0
    new_slugs = []
    for fname in sorted(os.listdir(mem_dir)):
        if fname.endswith(".md") and fname not in ("INDEX.md", "MEMORY.md", "README.md"):
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
    with open(index_path, "w") as f:
        f.write(index_md)

    global_mem = os.path.expanduser("~/.claude/global/memory")
    scope = "global" if mem_dir.rstrip("/") == global_mem.rstrip("/") else "project"
    hot_target = common.get_hot_list_target(scope, cwd=None)
    test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
    if test_dir:
        print(f"[TEST MODE] Skipping hot list injection (would write to {hot_target}).")
    elif not os.path.exists(hot_target) or not common.inject_hot_list(hot_target, metadata):
        if os.path.exists(hot_target) and common.ensure_markers(hot_target):
            print(f"Added memory-index markers to {hot_target}.")
            common.inject_hot_list(hot_target, metadata)
            print(f"Hot list updated in {hot_target}")
        else:
            print(f"WARNING: No memory-index markers in {hot_target}. Run install.py or add markers manually.")

    print(f"INDEX.md written ({len(metadata)} memories)")
    if new_stubs:
        print(f"{new_stubs} new memories awaiting metadata. Run $SM hint <slug> for each.")
    return 0


def cmd_hint(mem_dir, slug, hook_mode=False):
    """Return (exit_code, needs_review). needs_review=True when required metadata
    fields (description, read_when) are missing or likely stale.

    In hook_mode: diagnostic text goes to stderr (user-visible via UI notification),
    not stdout — only the additionalContext JSON should land on stdout.
    In manual mode: everything goes to stdout so the model can read it."""
    out = sys.stderr if hook_mode else sys.stdout
    if slug in ("INDEX", "MEMORY", "README"):
        return 0, False  # silent skip for hot-list and infrastructure files
    md_path = os.path.join(mem_dir, f"{slug}.md")
    if not os.path.exists(md_path):
        print(f"{slug}: file not found", file=sys.stderr)
        return 1, False

    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)

    if slug not in metadata:
        print(f"{slug}: not yet registered in metadata. Run sync-memory sync first, then hint again.",
              file=out)
        return 1, False

    with open(md_path, "r") as f:
        body = f.read()

    headings = common.extract_headings(body)
    entry = metadata[slug]
    existing_refs = entry.get("references", [])
    current_slugs = [n for n in metadata if n != slug]
    global_mem_dir = os.path.expanduser("~/.claude/global/memory")
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
    needs_review = False
    print("  Status:", file=out)
    if not desc.strip():
        print(f"    description  ✗  required, min 20 chars", file=out)
        needs_review = True
    else:
        print(f"    description  (review)  {desc[:70]}", file=out)
    if not rw:
        print(f"    read_when    ✗  required, min 1 phrase, max 8", file=out)
        needs_review = True
    else:
        print(f"    read_when    (review)  {rw[:3]}{'...' if len(rw) > 3 else ''}", file=out)
    if refs:
        print(f"    references   (review)  {refs}", file=out)
    else:
        print(f"    references   [empty]  optional, max 10", file=out)
    print(f"  Next:  $SM set-metadata {slug} <<'EOF' ...", file=out)
    return 0, needs_review


def cmd_set_metadata(mem_dir, slug):
    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)

    if slug not in metadata:
        print(f"{slug}: no metadata entry. Run sync-memory sync first.", file=sys.stderr)
        return 1

    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"invalid JSON: {e}", file=sys.stderr)
        return 2

    if not isinstance(data, dict):
        print("input must be a JSON object", file=sys.stderr)
        return 2

    current_slugs = set(metadata.keys())
    global_slugs = set()
    global_mem_dir = os.path.expanduser("~/.claude/global/memory")
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
    with open(index_path, "w") as f:
        f.write(common.generate_index_md(metadata))

    test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
    if not test_dir:
        scope = "global" if mem_dir.rstrip("/") == os.path.expanduser("~/.claude/global/memory").rstrip("/") else "project"
        hot_target = common.get_hot_list_target(scope, cwd=None)
        if os.path.exists(hot_target):
            common.inject_hot_list(hot_target, metadata)

    return 0


def cmd_audit(mem_dir):
    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)
    _, in_degree = common.compute_scores(metadata)  # compute_scores returns (scores, in_degree)

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


def main():
    parser = argparse.ArgumentParser(prog="sync-memory", description="Memory lifecycle sync engine v2.1")
    sub = parser.add_subparsers(dest="command")

    sync_p = sub.add_parser("sync", help="Full sync: scan .md, update metadata, rebuild INDEX, update hot list")
    sync_p.add_argument("--dry-run", action="store_true", help="Validate only, no writes")
    sync_p.add_argument("--scope-from-file", type=str, help="Pin scope from file path")
    sub.add_parser("sync-and-hint", help="DEPRECATED: use sync + hint as separate hooks")
    hint_p = sub.add_parser("hint", help="Show metadata hints for a memory")
    hint_p.add_argument("slug", nargs="?", help="Memory slug")
    set_p = sub.add_parser("set-metadata", help="Batch write metadata from stdin JSON")
    set_p.add_argument("slug")
    del_p = sub.add_parser("delete", help="Delete a memory and clean dangling refs")
    del_p.add_argument("slug")
    del_p.add_argument("--dry-run", action="store_true", help="Validate only, no writes")
    del_p.add_argument("--scope-from-file", type=str, help="Pin scope from file path")
    sub.add_parser("audit", help="Structural audit of the memory graph")
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # PostToolUse hook pipes tool result JSON to stdin. sync and hint both
    # consume it (for scope + slug). Each hook gets its own independent pipe.
    hook_data = None
    scope_file = getattr(args, 'scope_from_file', None)
    if args.command in ("sync", "hint", "sync-and-hint") and not sys.stdin.isatty() and not scope_file:
        import select
        if select.select([sys.stdin], [], [], 0.1)[0]:
            try:
                data = json.load(sys.stdin)
                if data.get("hook_event_name"):
                    hook_data = data
                    scope_file = data.get("tool_input", {}).get("file_path", "")
            except Exception:
                pass

    mem_dir = get_mem_dir(scope_from_file=scope_file) if scope_file else get_mem_dir()
    os.makedirs(mem_dir, exist_ok=True)

    dry_run = getattr(args, 'dry_run', False)

    if args.command == "sync":
        return cmd_sync(mem_dir, dry_run=dry_run, scope_from_file=args.scope_from_file)
    elif args.command == "sync-and-hint":
        # DEPRECATED — kept for compatibility only, use sync + hint hooks instead.
        ret = cmd_sync(mem_dir, dry_run=dry_run, scope_from_file=scope_file)
        if ret != 0:
            return ret
        if not scope_file:
            return 0
        slug = os.path.splitext(os.path.basename(scope_file))[0]
        _ec, needs_review = cmd_hint(mem_dir, slug, hook_mode=True)
        if needs_review:
            ctx = (f"Metadata stale for '{slug}'. "
                   f"Run $SM set-metadata {slug} to update read_when / description.")
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": f"⚠️ {ctx}"
                }
            }))
            return 1
        return 0
    elif args.command == "hint":
        # From hook: stdin has file_path; from CLI: args.slug.
        fp = args.slug
        if not fp and hook_data:
            fp = hook_data.get("tool_input", {}).get("file_path", "")
        if not fp:
            print("hint: no file path. Run $SM hint <slug>.", file=sys.stderr)
            return 1

        slug = os.path.splitext(os.path.basename(fp))[0]
        exit_code, needs_review = cmd_hint(mem_dir, slug, hook_mode=bool(hook_data))

        if hook_data:
            if needs_review:
                ctx = (f"Metadata stale for '{slug}'. "
                       f"Run $SM set-metadata {slug} to update read_when / description.")
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": f"⚠️ {ctx}"
                    }
                }))
                return 1
            return 0 if exit_code == 0 else 1
        # Manual mode: actual errors (file not found, not in metadata) still
        # exit non-zero; needs_review is suppressed so model can read stdout.
        return exit_code
    elif args.command == "set-metadata":
        return cmd_set_metadata(mem_dir, args.slug)
    elif args.command == "delete":
        return cmd_delete(mem_dir, args.slug, dry_run=dry_run, scope_from_file=args.scope_from_file)
    elif args.command == "audit":
        return cmd_audit(mem_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
