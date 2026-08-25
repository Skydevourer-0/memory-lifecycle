# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import tempfile
import unittest

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLAUDE_INSTALL = os.path.join(SKILL_ROOT, ".claude", "install.py")
CODEX_INSTALL = os.path.join(SKILL_ROOT, ".codex", "install.py")
COMPAT_INSTALL = os.path.join(SKILL_ROOT, "scripts", "install.py")


def _env(home=None, codex_home=None):
    env = os.environ.copy()
    env.pop("_MEMORY_SYNC_TEST_DIR", None)
    if home is not None:
        env["HOME"] = home
        env["USERPROFILE"] = home
    if codex_home is not None:
        env["CODEX_HOME"] = codex_home
    else:
        env.pop("CODEX_HOME", None)
    return env


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _all_commands(hooks_data, event):
    return [h.get("command", "") for block in hooks_data.get(event, []) for h in block.get("hooks", [])]


class TestClaudeInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.settings_path = os.path.join(self.home, ".claude", "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _install(self):
        return subprocess.run(
            [sys.executable, CLAUDE_INSTALL],
            capture_output=True, text=True, encoding="utf-8", env=_env(home=self.home),
        )

    def _load_settings(self):
        with open(self.settings_path, encoding="utf-8") as f:
            return json.load(f)

    def _seed_settings(self):
        _write(self.settings_path, json.dumps({
            "env": {"TEST": "1"},
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Write|Edit|MultiEdit",
                        "pathPattern": "**/.claude/**/memory/*.md",
                        "hooks": [
                            {"type": "command", "command": '"py" "C:/skills/memory-lifecycle/scripts/memory-sync.py" sync'},
                            {"type": "command", "command": '"py" "C:/skills/memory-lifecycle/scripts/memory-sync.py" hint'},
                            {"type": "command", "command": '"py" "other-skill" scan'},
                        ],
                    }
                ],
                "SessionStart": [
                    {"matcher": "startup|clear|compact",
                     "hooks": [{"type": "command", "command": '"py" "checkpoint" list --hook'}]}
                ],
            },
            "autoMemoryEnabled": True,
        }, indent=2))

    def test_cleans_old_hooks_and_registers_new(self):
        self._seed_settings()
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stderr)
        settings = self._load_settings()
        post_cmds = _all_commands(settings["hooks"], "PostToolUse")
        self.assertFalse(any("memory-sync.py" in c and (c.rstrip().endswith(" sync") or c.rstrip().endswith(" hint")) for c in post_cmds))
        self.assertTrue(any("other-skill" in c for c in post_cmds), "其它技能 hook 必须保留")
        self.assertTrue(any("sync-and-hint" in c for c in post_cmds))
        # 本技能块无 pathPattern
        for block in settings["hooks"]["PostToolUse"]:
            if any("memory-sync.py" in h.get("command", "") for h in block.get("hooks", [])):
                self.assertNotIn("pathPattern", block)
        session_cmds = _all_commands(settings["hooks"], "SessionStart")
        self.assertTrue(any("session-start" in c for c in session_cmds))
        self.assertTrue(any("checkpoint" in c for c in session_cmds), "checkpoint 块必须保留")
        self.assertTrue(settings["autoMemoryEnabled"], "autoMemoryEnabled 不得改动")
        self.assertEqual(settings["env"]["TEST"], "1")

    def test_idempotent(self):
        self._seed_settings()
        self._install()
        first = self._load_settings()
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stderr)
        second = self._load_settings()
        self.assertEqual(first, second)

    def test_creates_settings_and_dirs_when_missing(self):
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(self.settings_path))
        settings = self._load_settings()
        self.assertTrue(any("sync-and-hint" in c for c in _all_commands(settings["hooks"], "PostToolUse")))
        self.assertTrue(any("session-start" in c for c in _all_commands(settings["hooks"], "SessionStart")))
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".cc-switch", "memory", "global")))
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".cc-switch", "memory", "projects")))

    def test_commands_unquoted_when_no_spaces(self):
        # Windows cmd/CreateProcess fails when the executable name is quoted
        # (Codex hooks). Paths without spaces must stay unquoted.
        if " " not in sys.executable and " " not in SKILL_ROOT:
            self._install()
            settings = self._load_settings()
            for cmd in _all_commands(settings["hooks"], "PostToolUse") + _all_commands(settings["hooks"], "SessionStart"):
                self.assertFalse(cmd.startswith('"'), f"command must not start with a quote: {cmd}")


class TestCodexInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.codex_home = os.path.join(self.tmp.name, "codex-home")
        os.makedirs(self.codex_home)

    def tearDown(self):
        self.tmp.cleanup()

    def _install(self):
        return subprocess.run(
            [sys.executable, CODEX_INSTALL],
            capture_output=True, text=True, encoding="utf-8",
            env=_env(home=self.home, codex_home=self.codex_home),
        )

    def _load_hooks(self):
        with open(os.path.join(self.codex_home, "hooks.json"), encoding="utf-8") as f:
            return json.load(f)

    def _seed_hooks(self):
        _write(os.path.join(self.codex_home, "hooks.json"), json.dumps({
            "hooks": {
                "PostToolUse": [
                    {"matcher": "apply_patch",
                     "hooks": [
                         {"type": "command", "command": '"py" "C:/skills/memory-lifecycle/scripts/memory-sync.py" sync'},
                         {"type": "command", "command": '"py" "other" x'},
                     ]}
                ]
            }
        }, indent=2))

    def test_merges_and_cleans_old(self):
        self._seed_hooks()
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self._load_hooks()
        post_cmds = _all_commands(data["hooks"], "PostToolUse")
        self.assertFalse(any("memory-sync.py" in c and c.rstrip().endswith(" sync") for c in post_cmds))
        self.assertTrue(any("other" in c and "mem-script" not in c for c in post_cmds), "其它 hook 必须保留")
        self.assertTrue(any("sync-and-hint" in c for c in post_cmds))
        session_cmds = _all_commands(data["hooks"], "SessionStart")
        self.assertTrue(any("session-start" in c for c in session_cmds))
        matchers = [block.get("matcher") for block in data["hooks"]["SessionStart"]]
        self.assertIn("startup|resume|clear|compact", matchers)

    def test_idempotent(self):
        self._seed_hooks()
        self._install()
        first = self._load_hooks()
        self._install()
        second = self._load_hooks()
        self.assertEqual(first, second)

    def test_creates_hooks_json_when_missing(self):
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self._load_hooks()
        self.assertIn("hooks", data)
        self.assertTrue(any("sync-and-hint" in c for c in _all_commands(data["hooks"], "PostToolUse")))
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".cc-switch", "memory", "global")))

    def test_warns_on_inline_hooks_in_config_toml(self):
        _write(os.path.join(self.codex_home, "config.toml"), 'model = "x"\n\n[hooks]\nfoo = "bar"\n')
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("config.toml", r.stdout)
        self.assertIn("WARNING", r.stdout)

    def test_trust_guidance_printed(self):
        r = self._install()
        self.assertIn("/hooks", r.stdout)


class TestCompatInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.codex_home = os.path.join(self.tmp.name, "codex-home")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, codex_home=None):
        return subprocess.run(
            [sys.executable, COMPAT_INSTALL],
            capture_output=True, text=True, encoding="utf-8",
            env=_env(home=self.home, codex_home=codex_home),
        )

    def test_forwards_to_claude_when_settings_exists(self):
        _write(os.path.join(self.home, ".claude", "settings.json"), json.dumps({"hooks": {}}))
        r = self._run(codex_home=self.codex_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.home, ".claude", "settings.json"), encoding="utf-8") as f:
            settings = json.load(f)
        self.assertIn("PostToolUse", settings["hooks"])
        # Codex 端未被触碰(无 CODEX_HOME 下的 hooks.json)
        self.assertFalse(os.path.exists(os.path.join(self.codex_home, "hooks.json")))

    def test_forwards_to_codex_when_only_codex_home(self):
        os.makedirs(self.codex_home)
        r = self._run(codex_home=self.codex_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.codex_home, "hooks.json")))

    def test_usage_when_no_target(self):
        r = self._run(codex_home=None)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)

    def test_explicit_flags(self):
        os.makedirs(self.codex_home)
        r = subprocess.run(
            [sys.executable, COMPAT_INSTALL, "--codex"],
            capture_output=True, text=True, encoding="utf-8",
            env=_env(home=self.home, codex_home=self.codex_home),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.codex_home, "hooks.json")))

