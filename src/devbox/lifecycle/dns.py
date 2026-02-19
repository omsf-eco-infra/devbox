"""DNS lifecycle handlers for devbox Lambda and CLI use."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from boto3.dynamodb.conditions import Attr

from devbox.dns import DNSManager

if TYPE_CHECKING:  # pragma: no cover
    from mypy_boto3_dynamodb.service_resource import Table as DynamoDBTable
    from mypy_boto3_ec2.client import EC2Client
    from mypy_boto3_ssm.client import SSMClient
else:
    DynamoDBTable = Any
    EC2Client = Any
    SSMClient = Any

_logger = logging.getLogger(__name__)


def _extract_instance_id(event: Dict[str, Any]) -> Optional[str]:
    return event.get("detail", {}).get("instance-id")


def _should_process_event(event: Dict[str, Any]) -> bool:
    state = str(event.get("detail", {}).get("state", "")).lower()
    return state in {"shutting-down", "terminated"}


def _get_project_from_instance(ec2_client: EC2Client, instance_id: str) -> Optional[str]:
    try:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
    except Exception as exc:  # pragma: no cover - defensive guard for AWS edge cases
        _logger.warning("unable to describe instance %s: %s", instance_id, exc)
        return None

    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            for tag in instance.get("Tags", []):
                if tag.get("Key") == "Project":
                    project = str(tag.get("Value", "")).strip()
                    if project:
                        return project
    return None


def _get_project_item(table: DynamoDBTable, project: Optional[str]) -> Optional[Dict[str, Any]]:
    if not project:
        return None
    try:
        response = table.get_item(Key={"project": project})
        return response.get("Item")
    except Exception as exc:  # pragma: no cover - defensive guard for AWS edge cases
        _logger.warning("unable to load project record for %s: %s", project, exc)
        return None


def _scan_project_by_instance_id(table: DynamoDBTable, instance_id: str) -> Optional[Dict[str, Any]]:
    kwargs: Dict[str, Any] = {
        "FilterExpression": Attr("InstanceId").eq(instance_id),
        "ProjectionExpression": "#project, CNAMEDomain, InstanceId",
        "ExpressionAttributeNames": {
            "#project": "project",
        },
    }

    while True:
        response = table.scan(**kwargs)
        items = response.get("Items", [])
        if items:
            return items[0]

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return None
        kwargs["ExclusiveStartKey"] = last_key


def cleanup_dns(
    event: Dict[str, Any],
    *,
    main_table: DynamoDBTable,
    ec2_client: EC2Client,
    ssm_client: SSMClient,
    param_prefix: str = "/devbox",
) -> None:
    """Delete DNS records for terminated devbox instances."""
    if not _should_process_event(event):
        _logger.info("skipping event with non-termination state")
        return

    instance_id = _extract_instance_id(event)
    if not instance_id:
        _logger.warning("event does not contain detail.instance-id; skipping DNS cleanup")
        return

    project = _get_project_from_instance(ec2_client, instance_id)
    item = _get_project_item(main_table, project)

    if not item:
        item = _scan_project_by_instance_id(main_table, instance_id)
        if item and not project:
            project = item.get("project")

    if not item:
        _logger.info("no devbox project item found for instance %s", instance_id)
        return

    stored_dns_name = str(item.get("CNAMEDomain", "")).strip()
    if not stored_dns_name:
        _logger.info("no CNAMEDomain set for project %s; nothing to clean up", project or "unknown")
        return

    dns_manager = DNSManager.from_ssm(param_prefix=param_prefix, ssm_client=ssm_client)
    if dns_manager.provider is None:
        _logger.info("DNS provider not configured; skipping cleanup for %s", stored_dns_name)
        return

    subdomain = dns_manager.normalize_stored_subdomain(stored_dns_name)
    if not subdomain:
        _logger.warning(
            "stored DNS value '%s' could not be normalized for project %s; skipping cleanup",
            stored_dns_name,
            project or "unknown",
        )
        return

    deleted = dns_manager.remove_cname_for_project(
        project=project or subdomain,
        custom_subdomain=subdomain,
    )
    if deleted:
        _logger.info(
            "deleted DNS CNAME record for project=%s instance_id=%s subdomain=%s",
            project or "unknown",
            instance_id,
            subdomain,
        )
    else:
        _logger.info(
            "DNS record already absent project=%s instance_id=%s subdomain=%s",
            project or "unknown",
            instance_id,
            subdomain,
        )
