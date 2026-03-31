"""Unit tests for the status command module."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from devbox.cli_lambda.contracts import CliRequestEnvelope
from devbox.cli_protocol import CliAction
from devbox.commands.status import (
    build_status_payload,
    handle_status_action,
    rehydrate_status_result,
    run_status_command,
    serialize_status_payload,
    validate_status_payload,
)
from devbox.remote_client import RemoteInvocationError


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


def make_serialized_status_result(project: str = "demo") -> dict[str, object]:
    return {
        "instances": [
            {
                "InstanceId": "i-123",
                "Project": project,
                "LaunchTime": "2026-03-30T20:30:00+00:00",
            }
        ],
        "volumes": [
            {
                "VolumeId": "vol-123",
                "Project": project,
                "State": "in-use",
                "Size": 100,
                "AvailabilityZone": "us-east-1a",
                "IsOrphaned": False,
            }
        ],
        "snapshots": [
            {
                "SnapshotId": "snap-123",
                "Project": project,
                "Progress": "100%",
                "VolumeSize": 100,
                "StartTime": "2026-03-30T18:00:00+00:00",
                "IsOrphaned": False,
            }
        ],
    }


def test_build_status_payload_uses_project_field() -> None:
    assert build_status_payload("demo") == {"project": "demo"}
    assert build_status_payload(None) == {"project": None}


@patch("devbox.commands.status.invoke_action")
def test_run_status_command_fetches_and_renders_result(mock_invoke_action) -> None:
    mock_invoke_action.return_value = make_serialized_status_result()
    console = MagicMock()

    run_status_command("demo", "/devbox", console=console)

    mock_invoke_action.assert_called_once_with(
        action=CliAction.STATUS,
        payload={"project": "demo"},
        param_prefix="/devbox",
        console=console,
    )
    printed_instances = console.print_instances.call_args[0][0]
    printed_snapshots = console.print_snapshots.call_args[0][0]
    console.print_volumes.assert_called_once()
    assert isinstance(printed_instances[0]["LaunchTime"], datetime)
    assert isinstance(printed_snapshots[0]["StartTime"], datetime)


def test_rehydrate_status_result_rejects_malformed_collections() -> None:
    payload = {"instances": [], "volumes": []}

    with pytest.raises(RemoteInvocationError, match="expected collections"):
        rehydrate_status_result(payload)


def test_validate_status_payload_rejects_non_string_project() -> None:
    with pytest.raises(ValueError, match="must be a string or null"):
        validate_status_payload({"project": 123})


def test_serialize_status_payload_converts_datetimes_to_iso8601() -> None:
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


@patch("devbox.commands.status.DevBoxManager")
def test_handle_status_action_reuses_devbox_manager(mock_manager_class) -> None:
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
