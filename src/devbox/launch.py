#!/usr/bin/env python3
"""Launch script for creating and managing devbox EC2 instances."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, Any, Optional, Tuple, List, TYPE_CHECKING

from botocore.exceptions import ClientError

# Import local modules
from . import utils
from .utils import (
    get_ssm_client,
    get_ec2_client,
    get_ec2_resource,
    get_dynamodb_resource,
    get_dynamodb_table,
    get_ssm_parameter,
    ResourceNotFoundError,
    AWSClientError
)

if TYPE_CHECKING:
    from mypy_boto3_ec2.client import EC2Client
    from mypy_boto3_ec2.service_resource import EC2ServiceResource, Instance
    from mypy_boto3_dynamodb.service_resource import Table as DynamoDBTable
    from mypy_boto3_ssm.client import SSMClient

# Type aliases for runtime use
SSMClient = Any
DynamoDBTable = Any
EC2Client = Any
EC2ServiceResource = Any


def make_parser() -> argparse.ArgumentParser:
    """Create and return the argument parser for the launch script.

    Returns:
        Configured ArgumentParser instance with all command line arguments
    """
    parser = argparse.ArgumentParser(
        description="Launch a persistent devbox EC2 instance with attached EBS volume"
    )

    # Required arguments
    required = parser.add_argument_group('required arguments')
    required.add_argument("--project",
                         required=True,
                         help="Project name (alphanumeric and hyphens only)")
    required.add_argument("--instance-type",
                         required=True,
                         help="EC2 instance type (e.g., t3.medium, m5.large)")
    required.add_argument("--key-pair",
                         required=True,
                         help="Name of the EC2 Key Pair for SSH access")

    # Optional arguments
    parser.add_argument("--base-ami",
                       help="Base AMI ID (only required for new projects)")
    parser.add_argument("--param-prefix",
                       default="/devbox",
                       help="Prefix for AWS Systems Manager Parameter Store keys")
    parser.add_argument("--volume-size",
                       type=int,
                       default=0,
                       help="Minimum size (GiB) for the root EBS volume")

    return parser


def get_project_snapshot(
    table: Any,
    project_name: str
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Get the snapshot information for a project from DynamoDB.

    Args:
        table: The DynamoDB table resource
        project_name: Name of the project to look up

    Returns:
        A tuple of (item_dict, error_message).
        On success: (item_dict, None)
        On error: ({}, error_message)

    Note:
        Returns an empty dict and error message if the project doesn't exist
    """
    try:
        resp = table.get_item(Key={"project": project_name})
        item = resp.get("Item", {})
        if not item:
            # Return a nonexistent project entry instead of an error
            return {"project": project_name, "Status": "nonexistent"}, None
        return item, None
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "ResourceNotFoundException":
            return {"project": project_name, "Status": "nonexistent"}, None
        return {}, f"DynamoDB error: {str(e)}"


def get_volume_info(
    ec2: Any,
    image_id: str,
    min_volume_size: int = 0
) -> Tuple[List[Dict[str, Any]], int]:
    """Get and validate volume information for an AMI.

    Args:
        ec2: EC2 client
        image_id: ID of the AMI to get volume info for
        min_volume_size: Minimum size (GiB) for the largest volume

    Returns:
        Tuple of (volumes, largest_volume_size) where:
        - volumes: List of volume mappings with updated sizes if needed
        - largest_volume_size: Size of the largest volume (GiB)

    Raises:
        AWSClientError: If there's an error fetching image details
        ValueError: If the image is not found or invalid
    """
    try:
        resp = ec2.describe_images(ImageIds=[image_id])
    except ClientError as e:
        raise AWSClientError(
            f"Error fetching image details for {image_id}",
            error_code=e.response.get("Error", {}).get("Code"),
            original_exception=e
        )

    if not resp.get("Images"):
        raise ValueError(f"AMI {image_id} not found")

    image = resp["Images"][0]
    volumes = image.get("BlockDeviceMappings", []).copy()

    # Find the largest volume
    largest_volume = None
    largest_volume_size = 0

    for vol in volumes:
        if "Ebs" in vol and "VolumeSize" in vol["Ebs"]:
            size = vol["Ebs"]["VolumeSize"]
            if size > largest_volume_size:
                largest_volume_size = size
                largest_volume = vol

    # Update volumes if needed
    if min_volume_size > 0 and largest_volume_size < min_volume_size:
        if largest_volume:
            print(
                f"Increasing volume size from {largest_volume_size} GiB to {min_volume_size} GiB"
            )
            largest_volume["Ebs"]["VolumeSize"] = min_volume_size
            largest_volume_size = min_volume_size
            # Ensure volume type is set to a modern type
            if "VolumeType" not in largest_volume["Ebs"]:
                largest_volume["Ebs"]["VolumeType"] = "gp3"
        else:
            print(f"Creating new volume of size {min_volume_size} GiB")
            volumes.append({
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": min_volume_size,
                    "VolumeType": "gp3",  # Using gp3 as it's the latest generation
                    "Encrypted": True,
                    "DeleteOnTermination": True
                },
            })
            largest_volume_size = min_volume_size

    return volumes, largest_volume_size if largest_volume else 0


def get_launch_template_info(
    ec2: Any,
    lt_ids: List[str]
) -> Dict[str, Dict[str, str]]:
    """Get availability zone information for launch templates.

    Args:
        ec2: EC2 client
        lt_ids: List of launch template IDs

    Returns:
        Dictionary mapping launch template IDs to AZ info with keys:
        - name: AZ name (e.g., 'us-east-1a')
        - index: AZ index (e.g., '1' for az1)

    Note:
        If AZ information cannot be determined, defaults to generic az-{index} names
    """
    az_info = {}
    for idx, lt_id in enumerate(lt_ids, 1):
        # Set default values in case we can't determine AZ
        az_name = f"az-{idx}"
        az_index = str(idx)

        try:
            # Get launch template details
            lt_desc = ec2.describe_launch_templates(LaunchTemplateIds=[lt_id])
            if lt_desc.get("LaunchTemplates"):
                lt_name = lt_desc["LaunchTemplates"][0].get("LaunchTemplateName", "")
                if lt_name:
                    # Extract AZ from template name (e.g., 'devbox-us-east-1a-template' -> 'us-east-1a')
                    import re
                    az_pattern = r'([a-z]{2}-[a-z]+-\d+[a-z])'
                    match = re.search(az_pattern, lt_name)
                    if match:
                        az_name = match.group(1)
                        # Extract index from AZ suffix (a=1, b=2, etc.)
                        az_suffix = az_name[-1]
                        if az_suffix.isalpha():
                            az_index = str(ord(az_suffix.lower()) - ord('a') + 1)

            # Get subnet info from launch template
            lt_versions = ec2.describe_launch_template_versions(
                LaunchTemplateId=lt_id,
                Versions=["$Latest"]
            )

            if lt_versions.get("LaunchTemplateVersions"):
                lt_data = lt_versions["LaunchTemplateVersions"][0].get("LaunchTemplateData", {})
                network_interfaces = lt_data.get("NetworkInterfaces", [])

                # Try to get subnet ID from network interfaces
                subnet_id = None
                if network_interfaces and isinstance(network_interfaces, list):
                    subnet_id = network_interfaces[0].get("SubnetId")

                # If no subnet in network interfaces, check the top-level SubnetId
                if not subnet_id and "SubnetId" in lt_data:
                    subnet_id = lt_data["SubnetId"]

                # Get AZ from subnet if we have a subnet ID
                if subnet_id:
                    try:
                        subnet_desc = ec2.describe_subnets(SubnetIds=[subnet_id])
                        if subnet_desc.get("Subnets"):
                            az_name = subnet_desc["Subnets"][0].get("AvailabilityZone", az_name)
                    except ClientError:
                        pass

        except ClientError as e:
            print(f"Warning: Error processing launch template {lt_id}: {e}")

        az_info[lt_id] = {"name": az_name, "index": az_index}

    return az_info


def launch_instance(
    ec2: Any,
    ec2_resource: Any,
    launch_template_id: str,
    image_id: str,
    instance_type: str,
    key_name: str,
    volumes: List[Dict[str, Any]],
    project: str,
    az_name: str,
) -> Tuple[Optional[Any], Optional[str], Optional[Exception]]:
    """Attempt to launch an EC2 instance in the specified AZ.

    Args:
        ec2: EC2 client
        ec2_resource: EC2 service resource
        launch_template_id: ID of the launch template to use
        image_id: AMI ID to launch
        instance_type: EC2 instance type
        key_name: Name of the key pair for SSH access
        volumes: List of volume mappings
        project: Project name for tagging
        az_name: Availability zone name for logging

    Returns:
        Tuple of (instance, instance_id, error) where:
        - instance: EC2.Instance object if successful, None otherwise
        - instance_id: Instance ID if successful, None otherwise
        - error: Exception if an error occurred, None otherwise
    """
    try:
        print(f"Attempting to launch instance in {az_name}...")

        # Prepare common tags for all resources
        common_tags = [
            {"Key": "Name", "Value": f"devbox-{project}"},
            {"Key": "Project", "Value": project},
            {"Key": "InstanceType", "Value": instance_type},
            {"Key": "Environment", "Value": "devbox"},
            {"Key": "ManagedBy", "Value": "devbox-cli"},
            {"Key": "LaunchTemplateId", "Value": launch_template_id},
            {"Key": "AvailabilityZone", "Value": az_name}
        ]

        # Launch the instance
        resp = ec2.run_instances(
            LaunchTemplate={
                "LaunchTemplateId": launch_template_id,
                "Version": "$Latest",
            },
            ImageId=image_id,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            KeyName=key_name,
            BlockDeviceMappings=volumes,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": common_tags
                },
                {
                    "ResourceType": "volume",
                    "Tags": common_tags + [
                        {"Key": "Application", "Value": "devbox"},
                        {"Key": "DeleteOnTermination", "Value": "true"},
                        {"Key": "Backup", "Value": "true"}
                    ]
                },
                {
                    "ResourceType": "network-interface",
                    "Tags": common_tags
                }
            ],
        )

        # Get instance details from response
        instance_dct = resp["Instances"][0]
        instance_id = instance_dct["InstanceId"]
        instance = ec2_resource.Instance(instance_id)

        print(f"Instance launched in {az_name}: {instance_id}. Waiting for running state...")
        return instance, instance_id, None

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "UnknownError")
        error_message = e.response.get("Error", {}).get("Message", str(e))
        print(f"Failed to launch in {az_name}: {error_code} - {error_message}")
        return None, None, e
    except Exception as e:
        print(f"Unexpected error launching instance: {str(e)}")
        return None, None, e


def update_instance_status(
    table: Any,
    project: str,
    status: str,
    instance_id: str,
    image_id: str,
    instance_info: Optional[Dict[str, Any]] = None,
) -> None:
    """Update the instance status in DynamoDB.

    Args:
        table: DynamoDB table resource
        project: Project name
        status: Current status ("nonexistent", "READY", or "LAUNCHING")
        instance_id: ID of the EC2 instance
        image_id: AMI ID used for the instance
        instance_info: Optional instance metadata

    Raises:
        ValueError: If the status is unexpected
        ClientError: If there's an error updating DynamoDB
    """
    try:
        if status == "nonexistent" and instance_info:
            # Create a new item for this project
            item = {
                "project": project,
                "Status": "RUNNING",
                "AMI": image_id,
                "InstanceId": instance_id,
                "VirtualizationType": instance_info.get("VirtualizationType"),
                "Architecture": instance_info.get("Architecture"),
                "VolumeCount": len(instance_info.get("BlockDeviceMappings", [])),
                "RootDeviceName": instance_info.get("RootDeviceName"),
                "InstanceType": instance_info.get("InstanceType"),
                "LaunchTime": str(instance_info.get("LaunchTime", "")),
                "LastUpdated": str(utils.get_utc_now()),
                "State": "running"
            }

            # Add any additional instance info that might be useful
            if "State" in instance_info:
                item["State"] = instance_info["State"].get("Name", "unknown")
            if "PrivateIpAddress" in instance_info:
                item["PrivateIp"] = instance_info["PrivateIpAddress"]
            if "PublicIpAddress" in instance_info:
                item["PublicIp"] = instance_info["PublicIpAddress"]

            table.put_item(Item=item)

        elif status == "LAUNCHING":
            # Check if project already exists
            resp = table.get_item(Key={"project": project})
            existing_item = resp.get("Item", {})

            # Create item with launching status, preserving existing values
            item = existing_item.copy() if existing_item else {}
            item.update({
                "project": project,
                "Status": "LAUNCHING",
                "AMI": image_id,
                "InstanceId": instance_id,
                "LastUpdated": str(utils.get_utc_now()),
                "State": "launching"
            })

            # Add any additional instance info that might be useful
            if instance_info:
                if "VirtualizationType" in instance_info:
                    item["VirtualizationType"] = instance_info["VirtualizationType"]
                if "Architecture" in instance_info:
                    item["Architecture"] = instance_info["Architecture"]
                if "BlockDeviceMappings" in instance_info:
                    item["VolumeCount"] = len(instance_info["BlockDeviceMappings"])
                if "RootDeviceName" in instance_info:
                    item["RootDeviceName"] = instance_info["RootDeviceName"]
                if "InstanceType" in instance_info:
                    item["InstanceType"] = instance_info["InstanceType"]
                if "LaunchTime" in instance_info:
                    item["LaunchTime"] = str(instance_info["LaunchTime"])
                if "State" in instance_info:
                    item["State"] = instance_info["State"].get("Name", "launching")
                if "PrivateIpAddress" in instance_info:
                    item["PrivateIp"] = instance_info["PrivateIpAddress"]
                if "PublicIpAddress" in instance_info:
                    item["PublicIp"] = instance_info["PublicIpAddress"]

            table.put_item(Item=item)

        elif status == "READY":
            # Update existing item
            update_expr = """
                SET #s = :s,
                    InstanceId = :instance_id,
                    AMI = :ami,
                    LastUpdated = :now,
                    #st = :state
            """

            expr_attr_names = {
                "#s": "Status",
                "#st": "State"
            }

            expr_attr_values = {
                ":s": "RUNNING",
                ":instance_id": instance_id,
                ":ami": image_id,
                ":now": str(utils.get_utc_now()),
                ":state": "running"
            }

            # Add instance info if available
            if instance_info:
                if "State" in instance_info:
                    expr_attr_values[":state"] = instance_info["State"].get("Name", "unknown")
                if "PrivateIpAddress" in instance_info:
                    update_expr += ", PrivateIp = :private_ip"
                    expr_attr_values[":private_ip"] = instance_info["PrivateIpAddress"]
                if "PublicIpAddress" in instance_info:
                    update_expr += ", PublicIp = :public_ip"
                    expr_attr_values[":public_ip"] = instance_info["PublicIpAddress"]

            table.update_item(
                Key={"project": project},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_attr_names,
                ExpressionAttributeValues=expr_attr_values,
                ReturnValues="UPDATED_NEW"
            )
        else:
            raise ValueError(
                f"Unexpected status: {status} (expected 'nonexistent', 'READY', or 'LAUNCHING')"
            )

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "UnknownError")
        error_msg = f"Error updating DynamoDB for {project}: {error_code} - {str(e)}"
        print(error_msg)
        raise


def parse_arguments() -> argparse.Namespace:
    """Parse and validate command line arguments.

    Returns:
        Parsed command line arguments

    Raises:
        SystemExit: If required arguments are missing or invalid
    """
    parser = make_parser()
    args = parser.parse_args()

    # Validate arguments
    if not args.project.replace('-', '').isalnum():
        parser.error("Project name must be alphanumeric with optional hyphens")
    if args.volume_size < 0:
        parser.error("Volume size must be a non-negative number")

    return args


def initialize_aws_clients() -> Dict[str, Any]:
    """Initialize and return AWS client objects.

    Returns:
        Dictionary containing initialized AWS clients and resources

    Raises:
        AWSClientError: If there's an error initializing any client
    """
    try:
        return {
            "ssm": get_ssm_client(),
            "ddb": get_dynamodb_resource(),
            "ec2": get_ec2_client(),
            "ec2_resource": get_ec2_resource(),
        }
    except Exception as e:
        raise AWSClientError("Failed to initialize AWS clients") from e


def get_launch_config(aws: Dict[str, Any], param_prefix: str, project: str) -> Dict[str, Any]:
    """Get launch configuration including launch templates and project info.

    Args:
        aws: Dictionary of AWS clients
        param_prefix: Prefix for SSM parameters
        project: Project name

    Returns:
        Dictionary containing launch configuration

    Raises:
        ResourceNotFoundError: If required resources are not found
        AWSClientError: For AWS API errors
    """
    try:
        # Get launch template IDs from SSM
        lt_param = f"{param_prefix}/launchTemplateIds"
        try:
            lt_resp = aws["ssm"].get_parameter(Name=lt_param, WithDecryption=True)["Parameter"]["Value"]
        except Exception as e:
            raise AWSClientError(f"Failed to retrieve SSM parameter {lt_param}: {str(e)}")

        # Parse JSON to get launch template dictionary
        import json
        try:
            lt_dict = json.loads(lt_resp)
        except json.JSONDecodeError as e:
            raise AWSClientError(f"Invalid JSON in SSM parameter {lt_param}: {str(e)}")

        if isinstance(lt_dict, dict):
            # Handle legacy dictionary format
            lt_ids = list(lt_dict.values())
        elif isinstance(lt_dict, list):
            # Handle current list format from terraform
            lt_ids = lt_dict
        else:
            raise AWSClientError(f"Expected list or dictionary in SSM parameter {lt_param}, got {type(lt_dict)}")

        if not lt_ids:
            raise ResourceNotFoundError(f"No launch templates found in SSM parameter {lt_param}. Parameter contains: {lt_dict}")

        # Get DynamoDB table name from SSM
        table_param = f"{param_prefix}/snapshotTable"
        try:
            table_name = aws["ssm"].get_parameter(Name=table_param, WithDecryption=True)["Parameter"]["Value"]
        except Exception as e:
            raise AWSClientError(f"Failed to retrieve SSM parameter {table_param}: {str(e)}")

        table = aws["ddb"].Table(table_name)

        # Get project snapshot info
        item, error = get_project_snapshot(table, project)
        if error:
            raise ResourceNotFoundError(f"Error getting project snapshot: {error}")

        return {
            "lt_ids": lt_ids,
            "table": table,
            "item": item
        }

    except (ResourceNotFoundError, AWSClientError):
        # Re-raise our custom exceptions without wrapping
        raise
    except Exception as e:
        raise AWSClientError(f"Unexpected error in get_launch_config: {str(e)}") from e


def validate_project_status(item: Dict[str, Any], project: str) -> str:
    """Validate project status and return the status.

    Args:
        item: Project item from DynamoDB
        project: Project name for error messages

    Returns:
        Project status

    Raises:
        ValueError: If project status is invalid
    """
    if "Status" not in item:
        raise ValueError(f"Project {project} has no Status field")

    status = item["Status"]
    if status not in ["READY", "nonexistent"]:
        raise ValueError(
            f"Snapshot for project {project} is status {status}. "
            "Wait until it is READY or delete and recreate the project."
        )
    return status


def determine_ami(item: Dict[str, Any], base_ami: Optional[str]) -> str:
    """Determine which AMI to use based on project state and arguments.

    Args:
        item: Project item from DynamoDB
        base_ami: Optional base AMI from command line

    Returns:
        AMI ID to use

    Raises:
        ValueError: If no AMI can be determined

    Note:
        Priority order: RestoreAmi > BaseAmi > AMI > base_ami parameter
        The AMI field is used by lambda functions for storing snapshot AMIs.
    """
    restored_ami = item.get("RestoreAmi")
    base_ami_from_item = item.get("BaseAmi")
    ami_from_item = item.get("AMI")

    # Priority: RestoreAmi > BaseAmi > AMI from item > base_ami parameter
    ami_to_use = restored_ami or base_ami_from_item or ami_from_item or base_ami

    if not ami_to_use:
        raise ValueError(
            "No existing snapshot found. Please provide a base AMI with --base-ami "
            "to create a new snapshot."
        )

    if restored_ami and base_ami:
        print("Warning: base AMI is ignored when restoring from existing snapshot")

    return ami_to_use


def launch_instance_in_azs(
    aws: Dict[str, Any],
    lt_ids: List[str],
    az_info: Dict[str, Dict[str, str]],
    image_id: str,
    instance_type: str,
    key_name: str,
    volumes: List[Dict[str, Any]],
    project: str
) -> Tuple[Any, str, Dict[str, Any]]:
    """Attempt to launch an instance in any of the specified AZs.

    Args:
        aws: Dictionary of AWS clients
        lt_ids: List of launch template IDs to try
        az_info: Dictionary mapping launch template IDs to AZ info
        image_id: AMI ID to launch
        instance_type: EC2 instance type
        key_name: Name of the key pair for SSH access
        volumes: List of volume mappings
        project: Project name for tagging

    Returns:
        Tuple of (instance, instance_id, instance_info) on success

    Raises:
        RuntimeError: If instance launch fails in all AZs
    """
    last_error = None

    for lt_id in lt_ids:
        az_name = az_info[lt_id]["name"]
        try:
            instance, instance_id, error = launch_instance(
                ec2=aws["ec2"],
                ec2_resource=aws["ec2_resource"],
                launch_template_id=lt_id,
                image_id=image_id,
                instance_type=instance_type,
                key_name=key_name,
                volumes=volumes,
                project=project,
                az_name=az_name,
            )

            if instance and instance_id:
                return instance, instance_id, instance.meta.data

            last_error = error

        except Exception as e:
            last_error = e
            print(f"Error launching in {az_name}: {str(e)}")

    # If we get here, all launch attempts failed
    error_msg = "Failed to launch instance in all availability zones"
    if last_error:
        error_msg += f": {str(last_error)}"
    raise RuntimeError(error_msg)


def display_instance_info(ec2: Any, instance_id: str) -> None:
    """Display information about the launched instance.

    Args:
        ec2: EC2 client
        instance_id: ID of the instance to describe
    """
    try:
        desc = ec2.describe_instances(InstanceIds=[instance_id])
        instance = desc["Reservations"][0]["Instances"][0]

        print("\n" + "="*50)
        print("Instance Launched Successfully")
        print("="*50)

        print(f"\n{'Instance ID:':<20} {instance_id}")
        print(f"{'State:':<20} {instance.get('State', {}).get('Name', 'unknown')}")
        print(f"{'Type:':<20} {instance.get('InstanceType', 'unknown')}")
        print(f"{'AMI:':<20} {instance.get('ImageId', 'unknown')}")

        if 'Placement' in instance:
            print(f"\n{'Availability Zone:':<20} {instance['Placement'].get('AvailabilityZone', 'unknown')}")

        if 'PrivateIpAddress' in instance:
            print(f"{'Private IP:':<20} {instance['PrivateIpAddress']}")

        public_ip = instance.get('PublicIpAddress')
        if public_ip:
            print(f"{'Public IP:':<20} {public_ip}")
            print("\nYou can SSH into the instance using:")
            print(f"ssh -i /path/to/your-key.pem ec2-user@{public_ip}")

        print("\n" + "="*50 + "\n")

    except Exception as e:
        print(f"\nWarning: Could not get instance details: {str(e)}")


def launch_programmatic(
    project: str,
    instance_type: str,
    key_pair: str,
    volume_size: int = 0,
    base_ami: Optional[str] = None,
    param_prefix: str = "/devbox"
) -> None:
    """Launch a devbox instance programmatically.

    Args:
        project: Project name (alphanumeric and hyphens only)
        instance_type: EC2 instance type (e.g., t3.medium, m5.large)
        key_pair: Name of the EC2 Key Pair for SSH access
        volume_size: Minimum size (GiB) for the root EBS volume
        base_ami: Base AMI ID (only required for new projects)
        param_prefix: Prefix for AWS Systems Manager Parameter Store keys
    """
    try:
        # Validate project name
        if not project.replace('-', '').isalnum():
            raise ValueError("Project name must be alphanumeric with optional hyphens")
        if volume_size < 0:
            raise ValueError("Volume size must be a non-negative number")

        # Initialize AWS clients
        aws = initialize_aws_clients()

        # Get launch configuration
        config = get_launch_config(aws, param_prefix, project)

        # Validate project status
        status = validate_project_status(config["item"], project)

        # Determine which AMI to use
        image_id = determine_ami(config["item"], base_ami)
        print(f"Using AMI: {image_id}")

        # Get volume info
        volumes, _ = get_volume_info(aws["ec2"], image_id, volume_size)

        # Get launch template info
        az_info = get_launch_template_info(aws["ec2"], config["lt_ids"])

        # Launch instance in first available AZ
        print("Launching instance...")
        instance, instance_id, instance_info = launch_instance_in_azs(
            aws=aws,
            lt_ids=config["lt_ids"],
            az_info=az_info,
            image_id=image_id,
            instance_type=instance_type,
            key_name=key_pair,
            volumes=volumes,
            project=project
        )

        # Wait for instance to be running
        print("Waiting for instance to be ready...")
        instance.wait_until_running()
        instance.reload()  # Refresh instance attributes

        # Update instance status in DynamoDB
        update_instance_status(
            table=config["table"],
            project=project,
            status=status,
            instance_id=instance_id,
            image_id=image_id,
            instance_info=instance_info,
        )

        # Display instance information
        display_instance_info(aws["ec2"], instance_id)

    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except ResourceNotFoundError as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(2)
    except AWSClientError as e:
        print(f"AWS Error: {str(e)}", file=sys.stderr)
        if hasattr(e, 'error_code'):
            print(f"Error Code: {e.error_code}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(4)


def main() -> None:
    """Main launch function.

    This function parses command line arguments and calls launch_programmatic
    to launch a devbox EC2 instance.
    """
    try:
        # Parse and validate arguments
        args = parse_arguments()

        # Call the programmatic launch function
        launch_programmatic(
            project=args.project,
            instance_type=args.instance_type,
            key_pair=args.key_pair,
            volume_size=args.volume_size,
            base_ami=args.base_ami,
            param_prefix=args.param_prefix
        )

    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except ResourceNotFoundError as e:
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
