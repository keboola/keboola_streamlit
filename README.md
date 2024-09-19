# KeboolaStreamlit

Keboola Streamlit simplifies the use of Keboola Storage API within Streamlit apps, providing easy-to-use functions for authentication, data retrieval, event logging, and data loading.

## Installation

To install KeboolaStreamlit, you can use `pip`:

```bash
pip install keboola-streamlit
```


## Usage

### Import and Initialization

Create an instance of the `KeboolaStreamlit` class, and initialize it with the required parameters from Streamlit secrets:

```python
import streamlit as st
from keboola_streamlit import KeboolaStreamlit

URL = st.secrets["KEBOOLA_URL"]
TOKEN = st.secrets["STORAGE_API_TOKEN"]
ROLE_ID = st.secrets["REQUIRED_ROLE_ID"]

keboola = KeboolaStreamlit(root_url=URL, token=TOKEN)
```

### Authentication and Authorization

Ensure that the user is authenticated and authorized to access the Streamlit app:

```python
keboola.auth_check(required_role_id=ROLE_ID)
```

Add a logout button to your Streamlit app:

```python
keboola.logout_button(sidebar=True, use_container_width=True)
```

### Reading Data from Keboola Storage

Read data from a Keboola Storage table and return it as a Pandas DataFrame:

```python
df = keboola.read_table(table_id='YOUR_TABLE_ID')
```

### Writing Data to Keboola Storage

Write data from a Pandas DataFrame to a Keboola Storage table:

```python
keboola.write_table(table_id='YOUR_TABLE_ID', df=your_dataframe, is_incremental=False)
```

### Creating Events

Create an event in Keboola Storage to log activities:

```python
keboola.create_event(message="Custom Event Message", event_type="custom_event")
```

### Table Selection

Add a table selection interface in your Streamlit app:

```python
df = keboola.add_table_selection(sidebar=True)
```

### Logout Button

Add a logout button to your app:

```python
keboola.logout_button()
```

## License

This project is licensed under the MIT License. See the `LICENSE` file for more details.