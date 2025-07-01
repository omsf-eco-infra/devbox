"""Common utilities for devbox-tf project."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError
from botocore.client import BaseClient
from boto3.resources.base import ServiceResource

if TYPE_CHECKING:
    from mypy_boto3_ssm.client import SSMClient as SSMClientType
    from mypy_boto3_ec2.client import EC2Client as EC2ClientType
    from mypy_boto3_ec2.service_resource import EC2ServiceResource as EC2ResourceType
    from mypy_boto3_dynamodb.service_resource import Table as DynamoDBTableType


def get_ssm_client() -> BaseClient:
    """Get an SSM client."""
    return boto3.client('ssm')


def get_ec2_client() -> BaseClient:
    """Get an EC2 client."""
    return boto3.client('ec2')


def get_ec2_resource() -> ServiceResource:
    """Get an EC2 resource."""
    return boto3.resource('ec2')


def get_dynamodb_resource() -> ServiceResource:
    """Get a DynamoDB resource."""
    return boto3.resource('dynamodb')


def get_dynamodb_table(table_name: str) -> Any:
    """Get a DynamoDB table resource.

    Args:
        table_name: Name of the DynamoDB table

    Returns:
        A DynamoDB Table resource
    """
    dynamodb = get_dynamodb_resource()
    return dynamodb.Table(table_name)


def get_ssm_parameter(parameter_name: str, required: bool = True) -> str:
    """Get a parameter from SSM Parameter Store.

    Args:
        parameter_name: Name of the parameter to fetch
        required: If True, raises an exception if parameter is not found

    Returns:
        The parameter value as a string

    Raises:
        ValueError: If parameter is not found and required is True
    """
    try:
        ssm = get_ssm_client()
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        return response['Parameter']['Value']
    except (ClientError, KeyError) as e:
        if required:
            raise ValueError(f"Failed to get parameter '{parameter_name}': {str(e)}")
        return ""


def get_project_tag(tags: List[Dict[str, str]]) -> str:
    """Extract the Project tag value from a list of tags.

    Args:
        tags: List of tag dictionaries with 'Key' and 'Value' keys

    Returns:
        The value of the 'Project' tag, or empty string if not found
    """
    if not tags:
        return ""
    return next((t.get('Value', '') for t in tags if t.get('Key') == 'Project'), "")


def format_timedelta(delta) -> str:
    """Format a timedelta as a human-readable string.

    Args:
        delta: A datetime.timedelta object

    Returns:
        Formatted string like "1 day, 2:30:45"
    """
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days} day{'s' if days > 1 else ''}")
    if hours > 0 or days > 0:
        parts.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
    else:
        parts.append(f"{minutes:02d}:{seconds:02d}")

    return " ".join(parts)


def get_utc_now() -> datetime:
    """Get the current UTC datetime.

    Returns:
        Current datetime in UTC timezone
    """
    return datetime.now(timezone.utc)


class DevBoxError(Exception):
    """Base exception for devbox operations."""
    pass


class ResourceNotFoundError(DevBoxError):
    """Raised when a requested resource is not found."""
    pass


class AWSClientError(DevBoxError):
    """Raised when an AWS API call fails."""
    def __init__(self, message: str, error_code: str = None, original_exception: Exception = None):
        self.error_code = error_code
        self.original_exception = original_exception
        super().__init__(message)
