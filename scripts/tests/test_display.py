import json
import os
import subprocess
import sys
import tempfile
import unittest

MEMORY_SYNC = os.path.join(os.path.dirname(__file__), "..", "memory-sync.py")


class TestDisplayCLIBase(unittest.TestCase):
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
            capture_output=True, text=True, env=env, input=stdin_input,
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


class TestDisplayCLI(TestDisplayCLIBase):
    def test_display_recognized(self):
        result = self._run("display")
        # 空库 + 无参数 → 成功退出(空状态占位),退出码 0
        self.assertEqual(result.returncode, 0)


class TestDisplayGraph(TestDisplayCLIBase):
    def _setup_two_nodes_edge(self):
        self._make_md("alpha", "# Alpha\n\nContent.")
        self._make_md("beta", "# Beta\n\nContent.")
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": ["beta"]},
            {"name": "beta", "description": "Beta memory for testing.", "read_when": ["beta topic"], "references": []},
        ])

    def test_graph_basic_edges(self):
        self._setup_two_nodes_edge()
        result = self._run("display", "--view", "graph")
        self.assertEqual(result.returncode, 0)
        self.assertIn("graph LR", result.stdout)
        self.assertIn("alpha --> beta", result.stdout)
        self.assertIn("```mermaid", result.stdout)
        self.assertIn("```", result.stdout)

    def test_graph_node_id_quoted(self):
        # slug 含 `-`,必须用 ["..."] 或 ("...") 引号包裹
        self._setup_two_nodes_edge()
        result = self._run("display", "--view", "graph")
        # alpha 有出边,beta 有入边(alpha --> beta),两者均为有连接节点 → 方框
        self.assertIn('alpha["alpha"]', result.stdout)   # 有出边 → 方框
        self.assertIn('beta["beta"]', result.stdout)     # 有入边 → 方框

    def test_graph_isolated_node_shape(self):
        self._make_md("solo", "# Solo\n\nContent.")
        self._setup_metadata([
            {"name": "solo", "description": "Solo memory for testing.", "read_when": ["solo topic"], "references": []},
        ])
        result = self._run("display", "--view", "graph")
        self.assertIn('solo("solo")', result.stdout)

    def test_graph_hub_styling(self):
        # 入度≥3 → 圆角矩形 + 加粗
        self._make_md("hub", "# Hub\n\nContent.")
        self._setup_metadata([
            {"name": "hub", "description": "Hub memory for testing.", "read_when": ["hub topic"], "references": []},
            {"name": "a", "description": "A memory for testing.", "read_when": ["a topic"], "references": ["hub"]},
            {"name": "b", "description": "B memory for testing.", "read_when": ["b topic"], "references": ["hub"]},
            {"name": "c", "description": "C memory for testing.", "read_when": ["c topic"], "references": ["hub"]},
        ])
        result = self._run("display", "--view", "graph")
        self.assertIn('hub(["hub"])', result.stdout)

    def test_graph_excludes_node(self):
        self._setup_two_nodes_edge()
        result = self._run("display", "--view", "graph", "--exclude", "beta")
        self.assertNotIn("beta", result.stdout)          # 节点不出现在任何位置
        self.assertNotIn("alpha --> beta", result.stdout)  # 出边消失

    def test_graph_self_ref_defensive(self):
        self._make_md("selfy", "# Selfy\n\nContent.")
        self._setup_metadata([
            {"name": "selfy", "description": "Selfy memory for testing.", "read_when": ["selfy topic"], "references": ["selfy"]},
        ])
        result = self._run("display", "--view", "graph")
        self.assertNotIn("selfy --> selfy", result.stdout)

    def test_graph_global_prefix_edge(self):
        self._make_md("alpha", "# Alpha\n\nContent.")
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": ["global:onnx-shape-inference"]},
        ])
        result = self._run("display", "--view", "graph")
        self.assertIn("alpha --> global:onnx-shape-inference", result.stdout)

    def test_graph_node_ordering(self):
        # score = in*2 + out*0.5;beta 被引用(in=1, score=2.0)高于 alpha(out=1, score=0.5),beta 应在前
        self._make_md("alpha", "# Alpha\n\nContent.")
        self._make_md("beta", "# Beta\n\nContent.")
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": ["beta"]},
            {"name": "beta", "description": "Beta memory for testing.", "read_when": ["beta topic"], "references": []},
        ])
        result = self._run("display", "--view", "graph")
        # alpha 有出边(方框),beta 有入边(方框);beta score 高应在前
        self.assertLess(result.stdout.index('beta["beta"]'), result.stdout.index('alpha["alpha"]'))

    def test_graph_no_mermaid_flag(self):
        self._setup_two_nodes_edge()
        result = self._run("display", "--view", "graph", "--no-mermaid")
        self.assertNotIn("```mermaid", result.stdout)
        self.assertIn("| 节点 | 引用(出边) | 被引用(入边) |", result.stdout)

    def test_graph_large_truncation(self):
        # 51 节点 → 不截断,加密集提示注释
        for i in range(51):
            slug = f"node-{i:03d}"
            self._make_md(slug, f"# {slug}\n\nContent.")
        self._setup_metadata([
            {"name": f"node-{i:03d}", "description": f"Node {i} for testing.", "read_when": [f"topic {i}"], "references": []}
            for i in range(51)
        ])
        result = self._run("display", "--view", "graph")
        self.assertIn("may render densely in Feishu", result.stdout)
        self.assertIn("node-050", result.stdout)  # 不截断
