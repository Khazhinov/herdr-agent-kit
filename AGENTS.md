# Agent entry point

Any agent runtime: this file is the single source of instructions. Runtimes that read another
file name get a one-line shim (`CLAUDE.md` contains `@AGENTS.md`); if yours reads neither, read this
file first and treat it as binding.

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

You maintain the registry of this machine: folders with MCP servers (kind `mcp`) and service
repositories with `agent-service.toml` (kind `service`). Public files describe the mechanism only;
everything about this machine lives in `local/`. Ask the person when something is ambiguous; never
guess folder purposes, permissions, models or functions. Catalog: `README.md`; procedures and rules:
`concepts/`.

Start of every session: read `local/README.md` if it exists (machine facts, registry state, scan
results, open checks); if `local/` is missing, set it up as `README.md`, "Set up on a machine" says.
Record new machine facts there, never in tracked files.

## Register folders

1. `./tool_ask.py scan <root>` lists folders with MCP configs and their runtime. Show the person the
   new ones and register only those they choose.
2. Per folder add `[modules.<id>]` to `local/registry.toml` (format: `registry.example.toml`):
   `workdir`, `runtime`, `model` (ask the person which cheap model of that vendor to use; do not
   invent model names), `description` (one line), `mcp` if only some servers are meant,
   `tools` if the person wants a narrower tool set, `allow_write = false` unless the person says
   otherwise in this conversation.
3. Do not open secret values. The scan and `refresh` read configs only to start servers.
4. Apply "Runtime notes" of `README.md` for the module's runtime (Codex trust entry, OpenCode answer
   directory permission); show the person each change to their agent configs before writing it.
5. `./tool_ask.py refresh <id>`; check the card for `unavailable:` and report such servers.
   Then one read-only `ask` per new module proves the worker path.
6. Module-specific worker rules (scope limits, things never to print): `local/modules/<id>.md`.

## Register service repositories

1. Read the repository's own agent instructions and propose functions to the person: name, one-line
   description, typed `params`, `returns`, `writes` (paths inside the repository), `external`,
   profiles (runtime, model, effort: ask the person, do not invent model names). Format:
   `concepts/service-repos.md`, sample `examples/service-docs/agent-service.toml`.
2. Write `agent-service.toml` into that repository only after the person agrees; committing it follows
   that repository's own rules.
3. Add `[modules.<id>] kind = "service"`, `workdir`, `allow_write = false` unless the person says
   otherwise, `space` if the person wants the worker in the repository's own space.
4. `./tool_ask.py refresh <id>` (validates the manifest), `card <id>`, then one read-only `call`.

## Projects

- Link: `./tool_ask.py link <project> <id>...`. Show the person which tracked files changed in the
  project (`AGENTS.md`, `CLAUDE.md`): only a pointer line without paths. Files under
  `.agent-tools/` are machine-local: `concepts/linked-projects.md`.
- After any registry or MCP card change: `./tool_ask.py sync --dry-run`, then `sync`; report missing
  projects and stale modules. Service manifests propagate by themselves on `call`, `card`, `link`
  and `sync`.

## Workers

- Workers are visible in Herdr App and persistent. Do not stop or kill a worker the person may be
  watching unless they ask; never answer a worker's dialog on the person's behalf.
- Completion is the answer file marker; see `concepts/herdr-calls.md` before changing how waits work.

## Herdr App mods

- `herdr-app/install.py` installs `space-groups` and `agent-limits` (`concepts/herdr-app-mods.md`).
  Show the person the dry run first; `--claude-statusline` changes their global Claude settings
  and needs their agreement. After a mod change: `herdr-app check`, then a screenshot
  (`herdr-app shot`) of the running window. Never drive the person's pointer or keys.
- `space-groups` is derived from Herdr's shipped `spaces` (Apache-2.0): keep the notice at its top;
  after an app update compare it with the new shipped `spaces` and carry fixes over.

## Change this repository

Put private patterns (one regular expression per line) into `local/private-patterns.txt`. Run
`./scripts/self-check.sh` before every commit. Keep `README.md` and `README.ru.md` in sync.

## Upstream maintenance for shared agent tooling

Standing user authorization: when using herdr-gsd-template, herdr-agent-kit or agent-token-stack, if a correction or update is needed in one of those source repositories, prepare it and open a detailed MR/PR without asking again. This authorizes a separate branch, pushing that branch and creating the MR/PR; it does not authorize merge, release, deployment, force-push or disclosure of secrets or machine-specific data. Use a clean worktree/checkout, preserve unrelated changes and check for an existing proposal before opening a duplicate. Include the problem, reproduction, expected/actual behavior, change, compatibility and validation; use a draft for an incomplete fix and state what remains. If access is unavailable, retain the concrete patch and report the exact blocker.

Propagate this same rule into the primary AGENTS.md (or equivalent shared instruction entrypoint) of every project that consumes any of these repositories, including projects created or updated through their installers/templates. Preserve existing project instructions and keep vendor-specific files as shims to the common policy where possible.
