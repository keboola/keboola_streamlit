import sys
import os
import pytest
import pandas as pd

from unittest.mock import MagicMock, patch, PropertyMock
from streamlit.runtime.scriptrunner_utils.script_run_context import add_script_run_ctx

# src/keboola_streamlit/test_keboola_streamlit.py
# workaround to import module from parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from keboola_streamlit import KeboolaStreamlit  # noqa: E402


@pytest.fixture
def keboola_streamlit():
    return KeboolaStreamlit(root_url="https://example.com", token="dummy_token")


@pytest.fixture(autouse=True)
def initialize_streamlit_context():
    add_script_run_ctx()


def test_get_headers(keboola_streamlit):
    with patch('streamlit.context.headers', new_callable=PropertyMock) as mock_headers:
        mock_headers.return_value = {'X-Kbc-User-Email': 'test@example.com'}
        headers = keboola_streamlit._get_headers()
        assert headers == {'X-Kbc-User-Email': 'test@example.com'}


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


def test_auth_check_authorized(keboola_streamlit):
    with patch('streamlit.context.headers', {'X-Kbc-User-Roles': ['role_1']}):
        with patch('streamlit.stop') as mock_stop:
            keboola_streamlit.auth_check('role_1')
            mock_stop.assert_not_called()


def test_auth_check_unauthorized(keboola_streamlit):
    with patch('streamlit.context.headers', {'X-Kbc-User-Roles': ['role_2']}):
        with patch('streamlit.stop') as mock_stop:
            with patch('streamlit.error') as mock_error:
                keboola_streamlit.auth_check('role_1')
                mock_error.assert_called_once_with("You are not authorised to use this app.")
                mock_stop.assert_called_once()


def test_logout_button(keboola_streamlit):
    with patch('streamlit.context.headers', {'X-Kbc-User-Email': 'test@example.com'}):
        with patch('streamlit.sidebar') as mock_sidebar:
            mock_sidebar.write = MagicMock()
            mock_sidebar.link_button = MagicMock()
            keboola_streamlit.logout_button()
            mock_sidebar.write.assert_called_once_with('Logged in as user: test@example.com')
            mock_sidebar.link_button.assert_called_once_with('Logout', '/_proxy/sign_out', use_container_width=True)


def test_create_event(keboola_streamlit):
    with patch('streamlit.context.headers', {'X-Kbc-User-Email': 'test@example.com', 'Origin': 'localhost'}):
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.text = 'Success'
            status_code, response_text = keboola_streamlit.create_event()
            assert status_code == 200
            assert response_text == 'Success'


def test_read_table(keboola_streamlit):
    keboola_streamlit._KeboolaStreamlit__client.tables.detail = MagicMock(return_value={'name': 'test_table'})
    keboola_streamlit._KeboolaStreamlit__client.tables.export_to_file = MagicMock()
    with patch('builtins.open', MagicMock()):
        with patch('os.rename'):
            with patch('pandas.read_csv', return_value=pd.DataFrame({'col1': [1, 2], 'col2': [3, 4]})):
                df = keboola_streamlit.read_table('table_id')
                assert not df.empty
                assert list(df.columns) == ['col1', 'col2']


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
