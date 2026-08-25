# -*- coding: utf-8 -*-
"""Native auto-memory ingestion tests.

Native auto-memory files (~/.claude/projects/<native-slug>/memory/*.md) are
ingested one-way into the managed store (~/.cc-switch/memory) when the
PostToolUse hook fires on them (real-time), or via `import-native` (backfill
for auto-dream writes that never fire hooks).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEMORY_SYNC = os.path.join(SKILL_ROOT, "scripts", "memory-sync.py")
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))
import common  # noqa: E402

NATIVE_FRONTMATTER = (
    "---\n"
    "name: {slug}\n"
    "description: {description}\n"
    "metadata:\n"
    "  type: project\n"
    "---\n"
    "# Body Heading\n\nBody content.\n"
)


def _native_slug(path):
    return re.sub(r"[^a-zA-Z0-9]", "-", os.path.realpath(path))


class NativeImportBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        self.project = os.path.join(self.tmp.name, "proj")
        os.makedirs(os.path.join(self.project, ".git"))

    def tearDown(self):
        self.tmp.cleanup()

    def _env(self):
        env = os.environ.copy()
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        env.pop("_MEMORY_SYNC_TEST_DIR", None)
        env.pop("CODEX_HOME", None)
        return env

    def _native_dir(self, project=None):
        return os.path.join(self.home, ".claude", "projects",
                            _native_slug(project or self.project), "memory")

    def _write_native(self, slug, description, body=None, project=None, fname=None):
        native_dir = self._native_dir(project)
        os.makedirs(native_dir, exist_ok=True)
        path = os.path.join(native_dir, fname or f"{slug}.md")
        content = body if body is not None else NATIVE_FRONTMATTER.format(
            slug=slug, description=description)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _run_hook(self, file_path, cwd=None):
        payload = {"hook_event_name": "PostToolUse",
                   "tool_input": {"file_path": file_path},
                   "cwd": cwd or self.project}
        return subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync-and-hint"],
            capture_output=True, text=True, encoding="utf-8",
            env=self._env(), input=json.dumps(payload), cwd=cwd or self.project,
        )

    def _read_md(self, rel):
        with open(os.path.join(self.home, rel), encoding="utf-8") as f:
            return f.read()

    def _read_metadata(self, rel):
        with open(os.path.join(self.home, rel, "metadata.jsonl"), encoding="utf-8") as f:
            return {json.loads(l)["name"]: json.loads(l) for l in f if l.strip()}


class TestHookIngestion(NativeImportBase):
    def test_hook_imports_native_file_to_project(self):
        native_path = self._write_native("alpha-topic", "Alpha topic memory about project internals")
        r = self._run_hook(native_path)
        self.assertEqual(r.returncode, 0, r.stderr)
        slug = common.project_slug(self.project)
        md = self._read_md(os.path.join(".cc-switch", "memory", "projects", slug, "alpha-topic.md"))
        self.assertIn("# Body Heading", md)
        self.assertNotIn("---", md[:5])  # frontmatter stripped
        meta = self._read_metadata(os.path.join(".cc-switch", "memory", "projects", slug))
        entry = meta["alpha-topic"]
        self.assertEqual(entry["source"], "native")
        self.assertEqual(entry["imported_description"], "Alpha topic memory about project internals")
        self.assertTrue(entry["read_when"])
        # INDEX rebuilt
        index = self._read_md(os.path.join(".cc-switch", "memory", "projects", slug, "INDEX.md"))
        self.assertIn("alpha-topic", index)
        # hook JSON hint emitted
        self.assertIn("摄取", r.stdout)

    def test_hook_imports_to_global_without_git_root(self):
        no_git = os.path.join(self.tmp.name, "nogit")
        os.makedirs(no_git)
        native_path = self._write_native("beta-topic", "Beta topic memory without a git root project", project=no_git)
        r = self._run_hook(native_path, cwd=no_git)
        self.assertEqual(r.returncode, 0, r.stderr)
        md = self._read_md(os.path.join(".cc-switch", "memory", "global", "beta-topic.md"))
        self.assertIn("# Body Heading", md)
        meta = self._read_metadata(os.path.join(".cc-switch", "memory", "global"))
        self.assertIn("beta-topic", meta)

    def test_native_update_refreshes_when_native_owned(self):
        native_path = self._write_native("gamma-topic", "Gamma topic memory about project internals")
        self.assertEqual(self._run_hook(native_path).returncode, 0)
        # native writes a new version
        self._write_native("gamma-topic", "Gamma topic memory with updated internals",
                           body="---\nname: gamma-topic\ndescription: Gamma topic memory with updated internals\n---\n# Gamma\n\nNew body.\n")
        r = self._run_hook(native_path)
        self.assertEqual(r.returncode, 0, r.stderr)
        slug = common.project_slug(self.project)
        md = self._read_md(os.path.join(".cc-switch", "memory", "projects", slug, "gamma-topic.md"))
        self.assertIn("New body.", md)
        meta = self._read_metadata(os.path.join(".cc-switch", "memory", "projects", slug))
        self.assertEqual(meta["gamma-topic"]["description"], "Gamma topic memory with updated internals")

    def test_skips_manually_managed_memory(self):
        slug = common.project_slug(self.project)
        mem_dir = os.path.join(self.home, ".cc-switch", "memory", "projects", slug)
        os.makedirs(mem_dir)
        with open(os.path.join(mem_dir, "delta-topic.md"), "w", encoding="utf-8") as f:
            f.write("# Delta\n\nUser-curated body.")
        with open(os.path.join(mem_dir, "metadata.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"name": "delta-topic",
                                "description": "User-curated delta topic memory with enough words",
                                "read_when": ["delta topic"], "references": []}, ensure_ascii=False) + "\n")
        native_path = self._write_native("delta-topic", "Native delta topic memory content")
        r = self._run_hook(native_path)
        self.assertEqual(r.returncode, 0, r.stderr)
        md = self._read_md(os.path.join(".cc-switch", "memory", "projects", slug, "delta-topic.md"))
        self.assertIn("User-curated body.", md)  # untouched
        self.assertIn("already managed manually", r.stdout)

    def test_skips_user_curated_after_import(self):
        slug = common.project_slug(self.project)
        native_path = self._write_native("epsilon-topic", "Epsilon topic memory about project internals")
        self.assertEqual(self._run_hook(native_path).returncode, 0)
        mem_dir = os.path.join(self.home, ".cc-switch", "memory", "projects", slug)
        meta_path = os.path.join(mem_dir, "metadata.jsonl")
        entries = {json.loads(l)["name"]: json.loads(l) for l in open(meta_path, encoding="utf-8") if l.strip()}
        entries["epsilon-topic"]["description"] = "User curated description for epsilon topic memory"
        with open(meta_path, "w", encoding="utf-8") as f:
            for e in entries.values():
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        # native writes again with different content
        self._write_native("epsilon-topic", "Native epsilon topic memory rewritten",
                           body="---\nname: epsilon-topic\ndescription: Native epsilon topic memory rewritten\n---\n# Epsilon\n\nNative new body.\n")
        r = self._run_hook(native_path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("curated by user", r.stdout)
        md = self._read_md(os.path.join(".cc-switch", "memory", "projects", slug, "epsilon-topic.md"))
        self.assertNotIn("Native new body.", md)  # not overwritten

    def test_ignores_infra_and_invalid_files(self):
        native_dir = self._native_dir()
        os.makedirs(native_dir)
        with open(os.path.join(native_dir, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# Index\n")
        with open(os.path.join(native_dir, "Bad Slug.md"), "w", encoding="utf-8") as f:
            f.write("# Bad\n")
        with open(os.path.join(native_dir, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("plain\n")
        r = self._run_hook(os.path.join(native_dir, "MEMORY.md"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")  # nothing imported, no hint
        slug = common.project_slug(self.project)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cc-switch", "memory", "projects", slug, "MEMORY.md")))


class TestImportNativeCommand(NativeImportBase):
    def test_backfills_current_project_native_dir(self):
        self._write_native("zeta-topic", "Zeta topic memory about project internals")
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "import-native"],
            capture_output=True, text=True, encoding="utf-8",
            env=self._env(), cwd=self.project,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Imported 1", r.stdout)
        slug = common.project_slug(self.project)
        self.assertTrue(os.path.exists(os.path.join(
            self.home, ".cc-switch", "memory", "projects", slug, "zeta-topic.md")))

    def test_no_native_dir_reports_quietly(self):
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "import-native"],
            capture_output=True, text=True, encoding="utf-8",
            env=self._env(), cwd=self.project,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No native auto-memory directory", r.stderr)
