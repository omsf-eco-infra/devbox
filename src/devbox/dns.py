"""DNS provider abstractions and manager for devbox instances."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, NoReturn, Optional, Protocol

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from cloudflare import APIConnectionError, APIStatusError, Cloudflare

from . import utils

DEFAULT_TTL = 300
DEFAULT_CLOUDFLARE_TIMEOUT_SECONDS = 10.0
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
    """Cloudflare DNS provider implementation using the official SDK."""

    def __init__(
        self,
        api_token: str,
        zone_name: str,
        zone_id: Optional[str] = None,
        client: Optional[Cloudflare] = None,
        max_retries: int = 3,
        request_timeout_seconds: float = DEFAULT_CLOUDFLARE_TIMEOUT_SECONDS,
    ) -> None:
        self.zone_name = zone_name.rstrip(".")
        self.client = client or self._build_client(
            api_token=api_token,
            request_timeout_seconds=request_timeout_seconds,
            max_retries=max_retries,
        )
        self.zone_id = zone_id or self._resolve_zone_id()

    @staticmethod
    def _build_client(
        *,
        api_token: str,
        request_timeout_seconds: float,
        max_retries: int,
    ) -> Cloudflare:
        return Cloudflare(
            api_token=api_token,
            timeout=request_timeout_seconds,
            max_retries=max(max_retries - 1, 0),
        )

    @staticmethod
    def _status_error_message(exc: APIStatusError) -> str:
        if isinstance(exc.body, dict):
            errors = exc.body.get("errors") or []
            if errors:
                return errors[0].get("message", str(exc))
        return str(exc)

    def _raise_cloudflare_error(
        self,
        operation: str,
        exc: APIConnectionError | APIStatusError,
    ) -> NoReturn:
        if isinstance(exc, APIConnectionError):
            LOG.error("Cloudflare API request failed during %s: %s", operation, exc)
            raise DNSProviderError(f"Cloudflare API request failed: {exc}") from exc

        message = self._status_error_message(exc)
        LOG.error("Cloudflare API error (%s): %s", exc.response.status_code, message)
        raise DNSProviderError(message) from exc

    def _fqdn(self, subdomain: str) -> str:
        return f"{subdomain}.{self.zone_name}"

    def _record_from_cloudflare(
        self,
        record: Any,
        *,
        fallback_name: str,
        fallback_target: str = "",
        fallback_record_id: Optional[str] = None,
    ) -> CNAMERecord:
        return CNAMERecord(
            name=str(getattr(record, "name", fallback_name)),
            target=str(getattr(record, "content", fallback_target)),
            ttl=getattr(record, "ttl", None),
            provider_record_id=getattr(record, "id", fallback_record_id),
        )

    def _list_cname_records(self, fqdn: str) -> list[Any]:
        try:
            return list(
                self.client.dns.records.list(
                    zone_id=self.zone_id,
                    type="CNAME",
                    name=fqdn,
                )
            )
        except (APIConnectionError, APIStatusError) as exc:
            self._raise_cloudflare_error("DNS record lookup", exc)

    def _create_record(self, *, fqdn: str, target: str) -> Any:
        try:
            return self.client.dns.records.create(
                zone_id=self.zone_id,
                type="CNAME",
                name=fqdn,
                content=target,
                ttl=DEFAULT_TTL,
                proxied=False,
            )
        except (APIConnectionError, APIStatusError) as exc:
            self._raise_cloudflare_error("DNS record create", exc)

    def _update_record(self, *, record_id: str, fqdn: str, target: str) -> Any:
        try:
            return self.client.dns.records.update(
                record_id,
                zone_id=self.zone_id,
                type="CNAME",
                name=fqdn,
                content=target,
                ttl=DEFAULT_TTL,
                proxied=False,
            )
        except (APIConnectionError, APIStatusError) as exc:
            self._raise_cloudflare_error("DNS record update", exc)

    def _delete_record(self, record_id: str) -> None:
        try:
            self.client.dns.records.delete(
                record_id,
                zone_id=self.zone_id,
            )
        except (APIConnectionError, APIStatusError) as exc:
            self._raise_cloudflare_error("DNS record delete", exc)

    def _resolve_zone_id(self) -> str:
        """Resolve Cloudflare zone ID from configured zone name."""
        try:
            zones = list(self.client.zones.list(name=self.zone_name))
        except (APIConnectionError, APIStatusError) as exc:
            self._raise_cloudflare_error("zone lookup", exc)

        matches = [
            zone
            for zone in zones
            if str(getattr(zone, "name", "")).rstrip(".").lower() == self.zone_name.lower()
        ]

        if not matches:
            raise DNSProviderError(f"Cloudflare zone '{self.zone_name}' not found")
        if len(matches) > 1:
            raise DNSProviderError(
                f"Cloudflare zone lookup for '{self.zone_name}' is ambiguous ({len(matches)} matches)"
            )

        zone_id = str(getattr(matches[0], "id", "")).strip()
        if not zone_id:
            raise DNSProviderError(
                f"Cloudflare zone '{self.zone_name}' resolved without a usable zone id"
            )
        return zone_id

    def get_cname(self, subdomain: str) -> Optional[CNAMERecord]:
        name = self._fqdn(subdomain)
        records = self._list_cname_records(name)
        if not records:
            return None

        return self._record_from_cloudflare(
            records[0],
            fallback_name=name,
        )

    def create_cname(self, subdomain: str, target: str) -> CNAMERecord:
        existing = self.get_cname(subdomain)
        name = self._fqdn(subdomain)
        if existing and existing.target == target:
            return existing

        # Keep Cloudflare behavior in line with Route53 UPSERT semantics.
        if existing and existing.provider_record_id:
            record = self._update_record(
                record_id=existing.provider_record_id,
                fqdn=name,
                target=target,
            )
            if record is None:
                raise DNSProviderError("Cloudflare API returned no DNS record data")
            return self._record_from_cloudflare(
                record,
                fallback_name=name,
                fallback_target=target,
                fallback_record_id=existing.provider_record_id,
            )

        record = self._create_record(fqdn=name, target=target)
        if record is None:
            raise DNSProviderError("Cloudflare API returned no DNS record data")
        return self._record_from_cloudflare(
            record,
            fallback_name=name,
            fallback_target=target,
        )

    def delete_cname(self, subdomain: str) -> bool:
        existing = self.get_cname(subdomain)
        if not existing or not existing.provider_record_id:
            return False

        self._delete_record(existing.provider_record_id)
        return True


class Route53Provider(DNSProvider):
    """Route53 DNS provider implementation using boto3 client."""

    def __init__(
        self,
        zone_name: str,
        zone_id: Optional[str] = None,
        client: Optional[BaseClient] = None,
    ) -> None:
        self.zone_name = zone_name.rstrip(".")
        self.client = client or boto3.client("route53")
        self.zone_id = zone_id or self._resolve_zone_id()

    def _fqdn(self, subdomain: str) -> str:
        return f"{subdomain}.{self.zone_name}"

    def _resolve_zone_id(self) -> str:
        """Resolve Route53 hosted zone ID from configured zone name."""
        try:
            response = self.client.list_hosted_zones_by_name(
                DNSName=f"{self.zone_name}.",
                MaxItems="100",
            )
        except ClientError as exc:
            LOG.error("Route53 hosted zone lookup failed: %s", exc)
            raise DNSProviderError(str(exc)) from exc

        matches = []
        for zone in response.get("HostedZones", []):
            zone_name = str(zone.get("Name", "")).rstrip(".").lower()
            if zone_name != self.zone_name.lower():
                continue
            if zone.get("Config", {}).get("PrivateZone", False):
                continue

            zone_id = str(zone.get("Id", "")).strip()
            if zone_id:
                matches.append(zone_id.split("/")[-1])

        if not matches:
            raise DNSProviderError(
                f"No public Route53 hosted zone found for '{self.zone_name}'"
            )
        if len(matches) > 1:
            raise DNSProviderError(
                f"Route53 hosted zone lookup for '{self.zone_name}' is ambiguous ({len(matches)} public matches)"
            )

        return matches[0]

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
        cloudflare_client: Optional[Cloudflare] = None,
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
            if not api_token:
                LOG.warning("Cloudflare configuration incomplete; DNS disabled")
                return cls(None)

            try:
                provider_impl = CloudflareProvider(
                    api_token=api_token,
                    zone_name=zone_name,
                    client=cloudflare_client,
                )
            except DNSProviderError as exc:
                LOG.warning("Cloudflare provider initialization failed: %s", exc)
                return cls(None)
            return cls(provider_impl)

        if provider == "route53":
            route53 = route53_client or boto3.client("route53")
            try:
                provider_impl = Route53Provider(
                    zone_name=zone_name,
                    client=route53,
                )
            except DNSProviderError as exc:
                LOG.warning("Route53 provider initialization failed: %s", exc)
                return cls(None)
            return cls(provider_impl)

        LOG.warning("Unknown DNS provider '%s'; DNS disabled", provider)
        return cls(None)

    def sanitize_dns_name(self, subdomain: str) -> str:
        """Return a DNS-safe subdomain label."""
        subdomain = subdomain.strip().lower()
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
        """Best-effort parse for stored/optional values; returns None instead of raising."""
        try:
            return self.sanitize_dns_name(subdomain)
        except ValueError:
            return None

    def assign_cname(
        self,
        subdomain: str,
        instance_public_dns: str,
    ) -> Optional[str]:
        """Create or reuse a CNAME for the instance. Returns FQDN or None."""
        if not self.provider:
            LOG.info("DNS not configured; skipping CNAME assignment")
            return None

        normalized_subdomain = self.sanitize_dns_name(subdomain)
        record = self.provider.create_cname(normalized_subdomain, instance_public_dns)
        return record.name if record else None

    def remove_cname(
        self,
        subdomain: str,
    ) -> bool:
        """Remove a CNAME by subdomain label if it exists."""
        if not self.provider:
            LOG.info("DNS not configured; skipping CNAME removal")
            return False

        normalized_subdomain = self.sanitize_dns_name(subdomain)
        return self.provider.delete_cname(normalized_subdomain)
