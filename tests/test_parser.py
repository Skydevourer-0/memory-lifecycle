import importlib.util
import sys
from pathlib import Path

_scripts_dir = str(Path(__file__).parent.parent / "scripts")
_spec = importlib.util.spec_from_file_location("memory_sync", Path(_scripts_dir) / "memory-sync.py")
_memory_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_memory_sync)
parse_frontmatter = _memory_sync.parse_frontmatter

def test_minimal_frontmatter():
    text = """---
name: my-memory
description: A test memory
---
Body content."""
    meta, body = parse_frontmatter(text)
    assert meta["name"] == "my-memory"
    assert meta["description"] == "A test memory"
    assert meta["references"] == []
    assert meta["read_when"] == []
    assert body == "Body content."

def test_full_frontmatter():
    text = """---
name: pricing-fix
description: DeepSeek pricing for tt
references:
  - superpowers-hook
  - powershell-alias
read-when:
  - debugging tt cost
  - statusline wrong cost
---
Fix details."""
    meta, body = parse_frontmatter(text)
    assert meta["name"] == "pricing-fix"
    assert meta["description"] == "DeepSeek pricing for tt"
    assert meta["references"] == ["superpowers-hook", "powershell-alias"]
    assert meta["read_when"] == ["debugging tt cost", "statusline wrong cost"]

def test_empty_references():
    text = """---
name: test
description: test
references: []
read-when: []
---
Body."""
    meta, _ = parse_frontmatter(text)
    assert meta["references"] == []
    assert meta["read_when"] == []

def test_no_frontmatter():
    text = "Just body content."
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == "Just body content."
