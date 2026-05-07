"""Tests for the DNS cleanup Lambda wrapper."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from dns_cleanup import cleanup_dns as lambda_cleanup_dns


class TestLambdaCleanupDnsWrapper:
    @patch("dns_cleanup.dns_lifecycle.cleanup_dns")
    @patch("dns_cleanup.utils.get_ssm_client")
    @patch("dns_cleanup.utils.get_ec2_client")
    @patch("dns_cleanup.utils.get_dynamodb_table")
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
