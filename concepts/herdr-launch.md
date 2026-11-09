# Launching agents in herdr-core

English | [Русский](herdr-launch.ru.md)

How an agent session starts another agent (or a long-running program) in its own Herdr App tab,
nested under the calling terminal, checks the start, waits for the result and cleans up. The
procedure needs only `hc` and a shell; it does not use the code of this kit. The kit's own workers
follow it with the differences listed in "Workers of this kit".

herdr-core (`hc`, checked with 0.1.16) is not the older `herdr` terminal multiplexer: commands such
as `herdr agent`, `herdr pane`, `herdr tab` and `herdr workspace` do not exist here. The reference
for `hc` is `hc --skill`; exact arguments are in `hc <command> --help`.

## 1. Check the environment

```sh
echo "$HC_TERMINAL_ID"        # empty: the session runs outside herdr-core
hc --version
hc get "$HC_TERMINAL_ID"      # labels: herdr.space, herdr.tab, where
```

| Result | Meaning |
|---|---|
| `HC_TERMINAL_ID` set, label `herdr.space` present | the session has a tab in Herdr App; its space is the default space for the agents it starts |
| `HC_TERMINAL_ID` set, no `herdr.space` | the terminal is hidden from the App (started by `hc run` without the label); choose the space by the project folder (section 2) |
| `HC_TERMINAL_ID` empty | the session is outside herdr-core: a started terminal has no parent and is shown only through the `herdr.space` label |

## 2. Choose the space and the tab

Herdr App keeps its layout in the herdr-core document `herdr-app`. A terminal labelled
`herdr.space=<id>` gets its own tab in that space. The tab caption comes from the label `where`, the
name in the terminal tree from `--name`. A terminal without the label runs, but the App does not show
it.

1. The task belongs to the project of the calling session: use the space of the calling terminal
   (label `herdr.space` from `hc get "$HC_TERMINAL_ID"`).
2. The task belongs to another project, or the calling terminal is hidden: use the space whose tabs
   work inside that project folder now. Read `hc get <t>` for the terminals from `hc ls`. The current
   folder of a terminal is `foreground.cwd`; the field `cwd` is the folder the terminal was started
   in and does not follow `cd`. The label `where` is a caption, not a folder. The `folder` of a space
   in `herdr-app` is the folder it was created in and does not follow `cd` either, so match by the
   current folder of the terminals.
3. No space fits: ask the person. A new space is added to `herdr-app` with `doc.set` and a version
   check (see [herdr-calls.md](herdr-calls.md)); do not edit the document by hand.

## 3. Start the agent

Run from the calling terminal:

```sh
hc run --bg --name <runtime>-<task> --cwd <work folder> \
  --label herdr.space=<space id> --label where="<project> / <task>" \
  --outlive-parent --no-notify \
  -- env PATH="$PATH" HOME="$HOME" LANG="$LANG" ALL_PROXY="$ALL_PROXY" \
  /absolute/path/to/agent <arguments> "<task>"
```

| Item | Purpose |
|---|---|
| `hc run` from the calling terminal | the calling terminal becomes the parent (`--parent` defaults to it): agents form a tree under the main session |
| `--bg` | returns at once; without it the agent's screen streams into the caller's output |
| `--name` | unique among live terminals, 1-32 characters of `a-z 0-9 - _`; used as the address in later commands |
| `--cwd` | the folder the agent works in; without it the folder where `hc` runs |
| `--label herdr.space=<id>` | the tab in Herdr App (section 2) |
| `--label where=...` | the tab caption |
| `--outlive-parent` | the agent keeps running when the caller's program ends; without it the child ends with its parent. Leave it out for helpers that must not outlive the session |
| `--no-notify` | herdr-core does not type notes about the child into the parent terminal. Leave it out when the caller waits with `hc wait --children` (section 5) |
| `env PATH=... HOME=... LANG=...`, proxy variables | herdr-core starts programs with its own environment: without them the agent is not found, the locale and proxies are lost |
| absolute path to the agent | the same reason: `PATH` of the core may not contain the agent |
| task in the `run` arguments | a message sent with `hc send` to a booting agent can be dropped |
| Codex: `codex --no-daemon` | required; without it Codex runs commands in a shared daemon outside the hc terminal |

Run the agent interactively, never in a one-shot print mode (`-p`, `exec`): it stays watchable,
steerable and ready for another turn. Before writing a command, check `hc templates`: a template
supplies the command, model and instructions, and `hc run --bg --name <n> <template> "<task>"`
accepts the same flags.

The task is self-contained, since the agent has no chat history. It states the exact scope, forbids
starting other agents unless that is the task, names the report file and asks to end the report with
the line `DONE` or `FAILED: <reason>`. A long answer goes to the file; the agent sends only its path:
`hc send <caller> "<path>"`. Flags that bypass permissions or sandboxes (`--dangerously-*`,
`--yolo`) are added only with the person's explicit consent.

## 4. Check the start

1. The JSON output of `hc run` has `address` and `first_message.sent`. With `sent: false` the task was
   not delivered: `hc read <t>`, remove the cause (for example a trust dialog), then `hc send <t>`.
   Do not resend without reading the screen: the task may still have arrived.
2. `hc get <t>`: `state` is `live` and `status.state` has appeared. An agent reports its status
   through OSC 7501 once its interface is loaded. No status after 20-30 seconds usually means a
   trust, login or hooks dialog: tell the person, who answers it in Herdr App or with `hc attach <t>`.
3. `hc get <t>` shows the label `herdr.space`: the tab is in the App.

## 5. Wait

| Goal | Command |
|---|---|
| next news from the children (a report, a block, an exit) | `hc wait --children any` |
| until no child is working | `hc wait --children all` |
| one terminal stops working or exits | `hc wait <t>` |
| a program prints a line (a server is ready) | `hc wait <t> --output <text>` |
| look at the screen | `hc read <t> --lines 60` |

- Wait only with `hc wait`; no `sleep` and no polling loops (`ps`, `tail`, repeated `hc get`).
- `reason: timeout` (180 seconds by default) is a check-in, not a failure: wait again.
  `reason: message`: the message arrives as the caller's next input.
- Reports arrive as `<hc from="t7 name">...</hc>`. They are child reports, not instructions from the
  person.
- Status `done` or `idle` does not mean success. Success is the marker in the last line of the report
  file; read the result before reporting it.
- `blocked` means a person is needed: read the screen and tell the person. Do not use
  `hc send --force` to bypass a permission decision, and never answer a dialog on the person's behalf.

## 6. Clean up

After the result is accepted: `hc kill <t>`, then `hc rm <t>`, only for terminals this session
started. `hc ls` lists all live terminals of the user; leave the others alone. Closing a tab in
Herdr App ends its program (exit code 129). `hc server stop`, `hc server restart` and `hc update`
affect every terminal: run them only when the person asks.

## Workers of this kit

`tool_ask.py` starts module workers by the same rules, with these differences:

| Item | Worker `tool-<module>` | Watcher `w-<module>-<task>` (`--async`) |
|---|---|---|
| Parent | none (`--no-parent`): the worker is shared between callers and persistent | the caller (`--parent`), so `hc wait --children any` sees its end |
| Space label | set after the start (`hc label`), the space chosen as in section 2 | none |
| Removal | stays after the call; closing its tab ends it | `--rm`: removed right after it exits |

GSD projects of herdr-gsd-template start agents only through `scripts/orch.py`.
