"""Unit tests for the remote CLI client."""

from __future__ import annotations

from datetime import datetime
import json
from unittest.mock import MagicMock, patch

import pytest
import responses

from devbox.remote_client import (
    NDJSON_MIME_TYPE,
    RemoteInvocationError,
    fetch_remote_status,
    get_cli_function_url,
    get_function_url_region,
)


FUNCTION_URL = "https://abc123.lambda-url.us-east-1.on.aws/"


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


def test_fetch_remote_status_surfaces_missing_ssm_parameter():
    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        side_effect=ValueError("missing parameter"),
    ):
        with pytest.raises(RemoteInvocationError, match="Failed to resolve CLI function URL"):
            fetch_remote_status(None, "/devbox", console=MagicMock())


@responses.activate
def test_fetch_remote_status_rehydrates_timestamps():
    def request_callback(request):
        assert request.headers["Accept"] == NDJSON_MIME_TYPE
        assert request.headers["Content-Type"] == "application/json"
        assert "Authorization" in request.headers

        body = request.body
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        payload = json.loads(body)
        assert payload["action"] == "status"
        assert payload["param_prefix"] == "/devbox"
        assert payload["payload"] == {"project": "demo"}

        stream = (
            '{"type":"result","action":"status","message":"ready","data":'
            '{"instances":[{"InstanceId":"i-123","Project":"demo",'
            '"LaunchTime":"2026-03-30T20:30:00+00:00"}],'
            '"volumes":[{"VolumeId":"vol-123","Project":"demo","State":"in-use",'
            '"Size":100,"AvailabilityZone":"us-east-1a","IsOrphaned":false}],'
            '"snapshots":[{"SnapshotId":"snap-123","Project":"demo",'
            '"Progress":"100%","VolumeSize":100,'
            '"StartTime":"2026-03-30T18:00:00+00:00","IsOrphaned":false}]}}\n'
            '{"type":"success","action":"status","message":"done","data":{}}\n'
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
        result = fetch_remote_status("demo", "/devbox", console=MagicMock())

    assert isinstance(result["instances"][0]["LaunchTime"], datetime)
    assert isinstance(result["snapshots"][0]["StartTime"], datetime)


@responses.activate
def test_fetch_remote_status_surfaces_warning_events():
    console = MagicMock()
    stream = (
        '{"type":"warning","action":"status","message":"heads-up","data":{}}\n'
        '{"type":"result","action":"status","message":"ready",'
        '"data":{"instances":[],"volumes":[],"snapshots":[]}}\n'
        '{"type":"success","action":"status","message":"done","data":{}}\n'
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
        result = fetch_remote_status(None, "/devbox", console=console)

    assert result == {"instances": [], "volumes": [], "snapshots": []}
    console.print_warning.assert_called_once_with("heads-up")


@responses.activate
def test_fetch_remote_status_rejects_malformed_ndjson():
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
            fetch_remote_status(None, "/devbox", console=MagicMock())


@responses.activate
def test_fetch_remote_status_rejects_http_errors():
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
            fetch_remote_status(None, "/devbox", console=MagicMock())


@responses.activate
def test_fetch_remote_status_requires_terminal_event():
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body=(
            '{"type":"result","action":"status","message":"ready",'
            '"data":{"instances":[],"volumes":[],"snapshots":[]}}\n'
        ),
        content_type=NDJSON_MIME_TYPE,
        status=200,
    )

    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        with pytest.raises(RemoteInvocationError, match="terminal event"):
            fetch_remote_status(None, "/devbox", console=MagicMock())


@responses.activate
def test_fetch_remote_status_surfaces_terminal_error_event():
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body='{"type":"error","action":"status","message":"boom","data":{}}\n',
        content_type=NDJSON_MIME_TYPE,
        status=200,
    )

    with patch(
        "devbox.remote_client.utils.get_ssm_parameter",
        return_value=FUNCTION_URL,
    ):
        with pytest.raises(RemoteInvocationError, match="boom"):
            fetch_remote_status(None, "/devbox", console=MagicMock())


@responses.activate
def test_fetch_remote_status_rejects_success_without_result():
    responses.add(
        responses.POST,
        FUNCTION_URL,
        body='{"type":"success","action":"status","message":"done","data":{}}\n',
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
            fetch_remote_status(None, "/devbox", console=MagicMock())
