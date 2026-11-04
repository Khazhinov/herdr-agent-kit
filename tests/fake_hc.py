#!/usr/bin/env python3
"""herdr-core double for hub tests. State in $FAKE_HC_STATE. `send` of a
request writes the answer file named in it with FAKE_HC_ANSWER as last line;
FAKE_HC_DIE=1 ends the terminal instead (a worker that cannot run). `connect` serves doc.get and
doc.set of the App layout from state; `get` of FAKE_HC_CALLER returns FAKE_HC_CALLER_SPACE.
A request moves the turn counter; empty FAKE_HC_ANSWER leaves the worker working (no turn end);
FAKE_HC_SILENT=1 ends the turn without writing the answer."""
import json, os, re, sys
from pathlib import Path

S = Path(os.environ["FAKE_HC_STATE"])
s = json.loads(S.read_text()) if S.exists() else {"terms": {}, "calls": []}
s.setdefault("doc", {"value": {"spaces": []}, "version": 7})
cmd, args = sys.argv[1], sys.argv[2:]
s["calls"].append(sys.argv[1:])
out = "{}"
if cmd == "run":
    name = args[args.index("--name") + 1]
    s["terms"][name] = {"address": "t1", "name": name, "state": "live",
                        "status": {"state": os.environ.get("FAKE_HC_STATUS", "idle")},
                        "argv": args[args.index("--") + 1:], "cwd": args[args.index("--cwd") + 1], "turn": 0}
elif cmd == "ls":
    out = json.dumps({"terminals": list(s["terms"].values())})
elif cmd == "send":
    m = re.search(r"Write the brief answer to (\S+);", args[-2] if args[-1] == "--json" else args[-1])
    t = s["terms"].get(args[0])
    if m and os.environ.get("FAKE_HC_DIE"):
        s["terms"].pop(args[0], None)
    elif m and not os.environ.get("FAKE_HC_ANSWER") and not os.environ.get("FAKE_HC_SILENT"):
        t["status"] = {"state": "working"}
    elif m:
        t["turn"] += 1
        t["status"] = {"state": "idle"}
        if not os.environ.get("FAKE_HC_SILENT"):
            Path(m.group(1)).write_text("answer\n" + os.environ["FAKE_HC_ANSWER"] + "\n")
elif cmd == "get":
    t = next((x for x in s["terms"].values() if args[0] in (x["name"], x["address"])), None)
    if args[0] == os.environ.get("FAKE_HC_CALLER"):
        t = {"labels": {"herdr.space": os.environ.get("FAKE_HC_CALLER_SPACE")}}
    out = json.dumps(t or {})
elif cmd == "label":
    t = s["terms"].get(args[0])
    if t:
        t.setdefault("labels", {}).update(dict(x.split("=", 1) for x in args[1:] if "=" in x))
elif cmd == "connect":
    req = json.loads(sys.stdin.readline())
    if req["method"] == "doc.get":
        res = s["doc"]
    elif req["params"]["version"] != s["doc"]["version"]:
        res = None
    else:
        s["doc"] = {"value": req["params"]["value"], "version": s["doc"]["version"] + 1}
        res = {"version": s["doc"]["version"]}
    out = json.dumps({"jsonrpc": "2.0", "id": req["id"], **({"result": res} if res else
                     {"error": {"code": -32000, "data": {"kind": "conflict"}, "message": "conflict"}})})
elif cmd == "read":
    out = "screen line 1\nWhich task do you mean?"
elif cmd == "wait":
    out = json.dumps({"ended": True, "status": "idle"})
elif cmd in ("kill", "rm"):
    s["terms"].pop(args[0], None)
S.write_text(json.dumps(s))
print(out)
