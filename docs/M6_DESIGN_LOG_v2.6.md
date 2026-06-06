# M6 Design Log v2.6

**Status:** Pull-oracle pivot. M6 originally targeted Pyth's legacy
push-oracle `PriceAccount` (sunset June 30, 2024). v2.6 re-points the
oracle decode + validity gates at the live pull oracle (`PriceUpdateV2`,
pyth-solana-receiver-sdk format). This is a technical content change
(payload contract, `oracle_summary` shape, abort taxonomy), NOT a
doc-only patch — so per the v2.3 schema-coupling rule the wire
`schema_version` bumps with it.

**Predecessor:** `M6_DESIGN_LOG_v2.5.1.md`.

**Schema bump:** Wire `schema_version` moves `m6.v2.5` -> `m6.v2.6`
(per the v2.3 coupling rule — this patch changes the payload contract,
the `oracle_summary` shape, and the abort taxonomy, so the dataset
schema string must move with it). All Codex-verified API corrections
from v2.5 / v2.5.1 (Pattern C, `call_rpc` shape, `VersionedTransaction`
/ `ClientErrorKind` shapes, `solana-transaction` / `solana-message`
bincode features) stand unchanged — the pivot touches only the oracle
read + gate surface.

**What's new in v2.6 (pull-oracle pivot):**
1. Q5 re-pointed at `PriceUpdateV2` (pull oracle); legacy push-oracle
   decode removed. Adds the variable-length `verification_level`
   finding + decode-order coupling, corrected byte layout, and an
   inverted SDK fallback chain (receiver-sdk primary, manual Borsh
   fallback).
2. Oracle validity gates rewritten (Q3c / Q5 / Q9): `oracle_not_trading`
   (legacy `status`) -> `oracle_verification_partial`; slot-based
   `oracle_too_stale` -> time-based `oracle_publish_time_too_stale`
   (`chrono::Utc::now()` vs `publish_time`, seconds). Confidence gate
   unchanged.
3. Payload / threshold + `oracle_summary` shape updated; `schema_version`
   bumped to `m6.v2.6`; C1 fixture is now the captured pull account.

All v2.5.1 additions (Builder priority order, scope guard, optional
5-value taxonomy collapse) are retained below unchanged.

**Self-contained:** Supersedes v2.5 and all prior.

**Reading order if reviewing:** Q1 → Q12, then Assumptions, then
Verified Zela facts, then Reconciliation changelog (v2.5→v2.5.1
deltas), then Pending verification items.

---

## Q1 — Success criterion + non-goals

### Framing

**M6 v1 builds a generic simulation recheck procedure**, not a
production liquidator. The narrow research question is: *can Zela
collapse fresh oracle read + safety gates + simulation into one
near-leader procedure call?* M6 dataset and results doc must use
"generic simulation recheck" wording, not "liquidator" wording.

### Implementation target

**Create new crate `procedures/m6_sim_recheck/` in the repo.** Do NOT
modify or repurpose the existing `procedures/oracle_read/` crate
(that is the M5 procedure artifact, preserved as-is per the
DOCUMENT REGENERATION preservation rule).

**Workspace registration (root `Cargo.toml`):** add the new crate to
`workspace.members` array alongside existing `procedures/oracle_read`
and `baseline_client`. Without this, `cargo build -p m6_sim_recheck`
fails with "package not found". Verify with `cargo metadata` after
edit.

### Architecture: Pattern C cfg-gated adapter (REVISED v2.3)

**Earlier v2.2 statement that "Pattern A suffices ... native testing
happens via `solana_client` instantiated inside the procedure body"
was architecturally incorrect.** WASM procedure body compiles to
`wasm32-wasip2` where `solana_client` cannot run (V2: outbound = Zela
proxy only). v2.3 mandates Pattern C with cfg-gated adapter functions.

**Reference:** `skills/zela-assistant/references/procedure-anatomy.md`,
Pattern C section ("cfg-gated trait + inherent native run"). Same
pattern used by `priority_fees` in `zela-demo`.

**Architecture skeleton:**

```rust
// procedures/m6_sim_recheck/src/lib.rs

use serde_json::Value;

pub struct M6SimRecheck;

// ─── SHARED CORE LOGIC ─────────────────────────────────────
// Runs on both wasm32-wasip2 and native targets.
// Calls RPC via cfg-gated functions below.
// Always returns Q9 schema as Value; aborts encoded as
// {"decision":"abort","abort_reason":"...", ...}.

impl M6SimRecheck {
    pub async fn run_core(params: Value) -> Value {
        // 1. Parse params: Value → typed Payload (or abort payload_invalid)
        // 2. Validate: max_account_count, max_tx_bytes, duplicate_pubkey,
        //    tx_uses_address_lookup_table, tx_not_v0
        // 3. Call read_accounts(payload) [cfg-gated below]
        // 4. Decode Pyth oracles + apply gates
        // 5. Call simulate_transaction(payload) [cfg-gated below]
        // 6. Assemble Q9 schema Value, return
    }
}

// ─── CFG-GATED RPC ADAPTERS ────────────────────────────────

#[cfg(target_arch = "wasm32")]
async fn read_accounts(payload: &Payload, timings: &mut Timings) -> ReadOutcome {
    use zela_std::call_rpc;
    // Uses zela_std::call_rpc per Q8 RPC error catch-and-convert rule.
    // Returns ReadOutcome::Success or ReadOutcome::Abort(reason, detail).
}

#[cfg(not(target_arch = "wasm32"))]
async fn read_accounts(payload: &Payload, timings: &mut Timings) -> ReadOutcome {
    use solana_client::nonblocking::rpc_client::RpcClient;
    // Native test adapter; instantiates solana_client.
    // Same return shape; maps solana_client errors to same abort reasons
    // so taxonomy is parallel.
}

// Same pattern for simulate_transaction.

// ─── WASM ENTRYPOINT ───────────────────────────────────────

#[cfg(target_arch = "wasm32")]
mod wasm_entry {
    use super::*;
    use zela_std::{CustomProcedure, JsonValue, RpcError, zela_custom_procedure};

    impl CustomProcedure for M6SimRecheck {
        type Params = JsonValue;        // see Q3h — never strict struct
        type SuccessData = JsonValue;
        type ErrorData = ();             // never used; always return Ok

        const LOG_MAX_LEVEL: log::LevelFilter = log::LevelFilter::Debug;

        async fn run(params: JsonValue) -> Result<JsonValue, RpcError<()>> {
            // Always Ok — abort encoded in schema, not via RpcError exit
            Ok(M6SimRecheck::run_core(params).await)
        }
    }
    zela_custom_procedure!(M6SimRecheck);
}
```

**Cargo.toml dependencies for the new crate (v2.5 source-verified):**

```toml
[dependencies]
zela-std = { ... }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
base64 = "0.22"
bs58 = "0.5"

# Solana transaction parsing — split crates verified at Task 0.
# Per Codex source-verified review of v2.4: VersionedTransaction lives
# in solana_transaction::versioned, VersionedMessage in solana_message.
# Both crates gate bincode/serde support behind the `bincode` feature.
# Verify at Task 0 that these compile to wasm32-wasip2 with these
# features enabled. If not, fall back to manual binary parsing.
solana-transaction = { version = "...", features = ["bincode"] }
solana-message     = { version = "...", features = ["bincode"] }
bincode            = "1.3"

# Pyth decode (pull oracle PriceUpdateV2), chosen per Task 0 outcome (Q5):
# pyth-solana-receiver-sdk (Anchor — pulls anchor-lang) primary,
# OR manual Borsh decode fallback if Anchor won't build on wasm32-wasip2.

[target.'cfg(not(target_arch = "wasm32"))'.dependencies]
# Only the native test path uses solana-client and convenience SDKs.
solana-client = "..."
solana-sdk    = "..."  # native test convenience constructors only
spl-token     = "..."
spl-associated-token-account = "..."
tokio         = { version = "1", features = ["macros", "rt-multi-thread"] }

[dev-dependencies]
# Offline-only test deps (binary fixture loading, etc.)
```

**Critical placement rules:**
- `solana-client` and `solana-sdk` NEVER in main `[dependencies]`.
  Native-only target dependencies. WASM build will fail if these leak.
- `solana-transaction` and `solana-message` MUST be in main
  `[dependencies]` with `features = ["bincode"]` (Codex-verified per
  local source). Without these features, `VersionedTransaction` lacks
  serde/bincode impls and the v0/ALT validation gate cannot
  deserialize tx bytes.
- Task 0 acceptance criteria (Q5) include explicit
  `cargo build --target wasm32-wasip2` smoke verifying both Pyth lib
  AND `solana-transaction` / `solana-message` compile on the WASM
  target with the chosen feature flags. Without this smoke, design
  assumes WASM compatibility that may not exist.

### Success criterion

**(B) Architectural feasibility gate.**

Concrete pass criteria:
- Procedure compiled and deployed via upload endpoint per
  `call-and-test.md`
- Native test suite (Pattern C, with `#[ignore]` gates — see Q11) green
  against mainnet for at least 1 **real mainnet payload** (real Pyth
  pubkeys + synthetic SPL token transfer tx per Q11 fixture; NOT a
  captured protocol-specific liquidation tx, which is M6.1+ scope)
- Output schema populated with all required fields per Q9
- No panics observed in deployed dataset
- Decision: both `execute` AND `abort` paths exercised at least once in
  the M6 dataset
- (R1 hardening) Abort path exercise must include at least one
  **non-trivial** abort — one of: `simulation_err`,
  `oracle_publish_time_too_stale`, `oracle_confidence_too_wide`,
  `oracle_verification_partial`, `read_rpc_error`, `simulate_rpc_error`,
  `min_context_slot_not_reached`. Trivial
  validation aborts (`payload_invalid`, `payload_too_large`,
  `max_tx_bytes_exceeded`, `tx_not_v0`, `tx_uses_address_lookup_table`,
  `duplicate_pubkey`) do not satisfy this quota.

### Builder implementation priority order (NEW v2.5.1)

Sequential gates. Each step is the precondition for the next. Items
beyond #7 in this design log are "nice if cheap", not Builder
acceptance criteria.

```
1. Can compile WASM.
2. Can decode 1 captured Pyth fixture.
3. Can parse v0 VersionedTransaction and reject ALT / legacy.
4. Can call getMultipleAccounts via zela_std::call_rpc.
5. Can call simulateTransaction via zela_std::call_rpc.
6. Can return Q9 execute/abort JSON schema.
7. Dataset runs (Phase 0 smoke: 1–2 days × 20 datapoints/day).
```

If any step blocks, Builder pauses and surfaces the blocker before
moving to subsequent steps. Steps 1–3 happen in Task 0; steps 4–7
happen in Phase 0 implementation.

### Scope guard for diagnostic refinements (NEW v2.5.1)

**M6 v1 should prioritize deployed feasibility evidence over
exhaustive taxonomy and fixture realism. If any diagnostic refinement
delays Task 0 or Phase 0 by more than a small amount, defer it to
M6.1.**

Concrete applications of this rule:
- 17-value abort taxonomy can be collapsed to 5-value subset for v1
  (see Q9 NEW v2.5.1 note)
- Native adapter error-mapping parity is desirable, not required
- Captured Pyth fixture decode is required (Task 0 #2); manual hex
  inspection is "sanity check against known live feed behavior", not
  archaeology
- SPL TransferChecked fixture is target; if construction blocks for
  more than a small amount, fall back to minimal valid v0 tx
- Variant order randomization in Q12 is desirable hygiene; fixed
  variant order does not invalidate M6 v1 result if randomization
  blocks orchestrator work

The rule applies to any single decision: ask whether the refinement
adds feasibility evidence or merely polish. Polish defers.

### Non-goals (NG1, NG2, NG3)

- **NG1 — No real submission.** `simulateTransaction` yes,
  `sendTransaction` no.
- **NG2 — No profitability claims.** Every dataset JSONL file carries
  a metadata row (see Q12).
- **NG3 — No retry, no idempotency.** Stateless single-shot.

---

## Q2 — Primary path: generic fallback (not Kamino-specific)

**Decision:** Generic fallback path for M6 v1. Kamino-specific path
deferred to backlog.

### Reasoning

Generic path:
- Payload: pubkey list + pre-built sim tx bytes (off-Zela source)
- Decode: minimal — Pyth oracle accounts only
- Recompute: none; freshness reporting only (see Q3c)
- Simulation: `simulateTransaction` on pre-built tx bytes
- Cost: days
- Risk: low

### Implications across M-roadmap

- **M6 itself:** generic answers the research question faster
- **M7 (multi-region routing):** neutral — procedure shape unaffected
- **M8 (sims throughput, write-path):** generic strictly better for
  M8a infrastructure throughput; M8b semantic verification of "300
  sims in 300ms" claim requires Kamino path from backlog
- **M9 (multi-account scaling):** generic strictly better — account
  count is free parameter

Kamino-specific work is deferred, not abandoned. Backlog trigger:
*"required for marketplace addon path OR for M8b full-semantic
verification of '300 sims in 300ms' marketing claim"*.

---

## Q3 — Payload contract

### Q3a — `protocol_label`

Optional string, informational tag only. Logging/dataset filtering.

### Q3b — Account list shape

Two separate fields in payload:
- `oracle_pubkeys: Vec<String>` — Pyth accounts
- `other_pubkeys: Vec<String>` — everything else (read, not decoded)

### Q3c — Freshness gate semantics

**No Zela context-slot freshness gate.** Procedure does NOT abort
based on Zela read context_slot vs caller's expected slot. Both
`context_slot` (from read) and `simulate_context_slot` (from sim) are
reported in output for caller's own drift check.

**Pyth oracle validity gates remain active.** Three Pyth-data-sourced
abort paths (rewritten for the pull oracle in v2.6):
- `oracle_publish_time_too_stale` — `now_unix - publish_time >
  max_publish_time_lag_seconds`, where `now_unix =
  chrono::Utc::now().timestamp()` (executor host clock; see Q5)
- `oracle_confidence_too_wide` — `(conf × 10000) / abs(price) >
  max_confidence_ratio_bps` (math is overflow-safe; see Q5)
- `oracle_verification_partial` — PriceUpdateV2 `verification_level`
  is not `Full` (replaces the legacy `status` gate; see Q5)

### Q3d — Simulation tx encoding (with version + ALT restrictions)

Base64-encoded serialized `VersionedTransaction`. **Only the v0
variant is supported in M6 v1.**

**M6 v1 restrictions, in payload validation order:**

1. **`tx_not_v0`** — payload's deserialized `VersionedTransaction`
   uses the legacy message variant. **Correct API shape:**
   `VersionedTransaction` is a struct with field `message:
   VersionedMessage`, where `VersionedMessage` is an enum with
   variants `Legacy(Message)` and `V0(v0::Message)`. Validation
   pseudocode:
   ```rust
   use solana_message::VersionedMessage;
   use solana_transaction::versioned::VersionedTransaction;
   // exact import paths verified per Codex local-crate source check;
   // re-verify at Task 0 with chosen crate versions
   let tx: VersionedTransaction =
       bincode::deserialize(&base64::decode(payload.tx_b64)?)?;
   match tx.message {
       VersionedMessage::Legacy(_) => return abort("tx_not_v0", ...),
       VersionedMessage::V0(msg) => msg,  // continue with v0 msg
   }
   ```
   Reason for restriction: legacy transactions have a different
   account-resolution model and would require separate sanity-checking
   in M6 v1; out of scope.
2. **`tx_uses_address_lookup_table`** — the unwrapped v0 message has
   non-empty `address_table_lookups` field. Procedure aborts. Reason:
   ALTs make tx-touched account set non-locally-determinable from tx
   bytes alone.

Legacy support (M6.1) and ALT support (M6.1 / future) live in
BACKLOG.

**Tx-account coverage rule (explicit constraint):**
- M6 v1 only rechecks accounts listed in payload's `oracle_pubkeys` +
  `other_pubkeys` groups
- The sim tx may touch additional accounts (token accounts, vaults,
  sysvars, programs, fee_payer) NOT independently fetched/verified
- Caller is responsible for tx-touched accounts validity
- `tx_not_v0` + ALT abort gates + documentation constraint; no full
  tx parsing in v1

### Q3e — Commitment levels (both pinned)

Both `getMultipleAccounts` read and `simulateTransaction` sim use
`processed`. Output records `read_commitment` and `simulate_commitment`
per row.

### Q3f — Payload serialization

JSON. Borsh/bincode is M6.x optimization.

### Q3g — `max_account_count` threshold

Default value: 32 (conservative starter).

**Upstream context:** Solana RPC documentation lists max 100 pubkeys
per `getMultipleAccounts` call. Zela-specific lower limit is unverified
per V7. v1 conservative starter sits well below both.

In practice, M6 v1 uses 10–15 accounts (Pyth oracle group ~10 + other
group ~3–5).

`max_tx_bytes` payload field is separate from `max_account_count`.
Default 1232 bytes (Solana protocol max tx size).

### Q3h — `CustomProcedure::Params` type (v2.3 corrected)

**Use `type Params = JsonValue;`**, not a strict typed Rust struct.

**Why this matters:** the `zela_custom_procedure!` macro and the
underlying WIT interface deserialize the incoming JSON into the
`Params` type **before** `run()` executes. If `Params` is a typed
struct (e.g., `struct M6Payload { oracle_pubkeys: Vec<String>, ... }`)
and the caller sends a payload where the JSON parses syntactically
but doesn't match the struct shape (missing field, wrong type for a
field), the macro returns a host-level JSON-RPC error directly to
the caller — completely bypassing the procedure body and our Q9
output schema. The abort taxonomy value `payload_invalid` would be
unreachable for shape mismatches.

**What `Params = JsonValue` catches and does NOT catch:**

- **Catches as `payload_invalid` (shape/type parse errors only):** valid
  JSON syntax that fails to fit the typed Payload struct during the
  manual `serde_json::from_value` step — missing required fields, wrong
  type for fields (e.g., expecting `Vec<String>`, got number).
- **Catches as specific taxonomy values (domain validation errors):**
  validation failures detected *after* the Payload struct parses
  successfully:
  - `duplicate_pubkey` — same pubkey across or within groups (Q4)
  - `payload_too_large` — account count > `max_account_count` (Q3g)
  - `max_tx_bytes_exceeded` — base64-decoded tx exceeds `max_tx_bytes`
  - `tx_not_v0` — legacy VersionedTransaction variant (Q3d)
  - `tx_uses_address_lookup_table` — v0 tx with ALT (Q3d)
  - All other Q5/Q8 abort reasons fire after read/decode/simulate
    phases reach them
- **Does NOT catch at all (host returns JSON-RPC error before `run()`):**
  syntactically malformed JSON itself — unbalanced braces, invalid
  escape sequences, trailing commas, etc. These fail at the WASI
  host-level deserialize-from-bytes step before `run()` is invoked.
  Caller receives a JSON-RPC error envelope, not the Q9 schema. This
  is unavoidable architecturally; document it explicitly so callers
  know to handle both possibilities.

**Classification rule:** if you can write a Rust struct field that
captures the constraint via type or `#[serde(...)]` attribute, the
violation lands in `payload_invalid`. If the constraint requires
post-parse logic (counting, comparing values, parsing tx bytes), the
violation lands in its specific taxonomy value.

**Required pattern:**

```rust
impl CustomProcedure for M6SimRecheck {
    type Params = JsonValue;
    type SuccessData = JsonValue;
    type ErrorData = ();

    async fn run(params: JsonValue) -> Result<JsonValue, RpcError<()>> {
        // 1. Manually parse params into typed payload
        let payload: M6Payload = match serde_json::from_value(params) {
            Ok(p) => p,
            Err(e) => {
                return Ok(abort_output(
                    "payload_invalid",
                    format!("payload shape: {e}"),
                    timings_zero(),
                ));
            }
        };
        // 2. Validate payload (max_account_count, max_tx_bytes,
        //    duplicate_pubkey, tx_uses_address_lookup_table, tx_not_v0)
        // 3. Continue with read/decode/simulate phases
    }
}
```

**Serde `deny_unknown_fields` decision for M6 v1:** NOT enabled.
Default serde behavior ignores unknown fields, which makes the
payload schema forward-compatible (caller can include extra fields
for their own tracking; procedure ignores them). If we ever want
strict validation that rejects payload with extra fields, add
`#[serde(deny_unknown_fields)]` to the `M6Payload` struct. M6 v1
prototype default = lenient (matches Solana RPC conventions).

This pattern preserves the invariant: **for any payload that the
WASI host accepts as syntactically valid JSON, the procedure
response schema (Q9) is what the caller receives, never a
host-level JSON-RPC error envelope.** For malformed JSON syntax,
host-level error is unavoidable.

---

## Q4 — Account list determinism

- **Internal handling:** during `getMultipleAccounts` decode, the
  procedure holds account data in an internal data structure
  (HashMap-by-pubkey or Vec keyed by pubkey order). This structure is
  **not exposed in the procedure response.**
- **Output exposes only:** `accounts_read` (u32 count) and
  `oracle_summary` (per-Pyth-pubkey decoded fields). Raw account data
  is intentionally not returned — sending byte blobs back to caller
  would inflate response size with no benefit (caller already has the
  pubkey list).
- **Duplicate handling:** abort `duplicate_pubkey` (same pubkey in
  oracle + other groups, or twice in one group).
- **Missing account handling:** abort `account_not_found` (RPC
  returned `null` for some pubkey; pubkey listed in abort detail).

---

## Q5 — Decode strategy (Pyth pull oracle, PriceUpdateV2)

After Q2 generic + A6 (Pyth-only), decode reduces to the Pyth **pull-oracle**
`PriceUpdateV2` account layout for each `oracle_pubkey`. (Pivot from the legacy
push-oracle `PriceAccount`, which Pyth sunset June 30, 2024 — see "Dead legacy
push oracle" note at the end of this section.)

### Field extraction (6 reported, 3 gated)

Decoded per Pyth account and surfaced in `oracle_summary`:
- `price` (i64) + `expo` (i32, the PriceUpdateV2 `exponent` field) — current
  price = `price × 10^expo`
- `publish_time` (i64, unix seconds) — recency signal; gate
  `oracle_publish_time_too_stale`
- `conf` (u64) — confidence interval; gate `oracle_confidence_too_wide`
- `verification_level` (enum -> string) — Wormhole guardian verification; gate
  `oracle_verification_partial`
- `posted_slot` (u64) — slot the update landed on-chain; **reported only**, not
  gated (caller's own slot-drift check, analogous to `context_slot` in Q3c)
- `feed_id` (32 bytes) — **decoded and reported**, NOT gated at runtime (generic
  path; caller is responsible for passing correct `oracle_pubkeys`, per A7). The
  Task 0 fixture test DOES assert `feed_id` exact-match as a decode-correctness
  cross-check (see below).

### verification_level is variable-length — decode-order coupling (CRITICAL)

`VerificationLevel` (canonical source: `pyth_solana_receiver_sdk::price_update`):
```rust
pub enum VerificationLevel {
    Partial { num_signatures: u8 },  // Borsh tag 0x00, then 1 byte num_sigs -> 2 bytes
    Full,                            // Borsh tag 0x01, no payload          -> 1 byte
}
```
Its serialized length is **not fixed**: `Full` = 1 byte, `Partial` = 2 bytes.
The account is allocated at the maximum (`PriceUpdateV2::LEN = 134`), so a `Full`
account serializes to 133 bytes with 1 trailing zero pad; a `Partial` account
uses all 134.

**Consequence:** every field after offset 40 shifts by +1 between Full and
Partial. The hand-verified offset table below is the **Full** layout. A manual
decoder hardcoded to it would misread a Partial account by one byte for every
field after `verification_level`.

**Mitigation (mandatory):** read the `verification_level` tag at offset 40 FIRST
and apply the `oracle_verification_partial` gate before reading any price field.
Because the gate requires `Full`, any account that reaches price decode is
guaranteed Full, and the Full-layout offsets are then sound. (If the
SDK-deserialize path is used instead of manual offsets, Borsh handles the
variable length natively and this coupling is automatic — but the gate still
runs.)

### PriceUpdateV2 byte layout (Full account; market-price cross-checked)

| Offset  | Field                | Size | Notes |
|---------|----------------------|------|-------|
| 0–7     | Anchor discriminator | 8    | = sha256("account:PriceUpdateV2")[..8] = `22 f1 23 63 9d 7e f4 cd` (Anchor-derived; C1 asserts the fixture matches) |
| 8–39    | write_authority      | 32   | Pubkey |
| 40      | verification_level   | 1    | 0x01 = Full (this table); 0x00 = Partial -> +1 shift below |
| 41–72   | feed_id              | 32   | SOL/USD = `ef0d…b56d` |
| 73–80   | price                | 8    | i64 LE |
| 81–88   | conf                 | 8    | u64 LE |
| 89–92   | exponent             | 4    | i32 LE (−8 for SOL/USD at capture) |
| 93–100  | publish_time         | 8    | i64 LE — recency gate target |
| 101–108 | prev_publish_time    | 8    | i64 LE |
| 109–116 | ema_price            | 8    | i64 LE |
| 117–124 | ema_conf             | 8    | u64 LE |
| 125–132 | posted_slot          | 8    | u64 LE |
| 133     | (trailing zero pad)  | 1    | Full only; absent in Partial (which ends at 133) |

Offsets 0–92 are empirically verified (prior-session hand-decode + market-quote
cross-check on the captured fixture). Offsets 93–133 are **source-confirmed**
against `pythnet-sdk 2.3.1` `src/messages.rs`: `PriceFeedMessage` is declared
`feed_id, price, conf, exponent, publish_time, prev_publish_time, ema_price,
ema_conf`, and Borsh serializes in declaration order with no alignment padding,
so `publish_time` is exactly at 93–100 and `posted_slot` (the last
`PriceUpdateV2` field, after the 84-byte `price_message`) at 125–132. The
`publish_time` doc-comment in that source confirms the field is in **seconds**,
which is what the time-based staleness gate assumes. C1 should still spot-check
the decoded values against the fixture (recent-ish `publish_time`; `posted_slot`
≈ 422767475) as a sanity check, but the field order is no longer inferred. For a
Partial account add +1 to every offset from feed_id (41) onward.

### Edge cases in math (overflow-safe — unchanged from v2.5)

- **Confidence ratio (overflow-safe u128 math):**
  ```rust
  let price_abs = price.checked_abs().ok_or(OracleDecodeFailed)? as u128;
  if price_abs == 0 {
      return Err(OracleDecodeFailed); // non-positive price
  }
  let ratio_bps_u128 = (conf as u128).saturating_mul(10_000) / price_abs;
  if ratio_bps_u128 > max_confidence_ratio_bps as u128 {
      return Err(OracleConfidenceTooWide);
  }
  ```
  - `checked_abs` handles `i64::MIN` correctly (returns `None`)
  - `u128` prevents overflow when `conf` × 10000 exceeds `u64::MAX`
  - `saturating_mul` prevents wrap; division by zero guarded above
- **Zero or negative price:** abort `oracle_decode_failed`, detail
  "non-positive price"
- **Publish time in future:** if `publish_time > now_unix +
  max_clock_skew_seconds` (payload field, default 5), abort
  `oracle_decode_failed`, detail "publish_time in future". `now_unix =
  chrono::Utc::now().timestamp()`. Rationale: a `publish_time` ahead of the
  executor clock is almost always benign executor-vs-chain clock skew, not a
  malicious or corrupt update, so a small tolerance avoids spurious aborts;
  only a publish_time implausibly far ahead indicates a decode / byte-order
  error. This is a *distinct* check from the staleness gate — see "Two
  distinct time checks" below.

### Staleness gate (time-based)

`oracle_publish_time_too_stale` fires when
`now_unix - publish_time > max_publish_time_lag_seconds`, where
`now_unix = chrono::Utc::now().timestamp()` (executor host wall-clock).

Rationale: this matches the receiver-SDK's own canonical recency check
(`price.publish_time + maximum_age >= clock.unix_timestamp`, age in **seconds**;
the SDK examples use 60). The executor wall-clock is the time source available
inside the procedure — `block_time` in `zela-demo` already calls
`chrono::Utc::now()` and compares it against on-chain block time, establishing
both that the clock is present and that it tracks chain time closely. No
Clock-sysvar read is added (account count stays unchanged); `Clock::get()` is
unavailable here (we are a Zela RPE procedure, not an on-chain Solana program).

`max_publish_time_lag_seconds` is a payload field, Phase-0-calibrated. Pyth's
heartbeat is ~1 min / 0.5 % deviation, so a healthy feed's `publish_time` is
typically seconds old; thresholds in the 30–60 s range are the natural starting
point, with `v2_tight_staleness` exercising a low (borderline) value.

(`chrono::Utc::now()` availability on `wasm32-wasip2` is now Task 0 acceptance
criterion #7 — a falsifiable build check rather than a prose aspiration.)

### Two distinct time checks (implement BOTH)

`publish_time` drives two separate guards. They are not the same check and
Builder must implement both:

| Check | Condition | Abort | Threshold (default) |
|-------|-----------|-------|---------------------|
| Future-skew sanity | `publish_time > now_unix + max_clock_skew_seconds` | `oracle_decode_failed` ("publish_time in future") | `max_clock_skew_seconds` (5) |
| Staleness gate | `now_unix - publish_time > max_publish_time_lag_seconds` | `oracle_publish_time_too_stale` | `max_publish_time_lag_seconds` (30–60) |

Both use `now_unix = chrono::Utc::now().timestamp()`. The first catches a
timestamp *ahead* of the clock (decode error or large skew); the second catches
a timestamp too far *behind* (stale feed). A correct update satisfies
`now_unix - max_publish_time_lag_seconds <= publish_time <= now_unix + max_clock_skew_seconds`.

### SDK choice and ordering (Task 0 — hard precondition)

Primary decode crate is **`pyth-solana-receiver-sdk`** (`PriceUpdateV2`).
Caveat: it is an **Anchor** crate (`use anchor_lang::prelude::*`, `#[account]`,
`AnchorSerialize` / `AnchorDeserialize`). Pulling Anchor and its transitive
`solana-program` deps into a `wasm32-wasip2` Zela procedure is a real compile
risk; do **not** assume it builds. Task 0 gates it empirically.

Carry forward the prior-C1 alignment finding: `bytemuck::from_bytes` panics on
unaligned input; `include_bytes!` and base64-decoded `Vec<u8>` are both 1-byte
aligned. Copy into an 8-byte-aligned buffer before any zero-copy SDK call.
Affects both fixture loading AND the production RPC data path.

**Captured Pyth fixture (pull oracle — already captured):**
- Address: `7UVimffxr9ow1uXYxsr4LHAcV58mLzhmwaeKvJ1pjLiE` (sponsored shard 0, SOL/USD)
- Owner: `rec5EKMGg6MxZYaMdyBfgwp4d5rB9T1VQH5pJv5LtFJ` (pull receiver)
- feed_id: `ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d`
- Size: 134 bytes; heartbeat 1 min / 0.5 % deviation
- Fixture: `procedures/m6_sim_recheck/tests/fixtures/pyth_sol_usd_pull_422767475.bin` + `.json`
- Re-capture: `procedures/m6_sim_recheck/tests/fixtures/capture_pull.sh`
  (self-verifying: rejects the dead legacy owner; requires feed_id present in data)

**Task 0 acceptance criteria (pull oracle):**

1. Build minimum-viable Pattern C crate per Q1 architecture: shared `run_core` +
   cfg-gated `read_accounts` / `simulate_transaction` adapter functions + WASM
   entrypoint module.
2. Import the chosen Pyth decode path AND the Solana tx-parsing crates
   (`solana-transaction` / `solana-message`, `features = ["bincode"]`).
3. PriceUpdateV2 fixture decode exposes all of: `price` (i64), `expo` (i32,
   = exponent), `conf` (u64), `publish_time` (i64), `verification_level`
   (Full / Partial(N)), `posted_slot` (u64).
4. Decode cross-checks (prove the layout, not just "it ran"):
   - first 8 bytes == Anchor discriminator `22 f1 23 63 9d 7e f4 cd`
     (= sha256("account:PriceUpdateV2")[..8]); a mismatch means the account is
     not a PriceUpdateV2 or uses a non-standard discriminator — flag and stop
   - `feed_id` exact-match `ef0d…b56d` (strong anchor)
   - i32 LE `exponent` at the layout offset == expected (−8 for SOL/USD at capture)
   - decoded price in a loose USD sanity band: `100 < price * 10^expo < 10_000`
     (intentionally far wider than any realistic SOL price — dispositive against
     byte-order / decode errors, not a market check)
   - offsets 93–133 (`publish_time` … `posted_slot`) spot-checked against the
     fixture (order is source-confirmed from pythnet-sdk 2.3.1; this is a sanity
     check, not archaeology): `publish_time` decodes to a plausible recent unix
     time, `posted_slot` ≈ 422767475
5. Solana tx parsing smoke (unchanged from v2.4): construct a minimal v0
   `VersionedTransaction`, bincode round-trip, match on `VersionedMessage::V0(_)`;
   repeat with legacy, match on `VersionedMessage::Legacy(_)`.
6. Compile clean on BOTH `wasm32-wasip2` AND native — Pyth decode path AND tx
   parsing crates. `cargo build --target wasm32-wasip2` and `cargo build` green.
   **If the receiver-SDK does not build on wasip2, fall back to the manual Borsh
   decode (below) and record the outcome.**
7. Cross-check: `chrono::Utc::now().timestamp()` inside the procedure returns a
   unix time within ±5 s of host wall-clock at call time. If it doesn't
   (constant clock, panic, or wildly off), the publish_time staleness gate
   cannot be implemented as designed — flag and stop.

**Fallback chain (inverted for the pull pivot):**

1. **Primary:** `pyth-solana-receiver-sdk` `PriceUpdateV2` via Borsh / Anchor
   deserialize — if it compiles to wasip2 AND decodes the fixture, use it. Borsh
   handles the variable-length `verification_level` automatically.
2. **Fallback — manual Borsh decode (likely, given Anchor-on-wasip2 risk):** read
   discriminator (8) + write_authority (32), then the `verification_level` tag at
   offset 40 (gate Full first), then the Full-layout offsets above for
   `price` / `conf` / `exponent` / `publish_time` / `posted_slot`. No Anchor
   dependency. Document the offsets and the verification_level-length rule in
   source comments. **Cleanest manual path (verified):** depend on `pythnet-sdk`
   WITHOUT its `solana-program` feature — its conditional derives then resolve to
   plain `BorshSerialize`/`BorshDeserialize` (no `anchor_lang`), so you can Borsh-
   decode `PriceFeedMessage` directly with zero Anchor in the wasip2 build. Wrap
   it as: 8-byte discriminator + `write_authority` (32) + `verification_level`
   (1 B Full after gate) + `PriceFeedMessage` (Borsh) + `posted_slot` (u64).

There is no longer a "try legacy `pyth-sdk-solana` first" step — `pyth-sdk-solana`
decodes the sunset push-oracle format and is not applicable to `PriceUpdateV2`.

**Dead legacy push oracle (do NOT use; historical / footgun reference):**
- Account `H6ARHf6YXhGYeQfUzQNGk6rDNnLBQKrenN712K4AQJEG`, owner
  `FsJ3A3u2vn5cTVofAjvy6y5kwABJAqYWpe4975bi2epH` (sunset program).
- Sunset June 30, 2024. When read now: publish_slot ~13 months stale, status
  "Unknown". M5 fixtures came from these accounts (latency numbers stand — the
  read path is freshness-agnostic; framing footnote owed, see BACKLOG B5).
- This address still appears in `solana-developers/program-examples` as of the
  pivot date — that third-party reference is NOT updated for the sunset. Footgun.

### Per-oracle error handling

Abort the entire procedure on any per-oracle decode failure
(`oracle_decode_failed` with the offending pubkey in abort detail). A partially
decoded set is an inconsistent decision input.

---

## Q6, Q7 — Dropped

Consequences of Q2 generic resolution:
- Q6 (recompute) — Kamino-specific work
- Q7 (tx assembly) — Kamino-specific work

Both in backlog under Kamino deferred work.

---

## Q8 — `simulateTransaction` config

Six flags with explicit values:

- **`sigVerify: false`** — recorded in output (`sig_verify` field) for
  honest interpretation
- **`replaceRecentBlockhash: true`** — recorded in output. Durable
  nonce txs are out of scope for M6 v1; behavior under
  replaceRecentBlockhash is unverified (Pending Verification #1).
- **`commitment: processed`** — matches read commitment per Q3e
- **`accounts` snapshot: none in M6 v1** — consistent with A4
- **`minContextSlot: <read step's context_slot>`** — recorded in
  output. If cluster has not reached this slot when sim arrives at
  RPC, behavior produces a `min_context_slot_not_reached` abort. Wait
  semantic vs immediate error is unverified (Pending Verification #2).
- **`encoding: "base64"`** — from Q3d

All six flag values appear individually in procedure response (Q9
per-row metadata: `sig_verify`, `replace_recent_blockhash`,
`simulate_commitment`, `min_context_slot`, etc.). No bundled
`sim_config_echo` object — individual fields are discoverable.

### RPC error catch-and-convert rule (v2.2 — corrected against actual zela-std API)

**zela-std `call_rpc` signature (verified from source):**
```rust
pub fn call_rpc<P, Rs, Re>(method: &str, params: P)
    -> Result<Result<Rs, RpcError<Re>>, std::io::Error>
```

This is **synchronous (no `.await`)** and returns a **nested
Result**: outer `io::Error` covers WASI/host-level transport errors
and JSON deserialization failures into `Rs`; inner `RpcError {
code: i32, message: String, data: Option<Re> }` covers Solana
JSON-RPC `error` responses. **Note on `code` type (v2.4 correction):**
this is `i32` per the WIT contract `record rpc-error { code: s32,
... }` in `zela-std/src/zela.wit`. Earlier design log versions and the
`procedure-anatomy.md` reference document say `i64`; that documentation
is stale — actual zela-std source produces `i32`. There is no
`RpcError::Transport / Server / Decode` variant enum — earlier design
log versions described this incorrectly.

**To make abort taxonomy values reachable, deserialize into
`serde_json::Value` first, then parse manually:**

```rust
// READ phase
let read_raw: Result<Result<Value, RpcError<JsonValue>>, io::Error> =
    call_rpc("getMultipleAccounts", params);

match read_raw {
    Err(io_err) => {
        // Transport/host-level error — WASI errno or response not parseable as JSON
        return Ok(abort_output(
            "read_rpc_error",
            format!("transport: {io_err}"),
            phase_timings,
        ));
    }
    Ok(Err(rpc_err)) => {
        // Solana RPC returned JSON-RPC error envelope
        return Ok(abort_output(
            "read_rpc_error",
            format!("rpc code {}: {}", rpc_err.code, rpc_err.message),
            phase_timings,
        ));
    }
    Ok(Ok(value)) => {
        // Got JSON; now manually parse expected shape
        match parse_multiple_accounts_response(&value) {
            Ok(parsed) => /* continue */,
            Err(parse_err) => {
                return Ok(abort_output(
                    "read_decode_error",
                    format!("response shape: {parse_err}"),
                    phase_timings,
                ));
            }
        }
    }
}
```

Same pattern for `simulateTransaction`:
- Outer `io::Error` → `simulate_rpc_error`
- Inner `RpcError` → `simulate_rpc_error` (with `code` and `message`
  in detail). **Special case:** if inner `rpc_err.message` contains
  "minimum context slot" or "minContextSlot" substring (case-insensitive),
  map to `min_context_slot_not_reached` instead. This is the explicit
  classification rule for `min_context_slot_not_reached` (per v2.2
  patch list); otherwise the abort taxonomy value would never trigger.
- Manual response parse failure → `simulate_response_decode_error`
- Inner `RpcError` with no min-context-slot signature → fall back to
  `simulate_rpc_error`
- `value.err` extracted from successfully-parsed sim response →
  `simulation_err` (tx-level execution failure)

**Why `serde_json::Value` instead of typed struct deserialization:**
if `call_rpc` is parameterized with a typed `Rs = MultipleAccounts`
and the response shape unexpectedly differs (Solana RPC schema drift,
proxy injecting different envelope, etc.), the deserialization
failure collapses into the outer `io::Error`, making
`read_decode_error` unreachable as a distinct taxonomy value. Using
`Value` and manually parsing keeps the two error sources cleanly
separated.

**Without these rules, the abort taxonomy values
`read_rpc_error`, `read_decode_error`, `simulate_rpc_error`,
`simulate_response_decode_error`, and `min_context_slot_not_reached`
are all unreachable.**

### Native adapter parity (NEW v2.4)

The native test adapter
(`#[cfg(not(target_arch = "wasm32"))] async fn read_accounts(...) /
simulate_transaction(...)`) uses `solana_client::nonblocking::RpcClient`,
which has its own error model. **Correct API shape (Codex
source-verified):** `solana_client::client_error::ClientError` is a
struct with a `.kind()` accessor returning
`solana_client::client_error::ClientErrorKind`, which IS the enum.
Variants are `Io(io::Error)`, `Reqwest(reqwest::Error)`,
`RpcError(rpc_request::RpcError)`, `SerdeJson(serde_json::Error)`,
`SigningError(SignerError)`, `TransactionError(TransactionError)`,
`Custom(String)`. The nested `rpc_request::RpcError` enum has
`RpcRequestError(String)`, `RpcResponseError { code: i64, message:
String, data: RpcResponseErrorData }` (struct-variant), `ParseError`,
`ForUser`.

The native adapter MUST map these to the same abort taxonomy values
so analysis code processing M6 dataset doesn't need to branch on
adapter-of-origin.

**Required native mapping (parallel to WASM rules above):**

```rust
use solana_client::client_error::{ClientError, ClientErrorKind};
use solana_client::rpc_request::RpcError as SolanaRpcError;

match client_error.kind() {
    ClientErrorKind::Reqwest(_) | ClientErrorKind::Io(_) => {
        // transport-level failure to reach RPC
        abort("read_rpc_error", ...) // or "simulate_rpc_error" depending on phase
    }
    ClientErrorKind::RpcError(SolanaRpcError::RpcResponseError {
        code, message, ..
    }) => {
        // Solana RPC returned JSON-RPC error envelope
        // For simulate_* phase, check min_context_slot signature:
        if phase == Phase::Simulate
            && message.to_lowercase().contains("minimum context slot")
        {
            abort("min_context_slot_not_reached", format!("code {code}: {message}"))
        } else {
            abort("simulate_rpc_error" or "read_rpc_error", ...)
        }
    }
    ClientErrorKind::SerdeJson(_) => {
        abort("read_decode_error" or "simulate_response_decode_error", ...)
    }
    ClientErrorKind::TransactionError(_)
    | ClientErrorKind::SigningError(_)
    | ClientErrorKind::Custom(_) => {
        // Unexpected in M6 read/simulate happy paths; map to phase-rpc-error
        // with detail for forensic analysis
        abort("read_rpc_error" or "simulate_rpc_error", format!("unexpected: {client_error}"))
    }
}
```

For `simulateTransaction` returning success with non-null `value.err`
field → `simulation_err` (tx-level execution failure, parallel to
WASM path; this is not a `ClientError`, it's a successful response
with embedded tx failure).

Implementation note: the native adapter wraps `RpcClient` calls in a
thin helper that returns the same `ReadOutcome` / `SimulateOutcome`
enum the WASM adapter returns, so `run_core()` sees identical abort
surface regardless of compile target.

---

## Q9 — Output schema (split into procedure response + dataset row)

**v2.1 schema layering correction:** v2 conflated procedure-emitted
fields with orchestrator-enriched fields. v2.1 splits them
explicitly.

### Procedure response schema (what the WASM procedure returns)

JSON object returned via Zela JSON-RPC response.

#### Always-present fields

```
decision                    enum: "execute" | "abort"
schema_version              string        ("m6.v2.6")
validation_us               u64           (wall-clock for payload validation
                                           + tx parse + ALT check; before any RPC)
total_us                    u64           (wall-clock for entire procedure run,
                                           end-to-end; always present if
                                           procedure returns Ok)
read_commitment             string        ("processed")
simulate_commitment         string        ("processed")
sig_verify                  bool          (echo of sim config)
replace_recent_blockhash    bool          (echo of sim config)
submit_ready                bool          (HARDCODED FALSE in M6 v1; see Q10)
thresholds                  object        (echo of payload thresholds:
                                           max_publish_time_lag_seconds,
                                           max_clock_skew_seconds,
                                           max_confidence_ratio_bps,
                                           max_account_count,
                                           max_tx_bytes)
```

#### Nullable fields (depend on procedure phase reached)

```
abort_reason                enum | null   (null iff decision="execute")
context_slot                u64 | null    (null if abort pre-read)
read_us                     u64 | null    (null if abort pre-read)
decode_us                   u64 | null    (null if abort pre-decode; covers
                                           oracle decode + Pyth gates only,
                                           NOT validation)
simulate_us                 u64 | null    (null if abort pre-sim)
simulate_context_slot       u64 | null    (null if abort pre-sim)
min_context_slot            u64 | null    (null if abort pre-sim, since not
                                           passed to RPC yet)
accounts_read               u32 | null    (null if abort pre-read)
units_consumed              u64 | null    (null if abort pre-sim)
simulation_err              string | null (tx-level value.err from sim only;
                                           non-null iff sim ran and returned
                                           value.err; orthogonal to
                                           simulate_rpc_error abort)
oracle_summary              object | null (null if abort pre-decode)
log_summary                 string | null (optional)
```

#### `oracle_summary` shape (pull oracle — PriceUpdateV2 fields)

Map from pubkey (base58) to object:
```
{ price: i64, expo: i32, publish_time: i64, conf: u64,
  verification_level: string, posted_slot: u64, feed_id: string }
```

### Dataset row schema (procedure response + orchestrator enrichment)

The cron orchestrator wraps each procedure response into a dataset row
by adding orchestrator-side metadata. These fields are NOT returned by
the procedure; the orchestrator adds them when writing to the JSONL.

```
procedure_response          object        (the full procedure response above)
route_header                string        (echo of zela-route-by sent by cron,
                                           e.g., "static fr2")
procedure_revision          string        (commit hash or deploy hash, known
                                           to cron at invocation time)
payload_variant             string        (e.g., "v1_happy", "v2_tight_staleness";
                                           cron-assigned per invocation)
cron_tick_id                string        (UUID or timestamp identifier for the
                                           5-per-day tick)
invocation_index            u32           (0..3 within the tick — which of the
                                           4 variants this was)
variant_order               string        (e.g., "v3,v1,v4,v2" — the
                                           randomized order for this tick)
is_first_in_tick            bool          (true if invocation_index == 0;
                                           explicit for analyzers)
client_pre_call_us          i64           (orchestrator wall-clock pre-call,
                                           unix epoch microseconds)
client_post_call_us         i64           (orchestrator wall-clock post-call)
client_total_us             u64           (post - pre)
```

This split makes it unambiguous: WASM procedure code is responsible for
the procedure response schema only. Orchestrator code is responsible
for dataset row enrichment.

### `abort_reason` taxonomy (expanded — 17 values for v2.3)

```
"payload_invalid"                  — JSON valid but payload shape invalid
                                     (missing required field, wrong type, etc.)
"payload_too_large"                — account count > max_account_count
"max_tx_bytes_exceeded"            — base64-decoded tx exceeds max_tx_bytes
"tx_not_v0"                        — Q3d v2.3 restriction: legacy tx variant
                                     not supported in M6 v1
"tx_uses_address_lookup_table"     — Q3d M6 v1 restriction (v0 tx with ALT)
"duplicate_pubkey"                 — Q4
"account_not_found"                — Q4 (RPC returned null)
"read_rpc_error"                   — getMultipleAccounts JSON-RPC error
"read_decode_error"                — getMultipleAccounts response shape/decode failure
"oracle_decode_failed"             — Q5 (Pyth decode error, non-Pyth pubkey per A6,
                                     non-positive price, publish_time in future)
"oracle_verification_partial"      — Q5 (PriceUpdateV2 verification_level != Full)
"oracle_publish_time_too_stale"    — Q5 (now - publish_time > max_publish_time_lag_seconds)
"oracle_confidence_too_wide"       — Q5 (confidence ratio threshold)
"simulate_rpc_error"               — simulateTransaction JSON-RPC error
"simulate_response_decode_error"   — simulateTransaction response shape/decode failure
"min_context_slot_not_reached"     — minContextSlot constraint not satisfied
                                     (classified by Q8 substring rule)
"simulation_err"                   — simulateTransaction returned value.err
                                     (tx-level failure; detail in simulation_err string)
```

Intentionally excluded:
- `state_drifted` / `state_too_old` (Q3c removed)
- `min_profit_not_met` (A4 deferred)
- `simulation_timeout` (V1: no hard timeout)

### Optional taxonomy collapse for v1 (NEW v2.5.1)

Per Q1 scope guard, Builder may collapse the 17-value taxonomy to a
5-value subset for M6 v1 implementation if convenient. Aggregation:

```
"payload_invalid"   — covers all payload-side validation failures:
                      original payload_invalid, payload_too_large,
                      max_tx_bytes_exceeded, tx_not_v0,
                      tx_uses_address_lookup_table, duplicate_pubkey
                      (caller must read abort_detail string for
                      sub-classification)

"read_error"        — covers all getMultipleAccounts-phase failures:
                      original read_rpc_error, read_decode_error,
                      account_not_found
                      (abort_detail carries specific cause)

"oracle_error"      — covers all Pyth decode + gate failures:
                      original oracle_decode_failed,
                      oracle_verification_partial,
                      oracle_publish_time_too_stale,
                      oracle_confidence_too_wide
                      (abort_detail carries specific cause)

"simulate_error"    — covers all simulateTransaction-phase failures
                      OTHER than tx-level value.err:
                      original simulate_rpc_error,
                      simulate_response_decode_error,
                      min_context_slot_not_reached
                      (abort_detail carries specific cause)

"simulation_err"    — tx-level value.err from simulateTransaction.
                      Kept separate because tx-level failure has
                      different operational meaning than RPC/config
                      failures.
```

The 5-value subset still satisfies Q1 success criterion (execute +
non-trivial abort exercised); `simulation_err`, `oracle_error`, and
`read_error` are all non-trivial. Expansion to full 17-value taxonomy
is M6.1+ refinement if Phase 1 dataset shows diagnostic value in the
finer split.

If Builder chooses the 5-value subset, `abort_detail` field becomes
the disambiguation source — every abort carries a human-readable
string with the specific sub-cause for forensic analysis. This is
also forward-compatible: collapsed values can later be split into the
17-value taxonomy by parsing `abort_detail` strings in analyzer code.

Decision is Builder's call at implementation time; full 17-value
taxonomy stays in this document as aspirational reference.

---

## Q10 — Decision semantic

Per A2 (Q1) decision is binary enum `execute | abort`.

> `execute` = "all relevant abort gates passed; `simulateTransaction`
> RPC call succeeded; `value.err` is null;
> `simulate_context_slot >= context_slot`."
>
> `execute` does **not** mean: tx is profitable, state hasn't drifted,
> signatures are valid, caller should submit.

**Mental rename:** literal string is `execute` for schema stability;
operational meaning is `"simulation_passed_under_sigverify_false"`.
The `submit_ready: false` field in every procedure response is the
explicit warning.

Off-Zela caller drives final submit decision.

**Determinism:** same payload + same Solana state → same decision.

---

## Q11 — Native test scope (Pattern C, gated)

### Compile-target ordering (binding precondition)

Per Q5 Task 0: verify Pyth library compiles to `wasm32-wasip2` before
any other implementation work. Native test scope below applies AFTER
WASM compile is verified.

### Framework

`cargo test` + `tokio::test` +
`solana_client::nonblocking::rpc_client::RpcClient`.

### Test gating

All tests hitting real mainnet RPC are `#[ignore]` OR gated behind
`mainnet-tests` cargo feature. Default `cargo test` runs ONLY pure
validation unit tests (offline). Mainnet tests run via:
```
cargo test -- --ignored
```
or feature flag. CI configures accordingly.

### Test scenarios

Offline unit tests (always run):
```
test_payload_invalid               — "payload_invalid"
test_payload_too_large             — "payload_too_large"
test_max_tx_bytes_exceeded         — "max_tx_bytes_exceeded"
test_duplicate_pubkey              — "duplicate_pubkey"
test_tx_not_v0                     — legacy VersionedTransaction in payload → "tx_not_v0"
test_tx_uses_alt                   — v0 tx with non-empty address_table_lookups → "tx_uses_address_lookup_table"
test_confidence_math_overflow      — u128 math: large conf doesn't overflow
test_confidence_math_negative_price — checked_abs guards i64::MIN
test_abort_oracle_verification_partial — fixture-based: synthesize a
                                     PriceUpdateV2 byte buffer with
                                     verification_level = Partial (tag 0x00
                                     + num_sigs), i.e. the +1-shifted layout;
                                     decode and verify
                                     "oracle_verification_partial" abort.
                                     Doubles as the test that the
                                     verification_level-length / offset-shift
                                     handling is correct (offline, deterministic)
```

Mainnet-gated tests (`#[ignore]`):
```
test_execute_happy_path            — valid Pyth pubkeys, realistic SPL token
                                     transfer tx fixture (see below),
                                     generous thresholds → "execute"
test_abort_account_not_found       — nonexistent pubkey → "account_not_found"
test_abort_oracle_decode           — non-Pyth pubkey in oracle group → "oracle_decode_failed"
test_abort_oracle_pub_time_stale   — max_publish_time_lag_seconds = 0 → "oracle_publish_time_too_stale"
test_abort_oracle_conf_wide        — max_confidence_ratio_bps = 1 → "oracle_confidence_too_wide"
test_abort_simulation_err          — valid pubkeys + intentionally invalid tx
                                     (fee_payer with insufficient lamports for fee
                                     is the recommended construction). MUST include
                                     a preflight assertion test that calls
                                     simulateTransaction directly against Helius
                                     (no Zela proxy) with this fixture and asserts
                                     the response contains a non-null value.err.
                                     If the preflight surfaces an RPC error instead,
                                     the fixture is wrong and must be re-constructed;
                                     test must specifically validate tx-level
                                     simulation failure, NOT RPC/config failure.
                                     Expected M6 abort: "simulation_err".
test_replace_blockhash_smoke       — valid tx with deliberately stale blockhash →
                                     "execute" (verifies replaceRecentBlockhash)
```

### Happy-path tx fixture (precise specification)

**Use `spl_token::instruction::transfer_checked`**, not plain
`spl_token::instruction::transfer`. Justification:
- `transfer_checked` requires mint account in instruction accounts,
  producing more realistic 6-account fan-in (source token account,
  dest token account, mint, owner, Token Program, fee_payer signer)
- No CPI in this fixture — it's a single instruction targeting the
  Token Program from the transaction layer (correction from v2: v2
  incorrectly described this as "System Program → Token Program →
  invoke CPI"; that is not how plain or checked SPL Token transfers
  work)
- Realistic compute unit consumption (~5,000–10,000 CU range)

Fixture parameters:
- `fee_payer` = test wallet with verified mainnet SOL balance for fee
  account (e.g., a known funded address chosen specifically for tests).
  **The fee_payer keypair is NOT required**; the wallet just needs to
  exist on mainnet with non-zero SOL balance for sanitization to pass.
- `source_token_account`, `dest_token_account` = pre-existing token
  accounts holding the same mint with non-zero balance at source
- `mint` = a stable, widely-held SPL token (e.g., USDC or USDT mint)
- Amount transferred = 1 raw unit (minimum non-zero)

### Signing convention (v2.3 explicit decision)

**Use dummy signatures (zero-filled or random bytes) under
`sigVerify: false`.** Q8 default already specifies `sigVerify: false`
(consistent with `submit_ready: false` discipline). Therefore:

- The tx fixture does NOT need to be signed with a real keypair
  controlling `fee_payer`
- Signatures are placeholder bytes (`[0u8; 64]` per signer slot)
- Solana's `simulateTransaction` accepts unsigned-but-syntactically-valid
  tx when `sigVerify: false`
- Caller workflow in production may submit signed tx separately
  (off-Zela); M6 procedure semantic is "simulation succeeded under
  no-signature-check", explicitly NOT "this tx is submission-ready"

**Preflight assertion:** `test_happy_path_preflight_outside_zela`
calls `simulateTransaction` directly against Helius (no Zela proxy)
with the same dummy-signed fixture, asserts success (i.e.,
`value.err: null`). Validates fixture independently of procedure
logic. If preflight fails, fixture is wrong (likely fee_payer
balance issue, wrong mint, or invalid account state) — must be
re-constructed.

If captured real protocol tx (Kamino liquidation) is needed for M6.1
demo, that's separate fixture work with real signing requirements.

### WASM smoke test

After `cargo test --ignored` passes: build for `wasm32-wasip2`,
manual upload + invoke via `call-and-test.md` script.

**Coverage expectation:** smoke test invokes happy path AND at least
one non-trivial abort (`oracle_publish_time_too_stale` via
`max_publish_time_lag_seconds = 0` is easiest to trigger
deterministically) via deployed WASM. Not all scenarios in WASM; M6
dataset substitutes via Phase 0 + Phase 1 production runs.

---

## Q12 — Measurement plan (corrected cadence, rotated order, valid JSONL)

### Cadence

**All 4 variants run per cron tick.** Each tick emits 4 procedure
invocations sequentially.

5x daily cron × 4 variants per tick = **20 datapoints/day**.

### Variant order rotation (new in v2.1)

Variants are NOT in fixed `v1, v2, v3, v4` order per tick. If always
fixed, `v1_happy` always pays first-call/cold/session effects and
latency comparisons become biased.

**Per tick, the orchestrator randomizes the order** of the 4 variants
and records the order in dataset row metadata
(`variant_order`, `invocation_index`, `is_first_in_tick`). Simple
Python random.shuffle is sufficient; deterministic seed not required.

Analysis must condition on `is_first_in_tick` when comparing
per-variant latency.

### Payload variants (4)

```
v1_happy            valid Pyth, realistic SPL TransferChecked tx fixture,
                    generous thresholds
v2_tight_staleness  valid Pyth, max_publish_time_lag_seconds = 5
                    (borderline; Phase 0 calibration vs observed publish_time age)
v3_tight_confidence valid Pyth, max_confidence_ratio_bps = low
                    (borderline; Phase 0 calibration)
v4_bad_tx           valid Pyth, intentionally invalid tx → guaranteed
                    "simulation_err"
```

### Phased rollout

- **Phase 0 (smoke):** 1–2 days × 20/day = **20–40 datapoints**.
  Pipeline verification + threshold tuning for v2/v3.
- **Phase 1 (M6 v1 dataset):** after Phase 0 green, **continue 5 more
  days × 20 = ~100 additional datapoints**, yielding **~140 total
  datapoints** combined with Phase 0.
- **Continuous:** cron keeps running after Phase 1 close.

### Routing

`zela-route-by: static fr2`; recorded in `route_header` field of every
dataset row.

### Baseline

**No baseline in M6 v1 dataset.** Per Q1 architectural feasibility
focus. Baseline belongs to M6.1 or M7.

### Timing brackets (defined explicitly, v2.1 corrected)

```
validation_us — wall-clock for payload validation, tx parse, ALT check,
                duplicate check, account count check. Before any RPC.
                Always present.
read_us       — wall-clock for getMultipleAccounts RPC call only.
                Excludes local processing. Null if abort pre-read.
decode_us     — wall-clock for oracle decode + Pyth gates only
                (post-read, pre-sim). Validation is in validation_us,
                NOT decode_us. Null if abort pre-decode.
simulate_us   — wall-clock for simulateTransaction RPC call only.
                Excludes local processing. Null if abort pre-sim.
total_us      — wall-clock for entire procedure run, end-to-end.
                Always present.
```

If procedure path = `validation → read → decode → simulate`:
`total_us ≈ validation_us + read_us + decode_us + simulate_us +
intra-step overhead`.

### Per-row capture

Procedure response (full Q9 procedure-response object) + orchestrator
enrichment (Q9 dataset-row schema). All in JSONL daily files at
`zela_datasets/m6_sim_recheck/YYYY-MM-DD.jsonl`.

### JSONL metadata row

First line of each JSONL file is a valid JSON object:

```json
{
  "record_type": "metadata",
  "schema_version": "m6.v2.6",
  "generated_at": "<ISO 8601 timestamp>",
  "phase": "0_smoke" | "1_dataset" | "continuous",
  "disclaimer": "M6 measures generic simulation recheck feasibility on Zela, not strategy profitability. The 'execute' decision indicates simulateTransaction succeeded under sigVerify=false; it is NOT a submission endorsement. Caller is responsible for signature validity, profitability assessment, state-drift verification, and tx-touched-account validation beyond the payload pubkey list."
}
```

Subsequent rows have `record_type: "data"` (or omit `record_type`).
Analyzer skips rows where `record_type == "metadata"`.

---

## Assumptions log (A1–A7)

- **A1** — M6 v1 runs generic path only.
- **A2** — `decision` enum: `execute | abort` only.
- **A3** — Pattern C: one inherent `run` + WASM `CustomProcedure`
  impl `#[cfg(target_arch = "wasm32")]`.
- **A4** — `simulated_profit` decoding deferred.
- **A5** — Output JSON is primary observability surface; procedure
  logs via Zela's primitive are secondary, not relied upon for
  dataset.
- **A6** — Oracle decode = Pyth **pull-oracle** `PriceUpdateV2` binary
  layout exclusively (post-June-2024 Pyth standard; legacy push-oracle
  `PriceAccount` is sunset — see Q5 dead-legacy footgun note).
- **A7** — M6 v1 does not parse tx for full account fan-in
  verification beyond ALT check. Accounts touched by tx are caller's
  responsibility.

---

## Reassess fixes (R1–R5)

- **R1** — Q1 non-trivial abort requirement
- **R2** — Q1 NG2 dataset metadata row disclaimer
- **R3** — Q2 M8 decomposition (M8a/M8b/M8c)
- **R4** — Q3b → A6 (Pyth-only)
- **R5** — Q3g concurrency vs account count limit correction

---

## Verified Zela facts from docs.zela.io (V1–V9)

- **V1** — No hard timeout on procedure runtime
- **V2** — Outbound = Zela Solana proxy only
- **V3** — No procedure-to-procedure calls
- **V4** — No shred stream access
- **V5** — No scheduling / event triggers
- **V6** — Sequential awaits only
- **V7** — Per-call account count limit not documented (Solana
  upstream max is 100 per Solana RPC docs)
- **V8** — Concurrency limit is per-procedure parallel invocations,
  NOT per-call account limit
- **V9** — Non-custodial; pre-signed tx or client-side signing

---

## Context references with source URLs

Out of implementation acceptance criteria; tracked for context only:

- **"300 sims in 300ms"** — Zela marketing (Apr 30 post). In-procedure
  loop semantics. M8b scope to verify with representative workload.
- **Private fiber backbone** — Zela marketing (Apr 16 post). Relevant
  to M7.
- **Stake-weighted submission** — Zela marketing (Apr 2 post). FAQ
  ambiguity flagged for M8c.
- **Early beta with limits subject to change** — FAQ #5. M9 caveat.
- **Solana `simulateTransaction` reference:**
  https://solana.com/docs/rpc/http/simulatetransaction
- **Solana `getMultipleAccounts` reference (max 100 pubkeys):**
  https://solana.com/docs/rpc/http/getmultipleaccounts
- **Pyth pull-oracle integration (PriceUpdateV2, verification_level,
  publish_time, get_price_no_older_than):**
  https://docs.pyth.network/price-feeds/core/use-real-time-data/pull-integration/solana
- **`pyth-solana-receiver-sdk` PriceUpdateV2 source (canonical layout):**
  https://github.com/pyth-network/pyth-crosschain/blob/main/target_chains/solana/pyth_solana_receiver_sdk/src/price_update.rs

---

## Tracker queue (RESOLVED as of v2.2)

**Closing update was completed May 26, 2026 with split into 3 files
(SESSION_SUMMARY.md, BACKLOG.md, POTENTIALITIES.md). All tracker items
below have been migrated to their respective files. This section
retained for v1→v2.2 traceability.**

### Backlog (now in `BACKLOG.md`)

1. Kamino-specific liquidator procedure
2. Hard latency budget definition (p50/p95 SLO)
3. Switchboard / alternative oracle decode support
4. ALT support in M6 procedure
5. ~~SESSION_SUMMARY split refactor~~ — RESOLVED in closing pass

### Potentialities (now in `POTENTIALITIES.md`)

1. Oracle price drift gate with per-asset thresholds (Potenciality 17)

### SESSION_SUMMARY closing additions (all migrated)

- V1–V9 verified Zela facts (+ Solana RPC max-100 upstream)
- M8 scope decomposition + FAQ ambiguity flag
- M7 fiber backbone fact
- M9 early-beta caveat
- M6 acceptance criteria
- A6 + A7 assumptions
- Q3g justification rewrite
- Section rename Potencialitas → Potenciality
- Q12 cadence + variant rotation methodology
- Three-axis observation: Zela executor locations × client orchestrator
  locations × baseline RPC providers
- Pyth status field check as standard oracle gate (A6 supplement)

---

## Reconciliation changelog (v2.5.1 -> v2.6)

Pull-oracle pivot. M6 was targeting Pyth's legacy push-oracle
`PriceAccount`; the captured C1 fixture during the prior Task 0 session
turned out to be that sunset account (Pyth sunset June 30, 2024). v2.6
re-points decode + gates at the live pull oracle (`PriceUpdateV2`).
Unlike v2.5.1 (doc-only), this changes technical content + schema.

| # | Item | v2.6 location | Severity | Source |
|---|------|---------------|----------|--------|
| 1 | Q5 rewritten for `PriceUpdateV2` pull oracle: variable-length `verification_level` finding (`Full`=1 B / `Partial`=2 B) + decode-order coupling (gate Full before reading Full-layout price offsets); corrected byte layout (0-92 empirically hand-verified; 93-133 source-confirmed from pythnet-sdk 2.3.1 messages.rs, C1 spot-checks); SDK fallback chain inverted (receiver-sdk Anchor primary + wasip2 compile risk, manual Borsh fallback); legacy `pyth-sdk-solana` step removed. | Q5 (rewritten) | HIGH | pull pivot + canonical source cross-check |
| 2 | Staleness gate slot-based -> time-based: `oracle_too_stale` (`context_slot - publish_slot > max_publish_slot_lag`) -> `oracle_publish_time_too_stale` (`now_unix - publish_time > max_publish_time_lag_seconds`, `now_unix = chrono::Utc::now().timestamp()`). Matches receiver-SDK's own `get_price_no_older_than` (age in seconds). `chrono::Utc::now()` availability confirmed via zela-demo `block_time` precedent. | Q3c, Q5, Q9 taxonomy, Q11, Q12 v2 variant | HIGH | pull pivot + SDK source + zela-demo |
| 3 | Verification gate replaces trading-status gate: `oracle_not_trading` (legacy `status` enum) -> `oracle_verification_partial` (`verification_level != Full`). PriceUpdateV2 has no `status` field. | Q3c, Q5, Q9 taxonomy, Q11 | HIGH | pull pivot + canonical source |
| 4 | `oracle_summary` shape: drop `publish_slot`, `status`; add `publish_time`, `verification_level`, `posted_slot`, `feed_id`. | Q9 oracle_summary | MED | derived from #1-#3 |
| 5 | Payload / threshold field `max_publish_slot_lag` -> `max_publish_time_lag_seconds`; thresholds echo + v2 variant updated. | Q3c, Q9 thresholds, Q12 | MED | derived from #2 |
| 6 | `oracle_decode_failed` future-check `publish_slot in future` -> `publish_time in future`. | Q5, Q9 taxonomy | LOW | derived from #2 |
| 7 | C1 fixture is the captured pull account (`7UVi...jLiE`, owner `rec5...LtFJ`, 134 B, slot 422767475, `capture_pull.sh`); legacy `H6ARHf...` retained only as a dead-account footgun reference. | Q5 captured fixture | MED | prior Task 0 session |
| 8 | `schema_version` `m6.v2.5` -> `m6.v2.6` (v2.3 coupling rule applies — not a doc-only patch). | Q9 + Q12 schema_version | MED | coupling rule |
| 9 | Pending verification items 3-4 re-pointed (receiver-sdk wasip2 risk; `chrono::Utc::now()` on wasip2). Context-reference URLs updated to pull-oracle docs + receiver-sdk source. | Pending verification; Context references | LOW | discipline |
| - | 5-value taxonomy collapse `oracle_error` membership updated to the new gate names. | Q9 collapse | LOW | derived from #2-#3 |
| - | End marker `v2.5.1` -> `v2.6`. | Last line | LOW | discipline |

### Carried forward unchanged

Procedure architecture (Pattern C cfg-gated adapters), `call_rpc` shape,
`VersionedTransaction` / `VersionedMessage` API, `ClientErrorKind` native
mapping, `solana-transaction` / `solana-message` bincode features, Q9 schema
layering (procedure response vs dataset row), Q12 cadence + variant rotation,
the full 17-value / 5-value abort taxonomy structure (only the three oracle
gate *names* changed), and all v2.5.1 scope-guard / Builder-priority additions.
The pivot touches the oracle read + gate surface only.

**Note (schema_version literals):** the two canonical declarations — the Q9
procedure-response schema field and the Q12 JSONL metadata row — now read
`m6.v2.6`. Builder should source `schema_version` from a single constant rather
than hardcoding the string in multiple places, so future bumps touch one line.

---

## Reconciliation changelog (v2.5 → v2.5.1)

Documentation-only patch. 3 targeted additions based on two reviewer
reflections converging with internal critique on overengineering
signal. No technical content changes, no schema changes.

| # | Item | v2.5.1 location | Source |
|---|------|-----------------|--------|
| 1 | Builder implementation priority order — 7-step sequential gate from feasibility floor. | Q1 (new subsection between Success criterion and Non-goals) | Reviewer reflection R2 |
| 2 | Scope guard rule with delay-test for diagnostic refinements. Concrete applications listed (taxonomy collapse, native parity, fixture realism, variant order). | Q1 (new subsection) | Reviewer reflection R1 |
| 3 | Optional taxonomy collapse — Builder may ship 5-value subset (`payload_invalid`, `read_error`, `oracle_error`, `simulate_error`, `simulation_err`) for M6 v1; `abort_detail` carries sub-classification. Expansion to full 17-value taxonomy is M6.1+ refinement if dataset reveals diagnostic value. | Q9 (new subsection after taxonomy list) | Reviewer reflection R1+R2 |
| - | End marker `v2.5` → `v2.5.1`. | Last line | discipline |
| - | Schema `m6.v2.5` UNCHANGED (explicit coupling-rule exception: doc-only patch should not force dataset schema string change). | Q9 + Q12 schema_version fields | reasoning in header |

### Why no full rewrite

The reflections argued — and I agree — that the spec accumulated
overengineering across 6 review cycles. The correct response is NOT
to discard the accumulated rigor (which prevented real implementation
traps: Pattern A/C contradiction, solana-client-in-WASM,
VersionedTransaction API shape, ClientErrorKind enum location, etc.).
The correct response is to give Builder explicit license to ship
lean version while preserving aspirational spec as M6.1+ reference.
v2.5.1 does exactly that.

### Self-flag (process-level, not API-level)

Six per-version Codex review cycles each found legitimate fixes.
Cumulative effect: spec became heavier than M6 purpose. No single
review was wrong; the meta-loop optimized for "fix everything Codex
flags" without scope discipline. Two independent reviewer reflections
caught the meta-pattern; my own reflection earlier in session did
too. Triangulation of three sources is the discipline signal that
mattered, not any single critique.

Mitigation for future similar work: after first 2–3 review rounds on
a design doc, add explicit "are we still adding necessary fixes or
adding polish?" check before accepting next round.

---

## Reconciliation changelog (v2.4 → v2.5)

6 patch items from two independent Codex reviews of v2.4 integrated.
All source-verified bugs in v2.4 prose; no new design surface.

| # | Item | v2.5 location | Severity | Source |
|---|------|---------------|----------|--------|
| 1 | Schema version `m6.v2.3` → `m6.v2.5` (v2.4 forgot to bump from v2.3 despite explicit coupling rule). | Q9 procedure response + Q12 JSONL metadata | MED | R1 + R2 |
| 2 | `tx_not_v0` added to Q1 trivial aborts exclusion list (Q1 v2.4 missed it; it is validation-level, must not satisfy non-trivial abort quota). | Q1 success criterion | MED | R1 |
| 3 | Solana tx parsing dependency guidance precise: `solana-transaction = { features = ["bincode"] }` + `solana-message = { features = ["bincode"] }`. Removed vague "solana-program likely candidate" wording. Verified per Codex source-check of local crate releases. | Q1 Cargo.toml dependencies (rewritten) + Q3d pseudokod imports | HIGH | R1 + R2 |
| 4 | Native adapter error mapping corrected: `client_error.kind()` returns `ClientErrorKind`, the enum with variants. `ClientError` is a struct (not enum); pattern `ClientError::Reqwest(_)` is invalid. Also `RpcError::RpcResponseError` is struct-variant `{ code, message, data }`, not tuple. Full Rust pseudokod added with correct paths. | Q8 Native adapter parity (rewritten) | MED | R2 |
| 5 | SESSION_SUMMARY M-roadmap "Implementation gating" line updated to mention BOTH Pyth WASM compile gate AND Solana tx parsing WASM compile gate (was Pyth-only). | SESSION_SUMMARY.md (separate file) | LOW | R1 + R2 |
| 6 | Added historical note to v2.1→v2.2 changelog clarifying its claim "16 values" reflects v2.2 state, not current v2.5 state (which is 17). | v2.1→v2.2 changelog section | LOW | R1 |
| - | Added "Scope discipline" note at top of design log (NEW v2.5 paragraph): success criterion is floor not ceiling; ~30–40% of design log surface is over-cautious enumeration; Builder implements minimum to satisfy success criterion, expands if Phase 0 dataset reveals genuine need. | Header section | — | reflection |
| - | End marker `v2.4` → `v2.5`. | Last line | LOW | discipline |

### Self-flag (sixth incident in session)

v2.4 prose contained two source-verifiable errors that Codex R1+R2
caught:

1. **Schema version not bumped from `m6.v2.3` despite v2.3 explicit
   coupling rule.** I wrote the coupling rule, then in v2.4 I added
   reconciliation entries but missed running the schema bump
   alongside. Mechanical slip, not knowledge error.

2. **Native adapter error pattern matched against `ClientError::*`
   variants which don't exist.** The actual API is `ClientError` (a
   struct) with `.kind()` returning `ClientErrorKind` (the enum). I
   wrote the wrong API shape — same pattern as `VersionedTransaction`
   error in v2.3.

Total session: six confident-but-wrong incidents, all in pseudocode
referencing external crate APIs. Mitigation note (from v2.4 self-flag,
recommended for next-session operating instructions): **before writing
any Rust code or pseudocode referencing function signature, struct
shape, enum variant, or type belonging to an external crate or
platform, do `project_knowledge_search` or `web_search` for canonical
source within the same writing message**. The fact that this is now
the sixth incident in one session is evidence the mitigation note
needs to land in operating instructions, not just self-flag prose.

---

## Reconciliation changelog (v2.3 → v2.4)

8 patch items from two independent Codex reviews of v2.3 integrated:

| # | Item | v2.4 location | Severity | Source |
|---|------|---------------|----------|--------|
| 1 | Solana tx parsing types (`solana-program` / `solana-transaction` / `solana-message`) moved from native-only to main `[dependencies]` — `run_core()` needs `VersionedTransaction` deserialization on both WASM and native. `solana-client` stays native-only. | Q1 Cargo.toml dependencies (rewritten) | HIGH | R1 + R2 |
| 2 | `VersionedTransaction` API shape corrected — match on `tx.message: VersionedMessage` enum (`Legacy(_)` / `V0(_)`), not on `VersionedTransaction::Legacy` (which doesn't exist). | Q3d validation pseudocode | HIGH | R1 + R2 |
| 3 | `RpcError.code` type corrected `i64` → `i32` per WIT `record rpc-error { code: s32, ... }` in `zela-std/src/zela.wit`. The `procedure-anatomy.md` doc claim of `i64` is stale; actual source is `i32`. Explicit note added. | Q8 RPC error catch-and-convert rule prose | MED | R2 |
| 4 | Q5 Task 0 acceptance criterion #1 wording fixed: "Pattern A skeleton" → "Pattern C procedure crate per Q1 architecture" (leftover from v2.2). | Q5 Task 0 acceptance criteria | MED | R1 + R2 |
| 5 | Q5 Task 0 acceptance criteria extended — explicit Solana tx parsing crate wasm32-wasip2 verification with v0 + legacy `VersionedTransaction` round-trip smoke (criterion #4 NEW). | Q5 Task 0 acceptance criteria | MED | R1 (implied) |
| 6 | Q11 offline tests now include `test_tx_not_v0` — new abort path covered without RPC. | Q11 offline tests | MED | R1 |
| 7 | Q8 native adapter parity rule added — explicit mapping from `solana_client::ClientError` variants to same abort taxonomy values WASM path uses. Without this, native test dataset rows would have different abort distributions than WASM production for equivalent failures. | Q8 (new subsection) | MED | R1 |
| 8 | Q3h overgrouping corrected — split shape/type parse errors (`payload_invalid`) vs domain validation failures (their specific taxonomy values: `duplicate_pubkey`, `payload_too_large`, `max_tx_bytes_exceeded`, `tx_not_v0`, `tx_uses_address_lookup_table`). Added classification rule. | Q3h catches/doesn't-catch list | MED | R2 |
| - | SESSION_SUMMARY "Current milestone" line bumped v2.1 → v2.4 (separate file). | SESSION_SUMMARY.md | LOW | R1 + R2 |
| - | End marker `v2.3` → `v2.4`. | Last line | LOW | discipline |

### Self-flag (operating instructions precedent — fifth incident)

Two errors in v2.3 contributed to this round:

1. **`VersionedTransaction::Legacy` API shape** — confident-but-wrong
   Rust pseudocode. The actual solana-sdk API exposes `Legacy` as a
   variant of the `VersionedMessage` enum (field of
   `VersionedTransaction`), not as a variant of `VersionedTransaction`
   itself. This is the fifth incident in the pseudokode-without-verify
   pattern across this session.

2. **`RpcError.code: i64`** — propagated from `procedure-anatomy.md`
   doc which is stale. WIT source `zela-std/src/zela.wit` defines
   `record rpc-error { code: s32, ... }` which produces `i32` in
   Rust. The WIT source was in my context during multiple searches;
   not cross-checked against the doc claim. This is a "trusted stale
   reference without source verification" failure — different mode
   from the prior four, but same root cause: claims about platform
   APIs without source-level verification.

Net across session: five documented incidents. Pattern is consistent —
all involve writing about external APIs (zela-std, solana-sdk, Pyth
SDK, Solana RPC, repo state) where source verification was available
but skipped. Mitigation note for next session: **before writing any
Rust code, pseudocode, or claim referencing a specific API (function
signature, struct shape, enum variant, type) belonging to an
external crate or platform, do a `project_knowledge_search` or `view`
on the canonical source within the same writing message.** Add this
to operating instructions in next closing pass if user agrees.

---

## Reconciliation changelog (v2.2 → v2.3)

13 patch items from two independent Codex reviews of v2.2 integrated:

| # | Item | v2.3 location | Severity | Source |
|---|------|---------------|----------|--------|
| 1 | Pattern A vs Pattern C contradiction resolved — v2.2 incorrectly said "Pattern A suffices, instantiate solana_client inside procedure body". v2.3 mandates Pattern C with cfg-gated `read_accounts` / `simulate_transaction` adapter functions; `solana_client` lives ONLY in `cfg(not(target_arch = "wasm32"))` adapter and as `[target.'cfg(not(target_arch = "wasm32"))'.dependencies]` in Cargo.toml. | Q1 Architecture section (rewritten) | HIGH | R1 + R2 |
| 2 | Workspace registration — root `Cargo.toml` `workspace.members` must add `procedures/m6_sim_recheck` explicitly. | Q1 Implementation target | MED | R1 |
| 3 | Q3h JSON syntax clarification — `Params = JsonValue` catches payload shape errors, NOT malformed JSON syntax. Removed "syntax error" claim. Added explicit catches/doesn't-catch bullets. | Q3h (rewritten) | HIGH | R1 + R2 |
| 4 | Q3h `#[serde(deny_unknown_fields)]` decision — M6 v1 NOT enabled (lenient/forward-compat default). Documented explicitly. | Q3h | LOW | R2 |
| 5 | Q3d added `tx_not_v0` abort value — legacy VersionedTransaction variant explicitly classified, not left implicit. | Q3d (rewritten) + Q9 taxonomy | MED | R1 + R2 |
| 6 | Schema version bumped to `m6.v2.3` (was stuck at `m6.v2.1`). Schema and design doc versions are coupled going forward — bumping together for sanity. | Q9 procedure response schema + Q12 JSONL metadata row | MED | R1 + R2 |
| 7 | Q11 `test_abort_simulation_err` preflight assertion required — verify fixture lands in `value.err` not RPC error before depending on it for `simulation_err` taxonomy coverage. | Q11 test scenarios | MED | R1 |
| 8 | Q11 happy-path fixture signing convention — explicit decision: dummy signatures `[0u8; 64]` under `sigVerify: false`; fee_payer keypair NOT required, only wallet existence with SOL balance for sanitization. | Q11 Signing convention (new subsection) | MED | R2 |
| 9 | End marker `v2.1` → `v2.3` (was stale from v2.2 carryover). | Last line | LOW | R1 + R2 |
| 10 | SESSION_SUMMARY canonical reference bumped `v2.1` → `v2.3` (separate file update; see SESSION_SUMMARY changelog row). | SESSION_SUMMARY.md | MED | R2 |
| 11 | Total abort taxonomy: **17 values** (was 16 in v2.1/v2.2; added `tx_not_v0`). | Q9 taxonomy heading | LOW | derived from #5 |
| 12 | Reconciliation changelog v2.1→v2.2 retained for v1→v2.3 traceability; new v2.2→v2.3 section added (this section). | This section | LOW | discipline |
| 13 | Tracker queue + closing additions sections retained from v2.2 as historical record (closing pass completed May 26). | Tracker queue section | LOW | discipline |

### Self-flag (operating instructions precedent)

The Pattern A claim in v2.2 (Q1 Implementation target) is the **fourth
documented confident-but-wrong incident in this M6 grill session**, and
particularly painful because:

1. `procedure-anatomy.md` Pattern C section was already cited in v2.1
   Q11 ("Pattern C cfg-gated trait + inherent native run")
2. The reference file was directly visible in project knowledge
3. The cfg-gated import pattern (`#[cfg(target_arch = "wasm32")] use
   zela_std::rpc_client::RpcClient;` vs `#[cfg(not(...))] use
   solana_client::...`) is verbatim in that file
4. v2 verified fact V2 ("Outbound = Zela proxy only") makes solana_client
   in WASM architecturally impossible

The error pattern across all four incidents: when writing architectural
pseudocode involving platform APIs, I default to plausible synthesis
instead of verifying against the reference. The mitigation, going
forward: before writing ANY pseudocode using platform APIs, `view` the
reference SKILL or anatomy file explicitly within the same writing
session. Adding this as durable practice note.

---

## Reconciliation changelog (v2.1 → v2.2)

*(Historical changelog as recorded at v2.2; values reflect v2.2 state.
Subsequent versions added more taxonomy values — see later changelogs.)*

10 patch items from third Codex review (Builder-readiness gate)
integrated:

| # | Item | v2.2 location | Source |
|---|------|---------------|--------|
| 1 | `Params = JsonValue` rule (typed structs cause host-level JSON-RPC error, bypass abort taxonomy) | new Q3h | Codex v2.1 High |
| 2 | `call_rpc` signature corrected (sync, nested Result, IoError outer, RpcError struct not enum); RPC error pattern rewritten using `serde_json::Value` parse + manual shape parsing | Q8 RPC error catch-and-convert rule (rewritten) | Codex v2.1 High |
| 3 | Implementation target = new crate `procedures/m6_sim_recheck/` (preserve `procedures/oracle_read/`); Pattern A skeleton | new Q1 Implementation target | Codex v2.1 High |
| 4 | Captured Pyth fixture prerequisite for Task 0; `pyth-solana-receiver-sdk` is not drop-in for legacy push-oracle accounts; fallback chain hardened | Q5 SDK choice and ordering (rewritten) | Codex v2.1 High + planner add-on |
| 5 | Q4 stale `accounts` ref removed; clarified raw accounts are internal only, output exposes `accounts_read` + `oracle_summary` | Q4 (rewritten) | Codex v2.1 Med |
| 6 | Dataset path `m6_liquidator_sim` → `m6_sim_recheck` (consistent with generic-recheck framing per Q1) | Q12 dataset path | Codex v2.1 Med |
| 7 | `min_context_slot_not_reached` explicit classification rule (match on "minimum context slot" / "minContextSlot" substring in sim RPC error message) | Q8 RPC error catch-and-convert | Codex v2.1 Med |
| 8 | Pyth fallback wording: receiver-sdk may NOT decode legacy push-oracle accounts; Task 0 acceptance criteria mandate captured fixture decode test | Q5 (folded into #4) | Codex v2.1 Med |
| 9 | Taxonomy heading "15 values" → "16 values" (consistent with changelog) | Q9 abort_reason taxonomy heading | Codex v2.1 Low |
| 10 | `test_abort_oracle_not_trading` moved to offline fixture-based unit test (mainnet non-Trading feed is brittle) | Q11 test scenarios | Codex v2.1 Low |
| - | Tracker queue marked RESOLVED (closing pass completed split into 3 private files) | Tracker queue section (rewritten) | Codex v2.1 Low |

### Net schema impact

- Abort taxonomy: unchanged at 16 values (already in v2.1)
- Procedure response schema: unchanged
- Dataset row schema: unchanged
- Implementation patterns: explicit `Params = JsonValue` + manual
  parse; explicit `call_rpc` -> `serde_json::Value` -> manual shape
  parse; explicit min_context_slot_not_reached classifier
- New artifacts required: captured Pyth fixture binary in
  `tests/fixtures/`

## Reconciliation changelog (v2 → v2.1)

13 patch items integrated; sources cited per item:

| # | Item | v2.1 location | Source |
|---|------|---------------|--------|
| 1 | Q9 nullability for pre-read abort fields; new `validation_us` always-present field | Q9 procedure response schema | Review 1+2 (both High) |
| 2 | Schema layering split: procedure response vs dataset row | Q9 (two subsections) | Review 1 (High) |
| 3 | RPC error catch-and-convert implementation rule | Q8 new subsection | Review 1 (High) |
| 4 | Pyth `status` field check + `oracle_not_trading` abort + status in `oracle_summary` | Q5 quartet, Q9 abort taxonomy, Q9 oracle_summary shape | Review 2 (Must Fix #2) |
| 5 | u128 overflow-safe confidence math + `checked_abs` | Q5 Edge cases in math (with code sample) | Review 2 (Must Fix #3) |
| 6 | SPL TransferChecked fixture; remove inaccurate CPI claim | Q11 Happy-path tx fixture | Review 1+2 (both flagged) |
| 7 | Variant order rotation per tick + new orchestrator metadata fields | Q12 Variant order rotation; Q9 dataset row schema | Review 2 (Must Fix #5) |
| 8 | Solana upstream max 100 pubkeys reference | Q3g + V7 + Context references | Review 2 (Must Fix #6) |
| 9 | "Real candidate" → "real mainnet payload" wording | Q1 success criterion | Review 1 (Medium) |
| 10 | Phase 1 math: 5 more days × 20 = ~100 additional = ~140 total | Q12 Phased rollout | Review 1 (Medium) |
| 11 | Drop `sim_config_echo` from Q8; individual fields in Q9 sufficient | Q8 (text rewritten) | Review 1 (Medium) |
| 12 | `decode_us` narrowed to post-read; `validation_us` added | Q9 + Q12 Timing brackets | Review 1 (Low) |
| 13 | "Generic simulation recheck" framing in Q1 (not implicit liquidator) | Q1 Framing subsection | Review 2 (Remaining Risks) |

### Net-new abort taxonomy values added in v2.1

- `max_tx_bytes_exceeded` (was implicit in v2; now explicit)
- `oracle_not_trading` (Pyth status check, new gate)

Total taxonomy: 16 values (was 14 in v2, 8 in v1).

---

## Pending verification items (carried over from v2)

Unchanged from v2; still pending. Can be sent as standalone neutral
verification asks:

1. **Durable nonce txs + `replaceRecentBlockhash: true`** — explicitly
   out of scope for M6 v1; behavior unverified. Solana
   `simulateTransaction` docs do not address. Verify if needed before
   ALT support (M6.1).
2. **`minContextSlot` wait semantic** — bounded wait vs immediate
   error. Solana docs do not specify. Empirical observation in Phase
   0 may suffice.
3. **`pyth-solana-receiver-sdk` `wasm32-wasip2` compile status** — it
   is an Anchor crate (`anchor_lang` + transitive `solana-program`);
   Anchor on wasip2 is a real compile risk. Task 0 resolves
   empirically; manual Borsh decode is the pre-specified fallback (Q5).
4. **`chrono::Utc::now()` on `wasm32-wasip2`** — the time-based
   staleness gate depends on it returning real wall-clock time.
   `block_time` in zela-demo uses it (strong precedent); confirm on the
   actual procedure build in Task 0, and sanity-check executor clock vs
   on-chain time once.

---

*End of M6 Design Log v2.6.*
