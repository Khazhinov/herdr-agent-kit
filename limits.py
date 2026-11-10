#!/usr/bin/env python3
"""Usage limits of agent runtimes, for the Herdr App mod `agent-limits`.

  limits.py                         the configured agents' limits as JSON
  limits.py statusline -- CMD...    Claude Code statusLine wrapper: keeps this
                                    session's rate_limits, then runs CMD with
                                    the same input and prints its output

Settings: `[limits]` of the registry (registry.example.toml):
  agents = ["claude", "codex"]        order shown; default: these two; kimi also built in
  show = "used"                       or "left": what the percent means
  [limits.commands]
  other = ["/path/to/agent-limits"]     any other agent: a command printing
                                      {"windows": [{"name", "used", "resets_at"}]}

Output: {"now", "show", "agents": [{"id", "seen", "windows", "resets", "error"}]};
a window is {"name", "used" (percent), "resets_at" (epoch s), "reset"}. `reset`
true: the window renewed after the last reading, so `used` is unknown (null).
`resets`: null, or {"available", "expires_at"}: limit resets the account may
spend (Codex grants them), the earliest expiry first.
Standard library only.
"""
import hashlib
import json
import os
import select
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HUB = Path(__file__).resolve().parent
REGISTRY = Path(os.environ.get("HERDR_AGENT_KIT_REGISTRY") or HUB / "local" / "registry.toml")
CACHE = Path(os.environ.get("HERDR_AGENT_KIT_CACHE") or Path.home() / ".cache" / "herdr-agent-kit")
CLAUDE = CACHE / "limits" / "claude"
# herdr-gsd-template keeps its own taps of the same statusLine input; read them too.
ORCH = Path(os.environ.get("ORCH_CACHE_DIR") or Path.home() / ".cache" / "herdr-orch")
CODEX = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
KIMI = Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code")
FRESH = 6 * 3600  # Claude taps older than this are ignored when newer ones exist


def span(minutes):
    """300 -> 5h, 10080 -> 7d."""
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    return f"{minutes // 60}h" if minutes and minutes % 60 == 0 else f"{minutes}m"


def window(name, used, resets_at):
    return {"name": name, "used": round(float(used), 1), "resets_at": int(resets_at) if resets_at else None}


# ---------------------------------------------------------------- claude

CLAUDE_NAMES = {"five_hour": "5h", "seven_day": "7d", "seven_day_opus": "7d opus",
                "seven_day_sonnet": "7d sonnet", "spend_limit": "$"}


def claude_taps():
    """(seen, rate_limits) of every recorded Claude session."""
    for p in [*CLAUDE.glob("*.json"), *(ORCH / "owner-sessions").glob("*.json"), ORCH / "claude-statusline.json"]:
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        seen = d.get("seen") or d.get("observed_at")
        if isinstance(d.get("rate_limits"), dict) and seen:
            yield int(seen), d["rate_limits"]


def claude(now):
    """An idle session keeps showing the limits it last saw, maybe from before a
    reset: per window the latest resets_at is the current one, and in one window
    usage only grows, so the highest used wins."""
    taps = list(claude_taps())
    if not taps:
        return None, "no data yet: wrap the Claude statusLine (concepts/herdr-app-mods.md)"
    newest = max(s for s, _ in taps)
    best = {}
    for seen, rl in taps:
        if newest - seen > FRESH:
            continue
        for key, w in rl.items():
            if isinstance(w, dict) and w.get("used_percentage") is not None:
                k = (w.get("resets_at") or 0, w["used_percentage"])
                if key not in best or k > best[key][0]:
                    best[key] = (k, w)
    order = list(CLAUDE_NAMES)
    keys = sorted(best, key=lambda k: order.index(k) if k in order else len(order))
    return {"seen": newest, "windows": [
        window(CLAUDE_NAMES.get(k, k), best[k][1]["used_percentage"], best[k][1].get("resets_at")) for k in keys]}, None


# ---------------------------------------------------------------- codex

def codex_rpc(timeout=10.0):
    """Live account limits from `codex app-server` (account/rateLimits/read)."""
    p = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"clientInfo": {"name": "herdr-agent-kit", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"}]
        p.stdin.write("".join(json.dumps(m) + "\n" for m in msgs).encode())
        p.stdin.flush()
        deadline, buf = time.time() + timeout, b""
        while time.time() < deadline:
            if not select.select([p.stdout], [], [], max(0.0, deadline - time.time()))[0]:
                break
            chunk = os.read(p.stdout.fileno(), 65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("id") == 2:
                    if "error" in o:
                        raise RuntimeError(o["error"].get("message", "rpc error"))
                    rl = o["result"].get("rateLimits") or {}
                    wins = [rl.get(k) for k in ("primary", "secondary")]
                    credits = [c for c in (o["result"].get("rateLimitResetCredits") or {}).get("credits") or []
                               if c.get("status") == "available"]
                    return {"windows": [window(span(w.get("windowDurationMins")), w["usedPercent"], w.get("resetsAt"))
                                        for w in wins if w and w.get("usedPercent") is not None],
                            "resets": {"available": len(credits),
                                       "expires_at": min((c["expiresAt"] for c in credits if c.get("expiresAt")), default=None)}}
        raise TimeoutError("codex app-server did not answer")
    finally:
        p.kill()
        p.wait()


def codex_logs():
    """Fallback: the newest rate_limits Codex wrote into its session logs."""
    files = sorted(CODEX.glob("*/*/*/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if CODEX.is_dir() else []
    for f in files[:20]:
        with f.open(encoding="utf-8", errors="replace") as fh:
            lines = [ln for ln in fh if '"rate_limits"' in ln]
        for line in reversed(lines):
            try:
                rl = (json.loads(line).get("payload") or {}).get("rate_limits") or {}
            except ValueError:
                continue
            wins = [w for w in (rl.get("primary"), rl.get("secondary")) if w and w.get("used_percent") is not None]
            if wins:
                return int(f.stat().st_mtime), [window(span(w.get("window_minutes")), w["used_percent"], w.get("resets_at")) for w in wins]
    return None, []


RPC_EVERY = 30  # s; one app-server start costs about a second


def codex(now):
    """The fresher of two readings: the session logs (written on every Codex turn
    here, read on every call) and the live account limits (cached RPC_EVERY s:
    they also see usage from other machines)."""
    cache = CACHE / "limits" / "codex-rpc.json"
    try:
        rpc = json.loads(cache.read_text())
    except (OSError, ValueError):
        rpc = None
    error = None
    if not rpc or now - rpc.get("seen", 0) >= RPC_EVERY:
        try:
            rpc = {"seen": now, **codex_rpc()}
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(rpc))
            tmp.replace(cache)
        except Exception as e:  # no binary, logged out, protocol change
            error = f"app-server: {e}"
    seen, wins = codex_logs()
    best = max([r for r in (rpc, {"seen": seen, "windows": wins}) if r and r.get("windows")],
               key=lambda r: r["seen"] or 0, default=None)
    if best and rpc:
        best = {**best, "resets": rpc.get("resets")}
    return (best, None) if best else (None, f"{error or 'app-server: no limits'}; no limits in session logs")


# ---------------------------------------------------------------- kimi

KIMI_EVERY = 90  # s; the UI can poll without making an API request each time
KIMI_NAMES = {"limit_5h": "5h", "limit_7d": "7d", "limit_month_total": "month"}
KIMI_BASES = {"https://api.kimi.com/coding/v1", "https://api.kimi.ai/coding/v1"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never forward the bearer token to a redirect destination


def kimi_refresh():
    """Delegate refresh rotation, locking and credential writes to the installed CLI.

    Start only our own loopback server, use its authenticated quota endpoint,
    then shut it down. Never implement a second OAuth writer beside Kimi.
    """
    binary = shutil.which("kimi")
    if not binary:
        candidate = KIMI / "bin" / "kimi"
        if candidate.is_file():
            binary = str(candidate)
    if not binary:
        raise RuntimeError("Kimi CLI is required to renew OAuth authorization")
    env = {**os.environ, "KIMI_CODE_HOME": str(KIMI)}
    p = subprocess.Popen([binary, "web", "--no-open", "--host", "127.0.0.1", "--port", "0"],
                         cwd=KIMI, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Ignore proxy settings and refuse redirects for the local server bearer.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        deadline = time.monotonic() + 20
        origin = None
        while time.monotonic() < deadline:
            if p.poll() is not None:
                raise RuntimeError("Kimi web exited before OAuth renewal; requires a compatible Kimi Code CLI")
            for path in (KIMI / "server" / "instances").glob("*.json"):
                try:
                    info = json.loads(path.read_text())
                    port = info.get("port")
                    if (info.get("pid") != p.pid or info.get("host") != "127.0.0.1"
                            or not isinstance(port, int) or not 0 < port < 65536):
                        continue
                    candidate = f"http://127.0.0.1:{port}"
                    with opener.open(candidate + "/api/v1/healthz", timeout=0.5):
                        origin = candidate
                    break
                except (OSError, ValueError, urllib.error.URLError):
                    continue
            if origin:
                break
            time.sleep(0.1)
        if not origin:
            raise TimeoutError("Kimi OAuth renewal server did not become ready")
        token = (KIMI / "server.token").read_text().strip()
        if not token:
            raise RuntimeError("Kimi local server authorization is unavailable")
        req = urllib.request.Request(origin + "/api/v1/oauth/usage",
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with opener.open(req, timeout=20) as response:
                result = json.load(response)
        except urllib.error.HTTPError as e:
            e.close()
            raise RuntimeError(f"Kimi local OAuth renewal HTTP {e.code}") from None
        if result.get("code") != 0 or (result.get("data") or {}).get("kind") != "ok":
            raise RuntimeError("Kimi could not renew authorization or read quota; check connectivity or kimi /login")
    finally:
        if p.poll() is None:
            p.terminate()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def kimi_request():
    """Read quota, asking the installed Kimi CLI to renew expiring OAuth credentials."""
    with (KIMI / "config.toml").open("rb") as f:
        provider = tomllib.load(f).get("providers", {}).get("managed:kimi-code", {})
    configured_base = provider.get("base_url") or "https://api.kimi.com/coding/v1"
    base = os.environ.get("KIMI_CODE_BASE_URL", configured_base).rstrip("/")
    if base not in KIMI_BASES:
        raise ValueError("Kimi quota requires an official Kimi Code API endpoint")

    def nonempty(value):
        return value.strip() if isinstance(value, str) else ""

    inline = nonempty(provider.get("api_key"))
    env_name = nonempty(provider.get("api_key_env"))
    modes = [name for name, present in (("api_key", bool(inline)),
             ("api_key_env", bool(env_name)), ("oauth", "oauth" in provider)) if present]
    if len(modes) > 1:
        raise ValueError(f"Kimi provider credential conflict: {', '.join(modes)} are mutually exclusive")
    oauth = provider.get("oauth") or {}
    path = None
    refreshed = False

    def read_credential():
        try:
            credential = json.loads(path.read_text())
            if not isinstance(credential, dict):
                raise ValueError()
            return credential
        except (OSError, ValueError):
            raise ValueError("Kimi OAuth credential unavailable; reauthorize with kimi /login") from None

    def expiring(credential):
        try:
            expiry = credential.get("expires_at")
            return bool(credential.get("refresh_token")) and (expiry is None or float(expiry) <= time.time() + 60)
        except (TypeError, ValueError):
            raise ValueError("invalid Kimi OAuth credential expiry") from None

    def renew(credential):
        nonlocal refreshed
        if expiring(credential) and not refreshed:
            refreshed = True  # at most one native helper per collection attempt
            try:
                kimi_refresh()
            except Exception:
                raise RuntimeError("Kimi OAuth renewal failed; check CLI connectivity or kimi /login") from None
            return read_credential()  # native CLI owns writes and the durable winner
        return credential

    if "oauth" in provider:
        key = nonempty(oauth.get("key"))
        name = key.removeprefix("oauth/")
        if not name or name.startswith(".") or "/" in name or "\\" in name:
            raise ValueError("invalid Kimi credential name")
        if oauth.get("storage", "file") != "file":
            raise ValueError("Kimi quota requires file-based OAuth credentials")
        # Match the native runtime's slot identity, but never switch credentials silently.
        has_override = any(k in os.environ for k in
                           ("KIMI_CODE_BASE_URL", "KIMI_CODE_OAUTH_HOST", "KIMI_OAUTH_HOST"))
        host = (os.environ.get("KIMI_CODE_OAUTH_HOST", os.environ.get("KIMI_OAUTH_HOST"))
                if has_override else oauth.get("oauth_host"))
        host = (host if host is not None else "https://auth.kimi.com").strip().rstrip("/")
        expected = "oauth/kimi-code"
        if host != "https://auth.kimi.com" or base != "https://api.kimi.com/coding/v1":
            identity = json.dumps({"oauthHost": host, "baseUrl": base}, separators=(",", ":"), ensure_ascii=False)
            expected = "oauth/kimi-code-env-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        if key != expected:
            raise ValueError("Kimi OAuth configuration/environment mismatch; align KIMI_CODE_BASE_URL, "
                             "KIMI_CODE_OAUTH_HOST and KIMI_OAUTH_HOST with config.toml, then use kimi /login")
        path = KIMI / "credentials" / f"{name}.json"
        token = nonempty(renew(read_credential()).get("access_token"))
    elif env_name:
        token = nonempty(os.environ.get(env_name))
        if not token:
            raise ValueError(f"Kimi api_key_env {env_name} is not set or is empty in the collector environment")
    else:
        token = inline
    if not token:
        raise ValueError("Kimi is not logged in; run kimi login")
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(2):  # one direct quota retry, only with a different durable OAuth token
        req = urllib.request.Request(base + "/usages", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "herdr-agent-kit/1"})
        try:
            with opener.open(req, timeout=8) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 401 and path:
                if attempt == 0:
                    winner = renew(read_credential())
                    new_token = nonempty(winner.get("access_token"))
                    if new_token and new_token != token:
                        token = new_token
                        continue
                # The native usage route does not force renewal of a fresh rejected token.
                raise RuntimeError("Kimi quota HTTP 401; reauthorize with kimi /login") from None
            if e.code == 401:
                raise RuntimeError("Kimi quota HTTP 401; check the configured API key") from None
            raise RuntimeError(f"Kimi quota HTTP {e.code}") from None
        except (OSError, urllib.error.URLError):
            raise RuntimeError("Kimi quota connection failed; check connectivity") from None
        except Exception:
            raise RuntimeError("Kimi quota request or response is invalid") from None
    try:
        wins = []
        for key, name in KIMI_NAMES.items():
            item = payload.get("usages", {}).get(key)
            if not isinstance(item, dict) or item.get("used_ratio") is None:
                continue
            reset = item.get("reset_time")
            resets_at = datetime.fromisoformat(reset.replace("Z", "+00:00")).timestamp() if reset else None
            win = window(name, max(0, min(100, float(item["used_ratio"]) * 100)), resets_at)
            # month_code is the Code share of the same monthly pool, not another limit.
            code = payload.get("usages", {}).get("limit_month_code")
            if key == "limit_month_total" and isinstance(code, dict) and code.get("used_ratio") is not None:
                code_used = round(max(0, min(100, float(code["used_ratio"]) * 100)), 1)
                win["breakdown"] = [{"name": "Kimi Code", "used": code_used},
                                    {"name": "Other Kimi", "used": round(max(0, win["used"] - code_used), 1)}]
            wins.append(win)
    except Exception:
        raise ValueError("Kimi quota response is invalid") from None
    if not wins:
        raise ValueError("Kimi quota response has no usage windows")
    return wins


def kimi(now):
    """Cache both success and failure; keep last known readings if the API is unavailable."""
    path = CACHE / "limits" / "kimi.json"
    try:
        cached = json.loads(path.read_text())
    except (OSError, ValueError):
        cached = {}
    if now - cached.get("attempted", 0) >= KIMI_EVERY:
        try:
            cached = {"seen": now, "windows": kimi_request(), "error": None}
        except Exception as e:
            cached["error"] = f"Kimi quota: {e}"
        cached["attempted"] = now
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cached))
        tmp.replace(path)
    return (cached if cached.get("windows") else None), cached.get("error")


# ---------------------------------------------------------------- any other agent

def command(argv, now):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if r.returncode:
        return None, (r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}"
    data = json.loads(r.stdout)
    return {"seen": data.get("seen", now), "resets": data.get("resets"),
            "windows": [window(w["name"], w["used"], w.get("resets_at")) for w in data.get("windows", [])]}, None


BUILTIN = {"claude": claude, "codex": codex, "kimi": kimi}


def config():
    if REGISTRY.exists():
        with REGISTRY.open("rb") as f:
            return tomllib.load(f).get("limits", {})
    return {}


def collect(now=None):
    now = int(now or time.time())
    cfg = config()
    commands = cfg.get("commands", {})
    agents = []
    for aid in cfg.get("agents", ["claude", "codex"]):
        try:
            if aid in commands:
                data, error = command(commands[aid], now)
            elif aid in BUILTIN:
                data, error = BUILTIN[aid](now)
            else:
                data, error = None, "no command in [limits.commands]"
        except Exception as e:  # one broken source must not hide the others
            data, error = None, str(e)[-200:]
        entry = {"id": aid, "seen": None, "windows": [], "resets": None, "error": error}
        if data:
            entry["seen"], entry["resets"] = data.get("seen"), data.get("resets")
            for w in data["windows"]:
                gone = bool(w["resets_at"] and w["resets_at"] <= now)
                entry["windows"].append({**w, "used": None if gone else w["used"], "reset": gone})
        agents.append(entry)
    return {"now": now, "show": cfg.get("show", "used"), "agents": agents}


def statusline(cmd):
    """Keep this session's rate_limits, then hand the same input to CMD."""
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw)
        if data.get("rate_limits"):
            CLAUDE.mkdir(parents=True, exist_ok=True)
            name = "".join(c for c in str(data.get("session_id") or "session") if c.isalnum() or c == "-")
            tmp = CLAUDE / f".{name}.{os.getpid()}.tmp"
            tmp.write_text(json.dumps({"seen": int(time.time()), "rate_limits": data["rate_limits"]}))
            tmp.replace(CLAUDE / f"{name}.json")
            for old in CLAUDE.glob("*.json"):  # sessions long gone
                if time.time() - old.stat().st_mtime > 8 * 86400:
                    old.unlink(missing_ok=True)
    except Exception:
        pass  # the status line itself must keep working
    if not cmd:
        return 0
    r = subprocess.run(cmd, input=raw, capture_output=True)
    sys.stdout.buffer.write(r.stdout)
    return r.returncode


def main(argv):
    if argv[:1] == ["statusline"]:
        rest = argv[1:]
        return statusline(rest[1:] if rest[:1] == ["--"] else rest)
    print(json.dumps(collect()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
