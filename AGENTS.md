# Agent entry point

## Publication safety (mandatory, read first)

This repository is public. Every agent working here, including one that only installs or configures something, keeps
the machine out of git:

1. Machine-specific data stays in git-ignored places (`local/`, `.mcp.json`, `*.env`). Never copy it into tracked files,
   commit messages, tags, release notes, pull requests or issues.
2. Tracked files never contain absolute home or mount paths, user, host, company or project names,
   private service addresses, tokens, keys, `.env` values, filled MCP configs or session logs. Use
   placeholders (`<home>`, `/absolute/path/to/...`, `example.com`).
3. A new tool, installer or config may write files into the working tree. Before the first commit
   add such paths to `.gitignore` and check `git status --short --ignored`.
4. Before a commit read `git diff --cached` and run `./scripts/self-check.sh`. Commit only with the author identity
   the person set for this repository. Push and publish only when the person asks.
5. In doubt whether something is private, do not commit it: ask the person.

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
