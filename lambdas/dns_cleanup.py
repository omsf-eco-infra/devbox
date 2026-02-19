"""Lambda wrapper for DNS cleanup lifecycle logic."""
from __future__ import annotations

import os

from devbox import utils
from devbox.lifecycle import dns as dns_lifecycle


def cleanup_dns(event, context) -> None:  # noqa: ARG001 - AWS Lambda signature
    dns_lifecycle.cleanup_dns(
        event,
        main_table=utils.get_dynamodb_table(os.environ["MAIN_TABLE"]),
        ec2_client=utils.get_ec2_client(),
        ssm_client=utils.get_ssm_client(),
        param_prefix=os.environ.get("PARAM_PREFIX", "/devbox"),
    )
