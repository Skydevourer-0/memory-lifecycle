import json
import os
import tempfile
import unittest
import common


class TestJsonlIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jsonl_path = os.path.join(self.tmp.name, "metadata.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_empty_jsonl(self):
        with open(self.jsonl_path, "w") as f:
            pass  # create empty file
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

    def test_scope_from_global_file(self):
        result = common.detect_scope_from_file(
            os.path.expanduser("~/.claude/global/memory/foo.md")
        )
        self.assertEqual(result, "global")

    def test_scope_from_project_file(self):
        result = common.detect_scope_from_file(
            "/home/user/projects/foo/.claude/projects/foo-bar/memory/slug.md"
        )
        self.assertEqual(result, "project")


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
        with open(self.target, "w") as f:
            f.write("before\n<!-- memory-index:start -->\nold\n<!-- memory-index:end -->\nafter\n")
        metadata = {"a": {"name": "a", "description": "A memory",
                           "read_when": ["x"], "references": []}}
        result = common.inject_hot_list(self.target, metadata)
        self.assertTrue(result)
        with open(self.target, "r") as f:
            content = f.read()
        self.assertIn("A memory", content)
        self.assertNotIn("old", content)
        self.assertIn("before", content)
        self.assertIn("after", content)

    def test_returns_false_when_file_missing(self):
        result = common.inject_hot_list("/nonexistent/path.md", {})
        self.assertFalse(result)

    def test_returns_false_when_markers_missing(self):
        with open(self.target, "w") as f:
            f.write("no markers here")
        result = common.inject_hot_list(self.target, {"a": {"name": "a", "description": "d", "read_when": [], "references": []}})
        self.assertFalse(result)


class TestEnsureMarkers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.tmp.name, "test.md")

    def tearDown(self):
        self.tmp.cleanup()

    def test_appends_markers_when_missing(self):
        with open(self.target, "w") as f:
            f.write("existing content\n")
        result = common.ensure_markers(self.target)
        self.assertTrue(result)
        with open(self.target, "r") as f:
            content = f.read()
        self.assertIn("existing content", content)
        self.assertIn("<!-- memory-index:start -->", content)
        self.assertIn("<!-- memory-index:end -->", content)

    def test_returns_false_when_markers_present(self):
        with open(self.target, "w") as f:
            f.write("<!-- memory-index:start -->\n<!-- memory-index:end -->\n")
        result = common.ensure_markers(self.target)
        self.assertFalse(result)

    def test_returns_false_when_file_missing(self):
        result = common.ensure_markers("/nonexistent/file.md")
        self.assertFalse(result)


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
