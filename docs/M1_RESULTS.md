# M1 Results — Multi-feed Sequential Read

**Status:** Complete.
**Revision deployed:** `d08eb3550e758c1b9031713076908ae484f40663`
**Date:** April 17, 2026

M1 extends M0's single-account read into a **10 sequential `getAccountInfo`
calls** within one procedure invocation. This matches Zela's homepage benchmark
methodology ("10 sequential reads") and produces per-feed and aggregate timing.

---

## Methodology

The procedure reads 10 Pyth legacy push oracle accounts on Solana mainnet,
sequentially via `call_rpc("getAccountInfo", ...)`. Per-feed timing wraps each
individual `call_rpc`. Aggregate timing brackets the entire 10-call loop
(includes per-feed bookkeeping like `account_data_len` extraction and
`feeds.push`).

**Feeds read (in order):**

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
| 9 | W/USD | `8gTpR6DjS66SeyUvy3TXMBZpJQ3ZMMcifTGp3zhwS5gS` |
| 10 | JTO/USD | `D8UUgr8a3aR3yUeHLu7v8FWK7E8Y5sSU7qrYBXUJXBQ5` |

---

## Cold-start sample (first invocation after deploy)

Single invocation of the freshly-deployed procedure:

| Field | Value |
|---|---|
| `genesis_hash` | `5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d` (mainnet ✓) |
| `aggregate.feed_count` | 10 |
| `aggregate.wall_clock_total_us` | 2155 µs |
| Distinct `context_slot` values across 10 feeds | 1 (`413898765`) |
| Feeds with `account_found: true` | 9 |
| Feeds with `account_found: false` | 1 (W/USD) |

**Per-feed wall-clock latency:**

| Symbol | µs |
|---|---|
| SOL/USD | 472 |
| BTC/USD | 339 |
| USDC/USD | 221 |
| USDT/USD | 203 |
| BONK/USD | 198 |
| JTO/USD | 193 |
| JUP/USD | 148 |
| ETH/USD | 145 |
| BNB/USD | 128 |
| W/USD | 101 (missing account, fast fail) |

---

## Warm samples (5 sequential invocations, ~1 sec apart)

| Run | Total µs | Slot | Δ slot |
|---|---|---|---|
| 1 | 2577 | 413899181 | — |
| 2 | 2107 | 413899184 | +3 |
| 3 | 3970 | 413899187 | +3 |
| 4 | 2194 | 413899190 | +3 |
| 5 | 2068 | 413899193 | +3 |

**Aggregate stats (n=5):** median **2194 µs**, range 2068–3970 µs.

**Per-position latency by run (µs):**

| Pos | Symbol | R1 | R2 | R3 | R4 | R5 |
|---|---|---|---|---|---|---|
| 1 | SOL/USD | 864 | 658 | 555 | 699 | 516 |
| 2 | BTC/USD | 235 | 234 | 375 | 224 | 239 |
| 3 | ETH/USD | 168 | 158 | 163 | 162 | 161 |
| 4 | USDC/USD | 282 | 157 | 146 | 165 | 218 |
| 5 | USDT/USD | 190 | 161 | 148 | 180 | 157 |
| 6 | BNB/USD | 186 | 155 | 140 | 152 | 155 |
| 7 | JUP/USD | 166 | 146 | 144 | 142 | 159 |
| 8 | BONK/USD | 164 | 142 | 137 | 154 | 152 |
| 9 | W/USD | 139 | 128 | **1988** | 125 | 138 |
| 10 | JTO/USD | 163 | 147 | 153 | 172 | 154 |

---

## Findings

### 1. Slot consistency within a single invocation

Every single invocation has all 10 feeds returning the same `context_slot`.
This proves the entire 10-call batch hits the RPC proxy within one Solana slot
(~400 ms window). Per-feed timing therefore measures **read latency within
a stable on-chain state**, not artifacts of slot transitions.

### 2. First-call-in-batch overhead

Position 1 (SOL/USD) is consistently the slowest in every warm run: 516–864 µs,
versus 125–375 µs for positions 2–10. This is **not procedure cold-start** — the
procedure is already cached in the Zela executor across these warm runs. The
overhead is most likely:

- TLS/HTTP connection pool warm-up between Zela executor and downstream Solana
  RPC node, or
- DNS / connection setup on the proxy side.

This is a methodology artifact, not a result. M4 README must explicitly note
that first-call-in-batch latency is structurally higher than subsequent calls
in the same loop.

### 3. Slot progression across runs

Runs are spaced 1 second apart and consistently advance by +3 slots between
runs. Solana mainnet target slot time is ~400 ms, so 3 slots/sec ≈ 333 ms/slot
in observed conditions. This confirms the procedure is fetching fresh state
each invocation, not serving cached responses.

### 4. Tail latency is real even in warm runs

Run 3 hit 3970 µs aggregate (vs 2068–2577 µs for the other four), driven
entirely by W/USD spiking to 1988 µs on that run. Other feeds in Run 3 were
typical. This single sample confirms the M0 finding that tail latency must
be measured (median + p95) rather than averaged.

### 5. W/USD missing account

`account_found: false` for W/USD across all runs. The Pyth legacy push oracle
account at `8gTpR6DjS66SeyUvy3TXMBZpJQ3ZMMcifTGp3zhwS5gS` does not currently
exist on mainnet. Possible reasons: deprecated in favor of pull oracle
`PriceUpdateV2`, never deployed on legacy push, or wrong pubkey in the
reference list.

The `account_found: false` path completes correctly (~100–140 µs in 4 of 5
runs), confirming the skip-missing-account logic works. But the missing feed
introduces an extra latency mode: most calls fast-fail in ~100 µs, but Run 3's
1988 µs spike suggests the RPC node occasionally takes longer to confirm
non-existence.

**Action before M2:** replace W/USD with another active Pyth legacy push feed,
or accept 9-feed sample size and document the choice in M4. Recommended:
replace, because variable feed counts across milestones complicate stats.

### 6. Aggregate vs sum of per-feed times

Aggregate `wall_clock_total_us` is consistently larger than the sum of per-feed
times. Example, Run 5: aggregate 2068 µs vs sum 2049 µs (diff 19 µs). The diff
is small loop bookkeeping (vec push, base64 length calc, struct serialization)
that runs between RPC calls but outside per-feed timing. This is acceptable
for M1; M4 README will document that aggregate brackets the whole loop and
sum understates by a known small overhead.

---

## Acceptance check vs M1 spec

| Criterion | Result |
|---|---|
| `cargo build --release --target wasm32-wasip2` succeeds | ✓ |
| Returns JSON matching schema | ✓ |
| `feeds` array has 10 entries in spec order | ✓ |
| Each `wall_clock_elapsed_us` non-zero, plausible | ✓ (range 101–1988 µs) |
| `wall_clock_total_us` ≈ sum of per-feed times | ✓ (diff <1%) |
| `context_slot` consistent across feeds in one batch | ✓ (1 unique slot per run) |
| Code under ~200 lines | ✓ |

**M1 hotovo.**

---

## Carry-forward to next milestones

**For M2 (external baseline client):**
- Replace W/USD pubkey with active Pyth legacy push feed before sampling.
- Decide on baseline RPC endpoint (candidates: Helius free tier, public mainnet
  endpoint, dedicated RPC).
- Decide on baseline client language (Rust for symmetry with procedure, or TS
  for faster iteration).
- Baseline client should use the same `getAccountInfo` calls in the same order
  to enable apples-to-apples comparison.

**For M3 (orchestrator + CSV):**
- Sample size: target 100 paired runs for statistically meaningful p95.
- Run distribution: spread across multiple times of day to capture diurnal
  variation in network load.
- Storage: simple CSV with columns per feed × per side × per metric.

**For M4 (analysis + README):**
- First-call-in-batch overhead must be disclosed and quantified.
- Aggregate-vs-sum diff must be disclosed.
- Baseline client config must be fully specified (endpoint, region, region of
  client machine).
- Tail latency must be reported as median + p95, not mean.
- W/USD outcome must be documented (replaced, or kept and noted).
