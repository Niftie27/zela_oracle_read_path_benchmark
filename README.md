# Zela vs Standard RPC: Oracle Read-Path Benchmark

A benchmark measuring the latency and slot-consistency of reading 10 Pyth
legacy push oracle accounts on Solana mainnet via two paths: (a) a Zela
WASM procedure running in Zela's leader-proximate executor, and (b) a
Rust client running on a remote developer machine in Prague reading the
same 10 accounts via a Helius free-tier RPC endpoint. 500 runs per side
across five collection windows on April 21–22, 2026. Three Zela runs
failed and were filtered, yielding 497 Zela / 500 baseline paired runs.

## Headline numbers

- **Median latency ratio (Baseline / Zela): 231×** — Zela p50 2.1 ms,
  Baseline p50 486 ms, across 497 paired runs.
- **p95 latency ratio: 2.8×** — Zela p95 233 ms, Baseline p95 648 ms.
  Zela's latency distribution is bimodal; see Bimodality below.
- **Slot consistency: Zela holds all 10 feeds in one Solana slot in 91%
  of runs; Baseline does so in 7%.**

![Latency distribution](docs/figures/latency_distribution.png)

---

## Methodology

### What was measured

Wall-clock time around each `getAccountInfo` call, measured on the side
making the call, and an aggregate wall-clock bracket around the full
10-call loop. All 10 calls are issued sequentially within one
invocation.

### What was not measured

- Transaction submission and block confirmation. This is a read-only
  benchmark.
- Oracle freshness. The on-chain slot at which Pyth last updated a
  price is not the same as the `context.slot` returned for the account
  read, and this benchmark reports the latter.
- Steady-state connection behavior of a long-lived client. Each
  baseline invocation starts a fresh process.

### The two paths

**Zela path.** WASM procedure at
[`procedures/oracle_read`](procedures/oracle_read) compiled to
`wasm32-wasip2`, deployed at revision
`56df99e1e31be5ac2e9e3c08d2cae261d0757490`, invoked via Zela executor
HTTP API. Per-feed timing wraps each `call_rpc("getAccountInfo", ...)`;
aggregate timing brackets the full 10-call loop.

**Baseline path.** Rust binary at
[`baseline_client`](baseline_client) using raw JSON-RPC via `reqwest`,
connecting to `https://mainnet.helius-rpc.com` (Helius free tier) from
a Prague developer machine. Same 10 accounts in the same order. Per-feed
timing wraps each HTTP call; aggregate timing brackets the loop.

Both sides issue `getAccountInfo` without specifying a commitment
field, so the RPC node's default commitment applies to both.

### Feeds

Ten Pyth legacy push oracle accounts, read in this order:

| # | Symbol | Pubkey |
|---|---|---|
| 1 | SOL/USD | `H6ARHf6YXhGYeQfUzQNGk6rDNnLBQKrenN712K4AQJEG` |
| 2 | BTC/USD | `GVXRSBjFk6e6J3NbVPXohDJetcTjaeeuykUpbQF8UoMU` |
| 3 | ETH/USD | `JBu1AL4obBcCMqKBBxhpWCNUt136ijcuMZLFvTP7iWdB` |
| 4 | USDC/USD | `Gnt27xtC473ZT2Mw5u8wZ68Z3gULkSTb5DuxJy7eJotD` |
| 5 | USDT/USD | `3vxLXJqLqF3JG5TCbYycbKWRBbCJQLxQmBGCkyqEEefL` |
| 6 | BNB/USD | `4CkQJBxhU8EZ2UjhigbtdaPbpTe6mqf811fipYBFbSYN` |
| 7 | JUP/USD | `g6eRCbboSwK4tSWngn773RCMexr1APQr4uA9bGZBYfo` |
| 8 | BONK/USD | `8ihFLu5FimgTQ1Unh4dVyEHUGodJ5gJQCrQf4KUVB9bN` |
| 9 | PYTH/USD | `nrYkQQQur7z8rYTST3G9GqATviK5SxTDkrqd21MW6Ue` |
| 10 | JTO/USD | `D8UUgr8a3aR3yUeHLu7v8FWK7E8Y5sSU7qrYBXUJXBQ5` |

### Pairing and sampling

The orchestrator at [`orchestrator/orchestrate.py`](orchestrator/orchestrate.py)
alternates Zela and Baseline invocations with a 1-second sleep between
calls, 100 paired runs per collection window. Five windows were
collected across two consecutive days:

| Window | Date (local) | Dataset |
|---|---|---|
| Late morning | 2026-04-21 ~11:00 | `dataset_2026_04_21_late_morning` |
| Afternoon | 2026-04-21 ~13:00 | `dataset_2026_04_21_afternoon` |
| Evening | 2026-04-21 ~17:00 | `dataset_2026_04_21_evening` |
| Night | 2026-04-22 ~02:00 (UTC 2026-04-21 23:55) | `dataset_2026_04_21_night` |
| Morning | 2026-04-22 ~09:26 | `dataset_2026_04_22_morning` |

Raw CSV datasets are checked into
[`zela_datasets/`](zela_datasets/) for full reproducibility. Combined
statistics live in
[`docs/figures/summary.json`](docs/figures/summary.json); the analysis
script that produces them is
[`analysis/analyze.py`](analysis/analyze.py).

---

## Results

All numbers in this section come from
[`docs/figures/summary.json`](docs/figures/summary.json), computed by
[`analysis/analyze.py`](analysis/analyze.py) across all five datasets.
Three error rows (3 Zela runs in the afternoon window) were filtered
before analysis.

### Aggregate latency

Per-dataset and combined p50 / p95 / p99, Zela versus Baseline, both in
milliseconds. Ratios are `baseline / zela` at matched percentiles.

| Dataset | Zela p50 | Zela p95 | Zela p99 | Baseline p50 | Baseline p95 | Baseline p99 | Median ratio | p95 ratio |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| late_morning | 2.07 ms | 223.27 ms | 249.54 ms | 484.16 ms | 614.60 ms | 660.10 ms | 233× | 2.8× |
| afternoon | 2.19 ms | 220.44 ms | 244.50 ms | 514.36 ms | 727.46 ms | 843.70 ms | 235× | 3.3× |
| evening | 2.12 ms | 325.50 ms | 477.89 ms | 525.63 ms | 675.06 ms | 790.30 ms | 248× | 2.1× |
| night | 2.13 ms | 12.88 ms | 192.03 ms | 429.19 ms | 516.16 ms | 544.47 ms | 202× | **40×** |
| morning | 1.99 ms | 365.05 ms | 408.38 ms | 486.17 ms | 646.06 ms | 712.84 ms | 244× | 1.8× |
| **combined** | **2.10 ms** | **233.36 ms** | **408.56 ms** | **486.19 ms** | **647.71 ms** | **788.70 ms** | **231×** | **2.8×** |

The night window's p95 ratio of 40× is not a typo: when slow-mode
events are rare (see next section), Zela's p95 collapses toward the
fast-mode median while baseline stays near its usual distribution.

### Bimodality (the key finding)

Zela's latency distribution is bimodal. The CDF shows a characteristic
plateau between approximately 80% and 89% of runs — the gap between
Zela's "fast mode" (median around 2 ms) and "slow mode" (median around
230 ms). In the combined dataset, about 80% of Zela runs sit in the
fast mode (< 5 ms), about 12% sit in the slow mode (> 200 ms), and
about 9% sit in between. Baseline shows no such plateau; its
distribution is unimodal and tight around 500 ms.

![CDF](docs/figures/cdf.png)

**The frequency of slow-mode events varies with time of day**, and this
is a stronger statement than "bimodality exists":

| Window | Fast mode (< 5 ms) | Intermediate | Slow mode (> 200 ms) |
|---|---:|---:|---:|
| late_morning | 72% | 6% | **22%** |
| afternoon | 81% | 9% | 9% |
| evening | 77% | 12% | 11% |
| night | 84% | 15% | **1%** |
| morning | 85% | 1% | 14% |

The night window (local Prague ~02:00, UTC ~23:55 the day before) had
1 slow-mode run out of 100, versus 22 in the late-morning window seven
hours earlier. The bimodal shape itself is consistent across windows;
what varies is how often a run lands in the slow mode. This pattern is
consistent with a load-dependent cause rather than a constant
overhead.

A plausible mechanism is HTTP connection pool reset between the Zela
proxy and the downstream Solana RPC node. Ten sequential calls × a
~25 ms per-call handshake cost ≈ 250 ms, which matches the observed
slow-mode aggregate. Higher network or RPC-node load would increase
the rate of connection resets, raising slow-mode frequency. This has
not been confirmed against Zela infrastructure internals.

### Slot consistency

Zela holds all 10 feeds in a single Solana slot in about 91% of runs
across the five datasets; Baseline does so in about 7%. This is a
structural property: Zela's executor runs physically close to the
current Solana leader, so 10 sequential `getAccountInfo` calls
typically complete well inside a single ~400 ms slot. A remote RPC
client reaching Helius from Prague crosses at least one slot boundary
in about 93% of runs in the observed data.

![Slot consistency](docs/figures/slot_consistency.png)

This matters for workflows that need a consistent multi-asset price
snapshot within one on-chain state: basket pricing, cross-asset
arbitrage detection, portfolio risk checks. Nothing in this benchmark
measures whether either path delivers *fresh* prices; only whether the
10 reads land in one slot.

### Per-feed breakdown

Per-feed p50 and p95 across all five datasets combined. Position is the
order in which the feed is read within each invocation.

| Pos | Symbol | Zela p50 | Zela p95 | Baseline p50 | Baseline p95 |
|---:|---|---:|---:|---:|---:|
| 1 | SOL/USD | 538 µs | 35.3 ms | 95.9 ms | 147.6 ms |
| 2 | BTC/USD | 230 µs | 23.8 ms | 42.5 ms | 60.9 ms |
| 3 | ETH/USD | 174 µs | 24.0 ms | 42.5 ms | 59.0 ms |
| 4 | USDC/USD | 161 µs | 25.4 ms | 42.3 ms | 61.5 ms |
| 5 | USDT/USD | 157 µs | 24.0 ms | 42.6 ms | 58.8 ms |
| 6 | BNB/USD | 154 µs | 22.4 ms | 42.4 ms | 59.3 ms |
| 7 | JUP/USD | 154 µs | 22.0 ms | 43.2 ms | 58.8 ms |
| 8 | BONK/USD | 151 µs | 22.6 ms | 41.9 ms | 58.9 ms |
| 9 | PYTH/USD | 151 µs | 22.5 ms | 41.5 ms | 58.1 ms |
| 10 | JTO/USD | 149 µs | 24.2 ms | 41.7 ms | 56.4 ms |

SOL/USD is position 1 in every batch and shows a first-call-in-batch
overhead on both sides: Zela p50 538 µs versus 149–230 µs for positions
2–10; Baseline p50 95.9 ms versus 41.5–43.2 ms for positions 2–10. This
is TLS / HTTP connection setup on the first call in a fresh process,
not an infrastructure-quality signal. A long-lived client reusing
connections would only see warm-call latency. The per-feed p95 values
are dominated by the slow-mode events discussed earlier: they measure
the ceiling during slow-mode, not a steady-state tail.

![Per-feed](docs/figures/per_feed_latency.png)

### Time of day

![Time of day](docs/figures/time_of_day.png)

Zela's median (~2.1 ms) and Baseline's median (~486 ms) are stable
across all five windows. What varies is the intensity of Zela's
slow-mode tail, as already shown above. Baseline's tail also varies —
the afternoon window has a handful of outliers above 800 ms — but the
variation is much smaller in relative terms (hundreds of milliseconds
on top of a ~500 ms floor).

---

## Findings

1. **Zela is roughly 231× faster than a remote RPC client at the median,
   but only 2.8× faster at p95 in the combined dataset.** Reporting the
   median alone hides the bimodal reality; both numbers are needed for a
   faithful picture. In a low-load window (night) the p95 ratio widens
   to 40× because slow-mode events are rare; in a high-load window
   (late morning) the ratio is 2.8× because slow-mode events hit 22%
   of runs.

2. **Zela returns a consistent multi-feed snapshot far more often than
   a remote RPC client.** 91% of Zela runs hold all 10 feeds in one
   slot; 7% of baseline runs do. This is independent of raw speed and
   matters for workflows that depend on cross-asset consistency.

3. **Zela's bimodality is consistent in shape but load-dependent in
   frequency.** The fast and slow modes are present in every window.
   The share of runs in the slow mode ranges from 1% (night) to 22%
   (late morning). This pattern is more suggestive of load-dependent
   connection churn than of a constant architectural tax.

4. **First-call-in-batch overhead exists on both paths and is a
   methodology artifact.** SOL/USD is consistently slower than feeds
   2–10 on both sides because it pays TLS / connection setup. A
   long-lived client reusing connections would not see this tax.

---

## Limitations

- **Single client location.** The baseline was run from one developer
  machine in Prague. A client colocated with Helius (or with any RPC
  node) would see a much smaller gap.
- **Single baseline endpoint.** Helius free tier. Other providers
  (Triton, QuickNode, self-hosted) may show different latency and
  different tail behavior.
- **Five 100-run datasets across two days.** Not representative of
  long-term infrastructure behavior. No claim about week-over-week or
  month-over-month variation.
- **No commitment level specified.** The RPC node default applies to
  both paths. A workflow requiring `finalized` data would see different
  numbers.
- **Ten specific Pyth legacy push oracle feeds.** Workflows reading
  different accounts, different counts, or pull oracles (`PriceUpdateV2`)
  may see different patterns.
- **Slow-mode hypothesis not confirmed.** The connection-pool-reset
  explanation fits the numbers but was not verified against Zela
  infrastructure internals.
- **Read-only benchmark.** No conclusions about transaction submission,
  block confirmation, or full transaction lifecycle.

---

## Reproducibility

### Build

```bash
# Zela procedure (WASM component)
cargo build --release --target wasm32-wasip2 -p oracle_read

# Baseline client (native)
cargo build --release -p baseline_client
```

The workspace pins `web-sys = 0.3.82` in `Cargo.lock` to keep
`wasm-bindgen` on a version that `wasm-component-ld` can link. If you
do a fresh `cargo clean && cargo update`, re-pin before rebuilding the
procedure.

### Deploy the procedure

Detailed deploy steps (JWT flow, procedure upload, revision handling)
live in [`docs/M0_DEPLOY_SUMMARY.md`](docs/M0_DEPLOY_SUMMARY.md). The
revision used for this benchmark is
`56df99e1e31be5ac2e9e3c08d2cae261d0757490`.

### Run the orchestrator

The orchestrator reads the following environment variables (keep them
in a local `.env` file; do not commit):

- `ZELA_KEY_ID`, `ZELA_KEY_SECRET` — Zela executor API key
- `ZELA_PROCEDURE` — procedure name (e.g. `oracle_read`)
- `ZELA_PROCEDURE_REVISION` — deployed revision hash
- `BASELINE_RPC_URL` — Helius (or other) RPC endpoint

```bash
set -a; source .env; set +a
pip install -r orchestrator/requirements.txt
python orchestrator/orchestrate.py --runs 100
```

Progress prints to stderr, one line per paired run:

```
Run 34/100 | zela: 1892µs (1 slot) | baseline: 451ms (2 slots) | elapsed: 14m32s
```

Output goes to `data/run_YYYYMMDD_HHMMSS/feeds.csv` and `aggregates.csv`.
stdout is silent. On transient HTTP failures the orchestrator writes an
error row and continues. JWT refresh on 401 is automatic.

### Run the analysis

```bash
pip install -r analysis/requirements.txt
python analysis/analyze.py \
  path/to/dataset_1 path/to/dataset_2 path/to/dataset_N
```

Output goes to `docs/figures/summary.json` (all computed statistics) and
five PNG figures in `docs/figures/`. A short text summary is also
printed to stdout:

```
=== M4a Analysis Summary ===
Runs analyzed (combined):          497
Zela p50 / p95:                    2.10ms / 233.36ms
Baseline p50 / p95:                486.19ms / 647.71ms
Median ratio (combined):           231.3x
p95 ratio (combined):              2.8x
Zela slot consistency (1 slot):    91.1%
Baseline slot consistency (1 slot):7.4%
Zela bimodality: fast(<5ms)=79.9%  slow(>200ms)=11.5%
============================
```

Error rows (`error=true` in either CSV) are filtered before analysis
and counted on stderr.

### Per-milestone detail

- [`docs/M0_DEPLOY_SUMMARY.md`](docs/M0_DEPLOY_SUMMARY.md) — first deploy
- [`docs/M1_RESULTS.md`](docs/M1_RESULTS.md) — extension to 10 sequential reads
- [`docs/M2_RESULTS.md`](docs/M2_RESULTS.md) — feed swap and baseline client
- [`docs/M3_RESULTS.md`](docs/M3_RESULTS.md) — orchestrator and first dataset

---

## Repository structure

```
procedures/oracle_read/    — Zela WASM procedure
baseline_client/           — Rust standard-RPC client
orchestrator/              — Python orchestrator (paired runs, CSV output)
analysis/                  — Python analysis script (stats + figures)
zela_datasets/             — Raw CSV datasets (5 × 100 paired runs)
docs/                      — Milestone writeups and figures
    figures/               — Generated PNGs + summary.json
    M0_DEPLOY_SUMMARY.md
    M1_RESULTS.md
    M2_RESULTS.md
    M3_RESULTS.md
```

---

## Context

Zela is an execution platform from RockawayX that runs WASM procedures
in Solana leader-proximate executors. Procedure source in this
repository uses the public
[`zela-std`](https://github.com/Zela-io/zela-std) crate, which exposes
the `CustomProcedure` trait, the `call_rpc` host binding, and the
`zela_custom_procedure!` registration macro. Pyth legacy push oracle
accounts are documented in the Pyth reference materials; this
benchmark does not parse the on-chain account bytes, so no Pyth SDK
is used.

This benchmark is a research artifact — a measurement of a specific
read path on two specific days from a specific client location, against
one specific baseline endpoint. It is not a product evaluation, not a
buying recommendation, and not a claim about suitability for any
particular workload. The numbers presented here will change if any of
the underlying conditions change: client location, baseline provider,
feed set, commitment level, or the mainnet load pattern on the day of
collection. Readers who care about a specific workload should
re-measure under conditions matching that workload.
