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


class TestDisplayStats(TestDisplayCLIBase):
    def _setup_three_nodes(self):
        for slug in ("alpha", "beta", "gamma"):
            self._make_md(slug, f"# {slug.title()}\n\nContent.")
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": ["beta", "gamma"]},
            {"name": "beta", "description": "Beta memory for testing.", "read_when": ["beta topic"], "references": ["alpha"]},
            {"name": "gamma", "description": "Gamma memory for testing.", "read_when": ["gamma topic"], "references": []},
        ])

    def test_stats_counts(self):
        self._setup_three_nodes()
        result = self._run("display", "--view", "stats")
        self.assertEqual(result.returncode, 0)
        self.assertIn("| 记忆总数 | 3 |", result.stdout)
        self.assertIn("| 引用边总数 | 3 |", result.stdout)   # alpha→beta/gamma,beta→alpha
        self.assertIn("| 有引用的记忆数 | 2 |", result.stdout)  # alpha, beta

    def test_stats_avg_out_degree(self):
        self._setup_three_nodes()
        result = self._run("display", "--view", "stats")
        self.assertIn("| 平均出度 | 1.00 |", result.stdout)   # 3 边 / 3 节点

    def test_stats_bidirectional_pairs(self):
        self._setup_three_nodes()
        result = self._run("display", "--view", "stats")
        self.assertIn("| 双向引用对数 | 1 |", result.stdout)   # alpha↔beta

    def test_stats_hub_count(self):
        # gamma 入度 0,无枢纽;构造入度≥3
        self._make_md("hub", "# Hub\n\nContent.")
        self._setup_metadata([
            {"name": "hub", "description": "Hub memory for testing.", "read_when": ["hub topic"], "references": []},
            {"name": "a", "description": "A memory for testing.", "read_when": ["a topic"], "references": ["hub"]},
            {"name": "b", "description": "B memory for testing.", "read_when": ["b topic"], "references": ["hub"]},
            {"name": "c", "description": "C memory for testing.", "read_when": ["c topic"], "references": ["hub"]},
        ])
        result = self._run("display", "--view", "stats")
        self.assertIn("| 枢纽节点(入度≥3) | 1 (hub) |", result.stdout)

    def test_stats_top5_ordering(self):
        self._setup_three_nodes()
        result = self._run("display", "--view", "stats")
        # score: alpha = 1*2+2*0.5=3.0,beta = 1*2+1*0.5=2.5,gamma = 1*2+0*0.5=2.0
        idx_a = result.stdout.index("alpha")
        idx_b = result.stdout.index("beta")
        idx_g = result.stdout.index("gamma")
        self.assertLess(idx_a, idx_b)
        self.assertLess(idx_b, idx_g)

    def test_stats_bidirectional_global_prefix(self):
        # a 引用 global:b,b 引用 global:a → 双向对数 1(对称清洗)
        self._make_md("a", "# A\n\nContent.")
        self._make_md("b", "# B\n\nContent.")
        self._setup_metadata([
            {"name": "a", "description": "A memory for testing.", "read_when": ["a topic"], "references": ["global:b"]},
            {"name": "b", "description": "B memory for testing.", "read_when": ["b topic"], "references": ["global:a"]},
        ])
        result = self._run("display", "--view", "stats")
        self.assertIn("| 双向引用对数 | 1 |", result.stdout)

    def test_stats_tech_topics(self):
        self._setup_three_nodes()
        result = self._run("display", "--view", "stats")
        self.assertIn("覆盖技术主题", result.stdout)
        self.assertIn("other", result.stdout)   # alpha/beta/gamma 无前缀 → other

    def test_stats_empty(self):
        result = self._run("display", "--view", "stats")
        self.assertEqual(result.returncode, 0)
        self.assertIn("记忆总数 | 0", result.stdout)

    def test_stats_excludes_reflected(self):
        self._setup_three_nodes()
        result = self._run("display", "--view", "stats", "--exclude", "beta")
        self.assertIn("| 记忆总数 | 2 |", result.stdout)
        self.assertIn("| 引用边总数 | 1 |", result.stdout)  # alpha→gamma 保留,beta 边剔除


class TestDisplayTimeline(TestDisplayCLIBase):
    def _set_mtime(self, slug, y, m, d):
        import datetime
        path = os.path.join(self.mem_dir, f"{slug}.md")
        os.utime(path, (datetime.datetime(y, m, d, 12, 0, tzinfo=datetime.timezone.utc).timestamp(),) * 2)

    def _setup_two_months(self):
        self._make_md("alpha", "# Alpha\n\nContent.")
        self._make_md("beta", "# Beta\n\nContent.")
        self._set_mtime("alpha", 2026, 6, 30)
        self._set_mtime("beta", 2026, 7, 1)
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": []},
            {"name": "beta", "description": "Beta memory for testing.", "read_when": ["beta topic"], "references": []},
        ])

    def test_timeline_monthly_buckets(self):
        self._setup_two_months()
        result = self._run("display", "--view", "timeline")
        self.assertEqual(result.returncode, 0)
        self.assertIn("section 2026-06", result.stdout)
        self.assertIn("section 2026-07", result.stdout)
        self.assertIn("timeline", result.stdout)

    def test_timeline_same_day_merge(self):
        self._make_md("alpha", "# Alpha\n\nContent.")
        self._make_md("beta", "# Beta\n\nContent.")
        self._set_mtime("alpha", 2026, 7, 1)
        self._set_mtime("beta", 2026, 7, 1)
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": []},
            {"name": "beta", "description": "Beta memory for testing.", "read_when": ["beta topic"], "references": []},
        ])
        result = self._run("display", "--view", "timeline")
        # 同日合并到同一行,用 `:` 换行续接
        self.assertIn("7月1日 : alpha", result.stdout)
        self.assertIn(": beta", result.stdout)

    def test_timeline_skips_index_files(self):
        self._make_md("alpha", "# Alpha\n\nContent.")
        self._make_md("INDEX.md", "# Index")
        self._make_md("MEMORY.md", "# Memory")
        self._make_md("README.md", "# Readme")
        self._make_md("old.migrate-bak", "# Old")
        self._set_mtime("alpha", 2026, 7, 1)
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": []},
        ])
        result = self._run("display", "--view", "timeline")
        self.assertNotIn("INDEX", result.stdout)
        self.assertNotIn("MEMORY", result.stdout)
        self.assertNotIn("README", result.stdout)
        self.assertNotIn("migrate-bak", result.stdout)

    def test_timeline_date_format(self):
        self._setup_two_months()
        result = self._run("display", "--view", "timeline")
        self.assertIn("6月30日", result.stdout)
        self.assertIn("7月1日", result.stdout)

    def test_timeline_excludes_slug(self):
        self._setup_two_months()
        result = self._run("display", "--view", "timeline", "--exclude", "beta")
        self.assertNotIn("beta", result.stdout)

    def test_timeline_single_month(self):
        self._make_md("alpha", "# Alpha\n\nContent.")
        self._set_mtime("alpha", 2026, 7, 1)
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": []},
        ])
        result = self._run("display", "--view", "timeline")
        self.assertIn("section 2026-07", result.stdout)

    def test_timeline_no_mermaid_flag(self):
        self._setup_two_months()
        result = self._run("display", "--view", "timeline", "--no-mermaid")
        self.assertNotIn("```mermaid", result.stdout)
        self.assertIn("| 月份 | 当月活跃记忆数 | 记忆列表 |", result.stdout)

    def test_timeline_empty(self):
        result = self._run("display", "--view", "timeline")
        self.assertEqual(result.returncode, 0)
        self.assertIn("暂无时间线数据", result.stdout)


class TestDisplayUsage(TestDisplayCLIBase):
    def _setup_nodes(self):
        self._make_md("alpha", "# Alpha\n\nContent.")
        self._make_md("beta", "# Beta\n\nContent.")
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": ["beta"]},
            {"name": "beta", "description": "Beta memory for testing.", "read_when": ["beta topic"], "references": []},
        ])

    def _make_hotlist_file(self, content):
        # 在 _MEMORY_SYNC_TEST_DIR 下写 mock 热榜文件(测试模式重定向目标)
        path = os.path.join(self.mem_dir, "CLAUDE.md")
        with open(path, "w") as f:
            f.write(content)

    def test_usage_bar_chart(self):
        self._setup_nodes()
        result = self._run("display", "--view", "usage")
        self.assertEqual(result.returncode, 0)
        self.assertIn("xychart-beta", result.stdout)
        self.assertIn("bar [", result.stdout)
        # alpha references beta → beta in_degree=1 (score 2.0) > alpha (score 0.5),
        # so beta sorts first in top10. Assert the actual correct order.
        self.assertIn('x-axis ["beta", "alpha"]', result.stdout)

    def test_usage_reads_real_hotlist(self):
        # mock 热榜文件:含 2 条 slug 热榜行 → usage 输出应含这 2 行
        self._setup_nodes()
        self._make_hotlist_file(
            "<!-- memory-index:start -->\n"
            "- [alpha](alpha.md) — Alpha description here.\n"
            "- [beta](beta.md) — Beta description here.\n"
            "<!-- memory-index:end -->\n"
        )
        result = self._run("display", "--view", "usage")
        self.assertIn("Alpha description here.", result.stdout)
        self.assertIn("Beta description here.", result.stdout)

    def test_usage_hotlist_excludes_slug(self):
        self._setup_nodes()
        self._make_hotlist_file(
            "<!-- memory-index:start -->\n"
            "- [alpha](alpha.md) — Alpha description here.\n"
            "- [beta](beta.md) — Beta description here.\n"
            "<!-- memory-index:end -->\n"
        )
        result = self._run("display", "--view", "usage", "--exclude", "alpha")
        self.assertIn("Beta description here.", result.stdout)
        self.assertNotIn("Alpha description here.", result.stdout)

    def test_usage_hotlist_no_markers(self):
        self._setup_nodes()
        self._make_hotlist_file("no markers here\n")
        result = self._run("display", "--view", "usage")
        self.assertEqual(result.returncode, 0)
        self.assertIn("未找到 memory-index 热榜块", result.stdout)

    def test_usage_demo_script_present(self):
        self._setup_nodes()
        result = self._run("display", "--view", "usage")
        self.assertIn("演示脚本(可照着跑)", result.stdout)
        self.assertIn("$SM display --view stats", result.stdout)

    def test_usage_no_mermaid_flag(self):
        self._setup_nodes()
        result = self._run("display", "--view", "usage", "--no-mermaid")
        self.assertNotIn("```mermaid", result.stdout)
        self.assertIn("| 排名 | 记忆 | 分数 |", result.stdout)
