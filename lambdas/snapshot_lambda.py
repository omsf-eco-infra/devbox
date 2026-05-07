"""Lambda handlers for snapshot lifecycle events."""
from __future__ import annotations

import os
from typing import Any, Dict

from devbox import utils
from devbox.lifecycle import snapshots


def _load_tables() -> Dict[str, Any]:
    main_table_name = os.environ["MAIN_TABLE"]
    meta_table_name = os.environ["META_TABLE"]
    return {
        "main_table": utils.get_dynamodb_table(main_table_name),
        "meta_table": utils.get_dynamodb_table(meta_table_name),
    }


def create_snapshots(event, context) -> None:  # noqa: ARG001 - AWS Lambda signature
    deps = _load_tables()
    snapshots.create_snapshots(
        event,
        ec2_resource=utils.get_ec2_resource(),
        **deps,
    )


def create_image(event, context) -> None:  # noqa: ARG001 - AWS Lambda signature
    deps = _load_tables()
    snapshots.create_image(
        event,
        ec2_client=utils.get_ec2_client(),
        ec2_resource=utils.get_ec2_resource(),
        **deps,
    )


def mark_ready(event, context) -> None:  # noqa: ARG001 - AWS Lambda signature
    deps = _load_tables()
    snapshots.mark_ready(event, **deps)


def delete_volume(event, context) -> None:  # noqa: ARG001 - AWS Lambda signature
    deps = _load_tables()
    snapshots.delete_volume(
        event,
        ec2_client=utils.get_ec2_client(),
        **deps,
    )
