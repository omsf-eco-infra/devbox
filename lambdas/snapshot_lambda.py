import os
import time
import boto3
from botocore.exceptions import ClientError

ec2      = boto3.resource("ec2")
ec2_client = boto3.client("ec2")
dynamodb = boto3.resource("dynamodb")

MAIN_TABLE = os.environ["MAIN_TABLE"]
META_TABLE = os.environ["META_TABLE"]
main_tbl   = dynamodb.Table(MAIN_TABLE)
meta_tbl   = dynamodb.Table(META_TABLE)

def create_snapshots(event, context):
    # triggered by EC2 instance shutting down
    detail      = event.get("detail", {})
    instance_id = detail.get("instance-id")
    state       = detail.get("state")

    if state != "shutting-down":
        return

    instance = ec2.Instance(instance_id)
    tags = {t["Key"]: t["Value"] for t in instance.tags or []}
    project = tags.get("Project")
    if not project:
        print(f"Instance '{instance_id}' missing project tag – skipping.")
        return

    print(f"[create_snapshots] instance {instance_id} → project {project}")

    vols = list(instance.volumes.all())
    vol_count = len(vols)
    if vol_count == 0:
        print("No volumes attached; nothing to snapshot.")
        return

    main_tbl.put_item(Item={
        "project":     project,
        "VolumeCount": vol_count,
        "Status":      "SNAPSHOTTING",
        "AMI": instance.image_id,
        "RootDeviceName": instance.root_device_name,
        "Architecture": instance.architecture,
        "VirtualizationType": instance.virtualization_type,
        "LastInstanceType": instance.instance_type,
        "LastKeyPair": instance.key_name,
    })

    for vol in vols:
        vol_id = vol.id
        snap = vol.create_snapshot(Description=f"{project}-{vol_id}")
        print(f"  ➤ creating snapshot {snap.snapshot_id} for volume {vol_id}")
        snap.create_tags(Tags=[
            {"Key": "Project",   "Value": project},
            {"Key": "VolumeID",  "Value": vol_id},
        ])
        attachment = next(a for a in vol.attachments if a["InstanceId"] == instance_id)
        device_name = attachment["Device"]
        meta_tbl.put_item(Item={
            "project":    project,
            "volumeId":   vol_id,
            "instanceId": instance_id,
            "deviceName": device_name,
            "snapshotId": snap.snapshot_id,
            "State":      "PENDING"
        })

    print(f"[create_snapshots] Done. Created {vol_count} snapshots.")
    return

def cleanup_ami_and_snapshots(ami_id):
    image = ec2.Image(ami_id)

    bdms = image.block_device_mappings  # list of dicts
    snapshot_ids = []
    for mapping in bdms:
        ebs = mapping.get("Ebs", {})
        snap = ebs.get("SnapshotId")
        if snap:
            snapshot_ids.append(snap)

    print(f"AMI {ami_id} is backed by snapshots: {snapshot_ids}")

    print(f"Deregistering AMI {ami_id} …")
    image.deregister()

    for snap_id in snapshot_ids:
        snap = ec2.Snapshot(snap_id)
        try:
            print(f"Deleting snapshot {snap_id} …")
            snap.delete()
        except Exception as e:
            print(f"  ‼️ could not delete snapshot {snap_id}: {e}")

    print(f"[cleanup] Waiting for AMI='{ami_id}' to vanish …")
    for attempt in range(12):
        time.sleep(5)
        resp = ec2_client.describe_images(ImageIds=[ami_id])
        images = resp.get("Images", [])

        if not images:
            print(f"[cleanup] AMI '{ami_id}' no longer exists in describe_images.")
            break
        else:
            print(f"[cleanup] Still finds {len(resp)} image(s) named '{ami_id}', retrying …")

    else:
        raise RuntimeError(f"[cleanup] Timed out waiting for AMI '{ami_id}' to deregister")


    print(f"Cleanup complete for AMI {ami_id} and its snapshots.")

def create_image(event, context):
    # triggered by EC2 snapshot state-change to "completed"
    print("[create_image] Triggered by snapshot completion event:", event)
    detail     = event.get("detail", {})
    snap_arn    = detail.get("snapshot_id")
    result     = detail.get("result")

    if result != "succeeded":
        return

    if not snap_arn:
        print("[create_image] No snapshot ARN found in event; skipping.")
        return

    snap_id = snap_arn.split("/")[-1]

    resp = meta_tbl.query(
        IndexName = "SnapshotIndex",
        KeyConditionExpression = boto3.dynamodb.conditions.Key("snapshotId").eq(snap_id)
    )
    items = resp.get("Items", [])
    if not items:
        print(f"No meta entry found for snapshot {snap_id}; skipping.")
        return

    assert len(items) == 1, f"Expected exactly one meta entry for snapshot {snap_id}, found {len(items)}"
    meta_item = items[0]
    project   = meta_item["project"]
    volume_id = meta_item["volumeId"]
    inst_id = meta_item["instanceId"]
    root_dev = meta_item["deviceName"]
    print(f"[create_image] Snapshot {snap_id} for volume {volume_id} in project {project}")

    meta_tbl.update_item(
        Key   = {"project": project, "volumeId": volume_id},
        UpdateExpression = "SET #S = :s",
        ExpressionAttributeNames  = {"#S": "State"},
        ExpressionAttributeValues = {":s": "COMPLETED"},
    )
    print(f"[create_image] Marked {snap_id} (volume {volume_id}) COMPLETE for project {project}")


    main_resp = main_tbl.get_item(Key={"project": project})
    main_item = main_resp.get("Item")
    if not main_item:
        print(f"[create_image] No main entry for project {project}; skipping.")
        return

    virtualization_type = main_item.get("VirtualizationType")
    architecture = main_item.get("Architecture")

    meta_resp = meta_tbl.query(
        KeyConditionExpression = boto3.dynamodb.conditions.Key("project").eq(project)
    )
    all_meta = meta_resp.get("Items", [])
    required = main_item["VolumeCount"]
    done = sum((m.get("State") == "COMPLETED") for m in all_meta)

    print(f"[create_image] {done}/{required} snapshots done for {project}")
    if done < required:
        return

    def make_mapping(item):
        snap = item["snapshotId"]
        snap_info = ec2_client.describe_snapshots(SnapshotIds=[snap])["Snapshots"][0]
        vol_size = snap_info["VolumeSize"]
        vol_type = snap_info.get("VolumeType", "gp3")
        return {
            "DeviceName": item["deviceName"],
            "Ebs": {
                "SnapshotId":          snap,
                "VolumeSize":          vol_size,
                "VolumeType":          vol_type,
                "DeleteOnTermination": True,
            }
        }

    mappings = [make_mapping(item) for item in all_meta]
    root_m = next((x for x in all_meta if x["deviceName"] == main_item.get("RootDeviceName")), all_meta[0])

    old_ami = main_item.get("AMI")
    desc = ec2_client.describe_images(ImageIds=[old_ami])["Images"]
    if not desc:
        print(f"[create_image] AMI {old_ami} not found (perhaps already deleted).")
        return

    image = desc[0]
    tags  = { t["Key"]: t["Value"] for t in image.get("Tags", []) }
    if old_ami:
        if tags.get("ManagedBy") != "devbox-lambda":
            print(f"[create_image] AMI {old_ami} is not managed by devbox-lambda; skipping cleanup.")
        else:
            print(f"[create_image] Cleaning up old AMI {old_ami} for project {project}")
            cleanup_ami_and_snapshots(old_ami)

    image_resp = ec2_client.register_image(
        Name               = f"{project}-ami",
        BlockDeviceMappings = mappings,
        RootDeviceName     = root_m["deviceName"],
        VirtualizationType = virtualization_type,
        Architecture       = architecture,
        TagSpecifications   = [
            {
                "ResourceType": "image",
                "Tags": [
                    {"Key": "Project",   "Value": project},
                    {"Key": "ManagedBy", "Value": "devbox-lambda"}
                ]
            }
        ]
    )
    new_ami = image_resp["ImageId"]
    print(f"[create_image] Registered AMI {new_ami} for project {project}")

    main_tbl.update_item(
        Key = {"project": project},
        UpdateExpression = "SET #A = :a, #S = :s",
        ExpressionAttributeNames = {"#A": "AMI", "#S": "Status"},
        ExpressionAttributeValues = {":a": new_ami, ":s": "IMAGING"},
    )

    return


def mark_ready(event, context):
    # trigger = EC2 AMI state-change to "available"
    print("[mark_ready] Triggered by AMI state-change event:", event)
    detail    = event.get("detail", {})
    ami_id    = detail.get("ImageId")
    state     = detail.get("State")
    print(f"[mark_ready] AMI {ami_id} changed state → {state}")

    if state != "available":
        return

    resp = main_tbl.scan(
        FilterExpression = boto3.dynamodb.conditions.Attr("AMI").eq(ami_id)
    )
    items = resp.get("Items", [])
    if not items:
        print(f"[mark_ready] No main entry found for AMI {ami_id}; skipping.")
        return

    project = items[0]["project"]
    print(f"[mark_ready] Project is {project}")

    # clear out the old metadata
    scan_resp = meta_tbl.query(
        KeyConditionExpression = boto3.dynamodb.conditions.Key("project").eq(project)
    )
    print(f"[mark_ready] Deleting meta rows (volumes) now.")
    meta_items = scan_resp.get("Items", [])
    for m in meta_items:
        vol_id = m["volumeId"]
        try:
            print(f"[mark_ready] Deleting meta row for volume {vol_id}")
            meta_tbl.delete_item(
                Key = {
                    "project":  project,
                    "volumeId": vol_id
                }
            )
        except Exception as e:
            print(f"[mark_ready] Warning: failed to delete meta row ({project}, {vol_id}): {e}")


    main_tbl.update_item(
        Key = {"project": project},
        UpdateExpression = "SET #S = :s",
        ExpressionAttributeNames = {"#S": "Status"},
        ExpressionAttributeValues = {":s": "READY"},
    )

    print(f"[mark_ready] Set project {project} status → READY")
    return


def delete_volume(event, context):
    # triggers by EC2 volume state-change to "available"
    detail   = event.get("detail", {})
    vol_id   = detail.get("volume-id")
    state    = detail.get("state")
    print(f"[delete_volume] Volume {vol_id} state → {state}")

    if state != "available":
        return

    resp = meta_tbl.scan(
        FilterExpression = boto3.dynamodb.conditions.Attr("volumeId").eq(vol_id)
    )
    items = resp.get("Items", [])
    if not items:
        print(f"[delete_volume] Volume {vol_id} not found in meta; nothing to delete.")
        return

    meta_item = items[0]
    project   = meta_item["project"]
    snap_id   = meta_item["snapshotId"]
    state_tag = meta_item["State"]

    if state_tag == "COMPLETED":
        print(f"[delete_volume] Deleting detached volume {vol_id} for project {project}")
        try:
            ec2_client.delete_volume(VolumeId=vol_id)
        except ClientError as e:
            print(f"Error deleting volume {vol_id}: {e}")
        return

    print(f"[delete_volume] Volume {vol_id} for project {project} not snapshotted → ERROR")
    main_tbl.update_item(
        Key = {"project": project},
        UpdateExpression = "SET #S = :s",
        ExpressionAttributeNames = {"#S": "Status"},
        ExpressionAttributeValues = {":s": "ERROR"},
    )
    return
