"""Tests for snapshot lifecycle handlers."""
from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

import boto3
import pytest
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from moto import mock_aws

from devbox.lifecycle import snapshots


def _create_tables(dynamodb) -> Tuple[Any, Any]:
    main_table = dynamodb.create_table(
        TableName="main-table",
        KeySchema=[{"AttributeName": "project", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "project", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    meta_table = dynamodb.create_table(
        TableName="meta-table",
        KeySchema=[
            {"AttributeName": "project", "KeyType": "HASH"},
            {"AttributeName": "volumeId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "project", "AttributeType": "S"},
            {"AttributeName": "volumeId", "AttributeType": "S"},
            {"AttributeName": "snapshotId", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "SnapshotIndex",
                "KeySchema": [{"AttributeName": "snapshotId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    main_table.wait_until_exists()
    meta_table.wait_until_exists()
    return main_table, meta_table


def _register_image(ec2_client) -> str:
    resp = ec2_client.register_image(
        Name="base-ami",
        Architecture="x86_64",
        RootDeviceName="/dev/sda1",
        VirtualizationType="hvm",
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {"VolumeSize": 8, "VolumeType": "gp3"},
            }
        ],
    )
    return resp["ImageId"]


@pytest.fixture
def snapshot_env() -> Dict[str, Any]:
    with mock_aws():
        region = "us-east-1"
        ec2_client = boto3.client("ec2", region_name=region)
        ec2_resource = boto3.resource("ec2", region_name=region)
        dynamodb = boto3.resource("dynamodb", region_name=region)
        main_table, meta_table = _create_tables(dynamodb)
        yield {
            "ec2_client": ec2_client,
            "ec2_resource": ec2_resource,
            "main_table": main_table,
            "meta_table": meta_table,
        }


def _create_instance(ec2_resource, ec2_client, project: str) -> Any:
    image_id = _register_image(ec2_client)
    instance = ec2_resource.create_instances(
        ImageId=image_id,
        MinCount=1,
        MaxCount=1,
    )[0]
    instance.create_tags(Tags=[{"Key": "Project", "Value": project}])
    return instance


def test_create_snapshots_creates_records(snapshot_env):
    instance = _create_instance(
        snapshot_env["ec2_resource"],
        snapshot_env["ec2_client"],
        "proj-one",
    )
    event = {"detail": {"instance-id": instance.id, "state": "shutting-down"}}

    snapshots.create_snapshots(
        event,
        ec2_resource=snapshot_env["ec2_resource"],
        main_table=snapshot_env["main_table"],
        meta_table=snapshot_env["meta_table"],
    )

    main_item = snapshot_env["main_table"].get_item(Key={"project": "proj-one"})[
        "Item"
    ]
    assert main_item["Status"] == "SNAPSHOTTING"
    assert main_item["VolumeCount"] == 1

    meta_items = snapshot_env["meta_table"].query(
        KeyConditionExpression=Key("project").eq("proj-one")
    )["Items"]
    assert len(meta_items) == 1
    assert meta_items[0]["State"] == "PENDING"


def test_create_image_registers_new_ami(snapshot_env):
    instance = _create_instance(
        snapshot_env["ec2_resource"],
        snapshot_env["ec2_client"],
        "proj-two",
    )
    snapshots.create_snapshots(
        {"detail": {"instance-id": instance.id, "state": "shutting-down"}},
        ec2_resource=snapshot_env["ec2_resource"],
        main_table=snapshot_env["main_table"],
        meta_table=snapshot_env["meta_table"],
    )
    meta_items = snapshot_env["meta_table"].query(
        KeyConditionExpression=Key("project").eq("proj-two")
    )["Items"]
    snap_id = meta_items[0]["snapshotId"]

    event = {
        "detail": {
            "snapshot_id": f"arn:aws:ec2:us-east-1::snapshot/{snap_id}",
            "result": "succeeded",
        }
    }
    snapshots.create_image(
        event,
        ec2_client=snapshot_env["ec2_client"],
        ec2_resource=snapshot_env["ec2_resource"],
        main_table=snapshot_env["main_table"],
        meta_table=snapshot_env["meta_table"],
    )

    main_item = snapshot_env["main_table"].get_item(Key={"project": "proj-two"})[
        "Item"
    ]
    assert main_item["Status"] == "IMAGING"
    assert main_item["AMI"].startswith("ami-")

    meta_items = snapshot_env["meta_table"].query(
        KeyConditionExpression=Key("project").eq("proj-two")
    )["Items"]
    assert meta_items[0]["State"] == "COMPLETED"


def test_mark_ready_clears_meta_and_sets_ready(snapshot_env):
    snapshot_env["main_table"].put_item(
        Item={"project": "proj-ready", "AMI": "ami-ready", "Status": "IMAGING"}
    )
    snapshot_env["meta_table"].put_item(
        Item={
            "project": "proj-ready",
            "volumeId": "vol-1",
            "snapshotId": "snap-1",
            "State": "COMPLETED",
        }
    )

    snapshots.mark_ready(
        {"detail": {"ImageId": "ami-ready", "State": "available"}},
        main_table=snapshot_env["main_table"],
        meta_table=snapshot_env["meta_table"],
    )

    meta_items = snapshot_env["meta_table"].query(
        KeyConditionExpression=Key("project").eq("proj-ready")
    )["Items"]
    assert meta_items == []

    main_item = snapshot_env["main_table"].get_item(
        Key={"project": "proj-ready"}
    )["Item"]
    assert main_item["Status"] == "READY"


@pytest.mark.parametrize("describe_returns_empty", [False, True])
def test_delete_volume_removes_completed_volume(snapshot_env, describe_returns_empty, monkeypatch):
    resp = snapshot_env["ec2_client"].create_volume(
        AvailabilityZone="us-east-1a", Size=1, VolumeType="gp3"
    )
    vol_id = resp["VolumeId"]
    snapshot_env["meta_table"].put_item(
        Item={
            "project": "proj-del",
            "volumeId": vol_id,
            "snapshotId": "snap-1",
            "State": "COMPLETED",
        }
    )

    snapshots.delete_volume(
        {"detail": {"volume-id": vol_id, "state": "available"}},
        ec2_client=snapshot_env["ec2_client"],
        main_table=snapshot_env["main_table"],
        meta_table=snapshot_env["meta_table"],
    )

    if describe_returns_empty:
        def _describe_volumes(**_kwargs):
            return {"Volumes": []}
        monkeypatch.setattr(snapshot_env["ec2_client"], "describe_volumes", _describe_volumes)

    try:
        resp = snapshot_env["ec2_client"].describe_volumes(VolumeIds=[vol_id])
    except ClientError as exc:
        assert exc.response["Error"]["Code"] in {
            "InvalidVolume.NotFound",
            "InvalidVolumeID.NotFound",
        }
    else:
        assert resp["Volumes"] == []


def test_delete_volume_sets_error_when_incomplete(snapshot_env):
    snapshot_env["main_table"].put_item(
        Item={"project": "proj-err", "Status": "SNAPSHOTTING"}
    )
    snapshot_env["meta_table"].put_item(
        Item={
            "project": "proj-err",
            "volumeId": "vol-err",
            "snapshotId": "snap-err",
            "State": "PENDING",
        }
    )

    snapshots.delete_volume(
        {"detail": {"volume-id": "vol-err", "state": "available"}},
        ec2_client=snapshot_env["ec2_client"],
        main_table=snapshot_env["main_table"],
        meta_table=snapshot_env["meta_table"],
    )

    main_item = snapshot_env["main_table"].get_item(Key={"project": "proj-err"})[
        "Item"
    ]
    assert main_item["Status"] == "ERROR"


def test_create_snapshots_logs_missing_instance_id(snapshot_env, caplog):
    with caplog.at_level(logging.WARNING):
        snapshots.create_snapshots(
            {"detail": {"state": "shutting-down"}},
            ec2_resource=snapshot_env["ec2_resource"],
            main_table=snapshot_env["main_table"],
            meta_table=snapshot_env["meta_table"],
        )

    assert "missing instance id in shutdown event" in caplog.text


def test_create_snapshots_logs_missing_project_tag(snapshot_env, caplog):
    image_id = _register_image(snapshot_env["ec2_client"])
    instance = snapshot_env["ec2_resource"].create_instances(
        ImageId=image_id,
        MinCount=1,
        MaxCount=1,
    )[0]

    with caplog.at_level(logging.WARNING):
        snapshots.create_snapshots(
            {"detail": {"instance-id": instance.id, "state": "shutting-down"}},
            ec2_resource=snapshot_env["ec2_resource"],
            main_table=snapshot_env["main_table"],
            meta_table=snapshot_env["meta_table"],
        )

    assert "instance missing project tag" in caplog.text


def test_create_image_logs_missing_snapshot_arn(snapshot_env, caplog):
    with caplog.at_level(logging.WARNING):
        snapshots.create_image(
            {"detail": {"result": "succeeded"}},
            ec2_client=snapshot_env["ec2_client"],
            ec2_resource=snapshot_env["ec2_resource"],
            main_table=snapshot_env["main_table"],
            meta_table=snapshot_env["meta_table"],
        )

    assert "no snapshot arn in event" in caplog.text


def test_create_image_logs_missing_meta_entry(snapshot_env, caplog):
    with caplog.at_level(logging.WARNING):
        snapshots.create_image(
            {
                "detail": {
                    "snapshot_id": "arn:aws:ec2:us-east-1::snapshot/snap-missing",
                    "result": "succeeded",
                }
            },
            ec2_client=snapshot_env["ec2_client"],
            ec2_resource=snapshot_env["ec2_resource"],
            main_table=snapshot_env["main_table"],
            meta_table=snapshot_env["meta_table"],
        )

    assert "no meta entry found for snapshot" in caplog.text


def test_create_image_logs_missing_main_entry(snapshot_env, caplog):
    snapshot_env["meta_table"].put_item(
        Item={
            "project": "proj-missing",
            "volumeId": "vol-missing",
            "instanceId": "i-missing",
            "deviceName": "/dev/sda1",
            "snapshotId": "snap-missing",
            "State": "PENDING",
        }
    )

    with caplog.at_level(logging.WARNING):
        snapshots.create_image(
            {
                "detail": {
                    "snapshot_id": "arn:aws:ec2:us-east-1::snapshot/snap-missing",
                    "result": "succeeded",
                }
            },
            ec2_client=snapshot_env["ec2_client"],
            ec2_resource=snapshot_env["ec2_resource"],
            main_table=snapshot_env["main_table"],
            meta_table=snapshot_env["meta_table"],
        )

    assert "no main entry found" in caplog.text


def test_create_image_logs_old_ami_not_found(snapshot_env, caplog):
    resp = snapshot_env["ec2_client"].create_volume(
        AvailabilityZone="us-east-1a", Size=1, VolumeType="gp3"
    )
    vol_id = resp["VolumeId"]
    snap_resp = snapshot_env["ec2_client"].create_snapshot(VolumeId=vol_id)
    snap_id = snap_resp["SnapshotId"]

    snapshot_env["main_table"].put_item(
        Item={
            "project": "proj-old-ami",
            "VolumeCount": 1,
            "Status": "SNAPSHOTTING",
            "AMI": "ami-missing",
            "RootDeviceName": "/dev/sda1",
            "Architecture": "x86_64",
            "VirtualizationType": "hvm",
        }
    )
    snapshot_env["meta_table"].put_item(
        Item={
            "project": "proj-old-ami",
            "volumeId": vol_id,
            "instanceId": "i-old-ami",
            "deviceName": "/dev/sda1",
            "snapshotId": snap_id,
            "State": "COMPLETED",
        }
    )

    with caplog.at_level(logging.WARNING):
        snapshots.create_image(
            {
                "detail": {
                    "snapshot_id": f"arn:aws:ec2:us-east-1::snapshot/{snap_id}",
                    "result": "succeeded",
                }
            },
            ec2_client=snapshot_env["ec2_client"],
            ec2_resource=snapshot_env["ec2_resource"],
            main_table=snapshot_env["main_table"],
            meta_table=snapshot_env["meta_table"],
        )

    assert "old ami not found" in caplog.text


def test_mark_ready_logs_missing_ami_id(snapshot_env, caplog):
    with caplog.at_level(logging.WARNING):
        snapshots.mark_ready(
            {"detail": {"State": "available"}},
            main_table=snapshot_env["main_table"],
            meta_table=snapshot_env["meta_table"],
        )

    assert "missing ami id in event" in caplog.text


def test_mark_ready_logs_missing_main_entry(snapshot_env, caplog):
    with caplog.at_level(logging.WARNING):
        snapshots.mark_ready(
            {"detail": {"ImageId": "ami-missing", "State": "available"}},
            main_table=snapshot_env["main_table"],
            meta_table=snapshot_env["meta_table"],
        )

    assert "no main entry found for ami" in caplog.text


def test_delete_volume_logs_missing_volume_id(snapshot_env, caplog):
    with caplog.at_level(logging.WARNING):
        snapshots.delete_volume(
            {"detail": {"state": "available"}},
            ec2_client=snapshot_env["ec2_client"],
            main_table=snapshot_env["main_table"],
            meta_table=snapshot_env["meta_table"],
        )

    assert "missing volume id in event" in caplog.text


def test_cleanup_ami_and_snapshots_deletes_resources(snapshot_env, monkeypatch):
    vol_resp = snapshot_env["ec2_client"].create_volume(
        AvailabilityZone="us-east-1a", Size=1, VolumeType="gp3"
    )
    vol_id = vol_resp["VolumeId"]
    snap_resp = snapshot_env["ec2_client"].create_snapshot(VolumeId=vol_id)
    snap_id = snap_resp["SnapshotId"]

    image_resp = snapshot_env["ec2_client"].register_image(
        Name="cleanup-ami",
        Architecture="x86_64",
        RootDeviceName="/dev/sda1",
        VirtualizationType="hvm",
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "SnapshotId": snap_id,
                    "VolumeSize": 1,
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,
                },
            }
        ],
    )
    ami_id = image_resp["ImageId"]

    deleted_snapshots = []

    class _SnapshotStub:
        def __init__(self, snapshot_id: str) -> None:
            self.snapshot_id = snapshot_id

        def delete(self) -> None:
            deleted_snapshots.append(self.snapshot_id)

    monkeypatch.setattr(
        snapshot_env["ec2_resource"],
        "Snapshot",
        lambda snapshot_id: _SnapshotStub(snapshot_id),
    )

    image = snapshot_env["ec2_resource"].Image(ami_id)
    image_snapshot_ids = [
        mapping.get("Ebs", {}).get("SnapshotId")
        for mapping in image.block_device_mappings
        if mapping.get("Ebs", {}).get("SnapshotId")
    ]

    snapshots.cleanup_ami_and_snapshots(
        ami_id,
        ec2_resource=snapshot_env["ec2_resource"],
        ec2_client=snapshot_env["ec2_client"],
        config=snapshots.SnapshotConfig(cleanup_max_attempts=1, cleanup_wait_seconds=0),
    )

    try:
        resp = snapshot_env["ec2_client"].describe_images(ImageIds=[ami_id])
    except ClientError as exc:
        assert exc.response["Error"]["Code"] in {
            "InvalidAMIID.NotFound",
            "InvalidAMIID.Malformed",
        }
    else:
        assert resp["Images"] == []

    assert deleted_snapshots == image_snapshot_ids


def test_cleanup_ami_and_snapshots_times_out(snapshot_env, monkeypatch):
    image_resp = snapshot_env["ec2_client"].register_image(
        Name="timeout-ami",
        Architecture="x86_64",
        RootDeviceName="/dev/sda1",
        VirtualizationType="hvm",
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {"VolumeSize": 1, "VolumeType": "gp3"},
            }
        ],
    )
    ami_id = image_resp["ImageId"]

    def _describe_images(**_kwargs):
        return {"Images": [{"ImageId": ami_id}]}

    monkeypatch.setattr(snapshot_env["ec2_client"], "describe_images", _describe_images)

    with pytest.raises(RuntimeError, match="Timed out waiting for AMI"):
        snapshots.cleanup_ami_and_snapshots(
            ami_id,
            ec2_resource=snapshot_env["ec2_resource"],
            ec2_client=snapshot_env["ec2_client"],
            config=snapshots.SnapshotConfig(
                cleanup_max_attempts=1,
                cleanup_wait_seconds=0,
            ),
        )


def test_cleanup_ami_and_snapshots_handles_invalid_ami_not_found(
    snapshot_env, monkeypatch
):
    image_resp = snapshot_env["ec2_client"].register_image(
        Name="not-found-ami",
        Architecture="x86_64",
        RootDeviceName="/dev/sda1",
        VirtualizationType="hvm",
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {"VolumeSize": 1, "VolumeType": "gp3"},
            }
        ],
    )
    ami_id = image_resp["ImageId"]

    def _describe_images(**_kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "InvalidAMIID.NotFound",
                    "Message": f"The image id '{ami_id}' does not exist",
                }
            },
            "DescribeImages",
        )

    monkeypatch.setattr(snapshot_env["ec2_client"], "describe_images", _describe_images)

    snapshots.cleanup_ami_and_snapshots(
        ami_id,
        ec2_resource=snapshot_env["ec2_resource"],
        ec2_client=snapshot_env["ec2_client"],
        config=snapshots.SnapshotConfig(cleanup_max_attempts=1, cleanup_wait_seconds=0),
    )


def test_cleanup_ami_and_snapshots_reraises_unexpected_describe_error(
    snapshot_env, monkeypatch
):
    image_resp = snapshot_env["ec2_client"].register_image(
        Name="unexpected-error-ami",
        Architecture="x86_64",
        RootDeviceName="/dev/sda1",
        VirtualizationType="hvm",
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {"VolumeSize": 1, "VolumeType": "gp3"},
            }
        ],
    )
    ami_id = image_resp["ImageId"]

    def _describe_images(**_kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "UnauthorizedOperation",
                    "Message": "Not authorized",
                }
            },
            "DescribeImages",
        )

    monkeypatch.setattr(snapshot_env["ec2_client"], "describe_images", _describe_images)

    with pytest.raises(ClientError, match="UnauthorizedOperation"):
        snapshots.cleanup_ami_and_snapshots(
            ami_id,
            ec2_resource=snapshot_env["ec2_resource"],
            ec2_client=snapshot_env["ec2_client"],
            config=snapshots.SnapshotConfig(
                cleanup_max_attempts=1,
                cleanup_wait_seconds=0,
            ),
        )
