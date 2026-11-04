# MCP registry

English | [Русский](mcp-registry.ru.md)

## Problem

A project agent with many MCP servers loads hundreds of tool schemas into every request, and every
project needs its own copy of tokens.

## Idea

MCP servers stay in the folders where they are configured, with their secrets. A machine-wide
registry describes each folder once (a module of kind `mcp`). A project keeps only tool names and
one call line. The call goes to a persistent worker agent in that folder, usually on the cheapest
model of its vendor; the project agent reads a short answer file.

| Property | How |
|---|---|
| Project context stays small | tool names per module; parameters on demand (`card <module> <tool>`) |
| Accurate tool descriptions | `refresh` starts each MCP server from the folder's own config and reads `tools/list`; no LLM involved |
| Any runtime | built in: `claude`, `codex`, `opencode`; any other: `command`, `reset`, `mcp_config`. Config shape is detected from content (`mcpServers`, `servers`, `mcp`, `[mcp_servers]`) |
| Cheap model for tool calls | `model` and optional `effort` of the module |
| Secrets stay in one folder | projects get a capability, not a token |
| Writes need the person | `--write` works only with `allow_write = true`; the request carries `WRITE ALLOWED BY USER` |

## Registry entry

```toml
[modules.github]
description = "GitHub repositories, issues, pull requests, Actions runs"
workdir = "~/tool-agents/github"
runtime = "claude"
model = "haiku"
mcp = ["github"]                  # servers the worker may use; absent = all
# tools = ["github:get_*"]        # filter shown to projects
allow_write = false
```

Full annotated sample: `registry.example.toml`.

## Call

```bash
tool_ask.py ask github "list open PRs of repo X, fields: number, title, author"
```

The command prints the answer file path. Protocol, completion and exit codes:
[herdr-calls.md](herdr-calls.md). A tool card changes only on `refresh` (it starts the servers),
then `sync` updates linked projects: [linked-projects.md](linked-projects.md).

## Runtime notes

Verified end to end (start, reset, MCP call, answer file) for Claude Code, Codex and OpenCode.

| Runtime | Needs once per module folder |
|---|---|
| Claude Code | answer the folder trust prompt on the first start |
| Codex | the folder is a trusted project (`[projects."<workdir>"] trust_level = "trusted"`), otherwise its `.codex/config.toml` and MCP servers are ignored; changed global hooks ask for trust once on start |
| OpenCode | allow writing answers outside the folder: `"permission": {"external_directory": {"<home>/.cache/herdr-agent-kit/**": "allow"}}` in the folder's `opencode.json` |
| Other | `command` (full argv with a cheap model), `reset`, `mcp_config` when the config is not in a default place; check the runtime's own trust and directory permissions |

## Reuse without this code

Keep a table of folders with MCP configs, generate a list of tool names per folder from
`tools/list`, give projects the names and one command that sends a narrow request to an agent
started in that folder, and read the answer from a file.
