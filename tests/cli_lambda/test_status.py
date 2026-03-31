"""Unit tests for the CLI Lambda status action."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from devbox.cli_lambda.contracts import CliRequestEnvelope
from devbox.cli_lambda.status import (
    handle_status_action,
    serialize_status_payload,
    validate_status_payload,
)
from devbox.cli_protocol import CliAction


def make_status_envelope(
    project: str | None = None,
    param_prefix: str = "/devbox",
) -> CliRequestEnvelope:
    return CliRequestEnvelope(
        version="v1",
        action=CliAction.STATUS,
        request_id="req-123",
        param_prefix=param_prefix,
        payload={"project": project},
    )


def test_validate_status_payload_rejects_non_string_project():
    with pytest.raises(ValueError, match="must be a string or null"):
        validate_status_payload({"project": 123})


def test_serialize_status_payload_converts_datetimes_to_iso8601():
    payload = serialize_status_payload(
        instances=[
            {
                "InstanceId": "i-123",
                "LaunchTime": datetime(2026, 3, 30, 20, 30, tzinfo=timezone.utc),
            }
        ],
        volumes=[{"VolumeId": "vol-123"}],
        snapshots=[
            {
                "SnapshotId": "snap-123",
                "StartTime": datetime(2026, 3, 30, 18, 0, tzinfo=timezone.utc),
            }
        ],
    )

    assert payload["instances"][0]["LaunchTime"] == "2026-03-30T20:30:00+00:00"
    assert payload["snapshots"][0]["StartTime"] == "2026-03-30T18:00:00+00:00"


@patch("devbox.cli_lambda.status.DevBoxManager")
def test_handle_status_action_reuses_devbox_manager(mock_manager_class):
    mock_manager = MagicMock()
    mock_manager_class.return_value = mock_manager
    mock_manager.list_instances.return_value = [{"InstanceId": "i-123"}]
    mock_manager.list_volumes.return_value = [{"VolumeId": "vol-123"}]
    mock_manager.list_snapshots.return_value = [{"SnapshotId": "snap-123"}]

    events = handle_status_action(
        make_status_envelope(project="demo", param_prefix="/custom/devbox")
    )

    mock_manager_class.assert_called_once_with(prefix="custom/devbox")
    mock_manager.list_instances.assert_called_once_with("demo")
    mock_manager.list_volumes.assert_called_once_with("demo")
    mock_manager.list_snapshots.assert_called_once_with("demo")
    assert events[0]["type"] == "result"
    assert events[0]["action"] == "status"
    assert events[1]["type"] == "success"
