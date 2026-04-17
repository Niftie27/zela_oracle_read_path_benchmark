use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use zela_std::{call_rpc, CustomProcedure, JsonValue, RpcError, zela_custom_procedure};

const ORACLE_PUBKEY: &str = "H6ARHf6YXhGYeQfUzQNGk6rDNnLBQKrenN712K4AQJEG";

pub struct OracleRead;

#[derive(Serialize)]
pub struct Output {
    genesis_hash: String,
    account_pubkey: String,
    account_found: bool,
    account_data_len: usize,
    context_slot: u64,
    wall_clock_start_ms: i64,
    wall_clock_end_ms: i64,
    wall_clock_elapsed_us: i64,
}

#[derive(Deserialize)]
struct Ctx {
    slot: u64,
}

#[derive(Deserialize)]
struct AccValue {
    data: Value,
}

#[derive(Deserialize)]
struct AccInfo {
    context: Ctx,
    value: Option<AccValue>,
}

fn io_err(e: impl std::fmt::Display) -> RpcError<JsonValue> {
    RpcError { code: -32000, message: e.to_string(), data: None }
}

impl CustomProcedure for OracleRead {
    type Params = ();
    type SuccessData = Output;
    type ErrorData = JsonValue;

    async fn run(_: ()) -> Result<Output, RpcError<JsonValue>> {
        let start = Utc::now();
        // call_rpc is sync (WIT host call); getAccountInfo timing wraps only this call
        let acc: AccInfo = call_rpc("getAccountInfo", json!([ORACLE_PUBKEY, {"encoding": "base64"}]))
            .map_err(io_err)
            .flatten()?;
        let end = Utc::now();

        let genesis_hash: String = call_rpc("getGenesisHash", json!([]))
            .map_err(io_err)
            .flatten()?;

        // base64 decoded length from string length minus padding chars
        let data_len = acc.value.as_ref()
            .and_then(|v| v.data.as_array())
            .and_then(|a| a.first())
            .and_then(|s| s.as_str())
            .map(|s| s.len() * 3 / 4 - s.chars().rev().take_while(|c| *c == '=').count())
            .unwrap_or(0);

        Ok(Output {
            genesis_hash,
            account_pubkey: ORACLE_PUBKEY.to_string(),
            account_found: acc.value.is_some(),
            account_data_len: data_len,
            context_slot: acc.context.slot,
            wall_clock_start_ms: start.timestamp_millis(),
            wall_clock_end_ms: end.timestamp_millis(),
            wall_clock_elapsed_us: (end - start).num_microseconds().unwrap_or(0),
        })
    }
}

zela_custom_procedure!(OracleRead);
