# Calls through herdr

English | [Русский](herdr-calls.ru.md)

One protocol serves every worker, whatever runtime the caller and the worker use. The caller needs
only a shell; the worker is an interactive agent in a herdr-core terminal.

## Worker terminal

| Item | Rule |
|---|---|
| Name | `tool-<module>` (MCP), `tool-<module>-<profile>` (service) |
| Start | on the first call, in the module folder, with `env` carrying `PATH`, locale and proxies (herdr-core starts programs with its own environment) |
| Lifetime | stays after the call; one request at a time (a busy worker gives exit code 3). Closing its tab in Herdr App ends it; the next call starts it again |
| Visibility | always shown in Herdr App. `space = "caller"` (default): the space of the calling terminal. `space = "module"`: the space whose tabs work inside the module folder now; created when none exists. Registry key `space`, flag `--space` |
| Reset | before each request the runtime's fresh-conversation command (`/clear`, `/new`): one request, one conversation |

Herdr App keeps its layout in the herdr-core document `herdr-app` (spaces, tabs, terminals). A
terminal labelled `herdr.space=<id>` gets a tab in that space. A space is matched by the live
working folder of its terminals, since its stored `folder` is the folder it was created in. A new
space is added with `doc.set` and a version check.

## Request

The request carries the worker rules (scope, write mode, no secrets, no waiting for a person), the
task, the answer file path and, for a service, the directory for files meant for the caller. Rules
travel with the request, so every runtime gets them the same way. The answer file ends with exactly
`DONE` or `FAILED: <why>`; `FAILED: needs: <question>` is a question for the person.

## Completion

The rules follow herdr-gsd-template: durable evidence first, terminal facts second.

1. The answer file marker (`DONE`, `FAILED`) is the only proof of completion.
2. A terminal status alone never ends a wait. The turn counter of the worker must have moved past the
   request; after that the file gets `HERDR_AGENT_KIT_GRACE` seconds (20), since status and file
   writes are not ordered.
3. A turn that ended without a marker is reported with the tail of the worker's screen: usually a
   question or an error the worker printed instead of writing the file.
4. `blocked` (a permission or question dialog) gives exit code 4: a person answers it in Herdr App or
   `hc attach <terminal>`, never the calling agent.

Synchronous calls wait in one blocking command. With `--async` the call prints a task id; then:

| Caller | Signal |
|---|---|
| any | one blocking `wait <module> <task>` (as a background command where the runtime allows), or `result <module> <task>` (0 ready, 3 still running); no polling loops |
| inside herdr | a watcher terminal `w-<module>-<task>`, a child of the caller, runs `wait`; herdr notes the caller when it ends (`hc wait --children any` waits for it) |

## Exit codes

| Code | Meaning | Caller action |
|---|---|---|
| 0 | answer ready, path on stdout | read the file |
| 1 | failed or timeout | read stderr and the file |
| 2 | usage or configuration | fix the call |
| 3 | worker busy, or task still running | retry later |
| 4 | needs a person | ask the person to answer in Herdr App |
| 5 | write refused | ask the person; `--write` only after approval |
| 6 | profile failed (service) | ask the person, then `--profile <name>` from stderr |

## Audit

Every request and result is a line in `~/.cache/herdr-agent-kit/audit.jsonl`; a service task keeps
`task.json`, `answer.md` and its files in `~/.cache/herdr-agent-kit/<module>/<task>/`.
