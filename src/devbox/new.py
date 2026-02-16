#!/usr/bin/env python3
"""Create new devbox projects without launching instances.

This module provides functionality to create new DevBox projects by setting up
the project metadata in DynamoDB without actually launching any EC2 instances.
This allows users to pre-configure projects with a base AMI and then launch
instances later using the 'launch' command.

The module validates that:
- The project name follows naming conventions
- The specified base AMI exists and is accessible
- The project doesn't already exist
- All required AWS resources (DynamoDB table, SSM parameters) are available

Example:
    Create a new project from command line:
        $ python -m devbox.new myproject --base-ami ami-12345678

    Create a new project programmatically:
        >>> from devbox.new import new_project_programmatic
        >>> new_project_programmatic("myproject", "ami-12345678")
"""
from __future__ import annotations

import sys
from typing import Dict, Any, Optional, TYPE_CHECKING

from botocore.exceptions import ClientError

# Import local modules
from . import utils
from .utils import (
    get_ssm_client,
    get_ec2_client,
    get_dynamodb_resource,
    ResourceNotFoundError,
    AWSClientError
)

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

    Returns:
        Dictionary containing AWS clients

    Raises:
        AWSClientError: If client initialization fails
    """
    try:
        return {
            "ssm": get_ssm_client(),
            "ec2": get_ec2_client(),
            "ddb": get_dynamodb_resource()
        }
    except Exception as e:
        raise AWSClientError(f"Failed to initialize AWS clients: {str(e)}") from e


def validate_ami_exists(ec2_client: Any, ami_id: str) -> Dict[str, Any]:
    """Validate that the specified AMI exists and get its details.

    This function checks if the AMI exists and is accessible to the current
    AWS account. It also retrieves metadata about the AMI including its
    architecture, virtualization type, and other properties needed for
    project creation.

    Args:
        ec2_client: Boto3 EC2 client instance
        ami_id: AMI ID to validate (must start with 'ami-')

    Returns:
        Dictionary containing AMI details including:
        - ImageId: The AMI ID
        - Architecture: CPU architecture (x86_64, arm64, etc.)
        - VirtualizationType: Virtualization type (hvm, paravirtual)
        - RootDeviceName: Root device name (/dev/sda1, /dev/xvda, etc.)
        - Name: AMI name (if available)
        - Description: AMI description (if available)
        - CreationDate: When the AMI was created

    Raises:
        ResourceNotFoundError: If AMI doesn't exist or is not accessible
        AWSClientError: For other AWS API errors (permissions, etc.)
    """
    try:
        response = ec2_client.describe_images(ImageIds=[ami_id])
        images = response.get('Images', [])

        if not images:
            raise ResourceNotFoundError(f"AMI {ami_id} not found")

        return images[0]
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')
        if error_code == 'InvalidAMIID.NotFound':
            raise ResourceNotFoundError(f"AMI {ami_id} not found")
        raise AWSClientError(f"Error validating AMI {ami_id}: {str(e)}") from e


def get_dynamodb_table(aws: Dict[str, Any], param_prefix: str) -> Any:
    """Get the DynamoDB table for storing project information.

    Retrieves the DynamoDB table name from SSM Parameter Store and returns
    a table resource object. The table name is expected to be stored in
    the parameter: {param_prefix}/snapshotTable

    Args:
        aws: Dictionary of AWS clients containing 'ssm' and 'ddb' keys
        param_prefix: Prefix for SSM parameters (e.g., '/devbox')

    Returns:
        DynamoDB Table resource object for project storage

    Raises:
        AWSClientError: If SSM parameter is missing, table doesn't exist,
                       or there are permission issues accessing the table
    """
    try:
        table_param = f"{param_prefix}/snapshotTable"
        table_name = aws["ssm"].get_parameter(Name=table_param, WithDecryption=True)["Parameter"]["Value"]
        return aws["ddb"].Table(table_name)
    except Exception as e:
        raise AWSClientError(f"Failed to get DynamoDB table: {str(e)}") from e


def check_project_exists(table: Any, project_name: str) -> Optional[Dict[str, Any]]:
    """Check if a project already exists in DynamoDB.

    Queries the DynamoDB table to see if a project with the given name
    already exists. This helps prevent duplicate project creation.

    Args:
        table: DynamoDB Table resource object
        project_name: Name of the project to check for existence

    Returns:
        Dictionary containing the existing project item if found, including:
        - project: Project name
        - Status: Current project status (READY, RUNNING, etc.)
        - AMI: Associated AMI ID
        - LastUpdated: Timestamp of last update
        Returns None if no project with this name exists.

    Raises:
        AWSClientError: For DynamoDB access errors or permission issues
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

    Creates a new project record in the DynamoDB table with metadata derived
    from the specified AMI. The project is created in "READY" status, meaning
    it's ready to have instances launched from it.

    Args:
        table: DynamoDB Table resource object
        project_name: Name of the new project (will be used as partition key)
        ami_info: Dictionary containing AMI metadata from describe_images call
        instance_type: Optional default instance type for future launches
        key_pair: Optional default SSH key pair name for future launches

    Raises:
        AWSClientError: If the DynamoDB put_item operation fails due to
                       permissions, table issues, or other AWS errors
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

        table.put_item(Item=item)
    except ClientError as e:
        raise AWSClientError(f"Failed to create project entry: {str(e)}") from e


def new_project_programmatic(
    project: str,
    base_ami: str,
    instance_type: Optional[str] = None,
    key_pair: Optional[str] = None,
    param_prefix: str = "/devbox"
) -> None:
    """Create a new devbox project without launching an instance.

    Args:
        project: Project name (alphanumeric and hyphens only)
        base_ami: Base AMI ID for the project
        instance_type: Optional default EC2 instance type for future launches
        key_pair: Optional default SSH key pair name for future launches
        param_prefix: Prefix for AWS Systems Manager Parameter Store keys

    Raises:
        ValueError: If inputs are invalid
        ResourceNotFoundError: If required resources are not found
        AWSClientError: For AWS API errors
    """
    # Validate project name
    if not project:
        raise ValueError("Project name cannot be empty")

    if not project.replace('-', '').replace('_', '').isalnum():
        raise ValueError("Project name must be alphanumeric with optional hyphens and underscores")

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

    # Validate param_prefix format
    if not param_prefix.startswith('/'):
        raise ValueError("Parameter prefix must start with '/'")

    if param_prefix.endswith('/'):
        raise ValueError("Parameter prefix cannot end with '/'")

    if '//' in param_prefix:
        raise ValueError("Parameter prefix cannot contain consecutive slashes")

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
    table = get_dynamodb_table(aws, param_prefix)

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
    """Entry point for standalone execution."""
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
