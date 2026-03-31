"""Remote invocation helpers for Lambda-backed CLI commands."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import uuid4

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from . import utils
from .cli_protocol import (
    EVENT_TYPES,
    FUNCTION_URL_PARAMETER_SUFFIX,
    NDJSON_MIME_TYPE,
    REQUEST_VERSION,
    CliAction,
    CliEventType,
    normalize_action,
)


class RemoteInvocationError(utils.DevBoxError):
    """Raised when a remote CLI Lambda invocation fails."""


def normalize_param_prefix(param_prefix: str) -> str:
    """Normalize a CLI parameter prefix to `/prefix` form."""
    stripped = param_prefix.strip("/")
    if not stripped:
        return "/devbox"
    return f"/{stripped}"


def get_cli_function_url(param_prefix: str) -> str:
    """Resolve the CLI Lambda Function URL from SSM."""
    normalized_prefix = normalize_param_prefix(param_prefix)
    parameter_name = f"{normalized_prefix}{FUNCTION_URL_PARAMETER_SUFFIX}"
    try:
        return utils.get_ssm_parameter(parameter_name)
    except ValueError as exc:
        raise RemoteInvocationError(
            f"Failed to resolve CLI function URL: {exc}"
        ) from exc


def get_function_url_region(function_url: str) -> str:
    """Extract the AWS region from a Lambda Function URL."""
    parsed = urlparse(function_url)
    hostname = parsed.hostname
    if parsed.scheme != "https" or not hostname:
        raise RemoteInvocationError(f"Invalid Lambda Function URL: {function_url}")

    parts = hostname.split(".")
    if len(parts) < 5 or parts[-4] != "lambda-url" or parts[-2:] != ["on", "aws"]:
        raise RemoteInvocationError(f"Invalid Lambda Function URL: {function_url}")

    return parts[-3]


def build_request_envelope(
    action: CliAction | str,
    payload: dict[str, Any],
    param_prefix: str,
) -> dict[str, Any]:
    """Create a request envelope for the CLI Lambda."""
    return {
        "version": REQUEST_VERSION,
        "action": normalize_action(action),
        "request_id": str(uuid4()),
        "param_prefix": normalize_param_prefix(param_prefix),
        "payload": payload,
    }


def sign_request(
    method: str,
    url: str,
    body: str,
    region: str,
    session: boto3.session.Session | None = None,
) -> dict[str, str]:
    """Apply SigV4 signing headers for a Lambda Function URL request."""
    active_session = session or boto3.Session()
    credentials = active_session.get_credentials()
    if credentials is None:
        raise RemoteInvocationError("No AWS credentials available for request signing.")

    request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers={
            "Accept": NDJSON_MIME_TYPE,
            "Content-Type": "application/json",
        },
    )
    SigV4Auth(credentials.get_frozen_credentials(), "lambda", region).add_auth(request)
    return dict(request.headers.items())


def parse_event_line(line: str) -> dict[str, Any]:
    """Parse and validate one NDJSON event line."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RemoteInvocationError(f"Malformed NDJSON event: {line}") from exc

    if not isinstance(event, dict):
        raise RemoteInvocationError("Malformed NDJSON event: expected an object.")

    event_type = event.get("type")
    if event_type not in EVENT_TYPES:
        raise RemoteInvocationError(f"Unknown event type: {event_type}")

    action = event.get("action")
    if not isinstance(action, str) or not action:
        raise RemoteInvocationError("Malformed event: missing action.")

    message = event.get("message")
    if not isinstance(message, str):
        raise RemoteInvocationError("Malformed event: missing message.")

    data = event.get("data", {})
    if not isinstance(data, dict):
        raise RemoteInvocationError("Malformed event: data must be an object.")

    return {
        "type": event_type,
        "action": action,
        "message": message,
        "data": data,
    }


def iter_response_events(response: requests.Response) -> Iterable[dict[str, Any]]:
    """Yield validated NDJSON events from a Function URL response."""
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        yield parse_event_line(line)


def invoke_action(
    action: CliAction | str,
    payload: dict[str, Any],
    param_prefix: str,
    console: Any | None = None,
) -> dict[str, Any]:
    """Invoke a CLI Lambda action and return the result payload."""
    action_name = normalize_action(action)
    function_url = get_cli_function_url(param_prefix)
    region = get_function_url_region(function_url)
    body = json.dumps(build_request_envelope(action, payload, param_prefix))
    headers = sign_request("POST", function_url, body, region)

    response = requests.post(
        function_url,
        data=body,
        headers=headers,
        stream=True,
        timeout=30,
    )
    try:
        if response.status_code >= 400:
            raise RemoteInvocationError(
                f"Remote {action_name} request failed: HTTP {response.status_code}"
            )

        result_data: dict[str, Any] | None = None
        saw_terminal = False

        for event in iter_response_events(response):
            if event["action"] != action_name:
                raise RemoteInvocationError(
                    "Remote "
                    f"{action_name} response included an event for unexpected action "
                    f"{event['action']}."
                )
            if saw_terminal:
                raise RemoteInvocationError(
                    f"Remote {action_name} response included events after a terminal event."
                )

            event_type = event["type"]
            if event_type == CliEventType.WARNING.value and console is not None:
                console.print_warning(event["message"])
                continue

            if event_type == CliEventType.RESULT.value:
                if result_data is not None:
                    raise RemoteInvocationError(
                        f"Remote {action_name} response included multiple result events."
                    )
                result_data = event["data"]
                continue

            if event_type == CliEventType.ERROR.value:
                saw_terminal = True
                raise RemoteInvocationError(event["message"])

            if event_type == CliEventType.SUCCESS.value:
                saw_terminal = True

        if not saw_terminal:
            raise RemoteInvocationError(
                f"Remote {action_name} response ended without a terminal event."
            )

        if result_data is None:
            raise RemoteInvocationError(
                f"Remote {action_name} response did not include a result event."
            )

        return result_data
    finally:
        response.close()


def rehydrate_status_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert serialized status timestamps back to datetimes."""
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


def fetch_remote_status(
    project: str | None,
    param_prefix: str,
    console: Any | None = None,
) -> dict[str, Any]:
    """Fetch status data from the CLI Lambda and rehydrate timestamps."""
    payload = {"project": project}
    result = invoke_action(
        action=CliAction.STATUS,
        payload=payload,
        param_prefix=param_prefix,
        console=console,
    )
    return rehydrate_status_result(result)
