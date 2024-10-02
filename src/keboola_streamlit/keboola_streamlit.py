import streamlit as st
import pandas as pd
import requests
import datetime
import os
import csv
import logging

from kbcstorage.client import Client
from requests.exceptions import HTTPError
from typing import Dict, List, Tuple, Optional

# Configure logging
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

class KeboolaStreamlit:
    def __init__(self, root_url: str, token: str):
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

    def _get_headers(self) -> dict:
        """
        Retrieves the headers for the current request, including development mock headers if set.

        Returns:
            dict: The headers for the current request.
        """
        headers = st.context.headers
        return headers if 'X-Kbc-User-Email' in headers else (self.dev_mockup_headers or {})
    
    def _get_event_job_id(self, table_id: str, operation_name: str) -> Optional[int]:
        """
        Retrieves the job ID for a specific table and operation.

        Args:
            table_id (str): The ID of the table.
            operation_name (str): The name of the operation.

        Returns:
            Optional[int]: The job ID if found, otherwise None.
        """
        try:
            job_list = self.__client.jobs.list()
            for job in job_list:
                if job.get('tableId') == table_id and job.get('operationName') == operation_name:
                    return job.get('id')
            return None
        except Exception as e:
            logging.error(f"Failed to get event job ID for table {table_id} and operation {operation_name}: {e}")
            return None

    def set_dev_mockup_headers(self, headers: dict) -> None:
        """
        Sets the development mock headers for local development.

        Args:
            headers (dict): The mock headers to set.
        """
        self.dev_mockup_headers = headers

    def auth_check(self, required_role_id: str, debug: bool = False) -> None:
        """
        Checks the user's authorization based on the headers.

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

    def logout_button(self, sidebar: bool = True, use_container_width: bool = True) -> None:
        """
        Adds a logout button to the Streamlit app.

        Args:
            sidebar (bool): Flag to display the button in the sidebar. Defaults to True.
            use_container_width (bool): Flag to use the container width for the button. Defaults to True.
        """
        headers = self._get_headers()
        
        container = st.sidebar if sidebar else st
        if 'X-Kbc-User-Email' in headers:
            user_email = headers['X-Kbc-User-Email']
            container.write(f'Logged in as user: {user_email}')
            container.link_button('Logout', '/_proxy/sign_out', use_container_width=use_container_width)

    def create_event(self, message: str = 'Streamlit App Create Event', endpoint: Optional[str] = None, 
                     event_data: Optional[str] = None, jobId: Optional[int] = None, 
                     event_type: str = 'keboola_data_app_create_event') -> Tuple[Optional[int], Optional[str]]:
        """
        Creates an event in Keboola Storage.

        Args:
            message (str): The message for the event.
            endpoint (str): The endpoint for the event.
            event_data (Optional[str]): The data associated with the event.
            jobId (Optional[int]): The job ID for the event.
            event_type (str): The type of the event. Defaults to 'keboola_data_app_create_event'.

        Returns:
            Tuple[int, str]: The response status code and response text.
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

        try:
            response = requests.post(url, headers=requestHeaders, json=requestData)
            response.raise_for_status()
            return response.status_code, response.text
        except Exception as e:
            logging.error(f"An error occurred while creating event: {e}")
            st.error(f"An error occurred while creating event: {e}")
        return None, None
        
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
            logging.error(f"An error occurred while reading table {table_id}: {e}")
            st.error(f"An error occurred while reading table {table_id}: {e}")
        return pd.DataFrame() 

    def write_table(self, table_id: str, df: pd.DataFrame, is_incremental: bool = False) -> None:
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
            logging.error(f"An error occurred while writing to table {table_id}: {e}")
            st.error(f"An error occurred while writing to table {table_id}: {e}")
        finally:
            if os.path.exists(csv_path):
                os.remove(csv_path)

    def add_table_selection(self, sidebar: bool = True) -> pd.DataFrame:
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

    def _add_connection_form(self, container: st.delta_generator.DeltaGenerator) -> None:
        """
        Adds a connection form.

        Args:
            container: Determined by the sidebar argument in the add_table_selection function.
        """
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
                logging.error(f"An error occurred while connecting to storage: {e}")
                st.error(f"An error occurred while connecting to storage: {e}")

    def _add_bucket_form(self, container: st.delta_generator.DeltaGenerator) -> None:
        """
        Adds a bucket selection.

        Args:
            container: Determined by the sidebar argument in the add_table_selection function.
        """
        with container.form('Bucket Details'):
            buckets = self._get_buckets_from_bucket_list()
            if not buckets:
                st.warning("No buckets found in the storage.")
                st.form_submit_button('Select Bucket', disabled=True, use_container_width=True)
            else:
                bucket = st.selectbox('Bucket', buckets)
                if st.form_submit_button('Select Bucket', use_container_width=True):
                    st.session_state['selected_bucket'] = bucket

    def _add_table_form(self, container: st.delta_generator.DeltaGenerator) -> None:
        """
        Adds a table selection.

        Args:
            container: Determined by the sidebar argument in the add_table_selection function.
        """
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

    def _get_bucket_list(self, kbc_storage_client: Client) -> List[dict]:
        """
        Retrieves the list of buckets from Keboola Storage.

        Args:
            kbc_storage_client: The Keboola Storage client.

        Returns:
            List[dict]: The list of buckets.
        """
        try:
            return kbc_storage_client.buckets.list()
        except Exception as e:
            logging.error(f"An error occurred while retrieving bucket list: {e}")
            st.error(f"An error occurred while retrieving bucket list: {e}")

    def _get_buckets_from_bucket_list(self) -> List[str]:
        """
        Retrieves the list of bucket IDs from the session state.

        Returns:
            List[str]: The list of bucket IDs.
        """
        try:
            return [bucket['id'] for bucket in st.session_state['bucket_list']]
        except Exception as e:
            logging.error(f"Could not list buckets: {e}")
            st.error(f"Could not list buckets: {e}")
            return []

    def _get_tables(self, bucket_id: str) -> Tuple[List[str], Dict[str, dict]]:
        """
        Retrieves the list of tables from a specific bucket.

        Args:
            bucket_id (str): The ID of the bucket.

        Returns:
            Tuple[List[str], Dict[str, dict]]: A tuple containing the list of table names and a dictionary of table details.
        """
        try:
            tables = {table['name']: table for table in st.session_state['kbc_storage_client'].buckets.list_tables(bucket_id)}
            return list(tables.keys()), tables
        except Exception as e:
            logging.error(f"An error occurred while retrieving tables from bucket {bucket_id}: {e}")
            st.error(f"An error occurred while retrieving tables from bucket {bucket_id}: {e}")
            return [], {}