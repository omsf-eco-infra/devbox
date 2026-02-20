"""DNS provider abstractions and manager for devbox instances."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

import boto3
import requests
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from . import utils

DEFAULT_TTL = 300
LOG = logging.getLogger(__name__)


@dataclass
class CNAMERecord:
    """Represents a DNS CNAME record."""

    name: str
    target: str
    ttl: Optional[int] = None
    provider_record_id: Optional[str] = None


class DNSProviderError(Exception):
    """Raised when DNS provider operations fail."""


class DNSProvider(Protocol):
    """DNS provider interface."""

    zone_name: str

    def create_cname(self, subdomain: str, target: str) -> CNAMERecord:
        ...

    def delete_cname(self, subdomain: str) -> bool:
        ...

    def get_cname(self, subdomain: str) -> Optional[CNAMERecord]:
        ...


class CloudflareProvider(DNSProvider):
    """Cloudflare DNS provider implementation using REST API."""

    def __init__(
        self,
        api_token: str,
        zone_id: str,
        zone_name: str,
        session: Optional[requests.Session] = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ) -> None:
        self.api_token = api_token
        self.zone_id = zone_id
        self.zone_name = zone_name.rstrip(".")
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        """Perform a Cloudflare API request with basic retry on 429."""
        url = f"https://api.cloudflare.com/client/v4{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_token}"
        headers["Content-Type"] = "application/json"

        for attempt in range(1, self.max_retries + 1):
            response = self.session.request(method, url, headers=headers, **kwargs)
            if response.status_code == 429 and attempt < self.max_retries:
                time.sleep(self.backoff_seconds * attempt)
                continue

            try:
                payload = response.json()
            except ValueError:
                payload = {}

            if response.ok and payload.get("success", False):
                return payload

            errors = payload.get("errors") or []
            message = errors[0].get("message") if errors else response.text
            LOG.error("Cloudflare API error (%s): %s", response.status_code, message)
            raise DNSProviderError(message)

        raise DNSProviderError("Exceeded Cloudflare API retry attempts")

    def _fqdn(self, subdomain: str) -> str:
        return f"{subdomain}.{self.zone_name}"

    def get_cname(self, subdomain: str) -> Optional[CNAMERecord]:
        name = self._fqdn(subdomain)
        payload = self._request(
            "GET",
            f"/zones/{self.zone_id}/dns_records",
            params={"type": "CNAME", "name": name},
        )
        records = payload.get("result", [])
        if not records:
            return None

        record = records[0]
        return CNAMERecord(
            name=record.get("name", name),
            target=record.get("content", ""),
            ttl=record.get("ttl"),
            provider_record_id=record.get("id"),
        )

    def create_cname(self, subdomain: str, target: str) -> CNAMERecord:
        existing = self.get_cname(subdomain)
        name = self._fqdn(subdomain)
        if existing and existing.target == target:
            return existing

        # Keep Cloudflare behavior in line with Route53 UPSERT semantics.
        if existing and existing.provider_record_id:
            payload = self._request(
                "PUT",
                f"/zones/{self.zone_id}/dns_records/{existing.provider_record_id}",
                json={
                    "type": "CNAME",
                    "name": name,
                    "content": target,
                    "ttl": DEFAULT_TTL,
                    "proxied": False,
                },
            )

            record = payload.get("result", {})
            return CNAMERecord(
                name=record.get("name", name),
                target=record.get("content", target),
                ttl=record.get("ttl", DEFAULT_TTL),
                provider_record_id=record.get("id", existing.provider_record_id),
            )

        payload = self._request(
            "POST",
            f"/zones/{self.zone_id}/dns_records",
            json={
                "type": "CNAME",
                "name": name,
                "content": target,
                "ttl": DEFAULT_TTL,
                "proxied": False,
            },
        )

        record = payload.get("result", {})
        return CNAMERecord(
            name=record.get("name", name),
            target=record.get("content", target),
            ttl=record.get("ttl", DEFAULT_TTL),
            provider_record_id=record.get("id"),
        )

    def delete_cname(self, subdomain: str) -> bool:
        existing = self.get_cname(subdomain)
        if not existing or not existing.provider_record_id:
            return False

        self._request(
            "DELETE",
            f"/zones/{self.zone_id}/dns_records/{existing.provider_record_id}",
        )
        return True


class Route53Provider(DNSProvider):
    """Route53 DNS provider implementation using boto3 client."""

    def __init__(
        self,
        zone_id: str,
        zone_name: str,
        client: Optional[BaseClient] = None,
    ) -> None:
        self.zone_id = zone_id
        self.zone_name = zone_name.rstrip(".")
        self.client = client or boto3.client("route53")

    def _fqdn(self, subdomain: str) -> str:
        return f"{subdomain}.{self.zone_name}"

    def get_cname(self, subdomain: str) -> Optional[CNAMERecord]:
        name = self._fqdn(subdomain)
        try:
            resp = self.client.list_resource_record_sets(
                HostedZoneId=self.zone_id,
                StartRecordName=name,
                StartRecordType="CNAME",
                MaxItems="1",
            )
            record_sets = resp.get("ResourceRecordSets", [])
            if not record_sets:
                return None

            record = record_sets[0]
            if record.get("Name", "").rstrip(".") != name:
                return None

            values = record.get("ResourceRecords", [])
            target = values[0].get("Value", "") if values else ""
            ttl = record.get("TTL")
            return CNAMERecord(name=name, target=target.rstrip("."), ttl=ttl)
        except ClientError as exc:
            LOG.error("Route53 get_cname failed: %s", exc)
            raise DNSProviderError(str(exc)) from exc

    def create_cname(self, subdomain: str, target: str) -> CNAMERecord:
        name = self._fqdn(subdomain)
        try:
            self.client.change_resource_record_sets(
                HostedZoneId=self.zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "UPSERT",
                            "ResourceRecordSet": {
                                "Name": name,
                                "Type": "CNAME",
                                "TTL": DEFAULT_TTL,
                                "ResourceRecords": [{"Value": target}],
                            },
                        }
                    ]
                },
            )
            return CNAMERecord(name=name, target=target, ttl=DEFAULT_TTL)
        except ClientError as exc:
            LOG.error("Route53 create_cname failed: %s", exc)
            raise DNSProviderError(str(exc)) from exc

    def delete_cname(self, subdomain: str) -> bool:
        existing = self.get_cname(subdomain)
        if not existing:
            return False

        try:
            self.client.change_resource_record_sets(
                HostedZoneId=self.zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "DELETE",
                            "ResourceRecordSet": {
                                "Name": existing.name,
                                "Type": "CNAME",
                                "TTL": existing.ttl or DEFAULT_TTL,
                                "ResourceRecords": [{"Value": existing.target}],
                            },
                        }
                    ]
                },
            )
            return True
        except ClientError as exc:
            LOG.error("Route53 delete_cname failed: %s", exc)
            raise DNSProviderError(str(exc)) from exc


class DNSManager:
    """Manager that dispatches DNS operations to a configured provider."""

    def __init__(self, provider: Optional[DNSProvider]) -> None:
        self.provider = provider

    @classmethod
    def from_ssm(
        cls,
        param_prefix: str = "/devbox",
        *,
        ssm_client: Optional[BaseClient] = None,
        route53_client: Optional[BaseClient] = None,
        http_session: Optional[requests.Session] = None,
    ) -> "DNSManager":
        """Create a DNSManager configured from SSM parameters."""
        ssm = ssm_client or utils.get_ssm_client()

        def _get_parameter(name: str, required: bool = True) -> Optional[str]:
            try:
                response = ssm.get_parameter(Name=name, WithDecryption=True)
                value = response.get("Parameter", {}).get("Value", "")
                return value.strip()
            except (ClientError, KeyError) as exc:
                log = LOG.warning if required else LOG.info
                log("Unable to read SSM parameter %s: %s", name, exc)
                return None

        provider_param = f"{param_prefix}/dns/provider"
        provider = (_get_parameter(provider_param, required=False) or "").lower()
        if not provider or provider == "none":
            LOG.info("DNS provider not configured (value: %s); DNS disabled", provider or "unset")
            return cls(None)

        zone_name_param = f"{param_prefix}/dns/zone"
        zone_name = _get_parameter(zone_name_param)
        if not zone_name:
            LOG.warning("DNS zone parameter %s missing; DNS disabled", zone_name_param)
            return cls(None)
        zone_name = zone_name.rstrip(".")

        if provider == "cloudflare":
            api_token = _get_parameter(f"{param_prefix}/secrets/cloudflare/apiToken")
            zone_id = _get_parameter(f"{param_prefix}/secrets/cloudflare/zoneId")

            if not api_token or not zone_id:
                LOG.warning("Cloudflare configuration incomplete; DNS disabled")
                return cls(None)

            provider_impl = CloudflareProvider(
                api_token=api_token,
                zone_id=zone_id,
                zone_name=zone_name,
                session=http_session,
            )
            return cls(provider_impl)

        if provider == "route53":
            route53 = route53_client or boto3.client("route53")
            zone_id_param = f"{param_prefix}/dns/route53/zoneId"
            zone_id = _get_parameter(zone_id_param)
            if not zone_id:
                LOG.warning("Route53 hosted zone ID missing for zone %s; DNS disabled", zone_name)
                return cls(None)

            provider_impl = Route53Provider(
                zone_id=zone_id,
                zone_name=zone_name,
                client=route53,
            )
            return cls(provider_impl)

        LOG.warning("Unknown DNS provider '%s'; DNS disabled", provider)
        return cls(None)

    def sanitize_dns_name(self, project: str, custom_subdomain: Optional[str] = None) -> str:
        """Return a DNS-safe subdomain."""
        subdomain = (custom_subdomain or project).strip().lower()
        sanitized = subdomain.replace("_", "-")
        if not re.fullmatch(r"[a-z0-9-]+", sanitized):
            raise ValueError("Subdomain must contain only letters, numbers, and hyphens")
        sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-")
        if not sanitized:
            raise ValueError("Subdomain cannot be empty after sanitization")
        if len(sanitized) > 63:
            raise ValueError("Subdomain exceeds 63 characters")
        return sanitized

    def normalize_subdomain(self, subdomain: str) -> Optional[str]:
        """Validate and normalize a user-facing subdomain label."""
        candidate = subdomain.strip().lower()
        if not candidate:
            return None
        if "." in candidate:
            return None

        try:
            return self.sanitize_dns_name(project=candidate, custom_subdomain=candidate)
        except ValueError:
            return None

    def normalize_stored_subdomain(self, stored_value: str) -> Optional[str]:
        """Normalize a stored DNS value (subdomain label)."""
        return self.normalize_subdomain(stored_value)

    def assign_cname_to_instance(
        self,
        project: str,
        instance_public_dns: str,
        custom_subdomain: Optional[str] = None,
    ) -> Optional[str]:
        """Create or reuse a CNAME for the instance. Returns FQDN or None."""
        if not self.provider:
            LOG.info("DNS not configured; skipping CNAME assignment")
            return None

        if custom_subdomain is None:
            subdomain = self.sanitize_dns_name(project)
        else:
            subdomain = self.normalize_subdomain(custom_subdomain)
            if not subdomain:
                raise ValueError("Subdomain must not include domain suffixes")
        record = self.provider.create_cname(subdomain, instance_public_dns)
        return record.name if record else None

    def remove_cname_for_project(
        self,
        project: str,
        custom_subdomain: Optional[str] = None,
    ) -> bool:
        """Remove a project's CNAME if it exists."""
        if not self.provider:
            LOG.info("DNS not configured; skipping CNAME removal")
            return False

        if custom_subdomain is None:
            subdomain = self.sanitize_dns_name(project)
        else:
            subdomain = self.normalize_subdomain(custom_subdomain)
            if not subdomain:
                raise ValueError("Subdomain must not include domain suffixes")
        return self.provider.delete_cname(subdomain)
