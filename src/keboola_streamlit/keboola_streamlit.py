from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd
import requests
import streamlit as st
from kbcstorage.client import Client

if TYPE_CHECKING:
    from keboola_query_service import Client as QueryServiceClient
    from snowflake.snowpark import Session


# Query Service defaults. The wait time can be overridden per call (max_wait_time).
QUERY_SERVICE_MAX_WAIT_TIME_SECONDS = 300.0
QUERY_SERVICE_PAGE_SIZE = 5000
QUERY_SERVICE_SUBMIT_ATTEMPTS = 3
QUERY_SERVICE_SUBMIT_RETRY_DELAY_SECONDS = 1.0
STORAGE_INDEX_TIMEOUT_SECONDS = 10.0
# Query Service returns every value as a string; these Snowflake column types are converted to numbers.
QUERY_SERVICE_NUMERIC_TYPES = frozenset({"fixed", "real", "number", "numeric", "decimal", "float", "double", "integer"})


# Configure logging
logging.basicConfig(level=logging.ERROR, format="%(asctime)s - %(levelname)s - %(message)s")


class KeboolaStreamlit:
    def __init__(
        self,
        root_url: str,
        token: str,
        *,
        query_service_url: Optional[str] = None,
        branch_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ):
        """
        Initializes the KeboolaStreamlit class with the provided parameters.

        Args:
            root_url (str): The root URL for the Keboola Storage API.
            token (str): The API token for accessing Keboola Storage.
            query_service_url (Optional[str]): Query Service base URL. Falls back to the QUERY_SERVICE_URL env var,
                then to discovery from the Storage API index. Only needed for the query_service_* methods.
            branch_id (Optional[str]): Numeric branch ID for Query Service. Falls back to the BRANCH_ID env var.
            workspace_id (Optional[str]): Workspace ID for Query Service. Falls back to the workspace manifest
                (KBC_WORKSPACE_MANIFEST_PATH), then to the WORKSPACE_ID env var.
        """
        self.__client = Client(root_url, token)
        self.__token = token
        self.__root_url = root_url.strip("/")
        self.dev_mockup_headers = None
        self.aggrid_license_key = os.environ.get("AGGRID_LICENSE_KEY")
        self._query_service_url = query_service_url
        self._branch_id = branch_id
        self._workspace_id = workspace_id
        self._query_service_client: Optional[QueryServiceClient] = None

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
        job_id: Optional[int] = None,
        event_type: str = "keboola_data_app_create_event",
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Creates an event in Keboola Storage.

        Args:
            message (str): The message for the event.
            endpoint (str): The endpoint for the event.
            event_data (Optional[str]): The data associated with the event.
            job_id (Optional[int]): The job ID for the event.
            event_type (str): The type of the event. Defaults to 'keboola_data_app_create_event'.
            extra_params (Optional[Dict[str, Any]]): Additional key/value pairs stored in the event params.

        Returns:
            Tuple[int, str]: The response status code and response text.
        """
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

        if "X-Kbc-User-Id" in headers:
            requestData["params"]["user_id"] = headers["X-Kbc-User-Id"]

        if event_application != "Unknown":
            match = re.search(r"https://.*-(\d+)\.", event_application)
            if match:
                requestData["params"]["application_id"] = match.group(1)

        if event_data is not None:
            requestData["params"]["event_data"] = f"{event_data}"
        if job_id is not None:
            requestData["params"]["event_job_id"] = job_id
        if extra_params:
            requestData["params"].update(extra_params)

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

    def _get_query_service_url(self) -> str:
        """
        Resolves the Query Service base URL: constructor argument, QUERY_SERVICE_URL env var,
        then the `query` service advertised by the Storage API index (GET /v2/storage).
        """
        if self._query_service_url:
            return self._query_service_url.rstrip("/")
        env_url = os.environ.get("QUERY_SERVICE_URL")
        if env_url:
            self._query_service_url = env_url
            return env_url.rstrip("/")

        response = requests.get(
            f"{self.__root_url}/v2/storage",
            headers={"X-StorageApi-Token": self.__token},
            timeout=STORAGE_INDEX_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        for service in response.json().get("services", []):
            if service.get("id") == "query" and service.get("url"):
                self._query_service_url = service["url"]
                return service["url"].rstrip("/")
        raise ValueError(
            "Query Service URL not found. Pass query_service_url, set the QUERY_SERVICE_URL env var, "
            "or make sure the stack advertises the 'query' service in GET /v2/storage."
        )

    def _get_branch_id(self) -> str:
        """
        Resolves the numeric branch ID: constructor argument, then the BRANCH_ID env var.
        Query Service rejects aliases such as 'default'.
        """
        branch_id = self._branch_id or os.environ.get("BRANCH_ID")
        if not branch_id:
            raise ValueError("Branch ID not found. Pass branch_id or set the BRANCH_ID env var.")
        if not str(branch_id).isdigit():
            raise ValueError(f"Query Service needs a numeric branch ID, got '{branch_id}'.")
        return str(branch_id)

    def _get_workspace_id(self) -> str:
        """
        Resolves the workspace ID: constructor argument, the workspace manifest
        (KBC_WORKSPACE_MANIFEST_PATH), then the WORKSPACE_ID env var.
        """
        if self._workspace_id:
            return str(self._workspace_id)
        manifest_path = os.environ.get("KBC_WORKSPACE_MANIFEST_PATH")
        if manifest_path and os.path.exists(manifest_path):
            with open(manifest_path, encoding="utf-8") as manifest_file:
                workspace_id = json.load(manifest_file).get("workspaceId")
            if workspace_id:
                return str(workspace_id)
        workspace_id = os.environ.get("WORKSPACE_ID")
        if workspace_id:
            return str(workspace_id)
        raise ValueError(
            "Workspace ID not found. Pass workspace_id, or enable Storage Access in the data app configuration "
            "so the platform provides KBC_WORKSPACE_MANIFEST_PATH / WORKSPACE_ID."
        )

    def query_service_get_client(self) -> QueryServiceClient:
        """
        Returns the underlying keboola_query_service Client (created on first use).

        Queries sent through this client directly are NOT logged as Storage events.
        Use query_service_execute_query() whenever user access must be audited.
        """
        try:
            from keboola_query_service import Client as QueryServiceClient
        except ModuleNotFoundError as e:
            missing_root = (e.name or "").split(".", 1)[0]
            if missing_root == "keboola_query_service":
                raise ImportError(
                    "Query Service support requires an optional dependency. "
                    "Install it with: pip install keboola-streamlit[query-service]"
                ) from e
            raise

        if self._query_service_client is None:
            self._query_service_client = QueryServiceClient(base_url=self._get_query_service_url(), token=self.__token)
        return self._query_service_client

    def _query_service_submit(self, client: QueryServiceClient, statements: List[str], transactional: bool) -> str:
        """
        Submits a query job. Retries when Query Service cannot obtain workspace credentials yet (403)
        or the credentials lock is held (409); both are transient.
        """
        from keboola_query_service import QueryServiceError

        branch_id = self._get_branch_id()
        workspace_id = self._get_workspace_id()
        for attempt in range(1, QUERY_SERVICE_SUBMIT_ATTEMPTS + 1):
            try:
                return client.submit_job(
                    branch_id=branch_id,
                    workspace_id=workspace_id,
                    statements=statements,
                    transactional=transactional,
                )
            except QueryServiceError as e:
                transient = e.status_code == 409 or (e.status_code == 403 and "workspace credentials" in str(e).lower())
                if not transient or attempt == QUERY_SERVICE_SUBMIT_ATTEMPTS:
                    raise
                logging.warning(f"Query Service submit attempt {attempt} failed, retrying: {e}")
                time.sleep(QUERY_SERVICE_SUBMIT_RETRY_DELAY_SECONDS * attempt)
        raise RuntimeError("unreachable")

    @staticmethod
    def _query_service_fetch_results(
        client: QueryServiceClient, job_id: str, statement_id: str, max_rows: Optional[int]
    ) -> Any:
        """
        Fetches all result pages of one statement (up to max_rows) and merges them into one QueryResult.
        """
        merged = None
        offset = 0
        while True:
            page_size = QUERY_SERVICE_PAGE_SIZE if max_rows is None else min(QUERY_SERVICE_PAGE_SIZE, max_rows - offset)
            page = client.get_job_results(job_id, statement_id, offset=offset, page_size=page_size)
            if merged is None:
                merged = page
            else:
                merged.data.extend(page.data)
            offset += len(page.data)
            reached_cap = max_rows is not None and offset >= max_rows
            reached_total = page.number_of_rows is not None and offset >= page.number_of_rows
            if len(page.data) < page_size or reached_cap or reached_total:
                # Query Service enforces a minimum page size, so the last page can overshoot max_rows.
                if max_rows is not None:
                    merged.data = merged.data[:max_rows]
                return merged

    @staticmethod
    def _query_result_to_df(result: Any, coerce_numeric: bool) -> pd.DataFrame:
        """
        Converts a keboola_query_service QueryResult to a DataFrame. Query Service returns all values
        as strings; numeric Snowflake types are converted when coerce_numeric is True.
        """
        columns = [column.name for column in result.columns]
        df = pd.DataFrame(result.data, columns=columns)
        if coerce_numeric:
            for column in result.columns:
                if (column.type or "").lower() in QUERY_SERVICE_NUMERIC_TYPES:
                    df[column.name] = pd.to_numeric(df[column.name])
        return df

    def query_service_execute_query(
        self,
        query: Union[str, Sequence[str]],
        *,
        return_df: bool = True,
        transactional: bool = True,
        max_rows: Optional[int] = None,
        max_wait_time: float = QUERY_SERVICE_MAX_WAIT_TIME_SECONDS,
        coerce_numeric: bool = True,
        raise_on_error: bool = False,
    ) -> Union[pd.DataFrame, List[pd.DataFrame], None]:
        """
        Executes SQL in the data app's workspace through the Keboola Query Service and logs the access.

        Every call is attributed to the logged-in app user (X-Kbc-User-Email / X-Kbc-User-Id headers from
        the data app proxy) through a Storage event. The access event is written as soon as the job is
        submitted and carries the Query Service job ID, so it can be matched to the Query Service history.
        If the access event cannot be written, the job is cancelled and no data is returned, so the app
        never shows data that was not logged. A failed or timed-out job writes a second event with the error.

        Query Service does not support bind parameters; escape any user input before putting it into SQL.

        Args:
            query (Union[str, Sequence[str]]): One SQL statement, or a list of statements. Each item must be a
                single SQL command.
            return_df (bool): Return results as DataFrames. Defaults to True.
            transactional (bool): Run the statements in one transaction. Defaults to True.
            max_rows (Optional[int]): Cap on rows fetched per statement. Defaults to all rows.
            max_wait_time (float): Seconds to wait for the job before cancelling it.
            coerce_numeric (bool): Convert numeric columns from strings to numbers. Defaults to True.
            raise_on_error (bool): Re-raise errors instead of showing them with st.error. Defaults to False.

        Returns:
            Union[pd.DataFrame, List[pd.DataFrame], None]: A DataFrame for a single statement, a list of
            DataFrames for a list of statements, or None when return_df is False or the query failed.
        """
        statements = [query] if isinstance(query, str) else list(query)
        if not statements:
            raise ValueError("query must contain at least one SQL statement.")
        if max_rows is not None and max_rows <= 0:
            raise ValueError("max_rows must be a positive integer.")
        query_text = "\n".join(statements)
        job_id: Optional[str] = None
        try:
            client = self.query_service_get_client()
            from keboola_query_service import JobTimeoutError

            job_id = self._query_service_submit(client, statements, transactional)
            audit_params = {
                "query_job_id": job_id,
                "branch_id": self._get_branch_id(),
                "workspace_id": self._get_workspace_id(),
            }
            status_code, _ = self.create_event(
                message="Streamlit App Query Service Query",
                endpoint=f"{self._get_query_service_url()}/api/v1/queries/{job_id}",
                event_data=f"Query: {query_text}",
                event_type="keboola_data_app_query_service_query",
                extra_params=audit_params,
            )
            if status_code is None:
                client.cancel_job(job_id, reason="keboola_streamlit: access event could not be written")
                raise RuntimeError("Query cancelled because the access event could not be written to Storage.")

            try:
                status = client.wait_for_job(job_id, max_wait_time=max_wait_time)
            except JobTimeoutError:
                client.cancel_job(job_id, reason=f"keboola_streamlit: timed out after {max_wait_time}s")
                raise

            if not return_df:
                return None
            results = []
            for statement in status.statements:
                result = self._query_service_fetch_results(client, job_id, statement.id, max_rows)
                results.append(self._query_result_to_df(result, coerce_numeric))
            return results[0] if isinstance(query, str) else results
        except Exception as e:
            logging.error(f"An error occurred while executing Query Service query: {e}")
            if job_id is not None:
                self.create_event(
                    message="Streamlit App Query Service Query Failed",
                    event_type="keboola_data_app_query_service_query_failed",
                    event_data=f"Query: {query_text}",
                    extra_params={"query_job_id": job_id, "error": str(e)},
                )
            if raise_on_error:
                raise
            st.error(f"An error occurred while executing Query Service query: {e}")
        return None
