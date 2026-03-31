"""Starlette application for the CLI Lambda."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route

from ..cli_protocol import CliAction, CliEventType
from .contracts import (
    CliRequestEnvelope,
    NDJSON_MIME_TYPE,
    InvalidCliRequestError,
    build_event,
    encode_event,
    parse_request_envelope,
)
from .status import handle_status_action

ActionHandler = Callable[[CliRequestEnvelope], list[dict[str, Any]]]

ACTION_HANDLERS: dict[CliAction, ActionHandler] = {
    CliAction.STATUS: handle_status_action,
}


def dispatch_action(envelope: CliRequestEnvelope) -> list[dict[str, object]]:
    """Dispatch one validated CLI Lambda request."""
    try:
        handler = ACTION_HANDLERS[envelope.action]
    except KeyError as exc:
        raise InvalidCliRequestError(f"Unsupported action: {envelope.action}") from exc
    return handler(envelope)


def execute_action(envelope: CliRequestEnvelope) -> list[dict[str, object]]:
    """Execute a request and map application failures to terminal error events."""
    try:
        return dispatch_action(envelope)
    except InvalidCliRequestError:
        raise
    except Exception as exc:
        return [build_event(CliEventType.ERROR, envelope.action, str(exc))]


def stream_events(events: Iterable[dict[str, object]]) -> StreamingResponse:
    """Create a streaming NDJSON response."""
    return StreamingResponse(
        (encode_event(event).encode("utf-8") for event in events),
        media_type=NDJSON_MIME_TYPE,
    )


def invalid_request_response(action: str, message: str) -> PlainTextResponse:
    """Create an NDJSON error response for invalid requests."""
    event = build_event(CliEventType.ERROR, action, message)
    return PlainTextResponse(
        encode_event(event),
        media_type=NDJSON_MIME_TYPE,
        status_code=400,
    )


async def invoke(request: Request) -> StreamingResponse | PlainTextResponse:
    """Handle one CLI Function URL request."""
    envelope: CliRequestEnvelope | None = None
    try:
        envelope = parse_request_envelope(await request.body())
        return stream_events(execute_action(envelope))
    except InvalidCliRequestError as exc:
        action = envelope.action if envelope is not None else "unknown"
        return invalid_request_response(action, str(exc))


async def healthcheck(request: Request) -> PlainTextResponse:  # noqa: ARG001
    """Readiness endpoint for Lambda Web Adapter."""
    return PlainTextResponse("ok")


app = Starlette(
    routes=[
        Route("/", invoke, methods=["POST"]),
        Route("/healthz", healthcheck, methods=["GET"]),
    ]
)
