"""Authentication helpers for Zela executor calls."""

from __future__ import annotations

import os
from typing import Mapping

TOKEN_SCOPE = "zela-executor:call"


class AuthError(RuntimeError):
    """Raised when the orchestrator cannot get a usable executor JWT."""


def read_credentials(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    env_map = os.environ if env is None else env
    key_id = env_map.get("ZELA_KEY_ID")
    key_secret = env_map.get("ZELA_KEY_SECRET")
    missing = [
        name
        for name, value in (
            ("ZELA_KEY_ID", key_id),
            ("ZELA_KEY_SECRET", key_secret),
        )
        if not value
    ]
    if missing:
        raise AuthError(f"missing env vars: {', '.join(missing)}")
    return key_id, key_secret


def fetch_jwt(
    session,
    auth_url: str,
    key_id: str,
    key_secret: str,
    timeout_seconds: int,
) -> str:
    response = session.post(
        auth_url,
        auth=(key_id, key_secret),
        data={"grant_type": "client_credentials", "scope": TOKEN_SCOPE},
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        raise AuthError(
            f"JWT fetch failed: http_status={response.status_code} body={response.text}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise AuthError(f"JWT fetch returned non-JSON body: {response.text}") from exc

    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AuthError("JWT fetch returned null/empty access_token")
    return access_token
