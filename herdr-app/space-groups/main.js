// Space groups: the shipped `spaces` sidebar plus folders of spaces.
//
// Derived from the Herdr App mod `spaces` (Apache License 2.0, Herdr). Changes:
// groups and subgroups (accordion), drag a space or a group onto a group, a
// space or the empty end of the list; a group shows how many spaces it holds and
// the most urgent agent status in them. Groups are this screen's view only:
// shared("space-groups"), never the core layout document, so tools that find
// spaces by their terminals' folders see the same spaces as before.
//
// Shipped behavior kept: stable colored avatars, worktrees under their checkout,
// grouped by machine when more than one is known; after a space's name the repo
// and branch where unusual. Click to switch, right-click for its menu.

import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { spaces, machines, panes, run, openOverlay, closeOverlay, props, exec, hc, theme, shared, setShared, watchFolder, window, layout } from "herdr";

// Groups: [{ id, name, machine, parent, folded, spaces: [space id] }], siblings in array order.
const KEY = "space-groups";
const RANK = { blocked: 4, done: 3, working: 2, idle: 1 };
const load = () => ({ groups: (shared(KEY)?.groups ?? []).map((g) => ({ ...g, spaces: [...(g.spaces ?? [])] })) });
const save = (s) => setShared(KEY, s);
const newId = () => `g${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
const kids = (s, parent, machine) => s.groups.filter((g) => (g.parent ?? null) === parent && (g.machine ?? null) === machine);
// `id` is `ancestor` or inside it.
function within(s, id, ancestor) {
  for (let g = s.groups.find((x) => x.id === id); g; g = s.groups.find((x) => x.id === g.parent)) if (g.id === ancestor) return true;
  return false;
}
const unplace = (s, space) => s.groups.forEach((g) => { g.spaces = g.spaces.filter((x) => x !== space); });

function find(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = find(child, id);
    if (found) return found;
  }
  return null;
}

// The sidebar's rail goes by its width, not a flag: dragging its edge narrows it too.
const narrow = () => (find(layout(), "sidebar")?.size?.px ?? Infinity) < 160;

// Where a space is now: its first terminal's live folder, else where it was made.
// `ps`: panes(), read once by the caller (each read copies every pane).
function place(s, ps) {
  const tabs = new Set((s.tabs ?? []).map((c) => c.id));
  return ps.find((p) => tabs.has(p.tab) && p.cwd)?.cwd || s.folder;
}
const rowHeight = ({ tree }) => (tree ? 28 : 30);
const where = (s, ps) => JSON.stringify([s.machine ?? null, place(s, ps)]);
const base = (path) => path.replace(/\/+$/, "").split("/").pop();
// Changes made outside Herdr (an editor's save, a commit in another app): looked
// for this often, in repos the list shows, while the window is in front.
const FALLBACK = 60000;

// What a stored color name looks like on an avatar (the same in every mod
// that draws space avatars): one strong color per hue, light and dark alike.
const MARK = { ansi6: "#22a99a", ansi3: "#e3814f", ansi4: "#5b8def", ansi5: "#8b7cf6", ansi2: "#3fae6a", ansi1: "#d9649a" };

// A space speaks up only when something in it needs you.
export function glyph(status) {
  return status === "blocked" || status === "done" ? "●" : "·";
}

export function tone(t, status) {
  return t[status] ?? t.unknown;
}

export default class Spaces extends View {
  init(_props, cx) {
    // The same mod opens as a group's menu or its name dialog.
    this.mode = props()?.menu ? "menu" : props()?.dialog ? "dialog" : "list";
    if (this.mode !== "list") return this.initOverlay(cx);
    this.git = {}; // where() -> { name, branch, ahead, behind, repo, linked }, null: not a repo
    this.checked = {}; // where() -> when it was last looked at
    this.unsure = new Set(); // where()s git couldn't answer for (a machine away)
    this.pending = new Set(); // where()s to look at next
    this.busy = {}; // pane -> an agent working or a program in front of its shell
    this.ready = {}; // machine -> its layout was readable at the last look
    this.at = {}; // space -> its where() at the last look
    this.watching = new Set(); // local repos' git folders
    // Spaces made while this runs wait (at most a moment) for their git, so a
    // worktree appears under its repo instead of jumping there.
    this.shown = new Set(spaces().map((s) => s.id));
    this.born = {};
    this.drag = null; // { kind: "space" | "group", id, machine } while the left button is down
    this.over = null; // { kind: "space" | "group" | "end", id, machine, zone: "before" | "after" | "into" }
    this.moved = false;
    this.of = {}; // space -> its group id (a worktree follows its checkout), from the last render
    this.anchor = {}; // space -> the space it is listed after in a group (itself, or its checkout)
    this.machines = [];
    this.reconnecting = JSON.parse(localStorage.getItem("machines.reconnecting") ?? "{}");
    for (const state of Object.values(this.reconnecting)) state.busy = false;
    this.folded = new Set(JSON.parse(localStorage.getItem("machines.folded") ?? "[]"));
    this.refreshMachines(cx);
    cx.timer.every(15000, cx => this.refreshMachines(cx));
    // `git status` walks the whole tree, on another machine a process there:
    // only when its answer could have changed (see `due`).
    cx.timer.every(250, (cx) => this.checkNew(cx));
  }

  checkNew(cx) {
    const now = Date.now();
    const sp = spaces();
    for (const s of sp) if (!this.shown.has(s.id)) this.born[s.id] ??= now;
    const due = this.due(now, sp, panes());
    if (due.size) this.refreshGit(due, cx);
    if (sp.some((s) => !this.shown.has(s.id)) || this.orderingDue(now)) cx.notify();
  }

  // Places whose git could have changed: new ones (a new space, a cd), where a
  // command or an agent's turn just ended (a checkout, a commit, edits), those on
  // a machine that is back, and repos in view whose fallback is due.
  due(now, sp, ps) {
    // Moved there: what it knew of that place may be old.
    const at = {};
    for (const s of sp) at[s.id] = where(s, ps);
    const keys = new Set(Object.values(at).filter((k) => JSON.parse(k)[1]));
    const due = new Set([...keys].filter((k) => !(k in this.git)));
    for (const [id, k] of Object.entries(at)) if (this.at[id] && this.at[id] !== k && keys.has(k)) due.add(k);
    this.at = at;
    const spaceOf = {};
    for (const s of sp) for (const t of s.tabs ?? []) spaceOf[t.id] = s;
    const busy = {};
    for (const p of ps) {
      if (!p.terminal || !spaceOf[p.tab]) continue;
      busy[p.pane] = p.status === "working" || !(p.program?.shell || p.program?.agent);
      if (this.busy[p.pane] && !busy[p.pane]) this.sameRepo(at[spaceOf[p.tab].id], keys).forEach((k) => due.add(k));
    }
    this.busy = busy;
    for (const m of machines()) {
      const ready = m.layout === "ready";
      if (ready && this.ready[m.name] === false) for (const k of keys) if (JSON.parse(k)[0] === m.name) due.add(k);
      this.ready[m.name] = ready;
    }
    if (window().active) {
      for (const k of keys) {
        const asks = this.git[k] || this.unsure.has(k);
        if (asks && !this.folded.has(JSON.parse(k)[0]) && now - (this.checked[k] ?? 0) > FALLBACK) due.add(k);
      }
    }
    return due;
  }

  // `key` and the other places in its repo (a commit there moves their ahead/behind).
  sameRepo(key, keys) {
    const repo = this.git[key]?.repo;
    return [key, ...(repo ? [...keys].filter((k) => this.git[k]?.repo === repo) : [])];
  }

  refreshMachines(cx) {
    cx.spawn(async cx => {
      try { const r = await hc(["machine", "ls", "--json"]); if (r.code === 0) this.machines = (JSON.parse(r.stdout).machines ?? []).filter((m) => !m.temporary); } catch {}
      cx.notify();
    });
  }

  rebuilt(machine) {
    return machine?.state === "unreachable" && /^a different core answers there \("[^"]+"\)$/.test(machine.why ?? "");
  }

  reconnect(machine, cx) {
    const name = machine.name;
    if (this.reconnecting[name]?.busy) return;
    const state = this.reconnecting[name] ??= { machine, removed: false };
    state.busy = true;
    state.error = null;
    localStorage.setItem("machines.reconnecting", JSON.stringify(this.reconnecting));
    cx.notify();
    cx.spawn(async cx => {
      try {
        const ask = async args => {
          const r = await hc([...args, "--json"]);
          if (r.code !== 0) {
            let error;
            try { error = JSON.parse(r.stdout || r.stderr).error?.message; } catch {}
            throw new Error(error || r.stderr || r.stdout || "Couldn't connect.");
          }
        };
        if (!state.removed) {
          await ask(["machine", "rm", name]);
          state.removed = true;
          localStorage.setItem("machines.reconnecting", JSON.stringify(this.reconnecting));
        }
        const args = ["machine", "add"];
        if (machine.via) args.push("--via", machine.target);
        else args.push(machine.target);
        args.push("--name", name);
        if (machine.session) args.push("--session", machine.session);
        if (machine.temporary) args.push("--temp");
        await ask(args);
        this.folded.delete(name);
        localStorage.setItem("machines.folded", JSON.stringify([...this.folded]));
        delete this.reconnecting[name];
      } catch (e) {
        state.error = String(e.message ?? e);
        state.busy = false;
      }
      localStorage.setItem("machines.reconnecting", JSON.stringify(this.reconnecting));
      this.refreshMachines(cx);
      cx.notify();
    });
  }

  // One look at a time, each place once however often it was asked for meanwhile.
  refreshGit(keys, cx) {
    for (const k of keys) this.pending.add(k);
    if (this.refreshing) return;
    this.refreshing = true;
    cx.spawn(async (cx) => {
      while (this.pending.size) {
        const keys = [...this.pending];
        this.pending.clear();
        for (const key of keys) await this.check(key, cx);
      }
      const used = new Set(Object.values(this.git).filter(Boolean).map((g) => JSON.parse(g.repo)).filter(([on]) => !on).map(([, repo]) => repo));
      for (const repo of this.watching) if (!used.has(repo)) this.watching.delete(repo);
      this.refreshing = false;
      cx.notify();
    });
  }

  // One place's git: branch, ahead/behind, its repo.
  async check(key, cx) {
    const [on, cwd] = JSON.parse(key);
    this.checked[key] = Date.now();
    this.unsure.delete(key);
    try {
      // No optional locks: a status that touched the index would wake the watch again.
      // Writes nothing under .git (index lock, fsmonitor cookie): watch() would wake on it.
      const r = await exec("git", ["--no-optional-locks", "-c", "core.fsmonitor=false", "status", "--porcelain=v2", "--branch"], { cwd, on });
      if (r.code !== 0) {
        // Only git's own "no" is remembered as no repo; anything else is asked again.
        if (!/not a git repository/i.test(r.stderr)) this.unsure.add(key);
        this.git[key] = null;
        return;
      }
      // The repo's own .git folder names it; a worktree's top differs from it.
      const top = await exec("git", ["rev-parse", "--show-toplevel"], { cwd, on });
      const common = await exec("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd, on });
      const repo = common.stdout.trim();
      const name = repo.endsWith("/.git") ? base(repo.slice(0, -5)) : base(repo).replace(/\.git$/, "");
      const info = { name, branch: null, ahead: 0, behind: 0, repo: JSON.stringify([on, repo]), linked: repo !== `${top.stdout.trim()}/.git` };
      for (const line of r.stdout.split("\n")) {
        if (line.startsWith("# branch.head ")) info.branch = line.slice(14);
        else if (line.startsWith("# branch.ab ")) {
          const [a, b] = line.slice(12).split(" ");
          info.ahead = Math.abs(parseInt(a, 10)) || 0;
          info.behind = Math.abs(parseInt(b, 10)) || 0;
        }
      }
      this.git[key] = info;
      if (!on) this.watch(repo, cx);
    } catch {
      // Couldn't ask (the machine is away): keep what it showed, look again later.
      this.git[key] ??= null;
      this.unsure.add(key);
    }
  }

  watch(repo, cx) {
    if (this.watching.has(repo)) return;
    this.watching.add(repo);
    cx.spawn(async (cx) => {
      try {
        while (this.watching.has(repo)) {
          await watchFolder(repo);
          const mine = JSON.stringify([null, repo]);
          if (this.watching.has(repo)) this.refreshGit(Object.keys(this.git).filter((k) => this.git[k]?.repo === mine), cx);
        }
      } catch { this.watching.delete(repo); }
    });
  }

  menu(w) {
    // A popup is as tall as its items; height is only the most.
    openOverlay({ mod: "menu", kind: "popup", width: 200, height: 260, props: { tab: "space", target: { id: w.id } } });
  }

  orderingDue(now) {
    return this.ordering && now - this.ordering.at >= 1000
      && this.ordering.machines.every(name => machines().some(m => m.name === name && m.layout === "ready"));
  }

  // Spaces in order, each worktree space moved under its main checkout's.
  ordered() {
    const sp = spaces(), ps = panes();
    const ready = (w) => this.shown.has(w.id) || where(w, ps) in this.git || Date.now() - (this.born[w.id] ??= Date.now()) > 1500;
    for (const w of sp) if (!this.shown.has(w.id) && ready(w)) this.shown.add(w.id);
    const list = sp.map((w, i) => ({ w, i, g: this.git[where(w, ps)] ?? null }));
    const mains = new Set(list.filter((r) => r.g && !r.g.linked).map((r) => r.g.repo));
    const nested = (r) => r.g?.linked && mains.has(r.g.repo);
    const out = [];
    const placed = new Set(); // repos whose worktrees are listed (under the first of its spaces)
    for (const r of list) {
      if (nested(r)) continue;
      out.push({ ...r, tree: null });
      if (!r.g || r.g.linked || placed.has(r.g.repo)) continue;
      placed.add(r.g.repo);
      const kids = list.filter((k) => nested(k) && k.g.repo === r.g.repo);
      kids.forEach((k, n) => out.push({ ...k, tree: n === kids.length - 1 ? "└─" : "├─" }));
    }
    // Simulate the batch so later indexes include earlier moves. An attempted
    // write can lose a sync conflict: allow the echo to settle before retrying.
    const order = sp.map(w => w.id);
    const target = out.map(r => r.w.id);
    const key = JSON.stringify([order, target, machines()]);
    if (order.every((id, i) => id === target[i])) {
      this.ordering = null;
    } else if (this.ordering?.key !== key || this.orderingDue(Date.now())) {
      const moved = new Set();
      this.ordering = { key, at: Date.now(), machines: [] };
      for (let i = 0; i < out.length; i++) {
        const from = order.indexOf(out[i].w.id);
        if (from === i) continue;
        run("space.move", { id: out[i].w.id, index: i + 1 });
        moved.add(out[i].w.machine ?? null);
        order.splice(i, 0, order.splice(from, 1)[0]);
      }
      this.ordering.machines = [...moved];
    }
    return out.filter(r => this.shown.has(r.w.id));
  }

  // The rows, under a header per machine once one isn't this computer.
  grouped(t, collapsed) {
    const rows = this.ordered();
    // The rail stays a flat list of numbers; the full sidebar shows the groups.
    const draw = (list, m) => (collapsed ? list.map((r) => this.compact(r, t)) : this.tree(list, m, t));
    this.of = {};
    this.anchor = {};
    const remote = [...new Set([...rows.map(r => r.w.machine).filter(Boolean), ...this.machines.map(m => m.name), ...Object.keys(this.reconnecting)])].sort();
    const loading = m => {
      const layout = machines().find(k => k.name === m)?.layout;
      return layout === "loading" || (!layout && this.machines.some(k => k.name === m && k.state === "connected"))
        || spaces().some(s => (s.machine ?? null) === m && !this.shown.has(s.id));
    };
    const loadingRow = m => div().id(`machine-loading-${m ?? "local"}`).h(28).flex_none().px(8).text_size(12).text_color(t.dim).child(collapsed ? "…" : "Loading…");
    if (!remote.length) return loading(null) && !rows.length ? [loadingRow(null)] : draw(rows, null);
    const machineNames = [null, ...remote];
    return machineNames.flatMap((m) => {
      const mine = rows.filter((r) => (r.w.machine ?? null) === m);
      const repair = this.reconnecting[m];
      const machine = this.machines.find(k => k.name === m) ?? repair?.machine;
      const connected = !m || machine?.state === "connected";
      const rebuilt = this.rebuilt(machine) || !!repair;
      const folded = this.folded.has(m);
      const head = h_flex().id(`machine-${m ?? "local"}`).h(26).flex_none().px(collapsed ? 2 : 8).gap(7).items_center().text_size(11).text_color(t.dim)
        .cursor_pointer().hover(s => s.bg(t.hover))
        .on_click((_e, cx) => {
          folded ? this.folded.delete(m) : this.folded.add(m);
          localStorage.setItem("machines.folded", JSON.stringify([...this.folded]));
          cx.notify();
        })
        .child(svg(`mods/workspace-chrome/${m ? "server" : "monitor"}.svg`).size(13).flex_none())
        .when(!collapsed, el => el.child(div().flex_1().truncate().child(m ?? "This computer"))
          .when(!connected && !rebuilt, el => el.child(div().flex_none().child((machine?.state ?? "asleep").replace(/_/g, " "))))
          .when(rebuilt, el => el.child(h_flex().flex_none().gap(3).items_center()
            .child(div().max_w(140).truncate().child(repair?.error || "rebuilt ·"))
            .child(div().id(`machine-connect-${m}`).text_color(repair?.error ? t.blocked : t.muted)
              .hover(s => s.text_color(t.foreground))
              .tooltip(repair?.error || "This machine was rebuilt or replaced. Click to connect to the new one.")
              .on_click((_e, cx) => { cx.stop_propagation(); this.reconnect(machine, cx); })
              .child(repair?.busy ? "Connecting…" : "Connect"))))
          .when(connected, el => el.child(div().id(`machine-add-${m ?? "local"}`).flex_none().px(4).rounded(4).hover(s => s.bg(t.hover).text_color(t.foreground))
            .on_click((_e, cx) => { cx.stop_propagation(); run("space.new", { machine: m ?? "" }); }).child("+")))
          .child(div().size(6).rounded(3).flex_none().bg(connected ? t.ansi2 : t.dim))
          .child(svg(`mods/workspace-chrome/chevron-${folded ? "right" : "down"}.svg`).size(11).flex_none()));
      const empty = h_flex().id(`machine-new-${m ?? "local"}`).h(28).flex_none().px(8).gap(7).items_center().rounded(7)
        .text_size(12).text_color(t.dim).hover(s => s.bg(t.hover).text_color(t.foreground))
        .on_click(() => run("space.new", { machine: m ?? "" }))
        .child(svg("mods/workspace-chrome/plus.svg").size(12)).when(!collapsed, el => el.child("New space there"));
      if (!collapsed && rebuilt) return [head];
      return [head, ...(folded ? [] : mine.length || kids(load(), null, m).length ? draw(mine, m) : [loading(m) ? loadingRow(m) : empty])];
    });
  }

  // Only what's unusual, after the name: "herdr \u00b7 spike \u21912 \u21931". The repo or
  // branch goes when the name already says it (a worktree is named after its branch),
  // and master/main go: nearly every space is on one.
  gitTail(g, tree, name, t) {
    const raw = g.branch ?? "detached";
    const same = (a) => a.toLowerCase() === name.toLowerCase();
    const branch = same(raw) || raw === "master" || raw === "main" ? "" : raw;
    const repo = tree || same(g.name) ? "" : g.name;
    const where = [repo, branch].filter(Boolean).join(" \u00b7 ");
    return h_flex()
      .flex_1()
      .min_w(0)
      .gap(5)
      .justify_end()
      .items_center()
      .text_size(11.5)
      .text_color(t.dim)
      .when(Boolean(where), (el) => el.child(div().min_w(0).truncate().child(where)))
      .when(g.ahead > 0, (el) => el.child(div().flex_none().text_color(t.ansi2).child(`\u2191${g.ahead}`)))
      .when(g.behind > 0, (el) => el.child(div().flex_none().text_color(t.ansi1).child(`\u2193${g.behind}`)));
  }

  // A drop line where the dragged item will land, or a frame when it goes into a group.
  dropMark(el, kind, id, t, after) {
    const o = this.over;
    if (!this.drag || !this.moved || o?.kind !== kind || o.id !== id || this.drag.id === id) return el;
    if (o.zone === "into") return el.border_1().border_color(t.accent);
    return el.child(div().absolute().left(0).right(0).h(2).bg(t.accent).when(after, (l) => l.bottom(0)).when(!after, (l) => l.top(0)));
  }

  row({ w, i, g, tree, depth = 0 }, t) {
    const on = w.active;
    const fromIndex = this.drag?.kind === "space" ? spaces().findIndex((s) => s.id === this.drag.id) : -1;
    // In a group the pointer picks the side; elsewhere the shipped core order decides.
    const after = this.of[w.id] || this.drag?.kind === "group" ? this.over?.zone === "after" : fromIndex < i;
    const colors = shared("space.colors") ?? {};
    const color = colors[w.id] ?? "ansi6";
    const fill = color.startsWith("#") ? color : (MARK[color] ?? t[color]);
    const target = { kind: "space", id: w.id, machine: w.machine ?? null };
    return this.dropMark(v_flex()
      .relative()
      .id(`space-${w.id}`)
      .pl(8 + depth * 14)
      .pr(8)
      .h(rowHeight({ tree }))
      .flex_none()
      .justify_center()
      .rounded(8)
      .when(on, (el) => el.bg(t.selected))
      .when(!on, (el) => el.hover((s) => s.bg(t.hover)))
      .when(this.drag?.id === w.id && this.moved, (el) => el.opacity(0.5)), "space", w.id, t, after)
      .on_mouse_down("left", () => this.press(target))
      .on_mouse_move((e, cx) => this.hover(target, e, cx))
      .on_mouse_up("left", (_e, cx) => this.drop(cx))
      .on_mouse_down("right", () => this.menu(w))
      .child(
        h_flex()
          .gap(9)
          .items_center()
          .when(Boolean(tree), el => el.pl(22).child(svg("mods/workspace-chrome/branch.svg").size(13).text_color(t.dim)))
          .when(!tree, el => el.child(h_flex().size(18).flex_none().rounded(5).bg(fill).items_center().justify_center().text_size(10.5).font_bold().text_color("#ffffff").child([...w.name][0]?.toUpperCase() ?? "?")))
          .child(div().flex_shrink(0).max_w(150).truncate().text_color(t.foreground).child(w.name))
          .child(g ? this.gitTail(g, tree, w.name, t) : div().flex_1()),
      );
  }

  // One machine's rows: its groups (subgroups first, then spaces), then the spaces in no group.
  tree(list, m, t) {
    const s = load();
    // Forget spaces that are gone, only while their machine's layout is readable.
    const ready = machines().some((k) => k.name === m && k.layout === "ready") || (m === null && !machines().length);
    const live = new Set(spaces().map((w) => w.id));
    let pruned = false;
    for (const g of s.groups) if ((g.machine ?? null) === m && ready && g.spaces.some((x) => !live.has(x))) { g.spaces = g.spaces.filter((x) => live.has(x)); pruned = true; }
    if (pruned) save(s);

    let main = null;
    for (const r of list) {
      const own = s.groups.find((g) => g.spaces.includes(r.w.id));
      this.of[r.w.id] = own?.id ?? (r.tree && main ? this.of[main] : null);
      this.anchor[r.w.id] = own || !r.tree || !main ? r.w.id : main;
      if (!r.tree) main = r.w.id;
    }
    const inside = (g) => list.filter((r) => this.of[r.w.id] === g.id)
      .sort((a, b) => g.spaces.indexOf(this.anchor[a.w.id]) - g.spaces.indexOf(this.anchor[b.w.id]) || list.indexOf(a) - list.indexOf(b));
    const all = (g) => [...inside(g), ...s.groups.filter((k) => k.parent === g.id).flatMap(all)];
    const out = [];
    const walk = (parent, depth) => {
      for (const g of kids(s, parent, m)) {
        const rows = all(g);
        const status = rows.map((r) => r.w.status).reduce((a, b) => ((RANK[b] ?? 0) > (RANK[a] ?? 0) ? b : a), null);
        out.push(this.groupRow(g, depth, rows.length, status, t));
        if (g.folded) continue;
        walk(g.id, depth + 1);
        for (const r of inside(g)) out.push(this.row({ ...r, depth: depth + 1 }, t));
      }
    };
    walk(null, 0);
    for (const r of list) if (!this.of[r.w.id]) out.push(this.row(r, t));
    return out;
  }

  // A group: chevron, name, the most urgent agent status inside and how many spaces it holds.
  groupRow(g, depth, count, status, t) {
    const target = { kind: "group", id: g.id, machine: g.machine ?? null };
    const mark = status === "working"
      ? svg("mods/workspace-chrome/spinner.svg").size(12).flex_none().text_color(t.working).spin(900)
      : status === "idle"
        ? div().size(6).flex_none().rounded(3).border_1().border_color(t.dim)
        : status ? div().size(6).flex_none().rounded(3).bg(t[status]) : null;
    return this.dropMark(h_flex()
      .relative()
      .id(`group-${g.id}`)
      .h(30)
      .flex_none()
      .pl(8 + depth * 14)
      .pr(10)
      .gap(9)
      .items_center()
      .rounded(8)
      .hover((s) => s.bg(t.hover))
      .when(this.drag?.id === g.id && this.moved, (el) => el.opacity(0.5)), "group", g.id, t, this.over?.zone === "after")
      .tooltip(`${count} space${count === 1 ? "" : "s"}${status ? `, ${status}` : ""}`)
      .on_mouse_down("left", () => this.press(target))
      .on_mouse_move((e, cx) => this.hover(target, e, cx))
      .on_mouse_up("left", (_e, cx) => this.drop(cx))
      .on_mouse_down("right", () => this.groupMenu(g))
      .child(h_flex().size(18).flex_none().items_center().justify_center().text_color(t.dim)
        .child(svg(`mods/workspace-chrome/chevron-${g.folded ? "right" : "down"}.svg`).size(12)))
      .child(div().flex_1().min_w(0).truncate().font_medium().text_color(t.foreground).child(g.name))
      .when(Boolean(mark), (el) => el.child(mark))
      .child(div().flex_none().text_size(11.5).text_color(t.dim).child(String(count)));
  }

  press(target) {
    this.drag = target;
    this.over = { ...target, zone: "into" };
    this.moved = false;
  }

  // Where a drop would land: a space's upper or lower half; a group's middle is "into"
  // (a space always goes into it), its edges before or after it among its siblings.
  hover(target, e, cx) {
    if (!this.drag) return;
    let over = null;
    if (target.machine === this.drag.machine) {
      const h = e.bounds?.height || 28, y = e.local_position?.y ?? h / 2;
      const zone = target.kind === "end" || (target.kind === "group" && this.drag.kind === "space") ? "into"
        : target.kind === "group" ? (y < h * 0.3 ? "before" : y > h * 0.7 ? "after" : "into")
          : y < h / 2 ? "before" : "after";
      over = { ...target, zone };
    }
    if (JSON.stringify(over) === JSON.stringify(this.over)) return;
    this.over = over;
    if (over?.id !== this.drag.id) this.moved = true;
    cx.notify();
  }

  drop(cx) {
    const d = this.drag, o = this.over, moved = this.moved;
    this.drag = null;
    this.over = null;
    this.moved = false;
    if (!d) return;
    if (!moved) {
      if (d.kind === "space") run("space.select", { id: d.id });
      else this.edit((s) => { const g = s.groups.find((x) => x.id === d.id); if (g) g.folded = !g.folded; });
      return cx.notify();
    }
    if (!o || o.id === d.id) return cx.notify();
    const s = load();
    const find = (id) => s.groups.find((x) => x.id === id);
    if (d.kind === "space") {
      unplace(s, d.id);
      if (o.kind === "group") { find(o.id).spaces.push(d.id); find(o.id).folded = false; }
      else if (o.kind === "space" && this.of[o.id] && find(this.of[o.id])) {
        const list = find(this.of[o.id]).spaces;
        let at = list.indexOf(this.anchor[o.id]);
        at = at < 0 ? list.length : at + (o.zone === "after" ? 1 : 0);
        list.splice(at, 0, d.id);
      } else if (o.kind === "space") {
        const index = spaces().findIndex((w) => w.id === o.id);
        if (index !== -1) run("space.move", { id: d.id, index: index + 1 });
      }
    } else {
      const g = find(d.id);
      if (!g) return cx.notify();
      const ref = o.kind === "group" && o.zone !== "into" ? find(o.id) : null;
      const parent = o.kind === "group" ? (ref ? ref.parent ?? null : o.id) : o.kind === "space" ? this.of[o.id] ?? null : null;
      if (parent && within(s, parent, g.id)) return cx.notify(); // not into itself or below
      s.groups.splice(s.groups.indexOf(g), 1);
      g.parent = parent;
      s.groups.splice(ref ? s.groups.indexOf(ref) + (o.zone === "after" ? 1 : 0) : s.groups.length, 0, g);
      if (parent) find(parent).folded = false;
    }
    save(s);
    cx.notify();
  }

  edit(change) {
    const s = load();
    change(s);
    save(s);
  }

  groupMenu(g) {
    openOverlay({ mod: "space-groups", kind: "popup", width: 210, height: 140, props: { menu: { id: g.id } } });
  }

  newGroup() {
    const m = spaces().find((w) => w.active)?.machine ?? null;
    openOverlay({ mod: "space-groups", kind: "dialog", width: 380, props: { dialog: { action: "new", machine: m, parent: null } } });
  }

  // --- The menu and the name dialog (this mod opened as an overlay) ---

  initOverlay(cx) {
    this.handle = cx.focus_handle();
    this.at = 0;
    const d = props().dialog;
    if (!d) return;
    const g = load().groups.find((x) => x.id === d.id);
    this.text = d.action === "rename" ? g?.name ?? "" : "";
    this.input = InputState.new({ value: this.text, placeholder: "Group name" });
    if (this.text) this.input.set_selected_range({ start: 0, end: this.text.length });
    this.input.on("change", (_e, cx) => { this.text = this.input.value(); cx.notify(); });
    this.input.on("submit", () => this.submit());
    this.input.focus();
    cx.timer.after(600, () => this.input.focus());
  }

  submit() {
    const p = props(), d = p.dialog, name = this.text.trim();
    closeOverlay(p.overlay);
    if (!name) return;
    this.edit((s) => {
      if (d.action === "rename") { const g = s.groups.find((x) => x.id === d.id); if (g) g.name = name; }
      else s.groups.push({ id: newId(), name, machine: d.machine ?? null, parent: d.parent ?? null, folded: false, spaces: [] });
    });
  }

  menuItems() {
    const p = props(), id = p.menu.id;
    const g = load().groups.find((x) => x.id === id);
    const dialog = (dialog) => () => openOverlay({ mod: "space-groups", kind: "dialog", width: 380, props: { dialog } });
    return !g ? [] : [
      ["Rename…", "square-pen", dialog({ action: "rename", id })],
      ["New subgroup…", "folder-plus", dialog({ action: "new", machine: g.machine ?? null, parent: id })],
      // Its spaces and subgroups go to its parent; the spaces themselves are untouched.
      ["Ungroup", "x", () => this.edit((s) => {
        const i = s.groups.findIndex((x) => x.id === id);
        const [gone] = s.groups.splice(i, 1);
        const up = s.groups.find((x) => x.id === gone.parent);
        if (up) up.spaces.push(...gone.spaces);
        s.groups.forEach((x) => { if (x.parent === id) x.parent = gone.parent ?? null; });
      })],
    ];
  }

  renderMenu(t) {
    const list = this.menuItems();
    const choose = (i) => { closeOverlay(props().overlay); list[i]?.[2](); };
    return v_flex().id("group-menu").track_focus(this.handle).w_full().min_h(0).p(5).bg(t.popover).text_size(12.8)
      .on_key_down((e, cx) => {
        if (e.key === "down") this.at = (this.at + 1) % list.length;
        else if (e.key === "up") this.at = (this.at + list.length - 1) % list.length;
        else if (e.key === "enter") return choose(this.at);
        else if (e.key === "escape") return closeOverlay(props().overlay);
        else return;
        cx.notify();
      })
      .children(list.map(([label, icon], i) => h_flex().id(`gm-${i}`).h(31).px(8).gap(9).rounded(7).items_center()
        .text_color(t.foreground).when(i === this.at, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
        .on_click(() => choose(i))
        .child(svg(`mods/workspace-chrome/${icon}.svg`).size(14).flex_none().text_color(t.muted))
        .child(div().flex_1().child(label))));
  }

  renderDialog(t) {
    const p = props(), d = p.dialog;
    const button = (id, text, primary, action) => h_flex().id(id).h(32).px(14).items_center().rounded(8)
      .border_1().border_color(primary ? t.solid : t.input).bg(primary ? t.solid : t.raised)
      .text_color(primary ? t.solid_foreground : t.foreground).text_size(12.5).font_medium().cursor_pointer()
      .hover((s) => s.opacity(0.88)).on_click(action).child(text);
    const title = d.action === "rename" ? "Rename group" : d.parent ? "New subgroup" : "New group";
    return v_flex().id("group-dialog").track_focus(this.handle)
      .on_key_down((e, cx) => { if (e.key === "escape") { closeOverlay(p.overlay); cx.stop_propagation(); } })
      .w_full().min_h(0).p(18).bg(t.popover).text_color(t.foreground)
      .child(h_flex().items_center().child(div().flex_1().text_size(15).font_semibold().child(title))
        .child(div().text_size(11).text_color(t.dim).child("Esc")))
      .child(div().pt(16).pb(7).text_size(11.5).text_color(t.dim).child("Name"))
      .child(Input.new(this.input).h(36).px(10).rounded(9).border_1().border_color(t.accent).bg(t.background).text_size(12.8).text_color(t.foreground))
      .child(div().pt(6).text_size(11.5).text_color(t.dim).child("Drag spaces onto the group to fill it. Groups live in this window only."))
      .child(h_flex().pt(20).gap(8).justify_end()
        .child(button("gd-cancel", "Cancel", false, () => closeOverlay(p.overlay)))
        .child(button("gd-save", "Save", true, () => this.submit())));
  }

  compact({ w, i }, t) {
    return h_flex()
      .id(`space-${w.id}`)
      .h(26)
      .flex_none()
      .justify_center()
      .gap(4)
      .when(w.active, (el) => el.bg(t.raised))
      .hover((s) => s.bg(t.hover))
      .on_click(() => run("space.select", { id: w.id }))
      .child(div().text_color(w.active ? t.foreground : t.muted).child(String(i + 1)))
      .child(div().text_color(tone(t, w.status)).child(glyph(w.status)));
  }

  render() {
    const t = theme();
    if (this.mode === "menu") return this.renderMenu(t);
    if (this.mode === "dialog") return this.renderDialog(t);
    const collapsed = narrow();
    const palette = ["ansi6", "ansi3", "ansi4", "ansi5", "ansi2", "ansi1"];
    const sp = spaces();
    const ids = new Set(sp.map(p => p.id));
    const kept = Object.fromEntries(Object.entries(shared("space.colors") ?? {}).filter(([id]) => ids.has(id)));
    let changed = Object.keys(kept).length !== Object.keys(shared("space.colors") ?? {}).length;
    for (const p of sp) {
      if (kept[p.id] || p.name === "~") continue;
      const uses = c => Object.values(kept).filter(v => v === c).length;
      kept[p.id] = palette.reduce((best, c) => uses(c) < uses(best) ? c : best);
      changed = true;
    }
    if (changed) setShared("space.colors", kept);
    return v_flex().size_full().min_h(0).bg(t.chrome).text_size(13)
      .child(h_flex().h(31).pl(16).pr(10).items_center().justify_between().text_size(11.5).text_color(t.dim)
        .when(!collapsed, el => el.child(div().flex_1().child("Spaces"))
          .child(div().id("group-new").size(26).flex_none().flex().items_center().justify_center().rounded(6).hover(s => s.bg(t.hover).text_color(t.foreground))
            .tooltip("New group").on_click(() => this.newGroup()).child(svg("mods/workspace-chrome/folder-plus.svg").size(14))))
        .child(div().id("space-new").size(26).flex_none().flex().items_center().justify_center().rounded(6).hover(s => s.bg(t.hover).text_color(t.foreground))
          .on_click(() => run("space.new")).child(svg("mods/workspace-chrome/plus.svg").size(14))))
      .child(v_flex().flex_1().min_h(0).px(8).pb(14).overflow_y_scrollbar().mode("always")
        .on_mouse_up("left", (_e, cx) => { this.drag = null; this.over = null; cx.notify(); })
        .children(this.grouped(t, collapsed))
        // Below the last row: out of every group (a group: to the top level).
        .when(!collapsed, el => el.child(this.dropMark(div().id("groups-end").flex_1().min_h(24).rounded(8), "end", "end", t, false)
          .on_mouse_move((e, cx) => this.drag && this.hover({ kind: "end", id: "end", machine: this.drag.machine }, e, cx))
          .on_mouse_up("left", (_e, cx) => this.drop(cx)))));
  }
}
