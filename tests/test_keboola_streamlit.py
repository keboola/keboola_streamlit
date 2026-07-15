import builtins
import sys
import os
import pytest
import pandas as pd

from unittest.mock import MagicMock, patch, mock_open

# src/keboola_streamlit/test_keboola_streamlit.py
# workaround to import module from parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit import KeboolaStreamlit  # noqa: E402


@pytest.fixture
def keboola_streamlit():
    return KeboolaStreamlit(root_url="https://example.com", token="dummy_token")


def test_get_event_job_id(keboola_streamlit):
    keboola_streamlit._KeboolaStreamlit__client.jobs.list = MagicMock(
        return_value=[
            {"tableId": "table_1", "operationName": "operation_1", "id": 123},
            {"tableId": "table_2", "operationName": "operation_2", "id": 456},
        ]
    )
    job_id = keboola_streamlit._get_event_job_id("table_1", "operation_1")
    assert job_id == 123


def test_set_dev_mockup_headers(keboola_streamlit):
    headers = {"X-Kbc-User-Email": "test@example.com"}
    keboola_streamlit.set_dev_mockup_headers(headers)
    assert keboola_streamlit.dev_mockup_headers == headers


def test_read_table(keboola_streamlit):
    mock_client = MagicMock()
    mock_client.tables.detail.return_value = {"name": "test_table"}
    mock_client.tables.export_to_file = MagicMock()

    keboola_streamlit._KeboolaStreamlit__client = mock_client
    keboola_streamlit._get_event_job_id = MagicMock(return_value="mock_event_job_id")
    keboola_streamlit.create_event = MagicMock()

    mock_csv_content = "col1,col2\n1,3\n2,4\n"

    with (
        patch("builtins.open", mock_open(read_data=mock_csv_content)),
        patch("os.rename"),
        patch("os.path.exists", return_value=True),
        patch("os.remove"),
        patch("pandas.read_csv", return_value=pd.DataFrame({"col1": [1, 2], "col2": [3, 4]})),
    ):
        result = keboola_streamlit.read_table("mock_table_id")

        assert not result.empty
        assert list(result.columns) == ["col1", "col2"]
        assert result.iloc[0].tolist() == [1, 3]
        assert result.iloc[1].tolist() == [2, 4]

        mock_client.tables.detail.assert_called_once_with("mock_table_id")
        mock_client.tables.export_to_file.assert_called_once_with(table_id="mock_table_id", path_name="")
        keboola_streamlit.create_event.assert_called_once_with(
            message="Streamlit App Read Table",
            endpoint="https://example.com/v2/storage/tables/mock_table_id/export-async",
            job_id="mock_event_job_id",
            event_type="keboola_data_app_read_table",
        )


def test_write_table(keboola_streamlit):
    keboola_streamlit._KeboolaStreamlit__client.tables.load = MagicMock()
    df = pd.DataFrame({"col1": [1, 2], "col2": [3, 4]})
    with patch("pandas.DataFrame.to_csv"):
        with patch("os.remove"):
            keboola_streamlit.write_table("table_id", df)
            keboola_streamlit._KeboolaStreamlit__client.tables.load.assert_called_once()


def test_snowflake_create_session_object_without_extra(keboola_streamlit):
    with patch.dict(sys.modules, {"snowflake": None, "snowflake.snowpark": None}):
        with pytest.raises(ImportError, match=r"keboola-streamlit\[snowflake\]"):
            keboola_streamlit.snowflake_create_session_object()


def test_snowflake_create_session_object_missing_unrelated_dependency_is_not_masked(keboola_streamlit):
    # If snowflake IS installed but one of its own transitive dependencies is broken/missing
    # (e.g. a pyarrow ABI mismatch), the error shouldn't be rewritten into a misleading
    # "install the extra" message.
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "snowflake.snowpark":
            raise ModuleNotFoundError("No module named 'pyarrow'", name="pyarrow")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(ModuleNotFoundError, match="pyarrow"):
            keboola_streamlit.snowflake_create_session_object()


def _mock_snowpark_module():
    fake_session_instance = MagicMock(name="snowflake_session")
    fake_session_cls = MagicMock(name="Session")
    fake_session_cls.builder.configs.return_value.create.return_value = fake_session_instance
    fake_module = MagicMock(Session=fake_session_cls)
    return fake_module, fake_session_cls, fake_session_instance


def test_snowflake_create_session_object_with_password(keboola_streamlit):
    fake_module, fake_session_cls, fake_session_instance = _mock_snowpark_module()
    keboola_streamlit.create_event = MagicMock()

    with patch.dict(sys.modules, {"snowflake": MagicMock(), "snowflake.snowpark": fake_module}):
        with patch("streamlit.secrets") as mock_secrets:
            mock_secrets.to_dict.return_value = {
                "SNOWFLAKE_USER": "user",
                "SNOWFLAKE_ACCOUNT": "account",
                "SNOWFLAKE_ROLE": "role",
                "SNOWFLAKE_WAREHOUSE": "wh",
                "SNOWFLAKE_DATABASE": "db",
                "SNOWFLAKE_SCHEMA": "schema",
                "SNOWFLAKE_PASSWORD": "secret",
            }
            session = keboola_streamlit.snowflake_create_session_object()

    assert session is fake_session_instance
    connection_parameters = fake_session_cls.builder.configs.call_args[0][0]
    assert connection_parameters["password"] == "secret"
    assert "private_key" not in connection_parameters


def test_snowflake_create_session_object_with_private_key(keboola_streamlit):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    fake_module, fake_session_cls, fake_session_instance = _mock_snowpark_module()
    keboola_streamlit.create_event = MagicMock()

    with patch.dict(sys.modules, {"snowflake": MagicMock(), "snowflake.snowpark": fake_module}):
        with patch("streamlit.secrets") as mock_secrets:
            mock_secrets.to_dict.return_value = {
                "SNOWFLAKE_USER": "user",
                "SNOWFLAKE_ACCOUNT": "account",
                "SNOWFLAKE_ROLE": "role",
                "SNOWFLAKE_WAREHOUSE": "wh",
                "SNOWFLAKE_DATABASE": "db",
                "SNOWFLAKE_SCHEMA": "schema",
                "SNOWFLAKE_PRIVATE_KEY": private_key_pem,
            }
            session = keboola_streamlit.snowflake_create_session_object()

    assert session is fake_session_instance
    connection_parameters = fake_session_cls.builder.configs.call_args[0][0]
    assert isinstance(connection_parameters["private_key"], bytes)


def test_snowflake_read_table_without_session(keboola_streamlit):
    with patch("streamlit.error") as mock_error:
        result = keboola_streamlit.snowflake_read_table(None, "table_id")

    assert isinstance(result, pd.DataFrame)
    assert result.empty
    mock_error.assert_called_once()
    assert "No Snowflake session" in mock_error.call_args[0][0]


def test_snowflake_execute_query_without_session(keboola_streamlit):
    with patch("streamlit.error") as mock_error:
        result = keboola_streamlit.snowflake_execute_query(None, "SELECT 1")

    assert result is None
    mock_error.assert_called_once()
    assert "No Snowflake session" in mock_error.call_args[0][0]


def test_snowflake_write_table_without_session(keboola_streamlit):
    df = pd.DataFrame({"col1": [1]})
    with patch("streamlit.error") as mock_error:
        result = keboola_streamlit.snowflake_write_table(None, df, "table_id")

    assert result is None
    mock_error.assert_called_once()
    assert "No Snowflake session" in mock_error.call_args[0][0]


def test_add_table_selection(keboola_streamlit):
    with patch("streamlit.sidebar") as mock_sidebar:
        mock_sidebar.button = MagicMock(return_value=True)
        mock_sidebar.form = MagicMock()
        mock_sidebar.selectbox = MagicMock(return_value="bucket_1")
        with patch(
            "streamlit.session_state",
            {"kbc_storage_client": MagicMock(), "bucket_list": [{"id": "bucket_1"}]},
        ):
            df = keboola_streamlit.add_table_selection()
            assert isinstance(df, pd.DataFrame)
