import json
import os
import sys
import types
from dataclasses import dataclass, field
from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit import KeboolaStreamlit  # noqa: E402

USER_HEADERS = {"X-Kbc-User-Email": "jane@example.com", "X-Kbc-User-Id": "oidc-sub-1"}


class FakeQueryServiceError(Exception):
    def __init__(self, message, status_code=None, **kwargs):
        super().__init__(message)
        self.status_code = status_code


class FakeJobError(FakeQueryServiceError):
    pass


class FakeJobTimeoutError(FakeQueryServiceError):
    pass


@dataclass
class FakeColumn:
    name: str
    type: str


@dataclass
class FakeResult:
    columns: List[FakeColumn]
    data: List[List[Any]]
    number_of_rows: Optional[int] = None


@dataclass
class FakeStatement:
    id: str


@dataclass
class FakeStatus:
    statements: List[FakeStatement] = field(default_factory=list)


def make_fake_module(client):
    module = types.ModuleType("keboola_query_service")
    module.Client = MagicMock(return_value=client)
    module.QueryServiceError = FakeQueryServiceError
    module.JobError = FakeJobError
    module.JobTimeoutError = FakeJobTimeoutError
    return module


@pytest.fixture
def qs_client():
    client = MagicMock()
    client.submit_job.return_value = "job-1"
    client.wait_for_job.return_value = FakeStatus(statements=[FakeStatement(id="st-1")])
    client.get_job_results.return_value = FakeResult(
        columns=[FakeColumn("ID", "fixed"), FakeColumn("NAME", "text")],
        data=[["1", "a"], ["2", "b"]],
        number_of_rows=2,
    )
    return client


@pytest.fixture
def app(qs_client):
    ks = KeboolaStreamlit(
        root_url="https://connection.example.com",
        token="dummy_token",
        query_service_url="https://query.example.com",
        branch_id="123",
        workspace_id="456",
    )
    ks.set_dev_mockup_headers(USER_HEADERS)
    ks.create_event = MagicMock(return_value=(201, "{}"))
    with patch.dict(sys.modules, {"keboola_query_service": make_fake_module(qs_client)}):
        yield ks


def test_get_client_without_extra_raises_install_hint():
    ks = KeboolaStreamlit(root_url="https://connection.example.com", token="dummy_token")
    with patch.dict(sys.modules, {"keboola_query_service": None}):
        with pytest.raises(ImportError, match=r"keboola-streamlit\[query-service\]"):
            ks.query_service_get_client()


def test_query_service_url_from_env(monkeypatch):
    monkeypatch.setenv("QUERY_SERVICE_URL", "https://query.env.example.com/")
    ks = KeboolaStreamlit(root_url="https://connection.example.com", token="dummy_token")
    assert ks._get_query_service_url() == "https://query.env.example.com"


def test_query_service_url_discovered_from_storage_index(monkeypatch):
    monkeypatch.delenv("QUERY_SERVICE_URL", raising=False)
    ks = KeboolaStreamlit(root_url="https://connection.example.com", token="dummy_token")
    response = MagicMock()
    response.json.return_value = {"services": [{"id": "query", "url": "https://query.discovered.com"}]}
    with patch("requests.get", return_value=response) as get:
        assert ks._get_query_service_url() == "https://query.discovered.com"
    assert get.call_args.args[0] == "https://connection.example.com/v2/storage"
    assert get.call_args.kwargs["headers"] == {"X-StorageApi-Token": "dummy_token"}


def test_query_service_url_missing_raises(monkeypatch):
    monkeypatch.delenv("QUERY_SERVICE_URL", raising=False)
    ks = KeboolaStreamlit(root_url="https://connection.example.com", token="dummy_token")
    response = MagicMock()
    response.json.return_value = {"services": [{"id": "sync-actions", "url": "https://x"}]}
    with patch("requests.get", return_value=response):
        with pytest.raises(ValueError, match="Query Service URL not found"):
            ks._get_query_service_url()


def test_branch_id_from_env_and_alias_rejected(monkeypatch):
    ks = KeboolaStreamlit(root_url="https://connection.example.com", token="dummy_token")
    monkeypatch.setenv("BRANCH_ID", "987")
    assert ks._get_branch_id() == "987"
    monkeypatch.setenv("BRANCH_ID", "default")
    with pytest.raises(ValueError, match="numeric branch ID"):
        ks._get_branch_id()
    monkeypatch.delenv("BRANCH_ID")
    with pytest.raises(ValueError, match="BRANCH_ID"):
        ks._get_branch_id()


def test_workspace_id_manifest_beats_env(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"workspaceId": "111"}))
    monkeypatch.setenv("KBC_WORKSPACE_MANIFEST_PATH", str(manifest))
    monkeypatch.setenv("WORKSPACE_ID", "222")
    ks = KeboolaStreamlit(root_url="https://connection.example.com", token="dummy_token")
    assert ks._get_workspace_id() == "111"


def test_workspace_id_missing_raises(monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("WORKSPACE_ID", raising=False)
    ks = KeboolaStreamlit(root_url="https://connection.example.com", token="dummy_token")
    with pytest.raises(ValueError, match="Storage Access"):
        ks._get_workspace_id()


def test_execute_query_returns_dataframe_and_logs_access(app, qs_client):
    df = app.query_service_execute_query("SELECT ID, NAME FROM T")

    assert list(df.columns) == ["ID", "NAME"]
    assert df["ID"].tolist() == [1, 2]
    assert df["NAME"].tolist() == ["a", "b"]
    qs_client.submit_job.assert_called_once_with(
        branch_id="123", workspace_id="456", statements=["SELECT ID, NAME FROM T"], transactional=True
    )
    app.create_event.assert_called_once()
    kwargs = app.create_event.call_args.kwargs
    assert kwargs["event_type"] == "keboola_data_app_query_service_query"
    assert kwargs["event_data"] == "Query: SELECT ID, NAME FROM T"
    assert kwargs["extra_params"] == {"query_job_id": "job-1", "branch_id": "123", "workspace_id": "456"}


def test_access_event_is_written_before_results_are_fetched(app, qs_client):
    order = []
    app.create_event.side_effect = lambda **kwargs: order.append("event") or (201, "{}")
    qs_client.wait_for_job.side_effect = lambda *a, **k: order.append("wait") or FakeStatus([FakeStatement("st-1")])
    app.query_service_execute_query("SELECT 1")
    assert order == ["event", "wait"]


def test_execute_query_list_returns_list(app, qs_client):
    qs_client.wait_for_job.return_value = FakeStatus(statements=[FakeStatement("st-1"), FakeStatement("st-2")])
    result = app.query_service_execute_query(["SELECT 1", "SELECT 2"])
    assert isinstance(result, list) and len(result) == 2
    assert app.create_event.call_args.kwargs["event_data"] == "Query: SELECT 1\nSELECT 2"


def test_execute_query_without_return_df(app, qs_client):
    assert app.query_service_execute_query("DELETE FROM T", return_df=False) is None
    qs_client.get_job_results.assert_not_called()
    app.create_event.assert_called_once()


def test_coerce_numeric_disabled_keeps_strings(app):
    df = app.query_service_execute_query("SELECT ID, NAME FROM T", coerce_numeric=False)
    assert df["ID"].tolist() == ["1", "2"]


def test_failed_access_event_cancels_job_and_returns_no_data(app, qs_client):
    app.create_event.side_effect = [(None, None), (None, None)]
    with patch("streamlit.error") as st_error:
        assert app.query_service_execute_query("SELECT 1") is None
    qs_client.cancel_job.assert_called_once()
    qs_client.wait_for_job.assert_not_called()
    qs_client.get_job_results.assert_not_called()
    st_error.assert_called_once()


def test_failed_job_logs_failure_event(app, qs_client):
    qs_client.wait_for_job.side_effect = FakeJobError("syntax error", status_code=None)
    with patch("streamlit.error") as st_error:
        assert app.query_service_execute_query("SELEC 1") is None
    assert app.create_event.call_count == 2
    failure = app.create_event.call_args.kwargs
    assert failure["event_type"] == "keboola_data_app_query_service_query_failed"
    assert failure["extra_params"]["query_job_id"] == "job-1"
    assert "syntax error" in failure["extra_params"]["error"]
    st_error.assert_called_once()


def test_raise_on_error_reraises(app, qs_client):
    qs_client.wait_for_job.side_effect = FakeJobError("boom")
    with pytest.raises(FakeJobError):
        app.query_service_execute_query("SELECT 1", raise_on_error=True)


def test_timeout_cancels_job(app, qs_client):
    qs_client.wait_for_job.side_effect = FakeJobTimeoutError("too slow")
    with patch("streamlit.error"):
        app.query_service_execute_query("SELECT 1", max_wait_time=5)
    qs_client.cancel_job.assert_called_once()
    assert "timed out" in qs_client.cancel_job.call_args.kwargs["reason"]


def test_submit_retries_transient_credentials_error(app, qs_client):
    qs_client.submit_job.side_effect = [
        FakeQueryServiceError("Failed to get workspace credentials", status_code=403),
        "job-2",
    ]
    with patch("time.sleep"):
        app.query_service_execute_query("SELECT 1")
    assert qs_client.submit_job.call_count == 2
    assert app.create_event.call_args.kwargs["extra_params"]["query_job_id"] == "job-2"


def test_submit_does_not_retry_permanent_error(app, qs_client):
    qs_client.submit_job.side_effect = FakeQueryServiceError("Forbidden", status_code=403)
    with patch("streamlit.error"), patch("time.sleep"):
        assert app.query_service_execute_query("SELECT 1") is None
    assert qs_client.submit_job.call_count == 1
    # Nothing was submitted, so there is no job to attribute and no event to write.
    app.create_event.assert_not_called()


def test_results_are_paginated_and_capped_by_max_rows(app, qs_client):
    with patch("keboola_streamlit.keboola_streamlit.QUERY_SERVICE_PAGE_SIZE", 2):
        qs_client.get_job_results.side_effect = [
            FakeResult([FakeColumn("ID", "fixed")], [["1"], ["2"]], number_of_rows=5),
            FakeResult([FakeColumn("ID", "fixed")], [["3"], ["4"]], number_of_rows=5),
        ]
        df = app.query_service_execute_query("SELECT ID FROM T", max_rows=3)
    assert df["ID"].tolist() == [1, 2, 3]
    assert qs_client.get_job_results.call_args_list[1].kwargs == {"offset": 2, "page_size": 1}


def test_create_event_includes_user_id_and_extra_params():
    ks = KeboolaStreamlit(root_url="https://connection.example.com", token="dummy_token")
    ks.set_dev_mockup_headers(USER_HEADERS)
    with patch("requests.post") as post:
        post.return_value.status_code = 201
        ks.create_event(message="m", extra_params={"query_job_id": "job-1"})
    params = post.call_args.kwargs["json"]["params"]
    assert params["user"] == "jane@example.com"
    assert params["user_id"] == "oidc-sub-1"
    assert params["query_job_id"] == "job-1"


def test_empty_query_list_rejected(app):
    with pytest.raises(ValueError):
        app.query_service_execute_query([])


def test_dataframe_columns_from_result():
    result = FakeResult([FakeColumn("A", "text")], [["x"]])
    df = KeboolaStreamlit._query_result_to_df(result, coerce_numeric=True)
    pd.testing.assert_frame_equal(df, pd.DataFrame({"A": ["x"]}))
