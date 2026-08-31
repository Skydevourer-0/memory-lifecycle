import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone


SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

HOT_LIST_MARKER_START = "<!-- memory-index:start -->"
HOT_LIST_MARKER_END = "<!-- memory-index:end -->"

# 统一字符预算:HOTLIST.md 与 checkpoint 注入均 <= 1200 字符
HOTLIST_BUDGET = 1200


def read_metadata(jsonl_path):
    """Read metadata.jsonl, return dict keyed by name. Empty file -> {}."""
    if not os.path.exists(jsonl_path):
        return {}
    result = {}
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            result[entry["name"]] = entry
    return result


def write_metadata(jsonl_path, name, entry):
    """Atomic write: read all, upsert entry by name, write to temp + replace."""
    if entry is None:
        raise TypeError("entry must not be None")
    all_entries = read_metadata(jsonl_path)
    all_entries[name] = entry
    write_all_metadata(jsonl_path, all_entries)


def remove_metadata(jsonl_path, name):
    """Remove entry by name. Atomic write."""
    all_entries = read_metadata(jsonl_path)
    if name not in all_entries:
        return
    del all_entries[name]
    write_all_metadata(jsonl_path, all_entries)


def atomic_write_text(path, content):
    """Write text atomically (temp file + os.replace).

    os.replace is required on Windows: os.rename raises FileExistsError when
    the destination already exists."""
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        os.unlink(tmp_path)
        raise
    os.replace(tmp_path, path)


def write_all_metadata(jsonl_path, entries):
    """Atomic write of all metadata entries to jsonl."""
    lines = []
    for record in entries.values():
        lines.append(json.dumps(record, ensure_ascii=False))
    atomic_write_text(jsonl_path, "\n".join(lines) + ("\n" if lines else ""))


def validate_slug(slug):
    """Validate that slug is lowercase alphanumeric with hyphens."""
    return bool(SLUG_RE.fullmatch(slug))


def codex_home():
    """Resolve Codex home: $CODEX_HOME if set, else ~/.codex."""
    env = os.environ.get("CODEX_HOME")
    if env:
        return os.path.expanduser(env)
    return os.path.expanduser("~/.codex")


def _find_git_root(path):
    """Walk upward from path. Return git root path, or None.

    A .git entry may be a directory (regular repo) or a file (git worktree /
    submodule gitdir pointer)."""
    path = os.path.abspath(path)
    while True:
        git_entry = os.path.join(path, ".git")
        if os.path.isdir(git_entry) or os.path.isfile(git_entry):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def _normalized(path):
    """Normalize for path-boundary comparisons (absolute + case-folded on Windows)."""
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def detect_scope(cwd=None):
    """Walk from cwd upward. .git found -> 'project', else 'global'.

    Paths under the agents' own config/skill homes (~/.claude/,
    ~/.cc-switch/skills, ~/.zcode/skills, ~/.agents/skills,
    ~/.config/opencode/skills) are always global (skills, configs, etc.).
    Boundary checks use the OS separator and normcase (expanduser may yield
    mixed / and \\ separators on Windows, and the filesystem is
    case-insensitive) so ~/.cc-switch-foo is not misclassified and real
    skills dirs are not missed."""
    cwd = os.path.expanduser(cwd or os.getcwd())
    normalized = _normalized(cwd)
    for special in (
        "~/.claude",
        "~/.cc-switch/skills",
        "~/.zcode/skills",
        "~/.agents/skills",
        "~/.config/opencode/skills",
    ):
        base = _normalized(os.path.expanduser(special))
        if normalized == base or normalized.startswith(base + os.sep):
            return "global"
    return "project" if _find_git_root(cwd) else "global"


def detect_scope_from_file(filepath):
    """Infer scope from file path pattern.

    New prefix (~/.cc-switch/memory/...) is authoritative. Old ~/.claude
    prefixes are recognized only for migrate decisions. Returns None for
    paths outside both layouts."""
    mem_dir = resolve_mem_dir_from_file(filepath)
    if mem_dir is not None:
        return "global" if _is_global_mem_dir(mem_dir) else "project"
    expanded = _normalized(filepath)
    old_global = _normalized(os.path.expanduser("~/.claude/global/memory"))
    if expanded.startswith(old_global + os.sep):
        return "global"
    old_projects = _normalized(os.path.expanduser("~/.claude/projects"))
    if expanded.startswith(old_projects + os.sep):
        rel = expanded[len(old_projects + os.sep):]
        parts = rel.split(os.sep)
        if len(parts) >= 2 and parts[1] == "memory":
            return "project"
    return None


def _is_global_mem_dir(mem_dir):
    """True if mem_dir is the new global memory directory."""
    return _normalized(mem_dir) == _normalized(os.path.expanduser("~/.cc-switch/memory/global"))


def get_memory_dir(scope, cwd=None):
    """Return memory directory path for the given scope.

    global -> ~/.cc-switch/memory/global
    project -> ~/.cc-switch/memory/projects/<project-slug>"""
    if scope == "global":
        return os.path.expanduser("~/.cc-switch/memory/global")
    cwd = cwd or os.getcwd()
    git_root = _find_git_root(cwd)
    if git_root:
        return os.path.join(os.path.expanduser("~/.cc-switch/memory/projects"), project_slug(git_root))
    return os.path.expanduser("~/.cc-switch/memory/global")


def project_slug(path):
    """Unified project slug algorithm (shared spec with workflow-checkpoint):

    os.path.realpath() -> lower() -> non [a-z0-9] replaced with '-' ->
    consecutive '-' folded. Leading/trailing '-' stripped so the result
    always satisfies SLUG_RE. e.g. C:\\Users\\a\\proj -> c-users-a-proj."""
    real = os.path.realpath(path)
    lowered = real.lower()
    replaced = re.sub(r"[^a-z0-9]", "-", lowered)
    folded = re.sub(r"-+", "-", replaced)
    return folded.strip("-")


def resolve_mem_dir_from_file(filepath):
    """Single point for resolving the memory directory from a file path.

    New prefix only: ~/.cc-switch/memory/global -> global dir;
    ~/.cc-switch/memory/projects/<slug>/... -> project dir.
    Old ~/.claude prefixes and unknown paths -> None (guard is new-prefix only)."""
    expanded = _normalized(filepath)
    global_mem = _normalized(os.path.expanduser("~/.cc-switch/memory/global"))
    if expanded.startswith(global_mem + os.sep):
        return os.path.expanduser("~/.cc-switch/memory/global")
    projects_root = _normalized(os.path.expanduser("~/.cc-switch/memory/projects"))
    if expanded.startswith(projects_root + os.sep):
        rel = expanded[len(projects_root + os.sep):]
        parts = rel.split(os.sep)
        if parts and parts[0]:
            return os.path.join(os.path.expanduser("~/.cc-switch/memory/projects"), parts[0])
    return None


DESCRIPTION_BLACKLIST = [
    "tbd", "todo", "placeholder", "待补充", "wip", "draft",
    "to be written", "coming soon",
    "记住", "记一下", "重要", "备忘", "笔记", "总结", "概述", "相关信息",
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
        r"^这是关于.+的记忆$",
        r"^描述了.+$",
        r"^一些.+的笔记$",
    ]
    for pat in boilerplate_patterns:
        if re.fullmatch(pat, lowered):
            return {"error": "description: too generic, be specific"}
    return {}


def duplicate_read_when(phrases):
    """Return list of duplicate phrase texts (case-insensitive). Callers warn only."""
    seen = set()
    dups = []
    for phrase in phrases:
        key = phrase.strip().lower()
        if key in seen:
            dups.append(phrase.strip())
        seen.add(key)
    return dups


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
    if "importance" in data:
        imp = data["importance"]
        if not isinstance(imp, int) or isinstance(imp, bool) or not (1 <= imp <= 5):
            errors.append("fields.importance: expected int 1-5")
    if "entities" in data:
        if not isinstance(data["entities"], list) or not all(isinstance(e, str) for e in data["entities"]) or len(data["entities"]) > 50:
            errors.append("fields.entities: expected list of strings (max 50)")
    if "tags" in data:
        if not isinstance(data["tags"], list) or not all(isinstance(t, str) for t in data["tags"]) or len(data["tags"]) > 20:
            errors.append("fields.tags: expected list of strings (max 20)")
    if errors:
        return {"errors": errors}
    return {}


def zcode_installed():
    """True when a ZCode home exists (~/.zcode). Gates the ZCode hot-list
    target so a Claude/Codex-only machine never grows a stray ~/.zcode/AGENTS.md."""
    return os.path.isdir(os.path.expanduser("~/.zcode"))


def get_hot_list_target(scope, cwd=None):
    """Return hot list target(s) for the given scope.

    global -> list [~/.claude/CLAUDE.md, <codex_home>/AGENTS.md]
             (+ ~/.zcode/AGENTS.md when a ZCode home exists — three-end injection)
    project -> str <mem_dir>/HOTLIST.md (SessionStart injection, no markers)"""
    if scope == "global":
        targets = [
            os.path.expanduser("~/.claude/CLAUDE.md"),
            os.path.join(codex_home(), "AGENTS.md"),
        ]
        if zcode_installed():
            targets.append(os.path.expanduser("~/.zcode/AGENTS.md"))
        return targets
    cwd = cwd or os.getcwd()
    return os.path.join(get_memory_dir(scope, cwd), "HOTLIST.md")


# ── Effective Importance (mnemon-derived deterministic scoring) ──────────────
EI_BASE_WEIGHT = {5: 1.0, 4: 0.8, 3: 0.5, 2: 0.3, 1: 0.15}
EI_HALF_LIFE_DAYS = 30.0
DEFAULT_IMPORTANCE = 3


def parse_ts(ts_str):
    """Parse an ISO-8601 timestamp to a tz-aware datetime, or return None."""
    if not ts_str:
        return None
    s = str(ts_str).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _ref_target(ref):
    """Normalize a reference (slug string or typed dict) to its target slug."""
    if isinstance(ref, str):
        return ref.replace("global:", "", 1)
    if isinstance(ref, dict):
        return str(ref.get("to", "")).replace("global:", "", 1)
    return ""


def compute_ei(metadata, now=None):
    """Effective Importance (mnemon): base(importance) x access x decay x edge.

    References may be plain slugs (legacy) or typed dicts (forward-compat).
    Missing fields default: importance=3, access_count=0, last_accessed=created.
    Returns (scores, in_degree) — same contract as the former degree scorer.
    """
    now = now or datetime.now(timezone.utc)
    in_degree = {name: 0 for name in metadata}
    for name, entry in metadata.items():
        for ref in entry.get("references", []):
            clean = _ref_target(ref)
            if clean in in_degree:
                in_degree[clean] += 1
    scores = {}
    for name, entry in metadata.items():
        imp = int(entry.get("importance", DEFAULT_IMPORTANCE) or DEFAULT_IMPORTANCE)
        base = EI_BASE_WEIGHT.get(imp, 0.5)
        access = max(1.0, math.log(1 + int(entry.get("access_count", 0) or 0)))
        la = parse_ts(entry.get("last_accessed") or entry.get("created") or entry.get("updated"))
        days = 0.0
        if la is not None:
            days = max(0.0, (now - la).total_seconds() / 86400.0)
        decay = 0.5 ** (days / EI_HALF_LIFE_DAYS)
        edge_factor = 1.0 + 0.1 * min(in_degree[name] + len(entry.get("references", [])), 5)
        scores[name] = base * access * decay * edge_factor
    return scores, in_degree


def default_entry(name):
    """Default metadata entry with mnemon-style fields (importance/entities/tags/access)."""
    return {
        "name": name,
        "description": "",
        "read_when": [],
        "importance": DEFAULT_IMPORTANCE,
        "entities": [],
        "tags": [],
        "access_count": 0,
        "last_accessed": None,
        "references": [],
    }


def track_access(entry, now=None):
    """Increment access_count and set last_accessed (mnemon access/decay signals)."""
    now = now or datetime.now(timezone.utc)
    entry["access_count"] = int(entry.get("access_count", 0) or 0) + 1
    entry["last_accessed"] = now.isoformat()
    return entry


# ── Entity extraction (mnemon entity graph: regex + tech dictionary) ─────────
ENTITY_STOPWORDS = {
    "a", "an", "the", "and", "or", "not", "is", "are", "was", "were", "be",
    "been", "for", "with", "from", "into", "onto", "of", "in", "on", "at",
    "to", "by", "it", "its", "has", "have", "had", "do", "does", "did",
    "will", "would", "can", "could", "should", "shall", "if", "else", "then",
    "when", "where", "which", "what", "how", "why", "one", "all", "any",
    "some", "this", "that", "these", "those", "each", "every", "both", "none",
    "nothing", "zero", "must", "never", "only", "way", "no", "yes", "new",
    "old", "two", "names", "tool", "located", "rewrites", "unchanged",
    "basenames", "misses", "eof", "via", "per", "without", "another",
    "such", "more", "most", "other", "same",
}

ENTITY_PATTERNS = [
    re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b"),
    re.compile(r"\b[A-Z]{2,}\b"),
    re.compile(r"https?://\S+"),
    re.compile(r"@[\w-]+"),
    re.compile(r"《[^》\n]+》"),
]
TECH_TERMS = {
    "go", "python", "nodejs", "typescript", "javascript", "react", "vue",
    "sqlite", "postgres", "postgresql", "mysql", "redis", "mongodb", "docker",
    "kubernetes", "k8s", "git", "github", "claude", "codex", "dsh", "mnemon",
    "llm", "rag", "api", "http", "grpc", "json", "yaml", "sql", "openai",
    "anthropic", "ollama", "embedding", "vector", "hooks", "markdown", "jsonl",
    "windows", "linux", "macos", "zcode", "opencode",
}


def extract_entities(text):
    """Hybrid entity extraction: regex patterns + tech dictionary (deterministic)."""
    if not text:
        return []
    found = []
    seen = set()
    for pat in ENTITY_PATTERNS:
        for m in pat.findall(text):
            tok = m.group(0) if hasattr(m, "group") else m
            key = tok.lower()
            if key in ENTITY_STOPWORDS:
                continue
            if key and key not in seen:
                seen.add(key)
                found.append(tok)
    lowered = text.lower()
    for term in TECH_TERMS:
        if term in lowered and term not in seen:
            if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", lowered):
                seen.add(term)
                found.append(term)
    return found


def auto_link_entity_edges(metadata, name, entities, max_per_entity=5):
    """Create entity edges linking `name` to memories sharing any entity.

    Idempotent: drops existing type='entity' edges from `name` first, then
    re-adds from the current entity set. Returns the updated references list
    (manual string refs are preserved; typed dicts are the auto entity edges)."""
    refs = metadata[name].get("references", [])
    refs = [r for r in refs if not (isinstance(r, dict) and r.get("type") == "entity")]
    if entities:
        entity_index = {}
        for other, entry in metadata.items():
            if other == name:
                continue
            for ent in entry.get("entities", []):
                entity_index.setdefault(str(ent).lower(), []).append(other)
        seen = set()
        for ent in entities:
            for other in entity_index.get(str(ent).lower(), [])[:max_per_entity]:
                if other in seen:
                    continue
                seen.add(other)
                refs.append({"to": other, "type": "entity", "weight": 1.0, "entity": ent})
    metadata[name]["references"] = refs
    return refs


def compute_scores(metadata, now=None):
    """Compute Effective Importance scores. Returns (scores, in_degree).

    Backward-compatible name/contract for the former degree-based scorer."""
    return compute_ei(metadata, now=now)


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


def hot_list_lines(metadata, budget=HOTLIST_BUDGET):
    """Build hot list entry lines (top-scored), capped by the character budget."""
    scores, _ = compute_scores(metadata)
    sorted_entries = sorted(metadata.items(), key=lambda kv: (-scores[kv[0]], kv[0]))
    lines = []
    used = 0
    for name, entry in sorted_entries:
        desc = entry.get("description", "")
        line = f"- [{name}]({name}.md) — {desc}"
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line) + 1
    return lines


def inject_hot_list(target_path, metadata, standalone=False):
    """Inject top-scored links into the hot list target.

    standalone=True: full-file write (HOTLIST.md, no marker block required).
    Otherwise: replace the managed marker block; returns False when the
    file or markers are missing. All writes are atomic (os.replace)."""
    lines = hot_list_lines(metadata)

    if standalone:
        content = "\n".join(lines)
        if lines:
            content += "\n"
        atomic_write_text(target_path, content)
        return True

    if not os.path.exists(target_path):
        return False

    with open(target_path, "r", encoding="utf-8") as f:
        content = f.read()

    if HOT_LIST_MARKER_START not in content or HOT_LIST_MARKER_END not in content:
        return False

    start_idx = content.index(HOT_LIST_MARKER_START)
    end_idx = content.index(HOT_LIST_MARKER_END)
    before = content[:start_idx + len(HOT_LIST_MARKER_START)]
    after = content[end_idx:]
    new_content = before + "\n" + "\n".join(lines) + "\n" + after
    atomic_write_text(target_path, new_content)
    return True


def ensure_markers(target_path):
    """Append markers to file if missing (atomic). Returns True if markers were added."""
    if not os.path.exists(target_path):
        return False
    with open(target_path, "r", encoding="utf-8") as f:
        content = f.read()
    if HOT_LIST_MARKER_START in content and HOT_LIST_MARKER_END in content:
        return False
    new_content = content.rstrip("\n") + f"\n{HOT_LIST_MARKER_START}\n{HOT_LIST_MARKER_END}\n"
    atomic_write_text(target_path, new_content)
    return True