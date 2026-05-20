#!/usr/bin/env python3
"""
Fetch slot leaders that are NEEDED by datasets but entirely MISSING from
leader_cache.json (not null — just absent keys).

Reads only the datasets listed in the manifest so that post-M5 datasets
do not contaminate the frozen M5 cache priming.

For each run's context_slot, fetches the leader at context_slot + offset
for each offset in --offsets (default: 0,1 — covering the report's two
headline offsets). This ensures that a cold-cache reproduction of the
offset-+1 analysis reaches the full 2,571 known-leader count.

Run from repo root:
  python3 analysis/post_hoc/fetch_new_slots.py
  python3 analysis/post_hoc/fetch_new_slots.py --manifest analysis/m5_manifest.txt --offsets=-2,-1,0,1,2,3

Note: use --offsets=... (with equals sign) when passing negative offsets, so
argparse does not interpret the leading dash as a flag.
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

SOLANA_RPC = "https://api.mainnet-beta.solana.com"
BATCH_SIZE = 50


def fetch_leader_batch(start_slot, limit, session):
    body = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getSlotLeaders",
        "params": [start_slot, limit],
    }
    try:
        resp = session.post(SOLANA_RPC, json=body, timeout=30)
        if resp.status_code == 429:
            print(f"  429 at slot {start_slot}, sleeping 30s...", file=sys.stderr)
            time.sleep(30)
            resp = session.post(SOLANA_RPC, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "result" in data and data["result"]:
            return data["result"]
        if "error" in data:
            print(f"  RPC error at {start_slot}: {data['error']}", file=sys.stderr)
    except Exception as e:
        print(f"  Exception at {start_slot}: {e}", file=sys.stderr)
    return None


def main():
    ap = argparse.ArgumentParser(
        description="Prime leader_cache.json for all manifest datasets and offsets"
    )
    ap.add_argument("--manifest", default="analysis/m5_manifest.txt",
                    help="Dataset manifest (default: analysis/m5_manifest.txt)")
    ap.add_argument("--offsets", default="0,1",
                    help="Comma-separated slot offsets to prime (default: 0,1). "
                         "Use --offsets=... with equals sign when passing negative values, "
                         "e.g. --offsets=-2,-1,0,1,2,3")
    args = ap.parse_args()

    offsets = [int(x.strip()) for x in args.offsets.split(",")]

    repo_root = Path(".").resolve()
    out_dir = repo_root / "leader_correlation_results"
    leader_cache_file = out_dir / "leader_cache.json"

    leader_cache = json.loads(leader_cache_file.read_text())
    cache_slots = set(int(k) for k in leader_cache.keys())
    print(f"Leader cache: {len(cache_slots)} slots")

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)
    manifest_entries = [
        l.strip() for l in manifest_path.read_text().splitlines()
        if l.strip() and not l.startswith("#")
    ]
    datasets_dir = repo_root / "zela_datasets"
    datasets = [datasets_dir / name for name in manifest_entries
                if (datasets_dir / name / "feeds.csv").exists()]
    print(f"Loaded {len(datasets)}/{len(manifest_entries)} manifest datasets")

    # Collect all target slots: context_slot + each offset
    needed = set()
    for ds in datasets:
        feeds = ds / "feeds.csv"
        with open(feeds) as f:
            for row in csv.DictReader(f):
                if row["side"] == "zela":
                    base = int(row["context_slot"])
                    for off in offsets:
                        needed.add(base + off)
    print(f"Target slots across offsets {offsets}: {len(needed)}")

    missing = sorted(needed - cache_slots)
    print(f"Missing from cache: {len(missing)}")
    if not missing:
        print("Nothing to fetch.")
        return

    session = requests.Session()
    fetched = 0
    i = 0
    saved_at = 0
    while i < len(missing):
        start = missing[i]
        leaders = fetch_leader_batch(start, BATCH_SIZE, session)
        if leaders:
            for offset, leader in enumerate(leaders):
                slot = start + offset
                if leader:
                    leader_cache[str(slot)] = leader
                    fetched += 1
            covered_end = start + BATCH_SIZE
            while i < len(missing) and missing[i] < covered_end:
                i += 1
        else:
            i += 1
        if fetched - saved_at >= 200:
            leader_cache_file.write_text(json.dumps(leader_cache))
            saved_at = fetched
            print(f"  saved, fetched={fetched}, i={i}/{len(missing)}")
        time.sleep(0.5)

    leader_cache_file.write_text(json.dumps(leader_cache))
    print(f"\nFinal: {len(leader_cache)} slots in cache (added {fetched} this run)")

    cache_slots = set(int(k) for k in leader_cache.keys())
    still_missing = sorted(needed - cache_slots)
    print(f"Still missing: {len(still_missing)}")
    if still_missing:
        print(f"  (first 10: {still_missing[:10]})")
    else:
        print("  100% coverage — every target slot now has a leader.")
    print("\nNext: python3 analysis/post_hoc/fill_missing_validators.py")
    print("Then: python3 analysis/post_hoc/leader_correlation_v3.py --slot-offset 1")


if __name__ == "__main__":
    main()
