# Zela Oracle Read Path Benchmark

An independent, open-source benchmark of Solana oracle read latency through Zela's Remote Procedure Execution (RPE) platform, measured against a public-RPC baseline (Helius) over a 10-day production window.

**Full report:** [`docs/M5_RESULTS.md`](docs/M5_RESULTS.md)

---

## TL;DR

- **36 datasets, 10 days, 3,411 paired runs** (post cold-start filter).
- Median read latency from a Prague client: **18.7 ms** (Zela) vs **95.5 ms** (Helius).
- Headline client ratio is **5.1×**, but baseline carries a structural TCP/TLS handshake handicap of ~30–50 ms per run. After honest correction, architectural advantage is **~2.5–3.5×** — disclosed throughout the report.
- **Sub-millisecond server-side compute** (median 0.97 ms) is stable across all 36 datasets and time-of-day buckets, while baseline shows ~9 ms diurnal swing tied to public-internet congestion.
- **Leader-aware routing across 5 executor regions** verified at **83.7% per-run match rate** to the Solana leader schedule (n=2,571 known leaders, +1 slot offset).
- From a single Prague vantage point, two regions (Dubai, Newark) are indistinguishable by latency — the benchmark resolves 4 tiers, not 5.

---

## What this benchmark measures — and what it does not

This benchmark measures the **read leg** of a low-latency Solana data pipeline, comparing two paths from a Prague client:

- **Zela path:** a small WebAssembly procedure deployed on Zela's executor infrastructure issues one `getMultipleAccounts` call for 10 Pyth oracle accounts and returns a compact JSON response.
- **Baseline path:** a fresh subprocess (Rust) issues the same single `getMultipleAccounts` call through Helius's free-tier public RPC.

What this benchmark does **not** measure:
- Write-path latency (transaction submission, inclusion, finality)
- Real-world workflow shapes (liquidator state scans, cross-DEX arb reads, market-maker quoting batches)
- Multi-region client vantage points (single Prague client only)

These are all explicitly out of scope for M5 and are mapped to follow-up milestones (see [Roadmap](#roadmap)).

---

## Headline results

| Metric (per run, batch — 1 RPC call per side) | p50 | p95 | mean |
|---|---|---|---|
| **Zela server-side** (inside procedure, no internet) | **0.97 ms** | 37.4 ms | 7.0 ms |
| **Zela client e2e** (Prague→Frankfurt→Prague) | **18.71 ms** | 462.4 ms | 132.3 ms |
| **Baseline client e2e** (Prague→Helius→Prague) | **95.48 ms** | 139.7 ms | 103.1 ms |

Full distributions, CDF plots, and per-region breakdowns: [`docs/M5_RESULTS.md`](docs/M5_RESULTS.md) and [`docs/figures/`](docs/figures/).

---

## Methodology

Each run is a paired measurement: the orchestrator issues one HTTP request to Zela's executor endpoint, then waits one second (to respect baseline rate limits), then issues one HTTP request to Helius. Each side does one batch `getMultipleAccounts` call returning state for the same 10 Pyth price accounts.

The cron schedule runs five times daily (02:00, 08:00, 13:00, 17:00, 22:00 UTC) for 10 days, producing 36 datasets of 100 runs each. After symmetric cold-start filtering (first 5 runs per dataset, both sides), the clean dataset is **3,420 baseline warm runs and 3,411 complete paired runs** (9 Zela-side errors filtered).

A known measurement asymmetry is disclosed up-front: the baseline subprocess incurs a fresh TCP/TLS handshake on every run, while the Zela side uses a persistent `requests.Session()`. The handicap inflates the headline client ratio (5.1×) above the architectural advantage (~2.5–3.5×). See `## Measurement asymmetries` in the full report for the detailed accounting.

---

## Key findings

### 1. Sub-millisecond server-side compute, stable across 10 days

Zela server-side latency (the time spent inside the WASM procedure, querying a co-located Solana node and returning) sits at **0.97 ms median, range 0.91–1.02 ms across 36 datasets** — a variance of ~10% over a 10-day window. Average per cron hour: identical 0.96–0.97 ms across all five UTC slots. By contrast, baseline median ranges from 90 ms at 02:00 UTC to 98.7 ms at 22:00 UTC, a ~9 ms diurnal swing that tracks US-evening traffic peaks on the public internet path between Prague and Helius. Zela sidesteps this by running compute against a Solana RPC node in the same facility — no public-internet hop on the read.

### 2. Leader-aware routing across 5 executor regions

Zela operates 5 executor regions, identified per [docs.zela.io](https://docs.zela.io/): `fr2` Frankfurt, `tyo` Tokyo, `dx1` Dubai, `ewr` Newark NJ, `slc` Salt Lake City UT. Routing has two modes: **auto** (default — Zela's dispatcher selects a region based on the current Solana leader) and **static** (the client pins a specific region via the `zela-route-by: static <label>` HTTP header, bypassing the dispatcher).

Controlled per-region latency from a Prague client using static routing:

| Region | p50 | stdev |
|---|---|---|
| `fr2` Frankfurt | 18.6 ms | 1.1 ms |
| `dx1` Dubai | 227.7 ms | 7.5 ms |
| `ewr` Newark NJ | 229.7 ms | 11.3 ms |
| `slc` Salt Lake City | 321.7 ms | 37.4 ms |
| `tyo` Tokyo | 461.7 ms | 19.3 ms |

Dubai and Newark are statistically indistinguishable from a Prague vantage point (overlapping distributions). The benchmark therefore resolves 4 latency tiers, not 5; the merged tier is labeled `mid`. Full 5-tier resolution would require 3+ vantage points (M7B).

**Caveat on dispatcher internals.** Zela's auto-routing is empirically leader-aware (see finding #3), but its exact internal logic — how the dispatcher selects among regions when the leader sits between locations, how it handles upcoming leader transitions, what additional inputs (load, capacity, client geography) it factors in — is not publicly documented. The 83.7% match rate observed in this benchmark is consistent with leader-proximate routing but does not uniquely determine the underlying algorithm.

### 3. Per-run leader correlation at 83.7%

For each of the 3,411 Zela runs, the analysis pipeline reads `context_slot` from the response, looks up the Solana leader at that slot, geolocates the leader validator (via validators.app), maps the leader location to the expected Zela tier, and compares to the tier inferred from the run's observed client e2e latency.

| Slot offset | Match rate (n=2,571 known) |
|---|---|
| −2 | 57.8% |
| −1 | 66.2% |
| 0 (raw context_slot) | 73.5% |
| **+1** | **83.7%** ← chain-tip estimate |
| +2 | 81.3% |
| +3 | 73.0% |

The peak at +1 is consistent with a commitment-lag correction: the orchestrator reads with `confirmed` commitment (lagging the chain tip by ~1–2 slots), while Zela's dispatcher routes based on the chain tip. A boundary-gradient analysis (match rate decreases monotonically across the four slot positions within a leader's assignment window when measured at offset 0; full breakdown in the report) independently confirms the timing model.

Per-tier match rates at offset +1: `fr2` 89.6% (n=1,777), `tyo` 82.4% (n=346), `mid` 62.9% (n=434), `slc` 7.1% (n=14, sample too small to characterize).

---

## Repository structure

```
zela_oracle_read_path_benchmark/
├── README.md                          (this file)
├── docs/
│   ├── M5_RESULTS.md                  (full benchmark report)
│   └── figures/
│       ├── fig1_cdf.png               (CDF: Zela e2e vs baseline e2e vs Zela server-side)
│       ├── fig2_per_region.png        (per-region box plot, 5 regions + auto)
│       └── fig3_confusion_matrix.png  (routing confusion matrix at offset +1)
├── orchestrator/
│   └── orchestrate.py                 (paired-run collector, cron entry point)
├── analysis/
│   ├── analyze.py                     (combined-statistics pipeline, --mode batch)
│   └── post_hoc/
│       ├── route_test_session.py      (controlled per-region static-routing test)
│       ├── fetch_new_slots.py         (cache priming: slot → leader pubkey)
│       ├── fill_missing_validators.py (cache priming: pubkey → location)
│       └── leader_correlation_v3.py   (per-run correlation pipeline)
├── zela_datasets/
│   ├── dataset_YYYYMMDD_HHMM/         (M5 paired-run datasets, 36 entries)
│   └── legacy_sequential/             (M1–M4 sequential-mode datasets, preserved)
├── procedure/
│   └── oracle_read/                   (Rust WASM source, deployed to executor)
└── leader_correlation_results/        (caches: slot→leader, validator metadata, per-run CSV)
```

---

## How to reproduce

Prerequisites:
- Python 3.10+ with `requests`, `pandas`, `numpy`, `matplotlib`
- Rust toolchain with `wasm32-unknown-unknown` target (for procedure rebuild)
- Solana mainnet RPC access (Helius free tier sufficient for baseline)
- Zela platform access (early-builder credentials; see [docs.zela.io](https://docs.zela.io/) for onboarding)
- Environment variables: `ZELA_JWT`, `HELIUS_API_KEY`

Run a single paired collection:
```bash
python orchestrator/orchestrate.py --runs 100
```

Combined-statistics analysis across all datasets:
```bash
python analysis/analyze.py --mode batch
```

Per-region static-routing measurement:
```bash
python analysis/post_hoc/route_test_session.py --route fr2 --runs 100
```

Leader correlation pipeline:
```bash
python analysis/post_hoc/fetch_new_slots.py
python analysis/post_hoc/fill_missing_validators.py
python analysis/post_hoc/leader_correlation_v3.py
```

---

## Limitations and honest disclosures

The full disclosures section lives in [`docs/M5_RESULTS.md`](docs/M5_RESULTS.md) under *Measurement asymmetries*. Condensed:

- **Headline client ratio is inflated by baseline handicap.** The Rust subprocess starts fresh per run with a new TCP+TLS handshake (~30–50 ms overhead), while the Zela side reuses a persistent session. Correcting for this brings the architectural advantage to ~2.5–3.5×, not 5×.
- **Server ratio (98.7×) is apples-to-oranges and reported only with framing.** It compares server-side compute (no internet) to a client-side round-trip (full public-internet path); not a fair comparison, included for completeness.
- **Single vantage point.** All measurements are from a single Prague client. Some regional distinctions (Dubai vs Newark) collapse to a single observable tier at this vantage.
- **Per-location auto-routing distribution is inferred, not tabulated.** Zela's auto-routing dispatcher internals are not publicly documented, so the bimodal CDF can suggest "most requests land in Frankfurt with a minority in distant regions" but exact per-region request percentages cannot be measured from a single client without parallel static + auto datasets (planned for M7A).
- **Baseline (Helius) internal routing is opaque.** The benchmark measures Helius RPC end-to-end from a single Prague client, but Helius's own load-balancing and geographic node selection are not visible from outside. Variance attributed to the baseline (e.g., the ~9 ms diurnal swing) may originate from Helius's routing decisions, public-internet path congestion, or both — the benchmark cannot disambiguate.
- **slc tail outliers** show a stdev of 37 ms vs 1–19 ms on other static routes. Root cause not resolved (sporadic TCP events vs systematic US-West path variance) — open Backlog item.
- **Read-path only.** Write submission, inclusion latency, finality, BloXroute and Jito bundling are out of scope (M8).
- **`getMultipleAccounts` with 10 oracle accounts** is one specific workload. Larger account batches (50–100), heterogeneous account types (obligation accounts, pool state), and write-bearing workflows are not characterized here (M9).

---

## Roadmap

The benchmark is structured as a sequence of milestones; M5 (this report) is the first to ship publicly. Forthcoming work:

- **M6 — Liquidator simulation prototype.** End-to-end "watch + simulate" workflow against Kamino obligation accounts and Pyth oracle reads, including `simulateTransaction` for liquidation viability.
- **M7A — Multi-location latency map.** Long-running cron pinned via `zela-route-by: static` for each of 5 regions, from current Prague client. No cloud cost; resolves the per-location auto-routing distribution open question.
- **M7B — Multi-region client + async parallel orchestrator.** Add US East and (likely) APAC vantage points; switch to `asyncio.gather` for same-slot paired measurement.
- **M8 — Write-path benchmark + verification of "300 sims in 300 ms" claim.** Inclusion latency, finality, transaction routing through various submit relays.
- **M9 — Multi-account scaling and concurrency edge cases.** Sweeping 10/25/50/100 account batches and per-procedure concurrency-limit behavior (current dashboard cap 32; server-side hard max TBD).

---

## Background: the Zela platform

Zela is a Remote Procedure Execution (RPE) platform that lets users deploy small WebAssembly procedures to executors physically co-located with Solana validators. The intended workflow is *read → compute → simulate → decide → submit* within a single client request, eliminating multiple internet round-trips between data lookup and transaction submission. Reference: [docs.zela.io](https://docs.zela.io/).

Zela is currently in early access and offers production infrastructure to early builders for free for the time being. This benchmark is independently constructed and is not affiliated with or endorsed by the platform's operator; methodology, conclusions, and disclosures are the author's own.

---

## Author

Feedback, corrections, and replication on different vantage points are welcome — please open an issue.

---

## License

MIT — see [LICENSE](LICENSE) file.
