import os
import boto3

ec2 = boto3.resource("ec2")
ec2_client = boto3.client("ec2")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

def lambda_handler(event, context):
    print("Event:", event)
    detail = event.get("detail", {})
    instance_id = detail.get("instance-id")
    state = detail.get("state")
    print("Instance ID:", instance_id, "State:", state)
    if state not in ("shutting-down", "terminated"):
        return

    instance = ec2.Instance(instance_id)
    tags = {t["Key"]: t["Value"] for t in instance.tags or []}
    user = tags.get("UserID")
    snapshot_key = tags.get("SnapshotKey")
    print("Tags:", tags)
    print("UserID:", user, "SnapshotKey:", snapshot_key)
    if not user or not snapshot_key:
        return

    ami = instance.image_id
    instance_type = instance.instance_type

    # TODO: right now, if we crash while waiting for the volume to detach,
    # we double-create snapshot and AMI, which wastes compute time. If we
    # store the volume ID in the DynamoDB table, we can check if the volume
    # is different and skip the snapshot/AMI creation if it is the same.
    print(f"User: {user}, SnapshotKey: {snapshot_key}, AMI: {ami}, InstanceType: {instance_type}")

    root_name = instance.root_device_name
    try:
        root_vol = next(
          v for v in instance.volumes.all()
          if v.attachments[0]["Device"] == root_name
        )
    except StopIteration:
        print(f"No root volume found for instance {instance_id} on device {root_name}.")
        return
    vol_id = root_vol.id

    snapshot = root_vol.create_snapshot(
      Description = f"{user}-{snapshot_key}-root"
    )
    snapshot.create_tags(Tags=[
        {"Key": "UserID",         "Value": user},
        {"Key": "AMI",            "Value": ami},
        {"Key": "InstanceType",   "Value": instance_type},
        {"Key": "Name",           "Value": f"{user}-{ami}-{instance_type}-snapshot"},
    ])

    print(f"Waiting for snapshot {snapshot.snapshot_id} to complete…")
    waiter = ec2_client.get_waiter("snapshot_completed")
    waiter.wait(
        SnapshotIds=[snapshot.snapshot_id],
        WaiterConfig={"Delay": 10, "MaxAttempts": 30}
    )
    print("Snapshot is now completed.")

    # delete things
    key = {"user": user, "project": snapshot_key}
    resp = table.get_item(Key=key)
    item = resp.get("Item", {})
    old_ami = item.get("AMI")
    old_id = item.get("SnapshotID")
    print("Old AMI ID:", old_ami)
    print("Old Snapshot ID:", old_id)

    if old_ami:
        print("Deregistering old AMI:", old_ami)
        ec2_client.deregister_image(ImageId=old_ami)

    if old_id:
        print("Deleting old snapshot:", old_id)
        ec2.Snapshot(old_id).delete()

    arch = instance.architecture
    virt = instance.virtualization_type
    print("Registering new AMI.")
    print("Architecture:", arch, "Virtualization Type:", virt)

    ami_resp = ec2_client.register_image(
        Name                = f"{user}-{snapshot_key}-ami",
        Architecture        = arch,
        VirtualizationType  = virt,
        BlockDeviceMappings = [{
            "DeviceName": root_name,
            "Ebs": {
                "SnapshotId":          snapshot.snapshot_id,
                "DeleteOnTermination": True,
                "VolumeSize":          root_vol.size,
                "VolumeType":          root_vol.volume_type
            }
        }],
        RootDeviceName      = root_name,
    )
    new_ami = ami_resp["ImageId"]
    print("New AMI ID:", new_ami)

    table.put_item(Item={
        "user": user,
        "project": snapshot_key,
        "AMI": new_ami,
        "SnapshotID": snapshot.snapshot_id
    })

    print(f"Waiting for volume {vol_id} to become available for deletion…")
    waiter = ec2_client.get_waiter("volume_available")
    waiter.wait(VolumeIds=[vol_id], WaiterConfig={"Delay": 10, "MaxAttempts": 15})

    print("Deleting (detached) root volume:", vol_id)
    root_vol.delete()
    print("Volume deletion complete.")
