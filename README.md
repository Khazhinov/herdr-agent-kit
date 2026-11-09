# herdr-agent-kit

English | [Русский](README.ru.md)

Portable building blocks for agent work on top of [herdr-core](https://github.com/herdrdev/herdr-core).
Each block is a concept you can take into your own agent setup, plus a working implementation. No
block is tied to one agent runtime: Claude Code, Codex, OpenCode or any command herdr-core can drive
works on both sides of a call. Works with
[herdr-gsd-template](https://github.com/Khazhinov/herdr-gsd-template) (`orch.py tools`, `tools-link`,
`tool-ask`) but does not depend on it. Formerly `herdr-tool-agents`.

The kit needs herdr-core (`hc`, checked with 0.1.16), not the older `herdr` terminal multiplexer:
commands such as `herdr agent`, `herdr pane`, `herdr tab` or `herdr workspace` are not used.

## Catalog

| Block | What it gives | Concept | Implementation |
|---|---|---|---|
| MCP registry | a project uses MCP servers of other folders without loading their schemas or holding their tokens | [concepts/mcp-registry.md](concepts/mcp-registry.md) | `tool_ask.py ask`, `mcp_probe.py` |
| Service repositories | a project calls typed functions of another repository (documents, reports, reviews), each on the runtime, model and effort that repository chose | [concepts/service-repos.md](concepts/service-repos.md) | `tool_ask.py call`, `agent-service.toml` |
| Launching agents | a session starts an agent in its own Herdr App tab under the calling terminal: space and tab, flags, start checks, waiting, cleanup; needs only `hc` | [concepts/herdr-launch.md](concepts/herdr-launch.md) | none (procedure); `tool_ask.py` workers follow it |
| Calls through herdr | one protocol for every worker: visible terminal, reset, rules in the request, answer file, completion signals, exit codes | [concepts/herdr-calls.md](concepts/herdr-calls.md) | `tool_ask.py` |
| Linked projects | a project gets an index of linked modules, one details file per module and their change log, kept current by `sync` | [concepts/linked-projects.md](concepts/linked-projects.md) | `tool_ask.py link`, `sync` |
| Herdr App mods | space groups and subgroups in the sidebar (drag and drop, count and agent status per group); a compact block of agents' usage limits under the agents list | [concepts/herdr-app-mods.md](concepts/herdr-app-mods.md) | `herdr-app/`, `limits.py` |

Both module kinds share one registry, one card format and one way to link projects. A further block
gets its own top-level folder with its own README and a row in this table.

## Layout

| Path | In git | Purpose |
|---|---|---|
| `tool_ask.py`, `mcp_probe.py` | yes | CLI, MCP config reader and `tools/list` client (standard library only) |
| `herdr-app/`, `limits.py` | yes | the two Herdr App mods, their installer, the limits collector |
| `registry.example.toml` | yes | annotated registry entries of both kinds |
| `examples/<module>/` | yes | sample MCP config or service manifest |
| `concepts/` | yes | the concepts, written to be reused without this code |
| `VERSION`, `CHANGELOG.md` | yes | release number and changes per release |
| `AGENTS.md` | yes | procedure for the agent that maintains the registry |
| `local/registry.toml` | no | this machine's modules |
| `local/cards/<module>.md`, `.log` | no | generated cards (MCP tools or service functions) and their change log |
| `local/modules/<module>.md` | no | extra worker rules for a module |
| `local/links.json` | no | linked projects and their modules |

## Set up on a machine

The same order applies to the author and to anyone who cloned the repository; a fresh clone starts
with an empty `local/`.

1. Install herdr-core (`hc`), Herdr App if you want to see the workers, and the agent runtimes your
   module folders use.
2. `mkdir -p local && cp registry.example.toml local/registry.toml`, remove the sample entries.
3. `./tool_ask.py scan <root>` lists folders with MCP configs (default places of Claude Code, Codex,
   OpenCode, Gemini CLI, Cursor, VS Code) and folders with `agent-service.toml`.
4. Register the folders you want (by hand or ask an agent in this folder: it follows `AGENTS.md`).
5. `./tool_ask.py refresh --all`, then `./tool_ask.py list` and `./tool_ask.py card <module>`.
6. Try a call: `ask <module> "<narrow request>"` or `call <module> <function> --arg name=value`.
   The first start of a worker may wait for a trust or login prompt: answer it in Herdr App.
7. Link projects: `./tool_ask.py link /path/to/project <module>...` (see
   [concepts/linked-projects.md](concepts/linked-projects.md)).

## Environment

| Variable | Default | Old name, still read |
|---|---|---|
| `HERDR_AGENT_KIT_REGISTRY` | `local/registry.toml` | `HERDR_TOOL_AGENTS_REGISTRY` |
| `HERDR_AGENT_KIT_CACHE` | `~/.cache/herdr-agent-kit` (answers, tasks, `audit.jsonl`) | `HERDR_TOOL_AGENTS_CACHE` |
| `HERDR_AGENT_KIT_GRACE` | `20`: seconds the answer file gets after a worker turn ends | - |
| `ORCH_HC` | `hc` | - |

herdr-gsd-template finds this folder through `HERDR_AGENT_KIT` (old name `HERDR_TOOL_AGENTS`) or
`[tools] hub` in `routing.local.toml`.

## Checks

```bash
python3 tests/test_tool_ask.py
./scripts/self-check.sh        # tests + no machine paths or private names in tracked files
```

## License

MIT.
