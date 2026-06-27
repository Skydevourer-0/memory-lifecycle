#!/usr/bin/env python3
"""Remove a memory file and clean up dangling references."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _slugify(name: str) -> str:
    """Sanitize a directory name to kebab-case for use as a project slug."""
    name = name.lower()
    name = re.sub(r'[-]+', '-', name)
    name = re.sub(r'[^a-z0-9-]', '-', name)
    return name.strip('-')


def safe_read_text(filepath: Path) -> str | None:
    """Read a file as UTF-8, return None if encoding fails."""
    try:
        return filepath.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        print(f"Warning: cannot read {filepath} (not valid UTF-8), skipping", file=sys.stderr)
        return None


def find_memory_dir() -> Path:
    """Auto-detect memory directory from CWD.

    - In a git repo: ~/.claude/projects/<project-slug>/memory/
    - Outside git:   ~/.claude/global/memory/
    """
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            slug = _slugify(parent.name)
            return Path.home() / ".claude" / "projects" / slug / "memory"
    return Path.home() / ".claude" / "global" / "memory"


def find_referencing_files(memory_dir: Path, slug: str) -> list[Path]:
    """Find .md files whose references list includes the given slug."""
    refs = []
    for md in sorted(memory_dir.glob("*.md")):
        if md.stem == slug or md.name in ("INDEX.md", "MEMORY.md"):
            continue
        content = safe_read_text(md)
        if content is None:
            continue
        if not content.startswith("---"):
            continue
        end_idx = content.find("---", 3)
        if end_idx == -1:
            continue
        frontmatter = content[3:end_idx]
        # Check only the references block in frontmatter — exact line match
        for line in frontmatter.split("\n"):
            stripped = line.strip()
            if stripped == f"- {slug}" or stripped == f"[{slug}]" or stripped == f"references: [{slug}]":
                refs.append(md)
                break
    return refs


def remove_reference_line(filepath: Path, slug: str) -> bool:
    """Remove '- slug' from references block in frontmatter only. Returns True if changed."""
    content = safe_read_text(filepath)
    if content is None:
        return False
    if not content.startswith("---"):
        return False
    end_idx = content.find("---", 3)
    if end_idx == -1:
        return False
    frontmatter = content[3:end_idx]
    body_rest = content[end_idx:]

    lines = frontmatter.split("\n")
    new_lines = []
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped == f"- {slug}":
            changed = True
            continue
        new_lines.append(line)

    if changed:
        new_content = "---" + "\n".join(new_lines) + body_rest
        filepath.write_text(new_content, encoding="utf-8")
    return changed


def main():
    parser = argparse.ArgumentParser(description="Remove a memory and clean up references")
    parser.add_argument("slug", help="Memory slug to remove")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    args = parser.parse_args()

    slug = args.slug
    memory_dir = find_memory_dir()
    md_file = memory_dir / f"{slug}.md"

    if not md_file.exists():
        print(f"Memory not found: {md_file}", file=sys.stderr)
        sys.exit(1)

    # Find files referencing this slug
    refs = find_referencing_files(memory_dir, slug)
    if refs:
        print(f"These files reference '{slug}':")
        for rf in refs:
            print(f"  {rf.name}")
    else:
        print(f"No files reference '{slug}'.")

    if args.dry_run:
        print(f"\nWould delete: {md_file}")
        if refs:
            print(f"Would remove dangling references from {len(refs)} file(s).")
        return

    # Confirm
    if not args.yes:
        response = input(f"\nDelete '{slug}.md'? [y/N] ").strip().lower()
        if response != "y":
            print("Aborted.")
            return

    # Delete the file
    md_file.unlink()
    print(f"Deleted: {md_file}")

    # Clean up references in other files
    if refs:
        if not args.yes:
            response = input("Remove dangling references? [y/N] ").strip().lower()
            if response != "y":
                print("Skipping reference cleanup.")
                refs = []
        for rf in refs:
            changed = remove_reference_line(rf, slug)
            if changed:
                print(f"  Cleaned: {rf.name}")

    # Rebuild INDEX by running sync (pass scope context)
    sync_script = Path(__file__).parent / "memory-sync.py"
    result = subprocess.run(
        [sys.executable, str(sync_script), "--scope-from-file", str(md_file)],
        capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print(f"Warning: INDEX rebuild failed (exit {result.returncode})", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
    else:
        print("INDEX rebuilt.")


if __name__ == "__main__":
    main()
