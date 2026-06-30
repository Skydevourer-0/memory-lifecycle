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
    """Resolve memory directory. Test override -> scope-from-file -> CWD detection."""
    test_dir = os.environ.get("_MEMORY_SYNC_TEST_DIR")
    if test_dir:
        return test_dir
    if scope_from_file:
        scope = common.detect_scope_from_file(scope_from_file)
    else:
        scope = common.detect_scope()
    return common.get_memory_dir(scope, cwd=os.path.dirname(scope_from_file) if scope_from_file else None)


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
        print(f"{new_stubs} new memories awaiting metadata. Run sync-memory --hint <slug> for each.")
    return 0


def cmd_hint(mem_dir, slug):
    md_path = os.path.join(mem_dir, f"{slug}.md")
    if not os.path.exists(md_path):
        print(f"{slug}: file not found", file=sys.stderr)
        return 1

    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)

    if slug not in metadata:
        print(f"{slug}: not yet registered in metadata. Run sync-memory first, then --hint again.")
        return 1

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

    print(f"Metadata hints for '{slug}':")
    if headings:
        print("  Body headings (suggested read_when):")
        for h in headings:
            print(f"    ## {h}")
    print(f"  Existing references: {len(existing_refs)}/10  [{', '.join(existing_refs)}]" if existing_refs else f"  Existing references: 0/10")
    if current_slugs:
        print(f"  Available slugs (current scope):  [{', '.join(current_slugs)}]")
    if global_metadata:
        global_names = list(global_metadata.keys())
        print(f"  Available slugs (global, with prefix):  [{', '.join(f'global:{n}' for n in global_names)}]")
    desc = entry.get("description", "")
    rw = entry.get("read_when", [])
    refs = entry.get("references", [])
    print("  Status:")
    print(f"    description  {'✓' if len(desc.strip()) >= 20 else '✗'}  {'ok' if len(desc.strip()) >= 20 else 'required, min 20 chars'}")
    print(f"    read_when    {'✓' if rw else '✗'}  {'ok' if rw else 'required, min 1 phrase, max 8'}")
    print(f"    references   {'✓' if refs else '○'}  {'ok' if refs else 'optional, max 10'}")
    print(f"  Next:  sync-memory --set-metadata {slug} <<'EOF' ... EOF")
    return 0


def cmd_set_metadata(mem_dir, slug):
    jsonl_path = get_jsonl_path(mem_dir)
    metadata = common.read_metadata(jsonl_path)

    if slug not in metadata:
        print(f"{slug}: no metadata entry. Run sync-memory first.", file=sys.stderr)
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
    hint_p = sub.add_parser("hint", help="Show metadata hints for a memory")
    hint_p.add_argument("slug")
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

    mem_dir = get_mem_dir(scope_from_file=getattr(args, 'scope_from_file', None))
    os.makedirs(mem_dir, exist_ok=True)

    dry_run = getattr(args, 'dry_run', False)

    if args.command == "sync":
        return cmd_sync(mem_dir, dry_run=dry_run, scope_from_file=args.scope_from_file)
    elif args.command == "hint":
        return cmd_hint(mem_dir, args.slug)
    elif args.command == "set-metadata":
        return cmd_set_metadata(mem_dir, args.slug)
    elif args.command == "delete":
        return cmd_delete(mem_dir, args.slug, dry_run=dry_run, scope_from_file=args.scope_from_file)
    elif args.command == "audit":
        return cmd_audit(mem_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
