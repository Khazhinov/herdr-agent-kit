# herdr-tool-agents

English | [Русский](README.ru.md)

A machine-wide registry of folders that have MCP servers configured inside them, and a CLI that
lets any agent in any project use those servers through a cheap worker agent running in a
[herdr-core](https://github.com/herdrdev/herdr-core) terminal. Not tied to one agent runtime:
a module can be a Claude Code, Codex or OpenCode folder, or any command herdr-core can drive.
Works with [herdr-gsd-template](https://github.com/Khazhinov/herdr-gsd-template) (`orch.py tools`,
`tools-link`, `tool-ask`) but does not depend on it.

## Why

A project agent with many MCP servers loads hundreds of tool schemas into every request, and every
project needs its own copy of tokens. Here the MCP servers stay in their folders. The registry
describes them once; a project keeps only a short list of tool names and a way to call them. The
call goes to a persistent worker on the cheapest model of that folder's vendor; the project agent
reads a short answer file.

| Property | How |
|---|---|
| Project context stays small | `.agent-tools/TOOLS.md`: tool names per module; parameters on demand (`card <module> <tool>`) |
| Accurate tool descriptions | `refresh` starts each MCP server from the folder's own config and reads `tools/list`; no LLM involved |
| Any runtime | built in: `claude`, `codex`, `opencode` (start command, model flag, reset); any other: `command`, `reset`, `mcp_config`. MCP config shape is detected from content (`mcpServers`, `servers`, `mcp`, `[mcp_servers]`) |
| Cheap model for tool calls | `model` of the module (for example the vendor's smallest model) |
| Secrets stay in one folder | servers and tokens live only in the module folder; projects get a capability, not a token |
| Projects follow registry changes | the registry remembers linked projects; `sync` rewrites their `TOOLS.md` when a card changed |
| Writes need the person | `--write` works only with `allow_write = true`; the request carries `WRITE ALLOWED BY USER` |
| Audit | `~/.cache/herdr-tool-agents/audit.jsonl` |

## Layout

| Path | In git | Purpose |
|---|---|---|
| `tool_ask.py`, `mcp_probe.py` | yes | CLI and the MCP config reader and `tools/list` client (standard library only) |
| `registry.example.toml` | yes | annotated registry entry |
| `examples/<module>/` | yes | sample MCP config for a module folder |
| `AGENTS.md` | yes | procedure for the agent that maintains the registry |
| `local/registry.toml` | no | this machine's modules |
| `local/cards/<module>.md` | no | generated tool cards |
| `local/modules/<module>.md` | no | extra worker rules for a module |
| `local/links.json` | no | linked projects and their modules |

In a linked project: `.agent-tools/TOOLS.md` (excluded through `.git/info/exclude`, never committed)
and one pointer line in `AGENTS.md` (and `CLAUDE.md` when it does not import `AGENTS.md`) without
machine paths.

## Set up on a machine

The same order applies to the author and to anyone who cloned the repository; a fresh clone starts
with an empty `local/`.

1. Install [herdr-core](https://github.com/herdrdev/herdr-core) (`hc`) and the agent runtimes your
   module folders use.
2. `mkdir -p local && cp registry.example.toml local/registry.toml`, remove the sample entry.
3. `./tool_ask.py scan <root>` lists folders under `<root>` with MCP configs in the default places of
   Claude Code, Codex, OpenCode, Gemini CLI, Cursor and VS Code, with server names. Other runtimes:
   register the folder with `mcp_config`.
4. Register the folders you want (by hand or ask an agent in this folder: it follows `AGENTS.md`):
   id, `workdir`, `runtime`, `model`, `description`, optional `tools` filter, `allow_write`.
5. `./tool_ask.py refresh --all`, then `./tool_ask.py list` and `./tool_ask.py card <module>`.
   A server that fails to start is reported in the card; the others still work.
6. Try a call: `./tool_ask.py ask <module> "<narrow request>"`. The first start of a worker may wait
   for a trust or login prompt: answer it in Herdr App.

## Connect a project

```bash
./tool_ask.py link /path/to/project gitlab-main youtrack   # or from a herdr-gsd-template project:
python3 scripts/orch.py tools-link gitlab-main youtrack    # hub path: HERDR_TOOL_AGENTS or [tools] hub
```

The project agent then reads `.agent-tools/TOOLS.md` and calls
`tool_ask.py ask <module> "<request>"`. Exit codes: 0 answer ready, 1 failed or timeout,
2 configuration, 3 module busy, 4 needs a person, 5 write refused.

## Keep projects current

When a server, its config or the registry changes:

```bash
./tool_ask.py refresh <module>      # or --all
./tool_ask.py sync --dry-run        # which linked projects would change
./tool_ask.py sync                  # rewrite their TOOLS.md
```

`sync` also reports linked projects that no longer exist and modules removed from the registry.
`unlink <project> [module...]` removes a link.

## Runtime notes

Verified end to end (start, reset, MCP call, answer file) for Claude Code, Codex and OpenCode.

| Runtime | Needs once per module folder |
|---|---|
| Claude Code | answer the folder trust prompt on the first start |
| Codex | the folder is a trusted project (`[projects."<workdir>"] trust_level = "trusted"`), otherwise its `.codex/config.toml` and MCP servers are ignored; changed global hooks ask for trust once on start |
| OpenCode | allow writing answers outside the folder: `"permission": {"external_directory": {"<home>/.cache/herdr-tool-agents/**": "allow"}}` in the folder's `opencode.json` |
| Other | `command` (full argv with a cheap model), `reset`, `mcp_config` when the config is not in a default place; check the runtime's own trust and directory permissions |

A worker that waits for such a prompt makes `ask` exit with 4; answer it in Herdr App or
`hc attach tool-<module>`, never on the person's behalf.

## Request protocol

1. The worker terminal `tool-<module>` starts on the first request in the module folder and stays.
2. Before each request the worker gets its runtime's reset (`/clear` or `/new`): one request, one
   conversation.
3. The request carries the worker rules, the answer file path and the mode: read-only or
   `WRITE ALLOWED BY USER`. Rules travel with the request, so every runtime gets them the same way.
4. Completion is the last line of the answer file (`DONE` or `FAILED: ...`) plus herdr-core events.

## Checks

```bash
python3 tests/test_tool_ask.py
./scripts/self-check.sh        # tests + no machine paths or private names in tracked files
```

## License

MIT.
