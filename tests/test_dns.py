"""Tests for DNS provider implementations."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from cloudflare import APIConnectionError, APIStatusError
from moto import mock_aws

from devbox.dns import (
    CNAMERecord,
    CloudflareProvider,
    DNSManager,
    DEFAULT_TTL,
    DNSProviderError,
    Route53Provider,
)


@dataclass
class FakeZone:
    id: str | None
    name: str


@dataclass
class FakeRecord:
    id: str | None
    name: str
    content: str
    ttl: int | None = None


class FakeZonesClient:
    def __init__(self, responses: List[Any]) -> None:
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def list(self, *, name: str, **kwargs: Any) -> List[FakeZone]:
        self.calls.append({"name": name, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeRecordsClient:
    def __init__(
        self,
        *,
        list_responses: List[Any] | None = None,
        create_responses: List[Any] | None = None,
        update_responses: List[Any] | None = None,
        delete_responses: List[Any] | None = None,
    ) -> None:
        self.list_responses = list_responses or []
        self.create_responses = create_responses or []
        self.update_responses = update_responses or []
        self.delete_responses = delete_responses or []
        self.list_calls: List[Dict[str, Any]] = []
        self.create_calls: List[Dict[str, Any]] = []
        self.update_calls: List[Dict[str, Any]] = []
        self.delete_calls: List[Dict[str, Any]] = []

    def _next(self, responses: List[Any]) -> Any:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def list(self, *, zone_id: str, name: str, type: str, **kwargs: Any) -> List[FakeRecord]:
        self.list_calls.append(
            {
                "zone_id": zone_id,
                "name": name,
                "type": type,
                **kwargs,
            }
        )
        return self._next(self.list_responses)

    def create(
        self,
        *,
        zone_id: str,
        name: str,
        type: str,
        content: str,
        ttl: int,
        proxied: bool,
        **kwargs: Any,
    ) -> FakeRecord | None:
        self.create_calls.append(
            {
                "zone_id": zone_id,
                "name": name,
                "type": type,
                "content": content,
                "ttl": ttl,
                "proxied": proxied,
                **kwargs,
            }
        )
        return self._next(self.create_responses)

    def update(
        self,
        dns_record_id: str,
        *,
        zone_id: str,
        name: str,
        type: str,
        content: str,
        ttl: int,
        proxied: bool,
        **kwargs: Any,
    ) -> FakeRecord | None:
        self.update_calls.append(
            {
                "dns_record_id": dns_record_id,
                "zone_id": zone_id,
                "name": name,
                "type": type,
                "content": content,
                "ttl": ttl,
                "proxied": proxied,
                **kwargs,
            }
        )
        return self._next(self.update_responses)

    def delete(self, dns_record_id: str, *, zone_id: str, **kwargs: Any) -> dict[str, Any]:
        self.delete_calls.append(
            {
                "dns_record_id": dns_record_id,
                "zone_id": zone_id,
                **kwargs,
            }
        )
        return self._next(self.delete_responses)


class FakeCloudflareClient:
    def __init__(
        self,
        *,
        zone_responses: List[Any] | None = None,
        record_list_responses: List[Any] | None = None,
        create_responses: List[Any] | None = None,
        update_responses: List[Any] | None = None,
        delete_responses: List[Any] | None = None,
    ) -> None:
        self.zones = FakeZonesClient(zone_responses or [])
        self.records = FakeRecordsClient(
            list_responses=record_list_responses,
            create_responses=create_responses,
            update_responses=update_responses,
            delete_responses=delete_responses,
        )
        self.dns = SimpleNamespace(records=self.records)


def make_api_connection_error(message: str = "timed out") -> APIConnectionError:
    request = httpx.Request("GET", "https://api.cloudflare.com/client/v4/test")
    return APIConnectionError(message=message, request=request)


def make_api_status_error(
    status_code: int,
    body: Any,
    message: str = "Cloudflare API error",
) -> APIStatusError:
    request = httpx.Request("GET", "https://api.cloudflare.com/client/v4/test")
    response = httpx.Response(status_code, json=body, request=request)
    return APIStatusError(message, response=response, body=body)


def make_client_error(
    operation_name: str,
    *,
    code: str = "ServiceFailure",
    message: str = "service failed",
) -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name=operation_name,
    )


class StubProvider:
    def __init__(self) -> None:
        self.zone_name = "example.com"
        self.created: List[Any] = []
        self.deleted: List[str] = []

    def create_cname(self, subdomain: str, target: str) -> CNAMERecord:
        self.created.append((subdomain, target))
        return CNAMERecord(name=f"{subdomain}.example.com", target=target)

    def delete_cname(self, subdomain: str) -> bool:
        self.deleted.append(subdomain)
        return True

    def get_cname(self, subdomain: str) -> None:  # pragma: no cover
        # not used by DNSManager; so no need to cover
        return None


class TestCloudflareProvider:
    def test_request_wraps_connection_error(self):
        client = FakeCloudflareClient(
            record_list_responses=[make_api_connection_error("timed out")]
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        with pytest.raises(DNSProviderError, match="Cloudflare API request failed"):
            provider.get_cname("app")

    def test_request_wraps_status_error(self):
        client = FakeCloudflareClient(
            record_list_responses=[
                make_api_status_error(
                    403,
                    {"errors": [{"message": "forbidden"}]},
                )
            ]
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        with pytest.raises(DNSProviderError, match="forbidden"):
            provider.get_cname("app")

    def test_request_wraps_status_error_without_nested_message(self):
        # Cloudflare error payloads may omit errors[0].message; fall back to str(exc).
        client = FakeCloudflareClient(
            record_list_responses=[
                make_api_status_error(
                    500,
                    {},
                    message="status fallback",
                )
            ]
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        with pytest.raises(DNSProviderError, match="status fallback"):
            provider.get_cname("app")

    def test_request_wraps_status_error_with_non_dict_body(self):
        # Defensive coverage for unexpected SDK/body parsing shapes that are not dicts.
        client = FakeCloudflareClient(
            record_list_responses=[
                make_api_status_error(
                    502,
                    ["upstream unavailable"],
                    message="non-dict fallback",
                )
            ]
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        with pytest.raises(DNSProviderError, match="non-dict fallback"):
            provider.get_cname("app")

    def test_resolves_zone_id_from_zone_name(self):
        client = FakeCloudflareClient(
            zone_responses=[[FakeZone(id="zone123", name="example.com")]]
        )

        provider = CloudflareProvider(
            api_token="token",
            zone_name="example.com",
            client=client,
        )

        assert provider.zone_id == "zone123"
        assert client.zones.calls[0]["name"] == "example.com"

    def test_resolve_zone_id_raises_when_zone_not_found(self):
        client = FakeCloudflareClient(zone_responses=[[]])

        with pytest.raises(DNSProviderError, match="not found"):
            CloudflareProvider(
                api_token="token",
                zone_name="example.com",
                client=client,
            )

    def test_resolve_zone_id_raises_on_ambiguous_match(self):
        client = FakeCloudflareClient(
            zone_responses=[
                [
                    FakeZone(id="zone-1", name="example.com"),
                    FakeZone(id="zone-2", name="example.com"),
                ]
            ]
        )

        with pytest.raises(DNSProviderError, match="ambiguous"):
            CloudflareProvider(
                api_token="token",
                zone_name="example.com",
                client=client,
            )

    def test_resolve_zone_id_wraps_api_error(self):
        client = FakeCloudflareClient(
            zone_responses=[make_api_connection_error("timed out")]
        )

        with pytest.raises(DNSProviderError, match="Cloudflare API request failed"):
            CloudflareProvider(
                api_token="token",
                zone_name="example.com",
                client=client,
            )

    def test_resolve_zone_id_raises_without_usable_zone_id(self):
        client = FakeCloudflareClient(
            zone_responses=[[FakeZone(id=" ", name="example.com")]]
        )

        with pytest.raises(DNSProviderError, match="without a usable zone id"):
            CloudflareProvider(
                api_token="token",
                zone_name="example.com",
                client=client,
            )

    def test_get_cname_returns_record(self):
        client = FakeCloudflareClient(
            record_list_responses=[
                [
                    FakeRecord(
                        id="abc123",
                        name="app.example.com",
                        content="target.example.net",
                        ttl=120,
                    )
                ]
            ]
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        record = provider.get_cname("app")

        assert record is not None
        assert record.provider_record_id == "abc123"
        assert client.records.list_calls[0]["type"] == "CNAME"

    def test_create_cname_creates_record_when_missing(self):
        client = FakeCloudflareClient(
            record_list_responses=[[]],
            create_responses=[
                FakeRecord(
                    id="abc123",
                    name="app.example.com",
                    content="target.example.net",
                    ttl=300,
                )
            ],
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        record = provider.create_cname("app", "target.example.net")

        assert record.name == "app.example.com"
        assert record.target == "target.example.net"
        assert len(client.records.list_calls) == 1
        assert len(client.records.create_calls) == 1

    def test_create_cname_wraps_create_error(self):
        client = FakeCloudflareClient(
            record_list_responses=[[]],
            create_responses=[make_api_connection_error("timed out")],
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        with pytest.raises(DNSProviderError, match="Cloudflare API request failed"):
            provider.create_cname("app", "target.example.net")

    def test_create_cname_raises_when_create_returns_no_record_data(self):
        client = FakeCloudflareClient(
            record_list_responses=[[]],
            create_responses=[None],
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        with pytest.raises(DNSProviderError, match="returned no DNS record data"):
            provider.create_cname("app", "target.example.net")

    def test_create_cname_reuses_existing_record(self):
        client = FakeCloudflareClient(
            record_list_responses=[
                [
                    FakeRecord(
                        id="abc123",
                        name="app.example.com",
                        content="target.example.net",
                        ttl=450,
                    )
                ]
            ]
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        record = provider.create_cname("app", "target.example.net")

        assert record.provider_record_id == "abc123"
        assert len(client.records.list_calls) == 1
        assert client.records.create_calls == []

    def test_delete_cname_removes_existing_record(self):
        client = FakeCloudflareClient(
            record_list_responses=[
                [
                    FakeRecord(
                        id="abc123",
                        name="app.example.com",
                        content="target.example.net",
                        ttl=300,
                    )
                ]
            ],
            delete_responses=[{"id": "abc123"}],
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        assert provider.delete_cname("app") is True
        assert len(client.records.list_calls) == 1
        assert len(client.records.delete_calls) == 1

    def test_delete_cname_returns_false_without_provider_record_id(self):
        client = FakeCloudflareClient(
            record_list_responses=[
                [
                    FakeRecord(
                        id=None,
                        name="app.example.com",
                        content="target.example.net",
                        ttl=300,
                    )
                ]
            ]
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        assert provider.delete_cname("app") is False
        assert client.records.delete_calls == []

    def test_delete_cname_wraps_delete_error(self):
        client = FakeCloudflareClient(
            record_list_responses=[
                [
                    FakeRecord(
                        id="abc123",
                        name="app.example.com",
                        content="target.example.net",
                        ttl=300,
                    )
                ]
            ],
            delete_responses=[make_api_status_error(403, {"errors": [{"message": "forbidden"}]})],
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        with pytest.raises(DNSProviderError, match="forbidden"):
            provider.delete_cname("app")

    def test_create_cname_updates_existing_record_with_new_target(self):
        client = FakeCloudflareClient(
            record_list_responses=[
                [
                    FakeRecord(
                        id="abc123",
                        name="app.example.com",
                        content="old.example.net",
                        ttl=120,
                    )
                ]
            ],
            update_responses=[
                FakeRecord(
                    id="abc123",
                    name="app.example.com",
                    content="new.example.net",
                    ttl=300,
                )
            ],
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        record = provider.create_cname("app", "new.example.net")

        assert record.target == "new.example.net"
        assert len(client.records.list_calls) == 1
        assert len(client.records.update_calls) == 1

    def test_create_cname_wraps_update_error(self):
        client = FakeCloudflareClient(
            record_list_responses=[
                [
                    FakeRecord(
                        id="abc123",
                        name="app.example.com",
                        content="old.example.net",
                        ttl=120,
                    )
                ]
            ],
            update_responses=[make_api_connection_error("timed out")],
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        with pytest.raises(DNSProviderError, match="Cloudflare API request failed"):
            provider.create_cname("app", "new.example.net")

    def test_create_cname_raises_when_update_returns_no_record_data(self):
        client = FakeCloudflareClient(
            record_list_responses=[
                [
                    FakeRecord(
                        id="abc123",
                        name="app.example.com",
                        content="old.example.net",
                        ttl=120,
                    )
                ]
            ],
            update_responses=[None],
        )
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            client=client,
        )

        with pytest.raises(DNSProviderError, match="returned no DNS record data"):
            provider.create_cname("app", "new.example.net")


class TestRoute53Provider:
    @pytest.fixture()
    def route53_context(self):
        with mock_aws():
            client = boto3.client("route53", region_name="us-east-1")
            zone = client.create_hosted_zone(Name="example.com", CallerReference="test")
            zone_id = zone["HostedZone"]["Id"].split("/")[-1]
            provider = Route53Provider(zone_id=zone_id, zone_name="example.com", client=client)
            yield provider

    @mock_aws
    def test_resolves_zone_id_from_zone_name(self):
        client = boto3.client("route53", region_name="us-east-1")
        zone = client.create_hosted_zone(Name="example.com", CallerReference="lookup")
        expected_zone_id = zone["HostedZone"]["Id"].split("/")[-1]

        provider = Route53Provider(zone_name="example.com", client=client)

        assert provider.zone_id == expected_zone_id

    def test_resolve_zone_id_raises_when_no_public_zone_matches(self):
        client = MagicMock()
        client.list_hosted_zones_by_name.return_value = {
            "HostedZones": [
                {
                    "Id": "/hostedzone/ZPRIVATE",
                    "Name": "example.com.",
                    "Config": {"PrivateZone": True},
                }
            ]
        }

        with pytest.raises(DNSProviderError, match="No public Route53 hosted zone"):
            Route53Provider(zone_name="example.com", client=client)

    def test_resolve_zone_id_raises_on_ambiguous_public_matches(self):
        client = MagicMock()
        client.list_hosted_zones_by_name.return_value = {
            "HostedZones": [
                {
                    "Id": "/hostedzone/ZONE1",
                    "Name": "example.com.",
                    "Config": {"PrivateZone": False},
                },
                {
                    "Id": "/hostedzone/ZONE2",
                    "Name": "example.com.",
                    "Config": {"PrivateZone": False},
                },
            ]
        }

        with pytest.raises(DNSProviderError, match="ambiguous"):
            Route53Provider(zone_name="example.com", client=client)

    def test_resolve_zone_id_wraps_client_error(self):
        client = MagicMock()
        client.list_hosted_zones_by_name.side_effect = make_client_error(
            "ListHostedZonesByName",
            message="zone lookup failed",
        )

        with pytest.raises(DNSProviderError, match="zone lookup failed"):
            Route53Provider(zone_name="example.com", client=client)

    def test_resolve_zone_id_ignores_non_matching_names_and_blank_ids(self):
        client = MagicMock()
        client.list_hosted_zones_by_name.return_value = {
            "HostedZones": [
                {
                    "Id": "/hostedzone/OTHER",
                    "Name": "other.com.",
                    "Config": {"PrivateZone": False},
                },
                {
                    "Id": " ",
                    "Name": "example.com.",
                    "Config": {"PrivateZone": False},
                },
                {
                    "Id": "/hostedzone/ZONE1",
                    "Name": "example.com.",
                    "Config": {"PrivateZone": False},
                },
            ]
        }

        provider = Route53Provider(zone_name="example.com", client=client)

        assert provider.zone_id == "ZONE1"

    def test_create_cname(self, route53_context):
        provider = route53_context

        created = provider.create_cname("api", "api.internal.local")
        assert created.name == "api.example.com"
        assert created.target == "api.internal.local"
        assert created.ttl == DEFAULT_TTL

    def test_get_cname(self, route53_context):
        provider = route53_context
        provider.create_cname("api", "api.internal.local")

        record = provider.get_cname("api")

        assert record is not None
        assert record.target == "api.internal.local"

    def test_get_cname_returns_none_for_lexicographic_neighbor(self):
        client = MagicMock()
        client.list_resource_record_sets.return_value = {
            "ResourceRecordSets": [
                {
                    "Name": "api2.example.com.",
                    "ResourceRecords": [{"Value": "api.internal.local."}],
                    "TTL": DEFAULT_TTL,
                }
            ]
        }
        provider = Route53Provider(zone_id="ZONE1", zone_name="example.com", client=client)

        assert provider.get_cname("api") is None

    def test_get_cname_wraps_client_error(self):
        client = MagicMock()
        client.list_resource_record_sets.side_effect = make_client_error(
            "ListResourceRecordSets",
            message="record lookup failed",
        )
        provider = Route53Provider(zone_id="ZONE1", zone_name="example.com", client=client)

        with pytest.raises(DNSProviderError, match="record lookup failed"):
            provider.get_cname("api")

    def test_delete_cname(self, route53_context):
        provider = route53_context
        provider.create_cname("api", "api.internal.local")

        assert provider.delete_cname("api") is True
        assert provider.get_cname("api") is None

    def test_create_cname_wraps_client_error(self):
        client = MagicMock()
        client.change_resource_record_sets.side_effect = make_client_error(
            "ChangeResourceRecordSets",
            message="create failed",
        )
        provider = Route53Provider(zone_id="ZONE1", zone_name="example.com", client=client)

        with pytest.raises(DNSProviderError, match="create failed"):
            provider.create_cname("api", "api.internal.local")

    def test_delete_cname_returns_false_when_missing(self):
        client = MagicMock()
        client.list_resource_record_sets.return_value = {"ResourceRecordSets": []}
        provider = Route53Provider(zone_id="ZONE1", zone_name="example.com", client=client)

        assert provider.delete_cname("api") is False
        client.change_resource_record_sets.assert_not_called()

    def test_delete_cname_wraps_client_error(self):
        client = MagicMock()
        client.list_resource_record_sets.return_value = {
            "ResourceRecordSets": [
                {
                    "Name": "api.example.com.",
                    "ResourceRecords": [{"Value": "api.internal.local."}],
                    "TTL": DEFAULT_TTL,
                }
            ]
        }
        client.change_resource_record_sets.side_effect = make_client_error(
            "ChangeResourceRecordSets",
            message="delete failed",
        )
        provider = Route53Provider(zone_id="ZONE1", zone_name="example.com", client=client)

        with pytest.raises(DNSProviderError, match="delete failed"):
            provider.delete_cname("api")


class TestDNSManager:
    def test_sanitize_dns_name_replaces_underscore(self):
        manager = DNSManager(provider=None)
        assert manager.sanitize_dns_name("my_project") == "my-project"

    def test_assign_cname_gracefully_skips_when_unconfigured(self):
        manager = DNSManager(provider=None)
        result = manager.assign_cname(
            subdomain="demo",
            instance_public_dns="ec2-1-2-3-4.compute-1.amazonaws.com",
        )
        assert result is None

    def test_remove_cname_gracefully_skips_when_unconfigured(self):
        manager = DNSManager(provider=None)

        assert manager.remove_cname("demo") is False

    def test_assign_cname_invokes_provider(self):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        fqdn = manager.assign_cname("My_Project", "target.dev.internal")

        assert fqdn == "my-project.example.com"
        assert provider.created == [("my-project", "target.dev.internal")]

    def test_remove_cname_invokes_provider(self):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        assert manager.remove_cname("My_Project") is True
        assert provider.deleted == ["my-project"]

    def test_normalize_subdomain_accepts_subdomain_label(self):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        assert manager.normalize_subdomain("my-project") == "my-project"

    def test_normalize_subdomain_rejects_fqdn(self):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        assert manager.normalize_subdomain("my-project.other.com") is None

    @pytest.mark.parametrize("operation", ["assign", "remove"])
    def test_subdomain_with_suffix_raises_validation_error(self, operation):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        with pytest.raises(ValueError, match="only letters, numbers, and hyphens"):
            if operation == "assign":
                manager.assign_cname(
                    subdomain="my-project.example.com",
                    instance_public_dns="target.dev.internal",
                )
            else:
                manager.remove_cname(subdomain="my-project.example.com")

    @pytest.mark.parametrize("operation", ["assign", "remove"])
    def test_custom_subdomain_invalid_chars_raises_specific_error(self, operation):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        with pytest.raises(
            ValueError,
            match="only letters, numbers, and hyphens",
        ):
            if operation == "assign":
                manager.assign_cname(
                    subdomain="bad*name",
                    instance_public_dns="target.dev.internal",
                )
            else:
                manager.remove_cname(subdomain="bad*name")

    def test_sanitize_dns_name_rejects_empty_after_sanitization(self):
        manager = DNSManager(provider=None)

        with pytest.raises(ValueError, match="cannot be empty"):
            manager.sanitize_dns_name("---__---")

    def test_sanitize_dns_name_rejects_labels_longer_than_63_characters(self):
        manager = DNSManager(provider=None)

        with pytest.raises(ValueError, match="exceeds 63 characters"):
            manager.sanitize_dns_name("a" * 64)

    def test_remove_cname_invokes_provider_with_subdomain(self):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        assert manager.remove_cname(subdomain="my-project") is True
        assert provider.deleted == ["my-project"]


class TestDNSManagerFromSSM:
    def test_from_ssm_returns_disabled_when_provider_parameter_unreadable(self):
        ssm = MagicMock()
        ssm.get_parameter.side_effect = [
            make_client_error("GetParameter", code="ParameterNotFound", message="missing provider")
        ]

        manager = DNSManager.from_ssm(param_prefix="/devbox", ssm_client=ssm)

        assert manager.provider is None

    @mock_aws
    def test_from_ssm_returns_disabled_when_provider_none(self):
        ssm = boto3.client("ssm", region_name="us-east-1")
        ssm.put_parameter(
            Name="/devbox/dns/provider",
            Value="none",
            Type="String",
        )

        manager = DNSManager.from_ssm(param_prefix="/devbox", ssm_client=ssm)

        assert manager.provider is None

    def test_from_ssm_returns_disabled_when_zone_parameter_unreadable(self):
        ssm = MagicMock()
        ssm.get_parameter.side_effect = [
            {"Parameter": {"Value": "route53"}},
            make_client_error("GetParameter", code="ParameterNotFound", message="missing zone"),
        ]

        manager = DNSManager.from_ssm(param_prefix="/devbox", ssm_client=ssm)

        assert manager.provider is None

    @mock_aws
    def test_from_ssm_builds_cloudflare_provider(self):
        ssm = boto3.client("ssm", region_name="us-east-1")
        ssm.put_parameter(Name="/devbox/dns/provider", Value="cloudflare", Type="String")
        ssm.put_parameter(Name="/devbox/dns/zone", Value="example.com", Type="String")
        ssm.put_parameter(
            Name="/devbox/secrets/cloudflare/apiToken",
            Value="token",
            Type="SecureString",
        )
        fake_client = FakeCloudflareClient(
            zone_responses=[[FakeZone(id="zone-123", name="example.com")]]
        )

        manager = DNSManager.from_ssm(
            param_prefix="/devbox",
            ssm_client=ssm,
            cloudflare_client=fake_client,
        )

        assert isinstance(manager.provider, CloudflareProvider)
        assert manager.provider.zone_id == "zone-123"
        assert manager.provider.zone_name == "example.com"

    def test_from_ssm_returns_disabled_when_cloudflare_token_unreadable(self):
        ssm = MagicMock()
        ssm.get_parameter.side_effect = [
            {"Parameter": {"Value": "cloudflare"}},
            {"Parameter": {"Value": "example.com"}},
            make_client_error("GetParameter", code="ParameterNotFound", message="missing token"),
        ]

        manager = DNSManager.from_ssm(param_prefix="/devbox", ssm_client=ssm)

        assert manager.provider is None

    @mock_aws
    def test_from_ssm_builds_route53_provider(self):
        ssm = boto3.client("ssm", region_name="us-east-1")
        ssm.put_parameter(Name="/devbox/dns/provider", Value="route53", Type="String")
        ssm.put_parameter(Name="/devbox/dns/zone", Value="example.com", Type="String")

        route53 = boto3.client("route53", region_name="us-east-1")
        hosted_zone = route53.create_hosted_zone(Name="example.com", CallerReference="test")
        expected_zone_id = hosted_zone["HostedZone"]["Id"].split("/")[-1]

        manager = DNSManager.from_ssm(
            param_prefix="/devbox",
            ssm_client=ssm,
            route53_client=route53,
        )

        assert isinstance(manager.provider, Route53Provider)
        assert manager.provider.zone_id == expected_zone_id

    def test_from_ssm_returns_disabled_for_unknown_provider(self):
        ssm = MagicMock()
        ssm.get_parameter.side_effect = [
            {"Parameter": {"Value": "customdns"}},
            {"Parameter": {"Value": "example.com"}},
        ]

        manager = DNSManager.from_ssm(param_prefix="/devbox", ssm_client=ssm)

        assert manager.provider is None

    @mock_aws
    def test_from_ssm_disables_cloudflare_when_zone_lookup_fails(self):
        ssm = boto3.client("ssm", region_name="us-east-1")
        ssm.put_parameter(Name="/devbox/dns/provider", Value="cloudflare", Type="String")
        ssm.put_parameter(Name="/devbox/dns/zone", Value="example.com", Type="String")
        ssm.put_parameter(
            Name="/devbox/secrets/cloudflare/apiToken",
            Value="token",
            Type="SecureString",
        )
        fake_client = FakeCloudflareClient(zone_responses=[[]])

        manager = DNSManager.from_ssm(
            param_prefix="/devbox",
            ssm_client=ssm,
            cloudflare_client=fake_client,
        )

        assert manager.provider is None

    @mock_aws
    def test_from_ssm_disables_route53_when_zone_lookup_fails(self):
        ssm = boto3.client("ssm", region_name="us-east-1")
        ssm.put_parameter(Name="/devbox/dns/provider", Value="route53", Type="String")
        ssm.put_parameter(Name="/devbox/dns/zone", Value="missing-zone.com", Type="String")
        route53 = boto3.client("route53", region_name="us-east-1")

        manager = DNSManager.from_ssm(
            param_prefix="/devbox",
            ssm_client=ssm,
            route53_client=route53,
        )

        assert manager.provider is None
