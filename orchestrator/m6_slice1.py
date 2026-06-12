"""M6 phase 0 smoke orchestrator: one payload, one invocation, one JSONL row."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.auth import AuthError, fetch_jwt, read_credentials
from orchestrator.config import M6SliceConfig, load_config

METADATA_SCHEMA_VERSION = "m6.dataset.v1"
METADATA_PHASE = "0_smoke"
METADATA_DISCLAIMER = (
    "M6 measures generic simulation recheck feasibility on Zela, not strategy "
    "profitability. The 'execute' decision indicates simulateTransaction "
    "succeeded under sigVerify=false; it is NOT a submission endorsement. "
    "Caller is responsible for signature validity, profitability assessment, "
    "state-drift verification, and tx-touched-account validation beyond the "
    "payload pubkey list."
)

ROUTE_HEADER_NAME = "zela-route-by"
ROUTED_TO_HEADER_NAME = "zela-routed-to"
PAYLOAD_PATH = Path(__file__).resolve().parent / "payloads" / "v1_happy.json"


class InvokeError(RuntimeError):
    """Raised when the executor invocation should stop the smoke run."""


@dataclass(frozen=True)
class InvocationCapture:
    http_status: int
    response_body: Any
    response_text: str
    response_headers: dict[str, str]
    client_pre_call_us: int
    client_post_call_us: int

    @property
    def client_total_us(self) -> int:
        return self.client_post_call_us - self.client_pre_call_us


def epoch_us() -> int:
    return time.time_ns() // 1_000


def iso8601_utc(now: datetime | None = None) -> str:
    value = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def load_payload(path: Path = PAYLOAD_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"payload must be a JSON object: {path}")
    return payload


def configure_executor_session(
    session: requests.Session,
    token: str,
    config: M6SliceConfig,
) -> None:
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            ROUTE_HEADER_NAME: config.route_header,
        }
    )


def invoke_payload(
    session: requests.Session,
    config: M6SliceConfig,
    payload: Mapping[str, Any],
) -> InvocationCapture:
    request_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": config.method,
        "params": dict(payload),
    }

    client_pre_call_us = epoch_us()
    response = session.post(
        config.executor_url,
        json=request_body,
        timeout=config.invoke_timeout_seconds,
    )
    client_post_call_us = epoch_us()

    response_text = response.text
    try:
        response_body = response.json()
    except ValueError as exc:
        raise InvokeError(
            "invoke returned a non-JSON response: "
            f"http_status={response.status_code} body={response_text}"
        ) from exc

    return InvocationCapture(
        http_status=response.status_code,
        response_body=response_body,
        response_text=response_text,
        response_headers=dict(response.headers),
        client_pre_call_us=client_pre_call_us,
        client_post_call_us=client_post_call_us,
    )


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    needle = name.lower()
    for key, value in headers.items():
        if key.lower() == needle:
            return value
    return None


def metadata_row(generated_at: str | None = None) -> dict[str, Any]:
    return {
        "record_type": "metadata",
        "dataset_schema_version": METADATA_SCHEMA_VERSION,
        "generated_at": generated_at or iso8601_utc(),
        "phase": METADATA_PHASE,
        "disclaimer": METADATA_DISCLAIMER,
    }


def build_dataset_row(
    config: M6SliceConfig,
    capture: InvocationCapture,
    cron_tick_id: str,
    invocation_index: int = 0,
) -> dict[str, Any]:
    return {
        "procedure_response": capture.response_body,
        "executor_location": header_value(capture.response_headers, ROUTED_TO_HEADER_NAME),
        "route_header": config.route_header,
        "procedure_revision": config.procedure_revision,
        "payload_variant": config.payload_variant,
        "cron_tick_id": cron_tick_id,
        "invocation_index": invocation_index,
        "variant_order": [config.payload_variant],
        "is_first_in_tick": invocation_index == 0,
        "client_pre_call_us": capture.client_pre_call_us,
        "client_post_call_us": capture.client_post_call_us,
        "client_total_us": capture.client_total_us,
        "leader_slot": None,
        "leader_pubkey": None,
        "leader_resolved_via": None,
    }


def dataset_path(config: M6SliceConfig, run_date: date | None = None) -> Path:
    value = date.today() if run_date is None else run_date
    return config.output_dir / f"{value.isoformat()}.jsonl"


def append_dataset_row(
    config: M6SliceConfig,
    row: Mapping[str, Any],
    run_date: date | None = None,
    generated_at: str | None = None,
) -> Path:
    path = dataset_path(config, run_date=run_date)
    path.parent.mkdir(parents=True, exist_ok=True)

    should_write_metadata = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8") as handle:
        if should_write_metadata:
            handle.write(json.dumps(metadata_row(generated_at), separators=(",", ":")))
            handle.write("\n")
        handle.write(json.dumps(dict(row), separators=(",", ":")))
        handle.write("\n")
    return path


def jsonrpc_error(capture: InvocationCapture) -> Any | None:
    if isinstance(capture.response_body, dict):
        return capture.response_body.get("error")
    return None


def observed_decision(capture: InvocationCapture) -> Any | None:
    if not isinstance(capture.response_body, dict):
        return None
    result = capture.response_body.get("result")
    if not isinstance(result, dict):
        return None
    return result.get("decision")


def validate_capture(capture: InvocationCapture, row: Mapping[str, Any]) -> None:
    if capture.http_status != 200:
        raise InvokeError(
            "invoke failed: "
            f"http_status={capture.http_status} body={capture.response_text}"
        )

    error = jsonrpc_error(capture)
    if error is not None:
        raise InvokeError(
            "invoke returned JSON-RPC error: "
            f"http_status={capture.http_status} body={capture.response_text}"
        )

    if not row.get("executor_location"):
        raise InvokeError(
            "invoke response did not include zela-routed-to header: "
            f"http_status={capture.http_status} headers={capture.response_headers}"
        )

    decision = observed_decision(capture)
    if decision != "execute":
        raise InvokeError(
            "observed decision was not execute: "
            f"decision={decision!r} http_status={capture.http_status} "
            f"body={capture.response_text}"
        )


def run_once(
    session: requests.Session,
    config: M6SliceConfig,
    payload: Mapping[str, Any],
) -> tuple[Path, dict[str, Any], Any | None]:
    capture = invoke_payload(session, config, payload)
    row = build_dataset_row(config, capture, cron_tick_id=str(uuid4()))
    path = append_dataset_row(config, row)
    validate_capture(capture, row)
    return path, row, observed_decision(capture)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--payload",
        type=Path,
        default=PAYLOAD_PATH,
        help="Path to the v1_happy params object JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = load_config()
    payload = load_payload(args.payload)

    session = requests.Session()
    try:
        key_id, key_secret = read_credentials()
        token = fetch_jwt(
            session,
            config.auth_url,
            key_id,
            key_secret,
            timeout_seconds=config.auth_timeout_seconds,
        )
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"jwt_prefix={token[:16]}")
    configure_executor_session(session, token, config)

    try:
        path, _row, decision = run_once(session, config, payload)
    except InvokeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"observed_decision={decision}")
    print(f"wrote={path}")
    print(
        "note: client_total_us is a cold, TLS-dominated smoke number; "
        "it is not a clean latency datapoint."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
