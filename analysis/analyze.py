#!/usr/bin/env python3
"""Statistical analysis and figures for the Zela oracle read path benchmark.

Two schemas:
  --mode sequential : the original 10×getAccountInfo loop output (legacy)
  --mode batch      : the new getMultipleAccounts output with split
                      server_wall_clock_us / client_wall_clock_us, cold_start,
                      and commitment columns
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def _plain_log(x, _pos):
    """Format log-scale tick labels as plain integers (1, 10, 100) instead of 10^N."""
    return f"{int(x)}" if x >= 1 else f"{x:g}"


PLAIN_LOG = FuncFormatter(_plain_log)

SEQ_FEEDS_COLS = {
    "run_id", "timestamp_ms", "side", "symbol", "pubkey",
    "account_found", "account_data_len", "context_slot",
    "wall_clock_elapsed_us", "error",
}
SEQ_AGGS_COLS = {
    "run_id", "timestamp_ms", "side", "feed_count",
    "wall_clock_start_ms", "wall_clock_end_ms",
    "wall_clock_total_us", "unique_slots_count", "error",
}
BATCH_FEEDS_COLS = {
    "run_id", "timestamp_ms", "side", "symbol", "pubkey",
    "account_found", "account_data_len", "context_slot",
    "cold_start", "commitment", "error",
}
BATCH_AGGS_COLS = {
    "run_id", "timestamp_ms", "side", "feed_count",
    "wall_clock_start_ms", "wall_clock_end_ms",
    "server_wall_clock_us", "client_wall_clock_us",
    "unique_slots_count", "cold_start", "commitment", "error",
}
BATCH_FEEDS_FORBIDDEN = {"wall_clock_elapsed_us"}
BATCH_AGGS_FORBIDDEN = {"wall_clock_total_us"}
FEED_ORDER = [
    "SOL/USD", "BTC/USD", "ETH/USD", "USDC/USD", "USDT/USD",
    "BNB/USD", "JUP/USD", "BONK/USD", "PYTH/USD", "JTO/USD",
]
Z_COL = "#1f77b4"
B_COL = "#ff7f0e"
DS_COLS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
           "#8c564b", "#e377c2", "#7f7f7f"]


def short_ds(name: str) -> str:
    """Strip dataset_YYYY_MM_DD_ prefix for readable axis labels."""
    parts = name.split("_")
    return "_".join(parts[4:]) if len(parts) > 4 and parts[0] == "dataset" else name

FIGURES_DIR = Path(__file__).resolve().parent.parent / "docs" / "figures"


def pct(arr, q):
    a = np.asarray(arr, dtype=float)
    return float(np.percentile(a, q)) if len(a) else float("nan")


def agg_stats(s):
    a = np.asarray(s, dtype=float)
    if not len(a):
        return {}
    return {
        "min_us": round(float(a.min()), 1),
        "p50_us": round(pct(a, 50), 1),
        "p95_us": round(pct(a, 95), 1),
        "p99_us": round(pct(a, 99), 1),
        "max_us": round(float(a.max()), 1),
        "mean_us": round(float(a.mean()), 1),
        "n": int(len(a)),
    }


# ── Sequential mode (legacy, must stay bit-identical with pre-refactor output) ──

def load_dataset(path_str):
    path = Path(path_str)
    name = path.name
    if not (path / "feeds.csv").exists() or not (path / "aggregates.csv").exists():
        raise FileNotFoundError(f"Missing CSVs in {path}")
    feeds = pd.read_csv(path / "feeds.csv")
    aggs = pd.read_csv(path / "aggregates.csv")
    for df, fname, schema in [
        (feeds, "feeds.csv", SEQ_FEEDS_COLS), (aggs, "aggregates.csv", SEQ_AGGS_COLS),
    ]:
        missing = schema - set(df.columns)
        if missing:
            raise ValueError(f"{name}/{fname} missing columns: {missing}")
    ferr = (feeds["error"].astype(str).str.lower() == "true").sum()
    aerr = (aggs["error"].astype(str).str.lower() == "true").sum()
    if ferr or aerr:
        print(
            f"Filtered {ferr} error rows from {name}/feeds.csv, "
            f"{aerr} from aggregates.csv",
            file=sys.stderr,
        )
    feeds = feeds[feeds["error"].astype(str).str.lower() == "false"].copy()
    aggs = aggs[aggs["error"].astype(str).str.lower() == "false"].copy()
    for col in ["wall_clock_total_us", "unique_slots_count", "run_id"]:
        aggs[col] = pd.to_numeric(aggs[col], errors="coerce")
    for col in ["wall_clock_elapsed_us", "run_id"]:
        feeds[col] = pd.to_numeric(feeds[col], errors="coerce")
    return {"name": name, "feeds": feeds, "aggs": aggs, "error_count": int(aerr)}


def compute_stats(ds):
    aggs = ds["aggs"]
    if aggs.empty:
        print(f"Warning: {ds['name']} empty after filtering", file=sys.stderr)
        return None
    z_df = aggs[aggs["side"] == "zela"]
    b_df = aggs[aggs["side"] == "baseline"]
    z = z_df["wall_clock_total_us"].dropna()
    b = b_df["wall_clock_total_us"].dropna()

    def slot_pct(df, n):
        t = len(df)
        return round(100.0 * (df["unique_slots_count"] == n).sum() / t, 1) if t else 0.0

    nz = len(z)
    fast = int((z < 5_000).sum())
    slow = int((z > 200_000).sum())
    mid = nz - fast - slow

    # Use composite key (dataset_name, run_id) when "_ds" column present (combined case)
    # to avoid collision across datasets that each start run_id at 1.
    if "_ds" in aggs.columns:
        z_keys = list(zip(z_df["_ds"], z_df["run_id"]))
        b_keys = list(zip(b_df["_ds"], b_df["run_id"]))
    else:
        z_keys = list(z_df["run_id"])
        b_keys = list(b_df["run_id"])
    z_map = dict(zip(z_keys, z_df["wall_clock_total_us"]))
    b_map = dict(zip(b_keys, b_df["wall_clock_total_us"]))
    common = set(z_map) & set(b_map)

    z1, z2 = slot_pct(z_df, 1), slot_pct(z_df, 2)
    b1, b2 = slot_pct(b_df, 1), slot_pct(b_df, 2)
    return {
        "run_count": len(common),
        "error_count": ds.get("error_count", 0),
        "zela": agg_stats(z),
        "baseline": agg_stats(b),
        "slot_consistency": {
            "zela_1slot_pct": z1,
            "zela_2slot_pct": z2,
            "zela_3plus_slot_pct": abs(round(100 - z1 - z2, 1)),
            "baseline_1slot_pct": b1,
            "baseline_2slot_pct": b2,
            "baseline_3plus_slot_pct": abs(round(100 - b1 - b2, 1)),
        },
        "bimodality": {
            "fast_mode_pct": round(100 * fast / nz, 1) if nz else 0.0,
            "intermediate_pct": round(100 * mid / nz, 1) if nz else 0.0,
            "slow_mode_pct": round(100 * slow / nz, 1) if nz else 0.0,
        },
        "ratios": {
            "median_ratio": round(pct(b, 50) / pct(z, 50), 1) if len(z) and len(b) else None,
            "p95_ratio": round(pct(b, 95) / pct(z, 95), 1) if len(z) and len(b) else None,
        },
    }


def compute_per_feed(all_feeds):
    result = {}
    for sym in FEED_ORDER:
        rows = all_feeds[all_feeds["symbol"] == sym]
        z = rows[rows["side"] == "zela"]["wall_clock_elapsed_us"].dropna()
        b = rows[rows["side"] == "baseline"]["wall_clock_elapsed_us"].dropna()
        result[sym] = {
            "zela_p50_us": round(pct(z, 50), 1),
            "zela_p95_us": round(pct(z, 95), 1),
            "baseline_p50_us": round(pct(b, 50), 1),
            "baseline_p95_us": round(pct(b, 95), 1),
        }
    return result


# ── Figures ───────────────────────────────────────────────────────────────────

def fig_latency_distribution(datasets):
    # Facet grid: one row per dataset, two columns (Zela log-scale, Baseline linear).
    n = len(datasets)
    fig, axes = plt.subplots(n, 2, figsize=(12, 2.4 * n + 1.5), squeeze=False, sharey="col")

    # Shared x-axis ranges within each column so rows are visually comparable.
    # All latency values displayed in milliseconds (raw data in summary.json stays µs).
    all_z = pd.concat([ds["aggs"][ds["aggs"]["side"] == "zela"]["wall_clock_total_us"]
                       for ds in datasets]).dropna() / 1000.0
    all_b = pd.concat([ds["aggs"][ds["aggs"]["side"] == "baseline"]["wall_clock_total_us"]
                       for ds in datasets]).dropna() / 1000.0
    z_bins = np.geomspace(max(0.001, all_z.min()), all_z.max(), 40) if len(all_z) > 1 else 20
    b_bins = np.linspace(all_b.min(), all_b.max(), 40) if len(all_b) > 1 else 20
    z_xlim = (all_z.min() * 0.9, all_z.max() * 1.1) if len(all_z) else None
    b_xlim = (all_b.min() * 0.95, all_b.max() * 1.05) if len(all_b) else None

    for i, ds in enumerate(datasets):
        ax_z = axes[i, 0]
        ax_b = axes[i, 1]
        color = DS_COLS[i % len(DS_COLS)]
        aggs = ds["aggs"]
        z = aggs[aggs["side"] == "zela"]["wall_clock_total_us"].dropna() / 1000.0
        b = aggs[aggs["side"] == "baseline"]["wall_clock_total_us"].dropna() / 1000.0
        if len(z) > 0:
            ax_z.hist(z, bins=z_bins, color=color, alpha=0.85, edgecolor="white", linewidth=0.3)
        if len(b) > 0:
            ax_b.hist(b, bins=b_bins, color=color, alpha=0.85, edgecolor="white", linewidth=0.3)

        ax_z.set_xscale("log")
        ax_z.xaxis.set_major_formatter(PLAIN_LOG)
        if z_xlim:
            ax_z.set_xlim(z_xlim)
        if b_xlim:
            ax_b.set_xlim(b_xlim)
        ax_z.set_ylabel("Runs", fontsize=9)
        ax_z.annotate(
            short_ds(ds["name"]),
            xy=(-0.22, 0.5), xycoords="axes fraction",
            ha="right", va="center", fontsize=11, fontweight="bold",
        )
        ax_b.tick_params(labelleft=False)

        if i == 0:
            ax_z.set_title("Zela", fontsize=12)
            ax_b.set_title("Baseline (Helius)", fontsize=12)
        if i < n - 1:
            ax_z.tick_params(labelbottom=False)
            ax_b.tick_params(labelbottom=False)

    axes[n - 1, 0].set_xlabel("Latency per batch (ms)", fontsize=11)
    axes[n - 1, 1].set_xlabel("Latency per batch (ms)", fontsize=11)
    fig.suptitle(f"Aggregate Latency Distribution Across {n} Datasets (100 runs each)", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(FIGURES_DIR / "latency_distribution.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig_slot_consistency(datasets):
    # Grouped bars (non-stacked): single-slot rate for Zela and Baseline per window.
    ds_labels = [short_ds(ds["name"]) for ds in datasets]
    x = np.arange(len(datasets))
    width = 0.38

    def one_slot_pct(df):
        t = len(df)
        return float((df["unique_slots_count"] == 1).sum()) / t * 100 if t else 0.0

    z_vals = [one_slot_pct(ds["aggs"][ds["aggs"]["side"] == "zela"]) for ds in datasets]
    b_vals = [one_slot_pct(ds["aggs"][ds["aggs"]["side"] == "baseline"]) for ds in datasets]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(x - width / 2, z_vals, width=width, color=Z_COL, label="Zela")
    ax.bar(x + width / 2, b_vals, width=width, color=B_COL, label="Baseline (Helius)")
    ax.set_xticks(x)
    ax.set_xticklabels(ds_labels, rotation=45, ha="right", fontsize=10)
    ax.set_ylabel("% of runs in a single slot", fontsize=11)
    ax.set_ylim(0, 100)
    ax.legend(fontsize=10, loc="center right")
    ax.set_title("Slot Consistency: Fraction of Runs Returning Within a Single Slot", fontsize=13)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "slot_consistency.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig_per_feed_latency(per_feed):
    fig, (ax_z, ax_b) = plt.subplots(2, 1, figsize=(10, 6))
    x = np.arange(len(FEED_ORDER))
    for ax, side, col, log_scale in [
        (ax_z, "zela", Z_COL, True), (ax_b, "baseline", B_COL, False),
    ]:
        p50s = np.array([per_feed[s][f"{side}_p50_us"] for s in FEED_ORDER]) / 1000.0
        p95s = np.array([per_feed[s][f"{side}_p95_us"] for s in FEED_ORDER]) / 1000.0
        err_up = np.maximum(0, p95s - p50s)
        ax.errorbar(x, p50s, yerr=[np.zeros(len(FEED_ORDER)), err_up],
                    fmt="o", color=col, capsize=4, label="p50, error bar to p95")
        ax.set_xticks(x)
        ax.set_xticklabels(FEED_ORDER, rotation=30, ha="right", fontsize=9)
        ax.set_ylabel("ms", fontsize=11)
        ax.set_title(f"{side.capitalize()} per-feed latency", fontsize=11)
        ax.legend(fontsize=9)
        if log_scale:
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(PLAIN_LOG)
    fig.suptitle("Per-Feed Latency: Median and p95", fontsize=13)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "per_feed_latency.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig_time_of_day(datasets):
    ds_labels = [short_ds(ds["name"]) for ds in datasets]
    fig, (ax_z, ax_b) = plt.subplots(1, 2, figsize=(10, 6))
    for ax, side, col, title in [
        (ax_z, "zela", Z_COL, "Zela"), (ax_b, "baseline", B_COL, "Baseline (Helius)"),
    ]:
        data = [
            ds["aggs"][ds["aggs"]["side"] == side]["wall_clock_total_us"].dropna().values / 1000.0
            for ds in datasets
        ]
        ax.boxplot(data, patch_artist=True,
                   boxprops=dict(facecolor=col, alpha=0.5),
                   medianprops=dict(color="black", linewidth=2))
        ax.set_xticklabels(ds_labels, rotation=45, ha="right", fontsize=9)
        ax.set_ylabel("Aggregate batch latency (ms)", fontsize=11)
        ax.set_title(title, fontsize=11)
    ax_z.set_yscale("log")
    ax_z.yaxis.set_major_formatter(PLAIN_LOG)
    ax_z.set_ylabel("Aggregate batch latency (ms)", fontsize=11)
    fig.suptitle("Aggregate Latency by Collection Window", fontsize=13)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "time_of_day.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig_cdf(combined_aggs):
    fig, ax = plt.subplots(figsize=(10, 6))
    for side, col, label in [("zela", Z_COL, "Zela"), ("baseline", B_COL, "Baseline (Helius)")]:
        vals = combined_aggs[combined_aggs["side"] == side]["wall_clock_total_us"].dropna()
        vals = vals.sort_values().values / 1000.0  # convert to ms
        if not len(vals):
            continue
        cdf = np.arange(1, len(vals) + 1) / len(vals)
        ax.plot(vals, cdf, color=col, label=label, linewidth=2)
        for q, ls in [(50, "--"), (95, ":")]:
            vq = pct(vals, q)
            ax.axvline(vq, color=col, linestyle=ls, alpha=0.7,
                       label=f"{label} p{q}={vq:.1f}ms")
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(PLAIN_LOG)
    ax.set_xlabel("Latency per batch (ms)", fontsize=11)
    ax.set_ylabel("Fraction of runs ≤ x", fontsize=11)
    ax.set_title("Cumulative Distribution of Aggregate Latency (All Datasets Combined)", fontsize=13)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "cdf.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def print_summary(stats_by_name):
    combined = stats_by_name.get("combined")
    if not combined:
        return
    z = combined.get("zela", {})
    b = combined.get("baseline", {})
    sc = combined.get("slot_consistency", {})
    ratios = combined.get("ratios", {})
    bm = combined.get("bimodality", {})
    print("\n=== Sequential Analysis Summary ===")
    print(f"Runs analyzed (combined):          {combined.get('run_count')}")
    print(f"Zela p50 / p95:                    {z.get('p50_us', 0) / 1000:.2f}ms / {z.get('p95_us', 0) / 1000:.2f}ms")
    print(f"Baseline p50 / p95:                {b.get('p50_us', 0) / 1000:.2f}ms / {b.get('p95_us', 0) / 1000:.2f}ms")
    print(f"Median ratio (combined):           {ratios.get('median_ratio')}x")
    print(f"p95 ratio (combined):              {ratios.get('p95_ratio')}x")
    print(f"Zela slot consistency (1 slot):    {sc.get('zela_1slot_pct')}%")
    print(f"Baseline slot consistency (1 slot):{sc.get('baseline_1slot_pct')}%")
    print(f"Zela bimodality: fast(<5ms)={bm.get('fast_mode_pct')}%  slow(>200ms)={bm.get('slow_mode_pct')}%")
    print("===================================\n")


def run_sequential(paths):
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    datasets = []
    for path in paths:
        try:
            ds = load_dataset(path)
            datasets.append(ds)
            print(f"Loaded {ds['name']}: {len(ds['aggs'])} agg rows", file=sys.stderr)
        except Exception as e:
            print(f"Error loading {path}: {e}", file=sys.stderr)
            sys.exit(1)

    if not datasets:
        print("No datasets loaded.", file=sys.stderr)
        sys.exit(1)

    stats_by_name = {}
    for ds in datasets:
        s = compute_stats(ds)
        if s:
            stats_by_name[ds["name"]] = s

    combined_feeds = pd.concat([ds["feeds"] for ds in datasets], ignore_index=True)
    # Tag each row with its source dataset before concat so compute_stats can use
    # (dataset_name, run_id) as composite key, avoiding run_id collisions.
    tagged_aggs = []
    for ds in datasets:
        a = ds["aggs"].copy()
        a["_ds"] = ds["name"]
        tagged_aggs.append(a)
    combined_aggs = pd.concat(tagged_aggs, ignore_index=True)
    combined_stats = compute_stats({
        "name": "combined",
        "feeds": combined_feeds,
        "aggs": combined_aggs,
        "error_count": sum(ds.get("error_count", 0) for ds in datasets),
    })
    if combined_stats:
        stats_by_name["combined"] = combined_stats

    per_feed = compute_per_feed(combined_feeds)

    out_json = FIGURES_DIR / "summary.json"
    with open(out_json, "w") as f:
        json.dump({"datasets": stats_by_name, "per_feed": per_feed}, f, indent=2)
    print(f"Wrote {out_json}", file=sys.stderr)

    print("Generating figures...", file=sys.stderr)
    fig_latency_distribution(datasets)
    fig_slot_consistency(datasets)
    fig_per_feed_latency(per_feed)
    fig_time_of_day(datasets)
    fig_cdf(combined_aggs)
    for name in ["latency_distribution", "slot_consistency", "per_feed_latency", "time_of_day", "cdf"]:
        print(f"  wrote docs/figures/{name}.png", file=sys.stderr)

    print_summary(stats_by_name)


# ── Batch mode ────────────────────────────────────────────────────────────────

def _resolve_dataset_path(path: Path) -> Path:
    """If path/feeds.csv is missing, look for a single run_* subdir."""
    if (path / "feeds.csv").exists():
        return path
    candidates = sorted(
        p for p in path.glob("run_*")
        if (p / "feeds.csv").exists() and (p / "aggregates.csv").exists()
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise FileNotFoundError(
            f"Multiple run subdirectories in {path}: "
            f"{[c.name for c in candidates]}. Specify one explicitly."
        )
    raise FileNotFoundError(f"Missing CSVs in {path}")


def _validate_batch_slot_consistency(feeds_df, dataset_name):
    """Returns 1 if all runs have one unique slot, otherwise returns actual max."""
    group_cols = ["run_id", "side"]
    if "_ds" in feeds_df.columns:
        group_cols = ["_ds"] + group_cols
    by_run = feeds_df.groupby(group_cols)["context_slot"].nunique()
    bad = by_run[by_run > 1]
    if len(bad):
        print(
            f"WARNING: {dataset_name} has {len(bad)} (run,side) pairs with "
            f">1 unique slot in batch mode. This violates getMultipleAccounts "
            f"server-side guarantee. Max unique slots: {bad.max()}",
            file=sys.stderr,
        )
        return int(bad.max())
    return 1


def load_dataset_batch(path_str):
    path = _resolve_dataset_path(Path(path_str))
    name = path.name
    if not (path / "feeds.csv").exists() or not (path / "aggregates.csv").exists():
        raise FileNotFoundError(f"Missing CSVs in {path}")
    feeds = pd.read_csv(path / "feeds.csv")
    aggs = pd.read_csv(path / "aggregates.csv")
    for df, fname, forbidden in [
        (feeds, "feeds.csv", BATCH_FEEDS_FORBIDDEN),
        (aggs, "aggregates.csv", BATCH_AGGS_FORBIDDEN),
    ]:
        extra = forbidden & set(df.columns)
        if extra:
            raise ValueError(
                f"{name}/{fname} contains forbidden columns in batch mode: {extra}. "
                f"This data may be from sequential mode — use --mode sequential."
            )
    for df, fname, schema in [
        (feeds, "feeds.csv", BATCH_FEEDS_COLS), (aggs, "aggregates.csv", BATCH_AGGS_COLS),
    ]:
        missing = schema - set(df.columns)
        if missing:
            raise ValueError(f"{name}/{fname} missing columns: {missing}")
    ferr = (feeds["error"].astype(str).str.lower() == "true").sum()
    aerr = (aggs["error"].astype(str).str.lower() == "true").sum()
    if ferr or aerr:
        print(
            f"Filtered {ferr} error rows from {name}/feeds.csv, "
            f"{aerr} from aggregates.csv",
            file=sys.stderr,
        )
    feeds = feeds[feeds["error"].astype(str).str.lower() == "false"].copy()
    aggs = aggs[aggs["error"].astype(str).str.lower() == "false"].copy()

    cold_count_aggs = (aggs["cold_start"].astype(str).str.lower() == "true").sum()
    cold_count_feeds = (feeds["cold_start"].astype(str).str.lower() == "true").sum()
    if cold_count_aggs or cold_count_feeds:
        print(
            f"Filtered {cold_count_aggs} cold_start agg rows and "
            f"{cold_count_feeds} cold_start feed rows from {name}",
            file=sys.stderr,
        )
    feeds = feeds[feeds["cold_start"].astype(str).str.lower() != "true"].copy()
    aggs = aggs[aggs["cold_start"].astype(str).str.lower() != "true"].copy()

    for col in ["server_wall_clock_us", "client_wall_clock_us",
                "unique_slots_count", "run_id"]:
        aggs[col] = pd.to_numeric(aggs[col], errors="coerce")
    feeds["run_id"] = pd.to_numeric(feeds["run_id"], errors="coerce")
    feeds["context_slot"] = pd.to_numeric(feeds["context_slot"], errors="coerce")

    commitments = sorted(set(aggs["commitment"].dropna().astype(str)))
    return {
        "name": name,
        "path": path,
        "feeds": feeds,
        "aggs": aggs,
        "error_count": int(aerr),
        "cold_start_count": int(cold_count_aggs),
        "commitments": commitments,
    }


def compute_stats_batch(ds):
    aggs = ds["aggs"]
    if aggs.empty:
        print(f"Warning: {ds['name']} empty after filtering", file=sys.stderr)
        return None
    z_df = aggs[aggs["side"] == "zela"]
    b_df = aggs[aggs["side"] == "baseline"]
    pair_cols = ["run_id"]
    if "_ds" in aggs.columns:
        pair_cols = ["_ds"] + pair_cols
    paired = pd.merge(
        z_df[pair_cols + ["server_wall_clock_us", "client_wall_clock_us"]].rename(
            columns={
                "server_wall_clock_us": "z_server",
                "client_wall_clock_us": "z_client",
            }
        ),
        b_df[pair_cols + ["client_wall_clock_us"]].rename(
            columns={"client_wall_clock_us": "b_client"}
        ),
        on=pair_cols,
        how="inner",
    )
    dropped_unpaired = min(len(z_df), len(b_df)) - len(paired)
    if dropped_unpaired > 0:
        print(
            f"WARNING: {ds['name']} dropped {dropped_unpaired} unpaired batch runs "
            f"after inner join on {pair_cols}",
            file=sys.stderr,
        )
    z_server = paired["z_server"].dropna()
    z_client = paired["z_client"].dropna()
    b_client = paired["b_client"].dropna()
    unique_slot_value = _validate_batch_slot_consistency(ds["feeds"], ds["name"])

    server_ratio = (
        round(pct(b_client, 50) / pct(z_server, 50), 1)
        if len(z_server) and len(b_client) else None
    )
    client_ratio = (
        round(pct(b_client, 50) / pct(z_client, 50), 1)
        if len(z_client) and len(b_client) else None
    )
    if unique_slot_value == 1:
        slot_note = (
            "Structurally 1 in batch mode (server-side getMultipleAccounts "
            "guarantee, validated against feeds.csv). See legacy_sequential "
            "for sequential measurement (Zela 91% / Baseline 13% one-slot "
            "consistency)."
        )
    else:
        slot_note = (
            "Value reflects actual max unique context_slot count per (run, "
            "side) pair in feeds.csv. Expected 1 for getMultipleAccounts "
            "server-side guarantee; value > 1 indicates data quality anomaly "
            "(possibly transitional dataset where orchestrator was updated "
            "before procedure WASM was redeployed). See legacy_sequential "
            "for sequential measurement reference."
        )

    return {
        "run_count": len(paired),
        "error_count": ds.get("error_count", 0),
        "cold_start_filtered": ds.get("cold_start_count", 0),
        "zela": {
            "server": agg_stats(z_server),
            "client": agg_stats(z_client),
        },
        "baseline": {
            "client": agg_stats(b_client),
        },
        "ratios": {
            "server_ratio": server_ratio,
            "client_ratio": client_ratio,
        },
        "unique_slots_count": {
            "value": unique_slot_value,
            "note": slot_note,
        },
    }


def run_batch(paths):
    datasets = []
    for path in paths:
        try:
            ds = load_dataset_batch(path)
            datasets.append(ds)
            print(f"Loaded {ds['name']}: {len(ds['aggs'])} agg rows (after cold_start filter)",
                  file=sys.stderr)
        except Exception as e:
            print(f"Error loading {path}: {e}", file=sys.stderr)
            sys.exit(1)

    if not datasets:
        print("No datasets loaded.", file=sys.stderr)
        sys.exit(1)

    stats_by_name = {}
    for ds in datasets:
        s = compute_stats_batch(ds)
        if s:
            stats_by_name[ds["name"]] = s

    if len(datasets) > 1:
        tagged_aggs = []
        tagged_feeds = []
        for ds in datasets:
            a = ds["aggs"].copy()
            a["_ds"] = ds["name"]
            tagged_aggs.append(a)
            f = ds["feeds"].copy()
            f["_ds"] = ds["name"]
            tagged_feeds.append(f)
        combined_aggs = pd.concat(tagged_aggs, ignore_index=True)
        combined_feeds = pd.concat(tagged_feeds, ignore_index=True)
        combined_stats = compute_stats_batch({
            "name": "combined",
            "aggs": combined_aggs,
            "feeds": combined_feeds,
            "error_count": sum(ds.get("error_count", 0) for ds in datasets),
            "cold_start_count": sum(ds.get("cold_start_count", 0) for ds in datasets),
        })
        if combined_stats:
            stats_by_name["combined"] = combined_stats

    all_commitments = sorted({c for ds in datasets for c in ds["commitments"]})
    payload = {
        "datasets": stats_by_name,
        "commitment": {
            "batch_mode": all_commitments[0] if len(all_commitments) == 1 else all_commitments,
            "legacy_sequential": (
                "not specified in source (server default applied). "
                "Verified in pre-batch-v2 source: procedures/oracle_read/src/lib.rs "
                "and baseline_client/src/main.rs called getAccountInfo without a "
                "commitment field. Solana RPC server default for unspecified commitment "
                "is 'finalized'; Helius may differ."
            ),
        },
        "asymmetries": {
            "zela_client_includes_genesis_hash": (
                "Zela client_wall_clock_us measures one HTTP round-trip that "
                "encloses both getMultipleAccounts and getGenesisHash inside the "
                "procedure. Baseline client_wall_clock_us measures only the "
                "getMultipleAccounts HTTP call. Estimated overhead: ~1-2ms on Zela "
                "side (server-local genesis hash query)."
            ),
            "baseline_no_connection_reuse_across_runs": (
                "baseline_client is a fresh subprocess per run, so each run pays "
                "TCP+TLS handshake cost (~30-50ms typical from Prague to Helius). "
                "Zela orchestrator uses requests.Session() with persistent "
                "connection, so warm-state runs avoid handshake. This systematically "
                "favors Zela in client_ratio. To eliminate, baseline would need to "
                "be refactored to a long-running daemon. Out of scope for v2."
            ),
        },
    }

    # Write summary.json into the first dataset's directory so the legacy
    # docs/figures/summary.json (sequential) is not overwritten.
    out_path = datasets[0]["path"] / "summary.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {out_path}", file=sys.stderr)

    primary = stats_by_name.get("combined") or next(iter(stats_by_name.values()))
    z_server_p50 = primary["zela"]["server"].get("p50_us", 0) / 1000
    z_client_p50 = primary["zela"]["client"].get("p50_us", 0) / 1000
    b_client_p50 = primary["baseline"]["client"].get("p50_us", 0) / 1000
    print("\n=== Batch Analysis Summary ===")
    print(f"Cold-start side-rows filtered:  {primary.get('cold_start_filtered')}  "
          f"(= 5 Zela + 5 baseline aggregate rows)")
    print(f"Zela server p50:           {z_server_p50:.2f} ms")
    print(f"Zela client p50:           {z_client_p50:.2f} ms")
    print(f"Baseline client p50:       {b_client_p50:.2f} ms")
    print(f"server_ratio:              {primary['ratios']['server_ratio']}x")
    print(f"client_ratio:              {primary['ratios']['client_ratio']}x")
    print(f"unique_slots_count.value:  {primary['unique_slots_count']['value']}")
    print("==============================\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sequential", "batch"], default="batch")
    ap.add_argument("paths", nargs="+", help="dataset directories")
    args = ap.parse_args()

    if args.mode == "sequential":
        run_sequential(args.paths)
    else:
        run_batch(args.paths)


if __name__ == "__main__":
    main()
