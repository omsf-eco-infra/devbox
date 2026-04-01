"""Unit tests for the CLI Lambda app dispatcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from devbox.cli_lambda.app import ACTION_HANDLERS, dispatch_action, execute_action
from devbox.cli_lambda.contracts import CliRequestEnvelope, InvalidCliRequestError
from devbox.cli_protocol import CliAction


def make_envelope(
    *,
    action: CliAction | str = CliAction.STATUS,
    payload: dict[str, object] | None = None,
) -> CliRequestEnvelope:
    """Build a request envelope for dispatcher tests."""
    return CliRequestEnvelope(
        version="v1",
        action=action,
        request_id="req-123",
        param_prefix="/devbox",
        payload={"project": None} if payload is None else payload,
    )


@pytest.mark.parametrize("action", list(CliAction))
def test_dispatch_action_routes_supported_actions(action: CliAction) -> None:
    envelope = make_envelope(action=action)
    handler = MagicMock(return_value=[{"type": "success"}])

    with patch.dict(ACTION_HANDLERS, {action: handler}, clear=True):
        result = dispatch_action(envelope)

    assert result == [{"type": "success"}]
    handler.assert_called_once_with(envelope)


def test_dispatch_action_rejects_unknown_action() -> None:
    envelope = make_envelope(action="no-such-action", payload={})

    with pytest.raises(InvalidCliRequestError, match="Unsupported action: no-such-action"):
        dispatch_action(envelope)


@pytest.mark.parametrize("action", list(CliAction))
def test_execute_action_maps_handler_failures_to_terminal_error(
    action: CliAction,
) -> None:
    envelope = make_envelope(action=action)
    handler = MagicMock(side_effect=RuntimeError("boom"))

    with patch.dict(ACTION_HANDLERS, {action: handler}, clear=True):
        events = execute_action(envelope)

    assert events == [
        {
            "type": "error",
            "action": action.value,
            "message": "boom",
            "data": {},
        }
    ]
