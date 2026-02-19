"""DNS-specific tests for devbox.launch."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from devbox.launch import launch_programmatic


@patch("devbox.launch.DNSManager.from_ssm")
@patch("devbox.launch.display_instance_info")
@patch("devbox.launch.update_instance_status")
@patch("devbox.launch.launch_instance_in_azs")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.get_volume_info")
@patch("devbox.launch.determine_ami")
@patch("devbox.launch.validate_project_status")
@patch("devbox.launch.get_launch_config")
@patch("devbox.launch.initialize_aws_clients")
def test_launch_programmatic_reuses_stored_cname_subdomain(
    mock_init_aws,
    mock_get_config,
    mock_validate,
    mock_determine_ami,
    mock_get_vol_info,
    mock_get_lt_info,
    mock_launch_azs,
    mock_update,
    mock_display,
    mock_from_ssm,
):
    mock_aws = {"ssm": MagicMock(), "ec2": MagicMock(), "ec2_resource": MagicMock()}
    mock_init_aws.return_value = mock_aws

    table = MagicMock()
    table.get_item.return_value = {"Item": {"Username": "ec2-user"}}
    mock_get_config.return_value = {
        "lt_ids": ["lt-12345"],
        "table": table,
        "item": {"Status": "READY", "CNAMEDomain": "saved-name"},
    }
    mock_validate.return_value = "READY"
    mock_determine_ami.return_value = "ami-12345"
    mock_get_vol_info.return_value = ([], 0)
    mock_get_lt_info.return_value = {"lt-12345": {"name": "us-east-1a"}}

    instance = MagicMock()
    instance.public_dns_name = "ec2-1-2-3-4.compute-1.amazonaws.com"
    mock_launch_azs.return_value = (instance, "i-12345", {"State": {"Name": "running"}})

    dns_manager = MagicMock()
    dns_manager.provider = object()
    dns_manager.normalize_stored_subdomain.return_value = "saved-name"
    dns_manager.assign_cname_to_instance.return_value = "saved-name.example.com"
    dns_manager.sanitize_dns_name.return_value = "saved-name"
    mock_from_ssm.return_value = dns_manager

    launch_programmatic("test-project", instance_type="t3.medium", key_pair="test-key")

    dns_manager.normalize_stored_subdomain.assert_called_once_with("saved-name")
    dns_manager.assign_cname_to_instance.assert_called_once_with(
        project="test-project",
        instance_public_dns="ec2-1-2-3-4.compute-1.amazonaws.com",
        custom_subdomain="saved-name",
    )

    assert mock_update.call_args.kwargs["cname_domain"] == "saved-name"
    mock_display.assert_called_once()


@patch("devbox.launch.DNSManager.from_ssm")
@patch("devbox.launch.display_instance_info")
@patch("devbox.launch.update_instance_status")
@patch("devbox.launch.launch_instance_in_azs")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.get_volume_info")
@patch("devbox.launch.determine_ami")
@patch("devbox.launch.validate_project_status")
@patch("devbox.launch.get_launch_config")
@patch("devbox.launch.initialize_aws_clients")
def test_launch_programmatic_invalid_stored_cname_falls_back_to_project_name(
    mock_init_aws,
    mock_get_config,
    mock_validate,
    mock_determine_ami,
    mock_get_vol_info,
    mock_get_lt_info,
    mock_launch_azs,
    mock_update,
    mock_display,
    mock_from_ssm,
):
    mock_aws = {"ssm": MagicMock(), "ec2": MagicMock(), "ec2_resource": MagicMock()}
    mock_init_aws.return_value = mock_aws

    table = MagicMock()
    table.get_item.return_value = {"Item": {"Username": "ec2-user"}}
    mock_get_config.return_value = {
        "lt_ids": ["lt-12345"],
        "table": table,
        "item": {"Status": "READY", "CNAMEDomain": "saved-name.other-zone.com"},
    }
    mock_validate.return_value = "READY"
    mock_determine_ami.return_value = "ami-12345"
    mock_get_vol_info.return_value = ([], 0)
    mock_get_lt_info.return_value = {"lt-12345": {"name": "us-east-1a"}}

    instance = MagicMock()
    instance.public_dns_name = "ec2-1-2-3-4.compute-1.amazonaws.com"
    mock_launch_azs.return_value = (instance, "i-12345", {"State": {"Name": "running"}})

    dns_manager = MagicMock()
    dns_manager.provider = object()
    dns_manager.normalize_stored_subdomain.return_value = None
    dns_manager.assign_cname_to_instance.return_value = "test-project.example.com"
    dns_manager.sanitize_dns_name.return_value = "test-project"
    mock_from_ssm.return_value = dns_manager

    launch_programmatic("test-project", instance_type="t3.medium", key_pair="test-key")

    dns_manager.assign_cname_to_instance.assert_called_once_with(
        project="test-project",
        instance_public_dns="ec2-1-2-3-4.compute-1.amazonaws.com",
        custom_subdomain=None,
    )
    assert mock_update.call_args.kwargs["cname_domain"] == "test-project"
    mock_display.assert_called_once()
