use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use zela_std::{call_rpc, CustomProcedure, JsonValue, RpcError, zela_custom_procedure};

const FEEDS: &[(&str, &str)] = &[
    ("SOL/USD",  "H6ARHf6YXhGYeQfUzQNGk6rDNnLBQKrenN712K4AQJEG"),
    ("BTC/USD",  "GVXRSBjFk6e6J3NbVPXohDJetcTjaeeuykUpbQF8UoMU"),
    ("ETH/USD",  "JBu1AL4obBcCMqKBBxhpWCNUt136ijcuMZLFvTP7iWdB"),
    ("USDC/USD", "Gnt27xtC473ZT2Mw5u8wZ68Z3gULkSTb5DuxJy7eJotD"),
    ("USDT/USD", "3vxLXJqLqF3JG5TCbYycbKWRBbCJQLxQmBGCkyqEEefL"),
    ("BNB/USD",  "4CkQJBxhU8EZ2UjhigbtdaPbpTe6mqf811fipYBFbSYN"),
    ("JUP/USD",  "g6eRCbboSwK4tSWngn773RCMexr1APQr4uA9bGZBYfo"),
    ("BONK/USD", "8ihFLu5FimgTQ1Unh4dVyEHUGodJ5gJQCrQf4KUVB9bN"),
    ("PYTH/USD", "nrYkQQQur7z8rYTST3G9GqATviK5SxTDkrqd21MW6Ue"),
    ("JTO/USD",  "D8UUgr8a3aR3yUeHLu7v8FWK7E8Y5sSU7qrYBXUJXBQ5"),
];

pub struct OracleRead;

#[derive(Serialize)]
pub struct FeedResult {
    symbol: String,
    pubkey: String,
    account_found: bool,
    account_data_len: usize,
    context_slot: u64,
    wall_clock_elapsed_us: i64,
}

#[derive(Serialize)]
pub struct Aggregate {
    feed_count: usize,
    wall_clock_start_ms: i64,
    wall_clock_end_ms: i64,
    wall_clock_total_us: i64,
}

#[derive(Serialize)]
pub struct Output {
    genesis_hash: String,
    feeds: Vec<FeedResult>,
    aggregate: Aggregate,
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

fn b64_decoded_len(s: &str) -> usize {
    let pad = s.chars().rev().take_while(|c| *c == '=').count();
    s.len() * 3 / 4 - pad
}

impl CustomProcedure for OracleRead {
    type Params = ();
    type SuccessData = Output;
    type ErrorData = JsonValue;

    async fn run(_: ()) -> Result<Output, RpcError<JsonValue>> {
        let batch_start = Utc::now();

        let mut feeds = Vec::with_capacity(FEEDS.len());
        for (symbol, pubkey) in FEEDS {
            let t0 = Utc::now();
            let acc: AccInfo = call_rpc("getAccountInfo", json!([pubkey, {"encoding": "base64"}]))
                .map_err(io_err)
                .flatten()?;
            let elapsed_us = (Utc::now() - t0).num_microseconds().unwrap_or(0);

            let data_len = acc.value.as_ref()
                .and_then(|v| v.data.as_array())
                .and_then(|a| a.first())
                .and_then(|s| s.as_str())
                .map(b64_decoded_len)
                .unwrap_or(0);

            feeds.push(FeedResult {
                symbol: symbol.to_string(),
                pubkey: pubkey.to_string(),
                account_found: acc.value.is_some(),
                account_data_len: data_len,
                context_slot: acc.context.slot,
                wall_clock_elapsed_us: elapsed_us,
            });
        }

        let batch_end = Utc::now();

        let genesis_hash: String = call_rpc("getGenesisHash", json!([]))
            .map_err(io_err)
            .flatten()?;

        Ok(Output {
            genesis_hash,
            aggregate: Aggregate {
                feed_count: feeds.len(),
                wall_clock_start_ms: batch_start.timestamp_millis(),
                wall_clock_end_ms: batch_end.timestamp_millis(),
                wall_clock_total_us: (batch_end - batch_start).num_microseconds().unwrap_or(0),
            },
            feeds,
        })
    }
}

zela_custom_procedure!(OracleRead);
