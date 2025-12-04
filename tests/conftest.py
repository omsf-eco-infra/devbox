"""Pytest configuration and fixtures for devbox tests."""

import os
from unittest.mock import patch, MagicMock

import boto3
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def mock_ssm():
    """Mock SSM client."""
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


@pytest.fixture
def mock_dynamodb():
    """Mock DynamoDB resource."""
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture
def mock_ec2():
    """Mock EC2 client and resource."""
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        ec2_resource = boto3.resource("ec2", region_name="us-east-1")
        yield ec2, ec2_resource


@pytest.fixture
def mock_cli_runner():
    """CLI runner for testing Click commands."""
    from click.testing import CliRunner

    return CliRunner()
