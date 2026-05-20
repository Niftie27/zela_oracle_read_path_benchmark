#!/usr/bin/env python3
"""
Route test — verify whether `zela-route-by: static <label>` header
actually changes client-side latency from Prague.

Measures Prague-client end-to-end latency to a specified executor route.
Uses a persistent `requests.Session` so the per-run TLS handshake cost is
paid once at warm-up, not per call. Timings therefore reflect the
steady-state round-trip cost of an established connection to the chosen
Zela executor region, not just procedure execution.

Outputs:
  analysis/post_hoc/route_test_results/<route>_session.txt

Each file: N lines (default 100), one wall_clock_us per run.
First 5 are warmup (drop in analysis); use --runs to change total.
"""

import argparse
import os
import sys
import time
import json
import requests
import statistics
from pathlib import Path


def load_env():
    """Load .env file, walking up from script location until found."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            env = {}
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip().strip('"').strip("'")
            return env
    print("ERROR: .env not found in any parent directory", file=sys.stderr)
    sys.exit(1)


def fetch_jwt(key_id: str, key_secret: str) -> str:
    """Fetch executor JWT (same flow as orchestrator)."""
    resp = requests.post(
        "https://auth.zela.io/realms/zela/protocol/openid-connect/token",
        auth=(key_id, key_secret),
        data={
            "grant_type": "client_credentials",
            "scope": "zela-executor:call",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def run_test(session: requests.Session, route_label: str, procedure: str,
             revision: str, runs: int = 100) -> list[int]:
    """
    Run `runs` procedure calls with the given route header,
    using the persistent session (no per-call TLS handshake).
    Returns list of wall_clock_us per call.
    """
    headers = {"zela-route-by": f"static {route_label}"} if route_label else {}
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": f"zela.{procedure}#{revision}",
        "params": None,
    }
    times = []
    for i in range(runs):
        t0 = time.perf_counter_ns()
        resp = session.post(
            "https://executor.zela.io",
            json=body,
            headers=headers,
            timeout=30,
        )
        t1 = time.perf_counter_ns()
        # Drain body but DON'T include parse in timing
        _ = resp.content
        # TODO(M6): check resp.raise_for_status() and JSON-RPC "error"
        # before appending timing — currently failed executor responses
        # could be silently recorded as latency samples. The committed
        # M5 route-test results show no anomalous high-latency outliers
        # in the timing distribution, but the script cannot independently
        # verify response success from timing alone.
        wall_clock_us = (t1 - t0) // 1000
        times.append(wall_clock_us)
        time.sleep(1.0)  # match cron sleep for comparable noise floor
    return times


def main():
    ap = argparse.ArgumentParser(description="Per-region static-routing latency test")
    ap.add_argument("--route", required=True,
                    choices=["fr2", "dx1", "ewr", "slc", "tyo", "auto"],
                    help="Region to test (use 'auto' for default leader-aware routing)")
    ap.add_argument("--runs", type=int, default=100,
                    help="Number of runs (default: 100; first 5 are warmup)")
    args = ap.parse_args()

    env = load_env()
    key_id = env.get("ZELA_KEY_ID")
    key_secret = env.get("ZELA_KEY_SECRET")
    procedure = env.get("ZELA_PROCEDURE")
    revision = env.get("ZELA_PROCEDURE_REVISION")

    missing = [k for k, v in [
        ("ZELA_KEY_ID", key_id),
        ("ZELA_KEY_SECRET", key_secret),
        ("ZELA_PROCEDURE", procedure),
        ("ZELA_PROCEDURE_REVISION", revision),
    ] if not v]
    if missing:
        print(f"ERROR: missing env vars: {missing}", file=sys.stderr)
        sys.exit(1)

    print("Fetching JWT...")
    jwt = fetch_jwt(key_id, key_secret)
    print("OK")

    session = requests.Session()
    session.headers.update({
        "authorization": f"Bearer {jwt}",
        "content-type": "application/json",
    })

    repo_root = Path(__file__).resolve().parent.parent.parent
    out_dir = repo_root / "analysis" / "post_hoc" / "route_test_results"
    out_dir.mkdir(exist_ok=True)

    route_label = None if args.route == "auto" else args.route
    print(f"\nRunning {args.route} (label={route_label!r}), {args.runs} runs (first 5 = warmup)...")
    times = run_test(session, route_label, procedure, revision, runs=args.runs)
    out_file = out_dir / f"{args.route}_session.txt"
    with open(out_file, "w") as f:
        for t in times:
            f.write(f"{t}\n")
    warm = times[5:]
    warm_sorted = sorted(warm)
    p50 = warm_sorted[len(warm) // 2]
    p95_idx = max(0, int(len(warm) * 0.95) - 1)
    p95 = warm_sorted[p95_idx]
    print(f"  warm n={len(warm)}  p50={p50/1000:.2f}ms  p95={p95/1000:.2f}ms  "
          f"min={min(warm)/1000:.2f}ms  max={max(warm)/1000:.2f}ms")
    print(f"  -> {out_file}")
    print("\nResult saved.")


if __name__ == "__main__":
    main()
