import json
import os
import re
import tempfile
from datetime import datetime, timezone


SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def read_metadata(jsonl_path: str) -> dict:
    """Read metadata.jsonl, return dict keyed by name. Empty file -> {}."""
    if not os.path.exists(jsonl_path):
        return {}
    result = {}
    with open(jsonl_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            result[entry["name"]] = entry
    return result


def write_metadata(jsonl_path: str, name: str, entry: dict) -> None:
    """Atomic write: read all, upsert entry by name, write to temp + rename."""
    if entry is None:
        raise TypeError("entry must not be None")
    all_entries = read_metadata(jsonl_path)
    all_entries[name] = entry
    write_all_metadata(jsonl_path, all_entries)


def remove_metadata(jsonl_path: str, name: str) -> None:
    """Remove entry by name. Atomic write."""
    all_entries = read_metadata(jsonl_path)
    if name not in all_entries:
        return
    del all_entries[name]
    write_all_metadata(jsonl_path, all_entries)


def write_all_metadata(jsonl_path: str, entries: dict) -> None:
    """Atomic write of all metadata entries to jsonl."""
    dirname = os.path.dirname(jsonl_path)
    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix=".metadata-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for record in entries.values():
                json.dump(record, f, ensure_ascii=False)
                f.write("\n")
    except Exception:
        os.unlink(tmp_path)
        raise
    os.rename(tmp_path, jsonl_path)


def validate_slug(slug: str) -> bool:
    """Validate that slug is lowercase alphanumeric with hyphens."""
    return bool(SLUG_RE.fullmatch(slug))


def _find_git_root(path):
    """Walk upward from path. Return git root path, or None."""
    path = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def detect_scope(cwd=None):
    """Walk from cwd upward. .git found -> 'project', else 'global'."""
    cwd = cwd or os.getcwd()
    return "project" if _find_git_root(cwd) else "global"


def detect_scope_from_file(filepath):
    """Infer scope from file path pattern, not CWD."""
    expanded = os.path.expanduser(filepath)
    global_prefix = os.path.expanduser("~/.claude/global/memory/")
    if expanded.startswith(global_prefix):
        return "global"
    return "project"


def get_memory_dir(scope, cwd=None):
    """Return memory directory path for the given scope."""
    if scope == "global":
        return os.path.expanduser("~/.claude/global/memory")
    cwd = cwd or os.getcwd()
    git_root = _find_git_root(cwd)
    if git_root:
        project_slug = re.sub(r"[^a-z0-9/-]", "-", git_root.lower()).replace("/", "-")
        return os.path.expanduser(f"~/.claude/projects/{project_slug}/memory")
    return os.path.expanduser("~/.claude/global/memory")


DESCRIPTION_BLACKLIST = [
    "tbd", "todo", "placeholder", "待补充", "wip", "draft",
    "to be written", "coming soon"
]
READ_WHEN_BLACKLIST = ["tbd", "todo", "placeholder", "待补充"]
STOPWORDS = {"the", "a", "an", "stuff", "things", "thing", "this", "that", "is", "of", "in", "on", "at", "to", "for"}


def validate_description(desc):
    """Return {'error': msg} on failure, {} on success."""
    stripped = desc.strip()
    if len(stripped) < 20:
        return {"error": "description: min 20 non-whitespace chars"}
    lowered = stripped.lower()
    if lowered in DESCRIPTION_BLACKLIST:
        return {"error": f"description: blacklisted placeholder '{stripped}'"}
    boilerplate_patterns = [
        r"^this is a memory about .+$",
        r"^describes .+$",
        r"^a memory about .+$",
    ]
    for pat in boilerplate_patterns:
        if re.fullmatch(pat, lowered):
            return {"error": "description: too generic, be specific"}
    return {}


def validate_read_when(phrases):
    """Return {'error': msg} on failure, {} on success."""
    if not phrases:
        return {"error": "read-when: min 1 phrase required"}
    if len(phrases) > 8:
        return {"error": "read-when: max 8 phrases"}
    for i, phrase in enumerate(phrases):
        stripped = phrase.strip()
        lowered = stripped.lower()
        if lowered in READ_WHEN_BLACKLIST:
            return {"error": f"read-when[{i}]: blacklisted placeholder"}
        words = stripped.split()
        char_len = len(stripped)
        if len(words) < 2 and char_len < 10:
            return {"error": f"read-when[{i}]: too short (need >= 2 words or >= 10 chars)"}
        content_words = [w for w in words if w.lower() not in STOPWORDS]
        if not content_words:
            return {"error": f"read-when[{i}]: only stopwords"}
    return {}


def validate_references(refs, current_scope_slugs, global_slugs=None, current_slug=None):
    """Return {'error': msg} or {} on success.
    current_scope_slugs is the full set of slugs in the current scope
    (including the slug being validated). Self-reference is detected
    when a ref matches current_slug.
    """
    if len(refs) > 10:
        return {"error": "references: max 10 refs"}
    all_slugs = set(current_scope_slugs)
    if global_slugs:
        all_slugs.update(f"global:{s}" for s in global_slugs)
    for ref in refs:
        normalized = ref.strip()
        if current_slug and normalized == current_slug:
            return {"error": f"references: self-reference to '{normalized}'"}
        found = normalized in all_slugs
        if not found and not normalized.startswith("global:"):
            if global_slugs:
                found = f"global:{normalized}" in all_slugs
        if not found:
            return {"error": f"references: unknown slug '{normalized}'"}
    return {}


def validate_set_metadata_json(data, slug, current_scope_slugs, global_slugs=None):
    """Full gate: validate all provided fields. Return {'errors': [...]}."""
    errors = []
    if "description" in data:
        if not isinstance(data["description"], str):
            errors.append("fields.description: expected string")
        else:
            result = validate_description(data["description"])
            if result.get("error"):
                errors.append(result["error"])
    if "read_when" in data:
        if not isinstance(data["read_when"], list) or not all(isinstance(p, str) for p in data["read_when"]):
            errors.append("fields.read_when: expected list of strings")
        else:
            result = validate_read_when(data["read_when"])
            if result.get("error"):
                errors.append(result["error"])
    if "references" in data:
        if not isinstance(data["references"], list) or not all(isinstance(r, str) for r in data["references"]):
            errors.append("fields.references: expected list of strings")
        else:
            result = validate_references(data["references"], current_scope_slugs, global_slugs, slug)
            if result.get("error"):
                errors.append(result["error"])
    if errors:
        return {"errors": errors}
    return {}


def get_hot_list_target(scope, cwd=None):
    """Return the file path where hot list markers live.
    Global → ~/.claude/CLAUDE.md. Project → ~/.claude/projects/<slug>/MEMORY.md.
    """
    if scope == "global":
        return os.path.expanduser("~/.claude/CLAUDE.md")
    cwd = cwd or os.getcwd()
    git_root = _find_git_root(cwd)
    if git_root:
        return get_memory_dir(scope, cwd) + "/MEMORY.md"
    return os.path.expanduser("~/.claude/CLAUDE.md")


HOT_LIST_MARKER_START = "<!-- memory-index:start -->"
HOT_LIST_MARKER_END = "<!-- memory-index:end -->"


def compute_scores(metadata):
    """Compute in_degree and out_degree for all entries, return (scores, in_degree)."""
    in_degree = {name: 0 for name in metadata}
    for name, entry in metadata.items():
        for ref in entry.get("references", []):
            clean_ref = ref.replace("global:", "", 1)
            if clean_ref in in_degree:
                in_degree[clean_ref] += 1
    scores = {}
    for name, entry in metadata.items():
        out_degree = len(entry.get("references", []))
        score = in_degree[name] * 2.0 + out_degree * 0.5
        scores[name] = score
    return scores, in_degree


def extract_headings(body):
    """Extract ## and ### headings from body text."""
    headings = []
    for line in body.splitlines():
        m = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if m:
            headings.append(m.group(1).strip().strip("# "))
    return headings


def generate_index_md(metadata):
    """Generate INDEX.md content from metadata."""
    scores, in_degree = compute_scores(metadata)

    sorted_names = sorted(metadata.keys(), key=lambda n: (-scores[n], n))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# Memory Index", f"*{now} · {len(metadata)} memories*", ""]
    for name in sorted_names:
        entry = metadata[name]
        desc = entry.get("description", "(no description)")
        rw = entry.get("read_when", [])
        rw_line = ", ".join(rw) if rw else "(none)"
        out_deg = len(entry.get("references", []))
        score = scores[name]
        lines.append(f"- [{name}]({name}.md) — {desc}")
        lines.append(f"  read-when: {rw_line}")
        lines.append(f"  refs: in {in_degree[name]}, out {out_deg} · score: {score:.1f}")
        lines.append("")
    return "\n".join(lines)


def inject_hot_list(target_path, metadata):
    """Inject top-scored links into hot list managed block. Returns True if markers found."""
    scores, _ = compute_scores(metadata)
    sorted_entries = sorted(metadata.items(), key=lambda kv: (-scores[kv[0]], kv[0]))
    hot_lines = []
    budget = 2000
    used = 0
    for name, entry in sorted_entries:
        desc = entry.get("description", "")
        line = f"- [{name}]({name}.md) — {desc}"
        if used + len(line) > budget:
            break
        hot_lines.append(line)
        used += len(line) + 1

    if not os.path.exists(target_path):
        return False

    with open(target_path, "r") as f:
        content = f.read()

    if HOT_LIST_MARKER_START not in content or HOT_LIST_MARKER_END not in content:
        return False

    start_idx = content.index(HOT_LIST_MARKER_START)
    end_idx = content.index(HOT_LIST_MARKER_END)
    before = content[:start_idx + len(HOT_LIST_MARKER_START)]
    after = content[end_idx:]
    new_content = before + "\n" + "\n".join(hot_lines) + "\n" + after

    dirname = os.path.dirname(target_path)
    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix=".hotlist-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_content)
    except Exception:
        os.unlink(tmp_path)
        raise
    os.rename(tmp_path, target_path)
    return True


def ensure_markers(target_path):
    """Append markers to file if missing. Returns True if markers were added."""
    if not os.path.exists(target_path):
        return False
    with open(target_path, "r") as f:
        content = f.read()
    if HOT_LIST_MARKER_START in content and HOT_LIST_MARKER_END in content:
        return False
    with open(target_path, "a") as f:
        f.write(f"\n{HOT_LIST_MARKER_START}\n{HOT_LIST_MARKER_END}\n")
    return True
