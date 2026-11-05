#!/usr/bin/env python3
"""limits.py: Claude taps merge, resets, Codex fallback, statusLine wrapper, settings."""
import http.client
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
        env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                               if k not in ("KIMI_CODE_BASE_URL", "KIMI_CODE_OAUTH_HOST", "KIMI_OAUTH_HOST")}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.kimi = self.tmp / "kimi"
        self.kimi.mkdir()
        for name, value in (("CLAUDE", self.claude), ("ORCH", self.tmp / "orch"),
                            ("CODEX", self.tmp / "codex"), ("REGISTRY", self.reg), ("KIMI", self.kimi)):
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
        self.reg.write_text('[limits]\nagents = ["codex", "other"]\n')
        with mock.patch.object(limits, "codex_rpc", side_effect=FileNotFoundError("codex")), \
                mock.patch.object(limits, "CACHE", self.tmp / "cache"):
            out = limits.collect(now=1500)
        codex, other = out["agents"]
        self.assertEqual([(w["name"], w["used"]) for w in codex["windows"]], [("5h", 5.0), ("7d", 40.0)])
        self.assertIn("[limits.commands]", other["error"])

    def test_codex_rpc_is_cached(self):
        with mock.patch.object(limits, "CACHE", self.tmp / "cache"), \
                mock.patch.object(limits, "codex_rpc", return_value={"windows": [limits.window("5h", 7, 9999)],
                                                                  "resets": {"available": 2, "expires_at": 5000}}) as rpc:
            limits.codex(1000)
            data, _ = limits.codex(1000 + limits.RPC_EVERY - 1)
            self.assertEqual(rpc.call_count, 1)
            self.assertEqual(data["windows"][0]["used"], 7.0)
            self.assertEqual(data["resets"]["available"], 2)
            limits.codex(1000 + limits.RPC_EVERY)
            self.assertEqual(rpc.call_count, 2)

    def test_collect_preserves_codex_resets_and_kimi_monthly_breakdown(self):
        self.reg.write_text('[limits]\nagents = ["codex", "kimi"]\n')
        resets = {"available": 2, "expires_at": 5000}
        month = {**limits.window("month", 40, 99999), "breakdown": [
            {"name": "Kimi Code", "used": 25}, {"name": "Other Kimi", "used": 15}]}
        with mock.patch.dict(limits.BUILTIN, {
            "codex": lambda now: ({"seen": now, "windows": [limits.window("5h", 7, 9999)], "resets": resets}, None),
            "kimi": lambda now: ({"seen": now, "windows": [month]}, None),
        }):
            codex, kimi = limits.collect(now=1500)["agents"]
        self.assertEqual(codex["resets"], resets)
        self.assertIsNone(kimi["resets"])
        self.assertEqual(kimi["windows"][0], {**month, "reset": False})
        self.assertIsNone(kimi["error"])

    def test_command_agent(self):
        cmd = [sys.executable, "-c", 'print(\'{"windows": [{"name": "30d", "used": 12, "resets_at": 99999}]}\')']
        self.reg.write_text(f'[limits]\nagents = ["kimi"]\n[limits.commands]\nkimi = {json.dumps(cmd)}\n')
        out = limits.collect(now=1500)
        self.assertEqual(out["agents"][0]["windows"][0]["name"], "30d")

    def test_kimi_parses_available_windows_and_does_not_export_token(self):
        (self.kimi / "config.toml").write_text(
            '[providers."managed:kimi-code"]\nbase_url = "https://api.kimi.ai/coding/v1"\n'
            '[providers."managed:kimi-code".oauth]\nkey = "oauth/kimi-code-env-d44abaad1d85681f"\n')
        (self.kimi / "credentials").mkdir()
        (self.kimi / "credentials/kimi-code-env-d44abaad1d85681f.json").write_text(json.dumps({"access_token": "test-secret"}))
        payload = {"usages": {
            "limit_5h": {"used_ratio": 1.5, "reset_time": "2030-01-01T00:00:00Z"},
            "limit_month_total": {"used_ratio": "0.4"},
            "limit_month_code": {"used_ratio": 0.25}}}
        response = mock.MagicMock()
        response.__enter__.return_value = io.StringIO(json.dumps(payload))
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.dict(os.environ, {"KIMI_CODE_BASE_URL": "https://api.kimi.ai/coding/v1"}), \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener):
            result = limits.kimi_request()
        self.assertEqual([(w["name"], w["used"]) for w in result], [("5h", 100.0), ("month", 40.0)])
        self.assertEqual(result[1]["breakdown"], [{"name": "Kimi Code", "used": 25.0},
                                                {"name": "Other Kimi", "used": 15.0}])
        self.assertEqual(result[0]["resets_at"], 1893456000)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.kimi.ai/coding/v1/usages")
        self.assertEqual(req.get_header("Authorization"), "Bearer test-secret")
        self.assertNotIn("test-secret", json.dumps(result))

    def test_kimi_rejects_custom_endpoint_before_loading_credentials(self):
        (self.kimi / "config.toml").write_text('[providers."managed:kimi-code"]\n')
        with mock.patch.dict(os.environ, {"KIMI_CODE_BASE_URL": "https://example.com/coding/v1"}), \
                mock.patch.object(limits.urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "official Kimi"):
                limits.kimi_request()
        opener.assert_not_called()

    def test_kimi_does_not_redirect_bearer_token(self):
        req = limits.urllib.request.Request("https://api.kimi.ai/coding/v1/usages")
        self.assertIsNone(limits.NoRedirect().redirect_request(
            req, None, 302, "Found", {}, "https://example.com"))

    def test_kimi_renews_expiring_token_and_reads_cli_result(self):
        (self.kimi / "config.toml").write_text(
            '[providers."managed:kimi-code"]\nbase_url = "https://api.kimi.ai/coding/v1"\n'
            '[providers."managed:kimi-code".oauth]\nkey = "oauth/kimi-code-env-d44abaad1d85681f"\n')
        (self.kimi / "credentials").mkdir()
        path = self.kimi / "credentials/kimi-code-env-d44abaad1d85681f.json"
        payload = {"usages": {"limit_5h": {"used_ratio": 0.1}}}
        opener = mock.Mock()
        opener.open.side_effect = lambda *a, **k: io.StringIO(json.dumps(payload))
        for expiry, expected_refreshes in ((900, 1), (1050, 1), (2000, 0)):
            with self.subTest(expiry=expiry):
                path.write_text(json.dumps({"access_token": "old", "refresh_token": "refresh", "expires_at": expiry}))
                def renew():
                    path.write_text(json.dumps({"access_token": "new", "refresh_token": "rotated", "expires_at": 2000}))
                with mock.patch.object(limits.time, "time", return_value=1000), \
                        mock.patch.object(limits, "kimi_refresh", side_effect=renew) as refresh, \
                        mock.patch.object(limits.urllib.request, "build_opener", return_value=opener), \
                        mock.patch.dict(os.environ, {"KIMI_CODE_BASE_URL": "https://api.kimi.ai/coding/v1"}):
                    limits.kimi_request()
                self.assertEqual(refresh.call_count, expected_refreshes)
                expected_token = "new" if expected_refreshes else "old"
                self.assertEqual(opener.open.call_args.args[0].get_header("Authorization"), "Bearer " + expected_token)

    def kimi_provider(self, auth):
        (self.kimi / "config.toml").write_text(
            '[providers."managed:kimi-code"]\nbase_url = "https://api.kimi.ai/coding/v1"\n' + auth)

    def kimi_credential(self, token="fresh-secret", expiry=2000):
        self.kimi_provider('[providers."managed:kimi-code".oauth]\nkey = "oauth/kimi-code-env-d44abaad1d85681f"\n')
        path = self.kimi / "credentials/kimi-code-env-d44abaad1d85681f.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"access_token": token, "refresh_token": "refresh-secret", "expires_at": expiry}))
        return path

    def quota_response(self):
        return io.StringIO('{"usages":{"limit_5h":{"used_ratio":0.25}}}')

    def quota_error(self, code=401):
        error = limits.urllib.error.HTTPError("https://api.kimi.ai/coding/v1/usages", code,
                                              "fresh-secret env-secret", {}, io.BytesIO(b"refresh-secret"))
        self.addCleanup(error.close)
        return error

    def test_kimi_env_key_reaches_authorization_but_not_result_or_cache(self):
        self.kimi_provider('api_key_env = "  KIMI_TEST_KEY  "\n')
        opener = mock.Mock()
        opener.open.return_value = self.quota_response()
        with mock.patch.dict(os.environ, {"KIMI_TEST_KEY": "  env-secret  "}), \
                mock.patch.object(limits, "CACHE", self.tmp / "cache"), \
                mock.patch.object(limits, "kimi_refresh") as refresh, \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener):
            data, error = limits.kimi(1000)
        self.assertIsNone(error)
        self.assertEqual(opener.open.call_args.args[0].get_header("Authorization"), "Bearer env-secret")
        self.assertNotIn("env-secret", json.dumps(data))
        self.assertNotIn("env-secret", (self.tmp / "cache/limits/kimi.json").read_text())
        refresh.assert_not_called()

    def test_kimi_missing_empty_or_blank_env_is_configuration_error(self):
        self.kimi_provider('api_key_env = "KIMI_TEST_KEY"\n')
        for value in (None, "", "   "):
            with self.subTest(value=value), mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(limits, "kimi_refresh") as refresh, \
                    mock.patch.object(limits.urllib.request, "build_opener") as build:
                if value is not None:
                    os.environ["KIMI_TEST_KEY"] = value
                with self.assertRaisesRegex(ValueError, "KIMI_TEST_KEY.*not set or is empty") as caught:
                    limits.kimi_request()
                self.assertNotIn("login", str(caught.exception))
                refresh.assert_not_called()
                build.assert_not_called()

    def test_kimi_auth_conflicts_fail_before_network(self):
        for auth in ('api_key = " inline-secret "\napi_key_env = " KEY "\n',
                     'api_key = " inline-secret "\noauth = {}\n',
                     'api_key_env = " KEY "\noauth = {}\n'):
            with self.subTest(auth=auth), \
                    mock.patch.object(limits, "kimi_refresh") as refresh, \
                    mock.patch.object(limits.urllib.request, "build_opener") as build:
                self.kimi_provider(auth)
                with self.assertRaisesRegex(ValueError, "mutually exclusive") as caught:
                    limits.kimi_request()
                self.assertNotIn("inline-secret", str(caught.exception))
                refresh.assert_not_called()
                build.assert_not_called()

    def test_kimi_inline_key_ignores_empty_env_setting(self):
        self.kimi_provider('api_key = " inline-secret "\napi_key_env = "  "\n')
        opener = mock.Mock()
        opener.open.return_value = self.quota_response()
        with mock.patch.object(limits.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(limits, "kimi_refresh") as refresh:
            limits.kimi_request()
        self.assertEqual(opener.open.call_args.args[0].get_header("Authorization"), "Bearer inline-secret")
        refresh.assert_not_called()

    def test_kimi_401_adopts_concurrently_rotated_oauth_winner(self):
        path = self.kimi_credential()
        opener = mock.Mock()
        def request(req, **kwargs):
            if opener.open.call_count == 1:
                path.write_text(json.dumps({"access_token": "new-secret", "expires_at": 2000}))
                raise self.quota_error()
            return self.quota_response()
        opener.open.side_effect = request
        with mock.patch.object(limits.time, "time", return_value=1000), \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(limits, "kimi_refresh") as refresh:
            result = limits.kimi_request()
        self.assertEqual(result[0]["used"], 25)
        self.assertEqual([c.args[0].get_header("Authorization") for c in opener.open.call_args_list],
                         ["Bearer fresh-secret", "Bearer new-secret"])
        refresh.assert_not_called()

    def test_kimi_401_unchanged_fresh_oauth_requires_reauthorization(self):
        self.kimi_credential()
        opener = mock.Mock()
        opener.open.side_effect = self.quota_error()
        with mock.patch.object(limits.time, "time", return_value=1000), \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(limits, "kimi_refresh") as refresh:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401; reauthorize with kimi /login") as caught:
                limits.kimi_request()
        self.assertEqual(opener.open.call_count, 1)
        self.assertNotIn("secret", str(caught.exception))
        refresh.assert_not_called()

    def test_kimi_repeated_401_has_one_retry_and_no_loop(self):
        path = self.kimi_credential()
        opener = mock.Mock()
        def rejected(req, **kwargs):
            path.write_text(json.dumps({"access_token": "next-secret", "expires_at": 2000}))
            raise self.quota_error()
        opener.open.side_effect = rejected
        with mock.patch.object(limits.time, "time", return_value=1000), \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(limits, "kimi_refresh") as refresh:
            with self.assertRaisesRegex(RuntimeError, "reauthorize"):
                limits.kimi_request()
        self.assertEqual(opener.open.call_count, 2)
        refresh.assert_not_called()

    def test_kimi_401_expiring_winner_uses_native_renewal_once(self):
        path = self.kimi_credential()
        opener = mock.Mock()
        def request(req, **kwargs):
            if opener.open.call_count == 1:
                path.write_text(json.dumps({"access_token": "fresh-secret", "refresh_token": "refresh-secret",
                                            "expires_at": 1001}))
                raise self.quota_error()
            return self.quota_response()
        def renew():
            path.write_text(json.dumps({"access_token": "renewed-secret", "expires_at": 2000}))
        opener.open.side_effect = request
        with mock.patch.object(limits.time, "time", return_value=1000), \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(limits, "kimi_refresh", side_effect=renew) as refresh:
            limits.kimi_request()
        refresh.assert_called_once()
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(opener.open.call_args.args[0].get_header("Authorization"), "Bearer renewed-secret")

    def test_kimi_401_cannot_start_second_native_helper(self):
        path = self.kimi_credential(expiry=900)
        opener = mock.Mock()
        opener.open.side_effect = self.quota_error()
        def renew():
            path.write_text(json.dumps({"access_token": "renewed-secret", "refresh_token": "refresh-secret",
                                        "expires_at": 1001}))
        with mock.patch.object(limits.time, "time", return_value=1000), \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(limits, "kimi_refresh", side_effect=renew) as refresh:
            with self.assertRaisesRegex(RuntimeError, "reauthorize"):
                limits.kimi_request()
        refresh.assert_called_once()
        self.assertEqual(opener.open.call_count, 1)

    def test_kimi_401_renewal_failure_retains_reading_without_secrets(self):
        path = self.kimi_credential()
        opener = mock.Mock()
        def rejected(req, **kwargs):
            path.write_text(json.dumps({"access_token": "fresh-secret", "refresh_token": "refresh-secret",
                                        "expires_at": 1001}))
            raise self.quota_error()
        opener.open.side_effect = rejected
        cache = self.tmp / "cache/limits/kimi.json"
        cache.parent.mkdir(parents=True)
        previous = {"seen": 800, "attempted": 800, "windows": [limits.window("5h", 7, 9999)]}
        cache.write_text(json.dumps(previous))
        with mock.patch.object(limits, "CACHE", self.tmp / "cache"), \
                mock.patch.object(limits.time, "time", return_value=1000), \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(limits, "kimi_refresh", side_effect=RuntimeError("refresh-secret")) as refresh:
            result, error = limits.kimi(1000)
            limits.kimi(1001)
        self.assertIn("renewal failed", error)
        self.assertEqual(result["seen"], previous["seen"])
        self.assertEqual(result["windows"], previous["windows"])
        self.assertEqual(result["attempted"], 1000)
        self.assertNotIn("secret", cache.read_text())
        refresh.assert_called_once()
        self.assertEqual(opener.open.call_count, 1)

    def test_kimi_api_key_401_never_starts_oauth_renewal(self):
        for auth in ('api_key = "env-secret"\n', 'api_key_env = "KIMI_TEST_KEY"\n'):
            with self.subTest(auth=auth), mock.patch.dict(os.environ, {"KIMI_TEST_KEY": "env-secret"}), \
                    mock.patch.object(limits, "kimi_refresh") as refresh, \
                    mock.patch.object(limits.urllib.request, "build_opener") as build:
                self.kimi_provider(auth)
                build.return_value.open.side_effect = self.quota_error()
                with self.assertRaisesRegex(RuntimeError, "HTTP 401; check the configured API key") as caught:
                    limits.kimi_request()
                self.assertNotIn("secret", str(caught.exception))
                self.assertNotIn("login", str(caught.exception))
                self.assertEqual(build.return_value.open.call_count, 1)
                refresh.assert_not_called()

    def test_kimi_other_http_and_transport_failures_never_rotate_oauth(self):
        self.kimi_credential()
        errors = [self.quota_error(code) for code in (403, 429, 500, 503)]
        errors.append(limits.urllib.error.URLError("fresh-secret"))
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(limits.time, "time", return_value=1000), \
                    mock.patch.object(limits, "kimi_refresh") as refresh, \
                    mock.patch.object(limits.urllib.request, "build_opener") as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(RuntimeError) as caught:
                    limits.kimi_request()
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(build.return_value.open.call_count, 1)
                refresh.assert_not_called()

    def test_kimi_oauth_slot_mismatch_fails_before_credential_read_or_network(self):
        cases = [
            # Moving a default credential to the global API requires a different slot.
            ("https://api.kimi.com/coding/v1", "oauth/kimi-code", "", {"KIMI_CODE_BASE_URL": "https://api.kimi.ai/coding/v1"}),
            # A persisted wrong slot must also fail without any env override.
            ("https://api.kimi.ai/coding/v1", "oauth/kimi-code", "", {}),
            ("https://api.kimi.com/coding/v1", "oauth/kimi-code", "", {"KIMI_CODE_OAUTH_HOST": "https://auth.kimi.ai"}),
            ("https://api.kimi.com/coding/v1", "oauth/kimi-code", "", {"KIMI_OAUTH_HOST": "https://auth.kimi.ai"}),
            # Even an equal base override makes native runtime ignore the persisted OAuth host.
            ("https://api.kimi.ai/coding/v1", "oauth/kimi-code-env-0e4f99c69cc27850",
             'oauth_host = "https://auth.kimi.ai"\n', {"KIMI_CODE_BASE_URL": "https://api.kimi.ai/coding/v1"}),
        ]
        for base, key, extra, env in cases:
            with self.subTest(base=base, key=key, env=env), mock.patch.dict(os.environ, env), \
                    mock.patch.object(limits.Path, "read_text") as read, \
                    mock.patch.object(limits, "kimi_refresh") as refresh, \
                    mock.patch.object(limits.urllib.request, "build_opener") as build:
                (self.kimi / "config.toml").write_text(
                    f'[providers."managed:kimi-code"]\nbase_url = "{base}"\n'
                    f'[providers."managed:kimi-code".oauth]\nkey = "{key}"\n' + extra)
                with self.assertRaisesRegex(ValueError, "configuration/environment mismatch.*align"):
                    limits.kimi_request()
                read.assert_not_called()
                refresh.assert_not_called()
                build.assert_not_called()

    def test_kimi_matching_default_and_global_oauth_slots_work(self):
        cases = [
            ("https://api.kimi.com/coding/v1", "kimi-code", "", {}),
            ("https://api.kimi.com/coding/v1", "kimi-code", "", {"KIMI_CODE_BASE_URL": "https://api.kimi.com/coding/v1/"}),
            ("https://api.kimi.ai/coding/v1", "kimi-code-env-d44abaad1d85681f", "", {}),
            ("https://api.kimi.ai/coding/v1", "kimi-code-env-d44abaad1d85681f", "",
             {"KIMI_CODE_BASE_URL": "https://api.kimi.ai/coding/v1"}),
            ("https://api.kimi.ai/coding/v1", "kimi-code-env-0e4f99c69cc27850",
             'oauth_host = "https://auth.kimi.ai"\n', {}),
            ("https://api.kimi.ai/coding/v1", "kimi-code-env-0e4f99c69cc27850",
             'oauth_host = "https://auth.kimi.ai"\n', {"KIMI_CODE_OAUTH_HOST": "https://auth.kimi.ai/"}),
        ]
        for base, name, extra, env in cases:
            with self.subTest(base=base, name=name, env=env), mock.patch.dict(os.environ, env), \
                    mock.patch.object(limits, "kimi_refresh") as refresh, \
                    mock.patch.object(limits.urllib.request, "build_opener") as build:
                (self.kimi / "config.toml").write_text(
                    f'[providers."managed:kimi-code"]\nbase_url = "{base}"\n'
                    f'[providers."managed:kimi-code".oauth]\nkey = "oauth/{name}"\n' + extra)
                path = self.kimi / f"credentials/{name}.json"
                path.parent.mkdir(exist_ok=True)
                path.write_text('{"access_token":"slot-secret"}')
                build.return_value.open.return_value = self.quota_response()
                self.assertEqual(limits.kimi_request()[0]["used"], 25)
                request = build.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, base + "/usages")
                self.assertEqual(request.get_header("Authorization"), "Bearer slot-secret")
                refresh.assert_not_called()

    def test_kimi_invalid_oauth_key_is_not_silently_reduced_to_basename(self):
        for key in ("oauth/a/kimi-code", "oauth/../kimi-code", "../kimi-code", "oauth/.hidden", "oauth/"):
            with self.subTest(key=key), mock.patch.object(limits.Path, "read_text") as read, \
                    mock.patch.object(limits, "kimi_refresh") as refresh, \
                    mock.patch.object(limits.urllib.request, "build_opener") as build:
                self.kimi_provider(f'[providers."managed:kimi-code".oauth]\nkey = "{key}"\n')
                with self.assertRaisesRegex(ValueError, "invalid Kimi credential name"):
                    limits.kimi_request()
                read.assert_not_called()
                refresh.assert_not_called()
                build.assert_not_called()

    def test_kimi_malformed_response_and_external_errors_are_safe_and_cached(self):
        marker = "PRIVATE_RESPONSE_TEXT"
        self.kimi_credential()
        payloads = [
            {"usages": {"limit_5h": {"used_ratio": marker}}},
            {"usages": {"limit_5h": {"used_ratio": 0.1, "reset_time": marker}}},
            {"usages": [marker]},
            [marker],
            {"usages": {"limit_month_total": {"used_ratio": 0.4}, "limit_month_code": {"used_ratio": marker}}},
        ]
        errors = [limits.urllib.error.URLError(marker),
                  http.client.HTTPException(marker)]
        cache = self.tmp / "cache/limits/kimi.json"
        cache.parent.mkdir(parents=True)
        previous = {"seen": 800, "attempted": 800, "windows": [limits.window("5h", 7, 9999)]}
        for payload in payloads + errors:
            with self.subTest(payload=type(payload).__name__), \
                    mock.patch.object(limits, "CACHE", self.tmp / "cache"), \
                    mock.patch.object(limits.time, "time", return_value=1000), \
                    mock.patch.object(limits, "kimi_refresh") as refresh, \
                    mock.patch.object(limits.urllib.request, "build_opener") as build:
                cache.write_text(json.dumps(previous))
                if isinstance(payload, Exception):
                    build.return_value.open.side_effect = payload
                else:
                    build.return_value.open.return_value = io.StringIO(json.dumps(payload))
                result, error = limits.kimi(1000)
                self.assertTrue(error)
                self.assertEqual(result["seen"], previous["seen"])
                self.assertEqual(result["windows"], previous["windows"])
                self.assertEqual(result["attempted"], 1000)
                self.assertNotIn(marker, json.dumps(result))
                self.assertNotIn(marker, error)
                self.assertNotIn(marker, cache.read_text())
                limits.kimi(1089)
                self.assertEqual(build.return_value.open.call_count, 1)
                build.return_value.open.side_effect = None
                build.return_value.open.return_value = self.quota_response()
                recovered, error = limits.kimi(1090)
                self.assertIsNone(error)
                self.assertEqual(recovered["seen"], 1090)
                self.assertEqual(build.return_value.open.call_count, 2)
                refresh.assert_not_called()

    @unittest.skipUnless(limits.shutil.which("node"), "Node is needed to execute the mod tooltip regression")
    def test_kimi_monthly_tooltip_hides_breakdown_after_reset(self):
        self.reg.write_text('[limits]\nagents = ["kimi"]\n')
        month = {**limits.window("month", 40, 2000), "breakdown": [
            {"name": "Kimi Code", "used": 25}, {"name": "Other Kimi", "used": 15}]}
        with mock.patch.dict(limits.BUILTIN, {"kimi": lambda now: ({"seen": 800, "windows": [month]}, None)}):
            windows = [limits.collect(now=now)["agents"][0]["windows"][0] for now in (1000, 2001)]
        # Execute the real cell method with native drawing calls stubbed, inspecting its tooltip.
        source = (limits.HUB / "herdr-app/agent-limits/main.js").read_text()
        cell = source.split("  cell(", 1)[1].split("\n  mark(", 1)[0]
        script = r'''const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
let tooltip;
const element = new Proxy({}, {get: (_, method) => (...args) => {
  if (method === "tooltip") tooltip = args[0];
  return element;
}});
const draw = () => element;
const Cell = new Function("v_flex", "h_flex", "div", "full", "span", "when",
  "return class { cell(" + input.cell + " }")(draw, draw, draw, () => "date", () => "time", () => "time");
const view = new Cell();
console.log(JSON.stringify(input.windows.map((window) => {
  view.cell(window, 1000, {}, "test", "used", 100);
  return tooltip;
})));'''
        result = limits.subprocess.run(["node", "-e", script], input=json.dumps({"cell": cell, "windows": windows}),
                                       text=True, capture_output=True, check=True)
        current, renewed = json.loads(result.stdout)
        self.assertIn("Kimi Code: 25% of monthly budget", current)
        self.assertIn("Other Kimi: 15% of monthly budget", current)
        self.assertIn("renewed since the last reading", renewed)
        self.assertNotIn("monthly budget", renewed)
        self.assertNotIn("Kimi Code", renewed)

    def test_kimi_native_refresh_uses_owned_loopback_server_and_cleans_up(self):
        directory = self.kimi / "server/instances"
        directory.mkdir(parents=True)
        (directory / "ours.json").write_text(json.dumps({"pid": 123, "host": "127.0.0.1", "port": 4567}))
        (self.kimi / "server.token").write_text("local-secret")
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        opener = mock.Mock()
        opener.open.side_effect = [io.StringIO('{}'), io.StringIO('{"code":0,"data":{"kind":"ok"}}')]
        with mock.patch.object(limits.shutil, "which", return_value="kimi"), \
                mock.patch.object(limits.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener):
            limits.kimi_refresh()
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["kimi", "web", "--no-open", "--host", "127.0.0.1", "--port", "0"])
        self.assertEqual(popen.call_args.kwargs["stdout"], limits.subprocess.DEVNULL)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:4567/api/v1/oauth/usage")
        self.assertEqual(req.get_header("Authorization"), "Bearer local-secret")
        process.terminate.assert_called_once()
        process.wait.assert_called_once()

    def test_kimi_native_refresh_rejects_other_process_and_non_loopback(self):
        directory = self.kimi / "server/instances"
        directory.mkdir(parents=True)
        for pid, host in ((999, "127.0.0.1"), (123, "0.0.0.0")):
            with self.subTest(pid=pid, host=host):
                (directory / "server.json").write_text(json.dumps({"pid": pid, "host": host, "port": 4567}))
                process = mock.Mock(pid=123)
                process.poll.return_value = None
                with mock.patch.object(limits.shutil, "which", return_value="kimi"), \
                        mock.patch.object(limits.subprocess, "Popen", return_value=process), \
                        mock.patch.object(limits.time, "monotonic", side_effect=[0, 0, 21]), \
                        mock.patch.object(limits.time, "sleep"), \
                        mock.patch.object(limits.urllib.request, "build_opener") as build:
                    with self.assertRaisesRegex(TimeoutError, "did not become ready"):
                        limits.kimi_refresh()
                build.return_value.open.assert_not_called()
                process.terminate.assert_called_once()

    def test_kimi_native_refresh_stops_service_on_auth_failure_without_leaking_response(self):
        directory = self.kimi / "server/instances"
        directory.mkdir(parents=True)
        (directory / "ours.json").write_text(json.dumps({"pid": 123, "host": "127.0.0.1", "port": 4567}))
        (self.kimi / "server.token").write_text("local-secret")
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        opener = mock.Mock()
        opener.open.side_effect = [io.StringIO('{}'), io.StringIO(
            '{"code":0,"data":{"kind":"error","message":"test-secret"}}')]
        with mock.patch.object(limits.shutil, "which", return_value="kimi"), \
                mock.patch.object(limits.subprocess, "Popen", return_value=process), \
                mock.patch.object(limits.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(RuntimeError) as caught:
                limits.kimi_refresh()
        self.assertNotIn("test-secret", str(caught.exception))
        process.terminate.assert_called_once()

    def test_kimi_native_refresh_requires_installed_cli(self):
        with mock.patch.object(limits.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "CLI is required"):
                limits.kimi_refresh()

    def test_kimi_caches_errors_and_keeps_last_success(self):
        with mock.patch.object(limits, "CACHE", self.tmp / "cache"), \
                mock.patch.object(limits, "kimi_request", return_value=[limits.window("5h", 25, 9999)]) as api:
            first, error = limits.kimi(1000)
            self.assertIsNone(error)
            limits.kimi(1001)
            self.assertEqual(api.call_count, 1)
            api.side_effect = RuntimeError("offline")
            stale, error = limits.kimi(1000 + limits.KIMI_EVERY)
            self.assertEqual(stale["seen"], first["seen"])
            self.assertEqual(stale["windows"], first["windows"])
            self.assertIn("offline", error)
            limits.kimi(1001 + limits.KIMI_EVERY)
            self.assertEqual(api.call_count, 2)

    def test_kimi_failure_does_not_hide_codex(self):
        self.reg.write_text('[limits]\nagents = ["codex", "kimi"]\n')
        with mock.patch.object(limits, "CACHE", self.tmp / "cache"), \
                mock.patch.dict(limits.BUILTIN, {"codex": lambda now: ({"seen": now, "windows": []}, None)}), \
                mock.patch.object(limits, "kimi_request", side_effect=RuntimeError("offline")):
            out = limits.collect(now=1500)
        self.assertEqual([a["id"] for a in out["agents"]], ["codex", "kimi"])
        self.assertIsNone(out["agents"][0]["error"])
        self.assertIn("offline", out["agents"][1]["error"])

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
