"""PWA shell assets + reach-key lifecycle (Phase 8a, PHASE8_REACH.md).

The Observatory becomes installable: a manifest, a service worker, and
a maskable icon — all generated here, all pure stdlib. Doctrine:

- **The shell is cached, the entity is not.** The service worker below
  is cache-first for static assets only; every /api/ request goes to
  the network, and the page itself is network-first with an honest
  "unreachable" fallback. A companion that answers from cache is lying
  about presence (same contract as the boot_id/staleness honesty work).
- **The icon is grown, not shipped.** A radial glyph — the dome lamp
  inside bioluminescent rings — rendered pixel-by-pixel into a real
  PNG with nothing but zlib + struct. Written once under
  identity/pwa/ and reused forever; delete the files to regrow them.
- **VAPID keys are identity.** The entity signs its own pushes; the
  keypair lives under identity/vapid/ (private key 0600), generated at
  `anima init` and lazily at web-sense start for pre-8a roots.
- **TLS is optional but honest.** iOS refuses push (and home-screen
  install over LAN) without HTTPS; `ensure_tls_cert` shells out to the
  system openssl for a self-signed cert under identity/tls/ — the one
  deliberate non-stdlib dependency, because X.509 generation does not
  exist in the stdlib and openssl exists everywhere the entity would
  live. Plain HTTP keeps working with push disabled.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import zlib
from typing import Dict, Optional, Tuple

from ..crypto import vapid as vapid_mod

# ── palette (mirrors the Observatory's dark-sky variables) ────────────
ABYSS = (3, 8, 9)          # --abyss
DEEP = (6, 18, 26)         # --deep
GLOW = (94, 234, 212)      # --glow: bioluminescent teal
LAMP = (255, 180, 94)      # --lamp: the dome lamp

THEME_COLOR = "#06121a"
BACKGROUND_COLOR = "#030809"

ICON_SIZES = (192, 512)


# ── manifest ──────────────────────────────────────────────────────────

def build_manifest(entity_name: str) -> Dict[str, object]:
    """The web app manifest: name from identity, colors from the sky."""
    name = entity_name or "anima"
    return {
        "name": f"{name} — Observatory",
        "short_name": name[:12],
        "description": f"the observatory of {name}",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": BACKGROUND_COLOR,
        "theme_color": THEME_COLOR,
        "icons": [
            {"src": f"/icon-{s}.png", "sizes": f"{s}x{s}",
             "type": "image/png", "purpose": "any maskable"}
            for s in ICON_SIZES
        ],
    }


def render_manifest(entity_name: str) -> str:
    return json.dumps(build_manifest(entity_name), indent=2) + "\n"


# ── the maskable icon: a radial glyph, rendered from math ─────────────

def _blend(base: Tuple[float, float, float],
           over: Tuple[int, int, int], a: float):
    """base ← over at alpha a (a clamped to [0,1])."""
    a = 0.0 if a < 0.0 else (1.0 if a > 1.0 else a)
    return (base[0] + (over[0] - base[0]) * a,
            base[1] + (over[1] - base[1]) * a,
            base[2] + (over[2] - base[2]) * a)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def generate_icon_png(size: int = 512) -> bytes:
    """The entity's face mark as a real PNG: the dome lamp inside two
    bioluminescent rings on the deep-sky ground. Full-bleed (maskable);
    all marks live inside the 40% safe-zone circle. Deterministic —
    the same size always renders the same bytes."""
    if size < 16 or size > 1024:
        raise ValueError("icon size must be within 16..1024")
    centre = (size - 1) / 2.0
    maxr = size / 2.0
    rings = ((0.36, 0.026, 0.90), (0.58, 0.020, 0.42))  # (r, width, alpha)
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # PNG filter type 0 per scanline
        dy = (y - centre) / maxr
        dy2 = dy * dy
        for x in range(size):
            dx = (x - centre) / maxr
            r = math.sqrt(dx * dx + dy2)
            # ground: deep centre falling toward the abyss at the rim
            t = min(1.0, r * 0.8)
            px = (DEEP[0] + (ABYSS[0] - DEEP[0]) * t,
                  DEEP[1] + (ABYSS[1] - DEEP[1]) * t,
                  DEEP[2] + (ABYSS[2] - DEEP[2]) * t)
            # ambient bioluminescence around the core
            amb = 1.0 - r / 0.85
            if amb > 0.0:
                px = _blend(px, GLOW, 0.22 * amb * amb)
            # the rings
            for r0, w, a in rings:
                d = abs(r - r0) / w
                if d < 1.0:
                    px = _blend(px, GLOW, a * (1.0 - d * d))
            # the lamp
            if r < 0.15:
                c = 1.0 - (r / 0.15) ** 2
                px = _blend(px, LAMP, c)
            raw.extend((int(px[0] + 0.5), int(px[1] + 0.5),
                        int(px[2] + 0.5)))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _png_chunk(b"IEND", b""))


def pwa_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "identity", "pwa")


def icon_path(root: str, size: int) -> str:
    return os.path.join(pwa_dir(root), f"icon-{size}.png")


def ensure_icon(root: str, size: int) -> str:
    """Render the icon once, keep it forever. Returns the file path."""
    if size not in ICON_SIZES:
        raise ValueError(f"icon size must be one of {ICON_SIZES}")
    path = icon_path(root, size)
    if not os.path.exists(path):
        os.makedirs(pwa_dir(root), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(generate_icon_png(size))
        os.replace(tmp, path)
    return path


def ensure_icons(root: str) -> Dict[int, str]:
    return {s: ensure_icon(root, s) for s in ICON_SIZES}


# ── VAPID keypair lifecycle ───────────────────────────────────────────

def vapid_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "identity", "vapid")


def load_vapid_keys(root: str) -> Optional[Dict[str, str]]:
    """{'private_key','public_key'} from identity/vapid/, or None."""
    d = vapid_dir(root)
    priv_p = os.path.join(d, "private_key")
    pub_p = os.path.join(d, "public_key")
    try:
        with open(priv_p, "r", encoding="utf-8") as f:
            private = f.read().strip()
        with open(pub_p, "r", encoding="utf-8") as f:
            public = f.read().strip()
    except OSError:
        return None
    if not private or not public:
        return None
    return {"private_key": private, "public_key": public}


def ensure_vapid_keys(root: str) -> Dict[str, str]:
    """Load the entity's VAPID keypair, generating it on first need.
    The private key file is 0600 — it is the entity's signature."""
    keys = load_vapid_keys(root)
    if keys is not None:
        return keys
    d = vapid_dir(root)
    os.makedirs(d, exist_ok=True)
    keys = vapid_mod.generate_vapid_keys()
    priv_p = os.path.join(d, "private_key")
    with open(priv_p, "w", encoding="utf-8") as f:
        f.write(keys["private_key"] + "\n")
    os.chmod(priv_p, 0o600)
    with open(os.path.join(d, "public_key"), "w", encoding="utf-8") as f:
        f.write(keys["public_key"] + "\n")
    return keys


# ── TLS: the self-signed path iOS demands ─────────────────────────────

def tls_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), "identity", "tls")


def ensure_tls_cert(root: str, *, entity_name: str = "anima",
                    days: int = 3650) -> Tuple[str, str]:
    """(cert.pem, key.pem) under identity/tls/, generated via the
    system openssl on first need. Self-signed, trust-on-first-use:
    good enough for a home LAN, and the only path to iOS push."""
    d = tls_dir(root)
    cert_p = os.path.join(d, "cert.pem")
    key_p = os.path.join(d, "key.pem")
    if os.path.exists(cert_p) and os.path.exists(key_p):
        return cert_p, key_p
    if shutil.which("openssl") is None:
        raise RuntimeError(
            "TLS requested but no cert exists and `openssl` is not on "
            "PATH — install openssl, or place cert.pem + key.pem under "
            f"{d}/ yourself")
    os.makedirs(d, exist_ok=True)
    safe = "".join(ch for ch in (entity_name or "anima")
                   if ch.isalnum() or ch in "._-") or "anima"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec",
         "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-keyout", key_p, "-out", cert_p,
         "-days", str(days), "-nodes",
         "-subj", f"/CN={safe}",
         "-addext", f"subjectAltName=DNS:{safe}.local,DNS:localhost"],
        check=True, capture_output=True, timeout=30)
    os.chmod(key_p, 0o600)
    return cert_p, key_p


# ── the service worker ────────────────────────────────────────────────

SW_TEMPLATE = r"""/* __NAME__ — Observatory service worker (Phase 8a).
   The shell is cached; the entity is not. Every /api/ request rides
   the network — presence is never answered from a cache. */
"use strict";
const CACHE = "anima-shell-v1";
const SHELL = ["/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];

const OFFLINE_PAGE = `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ — unreachable</title><style>
body { background: radial-gradient(700px 400px at 50% 30%, #07202a,
       #030809 70%); color: #6d8a86; font-family: Georgia, serif;
       display: flex; align-items: center; justify-content: center;
       height: 100dvh; margin: 0; }
.box { text-align: center; border: 1px solid rgba(94,234,212,.14);
       border-radius: 14px; padding: 40px 44px; max-width: 340px; }
.box h1 { color: #c9dcd8; font-size: 16px; letter-spacing: .26em;
          font-weight: 400; }
.box p { font-style: italic; font-size: 13px; line-height: 1.7; }
.dome { color: #3c5450; }
</style></head><body><div class="box">
<h1><span class="dome">◉</span> UNREACHABLE</h1>
<p>__NAME__ is not answering — the entity is offline or you are off
its network. Nothing here pretends otherwise; try again when you can
reach it.</p></div></body></html>`;

self.addEventListener("install", ev => {
  ev.waitUntil(caches.open(CACHE)
    .then(c => c.addAll(SHELL))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", ev => {
  ev.waitUntil(caches.keys()
    .then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", ev => {
  const url = new URL(ev.request.url);
  if (url.origin !== location.origin) return;
  /* liveness is never faked: the API always rides the network */
  if (url.pathname.startsWith("/api/")) return;
  if (ev.request.mode === "navigate") {
    /* the page itself: network-first — an answer means a live entity;
       no answer means an honest offline screen, never a stale shell */
    ev.respondWith(fetch(ev.request).catch(() =>
      new Response(OFFLINE_PAGE, { status: 503,
        headers: { "Content-Type": "text/html; charset=utf-8" } })));
    return;
  }
  /* static shell: cache-first, refreshed in the background */
  ev.respondWith(caches.match(ev.request).then(hit => {
    const refresh = fetch(ev.request).then(res => {
      if (res && res.ok)
        caches.open(CACHE).then(c => c.put(ev.request, res.clone()));
      return res;
    });
    return hit || refresh;
  }));
});

self.addEventListener("push", ev => {
  let doc = {};
  try { doc = ev.data ? ev.data.json() : {}; }
  catch (e) { doc = { body: ev.data ? ev.data.text() : "" }; }
  const title = doc.title || "__NAME__";
  ev.waitUntil(self.registration.showNotification(title, {
    body: doc.body || "",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    data: { url: doc.url || "/" },
  }));
});

self.addEventListener("notificationclick", ev => {
  ev.notification.close();
  const url = (ev.notification.data && ev.notification.data.url) || "/";
  ev.waitUntil(clients.matchAll({ type: "window",
                                  includeUncontrolled: true })
    .then(list => {
      for (const c of list)
        if ("focus" in c) { c.navigate(url); return c.focus(); }
      return clients.openWindow(url);
    }));
});
"""


def render_sw(entity_name: str) -> str:
    """Fill the service-worker template (marker replace — the JS is
    full of braces, same discipline as render_page)."""
    safe = (entity_name or "anima").replace("<", "").replace(">", "")
    safe = (safe.replace("\\", "").replace('"', "").replace("'", "")
            .replace("`", "").replace("$", ""))
    return SW_TEMPLATE.replace("__NAME__", safe)
