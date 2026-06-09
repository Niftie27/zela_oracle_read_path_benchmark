use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64_STANDARD};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use solana_message::VersionedMessage;
use solana_transaction::versioned::VersionedTransaction;
use std::collections::HashSet;

const SCHEMA_VERSION: &str = "m6.v2.6";
const READ_COMMITMENT: &str = "processed";
const SIMULATE_COMMITMENT: &str = "processed";
const SIMULATE_SIG_VERIFY: bool = false;
const SIMULATE_REPLACE_RECENT_BLOCKHASH: bool = true;
const SUBMIT_READY: bool = false;
const DEFAULT_MAX_ACCOUNT_COUNT: usize = 32;
const DEFAULT_MAX_TX_BYTES: usize = 1232;
const DEFAULT_MAX_CLOCK_SKEW_SECONDS: i64 = 5;
const DEFAULT_MAX_PUBLISH_TIME_LAG_SECONDS: i64 = 60;
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

#[derive(Debug, Clone, Deserialize)]
struct Payload {
    #[serde(default)]
    oracle_pubkeys: Vec<String>,
    #[serde(default)]
    other_pubkeys: Vec<String>,
    tx_b64: String,
    #[serde(default = "default_max_account_count")]
    max_account_count: usize,
    #[serde(default = "default_max_tx_bytes")]
    max_tx_bytes: usize,
    #[serde(default = "default_max_clock_skew_seconds")]
    max_clock_skew_seconds: i64,
    #[serde(default = "default_max_publish_time_lag_seconds")]
    max_publish_time_lag_seconds: i64,
    max_confidence_ratio_bps: u64,
}

#[derive(Debug, Clone)]
struct ValidatedPayload {
    payload: Payload,
    #[cfg_attr(target_arch = "wasm32", allow(dead_code))]
    tx: VersionedTransaction,
}

#[derive(Debug, Default)]
struct Timings {
    validation_us: u64,
    read_us: Option<u64>,
    decode_us: Option<u64>,
    simulate_us: Option<u64>,
}

#[derive(Debug, Default)]
struct RuntimeFields {
    context_slot: Option<u64>,
    simulate_context_slot: Option<u64>,
    min_context_slot: Option<u64>,
    accounts_read: Option<u32>,
    units_consumed: Option<u64>,
    simulation_err: Option<String>,
    oracle_summary: Option<Value>,
    log_summary: Option<String>,
}

#[derive(Debug)]
struct Abort {
    reason: &'static str,
    detail: Option<String>,
    context_slot: Option<u64>,
    accounts_read: Option<u32>,
    simulate_context_slot: Option<u64>,
    units_consumed: Option<u64>,
    simulation_err: Option<String>,
}

#[derive(Debug)]
struct ReadSuccess {
    context_slot: u64,
    account_data: Vec<Vec<u8>>,
}

#[derive(Debug)]
struct SimulateSuccess {
    context_slot: u64,
    units_consumed: Option<u64>,
    simulation_err: Option<String>,
    log_summary: Option<String>,
}

impl Abort {
    fn new(reason: &'static str, detail: impl Into<String>) -> Self {
        Self {
            reason,
            detail: Some(detail.into()),
            context_slot: None,
            accounts_read: None,
            simulate_context_slot: None,
            units_consumed: None,
            simulation_err: None,
        }
    }

    fn without_detail(reason: &'static str) -> Self {
        Self {
            reason,
            detail: None,
            context_slot: None,
            accounts_read: None,
            simulate_context_slot: None,
            units_consumed: None,
            simulation_err: None,
        }
    }

    fn with_context_slot(mut self, context_slot: u64) -> Self {
        self.context_slot = Some(context_slot);
        self
    }

    fn with_accounts_read(mut self, accounts_read: u32) -> Self {
        self.accounts_read = Some(accounts_read);
        self
    }

    fn with_simulation_err(mut self, simulation_err: String) -> Self {
        self.simulation_err = Some(simulation_err);
        self
    }
}

impl Payload {
    fn all_pubkeys(&self) -> Vec<String> {
        self.oracle_pubkeys
            .iter()
            .chain(self.other_pubkeys.iter())
            .cloned()
            .collect()
    }

    fn oracle_gate_params(&self) -> OracleGateParams {
        OracleGateParams {
            max_publish_time_lag_seconds: self.max_publish_time_lag_seconds,
            max_clock_skew_seconds: self.max_clock_skew_seconds,
            max_confidence_ratio_bps: self.max_confidence_ratio_bps,
        }
    }
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
        run_core_live(params).await
    }
}

async fn run_core_live(params: Value) -> Value {
    let total_start = Utc::now();
    let executor_now_unix = total_start.timestamp();
    let mut timings = Timings::default();
    let mut runtime = RuntimeFields::default();

    let validation_start = Utc::now();
    let payload: Payload = match serde_json::from_value(params.clone()) {
        Ok(payload) => payload,
        Err(err) => {
            timings.validation_us = elapsed_us(validation_start);
            let abort = Abort::new("payload_invalid", format!("payload shape: {err}"));
            return q9_response(
                None,
                &timings,
                &runtime,
                Some(&abort),
                total_start,
                executor_now_unix,
            );
        }
    };

    let validated = match validate_payload(payload) {
        Ok(validated) => {
            timings.validation_us = elapsed_us(validation_start);
            validated
        }
        Err((payload, abort)) => {
            timings.validation_us = elapsed_us(validation_start);
            return q9_response(
                Some(&payload),
                &timings,
                &runtime,
                Some(&abort),
                total_start,
                executor_now_unix,
            );
        }
    };

    let read = match read_accounts(&validated.payload, &mut timings).await {
        Ok(read) => read,
        Err(abort) => {
            return q9_response(
                Some(&validated.payload),
                &timings,
                &runtime,
                Some(&abort),
                total_start,
                executor_now_unix,
            );
        }
    };

    runtime.context_slot = Some(read.context_slot);
    runtime.accounts_read = Some(read.account_data.len() as u32);

    let decode_start = Utc::now();
    let oracle_summary =
        match decode_and_gate_oracles(&validated.payload, &read.account_data, executor_now_unix) {
            Ok(summary) => {
                timings.decode_us = Some(elapsed_us(decode_start));
                summary
            }
            Err(abort) => {
                timings.decode_us = Some(elapsed_us(decode_start));
                let abort = abort
                    .with_context_slot(read.context_slot)
                    .with_accounts_read(read.account_data.len() as u32);
                return q9_response(
                    Some(&validated.payload),
                    &timings,
                    &runtime,
                    Some(&abort),
                    total_start,
                    executor_now_unix,
                );
            }
        };
    runtime.oracle_summary = Some(oracle_summary);
    runtime.min_context_slot = Some(read.context_slot);

    let simulate = match simulate_transaction(&validated, read.context_slot, &mut timings).await {
        Ok(simulate) => simulate,
        Err(abort) => {
            return q9_response(
                Some(&validated.payload),
                &timings,
                &runtime,
                Some(&abort),
                total_start,
                executor_now_unix,
            );
        }
    };

    runtime.simulate_context_slot = Some(simulate.context_slot);
    runtime.units_consumed = simulate.units_consumed;
    runtime.log_summary = simulate.log_summary;

    if simulate.context_slot < read.context_slot {
        let abort = Abort {
            reason: "simulate_response_decode_error",
            detail: Some(format!(
                "simulate_context_slot {} is below read context_slot {}",
                simulate.context_slot, read.context_slot
            )),
            context_slot: Some(read.context_slot),
            accounts_read: Some(read.account_data.len() as u32),
            simulate_context_slot: Some(simulate.context_slot),
            units_consumed: simulate.units_consumed,
            simulation_err: None,
        };
        return q9_response(
            Some(&validated.payload),
            &timings,
            &runtime,
            Some(&abort),
            total_start,
            executor_now_unix,
        );
    }

    if let Some(simulation_err) = simulate.simulation_err {
        runtime.simulation_err = Some(simulation_err.clone());
        let abort = Abort::new("simulation_err", simulation_err.clone())
            .with_context_slot(read.context_slot)
            .with_accounts_read(read.account_data.len() as u32)
            .with_simulation_err(simulation_err);
        return q9_response(
            Some(&validated.payload),
            &timings,
            &runtime,
            Some(&abort),
            total_start,
            executor_now_unix,
        );
    }

    q9_response(
        Some(&validated.payload),
        &timings,
        &runtime,
        None,
        total_start,
        executor_now_unix,
    )
}

fn validate_payload(payload: Payload) -> Result<ValidatedPayload, (Payload, Abort)> {
    let account_count = payload.oracle_pubkeys.len() + payload.other_pubkeys.len();
    if account_count > payload.max_account_count {
        return Err((
            payload,
            Abort::new(
                "payload_too_large",
                format!("account_count {account_count} exceeds max_account_count"),
            ),
        ));
    }

    let mut seen = HashSet::with_capacity(account_count);
    for pubkey in payload
        .oracle_pubkeys
        .iter()
        .chain(payload.other_pubkeys.iter())
    {
        if !seen.insert(pubkey.as_str()) {
            let duplicate = pubkey.clone();
            return Err((
                payload,
                Abort::new("duplicate_pubkey", format!("duplicate pubkey: {duplicate}")),
            ));
        }
    }

    let tx_bytes = match BASE64_STANDARD.decode(&payload.tx_b64) {
        Ok(tx_bytes) => tx_bytes,
        Err(err) => {
            return Err((
                payload,
                Abort::new("payload_invalid", format!("invalid tx_b64: {err}")),
            ));
        }
    };

    if tx_bytes.len() > payload.max_tx_bytes {
        return Err((
            payload,
            Abort::new(
                "max_tx_bytes_exceeded",
                format!("tx bytes {} exceeds max_tx_bytes", tx_bytes.len()),
            ),
        ));
    }

    let tx: VersionedTransaction = match bincode::deserialize(&tx_bytes) {
        Ok(tx) => tx,
        Err(err) => {
            return Err((
                payload,
                Abort::new(
                    "payload_invalid",
                    format!("tx_b64 is not a transaction: {err}"),
                ),
            ));
        }
    };

    match &tx.message {
        VersionedMessage::Legacy(_) => {
            return Err((payload, Abort::without_detail("tx_not_v0")));
        }
        VersionedMessage::V0(message) if !message.address_table_lookups.is_empty() => {
            return Err((
                payload,
                Abort::without_detail("tx_uses_address_lookup_table"),
            ));
        }
        VersionedMessage::V0(_) => {}
    }

    Ok(ValidatedPayload { payload, tx })
}

fn decode_and_gate_oracles(
    payload: &Payload,
    account_data: &[Vec<u8>],
    executor_now_unix: i64,
) -> Result<Value, Abort> {
    let mut summaries = Map::new();
    let gate_params = payload.oracle_gate_params();

    for (index, pubkey) in payload.oracle_pubkeys.iter().enumerate() {
        let data = account_data.get(index).ok_or_else(|| {
            Abort::new(
                "read_decode_error",
                format!("missing account bytes for oracle index {index}"),
            )
        })?;

        let summary = decode_price_update_v2(data)
            .map_err(|err| Abort::new("oracle_decode_failed", format!("oracle {pubkey}: {err}")))?;

        apply_oracle_gates(&summary, gate_params, executor_now_unix).map_err(|abort| {
            let detail = abort
                .detail
                .map(|detail| format!("oracle {pubkey}: {detail}"))
                .unwrap_or_else(|| format!("oracle {pubkey}"));
            Abort::new(abort.reason, detail)
        })?;

        summaries.insert(pubkey.clone(), oracle_summary_json(&summary));
    }

    Ok(Value::Object(summaries))
}

fn q9_response(
    payload: Option<&Payload>,
    timings: &Timings,
    runtime: &RuntimeFields,
    abort: Option<&Abort>,
    total_start: DateTime<Utc>,
    executor_now_unix: i64,
) -> Value {
    let context_slot = runtime
        .context_slot
        .or_else(|| abort.and_then(|abort| abort.context_slot));
    let accounts_read = runtime
        .accounts_read
        .or_else(|| abort.and_then(|abort| abort.accounts_read));
    let simulate_context_slot = runtime
        .simulate_context_slot
        .or_else(|| abort.and_then(|abort| abort.simulate_context_slot));
    let units_consumed = runtime
        .units_consumed
        .or_else(|| abort.and_then(|abort| abort.units_consumed));
    let simulation_err = runtime
        .simulation_err
        .clone()
        .or_else(|| abort.and_then(|abort| abort.simulation_err.clone()));

    json!({
        "schema_version": SCHEMA_VERSION,
        "decision": if abort.is_some() { "abort" } else { "execute" },
        "abort_reason": abort.map(|abort| abort.reason),
        "abort_detail": abort.and_then(|abort| abort.detail.clone()),
        "validation_us": timings.validation_us,
        "total_us": elapsed_us(total_start),
        "read_commitment": READ_COMMITMENT,
        "simulate_commitment": SIMULATE_COMMITMENT,
        "sig_verify": SIMULATE_SIG_VERIFY,
        "replace_recent_blockhash": SIMULATE_REPLACE_RECENT_BLOCKHASH,
        "submit_ready": SUBMIT_READY,
        "thresholds": thresholds_json(payload),
        "context_slot": context_slot,
        "read_us": timings.read_us,
        "decode_us": timings.decode_us,
        "simulate_us": timings.simulate_us,
        "simulate_context_slot": simulate_context_slot,
        "min_context_slot": runtime.min_context_slot,
        "accounts_read": accounts_read,
        "units_consumed": units_consumed,
        "simulation_err": simulation_err,
        "oracle_summary": runtime.oracle_summary,
        "log_summary": runtime.log_summary,
        "executor_now_unix": executor_now_unix,
    })
}

fn thresholds_json(payload: Option<&Payload>) -> Value {
    match payload {
        Some(payload) => json!({
            "max_publish_time_lag_seconds": payload.max_publish_time_lag_seconds,
            "max_clock_skew_seconds": payload.max_clock_skew_seconds,
            "max_confidence_ratio_bps": payload.max_confidence_ratio_bps,
            "max_account_count": payload.max_account_count,
            "max_tx_bytes": payload.max_tx_bytes,
        }),
        None => json!({
            "max_publish_time_lag_seconds": DEFAULT_MAX_PUBLISH_TIME_LAG_SECONDS,
            "max_clock_skew_seconds": DEFAULT_MAX_CLOCK_SKEW_SECONDS,
            "max_confidence_ratio_bps": null,
            "max_account_count": DEFAULT_MAX_ACCOUNT_COUNT,
            "max_tx_bytes": DEFAULT_MAX_TX_BYTES,
        }),
    }
}

#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
fn parse_multiple_accounts_response(
    value: &Value,
    requested_pubkeys: &[String],
) -> Result<ReadSuccess, Abort> {
    let context_slot = value
        .get("context")
        .and_then(|context| context.get("slot"))
        .and_then(Value::as_u64)
        .ok_or_else(|| Abort::new("read_decode_error", "response shape: missing context.slot"))?;
    let accounts = value
        .get("value")
        .and_then(Value::as_array)
        .ok_or_else(|| Abort::new("read_decode_error", "response shape: missing value array"))?;

    if accounts.len() != requested_pubkeys.len() {
        return Err(Abort::new(
            "read_decode_error",
            format!(
                "response shape: value length {} expected {}",
                accounts.len(),
                requested_pubkeys.len()
            ),
        ));
    }

    let mut account_data = Vec::with_capacity(accounts.len());
    for (index, account) in accounts.iter().enumerate() {
        if account.is_null() {
            let found = accounts.iter().filter(|account| !account.is_null()).count() as u32;
            return Err(Abort::new(
                "account_not_found",
                format!("account_not_found: {}", requested_pubkeys[index]),
            )
            .with_context_slot(context_slot)
            .with_accounts_read(found));
        }

        let encoded = account
            .get("data")
            .and_then(base64_data_from_rpc_value)
            .ok_or_else(|| {
                Abort::new(
                    "read_decode_error",
                    format!("response shape: account {index} missing base64 data"),
                )
            })?;
        let decoded = BASE64_STANDARD.decode(encoded).map_err(|err| {
            Abort::new(
                "read_decode_error",
                format!("response shape: account {index} invalid base64: {err}"),
            )
        })?;
        account_data.push(decoded);
    }

    Ok(ReadSuccess {
        context_slot,
        account_data,
    })
}

#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
fn base64_data_from_rpc_value(data: &Value) -> Option<&str> {
    if let Some(encoded) = data.as_str() {
        return Some(encoded);
    }

    data.as_array()
        .and_then(|items| items.first())
        .and_then(Value::as_str)
}

#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
fn parse_simulate_response(value: &Value) -> Result<SimulateSuccess, Abort> {
    let context_slot = value
        .get("context")
        .and_then(|context| context.get("slot"))
        .and_then(Value::as_u64)
        .ok_or_else(|| {
            Abort::new(
                "simulate_response_decode_error",
                "response shape: missing context.slot",
            )
        })?;
    let sim_value = value.get("value").ok_or_else(|| {
        Abort::new(
            "simulate_response_decode_error",
            "response shape: missing value",
        )
    })?;

    let simulation_err = match sim_value.get("err") {
        Some(err) if !err.is_null() => Some(err.to_string()),
        Some(_) => None,
        None => {
            return Err(Abort::new(
                "simulate_response_decode_error",
                "response shape: missing value.err",
            ));
        }
    };

    let units_consumed = sim_value.get("unitsConsumed").and_then(Value::as_u64);
    let log_summary = sim_value
        .get("logs")
        .and_then(Value::as_array)
        .map(|logs| {
            logs.iter()
                .filter_map(Value::as_str)
                .take(3)
                .collect::<Vec<_>>()
                .join(" | ")
        })
        .filter(|summary| !summary.is_empty());

    Ok(SimulateSuccess {
        context_slot,
        units_consumed,
        simulation_err,
        log_summary,
    })
}

#[cfg(test)]
pub fn run_core_sync(params: Value) -> Value {
    let executor_now_unix = chrono::Utc::now().timestamp();
    run_decode_only_sync_at(params, executor_now_unix)
}

#[cfg(test)]
fn run_decode_only_sync_at(params: Value, executor_now_unix: i64) -> Value {
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
        "abort_reason": "payload_invalid",
        "abort_detail": "oracle_account_data_base64 is required for decode-only helper",
        "executor_now_unix": executor_now_unix,
    })
}

#[cfg(test)]
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

#[cfg(test)]
fn read_required_i64_param(params: &Value, field: &'static str) -> Result<i64, String> {
    let raw = read_required_u64_param(params, field)?;
    i64::try_from(raw).map_err(|_| format!("{field} is too large"))
}

#[cfg(test)]
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

#[cfg(test)]
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

fn default_max_account_count() -> usize {
    DEFAULT_MAX_ACCOUNT_COUNT
}

fn default_max_tx_bytes() -> usize {
    DEFAULT_MAX_TX_BYTES
}

fn default_max_clock_skew_seconds() -> i64 {
    DEFAULT_MAX_CLOCK_SKEW_SECONDS
}

fn default_max_publish_time_lag_seconds() -> i64 {
    DEFAULT_MAX_PUBLISH_TIME_LAG_SECONDS
}

fn elapsed_us(start: DateTime<Utc>) -> u64 {
    let elapsed = (Utc::now() - start).num_microseconds().unwrap_or(0);
    u64::try_from(elapsed).unwrap_or(0)
}

#[cfg(target_arch = "wasm32")]
async fn read_accounts(payload: &Payload, timings: &mut Timings) -> Result<ReadSuccess, Abort> {
    let pubkeys = payload.all_pubkeys();
    let params = json!([
        pubkeys,
        {
            "encoding": "base64",
            "commitment": READ_COMMITMENT,
        }
    ]);

    let start = Utc::now();
    let raw: Result<Result<Value, zela_std::RpcError<Value>>, std::io::Error> =
        zela_std::call_rpc("getMultipleAccounts", params);
    timings.read_us = Some(elapsed_us(start));

    match raw {
        Err(err) => Err(Abort::new("read_rpc_error", format!("transport: {err}"))),
        Ok(Err(err)) => Err(Abort::new(
            "read_rpc_error",
            format!("rpc code {}: {}", err.code, err.message),
        )),
        Ok(Ok(value)) => parse_multiple_accounts_response(&value, &payload.all_pubkeys()),
    }
}

#[cfg(not(target_arch = "wasm32"))]
async fn read_accounts(payload: &Payload, timings: &mut Timings) -> Result<ReadSuccess, Abort> {
    use solana_client::nonblocking::rpc_client::RpcClient;
    use solana_client::rpc_config::RpcAccountInfoConfig;
    use solana_sdk::{commitment_config::CommitmentConfig, pubkey::Pubkey};
    use std::str::FromStr;

    let requested_pubkeys = payload.all_pubkeys();
    let pubkeys = requested_pubkeys
        .iter()
        .map(|pubkey| {
            Pubkey::from_str(pubkey).map_err(|err| {
                Abort::new("read_rpc_error", format!("invalid pubkey {pubkey}: {err}"))
            })
        })
        .collect::<Result<Vec<_>, _>>()?;

    let client = RpcClient::new_with_commitment(native_rpc_url(), CommitmentConfig::processed());
    let config = RpcAccountInfoConfig {
        commitment: Some(CommitmentConfig::processed()),
        ..RpcAccountInfoConfig::default()
    };

    let start = Utc::now();
    let response = client
        .get_multiple_accounts_with_config(&pubkeys, config)
        .await;
    timings.read_us = Some(elapsed_us(start));

    let response = response.map_err(|err| native_client_abort(err, Phase::Read))?;
    let mut account_data = Vec::with_capacity(response.value.len());
    for (index, account) in response.value.into_iter().enumerate() {
        match account {
            Some(account) => account_data.push(account.data),
            None => {
                return Err(Abort::new(
                    "account_not_found",
                    format!("account_not_found: {}", requested_pubkeys[index]),
                )
                .with_context_slot(response.context.slot)
                .with_accounts_read(account_data.len() as u32));
            }
        }
    }

    Ok(ReadSuccess {
        context_slot: response.context.slot,
        account_data,
    })
}

#[cfg(target_arch = "wasm32")]
async fn simulate_transaction(
    validated: &ValidatedPayload,
    context_slot: u64,
    timings: &mut Timings,
) -> Result<SimulateSuccess, Abort> {
    let params = json!([
        validated.payload.tx_b64,
        {
            "sigVerify": SIMULATE_SIG_VERIFY,
            "replaceRecentBlockhash": SIMULATE_REPLACE_RECENT_BLOCKHASH,
            "commitment": SIMULATE_COMMITMENT,
            "minContextSlot": context_slot,
            "encoding": "base64",
        }
    ]);

    let start = Utc::now();
    let raw: Result<Result<Value, zela_std::RpcError<Value>>, std::io::Error> =
        zela_std::call_rpc("simulateTransaction", params);
    timings.simulate_us = Some(elapsed_us(start));

    match raw {
        Err(err) => Err(Abort::new(
            "simulate_rpc_error",
            format!("transport: {err}"),
        )),
        Ok(Err(err)) if is_min_context_slot_error(&err.message) => Err(Abort::new(
            "min_context_slot_not_reached",
            format!("rpc code {}: {}", err.code, err.message),
        )),
        Ok(Err(err)) => Err(Abort::new(
            "simulate_rpc_error",
            format!("rpc code {}: {}", err.code, err.message),
        )),
        Ok(Ok(value)) => parse_simulate_response(&value),
    }
}

#[cfg(not(target_arch = "wasm32"))]
async fn simulate_transaction(
    validated: &ValidatedPayload,
    context_slot: u64,
    timings: &mut Timings,
) -> Result<SimulateSuccess, Abort> {
    use solana_client::nonblocking::rpc_client::RpcClient;
    use solana_client::rpc_config::RpcSimulateTransactionConfig;
    use solana_sdk::commitment_config::CommitmentConfig;

    let client = RpcClient::new_with_commitment(native_rpc_url(), CommitmentConfig::processed());
    let config = RpcSimulateTransactionConfig {
        sig_verify: SIMULATE_SIG_VERIFY,
        replace_recent_blockhash: SIMULATE_REPLACE_RECENT_BLOCKHASH,
        commitment: Some(CommitmentConfig::processed()),
        min_context_slot: Some(context_slot),
        ..RpcSimulateTransactionConfig::default()
    };

    let start = Utc::now();
    let response = client
        .simulate_transaction_with_config(&validated.tx, config)
        .await;
    timings.simulate_us = Some(elapsed_us(start));

    let response = response.map_err(|err| native_client_abort(err, Phase::Simulate))?;
    let log_summary = response
        .value
        .logs
        .as_ref()
        .map(|logs| logs.iter().take(3).cloned().collect::<Vec<_>>().join(" | "))
        .filter(|summary| !summary.is_empty());

    Ok(SimulateSuccess {
        context_slot: response.context.slot,
        units_consumed: response.value.units_consumed,
        simulation_err: response.value.err.map(|err| format!("{err:?}")),
        log_summary,
    })
}

fn is_min_context_slot_error(message: &str) -> bool {
    let normalized = message.to_ascii_lowercase();
    normalized.contains("minimum context slot") || normalized.contains("mincontextslot")
}

#[cfg(not(target_arch = "wasm32"))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Phase {
    Read,
    Simulate,
}

#[cfg(not(target_arch = "wasm32"))]
fn native_rpc_url() -> String {
    std::env::var("M6_RPC_URL")
        .or_else(|_| std::env::var("SOLANA_RPC_URL"))
        .unwrap_or_else(|_| "https://api.mainnet-beta.solana.com".to_string())
}

#[cfg(not(target_arch = "wasm32"))]
fn native_client_abort(err: solana_client::client_error::ClientError, phase: Phase) -> Abort {
    use solana_client::client_error::ClientErrorKind;
    use solana_client::rpc_request::RpcError as SolanaRpcError;

    let (rpc_reason, decode_reason) = match phase {
        Phase::Read => ("read_rpc_error", "read_decode_error"),
        Phase::Simulate => ("simulate_rpc_error", "simulate_response_decode_error"),
    };

    match err.kind() {
        ClientErrorKind::Reqwest(_) | ClientErrorKind::Io(_) => {
            Abort::new(rpc_reason, format!("transport: {err}"))
        }
        ClientErrorKind::RpcError(SolanaRpcError::RpcResponseError { code, message, .. }) => {
            if phase == Phase::Simulate && is_min_context_slot_error(message) {
                Abort::new(
                    "min_context_slot_not_reached",
                    format!("rpc code {code}: {message}"),
                )
            } else {
                Abort::new(rpc_reason, format!("rpc code {code}: {message}"))
            }
        }
        ClientErrorKind::SerdeJson(_) => {
            Abort::new(decode_reason, format!("response shape: {err}"))
        }
        _ => Abort::new(rpc_reason, format!("unexpected: {err}")),
    }
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
    use solana_message::{
        MessageHeader, VersionedMessage,
        compiled_instruction::CompiledInstruction,
        legacy::Message as LegacyMessage,
        v0::{self, MessageAddressTableLookup},
    };
    use solana_sdk::{hash::Hash, pubkey::Pubkey, signature::Signature};
    use solana_transaction::versioned::VersionedTransaction;

    const SOL_USD_PULL_ORACLE: &str = "7UVimffxr9ow1uXYxsr4LHAcV58mLzhmwaeKvJ1pjLiE";
    const GENERIC_FEE_PAYER: &str = "99P8ZgtJYe1buSK8JXkvpLh8xPsCFuLYhz9hQFNw93WJ";
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
    fn decode_only_helper_executes_for_fresh_full_oracle() {
        let fixture = fixture_with_publish_time(TEST_NOW_UNIX - 10);

        let result = run_decode_only_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
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
    fn decode_only_helper_requires_oracle_gate_thresholds() {
        let fixture = fixture_with_publish_time(TEST_NOW_UNIX);
        let params = json!({
            "oracle_account_data_base64": BASE64_STANDARD.encode(&fixture),
        });

        let result = run_decode_only_sync_at(params, TEST_NOW_UNIX);
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

        let result = run_decode_only_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_verification_partial");
        assert_eq!(result["oracle_summary"]["verification_level"], "Partial(7)");
    }

    #[test]
    fn oracle_gates_abort_on_non_positive_price() {
        let mut fixture = fixture_with_publish_time(TEST_NOW_UNIX - 10);
        write_i64(&mut fixture, PRICE_OFFSET_FULL, 0);

        let result = run_decode_only_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_decode_failed");
        assert_eq!(result["abort_detail"], "non-positive price");
    }

    #[test]
    fn oracle_gates_abort_on_future_publish_time() {
        let fixture = fixture_with_publish_time(TEST_NOW_UNIX + 6);

        let result = run_decode_only_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_decode_failed");
        assert_eq!(result["abort_detail"], "publish_time in future");
    }

    #[test]
    fn oracle_gates_abort_on_stale_publish_time() {
        let fixture = fixture_with_publish_time(TEST_NOW_UNIX - 61);

        let result = run_decode_only_sync_at(gated_params(&fixture), TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_publish_time_too_stale");
    }

    #[test]
    fn oracle_gates_abort_on_wide_confidence_ratio() {
        let mut fixture = fixture_with_publish_time(TEST_NOW_UNIX - 10);
        write_i64(&mut fixture, PRICE_OFFSET_FULL, 100_000);
        write_u64(&mut fixture, CONF_OFFSET_FULL, 100_000);

        let params = gated_params_with_thresholds(&fixture, 60, 5, 9_999);
        let result = run_decode_only_sync_at(params, TEST_NOW_UNIX);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], "oracle_confidence_too_wide");
    }

    #[tokio::test]
    async fn run_core_returns_q9_payload_invalid_without_rpc() {
        let result = M6SimRecheck::run_core(json!({})).await;

        assert_q9_validation_abort(&result, "payload_invalid");
        assert_eq!(
            result["thresholds"]["max_account_count"],
            DEFAULT_MAX_ACCOUNT_COUNT
        );
        assert_eq!(result["thresholds"]["max_tx_bytes"], DEFAULT_MAX_TX_BYTES);
        assert_eq!(
            result["thresholds"]["max_clock_skew_seconds"],
            DEFAULT_MAX_CLOCK_SKEW_SECONDS
        );
        assert_eq!(
            result["thresholds"]["max_publish_time_lag_seconds"],
            DEFAULT_MAX_PUBLISH_TIME_LAG_SECONDS
        );
        assert!(result["thresholds"]["max_confidence_ratio_bps"].is_null());
    }

    #[tokio::test]
    async fn run_core_aborts_on_payload_too_large_without_rpc() {
        let result = M6SimRecheck::run_core(valid_payload_json_with_overrides(json!({
            "oracle_pubkeys": ["oracle_a", "oracle_b"],
            "max_account_count": 1,
        })))
        .await;

        assert_q9_validation_abort(&result, "payload_too_large");
    }

    #[tokio::test]
    async fn run_core_aborts_on_duplicate_pubkey_without_rpc() {
        let result = M6SimRecheck::run_core(valid_payload_json_with_overrides(json!({
            "oracle_pubkeys": ["dup"],
            "other_pubkeys": ["dup"],
        })))
        .await;

        assert_q9_validation_abort(&result, "duplicate_pubkey");
    }

    #[tokio::test]
    async fn run_core_aborts_on_max_tx_bytes_without_rpc() {
        let result = M6SimRecheck::run_core(valid_payload_json_with_overrides(json!({
            "max_tx_bytes": 1,
        })))
        .await;

        assert_q9_validation_abort(&result, "max_tx_bytes_exceeded");
    }

    #[tokio::test]
    async fn run_core_aborts_on_legacy_tx_without_rpc() {
        let tx = VersionedTransaction {
            signatures: Vec::new(),
            message: VersionedMessage::Legacy(LegacyMessage::default()),
        };
        let result = M6SimRecheck::run_core(valid_payload_json_with_overrides(json!({
            "tx_b64": BASE64_STANDARD.encode(bincode::serialize(&tx).unwrap()),
        })))
        .await;

        assert_q9_validation_abort(&result, "tx_not_v0");
    }

    #[tokio::test]
    async fn run_core_aborts_on_v0_address_lookup_table_without_rpc() {
        let tx_b64 = minimal_v0_tx_b64_with_lookups(vec![MessageAddressTableLookup {
            account_key: Pubkey::new_unique(),
            writable_indexes: vec![0],
            readonly_indexes: Vec::new(),
        }]);
        let result = M6SimRecheck::run_core(valid_payload_json_with_overrides(json!({
            "tx_b64": tx_b64,
        })))
        .await;

        assert_q9_validation_abort(&result, "tx_uses_address_lookup_table");
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

    #[tokio::test]
    #[ignore]
    async fn mainnet_live_read_simulate_exercises_execute_and_non_trivial_abort() {
        use std::str::FromStr;

        let fee_payer = Pubkey::from_str(GENERIC_FEE_PAYER).expect("fee payer pubkey");
        let tx_b64 = no_instruction_v0_tx_b64(fee_payer);

        let execute = M6SimRecheck::run_core(json!({
            "oracle_pubkeys": [SOL_USD_PULL_ORACLE],
            "other_pubkeys": [],
            "tx_b64": tx_b64,
            "max_account_count": DEFAULT_MAX_ACCOUNT_COUNT,
            "max_tx_bytes": DEFAULT_MAX_TX_BYTES,
            "max_clock_skew_seconds": DEFAULT_MAX_CLOCK_SKEW_SECONDS,
            "max_publish_time_lag_seconds": DEFAULT_MAX_PUBLISH_TIME_LAG_SECONDS,
            "max_confidence_ratio_bps": 1_000_000,
        }))
        .await;
        assert_eq!(execute["decision"], "execute", "{execute}");
        assert_eq!(execute["abort_reason"], Value::Null);
        assert_eq!(execute["sig_verify"], SIMULATE_SIG_VERIFY);
        assert_eq!(
            execute["replace_recent_blockhash"],
            SIMULATE_REPLACE_RECENT_BLOCKHASH
        );
        assert_eq!(execute["simulate_commitment"], SIMULATE_COMMITMENT);
        assert_eq!(execute["min_context_slot"], execute["context_slot"]);
        assert_eq!(execute["accounts_read"], 1);
        assert!(execute["oracle_summary"][SOL_USD_PULL_ORACLE].is_object());

        let abort = M6SimRecheck::run_core(json!({
            "oracle_pubkeys": [SOL_USD_PULL_ORACLE],
            "other_pubkeys": [],
            "tx_b64": no_instruction_v0_tx_b64(fee_payer),
            "max_account_count": DEFAULT_MAX_ACCOUNT_COUNT,
            "max_tx_bytes": DEFAULT_MAX_TX_BYTES,
            "max_clock_skew_seconds": DEFAULT_MAX_CLOCK_SKEW_SECONDS,
            "max_publish_time_lag_seconds": DEFAULT_MAX_PUBLISH_TIME_LAG_SECONDS,
            "max_confidence_ratio_bps": 0,
        }))
        .await;
        assert_eq!(abort["decision"], "abort", "{abort}");
        assert_eq!(abort["abort_reason"], "oracle_confidence_too_wide");
    }

    fn assert_q9_validation_abort(result: &Value, reason: &str) {
        assert_eq!(result["schema_version"], SCHEMA_VERSION);
        assert_eq!(result["decision"], "abort");
        assert_eq!(result["abort_reason"], reason);
        assert!(result["validation_us"].is_u64());
        assert!(result["total_us"].is_u64());
        assert_eq!(result["read_commitment"], READ_COMMITMENT);
        assert_eq!(result["simulate_commitment"], SIMULATE_COMMITMENT);
        assert_eq!(result["sig_verify"], SIMULATE_SIG_VERIFY);
        assert_eq!(
            result["replace_recent_blockhash"],
            SIMULATE_REPLACE_RECENT_BLOCKHASH
        );
        assert_eq!(result["submit_ready"], SUBMIT_READY);
        assert!(result["context_slot"].is_null());
        assert!(result["read_us"].is_null());
        assert!(result["decode_us"].is_null());
        assert!(result["simulate_us"].is_null());
        assert!(result["simulate_context_slot"].is_null());
        assert!(result["min_context_slot"].is_null());
        assert!(result["accounts_read"].is_null());
        assert!(result["units_consumed"].is_null());
        assert!(result["simulation_err"].is_null());
        assert!(result["oracle_summary"].is_null());
        assert!(result["log_summary"].is_null());
    }

    fn valid_payload_json_with_overrides(overrides: Value) -> Value {
        let mut payload = json!({
            "oracle_pubkeys": ["oracle_a"],
            "other_pubkeys": [],
            "tx_b64": minimal_v0_tx_b64(),
            "max_account_count": DEFAULT_MAX_ACCOUNT_COUNT,
            "max_tx_bytes": DEFAULT_MAX_TX_BYTES,
            "max_clock_skew_seconds": DEFAULT_MAX_CLOCK_SKEW_SECONDS,
            "max_publish_time_lag_seconds": DEFAULT_MAX_PUBLISH_TIME_LAG_SECONDS,
            "max_confidence_ratio_bps": 1_000_000,
        });

        let payload_object = payload.as_object_mut().unwrap();
        for (key, value) in overrides.as_object().unwrap() {
            payload_object.insert(key.clone(), value.clone());
        }
        payload
    }

    fn minimal_v0_tx_b64() -> String {
        minimal_v0_tx_b64_with_lookups(Vec::new())
    }

    fn minimal_v0_tx_b64_with_lookups(
        address_table_lookups: Vec<MessageAddressTableLookup>,
    ) -> String {
        let tx = VersionedTransaction {
            signatures: Vec::new(),
            message: VersionedMessage::V0(v0::Message {
                address_table_lookups,
                ..v0::Message::default()
            }),
        };
        BASE64_STANDARD.encode(bincode::serialize(&tx).unwrap())
    }

    fn no_instruction_v0_tx_b64(fee_payer: Pubkey) -> String {
        let tx = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::V0(v0::Message {
                header: MessageHeader {
                    num_required_signatures: 1,
                    num_readonly_signed_accounts: 0,
                    num_readonly_unsigned_accounts: 0,
                },
                account_keys: vec![fee_payer],
                recent_blockhash: Hash::default(),
                instructions: Vec::<CompiledInstruction>::new(),
                address_table_lookups: Vec::new(),
            }),
        };
        BASE64_STANDARD.encode(bincode::serialize(&tx).unwrap())
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
