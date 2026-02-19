"""Tests for DNS cleanup lifecycle and Lambda wrapper."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from devbox.lifecycle.dns import cleanup_dns as lifecycle_cleanup_dns
from devbox.lambdas.dns_cleanup import cleanup_dns as lambda_cleanup_dns


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
        dns_manager.normalize_stored_subdomain.return_value = "my-project"
        dns_manager.remove_cname_for_project.return_value = True
        mock_from_ssm.return_value = dns_manager

        event = {"detail": {"state": "shutting-down", "instance-id": "i-1234567890"}}
        lifecycle_cleanup_dns(
            event,
            main_table=table,
            ec2_client=ec2,
            ssm_client=MagicMock(),
            param_prefix="/devbox",
        )

        dns_manager.remove_cname_for_project.assert_called_once_with(
            project="my-project",
            custom_subdomain="my-project",
        )

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
        dns_manager.normalize_stored_subdomain.return_value = "keep-name"
        dns_manager.remove_cname_for_project.return_value = True
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
        dns_manager.remove_cname_for_project.assert_called_once_with(
            project="my-project",
            custom_subdomain="keep-name",
        )

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


class TestLambdaCleanupDnsWrapper:
    @patch("devbox.lambdas.dns_cleanup.dns_lifecycle.cleanup_dns")
    @patch("devbox.lambdas.dns_cleanup.utils.get_ssm_client")
    @patch("devbox.lambdas.dns_cleanup.utils.get_ec2_client")
    @patch("devbox.lambdas.dns_cleanup.utils.get_dynamodb_table")
    def test_lambda_wrapper_delegates_to_lifecycle(
        self,
        mock_get_table,
        mock_get_ec2,
        mock_get_ssm,
        mock_cleanup_dns,
    ):
        table = MagicMock()
        ec2 = MagicMock()
        ssm = MagicMock()
        mock_get_table.return_value = table
        mock_get_ec2.return_value = ec2
        mock_get_ssm.return_value = ssm

        event = {"detail": {"state": "shutting-down", "instance-id": "i-123"}}
        with patch.dict(os.environ, {"MAIN_TABLE": "devbox-main", "PARAM_PREFIX": "/devbox"}):
            lambda_cleanup_dns(event, None)

        mock_cleanup_dns.assert_called_once_with(
            event,
            main_table=table,
            ec2_client=ec2,
            ssm_client=ssm,
            param_prefix="/devbox",
        )
