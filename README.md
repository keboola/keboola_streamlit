![Alt text](https://assets-global.website-files.com/5e21dc6f4c5acf29c35bb32c/5e21e66410e34945f7f25add_Keboola_logo.svg)

# KeboolaStreamlit

KeboolaStreamlit simplifies the use of Keboola Storage API within Streamlit apps, providing easy-to-use functions for authentication, data retrieval, event logging, and data loading.

## Installation

To install:

```bash
pip install keboola-streamlit
```

If you also need the [Snowflake Integration](#snowflake-integration) functions, install the
`snowflake` extra:

```bash
pip install keboola-streamlit[snowflake]
```

If you query the data app workspace through [Query Service](#query-service-integration), install the
`query-service` extra:

```bash
pip install keboola-streamlit[query-service]
```

_If you are using `streamlit<=1.36.0`, please use version `0.0.5` of the keboola-streamlit package._

## Usage

### Import and Initialization

Create an instance of the `KeboolaStreamlit` class, and initialize it with the required parameters from Streamlit secrets:

```python
import streamlit as st
from keboola_streamlit import KeboolaStreamlit

URL = st.secrets["KEBOOLA_URL"]
TOKEN = st.secrets["STORAGE_API_TOKEN"]

keboola = KeboolaStreamlit(root_url=URL, token=TOKEN)
```

### Authentication and Authorization

If only selected roles can access the app, make sure the user is authorized by:

```python
ROLE_ID = st.secrets["REQUIRED_ROLE_ID"]

keboola.auth_check(required_role_id=ROLE_ID)
```

Add a logout button to your app:

```python
keboola.logout_button(sidebar=True, use_container_width=True)
```

💡 _You can find more about authorization settings in Keboola [here](https://help.keboola.com/components/data-apps/#authorization)._

### Reading Data from Keboola Storage

Read data from a Keboola Storage table and return it as a Pandas DataFrame:

```python
df = keboola.read_table(table_id='YOUR_TABLE_ID')
```

💡 _Wrap the function and use the `st.cache_data` decorator to prevent your data from being read every time you interact with the app. Learn more about caching [here](https://docs.streamlit.io/develop/concepts/architecture/caching)._

### Writing Data to Keboola Storage

Write data from a Pandas DataFrame to a Keboola Storage table:

```python
keboola.write_table(table_id='YOUR_TABLE_ID', df=your_dataframe, is_incremental=False)
```

### Creating Events

Create an event in Keboola Storage to log activities:

```python
keboola.create_event(message='Streamlit App Create Event', event_type='keboola_data_app_create_event')
```

Every event the library writes holds the logged-in user: the email (`user`) and, for apps with an identity provider
login, the provider's user ID (`user_id`), which stays the same when the email changes.

### Table Selection

Add a table selection interface in your app:

```python
df = keboola.add_table_selection(sidebar=True)
```

### Snowflake Integration

> Requires the `snowflake` extra: `pip install keboola-streamlit[snowflake]`

#### Creating a Snowflake Session

To interact with Snowflake, first create a session using your Streamlit secrets. Ensure that the following secrets are set in your Streamlit configuration:

- `SNOWFLAKE_USER`
- `SNOWFLAKE_ACCOUNT`
- `SNOWFLAKE_ROLE`
- `SNOWFLAKE_WAREHOUSE`
- `SNOWFLAKE_DATABASE`
- `SNOWFLAKE_SCHEMA`

For authentication you can use password:

- `SNOWFLAKE_PASSWORD`

Or key-pair:

- `SNOWFLAKE_PRIVATE_KEY`
- `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` (Optional)

Streamlit will automatically use type of authorization based on what you fill in secrets.

> NOTE: If both password and key-pair are filled in secrets, Streamlit by default will use the key-pair authorization.

Then, create the session as follows:

```python
st.session_state['snowflake_session'] = keboola.snowflake_create_session_object()
```

#### Reading Data from Snowflake

Load a table from Snowflake into a Pandas DataFrame:

```python
df_snowflake = keboola.snowflake_read_table(session=st.session_state['snowflake_session'], table_id='YOUR_SNOWFLAKE_TABLE_ID')
```

#### Executing a Snowflake Query

Execute a SQL query on Snowflake and optionally return the results as a DataFrame:

```python
query = "SELECT * FROM YOUR_SNOWFLAKE_TABLE"
df_query_result = keboola.snowflake_execute_query(session=st.session_state['snowflake_session'], query=query, return_df=True)
```

#### Writing Data to Snowflake

Write a Pandas DataFrame to a Snowflake table:

```python
keboola.snowflake_write_table(session=st.session_state['snowflake_session'], df=your_dataframe, table_id='YOUR_SNOWFLAKE_TABLE_ID')
```

### Query Service Integration

> Requires the `query-service` extra: `pip install keboola-streamlit[query-service]`

Turn on **Storage Access** in the data app configuration. Keboola then creates a workspace for the app, and the app
queries it through the Keboola Query Service. The workspace can read the project's Storage, and write to the tables
in the app's output mapping (direct grant). No Snowflake credentials are needed in secrets.

```python
df = keboola.query_service_execute_query(query='SELECT * FROM "in.c-main"."users" LIMIT 100')

# Statements that change data: return_df=False returns the number of affected rows, or None when the query failed.
rows_affected = keboola.query_service_execute_query(
    query='UPDATE "in.c-main"."users" SET "active" = FALSE WHERE "id" = \'42\'',
    return_df=False,
)
```

The library finds the workspace and the service from environment variables that Keboola sets:

- workspace ID: the JSON file at `KBC_WORKSPACE_MANIFEST_PATH`, or the `WORKSPACE_ID` variable
- `BRANCH_ID` (numeric)
- `QUERY_SERVICE_URL`; when it is not set, the URL is read from the Storage API index

Each query is logged as Storage events with the logged-in user (email and user ID). The events of one query share a
`request_id` and hold the workspace ID and branch ID:

- `keboola_data_app_query_service_query` is written **before** the query is sent. If it cannot be written, the query
  is not sent, so no query runs without an audit entry.
- `keboola_data_app_query_service_query_submitted` holds the Query Service job ID, to match the query with the Query
  Service history.
- `keboola_data_app_query_service_query_completed` holds the backend query ID (`backend_query_id`, the Snowflake query
  ID), to find the query in the Snowflake query history.
- `keboola_data_app_query_service_query_failed` is written when the query fails, is cancelled or times out, or its
  results cannot be read. It holds the error message and, when the query reached the backend, the backend query ID.
  A query that failed or timed out is cancelled, so it does not keep running. When the cancel has not taken effect
  within 30 seconds, the event says so, because the query may still complete.

The events do not hold the SQL text, because it can contain personal data. Use the backend query ID to see the SQL.
The error message in the failed event comes from the database and can repeat values from the query, e.g.
`Numeric value 'jane@example.com' is not recognized`.

Results cached with `st.cache_data` are shared by all users of the app, and a cached call writes no events. To log
every read, call `query_service_execute_query` without caching, or include the user (e.g. the `X-Kbc-User-Email`
header) in the arguments of the cached function, so each user gets their own cache entry and event.

Query Service returns every value as a string. The library converts number, boolean, date and timestamp columns
to pandas types based on the column type. Integers become `Int64`, decimals become `float64` (about 15 significant
digits), and timestamps with a time zone are converted to UTC. Snowflake dates and timestamps outside the pandas
range (years 1677 to 2262, e.g. `9999-12-31`) become Python `date` and `datetime` objects, and then the whole column
has the `object` type. BigQuery dates and timestamps outside that range stay as strings. Integers outside the `Int64` range,
columns of other types (including `TIME`, which Snowflake sends as seconds since midnight), and columns whose values
do not match their type stay as strings. Pass `convert_types=False` to get only strings.

Other options:

- `refresh_metadata` refreshes the Storage metadata of changed tables after the query, so Storage exports show the new
  data. It is on by default when `return_df=False`; pass `refresh_metadata=True` when a query with `return_df=True`
  changes data.
- `max_rows` reads at most that many rows.
- `max_wait_time` (seconds, default 300) after which the query is cancelled. A slow Query Service response can make
  the call take longer.

Query Service does not support bind parameters, so escape any user input before putting it into SQL.

For local development, set `BRANCH_ID` and `WORKSPACE_ID` (and optionally `QUERY_SERVICE_URL`) yourself.

## License

This project is licensed under the MIT License. See the `LICENSE` file for more details.
