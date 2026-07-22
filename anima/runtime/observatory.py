"""The Observatory page (Phase 6b) — the entity's face.

Single-file, single-page: HTML/CSS/JS live here as Python string
constants so packaging stays trivial (pure stdlib, no assets dir, no
external references of any kind — the stdlib-only discipline includes
assets). Served by anima.runtime.senses.web_sense.

Design language — per the project owner's direction, beauty is a
functional requirement here, equal in rank to the API endpoints:
an observatory at night crossed with bioluminescence. Deep near-black
blue-greens, a cold teal glow (starlight on water) and one warm amber
(the dome lamp). Things that are alive, move: drive gauges breathe at
a rate set by their pressure, new ledger lines and expression cards
arrive with a soft bloom, the lineage is an illuminated timeline of
births, sleeps and migrations. Nothing shouts; everything glows a
little. This is not an admin panel — it is a window into a living
thing, and it should feel like one.
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
  --abyss:  #030809;
  --deep:   #06121a;
  --pool:   #0a1c26;
  --panel:  rgba(9, 24, 32, 0.72);
  --line:   rgba(94, 234, 212, 0.10);
  --line2:  rgba(94, 234, 212, 0.22);
  --ink:    #c9dcd8; --ink-dim: #6d8a86; --ink-faint: #3c5450;
  --glow:   #5eead4;            /* cold: bioluminescent teal      */
  --glow2:  #7fd4ff;            /* colder: starlight blue         */
  --lamp:   #ffb45e;            /* warm: the dome lamp            */
  --err:    #ff8d9d;
  --serif:  Georgia, "Iowan Old Style", "Times New Roman", serif;
  --sans:   system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono:   ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body {
  font-family: var(--sans); color: var(--ink);
  background:
    radial-gradient(1200px 620px at 78% -12%, #0b2430 0%, transparent 60%),
    radial-gradient(1000px 700px at -12% 108%, #07202a 0%, transparent 55%),
    radial-gradient(700px 500px at 50% 120%, rgba(94,234,212,.05), transparent 60%),
    linear-gradient(180deg, var(--deep), var(--abyss) 62%);
  background-attachment: fixed;
}
/* drifting plankton-starfield, pure CSS */
body::before {
  content: ""; position: fixed; inset: -60px; pointer-events: none; z-index: 0;
  background-image:
    radial-gradient(1px 1px at 11% 21%, rgba(180,240,230,.8), transparent 55%),
    radial-gradient(1.5px 1.5px at 33% 9%, rgba(140,220,255,.6), transparent 55%),
    radial-gradient(1px 1px at 57% 17%, rgba(180,240,230,.5), transparent 55%),
    radial-gradient(1px 1px at 72% 33%, rgba(180,240,230,.35), transparent 55%),
    radial-gradient(1.5px 1.5px at 88% 11%, rgba(140,220,255,.55), transparent 55%),
    radial-gradient(1px 1px at 94% 44%, rgba(180,240,230,.3), transparent 55%),
    radial-gradient(1px 1px at 21% 57%, rgba(180,240,230,.22), transparent 55%),
    radial-gradient(1.5px 1.5px at 46% 74%, rgba(140,220,255,.2), transparent 55%),
    radial-gradient(1px 1px at 68% 88%, rgba(180,240,230,.25), transparent 55%),
    radial-gradient(1px 1px at 8% 84%,  rgba(180,240,230,.2), transparent 55%);
  animation: drift 120s linear infinite alternate;
}
@keyframes drift { from { transform: translate3d(0,0,0); }
                   to   { transform: translate3d(40px,-25px,0); } }

.wrap { max-width: 1220px; margin: 0 auto; padding: 22px 24px 70px;
        position: relative; z-index: 1; }

/* ── header ── */
header { display: flex; align-items: baseline; gap: 18px; flex-wrap: wrap;
  padding: 10px 2px 20px; margin-bottom: 22px; position: relative; }
header::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--line2) 20%,
              var(--line2) 60%, transparent); }
header h1 { font-family: var(--serif); font-size: 30px; font-weight: 400;
  margin: 0; letter-spacing: .06em; color: #e9f4f0;
  text-shadow: 0 0 26px rgba(94,234,212,.35); }
header h1 .dome { color: var(--lamp);
  text-shadow: 0 0 18px rgba(255,180,94,.6); animation: lamp 7s ease-in-out infinite; }
@keyframes lamp { 0%,100% { opacity: .85; } 50% { opacity: 1;
  text-shadow: 0 0 26px rgba(255,180,94,.8); } }
header .sub { font-family: var(--serif); font-style: italic; font-size: 13px;
  color: var(--ink-dim); letter-spacing: .12em; }
header .spacer { flex: 1; }
.pill { font-family: var(--mono); font-size: 11px; padding: 4px 12px;
  border: 1px solid var(--line); border-radius: 999px; color: var(--ink-dim);
  background: rgba(3,10,12,.4); }
.pill .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: var(--glow); box-shadow: 0 0 10px var(--glow); margin-right: 7px;
  vertical-align: 1px; animation: heartbeat 4s ease-in-out infinite; }
@keyframes heartbeat { 0%,100% { opacity:.6; } 50% { opacity:1; } }

/* ── panels ── */
.panel { background: var(--panel); border: 1px solid var(--line);
  border-radius: 14px; padding: 16px 18px; backdrop-filter: blur(7px);
  box-shadow: 0 14px 50px rgba(0,0,0,.4), inset 0 1px 0 rgba(255,255,255,.03); }
.panel h2 { margin: 0 0 12px; font-family: var(--serif); font-size: 13px;
  font-weight: 400; font-style: italic; letter-spacing: .3em;
  text-transform: lowercase; color: var(--ink-dim); }
.panel h2 .tick { color: var(--glow); font-style: normal;
  text-shadow: 0 0 10px rgba(94,234,212,.6); }
.panel h2 .note { float: right; font-size: 11px; letter-spacing: .04em;
  color: var(--ink-faint); }
.empty { color: var(--ink-faint); font-size: 13px; font-style: italic;
  font-family: var(--serif); padding: 14px 6px; }

/* ── main grid: expressions are the centerpiece ── */
.grid { display: grid; grid-template-columns: minmax(0, 8fr) minmax(0, 5fr);
  gap: 20px; align-items: start; }
@media (max-width: 940px) { .grid { grid-template-columns: 1fr; } }

/* ── the art wall ── */
#exprs { columns: 2 240px; column-gap: 14px; max-height: 620px;
  overflow-y: auto; padding: 2px; }
.card { break-inside: avoid; margin: 0 0 14px; border: 1px solid var(--line);
  border-radius: 12px; background:
    linear-gradient(180deg, rgba(6,18,24,.85), rgba(3,9,12,.9));
  overflow: hidden; opacity: 0; transform: translateY(14px) scale(.985);
  transition: opacity .9s cubic-bezier(.22,1,.36,1),
              transform .9s cubic-bezier(.22,1,.36,1),
              border-color .6s, box-shadow .6s; }
.card.shown { opacity: 1; transform: none; }
@keyframes surface { from { opacity: 0; transform: translateY(10px); }
                     to { opacity: 1; transform: none; } }
.card.fresh { border-color: rgba(94,234,212,.45);
  box-shadow: 0 0 30px rgba(94,234,212,.18), 0 0 8px rgba(94,234,212,.15); }
.card .body { padding: 14px; overflow: auto; max-height: 320px;
  font-size: 13.5px; line-height: 1.55; }
.card .body svg { max-width: 100%; height: auto; display: block; margin: 0 auto; }
.card .cap { display: flex; justify-content: space-between; gap: 8px;
  align-items: baseline; padding: 7px 13px;
  border-top: 1px solid rgba(94,234,212,.07);
  background: rgba(3,9,12,.5); }
.card .cap .t { font-family: var(--serif); font-style: italic; font-size: 12px;
  color: var(--lamp); }
.card .cap .when { font-family: var(--mono); font-size: 9.5px;
  color: var(--ink-faint); }

/* ── chat ── */
#chatlog { height: 470px; overflow-y: auto; display: flex;
  flex-direction: column; gap: 9px; padding: 4px 2px; scroll-behavior: smooth; }
.msg { max-width: 88%; padding: 9px 13px; border-radius: 12px; font-size: 14px;
  line-height: 1.5; white-space: pre-wrap; word-break: break-word;
  animation: surface .5s cubic-bezier(.22,1,.36,1) both; }
.msg.you { align-self: flex-end; background: rgba(127,212,255,.09);
  border: 1px solid rgba(127,212,255,.22); border-bottom-right-radius: 4px; }
.msg.ent { align-self: flex-start; background: rgba(255,180,94,.06);
  border: 1px solid rgba(255,180,94,.2); border-bottom-left-radius: 4px; }
.msg .who { display: block; font-family: var(--mono); font-size: 9.5px;
  color: var(--ink-dim); margin-bottom: 4px; letter-spacing: .14em;
  text-transform: uppercase; }
#chatform { display: flex; gap: 9px; margin-top: 12px; }
#chatinput, #meminput { flex: 1; background: rgba(3,9,12,.65);
  color: var(--ink); border: 1px solid var(--line); border-radius: 10px;
  padding: 11px 14px; font: 14px var(--sans); outline: none;
  transition: border-color .3s, box-shadow .3s; }
#chatinput:focus { border-color: rgba(127,212,255,.5);
  box-shadow: 0 0 0 3px rgba(127,212,255,.07), 0 0 18px rgba(127,212,255,.1); }
#meminput:focus { border-color: rgba(255,180,94,.45);
  box-shadow: 0 0 0 3px rgba(255,180,94,.06), 0 0 18px rgba(255,180,94,.08); }
button { background: linear-gradient(180deg, rgba(94,234,212,.14),
  rgba(94,234,212,.04)); color: var(--glow);
  border: 1px solid rgba(94,234,212,.35); border-radius: 10px;
  padding: 11px 20px; font: 600 13px var(--sans); cursor: pointer;
  letter-spacing: .06em; transition: box-shadow .3s; }
button:hover { box-shadow: 0 0 20px rgba(94,234,212,.3); }
button:disabled { opacity: .4; cursor: default; box-shadow: none; }

/* ── lower row ── */
.threecol { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1.1fr)
  minmax(0,1.3fr); gap: 20px; margin-top: 20px; }
@media (max-width: 940px) { .threecol { grid-template-columns: 1fr; } }

/* ── drives: breathing radial gauges ── */
#drives { display: flex; flex-wrap: wrap; gap: 14px; justify-content: center;
  padding: 6px 0; }
.drive { width: 118px; text-align: center; }
.drive .ring { position: relative; width: 96px; height: 96px; margin: 0 auto; }
.drive .ring svg { transform: rotate(-90deg); }
.drive .ring .track { fill: none; stroke: rgba(94,234,212,.08);
  stroke-width: 5; }
.drive .ring .fill { fill: none; stroke: var(--glow); stroke-width: 5;
  stroke-linecap: round; transition: stroke-dashoffset 1.4s
  cubic-bezier(.22,1,.36,1), stroke .8s; }
.drive .ring.hot .fill { stroke: var(--lamp); }
.drive .ring .core { position: absolute; inset: 22px; border-radius: 50%;
  background: radial-gradient(circle at 45% 40%, rgba(94,234,212,.28),
    rgba(94,234,212,.05) 65%, transparent 75%);
  animation: breathe var(--breath, 6s) ease-in-out infinite; }
.drive .ring.hot .core { background: radial-gradient(circle at 45% 40%,
  rgba(255,180,94,.34), rgba(255,180,94,.06) 65%, transparent 75%); }
@keyframes breathe { 0%,100% { transform: scale(.86); opacity: .55; }
                     50% { transform: scale(1.06); opacity: 1; } }
.drive .ring .num { position: absolute; inset: 0; display: flex;
  align-items: center; justify-content: center; font-family: var(--mono);
  font-size: 13px; color: var(--ink); text-shadow: 0 0 12px rgba(94,234,212,.5); }
.drive .nm { font-family: var(--serif); font-style: italic; font-size: 13px;
  color: var(--ink); margin-top: 7px; }
.drive .desc { font-size: 10.5px; color: var(--ink-faint); margin-top: 2px;
  line-height: 1.4; }
.drive .wake { font-family: var(--mono); font-size: 9px; color: var(--lamp);
  letter-spacing: .2em; animation: heartbeat 1.6s ease-in-out infinite; }

/* ── lineage: illuminated timeline ── */
#lineage { position: relative; max-height: 330px; overflow-y: auto;
  padding: 6px 4px 6px 26px; }
#lineage::before { content: ""; position: absolute; left: 11px; top: 0;
  bottom: 0; width: 1px;
  background: linear-gradient(180deg, transparent,
    rgba(94,234,212,.35) 12%, rgba(94,234,212,.35) 88%, transparent); }
.lin { position: relative; padding: 5px 0 5px 6px; font-size: 12px;
  line-height: 1.45; }
.lin .glyph { position: absolute; left: -22px; top: 5px; width: 15px;
  height: 15px; line-height: 15px; text-align: center; font-size: 11px;
  color: var(--glow); text-shadow: 0 0 9px rgba(94,234,212,.8);
  background: var(--abyss); border-radius: 50%; }
.lin.k-init .glyph, .lin.k-migration .glyph { color: var(--lamp);
  text-shadow: 0 0 10px rgba(255,180,94,.8); }
.lin.k-shell_stop .glyph { color: var(--ink-faint); text-shadow: none; }
.lin .d { color: var(--ink); font-family: var(--serif); }
.lin.k-shell_stop .d { color: var(--ink-dim); }
.lin .ts { display: block; font-family: var(--mono); font-size: 9px;
  color: var(--ink-faint); letter-spacing: .06em; }

/* ── ledger stream ── */
#ledger { font-family: var(--mono); font-size: 11px; line-height: 1.8;
  max-height: 330px; overflow-y: auto; color: var(--ink-dim); }
.ln { padding: 2px 4px; border-bottom: 1px dotted rgba(94,234,212,.05);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  border-radius: 4px; }
.ln b { color: var(--ink); font-weight: 500; }
.ln .k { color: var(--glow); } .ln .e { color: var(--err); }
.ln .ts { color: var(--ink-faint); }
.ln.fresh { animation: bloom 2.4s ease-out both; }
@keyframes bloom { 0% { background: rgba(94,234,212,.18);
  box-shadow: 0 0 14px rgba(94,234,212,.25); }
  100% { background: transparent; box-shadow: none; } }

/* ── memory ── */
#memform { display: flex; gap: 9px; margin-bottom: 10px; }
#memresults .hit { padding: 9px 12px; margin: 7px 0; border-radius: 10px;
  border: 1px solid var(--line); background: rgba(3,9,12,.5);
  font-size: 13px; line-height: 1.5;
  animation: surface .5s cubic-bezier(.22,1,.36,1) both; }
#memresults .hit .tag { font-family: var(--mono); font-size: 9px;
  color: var(--lamp); letter-spacing: .18em; text-transform: uppercase;
  margin-right: 9px; }
#memresults .hit .meta { font-family: var(--mono); font-size: 10px;
  color: var(--ink-faint); margin-top: 3px; }

footer { margin-top: 30px; text-align: center; font-family: var(--serif);
  font-style: italic; font-size: 11px; color: var(--ink-faint);
  letter-spacing: .18em; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: rgba(94,234,212,.14);
  border-radius: 4px; }
::-webkit-scrollbar-track { background: transparent; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01s !important;
    transition-duration: .01s !important; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="dome">◉</span> __NAME__</h1>
    <span class="sub">the observatory</span>
    <span class="spacer"></span>
    <span class="pill" id="lockpill"><span class="dot"></span><span id="locktext">…</span></span>
    <span class="pill" id="statpill">—</span>
  </header>

  <div class="grid">
    <section class="panel">
      <h2><span class="tick">✶</span> expressions
        <span class="note">what it chose to show</span></h2>
      <div id="exprs"><div class="empty">Nothing expressed yet — the wall
        waits for its first light.</div></div>
    </section>

    <section class="panel">
      <h2><span class="tick">✶</span> conversation</h2>
      <div id="chatlog"><div class="empty">Say something. The entity wakes
        when spoken to.</div></div>
      <form id="chatform">
        <input id="chatinput" autocomplete="off"
               placeholder="speak into the dome…">
        <button type="submit" id="sendbtn">Send</button>
      </form>
    </section>
  </div>

  <div class="threecol">
    <section class="panel">
      <h2><span class="tick">✶</span> drives</h2>
      <div id="drives"><div class="empty">No drives configured.</div></div>
    </section>

    <section class="panel">
      <h2><span class="tick">✶</span> lineage
        <span class="note">a biography</span></h2>
      <div id="lineage"></div>
    </section>

    <section class="panel">
      <h2><span class="tick">✶</span> ledger
        <span class="note">every action has a receipt</span></h2>
      <div id="ledger"></div>
    </section>
  </div>

  <section class="panel" style="margin-top:20px">
    <h2><span class="tick">✶</span> memory</h2>
    <form id="memform">
      <input id="meminput" autocomplete="off"
             placeholder="search episodic + semantic memory…">
      <button type="submit">Recall</button>
    </form>
    <div id="memresults"></div>
  </section>

  <footer>anima · the agent is the artifact · continuity is the product</footer>
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

/* ── the art wall ── */
const seenExprs = new Set();
let exprEmpty = true;
async function pollExpressions() {
  const doc = await api("/api/expressions?limit=20");
  const items = (doc.expressions || []).slice().reverse(); // oldest first
  for (const x of items) {
    if (seenExprs.has(x.id)) continue;
    seenExprs.add(x.id);
    if (exprEmpty) { $("exprs").innerHTML = ""; exprEmpty = false; }
    const card = document.createElement("div");
    card.className = "card";
    const body = document.createElement("div");
    body.className = "body";
    body.innerHTML = x.body;               /* sanitized server-side */
    const cap = document.createElement("div");
    cap.className = "cap";
    cap.innerHTML = '<span class="t">' + esc(x.title || x.kind) + "</span>" +
                    '<span class="when">' + esc(fmtTs(x.ts)) + "</span>";
    card.appendChild(body); card.appendChild(cap);
    $("exprs").prepend(card);              /* newest surfaces on top */
    requestAnimationFrame(() => card.classList.add("shown"));
    if (!firstPaint) {                     /* bloom only for live arrivals */
      card.classList.add("fresh");
      setTimeout(() => card.classList.remove("fresh"), 6000);
    }
  }
}

/* ── drives: breathing rings ── */
const R = 40, CIRC = 2 * Math.PI * R;
async function pollDrives() {
  const doc = await api("/api/drives");
  const ds = doc.drives || [];
  if (!ds.length) return;
  const box = $("drives"); box.innerHTML = "";
  for (const d of ds) {
    const frac = Math.max(0, Math.min(1, d.fraction || 0));
    const hot = frac >= 0.85 || d.pending;
    /* breath rate: calm 8s at zero pressure → urgent 2.2s at full */
    const breath = (8 - 5.8 * frac).toFixed(2) + "s";
    const el = document.createElement("div");
    el.className = "drive";
    el.innerHTML =
      '<div class="ring' + (hot ? " hot" : "") + '">' +
        '<svg width="96" height="96" viewBox="0 0 96 96">' +
          '<circle class="track" cx="48" cy="48" r="' + R + '"/>' +
          '<circle class="fill" cx="48" cy="48" r="' + R + '" ' +
            'stroke-dasharray="' + CIRC.toFixed(1) + '" ' +
            'stroke-dashoffset="' + CIRC.toFixed(1) + '"/>' +
        "</svg>" +
        '<div class="core" style="--breath:' + breath + '"></div>' +
        '<div class="num">' + (d.pressure || 0).toFixed(1) + "</div>" +
      "</div>" +
      '<div class="nm">' + esc(d.name) + "</div>" +
      (d.pending ? '<div class="wake">WAKE PENDING</div>' : "") +
      '<div class="desc">' + esc(d.description || "") + "</div>";
    box.appendChild(el);
    requestAnimationFrame(() => {
      el.querySelector(".fill").style.strokeDashoffset =
        (CIRC * (1 - frac)).toFixed(1);
    });
  }
}

/* ── lineage: illuminated timeline ── */
const GLYPHS = { init: "✶", migration: "⇌", runtime_change: "↻",
                 shell_start: "◉", shell_stop: "◌" };
async function pollLineage() {
  const doc = await api("/api/lineage");
  $("lineage").innerHTML = (doc.lineage || []).slice(-60).reverse().map(l =>
    '<div class="lin k-' + esc(l.kind) + '">' +
      '<span class="glyph">' + (GLYPHS[l.kind] || "·") + "</span>" +
      '<span class="d">' + esc(l.detail) + "</span>" +
      '<span class="ts">' + esc(l.ts) + " · " + esc(l.kind) + "</span>" +
    "</div>").join("");
}

/* ── ledger stream ── */
let maxLedgerId = -1;
async function pollLedger() {
  const doc = await api("/api/ledger?limit=50");
  const rows = doc.actions || [];
  const newest = rows.length ? rows[0].id : -1;
  $("ledger").innerHTML = rows.map(a =>
    '<div class="ln' +
    (!firstPaint && a.id > maxLedgerId ? " fresh" : "") + '">' +
    '<span class="ts">' + esc(fmtTs(a.ts)) + "</span> " +
    '<span class="' + (a.outcome === "ok" ? "k" : "e") + '">' +
    esc(a.kind) + "</span> <b>" + esc(a.detail) + "</b></div>").join("");
  maxLedgerId = Math.max(maxLedgerId, newest);
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
      : '<div class="empty">nothing surfaced (the walls hold).</div>';
  } catch (e) { box.innerHTML = '<div class="empty">search failed.</div>'; }
});

/* ── main loop ── */
let firstPaint = true;
async function tick() {
  await Promise.allSettled([pollReplies(), pollExpressions(), pollDrives(),
                            pollLedger(), pollStats()]);
  firstPaint = false;
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
body { background: radial-gradient(700px 400px at 50% 30%, #07202a,
       #030809 70%); color:#6d8a86;
       font-family: Georgia, serif; display:flex; align-items:center;
       justify-content:center; height:100vh; margin:0; }
.box { text-align:center; border:1px solid rgba(94,234,212,.14);
       border-radius:14px; padding:44px 60px;
       box-shadow: 0 0 60px rgba(94,234,212,.06); }
.box h1 { color:#c9dcd8; font-size:17px; letter-spacing:.28em;
          font-weight:400; }
.box p { font-style: italic; font-size: 13px; line-height: 1.7; }
.dome { color:#ffb45e; text-shadow: 0 0 16px rgba(255,180,94,.6); }
</style></head>
<body><div class="box"><h1><span class="dome">◉</span> THE DOME IS CLOSED</h1>
<p>append ?token=&lt;your token&gt; to the URL once;<br>a cookie will keep
the dome open.</p>
</div></body></html>
"""


def render_page(entity_name: str) -> str:
    """Fill the page template. (No str.format — the CSS is full of
    braces; a plain marker replace is the honest tool here.)"""
    safe = (entity_name or "anima").replace("<", "").replace(">", "")
    return PAGE_TEMPLATE.replace("__NAME__", safe)
