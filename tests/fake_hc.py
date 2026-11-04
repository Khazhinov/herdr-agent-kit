#!/usr/bin/env python3
"""herdr-core double for hub tests. State in $FAKE_HC_STATE. `send` of a
request writes the answer file named in it with FAKE_HC_ANSWER as last line."""
import json, os, re, sys
from pathlib import Path

S = Path(os.environ["FAKE_HC_STATE"])
s = json.loads(S.read_text()) if S.exists() else {"terms": {}, "calls": []}
cmd, args = sys.argv[1], sys.argv[2:]
s["calls"].append(sys.argv[1:])
out = "{}"
if cmd == "run":
    name = args[args.index("--name") + 1]
    s["terms"][name] = {"address": "t1", "name": name, "state": "live",
                        "status": {"state": os.environ.get("FAKE_HC_STATUS", "idle")},
                        "argv": args[args.index("--") + 1:], "cwd": args[args.index("--cwd") + 1]}
elif cmd == "ls":
    out = json.dumps({"terminals": list(s["terms"].values())})
elif cmd == "send":
    m = re.search(r"Write the brief answer to (\S+);", args[-2] if args[-1] == "--json" else args[-1])
    if m and os.environ.get("FAKE_HC_ANSWER"):
        Path(m.group(1)).write_text("answer\n" + os.environ["FAKE_HC_ANSWER"] + "\n")
elif cmd == "wait":
    out = json.dumps({"ended": True, "status": "idle"})
elif cmd in ("kill", "rm"):
    s["terms"].pop(args[0], None)
S.write_text(json.dumps(s))
print(out)
