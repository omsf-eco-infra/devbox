"""Status command helpers shared by the CLI and CLI Lambda."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..cli_lambda.contracts import CliRequestEnvelope, build_event
from ..cli_protocol import CliAction, CliEventType
from ..devbox_manager import DevBoxManager
from ..remote_client import RemoteInvocationError, invoke_action


def build_status_payload(project: str | None) -> dict[str, Any]:
    """Build the status request payload."""
    return {"project": project}


def run_status_command(
    project: str | None,
    param_prefix: str,
    console: Any,
) -> None:
    """Fetch and render status output for the CLI."""
    result = invoke_action(
        action=CliAction.STATUS,
        payload=build_status_payload(project),
        param_prefix=param_prefix,
        console=console,
    )
    rehydrate_status_result(result)
    console.print_instances(result["instances"])
    console.print_volumes(result["volumes"])
    console.print_snapshots(result["snapshots"])


def rehydrate_status_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert serialized status timestamps back to datetimes."""
    instances, volumes, snapshots = _get_status_collections(payload)

    for instance in instances:
        if not isinstance(instance, dict):
            raise RemoteInvocationError("Remote status instances payload is malformed.")
        launch_time = instance.get("LaunchTime")
        if launch_time is not None:
            if not isinstance(launch_time, str):
                raise RemoteInvocationError(
                    "Remote status LaunchTime must be an ISO 8601 string."
                )
            instance["LaunchTime"] = datetime.fromisoformat(launch_time)

    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            raise RemoteInvocationError("Remote status snapshots payload is malformed.")
        start_time = snapshot.get("StartTime")
        if start_time is not None:
            if not isinstance(start_time, str):
                raise RemoteInvocationError(
                    "Remote status StartTime must be an ISO 8601 string."
                )
            snapshot["StartTime"] = datetime.fromisoformat(start_time)

    for volume in volumes:
        if not isinstance(volume, dict):
            raise RemoteInvocationError("Remote status volumes payload is malformed.")

    return payload


def validate_status_payload(payload: dict[str, Any]) -> str | None:
    """Validate and normalize the `status` payload."""
    project = payload.get("project")
    if project is not None and not isinstance(project, str):
        raise ValueError(
            "The `status` payload field `project` must be a string or null."
        )
    return project


def serialize_status_payload(
    instances: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Serialize status results into JSON-compatible data."""
    return {
        "instances": [_serialize_record(instance) for instance in instances],
        "volumes": [_serialize_record(volume) for volume in volumes],
        "snapshots": [_serialize_record(snapshot) for snapshot in snapshots],
    }


def handle_status_action(envelope: CliRequestEnvelope) -> list[dict[str, Any]]:
    """Execute the `status` action and return NDJSON events."""
    project = validate_status_payload(envelope.payload)
    manager_prefix = envelope.param_prefix.strip("/") or "devbox"
    manager = DevBoxManager(prefix=manager_prefix)

    result_data = serialize_status_payload(
        instances=manager.list_instances(project),
        volumes=manager.list_volumes(project),
        snapshots=manager.list_snapshots(project),
    )
    return [
        build_event(
            CliEventType.RESULT,
            CliAction.STATUS,
            "Status data ready",
            result_data,
        ),
        build_event(CliEventType.SUCCESS, CliAction.STATUS, "Status complete"),
    ]


def _serialize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Serialize one result record into JSON-compatible values."""
    serialized: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
        else:
            serialized[key] = value
    return serialized


def _get_status_collections(
    payload: dict[str, Any],
) -> tuple[list[Any], list[Any], list[Any]]:
    """Validate the top-level shape of a status result payload."""
    instances = payload.get("instances")
    volumes = payload.get("volumes")
    snapshots = payload.get("snapshots")
    if (
        not isinstance(instances, list)
        or not isinstance(volumes, list)
        or not isinstance(snapshots, list)
    ):
        raise RemoteInvocationError(
            "Remote status result did not include the expected collections."
        )
    return instances, volumes, snapshots
