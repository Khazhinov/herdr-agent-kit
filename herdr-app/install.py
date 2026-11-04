#!/usr/bin/env python3
"""Install the kit's Herdr App mods: space-groups and agent-limits.

  herdr-app/install.py [--claude-statusline] [--dry-run]

1. Copies mods/space-groups and mods/agent-limits into the app home
   (`HERDR_APP_HOME`, else ~/.config/herdr-app here, else the Windows
   profile's when this runs in WSL).
2. Writes mods/agent-limits/source.json: how the app runs limits.py (on which
   herdr machine; found by this core's id in the app's machine list).
3. Every layout of the person's own (not Herdr's main, terminals, win98) that
   has the shipped sidebar: the `spaces` block shows space-groups, and an
   `agent-limits` block goes under the agents list. Only these two blocks are
   touched. When only Herdr's main is there, a copy `kit` is made from it.
4. --claude-statusline: wraps the statusLine of ~/.claude/settings.json with
   `limits.py statusline -- <the old command>`; the visible line is unchanged.
Rerun after an app update, a change of [limits] agents, or a GSD template
install that regenerated its layout.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
MODS = ("space-groups", "agent-limits")
SHIPPED = {"main", "terminals", "win98"}
sys.path.insert(0, str(KIT))
import limits  # noqa: E402  (agents list for the block height)


def windows_profile():
    default = Path("/mnt", "c", "Windows", "System32", "cmd.exe")  # WSL's standard mount of C:
    cmd = shutil.which("cmd.exe") or (str(default) if default.exists() else None)
    if not cmd:
        return None
    out = subprocess.run([cmd, "/c", "echo %USERPROFILE%"], capture_output=True, text=True, cwd=str(Path("/mnt", "c"))).stdout.strip()
    return Path(subprocess.run(["wslpath", out], capture_output=True, text=True).stdout.strip()) if out else None


def app_home():
    """(home, app binary, hc binary the app uses)."""
    if os.environ.get("HERDR_APP_HOME"):
        return Path(os.environ["HERDR_APP_HOME"]), os.environ.get("HERDR_APP_BIN", "herdr-app"), "hc"
    if (Path.home() / ".config" / "herdr-app").is_dir():
        return Path.home() / ".config" / "herdr-app", "herdr-app", "hc"
    win = windows_profile()
    if win:
        progs = win / "AppData" / "Local" / "Programs"
        return win / ".config" / "herdr-app", str(progs / "herdr-app" / "herdr-app.exe"), str(progs / "herdr-core" / "hc.exe")
    sys.exit("Herdr App home not found: set HERDR_APP_HOME")


def machine(app_hc):
    """The app's name for this core, or None when the app runs on it."""
    if app_hc == "hc":
        return None
    me = json.loads(subprocess.run(["hc", "server", "status", "--json"], capture_output=True, text=True).stdout)["core"]["id"]
    listed = json.loads(subprocess.run([app_hc, "machine", "ls", "--json"], capture_output=True, text=True, cwd=str(Path("/mnt", "c"))).stdout)
    for m in listed.get("machines", []):
        if m.get("id") == me:
            return m["name"]
    sys.exit("this core is not among the app's machines (herdr-app: Add machine)")


def walk(node):
    yield node
    for c in node.get("children", []):
        yield from walk(c)


def patch(layout, height):
    """Point the spaces block at space-groups; put agent-limits under the lists."""
    nodes = {n.get("id"): n for n in walk(layout)}
    spaces, lists = nodes.get("spaces"), nodes.get("sidebar-lists")
    if not spaces or spaces.get("content", {}).get("mod") not in ("spaces", "space-groups") or not lists:
        return False
    spaces["content"] = {**spaces["content"], "mod": "space-groups"}
    parent = next(n for n in walk(layout) if lists in n.get("children", []))
    kids = [c for c in parent["children"] if c.get("id") != "agent-limits"]
    kids.insert(kids.index(lists) + 1, {"id": "agent-limits", "type": "block", "size": {"px": height},
                                        "content": {"mod": "agent-limits"}})
    parent["children"] = kids
    return True


def wrap_statusline(dry):
    path = Path.home() / ".claude" / "settings.json"
    data = json.loads(path.read_text())
    cur = (data.get("statusLine") or {}).get("command", "")
    if "limits.py statusline" in cur:
        print("claude statusLine: already wrapped")
        return
    tail = f" -- sh -c {shlex.quote(cur)}" if cur else ""
    new = f"python3 {shlex.quote(str(KIT / 'limits.py'))} statusline{tail}"
    print(f"claude statusLine:\n  was: {cur or '(none)'}\n  now: {new}")
    if not dry:
        shutil.copy2(path, path.with_name("settings.json.pre-agent-limits"))
        data["statusLine"] = {**(data.get("statusLine") or {"type": "command"}), "command": new}
        path.write_text(json.dumps(data, indent=2) + "\n")


def main(argv):
    dry = "--dry-run" in argv
    home, app, app_hc = app_home()
    src = KIT / "herdr-app"
    on = machine(app_hc)
    agents = limits.config().get("agents", ["claude", "codex"])
    height = 28 + 30 * len(agents) + 6
    print(f"app home: {home}\nmachine: {on or 'this one'}")
    if not dry:
        for m in MODS:
            (home / "mods" / m).mkdir(parents=True, exist_ok=True)
            for f in ("mod.json", "main.js"):  # mod.json first: a folder without it gets docked by the app
                shutil.copy2(src / m / f, home / "mods" / m / f)
        (home / "mods" / "agent-limits" / "source.json").write_text(
            json.dumps({"on": on, "argv": ["python3", str(KIT / "limits.py")]}, indent=2) + "\n")
    layouts = home / "layouts"
    done = []
    for p in sorted(layouts.glob("*.json")):
        if p.stem in SHIPPED:
            continue
        data = json.loads(p.read_text())
        if patch(data, height):
            done.append(p.stem)
            if not dry:
                p.write_text(json.dumps(data, indent=2) + "\n")
    if not done:
        data = json.loads((layouts / "main.json").read_text())
        data.pop("preview", None)
        data["name"] = "Herdr Kit"
        if patch(data, height):
            done.append("kit")
            if not dry:
                (layouts / "kit.json").write_text(json.dumps(data, indent=2) + "\n")
            print("switch: herdr-app do layout.switch --name kit")
    print("layouts:", ", ".join(done) or "none with the shipped sidebar")
    if "--claude-statusline" in argv:
        wrap_statusline(dry)
    if not dry:
        for m in MODS:
            target = home / "mods" / m
            arg = subprocess.run(["wslpath", "-w", str(target)], capture_output=True, text=True).stdout.strip() if app.endswith(".exe") else str(target)
            r = subprocess.run([app, "check", arg], capture_output=True, text=True, cwd=str(Path("/mnt", "c")) if app.endswith(".exe") else None)
            print(f"check {m}: {'ok' if r.returncode == 0 else 'FAILED'} {(r.stdout + r.stderr).strip()[-400:]}")
            if r.returncode:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
