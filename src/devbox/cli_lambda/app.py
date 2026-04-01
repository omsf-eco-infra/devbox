"""Starlette application for the CLI Lambda."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route

from ..commands.status import handle_status_action
from ..cli_protocol import NDJSON_MIME_TYPE, CliAction, CliEventType
from .contracts import (
    CliRequestEnvelope,
    InvalidCliRequestError,
    build_event,
    encode_event,
    parse_request_envelope,
)

ActionHandler = Callable[[CliRequestEnvelope], list[dict[str, Any]]]

ACTION_HANDLERS: dict[CliAction, ActionHandler] = {
    CliAction.STATUS: handle_status_action,
}


def dispatch_action(envelope: CliRequestEnvelope) -> list[dict[str, object]]:
    """Dispatch one validated CLI Lambda request.

    Parameters
    ----------
    envelope : CliRequestEnvelope
        Validated request envelope to route.

    Returns
    -------
    list[dict[str, object]]
        Event payloads emitted by the selected action handler.

    Raises
    ------
    InvalidCliRequestError
        Raised when the action is not registered in the dispatch table.
    """
    try:
        handler = ACTION_HANDLERS[envelope.action]
    except KeyError as exc:
        raise InvalidCliRequestError(f"Unsupported action: {envelope.action}") from exc
    return handler(envelope)


def execute_action(envelope: CliRequestEnvelope) -> list[dict[str, object]]:
    """Execute one CLI Lambda request.

    Parameters
    ----------
    envelope : CliRequestEnvelope
        Validated request envelope to execute.

    Returns
    -------
    list[dict[str, object]]
        Successful action events, or a single terminal ``error`` event when an
        application-level exception occurs.

    Raises
    ------
    InvalidCliRequestError
        Raised when the request is structurally invalid and should return HTTP
        400 instead of an application ``error`` event.
    """
    try:
        return dispatch_action(envelope)
    except InvalidCliRequestError:
        raise
    except Exception as exc:
        return [build_event(CliEventType.ERROR, envelope.action, str(exc))]


def stream_events(events: Iterable[dict[str, object]]) -> StreamingResponse:
    """Create a streaming NDJSON response.

    Parameters
    ----------
    events : collections.abc.Iterable[dict[str, object]]
        Event payloads to encode as NDJSON lines.

    Returns
    -------
    StreamingResponse
        Streaming HTTP response with the CLI NDJSON media type.
    """
    return StreamingResponse(
        (encode_event(event).encode("utf-8") for event in events),
        media_type=NDJSON_MIME_TYPE,
    )


def invalid_request_response(action: CliAction | str, message: str) -> PlainTextResponse:
    """Create an NDJSON HTTP 400 response for invalid requests.

    Parameters
    ----------
    action : CliAction | str
        Action name associated with the invalid request.
    message : str
        Human-readable validation error.

    Returns
    -------
    PlainTextResponse
        One-line NDJSON error response with HTTP status 400.
    """
    event = build_event(CliEventType.ERROR, action, message)
    return PlainTextResponse(
        encode_event(event),
        media_type=NDJSON_MIME_TYPE,
        status_code=400,
    )


async def invoke(request: Request) -> StreamingResponse | PlainTextResponse:
    """Handle one CLI Function URL request.

    Parameters
    ----------
    request : Request
        Incoming Starlette request from the Lambda Web Adapter.

    Returns
    -------
    StreamingResponse | PlainTextResponse
        Streaming event response for valid requests, or an NDJSON HTTP 400
        response for invalid requests.
    """
    envelope: CliRequestEnvelope | None = None
    try:
        envelope = parse_request_envelope(await request.body())
        return stream_events(execute_action(envelope))
    except InvalidCliRequestError as exc:
        action = envelope.action if envelope is not None else "unknown"
        return invalid_request_response(action, str(exc))


async def healthcheck(request: Request) -> PlainTextResponse:  # noqa: ARG001
    """Serve the Lambda Web Adapter readiness endpoint.

    Parameters
    ----------
    request : Request
        Incoming health-check request.

    Returns
    -------
    PlainTextResponse
        Plain-text ``ok`` readiness response.
    """
    return PlainTextResponse("ok")


app = Starlette(
    routes=[
        Route("/", invoke, methods=["POST"]),
        Route("/healthz", healthcheck, methods=["GET"]),
    ]
)
