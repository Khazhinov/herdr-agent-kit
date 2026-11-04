# Agent entry point

You maintain the tool agent hub on this machine. The public files describe the mechanism only.
Everything about this machine lives in `local/` (git-ignored). Ask the person when something is
ambiguous; never guess folder purposes or permissions.

## Register a folder

1. The person names a folder that has MCP servers configured (`.mcp.json` for Claude workers).
   Do not open secret values; read only server names (`jq 'keys' <(jq .mcpServers .mcp.json)` or
   equivalent).
2. Add `[modules.<id>]` to `local/registry.toml` (create it from `registry.example.toml`):
   `workdir`, `mcp` (server names), `description` (one line), `capabilities` and
   `write_capabilities` (few, concrete, `<area>.<object>.<verb>`). `allow_write = false` unless the
   person says otherwise in this conversation.
3. To learn the tools, run `./tool_ask.py ask <id> "List your MCP tools: name and one line each."`
   and derive capability names from the answer. Answer trust or login prompts only through the person.
4. Module-specific rules for the worker (scope limits, things never to print) go to
   `local/modules/<id>.md`.
5. Check: `./tool_ask.py list`, `./tool_ask.py status <id>`.

## Connect a project

Run `./tool_ask.py snippet <id>...` and give the block to the person or to the project's agent.
In a public project it belongs in an untracked file (`CLAUDE.local.md`, `AGENTS.local.md`).

## Change this repository

Public files must stay free of this machine: no absolute home or mount paths, host names, user,
company or project names, real module ids. Put private patterns (one regular expression per line)
into `local/private-patterns.txt`. Run `./scripts/self-check.sh` before every commit. Keep
`README.md` and `README.ru.md` in sync.
