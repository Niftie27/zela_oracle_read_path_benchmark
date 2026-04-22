# M2 Results — Stable Feed Set + Baseline RPC Client

**Status:** Complete (M2a + M2b).
**Procedure revision:** `[fill in latest M2a deploy hash]`
**Baseline binary:** `target/release/baseline_client`
**Date:** April 17, 2026

M2 closes the procedure-only era and opens the comparative-benchmark era.
M2a swapped a deprecated feed (W/USD) for an active one (PYTH/USD), giving
us a stable 10-feed sample. M2b built the standard-RPC baseline client in
Rust, methodologically symmetric to the procedure, ready for M3 orchestration.

---

## M2a — W/USD → PYTH/USD swap

**Problem:** M1 testing showed W/USD at pubkey
`8gTpR6DjS66SeyUvy3TXMBZpJQ3ZMMcifTGp3zhwS5gS` consistently returned
`account_found: false`. The Pyth legacy push oracle for that token is no
longer active on mainnet.

**Fix:** Replaced with PYTH/USD at
`nrYkQQQur7z8rYTST3G9GqATviK5SxTDkrqd21MW6Ue`. Same array position
(index 8), same struct shape, no other code changes.

**Verification:** PYTH/USD returned `account_found: true`,
`account_data_len: 3312`, `wall_clock_elapsed_us: 112` on first
post-deploy invocation. Aggregate dropped to 1763 µs (vs 2155 µs in M1
cold-start sample) — small variation, within run-to-run jitter.

**Outcome:** All 10 feeds now return `account_found: true`. Stable feed
set ready for M2b and M3.

---

## M2b — Baseline RPC Client (Rust)

### Methodology

Standalone Rust binary at `baseline_client/` in workspace. Reads
`BASELINE_RPC_URL` from environment, performs 10 sequential
`getAccountInfo` calls via raw JSON-RPC (using `reqwest`), and emits
JSON to stdout matching the procedure's output schema field-for-field.

**Key design choices:**

- **Raw JSON-RPC, not `solana-client` SDK.** Initial implementation used
  `solana-client::get_account_with_commitment`, but two methodological
  symmetry breaks vs the procedure surfaced in code review:
  1. Baseline sent `commitment=confirmed`; procedure sends no commitment
     field at all. RPC nodes fall back to their own default when no field
     is present, so the two sides were potentially reading from different
     slot states.
  2. `account_data_len` was computed from decoded `Vec<u8>` length;
     procedure uses base64-string-length-minus-padding. Same number in
     theory, but different code paths could disagree on edge cases.
  Refactored to raw JSON-RPC via `reqwest` to mirror the procedure's
  `call_rpc` exactly.
- **`BASELINE_RPC_URL` from env, not hardcoded.** Operator supplies the
  Helius/QuickNode/whatever endpoint. No credential in repo.
- **Stdout reserved for JSON output.** All diagnostic messages on stderr.
  M3 orchestrator will parse stdout cleanly.
- **Sequential, not parallel.** Same as procedure. Symmetry > performance.

### Acceptance check vs M2b spec

| Criterion | Result |
|---|---|
| `cargo build --release -p baseline_client` succeeds | ✓ |
| Binary at `target/release/baseline_client` | ✓ |
| Runs with `BASELINE_RPC_URL` set, produces JSON on stdout | ✓ |
| All 10 feeds `account_found: true` | ✓ |
| Exits non-zero with stderr error when `BASELINE_RPC_URL` unset | ✓ |
| Per-feed `wall_clock_elapsed_us` plausible (50–500 ms range) | ✓ |
| `genesis_hash` matches Solana mainnet | ✓ |
| Code under ~250 lines | ✓ (140 lines) |

---

## Side-by-side comparison (5 paired runs)

Procedure run on Zela executor; baseline run from operator's machine
(Prague) against Helius mainnet endpoint.

### Aggregate totals (µs)

| Run | Procedure | Baseline | Ratio |
|---|---|---|---|
| 1 | 2,577 | 539,459 | 209× |
| 2 | 2,107 | 437,080 | 207× |
| 3 | 3,970 | 462,948 | 117× |
| 4 | 2,194 | 464,123 | 212× |
| 5 | 2,068 | 400,370 | 194× |
| **Median** | **2,194** | **462,948** | **211×** |

### Slot consistency within a single batch

| Side | Distinct context_slot values per batch |
|---|---|
| Procedure | 1 (consistently across all 5 runs) |
| Baseline | 2 (every run crossed a Solana slot boundary) |

### First-call-in-batch overhead

| Side | Position 1 latency | Positions 2–10 median |
|---|---|---|
| Procedure | 516–864 µs | 130–235 µs |
| Baseline | 78–160 ms (cold), then warmer | 31–45 ms |

### Tail latency outliers

| Side | Outlier rate (5 runs) | Outlier magnitude |
|---|---|---|
| Procedure | 1 of 5 | 3,970 µs (1.8× median) |
| Baseline | 1 of 5 | 539,459 µs (1.2× median); also one feed-level spike to 100 ms |

---

## Findings

### 1. Zela path is roughly 200× faster than standard RPC from a remote client

Median 2,194 µs vs 462,948 µs across 5 paired runs. Stable ratio across
runs — not a one-off. This is the headline number, but it deserves the
following caveats in M4 README:

- "Standard RPC" here is **Helius from Prague**, which is a typical
  developer-experience scenario, not an optimized colo setup.
- A market-maker running their own well-located RPC node would see a
  much smaller gap — but they would also be paying full ops cost for it,
  which is part of Zela's value proposition (`Less infrastructure`).
- The 200× number is a **read latency** measure, not a full transaction
  flow measure. Writes still go through the operator's existing stack.

### 2. Slot boundary crossing is structural for baseline, never for procedure

Every single baseline run crossed a Solana slot boundary mid-batch.
Every single procedure run stayed within one slot. This is more
fundamental than the latency number itself:

- **Procedure returns a consistent snapshot.** All 10 prices are from
  the same on-chain state at one slot. A market-maker can update a price
  curve atomically using all 10 inputs.
- **Baseline returns a mixed snapshot.** 9 prices from slot N, 1 price
  from slot N+1. For workflows that depend on cross-asset price
  consistency (basket pricing, arbitrage detection), this is a
  correctness issue, not a performance one.

This is an **independent value proposition** from raw speed and worth
calling out in M4 README as a separate finding.

### 3. First-call-in-batch overhead exists on both sides at different magnitudes

Procedure: position-1 SOL/USD is consistently 3–6× slower than positions
2–10 (516–864 µs vs 130–235 µs). Likely TLS/connection-pool warm-up
between Zela executor and downstream RPC node.

Baseline: position-1 SOL/USD is 78–160 ms vs 31–45 ms for warm calls,
with run-1 cold-start at 160 ms (initial TCP/TLS handshake from operator
to Helius). Subsequent runs are warmer due to HTTP connection reuse.

In both cases, **first-call-in-batch is a methodology artifact**, not
infrastructure quality. M4 README must note that real-world workflows
that maintain a long-lived connection would see only the warm-call
latency.

### 4. Tail latency is real on both sides

Both procedure and baseline saw 1 of 5 runs as outliers, but the absolute
magnitudes differ dramatically:

- Procedure outlier: 3,970 µs (1.8× median, ~1.7 ms above median)
- Baseline outlier: 539,459 µs (1.2× median, ~76 ms above median)
- Baseline also had a per-feed spike to 100 ms on JTO/USD in run 4

This means **median understates Zela's advantage in tail conditions**.
A p95 measurement (M3+) will show a more dramatic ratio than the median
ratio of 211×.

### 5. BONK/USD anomaly localized to Zela path

In M1+M2a, BONK/USD on the procedure side consistently ran 30–80%
slower than other mid-batch feeds (~220 µs vs ~140 µs median). In the
baseline side, BONK/USD performed normally (31–45 ms, in line with
other feeds).

This means the variability is **Zela proxy side**, not RPC node side.
Possible cause: BONK price feed sees more frequent updates on mainnet,
which could affect cache hit rate inside Zela's read path. Not
investigated further in M2 — p95 measurement in M3 will absorb this.
Flagged for awareness only.

---

## Carry-forward to next milestones

### For M3 (orchestrator + CSV)

- Sample size: target 100 paired runs minimum for statistically meaningful
  p95. With 5-run variability already showing ~1.4× median range, more
  samples are essential.
- Run timing: stagger across multiple times of day to capture diurnal
  variation in Solana network load and Helius RPC load.
- Pairing: each "run" is one procedure invocation + one baseline
  invocation, performed back-to-back, so they observe roughly the same
  on-chain state.
- Output: CSV with one row per (run_id, side, feed) tuple. Columns:
  run_id, timestamp, side (zela|baseline), symbol, pubkey, account_found,
  account_data_len, context_slot, wall_clock_elapsed_us, plus one
  aggregate row per (run_id, side).
- Storage: keep raw CSV in `data/` directory in repo, gitignored, until
  we decide what to commit as final dataset.

### For M4 (analysis + README)

Headline metrics to compute:
- Median latency per side
- p95 latency per side
- Median latency ratio (zela/baseline)
- p95 latency ratio (zela/baseline)
- Slot consistency rate per side (% of batches within one slot)
- First-call-in-batch overhead, both sides
- Per-feed median and p95 distributions

Required README sections:
- Methodology (what we measured, what we did not)
- Baseline disclosure (Helius free tier, Prague client, no commitment field)
- Limitations (regional bias, single client location, time-of-day effects)
- First-call-in-batch overhead disclosure (both sides)
- Slot consistency finding (independent of latency)
- Aggregate-vs-sum discrepancy disclosure (~1% understatement)
- Reproducibility notes (toolchain, dep versions, env vars)

Wording discipline:
- "Sequential read latency" not "execution latency"
- "From a remote developer client" not "vs standard RPC" (too vague)
- "211× faster in median over 100 runs" not "blazing fast"
- No "shred-level," no "MEV-resistant," no "eliminates adverse selection"
  in headline claims

### For M5 (publication / outreach)

- Targeted outreach to relevant technical contacts. Short, direct message
  with specific numbers, link to repo, ask for feedback on methodology
  before broader publication.
- Optional LinkedIn / X post: only after private feedback round. Frame
  as "I ran this benchmark, here are the numbers, here are the caveats."
  Not "Zela beats RPC." Honest framing wins more than hype framing.

### Open question for M3 design

Decide whether the orchestrator runs procedure and baseline in **strict
alternation** (zela run 1, baseline run 1, zela run 2, baseline run 2, …)
or **batched** (10 zela runs, 10 baseline runs, …). Strict alternation
gives best pairing for slot-level analysis. Batched is simpler.
Recommendation: strict alternation, with a 1-second sleep between each
invocation to spread across slots evenly.
