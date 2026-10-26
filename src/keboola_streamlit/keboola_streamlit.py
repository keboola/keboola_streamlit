from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd
import requests
import streamlit as st
from kbcstorage.client import Client

from ._query_service_types import QueryServiceColumn, query_result_to_dataframe

if TYPE_CHECKING:
    from keboola_query_service import Client as QueryServiceClient
    from keboola_query_service import JobStatus
    from snowflake.snowpark import Session


EVENT_TIMEOUT_SECONDS = 30
QUERY_SERVICE_PAGE_SIZE = 5000
QUERY_SERVICE_SUBMIT_ATTEMPTS = 3
QUERY_SERVICE_FINAL_STATES = frozenset({"completed", "failed", "canceled"})
QUERY_SERVICE_POLL_RETRY_SECONDS = 2
QUERY_SERVICE_CANCEL_WAIT_SECONDS = 30
QUERY_SERVICE_OUTCOME_UNKNOWN = "The job was cancelled, but it had not ended yet, so the query may still complete."

_QUERY_SERVICE_CLIENTS: Dict[Tuple[str, str, bool], QueryServiceClient] = {}
_QUERY_SERVICE_URLS: Dict[str, str] = {}
_QUERY_SERVICE_LOCK = threading.Lock()

# Configure logging
logging.basicConfig(level=logging.ERROR, format="%(asctime)s - %(levelname)s - %(message)s")


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
        self.__root_url = root_url.strip("/")
        self.dev_mockup_headers = None
        self.aggrid_license_key = os.environ.get("AGGRID_LICENSE_KEY")

    def _get_headers(self) -> dict:
        """
        Retrieves the headers for the current request, including development mock headers if set.

        Returns:
            dict: The headers for the current request.
        """
        if self.dev_mockup_headers is not None:
            return self.dev_mockup_headers
        else:
            return st.context.headers

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
                if job.get("tableId") == table_id and job.get("operationName") == operation_name:
                    return job.get("id")
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

        if "X-Kbc-User-Roles" in headers:
            if debug:
                with st.sidebar.expander("Show more"):
                    st.write(headers)

            user_roles = headers.get("X-Kbc-User-Roles", [])
            if required_role_id not in user_roles:
                st.error("You are not authorised to use this app.")
                st.stop()
        else:
            if debug:
                st.info("Not using proxy.")
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
        if "X-Kbc-User-Email" in headers:
            user_email = headers["X-Kbc-User-Email"]
            container.write(f"Logged in as user: {user_email}")
            container.link_button("Logout", "/_proxy/sign_out", use_container_width=use_container_width)

    def create_event(
        self,
        message: str = "Streamlit App Create Event",
        endpoint: Optional[str] = None,
        event_data: Optional[str] = None,
        job_id: Optional[Union[int, str]] = None,
        event_type: str = "keboola_data_app_create_event",
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Creates an event in Keboola Storage.

        Args:
            message (str): The message for the event.
            endpoint (str): The endpoint for the event.
            event_data (Optional[str]): The data associated with the event.
            job_id (Optional[Union[int, str]]): The Storage or Query Service job ID for the event.
            event_type (str): The type of the event. Defaults to 'keboola_data_app_create_event'.

        Returns:
            Tuple[int, str]: The response status code and response text.
        """
        return self._create_event(
            message=message, endpoint=endpoint, event_data=event_data, job_id=job_id, event_type=event_type
        )

    def _create_event(
        self,
        message: str,
        event_type: str,
        endpoint: Optional[str] = None,
        event_data: Optional[str] = None,
        job_id: Optional[Union[int, str]] = None,
        request_id: Optional[str] = None,
        extra_params: Optional[Dict[str, str]] = None,
        show_error: bool = True,
    ) -> Tuple[Optional[int], Optional[str]]:
        headers = self._get_headers()
        url = f"{self.__root_url}/v2/storage/events"
        requestHeaders = {
            "Content-Type": "application/json",
            "X-StorageApi-Token": self.__token,
        }
        event_application = headers.get("Origin", "Unknown")
        requestData = {
            "message": message,
            "component": "keboola.data-apps",
            "params": {
                "user": headers.get("X-Kbc-User-Email", "Unknown"),
                "endpoint": endpoint or url,
                "event_type": event_type,
                "event_application": event_application,
            },
        }

        # The apps proxy sends the identity provider's user ID (OIDC subject, GitHub login), which stays the same
        # when the email changes. Password and no-auth apps have none.
        user_id = headers.get("X-Kbc-User-Id")
        if user_id:
            requestData["params"]["user_id"] = user_id

        if event_application != "Unknown":
            match = re.search(r"https://.*-(\d+)\.", event_application)
            if match:
                requestData["params"]["application_id"] = match.group(1)

        if event_data is not None:
            requestData["params"]["event_data"] = f"{event_data}"
        if job_id is not None:
            requestData["params"]["event_job_id"] = job_id
        if request_id is not None:
            requestData["params"]["request_id"] = request_id
        if extra_params:
            requestData["params"].update(extra_params)

        try:
            response = requests.post(url, headers=requestHeaders, json=requestData, timeout=EVENT_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.status_code, response.text
        except Exception as e:
            logging.error(f"An error occurred while creating event: {e}")
            if show_error:
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
            table_name = table_detail["name"]

            client.tables.export_to_file(table_id=table_id, path_name="")

            with open("./" + table_name, mode="rt", encoding="utf-8") as in_file:
                lazy_lines = (line.replace("\0", "") for line in in_file)
                reader = csv.reader(lazy_lines, lineterminator="\n")
                data = list(reader)
                df = pd.DataFrame(data[1:], columns=data[0])

            if os.path.exists(f"{table_name}.csv"):
                os.remove(f"{table_name}.csv")

            os.rename(table_name, f"{table_name}.csv")
            df = pd.read_csv(f"{table_name}.csv")

            event_job_id = self._get_event_job_id(table_id=table_id, operation_name="tableExport")
            self.create_event(
                message="Streamlit App Read Table",
                endpoint="{}/v2/storage/tables/{}/export-async".format(self.__root_url, table_id),
                job_id=event_job_id,
                event_type="keboola_data_app_read_table",
            )
            return df
        except Exception as e:
            logging.error(f"An error occurred while reading table {table_id}: {e}")
            st.error(f"An error occurred while reading table {table_id}: {e}")
        return pd.DataFrame()

    def write_table(
        self, table_id: str, df: pd.DataFrame, is_incremental: bool = False, raise_on_error: bool = False
    ) -> Optional[str]:
        """
        Load data into an existing table.

        Args:
            table_id (str): The ID of the table to load data into.
            df (pd.DataFrame): The DataFrame containing the data to be loaded.
            is_incremental (bool): Whether to load incrementally (do not truncate the table). Defaults to False.
            raise_on_error (bool): If True, raises the exception instead of returning it. Defaults to False.

        Returns:
            Optional[str]: None if successful, error message string if failed (when raise_on_error=False).
        """
        client = self.__client

        table_name = table_id.split(".")[-1]
        unique_id = uuid.uuid4()
        csv_path = f"{table_name}_{unique_id}.csv.gz"

        try:
            df.to_csv(csv_path, index=False, compression="gzip")

            client.tables.load(table_id=table_id, file_path=csv_path, is_incremental=is_incremental)
            event_job_id = self._get_event_job_id(table_id=table_id, operation_name="tableImport")

            self.create_event(
                message="Streamlit App Write Table",
                endpoint="{}/v2/storage/tables/{}/import-async".format(self.__root_url, table_id),
                event_data=df,
                job_id=event_job_id,
                event_type="keboola_data_app_write_table",
            )
            return None
        except Exception as e:
            logging.error(f"An error occurred while writing to table {table_id}: {e}")
            if raise_on_error:
                raise
            return repr(e)
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
        if "kbc_storage_client" in st.session_state:
            self._add_bucket_form(container)
        if "selected_bucket" in st.session_state and "kbc_storage_client" in st.session_state:
            self._add_table_form(container)
        if "selected_table_id" in st.session_state and "kbc_storage_client" in st.session_state:
            selected_table_id = st.session_state["selected_table_id"]
            if "tables_data" not in st.session_state:
                st.session_state["tables_data"] = {}
            if selected_table_id not in st.session_state["tables_data"]:
                st.session_state["tables_data"][selected_table_id] = self.read_table(table_id=selected_table_id)
            return st.session_state["tables_data"][selected_table_id]
        return pd.DataFrame()

    def _add_connection_form(self, container: st.delta_generator.DeltaGenerator) -> None:
        """
        Adds a connection form.

        Args:
            container: Determined by the sidebar argument in the add_table_selection function.
        """
        if container.button("Connect to Storage", use_container_width=True):
            try:
                kbc_client = self.__client

                if "kbc_storage_client" in st.session_state:
                    st.session_state.pop("kbc_storage_client")
                if "selected_table" in st.session_state:
                    st.session_state.pop("selected_table")
                if "selected_table_id" in st.session_state:
                    st.session_state.pop("selected_table_id")
                if "selected_bucket" in st.session_state:
                    st.session_state.pop("selected_bucket")
                if "uploaded_file" in st.session_state:
                    st.session_state.pop("uploaded_file")

                if self._get_bucket_list(kbc_client):
                    st.session_state["kbc_storage_client"] = kbc_client
                    st.session_state["bucket_list"] = self._get_bucket_list(kbc_client)
            except Exception as e:
                logging.error(f"An error occurred while connecting to storage: {e}")
                st.error(f"An error occurred while connecting to storage: {e}")

    def _add_bucket_form(self, container: st.delta_generator.DeltaGenerator) -> None:
        """
        Adds a bucket selection.

        Args:
            container: Determined by the sidebar argument in the add_table_selection function.
        """
        with container.form("Bucket Details"):
            buckets = self._get_buckets_from_bucket_list()
            if not buckets:
                st.warning("No buckets found in the storage.")
                st.form_submit_button("Select Bucket", disabled=True, use_container_width=True)
            else:
                bucket = st.selectbox("Bucket", buckets)
                if st.form_submit_button("Select Bucket", use_container_width=True):
                    st.session_state["selected_bucket"] = bucket

    def _add_table_form(self, container: st.delta_generator.DeltaGenerator) -> None:
        """
        Adds a table selection.

        Args:
            container: Determined by the sidebar argument in the add_table_selection function.
        """
        with container.form("Table Details"):
            table_names, tables = self._get_tables(st.session_state["selected_bucket"])
            if not table_names:
                st.warning("No tables found in the selected bucket.")
                st.form_submit_button("Select table", disabled=True, use_container_width=True)
            else:
                st.session_state["selected_table"] = st.selectbox("Table", table_names)
                table_id = tables[st.session_state["selected_table"]]["id"]
                if st.form_submit_button("Select table", use_container_width=True):
                    st.session_state["selected_table_id"] = table_id

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
            return [bucket["id"] for bucket in st.session_state["bucket_list"]]

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
            Tuple[List[str], Dict[str, dict]]:
            A tuple containing the list of table names and a dictionary of table details.
        """
        try:
            tables = {
                table["name"]: table for table in st.session_state["kbc_storage_client"].buckets.list_tables(bucket_id)
            }
            return list(tables.keys()), tables
        except Exception as e:
            logging.error(f"An error occurred while retrieving tables from bucket {bucket_id}: {e}")
            st.error(f"An error occurred while retrieving tables from bucket {bucket_id}: {e}")
            return [], {}

    def _get_connection_parameters(self) -> Tuple[dict, str]:
        """
        Returns the connection parameters from secrets.
        """
        scrts = st.secrets.to_dict()
        connection_parameters = {
            "user": scrts.get("SNOWFLAKE_USER", ""),
            "account": scrts.get("SNOWFLAKE_ACCOUNT", ""),
            "role": scrts.get("SNOWFLAKE_ROLE", ""),
            "warehouse": scrts.get("SNOWFLAKE_WAREHOUSE", ""),
            "database": scrts.get("SNOWFLAKE_DATABASE", ""),
            "schema": scrts.get("SNOWFLAKE_SCHEMA", ""),
        }

        private_key = scrts.get("SNOWFLAKE_PRIVATE_KEY")
        password = scrts.get("SNOWFLAKE_PASSWORD")

        if private_key:
            connection_parameters["private_key"] = private_key
            connection_parameters["private_key_passphrase"] = scrts.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or None

            if password:
                mess = "Both SNOWFLAKE_PRIVATE_KEY and SNOWFLAKE_PASSWORD are set. Using SNOWFLAKE_PRIVATE_KEY."
                logging.warning(mess)
                st.warning(mess)

            return connection_parameters

        if password:
            connection_parameters["password"] = password
            return connection_parameters

        raise KeyError("Neither SNOWFLAKE_PRIVATE_KEY nor SNOWFLAKE_PASSWORD is set in secrets")

    def snowflake_create_session_object(self) -> Optional[Session]:
        """
        Creates a Snowflake session.

        Returns:
            Optional[Session]: The Snowflake session, or None if session creation failed
            (the error is logged and shown via st.error).
        """
        try:
            from snowflake.snowpark import Session
            from cryptography.hazmat.primitives import serialization
        except ModuleNotFoundError as e:
            missing = e.name or ""
            missing_root = missing.split(".", 1)[0]
            if missing_root in ("snowflake", "cryptography"):
                raise ImportError(
                    "Snowflake support requires an optional dependency. "
                    "Install it with: pip install keboola-streamlit[snowflake]"
                ) from e
            raise

        session = None
        try:
            connection_parameters = self._get_connection_parameters()
            if "private_key" in connection_parameters:
                private_key_pem = connection_parameters["private_key"].encode("utf-8")
                pkp = connection_parameters.get("private_key_passphrase")
                passphrase = pkp.encode("utf-8") if pkp else None
                unlocked_private_key = serialization.load_pem_private_key(data=private_key_pem, password=passphrase)
                private_key = unlocked_private_key.private_bytes(
                    encoding=serialization.Encoding.DER,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
                connection_parameters["private_key"] = private_key

            session = Session.builder.configs(connection_parameters).create()
            self.create_event(
                message="Streamlit App Snowflake Create Session Object",
                event_type="keboola_data_app_snowflake_create_session_object",
            )
        except Exception as e:
            logging.error(f"An error occurred while creating Snowflake session: {e}")
            st.error(f"An error occurred while creating Snowflake session: {e}")
        return session

    def _log_missing_snowflake_session(self) -> None:
        message = (
            "No Snowflake session provided. snowflake_create_session_object() likely failed "
            "and returned None - check the error logged from that call."
        )
        logging.error(message)
        st.error(message)

    def snowflake_read_table(self, session: Optional[Session], table_id: str) -> pd.DataFrame:
        """
        Loads a table from Snowflake Workspace.

        Args:
            session: The Snowflake connection object.
            table_id (str): The ID of the table to load.

        Returns:
            pd.DataFrame: The table data as a Pandas DataFrame.
        """
        if session is None:
            self._log_missing_snowflake_session()
            return pd.DataFrame()
        try:
            df_snowflake = session.table(table_id).to_pandas()
            self.create_event(
                message="Streamlit App Snowflake Read Table",
                event_type="keboola_data_app_snowflake_read_table",
                event_data=f"table_id: {table_id}",
            )
            return df_snowflake
        except Exception as e:
            logging.error(f"An error occurred while loading Snowflake table {table_id}: {e}")
            st.error(f"An error occurred while loading Snowflake table {table_id}: {e}")
            return pd.DataFrame()

    def snowflake_execute_query(
        self,
        session: Optional[Session],
        query: str,
        params: Optional[Sequence[Any]] = None,
        return_df: bool = True,
    ) -> Optional[pd.DataFrame]:
        """
        Executes a SQL query on Snowflake Workspace.

        Args:
            session: The Snowflake connection object.
            query (str): The SQL query to execute.
            params (Optional[Sequence[Any]]): The parameters to bind to the query. Defaults to None.
            return_df (bool): Flag to indicate whether to return the result as a Pandas DataFrame. Defaults to True.

        Returns:
            Optional[pd.DataFrame]: The query results as a Pandas DataFrame if return_df is True, otherwise None.
        """
        if session is None:
            self._log_missing_snowflake_session()
            return None
        try:
            if return_df:
                snowflake_df = session.sql(query, params=params).to_pandas()
                self.create_event(
                    message="Streamlit App Snowflake Query",
                    event_type="keboola_data_app_snowflake_query",
                    event_data=f"Query: {query}",
                )
                return snowflake_df
            session.sql(query, params=params).collect()  # Execute the query without returning a DataFrame
            self.create_event(
                message="Streamlit App Snowflake Query Executed",
                event_type="keboola_data_app_snowflake_query",
                event_data=f"Query executed: {query}",
            )
        except Exception as e:
            logging.error(f"An error occurred while executing Snowflake query: {e}")
            st.error(f"An error occurred while executing Snowflake query: {e}")
        return None

    def snowflake_write_table(
        self,
        session: Optional[Session],
        df: pd.DataFrame,
        table_id: str,
        auto_create_table: bool = False,
        overwrite: bool = False,
    ) -> None:
        """
        Writes a DataFrame to a specified table in the Snowflake Workspace.

        Args:
            session (Session): The Snowflake connection object used for the operation.
            df (pd.DataFrame): The DataFrame containing data to be written to the table.
            table_id (str): The identifier of the table where data will be written.
            auto_create_table (bool): If True, automatically creates the table if it does not exist. Defaults to False.
            overwrite (bool): If True, overwrites the table if it already exists. Defaults to False.

        Returns:
            None
        """
        if session is None:
            self._log_missing_snowflake_session()
            return None
        try:
            session.write_pandas(df, table_id, auto_create_table=auto_create_table, overwrite=overwrite)
            self.create_event(
                message="Streamlit App Snowflake Write Table",
                event_type="keboola_data_app_snowflake_write_table",
                event_data=f"table_id: {table_id}",
            )
        except Exception as e:
            logging.error(f"An error occurred while writing to Snowflake table {table_id}: {e}")
            st.error(f"An error occurred while writing to Snowflake table {table_id}: {e}")

    @staticmethod
    def _require_env(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(
                f"Environment variable {name} is not set. "
                "Query Service needs a workspace enabled in the data app configuration."
            )
        return value

    def _get_query_service_client(self, retry_requests: bool = True) -> QueryServiceClient:
        try:
            from keboola_query_service import Client
        except ModuleNotFoundError as e:
            if (e.name or "").split(".", 1)[0] == "keboola_query_service":
                raise ImportError(
                    "Query Service support requires an optional dependency. "
                    "Install it with: pip install keboola-streamlit[query-service]"
                ) from e
            raise
        base_url = self._get_query_service_url()
        key = (base_url, self.__token, retry_requests)
        # Streamlit creates a new KeboolaStreamlit on every rerun, so clients are shared at module level. The cache
        # has no limit, which is fine for one app token but not for a token per user.
        with _QUERY_SERVICE_LOCK:
            client = _QUERY_SERVICE_CLIENTS.get(key)
            if client is None:
                options = {} if retry_requests else {"max_retries": 0}
                client = Client(base_url=base_url, token=self.__token, user_agent="keboola-streamlit", **options)
                _QUERY_SERVICE_CLIENTS[key] = client
        return client

    def _get_query_service_url(self) -> str:
        url = os.environ.get("QUERY_SERVICE_URL")
        if url:
            return url
        cached_url = _QUERY_SERVICE_URLS.get(self.__root_url)
        if cached_url:
            return cached_url
        # Apps that get the workspace through the deprecated WORKSPACE_ID secret, and local runs, have no
        # QUERY_SERVICE_URL, but the Storage API index lists the Query Service of the stack.
        response = requests.get(
            f"{self.__root_url}/v2/storage",
            params={"exclude": "components"},
            headers={"X-StorageApi-Token": self.__token},
            timeout=10,
        )
        response.raise_for_status()
        for service in response.json().get("services", []):
            if service.get("id") == "query" and service.get("url"):
                _QUERY_SERVICE_URLS[self.__root_url] = service["url"]
                return service["url"]
        raise RuntimeError("QUERY_SERVICE_URL is not set and the Storage API index does not list the Query Service.")

    def _read_workspace_id(self) -> str:
        manifest_path = os.environ.get("KBC_WORKSPACE_MANIFEST_PATH")
        if manifest_path:
            with open(manifest_path, encoding="utf-8") as manifest_file:
                workspace_id = json.load(manifest_file).get("workspaceId")
            if not workspace_id:
                raise RuntimeError(f"Workspace manifest {manifest_path} does not contain workspaceId.")
            if not _is_ascii_number(str(workspace_id)):
                raise RuntimeError(f"Workspace manifest {manifest_path} has a non-numeric workspaceId.")
            return str(workspace_id)
        workspace_id = os.environ.get("WORKSPACE_ID")
        if not workspace_id:
            raise RuntimeError(
                "Neither KBC_WORKSPACE_MANIFEST_PATH nor WORKSPACE_ID is set. "
                "Turn on Storage Access in the data app configuration."
            )
        if not _is_ascii_number(workspace_id):
            raise RuntimeError(f"WORKSPACE_ID must be a numeric workspace ID, got '{workspace_id}'.")
        return workspace_id

    def _get_branch_id(self) -> str:
        branch_id = self._require_env("BRANCH_ID")
        if not _is_ascii_number(branch_id):
            raise RuntimeError(f"Query Service needs a numeric branch ID, got '{branch_id}'.")
        return branch_id

    def query_service_execute_query(
        self,
        query: str,
        return_df: bool = True,
        convert_types: bool = True,
        max_wait_time: float = 300.0,
        max_rows: Optional[int] = None,
        refresh_metadata: Optional[bool] = None,
    ) -> Union[pd.DataFrame, int, None]:
        """
        Executes a SQL query through Query Service on the workspace provisioned for the data app.

        Every query is logged as a Storage event with the logged-in user before it is sent. If that event cannot be
        written, the query is not sent. Later events link the event's request ID to the Query Service job ID and to
        the backend query ID (the Snowflake query ID), and a failed query writes an event with the error. The events
        do not hold the SQL, which can contain personal data.

        Query Service does not support bind parameters, so escape any user input before putting it into SQL.

        Args:
            query (str): One SQL statement.
            return_df (bool): Flag to indicate whether to return the result as a Pandas DataFrame. Use False for
                statements that change data. Defaults to True.
            convert_types (bool): Convert columns from strings to pandas types based on the column type.
                Defaults to True.
            max_wait_time (float): Seconds to wait for the query before it is cancelled. Defaults to 300. A slow
                Query Service response can make the call take longer.
            max_rows (Optional[int]): The maximum number of rows to read. Defaults to all rows.
            refresh_metadata (Optional[bool]): Refresh the Storage metadata of changed tables after the query, so
                Storage exports show the new data. Defaults to True when return_df is False, otherwise False.

        Returns:
            Union[pd.DataFrame, int, None]: The query results if return_df is True, otherwise the number of affected
            rows. None when the query failed.
        """
        if max_rows is not None and (isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows <= 0):
            raise ValueError(f"max_rows must be a positive integer, got {max_rows!r}.")
        if (
            isinstance(max_wait_time, bool)
            or not isinstance(max_wait_time, (int, float))
            or not math.isfinite(max_wait_time)
            or max_wait_time <= 0
        ):
            raise ValueError(f"max_wait_time must be a positive number of seconds, got {max_wait_time!r}.")
        try:
            workspace_id = self._read_workspace_id()
            branch_id = self._get_branch_id()
            client = self._get_query_service_client()
            # The SDK repeats a request after a 5xx or a timeout, which could run a write twice.
            submit_client = self._get_query_service_client(retry_requests=False)
        except ImportError:
            raise
        except Exception as e:
            self._show_query_service_error(e)
            return None

        request_id = uuid.uuid4().hex

        def log_event(
            message: str,
            event_type: str,
            job_id: Optional[str] = None,
            backend_query_id: Optional[str] = None,
            event_data: Optional[str] = None,
        ) -> Optional[int]:
            params = {"workspace_id": workspace_id, "branch_id": branch_id}
            if backend_query_id is not None:
                params["backend_query_id"] = backend_query_id
            status_code, _ = self._create_event(
                message=message,
                event_type=event_type,
                endpoint=client.base_url,
                event_data=event_data,
                job_id=job_id,
                request_id=request_id,
                extra_params=params,
                show_error=False,
            )
            return status_code

        def log_failure(
            error: Exception,
            job_id: Optional[str] = None,
            backend_query_id: Optional[str] = None,
            outcome_unknown: bool = False,
        ) -> None:
            event_data = f"Error: {error}"
            if outcome_unknown:
                event_data += f"\n{QUERY_SERVICE_OUTCOME_UNKNOWN}"
            log_event(
                "Streamlit App Query Service Query Failed",
                "keboola_data_app_query_service_query_failed",
                job_id=job_id,
                backend_query_id=backend_query_id,
                event_data=event_data,
            )
            # Shown after the event is written: st.error raises when a Streamlit rerun is pending.
            self._show_query_service_error(error)

        if log_event("Streamlit App Query Service Query", "keboola_data_app_query_service_query") is None:
            message = "The query was not sent because its access event could not be written to Storage."
            logging.error(message)
            st.error(message)
            return None

        try:
            job_id = self._submit_query_service_job(
                submit_client,
                branch_id,
                workspace_id,
                query,
                refresh_metadata=not return_df if refresh_metadata is None else refresh_metadata,
            )
        except Exception as e:
            log_failure(e)
            return None
        log_event(
            "Streamlit App Query Service Query Submitted",
            "keboola_data_app_query_service_query_submitted",
            job_id=job_id,
        )

        try:
            status = self._wait_for_query_service_job(client, job_id, max_wait_time)
        except Exception as e:
            if _is_job_failed(e):
                log_failure(e, job_id, _first_backend_query_id(_get_job_status(client, job_id)))
                return None
            reason = f"No result within {max_wait_time} seconds." if _is_job_timeout(e) else "Stopped after an error."
            status = self._stop_query_service_job(client, job_id, reason)
            # A job that finished just before it was cancelled has a valid result.
            if status is None or not _is_job_completed(status):
                outcome_unknown = status is None or _state_name(status.status) not in QUERY_SERVICE_FINAL_STATES
                log_failure(e, job_id, _first_backend_query_id(status), outcome_unknown)
                return None

        statement = status.statements[0]
        log_event(
            "Streamlit App Query Service Query Completed",
            "keboola_data_app_query_service_query_completed",
            job_id=job_id,
            backend_query_id=statement.query_id,
        )
        if not return_df:
            return statement.rows_affected or 0
        try:
            columns, rows = self._fetch_query_service_results(client, job_id, statement.id, max_rows)
            return query_result_to_dataframe(columns, rows, convert_types)
        except Exception as e:
            log_failure(e, job_id, statement.query_id)
            return None

    @staticmethod
    def _submit_query_service_job(
        client: QueryServiceClient, branch_id: str, workspace_id: str, query: str, refresh_metadata: bool
    ) -> str:
        from keboola_query_service import QueryServiceError

        for attempt in range(1, QUERY_SERVICE_SUBMIT_ATTEMPTS + 1):
            try:
                return client.submit_job(
                    branch_id=branch_id,
                    workspace_id=workspace_id,
                    statements=[query],
                    transactional=False,
                    refresh_metadata_on_success=refresh_metadata,
                )
            except QueryServiceError as e:
                # Both errors mean no job was created, so the submit is safe to repeat. Query Service gives up on
                # fetching the workspace credentials sooner than they can take to provision.
                is_credentials_delay = e.status_code == 403 and "workspace credentials" in str(e).lower()
                is_rate_limited = e.status_code == 429
                if not (is_credentials_delay or is_rate_limited) or attempt == QUERY_SERVICE_SUBMIT_ATTEMPTS:
                    raise
                logging.warning(f"Query Service did not accept the query (attempt {attempt}), retrying: {e}")
                time.sleep(attempt)
        raise RuntimeError("Query Service submit attempts exhausted.")

    @staticmethod
    def _wait_for_query_service_job(client: QueryServiceClient, job_id: str, max_wait_time: float) -> JobStatus:
        deadline = time.monotonic() + max_wait_time
        wait_time = max_wait_time
        while True:
            try:
                status = client.wait_for_job(job_id, max_wait_time=wait_time)
                break
            except Exception as e:
                # The SDK retries a status request for less than a second, shorter than a Query Service restart.
                if not _is_temporary_error(e):
                    raise
                if time.monotonic() + QUERY_SERVICE_POLL_RETRY_SECONDS >= deadline:
                    from keboola_query_service import JobTimeoutError

                    raise JobTimeoutError(f"No result within {max_wait_time} seconds: {e}", job_id=job_id) from e
                logging.warning(f"Could not read the status of Query Service job {job_id}, retrying: {e}")
                time.sleep(QUERY_SERVICE_POLL_RETRY_SECONDS)
                wait_time = deadline - time.monotonic()
        # The SDK raises only for failed jobs and returns a canceled job like a completed one.
        if not _is_job_completed(status):
            reason = f": {status.cancellation_reason}" if status.cancellation_reason else ""
            raise RuntimeError(f"Query Service job {job_id} ended as {_state_name(status.status)}{reason}")
        return status

    @staticmethod
    def _stop_query_service_job(client: QueryServiceClient, job_id: str, reason: str) -> Optional[JobStatus]:
        """Cancels the job unless it has ended, and returns its last known status."""
        status = _get_job_status(client, job_id)
        if status is not None and _state_name(status.status) in QUERY_SERVICE_FINAL_STATES:
            return status
        try:
            client.cancel_job(job_id, reason=reason)
        except Exception as e:
            logging.error(f"Could not cancel Query Service job {job_id}: {e}")
        # Cancelling takes effect later, and the query can still complete in the meantime.
        try:
            client.wait_for_job(job_id, max_wait_time=QUERY_SERVICE_CANCEL_WAIT_SECONDS)
        except Exception as e:
            if not _is_job_failed(e):
                logging.error(f"Query Service job {job_id} did not end after the cancel: {e}")
        return _get_job_status(client, job_id)

    @staticmethod
    def _fetch_query_service_results(
        client: QueryServiceClient, job_id: str, statement_id: str, max_rows: Optional[int]
    ) -> Tuple[List[QueryServiceColumn], List[List[Any]]]:
        columns: Optional[List[QueryServiceColumn]] = None
        rows: List[List[Any]] = []
        while True:
            page_size = (
                QUERY_SERVICE_PAGE_SIZE if max_rows is None else min(QUERY_SERVICE_PAGE_SIZE, max_rows - len(rows))
            )
            page = client.get_job_results(job_id, statement_id, offset=len(rows), page_size=page_size)
            if columns is None:
                columns = page.columns
            rows.extend(page.data)
            if max_rows is not None and len(rows) >= max_rows:
                # A page can hold more rows than asked for if Query Service applies a minimum page size.
                return columns, rows[:max_rows]
            if page.number_of_rows is None:
                if len(page.data) < page_size:
                    return columns, rows
            elif len(rows) >= page.number_of_rows:
                return columns, rows
            elif not page.data:
                raise RuntimeError(f"Query Service returned {len(rows)} of {page.number_of_rows} rows.")

    @staticmethod
    def _show_query_service_error(error: Exception) -> None:
        logging.error(f"An error occurred while executing Query Service query: {error}")
        st.error(f"An error occurred while executing Query Service query: {error}")


def _is_ascii_number(value: str) -> bool:
    return re.fullmatch(r"[0-9]+", value) is not None


def _state_name(state: Any) -> str:
    """The SDK states are str enums; their str() differs between Python versions, their value does not."""
    return str(getattr(state, "value", state))


def _is_job_completed(status: JobStatus) -> bool:
    return (
        _state_name(status.status) == "completed"
        and bool(status.statements)
        and all(_state_name(statement.status) == "completed" for statement in status.statements)
    )


def _is_temporary_error(error: Exception) -> bool:
    from keboola_query_service import QueryServiceError

    try:
        from httpx import TransportError

        if isinstance(error, TransportError):
            return True
    except ImportError:
        # httpx comes with the query-service extra; only tests run with a fake SDK and without it.
        pass
    # Subclasses are job results (failed, timed out) or client errors (401, 400, 404). The base class without a
    # status code is a connection error or a timeout that outlasted the SDK retries.
    if type(error) is not QueryServiceError:
        return False
    return error.status_code is None or error.status_code == 429 or error.status_code >= 500


def _is_job_failed(error: Exception) -> bool:
    from keboola_query_service import JobError

    return isinstance(error, JobError)


def _is_job_timeout(error: Exception) -> bool:
    from keboola_query_service import JobTimeoutError

    return isinstance(error, JobTimeoutError)


def _get_job_status(client: QueryServiceClient, job_id: str) -> Optional[JobStatus]:
    try:
        return client.get_job_status(job_id)
    except Exception as e:
        logging.error(f"Could not read the status of Query Service job {job_id}: {e}")
        return None


def _first_backend_query_id(status: Optional[JobStatus]) -> Optional[str]:
    if status is None or not status.statements:
        return None
    return status.statements[0].query_id
