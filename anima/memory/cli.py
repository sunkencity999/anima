"""CLI for the ANIMA memory engine.

Usage: python3 -m anima.memory <command> [options]

Commands operate on an entity root directory (default ./entity):
    remember     add an episodic event
    recall       hybrid recall → markdown context pack on stdout
    settle       write a wake report (JSON via --report-file or stdin)
    consolidate  drain the consolidation queue (--dry-run = heuristic only)
    stats        store counts as JSON
"""

from __future__ import annotations

import argparse
import json
import sys

from .store import MemoryStore
from .recall import recall as do_recall
from .settle import settle as do_settle
from .consolidate import run_consolidation, DEFAULT_ENDPOINT, DEFAULT_MODEL


def _add_root(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", default="./entity",
                   help="entity root directory (default ./entity)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m anima.memory",
        description="ANIMA memory engine (episodic/semantic/procedural)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("remember", help="add an episodic event")
    _add_root(p)
    p.add_argument("summary", help="one-line summary of the event")
    p.add_argument("--detail", default="", help="longer detail text")
    p.add_argument("--kind", default="event",
                   help="event|decision|learning|drive|... (default event)")
    p.add_argument("--actors", default="", help="comma-separated actor names")
    p.add_argument("--tags", default="", help="comma-separated tags")
    p.add_argument("--wake-id", default=None)

    p = sub.add_parser("recall", help="hybrid recall → markdown pack")
    _add_root(p)
    p.add_argument("query")
    p.add_argument("--actors", default="", help="comma-separated actor filter")
    p.add_argument("--tags", default="", help="comma-separated tag filter")
    p.add_argument("--budget", type=int, default=1500, help="token budget")
    p.add_argument("--max-items", type=int, default=12)

    p = sub.add_parser("settle", help="write a wake report (JSON)")
    _add_root(p)
    p.add_argument("--report-file", default="-",
                   help="path to wake-report JSON, or - for stdin (default)")

    p = sub.add_parser("consolidate", help="drain consolidation queue")
    _add_root(p)
    p.add_argument("--dry-run", action="store_true",
                   help="heuristic engine only, no model endpoint")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--stale-after-days", type=float, default=None,
                   help="also flag beliefs unconfirmed for N days as stale")

    p = sub.add_parser("stats", help="store counts as JSON")
    _add_root(p)

    return parser


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with MemoryStore(args.root) as store:
        if args.command == "remember":
            eid = store.add_episode(
                summary=args.summary, detail=args.detail, kind=args.kind,
                actors=_csv(args.actors), tags=_csv(args.tags),
                wake_id=args.wake_id)
            print(json.dumps({"episode_id": eid}))

        elif args.command == "recall":
            print(do_recall(
                store, args.query,
                actors=_csv(args.actors) or None,
                tags=_csv(args.tags) or None,
                token_budget=args.budget, max_items=args.max_items))

        elif args.command == "settle":
            if args.report_file == "-":
                raw = sys.stdin.read()
            else:
                with open(args.report_file, "r", encoding="utf-8") as fh:
                    raw = fh.read()
            receipt = do_settle(store, json.loads(raw))
            print(json.dumps(receipt, indent=2))

        elif args.command == "consolidate":
            report = run_consolidation(
                store, dry_run=args.dry_run, endpoint=args.endpoint,
                model=args.model, limit=args.limit,
                stale_after_days=args.stale_after_days)
            print(json.dumps(report, indent=2))

        elif args.command == "stats":
            print(json.dumps(store.stats(), indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
