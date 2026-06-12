from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from orchestrator.auth import AuthError, fetch_jwt
from orchestrator.config import M6SliceConfig
from orchestrator.m6_slice1 import (
    InvocationCapture,
    append_dataset_row,
    build_dataset_row,
    configure_executor_session,
    invoke_payload,
    load_payload,
)


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, text=None):
        self.status_code = status_code
        self._body = {} if body is None else body
        self.headers = {} if headers is None else headers
        self.text = json.dumps(self._body) if text is None else text

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs, "headers": dict(self.headers)})
        if not self.responses:
            raise AssertionError("unexpected post call")
        return self.responses.pop(0)


def test_config(tmpdir: Path) -> M6SliceConfig:
    return M6SliceConfig(
        auth_url="https://auth.example.test/token",
        executor_url="https://executor.example.test",
        method="zela.oracle_read#testrevision",
        procedure_revision="testrevision",
        route_header="static fr2",
        output_dir=tmpdir,
    )


class M6SliceTests(unittest.TestCase):
    def test_fetch_jwt_rejects_empty_access_token(self):
        session = FakeSession([FakeResponse(body={"access_token": ""})])

        with self.assertRaisesRegex(AuthError, "null/empty access_token"):
            fetch_jwt(
                session,
                "https://auth.example.test/token",
                "key-id",
                "key-secret",
                timeout_seconds=30,
            )

    def test_invoke_payload_uses_direct_params_object_and_session_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = test_config(Path(temp_dir))
            response_body = {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"decision": "execute"},
            }
            session = FakeSession(
                [
                    FakeResponse(
                        body=response_body,
                        headers={"zela-routed-to": "fr2"},
                    )
                ]
            )
            configure_executor_session(session, "jwt-token", config)
            payload = {"oracle_pubkeys": ["pk"], "other_pubkeys": []}

            capture = invoke_payload(session, config, payload)

            self.assertEqual(capture.response_body, response_body)
            self.assertEqual(capture.http_status, 200)
            self.assertEqual(session.calls[0]["url"], config.executor_url)
            request_json = session.calls[0]["kwargs"]["json"]
            self.assertEqual(request_json["method"], config.method)
            self.assertEqual(request_json["params"], payload)
            self.assertNotIn("params", request_json["params"])
            self.assertEqual(
                session.calls[0]["headers"]["zela-route-by"],
                config.route_header,
            )
            self.assertEqual(
                session.calls[0]["headers"]["Authorization"],
                "Bearer jwt-token",
            )

    def test_dataset_row_shape_and_metadata_append(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = test_config(Path(temp_dir))
            response_body = {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "decision": "execute",
                    "context_slot": 425948374,
                    "price": 6676757184,
                },
            }
            capture = InvocationCapture(
                http_status=200,
                response_body=response_body,
                response_text=json.dumps(response_body),
                response_headers={"Zela-Routed-To": "fr2"},
                client_pre_call_us=100,
                client_post_call_us=175,
            )

            row = build_dataset_row(config, capture, cron_tick_id="tick-id")
            self.assertEqual(
                set(row),
                {
                    "procedure_response",
                    "executor_location",
                    "route_header",
                    "procedure_revision",
                    "payload_variant",
                    "cron_tick_id",
                    "invocation_index",
                    "variant_order",
                    "is_first_in_tick",
                    "client_pre_call_us",
                    "client_post_call_us",
                    "client_total_us",
                    "leader_slot",
                    "leader_pubkey",
                    "leader_resolved_via",
                },
            )
            self.assertEqual(row["procedure_response"], response_body)
            self.assertEqual(row["executor_location"], "fr2")
            self.assertEqual(row["variant_order"], ["v1_happy"])
            self.assertTrue(row["is_first_in_tick"])
            self.assertEqual(row["client_total_us"], 75)
            self.assertIsNone(row["leader_slot"])
            self.assertIsNone(row["leader_pubkey"])
            self.assertIsNone(row["leader_resolved_via"])

            path = append_dataset_row(
                config,
                row,
                run_date=date(2026, 6, 12),
                generated_at="2026-06-12T00:00:00Z",
            )
            append_dataset_row(
                config,
                row,
                run_date=date(2026, 6, 12),
                generated_at="2026-06-12T00:00:01Z",
            )

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            metadata = json.loads(lines[0])
            first_data_row = json.loads(lines[1])
            second_data_row = json.loads(lines[2])
            self.assertEqual(metadata["record_type"], "metadata")
            self.assertEqual(metadata["dataset_schema_version"], "m6.dataset.v1")
            self.assertEqual(metadata["generated_at"], "2026-06-12T00:00:00Z")
            self.assertEqual(first_data_row, row)
            self.assertEqual(second_data_row, row)

    def test_pinned_payload_is_valid_json_object(self):
        payload = load_payload()

        self.assertEqual(
            payload,
            {
                "oracle_pubkeys": ["7UVimffxr9ow1uXYxsr4LHAcV58mLzhmwaeKvJ1pjLiE"],
                "other_pubkeys": [],
                "tx_b64": (
                    "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAACAAQAAAXkDKjE+Oygp/uwN2t7+2YPT5ToQ1CH+V34F8YRsQM6r"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
                ),
                "max_account_count": 32,
                "max_tx_bytes": 1232,
                "max_clock_skew_seconds": 5,
                "max_publish_time_lag_seconds": 60,
                "max_confidence_ratio_bps": 1000000,
            },
        )


if __name__ == "__main__":
    unittest.main()
