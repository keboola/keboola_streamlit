# KeboolaStreamlit

Keboola Streamlit simplifies the use of Keboola Storage API within Streamlit apps, providing easy-to-use functions for authentication, data retrieval, event logging, and data loading.

## Installation

To install KeboolaStreamlit, you can use `pip`:

```bash
pip install keboola-streamlit
```

## Usage

### Initialization

Create an instance of the `KeboolaStreamlit` class with the required parameters:

```python
from keboola_streamlit import KeboolaStreamlit

keboola = KeboolaStreamlit(root_url='https://connection.keboola.com', token='YOUR_API_TOKEN')
```

### Authentication Check

Ensure that the user is authenticated and authorized to access the Streamlit app:

```python
keboola.auth_check(required_role_id='YOUR_REQUIRED_ROLE_ID')
```

### Retrieve Data

Fetch data from a Keboola Storage table and return it as a Pandas DataFrame:

```python
df = keboola.get_table(table_id='YOUR_TABLE_ID')
```

### Load Data

Load data from a Pandas DataFrame into a Keboola Storage table:

```python
keboola.load_table(table_id='YOUR_TABLE_ID', df=your_dataframe, is_incremental=False)
```

### Create Event

Create an event in Keboola Storage to log activities:

```python
keboola.create_event()
```

### Add Keboola Table Selection

Add a Keboola table selection form to your Streamlit app sidebar:

```python
selected_table_data = keboola.add_table_selection()
```

## License

This project is licensed under the MIT License. See the `LICENSE` file for more details.


