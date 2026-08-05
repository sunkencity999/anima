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

v3 (conversation-primary): the dialogue with the entity is the
centerpiece; instruments (drives, lineage, expressions, ledger, memory)
arrange around it as the supporting observatory. While the entity
composes, the memories it recalled surface as faint marginalia beside
the conversation. A time-travel scrub replays the ledger, the drive
gauges and the ambient mood as they were at any past moment (windowed
/api/history fetches — the past is read on demand, never shipped
wholesale). The whole page collapses gracefully to a phone: checking
on your entity from bed is a first-class use case.
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
html { background: var(--abyss); }  /* base under the fixed gradients —
  keeps overscroll/long pages dark on phones where background-attachment:
  fixed degrades */
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
body::before { animation-duration: var(--drift-s, 120s); }
/* ambient mood veil — see the "mood" comment in the script: hue and
   opacity are set from live drive pressures, nothing decorative */
body::after {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(1000px 640px at 50% -8%,
      hsla(var(--mood-h, 195), 75%, 60%, var(--mood-a, 0.035)),
      transparent 65%),
    radial-gradient(800px 520px at 82% 108%,
      hsla(var(--mood-h, 195), 65%, 50%, calc(var(--mood-a, 0.035) * .6)),
      transparent 60%);
}

.wrap { max-width: 1380px; margin: 0 auto; padding: 22px 24px 70px;
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

/* ── main grid: the CONVERSATION is the centerpiece (v3) ──
   Instruments arrange around the dialogue: drives + lineage on the
   left rail, expressions + ledger + memory on the right. DOM order
   puts the conversation first, so on a phone the stack begins with
   the dialogue — the layout collapses toward its own priorities. */
.obsgrid { display: grid; gap: 20px; align-items: start;
  grid-template-columns: minmax(240px, 3fr) minmax(0, 6fr)
                         minmax(260px, 4fr);
  grid-template-areas: "left centre right"; }
.centre { grid-area: centre; min-width: 0; }
.rail-l { grid-area: left;  min-width: 0; }
.rail-r { grid-area: right; min-width: 0; }
.rail-l .panel, .rail-r .panel { margin-bottom: 20px; }

/* ── the art wall ── */
#exprs { columns: 2 190px; column-gap: 14px; max-height: 460px;
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

/* ── tone expressions (v3b): sound as data ──
   The piano-roll is the instrument view of the entity's phrase; the
   play button lights the bars amber as the notes actually sound.
   Nothing here is a decoration — every bar is a validated note. */
.tonewrap { padding: 2px 0 4px; }
.tonewrap svg { display: block; width: 100%; height: auto; }
.tonebar { fill: var(--glow); opacity: .75;
  transition: fill .15s, opacity .15s; }
.tonebar.on { fill: var(--lamp); opacity: 1;
  filter: drop-shadow(0 0 5px rgba(255,180,94,.9)); }
.tonefoot { display: flex; align-items: center; gap: 10px;
  margin-top: 8px; }
.toneplay { width: 34px; height: 34px; min-height: 34px; padding: 0;
  border-radius: 50%; font-size: 13px; line-height: 1; flex: none; }
.tonemeta { font-family: var(--mono); font-size: 9.5px;
  color: var(--ink-faint); letter-spacing: .08em; }

/* ── conversation: the centerpiece ──
   .convo grows a marginalia column (recalled memories) when the
   entity is composing; otherwise the dialogue takes the full width. */
.convo { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; }
.convo.noted { grid-template-columns: 172px minmax(0, 1fr); }
#chatlog { height: clamp(380px, 58vh, 680px); overflow-y: auto;
  display: flex;
  flex-direction: column; gap: 9px; padding: 4px 2px; scroll-behavior: smooth; }

/* ── marginalia: what memory surfaced while it was composing ──
   Faint by design — these are the entity's half-thoughts, not content.
   They brighten while composing and settle (dim further) once the
   reply lands. Fed by the ACL-walled recall payload of /api/message. */
.marginalia { display: none; }
.marginalia.has { display: block; border-right: 1px solid var(--line);
  padding-right: 12px; max-height: clamp(380px, 58vh, 680px);
  overflow-y: auto; }
.marginalia .mhead { font-family: var(--mono); font-size: 8.5px;
  letter-spacing: .24em; text-transform: uppercase;
  color: var(--ink-faint); margin: 2px 0 10px; }
.mnote { font-family: var(--serif); font-style: italic; font-size: 11.5px;
  line-height: 1.55; color: var(--ink-dim); margin: 0 0 12px;
  opacity: 0; transition: opacity 1.4s ease; }
.mnote.shown { opacity: .78; }
.mnote.settled { opacity: .32; }
.mnote .mtag { display: block; font-family: var(--mono);
  font-style: normal; font-size: 8.5px; letter-spacing: .2em;
  text-transform: uppercase; color: var(--glow); opacity: .85;
  margin-bottom: 2px; }
.mnote.belief .mtag { color: var(--lamp); }

/* ── composing indicator ── */
.msg.thinking .dots i { display: inline-block; font-style: normal;
  color: var(--lamp); font-size: 17px; line-height: .6;
  animation: thinkp 1.4s ease-in-out infinite; }
.msg.thinking .dots i:nth-child(2) { animation-delay: .22s; }
.msg.thinking .dots i:nth-child(3) { animation-delay: .44s; }
@keyframes thinkp { 0%,100% { opacity: .2; transform: translateY(0); }
  50% { opacity: 1; transform: translateY(-2px); } }
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

/* ── lineage: constellation + illuminated timeline ── */
#constellation { display: block; width: 100%; height: 120px;
  margin-bottom: 10px; cursor: crosshair; border-radius: 10px;
  background: radial-gradient(500px 160px at 50% 110%,
    rgba(94,234,212,.05), transparent 70%); }
.lin.lit { background: rgba(94,234,212,.08); border-radius: 6px; }
.lin.lit .d { color: #e9f4f0; text-shadow: 0 0 12px rgba(94,234,212,.7); }
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

/* ── presence ──
   #iris: the dome opens on arrival — a JS-lerped radial aperture.
   body.adrift: the live stream is down; the room dims gently until
   the connection (or polling) restores the light. */
#iris { position: fixed; inset: 0; z-index: 60; pointer-events: none;
  background: radial-gradient(circle at 50% 42%,
    transparent var(--iris, 0%),
    var(--abyss) calc(var(--iris, 0%) + 16%)); }
body.adrift .wrap { filter: brightness(.7) saturate(.75);
  transition: filter 2.5s ease; }
body:not(.adrift) .wrap { transition: filter 1.2s ease; }
body.adrift .pill .dot { background: var(--ink-faint); box-shadow: none;
  animation: none; }

/* ── time travel ──
   The scrub is an instrument: your position in the entity's biography.
   The thumb is the dome lamp — warm at “now”; drag it back and the
   whole room goes cold and desaturated (the lamp belongs to the
   present). Hidden until the ledger has any history at all. */
.timebar { display: flex; align-items: center; gap: 14px;
  margin-bottom: 20px; padding: 10px 18px; }
.timebar[hidden] { display: none; }
.timebar .tword { font-family: var(--serif); font-style: italic;
  font-size: 12px; color: var(--ink-dim); letter-spacing: .14em;
  white-space: nowrap; }
#tlab { font-family: var(--mono); font-size: 11px; color: var(--ink);
  min-width: 110px; text-align: right; white-space: nowrap; }
#nowbtn { padding: 7px 14px; font-size: 12px; white-space: nowrap; }
#scrub { -webkit-appearance: none; appearance: none; flex: 1;
  min-width: 0; height: 28px; background: transparent; cursor: pointer;
  margin: 0; }
#scrub::-webkit-slider-runnable-track { height: 3px; border-radius: 2px;
  background: linear-gradient(90deg, rgba(94,234,212,.08),
              rgba(94,234,212,.4)); }
#scrub::-webkit-slider-thumb { -webkit-appearance: none; width: 18px;
  height: 18px; margin-top: -7.5px; border-radius: 50%; border: none;
  background: var(--lamp); box-shadow: 0 0 14px rgba(255,180,94,.75); }
#scrub::-moz-range-track { height: 3px; border-radius: 2px;
  background: linear-gradient(90deg, rgba(94,234,212,.08),
              rgba(94,234,212,.4)); }
#scrub::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%;
  border: none; background: var(--lamp);
  box-shadow: 0 0 14px rgba(255,180,94,.75); }
body.past #scrub::-webkit-slider-thumb { background: var(--glow2);
  box-shadow: 0 0 14px rgba(127,212,255,.75); }
body.past #scrub::-moz-range-thumb { background: var(--glow2);
  box-shadow: 0 0 14px rgba(127,212,255,.75); }
.pill.warm { color: var(--lamp); border-color: rgba(255,180,94,.4);
  animation: lamp 3s ease-in-out infinite; }

/* viewing the past: the lamp goes cold, the room desaturates — the
   present is the only time that gets the warm light */
body.past .wrap { filter: saturate(.72) brightness(.86); }
body.past header h1 .dome { color: var(--glow2); animation: none;
  text-shadow: 0 0 18px rgba(127,212,255,.6); }
body.past .ln.fresh { animation: none; }
body.past #chatform button, body.past #chatinput { opacity: .85; }
.drive.ghost { opacity: .35; }   /* pressure unknown for that moment */

/* ── foldable panels (mobile: collapse the instruments, keep the
   conversation) — headers become 44px+ touch targets ── */
[data-fold] > h2 { cursor: pointer; -webkit-user-select: none;
  user-select: none; }
[data-fold] > h2::before { content: "⌄"; float: right; font-style: normal;
  color: var(--ink-faint); margin-left: 8px;
  transition: transform .3s ease; }
.panel.folded > h2::before { transform: rotate(-90deg); }
.panel.folded > h2 { margin-bottom: 0; }
.panel.folded > :not(h2) { display: none !important; }  /* !important:
  outranks id-specificity display rules (#constellation, #memform) */

/* ── responsive: the observatory folds toward the dialogue ── */
@media (max-width: 1240px) {
  .obsgrid { grid-template-columns: minmax(0,1fr) minmax(0,1fr);
    grid-template-areas: "centre centre" "left right"; }
}
@media (max-width: 980px) {
  .obsgrid { grid-template-columns: minmax(0,1fr);
    grid-template-areas: "centre" "left" "right"; }
  #chatlog { height: min(52dvh, 460px); }
  .marginalia.has { border-right: none;
    border-bottom: 1px solid var(--line); padding: 0 0 8px;
    max-height: none; display: flex; gap: 12px; overflow-x: auto;
    overflow-y: hidden; }
  .convo.noted { grid-template-columns: minmax(0,1fr); }
  .marginalia .mhead { display: none; }
  .mnote { flex: 0 0 200px; margin: 0; font-size: 11px; }
}
@media (max-width: 700px) {
  .wrap { padding: 12px 12px 44px; }
  header { gap: 10px; padding-bottom: 14px; margin-bottom: 14px; }
  header h1 { font-size: 22px; }
  header .sub { display: none; }
  .pill { font-size: 10px; padding: 4px 10px; }
  .panel { padding: 12px 14px; border-radius: 12px;
    backdrop-filter: none; }         /* cheap on phone GPUs */
  .obsgrid { gap: 14px; }
  .rail-l .panel, .rail-r .panel { margin-bottom: 14px; }
  #chatlog { height: min(48dvh, 420px); }
  .msg { max-width: 94%; font-size: 15px; }
  #chatinput, #meminput { font-size: 16px; min-height: 44px; }
  button { min-height: 44px; padding: 10px 18px; }
  #exprs { columns: 1; max-height: 380px; }
  .drive { width: 104px; }
  .timebar { padding: 8px 12px; gap: 10px; flex-wrap: wrap; }
  .timebar .tword { display: none; }
  #tlab { min-width: 0; }
  #scrub { flex: 1 1 100%; order: -1; }
  #scrub::-webkit-slider-thumb { width: 24px; height: 24px;
    margin-top: -10.5px; }
  #scrub::-moz-range-thumb { width: 24px; height: 24px; }
  #lineage, #ledger { max-height: 240px; }
}

/* ── under the hood: the machinery beneath (a footnote, not a hijack) ── */
.hood { margin-top: 20px; }
.hood > h2 .hoodlamp { color: var(--lamp); font-style: normal;
  text-shadow: 0 0 14px rgba(255,180,94,.7);
  animation: lamp 7s ease-in-out infinite; }
.hoodgrid { display: grid; gap: 20px; align-items: start;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); }
.hoodhead { font-family: var(--serif); font-style: italic; font-size: 12px;
  letter-spacing: .22em; color: var(--ink-dim); margin: 2px 0 10px; }
.swapdot { display: inline-block; width: 8px; height: 8px;
  border-radius: 50%; background: var(--glow);
  box-shadow: 0 0 8px var(--glow); vertical-align: 0; margin-right: 5px; }
.swapdot.open { background: var(--lamp); box-shadow: 0 0 12px var(--lamp);
  animation: heartbeat 1.4s ease-in-out infinite; }
.tierrow { display: flex; align-items: baseline; gap: 10px;
  margin: 12px 0 8px; }
.tierrow:first-child { margin-top: 2px; }
.tierrow .tn { font-family: var(--mono); font-size: 10px;
  letter-spacing: .24em; text-transform: uppercase; color: var(--glow);
  text-shadow: 0 0 8px rgba(94,234,212,.4); }
button.mini { padding: 3px 12px; font-size: 10px; min-height: 0;
  border-radius: 999px; }
.cand { position: relative; border: 1px solid var(--line);
  border-radius: 10px; background: rgba(3,9,12,.5);
  padding: 8px 12px 8px 36px; margin: 0 0 8px; }
.cand .ord { position: absolute; left: 10px; top: 10px; width: 16px;
  height: 16px; line-height: 16px; text-align: center;
  font-family: var(--mono); font-size: 9px; color: var(--ink-faint);
  border: 1px solid var(--line2); border-radius: 50%; }
.cand .pm { font-family: var(--serif); font-size: 13px;
  color: var(--ink); }
.cand .bu { font-family: var(--mono); font-size: 10px;
  color: var(--ink-faint); margin-top: 2px; word-break: break-all; }
.sdot { display: inline-block; width: 7px; height: 7px;
  border-radius: 50%; margin-right: 6px; vertical-align: 1px;
  flex: none; background: var(--ink-faint); }
.sdot.ok { background: var(--glow); box-shadow: 0 0 9px var(--glow);
  animation: heartbeat 4s ease-in-out infinite; }
.sdot.warn { background: var(--lamp);
  box-shadow: 0 0 8px rgba(255,180,94,.6); }
.sdot.bad { background: var(--err); opacity: .6; box-shadow: none; }
.jsonedit { width: 100%; min-height: 190px; resize: vertical;
  background: rgba(3,9,12,.78); color: var(--ink);
  border: 1px solid rgba(94,234,212,.3); border-radius: 10px;
  padding: 10px 12px; font: 11px/1.55 var(--mono); outline: none; }
.jsonedit:focus { border-color: rgba(94,234,212,.55);
  box-shadow: 0 0 0 3px rgba(94,234,212,.06); }
.editrow { display: flex; gap: 8px; margin: 8px 0 12px; }
.editrow button { padding: 6px 16px; font-size: 11px; min-height: 0; }
.ten { display: flex; align-items: center; gap: 8px;
  font-family: var(--mono); font-size: 11px; padding: 4px 2px;
  border-bottom: 1px dotted rgba(94,234,212,.05); }
.ten .tn2 { color: var(--ink); flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.ten .tst { color: var(--ink-faint); }
.rep { font-family: var(--mono); font-size: 10px; color: var(--ink-faint); }
.drift { font-family: var(--mono); font-size: 10px; color: var(--warm, #d9a05b); }
.proxystats { font-family: var(--mono); font-size: 10px;
  color: var(--ink-faint); margin-top: 8px; line-height: 1.7; }
#hoodledger { font-family: var(--mono); font-size: 10.5px;
  line-height: 1.75; color: var(--ink-dim); max-height: 200px;
  overflow-y: auto; }
#toast { position: fixed; left: 50%; bottom: 26px; z-index: 80;
  transform: translateX(-50%) translateY(20px); opacity: 0;
  pointer-events: none; background: var(--panel);
  border: 1px solid var(--line2); border-radius: 999px;
  padding: 9px 20px; font: 12px var(--mono); color: var(--ink);
  backdrop-filter: blur(7px); box-shadow: 0 14px 40px rgba(0,0,0,.5);
  transition: opacity .4s, transform .4s; }
#toast.shown { opacity: 1; transform: translateX(-50%); }
#toast.err { border-color: rgba(255,141,157,.5); color: var(--err); }
@media (max-width: 980px) {
  .hoodgrid { grid-template-columns: minmax(0, 1fr); } }

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
<div id="iris"></div>
<div class="wrap">
  <header>
    <h1><span class="dome">◉</span> __NAME__</h1>
    <span class="sub">the observatory</span>
    <span class="spacer"></span>
    <span class="pill" id="lockpill"><span class="dot"></span><span id="locktext">…</span></span>
    <span class="pill" id="statpill">—</span>
  </header>

  <!-- time travel: your position in the entity's biography -->
  <div class="timebar panel" id="timebar" hidden>
    <span class="tword">time</span>
    <input type="range" id="scrub" min="0" max="1000" value="1000"
           step="1" aria-label="time travel: scrub through history">
    <span id="tlab">now</span>
    <span class="pill warm" id="pastpill" hidden>◷ viewing the past</span>
    <button type="button" id="nowbtn" hidden>return to now</button>
  </div>

  <div class="obsgrid">
    <!-- centerpiece: the dialogue (first in DOM → first on a phone) -->
    <main class="centre">
      <section class="panel">
        <h2><span class="tick">✶</span> conversation
          <span class="note">the dialogue is the centerpiece</span></h2>
        <div class="convo" id="convo">
          <aside id="marginalia" class="marginalia"
                 aria-label="memories recalled while composing"></aside>
          <div class="convo-main">
            <div id="chatlog"><div class="empty">Say something. The entity
              wakes when spoken to.</div></div>
            <form id="chatform">
              <input id="chatinput" autocomplete="off"
                     placeholder="speak into the dome…">
              <button type="submit" id="sendbtn">Send</button>
            </form>
          </div>
        </div>
      </section>
    </main>

    <!-- left rail: the body's instruments -->
    <aside class="rail-l">
      <section class="panel" data-fold="drives">
        <h2><span class="tick">✶</span> drives</h2>
        <div id="drives"><div class="empty">No drives configured.</div></div>
      </section>

      <section class="panel" data-fold="lineage">
        <h2><span class="tick">✶</span> lineage
          <span class="note">a biography</span></h2>
        <canvas id="constellation" height="120"></canvas>
        <div id="lineage"></div>
      </section>
    </aside>

    <!-- right rail: what it made, what it did, what it knows -->
    <aside class="rail-r">
      <section class="panel" data-fold="exprs">
        <h2><span class="tick">✶</span> expressions
          <span class="note">what it chose to show</span></h2>
        <div id="exprs"><div class="empty">Nothing expressed yet — the wall
          waits for its first light.</div></div>
      </section>

      <section class="panel" data-fold="ledger">
        <h2><span class="tick">✶</span> ledger
          <span class="note">every action has a receipt</span></h2>
        <div id="ledger"></div>
      </section>

      <section class="panel" data-fold="memory">
        <h2><span class="tick">✶</span> memory</h2>
        <form id="memform">
          <input id="meminput" autocomplete="off"
                 placeholder="search episodic + semantic memory…">
          <button type="submit">Recall</button>
        </form>
        <div id="memresults"></div>
      </section>
    </aside>
  </div>

  <!-- under the hood: a discovered footnote at the floor of the dome -->
  <section class="panel hood" data-fold="hood">
    <h2><span class="hoodlamp">●</span> under the hood
      <span class="note">routing, tenants, and the machinery beneath ·
        <span class="swapdot" id="swapdot"></span><span id="swaplab">…</span></span></h2>
    <div class="hoodgrid">
      <div>
        <div class="hoodhead">routing · who answers, in what order</div>
        <div id="routing"><div class="empty">reading routing.json…</div></div>
      </div>
      <div>
        <div class="hoodhead" id="tenanthead">tenants · the machinery this body leans on</div>
        <div id="tenants"><div class="empty">listening at the doors…</div></div>
        <div class="hoodhead" id="modelhead" style="margin-top:16px">models · what each endpoint says it is</div>
        <div id="hoodmodels"><div class="empty">asking…</div></div>
        <div class="hoodhead" style="margin-top:16px">ledger tail · the last eight receipts</div>
        <div id="hoodledger"><div class="empty">quiet.</div></div>
      </div>
    </div>
  </section>

  <footer>anima · the agent is the artifact · continuity is the product</footer>
</div>

<script>
"use strict";
const ENT = "__NAME__";
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
/* composing indicator: a breathing amber ellipsis while the entity
   is somewhere between hearing and answering */
function addThinking() {
  removeThinking();
  if (chatEmpty) { $("chatlog").innerHTML = ""; chatEmpty = false; }
  const div = document.createElement("div");
  div.className = "msg ent thinking"; div.id = "thinkmsg";
  div.innerHTML = '<span class="who">' + esc(ENT) + "</span>" +
    '<span class="dots"><i>●</i> <i>●</i> <i>●</i></span>';
  $("chatlog").appendChild(div);
  $("chatlog").scrollTop = $("chatlog").scrollHeight;
}
function removeThinking() {
  const el = $("thinkmsg");
  if (el) el.remove();
}

$("chatform").addEventListener("submit", async ev => {
  ev.preventDefault();
  const text = $("chatinput").value.trim();
  if (!text) return;
  $("chatinput").value = "";
  addMsg("you", "you", text);
  addThinking();
  try {
    const res = await api("/api/message", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }) });
    renderMarginalia(res.recall);
  } catch (e) {
    removeThinking();
    addMsg("observatory", "ent", "⚠ send failed: " + e.message);
  }
});

function renderReplies(doc) {
  const rs = doc.replies || [];
  if (rs.length) { removeThinking(); settleMarginalia(); }
  for (const r of rs) addMsg(doc.entity || "entity", "ent", r.text);
}
async function pollReplies() { renderReplies(await api("/api/replies")); }

/* ── marginalia: memories recalled while composing ──
   The POST /api/message response carries the ACL-walled episode and
   belief snippets the orient phase surfaces for that wake — the
   entity's half-thoughts, rendered faint beside the dialogue. They
   settle (dim) once the reply arrives. */
function renderMarginalia(rec) {
  const box = $("marginalia");
  const items = [];
  for (const b of ((rec || {}).beliefs || []))
    items.push({ tag: "belief", text: b.statement, cls: "belief" });
  for (const e of ((rec || {}).episodes || []))
    items.push({ tag: fmtTs(e.ts) || "episode", text: e.summary,
                 cls: "episode" });
  if (!items.length) return;               /* keep the last set visible */
  box.innerHTML = '<div class="mhead">recalled while composing</div>';
  box.classList.add("has");
  $("convo").classList.add("noted");
  for (const it of items.slice(0, 8)) {
    const d = document.createElement("div");
    d.className = "mnote " + it.cls;
    d.innerHTML = '<span class="mtag">' + esc(it.tag) + "</span>" +
                  esc(it.text);
    box.appendChild(d);
    requestAnimationFrame(() => d.classList.add("shown"));
  }
}
function settleMarginalia() {
  for (const el of document.querySelectorAll(".mnote"))
    el.classList.add("settled");
}

/* ── the art wall ── */
const seenExprs = new Set();
let exprEmpty = true;
function renderExpressions(doc, initial) {
  const items = (doc.expressions || []).slice().reverse(); // oldest first
  for (const x of items) {
    if (seenExprs.has(x.id)) continue;
    seenExprs.add(x.id);
    if (exprEmpty) { $("exprs").innerHTML = ""; exprEmpty = false; }
    const card = document.createElement("div");
    card.className = "card";
    const body = document.createElement("div");
    body.className = "body";
    if (x.kind === "tone") {
      const t = buildToneBody(x.body);     /* validated server-side;
                                              rebuilt as DOM here */
      if (t) body.appendChild(t);
      else body.innerHTML = '<span class="empty">(a tone that did not '
                          + 'survive validation)</span>';
    } else {
      body.innerHTML = x.body;             /* sanitized server-side */
    }
    const cap = document.createElement("div");
    cap.className = "cap";
    cap.innerHTML = '<span class="t">' + esc(x.title || x.kind) + "</span>" +
                    '<span class="when">' + esc(fmtTs(x.ts)) + "</span>";
    card.appendChild(body); card.appendChild(cap);
    $("exprs").prepend(card);              /* newest surfaces on top */
    requestAnimationFrame(() => card.classList.add("shown"));
    if (!initial) {                        /* bloom only for live arrivals */
      card.classList.add("fresh");
      setTimeout(() => card.classList.remove("fresh"), 6000);
    }
  }
}
async function pollExpressions() {
  renderExpressions(await api("/api/expressions?limit=20"), firstPaint);
}

/* ── tone rendering + playback (v3b) ──
   The body is canonical validated JSON ({tempo, wave, notes:[{pitch,
   dur, vel}]}) — sound as data. The card shows a piano-roll built
   entirely from those numbers (DOM-built, nothing injected) and a
   play button that synthesizes the phrase with WebAudio, lighting
   each bar as its note sounds. */
const midiHz = m => 440 * Math.pow(2, (m - 69) / 12);
let audioCtx = null;
function buildToneBody(bodyStr) {
  let doc;
  try { doc = JSON.parse(bodyStr); } catch (e) { return null; }
  if (!doc || doc.medium !== "tone" ||
      !Array.isArray(doc.notes) || !doc.notes.length) return null;
  const W = 260, H = 88, pad = 6;
  const total = doc.notes.reduce((s, n) => s + (n.dur || 0), 0) || 1;
  const ps = doc.notes.filter(n => n.pitch != null).map(n => n.pitch);
  const lo = ps.length ? Math.min(...ps) - 2 : 57;
  const hi = ps.length ? Math.max(...ps) + 2 : 81;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  let x = pad, bars = [];
  for (const n of doc.notes) {
    const w = Math.max(2, (n.dur / total) * (W - 2 * pad) - 1.5);
    if (n.pitch != null) {
      const y = pad + (1 - (n.pitch - lo) / (hi - lo)) * (H - 2 * pad - 6);
      const r = document.createElementNS(NS, "rect");
      r.setAttribute("x", x.toFixed(1));
      r.setAttribute("y", y.toFixed(1));
      r.setAttribute("width", w.toFixed(1));
      r.setAttribute("height", "6"); r.setAttribute("rx", "3");
      r.setAttribute("class", "tonebar");
      r.style.opacity = (0.35 + 0.55 * (n.vel == null ? 0.7 : n.vel))
                        .toFixed(2);
      svg.appendChild(r); bars.push(r);
    } else bars.push(null);                /* rest: silence has no bar */
    x += (n.dur / total) * (W - 2 * pad);
  }
  const wrap = document.createElement("div");
  wrap.className = "tonewrap";
  wrap.appendChild(svg);
  const secs = total * 60 / doc.tempo;
  const foot = document.createElement("div");
  foot.className = "tonefoot";
  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "toneplay";
  btn.textContent = "\u25B6";
  btn.setAttribute("aria-label", "play tone");
  const meta = document.createElement("span");
  meta.className = "tonemeta";
  meta.textContent = doc.wave + " \u00B7 " + doc.tempo + " bpm \u00B7 "
    + doc.notes.length + " notes \u00B7 " + secs.toFixed(1) + "s";
  foot.appendChild(btn); foot.appendChild(meta);
  wrap.appendChild(foot);
  btn.addEventListener("click", () => playTone(doc, bars, btn));
  return wrap;
}
function playTone(doc, bars, btn) {
  if (btn.disabled) return;
  try {
    audioCtx = audioCtx ||
      new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch (e) { return; }
  btn.disabled = true; btn.textContent = "\u25CF";
  const spb = 60 / doc.tempo;
  let t = audioCtx.currentTime + 0.06;
  const t0 = t;
  doc.notes.forEach((n, i) => {
    const d = n.dur * spb;
    if (n.pitch != null) {
      const o = audioCtx.createOscillator(),
            g = audioCtx.createGain();
      o.type = doc.wave; o.frequency.value = midiHz(n.pitch);
      const v = 0.22 * (n.vel == null ? 0.7 : n.vel);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.linearRampToValueAtTime(Math.max(v, 0.0001), t + 0.015);
      g.gain.setValueAtTime(Math.max(v, 0.0001),
                            Math.max(t + 0.016, t + d - 0.06));
      g.gain.linearRampToValueAtTime(0.0001, t + d);
      o.connect(g); g.connect(audioCtx.destination);
      o.start(t); o.stop(t + d + 0.03);
    }
    const bar = bars[i], onMs = (t - audioCtx.currentTime) * 1000,
          offMs = onMs + d * 1000;
    if (bar) { setTimeout(() => bar.classList.add("on"), onMs);
               setTimeout(() => bar.classList.remove("on"), offMs); }
    t += d;
  });
  setTimeout(() => { btn.disabled = false; btn.textContent = "\u25B6"; },
             (t - t0) * 1000 + 120);
}

/* ── drives: breathing rings ── */
const R = 40, CIRC = 2 * Math.PI * R;
function renderDrives(doc) {
  const ds = doc.drives || [];
  setMoodFromDrives(ds);
  if (!ds.length) return;
  const box = $("drives"); box.innerHTML = "";
  for (const d of ds) {
    const frac = Math.max(0, Math.min(1, d.fraction || 0));
    const hot = frac >= 0.85 || d.pending;
    /* breath rate: calm 8s at zero pressure → urgent 2.2s at full */
    const breath = (8 - 5.8 * frac).toFixed(2) + "s";
    const el = document.createElement("div");
    /* .ghost: reconstructed moment before this drive existed — the
       gauge is shown but honestly dim (pressure unknown, zero) */
    el.className = "drive" + (d.known === false ? " ghost" : "");
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
async function pollDrives() { liveDrives(await api("/api/drives")); }

/* ── ambient mood ──
   The background is an instrument, not a decoration. From the live
   drive pressures we derive a two-number mood vector:
     pressure = mean drive fraction (0..1)  — how much wants to happen
     heat     = max drive fraction, forced to 1 if any wake is pending
                                            — how close the nearest urge
                                              is to acting
   Mapping (lerped ~10%/200ms so the room changes like light, not like
   a status LED):
     --mood-h : veil hue, 195° starlight blue (calm) → 35° dome amber
                (about to wake), driven by heat
     --mood-a : veil opacity .03 → .11, driven by pressure
     --drift-s: plankton drift period 120s (still) → 45s (agitated),
                driven by pressure */
const mood = { h: 195, a: 0.035, d: 120 };
let moodTarget = { h: 195, a: 0.035, d: 120 };
function setMoodFromDrives(ds) {
  if (!ds.length) return;
  const fr = ds.map(d => Math.max(0, Math.min(1, d.fraction || 0)));
  const pressure = fr.reduce((s, f) => s + f, 0) / fr.length;
  const heat = ds.some(d => d.pending) ? 1 : Math.max(...fr);
  moodTarget = { h: 195 - 160 * heat,          /* 195° → 35° */
                 a: 0.03 + 0.08 * pressure,
                 d: 120 - 75 * pressure };
}
setInterval(() => {
  const k = 0.1, st = document.documentElement.style;
  mood.h += (moodTarget.h - mood.h) * k;
  mood.a += (moodTarget.a - mood.a) * k;
  mood.d += (moodTarget.d - mood.d) * k;
  st.setProperty("--mood-h", mood.h.toFixed(1));
  st.setProperty("--mood-a", mood.a.toFixed(4));
  st.setProperty("--drift-s", mood.d.toFixed(1) + "s");
}, 200);

/* ── lineage: illuminated timeline ── */
const GLYPHS = { init: "✶", migration: "⇌", runtime_change: "↻",
                 shell_start: "◉", shell_stop: "◌" };
let linEntries = [], linPos = [];
async function pollLineage() {
  const doc = await api("/api/lineage");
  linEntries = (doc.lineage || []).slice(-60);   /* chronological */
  $("lineage").innerHTML = linEntries.slice().reverse().map((l, i) =>
    '<div class="lin k-' + esc(l.kind) + '" data-idx="' +
      (linEntries.length - 1 - i) + '">' +
      '<span class="glyph">' + (GLYPHS[l.kind] || "·") + "</span>" +
      '<span class="d">' + esc(l.detail) + "</span>" +
      '<span class="ts">' + esc(l.ts) + " · " + esc(l.kind) + "</span>" +
    "</div>").join("");
  drawConstellation(-1);
}

/* ── the constellation: each lineage event is a star ──
   Layout is DETERMINISTIC: x advances with chronology, y (and the
   x-jitter) come from an FNV-1a hash of the entry itself — the same
   biography always draws the same sky. Warm stars are the big moments
   (init, migration); teal is ordinary life; sleeps (shell_stop) are
   faint. Faint lines join consecutive events: one continuous life. */
function fnv(s) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}
function drawConstellation(hover) {
  const cv = $("constellation");
  const W = cv.clientWidth || 300, H = 120, dpr =
    window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext("2d");
  ctx.scale(dpr, dpr); ctx.clearRect(0, 0, W, H);
  const n = linEntries.length;
  if (!n) { linPos = []; return; }
  const pad = 14;
  linPos = linEntries.map((l, i) => {
    const h = fnv(l.ts + "|" + l.kind + "|" + l.detail);
    const x = pad + (n > 1 ? i / (n - 1) : 0.5) * (W - 2 * pad) +
              ((h & 0xff) / 255 - 0.5) * Math.min(18, (W - 2 * pad) / n);
    const y = 16 + ((h >>> 8) % 1000) / 1000 * (H - 32);
    return { x, y };
  });
  ctx.lineWidth = 1;                       /* the thread of the life */
  ctx.strokeStyle = "rgba(94,234,212,.13)";
  ctx.beginPath();
  linPos.forEach((p, i) => i ? ctx.lineTo(p.x, p.y)
                             : ctx.moveTo(p.x, p.y));
  ctx.stroke();
  linPos.forEach((p, i) => {
    const k = linEntries[i].kind;
    const warm = (k === "init" || k === "migration");
    const faint = (k === "shell_stop");
    const r = (warm ? 3.2 : faint ? 1.4 : 2.2) + (i === hover ? 1.6 : 0);
    ctx.shadowColor = warm ? "rgba(255,180,94,.9)"
                           : "rgba(94,234,212,.9)";
    ctx.shadowBlur = i === hover ? 16 : (warm ? 10 : faint ? 2 : 7);
    ctx.fillStyle = warm ? "#ffb45e"
                  : faint ? "rgba(109,138,134,.7)" : "#5eead4";
    ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 7); ctx.fill();
  });
  ctx.shadowBlur = 0;
}
let hoverIdx = -1;
$("constellation").addEventListener("mousemove", ev => {
  const box = ev.target.getBoundingClientRect();
  const mx = ev.clientX - box.left, my = ev.clientY - box.top;
  let best = -1, bd = 144;                 /* 12px capture radius² */
  linPos.forEach((p, i) => {
    const d = (p.x - mx) ** 2 + (p.y - my) ** 2;
    if (d < bd) { bd = d; best = i; }
  });
  if (best === hoverIdx) return;
  hoverIdx = best;
  drawConstellation(hoverIdx);
  for (const el of document.querySelectorAll("#lineage .lin")) {
    const lit = +el.dataset.idx === hoverIdx;
    el.classList.toggle("lit", lit);
    if (lit) el.scrollIntoView({ block: "nearest" });
  }
});
$("constellation").addEventListener("mouseleave", () => {
  hoverIdx = -1; drawConstellation(-1);
  for (const el of document.querySelectorAll("#lineage .lin"))
    el.classList.remove("lit");
});

/* ── ledger stream ── */
let maxLedgerId = -1;
function renderLedger(doc, initial) {
  const rows = doc.actions || [];
  const newest = rows.length ? rows[0].id : -1;
  $("ledger").innerHTML = rows.map(a =>
    '<div class="ln' +
    (!initial && a.id > maxLedgerId ? " fresh" : "") + '">' +
    '<span class="ts">' + esc(fmtTs(a.ts)) + "</span> " +
    '<span class="' + (a.outcome === "ok" ? "k" : "e") + '">' +
    esc(a.kind) + "</span> <b>" + esc(a.detail) + "</b></div>").join("");
  maxLedgerId = Math.max(maxLedgerId, newest);
}
async function pollLedger() {
  liveLedger(await api("/api/ledger?limit=50"), firstPaint);
}

/* ── time travel ──
   The scrub maps [oldest ledger ts … now] onto 0…1000. Dragging back
   fetches a window of the past (/api/history?until=… — on demand,
   never the whole ledger) and re-renders the ledger, the drive gauges
   and — through renderDrives → setMoodFromDrives — the ambient mood as
   they were at that moment. Live SSE/poll updates for those panels are
   stashed while in the past and replayed on return; the conversation
   stays live (the dialogue is the present, always). */
const past = { active: false };
const stash = { ledger: null, drives: null };
let tBounds = null, scrubTimer = null;

function liveLedger(doc, initial) {
  if (past.active) { stash.ledger = doc; return; }
  renderLedger(doc, initial);
}
function liveDrives(doc) {
  if (past.active) { stash.drives = doc; return; }
  renderDrives(doc);
}

async function initTimebar() {
  try {
    const doc = await api("/api/history?limit=1");
    if (doc.bounds && doc.bounds.oldest != null &&
        doc.now - doc.bounds.oldest > 5) {
      tBounds = doc.bounds;
      $("timebar").hidden = false;
    }
  } catch (e) { /* no history yet: the bar stays hidden */ }
}

$("scrub").addEventListener("input", () => {
  if (!tBounds) return;
  const v = +$("scrub").value;
  if (v >= 998) { returnToNow(); return; }
  const nowS = Date.now() / 1000;
  const t = tBounds.oldest + (v / 1000) * (nowS - tBounds.oldest);
  $("tlab").textContent = fmtTs(t);
  clearTimeout(scrubTimer);
  scrubTimer = setTimeout(() => fetchPast(t), 180);
});
$("nowbtn").addEventListener("click", returnToNow);

async function fetchPast(t) {
  try {
    const doc = await api("/api/history?until=" + t.toFixed(1)
                          + "&limit=50");
    enterPast(doc);
  } catch (e) { /* a failed window fetch just leaves the view as-is */ }
}
function enterPast(doc) {
  past.active = true;
  document.body.classList.add("past");
  $("pastpill").hidden = false;
  $("nowbtn").hidden = false;
  $("tlab").textContent = fmtTs(doc.until);
  renderLedger({ actions: doc.actions }, true);   /* no fresh blooms */
  renderDrives({ drives: doc.drives });           /* mood follows     */
}
function returnToNow() {
  const was = past.active;
  past.active = false;
  document.body.classList.remove("past");
  $("pastpill").hidden = true;
  $("nowbtn").hidden = true;
  $("scrub").value = 1000;
  $("tlab").textContent = "now";
  clearTimeout(scrubTimer);
  if (!was) return;
  if (stash.ledger) renderLedger(stash.ledger, true);
  else pollLedger().catch(() => {});
  if (stash.drives) renderDrives(stash.drives);
  else pollDrives().catch(() => {});
  stash.ledger = stash.drives = null;
}

/* ── stats / lock ── */
function renderStats(doc) {
  const m = (doc.memory || {});
  $("statpill").textContent =
    "episodes " + (m.episodes ?? "—") +
    " · beliefs " + ((m.beliefs || {}).active ?? "—") +
    " · wakes " + (doc.wakes_dispatched ?? "—") +
    " · ledger " + (doc.ledger_entries ?? "—");
  $("locktext").textContent = doc.lock || "live";
  document.title = (doc.name || "anima") + " — Observatory";
}
async function pollStats() { renderStats(await api("/api/stats")); }

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

/* ── live stream (SSE) with graceful fallback to polling ──
   The dome prefers a single held-open /api/stream connection; if it
   drops (server restart, network), polling resumes on the next tick
   and we retry the stream with a gentle backoff. */
let sseLive = false, sseRetryMs = 3000, es = null;
function connectStream() {
  if (!window.EventSource) return;         /* ancient browser → polling */
  try { es = new EventSource("/api/stream"); } catch (e) { return; }
  let opened = false;
  es.onopen = () => { opened = true; sseLive = true; sseRetryMs = 3000;
                      setAdrift(false); };
  es.addEventListener("ledger", ev => {
    const doc = JSON.parse(ev.data);
    liveLedger(doc, !(doc.fresh_ids || []).length);
  });
  es.addEventListener("expressions", ev => {
    const doc = JSON.parse(ev.data);
    renderExpressions(doc, !!doc.initial);
  });
  es.addEventListener("drives", ev => liveDrives(JSON.parse(ev.data)));
  es.addEventListener("stats", ev => renderStats(JSON.parse(ev.data)));
  es.addEventListener("replies", ev => renderReplies(JSON.parse(ev.data)));
  es.onerror = () => {                     /* fall back to polling */
    sseLive = false;
    setAdrift(true);
    try { es.close(); } catch (e) {}
    es = null;
    sseRetryMs = Math.min(sseRetryMs * 2, 60000);
    setTimeout(connectStream, sseRetryMs);
  };
}

/* ── presence ── */
function setAdrift(on) {
  document.body.classList.toggle("adrift", on);
  if (on) $("locktext").textContent = "adrift · the window looks back";
}
function openIris() {                      /* the dome opens on arrival */
  const el = $("iris");
  let r = 0;
  const t0 = performance.now(), dur = 1600;
  (function step(t) {
    const k = Math.min(1, (t - t0) / dur);
    r = 140 * (1 - Math.pow(1 - k, 3));    /* ease-out cubic */
    el.style.setProperty("--iris", r.toFixed(1) + "%");
    if (k < 1) requestAnimationFrame(step);
    else el.remove();
  })(t0);
}
openIris();

/* ── foldable panels ──
   Every instrument header is a toggle (44px+ touch target). On a phone
   the noisier instruments start folded — the conversation is what you
   came for; the rest waits one tap away. */
for (const sec of document.querySelectorAll("[data-fold]")) {
  sec.querySelector("h2").addEventListener("click", () => {
    sec.classList.toggle("folded");
    if (sec.dataset.fold === "lineage" && !sec.classList.contains("folded"))
      requestAnimationFrame(() => drawConstellation(-1));
  });
}
if (window.matchMedia("(max-width: 700px)").matches) {
  for (const name of ["ledger", "lineage", "memory"]) {
    const sec = document.querySelector('[data-fold="' + name + '"]');
    if (sec) sec.classList.add("folded");
  }
}
window.addEventListener("resize", () => drawConstellation(hoverIdx));

/* ── under the hood: routing + host machinery ──
   The dome's engine room. Reads /api/routing (the failover ladder,
   with a live TCP ping per candidate) and /api/under-the-hood (swap
   proxy, GPU tenants, the shared swap marker, the last receipts).
   Editing a tier fetches the CURRENT file, patches just that tier,
   and POSTs the whole document — last-writer-wins, never a blind
   clobber of stale state. */
function showToast(msg, isErr) {
  let t = $("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast";
            document.body.appendChild(t); }
  t.textContent = msg;
  t.classList.toggle("err", !!isErr);
  t.classList.add("shown");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => t.classList.remove("shown"), 3200);
}

let lastRouting = null, editingTier = null;
async function pollRouting() {
  try { lastRouting = await api("/api/routing"); renderRouting(); }
  catch (e) { $("routing").innerHTML =
    '<div class="empty">routing.json unreadable.</div>'; }
}
function renderRouting() {
  if (!lastRouting) return;
  const tiers = (lastRouting.routing || {}).tiers || {};
  const st = lastRouting.candidates_status || [];
  const box = $("routing"); box.innerHTML = "";
  for (const [tname, tier] of Object.entries(tiers)) {
    const row = document.createElement("div");
    row.className = "tierrow";
    row.innerHTML = '<span class="tn">' + esc(tname) + "</span>";
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "mini";
    btn.textContent = editingTier === tname ? "editing…" : "edit";
    btn.disabled = editingTier === tname;
    btn.addEventListener("click", () => {
      editingTier = tname; renderRouting(); });
    row.appendChild(btn);
    box.appendChild(row);
    if (editingTier === tname) {
      box.appendChild(buildTierEditor(tname, tier)); continue; }
    (tier.candidates || []).forEach((c, i) => {
      const s = st.find(x => x.tier === tname && x.index === i) || {};
      const el = document.createElement("div");
      el.className = "cand";
      /* the endpoint's own answer, at render time; when it disagrees
         with routing.json, show BOTH — drift is information. */
      let rep = "";
      if (s.reported_model && s.drift)
        rep = ' <span class="drift">⚠ endpoint reports “' +
              esc(s.reported_model) + '” · drift</span>';
      else if (s.reported_model)
        rep = ' <span class="rep">· reports ' +
              esc(s.reported_model) + '</span>';
      el.innerHTML = '<span class="ord">' + (i + 1) + "</span>" +
        '<div class="pm"><span class="sdot ' +
        (s.alive ? "ok" : "bad") + '"></span>' +
        esc(c.provider) + " · " + esc(c.model) + rep + "</div>" +
        '<div class="bu">' + esc(c.base_url) +
        (s.alive && s.latency_ms != null
          ? " · " + s.latency_ms + "ms" : "") + "</div>";
      box.appendChild(el);
    });
  }
  if (!box.childNodes.length)
    box.innerHTML = '<div class="empty">no tiers configured.</div>';
}
function buildTierEditor(tname, tier) {
  const wrap = document.createElement("div");
  const ta = document.createElement("textarea");
  ta.className = "jsonedit"; ta.spellcheck = false;
  ta.value = JSON.stringify(tier, null, 2);
  const row = document.createElement("div"); row.className = "editrow";
  const save = document.createElement("button");
  save.type = "button"; save.textContent = "Save";
  const cancel = document.createElement("button");
  cancel.type = "button"; cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => {
    editingTier = null; renderRouting(); });
  save.addEventListener("click", async () => {
    let patch;
    try { patch = JSON.parse(ta.value); }
    catch (e) { showToast("invalid JSON: " + e.message, true); return; }
    save.disabled = true;
    try {
      const fresh = await api("/api/routing");  /* fetch, patch, POST */
      const doc = fresh.routing || {};
      doc.tiers = doc.tiers || {};
      doc.tiers[tname] = patch;
      await api("/api/routing", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(doc) });
      editingTier = null;
      showToast("routing saved · " + tname + " · backup written");
      await pollRouting();
    } catch (e) {
      save.disabled = false;
      showToast("save failed: " + e.message, true);
    }
  });
  row.appendChild(save); row.appendChild(cancel);
  wrap.appendChild(ta); wrap.appendChild(row);
  return wrap;
}

async function pollHood() {
  try { renderHood(await api("/api/under-the-hood")); }
  catch (e) { /* the engine room can wait a tick */ }
}
function renderHood(doc) {
  /* swap marker: only shown when web.json configures one */
  const m = doc.swap_marker;
  if (m) {
    $("swapdot").style.display = "";
    $("swapdot").classList.toggle("open", !!m.present);
    $("swaplab").textContent = m.present
      ? "swap window open · " + (m.age_seconds != null
          ? m.age_seconds.toFixed(0) + "s" : "?")
      : "swap window closed";
  } else {
    $("swapdot").style.display = "none";
    $("swaplab").textContent = "";
  }
  /* tenants: config-driven (senses/web.json "tenants"); no config,
     no panel — the dome never guesses at the host's machinery. */
  const ts = doc.tenants || [];
  if (!ts.length) {
    $("tenanthead").style.display = "none";
    $("tenants").style.display = "none";
  } else {
    $("tenanthead").style.display = "";
    $("tenants").style.display = "";
    let h = "";
    for (const t of ts) {
      const state = t.state || "unknown";
      const cls = (state === "active" || state === "up") ? "ok"
        : (state === "activating" || state === "inactive"
           || state === "reloading") ? "warn" : "bad";
      const detail = t.kind === "http"
        ? esc(String(t.url || "").replace("http://", "")) +
          (t.alive && t.latency_ms != null
            ? " · " + t.latency_ms + "ms" : "")
        : esc(state);
      h += '<div class="ten"><span class="sdot ' + cls + '"></span>' +
        '<span class="tn2">' + esc(t.label) + '</span>' +
        '<span class="tst">' + detail + "</span></div>";
    }
    $("tenants").innerHTML = h;
  }
  /* models: what each routing endpoint reports at render time */
  const reps = doc.models || [];
  if (!reps.length) {
    $("modelhead").style.display = "none";
    $("hoodmodels").style.display = "none";
  } else {
    $("modelhead").style.display = "";
    $("hoodmodels").style.display = "";
    $("hoodmodels").innerHTML = reps.map(r =>
      '<div class="ten"><span class="sdot ' +
      (r.reported ? "ok" : "bad") + '"></span><span class="tn2">' +
      esc(String(r.base_url || "").replace("http://", "")) +
      '</span><span class="tst">' +
      (r.reported
        ? esc(r.reported) + (r.drift
            ? ' <span class="drift">⚠ configured: ' +
              esc((r.configured || []).join(", ")) + "</span>"
            : "")
        : "no answer") + "</span></div>").join("");
  }
  const tail = doc.ledger_tail || [];
  $("hoodledger").innerHTML = tail.length ? tail.map(r =>
    '<div class="ln"><span class="ts">' +
    esc(typeof r.ts === "number" ? fmtTs(r.ts) : (r.ts || "")) +
    '</span> <span class="k">' + esc(r.kind) + "</span> <b>" +
    esc(r.detail) + "</b></div>").join("")
    : '<div class="empty">quiet.</div>';
}

/* ── main loop ── */
let firstPaint = true;
async function tick() {
  if (!sseLive) {
    await Promise.allSettled([pollReplies(), pollExpressions(),
                              pollDrives(), pollLedger(), pollStats()]);
  }
  firstPaint = false;
}
pollLineage(); setInterval(pollLineage, 30000);
tick(); setInterval(tick, 3000);
connectStream();
initTimebar(); setInterval(() => { if (!past.active) initTimebar(); },
                           60000);
pollRouting(); pollHood(); setInterval(pollHood, 5000);
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


SKY_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — shared sky</title>
<style>
:root {
  --abyss:  #030809;
  --deep:   #06121a;
  --panel:  rgba(9, 24, 32, 0.78);
  --line:   rgba(94, 234, 212, 0.10);
  --line2:  rgba(94, 234, 212, 0.22);
  --ink:    #c9dcd8; --ink-dim: #6d8a86; --ink-faint: #3c5450;
  --glow:   #5eead4; --glow2: #7fd4ff; --lamp: #ffb45e;
  --err:    #ff8d9d;
  --serif:  Georgia, "Iowan Old Style", "Times New Roman", serif;
  --sans:   system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono:   ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
html { background: var(--abyss); }
body {
  font-family: var(--sans); color: var(--ink);
  background:
    radial-gradient(1200px 620px at 70% -12%, #0b2430 0%, transparent 60%),
    radial-gradient(1000px 700px at -12% 108%, #07202a 0%, transparent 55%),
    linear-gradient(180deg, var(--deep), var(--abyss) 62%);
  background-attachment: fixed;
}
.wrap { max-width: 1380px; margin: 0 auto; padding: 22px 24px 60px;
        position: relative; }
header { display: flex; align-items: baseline; gap: 18px;
  flex-wrap: wrap; padding: 10px 2px 18px; margin-bottom: 18px;
  position: relative; }
header::after { content: ""; position: absolute; left: 0; right: 0;
  bottom: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--line2) 20%,
              var(--line2) 60%, transparent); }
header h1 { font-family: var(--serif); font-size: 28px; font-weight: 400;
  margin: 0; letter-spacing: .06em; color: #e9f4f0;
  text-shadow: 0 0 26px rgba(94,234,212,.35); }
header h1 .dome { color: var(--lamp);
  text-shadow: 0 0 18px rgba(255,180,94,.6);
  animation: lamp 7s ease-in-out infinite; }
@keyframes lamp { 0%,100% { opacity:.85; } 50% { opacity:1; } }
header .sub { font-family: var(--serif); font-style: italic;
  font-size: 13px; color: var(--ink-dim); letter-spacing: .12em; }
header .spacer { flex: 1; }
.pill { font-family: var(--mono); font-size: 11px; padding: 4px 12px;
  border: 1px solid var(--line); border-radius: 999px;
  color: var(--ink-dim); background: rgba(3,10,12,.4); }
.pill .dot { display: inline-block; width: 7px; height: 7px;
  border-radius: 50%; background: var(--glow);
  box-shadow: 0 0 10px var(--glow); margin-right: 7px;
  vertical-align: 1px; animation: heartbeat 4s ease-in-out infinite; }
@keyframes heartbeat { 0%,100% { opacity:.6; } 50% { opacity:1; } }

/* ── the sky itself ── */
#skybox { position: relative; border: 1px solid var(--line);
  border-radius: 14px; overflow: hidden;
  background: radial-gradient(900px 420px at 50% 115%,
    rgba(94,234,212,.05), transparent 70%),
    linear-gradient(180deg, rgba(6,18,24,.5), rgba(3,9,12,.7)); }
#sky { display: block; width: 100%; height: clamp(380px, 62vh, 720px);
  cursor: crosshair; }
.skyhint { position: absolute; left: 14px; bottom: 10px;
  font-family: var(--serif); font-style: italic; font-size: 11.5px;
  color: var(--ink-faint); letter-spacing: .1em; pointer-events: none; }

/* ── cluster summary card ── */
#card { position: absolute; top: 16px; right: 16px; width: 300px;
  max-width: calc(100% - 32px); max-height: calc(100% - 32px);
  overflow-y: auto; background: var(--panel);
  border: 1px solid var(--line2); border-radius: 14px;
  padding: 16px 18px; backdrop-filter: blur(7px);
  box-shadow: 0 14px 50px rgba(0,0,0,.5);
  opacity: 0; transform: translateY(-8px); pointer-events: none;
  transition: opacity .45s cubic-bezier(.22,1,.36,1),
              transform .45s cubic-bezier(.22,1,.36,1); }
#card.shown { opacity: 1; transform: none; pointer-events: auto; }
#card h3 { margin: 0 0 2px; font-family: var(--serif); font-weight: 400;
  font-size: 19px; color: #e9f4f0;
  text-shadow: 0 0 16px rgba(94,234,212,.4); }
#card .sub { font-family: var(--mono); font-size: 9.5px;
  color: var(--ink-faint); letter-spacing: .1em; margin-bottom: 10px; }
#card .sub.err { color: var(--err); }
#card .row { font-size: 12.5px; color: var(--ink-dim); margin: 6px 0; }
#card .row b { color: var(--ink); font-weight: 500; }
#card .dbar { display: flex; align-items: center; gap: 8px;
  margin: 5px 0; }
#card .dbar .nm { font-family: var(--serif); font-style: italic;
  font-size: 12px; color: var(--ink); width: 92px; flex: none;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#card .dbar .tr { flex: 1; height: 4px; border-radius: 2px;
  background: rgba(94,234,212,.08); overflow: hidden; }
#card .dbar .fl { height: 100%; border-radius: 2px;
  background: var(--glow); box-shadow: 0 0 8px rgba(94,234,212,.5);
  transition: width .9s cubic-bezier(.22,1,.36,1); }
#card .dbar.hot .fl { background: var(--lamp);
  box-shadow: 0 0 8px rgba(255,180,94,.6); }
#card .dbar .pv { font-family: var(--mono); font-size: 10px;
  color: var(--ink-faint); width: 28px; text-align: right; }
#card .xwrap { margin-top: 10px; border: 1px solid var(--line);
  border-radius: 10px; padding: 10px 12px; max-height: 170px;
  overflow: auto; font-size: 12.5px; line-height: 1.5;
  background: rgba(3,9,12,.5); }
#card .xwrap svg { max-width: 100%; height: auto; display: block; }
#card .xcap { font-family: var(--serif); font-style: italic;
  font-size: 11.5px; color: var(--lamp); margin-top: 6px; }
#card a.visit { display: inline-block; margin-top: 12px;
  font-size: 12.5px; color: var(--glow2); text-decoration: none;
  border-bottom: 1px dotted rgba(127,212,255,.4); }
#card a.visit:hover { text-shadow: 0 0 12px rgba(127,212,255,.5); }
#card .close { position: absolute; top: 8px; right: 12px;
  cursor: pointer; color: var(--ink-faint); font-size: 15px;
  background: none; border: none; padding: 4px 6px; min-height: 0; }
#card .close:hover { color: var(--ink); box-shadow: none; }
.empty { color: var(--ink-faint); font-size: 13px; font-style: italic;
  font-family: var(--serif); padding: 14px 6px; }
footer { margin-top: 26px; text-align: center;
  font-family: var(--serif); font-style: italic; font-size: 11px;
  color: var(--ink-faint); letter-spacing: .18em; }
@media (max-width: 700px) {
  .wrap { padding: 12px 12px 40px; }
  header h1 { font-size: 21px; }
  header .sub { display: none; }
  #sky { height: min(64dvh, 520px); }
  #card { top: 8px; right: 8px; left: 8px; width: auto; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01s !important;
    transition-duration: .01s !important; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="dome">◉</span> __TITLE__</h1>
    <span class="sub">every entity a constellation · migrations join them</span>
    <span class="spacer"></span>
    <span class="pill" id="countpill"><span class="dot"></span><span id="counttext">…</span></span>
  </header>

  <div id="skybox">
    <canvas id="sky"></canvas>
    <div class="skyhint">click a cluster to read its life</div>
    <div id="card" role="dialog" aria-label="entity summary"></div>
  </div>

  <footer>anima · many biographies · one sky</footer>
</div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const esc = s => { const d = document.createElement("span");
                   d.textContent = s == null ? "" : String(s);
                   return d.innerHTML; };
const fmtTs = t => { try { return new Date(t * 1000)
  .toISOString().replace("T", " ").slice(0, 19); } catch (e) { return ""; } };

/* deterministic star layout — same FNV hash as the single-entity
   constellation: the same biography always draws the same cluster */
function fnv(s) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

let SKY = { peers: [], edges: [] };
let clusters = [];          /* computed layout: {peer, cx, cy, r, stars} */
let selected = -1;

async function poll() {
  try {
    const r = await fetch("/api/sky");
    if (!r.ok) throw new Error(r.status);
    SKY = await r.json();
    const up = SKY.peers.filter(p => p.reachable).length;
    $("counttext").textContent =
      up + "/" + SKY.peers.length + " entities shining";
    layout();
  } catch (e) {
    $("counttext").textContent = "sky unreachable";
  }
}

/* ── layout: clusters on a loose ring, stars hashed within ── */
function layout() {
  const cv = $("sky");
  const W = cv.clientWidth || 800, H = cv.clientHeight || 480;
  const n = SKY.peers.length;
  clusters = SKY.peers.map((p, i) => {
    const h = fnv(p.name || p.url);
    let cx, cy;
    if (n === 1) { cx = W / 2; cy = H / 2; }
    else {
      const ang = (i / n) * 2 * Math.PI - Math.PI / 2 +
                  ((h & 0xff) / 255 - .5) * .25;
      cx = W / 2 + Math.cos(ang) * W * .30;
      cy = H / 2 + Math.sin(ang) * H * .28;
    }
    const lin = p.lineage || [];
    const R = Math.min(W, H) * (.10 + Math.min(.08, lin.length * .004));
    const stars = lin.map((l, j) => {
      const sh = fnv((l.ts||"") + "|" + (l.kind||"") + "|" + (l.detail||""));
      const a = ((sh & 0xffff) / 65535) * 2 * Math.PI;
      const rr = Math.sqrt(((sh >>> 16) & 0xffff) / 65535) * R;
      return { x: cx + Math.cos(a) * rr, y: cy + Math.sin(a) * rr,
               kind: l.kind };
    });
    return { peer: p, cx, cy, r: R, stars };
  });
}

/* ── draw loop: pulse rate is drive heat; unreachable stars are dim
   and still — liveness is legible at a glance ── */
function heatOf(p) {
  const ds = p.drives || [];
  if (!ds.length) return 0;
  if (ds.some(d => d.pending)) return 1;
  return Math.max(...ds.map(d => Math.max(0, Math.min(1, d.fraction||0))));
}
function draw(t) {
  const cv = $("sky");
  const W = cv.clientWidth || 800, H = cv.clientHeight || 480;
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  /* migration edges FIRST — under the clusters: warm threads that
     make several biographies one sky */
  for (const e of (SKY.edges || [])) {
    const a = clusters.find(c => c.peer.name === e.from);
    const b = clusters.find(c => c.peer.name === e.to);
    if (!a || !b) continue;
    const mx = (a.cx + b.cx) / 2, my = (a.cy + b.cy) / 2;
    const dx = b.cx - a.cx, dy = b.cy - a.cy;
    const len = Math.hypot(dx, dy) || 1;
    const bow = Math.min(48, len * .18);
    const qx = mx - (dy / len) * bow, qy = my + (dx / len) * bow;
    ctx.strokeStyle = "rgba(255,180,94,.32)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([5, 6]);
    ctx.lineDashOffset = -(t / 90) % 11;
    ctx.shadowColor = "rgba(255,180,94,.5)"; ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.moveTo(a.cx, a.cy);
    ctx.quadraticCurveTo(qx, qy, b.cx, b.cy);
    ctx.stroke();
    ctx.setLineDash([]); ctx.shadowBlur = 0;
  }

  clusters.forEach((c, ci) => {
    const p = c.peer, live = p.reachable;
    const heat = live ? heatOf(p) : 0;
    /* pulse: calm 5s breath at zero heat → urgent 1.4s at full;
       unreachable clusters do not breathe at all */
    const period = 5000 - 3600 * heat;
    const pulse = live ? .5 + .5 * Math.sin(t / period * 2 * Math.PI)
                       : 0;
    /* halo */
    const halo = ctx.createRadialGradient(c.cx, c.cy, 0,
                                          c.cx, c.cy, c.r * 1.35);
    const hue = live ? (heat > .85 ? "255,180,94" : "94,234,212")
                     : "109,138,134";
    halo.addColorStop(0, "rgba(" + hue + "," +
      (live ? (.10 + .10 * pulse + .06 * heat).toFixed(3) : ".035") + ")");
    halo.addColorStop(1, "rgba(" + hue + ",0)");
    ctx.fillStyle = halo;
    ctx.beginPath(); ctx.arc(c.cx, c.cy, c.r * 1.35, 0, 7); ctx.fill();

    /* the cluster's stars: its lineage */
    for (const s of c.stars) {
      const warm = (s.kind === "init" || s.kind === "migration");
      const faint = (s.kind === "shell_stop");
      ctx.shadowColor = live
        ? (warm ? "rgba(255,180,94,.9)" : "rgba(94,234,212,.9)")
        : "rgba(109,138,134,.4)";
      ctx.shadowBlur = live ? (warm ? 9 : faint ? 2 : 6) : 1;
      ctx.fillStyle = live
        ? (warm ? "#ffb45e" : faint ? "rgba(109,138,134,.7)" : "#5eead4")
        : "rgba(109,138,134,.55)";
      const r = (warm ? 2.6 : faint ? 1.1 : 1.8) *
                (live ? (1 + .18 * pulse) : 1);
      ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, 7); ctx.fill();
    }
    ctx.shadowBlur = 0;

    /* selection ring */
    if (ci === selected) {
      ctx.strokeStyle = "rgba(127,212,255,.5)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 5]);
      ctx.beginPath(); ctx.arc(c.cx, c.cy, c.r + 10, 0, 7); ctx.stroke();
      ctx.setLineDash([]);
    }

    /* name label — the cluster is addressable, not anonymous */
    ctx.font = "italic 13px Georgia, serif";
    ctx.textAlign = "center";
    ctx.fillStyle = live ? "rgba(201,220,216,.85)"
                         : "rgba(109,138,134,.6)";
    ctx.shadowColor = "rgba(3,8,9,.9)"; ctx.shadowBlur = 4;
    ctx.fillText(p.name || "?", c.cx, c.cy + c.r + 20);
    if (!live) {
      ctx.font = "9px ui-monospace, monospace";
      ctx.fillStyle = "rgba(255,141,157,.55)";
      ctx.fillText("unreachable", c.cx, c.cy + c.r + 33);
    }
    ctx.shadowBlur = 0;
  });

  requestAnimationFrame(draw);
}

/* ── cluster card ── */
function ageOf(p) {
  const lin = p.lineage || [];
  const first = lin.find(l => l.kind === "init") || lin[0];
  if (!first || !first.ts) return null;
  const born = Date.parse(first.ts);
  if (isNaN(born)) return null;
  const days = (Date.now() - born) / 86400000;
  if (days < 1) return (days * 24).toFixed(1) + " hours";
  if (days < 60) return days.toFixed(1) + " days";
  return (days / 30.44).toFixed(1) + " months";
}
function showCard(ci) {
  selected = ci;
  const c = clusters[ci];
  if (!c) { $("card").classList.remove("shown"); return; }
  const p = c.peer, box = $("card");
  const st = p.stats || {};
  let h = '<button class="close" aria-label="close">×</button>' +
    "<h3>" + esc(p.name) + "</h3>";
  h += p.reachable
    ? '<div class="sub">' + esc(st.lock || "live") + "</div>"
    : '<div class="sub err">unreachable · ' +
      esc(p.error || "no answer") +
      (p.last_seen ? " · last seen " + esc(fmtTs(p.last_seen)) : "") +
      "</div>";
  const age = ageOf(p);
  if (age) h += '<div class="row">alive <b>' + esc(age) + "</b></div>";
  if (p.reachable)
    h += '<div class="row">episodes <b>' + esc(st.episodes ?? "—") +
         "</b> · beliefs <b>" + esc(st.beliefs ?? "—") +
         "</b> · wakes <b>" + esc(st.wakes ?? "—") +
         "</b> · ledger <b>" + esc(st.ledger ?? "—") + "</b></div>";
  for (const d of (p.drives || [])) {
    const fr = Math.max(0, Math.min(1, d.fraction || 0));
    const hot = fr >= .85 || d.pending;
    h += '<div class="dbar' + (hot ? " hot" : "") + '">' +
      '<span class="nm">' + esc(d.name) + "</span>" +
      '<span class="tr"><span class="fl" style="width:' +
      (fr * 100).toFixed(0) + '%"></span></span>' +
      '<span class="pv">' + (d.pressure || 0).toFixed(1) + "</span></div>";
  }
  if (p.expression && p.expression.body) {
    h += '<div class="xwrap" id="cardexpr"></div>' +
         '<div class="xcap">' +
         esc(p.expression.title || p.expression.kind) +
         (p.expression.ts ? " · " + esc(fmtTs(p.expression.ts)) : "") +
         "</div>";
  }
  h += '<a class="visit" href="' +
    encodeURI(p.url) + '/" target="_blank" rel="noopener noreferrer">' +
    "open this entity's observatory →</a>";
  box.innerHTML = h;
  const xw = box.querySelector("#cardexpr");
  if (xw && p.expression) {
    if (p.expression.kind === "tone") {
      /* tones show as their meta line here; the peer's own
         Observatory is the place to play them */
      xw.textContent = "♪ a tone — open the observatory to play it";
    } else {
      xw.innerHTML = p.expression.body;  /* re-sanitized by the sky
                                            server before serving */
    }
  }
  box.querySelector(".close").addEventListener("click", () => {
    selected = -1; box.classList.remove("shown");
  });
  box.classList.add("shown");
}

$("sky").addEventListener("click", ev => {
  const box = ev.target.getBoundingClientRect();
  const mx = ev.clientX - box.left, my = ev.clientY - box.top;
  let best = -1, bd = Infinity;
  clusters.forEach((c, i) => {
    const d = Math.hypot(c.cx - mx, c.cy - my);
    if (d < c.r + 24 && d < bd) { bd = d; best = i; }
  });
  if (best < 0) { selected = -1; $("card").classList.remove("shown"); }
  else showCard(best);
});
window.addEventListener("resize", layout);

poll(); setInterval(poll, 5000);
requestAnimationFrame(draw);   /* one draw loop, started once */
</script>
</body>
</html>
"""


def render_sky_page(title: str) -> str:
    """Fill the shared-sky template (same marker-replace discipline as
    render_page — the CSS is full of braces)."""
    safe = (title or "the shared sky").replace("<", "").replace(">", "")
    safe = (safe.replace("\\", "").replace('"', "").replace("'", "")
            .replace("`", ""))
    return SKY_TEMPLATE.replace("__TITLE__", safe)


def render_page(entity_name: str) -> str:
    """Fill the page template. (No str.format — the CSS is full of
    braces; a plain marker replace is the honest tool here.)"""
    safe = (entity_name or "anima").replace("<", "").replace(">", "")
    # __NAME__ also lands inside a JS string literal (const ENT): strip
    # quote/backslash so a hostile directory name can't escape it.
    safe = (safe.replace("\\", "").replace('"', "").replace("'", "")
            .replace("`", ""))
    return PAGE_TEMPLATE.replace("__NAME__", safe)
