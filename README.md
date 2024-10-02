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

## License

This project is licensed under the MIT License. See the `LICENSE` file for more details.

## Testing

This project includes a suite of unit tests to ensure the functionality of the KeboolaStreamlit class. The tests are located in the `tests/test_keboola_streamlit.py` file.

### Running Tests

To run the tests, follow these steps:

1. Ensure you have installed the required dependencies, including `pytest` and `pytest-mock`. You can install them using:

   ```bash
   pip install pytest pytest-mock
   ```

2. Navigate to the root directory of the project in your terminal.

3. Run the tests using the following command:

   ```bash
   pytest tests/test_keboola_streamlit.py
   ```

### Test Coverage

The tests cover various aspects of the KeboolaStreamlit class, including:

- Initialization
- Setting development mockup headers
- Authentication checks
- Event creation
- Table reading and writing
- Table selection functionality

### Writing New Tests

If you're contributing to this project and adding new features, please ensure you also add corresponding tests. Follow the existing test structure in the `test_keboola_streamlit.py` file as a guide.

## Contributing

Contributions to KeboolaStreamlit are welcome! Please ensure that any pull requests include appropriate tests for new functionality or bug fixes.