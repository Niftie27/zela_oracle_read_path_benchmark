#!/usr/bin/env bash
# Capture Pyth SOL/USD price account snapshot for M6 Task 0 fixture.
# Re-run anytime to refresh fixture (will produce new file based on slot).
# Output: pyth_sol_usd_<slot>.bin (raw account data) + .json (sidecar metadata).
set -euo pipefail

PUBKEY="H6ARHf6YXhGYeQfUzQNGk6rDNnLBQKrenN712K4AQJEG"   # Pyth SOL/USD (legacy push-oracle)
COMMITMENT="confirmed"
RPC_URL="${BASELINE_RPC_URL:?Set BASELINE_RPC_URL (Helius mainnet) before running}"

FIXTURE_DIR="$(cd "$(dirname "$0")" && pwd)"

RESPONSE=$(curl -fsS -X POST "$RPC_URL" \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"getAccountInfo\",\"params\":[\"$PUBKEY\",{\"encoding\":\"base64\",\"commitment\":\"$COMMITMENT\"}]}")

if echo "$RESPONSE" | jq -e '.error' >/dev/null; then
    echo "RPC error:" >&2
    echo "$RESPONSE" | jq '.error' >&2
    exit 1
fi
if [ "$(echo "$RESPONSE" | jq -r '.result.value')" = "null" ]; then
    echo "Account not found: $PUBKEY" >&2
    exit 1
fi

SLOT=$(echo "$RESPONSE" | jq -r '.result.context.slot')
DATA_B64=$(echo "$RESPONSE" | jq -r '.result.value.data[0]')
OWNER=$(echo "$RESPONSE" | jq -r '.result.value.owner')
LAMPORTS=$(echo "$RESPONSE" | jq -r '.result.value.lamports')
EXECUTABLE=$(echo "$RESPONSE" | jq -r '.result.value.executable')
RENT_EPOCH=$(echo "$RESPONSE" | jq -r '.result.value.rentEpoch')

SOURCE_HOST=$(echo "$RPC_URL" | sed -E 's|https?://([^/?]+).*|\1|')

BIN_PATH="$FIXTURE_DIR/pyth_sol_usd_${SLOT}.bin"
JSON_PATH="$FIXTURE_DIR/pyth_sol_usd_${SLOT}.json"

echo "$DATA_B64" | base64 -d > "$BIN_PATH"
BIN_SIZE=$(stat -c%s "$BIN_PATH" 2>/dev/null || stat -f%z "$BIN_PATH")

cat > "$JSON_PATH" <<EOF
{
  "pubkey": "$PUBKEY",
  "slot": $SLOT,
  "commitment": "$COMMITMENT",
  "captured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source_endpoint_host": "$SOURCE_HOST",
  "account_meta": {
    "owner": "$OWNER",
    "lamports": $LAMPORTS,
    "executable": $EXECUTABLE,
    "rent_epoch": $RENT_EPOCH
  },
  "data_decoded_bytes": $BIN_SIZE
}
EOF

echo "Captured Pyth SOL/USD fixture:"
echo "  bin:   $BIN_PATH ($BIN_SIZE bytes)"
echo "  json:  $JSON_PATH"
echo "  slot:  $SLOT"
echo "  owner: $OWNER"
echo "  first 64 bytes (hex):"
od -An -tx1 -N 64 "$BIN_PATH" | sed 's/^/    /'
