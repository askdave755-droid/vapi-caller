"""Environment, optional .env, and config-file loading. Values are never printed."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from vapi_caller.client import VapiError

ENV_API_KEY = "VAPI_API_KEY"
ENV_PHONE_NUMBER_ID = "VAPI_PHONE_NUMBER_ID"
ENV_ASSISTANT_ID = "VAPI_ASSISTANT_ID"
ENV_PUBLIC_API_KEY = "VAPI_PUBLIC_API_KEY"

_SENSITIVE_ENV = frozenset({ENV_API_KEY, ENV_PUBLIC_API_KEY})


def first_nonempty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def load_dotenv(path: Path | None = None) -> Path | None:
    """Load KEY=VALUE pairs from a .env file using the standard library only.

    Existing environment variables are not overwritten (same rule as python-dotenv).
    Returns the path that was loaded, or None if no file was found.
    The file contents are never logged.
    """
    candidates: list[Path] = []
    if path is not None:
        candidates.append(path)
    else:
        candidates.append(Path.cwd() / ".env")
    for candidate in candidates:
        if candidate.is_file():
            _apply_dotenv(candidate)
            return candidate
    return None


def _apply_dotenv(path: Path) -> None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VapiError(f"Could not read env file {path}: {exc}") from exc
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _unquote_env_value(value.strip())


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VapiError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VapiError(f"Invalid JSON in {path}: {exc}") from exc


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = load_json_file(path)
    if not isinstance(data, dict):
        raise VapiError(f"Config {path} must be a JSON object.")
    return data


def parse_assistant(
    value: str | None,
    assistant_file: str | None,
    config: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """Return (assistantId, assistant) from CLI, environment, or config.

    Precedence: CLI flags, then VAPI_ASSISTANT_ID, then the config file.
    --assistant may be a saved assistant ID, a JSON object, or a path to JSON.
    """
    if assistant_file:
        data = load_json_file(Path(assistant_file))
        if not isinstance(data, dict):
            raise VapiError("--assistant-file must contain a JSON object (transient assistant).")
        return None, data
    if value:
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise VapiError(f"--assistant JSON is invalid: {exc}") from exc
            if not isinstance(data, dict):
                raise VapiError("--assistant JSON must be an object.")
            return None, data
        as_path = Path(stripped)
        if as_path.is_file():
            data = load_json_file(as_path)
            if not isinstance(data, dict):
                raise VapiError(f"{as_path} must contain a JSON object (transient assistant).")
            return None, data
        return stripped, None
    env_id = first_nonempty(os.environ.get(ENV_ASSISTANT_ID))
    if env_id:
        return env_id, None
    if config.get("assistant") and isinstance(config["assistant"], dict):
        return None, config["assistant"]
    if config.get("assistantId"):
        return str(config["assistantId"]), None
    return None, None


def resolve_phone_number_id(cli_value: str | None, config: dict[str, Any]) -> str | None:
    return first_nonempty(cli_value, os.environ.get(ENV_PHONE_NUMBER_ID), config.get("phoneNumberId"))


def resolve_customer(cli_value: str | None, config: dict[str, Any]) -> str | None:
    config_customer = config.get("customer")
    if isinstance(config_customer, dict):
        config_customer = config_customer.get("number")
    return first_nonempty(cli_value, config_customer)


def resolve_api_key(env_var_name: str) -> str | None:
    return first_nonempty(os.environ.get(env_var_name))


def resolve_public_api_key() -> str | None:
    return first_nonempty(os.environ.get(ENV_PUBLIC_API_KEY))
