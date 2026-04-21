#!/usr/bin/env python3
"""Orchestrate paired Zela procedure / baseline client runs and write CSV."""
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

FEED_COLUMNS = [
    "run_id", "timestamp_ms", "side", "symbol", "pubkey",
    "account_found", "account_data_len", "context_slot",
    "wall_clock_elapsed_us", "error",
]
AGGREGATE_COLUMNS = [
    "run_id", "timestamp_ms", "side", "feed_count",
    "wall_clock_start_ms", "wall_clock_end_ms",
    "wall_clock_total_us", "unique_slots_count", "error",
]


def fetch_jwt(key_id, key_secret):
    resp = requests.post(
        AUTH_URL,
        auth=(key_id, key_secret),
        data={"grant_type": "client_credentials", "scope": "zela-executor:call"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def call_zela(token, procedure, revision):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": f"zela.{procedure}#{revision}",
        "params": None,
    }
    return requests.post(
        EXECUTOR_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )


def invoke_zela(token_holder, key_id, key_secret, procedure, revision):
    """Returns (parsed_output_dict_or_None, error_str_or_None). Refreshes JWT on 401."""
    try:
        resp = call_zela(token_holder[0], procedure, revision)
        if resp.status_code == 401:
            token_holder[0] = fetch_jwt(key_id, key_secret)
            resp = call_zela(token_holder[0], procedure, revision)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
        body = resp.json()
        if "error" in body:
            return None, f"JSON-RPC error: {body['error']}"
        if "result" not in body:
            return None, f"missing result: {body}"
        return body["result"], None
    except Exception as e:
        return None, f"zela exception: {e}"


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


def side_summary(side, output, err):
    if err or not output:
        return f"{side}: ERROR"
    total = output["aggregate"]["wall_clock_total_us"]
    unique = len({f["context_slot"] for f in output["feeds"]})
    slot_word = "slot" if unique == 1 else "slots"
    if side == "zela":
        return f"zela: {total}µs ({unique} {slot_word})"
    ms = total / 1000.0
    return f"baseline: {ms:.0f}ms ({unique} {slot_word})"


def write_side_rows(output, run_id, side, err, feeds_w, agg_w):
    if err or not output:
        agg_w.writerow({
            "run_id": run_id,
            "timestamp_ms": int(time.time() * 1000),
            "side": side,
            "feed_count": "",
            "wall_clock_start_ms": "",
            "wall_clock_end_ms": "",
            "wall_clock_total_us": "",
            "unique_slots_count": "",
            "error": "true",
        })
        return

    agg = output["aggregate"]
    ts_ms = agg["wall_clock_start_ms"]
    feeds = output["feeds"]
    unique = len({f["context_slot"] for f in feeds})

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
            "wall_clock_elapsed_us": f["wall_clock_elapsed_us"],
            "error": "false",
        })

    agg_w.writerow({
        "run_id": run_id,
        "timestamp_ms": ts_ms,
        "side": side,
        "feed_count": agg["feed_count"],
        "wall_clock_start_ms": agg["wall_clock_start_ms"],
        "wall_clock_end_ms": agg["wall_clock_end_ms"],
        "wall_clock_total_us": agg["wall_clock_total_us"],
        "unique_slots_count": unique,
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
        token = fetch_jwt(key_id, key_secret)
    except Exception as e:
        print(f"error: JWT fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
    token_holder = [token]

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
            z_out, z_err = invoke_zela(
                token_holder, key_id, key_secret, procedure, revision,
            )
            write_side_rows(z_out, run_id, "zela", z_err, feeds_w, agg_w)
            ff.flush()
            af.flush()
            time.sleep(args.sleep)

            b_out, b_err = invoke_baseline(BASELINE_BIN)
            write_side_rows(b_out, run_id, "baseline", b_err, feeds_w, agg_w)
            ff.flush()
            af.flush()

            elapsed = format_elapsed(time.monotonic() - t0)
            z_summary = side_summary("zela", z_out, z_err)
            b_summary = side_summary("baseline", b_out, b_err)
            print(
                f"Run {run_id}/{args.runs} | {z_summary} | {b_summary} | elapsed: {elapsed}",
                file=sys.stderr,
            )

            if run_id < args.runs:
                time.sleep(args.sleep)

    print(f"wrote {feeds_path} and {agg_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
