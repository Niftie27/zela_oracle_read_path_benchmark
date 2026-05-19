#!/usr/bin/env python3
"""
Leader correlation analysis v3.

CHANGES FROM v2:
  - Empirical latency thresholds derived from route_test_session_100.py
    (100 runs/route, requests.Session, warm=last 95):
      fr2 ~18ms | dx1 ~228ms | ewr ~230ms | slc ~322ms | tyo ~462ms
  - dx1 and ewr are INDISTINGUISHABLE from Prague (both ~228ms, ranges
    fully overlap). Merged into a single "mid" tier. This is reported as
    a finding, not hidden.
  - 4 distinguishable tiers: fr2 / mid / slc / tyo
  - Thresholds set at midpoints between adjacent tier medians:
      fr2|mid = 123ms,  mid|slc = 275ms,  slc|tyo = 392ms
  - Expected-tier mapping updated: Middle East + US-East/Central +
    Canada/Mexico + South America all map to "mid" (Zela routes them to
    ewr or dx1, which we cannot tell apart). US-West maps to slc.

Uses existing caches (no network calls):
  - leader_correlation_results/leader_cache.json
  - leader_correlation_results/validator_cache.json

Run: python3 leader_correlation_v3.py
"""

import csv
import json
import re
import sys
from pathlib import Path
from collections import defaultdict


# Empirical thresholds (ms) — midpoints between adjacent tier medians
# from route_test_session_100.py
T_FR2_MID = 123    # fr2 (~18) | mid (~228)
T_MID_SLC = 275    # mid (~228) | slc (~322)
T_SLC_TYO = 392    # slc (~322) | tyo (~462)


def categorize_latency_us(us: int) -> str:
    """Map observed Zela client latency to 4-tier routing label."""
    ms = us / 1000
    if ms < T_FR2_MID:
        return "fr2"
    elif ms < T_MID_SLC:
        return "mid"      # dx1 + ewr merged — indistinguishable from Prague
    elif ms < T_SLC_TYO:
        return "slc"
    else:
        return "tyo"


def parse_country_from_city(city_str: str) -> tuple[str, str]:
    """validators.app encodes location as 'ASN-CC-City'."""
    if not city_str:
        return "", ""
    m = re.match(r"^\d+-([A-Z]{2})-(.+)$", city_str)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^([A-Z]{2})-(.+)$", city_str)
    if m:
        return m.group(1), m.group(2)
    return "", city_str


# Country → expected tier.
# fr2  = Europe
# mid  = Middle East (dx1) + US-East/Central, Canada, Mexico, South America (ewr)
#        — dx1 and ewr are indistinguishable from Prague, so merged
# slc  = US West
# tyo  = Asia-Pacific
EUROPE = {"DE", "NL", "FR", "GB", "UK", "CH", "AT", "BE", "IE", "IT", "ES",
          "PT", "SE", "NO", "DK", "FI", "PL", "CZ", "SK", "HU", "RO",
          "LT", "LV", "EE", "GR", "BG", "HR", "SI", "LU", "RU", "UA", "RS",
          "IS", "MT", "CY"}
MIDDLE_EAST = {"AE", "SA", "QA", "KW", "BH", "OM", "IL", "TR", "JO", "LB"}
APAC = {"JP", "SG", "KR", "HK", "TW", "TH", "VN", "MY", "PH", "ID", "AU",
        "NZ", "IN", "CN"}
AMERICAS_EAST = {"CA", "MX", "BR", "AR", "CL", "PE", "CO", "UY", "EC", "VE",
                 "BO", "PY", "CR", "PA", "GT", "DO"}
AFRICA = {"ZA", "NG", "KE", "EG", "MA", "TN"}


def country_to_tier(country: str) -> str:
    if not country:
        return "unknown"
    c = country.upper()
    if c in APAC:
        return "tyo"
    if c in MIDDLE_EAST:
        return "mid"
    if c in EUROPE:
        return "fr2"
    if c == "US":
        return "us_unknown"  # refine by city
    if c in AMERICAS_EAST:
        return "mid"          # routes to ewr; ewr indistinguishable from dx1
    if c in AFRICA:
        return "fr2"          # closest Zela executor
    return "unknown"


def us_city_to_tier(city: str) -> str:
    """US validators: West coast → slc, everything else → mid (ewr)."""
    if not city:
        return "mid"  # default US → ewr → mid
    c = city.lower()
    west = ["san francisco", "los angeles", "san jose", "seattle",
            "portland", "salt lake", "denver", "phoenix", "las vegas",
            "san diego", "oakland", "santa clara", "fremont", "california",
            "oregon", "washington state", "nevada", "utah", "arizona"]
    for kw in west:
        if kw in c:
            return "slc"
    # East + Central US → ewr → mid
    return "mid"


def infer_tier(validator_info) -> tuple[str, str, str]:
    """Returns (tier_label, country_code, city). Handles None safely."""
    if not validator_info or not isinstance(validator_info, dict):
        return "unknown", "", ""
    city_raw = validator_info.get("city", "")
    cc, city = parse_country_from_city(city_raw)
    tier = country_to_tier(cc)
    if tier == "us_unknown":
        tier = us_city_to_tier(city)
    return tier, cc, city


def get_name(validator_info) -> str:
    if not validator_info or not isinstance(validator_info, dict):
        return "(no data)"
    return validator_info.get("name") or "(no name)"


def main():
    repo_root = Path(".").resolve()
    out_dir = repo_root / "leader_correlation_results"

    leader_cache_file = out_dir / "leader_cache.json"
    validator_cache_file = out_dir / "validator_cache.json"

    if not leader_cache_file.exists() or not validator_cache_file.exists():
        print("ERROR: cache files not found in leader_correlation_results/")
        sys.exit(1)

    leader_cache_raw = json.loads(leader_cache_file.read_text())
    leader_cache = {int(k): v for k, v in leader_cache_raw.items() if v}
    validator_cache = json.loads(validator_cache_file.read_text())

    print(f"Leader cache: {len(leader_cache_raw)} entries "
          f"({len(leader_cache)} with leader)")
    with_data = sum(1 for v in validator_cache.values() if v)
    print(f"Validator cache: {len(validator_cache)} entries "
          f"({with_data} with data, {len(validator_cache) - with_data} without)")
    print(f"\nThresholds (ms): fr2<{T_FR2_MID}<=mid<{T_MID_SLC}<=slc<{T_SLC_TYO}<=tyo")
    print("  (mid = dx1+ewr merged — indistinguishable from Prague vantage point)")

    # Tier distribution among validators
    print("\n=== Validator → expected-tier distribution ===")
    tier_counts = defaultdict(int)
    for pubkey, vinfo in validator_cache.items():
        tier, cc, city = infer_tier(vinfo)
        tier_counts[tier] += 1
    for t in ["fr2", "mid", "slc", "tyo", "unknown"]:
        print(f"  {t:<10}: {tier_counts[t]:>4} validators")

    # Cross-correlate
    datasets_dir = repo_root / "zela_datasets"
    all_datasets = sorted([
        p for p in datasets_dir.iterdir()
        if p.is_dir() and p.name.startswith("dataset_2026_05_")
        and (p / "feeds.csv").exists()
    ])
    print(f"\nFound {len(all_datasets)} batch datasets")

    runs = []
    for ds in all_datasets:
        aggs = ds / "aggregates.csv"
        feeds = ds / "feeds.csv"
        slot_for_run = {}
        with open(feeds) as f:
            for row in csv.DictReader(f):
                if row["side"] != "zela":
                    continue
                rid = int(row["run_id"])
                if rid not in slot_for_run:
                    slot_for_run[rid] = int(row["context_slot"])
        with open(aggs) as f:
            for row in csv.DictReader(f):
                if row["side"] != "zela":
                    continue
                if row.get("cold_start", "false") == "true":
                    continue
                if row.get("error", "false") == "true":
                    continue
                rid = int(row["run_id"])
                slot = slot_for_run.get(rid)
                if slot is None:
                    continue
                runs.append({
                    "dataset": ds.name, "run_id": rid, "slot": slot,
                    "client_us": int(row["client_wall_clock_us"]),
                })

    print(f"Total Zela runs: {len(runs)}")

    out_csv = out_dir / "runs_with_leaders_v3.csv"
    matches = defaultdict(int)
    no_leader = 0
    no_vdata = 0

    with open(out_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "dataset", "run_id", "slot", "leader_pubkey",
            "leader_name", "leader_city", "leader_country",
            "client_us", "client_ms", "observed_tier", "expected_tier", "match",
        ])
        for r in runs:
            slot = r["slot"]
            leader = leader_cache.get(slot)
            if not leader:
                no_leader += 1
            vinfo = validator_cache.get(leader) if leader else None
            if leader and not vinfo:
                no_vdata += 1
            observed = categorize_latency_us(r["client_us"])
            expected, cc, city = infer_tier(vinfo)
            match = "yes" if observed == expected else "no"
            matches[(expected, observed)] += 1
            writer.writerow([
                r["dataset"], r["run_id"], slot, leader or "",
                get_name(vinfo), city, cc,
                r["client_us"], f"{r['client_us']/1000:.2f}",
                observed, expected, match,
            ])

    print(f"\nWrote {out_csv}")
    print(f"Runs with no leader in cache: {no_leader}")
    print(f"Runs with leader but no validator data: {no_vdata}")

    # Confusion matrix
    print("\n=== Confusion matrix (4-tier) ===")
    labels = ["fr2", "mid", "slc", "tyo", "unknown"]
    print(f"  {'expected down / observed right':<32}", end="")
    for o in labels:
        print(f"{o:>9}", end="")
    print(f"{'total':>9}")
    for e in labels:
        print(f"  {e:<32}", end="")
        row_total = 0
        for o in labels:
            n = matches.get((e, o), 0)
            row_total += n
            print(f"{n:>9}", end="")
        print(f"{row_total:>9}")

    total_matched = sum(n for (e, o), n in matches.items()
                        if e == o and e != "unknown")
    total_known = sum(n for (e, o), n in matches.items() if e != "unknown")
    total = sum(matches.values())
    print()
    if total_known:
        print(f"Match rate (excluding unknown expected): "
              f"{total_matched}/{total_known} = {100*total_matched/total_known:.1f}%")
    print(f"Unknown expected: {(total-total_known)/total*100:.1f}% "
          f"({total-total_known}/{total})")

    # Per-tier match rate
    print("\n=== Per-tier match rate ===")
    for tier in ["fr2", "mid", "slc", "tyo"]:
        tier_total = sum(matches.get((tier, o), 0) for o in labels)
        tier_match = matches.get((tier, tier), 0)
        if tier_total:
            print(f"  {tier:<6}: {tier_match}/{tier_total} = "
                  f"{100*tier_match/tier_total:.1f}%")
        else:
            print(f"  {tier:<6}: 0 runs")


if __name__ == "__main__":
    main()
