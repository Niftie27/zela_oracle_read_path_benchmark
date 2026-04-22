# M4 Results — Analysis, Figures, and Public README

**Status:** Complete. Public research artifact published.
**Final commit:** `8a80413`
**Scope:** Statistical analysis across five collection windows, five generated figures, and external-facing `README.md`. Raw CSV datasets committed for full reproducibility.

M4 delivers three things: (1) `analysis/analyze.py` that computes aggregate statistics and generates figures from one or more orchestrator-produced datasets, (2) five published figures in `docs/figures/` with associated `summary.json`, and (3) the root `README.md` as a standalone research artifact.

The milestone also documents the **load-dependent bimodality** finding — the most significant observation in M4 — which was surfaced only after expanding from three to five collection windows.

---

## Deliverables

### 1. Analysis script (`analysis/analyze.py`)

Python 3 script. External deps: `pandas`, `numpy`, `matplotlib`. Runs from workspace root.

**What it does:**
- Loads one or more dataset directories (each containing `feeds.csv` + `aggregates.csv`)
- Filters error rows and reports counts to stderr
- Computes per-dataset and combined statistics: p50/p95/p99 latency per side, median/p95 ratios, slot consistency rates, bimodality breakdown
- Uses composite key `(dataset_name, run_id)` when combining datasets to avoid run_id collisions (each dataset restarts at 1)
- Writes `docs/figures/summary.json` with full numeric results
- Generates five PNG figures
- Prints a short text summary to stdout

**Acceptance:**

| Criterion | Result |
|---|---|
| Computes per-dataset + combined stats | ✓ |
| summary.json contains all claimed numbers | ✓ |
| Figures regenerable from source | ✓ |
| Error rows filtered correctly | ✓ (3 Zela rows in afternoon filtered) |
| Composite key handles run_id collisions | ✓ (fixed after initial bug) |

### 2. Five figures (`docs/figures/`)

All axes in milliseconds. Log-scale tick labels rendered as plain integers (1, 10, 100, 1000) via custom formatter, not scientific notation.

| Figure | Purpose |
|---|---|
| `latency_distribution.png` | Per-window histogram grid (5 rows × 2 cols), showing bimodality on Zela and unimodal shape on Baseline. Shared Y axis within each column for visual comparability. |
| `cdf.png` | Cumulative distribution of combined latency with p50/p95 reference lines. Shows the Zela plateau between ~80% and ~89% (the gap between fast mode and slow mode). |
| `slot_consistency.png` | Non-stacked grouped bars per window. Zela 88–97%, Baseline 6–11%. Redesigned from earlier stacked version — the single-slot rate is the insight, stacking hid it. |
| `time_of_day.png` | Box plots per window. Zela panel log-scale to keep fast-mode median visible; Baseline panel linear. Makes the load-dependent slow-mode frequency visible. |
| `per_feed_latency.png` | Per-symbol p50/p95. Kept on disk for archive; not referenced in README since the per-feed table conveys the same data more precisely. |

### 3. Public README (`README.md`, 402 lines)

External-facing research artifact. Sections:

- Title + one-paragraph intro with collection summary (497/500 paired runs across five windows)
- Headline numbers (median 231×, p95 2.8×, slot consistency 91% vs 7%) followed by hero histogram grid
- Methodology (measured vs not-measured, two paths with revision hash, 10-feed table, pairing table with all five windows)
- Results: aggregate latency table, bimodality section with CDF, slot consistency, per-feed breakdown (table only), time-of-day
- Findings: four numbered items, each with median + p95 framing
- Limitations: seven specific bullets
- Reproducibility: build, deploy, orchestrate, analyze, with example outputs
- Repository structure and context

Tone: neutral research artifact, no marketing language. Every numeric claim paired with a caveat in the same paragraph.

### 4. Raw datasets (`zela_datasets/`)

Five 100-run datasets committed to the repository (~1 MB total):

| Dataset | Local time | Runtime | Errors |
|---|---|---|---|
| `dataset_2026_04_21_late_morning` | ~11:00 | ~1.5 min | 0 |
| `dataset_2026_04_21_afternoon` | ~13:00 | 4 min 48 s | 3 (Zela) |
| `dataset_2026_04_21_evening` | ~17:00 | 4 min 51 s | 0 |
| `dataset_2026_04_21_night` | ~02:00 | ~5 min | 0 |
| `dataset_2026_04_22_morning` | ~09:26 | ~5 min | 0 |

Committed for full reproducibility — anyone can re-run `analyze.py` against these CSVs and reproduce every number in the README.

---

## Combined statistics (497 Zela / 500 Baseline paired runs)

| Metric | Zela | Baseline | Ratio |
|---|---|---|---|
| p50 | 2.10 ms | 486.19 ms | **231×** |
| p95 | 233.36 ms | 647.71 ms | **2.8×** |
| p99 | 408.56 ms | 788.70 ms | 1.9× |
| Single-slot rate | 91.1% | 7.4% | — |
| Fast mode (<5 ms) | 79.9% | — | — |
| Slow mode (>200 ms) | 11.5% | — | — |

---

## Key findings

### 1. Load-dependent bimodality (the most important finding)

Zela's latency distribution is bimodal in every window, but the **frequency of slow-mode events varies dramatically** with time of day:

| Window | Fast mode | Slow mode |
|---|---:|---:|
| late_morning | 72% | **22%** |
| afternoon | 81% | 9% |
| evening | 77% | 11% |
| night | 84% | **1%** |
| morning | 85% | 14% |

The night window (local Prague ~02:00, UTC ~23:55) had **1 slow-mode run out of 100**, versus 22 in the late-morning window seven hours earlier. This is not the bimodal shape changing — the shape is consistent — but the rate at which runs land in the slow mode.

**Hypothesis (unconfirmed):** HTTP connection pool reset between Zela proxy and downstream Solana RPC node. Ten sequential calls × ~25 ms handshake cost ≈ 250 ms, which matches the observed slow-mode aggregate. Higher network/RPC-node load would increase the rate of connection resets. This explanation fits the numbers but has not been verified against Zela infrastructure internals.

### 2. Slot consistency is a separate value proposition

Independent of raw latency: Zela holds all 10 feeds in a single Solana slot in 91% of runs; Baseline in 7%. For workflows that require a consistent multi-asset snapshot (basket pricing, cross-asset arbitrage, portfolio risk), this matters as much as speed.

### 3. Median alone is misleading for bimodal data

Combined median ratio 231× but combined p95 ratio only 2.8×. In the night window p95 ratio is 40× (because slow-mode events almost never happen), in late_morning it is 2.8× (because slow-mode happens 22% of the time). Reporting median alone would hide the bimodal reality. The README headline shows both.

### 4. First-call-in-batch overhead exists on both paths

SOL/USD (position 1) is consistently slower than feeds 2–10 on both sides: Zela 538 µs vs 149–230 µs warm, Baseline 95.9 ms vs 41.5–43.2 ms warm. This is TLS / HTTP connection setup in a fresh process. A long-lived client reusing connections would not see this tax. Documented as a methodology artifact, not an infrastructure-quality signal.

---

## Process observations

### Builder / Reviewer agentic workflow

Pattern used throughout M4:
- Builder (Codex / Claude in VS Code) writes code, figures, prose
- Reviewer (separate model) critiques, flags blockers and non-blockers
- Ping-pong until consensus, then commit

This worked well for the analysis script (Reviewer caught two bugs: p95 ratio computed incorrectly as `percentile(ratios, 95)` instead of `percentile(baseline,95) / percentile(zela,95)`; and run_id collision when combining datasets). It also worked well for the README (Reviewer caught an inaccurate "tens of megabytes" justification for not committing raw datasets).

### Figure readability iteration

The final figures went through five rounds of iteration after the initial "technically correct" version:

1. Overlay histogram unreadable → replaced with 5×2 facet grid
2. Facet grid Y-axes independent → shared within each column
3. time_of_day Zela panel used linear Y, compressing bimodality → log scale
4. per_feed_latency image strictly less informative than the table → removed from README, file kept
5. Axis labels cryptic ("wall_clock_total_us", "10^3") → human-readable ("Aggregate batch latency (ms)", "1, 10, 100, 1000")

This was more iteration than initially planned but necessary. The reader-perspective check ("I'm not an engineer, what do I see?") surfaced clarity issues that were invisible from the author's perspective.

### Wording discipline maintained

- Every claim reports median with p95, never alone
- Specific baseline disclosure ("Helius free tier from Prague") everywhere the ratio appears
- Slow-mode hypothesis explicitly framed as unconfirmed
- Slot consistency presented as separate finding from latency
- No "blazing fast", "revolutionary", "beats", "unleashes"
- Limitations section lists the seven most important caveats without softening

---

## Commit history (M4)

- `17a016e` feat(M4a): analysis script and figures
- `62e90fd` chore(M4a): regenerate analysis with 5 datasets
- `6662192` chore(M4a): finalize analysis script and figures for 5-dataset output
- `91ac5e9` chore(M4a): switch latency_distribution to facet grid layout
- `abffe11` feat(M4b): add README and publish raw datasets
- `8a80413` docs(M4): improve figure readability with ms units, integer log ticks, and simplified slot consistency chart

---

## Carry-forward to M5

### Distribution

- Targeted outreach to technical contacts at RockawayX and adjacent Solana infrastructure teams
- Short, direct. Specific numbers (231× median, 2.8× p95, 91% vs 7% single-slot)
- Link to repo
- Honest framing: "research artifact, not product claim"
- Ask for feedback on methodology, not endorsement

### Optional public post (after private feedback)

- Frame as "I ran this benchmark, here are the numbers, here are the caveats"
- Lead with slot consistency (the less-contested finding), not raw latency ratio
- Link to repo for reproducibility

### Future extensions (post-M5)

- Alternative baselines: Triton, QuickNode, self-hosted RPC
- Colocated baseline (same DC as RPC node) to isolate network from architecture
- Pull oracle feeds (`PriceUpdateV2`) instead of legacy push
- Longer runtime (week-over-week) for infrastructure stability claims
- Interactive web visualization
- Continuous benchmark via GitHub Actions

### Open questions

- Is the connection-pool-reset hypothesis correct? Requires Zela infrastructure visibility that is not publicly available.
- Does the bimodality exist on other Zela procedures, or is it specific to this read pattern?
- How does slot consistency rate change with different feed counts (1, 3, 25, 50 feeds instead of 10)?

These are candidates for future milestones if the project continues past M5.
