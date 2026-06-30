import json
import os
import subprocess
import sys
import tempfile
import unittest

MEMORY_SYNC = os.path.join(os.path.dirname(__file__), "..", "memory-sync.py")


class TestMemorySyncCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem_dir = os.path.join(self.tmp.name, "memory")
        os.makedirs(self.mem_dir)
        self.jsonl_path = os.path.join(self.mem_dir, "metadata.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args, stdin_input=None):
        env = os.environ.copy()
        env["_MEMORY_SYNC_TEST_DIR"] = self.mem_dir
        proc = subprocess.run(
            [sys.executable, MEMORY_SYNC] + list(args),
            capture_output=True, text=True, env=env,
            input=stdin_input
        )
        return proc

    def _make_md(self, slug, body="# Test\n\nContent."):
        path = os.path.join(self.mem_dir, f"{slug}.md")
        with open(path, "w") as f:
            f.write(body)

    def _setup_metadata(self, entries):
        with open(self.jsonl_path, "w") as f:
            for entry in entries:
                json.dump(entry, f)
                f.write("\n")

    def test_sync_creates_stub_for_new_md(self):
        self._make_md("new-topic")
        result = self._run("sync", "--dry-run")
        self.assertEqual(result.returncode, 0)
        self.assertIn("new-topic", result.stdout)

    def test_hint_for_nonexistent_slug(self):
        result = self._run("hint", "nonexistent")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)

    def test_hint_shows_headings(self):
        self._make_md("my-topic", "## Design\n\n### Schema\n\nText.")
        self._setup_metadata([
            {"name": "my-topic", "description": "", "read_when": [], "references": []}
        ])
        result = self._run("hint", "my-topic")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Design", result.stdout)
        self.assertIn("Schema", result.stdout)

    def test_set_metadata_valid_json(self):
        self._make_md("my-topic")
        self._setup_metadata([
            {"name": "my-topic", "description": "", "read_when": [], "references": []},
            {"name": "other", "description": "Other memory reference target", "read_when": ["test"], "references": []}
        ])
        json_input = json.dumps({
            "description": "A detailed description of this memory topic.",
            "read_when": ["debugging memory", "testing sync"],
            "references": ["other"]
        })
        result = self._run("set-metadata", "my-topic", stdin_input=json_input)
        self.assertEqual(result.returncode, 0)

    def test_set_metadata_rejects_short_description(self):
        self._make_md("my-topic")
        self._setup_metadata([
            {"name": "my-topic", "description": "", "read_when": [], "references": []}
        ])
        json_input = json.dumps({"description": "short"})
        result = self._run("set-metadata", "my-topic", stdin_input=json_input)
        self.assertEqual(result.returncode, 2)

    def test_set_metadata_rejects_unknown_slug(self):
        json_input = json.dumps({"description": "A detailed description of this memory topic."})
        result = self._run("set-metadata", "no-entry", stdin_input=json_input)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no metadata entry", result.stderr)

    def test_delete_removes_slug(self):
        self._make_md("old-topic")
        self._setup_metadata([
            {"name": "old-topic", "description": "Old", "read_when": ["x"], "references": []}
        ])
        result = self._run("delete", "old-topic")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.mem_dir, "old-topic.md")))

    def test_delete_nonexistent(self):
        result = self._run("delete", "nonexistent")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)

    def test_audit(self):
        self._setup_metadata([
            {"name": "orphan", "description": "Orphan node", "read_when": ["x"], "references": []},
            {"name": "cited", "description": "Cited by others", "read_when": ["x"], "references": ["orphan"]},
        ])
        result = self._run("audit")
        self.assertEqual(result.returncode, 0)
        self.assertIn("orphan", result.stdout)

class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem_dir = os.path.join(self.tmp.name, "memory")
        os.makedirs(self.mem_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args, stdin_input=None):
        env = os.environ.copy()
        env["_MEMORY_SYNC_TEST_DIR"] = self.mem_dir
        return subprocess.run(
            [sys.executable, MEMORY_SYNC] + list(args),
            capture_output=True, text=True, env=env,
            input=stdin_input
        )

    def _make_md(self, slug, body):
        with open(os.path.join(self.mem_dir, f"{slug}.md"), "w") as f:
            f.write(body)

    def test_full_write_workflow(self):
        self._make_md("my-topic", "## Design\n\n### Testing\n\nContent about memory testing.")
        r = self._run("sync")
        self.assertEqual(r.returncode, 0)
        self.assertIn("1 new memories awaiting metadata", r.stdout)
        r = self._run("hint", "my-topic")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Design", r.stdout)
        self.assertIn("Testing", r.stdout)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import common as cm
        self._make_md("other-ref", "# Other\n\nReference target.")
        r = self._run("sync")
        r = self._run("set-metadata", "other-ref", stdin_input=json.dumps({
            "description": "Another memory for reference testing purposes.",
            "read_when": ["testing references"],
            "references": []
        }))
        r = self._run("set-metadata", "my-topic", stdin_input=json.dumps({
            "description": "A detailed memory about testing the sync engine.",
            "read_when": ["debugging sync engine", "memory testing"],
            "references": ["other-ref"]
        }))
        self.assertEqual(r.returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.mem_dir, "INDEX.md")))
        with open(os.path.join(self.mem_dir, "INDEX.md")) as f:
            idx = f.read()
        self.assertIn("my-topic", idx)
        self.assertIn("memory testing", idx)
        jsonl_path = os.path.join(self.mem_dir, "metadata.jsonl")
        metadata = cm.read_metadata(jsonl_path)
        self.assertIn("other-ref", metadata["my-topic"]["references"])

    def test_delete_cleans_dangling_refs(self):
        self._make_md("a", "# A")
        self._make_md("b", "# B")
        self._run("sync")
        self._run("set-metadata", "a", stdin_input=json.dumps({
            "description": "Memory A with description about a topic.",
            "read_when": ["topic a"],
            "references": ["b"]
        }))
        self._run("set-metadata", "b", stdin_input=json.dumps({
            "description": "Memory B with description about another thing.",
            "read_when": ["topic b"],
            "references": []
        }))
        self._run("delete", "b")
        jsonl_path = os.path.join(self.mem_dir, "metadata.jsonl")
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import common as cm2
        metadata = cm2.read_metadata(jsonl_path)
        self.assertNotIn("b", metadata)
        self.assertEqual(metadata["a"]["references"], [])
