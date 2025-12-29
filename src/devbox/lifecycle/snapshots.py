"""Snapshot lifecycle handlers for devbox Lambda and CLI use."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any, Dict, Optional

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from devbox.utils import get_project_tag


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotConfig:
    """Configuration for snapshot lifecycle operations."""

    managed_by_tag: str = "devbox-lambda"
    cleanup_max_attempts: int = 12
    cleanup_wait_seconds: int = 5


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    return str(value)


def _log(level: int, message: str, **fields: Any) -> None:
    payload = {"message": message, **fields}
    logger.log(level, json.dumps(payload, sort_keys=True, default=_json_default))


def _log_info(message: str, **fields: Any) -> None:
    _log(logging.INFO, message, **fields)


def _log_warning(message: str, **fields: Any) -> None:
    _log(logging.WARNING, message, **fields)


def _log_error(message: str, **fields: Any) -> None:
    _log(logging.ERROR, message, **fields)


def create_snapshots(
    event: Dict[str, Any],
    *,
    ec2_resource: Any,
    main_table: Any,
    meta_table: Any,
    config: Optional[SnapshotConfig] = None,
) -> None:
    """Handle EC2 instance shutdown by creating volume snapshots."""
    config = config or SnapshotConfig()
    detail = (event or {}).get("detail", {})
    instance_id = detail.get("instance-id")
    state = detail.get("state")

    if state != "shutting-down":
        return

    if not instance_id:
        _log_warning("missing instance id in shutdown event")
        return

    instance = ec2_resource.Instance(instance_id)
    project = get_project_tag(instance.tags or [])
    if not project:
        _log_warning("instance missing project tag", instance_id=instance_id)
        return

    _log_info("creating snapshots", instance_id=instance_id, project=project)

    vols = list(instance.volumes.all())
    vol_count = len(vols)
    if vol_count == 0:
        _log_info("no volumes attached; nothing to snapshot", project=project)
        return

    username = ""
    try:
        existing_resp = main_table.get_item(Key={"project": project})
        if "Item" in existing_resp:
            username = existing_resp["Item"].get("Username", "")
    except Exception as exc:
        _log_warning(
            "failed to retrieve existing username",
            project=project,
            error=str(exc),
        )

    main_table.put_item(
        Item={
            "project": project,
            "VolumeCount": vol_count,
            "Status": "SNAPSHOTTING",
            "AMI": instance.image_id,
            "RootDeviceName": instance.root_device_name,
            "Architecture": instance.architecture,
            "VirtualizationType": instance.virtualization_type,
            "LastInstanceType": instance.instance_type,
            "LastKeyPair": instance.key_name,
            "Username": username,
        }
    )

    for vol in vols:
        vol_id = vol.id
        snap = vol.create_snapshot(Description=f"{project}-{vol_id}")
        _log_info(
            "creating snapshot",
            snapshot_id=snap.snapshot_id,
            volume_id=vol_id,
            project=project,
        )
        snap.create_tags(
            Tags=[
                {"Key": "Project", "Value": project},
                {"Key": "VolumeID", "Value": vol_id},
            ]
        )
        attachment = next(
            (a for a in vol.attachments if a.get("InstanceId") == instance_id),
            None,
        )
        if not attachment:
            raise ValueError(
                f"Volume {vol_id} missing attachment for instance {instance_id}"
            )
        device_name = attachment["Device"]
        meta_table.put_item(
            Item={
                "project": project,
                "volumeId": vol_id,
                "instanceId": instance_id,
                "deviceName": device_name,
                "snapshotId": snap.snapshot_id,
                "State": "PENDING",
            }
        )

    _log_info("snapshot creation complete", project=project, volume_count=vol_count)


def cleanup_ami_and_snapshots(
    ami_id: str,
    *,
    ec2_resource: Any,
    ec2_client: Any,
    config: Optional[SnapshotConfig] = None,
) -> None:
    """Deregister an AMI and remove its backing snapshots."""
    config = config or SnapshotConfig()
    image = ec2_resource.Image(ami_id)

    bdms = image.block_device_mappings
    snapshot_ids = []
    for mapping in bdms:
        ebs = mapping.get("Ebs", {})
        snap = ebs.get("SnapshotId")
        if snap:
            snapshot_ids.append(snap)

    _log_info("ami backed by snapshots", ami_id=ami_id, snapshot_ids=snapshot_ids)

    _log_info("deregistering ami", ami_id=ami_id)
    image.deregister()

    for snap_id in snapshot_ids:
        snap = ec2_resource.Snapshot(snap_id)
        try:
            _log_info("deleting snapshot", snapshot_id=snap_id)
            snap.delete()
        except Exception as exc:
            _log_warning(
                "failed to delete snapshot", snapshot_id=snap_id, error=str(exc)
            )

    _log_info("waiting for ami to vanish", ami_id=ami_id)
    for _ in range(config.cleanup_max_attempts):
        time.sleep(config.cleanup_wait_seconds)
        resp = ec2_client.describe_images(ImageIds=[ami_id])
        images = resp.get("Images", [])

        if not images:
            _log_info("ami no longer exists", ami_id=ami_id)
            break
        _log_info("ami still present", ami_id=ami_id, image_count=len(images))
    else:
        raise RuntimeError(f"Timed out waiting for AMI '{ami_id}' to deregister")

    _log_info("cleanup complete", ami_id=ami_id)


def create_image(
    event: Dict[str, Any],
    *,
    ec2_client: Any,
    ec2_resource: Any,
    main_table: Any,
    meta_table: Any,
    config: Optional[SnapshotConfig] = None,
) -> None:
    """Handle snapshot completion by registering a new AMI."""
    config = config or SnapshotConfig()
    detail = (event or {}).get("detail", {})
    snap_arn = detail.get("snapshot_id")
    result = detail.get("result")

    if result != "succeeded":
        return

    if not snap_arn:
        _log_warning("no snapshot arn in event")
        return

    snap_id = snap_arn.split("/")[-1]

    resp = meta_table.query(
        IndexName="SnapshotIndex",
        KeyConditionExpression=Key("snapshotId").eq(snap_id),
    )
    items = resp.get("Items", [])
    if not items:
        _log_warning("no meta entry found for snapshot", snapshot_id=snap_id)
        return

    if len(items) != 1:
        raise ValueError(
            f"Expected exactly one meta entry for snapshot {snap_id}, found {len(items)}"
        )

    meta_item = items[0]
    project = meta_item["project"]
    volume_id = meta_item["volumeId"]
    inst_id = meta_item["instanceId"]
    root_dev = meta_item["deviceName"]
    _log_info(
        "snapshot completed",
        snapshot_id=snap_id,
        project=project,
        volume_id=volume_id,
        instance_id=inst_id,
        device_name=root_dev,
    )

    meta_table.update_item(
        Key={"project": project, "volumeId": volume_id},
        UpdateExpression="SET #S = :s",
        ExpressionAttributeNames={"#S": "State"},
        ExpressionAttributeValues={":s": "COMPLETED"},
    )

    main_resp = main_table.get_item(Key={"project": project})
    main_item = main_resp.get("Item")
    if not main_item:
        _log_warning("no main entry found", project=project)
        return

    virtualization_type = main_item.get("VirtualizationType")
    architecture = main_item.get("Architecture")

    meta_resp = meta_table.query(KeyConditionExpression=Key("project").eq(project))
    all_meta = meta_resp.get("Items", [])
    required = main_item["VolumeCount"]
    done = sum((m.get("State") == "COMPLETED") for m in all_meta)

    _log_info("snapshot completion progress", project=project, done=done, total=required)
    if done < required:
        return

    def make_mapping(item: Dict[str, Any]) -> Dict[str, Any]:
        snap = item["snapshotId"]
        snap_info = ec2_client.describe_snapshots(SnapshotIds=[snap])["Snapshots"][0]
        vol_size = snap_info["VolumeSize"]
        vol_type = snap_info.get("VolumeType", "gp3")
        return {
            "DeviceName": item["deviceName"],
            "Ebs": {
                "SnapshotId": snap,
                "VolumeSize": vol_size,
                "VolumeType": vol_type,
                "DeleteOnTermination": True,
            },
        }

    mappings = [make_mapping(item) for item in all_meta]
    root_m = next(
        (x for x in all_meta if x["deviceName"] == main_item.get("RootDeviceName")),
        all_meta[0],
    )

    old_ami = main_item.get("AMI")
    if old_ami:
        desc = ec2_client.describe_images(ImageIds=[old_ami])["Images"]
        if not desc:
            _log_warning("old ami not found", ami_id=old_ami)
            return

        image = desc[0]
        tags = {t["Key"]: t["Value"] for t in image.get("Tags", [])}
        if tags.get("ManagedBy") == config.managed_by_tag:
            _log_info("cleaning up old ami", ami_id=old_ami, project=project)
            cleanup_ami_and_snapshots(
                old_ami,
                ec2_resource=ec2_resource,
                ec2_client=ec2_client,
                config=config,
            )
        else:
            _log_info("old ami not managed by devbox", ami_id=old_ami, project=project)

    image_resp = ec2_client.register_image(
        Name=f"{project}-ami",
        BlockDeviceMappings=mappings,
        RootDeviceName=root_m["deviceName"],
        VirtualizationType=virtualization_type,
        Architecture=architecture,
        TagSpecifications=[
            {
                "ResourceType": "image",
                "Tags": [
                    {"Key": "Project", "Value": project},
                    {"Key": "ManagedBy", "Value": config.managed_by_tag},
                ],
            }
        ],
    )
    new_ami = image_resp["ImageId"]
    _log_info("registered new ami", ami_id=new_ami, project=project)

    main_table.update_item(
        Key={"project": project},
        UpdateExpression="SET #A = :a, #S = :s",
        ExpressionAttributeNames={"#A": "AMI", "#S": "Status"},
        ExpressionAttributeValues={":a": new_ami, ":s": "IMAGING"},
    )


def mark_ready(
    event: Dict[str, Any],
    *,
    main_table: Any,
    meta_table: Any,
) -> None:
    """Handle AMI availability by marking project ready and clearing metadata."""
    detail = (event or {}).get("detail", {})
    ami_id = detail.get("ImageId")
    state = detail.get("State")

    if state != "available":
        return

    if not ami_id:
        _log_warning("missing ami id in event")
        return

    resp = main_table.scan(FilterExpression=Attr("AMI").eq(ami_id))
    items = resp.get("Items", [])
    if not items:
        _log_warning("no main entry found for ami", ami_id=ami_id)
        return

    project = items[0]["project"]
    _log_info("marking project ready", project=project, ami_id=ami_id)

    scan_resp = meta_table.query(KeyConditionExpression=Key("project").eq(project))
    meta_items = scan_resp.get("Items", [])
    for meta_item in meta_items:
        vol_id = meta_item["volumeId"]
        try:
            meta_table.delete_item(Key={"project": project, "volumeId": vol_id})
            _log_info("deleted meta row", project=project, volume_id=vol_id)
        except Exception as exc:
            _log_warning(
                "failed to delete meta row",
                project=project,
                volume_id=vol_id,
                error=str(exc),
            )

    main_table.update_item(
        Key={"project": project},
        UpdateExpression="SET #S = :s",
        ExpressionAttributeNames={"#S": "Status"},
        ExpressionAttributeValues={":s": "READY"},
    )


def delete_volume(
    event: Dict[str, Any],
    *,
    ec2_client: Any,
    main_table: Any,
    meta_table: Any,
) -> None:
    """Handle detached volumes by deleting them once snapshots complete."""
    detail = (event or {}).get("detail", {})
    vol_id = detail.get("volume-id")
    state = detail.get("state")

    if state != "available":
        return

    if not vol_id:
        _log_warning("missing volume id in event")
        return

    resp = meta_table.scan(FilterExpression=Attr("volumeId").eq(vol_id))
    items = resp.get("Items", [])
    if not items:
        _log_info("volume not found in meta", volume_id=vol_id)
        return

    meta_item = items[0]
    project = meta_item["project"]
    state_tag = meta_item["State"]

    if state_tag == "COMPLETED":
        _log_info("deleting detached volume", volume_id=vol_id, project=project)
        try:
            ec2_client.delete_volume(VolumeId=vol_id)
        except ClientError as exc:
            _log_error("error deleting volume", volume_id=vol_id, error=str(exc))
        return

    _log_warning(
        "volume not snapshotted; marking error",
        volume_id=vol_id,
        project=project,
    )
    main_table.update_item(
        Key={"project": project},
        UpdateExpression="SET #S = :s",
        ExpressionAttributeNames={"#S": "Status"},
        ExpressionAttributeValues={":s": "ERROR"},
    )
