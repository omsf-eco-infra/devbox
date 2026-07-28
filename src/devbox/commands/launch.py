"""Launch command helpers shared by the CLI and CLI Lambda."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator, Optional

from ..cli_lambda.contracts import CliRequestEnvelope, build_event
from ..cli_protocol import CliAction, CliEventType
from ..launch import LaunchRequest, LaunchResult, LaunchUpdate, iter_launch_workflow
from ..remote_client import invoke_action, normalize_param_prefix

MAX_USERDATA_BYTES = 16_384
LAUNCH_READ_TIMEOUT_SECONDS = 930


def validate_userdata(userdata: Optional[str]) -> Optional[str]:
    """Validate optional raw EC2 userdata and return it unchanged."""
    if userdata is None:
        return None
    if not isinstance(userdata, str):
        raise ValueError("The `launch` payload field `userdata` must be a string or null.")
    if len(userdata.encode("utf-8")) > MAX_USERDATA_BYTES:
        raise ValueError(
            f"EC2 userdata must not exceed {MAX_USERDATA_BYTES} UTF-8 bytes."
        )
    return userdata


def read_userdata_file(userdata_file: Optional[str]) -> Optional[str]:
    """Read and validate a local userdata file for request inlining."""
    if userdata_file is None:
        return None
    return validate_userdata(Path(userdata_file).read_text(encoding="utf-8"))


def build_launch_payload(
    *,
    project: str,
    instance_type: Optional[str],
    key_pair: Optional[str],
    volume_size: int,
    base_ami: Optional[str],
    userdata: Optional[str],
    assign_dns: bool,
    dns_subdomain: Optional[str],
) -> dict[str, Any]:
    """Build the wire payload for a remote launch request."""
    return {
        "project": project,
        "instance_type": instance_type,
        "key_pair": key_pair,
        "volume_size": volume_size,
        "base_ami": base_ami,
        "userdata": validate_userdata(userdata),
        "assign_dns": assign_dns,
        "dns_subdomain": dns_subdomain,
    }


def run_launch_command(
    *,
    project: str,
    instance_type: Optional[str],
    key_pair: Optional[str],
    volume_size: int,
    base_ami: Optional[str],
    param_prefix: str,
    userdata_file: Optional[str],
    assign_dns: bool,
    dns_subdomain: Optional[str],
    console: Any,
) -> None:
    """Invoke the remote launch action and render its final result."""
    userdata = read_userdata_file(userdata_file)
    result = invoke_action(
        action=CliAction.LAUNCH,
        payload=build_launch_payload(
            project=project,
            instance_type=instance_type,
            key_pair=key_pair,
            volume_size=volume_size,
            base_ami=base_ami,
            userdata=userdata,
            assign_dns=assign_dns,
            dns_subdomain=dns_subdomain,
        ),
        param_prefix=param_prefix,
        console=console,
        read_timeout=LAUNCH_READ_TIMEOUT_SECONDS,
    )
    console.print_launch_result(result)


def _optional_string(payload: dict[str, Any], name: str) -> Optional[str]:
    value = payload.get(name)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"The `launch` payload field `{name}` must be a string or null.")
    return value


def validate_launch_payload(payload: dict[str, Any], param_prefix: str) -> LaunchRequest:
    """Validate a launch payload and return normalized workflow inputs."""
    project = payload.get("project")
    if not isinstance(project, str) or not project.replace("-", "").isalnum():
        raise ValueError(
            "The `launch` payload field `project` must be alphanumeric with optional hyphens."
        )

    volume_size = payload.get("volume_size", 0)
    if isinstance(volume_size, bool) or not isinstance(volume_size, int) or volume_size < 0:
        raise ValueError(
            "The `launch` payload field `volume_size` must be a non-negative integer."
        )
    assign_dns = payload.get("assign_dns", True)
    if not isinstance(assign_dns, bool):
        raise ValueError("The `launch` payload field `assign_dns` must be a boolean.")

    return LaunchRequest(
        project=project,
        instance_type=_optional_string(payload, "instance_type"),
        key_pair=_optional_string(payload, "key_pair"),
        volume_size=volume_size,
        base_ami=_optional_string(payload, "base_ami"),
        param_prefix=param_prefix,
        userdata=validate_userdata(payload.get("userdata")),
        assign_dns=assign_dns,
        dns_subdomain=_optional_string(payload, "dns_subdomain"),
    )


def configured_param_prefix(envelope_prefix: str) -> str:
    """Return the configured Lambda prefix after rejecting request mismatches."""
    configured = os.environ.get("DEVBOX_PARAM_PREFIX")
    if not configured:
        raise RuntimeError("CLI Lambda is missing DEVBOX_PARAM_PREFIX configuration.")
    normalized_configured = normalize_param_prefix(configured)
    if normalize_param_prefix(envelope_prefix) != normalized_configured:
        raise ValueError("Request parameter prefix does not match the CLI Lambda configuration.")
    return normalized_configured


def handle_launch_action(envelope: CliRequestEnvelope) -> Iterator[dict[str, Any]]:
    """Execute launch and lazily map workflow updates to NDJSON events."""
    param_prefix = configured_param_prefix(envelope.param_prefix)
    launch_request = validate_launch_payload(envelope.payload, param_prefix)
    saw_result = False
    for update in iter_launch_workflow(launch_request):
        if isinstance(update, LaunchUpdate):
            event_type = (
                CliEventType.WARNING
                if update.kind == "warning"
                else CliEventType.PROGRESS
            )
            yield build_event(event_type, CliAction.LAUNCH, update.message)
        elif isinstance(update, LaunchResult):
            saw_result = True
            yield build_event(
                CliEventType.RESULT,
                CliAction.LAUNCH,
                "Instance launched",
                update.to_dict(),
            )
    if not saw_result:
        raise RuntimeError("Launch workflow completed without a result.")
    yield build_event(CliEventType.SUCCESS, CliAction.LAUNCH, "Launch complete")
