"""Status command helpers shared by the CLI and CLI Lambda."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..cli_lambda.contracts import CliRequestEnvelope, build_event
from ..cli_protocol import CliAction, CliEventType
from ..devbox_manager import DevBoxManager
from ..remote_client import RemoteInvocationError, invoke_action


def build_status_payload(project: str | None) -> dict[str, Any]:
    """Build the remote ``status`` request payload.

    Parameters
    ----------
    project : str | None
        Optional project filter supplied by the CLI.

    Returns
    -------
    dict[str, Any]
        Action payload ready for the shared remote client.
    """
    return {"project": project}


def run_status_command(
    project: str | None,
    param_prefix: str,
    console: Any,
) -> None:
    """Run the remote ``status`` command and render the result.

    Parameters
    ----------
    project : str | None
        Optional project filter supplied by the CLI.
    param_prefix : str
        Parameter prefix used to discover the CLI Lambda endpoint.
    console : Any
        Console-like object that renders status tables and warnings.

    Raises
    ------
    RemoteInvocationError
        Raised when the remote action or result parsing fails.
    """
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
    """Convert serialized status timestamps back to ``datetime`` objects.

    Parameters
    ----------
    payload : dict[str, Any]
        JSON-decoded ``status`` result payload.

    Returns
    -------
    dict[str, Any]
        The same payload mapping with timestamp fields rehydrated in place.

    Raises
    ------
    RemoteInvocationError
        Raised when the payload shape is invalid or timestamp fields are not ISO
        8601 strings.
    """
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
    """Validate and normalize the ``status`` action payload.

    Parameters
    ----------
    payload : dict[str, Any]
        Action payload decoded from the request envelope.

    Returns
    -------
    str | None
        Normalized project filter.

    Raises
    ------
    ValueError
        Raised when ``project`` is neither ``None`` nor a string.
    """
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
    """Serialize status data into JSON-compatible structures.

    Parameters
    ----------
    instances : list[dict[str, Any]]
        Instance records returned by ``DevBoxManager``.
    volumes : list[dict[str, Any]]
        Volume records returned by ``DevBoxManager``.
    snapshots : list[dict[str, Any]]
        Snapshot records returned by ``DevBoxManager``.

    Returns
    -------
    dict[str, Any]
        JSON-serializable ``status`` result payload.
    """
    return {
        "instances": [_serialize_record(instance) for instance in instances],
        "volumes": [_serialize_record(volume) for volume in volumes],
        "snapshots": [_serialize_record(snapshot) for snapshot in snapshots],
    }


def handle_status_action(envelope: CliRequestEnvelope) -> list[dict[str, Any]]:
    """Execute the remote ``status`` action.

    Parameters
    ----------
    envelope : CliRequestEnvelope
        Validated request envelope for the ``status`` action.

    Returns
    -------
    list[dict[str, Any]]
        Result and terminal success events for the NDJSON response stream.

    Raises
    ------
    ValueError
        Raised when the action payload is invalid.
    """
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
    """Serialize one ``status`` record into JSON-compatible values.

    Parameters
    ----------
    record : dict[str, Any]
        One instance, volume, or snapshot record.

    Returns
    -------
    dict[str, Any]
        Record with ``datetime`` values converted to ISO 8601 strings.
    """
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
    """Validate the top-level ``status`` payload collections.

    Parameters
    ----------
    payload : dict[str, Any]
        Result payload to validate.

    Returns
    -------
    tuple[list[Any], list[Any], list[Any]]
        The ``instances``, ``volumes``, and ``snapshots`` collections.

    Raises
    ------
    RemoteInvocationError
        Raised when any required top-level collection is missing or malformed.
    """
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
