#!/usr/bin/env python3
import argparse
import os
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
        default=20,
        help="Size (GiB) for new empty data volume if no snapshot exists",
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

    lt_resp = ssm.get_parameter(Name=f"{args.param_prefix}/launchTemplateId")
    lt_id = lt_resp["Parameter"]["Value"]

    tbl_resp = ssm.get_parameter(Name=f"{args.param_prefix}/snapshotTable")
    table_name = tbl_resp["Parameter"]["Value"]

    table = ddb.Table(table_name)

    try:
        resp = table.get_item(Key={"project": args.project})
        item = resp.get("Item", {})
        restored_ami = item.get("AMI")
    except ClientError as e:
        print(f"Error fetching snapshot from DynamoDB: {e}")
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

    print("Launching EC2 instance...")
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
    print(f"Instance launched: {instance_id}. Waiting for running state...")


    instance.wait_until_running()
    # waiter = ec2.get_waiter("instance_running")
    # waiter.wait(InstanceIds=[instance_id])
    print("Instance is now running.")

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
