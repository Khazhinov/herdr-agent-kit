#!/usr/bin/env python3
"""Minimal stdio MCP server for tests: two tools, paginated tools/list. Fails if FAKE_TOKEN is unset."""
import json, os, sys
TOOLS = [{"name": "get_issue", "description": "Get one issue. Long tail text.",
          "inputSchema": {"type": "object", "properties": {"repo": {}, "number": {}, "fields": {}},
                          "required": ["repo", "number"]}, "annotations": {"readOnlyHint": True}},
         {"name": "close_issue", "description": "Close an issue.",
          "inputSchema": {"properties": {"number": {}}, "required": ["number"]},
          "annotations": {"readOnlyHint": False}}]
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if os.environ.get("FAKE_TOKEN") != "t0ken":
        r = {"error": {"code": -1, "message": "no token"}}
    elif m["method"] == "initialize":
        r = {"result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}}
    else:
        page = (m.get("params") or {}).get("cursor")
        r = {"result": {"tools": TOOLS[1:]} if page else {"tools": TOOLS[:1], "nextCursor": "p2"}}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], **r}), flush=True)
