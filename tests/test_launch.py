"""Unit tests for devbox launch module."""

import argparse
import sys
from unittest.mock import MagicMock, patch
import pytest
from botocore.exceptions import ClientError

from devbox.launch import (
    make_parser,
    get_project_snapshot,
    get_volume_info,
    get_launch_template_info,
    launch_instance,
    update_instance_status,
    parse_arguments,
    initialize_aws_clients,
    get_launch_config,
    validate_project_status,
    determine_ami,
    launch_instance_in_azs,
    display_instance_info,
    launch_programmatic,
    main,
)
from devbox.utils import ResourceNotFoundError, AWSClientError


# Fixtures for reusable setup


@pytest.fixture
def mock_table():
    """Mock DynamoDB table for testing."""
    table = MagicMock()
    table.table_name = "test-table"
    return table


@pytest.fixture
def mock_ec2_client():
    """Mock EC2 client for testing."""
    return MagicMock()


@pytest.fixture
def mock_ec2_resource():
    """Mock EC2 resource for testing."""
    return MagicMock()


@pytest.fixture
def mock_aws_clients():
    """Mock AWS clients dictionary for testing."""
    return {
        "ssm": MagicMock(),
        "ec2": MagicMock(),
        "ec2_resource": MagicMock(),
        "ddb": MagicMock(),
    }


# Test functions for make_parser


def test_make_parser_creates_parser():
    """Test that make_parser creates an ArgumentParser."""
    parser = make_parser()
    assert isinstance(parser, argparse.ArgumentParser)


def test_make_parser_required_arguments():
    """Test that parser has required arguments."""
    parser = make_parser()
    required_actions = [action for action in parser._actions if action.required]
    required_dests = [action.dest for action in required_actions]

    assert "project" in required_dests
    # instance_type and key_pair are no longer required
    assert "instance_type" not in required_dests
    assert "key_pair" not in required_dests


def test_make_parser_optional_arguments():
    """Test that parser has optional arguments with correct defaults."""
    parser = make_parser()

    # Test with optional arguments provided
    args = parser.parse_args(
        [
            "--project",
            "test-project",
            "--instance-type",
            "t3.medium",
            "--key-pair",
            "test-key",
        ]
    )
    assert args.project == "test-project"
    assert args.instance_type == "t3.medium"
    assert args.key_pair == "test-key"
    assert args.volume_size == 0
    assert args.base_ami is None
    assert args.param_prefix == "/devbox"
    assert args.assign_dns is True
    assert args.dns_subdomain is None

    # Test with only required arguments (instance-type and key-pair are now optional)
    args_minimal = parser.parse_args(["--project", "test-project"])
    assert args_minimal.project == "test-project"
    assert args_minimal.instance_type is None
    assert args_minimal.key_pair is None
    assert args_minimal.volume_size == 0
    assert args_minimal.base_ami is None
    assert args_minimal.param_prefix == "/devbox"
    assert args_minimal.assign_dns is True
    assert args_minimal.dns_subdomain is None


# Test functions for get_project_snapshot


def test_get_project_snapshot_success(mock_table):
    """Test successful project snapshot retrieval."""
    mock_response = {
        "Item": {
            "project": "test-project",
            "Status": "READY",
            "BaseAmi": "ami-12345",
            "RestoreAmi": "ami-67890",
        }
    }
    mock_table.get_item.return_value = mock_response

    item, error = get_project_snapshot(mock_table, "test-project")

    assert error is None
    assert item["project"] == "test-project"
    assert item["Status"] == "READY"


def test_get_project_snapshot_not_found(mock_table):
    """Test project snapshot not found."""
    mock_table.get_item.return_value = {}

    item, error = get_project_snapshot(mock_table, "nonexistent-project")

    assert item == {"project": "nonexistent-project", "Status": "nonexistent"}
    assert error is None


def test_get_project_snapshot_client_error(mock_table):
    """Test DynamoDB client error."""
    mock_table.get_item.side_effect = ClientError(
        error_response={"Error": {"Code": "ServiceUnavailable"}},
        operation_name="GetItem",
    )

    item, error = get_project_snapshot(mock_table, "test-project")

    assert item == {}
    assert error is not None
    assert "DynamoDB error" in error


def test_get_project_snapshot_resource_not_found(mock_table):
    """Test project snapshot with ResourceNotFoundException."""
    mock_table.get_item.side_effect = ClientError(
        error_response={"Error": {"Code": "ResourceNotFoundException"}},
        operation_name="GetItem",
    )

    item, error = get_project_snapshot(mock_table, "test-project")

    assert item == {"project": "test-project", "Status": "nonexistent"}
    assert error is None


# Test functions for get_volume_info


def test_get_volume_info_success(mock_ec2_client):
    """Test successful volume info retrieval."""
    mock_response = {
        "Images": [
            {
                "BlockDeviceMappings": [
                    {
                        "DeviceName": "/dev/sda1",
                        "Ebs": {"VolumeSize": 20, "VolumeType": "gp3"},
                    },
                    {
                        "DeviceName": "/dev/sdb",
                        "Ebs": {"VolumeSize": 100, "VolumeType": "gp3"},
                    },
                ]
            }
        ]
    }
    mock_ec2_client.describe_images.return_value = mock_response

    volumes, largest_size = get_volume_info(
        mock_ec2_client, "ami-12345", min_volume_size=50
    )

    assert len(volumes) == 2
    assert largest_size == 100
    assert volumes[0]["DeviceName"] == "/dev/sda1"
    assert volumes[1]["DeviceName"] == "/dev/sdb"


def test_get_volume_info_with_min_size(mock_ec2_client):
    """Test volume info with minimum size enforcement."""
    mock_response = {
        "Images": [
            {
                "BlockDeviceMappings": [
                    {
                        "DeviceName": "/dev/sda1",
                        "Ebs": {"VolumeSize": 20, "VolumeType": "gp3"},
                    }
                ]
            }
        ]
    }
    mock_ec2_client.describe_images.return_value = mock_response

    volumes, largest_size = get_volume_info(
        mock_ec2_client, "ami-12345", min_volume_size=100
    )

    assert len(volumes) == 1
    assert largest_size == 100  # Should be adjusted to minimum
    assert volumes[0]["Ebs"]["VolumeSize"] == 100


def test_get_volume_info_no_image(mock_ec2_client):
    """Test volume info when AMI doesn't exist."""
    mock_ec2_client.describe_images.return_value = {"Images": []}

    with pytest.raises(Exception) as exc_info:
        get_volume_info(mock_ec2_client, "ami-nonexistent")

    assert "AMI ami-nonexistent not found" in str(exc_info.value)


# Test functions for get_launch_template_info


def test_get_launch_template_info_success(mock_ec2_client):
    """Test successful launch template info retrieval."""
    mock_templates_response = {
        "LaunchTemplates": [{"LaunchTemplateName": "devbox-us-east-1a-template"}]
    }
    mock_versions_response = {"LaunchTemplateVersions": [{"LaunchTemplateData": {}}]}

    mock_ec2_client.describe_launch_templates.return_value = mock_templates_response
    mock_ec2_client.describe_launch_template_versions.return_value = (
        mock_versions_response
    )

    az_info = get_launch_template_info(mock_ec2_client, ["lt-12345"])

    assert "lt-12345" in az_info
    assert az_info["lt-12345"]["name"] == "us-east-1a"
    assert az_info["lt-12345"]["index"] == "1"


def test_get_launch_template_info_with_errors(mock_ec2_client):
    """Test launch template info with various naming patterns."""
    mock_templates_response = {
        "LaunchTemplates": [{"LaunchTemplateName": "custom-template-name"}]
    }
    mock_versions_response = {"LaunchTemplateVersions": [{"LaunchTemplateData": {}}]}

    mock_ec2_client.describe_launch_templates.return_value = mock_templates_response
    mock_ec2_client.describe_launch_template_versions.return_value = (
        mock_versions_response
    )

    az_info = get_launch_template_info(mock_ec2_client, ["lt-12345"])

    assert "lt-12345" in az_info
    assert "name" in az_info["lt-12345"]


# Test functions for launch_instance


def test_launch_instance_success(mock_ec2_client, mock_ec2_resource):
    """Test successful instance launch."""
    mock_instance = MagicMock()
    mock_instance.id = "i-12345"
    mock_instance.meta.data = {"State": {"Name": "running"}}

    mock_ec2_client.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-12345"}]
    }
    mock_ec2_resource.Instance.return_value = mock_instance

    instance, instance_id, error = launch_instance(
        mock_ec2_client,
        mock_ec2_resource,
        "lt-12345",
        "ami-12345",
        "t3.medium",
        "test-key",
        [{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 20}}],
        "test-project",
        "us-east-1a",
    )

    assert instance == mock_instance
    assert instance_id == "i-12345"
    assert error is None


def test_launch_instance_client_error(mock_ec2_client, mock_ec2_resource):
    """Test launch instance with client error."""
    mock_ec2_client.run_instances.side_effect = ClientError(
        error_response={"Error": {"Code": "InsufficientInstanceCapacity"}},
        operation_name="RunInstances",
    )

    instance, instance_id, error = launch_instance(
        mock_ec2_client,
        mock_ec2_resource,
        "lt-12345",
        "ami-12345",
        "t3.medium",
        "test-key",
        [{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 20}}],
        "test-project",
        "us-east-1a",
    )

    assert instance is None
    assert instance_id is None
    assert isinstance(error, ClientError)
    assert error is not None


# Test functions for update_instance_status


def test_update_instance_status_new_project(mock_table):
    """Test updating instance status for new project."""
    mock_table.get_item.return_value = {}

    update_instance_status(
        mock_table,
        "test-project",
        "LAUNCHING",
        "i-12345",
        "ami-12345",
        "t3.medium",
        "test-keypair",
    )

    mock_table.put_item.assert_called_once()
    call_args = mock_table.put_item.call_args[1]["Item"]
    assert call_args["project"] == "test-project"
    assert call_args["Status"] == "LAUNCHING"
    assert call_args["LastInstanceType"] == "t3.medium"
    assert call_args["LastKeyPair"] == "test-keypair"


def test_update_instance_status_existing_project(mock_table):
    """Test updating instance status for existing project."""
    existing_item = {
        "Item": {"project": "test-project", "Status": "READY", "BaseAmi": "ami-base"}
    }
    mock_table.get_item.return_value = existing_item

    update_instance_status(
        mock_table,
        "test-project",
        "LAUNCHING",
        "i-12345",
        "ami-12345",
        "t3.medium",
        "test-keypair",
    )

    mock_table.put_item.assert_called_once()
    call_args = mock_table.put_item.call_args[1]["Item"]
    assert call_args["BaseAmi"] == "ami-base"  # Should preserve existing values
    assert call_args["LastInstanceType"] == "t3.medium"
    assert call_args["LastKeyPair"] == "test-keypair"


def test_update_instance_status_invalid_status(mock_table):
    """Test updating with invalid status."""
    with pytest.raises(ValueError):
        update_instance_status(
            mock_table,
            "test-project",
            "INVALID_STATUS",
            "i-12345",
            "ami-12345",
            "t3.medium",
            "test-keypair",
        )


def test_update_instance_status_sets_cname(mock_table):
    """Test CNAMEDomain is set when provided."""
    mock_table.get_item.return_value = {}

    update_instance_status(
        mock_table,
        "test-project",
        "LAUNCHING",
        "i-12345",
        "ami-12345",
        "t3.medium",
        "test-keypair",
        cname_domain="dev.example.com",
    )

    call_args = mock_table.put_item.call_args[1]["Item"]
    assert call_args["CNAMEDomain"] == "dev.example.com"


# Test functions for parse_arguments


def test_parse_arguments_valid():
    """Test parsing valid arguments."""
    test_args = [
        "--project",
        "test-project",
        "--instance-type",
        "t3.medium",
        "--key-pair",
        "test-key",
        "--volume-size",
        "100",
    ]

    with patch.object(sys, "argv", ["launch.py"] + test_args):
        args = parse_arguments()

        assert args.project == "test-project"
        assert args.instance_type == "t3.medium"
        assert args.key_pair == "test-key"
        assert args.volume_size == 100


def test_parse_arguments_invalid_project_name():
    """Test parsing with invalid project name."""
    test_args = [
        "--project",
        "Invalid_Project!",
        "--instance-type",
        "t3.medium",
        "--key-pair",
        "test-key",
    ]

    with patch.object(sys, "argv", ["launch.py"] + test_args):
        with pytest.raises(SystemExit):
            parse_arguments()


def test_parse_arguments_negative_volume_size():
    """Test parsing with negative volume size."""
    test_args = [
        "--project",
        "test-project",
        "--instance-type",
        "t3.medium",
        "--key-pair",
        "test-key",
        "--volume-size",
        "-10",
    ]

    with patch.object(sys, "argv", ["launch.py"] + test_args):
        with pytest.raises(SystemExit):
            parse_arguments()


# Test functions for initialize_aws_clients


@patch("devbox.launch.utils.get_ssm_client")
@patch("devbox.launch.utils.get_ec2_client")
@patch("devbox.launch.utils.get_ec2_resource")
@patch("devbox.launch.utils.get_dynamodb_resource")
def test_initialize_aws_clients_success(
    mock_ddb, mock_ec2_resource, mock_ec2_client, mock_ssm
):
    """Test successful AWS clients initialization."""
    mock_ssm.return_value = MagicMock()
    mock_ec2_client.return_value = MagicMock()
    mock_ec2_resource.return_value = MagicMock()
    mock_ddb.return_value = MagicMock()

    aws = initialize_aws_clients()

    assert "ssm" in aws
    assert "ec2" in aws
    assert "ec2_resource" in aws
    assert "ddb" in aws


@patch("devbox.launch.get_ssm_client")
def test_initialize_aws_clients_error(mock_ssm):
    """Test AWS clients initialization with error."""
    mock_ssm.side_effect = Exception("Connection error")

    with pytest.raises(AWSClientError):
        initialize_aws_clients()


# Test functions for get_launch_config


def test_get_launch_config_success():
    """Test successful launch configuration retrieval."""
    mock_aws = {"ssm": MagicMock(), "ddb": MagicMock()}
    mock_table = MagicMock()

    mock_aws["ssm"].get_parameter.side_effect = [
        {"Parameter": {"Value": '["lt-12345", "lt-67890"]'}},
        {"Parameter": {"Value": "test-snapshot-table"}},
    ]
    mock_aws["ddb"].Table.return_value = mock_table
    mock_table.get_item.return_value = {
        "Item": {"project": "test-project", "Status": "READY"}
    }

    config = get_launch_config(mock_aws, "/test", "test-project")

    assert config["lt_ids"] == ["lt-12345", "lt-67890"]
    assert config["table"] == mock_table
    assert config["item"]["project"] == "test-project"


def test_get_launch_config_missing_templates():
    """Test launch configuration with missing templates."""
    mock_aws = {"ssm": MagicMock(), "ddb": MagicMock()}

    mock_aws["ssm"].get_parameter.side_effect = [
        {"Parameter": {"Value": "[]"}},
        {"Parameter": {"Value": "test-snapshot-table"}},
    ]

    with pytest.raises(Exception) as exc_info:
        get_launch_config(mock_aws, "/test", "test-project")

    assert "No launch templates found" in str(exc_info.value)


def test_get_launch_config_legacy_dict_format():
    """Test launch configuration with legacy dictionary format."""
    mock_aws = {"ssm": MagicMock(), "ddb": MagicMock()}
    mock_table = MagicMock()

    mock_aws["ssm"].get_parameter.side_effect = [
        {
            "Parameter": {
                "Value": '{"us-east-1a": "lt-12345", "us-east-1b": "lt-67890"}'
            }
        },
        {"Parameter": {"Value": "test-snapshot-table"}},
    ]
    mock_aws["ddb"].Table.return_value = mock_table
    mock_table.get_item.return_value = {
        "Item": {"project": "test-project", "Status": "READY"}
    }

    config = get_launch_config(mock_aws, "/test", "test-project")

    assert config["lt_ids"] == ["lt-12345", "lt-67890"]
    assert config["table"] == mock_table
    assert config["item"]["project"] == "test-project"


# Test functions for validate_project_status


def test_validate_project_status_valid():
    """Test validation with valid status."""
    item = {"Status": "READY"}
    status = validate_project_status(item, "test-project")
    assert status == "READY"


def test_validate_project_status_invalid():
    """Test validation with invalid status."""
    item = {"Status": "LAUNCHING"}

    with pytest.raises(Exception):
        validate_project_status(item, "test-project")


def test_validate_project_status_missing():
    """Test validation with missing status."""
    item = {}

    with pytest.raises(Exception):
        validate_project_status(item, "test-project")


# Test functions for determine_ami


def test_determine_ami_restored():
    """Test AMI determination with restored AMI."""
    item = {"RestoreAmi": "ami-restored", "BaseAmi": "ami-base"}
    ami = determine_ami(item, None)
    assert ami == "ami-restored"


def test_determine_ami_base_only():
    """Test AMI determination with base AMI only."""
    item = {"BaseAmi": "ami-base"}
    ami = determine_ami(item, None)
    assert ami == "ami-base"


def test_determine_ami_with_ami_field():
    """Test AMI determination with AMI field (used by lambda functions)."""
    item = {"AMI": "ami-lambda"}
    ami = determine_ami(item, None)
    assert ami == "ami-lambda"


def test_determine_ami_priority_order():
    """Test AMI determination priority order: RestoreAmi > BaseAmi > AMI > base_ami parameter."""
    # Test that RestoreAmi has highest priority
    item = {"RestoreAmi": "ami-restored", "BaseAmi": "ami-base", "AMI": "ami-lambda"}
    ami = determine_ami(item, "ami-param")
    assert ami == "ami-restored"

    # Test that BaseAmi has second priority
    item = {"BaseAmi": "ami-base", "AMI": "ami-lambda"}
    ami = determine_ami(item, "ami-param")
    assert ami == "ami-base"

    # Test that AMI field has third priority
    item = {"AMI": "ami-lambda"}
    ami = determine_ami(item, "ami-param")
    assert ami == "ami-lambda"

    # Test that base_ami parameter has lowest priority
    item = {}
    ami = determine_ami(item, "ami-param")
    assert ami == "ami-param"


def test_determine_ami_none_available():
    """Test AMI determination with no AMI available."""
    item = {}
    with pytest.raises(Exception):
        determine_ami(item, None)


# Test functions for launch_instance_in_azs


def test_launch_instance_in_azs_success_first_try(mock_aws_clients):
    """Test successful launch on first AZ attempt."""
    lt_ids = ["lt-12345", "lt-67890"]
    az_info = {
        "lt-12345": {"name": "us-east-1a", "index": "1"},
        "lt-67890": {"name": "us-east-1b", "index": "2"},
    }

    mock_instance = MagicMock()
    mock_instance.id = "i-12345"
    mock_instance.meta.data = {"State": {"Name": "running"}}
    mock_aws_clients["ec2"].run_instances.return_value = {
        "Instances": [{"InstanceId": "i-12345"}]
    }
    mock_aws_clients["ec2_resource"].Instance.return_value = mock_instance

    instance, instance_id, instance_info = launch_instance_in_azs(
        mock_aws_clients,
        lt_ids,
        az_info,
        "ami-12345",
        "t3.medium",
        "test-key",
        [],
        "test-project",
    )

    assert instance == mock_instance
    assert instance_id == "i-12345"


def test_launch_instance_in_azs_all_fail(mock_aws_clients):
    """Test when all AZ attempts fail."""
    lt_ids = ["lt-12345", "lt-67890"]
    az_info = {
        "lt-12345": {"name": "us-east-1a", "index": "1"},
        "lt-67890": {"name": "us-east-1b", "index": "2"},
    }

    mock_aws_clients["ec2"].run_instances.side_effect = ClientError(
        error_response={"Error": {"Code": "InsufficientInstanceCapacity"}},
        operation_name="RunInstances",
    )

    with pytest.raises(Exception):
        launch_instance_in_azs(
            mock_aws_clients,
            lt_ids,
            az_info,
            "ami-12345",
            "t3.medium",
            "test-key",
            [],
            "test-project",
        )


# Test functions for display_instance_info


def test_display_instance_info_success(mock_ec2_client):
    """Test successful instance info display."""
    mock_response = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-12345",
                        "State": {"Name": "running"},
                        "PublicIpAddress": "1.2.3.4",
                        "InstanceType": "t3.medium",
                    }
                ]
            }
        ]
    }
    mock_ec2_client.describe_instances.return_value = mock_response

    mock_table = MagicMock()
    mock_table.get_item.return_value = {"Item": {"Username": "ubuntu"}}

    display_instance_info(mock_ec2_client, "i-12345", "test-project", mock_table)

    mock_ec2_client.describe_instances.assert_called_once_with(InstanceIds=["i-12345"])


def test_display_instance_info_error(mock_ec2_client):
    """Test instance info display with error."""
    mock_ec2_client.describe_instances.side_effect = ClientError(
        error_response={"Error": {"Code": "InvalidInstanceID.NotFound"}},
        operation_name="DescribeInstances",
    )

    mock_table = MagicMock()

    display_instance_info(mock_ec2_client, "i-nonexistent", "test-project", mock_table)


# Test functions for launch_programmatic


@patch("devbox.launch.initialize_aws_clients")
@patch("devbox.launch.get_launch_config")
@patch("devbox.launch.validate_project_status")
@patch("devbox.launch.determine_ami")
@patch("devbox.launch.get_volume_info")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.launch_instance_in_azs")
@patch("devbox.launch.update_instance_status")
@patch("devbox.launch.display_instance_info")
def test_launch_programmatic_success(
    mock_display,
    mock_update,
    mock_launch_azs,
    mock_get_lt_info,
    mock_get_vol_info,
    mock_determine_ami,
    mock_validate,
    mock_get_config,
    mock_init_aws,
):
    """Test successful programmatic launch."""
    mock_aws = {"ec2": MagicMock(), "ec2_resource": MagicMock()}
    mock_init_aws.return_value = mock_aws

    mock_config = {
        "lt_ids": ["lt-12345"],
        "table": MagicMock(),
        "item": {"Status": "READY"},
    }
    mock_get_config.return_value = mock_config
    mock_validate.return_value = "READY"
    mock_determine_ami.return_value = "ami-12345"
    mock_get_vol_info.return_value = ([], 0)
    mock_get_lt_info.return_value = {"lt-12345": {"name": "us-east-1a"}}

    mock_instance = MagicMock()
    mock_instance.meta.data = {"State": {"Name": "running"}}
    mock_launch_azs.return_value = (
        mock_instance,
        "i-12345",
        {"State": {"Name": "running"}},
    )

    launch_programmatic("test-project", instance_type="t3.medium", key_pair="test-key", assign_dns=False)

    mock_init_aws.assert_called_once()
    mock_display.assert_called_once()


def test_launch_programmatic_invalid_project_name():
    """Test programmatic launch with invalid project name."""
    with pytest.raises(SystemExit):
        launch_programmatic(
            "invalid.project", instance_type="t3.medium", key_pair="test-key"
        )


@patch("devbox.launch.initialize_aws_clients")
@patch("devbox.launch.get_launch_config")
@patch("devbox.launch.validate_project_status")
@patch("devbox.launch.determine_ami")
@patch("devbox.launch.get_volume_info")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.launch_instance_in_azs")
@patch("devbox.launch.update_instance_status")
@patch("devbox.launch.display_instance_info")
def test_launch_programmatic_uses_last_keypair(
    mock_display,
    mock_update,
    mock_launch_azs,
    mock_get_lt_info,
    mock_get_vol_info,
    mock_determine_ami,
    mock_validate,
    mock_get_config,
    mock_init_aws,
):
    """Test programmatic launch uses last keypair when none specified."""
    mock_aws = {"ec2": MagicMock(), "ec2_resource": MagicMock()}
    mock_init_aws.return_value = mock_aws

    # Mock config with LastKeyPair in the item
    mock_config = {
        "lt_ids": ["lt-12345"],
        "table": MagicMock(),
        "item": {"Status": "READY", "LastKeyPair": "previous-keypair"},
    }
    mock_get_config.return_value = mock_config
    mock_validate.return_value = "READY"
    mock_determine_ami.return_value = "ami-12345"
    mock_get_vol_info.return_value = ([], 0)
    mock_get_lt_info.return_value = {"lt-12345": {"name": "us-east-1a"}}

    mock_instance = MagicMock()
    mock_instance.meta.data = {"State": {"Name": "running"}}
    mock_launch_azs.return_value = (
        mock_instance,
        "i-12345",
        {"State": {"Name": "running"}},
    )

    # Call with key_pair=None
    launch_programmatic("test-project", instance_type="t3.medium", key_pair=None, assign_dns=False)

    # Verify launch_instance_in_azs was called with the last keypair
    mock_launch_azs.assert_called_once()
    call_args = mock_launch_azs.call_args[1]
    assert call_args["key_name"] == "previous-keypair"


@patch("devbox.launch.initialize_aws_clients")
@patch("devbox.launch.get_launch_config")
@patch("devbox.launch.validate_project_status")
def test_launch_programmatic_no_keypair_error(
    mock_validate, mock_get_config, mock_init_aws
):
    """Test programmatic launch raises error when no keypair specified and none stored."""
    mock_aws = {"ec2": MagicMock(), "ec2_resource": MagicMock()}
    mock_init_aws.return_value = mock_aws

    # Mock config without LastKeyPair in the item
    mock_config = {
        "lt_ids": ["lt-12345"],
        "table": MagicMock(),
        "item": {"Status": "READY"},  # No LastKeyPair field
    }
    mock_get_config.return_value = mock_config
    mock_validate.return_value = "READY"

    # Should raise SystemExit when no keypair provided and none stored
    with pytest.raises(SystemExit) as exc_info:
        launch_programmatic("test-project", instance_type="t3.medium", key_pair=None, assign_dns=False)
    assert exc_info.value.code == 4


@patch("devbox.launch.initialize_aws_clients")
@patch("devbox.launch.get_launch_config")
@patch("devbox.launch.validate_project_status")
@patch("devbox.launch.determine_ami")
@patch("devbox.launch.get_volume_info")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.launch_instance_in_azs")
@patch("devbox.launch.update_instance_status")
@patch("devbox.launch.display_instance_info")
def test_launch_programmatic_uses_last_instance_type(
    mock_display,
    mock_update,
    mock_launch_azs,
    mock_get_lt_info,
    mock_get_vol_info,
    mock_determine_ami,
    mock_validate,
    mock_get_config,
    mock_init_aws,
):
    """Test programmatic launch uses last instance type when none specified."""
    mock_aws = {"ec2": MagicMock(), "ec2_resource": MagicMock()}
    mock_init_aws.return_value = mock_aws

    # Mock config with LastInstanceType in the item
    mock_config = {
        "lt_ids": ["lt-12345"],
        "table": MagicMock(),
        "item": {
            "Status": "READY",
            "LastInstanceType": "m5.large",
            "LastKeyPair": "test-keypair",
        },
    }
    mock_get_config.return_value = mock_config
    mock_validate.return_value = "READY"
    mock_determine_ami.return_value = "ami-12345"
    mock_get_vol_info.return_value = ([], 0)
    mock_get_lt_info.return_value = {"lt-12345": {"name": "us-east-1a"}}

    mock_instance = MagicMock()
    mock_instance.meta.data = {"State": {"Name": "running"}}
    mock_launch_azs.return_value = (
        mock_instance,
        "i-12345",
        {"State": {"Name": "running"}},
    )

    # Call with instance_type=None
    launch_programmatic("test-project", instance_type=None, key_pair="test-keypair", assign_dns=False)

    # Verify launch_instance_in_azs was called with the last instance type
    mock_launch_azs.assert_called_once()
    call_args = mock_launch_azs.call_args[1]
    assert call_args["instance_type"] == "m5.large"


@patch("devbox.launch.initialize_aws_clients")
@patch("devbox.launch.get_launch_config")
@patch("devbox.launch.validate_project_status")
def test_launch_programmatic_no_instance_type_error(
    mock_validate, mock_get_config, mock_init_aws
):
    """Test programmatic launch raises error when no instance type specified and none stored."""
    mock_aws = {"ec2": MagicMock(), "ec2_resource": MagicMock()}
    mock_init_aws.return_value = mock_aws

    # Mock config without LastInstanceType in the item
    mock_config = {
        "lt_ids": ["lt-12345"],
        "table": MagicMock(),
        "item": {
            "Status": "READY",
            "LastKeyPair": "test-keypair",
        },  # No LastInstanceType field
    }
    mock_get_config.return_value = mock_config
    mock_validate.return_value = "READY"

    # Should raise SystemExit when no instance type provided and none stored
    with pytest.raises(SystemExit) as exc_info:
        launch_programmatic("test-project", instance_type=None, key_pair="test-keypair", assign_dns=False)
    assert exc_info.value.code == 4


@patch("devbox.launch.initialize_aws_clients")
@patch("devbox.launch.get_launch_config")
@patch("devbox.launch.validate_project_status")
@patch("devbox.launch.determine_ami")
@patch("devbox.launch.get_volume_info")
@patch("devbox.launch.get_launch_template_info")
@patch("devbox.launch.launch_instance_in_azs")
@patch("devbox.launch.update_instance_status")
@patch("devbox.launch.display_instance_info")
def test_launch_programmatic_uses_both_last_values(
    mock_display,
    mock_update,
    mock_launch_azs,
    mock_get_lt_info,
    mock_get_vol_info,
    mock_determine_ami,
    mock_validate,
    mock_get_config,
    mock_init_aws,
):
    """Test programmatic launch uses both last instance type and keypair when neither specified."""
    mock_aws = {"ec2": MagicMock(), "ec2_resource": MagicMock()}
    mock_init_aws.return_value = mock_aws

    # Mock config with both LastInstanceType and LastKeyPair
    mock_config = {
        "lt_ids": ["lt-12345"],
        "table": MagicMock(),
        "item": {
            "Status": "READY",
            "LastInstanceType": "c5.xlarge",
            "LastKeyPair": "my-keypair",
        },
    }
    mock_get_config.return_value = mock_config
    mock_validate.return_value = "READY"
    mock_determine_ami.return_value = "ami-12345"
    mock_get_vol_info.return_value = ([], 0)
    mock_get_lt_info.return_value = {"lt-12345": {"name": "us-east-1a"}}

    mock_instance = MagicMock()
    mock_instance.meta.data = {"State": {"Name": "running"}}
    mock_launch_azs.return_value = (
        mock_instance,
        "i-12345",
        {"State": {"Name": "running"}},
    )

    # Call with both instance_type=None and key_pair=None
    launch_programmatic("test-project", instance_type=None, key_pair=None, assign_dns=False)

    # Verify launch_instance_in_azs was called with both last values
    mock_launch_azs.assert_called_once()
    call_args = mock_launch_azs.call_args[1]
    assert call_args["instance_type"] == "c5.xlarge"
    assert call_args["key_name"] == "my-keypair"


# Test functions for username determination


def test_display_instance_info_determines_username():
    """Test that display_instance_info determines and stores SSH username."""
    mock_ec2_client = MagicMock()
    mock_table = MagicMock()

    # Mock instance response
    mock_response = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-12345",
                        "State": {"Name": "running"},
                        "PublicIpAddress": "1.2.3.4",
                        "InstanceType": "t3.medium",
                    }
                ]
            }
        ]
    }
    mock_ec2_client.describe_instances.return_value = mock_response

    # Mock DynamoDB response with no stored username but with AMI
    mock_table.get_item.return_value = {"Item": {"Username": "", "AMI": "ami-12345"}}

    # Mock AMI response for username determination
    mock_ec2_client.describe_images.return_value = {
        "Images": [
            {
                "Name": "ubuntu/images/hvm-ssd/ubuntu-20.04",
                "Description": "Canonical, Ubuntu, 20.04 LTS",
            }
        ]
    }

    display_instance_info(mock_ec2_client, "i-12345", "test-project", mock_table)

    # Verify that username was determined and updated in DynamoDB
    mock_table.update_item.assert_called_once()
    update_call = mock_table.update_item.call_args
    assert update_call[1]["UpdateExpression"] == "SET Username = :u"
    assert "ubuntu" in str(update_call[1]["ExpressionAttributeValues"])


def test_display_instance_info_uses_existing_username():
    """Test that display_instance_info uses existing stored username."""
    mock_ec2_client = MagicMock()
    mock_table = MagicMock()

    # Mock instance response
    mock_response = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-12345",
                        "State": {"Name": "running"},
                        "PublicIpAddress": "1.2.3.4",
                        "InstanceType": "t3.medium",
                    }
                ]
            }
        ]
    }
    mock_ec2_client.describe_instances.return_value = mock_response

    # Mock DynamoDB response with existing username
    mock_table.get_item.return_value = {"Item": {"Username": "ec2-user"}}

    display_instance_info(mock_ec2_client, "i-12345", "test-project", mock_table)

    # Verify that no username update was made since it already exists
    mock_table.update_item.assert_not_called()


def test_launch_programmatic_determines_username():
    """Test that launch_programmatic determines and stores SSH username when not set."""
    with patch("devbox.launch.initialize_aws_clients") as mock_init_aws, patch(
        "devbox.launch.get_launch_config"
    ) as mock_get_config, patch(
        "devbox.launch.validate_project_status"
    ) as mock_validate, patch(
        "devbox.launch.determine_ami"
    ) as mock_determine_ami, patch(
        "devbox.launch.get_volume_info"
    ) as mock_get_vol_info, patch(
        "devbox.launch.get_launch_template_info"
    ) as mock_get_lt_info, patch(
        "devbox.launch.launch_instance_in_azs"
    ) as mock_launch_azs, patch(
        "devbox.launch.update_instance_status"
    ) as mock_update, patch("devbox.launch.display_instance_info") as mock_display:
        mock_aws = {"ec2": MagicMock(), "ec2_resource": MagicMock()}
        mock_init_aws.return_value = mock_aws

        mock_table = MagicMock()
        mock_config = {
            "lt_ids": ["lt-12345"],
            "table": mock_table,
            "item": {
                "Status": "READY",
                "LastInstanceType": "t3.medium",
                "LastKeyPair": "test-key",
            },
        }
        mock_get_config.return_value = mock_config
        mock_validate.return_value = "READY"
        mock_determine_ami.return_value = "ami-12345"
        mock_get_vol_info.return_value = ([], 0)
        mock_get_lt_info.return_value = {"lt-12345": {"name": "us-east-1a"}}

        mock_instance = MagicMock()
        mock_instance.meta.data = {"State": {"Name": "running"}}
        mock_launch_azs.return_value = (
            mock_instance,
            "i-12345",
            {"State": {"Name": "running"}},
        )

        # Mock DynamoDB response with no username
        mock_table.get_item.return_value = {"Item": {"Username": ""}}

        # Mock AMI response for username determination
        mock_aws["ec2"].describe_images.return_value = {
            "Images": [{"Name": "amzn2-ami-hvm", "Description": "Amazon Linux 2"}]
        }

        launch_programmatic("test-project", assign_dns=False)

        # Verify that username was determined and stored
        mock_table.update_item.assert_called()
        update_calls = mock_table.update_item.call_args_list
        username_update = None
        for call in update_calls:
            if "Username" in call[1].get("UpdateExpression", ""):
                username_update = call
                break

        assert username_update is not None
        assert "ec2-user" in str(username_update[1]["ExpressionAttributeValues"])


# Test functions for main


@patch("devbox.launch.parse_arguments")
@patch("devbox.launch.launch_programmatic")
def test_main_success(mock_launch, mock_parse):
    """Test successful main execution."""
    mock_args = MagicMock()
    mock_args.project = "test-project"
    mock_args.instance_type = "t3.medium"
    mock_args.key_pair = "test-key"
    mock_args.volume_size = 100
    mock_args.base_ami = "ami-12345"
    mock_args.param_prefix = "/test"
    mock_args.assign_dns = True
    mock_args.dns_subdomain = None
    mock_parse.return_value = mock_args

    main()

    mock_launch.assert_called_once_with(
        project="test-project",
        instance_type="t3.medium",
        key_pair="test-key",
        volume_size=100,
        base_ami="ami-12345",
        param_prefix="/test",
        assign_dns=True,
        dns_subdomain=None,
    )


@patch("devbox.launch.parse_arguments")
def test_main_keyboard_interrupt(mock_parse):
    """Test main function with keyboard interrupt."""
    mock_parse.side_effect = KeyboardInterrupt()

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


@patch("devbox.launch.parse_arguments")
@patch("devbox.launch.launch_programmatic")
def test_main_resource_not_found_error(mock_launch, mock_parse):
    """Test main function with ResourceNotFoundError."""
    mock_parse.return_value = MagicMock()
    mock_launch.side_effect = ResourceNotFoundError("Resource not found")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


@patch("devbox.launch.parse_arguments")
@patch("devbox.launch.launch_programmatic")
def test_main_aws_client_error(mock_launch, mock_parse):
    """Test main function with AWSClientError."""
    mock_parse.return_value = MagicMock()
    mock_launch.side_effect = AWSClientError("AWS error")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 3


@patch("devbox.launch.parse_arguments")
@patch("devbox.launch.launch_programmatic")
def test_main_general_exception(mock_launch, mock_parse):
    """Test main function with general exception."""
    mock_parse.return_value = MagicMock()
    mock_launch.side_effect = Exception("General error")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 4
