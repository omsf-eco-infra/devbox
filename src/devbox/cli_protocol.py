"""Shared wire-protocol constants for Lambda-backed CLI commands."""

from __future__ import annotations

from enum import StrEnum

NDJSON_MIME_TYPE = "application/x-ndjson"
REQUEST_VERSION = "v1"
FUNCTION_URL_PARAMETER_SUFFIX = "/cli/functionUrl"


class CliAction(StrEnum):
    """Enumeration of supported Lambda-backed CLI actions."""

    STATUS = "status"


class CliEventType(StrEnum):
    """Enumeration of supported CLI NDJSON event types."""

    PROGRESS = "progress"
    WARNING = "warning"
    RESULT = "result"
    SUCCESS = "success"
    ERROR = "error"


SUPPORTED_ACTIONS = frozenset(action.value for action in CliAction)
EVENT_TYPES = frozenset(event_type.value for event_type in CliEventType)


def normalize_action(action: CliAction | str) -> str:
    """Normalize an action enum or string to its wire-format value.

    Parameters
    ----------
    action : CliAction | str
        Action enum member or raw action string.

    Returns
    -------
    str
        Wire-format action name.
    """
    if isinstance(action, CliAction):
        return action.value
    return action


def normalize_event_type(event_type: CliEventType | str) -> str:
    """Normalize an event enum or string to its wire-format value.

    Parameters
    ----------
    event_type : CliEventType | str
        Event enum member or raw event string.

    Returns
    -------
    str
        Wire-format event type name.
    """
    if isinstance(event_type, CliEventType):
        return event_type.value
    return event_type
