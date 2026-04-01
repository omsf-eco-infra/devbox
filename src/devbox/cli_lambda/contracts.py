"""Contracts and event helpers for the CLI Lambda."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from ..cli_protocol import (
    EVENT_TYPES,
    REQUEST_VERSION,
    CliAction,
    CliEventType,
    normalize_action,
    normalize_event_type,
)


class InvalidCliRequestError(ValueError):
    """Error raised when a CLI Lambda request is malformed."""


@dataclass(frozen=True)
class CliRequestEnvelope:
    """Validated CLI request envelope.

    Attributes
    ----------
    version : str
        Wire-format version supplied by the client.
    action : CliAction
        Requested remote action.
    request_id : str
        Client-generated request identifier.
    param_prefix : str
        Parameter prefix supplied by the client.
    payload : dict[str, Any]
        Action-specific request payload.
    """

    version: str
    action: CliAction
    request_id: str
    param_prefix: str
    payload: dict[str, Any]


def parse_request_envelope(raw_body: bytes) -> CliRequestEnvelope:
    """Parse and validate a CLI Lambda request body.

    Parameters
    ----------
    raw_body : bytes
        Raw HTTP request body from the Lambda Function URL.

    Returns
    -------
    CliRequestEnvelope
        Parsed and validated request envelope.

    Raises
    ------
    InvalidCliRequestError
        Raised when the request body is not valid JSON or does not match the
        expected envelope shape.
    """
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
    """Build one CLI NDJSON event payload.

    Parameters
    ----------
    event_type : CliEventType | str
        Event type to emit.
    action : CliAction | str
        Action associated with the event.
    message : str
        Human-readable event message.
    data : dict[str, Any] | None, optional
        Structured event payload.

    Returns
    -------
    dict[str, Any]
        JSON-serializable event mapping.

    Raises
    ------
    ValueError
        Raised when ``event_type`` is not part of the shared protocol.
    """
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
    """Serialize one event mapping as an NDJSON line.

    Parameters
    ----------
    event : dict[str, Any]
        Event payload to serialize.

    Returns
    -------
    str
        Compact JSON string terminated by a newline.
    """
    return json.dumps(event, separators=(",", ":")) + "\n"
