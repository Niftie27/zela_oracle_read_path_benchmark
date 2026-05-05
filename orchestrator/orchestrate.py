#!/usr/bin/env python3
"""Orchestrate paired Zela procedure / baseline client runs and write CSV.

Batch v2 schema: getMultipleAccounts on both sides, end-to-end client_wall_clock
measured from the orchestrator (Session.post) for Zela, in addition to the
server-side wall_clock_total_us reported by the procedure.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

AUTH_URL = "https://auth.zela.io/realms/zela/protocol/openid-connect/token"
EXECUTOR_URL = "https://executor.zela.io"

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
BASELINE_BIN = WORKSPACE_ROOT / "target" / "release" / "baseline_client"

COMMITMENT = "confirmed"
COLD_START_RUNS = 5

FEED_COLUMNS = [
    "run_id", "timestamp_ms", "side", "symbol", "pubkey",
    "account_found", "account_data_len", "context_slot",
    "cold_start", "commitment", "error",
]
AGGREGATE_COLUMNS = [
    "run_id", "timestamp_ms", "side", "feed_count",
    "wall_clock_start_ms", "wall_clock_end_ms",
    "server_wall_clock_us", "client_wall_clock_us",
    "unique_slots_count", "cold_start", "commitment", "error",
]


def fetch_jwt(key_id, key_secret):
    """Returns (access_token, expires_at_monotonic_seconds)."""
    resp = requests.post(
        AUTH_URL,
        auth=(key_id, key_secret),
        data={"grant_type": "client_credentials", "scope": "zela-executor:call"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    expires_in = int(body.get("expires_in", 300))
    return body["access_token"], time.monotonic() + expires_in


def set_session_token(session, token):
    session.headers["Authorization"] = f"Bearer {token}"


def call_zela(session, procedure, revision):
    """Posts the JSON-RPC request and returns (response, client_wall_clock_us).

    The timing bracket wraps Session.post() only — JSON parsing happens outside.
    """
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": f"zela.{procedure}#{revision}",
        "params": None,
    }
    t0 = time.perf_counter_ns()
    resp = session.post(EXECUTOR_URL, json=body, timeout=60)
    t1 = time.perf_counter_ns()
    return resp, (t1 - t0) // 1000


def invoke_zela(session, token_holder, key_id, key_secret, procedure, revision):
    """Returns (parsed_output, client_wall_clock_us, error). Refreshes JWT on 401."""
    try:
        resp, client_us = call_zela(session, procedure, revision)
        if resp.status_code == 401:
            token, expires_at = fetch_jwt(key_id, key_secret)
            token_holder[0] = token
            token_holder[1] = expires_at
            set_session_token(session, token)
            resp, client_us = call_zela(session, procedure, revision)
        if resp.status_code != 200:
            return None, client_us, f"HTTP {resp.status_code}: {resp.text[:200]}"
        body = resp.json()
        if "error" in body:
            return None, client_us, f"JSON-RPC error: {body['error']}"
        if "result" not in body:
            return None, client_us, f"missing result: {body}"
        return body["result"], client_us, None
    except Exception as e:
        return None, None, f"zela exception: {e}"


def invoke_baseline(binary_path):
    try:
        proc = subprocess.run(
            [str(binary_path)],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if proc.returncode != 0:
            return None, f"baseline exit {proc.returncode}: {proc.stderr[:200]}"
        return json.loads(proc.stdout), None
    except Exception as e:
        return None, f"baseline exception: {e}"


def format_elapsed(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def side_summary(side, output, err, client_us):
    if err or not output:
        return f"{side}: ERROR"
    server_us = output["aggregate"]["wall_clock_total_us"]
    unique = len({f["context_slot"] for f in output["feeds"]})
    slot_word = "slot" if unique == 1 else "slots"
    if side == "zela":
        return f"zela: server {server_us}µs / client {client_us}µs ({unique} {slot_word})"
    ms = server_us / 1000.0
    return f"baseline: {ms:.0f}ms ({unique} {slot_word})"


def write_side_rows(output, run_id, side, err, feeds_w, agg_w,
                    cold_start, client_wall_clock_us):
    cold_str = "true" if cold_start else "false"
    if err or not output:
        agg_w.writerow({
            "run_id": run_id,
            "timestamp_ms": int(time.time() * 1000),
            "side": side,
            "feed_count": "",
            "wall_clock_start_ms": "",
            "wall_clock_end_ms": "",
            "server_wall_clock_us": "",
            "client_wall_clock_us": "" if client_wall_clock_us is None else client_wall_clock_us,
            "unique_slots_count": "",
            "cold_start": cold_str,
            "commitment": COMMITMENT,
            "error": "true",
        })
        return

    agg = output["aggregate"]
    ts_ms = agg["wall_clock_start_ms"]
    feeds = output["feeds"]
    unique = len({f["context_slot"] for f in feeds})
    server_us = agg["wall_clock_total_us"]
    # Baseline reuses its own self-measurement for client_wall_clock; server side is N/A.
    if side == "baseline":
        server_out = ""
        client_out = server_us
    else:
        server_out = server_us
        client_out = "" if client_wall_clock_us is None else client_wall_clock_us

    for f in feeds:
        feeds_w.writerow({
            "run_id": run_id,
            "timestamp_ms": ts_ms,
            "side": side,
            "symbol": f["symbol"],
            "pubkey": f["pubkey"],
            "account_found": str(f["account_found"]).lower(),
            "account_data_len": f["account_data_len"],
            "context_slot": f["context_slot"],
            "cold_start": cold_str,
            "commitment": COMMITMENT,
            "error": "false",
        })

    agg_w.writerow({
        "run_id": run_id,
        "timestamp_ms": ts_ms,
        "side": side,
        "feed_count": agg["feed_count"],
        "wall_clock_start_ms": agg["wall_clock_start_ms"],
        "wall_clock_end_ms": agg["wall_clock_end_ms"],
        "server_wall_clock_us": server_out,
        "client_wall_clock_us": client_out,
        "unique_slots_count": unique,
        "cold_start": cold_str,
        "commitment": COMMITMENT,
        "error": "false",
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--output-dir", type=str, default="data")
    args = ap.parse_args()

    key_id = os.environ.get("ZELA_KEY_ID")
    key_secret = os.environ.get("ZELA_KEY_SECRET")
    procedure = os.environ.get("ZELA_PROCEDURE")
    revision = os.environ.get("ZELA_PROCEDURE_REVISION")
    baseline_url = os.environ.get("BASELINE_RPC_URL")

    missing = [
        name for name, val in [
            ("ZELA_KEY_ID", key_id),
            ("ZELA_KEY_SECRET", key_secret),
            ("ZELA_PROCEDURE", procedure),
            ("ZELA_PROCEDURE_REVISION", revision),
            ("BASELINE_RPC_URL", baseline_url),
        ] if not val
    ]
    if missing:
        print(f"error: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    if not BASELINE_BIN.exists():
        print(f"error: baseline binary not found at {BASELINE_BIN}", file=sys.stderr)
        print("build it first: cargo build --release -p baseline_client", file=sys.stderr)
        sys.exit(1)

    try:
        token, expires_at = fetch_jwt(key_id, key_secret)
    except Exception as e:
        print(f"error: JWT fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
    token_holder = [token, expires_at]

    # Pre-window JWT refresh: if remaining lifetime is less than 2× expected window
    # duration (≈ 2 × runs × sleep × 2 sides), refresh before the first measurement
    # so the bracket never accidentally captures a 401 retry.
    expected_window_s = 2 * args.runs * args.sleep * 2
    remaining_s = token_holder[1] - time.monotonic()
    if remaining_s < 2 * expected_window_s:
        try:
            print(
                f"jwt remaining {remaining_s:.0f}s < 2×window {2*expected_window_s:.0f}s; refreshing pre-window",
                file=sys.stderr,
            )
            token, expires_at = fetch_jwt(key_id, key_secret)
            token_holder[0] = token
            token_holder[1] = expires_at
        except Exception as e:
            print(f"warning: pre-window JWT refresh failed: {e}", file=sys.stderr)

    # Reuse a single requests.Session() for all Zela calls (matches reqwest::Client
    # default pooling on the baseline side). Auth header lives on the session and is
    # rotated by set_session_token() whenever the JWT refreshes.
    session = requests.Session()
    session.headers["Content-Type"] = "application/json"
    set_session_token(session, token_holder[0])

    output_base = Path(args.output_dir)
    if not output_base.is_absolute():
        output_base = WORKSPACE_ROOT / output_base
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_base / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    feeds_path = run_dir / "feeds.csv"
    agg_path = run_dir / "aggregates.csv"

    t0 = time.monotonic()
    with open(feeds_path, "w", newline="") as ff, open(agg_path, "w", newline="") as af:
        feeds_w = csv.DictWriter(ff, fieldnames=FEED_COLUMNS)
        agg_w = csv.DictWriter(af, fieldnames=AGGREGATE_COLUMNS)
        feeds_w.writeheader()
        agg_w.writeheader()

        for run_id in range(1, args.runs + 1):
            cold = run_id <= COLD_START_RUNS

            z_out, z_client_us, z_err = invoke_zela(
                session, token_holder, key_id, key_secret, procedure, revision,
            )
            write_side_rows(z_out, run_id, "zela", z_err, feeds_w, agg_w,
                            cold_start=cold, client_wall_clock_us=z_client_us)
            ff.flush()
            af.flush()
            time.sleep(args.sleep)

            b_out, b_err = invoke_baseline(BASELINE_BIN)
            write_side_rows(b_out, run_id, "baseline", b_err, feeds_w, agg_w,
                            cold_start=cold, client_wall_clock_us=None)
            ff.flush()
            af.flush()

            elapsed = format_elapsed(time.monotonic() - t0)
            z_summary = side_summary("zela", z_out, z_err, z_client_us)
            b_summary = side_summary("baseline", b_out, b_err, None)
            print(
                f"Run {run_id}/{args.runs} | {z_summary} | {b_summary} | elapsed: {elapsed}",
                file=sys.stderr,
            )

            if run_id < args.runs:
                time.sleep(args.sleep)

    print(f"wrote {feeds_path} and {agg_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
