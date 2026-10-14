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

Turn on **Storage Access** in the data app configuration. Keboola then creates a read-only workspace for the app,
and the app queries it through the Keboola Query Service. No Snowflake credentials are needed in secrets.

```python
df = keboola.query_service_execute_query(query='SELECT * FROM "in.c-main"."users" LIMIT 100')
```

The library finds the workspace and the service from environment variables that Keboola sets:

- workspace ID: the JSON file at `KBC_WORKSPACE_MANIFEST_PATH`, or the `WORKSPACE_ID` variable
- `BRANCH_ID` (numeric)
- `QUERY_SERVICE_URL`; when it is not set, the URL is read from the Storage API index

Each query is logged as a Storage event with the logged-in user, the query text and the Query Service job ID:

- `keboola_data_app_query_service_query` is written right after the query is submitted. If it cannot be written,
  the query is cancelled and no data is returned.
- `keboola_data_app_query_service_query_failed` is written when the query fails or times out. It also holds the
  error message.

Query Service returns every value as a string. The library converts number, boolean, date and timestamp columns
to pandas types based on the column type. Integers become `Int64`, decimals become `float64` (about 15 significant
digits), and timestamps with a time zone are converted to UTC. Integers outside the `Int64` range, columns of other
types, and columns whose values do not match their type stay as strings. Pass `convert_types=False` to get only
strings.

Other options: `return_df=False` for statements without results, `max_rows` to read at most that many rows, and
`max_wait_time` (seconds, default 300) after which the query is cancelled.

Query Service does not support bind parameters, so escape any user input before putting it into SQL.

For local development, set `BRANCH_ID` and `WORKSPACE_ID` (and optionally `QUERY_SERVICE_URL`) yourself.

## License

This project is licensed under the MIT License. See the `LICENSE` file for more details.
