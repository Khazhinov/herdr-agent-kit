"""Read MCP server configs of agent folders and list their tools over the MCP protocol
(stdio or streamable HTTP). The file shape is detected from its content, so any runtime whose
config uses one of the common shapes works; the runtime name only picks default file names.
Standard library only.
Secrets from configs are passed to the server process, never returned or printed."""
import json
import os
import queue
import re
import subprocess
import threading
import tomllib
import urllib.request
from pathlib import Path

PROTOCOL = "2025-06-18"
# Default config files per runtime. Any other runtime: set `mcp_config` in the registry.
CONFIG_FILES = {"claude": [".mcp.json"], "codex": [".codex/config.toml"],
                "opencode": ["opencode.json", "opencode.jsonc"], "gemini": [".gemini/settings.json"],
                "cursor": [".cursor/mcp.json"], "vscode": [".vscode/mcp.json"]}


def strip_jsonc(text):
    out, i, s = [], 0, False
    while i < len(text):
        c = text[i]
        if s:
            out.append(c)
            if c == "\\":
                out.append(text[i + 1:i + 2])
                i += 1
            elif c == '"':
                s = False
        elif c == '"':
            s = True
            out.append(c)
        elif text.startswith("//", i):
            i = text.find("\n", i) - 1 if "\n" in text[i:] else len(text)
        elif text.startswith("/*", i):
            i = text.find("*/", i) + 1
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _expand(v):
    if isinstance(v, str):
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}",
                      lambda m: os.environ.get(m.group(1), m.group(2) or ""), v)
    if isinstance(v, list):
        return [_expand(x) for x in v]
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    return v


def _entry(c):
    if c.get("enabled", True) is False or c.get("disabled") is True:
        return None
    if c.get("url") or c.get("httpUrl") or c.get("type") == "remote":
        return {"url": c.get("url") or c.get("httpUrl"), "headers": c.get("headers") or c.get("http_headers") or {}}
    cmd = c["command"]
    return {"command": (list(cmd) if isinstance(cmd, list) else [cmd]) + list(c.get("args", [])),
            "env": c.get("env") or c.get("environment") or {}}


def read_config(path):
    """{name: entry} from one file. Shapes: mcpServers (Claude, Gemini, Cursor and most others),
    servers (VS Code), mcp (OpenCode), [mcp_servers] (Codex TOML)."""
    p = Path(path)
    data = tomllib.loads(p.read_text()) if p.suffix == ".toml" else json.loads(strip_jsonc(p.read_text()))
    block = next((data[k] for k in ("mcpServers", "mcp_servers", "servers", "mcp") if isinstance(data.get(k), dict)), {})
    return {n: e for n, c in block.items() if (e := _entry(c))}


def servers(workdir, runtime, config=None):
    """{name: {"command": [...], "env": {...}} | {"url": ..., "headers": {...}}} for a folder."""
    wd = Path(workdir)
    found = {}
    for rel in ([config] if config else CONFIG_FILES.get(runtime, [])):
        if (wd / rel).exists():
            found.update(read_config(wd / rel))
    if runtime == "claude" and not config:  # `claude mcp add` default (local) scope, overrides .mcp.json
        user = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home())) / ".claude.json"
        if user.exists():
            local = json.loads(user.read_text()).get("projects", {}).get(str(wd.resolve()), {})
            found.update({n: e for n, c in (local.get("mcpServers") or {}).items() if (e := _entry(c))})
    return _expand(found)


def detect(workdir):
    """Runtimes whose MCP config exists in the folder, with server names."""
    out = {}
    for rt in CONFIG_FILES:
        try:
            names = list(servers(workdir, rt))
        except (ValueError, KeyError, TypeError, tomllib.TOMLDecodeError):
            names = ["<unreadable config>"]
        if names:
            out[rt] = names
    return out


def _rpc(mid, method, params=None):
    m = {"jsonrpc": "2.0", "method": method}
    if mid is not None:
        m["id"] = mid
    if params is not None:
        m["params"] = params
    return m


INIT = {"protocolVersion": PROTOCOL, "capabilities": {},
        "clientInfo": {"name": "herdr-agent-kit", "version": "1"}}


def _list_stdio(cfg, cwd, timeout):
    p = subprocess.Popen(cfg["command"], cwd=cwd, env={**os.environ, **cfg.get("env", {})},
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    q = queue.Queue()
    threading.Thread(target=lambda: [q.put(x) for x in p.stdout], daemon=True).start()

    def call(mid, method, params=None):
        p.stdin.write(json.dumps(_rpc(mid, method, params)) + "\n")
        p.stdin.flush()
        if mid is None:
            return None
        while True:
            line = q.get(timeout=timeout)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "error"))
                return msg["result"]
    try:
        call(1, "initialize", INIT)
        call(None, "notifications/initialized")
        tools, cursor, n = [], None, 2
        while True:
            r = call(n, "tools/list", {"cursor": cursor} if cursor else {})
            tools += r.get("tools", [])
            cursor, n = r.get("nextCursor"), n + 1
            if not cursor:
                return tools
    except queue.Empty:
        raise RuntimeError(f"no answer in {timeout}s")
    finally:
        p.kill()
        p.wait()
        p.stdin.close()


def _list_http(cfg, timeout):
    session = {}

    def call(mid, method, params=None):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             **cfg.get("headers", {}), **session}
        req = urllib.request.Request(cfg["url"], json.dumps(_rpc(mid, method, params)).encode(), h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.headers.get("Mcp-Session-Id"):
                session["Mcp-Session-Id"] = r.headers["Mcp-Session-Id"]
            body = r.read().decode()
        if mid is None:
            return None
        chunks = [x[5:].strip() for x in body.splitlines() if x.startswith("data:")] or [body]
        for c in chunks:
            msg = json.loads(c)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "error"))
                return msg["result"]
        raise RuntimeError("no matching response")
    call(1, "initialize", INIT)
    call(None, "notifications/initialized")
    tools, cursor, n = [], None, 2
    while True:
        r = call(n, "tools/list", {"cursor": cursor} if cursor else {})
        tools += r.get("tools", [])
        cursor, n = r.get("nextCursor"), n + 1
        if not cursor:
            return tools


def list_tools(cfg, cwd, timeout=90):
    return _list_http(cfg, timeout) if "url" in cfg else _list_stdio(cfg, cwd, timeout)


def card(tools):
    """Compact tool lines: name(required, optional?) [write] - first sentence."""
    lines = []
    for t in sorted(tools, key=lambda t: t["name"]):
        schema = t.get("inputSchema") or {}
        req = set(schema.get("required", []))
        params = ", ".join(k if k in req else k + "?" for k in (schema.get("properties") or {}))
        ann = t.get("annotations") or {}
        mark = " [write]" if ann.get("readOnlyHint") is False or ann.get("destructiveHint") else ""
        desc = re.split(r"(?<=[.!?])\s", " ".join((t.get("description") or "").split()), maxsplit=1)[0][:160]
        lines.append(f"- `{t['name']}({params})`{mark} - {desc}")
    return "\n".join(lines)
