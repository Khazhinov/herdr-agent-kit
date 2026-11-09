#!/usr/bin/env python3
import importlib, io, json, os, sys, tempfile, time, unittest
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
                           "HERDR_AGENT_KIT_REGISTRY": str(self.reg),
                           "HERDR_AGENT_KIT_CACHE": str(self.td / "cache"),
                           "CLAUDE_CONFIG_DIR": str(self.td),
                           "FAKE_HC_ANSWER": "DONE", "FAKE_HC_STATUS": "idle", "FAKE_TOKEN_SRC": "t0ken"})
        os.environ["HERDR_AGENT_KIT_GRACE"] = "0"
        for k in ("HC_TERMINAL_ID", "FAKE_HC_CALLER", "FAKE_HC_CALLER_SPACE", "FAKE_HC_SILENT", "FAKE_HC_DIE"):
            os.environ.pop(k, None)
        import tool_ask
        self.t = importlib.reload(tool_ask)

    def run_cli(self, *a):
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            rc = self.t.main(list(a))
        return rc, o.getvalue(), e.getvalue()

    def calls(self):
        return json.loads((self.td / "hc.json").read_text())["calls"]

    def state(self):
        return json.loads((self.td / "hc.json").read_text())


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
        self.assertEqual(run[-4:], ["codex", "--no-daemon", "-m", "cheap-model"])
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

    def test_start_waits_for_loading_worker(self):
        os.environ["FAKE_HC_STATUS"] = "working"
        self.t.start("tool-github", "github", str(self.wd), self.t.load("github")[1]["command"])
        self.assertIn("wait", [c[0] for c in self.calls()])
        os.environ["FAKE_HC_STATUS"] = "idle"

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


class Placement(Base):
    """Every worker is shown in Herdr App: caller's space, or its own folder's space."""
    def labels(self):
        return self.state()["terms"]["tool-github"].get("labels", {})

    def test_caller_space(self):
        os.environ.update({"HC_TERMINAL_ID": "t9", "FAKE_HC_CALLER": "t9", "FAKE_HC_CALLER_SPACE": "spCALLER"})
        self.assertEqual(self.run_cli("ask", "github", "x", "--id", "c1")[0], 0)
        self.assertEqual(self.labels()["herdr.space"], "spCALLER")

    def test_module_space_by_live_folder(self):
        # the App names a space after the live folder of its tab; `folder` may be stale
        st = {"terms": {"user": {"address": "t7", "name": "user", "cwd": "/elsewhere", "state": "live",
                                 "foreground": {"cwd": str(self.wd / "sub")}}}, "calls": [],
              "doc": {"version": 1, "value": {"spaces": [
                  {"id": "spSTALE", "folder": str(self.wd), "tabs": []},
                  {"id": "spLIVE", "folder": "/elsewhere", "tabs": [{"root": {"children": [
                      {"type": "block", "content": {"terminal": "t7"}}]}}]}]}}}
        (self.td / "hc.json").write_text(json.dumps(st))
        self.assertEqual(self.run_cli("ask", "github", "x", "--space", "module", "--id", "l1")[0], 0)
        self.assertEqual(self.labels()["herdr.space"], "spLIVE")
        self.assertEqual(len(self.state()["doc"]["value"]["spaces"]), 2)

    def test_module_space_found_then_created(self):
        st = {"terms": {}, "calls": [], "doc": {"version": 3, "value": {"spaces": [
            {"id": "spOWN", "name": "", "folder": str(self.wd), "tabs": []}]}}}
        (self.td / "hc.json").write_text(json.dumps(st))
        self.assertEqual(self.run_cli("ask", "github", "x", "--space", "module", "--id", "m1")[0], 0)
        self.assertEqual(self.labels()["herdr.space"], "spOWN")
        self.run_cli("stop", "github")
        st = self.state()
        st["doc"]["value"]["spaces"] = []
        (self.td / "hc.json").write_text(json.dumps(st))
        self.assertEqual(self.run_cli("ask", "github", "x", "--id", "m2")[0], 0)  # no caller space: own space
        space = self.state()["doc"]["value"]["spaces"][0]
        self.assertEqual(space["folder"], os.path.realpath(self.wd))
        self.assertEqual(space["tabs"][0]["root"]["children"][0]["content"]["terminal"], "t1")
        self.assertEqual(self.labels()["herdr.space"], space["id"])


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
        out = self.run_cli("refresh", "--all")[1]
        self.assertIn("docs: error: module docs:", out)  # a missing manifest does not stop the others
        self.assertIn("unavailable: no token", (self.td / "local/cards/github.md").read_text())

    def test_tools_filter(self):
        self.reg.write_text(self.reg.read_text().replace('mcp = ["github"]', 'mcp = ["github"]\ntools = ["github:get_*"]'))
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
        (self.td / ".claude.json").write_text(json.dumps({"projects": {str(self.wd.resolve()): {"mcpServers": {
            "tg": {"command": "uv", "args": ["run", "main.py"]}}}}}))
        self.assertEqual(mcp_probe.detect(self.wd), {"claude": ["github", "tg"]})

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
        self.assertIn("| `github` | mcp | GitHub repositories, issues, pull requests, Actions runs | "
                      "`.agent-tools/github.md` | - |", tools)
        details = (self.pr / ".agent-tools/github.md").read_text()
        self.assertIn("github (2 tools): `close_issue`*, `get_issue`", details)
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
        self.assertNotIn("new_tool", (self.pr / ".agent-tools/github.md").read_text())
        self.assertIn("changed", self.run_cli("sync")[1])
        self.assertIn("new_tool", (self.pr / ".agent-tools/github.md").read_text())
        self.run_cli("unlink", str(self.pr))
        self.assertFalse((self.pr / ".agent-tools/TOOLS.md").exists())
        self.assertFalse((self.pr / ".agent-tools/github.md").exists())
        self.assertEqual(json.loads((self.td / "local/links.json").read_text()), {})

    def test_old_pointer_is_replaced(self):
        (self.pr / "AGENTS.md").write_text("# P\n\n" + self.t.OLD_POINTERS[0] + "\n")
        self.run_cli("link", str(self.pr), "github")
        text = (self.pr / "AGENTS.md").read_text()
        self.assertNotIn(self.t.OLD_POINTERS[0], text)
        self.assertEqual(text.count(self.t.POINTER), 1)

    def test_scan(self):
        out = self.run_cli("scan", str(self.td))[1]
        self.assertIn(f"{self.wd}  [registered as github]  claude: github", out)


class Service(Base):
    def setUp(self):
        super().setUp()
        self.sd = self.td / "docs"
        self.sd.mkdir()
        (self.sd / "agent-service.toml").write_text((HUB / "examples/service-docs/agent-service.toml").read_text())
        self.reg.write_text(self.reg.read_text().replace("~/tool-agents/docs", str(self.sd)))
        os.environ.pop("FAKE_HC_DIE", None)

    def run_of(self):
        return next(c for c in self.calls() if c[0] == "run")

    def test_card_has_signatures(self):
        self.assertEqual(self.run_cli("refresh", "docs")[0], 0)
        card = self.run_cli("card", "docs", "draft")[1]
        self.assertIn("- `draft(task: string, source: path, section?: string)` - Draft", card)
        self.assertIn("Profiles: writer > default.", card)
        self.assertIn("  - `source`: input file", card)
        self.assertIn("Profiles: reviewer > writer > default.", self.run_cli("card", "docs", "review")[1])
        self.assertIn("[service, card]", self.run_cli("list")[1])
        self.assertIn("- `publish(task: string)` [write]", self.run_cli("snippet", "docs")[1])

    def test_call_uses_profile_and_absolute_paths(self):
        rc, out, err = self.run_cli("call", "docs", "draft", "--arg", "task=t1", "--arg", "source=in.md", "--id", "s1")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.strip().endswith("docs/s1/answer.md"))
        run = self.run_of()
        self.assertIn("tool-docs-writer", run)
        self.assertEqual(run[-5:], ["claude", "--model", "sonnet", "--effort", "high"])
        req = [c for c in self.calls() if c[0] == "send"][1][-2]
        self.assertIn(f"- source = {Path('in.md').resolve()}", req)
        self.assertIn("only at: */drafts/**", req)
        self.assertIn("External systems: read-only", req)

    def test_codex_effort_and_claude_fallback_model(self):
        self.reg.write_text(self.reg.read_text() + '[modules.docs.profiles.reviewer]\nmodel = "m1"\n')
        self.assertEqual(self.run_cli("call", "docs", "review", "--arg", "document=d.md", "--id", "r1")[0], 0)
        self.assertEqual(self.run_of()[-6:], ["codex", "--no-daemon", "-m", "m1", "-c", 'model_reasoning_effort="medium"'])
        argv = self.t.command({"runtime": "claude", "model": "opus"}, [{"model": "sonnet"}, {"runtime": "codex", "model": "x"}])
        self.assertEqual(argv, ["claude", "--model", "opus", "--fallback-model", "sonnet"])

    def test_default_profile_and_opencode_effort(self):
        self.assertEqual(self.run_cli("call", "docs", "draft", "--arg", "task=t", "--arg", "source=x",
                                      "--profile", "default", "--id", "d1")[0], 0)
        self.assertEqual(self.run_of()[-1], "claude")
        self.assertEqual(self.t.command({"runtime": "opencode", "model": "p/m"}), ["opencode", "-m", "p/m"])
        self.reg.write_text(self.reg.read_text() + '[modules.docs.profiles.writer]\nruntime = "opencode"\n')
        with self.assertRaises(SystemExit) as e:
            self.run_cli("refresh", "docs")
        self.assertIn("no effort flag", str(e.exception))

    def test_bad_arguments(self):
        for args in (["--arg", "task=t"], ["--arg", "task=t", "--arg", "source=x", "--arg", "nope=1"]):
            with self.assertRaises(SystemExit) as e:
                self.run_cli("call", "docs", "draft", *args)
            self.assertIn("signature: draft(task: string", str(e.exception))
        with self.assertRaises(SystemExit):
            self.run_cli("ask", "docs", "x")

    def test_external_function_needs_write(self):
        self.assertEqual(self.run_cli("call", "docs", "publish", "--arg", "task=t")[0], 5)
        self.assertEqual(self.run_cli("call", "docs", "publish", "--arg", "task=t", "--write")[0], 5)

    def test_profile_failure_lists_fallbacks(self):
        os.environ["FAKE_HC_DIE"] = "1"
        rc, _, err = self.run_cli("call", "docs", "review", "--arg", "document=d", "--id", "p1")
        os.environ.pop("FAKE_HC_DIE")
        self.assertEqual(rc, 6)
        self.assertIn("retry with: --profile writer, --profile default", err)

    def test_async_then_wait_and_result(self):
        os.environ["FAKE_HC_ANSWER"] = ""
        rc, out, _ = self.run_cli("call", "docs", "draft", "--arg", "task=t", "--arg", "source=x", "--async", "--id", "a1")
        self.assertEqual((rc, out.strip()), (0, "a1"))
        self.assertEqual(self.run_cli("result", "docs", "a1")[0], 3)  # fresh task, no answer yet
        (self.td / "cache/docs/a1/answer.md").write_text("v03 ready\nDONE\n")
        rc, out, _ = self.run_cli("wait", "docs", "a1")
        self.assertEqual(rc, 0)
        self.assertTrue(out.strip().endswith("docs/a1/answer.md"))
        self.assertEqual(self.run_cli("result", "docs", "a1")[0], 0)

    def test_silent_turn_is_reported_with_screen(self):
        os.environ["FAKE_HC_SILENT"] = "1"
        rc, _, err = self.run_cli("call", "docs", "draft", "--arg", "task=t", "--arg", "source=x", "--id", "q1")
        self.assertEqual(rc, 6)
        self.assertIn("Which task do you mean?", err)
        rc, _, err = self.run_cli("ask", "github", "x", "--id", "q2")
        self.assertEqual(rc, 1)
        self.assertIn("without a DONE/FAILED line", err)

    def test_async_watcher_is_child_of_caller(self):
        os.environ.update({"HC_TERMINAL_ID": "t9", "FAKE_HC_ANSWER": ""})
        rc, out, err = self.run_cli("call", "docs", "draft", "--arg", "task=t", "--arg", "source=x", "--async", "--id", "w1")
        self.assertEqual(rc, 0)
        run = [c for c in self.calls() if c[0] == "run" and "w-docs-w1" in c][0]
        self.assertEqual(run[run.index("--parent") + 1], "t9")
        self.assertEqual(run[-3:], ["wait", "docs", "w1"])
        self.assertIn("herdr notes you", err)

    def test_manifest_change_reaches_linked_projects(self):
        pr = self.td / "proj"
        pr.mkdir()
        self.run_cli("link", str(pr), "docs")
        self.assertIn("draft(task: string", (pr / ".agent-tools/docs.md").read_text())
        man = self.sd / "agent-service.toml"
        man.write_text(man.read_text() + '\n[functions.summary]\ndescription = "Short summary"\n')
        os.utime(man, (time.time() + 5, time.time() + 5))
        rc, _, err = self.run_cli("call", "docs", "review", "--arg", "document=d", "--id", "n1")
        self.assertEqual(rc, 0)
        self.assertIn("manifest changed (", err)
        self.assertIn("added `summary`", err)
        details = (pr / ".agent-tools/docs.md").read_text()
        self.assertIn("summary()", details)
        self.assertIn("## Recent changes", details)
        self.assertIn("added `summary`", (pr / ".agent-tools/TOOLS.md").read_text())

    def test_scan_finds_manifest(self):
        self.assertIn(f"{self.sd}  [registered as docs]  service: agent-service.toml", self.run_cli("scan", str(self.td))[1])


class PublicFiles(unittest.TestCase):
    def test_examples_are_read_only(self):
        for p in (HUB / "examples").glob("*/.mcp.example.json"):
            t = p.read_text()
            self.assertTrue("readonly" in t or "--read-only" in t, p)


if __name__ == "__main__":
    unittest.main(verbosity=1)
