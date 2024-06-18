import streamlit as st
import pandas as pd
import requests
import datetime
import os

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
        self.client = Client(root_url, token)
        self.token = token
        self.root_url = root_url
        self.dev_mockup_headers = None
        self.tmp_data_folder = tmp_data_folder

    def _get_headers(self) -> dict:
        """
        Retrieves the headers for the current request, including development mock headers if set.

        Returns:
            dict: The headers for the current request.
        """
        headers = _get_websocket_headers()
        return headers if 'X-Kbc-User-Email' in headers else (self.dev_mockup_headers or {})

    def _get_sapi_client(self):
        """
        Getter for the Keboola Storage API client.

        Returns:
            Client: The Keboola Storage API client.
        """
        return self.client

    def set_dev_mockup_headers(self, headers: dict):
        """
        Sets the development mock headers for local development.

        Args:
            headers (dict): The mock headers to set.
        """
        self.dev_mockup_headers = headers

    def auth_check(self, required_role_id: str):
        """
        Checks the user's authentication and authorization based on the headers.

        Args:
            required_role_id (str): The required role ID for authorization.

        Stops the Streamlit app if the user is not authorized.
        """
        headers = self._get_headers()
        
        if 'X-Kbc-User-Email' in headers:
            user_email = headers['X-Kbc-User-Email']
            st.sidebar.write(f'Logged in as user: {user_email}')
            st.sidebar.link_button('Logout', '/_proxy/sign_out', use_container_width=True)
            with st.sidebar.expander('Show more'):
                st.write(headers)

            user_roles = headers.get('X-Kbc-User-Roles', [])
            if required_role_id not in user_roles:
                st.error("You are not authorised to use this app.")
                st.stop()
        else:
            st.write('Not using proxy.')


    def create_event(self, message: str = 'Streamlit App Create Event', endpoint: str = '/v2/storage/events/create', data: Optional[str] = None, jobId: Optional[int] = None):
        """
        Creates an event in Keboola Storage.

        Args:
            jobId (int): The job ID for the event.
            message (str): The message for the event.
            data (str): The data associated with the event.
            endpoint (str): The endpoint for the event.
        """
        headers = self._get_headers()
        url = f'{self.root_url}/v2/storage/events'
        requestHeaders = {
            'Content-Type': 'application/json',
            'X-StorageApi-Token': self.token
        }
        requestData = {
            'message': message,
            'component': 'keboola.data-apps',
            'params': {
                'user': headers.get('X-Kbc-User-Email', 'Unknown'),
                'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'endpoint': endpoint,
                'event_type': 'keboola_data_app_write',
                'event_application': headers.get('Origin', 'Unknown')
            }
        }
        
        if data is not None:
            requestData['params']['event_data'] = {'data': f'{data}'}

        if jobId is not None:
            requestData['params']['event_job_id'] = jobId

        response = requests.post(url, headers=requestHeaders, json=requestData)
        return response.status_code, response.text
    
    def get_event_job_id(self, table_id: str, operation_name: str):
        client = self._get_sapi_client()
        job_list = client.jobs.list()
        job_id = ''
        for job in job_list:
            if job['tableId'] == table_id and job['operationName'] == operation_name:
                job_id = job['id']
                break
        return job_id
    
    def get_table(self, table_id: str, endpoint: str = '/v2/storage/tables/export_to_file') -> pd.DataFrame:
        """
        Retrieves data from a Keboola Storage table and returns it as a Pandas DataFrame.

        Args:
            table_id (str): The ID of the table to retrieve data from.
            endpoint (str): The endpoint for retrieving table details.

        Returns:
            pd.DataFrame: The table data as a Pandas DataFrame.
        """
        client = self._get_sapi_client()
        try:
            table_detail = client.tables.detail(table_id)
            table_name = table_detail['name']
            if not os.path.exists(self.tmp_data_folder):
                os.makedirs(self.tmp_data_folder)

            client.tables.export_to_file(table_id=table_id, path_name=self.tmp_data_folder)
            
            csv_path = os.path.join(self.tmp_data_folder, f'{table_name}.csv')
            if os.path.exists(csv_path):
                os.remove(csv_path)

            exported_file_path = os.path.join(self.tmp_data_folder, table_name)
            os.rename(exported_file_path, csv_path)
            df = pd.read_csv(csv_path)

            event_job_id = self.get_event_job_id(table_id=table_id, operation_name='tableExport')
            self.create_event(
                jobId=event_job_id, 
                message='Streamlit App Download Table', 
                endpoint=endpoint
            )
            return df
        except Exception as e:
            st.error(f'An error occurred while retrieving data: {str(e)}')
            return pd.DataFrame() 


    def load_table(self, table_id: str, df: pd.DataFrame, is_incremental: bool = False, endpoint: str = '/v2/storage/tables/load'):
        """
        Load data into an existing table.

        Args:
            table_id (str): The ID of the table to load data into.
            df (pd.DataFrame): The DataFrame containing the data to be loaded.
            is_incremental (bool): Whether to load incrementally (do not truncate the table). Defaults to False.
            endpoint (str): The endpoint for loading data.
        """
        client = self._get_sapi_client()
        csv_path = f'{table_id}.csv'
        try:
            df.to_csv(csv_path, index=False)
            client.tables.load(table_id=table_id, file_path=csv_path, is_incremental=is_incremental)
            event_job_id = self.get_event_job_id(table_id=table_id, operation_name='tableImport')
            self.create_event(
                jobId=event_job_id, 
                message='Streamlit App Load Table', 
                endpoint=endpoint,
                data=df
            )
        except Exception as e:
            st.error(f'Data upload failed with: {str(e)}')
        finally:
            if os.path.exists(csv_path):
                os.remove(csv_path)

    def add_table_selection(self):
        self._add_connection_form()
        if 'kbc_storage_client' in st.session_state:
            self._add_bucket_form()
        if 'selected_bucket' in st.session_state and 'kbc_storage_client' in st.session_state:
            self._add_table_form()
        if 'selected_table_id' in st.session_state and 'kbc_storage_client' in st.session_state:
            selected_table_id = st.session_state['selected_table_id']
            if 'tables_data' not in st.session_state:
                st.session_state['tables_data'] = {}
            if selected_table_id not in st.session_state['tables_data']:
                st.session_state['tables_data'][selected_table_id] = self.get_table(table_id=selected_table_id)
            return st.session_state['tables_data'][selected_table_id]
        return pd.DataFrame()
    

    def _add_connection_form(self):
        with st.sidebar.form('Connection Details'):
            if st.form_submit_button('Connect', use_container_width=True):
                try:
                    kbc_client = self._get_sapi_client()
                    
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
    
    def _add_bucket_form(self):
        with st.sidebar.form('Bucket Details'):
            with st.header('Select a bucket from storage'):
                bucket = st.selectbox('Bucket', self._get_buckets_from_bucket_list())
            if st.form_submit_button('Select Bucket',use_container_width=True):
                st.session_state['selected_bucket'] = bucket

    def _add_table_form(self):
        with st.sidebar.form('Table Details'):
            table_names, tables = self._get_tables(st.session_state['selected_bucket'])
            st.session_state['selected_table'] = st.selectbox('Table', table_names)
            table_id = tables[st.session_state['selected_table']]['id']
            if st.form_submit_button('Select table', use_container_width=True):
                st.session_state['selected_table_id'] = table_id 

    def _get_bucket_list(self, kbc_storage_client):
        try:
            project_bucket_list = kbc_storage_client.buckets.list()
            return project_bucket_list
        except HTTPError:
            st.error('Invalid Connection settings')

    def _get_buckets_from_bucket_list(self):
        try:
            return [bucket['id'] for bucket in st.session_state['bucket_list']]
        except Exception:
            st.error('Could not list buckets')

    def _get_tables(self, bucket_id: str) -> Tuple[List, Dict]:
        try:
            tables = {}
            for table in st.session_state['kbc_storage_client'].buckets.list_tables(bucket_id):
                tables[table['name']] = table
            table_names = list(tables.keys())
            return table_names, tables
        except Exception as e:
            st.error('Could not list tables')
            st.error(e)