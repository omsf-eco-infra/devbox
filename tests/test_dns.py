"""Tests for DNS provider implementations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import boto3
import pytest
from moto import mock_aws

from src.devbox.dns import (
    CNAMERecord,
    CloudflareProvider,
    DNSManager,
    DEFAULT_TTL,
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
    def text(self) -> str:
        return str(self.payload)


class FakeSession:
    def __init__(self, responses: List[FakeResponse]) -> None:
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class StubProvider:
    def __init__(self) -> None:
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


class TestRoute53Provider:
    @pytest.fixture()
    def route53_context(self):
        with mock_aws():
            client = boto3.client("route53", region_name="us-east-1")
            zone = client.create_hosted_zone(Name="example.com", CallerReference="test")
            zone_id = zone["HostedZone"]["Id"].split("/")[-1]
            provider = Route53Provider(zone_id=zone_id, zone_name="example.com", client=client)
            yield provider

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
        result = manager.assign_cname_to_instance(
            project="demo",
            instance_public_dns="ec2-1-2-3-4.compute-1.amazonaws.com",
        )
        assert result is None

    def test_assign_cname_invokes_provider(self):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        fqdn = manager.assign_cname_to_instance("My_Project", "target.dev.internal")

        assert fqdn == "my-project.example.com"
        assert provider.created == [("my-project", "target.dev.internal")]

    def test_remove_cname_invokes_provider(self):
        provider = StubProvider()
        manager = DNSManager(provider=provider)

        assert manager.remove_cname_for_project("My_Project") is True
        assert provider.deleted == ["my-project"]
