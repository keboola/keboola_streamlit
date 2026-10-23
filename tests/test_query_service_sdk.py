import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("keboola_query_service")

# workaround to import module from parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit import KeboolaStreamlit  # noqa: E402
from keboola_streamlit import keboola_streamlit as module  # noqa: E402

JOB_PATH = "/api/v1/queries/qs-job-1"
SUBMIT_PATH = "/api/v1/branches/678/workspaces/12345/queries"


def _job_status(status, statement_status, query_id="sf-query-1", **extra):
    return {
        "queryJobId": "qs-job-1",
        "status": status,
        "actorType": "user",
        "createdAt": "2026-10-23T10:00:00Z",
        "changedAt": "2026-10-23T10:00:01Z",
        "statements": [
            {"id": "stmt-1", "query": "UPDATE t SET a = 1", "status": statement_status, "queryId": query_id}
        ],
        **extra,
    }


class FakeQueryService:
    """Serves the Query Service API from a dict of responses and records the requests."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def handle(self, request):
        self.requests.append((request.method, request.url.path))
        response = self.responses[(request.method, request.url.path)]
        if isinstance(response, list):
            response = response.pop(0)
        status_code, body = response
        return httpx.Response(status_code, json=body)


@pytest.fixture
def run_query(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"workspaceId": "12345"}')
    monkeypatch.setenv("KBC_WORKSPACE_MANIFEST_PATH", str(manifest))
    monkeypatch.setenv("QUERY_SERVICE_URL", "https://query.example.com")
    monkeypatch.setenv("BRANCH_ID", "678")
    module._QUERY_SERVICE_CLIENTS.clear()

    def run(responses, **kwargs):
        service = FakeQueryService(responses)
        app = KeboolaStreamlit(root_url="https://example.com", token="dummy_token")
        for retry_requests in (True, False):
            client = app._get_query_service_client(retry_requests=retry_requests)
            client._client = httpx.Client(
                base_url="https://query.example.com", transport=httpx.MockTransport(service.handle)
            )
        with (
            patch("requests.post") as mock_post,
            patch("streamlit.error"),
            patch("time.sleep"),
        ):
            mock_post.return_value.status_code = 201
            result = app.query_service_execute_query("UPDATE t SET a = 1", **kwargs)
        events = [call.kwargs["json"]["params"] for call in mock_post.call_args_list]
        return SimpleNamespace(result=result, events=events, requests=service.requests)

    yield run
    module._QUERY_SERVICE_CLIENTS.clear()


def test_completed_write_returns_rows_affected(run_query):
    completed = _job_status("completed", "completed")
    completed["statements"][0]["rowsAffected"] = 4

    run = run_query(
        {("POST", SUBMIT_PATH): (201, {"queryJobId": "qs-job-1"}), ("GET", JOB_PATH): (200, completed)},
        return_df=False,
    )

    assert run.result == 4
    assert run.events[-1]["event_type"] == "keboola_data_app_query_service_query_completed"
    assert run.events[-1]["backend_query_id"] == "sf-query-1"


def test_canceled_job_is_reported_as_failed(run_query):
    canceled = _job_status("canceled", "canceled", cancellationReason="Service restart")

    run = run_query(
        {("POST", SUBMIT_PATH): (201, {"queryJobId": "qs-job-1"}), ("GET", JOB_PATH): (200, canceled)},
        return_df=False,
    )

    assert run.result is None
    assert [event["event_type"] for event in run.events][-1] == "keboola_data_app_query_service_query_failed"
    assert "ended as canceled: Service restart" in run.events[-1]["event_data"]
    assert ("POST", f"{JOB_PATH}/cancel") not in run.requests


def test_failed_job_records_backend_query_id(run_query):
    failed = _job_status("failed", "failed")
    failed["statements"][0]["error"] = "SQL compilation error"

    run = run_query(
        {("POST", SUBMIT_PATH): (201, {"queryJobId": "qs-job-1"}), ("GET", JOB_PATH): (200, failed)},
        return_df=False,
    )

    assert run.result is None
    assert run.events[-1]["event_data"] == "Error: SQL compilation error"
    assert run.events[-1]["backend_query_id"] == "sf-query-1"


def test_submit_is_not_repeated_after_a_server_error(run_query):
    run = run_query({("POST", SUBMIT_PATH): (502, {"error": "Bad gateway"})}, return_df=False)

    assert run.result is None
    assert run.requests == [("POST", SUBMIT_PATH)]
    assert run.events[-1]["event_type"] == "keboola_data_app_query_service_query_failed"


def test_status_errors_are_retried(run_query):
    completed = _job_status("completed", "completed")

    run = run_query(
        {
            ("POST", SUBMIT_PATH): (201, {"queryJobId": "qs-job-1"}),
            ("GET", JOB_PATH): [(503, {"error": "Unavailable"}), (200, completed)],
        },
        return_df=False,
    )

    assert run.result == 0
    assert run.requests.count(("GET", JOB_PATH)) == 2


def test_polling_survives_an_outage_longer_than_the_sdk_retries(run_query):
    completed = _job_status("completed", "completed")
    unavailable = (503, {"error": "Unavailable"})

    run = run_query(
        {
            ("POST", SUBMIT_PATH): (201, {"queryJobId": "qs-job-1"}),
            ("GET", JOB_PATH): [unavailable] * 6 + [(200, completed)],
        },
        return_df=False,
    )

    assert run.result == 0
    assert ("POST", f"{JOB_PATH}/cancel") not in run.requests
