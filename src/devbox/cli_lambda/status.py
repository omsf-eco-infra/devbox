"""Status action support for the CLI Lambda."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..cli_protocol import CliAction, CliEventType
from ..devbox_manager import DevBoxManager
from .contracts import CliRequestEnvelope, build_event


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
        build_event(CliEventType.RESULT, CliAction.STATUS, "Status data ready", result_data),
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
