#!/usr/bin/env python3
import io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]


class ToolAsk(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        wd = self.td / "wd"
        wd.mkdir()
        (wd / ".mcp.json").write_text("{}")
        self.reg = self.td / "registry.toml"
        self.reg.write_text((HUB / "registry.example.toml").read_text().replace("~/tool-agents/github", str(wd)))
        os.environ.update({"ORCH_HC": f"{sys.executable} {HUB / 'tests/fake_hc.py'}",
                           "FAKE_HC_STATE": str(self.td / "hc.json"),
                           "HERDR_TOOL_AGENTS_REGISTRY": str(self.reg),
                           "HERDR_TOOL_AGENTS_CACHE": str(self.td / "cache"),
                           "FAKE_HC_ANSWER": "DONE", "FAKE_HC_STATUS": "idle"})
        sys.path.insert(0, str(HUB))
        import importlib, tool_ask
        self.t = importlib.reload(tool_ask)

    def run_cli(self, *a):
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            rc = self.t.main(list(a))
        return rc, o.getvalue(), e.getvalue()

    def calls(self):
        return json.loads((self.td / "hc.json").read_text())["calls"]

    def test_ask_starts_worker_and_returns_answer(self):
        rc, out, err = self.run_cli("ask", "github", "List open PRs", "--id", "r1")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.strip().endswith("github/r1.md"))
        run = next(c for c in self.calls() if c[0] == "run")
        self.assertIn("--no-parent", run)
        self.assertIn("orch.tool=github", run)
        self.assertIn("Use only the MCP tools of: github.", run[run.index("--append-system-prompt") + 1])
        sends = [c for c in self.calls() if c[0] == "send"]
        self.assertEqual(sends[0][-2], "/clear")
        self.assertIn("Read-only: change nothing.", sends[1][-2])
        audit = (self.td / "cache/audit.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(audit[-1])["result"], "DONE")

    def test_write_refused_without_allow_write(self):
        rc, _, err = self.run_cli("ask", "github", "Close issue 1", "--write")
        self.assertEqual(rc, 5)
        self.assertIn("allow_write = false", err)

    def test_write_allowed_marks_request(self):
        self.reg.write_text(self.reg.read_text().replace("allow_write = false", "allow_write = true"))
        rc, _, err = self.run_cli("ask", "github", "Comment on PR 2", "--write", "--id", "w1")
        self.assertEqual(rc, 0, err)
        self.assertIn("WRITE ALLOWED BY USER", [c for c in self.calls() if c[0] == "send"][1][-2])

    def test_busy_worker(self):
        os.environ["FAKE_HC_STATUS"] = "working"
        rc, _, err = self.run_cli("ask", "github", "x")
        self.assertEqual(rc, 3)

    def test_failed_answer(self):
        os.environ["FAKE_HC_ANSWER"] = "FAILED: no access"
        rc, _, err = self.run_cli("ask", "github", "x", "--id", "f1")
        self.assertEqual(rc, 1)

    def test_missing_mcp_config(self):
        self.reg.write_text(self.reg.read_text().replace(str(self.td / "wd"), str(self.td / "nope")))
        with self.assertRaises(SystemExit) as e:
            self.run_cli("ask", "github", "x")
        self.assertIn(".mcp.json missing", str(e.exception))

    def test_unknown_module(self):
        with self.assertRaises(SystemExit):
            self.run_cli("ask", "nope", "x")

    def test_list(self):
        rc, out, _ = self.run_cli("list", "--json")
        self.assertIn("github", json.loads(out))

    def test_extra_worker_rules(self):
        (self.td / "modules").mkdir()
        (self.td / "modules/github.md").write_text("Only repos of org X.")
        self.assertEqual(self.run_cli("ask", "github", "x", "--id", "e1")[0], 0)
        run = next(c for c in self.calls() if c[0] == "run")
        self.assertIn("Only repos of org X.", run[run.index("--append-system-prompt") + 1])

    def test_snippet(self):
        rc, out, _ = self.run_cli("snippet", "github")
        self.assertEqual(rc, 0)
        self.assertIn("tool_ask.py ask <module>", out)
        self.assertIn("| `github` |", out)
        self.assertNotIn(str(self.td), out)  # workdir and secrets stay out of projects

    def test_missing_registry(self):
        os.environ["HERDR_TOOL_AGENTS_REGISTRY"] = str(self.td / "none.toml")
        import importlib
        self.t = importlib.reload(self.t)
        with self.assertRaises(SystemExit) as e:
            self.run_cli("list")
        self.assertIn("missing", str(e.exception))

    def test_examples_are_read_only(self):
        for p in (HUB / "examples").glob("*/.mcp.example.json"):
            t = p.read_text()
            self.assertTrue("readonly" in t or "--read-only" in t, p)


if __name__ == "__main__":
    unittest.main(verbosity=1)
