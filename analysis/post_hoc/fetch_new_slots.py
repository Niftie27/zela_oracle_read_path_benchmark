#!/usr/bin/env python3
"""
Fetch slot leaders that are NEEDED by datasets but entirely MISSING from
leader_cache.json (not null — just absent keys).

fill_missing_slot_leaders.py only refills null entries. This one finds
slots referenced by zela runs that have no key at all in the cache, and
fetches them.

Run from repo root: python3 fetch_new_slots.py
"""

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
    repo_root = Path(".").resolve()
    out_dir = repo_root / "leader_correlation_results"
    leader_cache_file = out_dir / "leader_cache.json"

    leader_cache = json.loads(leader_cache_file.read_text())
    cache_slots = set(int(k) for k in leader_cache.keys())
    print(f"Leader cache: {len(cache_slots)} slots")

    # Collect all slots needed by zela runs
    needed = set()
    datasets = sorted(repo_root.glob("zela_datasets/dataset_2026_05_*"))
    for ds in datasets:
        feeds = ds / "feeds.csv"
        if not feeds.exists():
            continue
        with open(feeds) as f:
            for row in csv.DictReader(f):
                if row["side"] == "zela":
                    needed.add(int(row["context_slot"]))
    print(f"Needed slots: {len(needed)}")

    missing = sorted(needed - cache_slots)
    print(f"Missing from cache: {len(missing)}")
    if not missing:
        print("Nothing to fetch.")
        return

    # Fetch in batches. For each missing slot, one getSlotLeaders(slot, 50)
    # call covers it + 49 following. Skip ahead past covered slots.
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

    # Verify coverage
    cache_slots = set(int(k) for k in leader_cache.keys())
    still_missing = sorted(needed - cache_slots)
    print(f"Still missing: {len(still_missing)}")
    if still_missing:
        print(f"  (first 10: {still_missing[:10]})")
    else:
        print("  100% coverage — every zela run slot now has a leader.")
    print("\nNext: re-run leader_correlation_v3.py > stdout_v5.txt 2>&1")


if __name__ == "__main__":
    main()
