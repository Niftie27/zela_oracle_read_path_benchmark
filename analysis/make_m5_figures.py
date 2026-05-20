#!/usr/bin/env python3
"""Regenerate fig1_cdf, fig2_per_region, fig3_confusion_matrix from frozen M5 inputs.

Inputs:
  analysis/m5_manifest.txt
  zela_datasets/<entry>/feeds.csv + aggregates.csv
  leader_correlation_results/runs_with_leaders_offset_1.csv
  analysis/post_hoc/route_test_results/*.txt

Outputs:
  docs/figures/fig1_cdf.png
  docs/figures/fig2_per_region.png
  docs/figures/fig3_confusion_matrix.png
"""

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

# Add parent dir to path so analyze.py can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import load_dataset_batch, pct

REPO_ROOT = Path(__file__).resolve().parent.parent
FIGURES_DIR = REPO_ROOT / "docs" / "figures"

Z_CLIENT_COL = "#1f77b4"
Z_SERVER_COL = "#2ca02c"
B_COL = "#ff7f0e"

TIER_LABELS = ["fr2", "mid", "slc", "tyo"]
TIER_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]


def _plain_log(x, _pos):
    return f"{int(x)}" if x >= 1 else f"{x:g}"

PLAIN_LOG = FuncFormatter(_plain_log)


def load_manifest(manifest_path: Path) -> list[str]:
    entries = []
    for line in manifest_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def make_fig1_cdf(manifest_path: Path, out_path: Path):
    """CDF: Zela server-side (dashed), Zela client e2e, baseline client e2e."""
    entries = load_manifest(manifest_path)
    datasets_dir = REPO_ROOT / "zela_datasets"

    z_server_vals, z_client_vals, b_client_vals = [], [], []
    for entry in entries:
        path = datasets_dir / entry
        if not path.exists():
            print(f"  WARNING: {entry} not found, skipping", file=sys.stderr)
            continue
        try:
            ds = load_dataset_batch(str(path))
        except Exception as e:
            print(f"  WARNING: failed to load {entry}: {e}", file=sys.stderr)
            continue
        aggs = ds["aggs"]
        z_df = aggs[aggs["side"] == "zela"]
        b_df = aggs[aggs["side"] == "baseline"]
        z_server_vals.extend(z_df["server_wall_clock_us"].dropna().tolist())
        z_client_vals.extend(z_df["client_wall_clock_us"].dropna().tolist())
        b_client_vals.extend(b_df["client_wall_clock_us"].dropna().tolist())

    fig, ax = plt.subplots(figsize=(10, 6))
    for vals, col, label, ls in [
        (z_server_vals, Z_SERVER_COL, "Zela server-side", "--"),
        (z_client_vals, Z_CLIENT_COL, "Zela client e2e", "-"),
        (b_client_vals, B_COL, "Baseline client e2e (Helius)", "-"),
    ]:
        arr = np.array(vals, dtype=float) / 1000.0  # µs → ms
        arr = np.sort(arr)
        if not len(arr):
            continue
        cdf = np.arange(1, len(arr) + 1) / len(arr)
        ax.plot(arr, cdf, color=col, label=label, linewidth=2, linestyle=ls)
        p50v = float(np.percentile(arr, 50))
        p95v = float(np.percentile(arr, 95))
        ax.axvline(p50v, color=col, linestyle=":", alpha=0.6,
                   label=f"{label} p50={p50v:.1f}ms")
        ax.axvline(p95v, color=col, linestyle="-.", alpha=0.4,
                   label=f"{label} p95={p95v:.1f}ms")

    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(PLAIN_LOG)
    ax.set_xlabel("Latency (ms)", fontsize=12)
    ax.set_ylabel("Fraction of runs ≤ x", fontsize=12)
    ax.set_title("CDF of read-path latency — Zela client vs baseline client vs Zela server-side",
                 fontsize=12)
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {out_path}")


def make_fig2_per_region(route_results_dir: Path, out_path: Path):
    """Box plots: per-region latency (warm=last 95 of 100 runs per route)."""
    routes = [
        ("fr2", "fr2_session.txt", "fr2\nFrankfurt"),
        ("dx1", "dx1_session.txt", "dx1\nDubai"),
        ("ewr", "ewr_session.txt", "ewr\nNewark NJ"),
        ("slc", "slc_session.txt", "slc\nSalt Lake City"),
        ("tyo", "tyo_session.txt", "tyo\nTokyo"),
        ("auto", "auto_session.txt", "auto\n(default)"),
    ]
    data = []
    labels = []
    for name, fname, label in routes:
        fpath = route_results_dir / fname
        if not fpath.exists():
            print(f"  WARNING: {fpath} not found, skipping", file=sys.stderr)
            continue
        times_us = [int(l.strip()) for l in fpath.read_text().splitlines() if l.strip()]
        warm = times_us[5:]  # drop first 5 warmup
        data.append(np.array(warm, dtype=float) / 1000.0)  # µs → ms
        labels.append(label)

    fig, ax = plt.subplots(figsize=(10, 6))
    bp = ax.boxplot(data, patch_artist=True,
                    medianprops=dict(color="black", linewidth=2))
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
    for patch, color in zip(bp["boxes"], colors[:len(data)]):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Client e2e latency (ms)", fontsize=12)
    ax.set_title(
        "Per-region client latency from Prague — 100 runs per route, warm=last 95",
        fontsize=12)
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {out_path}")


def make_fig3_confusion_matrix(correlation_csv: Path, out_path: Path):
    """Row-normalised confusion matrix heatmap from leader correlation CSV."""
    if not correlation_csv.exists():
        print(f"  ERROR: {correlation_csv} not found", file=sys.stderr)
        sys.exit(1)

    from collections import defaultdict
    counts = defaultdict(lambda: defaultdict(int))
    with open(correlation_csv, newline="") as f:
        for row in csv.DictReader(f):
            exp = row.get("expected_tier", "").strip()
            obs = row.get("observed_tier", "").strip()
            if exp and obs:
                counts[exp][obs] += 1

    tiers = TIER_LABELS
    matrix = np.zeros((len(tiers), len(tiers)), dtype=float)
    for i, exp in enumerate(tiers):
        row_total = sum(counts[exp].values())
        if row_total:
            for j, obs in enumerate(tiers):
                matrix[i, j] = counts[exp][obs] / row_total

    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(matrix, cmap="Blues", vmin=0, vmax=1)
    plt.colorbar(im, ax=ax, label="Row-normalised fraction")
    ax.set_xticks(range(len(tiers)))
    ax.set_yticks(range(len(tiers)))
    ax.set_xticklabels(tiers, fontsize=11)
    ax.set_yticklabels(tiers, fontsize=11)
    ax.set_xlabel("Observed tier", fontsize=12)
    ax.set_ylabel("Expected tier", fontsize=12)
    ax.set_title("Routing confusion matrix at offset +1", fontsize=12)
    for i in range(len(tiers)):
        for j in range(len(tiers)):
            val = matrix[i, j]
            txt_color = "white" if val > 0.5 else "black"
            ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                    fontsize=11, color=txt_color, fontweight="bold")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {out_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Regenerate M5 figures from frozen inputs")
    ap.add_argument("--manifest",
                    default=str(REPO_ROOT / "analysis" / "m5_manifest.txt"),
                    help="Path to M5 dataset manifest")
    ap.add_argument("--correlation-csv",
                    default=str(REPO_ROOT / "leader_correlation_results" /
                                "runs_with_leaders_offset_1.csv"),
                    help="Path to leader correlation CSV (offset +1)")
    ap.add_argument("--route-results-dir",
                    default=str(REPO_ROOT / "analysis" / "post_hoc" /
                                "route_test_results"),
                    help="Directory containing *_session.txt route test files")
    ap.add_argument("--out-dir",
                    default=str(FIGURES_DIR),
                    help="Output directory for figures")
    ap.add_argument("--only", choices=["fig1", "fig2", "fig3"],
                    help="Regenerate only one figure")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    manifest = Path(args.manifest)
    corr_csv = Path(args.correlation_csv)
    route_dir = Path(args.route_results_dir)

    if not args.only or args.only == "fig1":
        print("Generating fig1_cdf.png...")
        make_fig1_cdf(manifest, out_dir / "fig1_cdf.png")

    if not args.only or args.only == "fig2":
        print("Generating fig2_per_region.png...")
        make_fig2_per_region(route_dir, out_dir / "fig2_per_region.png")

    if not args.only or args.only == "fig3":
        print("Generating fig3_confusion_matrix.png...")
        make_fig3_confusion_matrix(corr_csv, out_dir / "fig3_confusion_matrix.png")

    print("Done.")


if __name__ == "__main__":
    main()
