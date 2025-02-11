![Alt text](https://assets-global.website-files.com/5e21dc6f4c5acf29c35bb32c/5e21e66410e34945f7f25add_Keboola_logo.svg)

# KeboolaStreamlit

KeboolaStreamlit simplifies the use of Keboola Storage API within Streamlit apps, providing easy-to-use functions for authentication, data retrieval, event logging, and data loading.

## Installation

To install:

```bash
pip install keboola-streamlit
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

#### Creating a Snowflake Session

To interact with Snowflake, first create a session using your Streamlit secrets. Ensure that the following secrets are set in your Streamlit configuration:

- `SNOWFLAKE_USER`
- `SNOWFLAKE_PASSWORD`
- `SNOWFLAKE_ACCOUNT`
- `SNOWFLAKE_ROLE`
- `SNOWFLAKE_WAREHOUSE`
- `SNOWFLAKE_DATABASE`
- `SNOWFLAKE_SCHEMA`

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

## License

This project is licensed under the MIT License. See the `LICENSE` file for more details.
