#!/usr/bin/env python3
import argparse
import json
import sys
from typing import Dict, Any, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

# Type aliases
SSMClient = Any
DynamoDBTable = Any
EC2Client = Any
EC2ServiceResource = Any


def make_parser():
    """Create and return the argument parser for the launch script."""
    parser = argparse.ArgumentParser(
        description=("Launch a persistent devbox EC2 instance with attached EBS volume")
    )
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--base-ami", help="AMI: only used for new snapshot-keys")
    parser.add_argument("--instance-type", required=True, help="EC2 instance type")
    parser.add_argument("--key-pair", required=True, help="Name of the EC2 Key Pair")
    parser.add_argument(
        "--param-prefix", default="/devbox", help="Prefix for parameter store keys"
    )
    parser.add_argument(
        "--volume-size",
        type=int,
        default=0,
        help="Minimum size (GiB) for largest EBS volume",
    )
    return parser




def get_project_snapshot(
    table: DynamoDBTable, project_name: str
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Get the snapshot information for a project from DynamoDB.
    
    Args:
        table: The DynamoDB table resource
        project_name: Name of the project to look up
        
    Returns:
        A tuple of (item_dict, error_message). 
        On success: (item_dict, None)
        On error: ({}, error_message)
    """
    try:
        resp = table.get_item(Key={"project": project_name})
        item = resp.get("Item", {})
        return item, None
    except ClientError as e:
        return {}, str(e)


def get_volume_info(ec2: EC2Client, image_id: str, min_volume_size: int = 0) -> Tuple[list, int]:
    """Get and validate volume information for an AMI."""
    try:
        resp = ec2.describe_images(ImageIds=[image_id])
    except ClientError as e:
        raise RuntimeError(f"Error fetching image details: {e}")

    image = resp["Images"][0]
    volumes = image.get("BlockDeviceMappings", []).copy()
    
    # Find the largest volume
    largest_volume = None
    largest_volume_size = 0
    
    for idx, vol in enumerate(volumes):
        if "Ebs" in vol and "VolumeSize" in vol["Ebs"]:
            size = vol["Ebs"]["VolumeSize"]
            if size > largest_volume_size:
                largest_volume_size = size
                largest_volume = vol

    # Update volumes if needed
    if largest_volume_size < min_volume_size:
        if largest_volume:
            print(
                f"Increasing volume size from {largest_volume_size} GiB to {min_volume_size} GiB"
            )
            largest_volume["Ebs"]["VolumeSize"] = min_volume_size
        else:
            print(f"Creating new volume of size {min_volume_size} GiB")
            volumes.append({
                "DeviceName": "/dev/sda1",
                "Ebs": {"VolumeSize": min_volume_size, "VolumeType": "gp2"},
            })
    
    return volumes, largest_volume_size if largest_volume else 0


def get_launch_template_info(ec2: EC2Client, lt_ids: list) -> Dict[str, Dict[str, str]]:
    """Get availability zone information for launch templates."""
    az_info = {}
    for idx, lt_id in enumerate(lt_ids):
        try:
            lt_desc = ec2.describe_launch_templates(LaunchTemplateIds=[lt_id])
            lt_name = lt_desc["LaunchTemplates"][0]["LaunchTemplateName"]
            az_idx = lt_name.split("az")[1].split("-")[0]
            lt_version = ec2.describe_launch_template_versions(
                LaunchTemplateId=lt_id, Versions=["$Latest"]
            )
            network_interfaces = lt_version["LaunchTemplateVersions"][0][
                "LaunchTemplateData"
            ].get("NetworkInterfaces", [])
            if network_interfaces and "SubnetId" in network_interfaces[0]:
                subnet_id = network_interfaces[0]["SubnetId"]
                subnet = ec2.describe_subnets(SubnetIds=[subnet_id])
                az_name = subnet["Subnets"][0]["AvailabilityZone"]
                az_info[lt_id] = {"name": az_name, "index": az_idx}
        except Exception as e:
            print(f"Warning: Could not get AZ info for launch template {lt_id}: {e}")
            az_info[lt_id] = {"name": f"az-{idx}", "index": idx}
    return az_info


def launch_instance(
    ec2: EC2Client,
    ec2_resource: EC2ServiceResource,
    launch_template_id: str,
    image_id: str,
    instance_type: str,
    key_name: str,
    volumes: list,
    project: str,
    az_name: str,
) -> Tuple[Optional[Any], Optional[str], Optional[Exception]]:
    """Attempt to launch an EC2 instance in the specified AZ."""
    try:
        print(f"Attempting to launch instance in {az_name}...")
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
                    "Tags": [
                        {"Key": "Name", "Value": f"devbox-{project}"},
                        {"Key": "Project", "Value": project},
                        {"Key": "InstanceType", "Value": instance_type},
                    ],
                },
                {
                    "ResourceType": "volume",
                    "Tags": [
                        {"Key": "Project", "Value": project},
                        {"Key": "InstanceType", "Value": instance_type},
                        {"Key": "Application", "Value": "devbox"},
                    ],
                },
            ],
        )
        instance_dct = resp["Instances"][0]
        instance_id = instance_dct["InstanceId"]
        instance = ec2_resource.Instance(instance_id)
        print(
            f"Instance launched in {az_name}: {instance_id}. Waiting for running state..."
        )
        return instance, instance_id, None
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_message = e.response["Error"]["Message"]
        print(f"Failed to launch in {az_name}: {error_code} - {error_message}")
        return None, None, e


def update_instance_status(
    table: DynamoDBTable,
    project: str,
    status: str,
    instance_id: str,
    image_id: str,
    instance_info: Optional[Dict[str, Any]] = None,
) -> None:
    """Update the instance status in DynamoDB."""
    if status == "nonexistent" and instance_info:
        table.put_item(
            Item={
                "project": project,
                "Status": "RUNNING",
                "AMI": image_id,
                "InstanceId": instance_id,
                "VirtualizationType": instance_info.get("VirtualizationType"),
                "Architecture": instance_info.get("Architecture"),
                "VolumeCount": len(instance_info.get("BlockDeviceMappings", [])),
                "RootDeviceName": instance_info.get("RootDeviceName"),
            }
        )
    elif status == "READY":
        table.update_item(
            Key={"project": project},
            UpdateExpression="SET #s = :s, InstanceId = :id",
            ExpressionAttributeNames={"#s": "Status"},
            ExpressionAttributeValues={":s": "RUNNING", ":id": instance_id},
        )
    else:
        raise RuntimeError(
            f"Unexpected status in DynamoDB: {status} (this should never happen)"
        )


def main() -> None:
    """Main launch function.

    This reads from the parameter store to get information about this
    installation. Then it reads from the associated DynamoDB table to find
    objects referenced by the user, and to launch a new EC2 instance.
    """
    parser = make_parser()
    args = parser.parse_args()

    # Initialize AWS clients
    ssm = boto3.client("ssm")
    ddb = boto3.resource("dynamodb")
    ec2 = boto3.client("ec2")
    ec2_resource = boto3.resource("ec2")

    try:
        # Get launch template IDs from SSM
        lt_resp = ssm.get_parameter(Name=f"{args.param_prefix}/launchTemplateIds")
        lt_ids = json.loads(lt_resp["Parameter"]["Value"])

        # Get DynamoDB table name from SSM
        tbl_resp = ssm.get_parameter(Name=f"{args.param_prefix}/snapshotTable")
        table_name = tbl_resp["Parameter"]["Value"]
        table = ddb.Table(table_name)

        # Get project snapshot info
        item, error = get_project_snapshot(table, args.project)
        if error:
            print(f"Error fetching snapshot from DynamoDB: {error}")
            return

        restored_ami = item.get("AMI")
        status = item.get("Status", "nonexistent")

        # Validate status
        if status not in ["READY", "nonexistent"]:
            print(
                f"Snapshot for project {args.project} is status {status}. "
                "Wait until it is READY."
            )
            return

        # Determine which AMI to use
        if not restored_ami:
            print(f"No snapshot found for project {args.project}.")
            if not args.base_ami:
                raise ValueError("Please provide a base AMI to create a new snapshot.")
            image_id = args.base_ami
        else:
            image_id = restored_ami
            print(f"Restoring from AMI: {image_id}")
            if args.base_ami:
                print("Warning: base AMI is ignored when restoring from snapshot.")

        # Get and validate volume info
        try:
            volumes, _ = get_volume_info(ec2, image_id, args.volume_size)
        except RuntimeError as e:
            print(str(e))
            return

        # Get launch template info
        az_info = get_launch_template_info(ec2, lt_ids)

        # Try to launch instance in each AZ until successful
        instance = None
        instance_id = None
        last_error = None
        instance_info = None

        for lt_id in lt_ids:
            az_name = az_info[lt_id]["name"]
            instance, instance_id, error = launch_instance(
                ec2=ec2,
                ec2_resource=ec2_resource,
                launch_template_id=lt_id,
                image_id=image_id,
                instance_type=args.instance_type,
                key_name=args.key_pair,
                volumes=volumes,
                project=args.project,
                az_name=az_name,
            )
            
            if instance and instance_id:
                instance_info = instance.meta.data
                break
            last_error = error

        if not instance or not instance_id:
            print("Failed to launch instance in all availability zones")
            if last_error:
                print(f"Last error: {last_error}")
            return

        # Wait for instance to be running
        instance.wait_until_running()
        print("Instance is now running.")

        # Update instance status in DynamoDB
        update_instance_status(
            table=table,
            project=args.project,
            status=status,
            instance_id=instance_id,
            image_id=image_id,
            instance_info=instance_info,
        )

        # Get and display instance details
        desc = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
        az = desc["Placement"]["AvailabilityZone"]
        public_ip = desc.get("PublicIpAddress")
        print(f"Instance AZ: {az}, Public IP: {public_ip}")
        print("\nYou can SSH into the instance using:")
        print(f"ssh -i /path/to/your-key.pem ec2-user@{public_ip}\n")

    except ClientError as e:
        print(f"AWS error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
