# Herdr App mods: space groups and agent limits

Two panels for the Herdr App sidebar. They are ordinary Herdr App mods (gpui-kit JavaScript in
`mods/<name>/` of the app home) and change nothing in herdr-core.

## 1. Space groups (`herdr-app/space-groups`)

The shipped `spaces` list with folders. It keeps every shipped behavior: avatars, worktrees under
their checkout, sections per machine, git branch and ahead/behind, the space menu.

| Action | Result |
|---|---|
| Folder button in the Spaces header | new group on the machine of the shown space |
| Drag a space onto a group | the space goes into the group, at the end |
| Drag a space onto a space in a group | it lands before or after that space, by the pointer's half |
| Drag a space onto an ungrouped space | the shipped reorder (`space.move`) |
| Drag a space below the last row | out of every group |
| Drag a group onto a group's middle / edge | it becomes a subgroup / moves before or after it |
| Drag a group onto a space or below the last row | into that space's group / to the top level |
| Click a group | fold or unfold (accordion) |
| Right-click a group | Rename, New subgroup, Ungroup (its spaces and subgroups go to its parent) |

A group row shows how many spaces it holds, subgroups included, and the most urgent agent
status inside: spinner (working), colored dot (blocked, done), hollow dot (only idle agents).
Groups belong to one machine; a drag never crosses machines. In the narrow rail a group is a
chevron row with its status (click folds it) and its spaces keep their numbers.

Groups are a view of this screen. They live in the app's shared state (`shared("space-groups")`),
never in the core document `herdr-app`. Space ids, names, folders and tabs stay as they are, so
tools that find a space by the live folders of its terminals (`tool_ask.py --space module`, the GSD
template's `[workers] space = "project"`) work the same with or without groups. A space that is
gone is dropped from its group once its machine's layout is readable again.

## 2. Agent limits (`herdr-app/agent-limits`, `limits.py`)

One row per agent: its icon, name and every usage window the runtime reports (5h, 7d, a month, a
spend cap), each a percent with a thin bar. The color follows usage: green, then the theme's
working color from 70%, blocked color from 90%. Hover a window for used, left and the reset time;
hover the name for the data's age; click the header to refresh (it refreshes every 10 seconds).
Claude's limits come with its API answers, so they change when a session makes a request and the
tap writes them at once. Rows whose data is older than 3 hours are dimmed. In the narrow rail each
agent shows its icon, the tightest window's percent and bar. A window that renewed after the last reading
shows `new`: nothing proves its usage yet.

`limits.py` (standard library) collects the data where the kit lives; the mod runs it on that
herdr machine (`source.json`, written by the installer). Settings are `[limits]` in the registry:

| Key | Meaning |
|---|---|
| `agents` | which agents, in order; default `["claude", "codex"]` |
| `show` | `used` (0% = nothing spent, default) or `left` (100% = all left) |
| `[limits.commands]` | any other agent (Kimi, Cursor, Antigravity, ...): a command that prints `{"windows": [{"name", "used", "resets_at"}]}` |

Built-in sources:

| Agent | Source |
|---|---|
| Claude Code | `rate_limits` of the statusLine input. `limits.py statusline -- <old command>` wraps the statusLine, keeps one snapshot per session and runs the old command with the same input, so the visible line is unchanged. An idle session keeps showing what it saw last, maybe before a reset, so per window the latest `resets_at` wins and, within it, the highest usage. Snapshots of the GSD template's own tap are read too. |
| Codex | the fresher of the newest `rate_limits` in `~/.codex/sessions` logs (written on every Codex turn, read on every refresh) and `codex app-server` `account/rateLimits/read` (also sees other machines; asked at most every 30 s, one start costs about a second) |

## 3. Install

```sh
herdr-app/install.py --dry-run --claude-statusline   # what it would change
herdr-app/install.py --claude-statusline
```

The installer copies both mods, writes `source.json`, and in each of the person's layouts that has
the shipped sidebar points the `spaces` block at `space-groups` and adds an `agent-limits` block
under the agents list. Herdr's own layouts are read-only; when only `main` exists it writes a copy
`kit`. `--claude-statusline` wraps the global statusLine of `~/.claude/settings.json` once (backup:
`settings.json.pre-agent-limits`). Rerun it after an app update or a change of `[limits] agents`.
Removal: point the block back at `spaces`, delete the `agent-limits` block and the two mod folders.

The GSD template's installer regenerates its `gsd-mission-control` layout from `main` and keeps
blocks other installers changed or added to the sidebar, so both can be installed in either order.
