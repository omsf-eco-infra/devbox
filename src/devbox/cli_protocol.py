"""Shared wire-protocol constants for Lambda-backed CLI commands."""

from __future__ import annotations

from enum import StrEnum

NDJSON_MIME_TYPE = "application/x-ndjson"
REQUEST_VERSION = "v1"
FUNCTION_URL_PARAMETER_SUFFIX = "/cli/functionUrl"


class CliAction(StrEnum):
    """Supported Lambda-backed CLI actions."""

    STATUS = "status"


class CliEventType(StrEnum):
    """Supported NDJSON event types."""

    PROGRESS = "progress"
    WARNING = "warning"
    RESULT = "result"
    SUCCESS = "success"
    ERROR = "error"


SUPPORTED_ACTIONS = frozenset(action.value for action in CliAction)
EVENT_TYPES = frozenset(event_type.value for event_type in CliEventType)


def normalize_action(action: CliAction | str) -> str:
    """Return the wire-format action name."""
    if isinstance(action, CliAction):
        return action.value
    return action


def normalize_event_type(event_type: CliEventType | str) -> str:
    """Return the wire-format event type name."""
    if isinstance(event_type, CliEventType):
        return event_type.value
    return event_type
