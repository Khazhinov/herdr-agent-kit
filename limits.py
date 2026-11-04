#!/usr/bin/env python3
"""Usage limits of agent runtimes, for the Herdr App mod `agent-limits`.

  limits.py                         the configured agents' limits as JSON
  limits.py statusline -- CMD...    Claude Code statusLine wrapper: keeps this
                                    session's rate_limits, then runs CMD with
                                    the same input and prints its output

Settings: `[limits]` of the registry (registry.example.toml):
  agents = ["claude", "codex"]        order shown; default: these two
  show = "used"                       or "left": what the percent means
  [limits.commands]
  kimi = ["/path/to/kimi-limits"]     any other agent: a command printing
                                      {"windows": [{"name", "used", "resets_at"}]}

Output: {"now", "show", "agents": [{"id", "seen", "windows", "error"}]}; a window
is {"name", "used" (percent), "resets_at" (epoch s), "reset"}. `reset` true: the
window renewed after the last reading, so `used` is unknown (null).
Standard library only.
"""
import json
import os
import select
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HUB = Path(__file__).resolve().parent
REGISTRY = Path(os.environ.get("HERDR_AGENT_KIT_REGISTRY") or HUB / "local" / "registry.toml")
CACHE = Path(os.environ.get("HERDR_AGENT_KIT_CACHE") or Path.home() / ".cache" / "herdr-agent-kit")
CLAUDE = CACHE / "limits" / "claude"
# herdr-gsd-template keeps its own taps of the same statusLine input; read them too.
ORCH = Path(os.environ.get("ORCH_CACHE_DIR") or Path.home() / ".cache" / "herdr-orch")
CODEX = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
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
                    return [window(span(w.get("windowDurationMins")), w["usedPercent"], w.get("resetsAt"))
                            for w in wins if w and w.get("usedPercent") is not None]
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
            rpc = {"seen": now, "windows": codex_rpc()}
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(rpc))
            tmp.replace(cache)
        except Exception as e:  # no binary, logged out, protocol change
            error = f"app-server: {e}"
    seen, wins = codex_logs()
    best = max([r for r in (rpc, {"seen": seen, "windows": wins}) if r and r.get("windows")],
               key=lambda r: r["seen"] or 0, default=None)
    return (best, None) if best else (None, f"{error or 'app-server: no limits'}; no limits in session logs")


# ---------------------------------------------------------------- any other agent

def command(argv, now):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if r.returncode:
        return None, (r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}"
    data = json.loads(r.stdout)
    return {"seen": data.get("seen", now),
            "windows": [window(w["name"], w["used"], w.get("resets_at")) for w in data.get("windows", [])]}, None


BUILTIN = {"claude": claude, "codex": codex}


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
        entry = {"id": aid, "seen": None, "windows": [], "error": error}
        if data:
            entry["seen"] = data.get("seen")
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
