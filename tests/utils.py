"""Test utilities for devbox tests."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

import boto3
from botocore.exceptions import ClientError


def create_ssm_parameter(ssm_client, name, value, param_type="String"):
    """Helper to create an SSM parameter."""
    ssm_client.put_parameter(Name=name, Value=value, Type=param_type, Overwrite=True)


def create_dynamodb_table(dynamodb, table_name, key_schema, attribute_definitions):
    """Helper to create a DynamoDB table."""
    return dynamodb.create_table(
        TableName=table_name,
        KeySchema=key_schema,
        AttributeDefinitions=attribute_definitions,
        BillingMode="PAY_PER_REQUEST",
    )


def setup_launch_test_environment(ssm_client, dynamodb, table_name):
    """Set up test environment for launch.py tests."""
    # Create SSM parameters
    launch_templates = ["lt-1234567890abcdef0"]
    create_ssm_parameter(
        ssm_client, "/devbox/launchTemplateIds", json.dumps(launch_templates)
    )
    create_ssm_parameter(ssm_client, "/devbox/snapshotTable", table_name)

    # Create DynamoDB table
    table = create_dynamodb_table(
        dynamodb,
        table_name,
        [{"AttributeName": "project", "KeyType": "HASH"}],
        [{"AttributeName": "project", "AttributeType": "S"}],
    )
    return table
