"""Test utilities for devbox tests."""

from __future__ import annotations

import json


def setup_launch_test_environment(ssm_client, dynamodb, table_name):
    """Set up test environment for launch.py tests."""
    # Create SSM parameters
    launch_templates = ["lt-1234567890abcdef0"]
    ssm_client.put_parameter(
        Name="/devbox/launchTemplateIds",
        Value=json.dumps(launch_templates),
        Type="String",
        Overwrite=True,
    )
    ssm_client.put_parameter(
        Name="/devbox/snapshotTable",
        Value=table_name,
        Type="String",
        Overwrite=True,
    )

    # Create DynamoDB table
    table = dynamodb.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "project", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "project", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return table
