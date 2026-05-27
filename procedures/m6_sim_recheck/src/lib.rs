use serde_json::Value;

pub struct M6SimRecheck;

impl M6SimRecheck {
    /// Shared run logic. Both WASM and native targets call this.
    /// Full implementation lands in Task 0 phase C + Phase 0.
    pub async fn run_core(_params: Value) -> Value {
        serde_json::json!({
            "decision": "abort",
            "abort_reason": "not_implemented"
        })
    }
}

// TODO (Phase 0 step 4): full signature per design log Q8 + Q9.
#[cfg(target_arch = "wasm32")]
#[allow(dead_code)]
async fn read_accounts() { /* TODO: zela_std::call_rpc("getMultipleAccounts", ...) */ }

#[cfg(not(target_arch = "wasm32"))]
#[allow(dead_code)]
async fn read_accounts() { /* TODO: solana_client native adapter */ }

#[cfg(target_arch = "wasm32")]
#[allow(dead_code)]
async fn simulate_transaction() { /* TODO: zela_std::call_rpc("simulateTransaction", ...) */ }

#[cfg(not(target_arch = "wasm32"))]
#[allow(dead_code)]
async fn simulate_transaction() { /* TODO: solana_client native adapter */ }

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
            // Abort encoded inside SuccessData JSON, not via RpcError exit —
            // see design log Q9.
            Ok(M6SimRecheck::run_core(params).await)
        }
    }
    zela_custom_procedure!(M6SimRecheck);
}
