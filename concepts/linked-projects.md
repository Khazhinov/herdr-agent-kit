# Linked projects

English | [Русский](linked-projects.ru.md)

## Files in a project

`tool_ask.py link <project> <module>...` writes three layers. Only the first is tracked by the
project's git, and it holds no machine paths.

| File | Content | In git |
|---|---|---|
| `AGENTS.md` (and `CLAUDE.md` when it does not import `AGENTS.md`) | one universal line: the project has linked external tools and repositories, listed in `.agent-tools/TOOLS.md`; read it before looking for MCP tools or doing work a linked repository offers | yes |
| `.agent-tools/TOOLS.md` | index: one row per linked module (kind, what it offers, details file, last change) and the call rules: commands, answer file, `--async` and its signals, write approval, exit codes | no (`.git/info/exclude`) |
| `.agent-tools/<module>.md` | details of one module: call line, write permission, functions with signatures and parameter notes (service) or tool names (MCP), last five changes | no |

The pointer line is the same in every project and every runtime: any agent that reads `AGENTS.md`
finds the index, and the index is enough to call any module. Signatures stay out of the agent's
context until it opens a details file or asks `card <module> <name>`.

## Keeping projects current

| Change | What updates the projects |
|---|---|
| a service manifest (new function, new parameter, new profile) | automatic: the card is rebuilt when the manifest is newer, on `call`, `card`, `link` and `sync`; the change is logged and the linked projects of that module are rewritten |
| MCP servers or their config | `refresh <module>` (starts the servers), then `sync` |
| the registry (description, `allow_write`, removed module) | `sync` |

`sync --dry-run` lists projects that would change; `sync` also reports linked projects that no
longer exist and modules removed from the registry. A change log line looks like
"2026-11-04 15:20: added `summary`; changed `draft`" and appears in the index (last change) and in
the module file (recent changes). An agent that read the files earlier sees the change the next
time it opens them; a `call` that rebuilt a card says so on stderr.

`unlink <project> [module...]` removes module files; without modules it removes the index too.

## Reuse without this code

Give each project one tracked pointer line, an untracked index of linked capabilities with the call
rules, and one untracked file per capability. Regenerate them from the source of truth (MCP
`tools/list`, service manifests) whenever it changes, with a dated change line.
