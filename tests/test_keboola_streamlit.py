import pytest
import pandas as pd
from unittest.mock import Mock, patch
from keboola_streamlit import KeboolaStreamlit

@pytest.fixture
def keboola_instance():
    return KeboolaStreamlit(root_url="https://connection.keboola.com", token="your-token")

def test_init(keboola_instance):
    assert keboola_instance._KeboolaStreamlit__root_url == "https://connection.keboola.com"
    assert keboola_instance._KeboolaStreamlit__token == "your-token"

def test_set_dev_mockup_headers(keboola_instance):
    mock_headers = {"X-Kbc-User-Email": "test@example.com"}
    keboola_instance.set_dev_mockup_headers(mock_headers)
    assert keboola_instance.dev_mockup_headers == mock_headers

@patch('streamlit.error')
@patch('streamlit.stop')
def test_auth_check_unauthorized(mock_stop, mock_error, keboola_instance):
    keboola_instance.set_dev_mockup_headers({"X-Kbc-User-Roles": ["other_role"]})
    keboola_instance.auth_check("required_role")
    mock_error.assert_called_once_with("You are not authorised to use this app.")
    mock_stop.assert_called_once()

@patch('streamlit.error')
@patch('streamlit.stop')
def test_auth_check_authorized(mock_stop, mock_error, keboola_instance):
    keboola_instance.set_dev_mockup_headers({"X-Kbc-User-Roles": ["required_role"]})
    keboola_instance.auth_check("required_role")
    mock_error.assert_not_called()
    mock_stop.assert_not_called()

@patch('requests.post')
def test_create_event(mock_post, keboola_instance):
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = "Event created"
    mock_post.return_value = mock_response

    status_code, response_text = keboola_instance.create_event("Test event")
    assert status_code == 200
    assert response_text == "Event created"

@patch('keboola_streamlit.keboola_streamlit.Client')
def test_read_table(mock_client, keboola_instance):
    mock_table_detail = {"name": "test_table"}
    mock_client.return_value.tables.detail.return_value = mock_table_detail

    with patch('pandas.read_csv') as mock_read_csv:
        mock_df = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
        mock_read_csv.return_value = mock_df

        result_df = keboola_instance.read_table("table_id")
        pd.testing.assert_frame_equal(result_df, mock_df)

@patch('keboola_streamlit.keboola_streamlit.Client')
def test_write_table(mock_client, keboola_instance):
    mock_df = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
    
    with patch('pandas.DataFrame.to_csv') as mock_to_csv:
        keboola_instance.write_table("table_id", mock_df)
        mock_to_csv.assert_called_once()
        mock_client.return_value.tables.load.assert_called_once()

@patch('streamlit.sidebar.button')
@patch('keboola_streamlit.keboola_streamlit.Client')
def test_add_table_selection(mock_client, mock_button, keboola_instance):
    mock_button.return_value = True
    mock_client.return_value.buckets.list.return_value = [{"id": "bucket1", "name": "Bucket 1"}]
    
    with patch.object(keboola_instance, '_get_bucket_list') as mock_get_bucket_list:
        mock_get_bucket_list.return_value = [("bucket1", "Bucket 1")]
        result_df = keboola_instance.add_table_selection()
        assert isinstance(result_df, pd.DataFrame)
        assert 'bucket_list' in result_df.columns
