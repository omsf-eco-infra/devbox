"""Unit tests for the remote CLI client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
import responses

from devbox.remote_client import (
    NDJSON_MIME_TYPE,
    RemoteInvocationError,
    get_cli_function_url,
    get_function_url_region,
    invoke_action,
    normalize_param_prefix,
)


FUNCTION_URL = "https://abc123.lambda-url.us-east-1.on.aws/"


def test_normalize_param_prefix_adds_leading_slash():
    assert normalize_param_prefix("devbox") == "/devbox"


def test_normalize_param_prefix_strips_trailing_slash():
    assert normalize_param_prefix("/devbox/") == "/devbox"


def test_normalize_param_prefix_rejects_consecutive_slashes():
    with pytest.raises(ValueError, match="Parameter prefix cannot contain consecutive slashes"):
        normalize_param_prefix("/devbox//nested")


def test_get_cli_function_url_uses_expected_parameter_name():
    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ) as mock_get_parameter:
        result = get_cli_function_url("devbox")

    assert result == FUNCTION_URL
    mock_get_parameter.assert_called_once_with("/devbox/cli/functionUrl")


def test_get_function_url_region_parses_lambda_url():
    assert get_function_url_region(FUNCTION_URL) == "us-east-1"


def test_get_function_url_region_rejects_invalid_host():
    with pytest.raises(RemoteInvocationError, match="Invalid Lambda Function URL"):
        get_function_url_region("https://example.com/not-a-lambda-url")


def test_invoke_action_surfaces_missing_ssm_parameter():
    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        side_effect=ValueError("missing parameter"),
    ):
        with pytest.raises(RemoteInvocationError, match="Failed to resolve CLI function URL"):
            invoke_action("demo-action", {}, "/devbox", console=MagicMock())


@responses.activate
def test_invoke_action_dispatches_signed_request():
    def request_callback(request):
        assert request.headers["Accept"] == NDJSON_MIME_TYPE
        assert request.headers["Content-Type"] == "application/json"
        assert "Authorization" in request.headers

        payload = json.loads(request.body)
        assert payload["action"] == "demo-action"
        assert payload["param_prefix"] == "/devbox"
        assert payload["payload"] == {"project": "demo"}

        stream = (
            '{"type":"result","action":"demo-action","message":"ready","data":'
            '{"ok":true}}\n'
            '{"type":"success","action":"demo-action","message":"done","data":{}}\n'
        )
        return (200, {"Content-Type": NDJSON_MIME_TYPE}, stream)

    responses.add_callback(
        responses.POST,
        FUNCTION_URL,
        callback=request_callback,
        content_type=NDJSON_MIME_TYPE,
    )

    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        result = invoke_action(
            action="demo-action",
            payload={"project": "demo"},
            param_prefix="/devbox",
            console=MagicMock(),
        )

    assert result == {"ok": True}


@responses.activate
def test_invoke_action_surfaces_warning_events():
    console = MagicMock()
    stream = (
        '{"type":"warning","action":"demo-action","message":"heads-up","data":{}}\n'
        '{"type":"result","action":"demo-action","message":"ready","data":{"ok":true}}\n'
        '{"type":"success","action":"demo-action","message":"done","data":{}}\n'
    )
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body=stream,
        content_type=NDJSON_MIME_TYPE,
        status=200,
    )

    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        result = invoke_action("demo-action", {}, "/devbox", console=console)

    assert result == {"ok": True}
    console.print_warning.assert_called_once_with("heads-up")


@responses.activate
def test_invoke_action_surfaces_progress_events():
    console = MagicMock()
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body=(
            '{"type":"progress","action":"demo-action","message":"working","data":{}}\n'
            '{"type":"result","action":"demo-action","message":"ready","data":{"ok":true}}\n'
            '{"type":"success","action":"demo-action","message":"done","data":{}}\n'
        ),
        content_type=NDJSON_MIME_TYPE,
        status=200,
    )

    with patch("devbox.remote_client.utils.get_ssm_parameter", return_value=FUNCTION_URL):
        invoke_action("demo-action", {}, "/devbox", console=console)

    console.print_progress.assert_called_once_with("working")


def test_invoke_action_uses_configurable_timeout():
    response = MagicMock(status_code=200)
    response.iter_lines.return_value = [
        '{"type":"result","action":"demo-action","message":"ready","data":{"ok":true}}',
        '{"type":"success","action":"demo-action","message":"done","data":{}}',
    ]
    with (
        patch("devbox.remote_client.utils.get_ssm_parameter", return_value=FUNCTION_URL),
        patch("devbox.remote_client.requests.post", return_value=response) as mock_post,
    ):
        invoke_action("demo-action", {}, "/devbox", read_timeout=930)

    assert mock_post.call_args.kwargs["timeout"] == (10, 930)
    response.close.assert_called_once_with()


@responses.activate
def test_invoke_action_rejects_malformed_ndjson():
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body="not-json\n",
        content_type=NDJSON_MIME_TYPE,
        status=200,
    )

    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        with pytest.raises(RemoteInvocationError, match="Malformed NDJSON event"):
            invoke_action("demo-action", {}, "/devbox", console=MagicMock())


@responses.activate
def test_invoke_action_rejects_http_errors():
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body="forbidden",
        status=403,
    )

    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        with pytest.raises(RemoteInvocationError, match="HTTP 403"):
            invoke_action("demo-action", {}, "/devbox", console=MagicMock())


def test_invoke_action_wraps_request_exception():
    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        with patch(
            "devbox.remote_client.requests.post",
            side_effect=requests.Timeout("timed out"),
        ):
            with pytest.raises(
                RemoteInvocationError,
                match=r"Remote demo-action request failed: timed out",
            ):
                invoke_action("demo-action", {}, "/devbox", console=MagicMock())


@responses.activate
def test_invoke_action_requires_terminal_event():
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body=(
            '{"type":"result","action":"demo-action","message":"ready","data":{"ok":true}}\n'
        ),
        content_type=NDJSON_MIME_TYPE,
        status=200,
    )

    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        with pytest.raises(RemoteInvocationError, match="terminal event"):
            invoke_action("demo-action", {}, "/devbox", console=MagicMock())


@responses.activate
def test_invoke_action_surfaces_terminal_error_event():
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body='{"type":"error","action":"demo-action","message":"boom","data":{}}\n',
        content_type=NDJSON_MIME_TYPE,
        status=200,
    )

    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        with pytest.raises(RemoteInvocationError, match="boom"):
            invoke_action("demo-action", {}, "/devbox", console=MagicMock())


@responses.activate
def test_invoke_action_rejects_success_without_result():
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body='{"type":"success","action":"demo-action","message":"done","data":{}}\n',
        content_type=NDJSON_MIME_TYPE,
        status=200,
    )

    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        with pytest.raises(
            RemoteInvocationError,
            match="did not include a result event",
        ):
            invoke_action("demo-action", {}, "/devbox", console=MagicMock())
