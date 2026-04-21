# M3 Results — Orchestrator + First Benchmark Dataset

**Status:** Complete. First validated 100-run dataset collected.
**Dataset:** `dataset_2026_04_21_late_morning` (April 21, 2026, late morning collection window)
**Procedure revision:** `56df99e1e31be5ac2e9e3c08d2cae261d0757490`
**Orchestrator commit:** `0ce1459`

M3 delivers two things: (1) the orchestrator script that automates paired
Zela-vs-baseline runs and writes structured CSV output, and (2) the first
real benchmark dataset — 100 paired runs, 2000 per-feed rows, 200
aggregate rows, zero errors.

The dataset also surfaces an unexpected finding: the Zela path is
**bimodal**, not a tight distribution with a thin tail. This is the most
important thing in this document and drives M4 design.

---

## Orchestrator (orchestrator/orchestrate.py)

Python 3 script. External dep: `requests`. Runs from workspace root.

### What it does per invocation

1. Reads config from env: `ZELA_KEY_ID`, `ZELA_KEY_SECRET`, `ZELA_PROCEDURE`,
   `ZELA_PROCEDURE_REVISION`, `BASELINE_RPC_URL`.
2. Obtains Zela Executor JWT (`scope=zela-executor:call`) at startup.
3. Loops N times in strict alternation: zela call → sleep 1s →
   baseline call → sleep 1s → next zela call, no trailing sleep.
4. Writes two CSV files per invocation in
   `data/run_YYYYMMDD_HHMMSS/`:
   - `feeds.csv` — one row per (run_id, side, feed)
   - `aggregates.csv` — one row per (run_id, side)
5. Flushes per row, so crashes preserve partial data.
6. Progress indicator to stderr; stdout silent.
7. Error handling: single failed call writes a row with `error=true`,
   continues to next run. JWT refresh on 401.

### Acceptance check vs M3 spec

| Criterion | Result |
|---|---|
| Smoke test `--runs 3` end-to-end clean | ✓ (Reviewer, 60 feeds rows + 6 aggregate rows) |
| Full 100-run end-to-end clean | ✓ (2000 feeds rows + 200 aggregate rows, zero errors) |
| run_id correctly paired between sides | ✓ |
| Side labels correct (zela vs baseline) | ✓ |
| `unique_slots_count` computed correctly | ✓ |
| Progress to stderr, stdout silent | ✓ |
| JWT refresh on 401 | ✓ (Reviewer tested with intentionally invalid token) |
| Strict alternation enforced | ✓ |
| Boolean formats consistent (lowercase "true"/"false") | ✓ (after M3 review fix) |

---

## Dataset: dataset_2026_04_21_late_morning (100 runs)

### Aggregate latency summary

All values in microseconds. Computed from `aggregates.csv`,
`error=false` rows only (no errors in this dataset).

| Metric | Zela path | Baseline path | Ratio |
|---|---|---|---|
| min | 904 | 391,273 | — |
| p50 (median) | **2,076** | **484,681** | **233×** |
| p95 | 226,928 | 619,844 | 2.7× |
| p99 | 341,750 | 699,528 | 2.0× |
| max | 341,750 | 699,528 | 2.0× |
| mean | 57,856 | 497,428 | 8.6× |

**Headline:** median ratio 233×. But see bimodal distribution below —
p95 tells a very different story, and the headline needs both.

### Slot consistency

| Side | 1 unique slot | 2 unique slots | 3 unique slots |
|---|---|---|---|
| Zela (100 runs) | 88 | 12 | 0 |
| Baseline (100 runs) | 7 | 88 | 5 |

Zela keeps all 10 feeds in one Solana slot in 88% of runs. Baseline
crosses at least one slot boundary in 93% of runs. This is a
structural property, not a latency one — Zela returns a consistent
price snapshot across feeds, baseline does not.

### Zela distribution (the unexpected part)

| Bucket | Count / 100 |
|---|---|
| < 5 ms | 72 |
| 5–50 ms | 2 |
| 50–200 ms | 4 |
| > 200 ms | 22 |

**This is bimodal.** 72% of runs are in the "fast" mode (sub-5 ms),
22% are in the "slow" mode (200–340 ms). Only 6 runs sit in between.

Inside each slow run, **all 10 feeds are slow**, not just one. Example,
run 11: every feed 26–61 ms, aggregate 341 ms. Example, run 2: every
feed 18–37 ms, aggregate 226 ms.

This is not a single bad feed or a single bad RPC call. It's a
whole-batch-slow state that happens roughly once in every five runs.

### Hypothesis: Zela proxy connection pool reset

Three candidate explanations:

1. **Zela executor cache eviction.** The executor caches compiled
   procedures; heavy multi-tenant usage could evict the procedure,
   triggering a reload for the next invocation. Unlikely to manifest
   across 10 feeds in a single invocation.
2. **Zela proxy connection pool reset.** The Zela proxy maintains
   HTTP/gRPC connections to downstream Solana RPC nodes. If the pool
   drops a connection between invocations, the next batch pays the
   full handshake cost on every feed. 10 feeds × ~25 ms ≈ 250 ms,
   which matches observed slow-mode aggregates.
3. **Solana RPC node fluctuation at the downstream.** The RPC node
   Zela talks to could be overloaded. But if this were the cause, we
   would expect to see similar tail behavior on the baseline side
   (which talks to a different RPC, Helius). Baseline shows no such
   bimodality — its distribution is tight around 450–500 ms with a
   modest tail.

**Hypothesis 2 is most consistent with the data.** Not confirmed; M4
will note the hypothesis without claiming it as fact.

### Baseline distribution (for comparison)

| Bucket | Count / 100 |
|---|---|
| < 400 ms | 1 |
| 400–500 ms | 60 |
| 500–600 ms | 31 |
| 600–700 ms | 8 |

Unimodal, tight around the median. Typical remote-RPC behavior.
No second mode.

### Per-feed observations

- **SOL/USD (position 1 in batch)** is consistently the slowest in
  fast-mode runs (~500–800 µs vs ~130–250 µs for later positions).
  Same first-call-in-batch overhead we saw in M1/M2. In slow-mode
  runs it's still usually slowest but the gap is proportionally
  smaller.
- **BONK/USD anomaly flagged in M2 is no longer clearly anomalous**
  in this larger dataset. Its per-feed p95 is within range of other
  feeds. The M2 observation was probably a small-sample artifact.

---

## Findings

### 1. Median ratio is not the story p95 tells

In a short 5-run test (M2), the Zela path looked like a clean
low-variance distribution with an occasional outlier. Over 100 runs,
the "outlier" turns out to be a second mode containing ~22% of runs.

This means **median ratio (233×) significantly overstates typical
improvement** from Zela vs standard RPC. A faithful report has to
show both median and p95, or the bimodal reality is hidden.

At p95, Zela path is only 2.7× faster than baseline. Not 233×.

### 2. Slot consistency is an independent axis

Zela holds 10 feeds in one slot in 88% of runs. Baseline does that
in only 7%. This matters for workflows that want a consistent
cross-asset price snapshot (basket pricing, arbitrage detection,
risk checks). It is an orthogonal value proposition to raw speed
and should be called out separately in M4 README.

### 3. Sample size matters

The M2 5-run results were not wrong — they just missed the second
mode because 5 samples is too few to see a 22% event reliably. The
100-run result changes the story materially. This validates the
choice of 100-run minimum target and argues for at least a second
100-run dataset to verify bimodality is consistent across time.

### 4. One collection window is not enough

All 100 runs in this dataset were collected from a single operator
machine (Prague) in a single time window (late morning, April 21).
Bimodality could be:
- Time-of-day load dependent (would disappear in a different window)
- Operator-side network dependent (would disappear from a different
  client location)
- Zela-infrastructure dependent (would persist across time and client)

**A second dataset in a different time window will distinguish these.**
This is not optional — M4 analysis should integrate at least two
datasets before publishing headline numbers.

---

## Carry-forward to M4

### Required for M4 analysis

- At least one more 100-run dataset, ideally in a different time
  window (evening or morning), to check bimodality consistency.
- Statistical summary of both datasets combined, with per-dataset
  breakout to show consistency or divergence.
- Bimodality characterization: what fraction of runs are in each
  mode across datasets.
- Per-feed p50 and p95 tables for both sides.
- First-call-in-batch overhead quantified per side.

### Wording discipline for M4 README

- Never report median without also reporting p95.
- Never report Zela speedup as a single number; always show the
  median ratio and the p95 ratio side by side.
- Do not state the bimodality hypothesis (connection pool reset) as
  fact. Present data, name the most likely cause, say it is
  unconfirmed.
- Call out slot consistency as a separate finding from latency,
  not as a secondary caveat.
- Disclose baseline endpoint (Helius free tier), client location
  (Prague), and collection windows explicitly.

### Open question for M4 design

Do we publish the raw CSVs in the repo, or only the aggregated stats
and charts? Pros of raw CSV: reproducibility, trust. Cons: adds
~200 KB per dataset, readers may analyze and re-frame findings.
Recommendation: publish both.

---

## Data locations

- **In-repo orchestrator code:** `orchestrator/orchestrate.py` (committed)
- **This dataset (not in repo):** `~/zela_datasets/dataset_2026_04_21_late_morning/`
- **Future datasets (when collected):** `data/run_YYYYMMDD_HHMMSS/` inside
  repo (gitignored), rename and move to
  `~/zela_datasets/dataset_YYYY_MM_DD_timeofday/` after collection.
  Naming convention: `dataset_YYYY_MM_DD_timeofday` where `timeofday`
  is `morning`, `late_morning`, `evening`, or `night`.
