// Agent limits: how much of each agent runtime's usage windows is used
// (5 hours, 7 days, a month, a spend cap), one compact row per agent.
//
// Data: herdr-agent-kit's limits.py, run where the kit lives (`source.json`,
// written by the installer: { "on": machine or null, "argv": [...] }). Which
// agents are shown is the kit's `[limits]` setting, not this file.
// Hover a window for its reset time; click the header to refresh now.

import { View, div, svg, image } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, readTextFile, appHome, theme, shared } from "herdr";

const EVERY = 60000; // limits move slowly; the files behind them change on agent turns
const STALE = 3 * 3600; // data older than this is dimmed
const NAMES = { claude: "Claude", codex: "Codex", opencode: "OpenCode", cursor: "Cursor", kimi: "Kimi", antigravity: "Antigravity", gemini: "Gemini" };

function span(sec) {
  if (sec < 3600) return `${Math.max(1, Math.round(sec / 60))}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m`;
  return `${Math.floor(sec / 86400)}d ${Math.round((sec % 86400) / 3600)}h`;
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
  cell(w, now, t, key, show) {
    const known = w.used !== null && w.used !== undefined;
    const value = known ? Math.round(show === "left" ? 100 - w.used : w.used) : null;
    const tip = !known ? `${w.name}: renewed since the last reading`
      : `${w.name}: ${Math.round(w.used)}% used, ${Math.round(100 - w.used)}% left${w.resets_at ? `, resets in ${span(w.resets_at - now)}` : ""}`;
    return v_flex().id(key).w(50).flex_none().gap(3).tooltip(tip)
      .child(h_flex().gap(4).text_size(10.5).items_center()
        .child(div().text_color(t.dim).child(w.name))
        .child(div().text_color(known ? t.foreground : t.dim).child(known ? `${value}%` : "new")))
      .child(div().h(3).w_full().rounded(2).bg(t.hover)
        .when(known, (el) => el.child(div().h_full().rounded(2).bg(this.tone(t, w.used)).w(Math.max(2, Math.min(50, value / 2))))));
  }

  mark(a, t) {
    const ic = icon(a.id);
    if (ic) return ic.tint ? svg(ic.icon).size(14).flex_none().text_color(t.foreground) : image(ic.icon).size(14).flex_none();
    return h_flex().size(14).flex_none().rounded(4).bg(t.raised).items_center().justify_center()
      .text_size(9).font_bold().text_color(t.muted).child((a.id[0] ?? "?").toUpperCase());
  }

  row(a, now, t, rail) {
    const stale = a.seen && now - a.seen > STALE;
    const name = NAMES[a.id] ?? a.id;
    const tip = a.error ? `${name}: ${a.error}` : `${name}${a.seen ? `, data from ${span(Math.max(60, now - a.seen))} ago` : ""}`;
    const top = a.windows.reduce((m, w) => Math.max(m, w.used ?? 0), 0);
    const show = this.data?.show ?? "used";
    if (rail) {
      return h_flex().id(`limit-${a.id}`).h(26).flex_none().justify_center().gap(4).items_center().tooltip(tip)
        .child(this.mark(a, t))
        .child(div().text_size(10.5).text_color(a.windows.length ? this.tone(t, top) : t.dim).child(a.windows.length ? `${Math.round(show === "left" ? 100 - top : top)}` : "?"));
    }
    return h_flex().id(`limit-${a.id}`).h(30).flex_none().px(8).gap(8).items_center().rounded(8)
      .when(Boolean(stale), (el) => el.opacity(0.55))
      .child(h_flex().id(`limit-name-${a.id}`).flex_1().min_w(0).gap(8).items_center().tooltip(tip)
        .child(this.mark(a, t))
        .when(this.width >= 280, (el) => el.child(div().min_w(0).truncate().text_size(12.5).text_color(t.muted).child(name))))
      .when(!a.windows.length, (el) => el.child(div().flex_none().text_size(11).text_color(t.dim).child(a.error ? "no data" : "-")))
      .children(a.windows.map((w, i) => this.cell(w, now, t, `limit-${a.id}-${i}`, show)));
  }

  render() {
    const t = theme();
    const rail = this.width < 160;
    const now = Math.floor(Date.now() / 1000);
    const agents = this.data?.agents ?? [];
    return v_flex().id("root").size_full().min_h(0).bg(t.chrome).pb(6)
      .on_size_change((size, cx) => { if (Math.abs(size.width - this.width) > 1) { this.width = size.width; cx.notify(); } })
      .child(h_flex().id("limits-head").h(28).flex_none().pl(rail ? 0 : 16).pr(10).items_center().text_size(11.5).text_color(t.dim)
        .when(rail, (el) => el.justify_center())
        .cursor_pointer().on_click((_e, cx) => this.refresh(cx))
        .tooltip(this.error ?? "Click to refresh")
        .child(div().flex_1().child(rail ? "%" : (this.data?.show === "left" ? "Limits left" : "Limits used")))
        .when(Boolean(this.error), (el) => el.child(div().size(6).flex_none().rounded(3).bg(t.blocked))))
      .child(v_flex().px(rail ? 0 : 8).children(agents.map((a) => this.row(a, now, t, rail))));
  }
}
