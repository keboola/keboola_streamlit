import builtins
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# workaround to import module from parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit import KeboolaStreamlit  # noqa: E402


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
    return MagicMock(Client=fake_client_cls), fake_client_cls, fake_client


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


def test_require_env_missing(monkeypatch):
    monkeypatch.delenv("QUERY_SERVICE_URL", raising=False)
    with pytest.raises(RuntimeError, match="QUERY_SERVICE_URL"):
        KeboolaStreamlit._require_env("QUERY_SERVICE_URL")
