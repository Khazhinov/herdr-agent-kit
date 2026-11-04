#!/usr/bin/env python3
import importlib, io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB))
import mcp_probe  # noqa: E402

FAKE = [sys.executable, str(HUB / "tests/fake_mcp.py")]


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.wd = self.td / "wd"
        self.wd.mkdir()
        (self.wd / ".mcp.json").write_text(json.dumps({"mcpServers": {"github": {
            "command": FAKE[0], "args": FAKE[1:], "env": {"FAKE_TOKEN": "${FAKE_TOKEN_SRC}"}}}}))
        (self.td / "local").mkdir()
        self.reg = self.td / "local/registry.toml"
        self.reg.write_text((HUB / "registry.example.toml").read_text().replace("~/tool-agents/github", str(self.wd)))
        os.environ.update({"ORCH_HC": f"{sys.executable} {HUB / 'tests/fake_hc.py'}",
                           "FAKE_HC_STATE": str(self.td / "hc.json"),
                           "HERDR_TOOL_AGENTS_REGISTRY": str(self.reg),
                           "HERDR_TOOL_AGENTS_CACHE": str(self.td / "cache"),
                           "FAKE_HC_ANSWER": "DONE", "FAKE_HC_STATUS": "idle", "FAKE_TOKEN_SRC": "t0ken"})
        import tool_ask
        self.t = importlib.reload(tool_ask)

    def run_cli(self, *a):
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            rc = self.t.main(list(a))
        return rc, o.getvalue(), e.getvalue()

    def calls(self):
        return json.loads((self.td / "hc.json").read_text())["calls"]


class Ask(Base):
    def test_ask_starts_worker_and_returns_answer(self):
        rc, out, err = self.run_cli("ask", "github", "List open PRs", "--id", "r1")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.strip().endswith("github/r1.md"))
        run = next(c for c in self.calls() if c[0] == "run")
        self.assertIn("--no-parent", run)
        self.assertEqual(run[-2:], ["--model", "haiku"])
        sends = [c for c in self.calls() if c[0] == "send"]
        self.assertEqual(sends[0][-2], "/clear")
        self.assertIn("Use only the MCP tools of: github.", sends[1][-2])  # rules travel with the request
        self.assertIn("Read-only: change nothing.", sends[1][-2])
        audit = (self.td / "cache/audit.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(audit[-1])["result"], "DONE")

    def test_codex_runtime_defaults(self):
        (self.wd / ".codex").mkdir()
        (self.wd / ".codex/config.toml").write_text(f'[mcp_servers.github]\ncommand = "{FAKE[0]}"\nargs = ["{FAKE[1]}"]\n')
        self.reg.write_text(self.reg.read_text().replace('runtime = "claude"', 'runtime = "codex"')
                            .replace('model = "haiku"', 'model = "cheap-model"'))
        self.assertEqual(self.run_cli("ask", "github", "x", "--id", "c1")[0], 0)
        run = next(c for c in self.calls() if c[0] == "run")
        self.assertEqual(run[-3:], ["codex", "-m", "cheap-model"])
        self.assertEqual([c for c in self.calls() if c[0] == "send"][0][-2], "/new")

    def test_extra_worker_rules(self):
        (self.td / "local/modules").mkdir()
        (self.td / "local/modules/github.md").write_text("Only repos of org X.")
        self.assertEqual(self.run_cli("ask", "github", "x", "--id", "e1")[0], 0)
        self.assertIn("Only repos of org X.", [c for c in self.calls() if c[0] == "send"][1][-2])

    def test_write_refused_without_allow_write(self):
        rc, _, err = self.run_cli("ask", "github", "Close issue 1", "--write")
        self.assertEqual(rc, 5)

    def test_write_allowed_marks_request(self):
        self.reg.write_text(self.reg.read_text().replace("allow_write = false", "allow_write = true"))
        rc, _, err = self.run_cli("ask", "github", "Comment on PR 2", "--write", "--id", "w1")
        self.assertEqual(rc, 0, err)
        self.assertIn("WRITE ALLOWED BY USER: exactly", [c for c in self.calls() if c[0] == "send"][1][-2])

    def test_busy_worker(self):
        os.environ["FAKE_HC_STATUS"] = "working"
        self.assertEqual(self.run_cli("ask", "github", "x")[0], 3)
        os.environ["FAKE_HC_STATUS"] = "idle"

    def test_failed_answer(self):
        os.environ["FAKE_HC_ANSWER"] = "FAILED: no access"
        self.assertEqual(self.run_cli("ask", "github", "x", "--id", "f1")[0], 1)

    def test_folder_without_mcp(self):
        (self.wd / ".mcp.json").unlink()
        with self.assertRaises(SystemExit) as e:
            self.run_cli("ask", "github", "x")
        self.assertIn("no MCP servers", str(e.exception))

    def test_unknown_module_and_missing_registry(self):
        with self.assertRaises(SystemExit):
            self.run_cli("ask", "nope", "x")
        self.reg.unlink()
        with self.assertRaises(SystemExit) as e:
            self.run_cli("list")
        self.assertIn("missing", str(e.exception))


class Cards(Base):
    def test_refresh_builds_card_without_secrets(self):
        rc, out, _ = self.run_cli("refresh", "github")
        self.assertEqual(rc, 0)
        card = (self.td / "local/cards/github.md").read_text()
        self.assertIn("### github (2 tools)", card)  # both pages of tools/list
        self.assertIn("- `get_issue(repo, number, fields?)` - Get one issue.", card)
        self.assertIn("- `close_issue(number)` [write] - Close an issue.", card)
        self.assertNotIn("t0ken", card)
        self.assertIn("[claude, card]", self.run_cli("list")[1])
        self.assertEqual(self.run_cli("card", "github", "close_issue")[1].strip(),
                         "- `close_issue(number)` [write] - Close an issue.")

    def test_broken_server_is_reported_not_fatal(self):
        os.environ["FAKE_TOKEN_SRC"] = "wrong"
        self.run_cli("refresh", "--all")
        self.assertIn("unavailable: no token", (self.td / "local/cards/github.md").read_text())

    def test_tools_filter(self):
        self.reg.write_text(self.reg.read_text() + 'tools = ["github:get_*"]\n')
        self.run_cli("refresh", "github")
        card = (self.td / "local/cards/github.md").read_text()
        self.assertIn("get_issue", card)
        self.assertNotIn("close_issue", card)

    def test_config_readers(self):
        d = self.td / "oc"
        d.mkdir()
        (d / "opencode.jsonc").write_text('{\n // comment\n "mcp": {"a": {"type": "local", "command": ["x", "--u=http://h"],'
                                          ' "environment": {"K": "v"}},\n "b": {"type": "remote", "url": "http://h/mcp"},'
                                          ' "c": {"type": "local", "command": ["y"], "enabled": false},}}')
        s = mcp_probe.servers(d, "opencode")
        self.assertEqual(s["a"], {"command": ["x", "--u=http://h"], "env": {"K": "v"}})
        self.assertEqual(s["b"]["url"], "http://h/mcp")
        self.assertNotIn("c", s)
        self.assertEqual(mcp_probe.detect(self.wd), {"claude": ["github"]})
        (d / "x.toml").write_text('[mcp_servers.t]\ncommand = "z"\nargs = ["1"]\n')
        self.assertEqual(mcp_probe.read_config(d / "x.toml"), {"t": {"command": ["z", "1"], "env": {}}})
        (d / "vs.json").write_text('{"servers": {"v": {"type": "http", "url": "http://h/v"}}}')
        self.assertEqual(mcp_probe.read_config(d / "vs.json")["v"]["url"], "http://h/v")

    def test_any_runtime_with_command(self):
        (self.wd / "agent.json").write_text((self.wd / ".mcp.json").read_text())
        (self.wd / ".mcp.json").unlink()
        self.reg.write_text(self.reg.read_text().replace('runtime = "claude"', 'runtime = "other"\n'
                            'command = ["other-agent", "--small"]\nreset = "/reset"\nmcp_config = "agent.json"'))
        self.assertEqual(self.run_cli("ask", "github", "x", "--id", "o1")[0], 0)
        self.assertEqual(next(c for c in self.calls() if c[0] == "run")[-2:], ["other-agent", "--small"])
        self.assertEqual([c for c in self.calls() if c[0] == "send"][0][-2], "/reset")
        self.run_cli("refresh", "github")
        self.assertIn("get_issue", (self.td / "local/cards/github.md").read_text())


class Projects(Base):
    def setUp(self):
        super().setUp()
        self.run_cli("refresh", "github")
        self.pr = self.td / "proj"
        (self.pr / ".git/info").mkdir(parents=True)
        (self.pr / "CLAUDE.md").write_text("# Project\n")

    def test_link_sync_unlink(self):
        rc, out, _ = self.run_cli("link", str(self.pr), "github")
        self.assertEqual(rc, 0)
        tools = (self.pr / ".agent-tools/TOOLS.md").read_text()
        self.assertIn("## `github` - GitHub", tools)
        self.assertIn("github (2 tools): `close_issue`*, `get_issue`", tools)
        self.assertIn("/.agent-tools/", (self.pr / ".git/info/exclude").read_text())
        for f in ("AGENTS.md", "CLAUDE.md"):
            text = (self.pr / f).read_text()
            self.assertIn(".agent-tools/TOOLS.md", text)
            self.assertNotIn(str(HUB), text)  # tracked files get no machine path
        self.run_cli("link", str(self.pr), "github")  # idempotent
        self.assertEqual((self.pr / "AGENTS.md").read_text().count(".agent-tools/TOOLS.md"), 1)
        self.assertIn("same", self.run_cli("sync")[1])
        (self.td / "local/cards/github.md").write_text("### github (1 tools)\n\n- `new_tool()` - New.\n")
        self.assertIn("changed", self.run_cli("sync", "--dry-run")[1])
        self.assertNotIn("new_tool", (self.pr / ".agent-tools/TOOLS.md").read_text())
        self.assertIn("changed", self.run_cli("sync")[1])
        self.assertIn("new_tool", (self.pr / ".agent-tools/TOOLS.md").read_text())
        self.run_cli("unlink", str(self.pr))
        self.assertFalse((self.pr / ".agent-tools/TOOLS.md").exists())
        self.assertEqual(json.loads((self.td / "local/links.json").read_text()), {})

    def test_scan(self):
        out = self.run_cli("scan", str(self.td))[1]
        self.assertIn(f"{self.wd}  [registered as github]  claude: github", out)


class PublicFiles(unittest.TestCase):
    def test_examples_are_read_only(self):
        for p in (HUB / "examples").glob("*/.mcp.example.json"):
            t = p.read_text()
            self.assertTrue("readonly" in t or "--read-only" in t, p)


if __name__ == "__main__":
    unittest.main(verbosity=1)
