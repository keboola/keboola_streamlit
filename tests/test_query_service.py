import builtins
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# workaround to import module from parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit import KeboolaStreamlit  # noqa: E402
from keboola_streamlit import keboola_streamlit as module  # noqa: E402


class FakeQueryServiceError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FakeJobTimeoutError(FakeQueryServiceError):
    def __init__(self, message, job_id=None):
        super().__init__(message)
        self.job_id = job_id


class FakeJobError(FakeQueryServiceError):
    pass


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _column(name, type_="TEXT"):
    return SimpleNamespace(name=name, type=type_)


def _page(columns, data, number_of_rows=None):
    return SimpleNamespace(columns=columns, data=data, number_of_rows=number_of_rows)


def _statement(rows_affected=None, status="completed", query_id="sf-query-1"):
    return SimpleNamespace(id="stmt-1", query_id=query_id, rows_affected=rows_affected, status=status)


def _job(status="completed", statement=None, cancellation_reason=None):
    statement = statement or _statement(status=status)
    return SimpleNamespace(status=status, statements=[statement], cancellation_reason=cancellation_reason)


def _mock_query_service_module(columns=(("A", "TEXT"),), data=(("1",),)):
    fake_client = MagicMock(name="query_service_client")
    fake_client.submit_job.return_value = "qs-job-1"
    fake_client.base_url = "https://query.example.com"
    fake_client.wait_for_job.return_value = _job()
    fake_client.get_job_status.return_value = _job("processing")
    fake_client.get_job_results.return_value = _page(
        [_column(name, type_) for name, type_ in columns], [list(row) for row in data]
    )
    fake_client_cls = MagicMock(name="Client", return_value=fake_client)
    fake_module = MagicMock(
        Client=fake_client_cls,
        QueryServiceError=FakeQueryServiceError,
        JobTimeoutError=FakeJobTimeoutError,
        JobError=FakeJobError,
    )
    return fake_module, fake_client_cls, fake_client


@pytest.fixture(autouse=True)
def clear_query_service_caches():
    module._QUERY_SERVICE_CLIENTS.clear()
    module._QUERY_SERVICE_URLS.clear()


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


def test_get_query_service_client_is_shared_across_instances(keboola_streamlit, query_service_env):
    fake_module, fake_client_cls, fake_client = _mock_query_service_module()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}):
        first = keboola_streamlit._get_query_service_client()
        second = KeboolaStreamlit(root_url="https://example.com", token="dummy_token")._get_query_service_client()

    assert first is fake_client
    assert second is fake_client
    fake_client_cls.assert_called_once_with(
        base_url="https://query.example.com", token="dummy_token", user_agent="keboola-streamlit"
    )


def test_get_query_service_client_without_retries(keboola_streamlit, query_service_env):
    fake_module, fake_client_cls, _ = _mock_query_service_module()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}):
        keboola_streamlit._get_query_service_client()
        keboola_streamlit._get_query_service_client(retry_requests=False)

    assert fake_client_cls.call_count == 2
    assert fake_client_cls.call_args.kwargs["max_retries"] == 0


def test_get_query_service_client_per_token(keboola_streamlit, query_service_env):
    fake_module, fake_client_cls, _ = _mock_query_service_module()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}):
        keboola_streamlit._get_query_service_client()
        KeboolaStreamlit(root_url="https://example.com", token="other_token")._get_query_service_client()

    assert fake_client_cls.call_count == 2


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


def test_read_workspace_id_rejects_non_numeric_manifest(keboola_streamlit, query_service_env):
    query_service_env.write_text('{"workspaceId": "ws-1"}')
    with pytest.raises(RuntimeError, match="non-numeric"):
        keboola_streamlit._read_workspace_id()


def test_get_branch_id_rejects_non_ascii_digits(keboola_streamlit, monkeypatch):
    monkeypatch.setenv("BRANCH_ID", "²")
    with pytest.raises(RuntimeError, match="numeric"):
        keboola_streamlit._get_branch_id()


def _event(message, event_type, job_id=None, backend_query_id=None, event_data=None):
    extra_params = {"workspace_id": "12345", "branch_id": "678"}
    if backend_query_id is not None:
        extra_params["backend_query_id"] = backend_query_id
    return {
        "message": message,
        "event_type": event_type,
        "endpoint": "https://query.example.com",
        "event_data": event_data,
        "job_id": job_id,
        "request_id": "request-1",
        "extra_params": extra_params,
        "show_error": False,
    }


ACCESS_EVENT = _event("Streamlit App Query Service Query", "keboola_data_app_query_service_query")
SUBMITTED_EVENT = _event(
    "Streamlit App Query Service Query Submitted", "keboola_data_app_query_service_query_submitted", job_id="qs-job-1"
)
COMPLETED_EVENT = _event(
    "Streamlit App Query Service Query Completed",
    "keboola_data_app_query_service_query_completed",
    job_id="qs-job-1",
    backend_query_id="sf-query-1",
)


def _failed_event(error, job_id="qs-job-1", backend_query_id="sf-query-1", outcome_unknown=False):
    event_data = f"Error: {error}"
    if outcome_unknown:
        event_data += f"\n{module.QUERY_SERVICE_OUTCOME_UNKNOWN}"
    return _event(
        "Streamlit App Query Service Query Failed",
        "keboola_data_app_query_service_query_failed",
        job_id=job_id,
        backend_query_id=backend_query_id,
        event_data=event_data,
    )


def _events(query_service):
    return [call.kwargs for call in query_service.app._create_event.call_args_list]


@pytest.fixture
def query_service(keboola_streamlit, query_service_env):
    fake_module, fake_client_cls, fake_client = _mock_query_service_module(
        columns=(("ID", "NUMBER"), ("NAME", "VARCHAR")), data=(("1", "a"), ("2", "b"))
    )
    keboola_streamlit._create_event = MagicMock(return_value=(201, "{}"))
    clock = FakeClock()
    with (
        patch.dict(sys.modules, {"keboola_query_service": fake_module}),
        patch("streamlit.error") as mock_error,
        patch("uuid.uuid4", return_value=SimpleNamespace(hex="request-1")),
        patch.object(module, "time", clock),
    ):
        yield SimpleNamespace(
            app=keboola_streamlit, client=fake_client, client_cls=fake_client_cls, error=mock_error, clock=clock
        )


def test_execute_query_returns_typed_dataframe(query_service):
    query = 'SELECT "ID", "NAME" FROM "in.c-main"."users"'

    result = query_service.app.query_service_execute_query(query)

    assert list(result.columns) == ["ID", "NAME"]
    assert result["ID"].tolist() == [1, 2]
    assert str(result["ID"].dtype) == "Int64"
    assert result["NAME"].tolist() == ["a", "b"]
    query_service.client.submit_job.assert_called_once_with(
        branch_id="678",
        workspace_id="12345",
        statements=[query],
        transactional=False,
        refresh_metadata_on_success=False,
    )
    query_service.client.wait_for_job.assert_called_once_with("qs-job-1", max_wait_time=300.0)
    query_service.client.get_job_results.assert_called_once_with("qs-job-1", "stmt-1", offset=0, page_size=5000)
    assert _events(query_service) == [ACCESS_EVENT, SUBMITTED_EVENT, COMPLETED_EVENT]


def test_execute_query_submits_without_sdk_retries(query_service):
    query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False)

    assert {"max_retries": 0} in [
        {key: value for key, value in call.kwargs.items() if key == "max_retries"}
        for call in query_service.client_cls.call_args_list
    ]


def test_execute_query_convert_types_disabled(query_service):
    result = query_service.app.query_service_execute_query("SELECT 1", convert_types=False)

    assert result["ID"].tolist() == ["1", "2"]


def test_execute_query_writes_access_event_before_submit(query_service):
    calls = []
    query_service.app._create_event.side_effect = lambda **kwargs: calls.append(kwargs["event_type"]) or (201, "")
    query_service.client.submit_job.side_effect = lambda **kwargs: calls.append("submit") or "qs-job-1"
    query_service.client.wait_for_job.side_effect = lambda *args, **kwargs: calls.append("wait") or _job()

    query_service.app.query_service_execute_query("SELECT 1")

    assert calls == [
        "keboola_data_app_query_service_query",
        "submit",
        "keboola_data_app_query_service_query_submitted",
        "wait",
        "keboola_data_app_query_service_query_completed",
    ]


def test_execute_query_sends_nothing_when_access_event_fails(query_service):
    query_service.app._create_event.return_value = (None, None)

    result = query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False)

    assert result is None
    query_service.client.submit_job.assert_not_called()
    query_service.app._create_event.assert_called_once()
    assert "access event" in query_service.error.call_args[0][0]


def test_execute_query_continues_when_submitted_event_fails(query_service):
    query_service.app._create_event.side_effect = [(201, ""), (None, None), (None, None)]

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result["NAME"].tolist() == ["a", "b"]
    query_service.client.cancel_job.assert_not_called()


@pytest.mark.parametrize("rows_affected", [3, 0])
def test_execute_query_without_return_df_returns_rows_affected(query_service, rows_affected):
    query_service.client.wait_for_job.return_value = _job(statement=_statement(rows_affected))

    result = query_service.app.query_service_execute_query("DELETE FROM t", return_df=False)

    assert result == rows_affected
    query_service.client.get_job_results.assert_not_called()


def test_execute_query_without_return_df_and_no_rows_affected(query_service):
    result = query_service.app.query_service_execute_query("CREATE TABLE t (a INT)", return_df=False)

    assert result == 0


def test_execute_query_failed_write_returns_none(query_service):
    query_service.client.wait_for_job.side_effect = FakeJobError("SQL compilation error")
    query_service.client.get_job_status.return_value = _job("failed")

    result = query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False)

    assert result is None


@pytest.mark.parametrize(
    "return_df, refresh_metadata, expected",
    [
        (True, None, False),
        (False, None, True),
        (True, True, True),
        (False, False, False),
    ],
)
def test_execute_query_refreshes_metadata_for_writes(query_service, return_df, refresh_metadata, expected):
    query_service.app.query_service_execute_query(
        "UPDATE t SET a = 1", return_df=return_df, refresh_metadata=refresh_metadata
    )

    assert query_service.client.submit_job.call_args.kwargs["refresh_metadata_on_success"] is expected


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


def test_execute_query_keeps_paging_after_a_short_page(query_service):
    columns = [_column("N", "NUMBER")]
    query_service.client.get_job_results.side_effect = [
        _page(columns, [[str(i)] for i in range(1000)], number_of_rows=1500),
        _page(columns, [[str(i)] for i in range(1000, 1500)], number_of_rows=1500),
    ]

    result = query_service.app.query_service_execute_query("SELECT N FROM capped")

    assert len(result) == 1500
    assert query_service.client.get_job_results.call_args_list[1].kwargs == {"offset": 1000, "page_size": 5000}


def test_execute_query_fails_when_rows_are_missing(query_service):
    columns = [_column("N", "NUMBER")]
    query_service.client.get_job_results.side_effect = [
        _page(columns, [["1"]], number_of_rows=3),
        _page(columns, [], number_of_rows=3),
    ]

    result = query_service.app.query_service_execute_query("SELECT N FROM t")

    assert result is None
    assert _events(query_service) == [
        ACCESS_EVENT,
        SUBMITTED_EVENT,
        COMPLETED_EVENT,
        _failed_event("Query Service returned 1 of 3 rows."),
    ]


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
    query_service.client.wait_for_job.side_effect = FakeJobError("SQL compilation error")
    query_service.client.get_job_status.return_value = _job("failed")

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    assert "SQL compilation error" in query_service.error.call_args[0][0]
    assert _events(query_service) == [ACCESS_EVENT, SUBMITTED_EVENT, _failed_event("SQL compilation error")]
    query_service.client.cancel_job.assert_not_called()


def test_execute_query_writes_failed_event_before_showing_error(query_service):
    calls = []
    query_service.client.wait_for_job.side_effect = FakeJobError("SQL compilation error")
    query_service.app._create_event.side_effect = lambda **kwargs: calls.append(kwargs["event_type"]) or (201, "")
    query_service.error.side_effect = lambda *args: calls.append("st.error")

    query_service.app.query_service_execute_query("SELECT 1")

    assert calls[-2:] == ["keboola_data_app_query_service_query_failed", "st.error"]


def test_execute_query_failure_without_backend_query_id(query_service):
    query_service.client.wait_for_job.side_effect = FakeJobError("SQL compilation error")
    query_service.client.get_job_status.side_effect = FakeQueryServiceError("Not found")

    query_service.app.query_service_execute_query("SELECT 1")

    assert _events(query_service)[-1] == _failed_event("SQL compilation error", backend_query_id=None)


def test_execute_query_submit_failure_writes_failed_event_without_job_id(query_service):
    query_service.client.submit_job.side_effect = FakeQueryServiceError("Forbidden", status_code=403)

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    query_service.client.submit_job.assert_called_once()
    assert _events(query_service) == [ACCESS_EVENT, _failed_event("Forbidden", job_id=None, backend_query_id=None)]
    query_service.client.get_job_status.assert_not_called()


@pytest.mark.parametrize("return_df", [True, False])
def test_execute_query_canceled_job_is_a_failure(query_service, return_df):
    query_service.client.wait_for_job.return_value = _job("canceled", cancellation_reason="Canceled by user")
    query_service.client.get_job_status.return_value = _job("canceled")

    result = query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=return_df)

    assert result is None
    query_service.client.get_job_results.assert_not_called()
    query_service.client.cancel_job.assert_not_called()
    events = _events(query_service)
    assert [event["event_type"] for event in events] == [
        "keboola_data_app_query_service_query",
        "keboola_data_app_query_service_query_submitted",
        "keboola_data_app_query_service_query_failed",
    ]
    assert events[-1]["event_data"] == "Error: Query Service job qs-job-1 ended as canceled: Canceled by user"


def test_execute_query_failed_statement_in_completed_job_is_a_failure(query_service):
    query_service.client.wait_for_job.return_value = _job("completed", statement=_statement(status="notExecuted"))

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    assert _events(query_service)[-1]["event_type"] == "keboola_data_app_query_service_query_failed"


def test_execute_query_timeout_cancels_job(query_service):
    query_service.client.wait_for_job.side_effect = FakeJobTimeoutError("Job did not complete within 5s")

    result = query_service.app.query_service_execute_query("SELECT 1", max_wait_time=5)

    assert result is None
    assert query_service.client.wait_for_job.call_args_list[0].kwargs == {"max_wait_time": 5}
    query_service.client.cancel_job.assert_called_once_with("qs-job-1", reason="No result within 5 seconds.")
    assert _events(query_service)[-1] == _failed_event("Job did not complete within 5s", outcome_unknown=True)


def test_execute_query_waits_for_the_cancel_to_take_effect(query_service):
    query_service.client.wait_for_job.side_effect = [
        FakeJobTimeoutError("Job did not complete within 5s"),
        _job("canceled"),
    ]
    query_service.client.get_job_status.side_effect = [_job("processing"), _job("canceled")]

    query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False, max_wait_time=5)

    assert query_service.client.wait_for_job.call_args_list[1].kwargs == {"max_wait_time": 30}
    assert _events(query_service)[-1] == _failed_event("Job did not complete within 5s")


def test_execute_query_does_not_log_a_failed_job_after_the_cancel_as_still_running(query_service):
    query_service.client.wait_for_job.side_effect = [
        FakeJobTimeoutError("Job did not complete within 5s"),
        FakeJobError("SQL compilation error"),
    ]
    query_service.client.get_job_status.side_effect = [_job("processing"), _job("failed")]

    with patch("logging.error") as mock_log_error:
        query_service.app.query_service_execute_query("SELECT 1", max_wait_time=5)

    assert not any("did not end" in call.args[0] for call in mock_log_error.call_args_list)
    assert _events(query_service)[-1] == _failed_event("Job did not complete within 5s")


def test_execute_query_logs_unknown_outcome_when_the_wait_after_the_cancel_fails(query_service):
    query_service.client.wait_for_job.side_effect = [
        FakeJobTimeoutError("Job did not complete within 5s"),
        FakeQueryServiceError("Service unavailable", status_code=503),
    ]
    query_service.client.get_job_status.side_effect = [_job("processing"), _job("processing")]

    with patch("logging.error") as mock_log_error:
        query_service.app.query_service_execute_query("SELECT 1", max_wait_time=5)

    messages = [call.args[0] for call in mock_log_error.call_args_list]
    assert any("Could not check whether Query Service job qs-job-1 ended" in m for m in messages)
    assert not any("did not end" in m for m in messages)
    assert _events(query_service)[-1] == _failed_event("Job did not complete within 5s", outcome_unknown=True)


def test_execute_query_job_completed_while_cancelling(query_service):
    query_service.client.wait_for_job.side_effect = [FakeJobTimeoutError("Job did not complete within 5s"), _job()]
    query_service.client.get_job_status.side_effect = [_job("processing"), _job(statement=_statement(rows_affected=1))]

    result = query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False, max_wait_time=5)

    assert result == 1
    assert _events(query_service) == [ACCESS_EVENT, SUBMITTED_EVENT, COMPLETED_EVENT]


def test_execute_query_keeps_polling_through_a_short_outage(query_service):
    query_service.client.wait_for_job.side_effect = [
        FakeQueryServiceError("Service unavailable", status_code=503),
        FakeQueryServiceError("Request failed after 4 attempts: timed out"),
        _job(statement=_statement(rows_affected=5)),
    ]

    result = query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False, max_wait_time=60)

    assert result == 5
    query_service.client.cancel_job.assert_not_called()
    assert [call.kwargs["max_wait_time"] for call in query_service.client.wait_for_job.call_args_list] == [60, 58, 56]


def test_execute_query_cancels_job_when_the_outage_outlasts_max_wait_time(query_service):
    query_service.client.wait_for_job.side_effect = FakeQueryServiceError("Service unavailable", status_code=503)

    result = query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False, max_wait_time=10)

    assert result is None
    assert query_service.clock.now < 10 + 30
    query_service.client.cancel_job.assert_called_once_with("qs-job-1", reason="No result within 10 seconds.")
    event_data = _events(query_service)[-1]["event_data"]
    assert event_data.startswith("Error: No result within 10 seconds")
    assert event_data.endswith(module.QUERY_SERVICE_OUTCOME_UNKNOWN)


@pytest.mark.parametrize("status_code", [403, 409])
def test_execute_query_client_errors_are_not_retried(query_service, status_code):
    query_service.client.wait_for_job.side_effect = FakeQueryServiceError("Not allowed", status_code=status_code)

    query_service.app.query_service_execute_query("SELECT 1")

    assert query_service.clock.sleeps == []
    query_service.client.cancel_job.assert_called_once()


def test_execute_query_job_finished_before_cancel(query_service):
    query_service.client.wait_for_job.side_effect = FakeJobTimeoutError("Job did not complete within 5s")
    query_service.client.get_job_status.side_effect = [_job("processing"), _job(statement=_statement(rows_affected=2))]

    result = query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False, max_wait_time=5)

    assert result == 2
    assert _events(query_service) == [ACCESS_EVENT, SUBMITTED_EVENT, COMPLETED_EVENT]


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

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert not result.empty
    assert query_service.client.submit_job.call_count == 2
    assert query_service.clock.sleeps == [1]
    assert _events(query_service) == [ACCESS_EVENT, SUBMITTED_EVENT, COMPLETED_EVENT]


def test_execute_query_gives_up_after_three_credentials_errors(query_service):
    query_service.client.submit_job.side_effect = FakeQueryServiceError(
        "Failed to get workspace credentials", status_code=403
    )

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    assert query_service.client.submit_job.call_count == 3


def test_execute_query_retries_rate_limited_submit(query_service):
    query_service.client.submit_job.side_effect = [
        FakeQueryServiceError("Too many requests", status_code=429),
        "qs-job-1",
    ]

    result = query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False)

    assert result == 0
    assert query_service.client.submit_job.call_count == 2


@pytest.mark.parametrize("status_code", [500, 502, None])
def test_execute_query_does_not_repeat_submit_that_may_have_run(query_service, status_code):
    query_service.client.submit_job.side_effect = FakeQueryServiceError("Bad gateway", status_code=status_code)

    result = query_service.app.query_service_execute_query("UPDATE t SET a = 1", return_df=False)

    assert result is None
    query_service.client.submit_job.assert_called_once()


def test_execute_query_job_without_statements_is_a_failure(query_service):
    query_service.client.wait_for_job.return_value = SimpleNamespace(
        status="completed", statements=[], cancellation_reason=None
    )
    query_service.client.get_job_status.return_value = SimpleNamespace(
        status="completed", statements=[], cancellation_reason=None
    )

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    assert _events(query_service)[-1]["event_type"] == "keboola_data_app_query_service_query_failed"


def test_execute_query_without_workspace_writes_no_event(query_service, monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("WORKSPACE_ID", raising=False)

    result = query_service.app.query_service_execute_query("SELECT 1")

    assert result is None
    assert "KBC_WORKSPACE_MANIFEST_PATH" in query_service.error.call_args[0][0]
    query_service.client_cls.assert_not_called()
    query_service.app._create_event.assert_not_called()


def test_execute_query_without_extra(keboola_streamlit, query_service_env):
    with patch.dict(sys.modules, {"keboola_query_service": None}):
        with pytest.raises(ImportError, match=r"keboola-streamlit\[query-service\]"):
            keboola_streamlit.query_service_execute_query("SELECT 1")


@pytest.mark.parametrize(
    "headers, expected_user",
    [
        (
            {
                "X-Kbc-User-Email": "jane@example.com",
                "X-Kbc-User-Id": "oidc-subject-1",
                "Origin": "https://app-123.hub.keboola.com",
            },
            "jane@example.com",
        ),
        ({}, "Unknown"),
    ],
)
def test_execute_query_events_contain_user(keboola_streamlit, query_service_env, headers, expected_user):
    fake_module, _, _ = _mock_query_service_module()
    keboola_streamlit.set_dev_mockup_headers(headers)

    with (
        patch.dict(sys.modules, {"keboola_query_service": fake_module}),
        patch("requests.post") as mock_post,
        patch("uuid.uuid4", return_value=SimpleNamespace(hex="request-1")),
    ):
        mock_post.return_value.status_code = 201
        result = keboola_streamlit.query_service_execute_query("SELECT 1")

    assert not result.empty
    for call in mock_post.call_args_list:
        assert call.kwargs["timeout"] == 30
    access, submitted, completed = [call.kwargs["json"]["params"] for call in mock_post.call_args_list]
    for params in (access, submitted, completed):
        assert params["user"] == expected_user
        assert params.get("user_id") == headers.get("X-Kbc-User-Id")
        assert params["request_id"] == "request-1"
        assert params["endpoint"] == "https://query.example.com"
        assert params["workspace_id"] == "12345"
        assert params["branch_id"] == "678"
    assert access["event_type"] == "keboola_data_app_query_service_query"
    assert "event_data" not in access
    assert "event_job_id" not in access
    assert submitted["event_type"] == "keboola_data_app_query_service_query_submitted"
    assert submitted["event_job_id"] == "qs-job-1"
    assert completed["event_type"] == "keboola_data_app_query_service_query_completed"
    assert completed["backend_query_id"] == "sf-query-1"


def test_execute_query_event_failure_does_not_call_streamlit(keboola_streamlit, query_service_env):
    fake_module, _, fake_client = _mock_query_service_module()

    with (
        patch.dict(sys.modules, {"keboola_query_service": fake_module}),
        patch("requests.post") as mock_post,
        patch("streamlit.error") as mock_error,
    ):
        mock_post.return_value.status_code = 201
        mock_post.side_effect = [mock_post.return_value, Exception("Storage down"), mock_post.return_value]
        result = keboola_streamlit.query_service_execute_query("SELECT 1")

    assert not result.empty
    fake_client.wait_for_job.assert_called_once()
    mock_error.assert_not_called()


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


@pytest.mark.parametrize("max_wait_time", [None, 0, -5, True, "60", float("nan"), float("inf")])
def test_execute_query_rejects_invalid_max_wait_time(query_service, max_wait_time):
    with pytest.raises(ValueError, match="max_wait_time"):
        query_service.app.query_service_execute_query("SELECT 1", max_wait_time=max_wait_time)

    query_service.client.submit_job.assert_not_called()
