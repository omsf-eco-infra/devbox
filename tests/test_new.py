"""Unit tests for devbox.new module."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from devbox.new import (
    initialize_aws_clients,
    validate_ami_exists,
    get_dynamodb_table,
    check_project_exists,
    create_project_entry,
    new_project_programmatic,
    main,
)
from devbox.utils import AWSClientError, ResourceNotFoundError


def _client_error(code: str, message: str, operation: str) -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name=operation,
    )


@patch("devbox.new.get_dynamodb_resource")
@patch("devbox.new.get_ec2_client")
@patch("devbox.new.get_ssm_client")
def test_initialize_aws_clients_success(mock_ssm, mock_ec2, mock_ddb):
    mock_ssm.return_value = MagicMock()
    mock_ec2.return_value = MagicMock()
    mock_ddb.return_value = MagicMock()

    aws = initialize_aws_clients()

    assert aws["ssm"] is mock_ssm.return_value
    assert aws["ec2"] is mock_ec2.return_value
    assert aws["ddb"] is mock_ddb.return_value


@patch("devbox.new.get_ssm_client")
def test_initialize_aws_clients_error(mock_ssm):
    mock_ssm.side_effect = Exception("boom")

    with pytest.raises(AWSClientError):
        initialize_aws_clients()


def test_validate_ami_exists_success():
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {
        "Images": [{"ImageId": "ami-12345678", "Architecture": "x86_64"}]
    }

    ami = validate_ami_exists(mock_ec2, "ami-12345678")

    assert ami["ImageId"] == "ami-12345678"


def test_validate_ami_exists_not_found_empty_images():
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {"Images": []}

    with pytest.raises(ResourceNotFoundError):
        validate_ami_exists(mock_ec2, "ami-12345678")


def test_validate_ami_exists_not_found_client_error():
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.side_effect = _client_error(
        "InvalidAMIID.NotFound", "AMI not found", "DescribeImages"
    )

    with pytest.raises(ResourceNotFoundError):
        validate_ami_exists(mock_ec2, "ami-12345678")


def test_validate_ami_exists_other_client_error():
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.side_effect = _client_error(
        "UnauthorizedOperation", "Access denied", "DescribeImages"
    )

    with pytest.raises(AWSClientError):
        validate_ami_exists(mock_ec2, "ami-12345678")


def test_get_dynamodb_table_success():
    mock_table = MagicMock()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "snapshot-table"}}
    mock_ddb = MagicMock()
    mock_ddb.Table.return_value = mock_table
    aws = {"ssm": mock_ssm, "ddb": mock_ddb}

    table = get_dynamodb_table(aws, "/devbox")

    assert table is mock_table
    mock_ssm.get_parameter.assert_called_once_with(
        Name="/devbox/snapshotTable", WithDecryption=True
    )
    mock_ddb.Table.assert_called_once_with("snapshot-table")


def test_get_dynamodb_table_error():
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = Exception("missing")
    aws = {"ssm": mock_ssm, "ddb": MagicMock()}

    with pytest.raises(AWSClientError):
        get_dynamodb_table(aws, "/devbox")


def test_check_project_exists_returns_item():
    mock_table = MagicMock()
    mock_table.get_item.return_value = {"Item": {"project": "test", "Status": "READY"}}

    item = check_project_exists(mock_table, "test")

    assert item == {"project": "test", "Status": "READY"}


def test_check_project_exists_returns_none_when_missing():
    mock_table = MagicMock()
    mock_table.get_item.return_value = {}

    item = check_project_exists(mock_table, "test")

    assert item is None


def test_check_project_exists_handles_resource_not_found():
    mock_table = MagicMock()
    mock_table.get_item.side_effect = _client_error(
        "ResourceNotFoundException", "No table", "GetItem"
    )

    item = check_project_exists(mock_table, "test")

    assert item is None


def test_check_project_exists_other_client_error():
    mock_table = MagicMock()
    mock_table.get_item.side_effect = _client_error(
        "AccessDeniedException", "Denied", "GetItem"
    )

    with pytest.raises(AWSClientError):
        check_project_exists(mock_table, "test")


@patch("devbox.new.utils.get_utc_now")
def test_create_project_entry_success(mock_now):
    fixed_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    mock_now.return_value = fixed_time
    mock_table = MagicMock()
    ami_info = {
        "ImageId": "ami-12345678",
        "VirtualizationType": "hvm",
        "Architecture": "x86_64",
        "RootDeviceName": "/dev/xvda",
        "Name": "Test AMI",
        "Description": "Test Description",
        "CreationDate": "2025-01-01T00:00:00.000Z",
    }

    create_project_entry(
        mock_table,
        "my-project",
        ami_info,
        instance_type="m5.large",
        key_pair="my-key",
    )

    put_item_args = mock_table.put_item.call_args[1]["Item"]
    assert put_item_args["project"] == "my-project"
    assert put_item_args["Status"] == "READY"
    assert put_item_args["AMI"] == "ami-12345678"
    assert put_item_args["LastUpdated"] == str(fixed_time)
    assert put_item_args["AMIName"] == "Test AMI"
    assert put_item_args["AMIDescription"] == "Test Description"
    assert put_item_args["AMICreationDate"] == "2025-01-01T00:00:00.000Z"
    assert put_item_args["LastInstanceType"] == "m5.large"
    assert put_item_args["LastKeyPair"] == "my-key"


def test_create_project_entry_client_error():
    mock_table = MagicMock()
    mock_table.put_item.side_effect = _client_error("AccessDeniedException", "Denied", "PutItem")

    with pytest.raises(AWSClientError):
        create_project_entry(mock_table, "my-project", {"ImageId": "ami-12345678"})


def test_create_project_entry_without_launch_defaults():
    mock_table = MagicMock()
    ami_info = {"ImageId": "ami-12345678"}

    create_project_entry(mock_table, "my-project", ami_info)

    put_item_args = mock_table.put_item.call_args[1]["Item"]
    assert "LastInstanceType" not in put_item_args
    assert "LastKeyPair" not in put_item_args


@patch("devbox.new.create_project_entry")
@patch("devbox.new.check_project_exists")
@patch("devbox.new.get_dynamodb_table")
@patch("devbox.new.validate_ami_exists")
@patch("devbox.new.initialize_aws_clients")
def test_new_project_programmatic_success(
    mock_init_aws,
    mock_validate_ami,
    mock_get_table,
    mock_check_exists,
    mock_create_entry,
):
    aws = {"ec2": MagicMock()}
    mock_init_aws.return_value = aws
    mock_validate_ami.return_value = {"ImageId": "ami-12345678", "Architecture": "x86_64"}
    mock_get_table.return_value = MagicMock()
    mock_check_exists.return_value = None

    new_project_programmatic(
        project="my-project",
        base_ami="ami-12345678",
        instance_type="m5.large",
        key_pair="my-key",
        param_prefix="/devbox",
    )

    mock_init_aws.assert_called_once()
    mock_validate_ami.assert_called_once_with(aws["ec2"], "ami-12345678")
    mock_get_table.assert_called_once_with(aws, "/devbox")
    mock_check_exists.assert_called_once()
    mock_create_entry.assert_called_once_with(
        table=mock_get_table.return_value,
        project_name="my-project",
        ami_info=mock_validate_ami.return_value,
        instance_type="m5.large",
        key_pair="my-key",
    )


def test_new_project_programmatic_invalid_project_name():
    with pytest.raises(ValueError, match="Project name cannot be empty"):
        new_project_programmatic(project="", base_ami="ami-12345678")


def test_new_project_programmatic_invalid_ami():
    with pytest.raises(ValueError, match="Base AMI must be a valid AMI ID"):
        new_project_programmatic(project="my-project", base_ami="not-an-ami")


def test_new_project_programmatic_invalid_param_prefix():
    with pytest.raises(ValueError, match="Parameter prefix must start with '/'"):
        new_project_programmatic(
            project="my-project", base_ami="ami-12345678", param_prefix="devbox"
        )


@patch("devbox.new.create_project_entry")
@patch("devbox.new.check_project_exists")
@patch("devbox.new.get_dynamodb_table")
@patch("devbox.new.validate_ami_exists")
@patch("devbox.new.initialize_aws_clients")
def test_new_project_programmatic_existing_project(
    mock_init_aws,
    mock_validate_ami,
    mock_get_table,
    mock_check_exists,
    mock_create_entry,
):
    mock_init_aws.return_value = {"ec2": MagicMock()}
    mock_validate_ami.return_value = {"ImageId": "ami-12345678"}
    mock_get_table.return_value = MagicMock()
    mock_check_exists.return_value = {"project": "my-project", "Status": "READY"}

    with pytest.raises(ValueError, match="already exists"):
        new_project_programmatic(project="my-project", base_ami="ami-12345678")

    mock_create_entry.assert_not_called()


@patch("argparse.ArgumentParser.parse_args")
@patch("devbox.new.new_project_programmatic")
def test_main_success(mock_new_project, mock_parse_args):
    mock_args = MagicMock()
    mock_args.project = "my-project"
    mock_args.base_ami = "ami-12345678"
    mock_args.instance_type = "m5.large"
    mock_args.key_pair = "my-key"
    mock_args.param_prefix = "/devbox"
    mock_parse_args.return_value = mock_args

    main()

    mock_new_project.assert_called_once_with(
        project="my-project",
        base_ami="ami-12345678",
        instance_type="m5.large",
        key_pair="my-key",
        param_prefix="/devbox",
    )


@patch("argparse.ArgumentParser.parse_args")
def test_main_keyboard_interrupt(mock_parse_args):
    mock_parse_args.side_effect = KeyboardInterrupt()

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


@patch("argparse.ArgumentParser.parse_args")
@patch("devbox.new.new_project_programmatic")
def test_main_value_error(mock_new_project, mock_parse_args):
    mock_args = MagicMock()
    mock_args.project = "my-project"
    mock_args.base_ami = "ami-12345678"
    mock_args.instance_type = None
    mock_args.key_pair = None
    mock_args.param_prefix = "/devbox"
    mock_parse_args.return_value = mock_args
    mock_new_project.side_effect = ValueError("Invalid")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


@patch("argparse.ArgumentParser.parse_args")
@patch("devbox.new.new_project_programmatic")
def test_main_resource_not_found_error(mock_new_project, mock_parse_args):
    mock_args = MagicMock()
    mock_args.project = "my-project"
    mock_args.base_ami = "ami-12345678"
    mock_args.instance_type = None
    mock_args.key_pair = None
    mock_args.param_prefix = "/devbox"
    mock_parse_args.return_value = mock_args
    mock_new_project.side_effect = ResourceNotFoundError("AMI not found")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


@patch("argparse.ArgumentParser.parse_args")
@patch("devbox.new.new_project_programmatic")
def test_main_aws_client_error(mock_new_project, mock_parse_args):
    mock_args = MagicMock()
    mock_args.project = "my-project"
    mock_args.base_ami = "ami-12345678"
    mock_args.instance_type = None
    mock_args.key_pair = None
    mock_args.param_prefix = "/devbox"
    mock_parse_args.return_value = mock_args
    mock_new_project.side_effect = AWSClientError("AWS failed")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 3


@patch("argparse.ArgumentParser.parse_args")
@patch("devbox.new.new_project_programmatic")
def test_main_unexpected_exception(mock_new_project, mock_parse_args):
    mock_args = MagicMock()
    mock_args.project = "my-project"
    mock_args.base_ami = "ami-12345678"
    mock_args.instance_type = None
    mock_args.key_pair = None
    mock_args.param_prefix = "/devbox"
    mock_parse_args.return_value = mock_args
    mock_new_project.side_effect = Exception("boom")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 4
