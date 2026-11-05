// Agent limits: how much of each agent runtime's usage windows is used
// (5 hours, 7 days, a month, a spend cap), one compact row per agent.
//
// Data: herdr-agent-kit's limits.py, run where the kit lives (`source.json`,
// written by the installer: { "on": machine or null, "argv": [...] }). Which
// agents are shown is the kit's `[limits]` setting, not this file.
// Each window shows when it resets, in the system's date and time format; an
// agent with limit resets to spend (Codex grants them) shows their count.
// Click the header to refresh now.

import { View, div, svg, image } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, readTextFile, appHome, theme, shared, window } from "herdr";

const EVERY = 10000; // a tap is written on every agent turn; limits.py answers in ~50 ms
const STALE = 3 * 3600; // data older than this is dimmed
const NAMES = { claude: "Claude", codex: "Codex", opencode: "OpenCode", cursor: "Cursor", kimi: "Kimi", antigravity: "Antigravity", gemini: "Gemini" };

function span(sec) {
  if (sec < 3600) return `${Math.max(1, Math.round(sec / 60))}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m`;
  return `${Math.floor(sec / 86400)}d ${Math.round((sec % 86400) / 3600)}h`;
}

// The system's date and time format. The mod engine (QuickJS) has no Intl and
// ignores locales, so the format is read from the OS once: .NET-style patterns
// (H/HH/h/hh, mm, tt, d, MMMM) and short day and month names.
let FMT = { time: "HH:mm", monthDay: "MMMM d", am: "AM", pm: "PM",
  days: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
  months: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] };

async function systemFormat() {
  if (window().platform === "windows") {
    const ps = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $f=(Get-Culture).DateTimeFormat; "
      + "@{time=$f.ShortTimePattern; monthDay=$f.MonthDayPattern; am=$f.AMDesignator; pm=$f.PMDesignator; "
      + "days=$f.AbbreviatedDayNames; months=$f.AbbreviatedMonthGenitiveNames} | ConvertTo-Json -Compress";
    const r = await exec("powershell.exe", ["-NoProfile", "-Command", ps]);
    const f = JSON.parse(r.stdout);
    return { ...f, months: f.months.slice(0, 12) };
  }
  // POSIX: `locale -k LC_TIME` prints abday="Sun;...", t_fmt="%r", d_fmt="%m/%d/%Y"...
  const r = await exec("locale", ["-k", "LC_TIME"]);
  const k = Object.fromEntries([...r.stdout.matchAll(/^(\w+)="(.*)"$/gm)].map((m) => [m[1], m[2]]));
  const ampm = (k.am_pm ?? "AM;PM").split(";");
  return { time: /%[Irl]/.test(k.t_fmt ?? "") ? "h:mm tt" : "HH:mm",
    monthDay: (k.d_fmt ?? "%m").indexOf("%d") < (k.d_fmt ?? "%m").indexOf("%m") ? "d MMMM" : "MMMM d",
    am: ampm[0], pm: ampm[1], days: k.abday.split(";"), months: k.abmon.split(";") };
}

function fmt(pattern, d) {
  const h = d.getHours(), two = (n) => String(n).padStart(2, "0");
  return pattern.replace(/'([^']*)'|HH|H|hh|h|mm|tt|MMMM|MMM|dd|d/g, (m, quoted) =>
    quoted !== undefined ? quoted : { HH: two(h), H: String(h), hh: two(h % 12 || 12), h: String(h % 12 || 12),
      mm: two(d.getMinutes()), tt: h < 12 ? FMT.am : FMT.pm, MMMM: FMT.months[d.getMonth()],
      MMM: FMT.months[d.getMonth()], dd: two(d.getDate()), d: String(d.getDate()) }[m]).trim();
}

// The reset moment, short: a time today, a weekday and time this week, a date later on.
function when(ts, now) {
  const d = new Date(ts * 1000);
  if (d.toDateString() === new Date(now * 1000).toDateString()) return fmt(FMT.time, d);
  if (ts - now < 6 * 86400) return `${FMT.days[d.getDay()]} ${fmt(FMT.time, d)}`;
  return fmt(FMT.monthDay, d);
}

function full(ts) {
  const d = new Date(ts * 1000);
  return `${FMT.days[d.getDay()]}, ${fmt(FMT.monthDay, d)}, ${fmt(FMT.time, d)}`;
}

// The program table's icon for an agent (as the + menu shows it), if Herdr knows the agent.
function icon(id) {
  for (const list of Object.values(shared("new-menu.agents") ?? {})) {
    const a = (list ?? []).find((x) => x.key === id);
    if (a?.icon) return a;
  }
  return null;
}

export default class AgentLimits extends View {
  init(_props, cx) {
    this.data = null;
    this.error = null;
    this.width = 300;
    cx.timer.every(EVERY, (cx) => this.refresh(cx));
    this.refresh(cx);
    cx.spawn(async (cx) => {
      try { FMT = { ...FMT, ...(await systemFormat()) }; cx.notify(); } catch { /* keep the default */ }
    });
  }

  refresh(cx) {
    if (this.busy) return;
    this.busy = true;
    cx.spawn(async (cx) => {
      try {
        const text = await readTextFile(`${appHome()}/mods/agent-limits/source.json`);
        if (!text) throw new Error("source.json is missing: run the kit's herdr-app installer");
        const src = JSON.parse(text);
        const r = await exec(src.argv[0], src.argv.slice(1), { on: src.on ?? null });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout || `exit ${r.code}`).trim().slice(-200));
        this.data = JSON.parse(r.stdout);
        this.error = null;
      } catch (e) {
        this.error = String(e?.message ?? e);
      }
      this.busy = false;
      cx.notify();
    });
  }

  tone(t, used) {
    return used >= 90 ? t.blocked : used >= 70 ? t.working : t.ansi2;
  }

  // `show`: "used" (0% = nothing spent) or "left" (100% = all of it left); the color
  // always follows how much is used.
  cell(w, now, t, key, show, width) {
    const known = w.used !== null && w.used !== undefined;
    const value = known ? Math.round(show === "left" ? 100 - w.used : w.used) : null;
    const at = w.resets_at && w.resets_at > now ? w.resets_at : null;
    const breakdown = (known && !w.reset ? w.breakdown ?? [] : []).map((part) => `${part.name}: ${part.used}% of monthly budget`).join("; ");
    const tip = (!known ? `${w.name}: renewed since the last reading`
      : `${w.name}: ${Math.round(w.used)}% used, ${Math.round(100 - w.used)}% left`)
      + (at ? `\nresets ${full(at)} (in ${span(at - now)})` : "")
      + (breakdown ? `\n${breakdown}` : "");
    return v_flex().id(key).w(width).flex_none().gap(4).tooltip(tip)
      .child(h_flex().gap(4).text_size(10.5).items_center()
        .child(div().text_color(t.dim).child(w.name))
        .child(div().text_color(known ? t.foreground : t.dim).child(known ? `${value}%` : "new"))
        .when(Boolean(at) && width >= 84, (el) => el.child(div().flex_1().min_w(0).truncate().text_right().text_color(t.dim).child(when(at, now)))))
      .child(div().h(4).w_full().rounded(2).bg(t.hover)
        .when(known, (el) => el.child(div().h_full().rounded(2).bg(this.tone(t, w.used)).w(Math.max(2, (width * Math.min(100, value)) / 100)))));
  }

  mark(a, t) {
    const ic = icon(a.id);
    if (ic) return ic.tint ? svg(ic.icon).size(14).flex_none().text_color(t.foreground) : image(ic.icon).size(14).flex_none();
    return h_flex().size(14).flex_none().rounded(4).bg(t.raised).items_center().justify_center()
      .text_size(9).font_bold().text_color(t.muted).child((a.id[0] ?? "?").toUpperCase());
  }

  row(a, now, t, rail, cols) {
    const stale = a.seen && now - a.seen > STALE;
    const name = NAMES[a.id] ?? a.id;
    const tip = a.error ? `${name}: ${a.error}` : `${name}${a.seen ? `, data from ${span(Math.max(60, now - a.seen))} ago` : ""}`;
    const top = a.windows.reduce((m, w) => Math.max(m, w.used ?? 0), 0);
    const show = this.data?.show ?? "used";
    // The rail: the agent's icon over its tightest window, a bar and a number.
    if (rail) {
      const v = Math.round(show === "left" ? 100 - top : top);
      return v_flex().id(`limit-${a.id}`).h(32).flex_none().items_center().justify_center().gap(4).tooltip(tip)
        .when(Boolean(stale), (el) => el.opacity(0.55))
        .child(h_flex().gap(3).items_center().child(this.mark(a, t))
          .child(div().text_size(10).text_color(a.windows.length ? t.muted : t.dim).child(a.windows.length ? `${v}` : "?")))
        .child(div().w(30).h(3).rounded(2).bg(t.hover)
          .when(a.windows.length > 0, (el) => el.child(div().h_full().rounded(2).bg(this.tone(t, top)).w(Math.max(2, (30 * Math.min(100, v)) / 100)))));
    }
    // Every row uses the same columns (`cols`), so cells line up with or without resets.
    const { named, lead, slot, width } = cols;
    const resets = a.resets?.available ?? 0;
    const rtip = `${resets} limit reset${resets === 1 ? "" : "s"} to spend`
      + (a.resets?.expires_at ? `, the first expires ${full(a.resets.expires_at)}` : "");
    return h_flex().id(`limit-${a.id}`).h(30).flex_none().px(8).gap(8).items_center().rounded(8)
      .when(Boolean(stale), (el) => el.opacity(0.55))
      .child(h_flex().id(`limit-name-${a.id}`).w(lead).flex_none().gap(8).items_center().tooltip(tip)
        .child(this.mark(a, t))
        .child(div().flex_1().min_w(0).truncate().text_size(12.5).text_color(t.muted).when(named, (el) => el.child(name))))
      .when(slot > 0, (el) => el.child(h_flex().id(`limit-resets-${a.id}`).w(slot - 8).flex_none().gap(2).items_center().text_size(10.5).text_color(t.dim)
        .when(resets > 0, (el) => el.tooltip(rtip)
          .child(svg("mods/agent-limits/reset.svg").size(11).text_color(t.dim))
          .child(div().child(String(resets))))))
      .when(!a.windows.length, (el) => el.child(div().flex_none().text_size(11).text_color(t.dim).child(a.error ? "no data" : "-")))
      .children(a.windows.map((w, i) => this.cell(w, now, t, `limit-${a.id}-${i}`, show, width)));
  }

  render() {
    const t = theme();
    const rail = this.width < 160;
    const now = Math.floor(Date.now() / 1000);
    const agents = this.data?.agents ?? [];
    // Shared columns: icon and name, a slot for the resets count if any agent has one, cells.
    const named = this.width >= 280;
    const slot = agents.some((a) => a.resets?.available) ? 30 : 0;
    const lead = (named ? 72 : 14) + slot;
    const n = Math.max(1, ...agents.map((a) => a.windows.length));
    const width = Math.max(56, Math.min(130, Math.floor((this.width - 32 - lead - 8 * n) / n)));
    const cols = { named, lead: lead - slot, slot, width };
    return v_flex().id("root").size_full().min_h(0).bg(t.chrome).pb(6)
      .on_size_change((size, cx) => { if (Math.abs(size.width - this.width) > 1) { this.width = size.width; cx.notify(); } })
      .child(h_flex().id("limits-head").h(rail ? 14 : 28).flex_none().pl(rail ? 0 : 16).pr(rail ? 0 : 10).items_center().text_size(11.5).text_color(t.dim)
        .when(rail, (el) => el.justify_center().child(div().w(20).h(1).bg(t.border)))
        .cursor_pointer().on_click((_e, cx) => this.refresh(cx))
        .tooltip(this.error ?? "Click to refresh")
        .when(!rail, (el) => el.child(div().flex_1().child(this.data?.show === "left" ? "Limits left" : "Limits used")))
        .when(Boolean(this.error), (el) => el.child(div().size(6).flex_none().rounded(3).bg(t.blocked))))
      .child(v_flex().px(rail ? 0 : 8).children(agents.map((a) => this.row(a, now, t, rail, cols))));
  }
}
