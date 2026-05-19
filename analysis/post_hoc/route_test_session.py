#!/usr/bin/env python3
"""
Route test — verify whether `zela-route-by: static <label>` header
actually changes client-side latency from Prague.

Uses requests.Session() to reuse TCP/TLS connection across calls
(same pattern as orchestrate.py), so each measurement reflects
ONLY procedure execution, NOT TLS handshake overhead.

Outputs:
  ./route_test_results/fr2_session.txt
  ./route_test_results/auto_session.txt
  ./route_test_results/tyo_session.txt
  ./route_test_results/ewr_session.txt    (new: Newark)
  ./route_test_results/dx1_session.txt    (new: Dubai)
  ./route_test_results/slc_session.txt    (new: Salt Lake City)

Each file: 25 lines, one wall_clock_us per run.
First 5 are warmup (drop in analysis).
"""

import os
import sys
import time
import json
import requests
import statistics
from pathlib import Path


def load_env():
    """Load .env file from repo root."""
    repo_root = Path(__file__).parent.resolve()
    env_path = repo_root / ".env"
    if not env_path.exists():
        print(f"ERROR: .env not found at {env_path}", file=sys.stderr)
        sys.exit(1)

    env = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


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
             revision: str, runs: int = 25) -> list[int]:
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
        wall_clock_us = (t1 - t0) // 1000
        times.append(wall_clock_us)
        time.sleep(1.0)  # match cron sleep for comparable noise floor
    return times


def main():
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

    out_dir = Path("./route_test_results")
    out_dir.mkdir(exist_ok=True)

    # auto = no route-by header (default leader-aware)
    routes = [
        ("auto", None),       # default leader-aware routing
        ("fr2", "fr2"),       # Frankfurt
        ("tyo", "tyo"),       # Tokyo
        ("ewr", "ewr"),       # Newark NJ
        ("dx1", "dx1"),       # Dubai
        ("slc", "slc"),       # Salt Lake City
    ]

    print(f"\nRunning {len(routes)} tests, 25 runs each (first 5 = warmup)...")
    print(f"Total: {len(routes) * 25} runs, ~{len(routes) * 25} seconds")
    print()

    for name, label in routes:
        print(f"=== {name} (label={label!r}) ===")
        times = run_test(session, label, procedure, revision, runs=25)
        out_file = out_dir / f"{name}_session.txt"
        with open(out_file, "w") as f:
            for t in times:
                f.write(f"{t}\n")
        # Quick summary, warm = last 20
        warm = times[5:]
        warm_sorted = sorted(warm)
        p50 = warm_sorted[len(warm) // 2]
        p95_idx = max(0, int(len(warm) * 0.95) - 1)
        p95 = warm_sorted[p95_idx]
        print(f"  warm n={len(warm)}  p50={p50/1000:.2f}ms  p95={p95/1000:.2f}ms  "
              f"min={min(warm)/1000:.2f}ms  max={max(warm)/1000:.2f}ms")
        print(f"  -> {out_file}")
        print()

    print("All results saved to ./route_test_results/")
    print("Run again or upload to chat for analysis.")


if __name__ == "__main__":
    main()
