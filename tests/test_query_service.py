import builtins
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# workaround to import module from parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit import KeboolaStreamlit  # noqa: E402


class FakeQueryServiceError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FakeJobTimeoutError(FakeQueryServiceError):
    pass


def _column(name, type_="TEXT"):
    return SimpleNamespace(name=name, type=type_)


def _page(columns, data, number_of_rows=None):
    return SimpleNamespace(columns=columns, data=data, number_of_rows=number_of_rows)


def _mock_query_service_module(columns=(("A", "TEXT"),), data=(("1",),)):
    fake_client = MagicMock(name="query_service_client")
    fake_client.submit_job.return_value = "qs-job-1"
    fake_client.wait_for_job.return_value = SimpleNamespace(statements=[SimpleNamespace(id="stmt-1")])
    fake_client.get_job_results.return_value = _page(
        [_column(name, type_) for name, type_ in columns], [list(row) for row in data]
    )
    fake_client_cls = MagicMock(name="Client", return_value=fake_client)
    fake_module = MagicMock(
        Client=fake_client_cls, QueryServiceError=FakeQueryServiceError, JobTimeoutError=FakeJobTimeoutError
    )
    return fake_module, fake_client_cls, fake_client


@pytest.fixture
def keboola_streamlit():
    return KeboolaStreamlit(root_url="https://example.com", token="dummy_token")


@pytest.fixture
def query_service_env(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"workspaceId": "12345"}')
    monkeypatch.setenv("KBC_WORKSPACE_MANIFEST_PATH", str(manifest))
    monkeypatch.setenv("QUERY_SERVICE_URL", "https://query.example.com")
    monkeypatch.setenv("BRANCH_ID", "678")
    return manifest


def test_get_query_service_client_without_extra(keboola_streamlit, query_service_env):
    with patch.dict(sys.modules, {"keboola_query_service": None}):
        with pytest.raises(ImportError, match=r"keboola-streamlit\[query-service\]"):
            keboola_streamlit._get_query_service_client()


def test_get_query_service_client_missing_unrelated_dependency_is_not_masked(keboola_streamlit, query_service_env):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "keboola_query_service":
            raise ModuleNotFoundError("No module named 'httpx'", name="httpx")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(ModuleNotFoundError, match="httpx"):
            keboola_streamlit._get_query_service_client()


def test_get_query_service_client_is_created_once(keboola_streamlit, query_service_env):
    fake_module, fake_client_cls, fake_client = _mock_query_service_module()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}):
        first = keboola_streamlit._get_query_service_client()
        second = keboola_streamlit._get_query_service_client()

    assert first is fake_client
    assert second is fake_client
    fake_client_cls.assert_called_once_with(
        base_url="https://query.example.com", token="dummy_token", user_agent="keboola-streamlit"
    )


def test_query_service_url_from_storage_index(keboola_streamlit, monkeypatch):
    monkeypatch.delenv("QUERY_SERVICE_URL", raising=False)
    index = {
        "services": [
            {"id": "import", "url": "https://import.example.com"},
            {"id": "query", "url": "https://q.example.com"},
        ]
    }

    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = index
        assert keboola_streamlit._get_query_service_url() == "https://q.example.com"

    mock_get.assert_called_once_with(
        "https://example.com/v2/storage",
        params={"exclude": "components"},
        headers={"X-StorageApi-Token": "dummy_token"},
        timeout=10,
    )


def test_query_service_url_env_wins(keboola_streamlit, query_service_env):
    with patch("requests.get") as mock_get:
        assert keboola_streamlit._get_query_service_url() == "https://query.example.com"
    mock_get.assert_not_called()


def test_query_service_url_not_in_storage_index(keboola_streamlit, monkeypatch):
    monkeypatch.delenv("QUERY_SERVICE_URL", raising=False)

    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = {"services": []}
        with pytest.raises(RuntimeError, match="QUERY_SERVICE_URL"):
            keboola_streamlit._get_query_service_url()


def test_require_env_missing(monkeypatch):
    monkeypatch.delenv("QUERY_SERVICE_URL", raising=False)
    with pytest.raises(RuntimeError, match="QUERY_SERVICE_URL"):
        KeboolaStreamlit._require_env("QUERY_SERVICE_URL")


def test_read_workspace_id(keboola_streamlit, query_service_env):
    assert keboola_streamlit._read_workspace_id() == "12345"


def test_read_workspace_id_numeric(keboola_streamlit, query_service_env):
    query_service_env.write_text('{"workspaceId": 12345}')
    assert keboola_streamlit._read_workspace_id() == "12345"


def test_read_workspace_id_from_env_without_manifest(keboola_streamlit, monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    monkeypatch.setenv("WORKSPACE_ID", "999")
    assert keboola_streamlit._read_workspace_id() == "999"


def test_read_workspace_id_manifest_wins_over_env(keboola_streamlit, query_service_env, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ID", "999")
    assert keboola_streamlit._read_workspace_id() == "12345"


def test_read_workspace_id_without_manifest_and_env(keboola_streamlit, monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("WORKSPACE_ID", raising=False)
    with pytest.raises(RuntimeError, match="KBC_WORKSPACE_MANIFEST_PATH.*WORKSPACE_ID"):
        keboola_streamlit._read_workspace_id()


def test_read_workspace_id_rejects_non_numeric_env(keboola_streamlit, monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    monkeypatch.setenv("WORKSPACE_ID", "WORKSPACE_999")
    with pytest.raises(RuntimeError, match="numeric"):
        keboola_streamlit._read_workspace_id()


def test_read_workspace_id_missing_key(keboola_streamlit, query_service_env):
    query_service_env.write_text("{}")
    with pytest.raises(RuntimeError, match="workspaceId"):
        keboola_streamlit._read_workspace_id()


def test_read_workspace_id_missing_file(keboola_streamlit, monkeypatch, tmp_path):
    monkeypatch.setenv("KBC_WORKSPACE_MANIFEST_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        keboola_streamlit._read_workspace_id()


def test_get_branch_id(keboola_streamlit, query_service_env):
    assert keboola_streamlit._get_branch_id() == "678"


def test_get_branch_id_rejects_alias(keboola_streamlit, monkeypatch):
    monkeypatch.setenv("BRANCH_ID", "default")
    with pytest.raises(RuntimeError, match="numeric"):
        keboola_streamlit._get_branch_id()


@pytest.fixture
def query_service(keboola_streamlit, query_service_env):
    fake_module, fake_client_cls, fake_client = _mock_query_service_module(
        columns=(("ID", "NUMBER"), ("NAME", "VARCHAR")), data=(("1", "a"), ("2", "b"))
    )
    keboola_streamlit.create_event = MagicMock(return_value=(201, "{}"))
    with patch.dict(sys.modules, {"keboola_query_service": fake_module}), patch("streamlit.error") as mock_error:
        yield SimpleNamespace(app=keboola_streamlit, client=fake_client, client_cls=fake_client_cls, error=mock_error)


def test_execute_query_returns_typed_dataframe(query_service):
    query = 'SELECT "ID", "NAME" FROM "in.c-main"."users"'

    result = query_service.app.query_service_execute_query(query)

    assert list(result.columns) == ["ID", "NAME"]
    assert result["ID"].tolist() == [1, 2]
    assert str(result["ID"].dtype) == "Int64"
    assert result["NAME"].tolist() == ["a", "b"]
    query_service.client.submit_job.assert_called_once_with(branch_id="678", workspace_id="12345", statements=[query])
    query_service.client.wait_for_job.assert_called_once_with("qs-job-1", max_wait_time=300.0)
    query_service.client.get_job_results.assert_called_once_with("qs-job-1", "stmt-1", offset=0, page_size=5000)


def test_execute_query_convert_types_disabled(query_service):
    result = query_service.app.query_service_execute_query("SELECT 1", convert_types=False)

    assert result["ID"].tolist() == ["1", "2"]


def test_execute_query_writes_access_event_before_waiting(query_service):
    calls = []
    query_service.app.create_event.side_effect = lambda **kwargs: calls.append("event") or (201, "{}")
    query_service.client.wait_for_job.side_effect = lambda *args, **kwargs: calls.append("wait") or SimpleNamespace(
        statements=[SimpleNamespace(id="stmt-1")]
    )

    query_service.app.query_service_execute_query("SELECT 1")

    assert calls == ["event", "wait"]
    query_service.app.create_event.assert_called_once_with(
        message="Streamlit App Query Service Query",
        event_type="keboola_data_app_query_service_query",
        event_data="Query: SELECT 1",
        job_id="qs-job-1",
    )


def test_execute_query_cancels_job_when_access_event_fails(query_service):
    query_service.app.create_event.return_value = (None, None)

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    query_service.client.cancel_job.assert_called_once()
    assert query_service.client.cancel_job.call_args.args == ("qs-job-1",)
    query_service.client.wait_for_job.assert_not_called()
    query_service.client.get_job_results.assert_not_called()
    query_service.app.create_event.assert_called_once()
    assert "access event" in query_service.error.call_args[0][0]


def test_execute_query_without_return_df(query_service):
    result = query_service.app.query_service_execute_query("DELETE FROM t", return_df=False)

    assert result is None
    query_service.client.wait_for_job.assert_called_once()
    query_service.client.get_job_results.assert_not_called()


def test_execute_query_reads_all_pages(query_service):
    columns = [_column("N", "NUMBER")]
    query_service.client.get_job_results.side_effect = [
        _page(columns, [[str(i)] for i in range(5000)], number_of_rows=5002),
        _page(columns, [["5000"], ["5001"]], number_of_rows=5002),
    ]

    result = query_service.app.query_service_execute_query("SELECT N FROM big")

    assert len(result) == 5002
    assert result["N"].iloc[-1] == 5001
    assert query_service.client.get_job_results.call_args_list[1].kwargs == {"offset": 5000, "page_size": 5000}


def test_execute_query_stops_paging_at_number_of_rows(query_service):
    columns = [_column("N", "NUMBER")]
    query_service.client.get_job_results.side_effect = [_page(columns, [[str(i)] for i in range(5000)], 5000)]

    result = query_service.app.query_service_execute_query("SELECT N FROM exact")

    assert len(result) == 5000
    query_service.client.get_job_results.assert_called_once()


def test_execute_query_keeps_columns_of_first_page(query_service):
    columns = [_column("N", "NUMBER")]
    query_service.client.get_job_results.side_effect = [
        _page(columns, [[str(i)] for i in range(5000)]),
        _page([], []),
    ]

    result = query_service.app.query_service_execute_query("SELECT N FROM exactly_one_page")

    assert list(result.columns) == ["N"]
    assert len(result) == 5000


def test_execute_query_no_columns(query_service):
    query_service.client.get_job_results.return_value = _page([], [])

    result = query_service.app.query_service_execute_query("CREATE TABLE t (a INT)")

    assert result.empty


def test_execute_query_failure_writes_failed_event(query_service):
    query_service.client.wait_for_job.side_effect = FakeQueryServiceError("SQL compilation error")

    result = query_service.app.query_service_execute_query("SELEC 1")

    assert result is None
    assert "SQL compilation error" in query_service.error.call_args[0][0]
    assert query_service.app.create_event.call_args_list[-1].kwargs == {
        "message": "Streamlit App Query Service Query Failed",
        "event_type": "keboola_data_app_query_service_query_failed",
        "event_data": "Query: SELEC 1\nError: SQL compilation error",
        "job_id": "qs-job-1",
    }


def test_execute_query_submit_failure_writes_failed_event_without_job_id(query_service):
    query_service.client.submit_job.side_effect = FakeQueryServiceError("Forbidden", status_code=403)

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    query_service.client.submit_job.assert_called_once()
    query_service.app.create_event.assert_called_once_with(
        message="Streamlit App Query Service Query Failed",
        event_type="keboola_data_app_query_service_query_failed",
        event_data="Query: SELECT 1\nError: Forbidden",
        job_id=None,
    )


def test_execute_query_timeout_cancels_job(query_service):
    query_service.client.wait_for_job.side_effect = FakeJobTimeoutError("Job did not complete within 5s")

    result = query_service.app.query_service_execute_query("SELECT 1", max_wait_time=5)

    assert result is None
    query_service.client.wait_for_job.assert_called_once_with("qs-job-1", max_wait_time=5)
    query_service.client.cancel_job.assert_called_once()
    assert query_service.client.cancel_job.call_args.args == ("qs-job-1",)
    assert (
        query_service.app.create_event.call_args.kwargs["event_type"] == "keboola_data_app_query_service_query_failed"
    )


def test_execute_query_cancel_failure_keeps_original_error(query_service):
    query_service.client.wait_for_job.side_effect = FakeJobTimeoutError("Job did not complete within 5s")
    query_service.client.cancel_job.side_effect = FakeQueryServiceError("cancel failed")

    query_service.app.query_service_execute_query("SELECT 1")

    assert "did not complete" in query_service.error.call_args[0][0]


def test_execute_query_retries_workspace_credentials_error(query_service):
    query_service.client.submit_job.side_effect = [
        FakeQueryServiceError("Failed to get workspace credentials", status_code=403),
        "qs-job-1",
    ]

    with patch("time.sleep") as mock_sleep:
        result = query_service.app.query_service_execute_query("SELECT 1")

    assert not result.empty
    assert query_service.client.submit_job.call_count == 2
    mock_sleep.assert_called_once_with(1)


def test_execute_query_gives_up_after_three_credentials_errors(query_service):
    query_service.client.submit_job.side_effect = FakeQueryServiceError(
        "Failed to get workspace credentials", status_code=403
    )

    with patch("time.sleep"):
        result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    assert query_service.client.submit_job.call_count == 3


def test_execute_query_without_workspace_writes_no_event(query_service, monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("WORKSPACE_ID", raising=False)

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    assert "KBC_WORKSPACE_MANIFEST_PATH" in query_service.error.call_args[0][0]
    query_service.client_cls.assert_not_called()
    query_service.app.create_event.assert_not_called()


def test_execute_query_without_extra(keboola_streamlit, query_service_env):
    with patch.dict(sys.modules, {"keboola_query_service": None}):
        with pytest.raises(ImportError, match=r"keboola-streamlit\[query-service\]"):
            keboola_streamlit.query_service_execute_query("SELECT 1")


@pytest.mark.parametrize(
    "headers, expected_user",
    [
        ({"X-Kbc-User-Email": "jane@example.com", "Origin": "https://app-123.hub.keboola.com"}, "jane@example.com"),
        ({}, "Unknown"),
    ],
)
def test_execute_query_event_contains_user(keboola_streamlit, query_service_env, headers, expected_user):
    fake_module, _, _ = _mock_query_service_module()
    keboola_streamlit.set_dev_mockup_headers(headers)

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}), patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 201
        result = keboola_streamlit.query_service_execute_query("SELECT 1")

    assert not result.empty
    params = mock_post.call_args.kwargs["json"]["params"]
    assert params["user"] == expected_user
    assert params["event_type"] == "keboola_data_app_query_service_query"
    assert params["event_job_id"] == "qs-job-1"
    assert params["event_data"] == "Query: SELECT 1"


def test_execute_query_max_rows_within_one_page(query_service):
    columns = [_column("N", "NUMBER")]
    query_service.client.get_job_results.return_value = _page(columns, [[str(i)] for i in range(5)], 100)

    result = query_service.app.query_service_execute_query("SELECT N FROM t", max_rows=3)

    assert result["N"].tolist() == [0, 1, 2]
    query_service.client.get_job_results.assert_called_once_with("qs-job-1", "stmt-1", offset=0, page_size=3)


def test_execute_query_max_rows_across_pages(query_service):
    columns = [_column("N", "NUMBER")]
    query_service.client.get_job_results.side_effect = [
        _page(columns, [[str(i)] for i in range(5000)], 10000),
        _page(columns, [[str(i)] for i in range(5000, 7000)], 10000),
    ]

    result = query_service.app.query_service_execute_query("SELECT N FROM t", max_rows=7000)

    assert len(result) == 7000
    assert [call.kwargs for call in query_service.client.get_job_results.call_args_list] == [
        {"offset": 0, "page_size": 5000},
        {"offset": 5000, "page_size": 2000},
    ]


@pytest.mark.parametrize("max_rows", [0, -1, True, 1.5])
def test_execute_query_rejects_invalid_max_rows(query_service, max_rows):
    with pytest.raises(ValueError, match="max_rows"):
        query_service.app.query_service_execute_query("SELECT 1", max_rows=max_rows)

    query_service.client.submit_job.assert_not_called()
