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

You maintain the MCP registry of this machine. Public files describe the mechanism only;
everything about this machine lives in `local/`. Ask the person when something is ambiguous; never
guess folder purposes or permissions. Full procedures: `README.md`.

## Register folders

1. `./tool_ask.py scan <root>` lists folders with MCP configs and their runtime. Show the person the
   new ones and register only those they choose.
2. Per folder add `[modules.<id>]` to `local/registry.toml` (format: `registry.example.toml`):
   `workdir`, `runtime`, `model` (ask the person which cheap model of that vendor to use; do not
   invent model names), `description` (one line), `mcp` if only some servers are meant,
   `tools` if the person wants a narrower tool set, `allow_write = false` unless the person says
   otherwise in this conversation.
3. Do not open secret values. The scan and `refresh` read configs only to start servers.
4. `./tool_ask.py refresh <id>`; check the card for `unavailable:` and report such servers.
5. Module-specific worker rules (scope limits, things never to print): `local/modules/<id>.md`.

## Projects

- Link: `./tool_ask.py link <project> <id>...`. Show the person which tracked files changed in the
  project (`AGENTS.md`, `CLAUDE.md`): only a pointer line without paths.
- After any registry or card change: `./tool_ask.py sync --dry-run`, then `sync`; report missing
  projects and stale modules.

## Change this repository

Put private patterns (one regular expression per line) into `local/private-patterns.txt`. Run
`./scripts/self-check.sh` before every commit. Keep `README.md` and `README.ru.md` in sync.
