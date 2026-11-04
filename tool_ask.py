#!/usr/bin/env python3
"""Machine-wide registry of MCP folders, served by cheap worker agents over herdr-core.

Registry (this machine, git-ignored: local/)
  tool_ask.py scan ROOT [--depth N]           find folders with MCP configs (Claude, Codex, OpenCode)
  tool_ask.py list [--json]                   catalog: module, runtime, description, card state
  tool_ask.py refresh MODULE... | --all       rebuild tool cards via MCP tools/list
  tool_ask.py card MODULE [TOOL...]           print a module's tool card (or only these tools)

Projects
  tool_ask.py link PROJECT MODULE...          write PROJECT/.agent-tools/TOOLS.md, remember the link
  tool_ask.py unlink PROJECT [MODULE...]
  tool_ask.py sync [--dry-run]                rewrite TOOLS.md of linked projects whose cards changed
  tool_ask.py snippet MODULE...               print the TOOLS.md text

Calls
  tool_ask.py ask MODULE "REQUEST" [--write] [--timeout S] [--id ID]   answer file path on stdout
  tool_ask.py start|stop|status MODULE

A module is a folder with MCP servers configured for one agent runtime (claude, codex, opencode or
any command). Its worker runs in the herdr-core terminal `tool-<module>` started in that folder.
Callers get the answer file, never the MCP schemas or the secrets. One request at a time per module.

Exit codes: 0 ok, 1 failed/timeout, 2 usage/config, 3 busy, 4 needs a person, 5 write refused.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import mcp_probe

HUB = Path(__file__).resolve().parent
REGISTRY = Path(os.environ.get("HERDR_TOOL_AGENTS_REGISTRY", HUB / "local" / "registry.toml"))
LOCAL = REGISTRY.parent
CACHE = Path(os.environ.get("HERDR_TOOL_AGENTS_CACHE", Path.home() / ".cache" / "herdr-tool-agents"))
PASS_ENV = ["PATH", "LANG", "LC_ALL", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
            "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"]
WRITE_MARK = "WRITE ALLOWED BY USER"
# argv prefix, model flag, fresh-conversation command per runtime.
RUNTIMES = {"claude": (["claude"], "--model", "/clear"),
            "codex": (["codex"], "-m", "/new"),
            "opencode": (["opencode"], "-m", "/new")}
RULES = """[tool worker rules] You are a narrow tool agent of herdr-tool-agents. Use only the MCP tools of: {servers}.
Do not edit files except the answer file. Read-only unless this request contains the line `{mark}` and names
that exact change. Never print secrets, tokens, variable or environment values. Answer briefly: facts, ids,
statuses, links, decisive log lines; no speculation.{extra}"""
PROJECT_DIR = ".agent-tools"
POINTER = ("MCP tools of this machine: if `.agent-tools/TOOLS.md` exists, read it before looking for MCP "
           "tools; call them only as it describes.")


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
    m = {"runtime": "claude", "allow_write": False, "mcp": [], **reg[module]}
    if not m.get("workdir"):
        raise SystemExit(f"module {module}: workdir is required in {REGISTRY}")
    m["workdir"] = os.path.expanduser(m["workdir"])
    base, flag, reset = RUNTIMES.get(m["runtime"], ([], None, None))
    if "command" not in m:
        if not base:
            raise SystemExit(f"module {module}: runtime {m['runtime']!r} needs an explicit command")
        m["command"] = base + ([flag, m["model"]] if m.get("model") else [])
    m.setdefault("reset", reset)
    return reg, m


# ---------- cards ----------

def card_path(module):
    return LOCAL / "cards" / f"{module}.md"


def refresh(module):
    _, m = load(module)
    found = mcp_probe.servers(m["workdir"], m["runtime"])
    names = m["mcp"] or list(found)
    parts = []
    for n in names:
        if n not in found:
            parts.append(f"### {n}\n\nnot configured in {m['runtime']} config of the folder")
            continue
        try:
            tools = mcp_probe.list_tools(found[n], m["workdir"])
            if m.get("tools"):
                tools = [t for t in tools if any(fnmatch.fnmatchcase(f"{n}:{t['name']}", pat)
                                                 for pat in m["tools"])]
            parts.append(f"### {n} ({len(tools)} tools)\n\n{mcp_probe.card(tools)}")
        except Exception as e:  # one broken server must not hide the others
            parts.append(f"### {n}\n\nunavailable: {str(e)[:200]}")
    p = card_path(module)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n\n".join(parts) + "\n")
    return p


def card_text(module):
    p = card_path(module)
    return p.read_text().strip() if p.exists() else "(no card yet: run `tool_ask.py refresh` in the hub)"


def card_summary(module):
    """Server headings plus bare tool names: the part a project keeps in context."""
    out = []
    for line in card_text(module).splitlines():
        if line.startswith("### "):
            out.append(("\n" if out else "") + line[4:] + ":")
        elif line.startswith("- `"):
            name = line[3:line.index("(")]
            out[-1] += (" " if out[-1].endswith(":") else ", ") + f"`{name}`" + ("*" if "[write]" in line else "")
        elif line.strip():
            out.append(line)
    return "\n".join(out)


def tools_md(reg, mods):
    unknown = [x for x in mods if x not in reg]
    if unknown:
        raise SystemExit(f"unknown module(s): {', '.join(unknown)}")
    out = ["# MCP tools of this machine", "",
           "Generated by herdr-tool-agents (`link`, `sync`). Do not edit; machine-local, not for git.", "",
           "These MCP servers are not loaded in this project. Each module runs in a cheap worker agent",
           "through herdr. Send one narrow request naming the tool and its parameters:", "",
           f'`python3 {HUB / "tool_ask.py"} ask <module> "<request>"` prints the path of the answer file;',
           "read that file. Read-only by default. Add `--write` only after the person approved that exact",
           "change. Exit codes: 0 ready, 1 failed, 3 busy (retry later), 4 needs the person (answer the",
           "worker in Herdr), 5 write refused.",
           "`*` marks tools that change something. Parameters of every tool (`name(required, optional?)`):",
           f'`python3 {HUB / "tool_ask.py"} card <module> <tool>...`.']
    for mid in mods:
        mod = reg[mid]
        out += ["", f"## `{mid}` - {mod.get('description', '')}", ""]
        if mod.get("allow_write"):
            out += ["Writes allowed with `--write` after the person's approval.", ""]
        out.append(card_summary(mid))
    return "\n".join(out) + "\n"


# ---------- projects ----------

def links():
    p = LOCAL / "links.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_links(data):
    (LOCAL / "links.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def write_project(project, reg, mods):
    d = Path(project) / PROJECT_DIR
    d.mkdir(exist_ok=True)
    text = tools_md(reg, mods)
    changed = not (d / "TOOLS.md").exists() or (d / "TOOLS.md").read_text() != text
    if changed:
        (d / "TOOLS.md").write_text(text)
    return changed


def wire_project(project):
    """Keep .agent-tools out of git (local exclude) and point the agent files at it."""
    pr = Path(project)
    exclude = pr / ".git" / "info" / "exclude"
    if exclude.parent.exists():
        cur = exclude.read_text() if exclude.exists() else ""
        if f"/{PROJECT_DIR}/" not in cur.split("\n"):
            exclude.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n") + f"/{PROJECT_DIR}/\n")
    targets = [pr / "AGENTS.md"]
    claude = pr / "CLAUDE.md"
    if claude.exists() and "@AGENTS.md" not in claude.read_text():
        targets.append(claude)
    for t in targets:
        cur = t.read_text() if t.exists() else ""
        if ".agent-tools/TOOLS.md" not in cur:
            t.write_text(cur + ("\n" if cur and not cur.endswith("\n") else "") + ("\n" if cur else "") + POINTER + "\n")


def link(project, mods):
    reg, _ = load()
    project = str(Path(project).resolve())
    data = links()
    data[project] = sorted(set(data.get(project, [])) | set(mods))
    write_project(project, reg, data[project])
    wire_project(project)
    save_links(data)
    print(f"{project}/{PROJECT_DIR}/TOOLS.md: {', '.join(data[project])}")


def unlink(project, mods):
    project = str(Path(project).resolve())
    data = links()
    keep = [m for m in data.get(project, []) if mods and m not in mods]
    if keep:
        data[project] = keep
        reg, _ = load()
        write_project(project, reg, keep)
    else:
        data.pop(project, None)
        (Path(project) / PROJECT_DIR / "TOOLS.md").unlink(missing_ok=True)
    save_links(data)
    print(f"{project}: {', '.join(keep) or 'unlinked'}")


def sync(dry):
    reg, _ = load()
    rc = 0
    for project, mods in links().items():
        if not Path(project).is_dir():
            print(f"missing  {project}")
            rc = 1
            continue
        gone = [m for m in mods if m not in reg]
        if gone:
            print(f"stale    {project}: modules not in registry: {', '.join(gone)}")
            mods = [m for m in mods if m in reg]
        if dry:
            p = Path(project) / PROJECT_DIR / "TOOLS.md"
            changed = not p.exists() or p.read_text() != tools_md(reg, mods)
        else:
            changed = write_project(project, reg, mods)
        print(f"{'changed' if changed else 'same   '}  {project}")
    return rc


def scan(root, depth):
    reg, _ = load() if REGISTRY.exists() else ({}, None)
    known = {os.path.realpath(os.path.expanduser(m.get("workdir", ""))): k for k, m in reg.items()}
    root = Path(root).resolve()
    for dirpath, dirnames, _ in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".venv", "venv", "__pycache__")
                       and len(rel.parts) < depth]
        found = mcp_probe.detect(dirpath)
        if found:
            mark = f"registered as {known[os.path.realpath(dirpath)]}" if os.path.realpath(dirpath) in known else "new"
            rts = "; ".join(f"{rt}: {', '.join(n)}" for rt, n in found.items())
            print(f"{dirpath}  [{mark}]  {rts}")


# ---------- workers ----------

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
    if not wd.is_dir():
        raise SystemExit(f"{wd} missing")
    if m["runtime"] in RUNTIMES and not mcp_probe.servers(wd, m["runtime"]):
        raise SystemExit(f"{wd}: no MCP servers in its {m['runtime']} config")
    argv = ["env", *[f"{k}={os.environ[k]}" for k in PASS_ENV if k in os.environ], *m["command"]]
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
    if write and not m.get("allow_write"):
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
    extra = LOCAL / "modules" / f"{module}.md"
    rules = RULES.format(servers=", ".join(m["mcp"]) or "this folder", mark=WRITE_MARK,
                         extra=" " + " ".join(extra.read_text().split()) if extra.exists() else "")
    text = (f"{rules}\n\n{request}\n\n" + (f"{WRITE_MARK}: exactly the change named above.\n" if write else
                                           "Read-only: change nothing.\n")
            + f"Write the brief answer to {out}; its last line is exactly DONE (or FAILED: <why>).")
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").add_argument("--json", action="store_true")
    s = sub.add_parser("scan")
    s.add_argument("root")
    s.add_argument("--depth", type=int, default=4)
    s = sub.add_parser("refresh")
    s.add_argument("modules", nargs="*")
    s.add_argument("--all", action="store_true")
    s = sub.add_parser("card")
    s.add_argument("module")
    s.add_argument("tools", nargs="*")
    s = sub.add_parser("link")
    s.add_argument("project")
    s.add_argument("modules", nargs="+")
    s = sub.add_parser("unlink")
    s.add_argument("project")
    s.add_argument("modules", nargs="*")
    sub.add_parser("sync").add_argument("--dry-run", action="store_true")
    sub.add_parser("snippet").add_argument("modules", nargs="+")
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
                state = "card" if card_path(mid).exists() else "no card"
                print(f"{mid} [{mod.get('runtime', 'claude')}, {state}"
                      f"{', write' if mod.get('allow_write') else ''}]: {mod.get('description', '')}")
        return 0
    if a.cmd == "scan":
        scan(a.root, a.depth)
        return 0
    if a.cmd == "refresh":
        reg, _ = load()
        mods = list(reg) if a.all else a.modules
        if not mods:
            raise SystemExit("name modules or --all")
        for mid in mods:
            p = refresh(mid)
            bad = p.read_text().count("unavailable:") + p.read_text().count("not configured")
            print(f"{mid}: {p}{f' ({bad} server problem(s))' if bad else ''}")
        return 0
    if a.cmd == "card":
        load(a.module)
        text = card_text(a.module)
        if a.tools:
            text = "\n".join(x for x in text.splitlines() if any(x.startswith(f"- `{t}(") for t in a.tools))
        print(text or "no such tool in the card")
        return 0
    if a.cmd == "link":
        link(a.project, a.modules)
        return 0
    if a.cmd == "unlink":
        unlink(a.project, a.modules)
        return 0
    if a.cmd == "sync":
        return sync(a.dry_run)
    if a.cmd == "snippet":
        reg, _ = load()
        print(tools_md(reg, a.modules), end="")
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
        print(json.dumps({"name": name, "running": bool(t), "status": status_of(t), "runtime": m["runtime"],
                          "workdir": m["workdir"], "command": m["command"], "card": card_path(a.module).exists()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
