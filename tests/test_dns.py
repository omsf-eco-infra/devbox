"""Tests for DNS provider implementations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
from unittest.mock import MagicMock

import boto3
import pytest
import requests
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
class FakeResponse:
    status_code: int
    payload: Dict[str, Any]

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Dict[str, Any]:
        return self.payload

    @property
    # Keep parity with requests.Response for error-message paths.
    def text(self) -> str:  # pragma: no cover
        return str(self.payload)


class FakeSession:
    def __init__(self, responses: List[FakeResponse]) -> None:
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class FailingSession:
    def __init__(self, error: requests.RequestException) -> None:
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        raise self.error


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
    def test_request_wraps_requests_exception(self):
        session = FailingSession(requests.Timeout("timed out"))
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            session=session,
        )

        with pytest.raises(DNSProviderError, match="Cloudflare API request failed"):
            provider.get_cname("app")

    def test_resolves_zone_id_from_zone_name(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "result": [{"id": "zone123", "name": "example.com"}],
                    },
                )
            ]
        )

        provider = CloudflareProvider(
            api_token="token",
            zone_name="example.com",
            session=session,
        )

        assert provider.zone_id == "zone123"
        assert session.calls[0]["method"] == "GET"
        assert session.calls[0]["url"].endswith("/zones")

    def test_resolve_zone_id_raises_when_zone_not_found(self):
        session = FakeSession([FakeResponse(200, {"success": True, "result": []})])

        with pytest.raises(DNSProviderError, match="not found"):
            CloudflareProvider(
                api_token="token",
                zone_name="example.com",
                session=session,
            )

    def test_resolve_zone_id_raises_on_ambiguous_match(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "result": [
                            {"id": "zone-1", "name": "example.com"},
                            {"id": "zone-2", "name": "example.com"},
                        ],
                    },
                )
            ]
        )

        with pytest.raises(DNSProviderError, match="ambiguous"):
            CloudflareProvider(
                api_token="token",
                zone_name="example.com",
                session=session,
            )

    def test_get_cname_returns_record(self):
        get_response = FakeResponse(
            200,
            {
                "success": True,
                "result": [
                    {
                        "id": "abc123",
                        "name": "app.example.com",
                        "content": "target.example.net",
                        "ttl": 120,
                    }
                ],
            },
        )
        session = FakeSession([get_response])
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            session=session,
        )

        record = provider.get_cname("app")

        assert record is not None
        assert record.provider_record_id == "abc123"
        assert session.calls[0]["method"] == "GET"

    def test_create_cname_creates_record_when_missing(self):
        get_response = FakeResponse(
            200,
            {"success": True, "result": []},
        )
        create_response = FakeResponse(
            200,
            {
                "success": True,
                "result": {
                    "id": "abc123",
                    "name": "app.example.com",
                    "content": "target.example.net",
                    "ttl": 300,
                },
            },
        )
        session = FakeSession([get_response, create_response])
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            session=session,
        )

        record = provider.create_cname("app", "target.example.net")

        assert record.name == "app.example.com"
        assert record.target == "target.example.net"
        assert session.calls[0]["method"] == "GET"
        assert session.calls[1]["method"] == "POST"

    def test_create_cname_reuses_existing_record(self):
        get_response = FakeResponse(
            200,
            {
                "success": True,
                "result": [
                    {
                        "id": "abc123",
                        "name": "app.example.com",
                        "content": "target.example.net",
                        "ttl": 450,
                    }
                ],
            },
        )
        session = FakeSession([get_response])
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            session=session,
        )

        record = provider.create_cname("app", "target.example.net")

        assert record.provider_record_id == "abc123"
        assert len(session.calls) == 1
        assert session.calls[0]["method"] == "GET"

    def test_delete_cname_removes_existing_record(self):
        get_response = FakeResponse(
            200,
            {
                "success": True,
                "result": [
                    {
                        "id": "abc123",
                        "name": "app.example.com",
                        "content": "target.example.net",
                        "ttl": 300,
                    }
                ],
            },
        )
        delete_response = FakeResponse(200, {"success": True, "result": {}})
        session = FakeSession([get_response, delete_response])
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            session=session,
        )

        assert provider.delete_cname("app") is True
        assert session.calls[0]["method"] == "GET"
        assert session.calls[1]["method"] == "DELETE"

    def test_create_cname_updates_existing_record_with_new_target(self):
        get_response = FakeResponse(
            200,
            {
                "success": True,
                "result": [
                    {
                        "id": "abc123",
                        "name": "app.example.com",
                        "content": "old.example.net",
                        "ttl": 120,
                    }
                ],
            },
        )
        update_response = FakeResponse(
            200,
            {
                "success": True,
                "result": {
                    "id": "abc123",
                    "name": "app.example.com",
                    "content": "new.example.net",
                    "ttl": 300,
                },
            },
        )
        session = FakeSession([get_response, update_response])
        provider = CloudflareProvider(
            api_token="token",
            zone_id="zone123",
            zone_name="example.com",
            session=session,
        )

        record = provider.create_cname("app", "new.example.net")

        assert record.target == "new.example.net"
        assert session.calls[0]["method"] == "GET"
        assert session.calls[1]["method"] == "PUT"


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

    def test_delete_cname(self, route53_context):
        provider = route53_context
        provider.create_cname("api", "api.internal.local")

        assert provider.delete_cname("api") is True
        assert provider.get_cname("api") is None


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

    def test_remove_cname_invokes_provider_with_subdomain(self):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        assert manager.remove_cname(subdomain="my-project") is True
        assert provider.deleted == ["my-project"]


class TestDNSManagerFromSSM:
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
        fake_session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "result": [{"id": "zone-123", "name": "example.com"}],
                    },
                )
            ]
        )

        manager = DNSManager.from_ssm(
            param_prefix="/devbox",
            ssm_client=ssm,
            http_session=fake_session,
        )

        assert isinstance(manager.provider, CloudflareProvider)
        assert manager.provider.zone_id == "zone-123"
        assert manager.provider.zone_name == "example.com"

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
        fake_session = FakeSession([FakeResponse(200, {"success": True, "result": []})])

        manager = DNSManager.from_ssm(
            param_prefix="/devbox",
            ssm_client=ssm,
            http_session=fake_session,
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
