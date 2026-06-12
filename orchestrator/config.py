"""Configuration for the M6 phase 0 smoke orchestrator."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

AUTH_URL = "https://auth.zela.io/realms/zela/protocol/openid-connect/token"
EXECUTOR_URL = "https://executor.zela.io"
PROCEDURE_NAME = "oracle_read"
PROCEDURE_REVISION = "e442a8916f41ab2827ef64da6d75ed683a42b375"
ROUTE_HEADER = "static fr2"
PAYLOAD_VARIANT = "v1_happy"

# The dataset directory name intentionally differs from the deployed procedure
# name: "m6_sim_recheck" is the M6 dataset/crate name, while "oracle_read" is
# the deployed procedure name for this slice.
DATASET_DIR = "zela_datasets/m6_sim_recheck"

AUTH_TIMEOUT_SECONDS = 30
INVOKE_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class M6SliceConfig:
    auth_url: str
    executor_url: str
    method: str
    procedure_revision: str
    route_header: str
    output_dir: Path
    payload_variant: str = PAYLOAD_VARIANT
    auth_timeout_seconds: int = AUTH_TIMEOUT_SECONDS
    invoke_timeout_seconds: int = INVOKE_TIMEOUT_SECONDS


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _path_from_env(value: str, root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def load_config(
    env: Mapping[str, str] | None = None,
    root: Path | None = None,
) -> M6SliceConfig:
    env_map = os.environ if env is None else env
    root_path = repo_root() if root is None else root

    procedure_name = env_map.get("M6_PROCEDURE_NAME", PROCEDURE_NAME)
    procedure_revision = env_map.get("M6_PROCEDURE_REVISION", PROCEDURE_REVISION)
    method = env_map.get(
        "M6_PROCEDURE_METHOD",
        f"zela.{procedure_name}#{procedure_revision}",
    )
    output_dir = _path_from_env(env_map.get("M6_DATASET_DIR", DATASET_DIR), root_path)

    return M6SliceConfig(
        auth_url=env_map.get("M6_AUTH_URL", AUTH_URL),
        executor_url=env_map.get("M6_EXECUTOR_URL", EXECUTOR_URL),
        method=method,
        procedure_revision=procedure_revision,
        route_header=env_map.get("M6_ROUTE_HEADER", ROUTE_HEADER),
        output_dir=output_dir,
    )
