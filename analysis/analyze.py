#!/usr/bin/env python3
"""M4a: statistical analysis and figures for Zela oracle read path benchmark."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FEEDS_COLS = {
    "run_id", "timestamp_ms", "side", "symbol", "pubkey",
    "account_found", "account_data_len", "context_slot",
    "wall_clock_elapsed_us", "error",
}
AGGS_COLS = {
    "run_id", "timestamp_ms", "side", "feed_count",
    "wall_clock_start_ms", "wall_clock_end_ms",
    "wall_clock_total_us", "unique_slots_count", "error",
}
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


def load_dataset(path_str):
    path = Path(path_str)
    name = path.name
    if not (path / "feeds.csv").exists() or not (path / "aggregates.csv").exists():
        raise FileNotFoundError(f"Missing CSVs in {path}")
    feeds = pd.read_csv(path / "feeds.csv")
    aggs = pd.read_csv(path / "aggregates.csv")
    for df, fname, schema in [
        (feeds, "feeds.csv", FEEDS_COLS), (aggs, "aggregates.csv", AGGS_COLS),
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
    fig, axes = plt.subplots(n, 2, figsize=(12, 2.4 * n + 1.5), squeeze=False)

    # Shared x-axis ranges within each column so rows are visually comparable.
    all_z = pd.concat([ds["aggs"][ds["aggs"]["side"] == "zela"]["wall_clock_total_us"]
                       for ds in datasets]).dropna()
    all_b = pd.concat([ds["aggs"][ds["aggs"]["side"] == "baseline"]["wall_clock_total_us"]
                       for ds in datasets]).dropna()
    z_bins = np.geomspace(max(1, all_z.min()), all_z.max(), 40) if len(all_z) > 1 else 20
    b_bins = np.linspace(all_b.min(), all_b.max(), 40) if len(all_b) > 1 else 20
    z_xlim = (all_z.min() * 0.9, all_z.max() * 1.1) if len(all_z) else None
    b_xlim = (all_b.min() * 0.95, all_b.max() * 1.05) if len(all_b) else None

    for i, ds in enumerate(datasets):
        ax_z = axes[i, 0]
        ax_b = axes[i, 1]
        color = DS_COLS[i % len(DS_COLS)]
        aggs = ds["aggs"]
        z = aggs[aggs["side"] == "zela"]["wall_clock_total_us"].dropna()
        b = aggs[aggs["side"] == "baseline"]["wall_clock_total_us"].dropna()
        if len(z) > 0:
            ax_z.hist(z, bins=z_bins, color=color, alpha=0.85, edgecolor="white", linewidth=0.3)
        if len(b) > 0:
            ax_b.hist(b, bins=b_bins, color=color, alpha=0.85, edgecolor="white", linewidth=0.3)

        ax_z.set_xscale("log")
        if z_xlim:
            ax_z.set_xlim(z_xlim)
        if b_xlim:
            ax_b.set_xlim(b_xlim)
        ax_z.set_ylabel(short_ds(ds["name"]), fontsize=11, rotation=0, ha="right",
                        va="center", labelpad=8)
        ax_b.tick_params(labelleft=False)

        if i == 0:
            ax_z.set_title("Zela", fontsize=12)
            ax_b.set_title("Baseline", fontsize=12)
        if i < n - 1:
            ax_z.tick_params(labelbottom=False)
            ax_b.tick_params(labelbottom=False)

    axes[n - 1, 0].set_xlabel("Latency (µs, log scale)", fontsize=11)
    axes[n - 1, 1].set_xlabel("Latency (µs)", fontsize=11)
    fig.suptitle(f"Aggregate Latency Distribution Across {n} Datasets (100 runs each)", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(FIGURES_DIR / "latency_distribution.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig_slot_consistency(datasets):
    # Grouped stacked bar chart: two bars per dataset (Zela, Baseline), stacked by slot count.
    ds_labels = [short_ds(ds["name"]) for ds in datasets]
    x = np.arange(len(datasets))
    width = 0.35
    side_colors = {
        "zela":     {"1": "#1f77b4", "2": "#6baed6", "3+": "#c6dbef"},
        "baseline": {"1": "#ff7f0e", "2": "#fdae6b", "3+": "#fee6ce"},
    }
    fig, ax = plt.subplots(figsize=(10, 6))
    for side, offset in [("zela", -width / 2), ("baseline", width / 2)]:
        s1, s2, s3 = [], [], []
        for ds in datasets:
            df = ds["aggs"][ds["aggs"]["side"] == side]
            t = len(df)
            v1 = float((df["unique_slots_count"] == 1).sum()) / t * 100 if t else 0
            v2 = float((df["unique_slots_count"] == 2).sum()) / t * 100 if t else 0
            s1.append(v1); s2.append(v2); s3.append(max(0.0, 100 - v1 - v2))
        xpos = x + offset
        sc = side_colors[side]
        label = side.capitalize()
        ax.bar(xpos, s1, width=width, color=sc["1"],  label=f"{label} – 1 slot")
        ax.bar(xpos, s2, width=width, color=sc["2"],  label=f"{label} – 2 slots",
               bottom=s1)
        ax.bar(xpos, s3, width=width, color=sc["3+"], label=f"{label} – 3+ slots",
               bottom=[a + b for a, b in zip(s1, s2)])
    ax.set_xticks(x)
    ax.set_xticklabels(ds_labels, rotation=45, ha="right", fontsize=10)
    ax.set_ylabel("% of runs", fontsize=11)
    ax.set_ylim(0, 105)
    ax.legend(fontsize=9, loc="center left", bbox_to_anchor=(1.02, 0.5))
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
        p50s = np.array([per_feed[s][f"{side}_p50_us"] for s in FEED_ORDER])
        p95s = np.array([per_feed[s][f"{side}_p95_us"] for s in FEED_ORDER])
        err_up = np.maximum(0, p95s - p50s)
        ax.errorbar(x, p50s, yerr=[np.zeros(len(FEED_ORDER)), err_up],
                    fmt="o", color=col, capsize=4, label="p50, error bar to p95")
        ax.set_xticks(x)
        ax.set_xticklabels(FEED_ORDER, rotation=30, ha="right", fontsize=9)
        ax.set_ylabel("µs", fontsize=11)
        ax.set_title(f"{side.capitalize()} per-feed latency", fontsize=11)
        ax.legend(fontsize=9)
        if log_scale:
            ax.set_yscale("log")
    fig.suptitle("Per-Feed Latency: Median and p95", fontsize=13)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "per_feed_latency.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig_time_of_day(datasets):
    ds_labels = [short_ds(ds["name"]) for ds in datasets]
    fig, (ax_z, ax_b) = plt.subplots(1, 2, figsize=(10, 6))
    for ax, side, col, title in [
        (ax_z, "zela", Z_COL, "Zela"), (ax_b, "baseline", B_COL, "Baseline"),
    ]:
        data = [
            ds["aggs"][ds["aggs"]["side"] == side]["wall_clock_total_us"].dropna().values
            for ds in datasets
        ]
        ax.boxplot(data, patch_artist=True,
                   boxprops=dict(facecolor=col, alpha=0.5),
                   medianprops=dict(color="black", linewidth=2))
        ax.set_xticklabels(ds_labels, rotation=45, ha="right", fontsize=9)
        ax.set_ylabel("wall_clock_total_us (µs)", fontsize=11)
        ax.set_title(title, fontsize=11)
    fig.suptitle("Aggregate Latency by Collection Window", fontsize=13)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "time_of_day.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig_cdf(combined_aggs):
    fig, ax = plt.subplots(figsize=(10, 6))
    for side, col, label in [("zela", Z_COL, "Zela"), ("baseline", B_COL, "Baseline")]:
        vals = combined_aggs[combined_aggs["side"] == side]["wall_clock_total_us"].dropna()
        vals = vals.sort_values().values
        if not len(vals):
            continue
        cdf = np.arange(1, len(vals) + 1) / len(vals)
        ax.plot(vals, cdf, color=col, label=label, linewidth=2)
        for q, ls in [(50, "--"), (95, ":")]:
            vq = pct(vals, q)
            ax.axvline(vq, color=col, linestyle=ls, alpha=0.7,
                       label=f"{label} p{q}={vq / 1000:.1f}ms")
    ax.set_xscale("log")
    ax.set_xlabel("Latency (µs, log scale)", fontsize=11)
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
    print("\n=== M4a Analysis Summary ===")
    print(f"Runs analyzed (combined):          {combined.get('run_count')}")
    print(f"Zela p50 / p95:                    {z.get('p50_us', 0) / 1000:.2f}ms / {z.get('p95_us', 0) / 1000:.2f}ms")
    print(f"Baseline p50 / p95:                {b.get('p50_us', 0) / 1000:.2f}ms / {b.get('p95_us', 0) / 1000:.2f}ms")
    print(f"Median ratio (combined):           {ratios.get('median_ratio')}x")
    print(f"p95 ratio (combined):              {ratios.get('p95_ratio')}x")
    print(f"Zela slot consistency (1 slot):    {sc.get('zela_1slot_pct')}%")
    print(f"Baseline slot consistency (1 slot):{sc.get('baseline_1slot_pct')}%")
    print(f"Zela bimodality: fast(<5ms)={bm.get('fast_mode_pct')}%  slow(>200ms)={bm.get('slow_mode_pct')}%")
    print("============================\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze.py <dataset_dir> [<dataset_dir> ...]", file=sys.stderr)
        sys.exit(1)

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    datasets = []
    for path in sys.argv[1:]:
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


if __name__ == "__main__":
    main()
