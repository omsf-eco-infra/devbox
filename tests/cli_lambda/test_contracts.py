"""Unit tests for CLI Lambda request and event contracts."""

from __future__ import annotations

import json

import pytest

from devbox.cli_lambda.contracts import (
    CliRequestEnvelope,
    InvalidCliRequestError,
    build_event,
    encode_event,
    parse_request_envelope,
)
from devbox.cli_protocol import CliAction, CliEventType


def make_request_body(
    *,
    action: str = CliAction.STATUS.value,
    payload: object | None = None,
) -> bytes:
    """Build a serialized request envelope for contract tests."""
    return json.dumps(
        {
            "version": "v1",
            "action": action,
            "request_id": "req-123",
            "param_prefix": "/devbox",
            "payload": {"project": None} if payload is None else payload,
        }
    ).encode("utf-8")


@pytest.mark.parametrize("action", list(CliAction))
def test_parse_request_envelope_accepts_supported_actions(action: CliAction) -> None:
    envelope = parse_request_envelope(make_request_body(action=action.value))

    assert envelope == CliRequestEnvelope(
        version="v1",
        action=action,
        request_id="req-123",
        param_prefix="/devbox",
        payload={"project": None},
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"[]", "Request body must be a JSON object."),
        (make_request_body(action=""), "Request action must be a non-empty string."),
        (make_request_body(action="no-such-action"), "Unsupported action: no-such-action"),
        (
            make_request_body(payload=[]),
            "Request payload must be an object.",
        ),
    ],
)
def test_parse_request_envelope_rejects_invalid_requests(
    body: bytes,
    message: str,
) -> None:
    with pytest.raises(InvalidCliRequestError, match=message):
        parse_request_envelope(body)


def test_encode_event_serializes_single_ndjson_line():
    line = encode_event(
        build_event(
            CliEventType.RESULT,
            CliAction.STATUS,
            "ready",
            {"ok": True},
        )
    )

    assert (
        line
        == '{"type":"result","action":"status","message":"ready","data":{"ok":true}}\n'
    )
