#!/usr/bin/env bash
# Capture LIVE Pyth pull-oracle SOL/USD price feed account (PriceUpdateV2 layout).
# Self-verifying: rejects the sunset legacy account and confirms SOL/USD feed_id is present.
set -euo pipefail

PUBKEY="${1:-PASTE_FULL_LIVE_ADDRESS}"
EXPECTED_FEED_ID="ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d"  # SOL/USD, verified from SDK source
DEAD_LEGACY_OWNER="FsJ3A3u2vn5cTVofAjvy6y5kwABJAqYWpe4975bi2epH"                    # sunset push oracle program
COMMITMENT="confirmed"
RPC_URL="${BASELINE_RPC_URL:?Set BASELINE_RPC_URL before running}"
FIXTURE_DIR="$(cd "$(dirname "$0")" && pwd)"

[ "$PUBKEY" = "PASTE_FULL_LIVE_ADDRESS" ] && { echo "ERROR: pass full live address as arg 1." >&2; exit 1; }

RESPONSE=$(curl -fsS -X POST "$RPC_URL" -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"getAccountInfo\",\"params\":[\"$PUBKEY\",{\"encoding\":\"base64\",\"commitment\":\"$COMMITMENT\"}]}")

echo "$RESPONSE" | jq -e '.error' >/dev/null && { echo "RPC error:" >&2; echo "$RESPONSE" | jq '.error' >&2; exit 1; }
[ "$(echo "$RESPONSE" | jq -r '.result.value')" = "null" ] && { echo "Account not found: $PUBKEY" >&2; exit 1; }

SLOT=$(echo "$RESPONSE" | jq -r '.result.context.slot')
DATA_B64=$(echo "$RESPONSE" | jq -r '.result.value.data[0]')
OWNER=$(echo "$RESPONSE" | jq -r '.result.value.owner')

TMP_BIN=$(mktemp)
echo "$DATA_B64" | base64 -d > "$TMP_BIN"
BIN_SIZE=$(stat -c%s "$TMP_BIN" 2>/dev/null || stat -f%z "$TMP_BIN")

# Self-verification
[ "$OWNER" = "$DEAD_LEGACY_OWNER" ] && { echo "FAIL: owner = sunset legacy program. Wrong (dead) account." >&2; rm -f "$TMP_BIN"; exit 1; }
HEX=$(xxd -p "$TMP_BIN" | tr -d '\n')
echo "$HEX" | grep -qi "$EXPECTED_FEED_ID" || { echo "FAIL: SOL/USD feed_id not in data. Wrong feed/account." >&2; rm -f "$TMP_BIN"; exit 1; }

BIN_PATH="$FIXTURE_DIR/pyth_sol_usd_pull_${SLOT}.bin"
JSON_PATH="$FIXTURE_DIR/pyth_sol_usd_pull_${SLOT}.json"
mv "$TMP_BIN" "$BIN_PATH"
SOURCE_HOST=$(echo "$RPC_URL" | sed -E 's|https?://([^/?]+).*|\1|')

cat > "$JSON_PATH" <<EOF
{
  "pubkey": "$PUBKEY",
  "feed_id": "$EXPECTED_FEED_ID",
  "layout": "PriceUpdateV2 (pull oracle)",
  "slot": $SLOT,
  "commitment": "$COMMITMENT",
  "captured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source_endpoint_host": "$SOURCE_HOST",
  "owner": "$OWNER",
  "data_decoded_bytes": $BIN_SIZE
}
EOF

echo "PASS — live SOL/USD pull feed captured:"
echo "  pubkey:  $PUBKEY"
echo "  owner:   $OWNER"
echo "  size:    $BIN_SIZE bytes"
echo "  slot:    $SLOT"
echo "  feed_id: present ✓"
echo "  bin:     $BIN_PATH"
echo "  first 96 bytes (hex):"
od -An -tx1 -N 96 "$BIN_PATH" | sed 's/^/    /'
