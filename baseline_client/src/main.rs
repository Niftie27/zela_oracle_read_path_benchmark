use chrono::Utc;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::error::Error;

const FEEDS: &[(&str, &str)] = &[
    ("SOL/USD", "H6ARHf6YXhGYeQfUzQNGk6rDNnLBQKrenN712K4AQJEG"),
    ("BTC/USD", "GVXRSBjFk6e6J3NbVPXohDJetcTjaeeuykUpbQF8UoMU"),
    ("ETH/USD", "JBu1AL4obBcCMqKBBxhpWCNUt136ijcuMZLFvTP7iWdB"),
    ("USDC/USD", "Gnt27xtC473ZT2Mw5u8wZ68Z3gULkSTb5DuxJy7eJotD"),
    ("USDT/USD", "3vxLXJqLqF3JG5TCbYycbKWRBbCJQLxQmBGCkyqEEefL"),
    ("BNB/USD", "4CkQJBxhU8EZ2UjhigbtdaPbpTe6mqf811fipYBFbSYN"),
    ("JUP/USD", "g6eRCbboSwK4tSWngn773RCMexr1APQr4uA9bGZBYfo"),
    ("BONK/USD", "8ihFLu5FimgTQ1Unh4dVyEHUGodJ5gJQCrQf4KUVB9bN"),
    ("PYTH/USD", "nrYkQQQur7z8rYTST3G9GqATviK5SxTDkrqd21MW6Ue"),
    ("JTO/USD", "D8UUgr8a3aR3yUeHLu7v8FWK7E8Y5sSU7qrYBXUJXBQ5"),
];

#[derive(Serialize)]
struct FeedResult {
    symbol: String,
    pubkey: String,
    account_found: bool,
    account_data_len: usize,
    context_slot: u64,
}

#[derive(Serialize)]
struct Aggregate {
    feed_count: usize,
    wall_clock_start_ms: i64,
    wall_clock_end_ms: i64,
    wall_clock_total_us: i64,
}

#[derive(Serialize)]
struct Output {
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
struct MultipleAccounts {
    context: Ctx,
    value: Vec<Option<AccValue>>,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum JsonRpcResponse<T> {
    Success { result: T },
    Error { error: Value },
}

async fn rpc<T: for<'de> Deserialize<'de>>(
    http: &Client,
    url: &str,
    method: &str,
    params: Value,
) -> Result<T, Box<dyn Error>> {
    let body = json!({ "jsonrpc": "2.0", "id": 1, "method": method, "params": params });
    let resp: JsonRpcResponse<T> = http.post(url).json(&body).send().await?.json().await?;
    match resp {
        JsonRpcResponse::Success { result } => Ok(result),
        JsonRpcResponse::Error { error } => Err(format!("rpc error from {method}: {error}").into()),
    }
}

fn b64_decoded_len(s: &str) -> usize {
    let pad = s.chars().rev().take_while(|c| *c == '=').count();
    s.len() * 3 / 4 - pad
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let url = match std::env::var("BASELINE_RPC_URL") {
        Ok(u) => u,
        Err(_) => {
            eprintln!("error: BASELINE_RPC_URL environment variable is not set");
            std::process::exit(1);
        }
    };

    // Single Client reused across the batch + genesis call (default reqwest pooling).
    // Mirrors orchestrator's requests.Session() reuse on the Zela side.
    let http = Client::new();

    let pubkeys: Vec<&str> = FEEDS.iter().map(|(_, pk)| *pk).collect();

    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getMultipleAccounts",
        "params": [pubkeys, {"encoding": "base64", "commitment": "confirmed"}],
    });
    let batch_start = Utc::now();
    let raw_batch = http.post(&url).json(&body).send().await?.bytes().await?;
    let batch_end = Utc::now();
    let batch = match serde_json::from_slice::<JsonRpcResponse<MultipleAccounts>>(&raw_batch)? {
        JsonRpcResponse::Success { result } => result,
        JsonRpcResponse::Error { error } => {
            return Err(format!("rpc error from getMultipleAccounts: {error}").into());
        }
    };

    if batch.value.len() != FEEDS.len() {
        return Err(format!(
            "getMultipleAccounts returned {} entries, expected {}",
            batch.value.len(),
            FEEDS.len(),
        )
        .into());
    }

    let context_slot = batch.context.slot;
    let feeds: Vec<FeedResult> = FEEDS
        .iter()
        .zip(batch.value.into_iter())
        .map(|((symbol, pubkey), maybe_acc)| {
            let (account_found, account_data_len) = match maybe_acc {
                None => (false, 0usize),
                Some(acc) => {
                    let len = acc
                        .data
                        .as_array()
                        .and_then(|a| a.first())
                        .and_then(|s| s.as_str())
                        .map(b64_decoded_len)
                        .unwrap_or(0);
                    (true, len)
                }
            };
            FeedResult {
                symbol: symbol.to_string(),
                pubkey: pubkey.to_string(),
                account_found,
                account_data_len,
                context_slot,
            }
        })
        .collect();

    let genesis_hash: String = rpc(&http, &url, "getGenesisHash", json!([])).await?;

    let output = Output {
        genesis_hash,
        aggregate: Aggregate {
            feed_count: feeds.len(),
            wall_clock_start_ms: batch_start.timestamp_millis(),
            wall_clock_end_ms: batch_end.timestamp_millis(),
            wall_clock_total_us: (batch_end - batch_start).num_microseconds().unwrap_or(0),
        },
        feeds,
    };

    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}
