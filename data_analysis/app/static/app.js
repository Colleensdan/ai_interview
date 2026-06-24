"use strict";

// ---------------------------------------------------------------- state
const S = {
  models: [], model: null,
  overview: null,
  selected: new Set(),         // codes chosen for review
  docs: [], docIndex: 0,
  view: null,                  // current interview_view payload
  screen: "start",
  marginsHidden: false,        // 5.4 X / hamburger
  reasonsOpen: false,          // 5.5
  reasonList: [],              // 6a: AI reasons in render order
  activeReason: null,          // 6a: currently linked reason id
  reasonSource: null,          // patch 4: "span" (text→reason) | "reason" (reason→text)
  job: null,                   // 5.8 current job status
};

// ---------------------------------------------------------------- helpers
const $ = (sel, el = document) => el.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) n.append(kid.nodeType ? kid : document.createTextNode(kid));
  return n;
};
const esc = (s) => (s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const kfmt = (k) => (k == null ? "—" : Number(k).toFixed(2));
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) { location.href = "/login?next=/"; throw new Error("Not authenticated"); }
  if (!r.ok) { const t = await r.text(); throw new Error(t || r.statusText); }
  return r.json();
}
let toastTimer;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

// ---------------------------------------------------------------- boot
(async function init() {
  const m = await api("/api/models");
  S.models = m.models; S.model = m.default;
  const sel = $("#model-select");
  sel.innerHTML = "";
  S.models.forEach((mm) => sel.append(el("option", { value: mm }, mm)));
  sel.value = S.model;
  sel.addEventListener("change", async () => {
    S.model = sel.value;
    if (S.screen === "start") await loadOverview();
    if (S.screen === "compare") await loadInterview();
    render();
  });
  $("#hamburger").addEventListener("click", () => { S.marginsHidden = false; render(); });
  $("#finish-btn").addEventListener("click", () => { if (S.selected.size) go("finish"); else toast("Select at least one code first."); });
  await loadOverview();
  go("start");
})();

function go(screen) { S.screen = screen; render(); }

async function loadOverview() { S.overview = await api(`/api/overview?model=${encodeURIComponent(S.model)}`); }

// ---------------------------------------------------------------- top bar
function renderTop() {
  $("#model-pick-wrap").hidden = !(S.screen === "compare" || S.screen === "start");
  $("#finish-btn").hidden = !(S.screen === "compare");
  // Patch 8: the hamburger is the "restore the code-label margins" control.
  // It is shown ONLY on the comparison screen when the margins are hidden
  // (after the user closed them with X, or in the reasons view where they are
  // hidden by default), and it clearly says what it does.
  const hb = $("#hamburger");
  hb.hidden = !(S.screen === "compare" && S.marginsHidden);
  hb.textContent = "☰ Show code labels";
  hb.classList.add("restore-btn");
  $("#model-select").value = S.model;
  if (!$("#logout-link")) {
    const a = el("a", { id: "logout-link", href: "/logout", class: "navlink", style: "text-decoration:none" }, "Sign out");
    $(".topbar-right").append(a);
  }
  const nav = $("#topnav"); nav.innerHTML = "";
  const link = (label, scr, on) => el("button", {
    class: "navlink" + (S.screen === scr ? " active" : ""),
    onclick: on,
  }, label);
  nav.append(link("Overview", "start", () => go("start")));
  if (S.screen === "compare") {
    nav.append(link("Review", "compare", () => {}));
    nav.append(el("button", { class: "navlink", onclick: openFailures }, "Failure modes"));
    nav.append(el("button", { class: "navlink" + (S.reasonsOpen ? " active" : ""), onclick: toggleReasons }, "Reasons"));
    // Margins are hidden via the X on each margin and restored via the
    // hamburger (top-left); no separate nav toggle, to keep one clear control.
  }
}

// ---------------------------------------------------------------- router
function render() {
  hideHoverLabel();   // 6b: avoid a label orphaned across screen changes
  renderTop();
  const root = $("#screen"); root.innerHTML = "";
  ({ start: renderStart, compare: renderCompare, finish: renderFinish,
     loading: renderLoading, results: renderResults }[S.screen] || renderStart)(root);
}

// ================================================================ 5.2 START
function renderStart(root) {
  const o = S.overview;
  const wrap = el("div", { class: "start" });

  wrap.append(el("div", { class: "kpi-row" },
    el("div", { class: "kpi success" },
      el("div", { class: "big" }, `${o.n_success}`),
      el("div", { class: "lbl" }, `codes at κ > ${o.target}  ·  ${o.pct_success}%`)),
    el("div", { class: "kpi fail" },
      el("div", { class: "big" }, `${o.n_fail}`),
      el("div", { class: "lbl" }, `codes below κ ${o.target}  ·  ${o.pct_fail}%`)),
    el("div", { class: "kpi" },
      el("div", { class: "big" }, `${o.total}`),
      el("div", { class: "lbl" }, `codes total  ·  model: ${esc(o.model)}`)),
  ));

  const mkCol = (title, items, footer) => {
    const list = el("div", { class: "codelist" });
    items.forEach((it) => list.append(codeItem(it)));
    if (!items.length) list.append(el("div", { class: "empty" }, "None."));
    const col = el("div", { class: "col" },
      el("h3", {}, title, el("span", { class: "count" }, `${items.length}`)),
      list);
    if (footer) col.append(footer);
    return col;
  };

  // Patch 7: "Select all unsuccessful" sits under the Unsuccessful column.
  const allUnsuccessful = el("div", { class: "col-footer" },
    el("button", {
      class: "btn", disabled: o.fail.length === 0,
      onclick: () => { o.fail.forEach((e) => S.selected.add(e.code)); render(); },
    }, "Select all unsuccessful"));

  wrap.append(el("div", { class: "columns" },
    mkCol("✓ Successful codes", o.success),
    mkCol("✗ Unsuccessful codes", o.fail, allUnsuccessful),
  ));

  wrap.append(el("div", { class: "start-actions" },
    el("button", { class: "btn", onclick: () => { S.selected.clear(); render(); } }, "Clear"),
    el("span", { class: "hint" }, `${S.selected.size} selected`),
    el("span", { class: "spacer" }),
    el("button", {
      class: "btn btn-primary", disabled: S.selected.size === 0,
      onclick: startReview,
    }, "Review →"),
  ));
  root.append(wrap);
}

function codeItem(it) {
  const on = S.selected.has(it.code);
  return el("div", {
    class: "code-item" + (on ? " selected" : ""),
    onclick: () => { on ? S.selected.delete(it.code) : S.selected.add(it.code); render(); },
  },
    el("span", { class: "tick" }, on ? "✓" : ""),
    el("span", {}, it.code),
    el("span", { class: "kv" }, `κ ${kfmt(it.kappa)}`),
  );
}

async function startReview() {
  S.docs = (await api("/api/docs")).documents;
  if (!S.docs.length) { toast("No labelled interviews available."); return; }
  S.docIndex = 0; S.reasonsOpen = false; S.marginsHidden = false;
  await loadInterview();
  go("compare");
}

// ================================================================ 5.4 COMPARE
async function loadInterview() {
  const codes = [...S.selected].join("||");
  const doc = S.docs[S.docIndex];
  S.view = await api(`/api/interview?doc=${encodeURIComponent(doc)}&model=${encodeURIComponent(S.model)}&codes=${encodeURIComponent(codes)}`);
}

function renderSegments(segments, colors, collectReasons = false) {
  const frag = document.createDocumentFragment();
  segments.forEach((seg) => {
    if (!seg.codes.length) { frag.append(document.createTextNode(seg.text)); return; }
    const codeNames = seg.codes.map((c) => c.code);
    const color = colors[codeNames[0]] || "#ffe69a";
    const meaning = codeNames.map((c) => `${c}: ${S.view.meanings[c] || ""}`).join("\n");
    const m = el("mark", {
      class: "hl", style: `background:${color}`,
      title: meaning,                    // existing native tooltip (kept, 6b is additive)
      "data-codes": codeNames.join(" · "),
    }, seg.text);

    // 6a: link this AI span to its reason card.
    if (collectReasons) {
      const withReason = seg.codes.find((c) => c.reason);
      if (withReason) {
        const rid = S.reasonList.length;
        S.reasonList.push({ rid, code: withReason.code, quote: seg.text, reason: withReason.reason });
        m.dataset.rid = String(rid);
        if (String(rid) === String(S.activeReason)) m.classList.add("linked");
        m.style.cursor = "pointer";
        m.addEventListener("click", () => linkReason(rid));
      }
    }
    attachHoverLabel(m);   // 6b
    frag.append(m);
  });
  return frag;
}

// 6a: clicking an AI span highlights + scrolls its reason to the top of the panel.
function linkReason(rid) {
  S.activeReason = rid;
  S.reasonSource = "span";
  if (!S.reasonsOpen) { S.reasonsOpen = true; S.marginsHidden = true; }
  render();
}

function flashMark(m) {
  m.classList.add("flash");
  setTimeout(() => m.classList.remove("flash"), 1200);
}

// Patch 1: lock the two interview panels so scrolling one scrolls the other to
// the matching position (proportional, since both render the same transcript).
function setupSyncScroll() {
  const readings = document.querySelectorAll(".panels .reading");
  if (readings.length < 2) return;
  let lock = false;
  readings.forEach((src) => {
    src.addEventListener("scroll", () => {
      if (lock) return;
      lock = true;
      const denom = Math.max(1, src.scrollHeight - src.clientHeight);
      const ratio = src.scrollTop / denom;
      readings.forEach((dst) => {
        if (dst !== src) dst.scrollTop = ratio * (dst.scrollHeight - dst.clientHeight);
      });
      requestAnimationFrame(() => { lock = false; });
    });
  });
}

// 6b: floating code label at the cursor after a brief rest on a highlight.
let _hoverTimer, _hoverLabel, _lastMouse = { x: 0, y: 0 };
function attachHoverLabel(mark) {
  mark.addEventListener("mouseenter", () => {
    clearTimeout(_hoverTimer);
    _hoverTimer = setTimeout(() => showHoverLabel(mark.dataset.codes), 350);
  });
  mark.addEventListener("mousemove", (e) => {
    _lastMouse = { x: e.clientX, y: e.clientY };
    if (_hoverLabel) positionHoverLabel();
  });
  mark.addEventListener("mouseleave", () => { clearTimeout(_hoverTimer); hideHoverLabel(); });
}
function showHoverLabel(text) {
  hideHoverLabel();
  _hoverLabel = el("div", { class: "hover-label" }, text);
  document.body.append(_hoverLabel);
  positionHoverLabel();
}
function positionHoverLabel() {
  if (!_hoverLabel) return;
  _hoverLabel.style.left = _lastMouse.x + "px";
  _hoverLabel.style.top = _lastMouse.y + "px";
}
function hideHoverLabel() { if (_hoverLabel) { _hoverLabel.remove(); _hoverLabel = null; } }

function marginEl(codes, side, panelLabel) {
  const m = el("div", { class: "margin" + (S.marginsHidden ? " hidden" : "") });
  m.append(el("span", {
    class: "margin-x", title: "Hide margin",
    onclick: () => { S.marginsHidden = true; render(); },
  }, "✕"));
  m.append(el("div", { class: "mtitle" }, panelLabel + " codes"));
  if (!codes.length) m.append(el("div", { class: "empty" }, "—"));
  codes.forEach((c) => m.append(el("div", { class: "mcode" },
    el("span", { class: "swatch", style: `background:${S.view.colors[c] || "#ddd"}` }),
    el("span", {}, c))));
  return m;
}

function panel(kind, segments, codes, label) {
  const col = el("div", { class: "panel-col" },
    el("div", { class: "panel-head" }, el("span", {}, label), el("span", {}, `${codes.length} code(s)`)));
  const reading = el("div", { class: "reading" });
  reading.append(renderSegments(segments, S.view.colors, kind === "ai"));
  col.append(reading);
  const margin = marginEl(codes, kind === "ai" ? "left" : "right", kind === "ai" ? "AI" : "Human");
  const p = el("div", { class: "panel " + kind });
  // AI: margin on left (outer-left). Human: margin on right (outer-right via CSS order).
  p.append(margin, col);
  return p;
}

function renderCompare(root) {
  const v = S.view;
  S.reasonList = [];   // rebuilt as the AI panel renders (6a)
  const panels = el("div", { class: "panels" },
    panel("ai", v.ai_segments, v.ai_codes, `AI — ${esc(S.model)}`),
    panel("human", v.human_segments, v.human_codes, "Human ground truth"),
  );

  const compareWrap = el("div", { class: "compare-wrap" }, panels);
  if (S.reasonsOpen) compareWrap.append(reasonsPane());

  const foot = el("div", { class: "footbar" },
    el("div", { class: "counter" }, `${S.docIndex + 1}/${S.docs.length}`),
    el("div", {}, esc(v.title)),
    el("div", { class: "foot-actions" },
      el("button", { class: "btn", disabled: S.docIndex === 0, onclick: () => nav(-1) }, "←"),
      el("button", { class: "btn btn-primary", onclick: () => nav(1) }, "Next →")),
  );

  const container = el("div", { style: "display:flex;flex-direction:column;flex:1;min-height:0;" }, compareWrap, foot);
  root.append(container);

  requestAnimationFrame(() => {
    setupSyncScroll();  // patch 1
    if (!(S.reasonsOpen && S.activeReason != null)) return;
    const body = document.querySelector(".reasons-pane .rp-body");
    const card = body && body.querySelector(`.reason-card[data-rid="${S.activeReason}"]`);
    const mark = document.querySelector(`.panel.ai mark.hl[data-rid="${S.activeReason}"]`);
    // Reason card always scrolls to the top of its panel (6a).
    if (body && card) body.scrollTop = card.offsetTop - body.offsetTop;
    // Patch 4: if the user clicked a REASON, also jump the text to its span.
    if (S.reasonSource === "reason" && mark) {
      mark.scrollIntoView({ block: "center" });
      flashMark(mark);
    }
  });
}

async function nav(d) {
  const next = S.docIndex + d;
  if (next >= S.docs.length) { toast("You have reviewed all the data."); return; }
  if (next < 0) return;
  S.docIndex = next; await loadInterview(); render();
}

// ---- 5.5 reasons popup
function toggleReasons() {
  S.reasonsOpen = !S.reasonsOpen;
  if (S.reasonsOpen) S.marginsHidden = true; else S.activeReason = null;
  render();
}
function reasonsPane() {
  const body = el("div", { class: "rp-body" });
  if (!S.reasonList.length) {
    body.append(el("div", { class: "empty" }, "No AI reasons for the selected codes in this interview."));
  }
  S.reasonList.forEach((c) => {
    const active = String(c.rid) === String(S.activeReason);
    const card = el("div", { class: "reason-card" + (active ? " active" : ""), "data-rid": String(c.rid) },
      el("div", { class: "rc-code" }, c.code),
      el("div", { class: "rc-quote" }, `"${c.quote.trim().slice(0, 180)}"`),
      el("div", { class: "rc-reason" }, c.reason));
    card.addEventListener("click", () => { S.activeReason = c.rid; S.reasonSource = "reason"; render(); });
    body.append(card);
  });
  return el("div", { class: "reasons-pane" },
    el("div", { class: "rp-head" },
      el("span", {}, "Why the AI chose these codes"),
      el("span", { class: "hint", style: "font-weight:400;font-size:11px" }, "click a highlight or a reason to jump"),
      el("button", { class: "icon-btn", onclick: toggleReasons }, "✕")),
    body);
}

// ================================================================ 5.3 FAILURES
async function openFailures() {
  if (!S.selected.size) { toast("Select codes to inspect failure modes."); return; }
  let data;
  try { data = await api(`/api/failures?model=${encodeURIComponent(S.model)}&codes=${encodeURIComponent([...S.selected].join("||"))}`); }
  catch (e) { toast("Failed to load: " + e.message); return; }
  const failItem = (code, item, withReason) => {
    const p = { doc: item.document, code, quote: item.quote };
    if (item.start != null) { p.start = item.start; p.end = item.end; }
    const link = el("a", {
      class: "q fail-quote-link", href: "/context?" + new URLSearchParams(p).toString(),
      target: "_blank", rel: "noopener", title: "Open this passage in its full interview context (new tab)",
    }, `"${item.quote.slice(0, 160)}"`);
    const node = el("div", { class: "fail-item" }, el("div", { class: "doc" }, item.document), link);
    if (withReason && item.reason) node.append(el("div", { class: "fi-reason" }, item.reason));
    return node;
  };
  // Each failure is an individual span (patch 3).
  const blocks = data.failures.map((f) => el("div", { class: "fail-code-block" },
    el("h4", {}, f.code),
    el("div", { class: "fail-grid" },
      el("div", { class: "fail-col fp" },
        el("h5", {}, `Found where it should NOT be (${f.false_positives.length})`),
        ...(f.false_positives.length
          ? f.false_positives.map((fp) => failItem(f.code, fp, true))
          : [el("div", { class: "empty" }, "None.")])),
      el("div", { class: "fail-col fn" },
        el("h5", {}, `Missed where it SHOULD be (${f.false_negatives.length})`),
        ...(f.false_negatives.length
          ? f.false_negatives.map((fn) => failItem(f.code, fn, false))
          : [el("div", { class: "empty" }, "None.")])),
    )));
  const overlay = el("div", { class: "fail-overlay", onclick: (e) => { if (e.target === overlay) overlay.remove(); } },
    el("div", { class: "fail-modal" },
      el("h2", {}, "Failure modes", el("button", { class: "icon-btn", onclick: () => overlay.remove() }, "✕")),
      el("div", { class: "fail-section" }, ...blocks)));
  document.body.append(overlay);
}

// ================================================================ 5.7 FINISH
async function renderFinish(root) {
  const wrap = el("div", { class: "finish" });
  wrap.append(el("h2", {}, "Edit code definitions"));
  wrap.append(el("p", { class: "hint" }, "Edit a definition to improve LLM coding. Saving archives the previous version (never overwritten)."));
  const cards = el("div", {});
  wrap.append(cards);

  for (const code of S.selected) {
    const d = await api(`/api/definition?code=${encodeURIComponent(code)}`);
    cards.append(defCard(d));
  }

  const scope = el("select", {},
    el("option", { value: "one" }, "Default model only (OpenAI)"),
    el("option", { value: "all" }, "All available models"));
  wrap.append(el("div", { class: "finish-bar" },
    el("button", { class: "btn", onclick: () => go("compare") }, "← Back to review"),
    el("span", { class: "scope-pick" }, "Re-analyse with:", scope),
    el("button", { class: "btn btn-green", onclick: () => reanalyze(scope.value) }, "Re-Analyse"),
  ));
  root.append(wrap);
}

function defCard(d) {
  const ta = el("textarea", { class: "def-edit", placeholder: "Type a new definition…" }, d.current.definition);
  const box = el("div", { class: "def-box" }, d.current.definition);
  if (d.archived.length) {
    box.append(el("span", {
      class: "archive-arrow", title: "View previous definitions",
      onclick: (e) => {
        const card = e.target.closest(".def-card");
        const existing = card.querySelector(".archive-view");
        if (existing) { existing.remove(); return; }
        const av = el("div", { class: "archive-view" });
        d.archived.forEach((a) => av.append(el("div", {},
          el("div", {}, a.definition),
          el("div", { class: "ak" }, `v${a.version} · κ ${kfmt(a.kappa)}`))));
        box.after(av);
      },
    }, "⤵"));
  }
  return el("div", { class: "def-card", "data-code": d.code },
    el("h3", {}, d.code),
    el("div", { class: "kappa-badge" }, `current κ ${kfmt(d.current.kappa)} · v${d.current.version}`),
    el("div", { class: "hint" }, "Initial human definition (fixed reference — never changes)"),
    el("div", { class: "def-box def-initial" }, d.initial.definition),
    el("div", { class: "hint" }, "Current definition"),
    box,
    el("div", { class: "hint" }, "New definition"),
    ta,
    el("div", { style: "margin-top:8px" },
      el("button", {
        class: "btn", onclick: async (e) => {
          const def = ta.value.trim();
          if (!def) { toast("Definition is empty."); return; }
          await api("/api/definition", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: d.code, definition: def }) });
          toast(`Saved new definition for ${d.code}.`);
          render();
        },
      }, "Save definition")),
  );
}

// ================================================================ 5.8 / 5.9
async function reanalyze(scope) {
  // Auto-save any edited definitions that differ before re-running.
  for (const card of document.querySelectorAll(".def-card")) {
    const code = card.getAttribute("data-code");
    const ta = card.querySelector("textarea");
    const cur = card.querySelector(".def-box").firstChild.textContent;
    const val = ta.value.trim();
    if (val && val !== cur.trim()) {
      await api("/api/definition", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code, definition: val }) });
    }
  }
  const { job_id } = await api("/api/reanalyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ codes: [...S.selected], scope }) });
  S.job = { id: job_id, status: "running", done: 0, total: 0, message: "Starting…" };
  go("loading");
  pollJob(job_id);
}

async function pollJob(id) {
  try {
    const j = await api(`/api/jobs/${id}`);
    S.job = j;
    if (j.status === "running") { renderLoading($("#screen")); setTimeout(() => pollJob(id), 1500); return; }
    if (j.status === "error") { toast("Re-analysis failed: " + j.error); go("finish"); return; }
    S.results = j.results; go("results");
  } catch (e) { toast("Lost job: " + e.message); setTimeout(() => pollJob(id), 2500); }
}

function fmtEta(secs) {
  if (secs == null) return "estimating…";
  secs = Math.max(0, Math.round(secs));
  if (secs < 60) return `~${secs}s remaining`;
  const m = Math.floor(secs / 60), s = secs % 60;
  return `~${m}m ${s.toString().padStart(2, "0")}s remaining`;
}

function renderLoading(root) {
  root.innerHTML = "";
  const j = S.job || {};
  const pct = j.percent != null ? j.percent : 0;
  const feed = j.feed || [];
  const feedEl = el("div", { class: "feed" }, el("div", { class: "feed-title" }, "Live feed"));
  if (!feed.length) {
    feedEl.append(el("div", { class: "feed-empty" }, "Nothing found yet…"));
  } else {
    feed.forEach((f) => feedEl.append(el("div", { class: "feed-item" },
      el("span", { class: "fi-code" }, `Code found! ${f.code}`),
      el("div", { class: "fi-quote" }, `"${(f.quote || "").trim().slice(0, 160)}"`))));
  }
  root.append(el("div", { class: "loading" },
    el("div", { class: "spinner" }),
    el("div", { class: "loading-eta" }, `Re-analysing… ${pct}%`),
    el("div", { class: "progress-text" }, `${j.done || 0} / ${j.total || "?"} units · ${fmtEta(j.eta_seconds)}`),
    el("div", { class: "progress-text", style: "font-size:13px" }, j.message || ""),
    feedEl,
  ));
}

function renderResults(root) {
  const wrap = el("div", { class: "results" });
  wrap.append(el("h2", {}, "Re-analysis results"));
  wrap.append(el("p", { class: "hint" }, "Previous vs new Cohen's κ for each edited code."));
  (S.results || []).forEach((r) => {
    const prev = r.previous_kappa, nw = r.new_kappa;
    let cls = "same", arrow = "→";
    if (prev != null && nw != null) { if (nw > prev + 1e-9) { cls = "up"; } else if (nw < prev - 1e-9) { cls = "down"; } }
    else if (nw != null && prev == null) cls = "up";
    wrap.append(el("div", { class: "result-card" },
      el("div", { class: "rc-name" }, r.code),
      el("div", { class: "kappa-cmp" },
        el("span", {}, `κ ${kfmt(prev)}`),
        el("span", { class: "arrow" }, "→"),
        el("span", { class: cls }, `κ ${kfmt(nw)}`))));
  });
  wrap.append(el("button", {
    class: "btn btn-primary", style: "margin-top:14px",
    onclick: async () => { await loadOverview(); go("start"); },
  }, "Continue"));
  root.append(wrap);
}
