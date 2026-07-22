"""The Observatory page (Phase 6b) — the entity's face.

Single-file, single-page: HTML/CSS/JS live here as Python string
constants so packaging stays trivial (pure stdlib, no assets dir, no
CDN, no external fonts). Served by anima.runtime.senses.web_sense.

Design language: an observatory at night. Deep blue-black sky, faint
starfield, hairline borders, one warm accent (the dome lamp) and one
cold accent (starlight). Everything glows a little; nothing shouts.
"""

from __future__ import annotations

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ — Observatory</title>
<style>
:root {
  --sky0: #05070f; --sky1: #0a0f1f; --sky2: #101a33;
  --panel: rgba(13, 20, 40, 0.72);
  --line: rgba(120, 160, 255, 0.14);
  --ink: #c8d4ee; --ink-dim: #6a7a9c; --ink-faint: #3d4a66;
  --star: #7fd4ff;            /* cold accent: starlight */
  --lamp: #ffb45e;            /* warm accent: dome lamp */
  --ok: #6fe3a5; --err: #ff7d90;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --sans: system-ui, -apple-system, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body {
  font-family: var(--sans); color: var(--ink);
  background:
    radial-gradient(1100px 500px at 75% -10%, var(--sky2), transparent 60%),
    radial-gradient(900px 600px at -10% 110%, #0d1430, transparent 55%),
    linear-gradient(180deg, var(--sky1), var(--sky0) 60%);
  background-attachment: fixed;
  min-height: 100vh;
}
/* faint starfield, pure CSS */
body::before {
  content: ""; position: fixed; inset: 0; pointer-events: none;
  background-image:
    radial-gradient(1px 1px at 12% 22%, rgba(200,220,255,.7), transparent 51%),
    radial-gradient(1px 1px at 34% 8%,  rgba(200,220,255,.5), transparent 51%),
    radial-gradient(1.5px 1.5px at 58% 16%, rgba(160,210,255,.6), transparent 51%),
    radial-gradient(1px 1px at 71% 31%, rgba(200,220,255,.4), transparent 51%),
    radial-gradient(1px 1px at 86% 12%, rgba(200,220,255,.6), transparent 51%),
    radial-gradient(1.5px 1.5px at 93% 42%, rgba(160,210,255,.35), transparent 51%),
    radial-gradient(1px 1px at 22% 55%, rgba(200,220,255,.25), transparent 51%),
    radial-gradient(1px 1px at 45% 72%, rgba(200,220,255,.2), transparent 51%);
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 20px 22px 60px; position: relative; }

header {
  display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
  padding: 6px 2px 18px; border-bottom: 1px solid var(--line);
  margin-bottom: 20px;
}
header h1 {
  font-size: 22px; font-weight: 600; margin: 0; letter-spacing: .04em;
  color: #e8eefc; text-shadow: 0 0 18px rgba(127, 212, 255, .35);
}
header h1 .dome { color: var(--lamp); text-shadow: 0 0 14px rgba(255,180,94,.5); }
header .sub { font-family: var(--mono); font-size: 12px; color: var(--ink-dim); }
header .spacer { flex: 1; }
.pill {
  font-family: var(--mono); font-size: 11px; padding: 3px 10px;
  border: 1px solid var(--line); border-radius: 999px; color: var(--ink-dim);
}
.pill .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: var(--ok); box-shadow: 0 0 8px var(--ok); margin-right: 6px;
  vertical-align: 1px; }
.pill.off .dot { background: var(--err); box-shadow: 0 0 8px var(--err); }

.grid { display: grid; grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
  gap: 18px; align-items: start; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }

.panel {
  background: var(--panel); border: 1px solid var(--line);
  border-radius: 12px; padding: 14px 16px;
  backdrop-filter: blur(6px);
  box-shadow: 0 10px 40px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.03);
}
.panel h2 {
  margin: 0 0 10px; font-size: 11px; font-weight: 600; letter-spacing: .22em;
  text-transform: uppercase; color: var(--ink-dim);
}
.panel h2 .tick { color: var(--star); }

/* ── chat ── */
#chatlog { height: 380px; overflow-y: auto; display: flex;
  flex-direction: column; gap: 8px; padding: 4px 2px; scroll-behavior: smooth; }
.msg { max-width: 86%; padding: 8px 12px; border-radius: 10px;
  font-size: 14px; line-height: 1.45; white-space: pre-wrap;
  word-break: break-word; }
.msg.you { align-self: flex-end; background: rgba(127,212,255,.10);
  border: 1px solid rgba(127,212,255,.25); border-bottom-right-radius: 3px; }
.msg.ent { align-self: flex-start; background: rgba(255,180,94,.07);
  border: 1px solid rgba(255,180,94,.22); border-bottom-left-radius: 3px; }
.msg .who { display: block; font-family: var(--mono); font-size: 10px;
  color: var(--ink-dim); margin-bottom: 3px; letter-spacing: .08em; }
#chatform { display: flex; gap: 8px; margin-top: 10px; }
#chatinput { flex: 1; background: rgba(5,8,16,.6); color: var(--ink);
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
  font: 14px var(--sans); outline: none; }
#chatinput:focus { border-color: rgba(127,212,255,.45);
  box-shadow: 0 0 0 3px rgba(127,212,255,.08); }
button {
  background: linear-gradient(180deg, rgba(127,212,255,.16), rgba(127,212,255,.06));
  color: var(--star); border: 1px solid rgba(127,212,255,.35);
  border-radius: 8px; padding: 10px 18px; font: 600 13px var(--sans);
  cursor: pointer; letter-spacing: .05em;
}
button:hover { box-shadow: 0 0 16px rgba(127,212,255,.25); }
button:disabled { opacity: .4; cursor: default; box-shadow: none; }

/* ── expressions ── */
#exprs { display: flex; flex-direction: column; gap: 12px;
  max-height: 460px; overflow-y: auto; padding-right: 2px; }
.card { border: 1px solid var(--line); border-radius: 10px;
  background: rgba(5,9,18,.55); overflow: hidden; }
.card .body { padding: 12px; overflow: auto; max-height: 260px; }
.card .body svg { max-width: 100%; height: auto; display: block; }
.card .cap { display: flex; justify-content: space-between; gap: 8px;
  padding: 6px 12px; border-top: 1px solid var(--line);
  font-family: var(--mono); font-size: 10px; color: var(--ink-dim); }
.card .cap .t { color: var(--lamp); }
.empty { color: var(--ink-faint); font-size: 13px; font-style: italic;
  padding: 12px 4px; }

/* ── drives ── */
.drive { margin: 10px 0; }
.drive .row { display: flex; justify-content: space-between;
  font-family: var(--mono); font-size: 11px; margin-bottom: 4px; }
.drive .row .nm { color: var(--ink); letter-spacing: .06em; }
.drive .row .val { color: var(--ink-dim); }
.gauge { height: 6px; border-radius: 3px; background: rgba(255,255,255,.05);
  overflow: hidden; border: 1px solid rgba(255,255,255,.04); }
.gauge .fill { height: 100%; border-radius: 3px; width: 0;
  background: linear-gradient(90deg, #3d7ecf, var(--star));
  box-shadow: 0 0 10px rgba(127,212,255,.4);
  transition: width 1.2s cubic-bezier(.22,1,.36,1); }
.gauge .fill.hot { background: linear-gradient(90deg, #c77b2e, var(--lamp));
  box-shadow: 0 0 12px rgba(255,180,94,.5); }
.drive .desc { font-size: 11px; color: var(--ink-faint); margin-top: 3px; }

/* ── streams ── */
.threecol { display: grid; grid-template-columns: repeat(3, minmax(0,1fr));
  gap: 18px; margin-top: 18px; }
@media (max-width: 900px) { .threecol { grid-template-columns: 1fr; } }
.stream { font-family: var(--mono); font-size: 11px; line-height: 1.7;
  max-height: 300px; overflow-y: auto; color: var(--ink-dim); }
.stream .ln { padding: 2px 0; border-bottom: 1px dotted rgba(255,255,255,.04);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stream .ln b { color: var(--ink); font-weight: 600; }
.stream .ln .k { color: var(--star); }
.stream .ln .e { color: var(--err); }
.stream .ln .ts { color: var(--ink-faint); }

/* ── memory search ── */
#memform { display: flex; gap: 8px; margin-bottom: 10px; }
#meminput { flex: 1; background: rgba(5,8,16,.6); color: var(--ink);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px;
  font: 13px var(--sans); outline: none; }
#meminput:focus { border-color: rgba(255,180,94,.4);
  box-shadow: 0 0 0 3px rgba(255,180,94,.07); }
#memresults .hit { padding: 8px 10px; margin: 6px 0; border-radius: 8px;
  border: 1px solid var(--line); background: rgba(5,9,18,.45);
  font-size: 12.5px; line-height: 1.5; }
#memresults .hit .tag { font-family: var(--mono); font-size: 9.5px;
  color: var(--lamp); letter-spacing: .12em; text-transform: uppercase;
  margin-right: 8px; }
#memresults .hit .meta { font-family: var(--mono); font-size: 10px;
  color: var(--ink-faint); margin-top: 3px; }

footer { margin-top: 26px; text-align: center; font-family: var(--mono);
  font-size: 10px; color: var(--ink-faint); letter-spacing: .12em; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: rgba(120,160,255,.15); border-radius: 4px; }
::-webkit-scrollbar-track { background: transparent; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="dome">◉</span> __NAME__</h1>
    <span class="sub">the observatory</span>
    <span class="spacer"></span>
    <span class="pill" id="lockpill"><span class="dot"></span><span id="locktext">…</span></span>
    <span class="pill" id="statpill">episodes — · beliefs — · wakes —</span>
  </header>

  <div class="grid">
    <section class="panel">
      <h2><span class="tick">▸</span> conversation</h2>
      <div id="chatlog"><div class="empty">Say something. The entity wakes when spoken to.</div></div>
      <form id="chatform">
        <input id="chatinput" autocomplete="off"
               placeholder="speak into the dome…">
        <button type="submit" id="sendbtn">Send</button>
      </form>
    </section>

    <section class="panel">
      <h2><span class="tick">▸</span> expressions <span style="float:right;color:var(--ink-faint);font-weight:400;letter-spacing:0;text-transform:none">what it chose to show</span></h2>
      <div id="exprs"><div class="empty">Nothing expressed yet.</div></div>
    </section>
  </div>

  <div class="threecol">
    <section class="panel">
      <h2><span class="tick">▸</span> drives</h2>
      <div id="drives"><div class="empty">No drives configured.</div></div>
    </section>

    <section class="panel">
      <h2><span class="tick">▸</span> lineage</h2>
      <div class="stream" id="lineage"></div>
    </section>

    <section class="panel">
      <h2><span class="tick">▸</span> ledger</h2>
      <div class="stream" id="ledger"></div>
    </section>
  </div>

  <section class="panel" style="margin-top:18px">
    <h2><span class="tick">▸</span> memory</h2>
    <form id="memform">
      <input id="meminput" autocomplete="off"
             placeholder="search episodic + semantic memory…">
      <button type="submit">Recall</button>
    </form>
    <div id="memresults"></div>
  </section>

  <footer>ANIMA · the agent is the artifact · every action above has a receipt</footer>
</div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const esc = s => { const d = document.createElement("span");
                   d.textContent = s == null ? "" : String(s);
                   return d.innerHTML; };
const fmtTs = t => { try { return new Date(t * 1000)
  .toISOString().replace("T", " ").slice(5, 19); } catch (e) { return ""; } };

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error("api " + path + " -> " + r.status);
  return r.json();
}

/* ── chat ── */
let chatEmpty = true;
function addMsg(who, cls, text) {
  if (chatEmpty) { $("chatlog").innerHTML = ""; chatEmpty = false; }
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.innerHTML = '<span class="who">' + esc(who) + "</span>" + esc(text);
  $("chatlog").appendChild(div);
  $("chatlog").scrollTop = $("chatlog").scrollHeight;
}
$("chatform").addEventListener("submit", async ev => {
  ev.preventDefault();
  const text = $("chatinput").value.trim();
  if (!text) return;
  $("chatinput").value = "";
  addMsg("you", "you", text);
  try {
    await api("/api/message", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }) });
  } catch (e) { addMsg("observatory", "ent", "⚠ send failed: " + e.message); }
});

async function pollReplies() {
  const doc = await api("/api/replies");
  for (const r of (doc.replies || [])) addMsg(doc.entity || "entity", "ent", r.text);
}

/* ── expressions ── */
let lastExprId = -1;
async function pollExpressions() {
  const doc = await api("/api/expressions?limit=20");
  const items = doc.expressions || [];
  if (!items.length) return;
  if (items[0].id === lastExprId) return;
  lastExprId = items[0].id;
  const box = $("exprs"); box.innerHTML = "";
  for (const x of items) {
    const card = document.createElement("div");
    card.className = "card";
    const body = document.createElement("div");
    body.className = "body";
    body.innerHTML = x.body;               /* sanitized server-side */
    const cap = document.createElement("div");
    cap.className = "cap";
    cap.innerHTML = '<span class="t">' + esc(x.title || x.kind) + "</span>" +
                    '<span>' + esc(fmtTs(x.ts)) + "</span>";
    card.appendChild(body); card.appendChild(cap);
    box.appendChild(card);
  }
}

/* ── drives ── */
async function pollDrives() {
  const doc = await api("/api/drives");
  const ds = doc.drives || [];
  if (!ds.length) return;
  const box = $("drives"); box.innerHTML = "";
  for (const d of ds) {
    const frac = Math.min(1, d.fraction || 0);
    const el = document.createElement("div");
    el.className = "drive";
    el.innerHTML =
      '<div class="row"><span class="nm">' + esc(d.name) + "</span>" +
      '<span class="val">' + (d.pressure || 0).toFixed(2) + " / " +
      (d.threshold || 0).toFixed(2) + (d.pending ? " · WAKE" : "") +
      "</span></div>" +
      '<div class="gauge"><div class="fill' + (frac >= 0.85 ? " hot" : "") +
      '"></div></div>' +
      '<div class="desc">' + esc(d.description || "") + "</div>";
    box.appendChild(el);
    requestAnimationFrame(() =>
      { el.querySelector(".fill").style.width = (frac * 100) + "%"; });
  }
}

/* ── lineage / ledger ── */
async function pollLineage() {
  const doc = await api("/api/lineage");
  $("lineage").innerHTML = (doc.lineage || []).slice(-40).reverse().map(l =>
    '<div class="ln"><span class="ts">' + esc(l.ts) + "</span> " +
    '<span class="k">' + esc(l.kind) + "</span> <b>" + esc(l.detail) +
    "</b></div>").join("");
}
async function pollLedger() {
  const doc = await api("/api/ledger?limit=50");
  $("ledger").innerHTML = (doc.actions || []).map(a =>
    '<div class="ln"><span class="ts">' + esc(fmtTs(a.ts)) + "</span> " +
    '<span class="' + (a.outcome === "ok" ? "k" : "e") + '">' +
    esc(a.kind) + "</span> <b>" + esc(a.detail) + "</b></div>").join("");
}

/* ── stats / lock ── */
async function pollStats() {
  const doc = await api("/api/stats");
  const m = (doc.memory || {});
  $("statpill").textContent =
    "episodes " + (m.episodes ?? "—") +
    " · beliefs " + ((m.beliefs || {}).active ?? "—") +
    " · wakes " + (doc.wakes_dispatched ?? "—") +
    " · ledger " + (doc.ledger_entries ?? "—");
  $("locktext").textContent = doc.lock || "live";
  document.title = (doc.name || "anima") + " — Observatory";
}

/* ── memory search ── */
$("memform").addEventListener("submit", async ev => {
  ev.preventDefault();
  const q = $("meminput").value.trim();
  if (!q) return;
  const box = $("memresults");
  box.innerHTML = '<div class="empty">searching…</div>';
  try {
    const doc = await api("/api/memory/search?q=" + encodeURIComponent(q));
    const hits = [];
    for (const b of (doc.beliefs || []))
      hits.push('<div class="hit"><span class="tag">belief</span>' +
        esc(b.statement) + '<div class="meta">confidence ' +
        (b.confidence || 0).toFixed(2) + " · " + esc(b.scope) + "</div></div>");
    for (const e of (doc.episodes || []))
      hits.push('<div class="hit"><span class="tag">episode</span>' +
        esc(e.summary) + '<div class="meta">' + esc(fmtTs(e.ts)) + " · " +
        esc(e.scope) + (e.owner_person_id ? " · " + esc(e.owner_person_id) : "") +
        "</div></div>");
    box.innerHTML = hits.length ? hits.join("")
      : '<div class="empty">nothing surfaced (walls hold).</div>';
  } catch (e) { box.innerHTML = '<div class="empty">search failed.</div>'; }
});

/* ── main loop ── */
async function tick() {
  const jobs = [pollReplies(), pollExpressions(), pollDrives(),
                pollLedger(), pollStats()];
  await Promise.allSettled(jobs);
}
pollLineage(); setInterval(pollLineage, 30000);
tick(); setInterval(tick, 3000);
</script>
</body>
</html>
"""

LOCK_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Observatory — locked</title>
<style>
body { background:#05070f; color:#6a7a9c; font-family:ui-monospace,Menlo,monospace;
       display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }
.box { text-align:center; border:1px solid rgba(120,160,255,.14);
       border-radius:12px; padding:40px 56px; }
.box h1 { color:#c8d4ee; font-size:16px; letter-spacing:.2em; font-weight:600; }
.dome { color:#ffb45e; }
</style></head>
<body><div class="box"><h1><span class="dome">◉</span> OBSERVATORY LOCKED</h1>
<p>append ?token=&lt;your token&gt; to the URL once;<br>a cookie will keep the dome open.</p>
</div></body></html>
"""


def render_page(entity_name: str) -> str:
    """Fill the page template. (No str.format — the CSS is full of
    braces; a plain marker replace is the honest tool here.)"""
    safe = (entity_name or "anima").replace("<", "").replace(">", "")
    return PAGE_TEMPLATE.replace("__NAME__", safe)
