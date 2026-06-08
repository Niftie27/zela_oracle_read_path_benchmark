use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64_STANDARD};
use serde::Serialize;
use serde_json::{Value, json};

const SCHEMA_VERSION: &str = "m6.v2.6";
pub const PRICE_UPDATE_V2_DISCRIMINATOR: [u8; 8] = [0x22, 0xf1, 0x23, 0x63, 0x9d, 0x7e, 0xf4, 0xcd];

pub struct M6SimRecheck;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub enum VerificationLevel {
    Full,
    Partial(u8),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OracleSummary {
    pub price: i64,
    pub expo: i32,
    pub conf: u64,
    pub publish_time: i64,
    pub verification_level: VerificationLevel,
    pub posted_slot: u64,
    pub feed_id: [u8; 32],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodeError {
    TooShort { actual_len: usize },
    DiscriminatorMismatch { actual: [u8; 8] },
    InvalidVerificationLevel { tag: u8 },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct OracleGateParams {
    max_publish_time_lag_seconds: i64,
    max_clock_skew_seconds: i64,
    max_confidence_ratio_bps: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct OracleGateAbort {
    reason: &'static str,
    detail: Option<&'static str>,
}

impl core::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::TooShort { actual_len } => {
                write!(f, "PriceUpdateV2 account too short: {actual_len} bytes")
            }
            Self::DiscriminatorMismatch { actual } => {
                write!(
                    f,
                    "PriceUpdateV2 discriminator mismatch: {}",
                    bytes_to_hex(actual)
                )
            }
            Self::InvalidVerificationLevel { tag } => {
                write!(f, "invalid verification_level tag: 0x{tag:02x}")
            }
        }
    }
}

impl std::error::Error for DecodeError {}

impl M6SimRecheck {
    /// Shared run logic. Both WASM and native targets call this.
    pub async fn run_core(params: Value) -> Value {
        run_core_sync(params)
    }
}

pub fn run_core_sync(params: Value) -> Value {
    let executor_now_unix = chrono::Utc::now().timestamp();
    run_core_sync_at(params, executor_now_unix)
}

fn run_core_sync_at(params: Value, executor_now_unix: i64) -> Value {
    if let Some(encoded) = params
        .get("oracle_account_data_base64")
        .and_then(Value::as_str)
    {
        let gate_params = match parse_oracle_gate_params(&params) {
            Ok(gate_params) => gate_params,
            Err(err) => {
                return json!({
                    "schema_version": SCHEMA_VERSION,
                    "decision": "abort",
                    "abort_reason": "payload_invalid",
                    "abort_detail": err,
                    "executor_now_unix": executor_now_unix,
                });
            }
        };

        return match BASE64_STANDARD.decode(encoded) {
            Ok(account_data) => match decode_price_update_v2(&account_data) {
                Ok(summary) => match apply_oracle_gates(&summary, gate_params, executor_now_unix) {
                    Ok(()) => json!({
                        "schema_version": SCHEMA_VERSION,
                        "decision": "execute",
                        "executor_now_unix": executor_now_unix,
                        "oracle_summary": oracle_summary_json(&summary),
                    }),
                    Err(abort) => oracle_gate_abort_json(&summary, abort, executor_now_unix),
                },
                Err(err) => json!({
                    "schema_version": SCHEMA_VERSION,
                    "decision": "abort",
                    "abort_reason": "oracle_decode_failed",
                    "abort_detail": err.to_string(),
                    "executor_now_unix": executor_now_unix,
                }),
            },
            Err(err) => json!({
                "schema_version": SCHEMA_VERSION,
                "decision": "abort",
                "abort_reason": "payload_invalid",
                "abort_detail": format!("invalid oracle_account_data_base64: {err}"),
                "executor_now_unix": executor_now_unix,
            }),
        };
    }

    json!({
        "schema_version": SCHEMA_VERSION,
        "decision": "abort",
        "abort_reason": "not_implemented",
        "executor_now_unix": executor_now_unix,
    })
}

fn parse_oracle_gate_params(params: &Value) -> Result<OracleGateParams, String> {
    Ok(OracleGateParams {
        max_publish_time_lag_seconds: read_required_i64_param(
            params,
            "max_publish_time_lag_seconds",
        )?,
        max_clock_skew_seconds: read_required_i64_param(params, "max_clock_skew_seconds")?,
        max_confidence_ratio_bps: read_required_u64_param(params, "max_confidence_ratio_bps")?,
    })
}

fn read_required_i64_param(params: &Value, field: &'static str) -> Result<i64, String> {
    let raw = read_required_u64_param(params, field)?;
    i64::try_from(raw).map_err(|_| format!("{field} is too large"))
}

fn read_required_u64_param(params: &Value, field: &'static str) -> Result<u64, String> {
    params
        .get(field)
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("{field} must be a non-negative integer"))
}

pub fn decode_price_update_v2(data: &[u8]) -> Result<OracleSummary, DecodeError> {
    assert_price_update_v2_discriminator(data)?;
    let (verification_level, base_offset) = decode_verification_level(data)?;
    let posted_slot_offset = base_offset + 84;

    ensure_len(data, posted_slot_offset + 8)?;

    Ok(OracleSummary {
        price: read_i64(data, base_offset + 32)?,
        expo: read_i32(data, base_offset + 48)?,
        conf: read_u64(data, base_offset + 40)?,
        publish_time: read_i64(data, base_offset + 52)?,
        verification_level,
        posted_slot: read_u64(data, posted_slot_offset)?,
        feed_id: read_32(data, base_offset)?,
    })
}

pub fn assert_price_update_v2_discriminator(data: &[u8]) -> Result<(), DecodeError> {
    let Some(discriminator) = data.get(..PRICE_UPDATE_V2_DISCRIMINATOR.len()) else {
        return Err(DecodeError::TooShort {
            actual_len: data.len(),
        });
    };

    if discriminator != PRICE_UPDATE_V2_DISCRIMINATOR {
        let mut actual = [0u8; 8];
        actual.copy_from_slice(discriminator);
        return Err(DecodeError::DiscriminatorMismatch { actual });
    }

    Ok(())
}

fn decode_verification_level(data: &[u8]) -> Result<(VerificationLevel, usize), DecodeError> {
    ensure_len(data, 41)?;
    match data[40] {
        0x01 => Ok((VerificationLevel::Full, 41)),
        0x00 => {
            ensure_len(data, 42)?;
            Ok((VerificationLevel::Partial(data[41]), 42))
        }
        tag => Err(DecodeError::InvalidVerificationLevel { tag }),
    }
}

fn ensure_len(data: &[u8], min_len: usize) -> Result<(), DecodeError> {
    if data.len() < min_len {
        return Err(DecodeError::TooShort {
            actual_len: data.len(),
        });
    }

    Ok(())
}

fn read_32(data: &[u8], offset: usize) -> Result<[u8; 32], DecodeError> {
    ensure_len(data, offset + 32)?;
    let mut out = [0u8; 32];
    out.copy_from_slice(&data[offset..offset + 32]);
    Ok(out)
}

fn read_i64(data: &[u8], offset: usize) -> Result<i64, DecodeError> {
    Ok(i64::from_le_bytes(read_8(data, offset)?))
}

fn read_i32(data: &[u8], offset: usize) -> Result<i32, DecodeError> {
    ensure_len(data, offset + 4)?;
    let mut bytes = [0u8; 4];
    bytes.copy_from_slice(&data[offset..offset + 4]);
    Ok(i32::from_le_bytes(bytes))
}

fn read_u64(data: &[u8], offset: usize) -> Result<u64, DecodeError> {
    Ok(u64::from_le_bytes(read_8(data, offset)?))
}

fn read_8(data: &[u8], offset: usize) -> Result<[u8; 8], DecodeError> {
    ensure_len(data, offset + 8)?;
    let mut bytes = [0u8; 8];
    bytes.copy_from_slice(&data[offset..offset + 8]);
    Ok(bytes)
}

fn oracle_summary_json(summary: &OracleSummary) -> Value {
    json!({
        "price": summary.price,
        "expo": summary.expo,
        "conf": summary.conf,
        "publish_time": summary.publish_time,
        "verification_level": match summary.verification_level {
            VerificationLevel::Full => "Full".to_string(),
            VerificationLevel::Partial(num_signatures) => {
                format!("Partial({num_signatures})")
            }
        },
        "posted_slot": summary.posted_slot,
        "feed_id": bytes_to_hex(&summary.feed_id),
    })
}

fn apply_oracle_gates(
    summary: &OracleSummary,
    params: OracleGateParams,
    executor_now_unix: i64,
) -> Result<(), OracleGateAbort> {
    if summary.verification_level != VerificationLevel::Full {
        return Err(OracleGateAbort {
            reason: "oracle_verification_partial",
            detail: None,
        });
    }

    let Some(price_abs) = summary.price.checked_abs() else {
        return Err(OracleGateAbort {
            reason: "oracle_decode_failed",
            detail: Some("non-positive price"),
        });
    };
    if summary.price <= 0 || price_abs == 0 {
        return Err(OracleGateAbort {
            reason: "oracle_decode_failed",
            detail: Some("non-positive price"),
        });
    }

    if summary.publish_time > executor_now_unix.saturating_add(params.max_clock_skew_seconds) {
        return Err(OracleGateAbort {
            reason: "oracle_decode_failed",
            detail: Some("publish_time in future"),
        });
    }

    if executor_now_unix.saturating_sub(summary.publish_time) > params.max_publish_time_lag_seconds
    {
        return Err(OracleGateAbort {
            reason: "oracle_publish_time_too_stale",
            detail: None,
        });
    }

    let ratio_bps = (summary.conf as u128).saturating_mul(10_000) / (price_abs as u128);
    if ratio_bps > params.max_confidence_ratio_bps as u128 {
        return Err(OracleGateAbort {
            reason: "oracle_confidence_too_wide",
            detail: None,
        });
    }

    Ok(())
}

fn bytes_to_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

fn oracle_gate_abort_json(
    summary: &OracleSummary,
    abort: OracleGateAbort,
    executor_now_unix: i64,
) -> Value {
    let mut response = json!({
        "schema_version": SCHEMA_VERSION,
        "decision": "abort",
        "abort_reason": abort.reason,
        "executor_now_unix": executor_now_unix,
        "oracle_summary": oracle_summary_json(summary),
    });

    if let Some(detail) = abort.detail {
        response["abort_detail"] = json!(detail);
    }

    response
}

// TODO (Phase 0 step 4): full signature per design log Q8 + Q9.
#[cfg(target_arch = "wasm32")]
#[allow(dead_code)]
async fn read_accounts() {
    let _result: Result<Result<Value, zela_std::RpcError<Value>>, std::io::Error> =
        zela_std::call_rpc("getMultipleAccounts", json!([]));
}

#[cfg(not(target_arch = "wasm32"))]
#[allow(dead_code)]
async fn read_accounts() {
    use solana_client::nonblocking::rpc_client::RpcClient;
    let _client_type_size = core::mem::size_of::<RpcClient>();
}

#[cfg(target_arch = "wasm32")]
#[allow(dead_code)]
async fn simulate_transaction() {
    let _result: Result<Result<Value, zela_std::RpcError<Value>>, std::io::Error> =
        zela_std::call_rpc("simulateTransaction", json!([]));
}

#[cfg(not(target_arch = "wasm32"))]
#[allow(dead_code)]
async fn simulate_transaction() {
    use solana_client::nonblocking::rpc_client::RpcClient;
    let _client_type_size = core::mem::size_of::<RpcClient>();
}

#[cfg(target_arch = "wasm32")]
mod wasm_entry {
    use super::*;
    use zela_std::{CustomProcedure, JsonValue, RpcError, zela_custom_procedure};

    impl CustomProcedure for M6SimRecheck {
        type Params = JsonValue;
        type SuccessData = JsonValue;
        type ErrorData = ();
        const LOG_MAX_LEVEL: log::LevelFilter = log::LevelFilter::Debug;

        async fn run(params: JsonValue) -> Result<JsonValue, RpcError<()>> {
            // Abort encoded inside SuccessData JSON, not via RpcError exit.
            Ok(M6SimRecheck::run_core(params).await)
        }
    }
    zela_custom_procedure!(M6SimRecheck);
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_message::{VersionedMessage, legacy::Message as LegacyMessage, v0};
    use solana_transaction::versioned::VersionedTransaction;

    const FIXTURE: &[u8] = include_bytes!("../tests/fixtures/pyth_sol_usd_pull_422767475.bin");
    const EXPECTED_FEED_ID: [u8; 32] = [
        0xef, 0x0d, 0x8b, 0x6f, 0xda, 0x2c, 0xeb, 0xa4, 0x1d, 0xa1, 0x5d, 0x40, 0x95, 0xd1, 0xda,
        0x39, 0x2a, 0x0d, 0x2f, 0x8e, 0xd0, 0xc6, 0xc7, 0xbc, 0x0f, 0x4c, 0xfa, 0xc8, 0xc2, 0x80,
        0xb5, 0x6d,
    ];
    const FIXTURE_PRICE_LOWER_BOUND_USD: f64 = 50.0;
    const FIXTURE_PRICE_UPPER_BOUND_USD: f64 = 10_000.0;
    const TEST_NOW_UNIX: i64 = 1_780_000_100;
    const PRICE_OFFSET_FULL: usize = 73;
    const CONF_OFFSET_FULL: usize = 81;
    const PUBLISH_TIME_OFFSET_FULL: usize = 93;

    #[test]
    fn fixture_discriminator_matches_price_update_v2() {
        assert_price_update_v2_discriminator(FIXTURE).unwrap();
        assert_eq!(&FIXTURE[..8], PRICE_UPDATE_V2_DISCRIMINATOR);
    }

    #[test]
    fn decodes_pull_oracle_fixture_with_manual_offsets() {
        let summary = decode_price_update_v2(FIXTURE).unwrap();

        assert_eq!(summary.feed_id, EXPECTED_FEED_ID);
        assert_eq!(summary.expo, -8);
        assert_eq!(summary.verification_level, VerificationLevel::Full);

        let normalized_price = (summary.price as f64) * 10f64.powi(summary.expo);
        assert!(
            normalized_price > FIXTURE_PRICE_LOWER_BOUND_USD
                && normalized_price < FIXTURE_PRICE_UPPER_BOUND_USD,
            "normalized price {normalized_price} outside loose sanity band"
        );

        assert!(
            summary.publish_time > 1_700_000_000 && summary.publish_time < 1_850_000_000,
            "publish_time {} is outside the C1 plausibility window",
            summary.publish_time
        );
        assert!(
            summary.posted_slot.abs_diff(422_767_475) <= 16,
            "posted_slot {} is not near the captured slot",
            summary.posted_slot
        );
    }

    #[test]
    fn manual_decode_handles_partial_offset_shift() {
        let mut partial = [0u8; 134];
        partial[..40].copy_from_slice(&FIXTURE[..40]);
        partial[40] = 0x00;
        partial[41] = 7;
        partial[42..134].copy_from_slice(&FIXTURE[41..133]);

        let summary = decode_price_update_v2(&partial).unwrap();

        assert_eq!(summary.feed_id, EXPECTED_FEED_ID);
        assert_eq!(summary.expo, -8);
        assert_eq!(summary.verification_level, VerificationLevel::Partial(7));
        assert_eq!(
            summary.posted_slot,
            decode_price_update_v2(FIXTURE).unwrap().posted_slot
        );
    }

    #[test]
    fn run_core_executes_for_fresh_full_oracle() {
        let fixture = fixture_with_publish_time(TEST_NOW_UNIX - 10);

        let result = run_core_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
        assert_eq!(result["schema_version"], SCHEMA_VERSION);
        assert_eq!(result["decision"], "execute");
        assert!(result.get("abort_reason").is_none());
        assert_eq!(result["oracle_summary"]["expo"], -8);
        assert_eq!(
            result["oracle_summary"]["feed_id"],
            bytes_to_hex(&EXPECTED_FEED_ID)
        );
        assert_eq!(result["executor_now_unix"], TEST_NOW_UNIX);
    }

    #[test]
    fn run_core_requires_oracle_gate_thresholds() {
        let fixture = fixture_with_publish_time(TEST_NOW_UNIX);
        let params = json!({
            "oracle_account_data_base64": BASE64_STANDARD.encode(&fixture),
        });

        let result = run_core_sync_at(params, TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "payload_invalid");
        assert!(
            result["abort_detail"]
                .as_str()
                .unwrap()
                .contains("max_publish_time_lag_seconds")
        );
    }

    #[test]
    fn oracle_gates_abort_on_partial_verification() {
        let fixture = partial_fixture_with_publish_time(TEST_NOW_UNIX - 10);

        let result = run_core_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_verification_partial");
        assert_eq!(result["oracle_summary"]["verification_level"], "Partial(7)");
    }

    #[test]
    fn oracle_gates_abort_on_non_positive_price() {
        let mut fixture = fixture_with_publish_time(TEST_NOW_UNIX - 10);
        write_i64(&mut fixture, PRICE_OFFSET_FULL, 0);

        let result = run_core_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_decode_failed");
        assert_eq!(result["abort_detail"], "non-positive price");
    }

    #[test]
    fn oracle_gates_abort_on_future_publish_time() {
        let fixture = fixture_with_publish_time(TEST_NOW_UNIX + 6);

        let result = run_core_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_decode_failed");
        assert_eq!(result["abort_detail"], "publish_time in future");
    }

    #[test]
    fn oracle_gates_abort_on_stale_publish_time() {
        let fixture = fixture_with_publish_time(TEST_NOW_UNIX - 61);

        let result = run_core_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_publish_time_too_stale");
    }

    #[test]
    fn oracle_gates_abort_on_wide_confidence_ratio() {
        let mut fixture = fixture_with_publish_time(TEST_NOW_UNIX - 10);
        write_i64(&mut fixture, PRICE_OFFSET_FULL, 100_000);
        write_u64(&mut fixture, CONF_OFFSET_FULL, 100_000);

        let params = gated_params_with_thresholds(&fixture, 60, 5, 9_999);
        let result = run_core_sync_at(params, TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_confidence_too_wide");
    }

    #[test]
    fn versioned_transaction_bincode_round_trips_v0_and_legacy() {
        let v0_tx = VersionedTransaction {
            signatures: Vec::new(),
            message: VersionedMessage::V0(v0::Message::default()),
        };
        let v0_bytes = bincode::serialize(&v0_tx).unwrap();
        let decoded_v0: VersionedTransaction = bincode::deserialize(&v0_bytes).unwrap();
        assert!(matches!(decoded_v0.message, VersionedMessage::V0(_)));

        let legacy_tx = VersionedTransaction {
            signatures: Vec::new(),
            message: VersionedMessage::Legacy(LegacyMessage::default()),
        };
        let legacy_bytes = bincode::serialize(&legacy_tx).unwrap();
        let decoded_legacy: VersionedTransaction = bincode::deserialize(&legacy_bytes).unwrap();
        assert!(matches!(
            decoded_legacy.message,
            VersionedMessage::Legacy(_)
        ));
    }

    fn gated_params(data: &[u8]) -> Value {
        gated_params_with_thresholds(data, 60, 5, 10_000)
    }

    fn gated_params_with_thresholds(
        data: &[u8],
        max_publish_time_lag_seconds: i64,
        max_clock_skew_seconds: i64,
        max_confidence_ratio_bps: u64,
    ) -> Value {
        json!({
            "oracle_account_data_base64": BASE64_STANDARD.encode(data),
            "max_publish_time_lag_seconds": max_publish_time_lag_seconds,
            "max_clock_skew_seconds": max_clock_skew_seconds,
            "max_confidence_ratio_bps": max_confidence_ratio_bps,
        })
    }

    fn fixture_with_publish_time(publish_time: i64) -> Vec<u8> {
        let mut fixture = FIXTURE.to_vec();
        write_i64(&mut fixture, PUBLISH_TIME_OFFSET_FULL, publish_time);
        fixture
    }

    fn partial_fixture_with_publish_time(publish_time: i64) -> Vec<u8> {
        let fresh_full = fixture_with_publish_time(publish_time);
        let mut partial = vec![0u8; 134];
        partial[..40].copy_from_slice(&fresh_full[..40]);
        partial[40] = 0x00;
        partial[41] = 7;
        partial[42..134].copy_from_slice(&fresh_full[41..133]);
        partial
    }

    fn write_i64(data: &mut [u8], offset: usize, value: i64) {
        data[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
    }

    fn write_u64(data: &mut [u8], offset: usize, value: u64) {
        data[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
    }
}
