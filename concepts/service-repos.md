# Service repositories

English | [Русский](service-repos.ru.md)

## Problem

Some repositories are workplaces with their own procedures: a documents repository with GOST export,
a reporting repository, a review setup. Other projects need their results but should not copy their
rules, skills or tokens, and each kind of work wants its own runtime, model and effort.

## Idea

The repository declares what it offers in a manifest, `agent-service.toml` in its root. The registry
points at the folder (a module of kind `service`). A project calls a function by name with typed
arguments; a worker agent started in that repository runs it by the repository's own instructions.
The owner of the repository keeps the manifest; a machine may override its profiles.

## Manifest

```toml
[service]
description = "Documents: requirements, design notes, docx export"
profile = "writer"                 # profile of functions that name none

[profiles.writer]                  # one way to start the worker
runtime = "claude"
model = "sonnet"
effort = "high"

[profiles.reviewer]
runtime = "codex"
effort = "medium"                  # no model: the runtime default

[functions.draft]
description = "Draft a document section from a source file"
run = "Follow the task life cycle of this repository's agent instructions."
params = [
  {name = "task", type = "string", required = true, description = "task folder name"},
  {name = "source", type = "path", required = true, description = "input file of the calling project"},
]
returns = "path of the new document version and a list of open questions"
writes = ["*/drafts/**"]           # paths of this repository it may change without --write
profiles = ["writer", "reviewer"]  # order of profiles; the service profile and `default` follow
timeout = 3600
# external = true                  # changes an external system: needs --write and allow_write
```

Sample with three functions: `examples/service-docs/agent-service.toml`.

| Key | Meaning |
|---|---|
| `params[].type` | `string`, `path`, `number`, `bool`. A `path` is made absolute in the caller's folder: the worker runs elsewhere |
| `writes` | the only paths of the repository the function changes; absent = no files |
| `external` | the function changes something outside the repository; callers need `--write` after the person's approval, and the module `allow_write = true` |
| `profiles` | order of profiles shown to callers; then `[service] profile`, then the built-in `default` |

## Profiles

A profile is runtime, model and effort; every key is optional.

| Runtime | Model | Effort | Note |
|---|---|---|---|
| claude | `--model` | `--effort low..max` | the next claude profiles of the chain become `--fallback-model`: Claude Code switches when a model is unavailable |
| codex | `-m` | `-c model_reasoning_effort="<level>"` | |
| opencode | `-m provider/model` | none in interactive mode | `effort` is refused; set the variant in the agent of `opencode.json` |
| other | `command` (full argv), `reset` | in `command` | |

The built-in profile `default` is the runtime of the service profile without model and effort: the
runtime's own defaults, which survive a renamed or retired model. Models change; a machine
overrides a profile in its registry without touching the repository:

```toml
[modules.docs.profiles.reviewer]
model = "<new model id>"
```

There is no automatic retry on another profile. When a worker does not start or ends its turn
without an answer, the call exits with 6 and lists the other profiles; the calling agent asks the
person and repeats with `--profile <name>`.

## Calls

```bash
tool_ask.py call docs draft --arg task=20261104_spec --arg source=notes/meeting.md
tool_ask.py call docs draft --arg task=... --arg source=... --async     # prints a task id
tool_ask.py wait docs <task>                                             # or: result docs <task>
```

The worker gets the function, its `run` instruction, the arguments, the allowed `writes`, the
directory for files meant for the caller and the answer file. It never waits for a person: missing
input or a needed decision becomes `FAILED: needs: <question>`. Completion and exit codes:
[herdr-calls.md](herdr-calls.md).

## Changes reach linked projects

A manifest is read without starting anything, so its card is rebuilt automatically when the file is
newer than the card: on `call`, `card`, `link` and `sync`. Added, removed and changed functions are
logged with a date and written into the files of every linked project
([linked-projects.md](linked-projects.md)); a `call` that finds a changed manifest says so on stderr.

## Reuse without this code

Put a manifest with functions, typed parameters, write scope and profiles into the repository;
start an agent in the repository with the profile's flags; send it the function, the arguments and
an answer file path; read the answer by its last line.
