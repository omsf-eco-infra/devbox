#!/usr/bin/env python3
import argparse
import json
import boto3
from botocore.exceptions import ClientError


def make_parser():
    parser = argparse.ArgumentParser(description=(
        "Launch a persistent devbox EC2 instance with attached EBS volume"
    ))
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument(
        "--base-ami",
        help="AMI: only used for new snapshot-keys"
    )
    parser.add_argument(
        "--instance-type",
        required=True,
        help="EC2 instance type"
    )
    parser.add_argument("--key-pair", required=True, help="Name of the EC2 Key Pair")
    parser.add_argument('--param-prefix', default="/devbox",
                        help="Prefix for parameter store keys")
    parser.add_argument(
        "--volume-size",
        type=int,
        default=0,
        help="Minimum size (GiB) for largest EBS volume",
    )
    return parser


def main():
    """Main launch function.

    This reads from the parameter store to get information about this
    installation. Then it reads from the associated DynamoDB table to find
    objects referenced by the user, and to launch a new EC2 instance.
    """
    parser = make_parser()
    args = parser.parse_args()

    ec2 = boto3.client("ec2")
    ddb = boto3.resource("dynamodb")
    ssm = boto3.client("ssm")
    ec2_resource = boto3.resource("ec2")

    lt_resp = ssm.get_parameter(Name=f"{args.param_prefix}/launchTemplateIds")
    lt_ids = json.loads(lt_resp["Parameter"]["Value"])

    tbl_resp = ssm.get_parameter(Name=f"{args.param_prefix}/snapshotTable")
    table_name = tbl_resp["Parameter"]["Value"]

    table = ddb.Table(table_name)

    try:
        resp = table.get_item(Key={"project": args.project})
        item = resp.get("Item", {})
        restored_ami = item.get("AMI")
        status = item.get("Status", "nonexistent")
    except ClientError as e:
        print(f"Error fetching snapshot from DynamoDB: {e}")
        return

    if status not in ["READY", "nonexistent"]:
        print(
            f"Snapshot for project {args.project} is status {status}. "
            "Wait until it is READY."
        )
        return

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

    # determine the largest volume, make sure it is at least args.volume_size
    try:
        resp = ec2.describe_images(ImageIds=[image_id])
    except ClientError as e:
        print(f"Error fetching image details: {e}")
        return

    image = resp["Images"][0]
    volumes = image.get("BlockDeviceMappings", [])
    largest_volume = None
    largest_volume_size = 0
    largest_volume_idx = None
    for idx, vol in enumerate(volumes):
        if "Ebs" in vol and "VolumeSize" in vol["Ebs"]:
            size = vol["Ebs"]["VolumeSize"]
            if size > largest_volume_size:
                largest_volume_size = size
                largest_volume = vol
                largest_volume_idx = idx

    # update volumes so that the largest one is at least args.volume_size
    if largest_volume_size < args.volume_size:
        if largest_volume:
            print(f"Increasing volume size from {largest_volume_size} GiB to {args.volume_size} GiB")
            largest_volume["Ebs"]["VolumeSize"] = args.volume_size
        else:
            print(f"Creating new volume of size {args.volume_size} GiB")
            largest_volume = {
                "DeviceName": "/dev/sda1",
                "Ebs": {"VolumeSize": args.volume_size, "VolumeType": "gp2"}
            }
            volumes.append(largest_volume)


    # extract AZ names and indices from launch templates
    az_info = {}
    for idx, lt_id in enumerate(lt_ids):
        try:
            lt_desc = ec2.describe_launch_templates(LaunchTemplateIds=[lt_id])
            lt_name = lt_desc['LaunchTemplates'][0]['LaunchTemplateName']
            az_idx = lt_name.split('az')[1].split('-')[0]
            lt_version = ec2.describe_launch_template_versions(
                LaunchTemplateId=lt_id,
                Versions=['$Latest']
            )
            network_interfaces = lt_version['LaunchTemplateVersions'][0]['LaunchTemplateData'].get('NetworkInterfaces', [])
            if network_interfaces and 'SubnetId' in network_interfaces[0]:
                subnet_id = network_interfaces[0]['SubnetId']
                subnet = ec2.describe_subnets(SubnetIds=[subnet_id])
                az_name = subnet['Subnets'][0]['AvailabilityZone']
                az_info[lt_id] = {'name': az_name, 'index': az_idx}
        except Exception as e:
            print(f"Warning: Could not get AZ info for launch template {lt_id}: {e}")
            az_info[lt_id] = {'name': f"az-{idx}", 'index': idx}

    # loop over AZs to launch the instance
    instance = None
    instance_id = None
    last_error = None

    for lt_id in lt_ids:
        az_name = az_info[lt_id]['name']
        try:
            print(f"Attempting to launch instance in {az_name}...")
            resp = ec2.run_instances(
                LaunchTemplate={
                    "LaunchTemplateId": lt_id,
                    "Version": "$Latest",
                },
                ImageId=image_id,
                InstanceType=args.instance_type,
                MinCount=1,
                MaxCount=1,
                KeyName=args.key_pair,
                BlockDeviceMappings=volumes,
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": f"devbox-{args.project}"},
                            {"Key": "Project", "Value": args.project},
                            {"Key": "InstanceType", "Value": args.instance_type},
                        ],
                    },
                    {
                        "ResourceType": "volume",
                        "Tags": [
                            {"Key": "Project", "Value": args.project},
                            {"Key": "InstanceType", "Value": args.instance_type},
                            {"Key": "Application", "Value": "devbox"},
                        ],
                    },
                ],
            )
            instance_dct = resp["Instances"][0]
            instance_id = instance_dct["InstanceId"]
            instance = ec2_resource.Instance(instance_id)
            print(f"Instance launched in {az_name}: {instance_id}. Waiting for running state...")
            break  # Exit loop on successful launch
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            print(f"Failed to launch in {az_name}: {error_code} - {error_message}")
            last_error = e
            continue  # Try next AZ

    if not instance or not instance_id:
        print("Failed to launch instance in all availability zones")
        if last_error:
            print(f"Last error: {last_error}")
        return

    instance.wait_until_running()
    print("Instance is now running.")

    # update status to RUNNING
    if status == "nonexistent":
        table.put_item(
            Item={
                "project": args.project,
                "Status": "RUNNING",
                "AMI": image_id,
                "VirtualizationType": instance_dct["VirtualizationType"],
                "Architecture": instance_dct["Architecture"],
                "VolumeCount": len(instance_dct.get("BlockDeviceMappings", [])),
                "RootDeviceName": instance_dct["RootDeviceName"],
            }
        )
    elif status == "READY":
        table.update_item(
            Key={"project": args.project},
            UpdateExpression="SET #s = :s, InstanceId = :id",
            ExpressionAttributeNames={"#s": "Status"},
            ExpressionAttributeValues={":s": "RUNNING", ":id": instance_id},
        )
    else:
        raise RuntimeError(f"Unexpected status in DynamoDB: {status} "
                           "(this should never happen)")

    desc = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0][
        "Instances"
    ][0]
    az = desc["Placement"]["AvailabilityZone"]
    public_ip = desc.get("PublicIpAddress")
    print(f"Instance AZ: {az}, Public IP: {public_ip}")

    print("\nYou can SSH into the instance using:")
    print(f"ssh -i /path/to/your-key.pem ec2-user@{public_ip}\n")


if __name__ == "__main__":
    main()
