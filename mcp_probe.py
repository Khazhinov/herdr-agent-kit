"""Read MCP server configs of Claude Code, Codex and OpenCode folders and list
their tools over the MCP protocol (stdio or streamable HTTP). Standard library only.
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
CONFIG_FILES = {"claude": [".mcp.json"], "codex": [".codex/config.toml"],
                "opencode": ["opencode.json", "opencode.jsonc"]}


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


def servers(workdir, runtime):
    """{name: {"command": [...], "env": {...}} | {"url": ..., "headers": {...}}} for a folder."""
    wd = Path(workdir)
    found = {}
    for rel in CONFIG_FILES.get(runtime, []):
        p = wd / rel
        if not p.exists():
            continue
        if runtime == "claude":
            for n, c in json.loads(p.read_text()).get("mcpServers", {}).items():
                found[n] = ({"url": c["url"], "headers": c.get("headers", {})} if c.get("url") else
                            {"command": [c["command"], *c.get("args", [])], "env": c.get("env", {})})
        elif runtime == "codex":
            for n, c in tomllib.loads(p.read_text()).get("mcp_servers", {}).items():
                if c.get("enabled", True) is False:
                    continue
                found[n] = ({"url": c["url"], "headers": c.get("http_headers", {})} if c.get("url") else
                            {"command": [c["command"], *c.get("args", [])], "env": c.get("env", {})})
        else:
            for n, c in json.loads(strip_jsonc(p.read_text())).get("mcp", {}).items():
                if c.get("enabled", True) is False:
                    continue
                found[n] = ({"url": c["url"], "headers": c.get("headers", {})} if c.get("type") == "remote" else
                            {"command": list(c["command"]), "env": c.get("environment", {})})
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
        "clientInfo": {"name": "herdr-tool-agents", "version": "1"}}


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
