"""Contracts and event helpers for the CLI Lambda."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from ..cli_protocol import (
    EVENT_TYPES,
    NDJSON_MIME_TYPE,
    REQUEST_VERSION,
    CliAction,
    CliEventType,
    normalize_action,
    normalize_event_type,
)


class InvalidCliRequestError(ValueError):
    """Raised when a CLI Lambda request is malformed."""


@dataclass(frozen=True)
class CliRequestEnvelope:
    """Validated CLI request envelope."""

    version: str
    action: CliAction
    request_id: str
    param_prefix: str
    payload: dict[str, Any]


def parse_request_envelope(raw_body: bytes) -> CliRequestEnvelope:
    """Parse and validate a CLI Lambda request body."""
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise InvalidCliRequestError("Request body must be valid JSON.") from exc

    if not isinstance(body, dict):
        raise InvalidCliRequestError("Request body must be a JSON object.")

    version = body.get("version")
    if version != REQUEST_VERSION:
        raise InvalidCliRequestError(f"Unsupported request version: {version}")

    action_value = body.get("action")
    if not isinstance(action_value, str) or not action_value:
        raise InvalidCliRequestError("Request action must be a non-empty string.")
    try:
        action = CliAction(action_value)
    except ValueError as exc:
        raise InvalidCliRequestError(f"Unsupported action: {action_value}") from exc

    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise InvalidCliRequestError("Request request_id must be a non-empty string.")

    param_prefix = body.get("param_prefix")
    if not isinstance(param_prefix, str) or not param_prefix:
        raise InvalidCliRequestError("Request param_prefix must be a non-empty string.")

    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise InvalidCliRequestError("Request payload must be an object.")

    return CliRequestEnvelope(
        version=version,
        action=action,
        request_id=request_id,
        param_prefix=param_prefix,
        payload=payload,
    )


def build_event(
    event_type: CliEventType | str,
    action: CliAction | str,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one NDJSON event payload."""
    event_name = normalize_event_type(event_type)
    if event_name not in EVENT_TYPES:
        raise ValueError(f"Unsupported event type: {event_name}")

    action_name = normalize_action(action)

    return {
        "type": event_name,
        "action": action_name,
        "message": message,
        "data": data or {},
    }


def encode_event(event: dict[str, Any]) -> str:
    """Serialize an event as one NDJSON line."""
    return json.dumps(event, separators=(",", ":")) + "\n"
