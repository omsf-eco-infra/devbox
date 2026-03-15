"""Tests for DNS cleanup lifecycle and Lambda wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from devbox.lifecycle.dns import cleanup_dns as lifecycle_cleanup_dns


class TestLifecycleCleanupDns:
    @patch("devbox.lifecycle.dns.DNSManager.from_ssm")
    def test_cleanup_dns_deletes_domain_from_project_record(self, mock_from_ssm):
        table = MagicMock()
        table.get_item.return_value = {
            "Item": {"project": "my-project", "CNAMEDomain": "my-project"}
        }

        ec2 = MagicMock()
        ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {"Tags": [{"Key": "Project", "Value": "my-project"}]}
                    ]
                }
            ]
        }

        dns_manager = MagicMock()
        dns_manager.provider = object()
        dns_manager.normalize_subdomain.return_value = "my-project"
        dns_manager.remove_cname.return_value = True
        mock_from_ssm.return_value = dns_manager

        event = {"detail": {"state": "shutting-down", "instance-id": "i-1234567890"}}
        lifecycle_cleanup_dns(
            event,
            main_table=table,
            ec2_client=ec2,
            ssm_client=MagicMock(),
            param_prefix="/devbox",
        )

        dns_manager.remove_cname.assert_called_once_with(subdomain="my-project")

    @patch("devbox.lifecycle.dns.DNSManager.from_ssm")
    def test_cleanup_dns_falls_back_to_instance_id_scan(self, mock_from_ssm):
        table = MagicMock()
        table.scan.return_value = {
            "Items": [{"project": "my-project", "CNAMEDomain": "keep-name"}]
        }

        ec2 = MagicMock()
        ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"Tags": []}]}]}

        dns_manager = MagicMock()
        dns_manager.provider = object()
        dns_manager.normalize_subdomain.return_value = "keep-name"
        dns_manager.remove_cname.return_value = True
        mock_from_ssm.return_value = dns_manager

        event = {"detail": {"state": "shutting-down", "instance-id": "i-abc"}}
        lifecycle_cleanup_dns(
            event,
            main_table=table,
            ec2_client=ec2,
            ssm_client=MagicMock(),
            param_prefix="/devbox",
        )

        table.scan.assert_called_once()
        scan_kwargs = table.scan.call_args.kwargs
        assert scan_kwargs["ProjectionExpression"] == "#project, CNAMEDomain, InstanceId"
        assert scan_kwargs["ExpressionAttributeNames"] == {"#project": "project"}
        dns_manager.remove_cname.assert_called_once_with(subdomain="keep-name")

    @patch("devbox.lifecycle.dns.DNSManager.from_ssm")
    def test_cleanup_dns_skips_non_termination_events(self, mock_from_ssm):
        table = MagicMock()
        ec2 = MagicMock()
        event = {"detail": {"state": "running", "instance-id": "i-1234567890"}}

        lifecycle_cleanup_dns(
            event,
            main_table=table,
            ec2_client=ec2,
            ssm_client=MagicMock(),
        )

        table.get_item.assert_not_called()
        mock_from_ssm.assert_not_called()
