#!/usr/bin/env python3
"""limits.py: Claude taps merge, resets, Codex fallback, statusLine wrapper, settings."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import limits  # noqa: E402


class Limits(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.claude = self.tmp / "claude"
        self.claude.mkdir()
        self.reg = self.tmp / "registry.toml"
        for name, value in (("CLAUDE", self.claude), ("ORCH", self.tmp / "orch"),
                            ("CODEX", self.tmp / "codex"), ("REGISTRY", self.reg)):
            p = mock.patch.object(limits, name, value)
            p.start()
            self.addCleanup(p.stop)

    def tap(self, name, seen, five, resets):
        (self.claude / f"{name}.json").write_text(json.dumps(
            {"seen": seen, "rate_limits": {"five_hour": {"used_percentage": five, "resets_at": resets}}}))

    def test_idle_session_does_not_win(self):
        # The idle one saw the old window (earlier reset) with more usage.
        self.tap("idle", 1000, 90, 2000)
        self.tap("busy", 900, 10, 5000)
        self.tap("ancient", 1000 - limits.FRESH - 1, 99, 9000)
        self.reg.write_text('[limits]\nagents = ["claude"]\n')
        out = limits.collect(now=1500)
        self.assertEqual(out["show"], "used")
        self.assertEqual(out["agents"][0]["windows"], [{"name": "5h", "used": 10.0, "resets_at": 5000, "reset": False}])

    def test_reset_window_is_unknown(self):
        self.tap("s", 1000, 70, 1200)
        self.reg.write_text('[limits]\nagents = ["claude"]\nshow = "left"\n')
        out = limits.collect(now=1500)
        self.assertEqual(out["show"], "left")
        self.assertEqual(out["agents"][0]["windows"][0]["used"], None)
        self.assertTrue(out["agents"][0]["windows"][0]["reset"])

    def test_codex_falls_back_to_logs(self):
        d = self.tmp / "codex" / "2026" / "01" / "02"
        d.mkdir(parents=True)
        rl = {"primary": {"used_percent": 5, "window_minutes": 300, "resets_at": 9000},
              "secondary": {"used_percent": 40, "window_minutes": 10080, "resets_at": 9999}}
        (d / "rollout-x.jsonl").write_text(json.dumps({"payload": {"type": "token_count", "rate_limits": rl}}) + "\n")
        self.reg.write_text('[limits]\nagents = ["codex", "kimi"]\n')
        with mock.patch.object(limits, "codex_rpc", side_effect=FileNotFoundError("codex")), \
                mock.patch.object(limits, "CACHE", self.tmp / "cache"):
            out = limits.collect(now=1500)
        codex, kimi = out["agents"]
        self.assertEqual([(w["name"], w["used"]) for w in codex["windows"]], [("5h", 5.0), ("7d", 40.0)])
        self.assertIn("[limits.commands]", kimi["error"])

    def test_codex_rpc_is_cached(self):
        with mock.patch.object(limits, "CACHE", self.tmp / "cache"), \
                mock.patch.object(limits, "codex_rpc", return_value=[limits.window("5h", 7, 9999)]) as rpc:
            limits.codex(1000)
            data, _ = limits.codex(1000 + limits.RPC_EVERY - 1)
            self.assertEqual(rpc.call_count, 1)
            self.assertEqual(data["windows"][0]["used"], 7.0)
            limits.codex(1000 + limits.RPC_EVERY)
            self.assertEqual(rpc.call_count, 2)

    def test_command_agent(self):
        cmd = [sys.executable, "-c", 'print(\'{"windows": [{"name": "30d", "used": 12, "resets_at": 99999}]}\')']
        self.reg.write_text(f'[limits]\nagents = ["kimi"]\n[limits.commands]\nkimi = {json.dumps(cmd)}\n')
        out = limits.collect(now=1500)
        self.assertEqual(out["agents"][0]["windows"][0]["name"], "30d")

    def test_statusline_keeps_limits_and_chains(self):
        raw = json.dumps({"session_id": "abc", "rate_limits": {"five_hour": {"used_percentage": 3, "resets_at": 9}}})
        out = io.BytesIO()
        with mock.patch.object(sys, "stdin", mock.Mock(buffer=io.BytesIO(raw.encode()))), \
                mock.patch.object(sys, "stdout", mock.Mock(buffer=out)):
            code = limits.main(["statusline", "--", sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().decode().strip(), str(len(raw)))
        self.assertEqual(json.loads((self.claude / "abc.json").read_text())["rate_limits"]["five_hour"]["used_percentage"], 3)


if __name__ == "__main__":
    os.environ.pop("HERDR_AGENT_KIT_REGISTRY", None)
    unittest.main()
