"""Unit tests for devbox utils module."""

import json
from datetime import datetime, timedelta, timezone
# Removed unused imports - using moto fixtures instead

import pytest
from botocore.exceptions import ClientError

from devbox.utils import (
    get_ssm_client,
    get_ec2_client,
    get_ec2_resource,
    get_dynamodb_resource,
    get_dynamodb_table,
    get_ssm_parameter,
    get_project_tag,
    format_timedelta,
    get_utc_now,
    determine_ssh_username,
    DevBoxError,
    ResourceNotFoundError,
    AWSClientError,
)


def test_get_ssm_client():
    client = get_ssm_client()
    assert client is not None
    assert hasattr(client, "get_parameter")


def test_get_ec2_client():
    client = get_ec2_client()
    assert client is not None
    assert hasattr(client, "describe_instances")


def test_get_ec2_resource():
    resource = get_ec2_resource()
    assert resource is not None
    assert hasattr(resource, "instances")


def test_get_dynamodb_resource():
    resource = get_dynamodb_resource()
    assert resource is not None
    assert hasattr(resource, "Table")


def test_get_dynamodb_table(mock_dynamodb):
    table_name = "test-table"

    mock_dynamodb.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    table = get_dynamodb_table(table_name)
    assert table is not None
    assert table.name == table_name


def test_get_ssm_parameter_success(mock_ssm):
    param_name = "/test/param"
    param_value = "test-value"

    mock_ssm.put_parameter(Name=param_name, Value=param_value, Type="String")

    result = get_ssm_parameter(param_name)
    assert result == param_value

def test_get_ssm_parameter_not_found_required(mock_ssm):
    param_name = "/nonexistent/param"

    with pytest.raises(ValueError, match="Failed to get parameter"):
        get_ssm_parameter(param_name, required=True)

def test_get_ssm_parameter_not_found_optional(mock_ssm):
    param_name = "/nonexistent/param"

    result = get_ssm_parameter(param_name, required=False)
    assert result == ""

def test_get_ssm_parameter_with_decryption(mock_ssm):
    param_name = "/test/secure-param"
    param_value = "secure-value"

    mock_ssm.put_parameter(Name=param_name, Value=param_value, Type="SecureString")

    result = get_ssm_parameter(param_name)
    assert result == param_value


class TestProjectTag:
    """Test project tag extraction."""

    @pytest.mark.parametrize(
        "tags,expected",
        [
            ([], ""),
            ([{"Key": "Name", "Value": "test"}], ""),
            ([{"Key": "Project", "Value": "my-project"}], "my-project"),
            (
                [
                    {"Key": "Name", "Value": "test"},
                    {"Key": "Project", "Value": "my-project"},
                    {"Key": "Environment", "Value": "dev"},
                ],
                "my-project",
            ),
            ([{"Key": "Project", "Value": ""}], ""),
            ([{"Key": "project", "Value": "wrong-case"}], ""),  # Case sensitive
        ],
    )
    def test_get_project_tag(self, tags, expected):
        """Test project tag extraction with various inputs."""
        result = get_project_tag(tags)
        assert result == expected

    def test_get_project_tag_multiple_project_tags(self):
        """Test with multiple Project tags (should return first one)."""
        tags = [
            {"Key": "Project", "Value": "first-project"},
            {"Key": "Project", "Value": "second-project"},
        ]
        result = get_project_tag(tags)
        assert result == "first-project"

    def test_get_project_tag_malformed_tags(self):
        """Test with malformed tag structures."""
        tags = [
            {"Key": "Project"},  # Missing Value
            {"Value": "my-project"},  # Missing Key
            {"Key": "Project", "Value": "good-project"},
        ]
        result = get_project_tag(tags)
        # First Project tag has no Value, so returns empty string
        assert result == ""


@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(seconds=30), "00:30"),
        (timedelta(minutes=5, seconds=30), "05:30"),
        (timedelta(hours=2, minutes=30, seconds=45), "02:30:45"),
        (timedelta(days=1), "1 day 00:00:00"),
        (timedelta(days=2, hours=3, minutes=15, seconds=30), "2 days 03:15:30"),
        (timedelta(days=1, hours=0, minutes=0, seconds=0), "1 day 00:00:00"),
        (timedelta(seconds=0), "00:00"),
        (timedelta(days=365, hours=23, minutes=59, seconds=59), "365 days 23:59:59"),
        (timedelta(hours=1, seconds=1), "01:00:01"),
        (timedelta(hours=23, minutes=59, seconds=59), "23:59:59"),
    ],
)
def test_format_timedelta(delta, expected):
    """Test timedelta formatting with various inputs."""
    result = format_timedelta(delta)
    assert result == expected


def test_get_utc_now():
    before = datetime.now(timezone.utc)
    result = get_utc_now()
    after = datetime.now(timezone.utc)

    assert isinstance(result, datetime)
    assert result.tzinfo == timezone.utc
    assert before <= result <= after
    assert (result - before).total_seconds() < 1


class TestExceptionClasses:
    """Test custom exception classes."""

    def test_devbox_error_inheritance(self):
        """Test DevBoxError is a proper Exception subclass."""
        error = DevBoxError("test message")
        assert isinstance(error, Exception)
        assert str(error) == "test message"

    def test_resource_not_found_error_inheritance(self):
        """Test ResourceNotFoundError inherits from DevBoxError."""
        error = ResourceNotFoundError("resource not found")
        assert isinstance(error, DevBoxError)
        assert isinstance(error, Exception)
        assert str(error) == "resource not found"

    def test_aws_client_error_basic(self):
        """Test AWSClientError with basic parameters."""
        error = AWSClientError("AWS error occurred")
        assert isinstance(error, DevBoxError)
        assert str(error) == "AWS error occurred"
        assert error.error_code is None
        assert error.original_exception is None

    def test_aws_client_error_with_details(self):
        """Test AWSClientError with all parameters."""
        original_exception = ClientError(
            error_response={
                "Error": {"Code": "NoSuchBucket", "Message": "Bucket not found"}
            },
            operation_name="GetObject",
        )

        error = AWSClientError(
            "Failed to access S3",
            error_code="NoSuchBucket",
            original_exception=original_exception,
        )

        assert str(error) == "Failed to access S3"
        assert error.error_code == "NoSuchBucket"
        assert error.original_exception == original_exception

    def test_aws_client_error_chaining(self):
        """Test exception chaining works properly."""
        original = ValueError("Original error")
        error = AWSClientError("Wrapped error", original_exception=original)

        # Should be able to access the original exception
        assert error.original_exception == original

        # Should maintain the inheritance chain
        assert isinstance(error, DevBoxError)
        assert isinstance(error, Exception)


class TestIntegrationScenarios:
    """Test integration scenarios combining multiple functions."""

    def test_ssm_parameter_with_json_content(self, mock_ssm):
        """Test retrieving JSON content from SSM parameter."""
        param_name = "/test/json-param"
        json_content = {"key": "value", "number": 42}

        mock_ssm.put_parameter(
            Name=param_name, Value=json.dumps(json_content), Type="String"
        )

        # No patch needed - moto handles this automatically
        result = get_ssm_parameter(param_name)
        parsed = json.loads(result)
        assert parsed == json_content

    def test_project_tag_extraction_realistic(self):
        """Test project tag extraction with realistic AWS tag structure."""
        tags = [
            {"Key": "Name", "Value": "devbox-myproject"},
            {"Key": "Project", "Value": "myproject"},
            {"Key": "Environment", "Value": "development"},
            {"Key": "Owner", "Value": "team@company.com"},
            {"Key": "CostCenter", "Value": "engineering"},
        ]

        result = get_project_tag(tags)
        assert result == "myproject"

    def test_timedelta_formatting_realistic_uptime(self):
        """Test formatting realistic server uptime scenarios."""
        # Short uptime
        short_uptime = timedelta(minutes=15, seconds=30)
        assert format_timedelta(short_uptime) == "15:30"

        # Medium uptime
        medium_uptime = timedelta(hours=8, minutes=45, seconds=12)
        assert format_timedelta(medium_uptime) == "08:45:12"

        # Long uptime
        long_uptime = timedelta(days=30, hours=12, minutes=30, seconds=45)
        assert format_timedelta(long_uptime) == "30 days 12:30:45"


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_ssm_parameter_client_error_handling(self, mock_ssm):
        """Test SSM parameter retrieval with client errors."""
        param_name = "/nonexistent/param"

        # Use moto's built-in behavior for non-existent parameters
        with pytest.raises(ValueError, match="Failed to get parameter"):
            get_ssm_parameter(param_name, required=True)

    def test_ssm_parameter_access_denied(self, mock_ssm):
        """Test SSM parameter retrieval with access denied scenario."""
        # This test simulates a more realistic error scenario
        # Since moto doesn't simulate malformed responses, we test parameter not found instead
        param_name = "/restricted/param"

        # Don't create the parameter, so it will not be found
        with pytest.raises(ValueError, match="Failed to get parameter"):
            get_ssm_parameter(param_name, required=True)

    def test_project_tag_none_input(self):
        """Test project tag extraction with None input."""
        result = get_project_tag(None)
        assert result == ""

    @pytest.mark.parametrize("invalid_delta", [None, "not a timedelta", 42, []])
    def test_format_timedelta_invalid_input(self, invalid_delta):
        """Test format_timedelta with invalid input types."""
        with pytest.raises(AttributeError):
            format_timedelta(invalid_delta)


class TestDetermineSSHUsername:
    """Test cases for determine_ssh_username function."""

    def test_amazon_linux_patterns(self):
        """Test Amazon Linux AMI patterns."""
        test_cases = [
            ("amzn2-ami-hvm", "", "ec2-user"),
            ("amazon-linux-2", "", "ec2-user"),
            ("al2-ami", "", "ec2-user"),
            ("amazonlinux", "", "ec2-user"),
            ("AMZN2-AMI-HVM", "", "ec2-user"),  # case insensitive
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Failed for {name}"

    def test_ubuntu_patterns(self):
        """Test Ubuntu AMI patterns."""
        test_cases = [
            ("ubuntu/images/hvm-ssd/ubuntu-focal", "", "ubuntu"),
            ("ubuntu-20.04", "", "ubuntu"),
            ("Ubuntu Server 22.04", "", "ubuntu"),
            ("UBUNTU-18.04", "", "ubuntu"),  # case insensitive
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Failed for {name}"

    def test_rhel_patterns(self):
        """Test RHEL AMI patterns."""
        test_cases = [
            ("RHEL-8.6", "", "ec2-user"),
            ("Red Hat Enterprise Linux 9", "", "ec2-user"),
            ("rhel-7.9-hvm", "", "ec2-user"),
            ("RED HAT ENTERPRISE LINUX", "", "ec2-user"),  # case insensitive
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Failed for {name}"

    def test_centos_patterns(self):
        """Test CentOS AMI patterns."""
        test_cases = [
            ("CentOS-7-x86_64", "", "centos"),
            ("centos-stream-9", "", "centos"),
            ("CentOS Linux 8", "", "centos"),
            ("CENTOS-8", "", "centos"),  # case insensitive
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Failed for {name}"

    def test_debian_patterns(self):
        """Test Debian AMI patterns."""
        test_cases = [
            ("debian-11-amd64", "", "admin"),
            ("Debian GNU/Linux 10", "", "admin"),
            ("debian-bullseye", "", "admin"),
            ("DEBIAN-12", "", "admin"),  # case insensitive
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Failed for {name}"

    def test_suse_patterns(self):
        """Test SUSE AMI patterns."""
        test_cases = [
            ("suse-sles-15", "", "ec2-user"),
            ("SUSE Linux Enterprise Server", "", "ec2-user"),
            ("opensuse-leap", "", "ec2-user"),
            ("SLES-12-SP5", "", "ec2-user"),  # case insensitive
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Failed for {name}"

    def test_rocky_linux_patterns(self):
        """Test Rocky Linux AMI patterns."""
        test_cases = [
            ("Rocky-8-ec2", "", "rocky"),
            ("rocky-linux-9", "", "rocky"),
            ("Rocky Linux 8.6", "", "rocky"),
            ("ROCKY-9.0", "", "rocky"),  # case insensitive
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Failed for {name}"

    def test_almalinux_patterns(self):
        """Test AlmaLinux AMI patterns."""
        test_cases = [
            ("AlmaLinux-8", "", "almalinux"),
            ("alma-linux-9", "", "almalinux"),
            ("AlmaLinux OS 8.6", "", "almalinux"),
            ("ALMA-9.0", "", "almalinux"),  # case insensitive
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Failed for {name}"

    def test_description_matching(self):
        """Test matching patterns in AMI descriptions."""
        test_cases = [
            ("custom-ami", "Built on Ubuntu 20.04", "ubuntu"),
            ("my-ami", "Amazon Linux 2 based image", "ec2-user"),
            ("company-ami", "CentOS 7 with custom packages", "centos"),
            ("", "RHEL 8.6 enterprise image", "ec2-user"),
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Failed for name='{name}', desc='{desc}'"

    def test_combined_name_and_description(self):
        """Test matching patterns across both name and description."""
        result = determine_ssh_username("custom-server", "Based on Ubuntu Server 22.04")
        assert result == "ubuntu"

        result = determine_ssh_username("my-rhel-ami", "Enterprise Linux distribution")
        assert result == "ec2-user"

    def test_unknown_patterns(self):
        """Test AMI patterns that don't match any known distributions."""
        test_cases = [
            ("", ""),
            ("custom-ami", ""),
            ("unknown-distro", "Some custom distribution"),
            ("freebsd-13", "FreeBSD operating system"),
            ("windows-server", "Microsoft Windows Server"),
        ]
        for name, desc in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == "", f"Expected empty string for unknown pattern: name='{name}', desc='{desc}'"

    def test_case_insensitivity(self):
        """Test that pattern matching is case insensitive."""
        test_cases = [
            ("UBUNTU-20.04", "UBUNTU SERVER IMAGE", "ubuntu"),
            ("amazon-linux", "AMAZON LINUX 2 AMI", "ec2-user"),
            ("centos-stream", "CENTOS STREAM 9", "centos"),
            ("debian-bullseye", "DEBIAN GNU/LINUX", "admin"),
        ]
        for name, desc, expected in test_cases:
            result = determine_ssh_username(name, desc)
            assert result == expected, f"Case insensitive test failed for {name}"

    def test_empty_inputs(self):
        """Test function with empty or None-like inputs."""
        assert determine_ssh_username("", "") == ""
        assert determine_ssh_username() == ""  # default parameters
