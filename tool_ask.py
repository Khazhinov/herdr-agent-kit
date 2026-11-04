#!/usr/bin/env python3
"""Reusable tool agents over herdr-core.

  tool_ask.py list [--json]                      compact capability catalog
  tool_ask.py snippet [MODULE...]                block for a project's AGENTS.md
  tool_ask.py ask MODULE "REQUEST" [--write] [--timeout S] [--id ID]
                                                 answer file path on stdout
  tool_ask.py start|stop|status MODULE

A module is a folder on this machine with MCP servers configured inside it,
registered in local/registry.toml. Its worker is a persistent cheap agent
(default: Claude Haiku) in the herdr-core terminal `tool-<module>`, started
in that folder. Callers get the answer file, never the MCP schema or the
secrets. One request at a time per module.

Exit codes: 0 answer ready, 1 failed/timeout, 2 usage/config, 3 busy,
4 needs a person (dialog, permission), 5 write refused.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HUB = Path(__file__).resolve().parent
REGISTRY = Path(os.environ.get("HERDR_TOOL_AGENTS_REGISTRY", HUB / "local" / "registry.toml"))
CACHE = Path(os.environ.get("HERDR_TOOL_AGENTS_CACHE", Path.home() / ".cache" / "herdr-tool-agents"))
PASS_ENV = ["PATH", "LANG", "LC_ALL", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
            "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"]
WRITE_MARK = "WRITE ALLOWED BY USER"
DEFAULTS = {"command": ["claude", "--model", "haiku"], "reset": "/clear", "allow_write": False, "mcp": []}
RULES = """You are a narrow tool agent. Requests arrive from other agents through tool_ask.py.
1. Use only the MCP tools of: {servers}. Do not edit files except the answer file.
2. Read-only by default. Change something only when the request contains the line
   `WRITE ALLOWED BY USER` and names that exact change.
3. Never print secrets, tokens, variable values or environment values.
4. Answer briefly: facts, ids, statuses, links, the decisive log lines. No speculation.
5. Write the answer to the file named in the request. Its last line is exactly `DONE`
   (or `FAILED: <one line why>`).
"""


def hc(*args, check=True):
    p = subprocess.run([*os.environ.get("ORCH_HC", "hc").split(), *args], capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"hc {' '.join(args[:2])}: {p.stderr.strip()[:300]}")
    out = p.stdout.strip()
    try:
        return json.loads(out) if out.startswith(("{", "[")) else out
    except json.JSONDecodeError:
        return out


def load(module=None):
    if not REGISTRY.exists():
        raise SystemExit(f"{REGISTRY} missing: copy registry.example.toml there and register folders (AGENTS.md)")
    with open(REGISTRY, "rb") as f:
        reg = tomllib.load(f).get("modules", {})
    if module is None:
        return reg, None
    if module not in reg:
        raise SystemExit(f"unknown module {module!r}; known: {', '.join(reg)}")
    m = {**DEFAULTS, **reg[module]}
    if not m.get("workdir"):
        raise SystemExit(f"module {module}: workdir is required in {REGISTRY}")
    m["workdir"] = os.path.expanduser(m["workdir"])
    return reg, m


def worker_rules(module, m):
    extra = REGISTRY.parent / "modules" / f"{module}.md"
    return RULES.format(servers=", ".join(m["mcp"]) or "this folder") + (
        "\n" + extra.read_text() if extra.exists() else "")


def term(module):
    name = f"tool-{module}"[:32]
    for t in hc("ls", "--json").get("terminals", []):
        if t.get("name") == name:
            return name, t
    return name, None


def status_of(t):
    st = (t or {}).get("status") or {}
    return st.get("state") if isinstance(st, dict) else st


def start(module, m):
    name, t = term(module)
    if t:
        return name, t
    wd = Path(m["workdir"])
    claude = Path(m["command"][0]).name == "claude"
    if claude and not (wd / ".mcp.json").exists():
        raise SystemExit(f"{wd}/.mcp.json missing: configure the module's MCP servers in that folder")
    argv = ["env", *[f"{k}={os.environ[k]}" for k in PASS_ENV if k in os.environ], *m["command"]]
    if claude:
        argv += ["--append-system-prompt", worker_rules(module, m)]
    hc("run", "--bg", "--json", "--name", name, "--cwd", str(wd), "--no-parent",
       "--label", f"orch.tool={module}", "--", *argv)
    # Agents report status (OSC 7501) once their UI is ready.
    for _ in range(45):
        _, t = term(module)
        if status_of(t) is not None:
            break
        time.sleep(2)
    if status_of(t) is None:
        print(f"{name} started but reports no status: it may wait for a person (trust/login). "
              f"Answer it in Herdr App or `hc attach {name}`.", file=sys.stderr)
        raise SystemExit(4)
    return name, t


def audit(rec):
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(CACHE / "audit.jsonl", "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def last_line(path):
    try:
        lines = [x.strip() for x in Path(path).read_text().splitlines() if x.strip()]
    except OSError:
        return None
    return lines[-1] if lines else None


def ask(module, request, write=False, timeout=300, rid=None):
    reg, m = load(module)
    if write and not (m.get("allow_write") and reg[module].get("write_capabilities")):
        print(f"write refused: module {module} has allow_write = false (set it in {REGISTRY} after the user agrees)",
              file=sys.stderr)
        return 5
    name, t = start(module, m)
    if status_of(t) in ("working", "blocked"):
        print(f"{name} is {status_of(t)}; one request at a time", file=sys.stderr)
        return 3
    rid = rid or time.strftime("%Y%m%d-%H%M%S")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", rid):
        raise SystemExit("invalid --id")
    out = CACHE / module / f"{rid}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    if m.get("reset"):
        hc("send", "--wait", "--timeout", "60", name, m["reset"], "--json", check=False)
    text = (f"{request}\n\n" + (f"{WRITE_MARK}: exactly the change named above.\n" if write else
                                "Read-only: change nothing.\n")
            + f"Never print secrets. Write the brief answer to {out}; its last line is exactly DONE "
              f"(or FAILED: <why>).")
    t0 = time.time()
    audit({"at": t0, "module": module, "id": rid, "write": write, "request": request[:500], "answer": str(out)})
    deadline = t0 + timeout
    hc("send", name, text, "--json")
    while time.time() < deadline:
        tail = last_line(out)
        if tail == "DONE" or (tail or "").startswith("FAILED"):
            break
        r = hc("wait", name, "--timeout", str(int(max(5, min(170, deadline - time.time())))), "--json", check=False)
        st = r.get("status") if isinstance(r, dict) else None
        if st == "blocked":
            print(f"{name} is blocked (permission or question): answer it in Herdr App or `hc attach {name}`",
                  file=sys.stderr)
            return 4
        if isinstance(r, dict) and r.get("ended") and st in ("idle", "done"):
            # Turn ended: the answer must be there (allow the file system a moment).
            time.sleep(1)
            if not last_line(out):
                print(f"{name} finished its turn without writing {out}", file=sys.stderr)
                return 1
    tail = last_line(out)
    audit({"at": time.time(), "module": module, "id": rid, "result": tail, "seconds": round(time.time() - t0)})
    if tail == "DONE":
        print(out)
        return 0
    print(f"no answer ({tail or 'timeout'}); see {out} and `hc read {name}`", file=sys.stderr)
    return 1


def snippet(reg, mods):
    unknown = [x for x in mods if x not in reg]
    if unknown:
        raise SystemExit(f"unknown module(s): {', '.join(unknown)}")
    lines = ["## Tool agents (MCP through herdr)", "",
             "These MCP servers run in separate cheap worker agents on this machine; their tools are not",
             "loaded here. Ask a worker instead of looking for the tools:", "",
             f'`python3 {HUB / "tool_ask.py"} ask <module> "<one narrow request>"` prints the path of the',
             "answer file; read that file. Read-only by default. Add `--write` only after the person approved",
             "that exact change. Exit codes: 0 ready, 1 failed, 3 busy (retry later), 4 needs the person",
             "(answer the worker in Herdr), 5 write refused.", "", "| Module | What it knows | Read | Write |",
             "|---|---|---|---|"]
    for mid in mods:
        mod = reg[mid]
        lines.append(f"| `{mid}` | {mod.get('description', '')} | {', '.join(mod.get('capabilities', []))} | "
                     f"{', '.join(mod.get('write_capabilities', [])) or '-'} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--json", action="store_true")
    sub.add_parser("snippet").add_argument("modules", nargs="*")
    s = sub.add_parser("ask")
    s.add_argument("module")
    s.add_argument("request")
    s.add_argument("--write", action="store_true")
    s.add_argument("--timeout", type=int, default=300)
    s.add_argument("--id")
    for c in ("start", "stop", "status"):
        sub.add_parser(c).add_argument("module")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        reg, _ = load()
        if a.json:
            print(json.dumps(reg, indent=1))
        else:
            for mid, mod in reg.items():
                print(f"{mid}: {mod['description']}")
                print(f"  read:  {', '.join(mod.get('capabilities', []))}")
                print(f"  write: {', '.join(mod.get('write_capabilities', [])) or '-'} (needs allow_write and --write)")
        return 0
    if a.cmd == "snippet":
        reg, _ = load()
        print(snippet(reg, a.modules or list(reg)))
        return 0
    if a.cmd == "ask":
        return ask(a.module, a.request, a.write, a.timeout, a.id)
    _, m = load(a.module)
    if a.cmd == "start":
        name, t = start(a.module, m)
        print(f"{name}: {status_of(t)}")
    elif a.cmd == "stop":
        name, t = term(a.module)
        if t:
            hc("kill", name, "--json", check=False)
            hc("rm", name, "--json", check=False)
        print(f"{name} stopped" if t else f"{name} not running")
    else:
        name, t = term(a.module)
        print(json.dumps({"name": name, "running": bool(t), "status": status_of(t), "workdir": m["workdir"],
                          "mcp_config": os.path.exists(os.path.join(m["workdir"], ".mcp.json"))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
