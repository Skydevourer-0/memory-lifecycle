# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

import common


def _with_env(home):
    """Return env copy with HOME/USERPROFILE pointed at temp home."""
    env = os.environ.copy()
    env["HOME"] = home
    env["USERPROFILE"] = home
    env.pop("CODEX_HOME", None)
    return env


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class TestJsonlIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jsonl_path = os.path.join(self.tmp.name, "metadata.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_empty_jsonl(self):
        _write(self.jsonl_path, "")
        result = common.read_metadata(self.jsonl_path)
        self.assertEqual(result, {})

    def test_write_and_read_roundtrip(self):
        entry = {
            "name": "my-topic",
            "description": "One-line summary",
            "read_when": ["phrase one", "trigger two"],
            "references": ["other-slug"]
        }
        common.write_metadata(self.jsonl_path, "my-topic", entry)
        result = common.read_metadata(self.jsonl_path)
        self.assertEqual(result["my-topic"], entry)

    def test_write_atomic_does_not_corrupt_on_crash(self):
        existing = {"my-topic": {"name": "my-topic", "description": "old", "read_when": [], "references": []}}
        common.write_metadata(self.jsonl_path, "my-topic", existing["my-topic"])
        try:
            common.write_metadata(self.jsonl_path, "my-topic", None)
        except TypeError:
            pass
        result = common.read_metadata(self.jsonl_path)
        self.assertEqual(result["my-topic"]["description"], "old")

    def test_write_overwrites_existing_target_atomically(self):
        # os.replace semantics: destination already exists -> must succeed.
        first = {"a": {"name": "a", "description": "first", "read_when": [], "references": []}}
        common.write_all_metadata(self.jsonl_path, first)
        second = {"b": {"name": "b", "description": "second", "read_when": [], "references": []}}
        common.write_all_metadata(self.jsonl_path, second)
        result = common.read_metadata(self.jsonl_path)
        self.assertEqual(list(result.keys()), ["b"])
        self.assertEqual(result["b"]["description"], "second")

    def test_remove_entry(self):
        common.write_metadata(self.jsonl_path, "a", {"name": "a", "description": "d", "read_when": [], "references": []})
        common.write_metadata(self.jsonl_path, "b", {"name": "b", "description": "d", "read_when": [], "references": []})
        common.remove_metadata(self.jsonl_path, "a")
        result = common.read_metadata(self.jsonl_path)
        self.assertNotIn("a", result)
        self.assertIn("b", result)


class TestSlugValidation(unittest.TestCase):
    def test_valid_slug(self):
        self.assertTrue(common.validate_slug("my-topic"))
        self.assertTrue(common.validate_slug("a"))
        self.assertTrue(common.validate_slug("body-hash-incremental-skip"))

    def test_invalid_slug_uppercase(self):
        self.assertFalse(common.validate_slug("My-Topic"))

    def test_invalid_slug_special_chars(self):
        self.assertFalse(common.validate_slug("my_topic"))
        self.assertFalse(common.validate_slug("my topic"))
        self.assertFalse(common.validate_slug("my.topic"))
        self.assertFalse(common.validate_slug("archived_memory_engine_dev"))

    def test_invalid_slug_empty(self):
        self.assertFalse(common.validate_slug(""))
        self.assertFalse(common.validate_slug("-"))
        self.assertFalse(common.validate_slug("--"))


class TestProjectSlug(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows path semantics only on Windows")
    def test_windows_path_folded(self):
        # 统一 slug 算法:C:\Users\a\proj -> c-users-a-proj(折叠连续 '-')
        self.assertEqual(common.project_slug(r"C:\Users\a\proj"), "c-users-a-proj")

    def test_posix_path(self):
        # Windows 上 realpath 会补盘符,期望值按同一算法从 realpath 计算
        real = os.path.realpath("/home/user/code/my-project")
        lowered = real.lower()
        replaced = common.re.sub(r"[^a-z0-9]", "-", lowered)
        expected = common.re.sub(r"-+", "-", replaced).strip("-")
        self.assertEqual(common.project_slug("/home/user/code/my-project"), expected)

    def test_slug_always_valid(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            slug = common.project_slug(tmp.name)
            self.assertTrue(common.validate_slug(slug))
            self.assertEqual(slug, slug.lower())
        finally:
            tmp.cleanup()

    def test_get_memory_dir_project_uses_folded_slug(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            os.makedirs(os.path.join(tmp.name, ".git"))
            mem_dir = common.get_memory_dir("project", cwd=tmp.name)
            expected = os.path.join(
                os.path.expanduser("~/.cc-switch/memory/projects"),
                common.project_slug(tmp.name),
            )
            self.assertEqual(os.path.normcase(mem_dir), os.path.normcase(expected))
        finally:
            tmp.cleanup()


class TestCodexHome(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("CODEX_HOME", None)

    def test_env_var_wins(self):
        os.environ["CODEX_HOME"] = r"C:\custom\codex-home"
        self.assertEqual(common.codex_home(), r"C:\custom\codex-home")

    def test_default_is_codex_dir(self):
        os.environ.pop("CODEX_HOME", None)
        self.assertEqual(common.codex_home(), os.path.expanduser("~/.codex"))


class TestScopeDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_global_scope_no_git(self):
        self.assertEqual(common.detect_scope(cwd=self.tmp.name), "global")

    def test_project_scope_with_git(self):
        git_dir = os.path.join(self.tmp.name, ".git")
        os.makedirs(git_dir)
        self.assertEqual(common.detect_scope(cwd=self.tmp.name), "project")

    def test_project_scope_with_git_file_worktree(self):
        git_file = os.path.join(self.tmp.name, ".git")
        _write(git_file, "gitdir: /elsewhere/.git/worktrees/x\n")
        self.assertEqual(common.detect_scope(cwd=self.tmp.name), "project")
        self.assertEqual(common._find_git_root(self.tmp.name), self.tmp.name)

    def test_scope_from_global_file_new_prefix(self):
        result = common.detect_scope_from_file(
            os.path.expanduser("~/.cc-switch/memory/global/foo.md")
        )
        self.assertEqual(result, "global")

    def test_scope_from_project_file_new_prefix(self):
        result = common.detect_scope_from_file(
            os.path.expanduser("~/.cc-switch/memory/projects/foo-bar/slug.md")
        )
        self.assertEqual(result, "project")

    def test_scope_from_old_global_prefix_migrate_only(self):
        result = common.detect_scope_from_file(
            os.path.expanduser("~/.claude/global/memory/foo.md")
        )
        self.assertEqual(result, "global")

    def test_scope_from_old_project_prefix_migrate_only(self):
        result = common.detect_scope_from_file(
            os.path.expanduser("~/.claude/projects/foo-bar/memory/slug.md")
        )
        self.assertEqual(result, "project")

    def test_scope_from_unknown_path_none(self):
        self.assertIsNone(common.detect_scope_from_file("/tmp/unrelated/file.md"))

    def test_skills_dir_always_global(self):
        home = os.path.join(self.tmp.name, "home")
        skills = os.path.join(home, ".cc-switch", "skills", "memory-lifecycle")
        os.makedirs(os.path.join(skills, ".git"))
        saved_home = os.environ.get("HOME")
        saved_profile = os.environ.get("USERPROFILE")
        os.environ["HOME"] = home
        os.environ["USERPROFILE"] = home
        try:
            self.assertEqual(common.detect_scope(cwd=skills), "global")
        finally:
            if saved_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_home
            if saved_profile is None:
                os.environ.pop("USERPROFILE", None)
            else:
                os.environ["USERPROFILE"] = saved_profile

    def test_skills_like_sibling_not_global(self):
        # 路径边界:~/.cc-switch-skills 不应被误判为 global
        home = os.path.join(self.tmp.name, "home")
        sibling = os.path.join(home, ".cc-switch-skills", "proj")
        os.makedirs(os.path.join(sibling, ".git"))
        saved_home = os.environ.get("HOME")
        saved_profile = os.environ.get("USERPROFILE")
        os.environ["HOME"] = home
        os.environ["USERPROFILE"] = home
        try:
            self.assertEqual(common.detect_scope(cwd=sibling), "project")
        finally:
            if saved_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_home
            if saved_profile is None:
                os.environ.pop("USERPROFILE", None)
            else:
                os.environ["USERPROFILE"] = saved_profile


class TestResolveMemDirFromFile(unittest.TestCase):
    def test_global_new_prefix(self):
        path = os.path.expanduser("~/.cc-switch/memory/global/foo.md")
        self.assertEqual(
            os.path.normcase(common.resolve_mem_dir_from_file(path)),
            os.path.normcase(os.path.expanduser("~/.cc-switch/memory/global")),
        )

    def test_project_new_prefix(self):
        path = os.path.expanduser("~/.cc-switch/memory/projects/my-proj/sub/x.md")
        self.assertEqual(
            os.path.normcase(common.resolve_mem_dir_from_file(path)),
            os.path.normcase(os.path.expanduser("~/.cc-switch/memory/projects/my-proj")),
        )

    def test_old_prefixes_none(self):
        self.assertIsNone(common.resolve_mem_dir_from_file(os.path.expanduser("~/.claude/global/memory/foo.md")))
        self.assertIsNone(common.resolve_mem_dir_from_file("/home/u/.claude/projects/x/memory/y.md"))

    def test_unknown_path_none(self):
        self.assertIsNone(common.resolve_mem_dir_from_file("/tmp/unrelated.md"))


class TestValidationGates(unittest.TestCase):
    def test_description_too_short(self):
        self.assertEqual(
            common.validate_description("Short").get("error"),
            "description: min 20 non-whitespace chars"
        )

    def test_description_blacklist(self):
        for word in ["TBD", "todo", "PLACEHOLDER", "WIP", "draft"]:
            self.assertIsNotNone(
                common.validate_description(word).get("error")
            )

    def test_description_blacklist_chinese(self):
        for word in ["记住", "记一下", "重要", "备忘", "笔记", "总结", "概述", "相关信息", "待补充"]:
            self.assertIsNotNone(
                common.validate_description(word).get("error"),
                f"{word} 应在黑名单中",
            )

    def test_description_boilerplate_chinese(self):
        for text in ["这是关于部署的记忆", "描述了系统架构", "一些调试的笔记"]:
            self.assertIsNotNone(
                common.validate_description(text).get("error"),
                f"{text} 应命中 boilerplate",
            )

    def test_description_valid(self):
        self.assertIsNone(
            common.validate_description("This is a meaningful description about memory testing.")
                .get("error")
        )

    def test_read_when_empty(self):
        self.assertEqual(
            common.validate_read_when([]).get("error"),
            "read-when: min 1 phrase required"
        )

    def test_read_when_too_many(self):
        phrases = [f"phrase number {i}" for i in range(9)]
        self.assertIn("max 8", common.validate_read_when(phrases).get("error"))

    def test_read_when_phrase_too_short(self):
        self.assertIn(
            "too short",
            common.validate_read_when(["x"]).get("error")
        )

    def test_read_when_stopword_phrase(self):
        self.assertIsNotNone(
            common.validate_read_when(["the stuff"]).get("error")
        )

    def test_read_when_blacklist(self):
        self.assertIsNotNone(
            common.validate_read_when(["TBD"]).get("error")
        )

    def test_read_when_valid(self):
        self.assertIsNone(
            common.validate_read_when(["debugging cost display", "token tracking"]).get("error")
        )

    def test_duplicate_read_when_detected(self):
        self.assertEqual(common.duplicate_read_when(["a phrase", "A Phrase", "b phrase"]), ["A Phrase"])

    def test_duplicate_read_when_empty(self):
        self.assertEqual(common.duplicate_read_when(["a phrase", "b phrase"]), [])

    def test_references_too_many(self):
        refs = [f"slug-{i}" for i in range(11)]
        self.assertIn("max 10", common.validate_references(refs, set(refs)).get("error"))

    def test_references_self_reference(self):
        self.assertIn(
            "self-reference",
            common.validate_references(["my-slug"], {"my-slug", "other"}, current_slug="my-slug").get("error")
        )

    def test_references_unknown_slug(self):
        self.assertIn(
            "unknown",
            common.validate_references(["nonexistent"], {"known"}).get("error")
        )

    def test_references_valid(self):
        self.assertIsNone(
            common.validate_references(["known"], {"other"}, {"known"}).get("error")
        )

    def test_references_global_prefix(self):
        self.assertIsNone(
            common.validate_references(["global:security"], {"known"}, {"security"}).get("error")
        )


class TestScoring(unittest.TestCase):
    def test_score_with_references(self):
        metadata = {
            "a": {"name": "a", "description": "d", "read_when": ["x"], "references": ["b", "c"]},
            "b": {"name": "b", "description": "d", "read_when": ["y"], "references": ["c"]},
            "c": {"name": "c", "description": "d", "read_when": ["z"], "references": []},
        }
        scores, _ = common.compute_scores(metadata)
        # a: in=0 out=2 -> 0*2.0 + 2*0.5 = 1.0
        # b: in=1 out=1 -> 1*2.0 + 1*0.5 = 2.5
        # c: in=2 out=0 -> 2*2.0 + 0*0.5 = 4.0
        self.assertAlmostEqual(scores["a"], 1.0)
        self.assertAlmostEqual(scores["b"], 2.5)
        self.assertAlmostEqual(scores["c"], 4.0)

    def test_score_all_zero_tiebreaker(self):
        metadata = {
            "z": {"name": "z", "description": "d", "read_when": [], "references": []},
            "a": {"name": "a", "description": "d", "read_when": [], "references": []},
        }
        scores, _ = common.compute_scores(metadata)
        self.assertEqual(scores["a"], 0.0)
        self.assertEqual(scores["z"], 0.0)


class TestHeadingExtraction(unittest.TestCase):
    def test_extract_headings(self):
        body = "## Design decisions\nSome text.\n### Metadata schema\nMore text.\n# Top level ignored\n"
        headings = common.extract_headings(body)
        self.assertEqual(headings, ["Design decisions", "Metadata schema"])

    def test_extract_headings_empty(self):
        self.assertEqual(common.extract_headings(""), [])
        self.assertEqual(common.extract_headings("Just text, no headings."), [])


class TestIndexGeneration(unittest.TestCase):
    def test_generate_index_md(self):
        metadata = {
            "c": {"name": "c", "description": "See desc", "read_when": ["x"], "references": ["a"]},
            "a": {"name": "a", "description": "A memory", "read_when": [], "references": []},
            "b": {"name": "b", "description": "B memory", "read_when": ["y", "z"], "references": ["a"]},
        }
        index_md = common.generate_index_md(metadata)
        self.assertIn("# Memory Index", index_md)
        self.assertIn("[b]", index_md)
        self.assertIn("[c]", index_md)
        self.assertIn("[a]", index_md)
        bpos = index_md.index("[b]")
        cpos = index_md.index("[c]")
        self.assertLess(bpos, cpos)


class TestInjectHotList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.tmp.name, "CLAUDE.md")

    def tearDown(self):
        self.tmp.cleanup()

    def test_injects_content_between_markers(self):
        _write(self.target, "before\n<!-- memory-index:start -->\nold\n<!-- memory-index:end -->\nafter\n")
        metadata = {"a": {"name": "a", "description": "A memory",
                           "read_when": ["x"], "references": []}}
        result = common.inject_hot_list(self.target, metadata)
        self.assertTrue(result)
        content = _read(self.target)
        self.assertIn("A memory", content)
        self.assertNotIn("old", content)
        self.assertIn("before", content)
        self.assertIn("after", content)

    def test_returns_false_when_file_missing(self):
        result = common.inject_hot_list("/nonexistent/path.md", {})
        self.assertFalse(result)

    def test_returns_false_when_markers_missing(self):
        _write(self.target, "no markers here")
        result = common.inject_hot_list(self.target, {"a": {"name": "a", "description": "d", "read_when": [], "references": []}})
        self.assertFalse(result)

    def test_standalone_full_write_no_markers(self):
        target = os.path.join(self.tmp.name, "HOTLIST.md")
        _write(target, "stale old content\n")
        metadata = {
            "a": {"name": "a", "description": "Alpha memory description", "read_when": [], "references": []},
            "b": {"name": "b", "description": "Beta memory description", "read_when": [], "references": []},
        }
        result = common.inject_hot_list(target, metadata, standalone=True)
        self.assertTrue(result)
        content = _read(target)
        self.assertNotIn("stale", content)
        self.assertNotIn("memory-index:start", content)
        self.assertIn("[a](a.md)", content)
        self.assertIn("[b](b.md)", content)
        self.assertTrue(content.endswith("\n"))

    def test_standalone_overwrites_existing_target(self):
        # os.replace:目标已存在时覆盖成功(Windows os.rename 会抛 FileExistsError)
        target = os.path.join(self.tmp.name, "HOTLIST.md")
        _write(target, "first\n")
        metadata = {"a": {"name": "a", "description": "A memory", "read_when": [], "references": []}}
        common.inject_hot_list(target, metadata, standalone=True)
        common.inject_hot_list(target, metadata, standalone=True)  # 第二次覆盖
        self.assertIn("A memory", _read(target))

    def test_hot_list_budget_1200(self):
        metadata = {}
        for i in range(50):
            slug = f"mem-{i:03d}"
            metadata[slug] = {
                "name": slug,
                "description": "这是一条比较长的记忆描述用于测试字符预算上限是否生效 " * 5,
                "read_when": [],
                "references": [],
            }
        lines = common.hot_list_lines(metadata)
        total = sum(len(l) + 1 for l in lines)
        self.assertLessEqual(total, common.HOTLIST_BUDGET)
        self.assertLessEqual(len(lines), len(metadata))


class TestEnsureMarkers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.tmp.name, "test.md")

    def tearDown(self):
        self.tmp.cleanup()

    def test_appends_markers_when_missing(self):
        _write(self.target, "existing content\n")
        result = common.ensure_markers(self.target)
        self.assertTrue(result)
        content = _read(self.target)
        self.assertIn("existing content", content)
        self.assertIn("<!-- memory-index:start -->", content)
        self.assertIn("<!-- memory-index:end -->", content)

    def test_returns_false_when_markers_present(self):
        _write(self.target, "<!-- memory-index:start -->\n<!-- memory-index:end -->\n")
        result = common.ensure_markers(self.target)
        self.assertFalse(result)

    def test_returns_false_when_file_missing(self):
        result = common.ensure_markers("/nonexistent/file.md")
        self.assertFalse(result)


class TestGetHotListTarget(unittest.TestCase):
    def test_global_dual_targets(self):
        targets = common.get_hot_list_target("global")
        self.assertEqual(len(targets), 2)
        self.assertTrue(targets[0].endswith("CLAUDE.md"))
        self.assertTrue(targets[1].endswith("AGENTS.md"))

    def test_project_hotlist(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            os.makedirs(os.path.join(tmp.name, ".git"))
            target = common.get_hot_list_target("project", cwd=tmp.name)
            self.assertTrue(target.endswith(os.path.join("HOTLIST.md")))
            self.assertIn(os.path.join("projects", common.project_slug(tmp.name)), target)
        finally:
            tmp.cleanup()


class TestValidateSetMetadataJson(unittest.TestCase):
    def test_valid_input_passes(self):
        result = common.validate_set_metadata_json(
            {"description": "A detailed description of this memory topic."},
            "my-slug", {"my-slug", "other"}, None
        )
        self.assertEqual(result, {})

    def test_invalid_description_reports_error(self):
        result = common.validate_set_metadata_json(
            {"description": "short"}, "my-slug", {"my-slug"}, None
        )
        self.assertTrue(len(result.get("errors", [])) > 0)

    def test_type_error_reported(self):
        result = common.validate_set_metadata_json(
            {"read_when": "not a list"}, "my-slug", {"my-slug"}, None
        )
        self.assertTrue(len(result.get("errors", [])) > 0)

    def test_missing_field_not_validated(self):
        # only description provided, read_when and references omitted -> only description validated
        result = common.validate_set_metadata_json(
            {"description": "A valid description that is long enough."},
            "my-slug", {"my-slug"}, None
        )
        self.assertEqual(result, {})
