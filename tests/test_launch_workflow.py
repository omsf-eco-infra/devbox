"""Tests for the structured launch workflow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from devbox.launch import LaunchRequest, LaunchResult, LaunchUpdate, iter_launch_workflow


def make_instance(*states: str) -> MagicMock:
    """Build an EC2 instance resource that advances through states on reload."""
    instance = MagicMock()
    remaining = iter(states)

    def reload() -> None:
        state = next(remaining, states[-1])
        instance.state = {"Name": state}
        instance.meta.data = {
            "InstanceId": "i-123",
            "State": {"Name": state},
            "InstanceType": "t3.medium",
            "ImageId": "ami-123",
            "Placement": {"AvailabilityZone": "us-east-1a"},
            "PrivateIpAddress": "10.0.0.1",
            "PublicIpAddress": "54.0.0.1",
        }

    instance.meta.data = {"State": {"Name": states[0]}}
    instance.public_dns_name = "ec2.example.internal"
    instance.reload.side_effect = reload
    return instance


def make_aws() -> dict[str, MagicMock]:
    return {
        "ssm": MagicMock(),
        "ddb": MagicMock(),
        "ec2": MagicMock(),
        "ec2_resource": MagicMock(),
    }


def make_client_error(code: str) -> ClientError:
    """Build a representative EC2 client error."""
    return ClientError(
        {"Error": {"Code": code, "Message": code}},
        "DescribeInstances",
    )


@patch("devbox.launch.update_instance_status")
@patch("devbox.launch._resolve_ssh_username", return_value=("ubuntu", None))
@patch("devbox.launch.DNSManager.from_ssm")
@patch("devbox.launch.launch_instance")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.get_volume_info")
@patch("devbox.launch.get_launch_config")
def test_iter_launch_workflow_emits_updates_and_result(
    mock_config,
    mock_volumes,
    mock_templates,
    mock_launch,
    mock_dns_from_ssm,
    _mock_username,
    mock_update,
) -> None:
    aws = make_aws()
    table = MagicMock()
    mock_config.return_value = {
        "item": {"Status": "READY", "AMI": "ami-123"},
        "lt_ids": ["lt-123"],
        "table": table,
    }
    def get_volumes(_ec2, _image_id, _minimum, *, on_progress):
        on_progress("Increasing volume size from 20 GiB to 50 GiB")
        return [], 50

    def get_templates(_ec2, _lt_ids, *, on_warning):
        on_warning(
            "Unable to inspect launch template lt-123: AccessDenied: denied; "
            "using fallback availability-zone label us-east-1a"
        )
        return {"lt-123": {"name": "us-east-1a", "index": "1"}}

    mock_volumes.side_effect = get_volumes
    mock_templates.side_effect = get_templates
    instance = make_instance("pending", "running", "running")
    mock_launch.return_value = (instance, "i-123", None)
    dns_manager = MagicMock()
    dns_manager.provider = object()
    dns_manager.sanitize_dns_name.return_value = "demo"
    dns_manager.assign_cname.return_value = "demo.example.com"
    mock_dns_from_ssm.return_value = dns_manager
    sleeper = MagicMock()

    events = list(
        iter_launch_workflow(
            LaunchRequest(
                project="demo",
                instance_type="t3.medium",
                key_pair="demo-key",
                userdata="#!/bin/bash",
            ),
            aws=aws,
            sleeper=sleeper,
        )
    )

    assert any(
        isinstance(event, LaunchUpdate) and "waiting for running state" in event.message
        for event in events
    )
    volume_progress_index = events.index(
        LaunchUpdate("progress", "Increasing volume size from 20 GiB to 50 GiB")
    )
    template_warning_index = events.index(
        LaunchUpdate(
            "warning",
            "Unable to inspect launch template lt-123: AccessDenied: denied; "
            "using fallback availability-zone label us-east-1a",
        )
    )
    launch_attempt_index = events.index(
        LaunchUpdate("progress", "Attempting to launch instance in us-east-1a")
    )
    assert volume_progress_index < template_warning_index < launch_attempt_index
    result = events[-1]
    assert result == LaunchResult(
        project="demo",
        instance_id="i-123",
        state="running",
        instance_type="t3.medium",
        image_id="ami-123",
        availability_zone="us-east-1a",
        private_ip="10.0.0.1",
        public_ip="54.0.0.1",
        ssh_username="ubuntu",
        dns_name="demo.example.com",
    )
    assert mock_launch.call_args.kwargs["userdata"] == "#!/bin/bash"
    sleeper.assert_called_once_with(15)
    mock_update.assert_called_once()


@patch("devbox.launch.get_launch_config")
def test_iter_launch_workflow_uses_saved_instance_type_and_key_pair(mock_config) -> None:
    mock_config.return_value = {
        "item": {
            "Status": "READY",
            "AMI": "ami-123",
            "LastInstanceType": "m7i.large",
            "LastKeyPair": "saved-key",
        },
        "lt_ids": [],
        "table": MagicMock(),
    }

    with pytest.raises(RuntimeError, match="all availability zones"):
        list(iter_launch_workflow(LaunchRequest(project="demo"), aws=make_aws()))


@patch("devbox.launch.launch_instance")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.get_volume_info", return_value=([], 0))
@patch("devbox.launch.get_launch_config")
def test_iter_launch_workflow_reports_multi_az_failures(
    mock_config,
    _mock_volumes,
    mock_templates,
    mock_launch,
) -> None:
    mock_config.return_value = {
        "item": {"Status": "READY", "AMI": "ami-123"},
        "lt_ids": ["lt-a", "lt-b"],
        "table": MagicMock(),
    }
    mock_templates.return_value = {
        "lt-a": {"name": "us-east-1a", "index": "1"},
        "lt-b": {"name": "us-east-1b", "index": "2"},
    }
    instance = make_instance("running", "running")
    mock_launch.side_effect = [
        (None, None, RuntimeError("capacity")),
        (instance, "i-123", None),
    ]

    with (
        patch("devbox.launch.DNSManager.from_ssm") as mock_dns,
        patch("devbox.launch._resolve_ssh_username", return_value=(None, "unknown")),
        patch("devbox.launch.update_instance_status"),
    ):
        mock_dns.return_value.provider = None
        events = list(
            iter_launch_workflow(
                LaunchRequest(
                    project="demo",
                    instance_type="t3.medium",
                    key_pair="key",
                ),
                aws=make_aws(),
            )
        )

    assert any(
        isinstance(event, LaunchUpdate)
        and event.kind == "warning"
        and "capacity" in event.message
        for event in events
    )


@patch("devbox.launch.launch_instance")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.get_volume_info", return_value=([], 0))
@patch("devbox.launch.get_launch_config")
def test_iter_launch_workflow_times_out_waiting_for_running(
    mock_config,
    _mock_volumes,
    mock_templates,
    mock_launch,
) -> None:
    mock_config.return_value = {
        "item": {"Status": "READY", "AMI": "ami-123"},
        "lt_ids": ["lt-a"],
        "table": MagicMock(),
    }
    mock_templates.return_value = {"lt-a": {"name": "us-east-1a", "index": "1"}}
    mock_launch.return_value = (make_instance("pending"), "i-123", None)

    with pytest.raises(TimeoutError, match="did not reach running state"):
        list(
            iter_launch_workflow(
                LaunchRequest(
                    project="demo", instance_type="t3.medium", key_pair="key"
                ),
                aws=make_aws(),
                sleeper=lambda _seconds: None,
                max_poll_attempts=2,
            )
        )


@patch("devbox.launch.update_instance_status")
@patch("devbox.launch._resolve_ssh_username", return_value=("ubuntu", None))
@patch("devbox.launch.DNSManager.from_ssm")
@patch("devbox.launch.launch_instance")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.get_volume_info", return_value=([], 0))
@patch("devbox.launch.get_launch_config")
def test_iter_launch_workflow_retries_instance_not_found(
    mock_config,
    _mock_volumes,
    mock_templates,
    mock_launch,
    mock_dns,
    _mock_username,
    _mock_update,
) -> None:
    mock_config.return_value = {
        "item": {"Status": "READY", "AMI": "ami-123"},
        "lt_ids": ["lt-a"],
        "table": MagicMock(),
    }
    mock_templates.return_value = {"lt-a": {"name": "us-east-1a", "index": "1"}}
    instance = make_instance("running")
    normal_reload = instance.reload.side_effect
    calls = 0

    def reload() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise make_client_error("InvalidInstanceID.NotFound")
        normal_reload()

    instance.reload.side_effect = reload
    mock_launch.return_value = (instance, "i-123", None)
    mock_dns.return_value.provider = None
    sleeper = MagicMock()

    events = list(
        iter_launch_workflow(
            LaunchRequest(project="demo", instance_type="t3.medium", key_pair="key"),
            aws=make_aws(),
            sleeper=sleeper,
        )
    )

    assert isinstance(events[-1], LaunchResult)
    assert any(
        isinstance(event, LaunchUpdate)
        and "not yet visible to EC2" in event.message
        for event in events
    )
    sleeper.assert_called_once_with(15)


@patch("devbox.launch.launch_instance")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.get_volume_info", return_value=([], 0))
@patch("devbox.launch.get_launch_config")
def test_iter_launch_workflow_times_out_when_instance_remains_not_found(
    mock_config,
    _mock_volumes,
    mock_templates,
    mock_launch,
) -> None:
    mock_config.return_value = {
        "item": {"Status": "READY", "AMI": "ami-123"},
        "lt_ids": ["lt-a"],
        "table": MagicMock(),
    }
    mock_templates.return_value = {"lt-a": {"name": "us-east-1a", "index": "1"}}
    instance = MagicMock()
    instance.meta.data = {"State": {"Name": "pending"}}
    instance.reload.side_effect = make_client_error("InvalidInstanceID.NotFound")
    mock_launch.return_value = (instance, "i-123", None)

    with pytest.raises(TimeoutError, match="did not reach running state"):
        list(
            iter_launch_workflow(
                LaunchRequest(
                    project="demo", instance_type="t3.medium", key_pair="key"
                ),
                aws=make_aws(),
                sleeper=lambda _seconds: None,
                max_poll_attempts=2,
            )
        )


@patch("devbox.launch.launch_instance")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.get_volume_info", return_value=([], 0))
@patch("devbox.launch.get_launch_config")
def test_iter_launch_workflow_propagates_other_reload_errors(
    mock_config,
    _mock_volumes,
    mock_templates,
    mock_launch,
) -> None:
    mock_config.return_value = {
        "item": {"Status": "READY", "AMI": "ami-123"},
        "lt_ids": ["lt-a"],
        "table": MagicMock(),
    }
    mock_templates.return_value = {"lt-a": {"name": "us-east-1a", "index": "1"}}
    instance = MagicMock()
    instance.meta.data = {"State": {"Name": "pending"}}
    instance.reload.side_effect = make_client_error("UnauthorizedOperation")
    mock_launch.return_value = (instance, "i-123", None)

    with pytest.raises(ClientError) as exc_info:
        list(
            iter_launch_workflow(
                LaunchRequest(
                    project="demo", instance_type="t3.medium", key_pair="key"
                ),
                aws=make_aws(),
                sleeper=lambda _seconds: None,
            )
        )

    assert exc_info.value.response["Error"]["Code"] == "UnauthorizedOperation"


@pytest.mark.parametrize(
    "launch_request,message",
    [
        (LaunchRequest(project="bad project"), "Project name"),
        (LaunchRequest(project="demo", volume_size=-1), "Volume size"),
    ],
)
def test_iter_launch_workflow_validates_inputs(launch_request, message) -> None:
    with pytest.raises(ValueError, match=message):
        list(iter_launch_workflow(launch_request, aws=make_aws()))
