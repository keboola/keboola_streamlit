import streamlit as st
import pandas as pd
import requests
import datetime
import os
import csv

from kbcstorage.client import Client
from requests.exceptions import HTTPError
from typing import Dict, List, Tuple, Optional
from streamlit.web.server.websocket_headers import _get_websocket_headers

class KeboolaStreamlit:
    def __init__(self, root_url: str, token: str, tmp_data_folder: str = 'tmp/'):
        """
        Initializes the KeboolaStreamlit class with the provided parameters.

        Args:
            root_url (str): The root URL for the Keboola Storage API.
            token (str): The API token for accessing Keboola Storage.
            tmp_data_folder (str): The temporary data folder for storing files locally.
        """
        self.__client = Client(root_url, token)
        self.__token = token
        self.__root_url = root_url.strip('/')
        self.dev_mockup_headers = None
        self.tmp_data_folder = tmp_data_folder

    def _get_headers(self) -> dict:
        """
        Retrieves the headers for the current request, including development mock headers if set.

        Returns:
            dict: The headers for the current request.
        """
        headers = st.context.headers
        return headers if 'X-Kbc-User-Email' in headers else (self.dev_mockup_headers or {})
    
    def _get_event_job_id(self, table_id: str, operation_name: str):
        job_list = self.__client.jobs.list()
        for job in job_list:
            if job.get('tableId') == table_id and job.get('operationName') == operation_name:
                return job.get('id')
        return None

    def set_dev_mockup_headers(self, headers: dict):
        """
        Sets the development mock headers for local development.

        Args:
            headers (dict): The mock headers to set.
        """
        self.dev_mockup_headers = headers

    def auth_check(self, required_role_id: str, debug=False):
        """
        Checks the user's authentication and authorization based on the headers.

        Args:
            required_role_id (str): The required role ID for authorization.
            debug (bool): Flag to show detailed debug information.

        Stops the Streamlit app if the user is not authorized.
        """
        headers = self._get_headers()
        
        if 'X-Kbc-User-Roles' in headers:
            if debug:
                with st.sidebar.expander('Show more'):
                    st.write(headers)

            user_roles = headers.get('X-Kbc-User-Roles', [])
            if required_role_id not in user_roles:
                st.error("You are not authorised to use this app.")
                st.stop()
        else:
            if debug:
                st.info('Not using proxy.')
            st.error("Authentication headers are missing. You are not authorized to use this app.")
            st.stop()

    def logout_button(self, sidebar=True, use_container_width=True):
        """
        Adds a logout button to the Streamlit app.
        """
        headers = self._get_headers()
        
        container = st.sidebar if sidebar else st
        if 'X-Kbc-User-Email' in headers:
            user_email = headers['X-Kbc-User-Email']
            container.write(f'Logged in as user: {user_email}')
            container.link_button('Logout', '/_proxy/sign_out', use_container_width=use_container_width)

    def create_event(self, message: str = 'Streamlit App Create Event', endpoint: str = None, 
                     event_data: Optional[str] = None, jobId: Optional[int] = None, 
                     event_type: str = 'keboola_data_app_create_event'):
        """
        Creates an event in Keboola Storage.

        Args:
            message (str): The message for the event.
            endpoint (str): The endpoint for the event.
            data (Optional[str]): The data associated with the event.
            jobId (Optional[int]): The job ID for the event.
            event_type (str): The type of the event. Defaults to 'keboola_data_app_create_event'.
        """
        headers = self._get_headers()
        url = f'{self.__root_url}/v2/storage/events'
        requestHeaders = {
            'Content-Type': 'application/json',
            'X-StorageApi-Token': self.__token
        }
        requestData = {
            'message': message,
            'component': 'keboola.data-apps',
            'params': {
                'user': headers.get('X-Kbc-User-Email', 'Unknown'),
                'time': datetime.datetime.now(datetime.timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                'endpoint': endpoint or url,
                'event_type': event_type,
                'event_application': headers.get('Origin', 'Unknown')
            }
        }
        
        if event_data is not None:
            requestData['params']['event_data'] = {'data': f'{event_data}'}

        if jobId is not None:
            requestData['params']['event_job_id'] = jobId

        response = requests.post(url, headers=requestHeaders, json=requestData)
        return response.status_code, response.text
        
    def read_table(self, table_id: str) -> pd.DataFrame:
        """
        Retrieves data from a Keboola Storage table and returns it as a Pandas DataFrame.

        Args:
            table_id (str): The ID of the table to retrieve data from.            
        Returns:
            pd.DataFrame: The table data as a Pandas DataFrame.
        """
        client = self.__client
        try:
            table_detail = client.tables.detail(table_id)
            table_name = table_detail['name']
            
            client.tables.export_to_file(table_id=table_id, path_name='')
            
            with open('./' + table_name, mode='rt', encoding='utf-8') as in_file:
                lazy_lines = (line.replace('\0', '') for line in in_file)
                reader = csv.reader(lazy_lines, lineterminator='\n')

            if os.path.exists(f'{table_name}.csv'):
                os.remove(f'{table_name}.csv')

            os.rename(table_name, f'{table_name}.csv')
            df = pd.read_csv(f'{table_name}.csv')

            event_job_id = self._get_event_job_id(table_id=table_id, operation_name='tableExport')
            self.create_event(
                message='Streamlit App Read Table', 
                endpoint='{}/v2/storage/tables/{}/export-async'.format(self.__root_url, table_id),
                jobId=event_job_id, 
                event_type='keboola_data_app_read_table'
            )
            return df
        except Exception as e:
            st.error(f'An error occurred while retrieving data: {str(e)}')
            return pd.DataFrame() 

    def write_table(self, table_id: str, df: pd.DataFrame, is_incremental: bool = False):
        """
        Load data into an existing table.

        Args:
            table_id (str): The ID of the table to load data into.
            df (pd.DataFrame): The DataFrame containing the data to be loaded.
            is_incremental (bool): Whether to load incrementally (do not truncate the table). Defaults to False.
        """
        client = self.__client
        csv_path = f'{table_id}.csv.gz'
        
        try:
            df.to_csv(csv_path, index=False, compression='gzip')
            
            client.tables.load(
                table_id=table_id, 
                file_path=csv_path, 
                is_incremental=is_incremental
            )
            event_job_id = self._get_event_job_id(table_id=table_id, operation_name='tableImport')
            
            self.create_event( 
                message='Streamlit App Write Table', 
                endpoint='{}/v2/storage/tables/{}/import-async'.format(self.__root_url, table_id),
                event_data=df,
                jobId=event_job_id,
                event_type='keboola_data_app_write_table'
            )
        except Exception as e:
            st.error(f'Data upload failed with: {str(e)}')
        finally:
            if os.path.exists(csv_path):
                os.remove(csv_path)

    def add_table_selection(self, sidebar=True) -> pd.DataFrame:
        """
        Adds a table selection form to the Streamlit app.
        
        Args:
            sidebar (bool): Flag to display the form in the sidebar. Defaults to True.
        Returns:
            pd.DataFrame: The selected table data as a Pandas DataFrame.
        """
        container = st.sidebar if sidebar else st
        self._add_connection_form(container)
        if 'kbc_storage_client' in st.session_state:
            self._add_bucket_form(container)
        if 'selected_bucket' in st.session_state and 'kbc_storage_client' in st.session_state:
            self._add_table_form(container)
        if 'selected_table_id' in st.session_state and 'kbc_storage_client' in st.session_state:
            selected_table_id = st.session_state['selected_table_id']
            if 'tables_data' not in st.session_state:
                st.session_state['tables_data'] = {}
            if selected_table_id not in st.session_state['tables_data']:
                st.session_state['tables_data'][selected_table_id] = self.read_table(table_id=selected_table_id)
            return st.session_state['tables_data'][selected_table_id]
        return pd.DataFrame()

    def _add_connection_form(self, container):
        
        if container.button('Connect to Storage', use_container_width=True):
            try:
                kbc_client = self.__client
                    
                if 'kbc_storage_client' in st.session_state:
                    st.session_state.pop('kbc_storage_client')
                if 'selected_table' in st.session_state:
                    st.session_state.pop('selected_table')
                if 'selected_table_id' in st.session_state:
                    st.session_state.pop('selected_table_id')
                if 'selected_bucket' in st.session_state:
                    st.session_state.pop('selected_bucket')
                if 'uploaded_file' in st.session_state:
                    st.session_state.pop('uploaded_file')

                if self._get_bucket_list(kbc_client):
                    st.session_state['kbc_storage_client'] = kbc_client
                    st.session_state['bucket_list'] = self._get_bucket_list(kbc_client)
            except Exception as e:
                st.error(f'Connection failed: {str(e)}')

    def _add_bucket_form(self, container):
        with container.form('Bucket Details'):
            buckets = self._get_buckets_from_bucket_list()
            if not buckets:
                st.warning("No buckets found in the storage.")
                st.form_submit_button('Select Bucket', disabled=True, use_container_width=True)
            else:
                bucket = st.selectbox('Bucket', buckets)
                if st.form_submit_button('Select Bucket', use_container_width=True):
                    st.session_state['selected_bucket'] = bucket

    def _add_table_form(self, container):
        with container.form('Table Details'):
            table_names, tables = self._get_tables(st.session_state['selected_bucket'])
            if not table_names:
                st.warning("No tables found in the selected bucket.")
                st.form_submit_button('Select table', disabled=True, use_container_width=True)
            else:
                st.session_state['selected_table'] = st.selectbox('Table', table_names)
                table_id = tables[st.session_state['selected_table']]['id']
                if st.form_submit_button('Select table', use_container_width=True):
                    st.session_state['selected_table_id'] = table_id

    def _get_bucket_list(self, kbc_storage_client) -> List[dict]:
        try:
            return kbc_storage_client.buckets.list()
        except HTTPError:
            st.error('Invalid Connection settings')

    def _get_buckets_from_bucket_list(self) -> List[str]:
        try:
            return [bucket['id'] for bucket in st.session_state['bucket_list']]
        except Exception:
            st.error('Could not list buckets')
            return []

    def _get_tables(self, bucket_id: str) -> Tuple[List[str], Dict[str, dict]]:
        try:
            tables = {table['name']: table for table in st.session_state['kbc_storage_client'].buckets.list_tables(bucket_id)}
            return list(tables.keys()), tables
        except Exception as e:
            st.error('Could not list tables')
            st.error(e)
            return [], {}