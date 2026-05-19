#!/usr/bin/env python3
"""
Fill missing validators in validator_cache.json.

Previous run captured leader pubkey for all 3126 slots but
validators.app fetched only ~36 entries before rate-limiting.

This script:
  1. Loads leader_cache.json → unique pubkeys
  2. Loads validator_cache.json → known pubkeys
  3. Identifies missing pubkeys (= leader pubkeys not yet in validator cache)
  4. Fetches missing entries from validators.app with conservative rate limit
  5. Saves updated validator_cache.json
  6. Re-runs cross-correlation with full data

Conservative pacing: 1.5s between requests to avoid 429.
Cache is saved every 25 fetches, so partial progress is preserved.

Run from repo root: python3 fill_missing_validators.py
"""

import csv
import json
import sys
import time
from pathlib import Path
from collections import defaultdict

import requests


VALIDATORS_APP_BASE = "https://www.validators.app/api/v1"

# IMPORTANT: validators.app requires Token header for production use.
# Without it you may get rate-limited aggressively or 401s.
# Sign up free at https://www.validators.app/users/sign_up for higher limits.
# Set token via: export VALIDATORS_APP_TOKEN="your_token"
import os
TOKEN = os.environ.get("VALIDATORS_APP_TOKEN", "")


def fetch_validator(pubkey: str, cache: dict, session: requests.Session) -> dict:
    """Fetch validator info from validators.app."""
    if pubkey in cache:
        return cache[pubkey]
    if pubkey is None:
        return None
    url = f"{VALIDATORS_APP_BASE}/validators/mainnet/{pubkey}.json"
    headers = {"Token": TOKEN} if TOKEN else {}
    try:
        resp = session.get(url, headers=headers, timeout=20)
        if resp.status_code == 404:
            # validator not in validators.app database
            cache[pubkey] = None
            return None
        if resp.status_code == 429:
            # rate-limited; wait longer and retry once
            print(f"  RATE LIMIT for {pubkey[:10]}, sleeping 30s...", file=sys.stderr)
            time.sleep(30)
            resp = session.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        info = {
            "name": data.get("name"),
            "city": data.get("data_center_host") or data.get("data_center_key") or "",
            "country": (data.get("ip_country_iso") or data.get("country") or "").upper(),
            "asn": data.get("autonomous_system_number"),
        }
        cache[pubkey] = info
        return info
    except Exception as e:
        print(f"  ERROR for {pubkey[:10]}: {e}", file=sys.stderr)
        cache[pubkey] = None
        return None


def main():
    repo_root = Path(".").resolve()
    out_dir = repo_root / "leader_correlation_results"

    leader_cache_file = out_dir / "leader_cache.json"
    validator_cache_file = out_dir / "validator_cache.json"

    leader_cache = json.loads(leader_cache_file.read_text())
    leader_cache = {int(k): v for k, v in leader_cache.items() if v}
    validator_cache = json.loads(validator_cache_file.read_text())

    print(f"Leader cache: {len(leader_cache)} slots")
    print(f"Validator cache (existing): {len(validator_cache)} entries")

    # All unique leader pubkeys across all slots
    unique_leaders = set(leader_cache.values())
    print(f"Unique leader pubkeys in leader_cache: {len(unique_leaders)}")

    # Missing = leaders we haven't yet fetched
    missing = sorted([p for p in unique_leaders if p not in validator_cache])
    print(f"Missing from validator_cache: {len(missing)}")

    if not missing:
        print("Nothing to fetch! Cache is complete.")
        return

    if not TOKEN:
        print("\n  WARNING: VALIDATORS_APP_TOKEN env var not set.")
        print("  Without a token, you may hit aggressive rate limits.")
        print("  Sign up free at https://www.validators.app/users/sign_up")
        print("  Then: export VALIDATORS_APP_TOKEN=\"your_token\"")
        print("  Proceeding anyway with 1.5s sleep between requests...\n")

    print(f"\nFetching {len(missing)} missing validators with 1.5s sleep...")
    estimated_seconds = len(missing) * 1.5
    print(f"Estimated time: ~{estimated_seconds/60:.1f} min")
    print("Cache saves every 25 fetches.\n")

    session = requests.Session()
    fetched = 0
    for i, pubkey in enumerate(missing):
        info = fetch_validator(pubkey, validator_cache, session)
        fetched += 1
        status = "ok" if info else "(none)"
        print(f"  [{i+1}/{len(missing)}] {pubkey[:12]} → {status}")
        if fetched % 25 == 0:
            validator_cache_file.write_text(json.dumps(validator_cache, indent=2))
            print(f"  ★ saved cache ({len(validator_cache)} total entries)")
        time.sleep(1.5)

    validator_cache_file.write_text(json.dumps(validator_cache, indent=2))
    print(f"\n  Final cache: {len(validator_cache)} entries (added {fetched})")
    print(f"  Saved to {validator_cache_file}")

    # Quick check
    found = sum(1 for v in validator_cache.values() if v)
    missing_data = sum(1 for v in validator_cache.values() if v is None)
    print(f"  With data: {found}, no data (404 or fail): {missing_data}")
    print("\nNow re-run leader_correlation_fixed.py to get full confusion matrix.")


if __name__ == "__main__":
    main()
