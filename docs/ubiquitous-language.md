# Ubiquitous language

Last updated: 2026-06-08

Scope: the `zela_oracle_read_path_benchmark` repo — the M6 simulation-recheck
procedure, its Pyth pull-oracle decode path, abort taxonomy, and dataset.

> Provenance: this v1 is grounded in `M6_DESIGN_LOG_v2.6` (read in full), in
> source-verified Pyth types (`pyth-solana-receiver-sdk` `PriceUpdateV2`,
> `pythnet-sdk 2.3.1` `PriceFeedMessage`), and reconciled against the landed C1
> source in `procedures/m6_sim_recheck/src/lib.rs`. C1 confirms the Pattern C
> identifiers (`run_core`, `read_accounts`, `simulate_transaction`) and the
> decode-only `oracle_summary` keys. Phase 0 and dataset artifacts are not yet
> source-confirmed; keep open rows for surfaces that have not landed.

## Core entities

| Term | Definition | Canonical spelling in code | Notes |
|------|------------|----------------------------|-------|
| Zela | A WASM Remote Procedure Execution (RPE) platform, co-located with Solana validators; runs the procedure server-side | (n/a — platform) | The benchmarked system. |
| RPE procedure | A WASM module Zela executes server-side that reads chain state and returns a decision/summary | `procedure` | This repo's procedure is the M6 sim-recheck. |
| m6_sim_recheck | The M6 procedure crate under test: reads accounts, decodes oracle, (Phase 0+) simulates a tx and applies validity gates | `m6_sim_recheck` (`procedures/m6_sim_recheck/`) | NEW crate. Generic "simulation recheck" framing, not "liquidator". |
| oracle_read | The M5 procedure artifact | `oracle_read` (`procedures/oracle_read/`) | Preserved as-is; M6 work must not modify it. |
| baseline_client | The off-Zela baseline that performs the equivalent read path for comparison | `baseline_client` | Workspace member alongside the procedures. |
| PriceUpdateV2 | Pyth pull-oracle price account (post-June-2024 standard) | `PriceUpdateV2` | Owner `rec5…LtFJ`; 134 bytes; 8-byte Anchor discriminator prefix `22 f1 23 63 9d 7e f4 cd`. Replaces the sunset legacy push `PriceAccount`. |
| dataset row | One JSONL record emitted per run (procedure result + run metadata) | (dataset row) | Schema layered: procedure response vs dataset row (see Q9). |

## Value objects

| Term | Definition | Canonical spelling in code | Notes |
|------|------------|----------------------------|-------|
| price | Raw integer price from the oracle | `price` (i64) | Real value = `price × 10^exponent`. |
| exponent | Power-of-ten scale for a price | `exponent` (source); `expo` (wire) | Upstream `PriceFeedMessage` field is `exponent`; our `oracle_summary`/payload key is `expo`. Same concept (see Synonyms to kill). |
| conf | Confidence interval of the price, in price units | `conf` (u64) | Drives `oracle_confidence_too_wide` via overflow-safe u128 ratio. |
| publish_time | Unix time (seconds) the price was observed off-chain | `publish_time` (i64) | Drives the staleness gate AND the future-skew sanity check. Not the same as `posted_slot`. |
| prev_publish_time | publish_time of the previous update | `prev_publish_time` (i64) | Decoded; not currently surfaced or gated. |
| posted_slot | Solana slot at which the update landed on-chain | `posted_slot` (u64) | Reported only, not gated. Distinct from `publish_time` and from the read `context_slot`. |
| feed_id | 32-byte identity of a Pyth feed | `feed_id` ([u8;32]) | SOL/USD = `ef0d…b56d`. Decoded + reported; not runtime-gated (caller passes correct oracle pubkeys). |
| verification_level | Wormhole guardian verification level of a pull update | `verification_level` | `Full` (Borsh tag 0x01, 1 byte) or `Partial { num_signatures: u8 }` (tag 0x00, 2 bytes). VARIABLE LENGTH → +1 offset shift for Partial. Gate-Full-first. |
| context_slot | Slot at which the account read was taken (RPC-reported) | `context_slot` | The read snapshot slot. |
| simulate_context_slot | Slot at which the simulation was evaluated | `simulate_context_slot` | Reported for the caller's own drift check. |
| oracle_summary | Per-oracle-pubkey map of decoded fields in the procedure output | `oracle_summary` | Pull-oracle shape: `{ price, expo, publish_time, conf, verification_level, posted_slot, feed_id }`. |
| accounts_read | Output list of the account pubkeys actually read | `accounts_read` | Raw account bytes are internal only; output exposes this + `oracle_summary`. |
| schema_version | Wire/dataset schema string, coupled to the design-doc version | `schema_version` | Current: `m6.v2.6`. Bump on any payload/taxonomy/shape change. |
| max_publish_time_lag_seconds | Staleness threshold (seconds): max allowed `now − publish_time` | `max_publish_time_lag_seconds` | Payload field; Phase-0-calibrated (30–60 s start). |
| max_clock_skew_seconds | Tolerance (seconds) for a `publish_time` ahead of the executor clock | `max_clock_skew_seconds` | Payload field, default 5. Future-skew sanity, not the staleness gate. |
| max_confidence_ratio_bps | Max allowed confidence/price ratio in basis points | `max_confidence_ratio_bps` | Payload field. |
| max_account_count | Max number of accounts the payload may request | `max_account_count` | Payload field. |
| max_tx_bytes | Max serialized transaction size accepted | `max_tx_bytes` | Payload field. |

## Operations

| Term | Definition | Canonical spelling in code | Notes |
|------|------------|----------------------------|-------|
| run_core | Shared, target-agnostic core of the procedure (decode + gates + decision) | `run_core` | Pattern C: one core, two cfg-gated adapters. |
| read_accounts | Adapter that fetches account bytes | `read_accounts` | cfg-gated: native = Solana RPC client; wasm = `zela_std::call_rpc`. |
| simulate_transaction | Adapter that runs a transaction simulation | `simulate_transaction` | cfg-gated, same split as `read_accounts`. Phase 0+. |
| call_rpc | Zela's JSON-RPC primitive available inside the WASM procedure | `zela_std::call_rpc` | Synchronous (no `.await`); nested `Result`; accepts any JSON-RPC method (incl. `getMultipleAccounts`). |
| decode (oracle) | Parse a `PriceUpdateV2` account into its fields | (decode) | Gate-Full-first; handle variable-length `verification_level`; Borsh = declaration order, no alignment padding. |
| validity gate | A Pyth-data-sourced abort check applied after decode | (gate) | Three: `oracle_publish_time_too_stale`, `oracle_confidence_too_wide`, `oracle_verification_partial`. Phase 0+ (not C1). |

## Events / outcomes

Abort taxonomy values (each ends the procedure with `abort_detail`). Trivial =
input/validation; non-trivial = data/RPC/decision-sourced.

| Term | Definition | Canonical spelling in code | Notes |
|------|------------|----------------------------|-------|
| payload_invalid / payload_too_large / max_tx_bytes_exceeded / tx_not_v0 / tx_uses_address_lookup_table / duplicate_pubkey | Trivial input/validation aborts | as written | Do not satisfy the non-trivial coverage quota. |
| account_not_found | Requested account missing in the read | `account_not_found` | |
| read_rpc_error / simulate_rpc_error | RPC transport failures (read / simulate) | as written | Non-trivial. |
| simulation_err | Simulation returned an on-chain error | `simulation_err` | Non-trivial. |
| min_context_slot_not_reached | Read/sim context slot below the requested minimum | `min_context_slot_not_reached` | Classified by "minimum context slot"/"minContextSlot" substring in the RPC error. |
| oracle_decode_failed | Decode error, non-Pyth pubkey, non-positive price, or publish_time in the future | `oracle_decode_failed` | |
| oracle_verification_partial | `verification_level != Full` | `oracle_verification_partial` | Replaces the legacy `oracle_not_trading`. |
| oracle_publish_time_too_stale | `now − publish_time > max_publish_time_lag_seconds` | `oracle_publish_time_too_stale` | Replaces the legacy slot-based `oracle_too_stale`. |
| oracle_confidence_too_wide | Confidence ratio over `max_confidence_ratio_bps` | `oracle_confidence_too_wide` | Unchanged by the pull pivot. |
| oracle_error | Optional 5-value-collapse bucket for all oracle decode + gate failures | `oracle_error` | `abort_detail` carries the specific cause. |

## Synonyms to kill

| Variations found | Preferred term | Reason |
|------------------|----------------|--------|
| `expo`, `exponent` | `expo` for wire/output + payload; bind from upstream `exponent` at the decode boundary | Same i32 scale. Upstream crate field is immutably `exponent`; established schema key is `expo`. No third spelling (`exp`, …). |
| `m6_liquidator_sim`, `m6_sim_recheck` | `m6_sim_recheck` | Generic recheck framing, not liquidator-specific. `m6_liquidator_sim` is a dead historical name. |
| "raw accounts", `accounts_read` | `accounts_read` (output); "raw account bytes" (internal only) | Raw bytes never appear in output; only `accounts_read` + `oracle_summary` do. |
| legacy push oracle / `PriceAccount`, pull oracle / `PriceUpdateV2` | `PriceUpdateV2` (the live target) | `PriceAccount` is sunset (June 30 2024); keep it only as a footgun reference, never as the decode target. |

## Homonyms to disambiguate

| Shared term | Contexts it appears in | Proposed renames |
|-------------|------------------------|------------------|
| "time"/"slot" of an update | `publish_time` (off-chain observation, unix s) · `posted_slot` (on-chain landing slot) · `context_slot` (read snapshot slot) · `simulate_context_slot` (sim slot) | Keep all distinct; never collapse to a bare "time"/"slot". |
| "now" | executor wall-clock via `chrono::Utc::now().timestamp()` (used by both time checks) | Always qualify as the executor clock; it is NOT an on-chain `Clock::get()` (unavailable in the procedure). |
| "stale" | oracle staleness (`publish_time` age) vs the deprioritized Zela read-context-slot freshness | "stale" alone = oracle staleness; Zela read freshness is a separate, currently-deprioritized concern. |

## Terms still open

| Term | What I think it means | Need confirmation |
|------|-----------------------|-------------------|
| `abort_detail` field name/shape | The free-form detail string accompanying an abort value | Confirm the exact field name in the dataset row. |
| 5-value taxonomy collapse membership | `oracle_error` aggregates the four oracle decode+gate failures | Confirm whether v1 ships the full taxonomy or the collapsed subset. |
