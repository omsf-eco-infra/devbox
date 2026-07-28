"""Unit tests for the Lambda-backed launch command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from devbox.cli_lambda.contracts import CliRequestEnvelope
from devbox.cli_protocol import CliAction
from devbox.commands.launch import (
    MAX_USERDATA_BYTES,
    build_launch_payload,
    configured_param_prefix,
    handle_launch_action,
    read_userdata_file,
    run_launch_command,
    validate_launch_payload,
)
from devbox.launch import LaunchResult, LaunchUpdate


def make_envelope(payload: dict | None = None) -> CliRequestEnvelope:
    return CliRequestEnvelope(
        version="v1",
        action=CliAction.LAUNCH,
        request_id="req-123",
        param_prefix="/devbox",
        payload=payload
        or {
            "project": "demo",
            "instance_type": "t3.medium",
            "key_pair": "demo-key",
            "volume_size": 0,
            "base_ami": None,
            "userdata": None,
            "assign_dns": True,
            "dns_subdomain": None,
        },
    )


def test_read_userdata_file_inlines_content(tmp_path) -> None:
    path = tmp_path / "userdata.sh"
    path.write_text("#!/bin/bash\necho hello", encoding="utf-8")
    assert read_userdata_file(str(path)) == "#!/bin/bash\necho hello"


def test_read_userdata_file_rejects_oversized_content(tmp_path) -> None:
    path = tmp_path / "userdata.sh"
    path.write_text("x" * (MAX_USERDATA_BYTES + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="must not exceed"):
        read_userdata_file(str(path))


def test_build_launch_payload_contains_all_options() -> None:
    payload = build_launch_payload(
        project="demo",
        instance_type="t3.medium",
        key_pair="key",
        volume_size=100,
        base_ami="ami-123",
        userdata="data",
        assign_dns=False,
        dns_subdomain="custom",
    )
    assert payload == {
        "project": "demo",
        "instance_type": "t3.medium",
        "key_pair": "key",
        "volume_size": 100,
        "base_ami": "ami-123",
        "userdata": "data",
        "assign_dns": False,
        "dns_subdomain": "custom",
    }


@patch("devbox.commands.launch.invoke_action")
def test_run_launch_command_uses_long_timeout_and_renders(mock_invoke, tmp_path) -> None:
    path = tmp_path / "userdata.sh"
    path.write_text("data", encoding="utf-8")
    mock_invoke.return_value = {"instance_id": "i-123"}
    console = MagicMock()

    run_launch_command(
        project="demo",
        instance_type=None,
        key_pair=None,
        volume_size=0,
        base_ami=None,
        param_prefix="/devbox",
        userdata_file=str(path),
        assign_dns=True,
        dns_subdomain=None,
        console=console,
    )

    assert mock_invoke.call_args.kwargs["action"] is CliAction.LAUNCH
    assert mock_invoke.call_args.kwargs["payload"]["userdata"] == "data"
    assert mock_invoke.call_args.kwargs["read_timeout"] == 930
    console.print_launch_result.assert_called_once_with({"instance_id": "i-123"})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("project", "bad project", "alphanumeric"),
        ("volume_size", -1, "non-negative integer"),
        ("volume_size", True, "non-negative integer"),
        ("assign_dns", "yes", "boolean"),
        ("instance_type", "", "string or null"),
        ("userdata", 123, "string or null"),
    ],
)
def test_validate_launch_payload_rejects_invalid_fields(field, value, message) -> None:
    payload = make_envelope().payload.copy()
    payload[field] = value
    with pytest.raises(ValueError, match=message):
        validate_launch_payload(payload, "/devbox")


def test_configured_param_prefix_rejects_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("DEVBOX_PARAM_PREFIX", "/configured")
    with pytest.raises(ValueError, match="does not match"):
        configured_param_prefix("/requested")


@patch("devbox.commands.launch.iter_launch_workflow")
def test_handle_launch_action_maps_workflow_events(mock_workflow, monkeypatch) -> None:
    monkeypatch.setenv("DEVBOX_PARAM_PREFIX", "/devbox")
    mock_workflow.return_value = iter(
        [
            LaunchUpdate("progress", "working"),
            LaunchUpdate("warning", "heads up"),
            LaunchResult(
                project="demo",
                instance_id="i-123",
                state="running",
                instance_type="t3.medium",
                image_id="ami-123",
                availability_zone="us-east-1a",
                private_ip="10.0.0.1",
                public_ip="54.0.0.1",
                ssh_username="ubuntu",
                dns_name=None,
            ),
        ]
    )

    events = list(handle_launch_action(make_envelope()))

    assert [event["type"] for event in events] == [
        "progress",
        "warning",
        "result",
        "success",
    ]
    assert events[2]["data"]["instance_id"] == "i-123"
