#!/usr/bin/env python3
"""Create new DevBox projects without launching instances.

This module creates project metadata in DynamoDB without starting EC2
instances. It supports both the main CLI integration and a standalone
``python -m devbox.new`` entrypoint used for debugging.
"""
from __future__ import annotations

import sys
from typing import Dict, Any, Optional, TYPE_CHECKING

from botocore.exceptions import ClientError

from . import utils
from .utils import AWSClientError, ResourceNotFoundError, normalize_param_prefix

if TYPE_CHECKING:
    from mypy_boto3_ec2.client import EC2Client
    from mypy_boto3_dynamodb.service_resource import Table as DynamoDBTable
    from mypy_boto3_ssm.client import SSMClient

# Type aliases for runtime use
SSMClient = Any
DynamoDBTable = Any
EC2Client = Any


def initialize_aws_clients() -> Dict[str, Any]:
    """Initialize AWS clients needed for project creation.

    Returns
    -------
    dict[str, Any]
        Mapping of AWS client and resource handles used during project
        creation.

    Raises
    ------
    AWSClientError
        Raised when any required client or resource cannot be initialized.
    """
    try:
        return {
            "ssm": utils.get_ssm_client(),
            "ec2": utils.get_ec2_client(),
            "ddb": utils.get_dynamodb_resource()
        }
    except Exception as e:
        raise AWSClientError(f"Failed to initialize AWS clients: {str(e)}") from e


def validate_ami_exists(ec2_client: Any, ami_id: str) -> Dict[str, Any]:
    """Validate that the specified AMI exists and return its metadata.

    Parameters
    ----------
    ec2_client : Any
        Boto3 EC2 client instance.
    ami_id : str
        AMI identifier to validate. Expected to start with ``"ami-"``.

    Returns
    -------
    dict[str, Any]
        AMI metadata from ``describe_images`` for the requested image.

    Raises
    ------
    ResourceNotFoundError
        Raised when the AMI does not exist or is not accessible.
    AWSClientError
        Raised for other AWS API failures, such as permission errors.
    """
    try:
        image = utils.get_image(ami_id, ec2_client=ec2_client)
        if image is None:
            raise ResourceNotFoundError(f"AMI {ami_id} not found")
        return image
    except ClientError as e:
        raise AWSClientError(f"Error validating AMI {ami_id}: {str(e)}") from e


def check_project_exists(table: Any, project_name: str) -> Optional[Dict[str, Any]]:
    """Check whether a project already exists in DynamoDB.

    Parameters
    ----------
    table : Any
        DynamoDB table resource object.
    project_name : str
        Project name to query.

    Returns
    -------
    dict[str, Any] or None
        Existing project item when present; otherwise ``None``.

    Raises
    ------
    AWSClientError
        Raised for DynamoDB access errors other than a missing resource.
    """
    try:
        response = table.get_item(Key={"project": project_name})
        return response.get("Item")
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "ResourceNotFoundException":
            return None
        raise AWSClientError(f"Error checking project existence: {str(e)}") from e


def create_project_entry(
    table: Any,
    project_name: str,
    ami_info: Dict[str, Any],
    instance_type: Optional[str] = None,
    key_pair: Optional[str] = None,
) -> None:
    """Create a new project entry in DynamoDB.

    Parameters
    ----------
    table : Any
        DynamoDB table resource object.
    project_name : str
        Name of the new project. This becomes the table partition key.
    ami_info : dict[str, Any]
        AMI metadata returned from ``describe_images``.
    instance_type : str, optional
        Default EC2 instance type for future launches.
    key_pair : str, optional
        Default SSH key pair for future launches.

    Raises
    ------
    ValueError
        Raised when a project with the same name already exists.
    AWSClientError
        Raised when the ``put_item`` request fails.
    """
    try:
        item = {
            "project": project_name,
            "Status": "READY",
            "AMI": ami_info["ImageId"],
            "VirtualizationType": ami_info.get("VirtualizationType", "hvm"),
            "Architecture": ami_info.get("Architecture", "x86_64"),
            "RootDeviceName": ami_info.get("RootDeviceName", "/dev/sda1"),
            "LastUpdated": str(utils.get_utc_now()),
            "State": "ready"
        }

        # Add optional AMI metadata
        if "Name" in ami_info:
            item["AMIName"] = ami_info["Name"]
        if "Description" in ami_info:
            item["AMIDescription"] = ami_info["Description"]
        if "CreationDate" in ami_info:
            item["AMICreationDate"] = ami_info["CreationDate"]
        if instance_type:
            item["LastInstanceType"] = instance_type
        if key_pair:
            item["LastKeyPair"] = key_pair

        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(#project)",
            ExpressionAttributeNames={"#project": "project"},
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "ConditionalCheckFailedException":
            raise ValueError(f"Project '{project_name}' already exists") from e
        raise AWSClientError(f"Failed to create project entry: {str(e)}") from e


def new_project_programmatic(
    project: str,
    base_ami: str,
    instance_type: Optional[str] = None,
    key_pair: Optional[str] = None,
    param_prefix: str = "/devbox"
) -> None:
    """Create a new DevBox project without launching an instance.

    Parameters
    ----------
    project : str
        Project name. Must satisfy the command's naming constraints.
    base_ami : str
        Base AMI identifier for the project.
    instance_type : str, optional
        Default EC2 instance type for future launches.
    key_pair : str, optional
        Default SSH key pair name for future launches.
    param_prefix : str, default="/devbox"
        Prefix for AWS Systems Manager Parameter Store keys.

    Raises
    ------
    ValueError
        Raised when the project name or AMI identifier is invalid, or when
        the project already exists.
    ResourceNotFoundError
        Raised when required AWS resources cannot be found.
    AWSClientError
        Raised for AWS API failures.
    """
    # Validate project name
    if not project:
        raise ValueError("Project name cannot be empty")

    if not project.replace('-', '').isalnum():
        raise ValueError("Project name must be alphanumeric with optional hyphens")

    if len(project) < 1 or len(project) > 50:
        raise ValueError("Project name must be between 1 and 50 characters")

    if project.startswith('-') or project.endswith('-'):
        raise ValueError("Project name cannot start or end with hyphens")

    if '--' in project:
        raise ValueError("Project name cannot contain consecutive hyphens")

    # Validate base AMI format
    if not base_ami:
        raise ValueError("Base AMI cannot be empty")

    if not base_ami.startswith('ami-'):
        raise ValueError("Base AMI must be a valid AMI ID starting with 'ami-'")

    if len(base_ami) < 12:  # ami- + at least 8 characters
        raise ValueError("Base AMI ID appears to be too short to be valid")

    param_prefix = normalize_param_prefix(param_prefix)

    print(f"Creating new project: {project}")
    print(f"Base AMI: {base_ami}")
    if instance_type:
        print(f"Default instance type: {instance_type}")
    if key_pair:
        print(f"Default key pair: {key_pair}")
    print(f"Parameter prefix: {param_prefix}")

    # Initialize AWS clients
    print("Initializing AWS clients...")
    aws = initialize_aws_clients()

    # Validate AMI exists and get its details
    print("Validating AMI...")
    ami_info = validate_ami_exists(aws["ec2"], base_ami)
    print(f"AMI validated: {ami_info.get('Name', 'Unknown Name')} ({ami_info.get('Architecture', 'unknown arch')})")

    # Get DynamoDB table
    print("Getting DynamoDB table...")
    try:
        table_name = utils.get_ssm_parameter(
            f"{param_prefix}/snapshotTable", ssm_client=aws["ssm"]
        )
        table = utils.get_dynamodb_table(table_name, dynamodb_resource=aws["ddb"])
    except Exception as e:
        raise AWSClientError(f"Failed to get DynamoDB table: {str(e)}") from e

    # Check if project already exists
    print("Checking if project already exists...")
    existing_project = check_project_exists(table, project)
    if existing_project:
        raise ValueError(f"Project '{project}' already exists with status: {existing_project.get('Status', 'unknown')}")

    # Create project entry
    print("Creating project entry...")
    create_project_entry(
        table=table,
        project_name=project,
        ami_info=ami_info,
        instance_type=instance_type,
        key_pair=key_pair,
    )

    print(f"✅ Project '{project}' created successfully!")
    print("   Status: READY")
    print(f"   Base AMI: {base_ami}")
    print("   You can now launch instances for this project using the 'launch' command.")


def main():
    """Run the standalone ``python -m devbox.new`` entrypoint."""
    import argparse

    try:
        parser = argparse.ArgumentParser(description="Create a new devbox project")
        parser.add_argument("project", help="Project name")
        parser.add_argument("--base-ami", required=True, help="Base AMI ID")
        parser.add_argument(
            "--instance-type",
            help="Default EC2 instance type for future launches",
        )
        parser.add_argument(
            "--key-pair",
            help="Default SSH key pair name for future launches",
        )
        parser.add_argument("--param-prefix", default="/devbox", help="SSM parameter prefix")

        args = parser.parse_args()

        new_project_programmatic(
            project=args.project,
            base_ami=args.base_ami,
            instance_type=args.instance_type,
            key_pair=args.key_pair,
            param_prefix=args.param_prefix
        )

    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except (ValueError, ResourceNotFoundError) as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(2)
    except AWSClientError as e:
        print(f"AWS Error: {str(e)}", file=sys.stderr)
        if hasattr(e, 'error_code'):
            print(f"Error Code: {e.error_code}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Unexpected error: {str(e)}", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()
