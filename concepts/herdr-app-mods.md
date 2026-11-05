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
working color from 70%, blocked color from 90%. Each window shows when it resets, in the
system's date and time format (a time today, a weekday and time this week, a date later; read once
from `Get-Culture` on Windows, `locale -k LC_TIME` elsewhere, because the mod engine has no `Intl`).
An agent that has limit resets to spend (Codex grants them) shows their count next to its name.
Hover a window for used, left and the full reset time; hover the name for the data's age; click the header to refresh (it refreshes every 10 seconds).
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
| `[limits.commands]` | any other agent (Cursor, Antigravity, ...): a command that prints `{"windows": [{"name", "used", "resets_at"}]}`, optionally `"resets": {"available", "expires_at"}` |

Built-in sources:

| Agent | Source |
|---|---|
| Claude Code | `rate_limits` of the statusLine input. `limits.py statusline -- <old command>` wraps the statusLine, keeps one snapshot per session and runs the old command with the same input, so the visible line is unchanged. An idle session keeps showing what it saw last, maybe before a reset, so per window the latest `resets_at` wins and, within it, the highest usage. Snapshots of the GSD template's own tap are read too. |
| Codex | the fresher of the newest `rate_limits` in `~/.codex/sessions` logs (written on every Codex turn, read on every refresh) and `codex app-server` `account/rateLimits/read` (also sees other machines and the available limit resets, `rateLimitResetCredits`; asked at most every 30 s, one start costs about a second) |
| Kimi Code | `GET /usages` on the configured official Kimi Code endpoint. Shows the available 5h, 7d and monthly total windows; missing windows are omitted. `limit_month_code` is the Code share of the same monthly pool, shown in the `month` tooltip rather than as a separate limit. Once the window has renewed, its old breakdown is hidden until a new reading. Successes and errors are cached for 90 s. On failure, the last successful reading keeps its original timestamp and quota windows alongside the error. Custom API hosts and redirects are rejected. |

For Codex + Kimi, set `[limits] agents = ["codex", "kimi"]`; no Claude statusLine changes are needed.
Kimi provider settings come from `config.toml` in `~/.kimi-code` (or `KIMI_CODE_HOME`). Choose one authorization mode: `oauth`, a non-empty `api_key`, or `api_key_env` naming an environment variable. Conflicting modes are rejected. String values are trimmed. The named variable must be non-empty and visible to the collector process, including when it runs on another machine; an unset or empty variable reports a configuration error. API-key modes never start OAuth renewal.

For OAuth, the collector verifies that the configured credential slot matches the effective official API endpoint and OAuth host, then reads the CLI's existing credential. Mismatches, including incompatible `KIMI_CODE_BASE_URL`, `KIMI_CODE_OAUTH_HOST` or `KIMI_OAUTH_HOST` overrides, fail before credentials are read or requests are sent. Align the collector environment with `config.toml` and log in through Kimi; the collector never chooses another slot silently. Only file-based OAuth storage is supported. A token expiring within 60 s (or without an expiry) and with a refresh token triggers native renewal. This requires a compatible Kimi Code CLI on PATH or under `KIMI_CODE_HOME/bin` (the local API and instance registry were checked with 2.5.0). The collector briefly starts its own authenticated `kimi web --no-open` server on a loopback-only ephemeral port and calls its usage API. Kimi owns refresh locking, token rotation and atomic credential writes; the collector re-reads the durable winner, then stops its server. No browser, agent session or model request is started. Credential values never enter the kit cache or error messages.

After OAuth HTTP 401, the collector re-reads the durable credential to adopt a concurrent CLI rotation. If that winner needs ordinary renewal, it may invoke the native helper once. A different access token allows one final quota request. The compatible CLI's usage API does **not** expose forced renewal of a formally fresh token: an unchanged rejected token, or another 401, reports that reauthorization through `kimi /login` is needed. There are at most two direct quota requests and one native helper per collection attempt; the helper can make its own quota request. API-key 401 reports an API-key error. Other HTTP and connection errors do not trigger recovery or retries. Failures are retried after the 90 s cache interval.

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
