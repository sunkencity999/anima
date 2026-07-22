"""Standalone CLI / demo for the routing contract layer.

Usage::

    python3 -m anima.routing probe --policy examples/policy.example.json \\
        --tier standard --prompt "Say hello in five words."

Runs a real request through the candidate chain and prints the attempt
audit (which candidates were tried, why each failed, who served, whether
the result is degraded).

Wrapping an existing harness (verification shim)
------------------------------------------------
This layer is deliberately importable on its own — no memory/wake
dependencies — so it can be bolted onto any harness's model call path
today, before Anima runs standalone:

1. **As the caller** — replace the harness's "call model, hope for the
   best" function with ``Router.complete(tier, messages, ...)``. The
   harness gets contract-verified responses and an honest ``degraded``
   flag for free.

2. **As a verifier only** — keep the harness's own routing, but pipe each
   candidate response through ``verify_response(...)`` before accepting
   it, and each provider error through ``classify_error(status, body)``
   before deciding retry-vs-failover. This is exactly the shim that would
   have made the three historical dist patches unnecessary: empty replies
   can never be "success", Anthropic-shaped 400 bodies classify as
   failover_next even without HTTP status context, and DeploymentNotFound
   can never be marked candidate_succeeded.

3. **Custom transport** — pass ``transport=`` a callable that adapts the
   harness's existing HTTP stack (auth, proxies, telemetry). The router
   only cares that it returns ``TransportResult`` or raises
   ``TransportError(status=..., body=...)``.
"""

from __future__ import annotations

import argparse
import json
import sys

from .policy import RoutingPolicy
from .router import Router, RoutingExhausted


def _print_attempts(attempts) -> None:
    print("\n── attempt audit ─────────────────────────────")
    for a in attempts:
        line = (f"  [{a.index}] {a.candidate} try#{a.try_number} "
                f"→ {a.outcome}")
        if a.reason and a.outcome != "ok":
            line += f" ({a.reason}"
            if a.decision:
                line += f" → {a.decision}"
            line += ")"
        line += f"  {a.latency_s:.2f}s"
        print(line)
        if a.detail:
            print(f"        {a.detail[:120]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m anima.routing",
        description="ANIMA routing contract layer — probe CLI",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    probe = sub.add_parser("probe", help="run one request through a tier")
    probe.add_argument("--policy", required=True, help="policy JSON path")
    probe.add_argument("--tier", default="standard")
    probe.add_argument("--prompt", required=True)
    probe.add_argument("--system", default=None)
    probe.add_argument("--json", action="store_true", dest="as_json",
                       help="print machine-readable result")
    args = parser.parse_args(argv)

    policy = RoutingPolicy.from_file(args.policy)
    router = Router(policy)
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.prompt})

    try:
        result = router.complete(args.tier, messages)
    except RoutingExhausted as e:
        print(f"✗ ROUTING EXHAUSTED for tier {e.tier!r}", file=sys.stderr)
        _print_attempts(e.attempts)
        return 1

    if args.as_json:
        print(json.dumps({
            "content": result.content,
            "model_used": result.model_used,
            "provider": result.provider,
            "degraded": result.degraded,
            "failover_events": result.failover_events,
            "attempts": [a.to_dict() for a in result.attempts],
        }, indent=2))
        return 0

    tag = "DEGRADED" if result.degraded else "primary"
    print(f"✓ served by {result.provider}/{result.model_used} ({tag})")
    _print_attempts(result.attempts)
    if result.failover_events:
        print("\n── failover events ───────────────────────────")
        for ev in result.failover_events:
            print(f"  {ev['from']} → {ev['to'] or '(exhausted)'}: "
                  f"{ev['reason']}")
    print("\n── content ───────────────────────────────────")
    print(result.content)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
