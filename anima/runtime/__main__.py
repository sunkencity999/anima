"""CLI: python3 -m anima.runtime --root <entity_root> [--policy routing.json]

Runs a RuntimeShell against an entity root. With --console (default when
stdin is a TTY and no --http), a console sense chat loop runs in the
foreground while the scheduler ticks in a background thread. With
--http, the HTTP sense (senses/http.json inside the root, or --http-config)
is started and the shell loop runs in the foreground until SIGTERM/SIGINT.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading

from .senses.console import ConsoleSense
from .shell import RuntimeShell


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m anima.runtime")
    ap.add_argument("--root", required=True, help="entity root directory")
    ap.add_argument("--policy", default=None,
                    help="routing policy JSON (default: "
                         "<root>/identity/routing.json via EntityRoot)")
    ap.add_argument("--tick", type=float, default=0.5,
                    help="scheduler tick seconds (default 0.5)")
    ap.add_argument("--console", action="store_true",
                    help="attach the console sense (default on a TTY)")
    ap.add_argument("--http", action="store_true",
                    help="attach the HTTP sense")
    ap.add_argument("--http-config", default=None,
                    help="path to http sense config JSON "
                         "(default <root>/senses/http.json)")
    ap.add_argument("--telegram", action="store_true",
                    help="attach the Telegram sense (long polling)")
    ap.add_argument("--telegram-config", default=None,
                    help="path to telegram sense config JSON "
                         "(default <root>/senses/telegram.json)")
    ap.add_argument("--web", action="store_true",
                    help="attach the Observatory web GUI sense")
    ap.add_argument("--web-config", default=None,
                    help="path to web sense config JSON "
                         "(default <root>/senses/web.json)")
    ap.add_argument("--bind", default=None,
                    help="override the web sense bind address "
                         "(e.g. 0.0.0.0 for LAN, 127.0.0.1 for loopback)")
    ap.add_argument("--web-port", type=int, default=None,
                    help="override the web sense port")
    ap.add_argument("--sender", default="operator",
                    help="person id for console messages")
    args = ap.parse_args(argv)

    shell = RuntimeShell(args.root, policy_path=args.policy,
                         tick_s=args.tick)
    if shell.router is None:
        print("warning: no routing policy found — running with the "
              "record-only default handler (no model turns)",
              file=sys.stderr)

    use_console = args.console or (
        sys.stdin.isatty() and not args.http and not args.telegram
        and not args.web)

    # A missing sense config degrades that sense, never the organism:
    # the body keeps running with the senses it has. (Learned the hard
    # way — a missing senses/http.json once crash-looped a live entity
    # for ten silent days.)
    if args.http:
        from .senses.http_sense import HttpSense
        cfg = args.http_config or os.path.join(
            os.path.abspath(args.root), "senses", "http.json")
        if os.path.exists(cfg):
            shell.add_sense("http", HttpSense(config_path=cfg))
        else:
            print(f"warning: --http requested but {cfg} is missing — "
                  f"continuing without the http sense "
                  f"(`anima init` scaffolds it)", file=sys.stderr)

    if args.telegram:
        from .senses.telegram_sense import TelegramSense
        cfg = args.telegram_config or os.path.join(
            os.path.abspath(args.root), "senses", "telegram.json")
        if os.path.exists(cfg):
            shell.add_sense("telegram", TelegramSense(config_path=cfg))
        else:
            print(f"warning: --telegram requested but {cfg} is missing — "
                  f"continuing without the telegram sense "
                  f"(`anima init` scaffolds it)", file=sys.stderr)

    if args.web:
        import json as _json
        from .senses.web_sense import WebSense
        cfg_path = args.web_config or os.path.join(
            os.path.abspath(args.root), "senses", "web.json")
        web_cfg = None
        if not os.path.exists(cfg_path):
            print(f"warning: --web requested but {cfg_path} is missing — "
                  f"continuing without the Observatory "
                  f"(`anima init` scaffolds it)", file=sys.stderr)
        else:
            with open(cfg_path, "r", encoding="utf-8") as f:
                web_cfg = _json.load(f)
    else:
        web_cfg = None
    if web_cfg is not None:
        if args.bind is not None:
            web_cfg["bind"] = args.bind
        if args.web_port is not None:
            web_cfg["port"] = args.web_port
        web = WebSense(web_cfg)
        shell.add_sense("web", web)
        host = web.bind if web.bind not in ("0.0.0.0", "") else _lan_ip()
        if web.auth == "open":
            print(f"observatory: http://{host}:{web.port}/  (open — "
                  f"anyone on the LAN can say hello)", file=sys.stderr)
        else:
            print(f"observatory: http://{host}:{web.port}/?token=… "
                  f"(token in the web sense config)", file=sys.stderr)

    if use_console:
        console = ConsoleSense(sender=args.sender)
        shell.add_sense("console", console)
        shell.start()
        # scheduler ticks in the background; chat loop owns the terminal
        loop = threading.Thread(target=_tick_loop, args=(shell,),
                                daemon=True)
        loop.start()
        try:
            console.run_interactive(shell)
        except (KeyboardInterrupt, EOFError):
            pass
        shell.stop()
        shell.shutdown()
        return 0

    shell.run()
    return 0


def _lan_ip() -> str:
    """Best-effort LAN address for the printed URL (display only —
    the socket never sends a packet; UDP connect just picks a route)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def _tick_loop(shell: RuntimeShell) -> None:
    while not shell.stopping:
        try:
            shell.run_pending_once()
        except Exception as exc:  # keep ticking; settle guard has the rest
            print(f"[shell] tick error: {exc}", file=sys.stderr)
        shell._stop.wait(shell.tick_s)


if __name__ == "__main__":
    raise SystemExit(main())
