import sys
import os
import pytest
import pandas as pd

from unittest.mock import MagicMock, patch, mock_open

# src/keboola_streamlit/test_keboola_streamlit.py
# workaround to import module from parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from keboola_streamlit import KeboolaStreamlit  # noqa: E402


@pytest.fixture
def keboola_streamlit():
    return KeboolaStreamlit(root_url="https://example.com", token="dummy_token")


def test_get_event_job_id(keboola_streamlit):
    keboola_streamlit._KeboolaStreamlit__client.jobs.list = MagicMock(return_value=[
        {'tableId': 'table_1', 'operationName': 'operation_1', 'id': 123},
        {'tableId': 'table_2', 'operationName': 'operation_2', 'id': 456}
    ])
    job_id = keboola_streamlit._get_event_job_id('table_1', 'operation_1')
    assert job_id == 123


def test_set_dev_mockup_headers(keboola_streamlit):
    headers = {'X-Kbc-User-Email': 'test@example.com'}
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

    with patch("builtins.open", mock_open(read_data=mock_csv_content)), \
         patch("os.rename"), \
         patch("os.path.exists", return_value=True), \
         patch("os.remove"), \
         patch("pandas.read_csv", return_value=pd.DataFrame({"col1": [1, 2], "col2": [3, 4]})):

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
            event_type="keboola_data_app_read_table"
        )


def test_write_table(keboola_streamlit):
    keboola_streamlit._KeboolaStreamlit__client.tables.load = MagicMock()
    df = pd.DataFrame({'col1': [1, 2], 'col2': [3, 4]})
    with patch('pandas.DataFrame.to_csv'):
        with patch('os.remove'):
            keboola_streamlit.write_table('table_id', df)
            keboola_streamlit._KeboolaStreamlit__client.tables.load.assert_called_once()


def test_add_table_selection(keboola_streamlit):
    with patch('streamlit.sidebar') as mock_sidebar:
        mock_sidebar.button = MagicMock(return_value=True)
        mock_sidebar.form = MagicMock()
        mock_sidebar.selectbox = MagicMock(return_value='bucket_1')
        with patch('streamlit.session_state', {'kbc_storage_client': MagicMock(), 'bucket_list': [{'id': 'bucket_1'}]}):
            df = keboola_streamlit.add_table_selection()
            assert isinstance(df, pd.DataFrame)
