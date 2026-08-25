# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

MEMORY_SYNC = os.path.join(os.path.dirname(__file__), "..", "memory-sync.py")


def _home_env(home, extra=None):
    """Env with HOME/USERPROFILE redirected to a temp home."""
    env = os.environ.copy()
    env["HOME"] = home
    env["USERPROFILE"] = home
    env.pop("CODEX_HOME", None)
    env.pop("_MEMORY_SYNC_TEST_DIR", None)
    if extra:
        env.update(extra)
    return env


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


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
            capture_output=True, text=True, encoding="utf-8", env=env,
            input=stdin_input
        )
        return proc

    def _make_md(self, slug, body="# Test\n\nContent."):
        _write(os.path.join(self.mem_dir, f"{slug}.md"), body)

    def _setup_metadata(self, entries):
        with open(self.jsonl_path, "w", encoding="utf-8") as f:
            for entry in entries:
                json.dump(entry, f, ensure_ascii=False)
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

    def test_set_metadata_duplicate_read_when_warns(self):
        self._make_md("my-topic")
        self._setup_metadata([
            {"name": "my-topic", "description": "", "read_when": [], "references": []}
        ])
        json_input = json.dumps({
            "description": "A detailed description of this memory topic.",
            "read_when": ["same phrase", "same phrase"],
        })
        result = self._run("set-metadata", "my-topic", stdin_input=json_input)
        self.assertEqual(result.returncode, 0)
        self.assertIn("duplicate read_when", result.stderr)

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
            capture_output=True, text=True, encoding="utf-8", env=env,
            input=stdin_input
        )

    def _make_md(self, slug, body):
        _write(os.path.join(self.mem_dir, f"{slug}.md"), body)

    def test_full_write_workflow(self):
        self._make_md("my-topic", "## Design\n\n### Testing\n\nContent about memory testing.")
        r = self._run("sync")
        self.assertEqual(r.returncode, 0)
        self.assertIn("1 new memories awaiting metadata", r.stdout)
        r = self._run("hint", "my-topic")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Design", r.stdout)
        self.assertIn("Testing", r.stdout)
        self._make_md("other-ref", "# Other\n\nReference target.")
        r = self._run("sync")
        self.assertEqual(r.returncode, 0)
        r = self._run("set-metadata", "other-ref", stdin_input=json.dumps({
            "description": "Another memory for reference testing purposes.",
            "read_when": ["testing references"],
            "references": []
        }))
        self.assertEqual(r.returncode, 0)
        r = self._run("set-metadata", "my-topic", stdin_input=json.dumps({
            "description": "A detailed memory about testing the sync engine.",
            "read_when": ["debugging sync engine", "memory testing"],
            "references": ["other-ref"]
        }))
        self.assertEqual(r.returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.mem_dir, "INDEX.md")))
        with open(os.path.join(self.mem_dir, "INDEX.md"), encoding="utf-8") as f:
            idx = f.read()
        self.assertIn("my-topic", idx)
        self.assertIn("memory testing", idx)
        jsonl_path = os.path.join(self.mem_dir, "metadata.jsonl")
        metadata = common.read_metadata(jsonl_path)
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
        metadata = common.read_metadata(jsonl_path)
        self.assertNotIn("b", metadata)
        self.assertEqual(metadata["a"]["references"], [])
class TestHintHookMode(unittest.TestCase):
    """PostToolUse hint hook: exit 0 + soft additionalContext, no emoji.

    New behavior (v2.2): 命中一律 exit 0 + 软提示;metadata 为空时输出
    '元数据待补' 软提示;旧行为(exit 1 + 🔴 REVIEW)已废除。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.mem_dir = os.path.join(self.home, ".cc-switch", "memory", "global")
        os.makedirs(self.mem_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _run_hook(self, file_path, command="hint"):
        payload = json.dumps({
            "hook_event_name": "PostToolUse",
            "tool_input": {"file_path": file_path},
        })
        return subprocess.run(
            [sys.executable, MEMORY_SYNC, command],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), input=payload,
        )

    def _make_md(self, slug, body="# Test"):
        _write(os.path.join(self.mem_dir, f"{slug}.md"), body)

    def _sync(self):
        subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.home,
        )

    def _set_metadata(self, slug, description, read_when):
        subprocess.run(
            [sys.executable, MEMORY_SYNC, "set-metadata", slug],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.home,
            input=json.dumps({"description": description, "read_when": read_when, "references": []}),
        )

    def _ctx(self, r):
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_hook_skip_infra_files(self):
        for name in ("INDEX", "MEMORY", "README", "HOTLIST"):
            self._make_md(name)
            r = self._run_hook(os.path.join(self.mem_dir, f"{name}.md"))
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")
            self.assertNotIn("hookSpecificOutput", r.stdout, f"{name} 不应触发 hint")
            os.unlink(os.path.join(self.mem_dir, f"{name}.md"))

    def test_hook_file_not_found_exits_0(self):
        r = self._run_hook(os.path.join(self.mem_dir, "ghost.md"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_hook_stale_metadata_soft_hint(self):
        self._make_md("my-topic", "## Design\n\nContent.")
        self._sync()
        r = self._run_hook(os.path.join(self.mem_dir, "my-topic.md"))
        self.assertEqual(r.returncode, 0)
        ctx = self._ctx(r)
        self.assertIn("my-topic", ctx)
        self.assertIn("元数据待补", ctx)
        self.assertIn("set-metadata", ctx)

    def test_hook_complete_metadata_soft_hint(self):
        self._make_md("done", "## Design\n\nContent.")
        self._sync()
        self._set_metadata("done", "Complete memory with full metadata for testing purposes.",
                           ["hook testing", "verification"])
        r = self._run_hook(os.path.join(self.mem_dir, "done.md"))
        self.assertEqual(r.returncode, 0)
        ctx = self._ctx(r)
        self.assertIn("done", ctx)
        self.assertIn("hook testing", ctx)

    def test_hook_no_emoji(self):
        self._make_md("my-topic")
        self._sync()
        r = self._run_hook(os.path.join(self.mem_dir, "my-topic.md"))
        for symbol in ("🔴", "REVIEW", "⏸", "✗", "✅"):
            self.assertNotIn(symbol, r.stdout)

    def test_hook_non_memory_file_ignored(self):
        r = self._run_hook(os.path.join(self.home, "notes", "other.md"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_hook_old_prefix_auto_memory_noop(self):
        # 原生 auto-memory 目录(~/.claude/projects/<slug>/memory)是明确 no-op
        old = os.path.join(self.home, ".claude", "projects", "some-slug", "memory", "auto.md")
        r = self._run_hook(old)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_manual_hint_stale_exits_0(self):
        self._make_md("stale", "## Design")
        self._sync()
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "hint", "stale"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.home,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("required", r.stdout)

    def test_manual_hint_skips_hotlist_slug(self):
        # HOTLIST 不进 hint 名单(防自注册为 slug hotlist)
        self._make_md("HOTLIST")
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "hint", "HOTLIST"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.home,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")
class TestSyncAndHint(unittest.TestCase):
    """sync-and-hint hook:双 payload 解析(Claude file_path / Codex apply_patch)、
    Delete 分支、<=3 条提示、命中 exit 0。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.mem_dir = os.path.join(self.home, ".cc-switch", "memory", "global")
        os.makedirs(self.mem_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, payload, cwd=None):
        return subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync-and-hint"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), input=json.dumps(payload), cwd=cwd or self.home,
        )

    def _make_md(self, slug):
        _write(os.path.join(self.mem_dir, f"{slug}.md"), "# Test\n\nContent.")

    def test_claude_file_path_payload(self):
        self._make_md("alpha")
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": os.path.join(self.mem_dir, "alpha.md")},
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("memory-sync hook error", r.stderr)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("alpha", ctx)
        self.assertIn("元数据待补", ctx)
        # sync 生效:stub + INDEX
        metadata = common.read_metadata(os.path.join(self.mem_dir, "metadata.jsonl"))
        self.assertIn("alpha", metadata)
        self.assertTrue(os.path.exists(os.path.join(self.mem_dir, "INDEX.md")))

    def test_codex_apply_patch_relative_cwd(self):
        self._make_md("beta")
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "cwd": self.home,
            "tool_input": {
                "command": (
                    "*** Begin Patch\n"
                    "*** Update File: .cc-switch/memory/global/beta.md\n"
                    "*** End Patch"
                )
            },
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("beta", r.stdout)

    def test_codex_apply_patch_absolute_path(self):
        self._make_md("gamma")
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "cwd": self.home,
            "tool_input": {
                "command": f"*** Begin Patch\n*** Update File: {os.path.join(self.mem_dir, 'gamma.md')}\n*** End Patch"
            },
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("gamma", r.stdout)

    def test_guard_non_memory_file(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "cwd": self.home,
            "tool_input": {"command": "*** Begin Patch\n*** Update File: src/main.py\n*** End Patch"},
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_delete_branch(self):
        self._make_md("victim")
        # 先注册 stub
        self._run({"hook_event_name": "PostToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": os.path.join(self.mem_dir, "victim.md")}})
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "cwd": self.home,
            "tool_input": {
                "command": "*** Begin Patch\n*** Delete File: .cc-switch/memory/global/victim.md\n*** End Patch"
            },
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.mem_dir, "victim.md")))
        metadata = common.read_metadata(os.path.join(self.mem_dir, "metadata.jsonl"))
        self.assertNotIn("victim", metadata)
        self.assertEqual(r.stdout.strip(), "")  # delete 无 hint 输出

    def test_delete_infra_file_noop(self):
        self._make_md("INDEX")  # 基础设施文件
        payload = {
            "hook_event_name": "PostToolUse",
            "cwd": self.home,
            "tool_input": {"command": "*** Begin Patch\n*** Delete File: .cc-switch/memory/global/INDEX.md\n*** End Patch"},
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.mem_dir, "INDEX.md")))

    def test_delete_unknown_slug_noop(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "cwd": self.home,
            "tool_input": {"command": "*** Begin Patch\n*** Delete File: .cc-switch/memory/global/ghost.md\n*** End Patch"},
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_multifile_max_three_hints(self):
        for slug in ("a", "b", "c", "d"):
            self._make_md(slug)
        lines = [f"*** Update File: .cc-switch/memory/global/{s}.md" for s in ("a", "b", "c", "d")]
        payload = {
            "hook_event_name": "PostToolUse",
            "cwd": self.home,
            "tool_input": {"command": "*** Begin Patch\n" + "\n".join(lines) + "\n*** End Patch"},
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(ctx.count("元数据待补"), 3)

    def test_no_emoji_in_hook_output(self):
        self._make_md("alpha")
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": os.path.join(self.mem_dir, "alpha.md")},
        }
        r = self._run(payload)
        for symbol in ("🔴", "REVIEW", "⏸", "✗", "✅"):
            self.assertNotIn(symbol, r.stdout)

    def test_stdout_is_pure_json(self):
        self._make_md("alpha")
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": os.path.join(self.mem_dir, "alpha.md")},
        }
        r = self._run(payload)
        # sync 诊断输出不得污染 stdout(hook JSON 必须可解析)
        data = json.loads(r.stdout)
        self.assertIn("hookSpecificOutput", data)


class TestHookFailOpen(unittest.TestCase):
    """fail-open:坏输入 → 空 stdout + exit 0;不完整 JSON 绝不 fallback 到 cwd scope。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, ".git"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_incomplete_json_no_fallback(self):
        # 截断 JSON:hook 模式下绝不 fallback 到 cwd scope(不会在 repo 下建项目库)
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync-and-hint"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.repo,
            input='{"hook_event_name": "PostToolUse", "tool_input": {"file_path": ',
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")
        projects = os.path.join(self.home, ".cc-switch", "memory", "projects")
        self.assertFalse(os.path.exists(projects))

    def test_garbage_json_fail_open(self):
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync-and-hint"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.repo,
            input="<not json at all",
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_malformed_payload_fail_open(self):
        payload = json.dumps({
            "hook_event_name": "PostToolUse",
            "cwd": self.repo,
            "tool_input": "not-a-dict",
        })
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync-and-hint"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.repo, input=payload,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_exception_fail_open(self):
        # 含 memory 路径通过快速过滤 + cwd 非字符串 → 引擎内部异常 → stderr + 空 stdout + exit 0
        payload = json.dumps({
            "hook_event_name": "PostToolUse",
            "cwd": {"x": 1},
            "tool_input": {"command": "*** Begin Patch\n*** Update File: .cc-switch/memory/global/foo.md\n*** End Patch"},
        })
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync-and-hint"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.repo, input=payload,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("hook error", r.stderr)

    def test_noop_payload_no_stderr_noise(self):
        # 快速过滤跳过的 no-op:exit 0、空 stdout、且不产生 stderr 噪音
        payload = json.dumps({
            "hook_event_name": "PostToolUse",
            "cwd": self.repo,
            "tool_input": {"command": "*** Begin Patch\n*** Update File: src/main.py\n*** End Patch"},
        })
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync-and-hint"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.repo, input=payload,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(r.stderr.strip(), "")
class TestSessionStart(unittest.TestCase):
    """session-start hook:注入项目 HOTLIST.md;无 git root / HOTLIST 缺失 → 空输出 exit 0。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, ".git"))
        self.slug = common.project_slug(self.repo)
        self.hotlist = os.path.join(self.home, ".cc-switch", "memory", "projects", self.slug, "HOTLIST.md")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, payload, cwd=None):
        return subprocess.run(
            [sys.executable, MEMORY_SYNC, "session-start"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=cwd or self.repo,
            input=json.dumps(payload),
        )

    def test_injects_project_hotlist(self):
        os.makedirs(os.path.dirname(self.hotlist))
        _write(self.hotlist, "- [alpha](alpha.md) — Alpha desc\n- [beta](beta.md) — Beta desc\n")
        payload = {"hook_event_name": "SessionStart", "cwd": self.repo}
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("Alpha desc", out["hookSpecificOutput"]["additionalContext"])

    def test_getcwd_fallback_when_no_cwd_field(self):
        os.makedirs(os.path.dirname(self.hotlist))
        _write(self.hotlist, "- [alpha](alpha.md) — Alpha desc\n")
        r = self._run({"hook_event_name": "SessionStart"}, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Alpha desc", r.stdout)

    def test_no_git_root_empty(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        r = self._run({"hook_event_name": "SessionStart", "cwd": plain})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_hotlist_missing_empty(self):
        r = self._run({"hook_event_name": "SessionStart", "cwd": self.repo})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_global_scope_skills_no_inject(self):
        # ~/.claude 下即使有 .git 也按 global 处理 → 不注入项目热榜
        skills = os.path.join(self.home, ".claude", "skills", "demo")
        os.makedirs(os.path.join(skills, ".git"))
        r = self._run({"hook_event_name": "SessionStart", "cwd": skills})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_truncates_to_budget(self):
        os.makedirs(os.path.dirname(self.hotlist))
        _write(self.hotlist, "x" * 3000)
        r = self._run({"hook_event_name": "SessionStart", "cwd": self.repo})
        self.assertEqual(r.returncode, 0, r.stderr)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), common.HOTLIST_BUDGET)

    def test_subdirectory_cwd_walks_up_to_git_root(self):
        subdir = os.path.join(self.repo, "sub", "deep")
        os.makedirs(subdir)
        os.makedirs(os.path.dirname(self.hotlist))
        _write(self.hotlist, "- [alpha](alpha.md) — Alpha desc\n")
        r = self._run({"hook_event_name": "SessionStart", "cwd": subdir})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Alpha desc", r.stdout)


class TestProjectHotlistSync(unittest.TestCase):
    """sync 项目 scope:HOTLIST.md 生成、无 hotlist stub、预算 1200。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, ".git"))
        self.slug = common.project_slug(self.repo)
        self.mem_dir = os.path.join(self.home, ".cc-switch", "memory", "projects", self.slug)
        os.makedirs(self.mem_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _sync(self):
        return subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.repo,
        )

    def test_project_sync_generates_hotlist(self):
        for s in ("alpha", "beta"):
            _write(os.path.join(self.mem_dir, f"{s}.md"), f"# {s}\n\nContent.")
        r = self._sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        hotlist = os.path.join(self.mem_dir, "HOTLIST.md")
        self.assertTrue(os.path.exists(hotlist))
        content = open(hotlist, encoding="utf-8").read()
        self.assertIn("alpha", content)
        self.assertIn("beta", content)
        self.assertNotIn("memory-index:start", content)  # 无标记块、无头部注释
        self.assertLessEqual(len(content), common.HOTLIST_BUDGET + 1)
        # 不产生 slug hotlist stub
        metadata = common.read_metadata(os.path.join(self.mem_dir, "metadata.jsonl"))
        self.assertNotIn("hotlist", metadata)
        self.assertIn("alpha", metadata)
        # INDEX 存在且不含 hotlist
        idx = open(os.path.join(self.mem_dir, "INDEX.md"), encoding="utf-8").read()
        self.assertNotIn("hotlist", idx)

    def test_global_sync_creates_dual_skeletons(self):
        global_dir = os.path.join(self.home, ".cc-switch", "memory", "global")
        os.makedirs(global_dir)
        _write(os.path.join(global_dir, "gmem.md"), "# gmem\n\nContent.")
        r = subprocess.run(
            [sys.executable, MEMORY_SYNC, "sync"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home), cwd=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        for target in (os.path.join(self.home, ".claude", "CLAUDE.md"),
                       os.path.join(self.home, ".codex", "AGENTS.md")):
            self.assertTrue(os.path.exists(target), target)
            content = open(target, encoding="utf-8").read()
            self.assertIn("memory-index:start", content)
            self.assertIn("[gmem](gmem.md)", content)

    def test_sync_excludes_hotlist_from_stubs(self):
        # HOTLIST.md 已存在时,不会因 sync 生成 slug hotlist stub
        _write(os.path.join(self.mem_dir, "HOTLIST.md"), "- [alpha](alpha.md) — old\n")
        _write(os.path.join(self.mem_dir, "alpha.md"), "# alpha\n\nContent.")
        r = self._sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        metadata = common.read_metadata(os.path.join(self.mem_dir, "metadata.jsonl"))
        self.assertNotIn("hotlist", metadata)
        self.assertIn("alpha", metadata)


class TestMigrate(unittest.TestCase):
    """migrate:旧 global memory 复制 + metadata 合并;旧 workflows → 新目录;
    目标存在跳过;幂等;不迁 ~/.claude/projects/<slug>/memory/。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")

    def tearDown(self):
        self.tmp.cleanup()

    def _migrate(self):
        return subprocess.run(
            [sys.executable, MEMORY_SYNC, "migrate"],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home),
        )

    def test_migrate_copies_and_merges(self):
        old_mem = os.path.join(self.home, ".claude", "global", "memory")
        os.makedirs(old_mem)
        _write(os.path.join(old_mem, "legacy.md"), "# Legacy\n\nContent.")
        _write(os.path.join(old_mem, "metadata.jsonl"),
               json.dumps({"name": "legacy", "description": "Legacy memory entry with description.",
                           "read_when": ["legacy"], "references": []}) + "\n")
        old_wf = os.path.join(self.home, ".claude", "global", "workflows")
        os.makedirs(os.path.join(old_wf, "archived"))
        _write(os.path.join(old_wf, "workflows.jsonl"), "{}\n")
        _write(os.path.join(old_wf, "archived", "old-workflow.md"), "# wf")
        # 原生 auto-memory 项目目录:不应迁移
        auto = os.path.join(self.home, ".claude", "projects", "some-slug", "memory")
        os.makedirs(auto)
        _write(os.path.join(auto, "auto.md"), "# auto")

        r = self._migrate()
        self.assertEqual(r.returncode, 0, r.stderr)
        new_mem = os.path.join(self.home, ".cc-switch", "memory", "global")
        self.assertTrue(os.path.exists(os.path.join(new_mem, "legacy.md")))
        metadata = common.read_metadata(os.path.join(new_mem, "metadata.jsonl"))
        self.assertIn("legacy", metadata)
        new_wf = os.path.join(self.home, ".cc-switch", "workflows", "global")
        self.assertTrue(os.path.exists(os.path.join(new_wf, "workflows.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(new_wf, "archived", "old-workflow.md")))
        # auto-memory 未迁移
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cc-switch", "memory", "projects", "some-slug")))

    def test_migrate_metadata_merge_keeps_existing(self):
        old_mem = os.path.join(self.home, ".claude", "global", "memory")
        os.makedirs(old_mem)
        _write(os.path.join(old_mem, "a.md"), "# a")
        _write(os.path.join(old_mem, "metadata.jsonl"),
               json.dumps({"name": "a", "description": "Old description of a.", "read_when": ["a"], "references": []}) + "\n")
        # 目标已有同名条目(新前缀)→ 保留目标不覆盖
        new_mem = os.path.join(self.home, ".cc-switch", "memory", "global")
        os.makedirs(new_mem)
        _write(os.path.join(new_mem, "a.md"), "# a (new)")
        _write(os.path.join(new_mem, "metadata.jsonl"),
               json.dumps({"name": "a", "description": "New description of a.", "read_when": ["a-new"], "references": []}) + "\n")
        r = self._migrate()
        self.assertEqual(r.returncode, 0, r.stderr)
        metadata = common.read_metadata(os.path.join(new_mem, "metadata.jsonl"))
        self.assertEqual(metadata["a"]["description"], "New description of a.")

    def test_migrate_idempotent_skip_existing(self):
        old_mem = os.path.join(self.home, ".claude", "global", "memory")
        os.makedirs(old_mem)
        _write(os.path.join(old_mem, "legacy.md"), "v1 content")
        r = self._migrate()
        self.assertEqual(r.returncode, 0, r.stderr)
        # 修改旧文件后重跑:目标已存在 → 跳过不覆盖
        _write(os.path.join(old_mem, "legacy.md"), "v2 content")
        r2 = self._migrate()
        self.assertEqual(r2.returncode, 0, r2.stderr)
        content = open(os.path.join(self.home, ".cc-switch", "memory", "global", "legacy.md"), encoding="utf-8").read()
        self.assertEqual(content, "v1 content")

    def test_migrate_no_old_data(self):
        r = self._migrate()
        self.assertEqual(r.returncode, 0)
        self.assertIn("SKIP", r.stdout)


class TestFastPreFilter(unittest.TestCase):
    """v2.3 fast pre-filter: no-op hook payloads skip the engine before heavy
    imports; memory payloads (case-insensitive) still pass through. Also covers
    the normcase guard fix (differently-cased memory paths are recognized)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.mem_dir = os.path.join(self.home, ".cc-switch", "memory", "global")
        os.makedirs(self.mem_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, payload, command="sync-and-hint", stdin_input=None):
        return subprocess.run(
            [sys.executable, MEMORY_SYNC, command],
            capture_output=True, text=True, encoding="utf-8",
            env=_home_env(self.home),
            input=stdin_input if stdin_input is not None else json.dumps(payload),
        )

    def _make_md(self, slug):
        _write(os.path.join(self.mem_dir, f"{slug}.md"), "# Test\n\nContent.")

    def test_code_payload_skipped_no_output(self):
        # 无 "memory" 子串的代码编辑 -> 快速路径直接 exit 0(不经过引擎)
        payload = {
            "hook_event_name": "PostToolUse",
            "cwd": self.home,
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: src/main.py\n@@ -1 +1 @@\n-print(1)\n+print(2)\n*** End Patch"
            },
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_code_payload_mentioning_memory_falls_through(self):
        # diff 内容含 "memory" 但非记忆文件 -> 快速路径放行,精确守卫 exit 0
        payload = {
            "hook_event_name": "PostToolUse",
            "cwd": self.home,
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: src/cache.py\n@@ -1 +1 @@\n-# memory allocator\n+# faster memory allocator\n*** End Patch"
            },
        }
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_uppercase_memory_path_processed(self):
        # 大小写不同的记忆路径:快速路径 case-insensitive + normcase 守卫都通过 -> hint
        self._make_md("alpha")
        up = os.path.join(self.home, ".cc-switch", "MEMORY", "GLOBAL", "alpha.md")
        payload = {"hook_event_name": "PostToolUse", "tool_input": {"file_path": up}}
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alpha", r.stdout)

    def test_mixed_case_memory_path_processed(self):
        # 整个路径大小写混合仍被识别
        self._make_md("beta")
        mixed = os.path.join(self.home, ".cc-SWITCH", "memory", "GLOBAL", "beta.md")
        payload = {"hook_event_name": "PostToolUse", "tool_input": {"file_path": mixed}}
        r = self._run(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("beta", r.stdout)

    def test_manual_no_stdin_exits_0(self):
        # 手动调用(空 stdin)-> 快速路径放行,引擎 no-op exit 0
        r = self._run(None, stdin_input="")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_session_start_unaffected(self):
        # session-start 不走快速路径:payload 无 "memory" 子串也必须正常注入 HOTLIST
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        slug = common.project_slug(repo)
        hotlist = os.path.join(self.home, ".cc-switch", "memory", "projects", slug, "HOTLIST.md")
        os.makedirs(os.path.dirname(hotlist))
        _write(hotlist, "- [alpha](alpha.md) \u2014 Alpha desc\n")
        payload = {"hook_event_name": "SessionStart", "cwd": repo}
        r = self._run(payload, command="session-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Alpha desc", r.stdout)
