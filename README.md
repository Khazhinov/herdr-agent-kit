# herdr-tool-agents

English | [Русский](README.ru.md)

A machine-wide registry of folders that have MCP servers configured inside them, and a small CLI
that lets any agent in any project use those servers through a cheap worker agent running in a
[herdr-core](https://github.com/herdrdev/herdr-core) terminal. Works with
[herdr-gsd-template](https://github.com/Khazhinov/herdr-gsd-template) but does not depend on it.

## Why

A project agent with ten MCP servers loads hundreds of tool schemas into every request, and every
project needs its own copy of tokens. Here a project knows only a short catalog: module name, what it
knows, capability names. The request goes to a persistent worker (Claude Haiku by default) started in
the folder where the MCP servers and their secrets live. The project agent reads a short answer file.

| Property | How |
|---|---|
| Project context stays small | the project sees a few catalog lines (`snippet`), never MCP schemas |
| Cheap model for tool calls | `command` of the module, default `claude --model haiku` |
| One setup, every project | the worker terminal `tool-<module>` lives in herdr-core outside any project (`--no-parent`) |
| Secrets stay in one folder | `.mcp.json` lives only in the module's folder; projects get a capability, not a token |
| Writes need the person | `--write` works only with `allow_write = true` in the registry; the request carries `WRITE ALLOWED BY USER` |
| Audit | `~/.cache/herdr-tool-agents/audit.jsonl`: request, mode, result, duration |

## Layout

| Path | In git | Purpose |
|---|---|---|
| `tool_ask.py` | yes | the CLI |
| `registry.example.toml` | yes | annotated registry entry |
| `examples/<module>/` | yes | sample `.mcp.example.json` for a module folder |
| `AGENTS.md` | yes | procedure for the agent that maintains this hub on a machine |
| `local/registry.toml` | no | this machine's modules: folders, capabilities, write permission |
| `local/modules/<module>.md` | no | extra worker rules for a module |

## Use

```bash
cp registry.example.toml local/registry.toml   # then register real folders, see AGENTS.md
./tool_ask.py list                             # catalog
./tool_ask.py snippet github                   # block to paste into a project's AGENTS.md
./tool_ask.py ask github "State of the last Actions run on main of repo X"
# -> path of the answer file; its last line is DONE
./tool_ask.py status github
./tool_ask.py stop github
```

The `snippet` output contains the absolute path of `tool_ask.py` on this machine. In a public
project put it into an untracked file (`CLAUDE.local.md`, `AGENTS.local.md` or a git-ignored
include), not into the committed `AGENTS.md`.

From a herdr-gsd-template project: `python3 scripts/orch.py tools` and
`python3 scripts/orch.py tool-ask <module> "<request>"` (hub path: `HERDR_TOOL_AGENTS` or
`[tools] hub` in `.orchestration/routing.local.toml`).

Exit codes: 0 answer ready, 1 failed or timeout, 2 configuration, 3 module busy, 4 needs a person
(trust dialog, login, permission prompt), 5 write refused.

## Request protocol

1. The worker terminal starts on the first request and stays alive.
2. Before each request the worker gets `reset` (`/clear`): one request, one conversation.
3. The request names the answer file and the mode: read-only or `WRITE ALLOWED BY USER`.
4. Completion is the last line of the answer file (`DONE` or `FAILED: ...`) plus herdr-core events.
5. Claude workers get the worker rules through `--append-system-prompt`; the module folder's own
   `CLAUDE.md` and `.mcp.json` load as usual.

## Checks

```bash
python3 tests/test_tool_ask.py
./scripts/self-check.sh        # tests + no machine paths or private names in tracked files
```

## License

MIT.
