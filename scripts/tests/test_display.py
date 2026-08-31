import datetime
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
            capture_output=True, text=True, encoding="utf-8", env=env, input=stdin_input,
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

    def _make_hotlist_file(self, content):
        # 在 _MEMORY_SYNC_TEST_DIR 下写 mock 热榜文件(测试模式重定向目标)
        path = os.path.join(self.mem_dir, "CLAUDE.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


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
        self.assertLess(result.stdout.index('alpha["alpha"]'), result.stdout.index('beta["beta"]'))

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

    def test_usage_bar_chart(self):
        self._setup_nodes()
        result = self._run("display", "--view", "usage")
        self.assertEqual(result.returncode, 0)
        self.assertIn("xychart-beta", result.stdout)
        self.assertIn("bar [", result.stdout)
        # alpha references beta → beta in_degree=1 (score 2.0) > alpha (score 0.5),
        # so beta sorts first in top10. Assert the actual correct order.
        self.assertIn('x-axis ["alpha", "beta"]', result.stdout)

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

    def test_usage_empty(self):
        # spec §5.2: empty memory dir runs usage view, returncode 0, empty placeholder.
        result = self._run("display", "--view", "usage")
        self.assertEqual(result.returncode, 0)
        self.assertIn("记忆库为空", result.stdout)


class TestDisplayIntegration(TestDisplayCLIBase):
    def _setup_full(self):
        for slug in ("alpha", "beta", "gamma"):
            self._make_md(slug, f"# {slug.title()}\n\nContent.")
        self._setup_metadata([
            {"name": "alpha", "description": "Alpha memory for testing.", "read_when": ["alpha topic"], "references": ["beta"]},
            {"name": "beta", "description": "Beta memory for testing.", "read_when": ["beta topic"], "references": ["gamma"]},
            {"name": "gamma", "description": "Gamma memory for testing.", "read_when": ["gamma topic"], "references": []},
        ])
        # mtime 分布:alpha 6月30,beta 7月1,gamma 7月3
        for slug, (y, m, d) in {"alpha": (2026, 6, 30), "beta": (2026, 7, 1), "gamma": (2026, 7, 3)}.items():
            os.utime(os.path.join(self.mem_dir, f"{slug}.md"),
                     (datetime.datetime(y, m, d, 12, 0, tzinfo=datetime.timezone.utc).timestamp(),) * 2)

    def test_view_all_order(self):
        self._setup_full()
        result = self._run("display", "--view", "all")
        self.assertEqual(result.returncode, 0)
        idx_g = result.stdout.index("## 知识图谱")
        idx_s = result.stdout.index("## 全景统计")
        idx_t = result.stdout.index("## 积累时间线")
        idx_u = result.stdout.index("## 使用效果流")
        self.assertTrue(idx_g < idx_s < idx_t < idx_u)

    def test_out_file_writes(self):
        self._setup_full()
        out_path = os.path.join(self.tmp.name, "out", "display.md")
        result = self._run("display", "--view", "graph", "--out", out_path)
        self.assertEqual(result.returncode, 0)
        with open(out_path) as f:
            content = f.read()
        self.assertIn("graph LR", content)

    def test_out_file_unwritable(self):
        self._setup_full()
        # 父路径是文件而非目录 → os.makedirs 失败 → exit 2 + ERROR on stderr
        # (Windows 上 os.chmod 只读属性不可靠,不采用只读目录模拟)
        blocker = os.path.join(self.tmp.name, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("not a dir")
        result = self._run("display", "--view", "graph", "--out", os.path.join(blocker, "x.md"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("ERROR", result.stderr)

    def test_scope_global_explicit(self):
        self._setup_full()
        result = self._run("display", "--view", "graph", "--scope", "global")
        self.assertEqual(result.returncode, 0)

    def test_scope_project_no_git_fallback(self):
        self._setup_full()
        result = self._run("display", "--view", "graph", "--scope", "project")
        # 测试目录无 .git → get_memory_dir fallback 到 global;不报错
        # _MEMORY_SYNC_TEST_DIR 优先,mem_dir 为测试目录,returncode 0
        self.assertEqual(result.returncode, 0)

    def test_scope_explicit_overrides_auto(self):
        # `--scope global` 显式传参必须覆盖 CWD 自动检测。
        # 方案:临时 HOME 种入全局库,在带 .git 的项目目录下运行
        # 且不设 _MEMORY_SYNC_TEST_DIR,用 `--scope global` 覆盖 → 读临时全局库。
        project_tmp = tempfile.TemporaryDirectory()
        home_tmp = tempfile.TemporaryDirectory()
        try:
            project_dir = project_tmp.name
            os.makedirs(os.path.join(project_dir, ".git"))
            global_dir = os.path.join(home_tmp.name, ".cc-switch", "memory", "global")
            os.makedirs(global_dir)
            with open(os.path.join(global_dir, "seed-topic.md"), "w", encoding="utf-8") as f:
                f.write("# Seed\n\nContent.")
            with open(os.path.join(global_dir, "metadata.jsonl"), "w", encoding="utf-8") as f:
                json.dump({"name": "seed-topic", "description": "Seed memory for scope test.",
                           "read_when": ["seed topic"], "references": []}, f, ensure_ascii=False)
                f.write("\n")
            env = os.environ.copy()
            env.pop("_MEMORY_SYNC_TEST_DIR", None)
            env["HOME"] = home_tmp.name
            env["USERPROFILE"] = home_tmp.name
            proc = subprocess.run(
                [sys.executable, MEMORY_SYNC, "display", "--scope", "global", "--view", "stats"],
                capture_output=True, text=True, encoding="utf-8", env=env, cwd=project_dir,
            )
            self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
            self.assertIn("## 全景统计", proc.stdout)
            import re
            m = re.search(r"\| 记忆总数 \| (\d+) \|", proc.stdout)
            self.assertIsNotNone(m, f"记忆总数行未找到: {proc.stdout}")
            self.assertGreater(int(m.group(1)), 0, f"--scope global 未读到临时全局库: {proc.stdout}")
        finally:
            project_tmp.cleanup()
            home_tmp.cleanup()

    def test_empty_memory_dir(self):
        # 只有空 memory 目录,无 .md 无 jsonl
        result = self._run("display", "--view", "all")
        self.assertEqual(result.returncode, 0)
        self.assertIn("记忆库为空", result.stdout)

    def test_all_excluded_empty(self):
        self._setup_full()
        result = self._run("display", "--view", "graph", "--exclude", "alpha,beta,gamma")
        self.assertEqual(result.returncode, 0)
        self.assertIn("记忆库为空", result.stdout)

    def test_stale_metadata_warning(self):
        # metadata 有条目但 .md 已删
        self._setup_metadata([
            {"name": "ghost", "description": "Ghost memory for testing.", "read_when": ["ghost topic"], "references": []},
        ])
        result = self._run("display", "--view", "graph")
        self.assertEqual(result.returncode, 0)
        self.assertIn("WARNING", result.stderr)

    def test_readonly_no_side_effects(self):
        self._setup_full()
        jsonl_before = os.path.getmtime(self.jsonl_path)
        idx_path = os.path.join(self.mem_dir, "INDEX.md")
        if os.path.exists(idx_path):
            idx_before = os.path.getmtime(idx_path)
        else:
            idx_before = None
        self._run("display", "--view", "all")
        self.assertEqual(os.path.getmtime(self.jsonl_path), jsonl_before)
        if idx_before is not None:
            self.assertEqual(os.path.getmtime(idx_path), idx_before)

    def test_no_hook_trigger(self):
        self._setup_full()
        result = self._run("display", "--view", "graph")
        # 不输出 additionalContext JSON(与 hint 区分)
        self.assertNotIn("hookSpecificOutput", result.stdout)

    def test_test_mode_redirects_hotlist(self):
        # 验证 _resolve_hot_target_for_read 重定向逻辑本身
        self._setup_full()
        self._make_hotlist_file(
            "<!-- memory-index:start -->\n"
            "- [alpha](alpha.md) — Alpha description here.\n"
            "<!-- memory-index:end -->\n"
        )
        result = self._run("display", "--view", "usage")
        self.assertIn("Alpha description here.", result.stdout)
        self.assertNotIn("真实~/.claude/CLAUDE.md", result.stdout)  # 未访问真实文件


class TestDisplayDualRead(unittest.TestCase):
    """usage 视图全局热榜双读:CLAUDE.md + AGENTS.md 都读取并标注来源。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.global_dir = os.path.join(self.home, ".cc-switch", "memory", "global")
        os.makedirs(self.global_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_memory(self):
        with open(os.path.join(self.global_dir, "alpha.md"), "w", encoding="utf-8") as f:
            f.write("# Alpha\n\nContent.")
        with open(os.path.join(self.global_dir, "metadata.jsonl"), "w", encoding="utf-8") as f:
            json.dump({"name": "alpha", "description": "Alpha memory for testing.",
                       "read_when": ["alpha topic"], "references": []}, f, ensure_ascii=False)
            f.write("\n")

    def _write_marker_file(self, rel, entry_desc):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("<!-- memory-index:start -->\n")
            f.write(f"- [alpha](alpha.md) — {entry_desc}\n")
            f.write("<!-- memory-index:end -->\n")

    def test_usage_dual_read_annotates_sources(self):
        self._seed_memory()
        self._write_marker_file(os.path.join(".claude", "CLAUDE.md"), "Claude-side description.")
        self._write_marker_file(os.path.join(".codex", "AGENTS.md"), "Codex-side description.")
        env = os.environ.copy()
        env.pop("_MEMORY_SYNC_TEST_DIR", None)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        proc = subprocess.run(
            [sys.executable, MEMORY_SYNC, "display", "--view", "usage"],
            capture_output=True, text=True, encoding="utf-8", env=env, cwd=self.home,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("来源:", proc.stdout)
        self.assertIn("CLAUDE.md", proc.stdout)
        self.assertIn("AGENTS.md", proc.stdout)
        self.assertIn("Claude-side description.", proc.stdout)
        self.assertIn("Codex-side description.", proc.stdout)

    def test_usage_project_reads_hotlist(self):
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        slug = None
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import common as cm
        slug = cm.project_slug(repo)
        mem_dir = os.path.join(self.home, ".cc-switch", "memory", "projects", slug)
        os.makedirs(mem_dir)
        with open(os.path.join(mem_dir, "alpha.md"), "w", encoding="utf-8") as f:
            f.write("# Alpha\n\nContent.")
        with open(os.path.join(mem_dir, "metadata.jsonl"), "w", encoding="utf-8") as f:
            json.dump({"name": "alpha", "description": "Alpha memory for testing.",
                       "read_when": ["alpha topic"], "references": []}, f, ensure_ascii=False)
            f.write("\n")
        with open(os.path.join(mem_dir, "HOTLIST.md"), "w", encoding="utf-8") as f:
            f.write("- [alpha](alpha.md) — Project hotlist description.\n")
        env = os.environ.copy()
        env.pop("_MEMORY_SYNC_TEST_DIR", None)
        env["HOME"] = self.home
        env["USERPROFILE"] = self.home
        proc = subprocess.run(
            [sys.executable, MEMORY_SYNC, "display", "--view", "usage"],
            capture_output=True, text=True, encoding="utf-8", env=env, cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Project hotlist description.", proc.stdout)
        self.assertIn("HOTLIST.md", proc.stdout)
