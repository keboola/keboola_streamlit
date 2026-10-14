# Query Service Workspace Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Streamlit apps query the workspace that keboola-operator provisions for them, through Query Service, and log a Storage event for each query with the user's identity.

**Architecture:** Add `KeboolaStreamlit.query_service_execute_query()` behind a new optional extra `query-service` (it depends on `keboola-query-service`). The method reads the workspace ID from the manifest file that the operator mounts, and reads the branch ID and Query Service URL from env vars that sandboxes-service sets. It runs the query through the Query Service SDK, then calls the existing `create_event()`, for both successful and failed queries. That call sends the user email from the proxy headers and the Query Service job ID. Query Service does not record who ran a query, so this event is the only record of it.

**Tech Stack:** Python >=3.10, `keboola-query-service>=0.1.7` (httpx based), pandas, Streamlit, pytest, uv.

**Spec:** No separate spec. The design comes from the investigation summarized here:
- sandboxes-service `src/AppType/Streamlit/StreamlitAppType.php`: when `runtime.workspace.enabled` is true, the app container gets `QUERY_SERVICE_URL`. Every operator app gets `BRANCH_ID`, `KBC_URL` and `KBC_TOKEN`.
- keboola-operator `internal/controller/app/backend/k8s/app_controller.go` (`ensureWorkspaceSecret`, `addWorkspaceToPodSpec`): mounts `{"workspaceId": "<id>"}` at `/var/run/secrets/keboola.com/workspace/manifest.json` and sets `KBC_WORKSPACE_MANIFEST_PATH`. On E2B the file is `/data/kbc_workspace_manifest.json`, with the same env var.
- The workspace has `readOnlyStorageAccess: true` and the app gets no Snowflake credentials, so the existing `snowflake_*` methods cannot use it.
- `../python-storage` (`keboola.storage`) has no workspace or Query Service support and needs Python >=3.12, so it is not used here.
- Reference consumer: `../data-app-direct-access-skill/app/storage.py`.

## Global Constraints

- `requires-python = ">=3.10"` stays. CI runs Python 3.10 and 3.13.
- The base package must import without any extra installed (the CI wheel smoke test checks this).
- Import `keboola_query_service` lazily inside methods, plus once under `TYPE_CHECKING`, the same way `snowflake.snowpark` is imported.
- Line length is 120 (ruff and flake8).
- No ticket IDs in code, comments or test names. Comments only explain a *why* the code cannot show.
- Follow the library's error style: show errors with `logging.error` and `st.error`, then return `None`. The one exception is a missing optional dependency, which raises `ImportError` naming `keboola-streamlit[query-service]`.
- Event types: `keboola_data_app_query_service_query` (success) and `keboola_data_app_query_service_query_failed` (a query sent to Query Service that failed). Event component stays `keboola.data-apps` (set inside `create_event`).
- Failure events are sent only after the query reaches Query Service (submit, wait or fetch results). Configuration errors (missing env var, missing manifest) send no event, so a misconfigured app does not add an event on every Streamlit rerun.
- Commits use Conventional Commits. Work on a branch named `{name}-{TICKET}`.

## Review Focus

1. Workspace not turned on for the app (`KBC_WORKSPACE_MANIFEST_PATH` unset): show a clear `st.error` that names the missing env var, return `None`, send no event, and don't crash. Tested in Task 3 (`test_query_service_execute_query_without_workspace`).
2. A failed SQL statement (the SDK raises `JobError`): show the Query Service error message, return `None`, and send a `keboola_data_app_query_service_query_failed` event with the Query Service job ID. If `submit_job` itself fails, the event is sent without a job ID. Tested in Task 3 (`test_query_service_execute_query_failure`, `test_query_service_execute_query_submit_failure`).
3. A statement with no result columns (DDL, `USE`, and similar) with `return_df=True`: return an empty DataFrame, not an exception. Tested in Task 3 (`test_query_service_execute_query_no_columns`).
4. Result larger than one page (the API default is 500 rows): every row is returned, not only the first page. Tested in Task 3 (`test_query_service_execute_query_reads_all_pages`).
5. Manifest has `workspaceId` as a JSON number instead of a string: still works and is passed on as a string. Tested in Task 2 (`test_read_workspace_id_numeric`).
6. Local run without proxy headers: the event is still sent with user `Unknown` and the query still returns data. Tested in Task 3 (`test_query_service_execute_query_event_contains_user`, which also covers the email when headers exist).

---

### Task 1: Optional extra and Query Service client

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies]`)
- Modify: `src/keboola_streamlit/keboola_streamlit.py` (imports, `__init__`, new private methods after `_log_missing_snowflake_session`)
- Test: `tests/test_keboola_streamlit.py`

**Interfaces:**
- Produces:
  - `KeboolaStreamlit._require_env(name: str) -> str`, a static method. Raises `RuntimeError` when the env var is unset or empty.
  - `KeboolaStreamlit._get_query_service_client(self) -> QueryServiceClient`. Creates the client once and caches it in `self._query_service_client`. Raises `ImportError` when the extra is missing.

- [ ] **Step 1: Add the extra to `pyproject.toml`**

```toml
[project.optional-dependencies]
snowflake = ["snowflake-snowpark-python>=1.35.0"]
query-service = ["keboola-query-service>=0.1.7"]
```

Run: `uv lock`
Expected: `uv.lock` updated with `keboola-query-service` and `httpx`.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_keboola_streamlit.py` (add `from types import SimpleNamespace` to the imports):

```python
def _mock_query_service_module(columns=("A",), data=(("1",),)):
    fake_client = MagicMock(name="query_service_client")
    fake_client.submit_job.return_value = "qs-job-1"
    fake_client.wait_for_job.return_value = SimpleNamespace(statements=[SimpleNamespace(id="stmt-1")])
    fake_client.get_job_results.return_value = SimpleNamespace(
        columns=[SimpleNamespace(name=name) for name in columns],
        data=[list(row) for row in data],
    )
    fake_client_cls = MagicMock(name="Client", return_value=fake_client)
    return MagicMock(Client=fake_client_cls), fake_client_cls, fake_client


@pytest.fixture
def query_service_env(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"workspaceId": "12345"}')
    monkeypatch.setenv("KBC_WORKSPACE_MANIFEST_PATH", str(manifest))
    monkeypatch.setenv("QUERY_SERVICE_URL", "https://query.example.com")
    monkeypatch.setenv("BRANCH_ID", "678")
    return manifest


def test_get_query_service_client_without_extra(keboola_streamlit, query_service_env):
    with patch.dict(sys.modules, {"keboola_query_service": None}):
        with pytest.raises(ImportError, match=r"keboola-streamlit\[query-service\]"):
            keboola_streamlit._get_query_service_client()


def test_get_query_service_client_missing_unrelated_dependency_is_not_masked(keboola_streamlit, query_service_env):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "keboola_query_service":
            raise ModuleNotFoundError("No module named 'httpx'", name="httpx")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(ModuleNotFoundError, match="httpx"):
            keboola_streamlit._get_query_service_client()


def test_get_query_service_client_is_created_once(keboola_streamlit, query_service_env):
    fake_module, fake_client_cls, fake_client = _mock_query_service_module()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}):
        first = keboola_streamlit._get_query_service_client()
        second = keboola_streamlit._get_query_service_client()

    assert first is fake_client
    assert second is fake_client
    fake_client_cls.assert_called_once_with(
        base_url="https://query.example.com", token="dummy_token", user_agent="keboola-streamlit"
    )


def test_require_env_missing(keboola_streamlit, monkeypatch):
    monkeypatch.delenv("QUERY_SERVICE_URL", raising=False)
    with pytest.raises(RuntimeError, match="QUERY_SERVICE_URL"):
        keboola_streamlit._require_env("QUERY_SERVICE_URL")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_keboola_streamlit.py -k "query_service_client or require_env" -v`
Expected: FAIL with `AttributeError: 'KeboolaStreamlit' object has no attribute '_get_query_service_client'` (and `_require_env`).

- [ ] **Step 4: Write the implementation**

In `src/keboola_streamlit/keboola_streamlit.py`, extend the `TYPE_CHECKING` block:

```python
if TYPE_CHECKING:
    from keboola_query_service import Client as QueryServiceClient
    from snowflake.snowpark import Session
```

At the end of `__init__`:

```python
        self._query_service_client: Optional[QueryServiceClient] = None
```

After `_log_missing_snowflake_session`:

```python
    @staticmethod
    def _require_env(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(
                f"Environment variable {name} is not set. "
                "Query Service needs a workspace enabled in the data app configuration."
            )
        return value

    def _get_query_service_client(self) -> QueryServiceClient:
        if self._query_service_client is not None:
            return self._query_service_client
        try:
            from keboola_query_service import Client
        except ModuleNotFoundError as e:
            if (e.name or "").split(".", 1)[0] == "keboola_query_service":
                raise ImportError(
                    "Query Service support requires an optional dependency. "
                    "Install it with: pip install keboola-streamlit[query-service]"
                ) from e
            raise
        self._query_service_client = Client(
            base_url=self._require_env("QUERY_SERVICE_URL"),
            token=self.__token,
            user_agent="keboola-streamlit",
        )
        return self._query_service_client
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_keboola_streamlit.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/keboola_streamlit/keboola_streamlit.py tests/test_keboola_streamlit.py
git commit -m "feat: add optional query-service extra and client factory"
```

---

### Task 2: Read the workspace ID from the operator manifest

**Files:**
- Modify: `src/keboola_streamlit/keboola_streamlit.py` (add `import json`, new method after `_get_query_service_client`)
- Test: `tests/test_keboola_streamlit.py`

**Interfaces:**
- Consumes: `KeboolaStreamlit._require_env(name: str) -> str` (Task 1), and the `query_service_env` fixture (Task 1).
- Produces: `KeboolaStreamlit._read_workspace_id(self) -> str`. Raises `RuntimeError` when the env var is unset or the manifest has no `workspaceId`. Raises `OSError` / `json.JSONDecodeError` when the file can't be read.

- [ ] **Step 1: Write the failing tests**

```python
def test_read_workspace_id(keboola_streamlit, query_service_env):
    assert keboola_streamlit._read_workspace_id() == "12345"


def test_read_workspace_id_numeric(keboola_streamlit, query_service_env):
    query_service_env.write_text('{"workspaceId": 12345}')
    assert keboola_streamlit._read_workspace_id() == "12345"


def test_read_workspace_id_without_env(keboola_streamlit, monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    with pytest.raises(RuntimeError, match="KBC_WORKSPACE_MANIFEST_PATH"):
        keboola_streamlit._read_workspace_id()


def test_read_workspace_id_missing_key(keboola_streamlit, query_service_env):
    query_service_env.write_text("{}")
    with pytest.raises(RuntimeError, match="workspaceId"):
        keboola_streamlit._read_workspace_id()


def test_read_workspace_id_missing_file(keboola_streamlit, monkeypatch, tmp_path):
    monkeypatch.setenv("KBC_WORKSPACE_MANIFEST_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        keboola_streamlit._read_workspace_id()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_keboola_streamlit.py -k read_workspace_id -v`
Expected: FAIL with `AttributeError: ... no attribute '_read_workspace_id'`.

- [ ] **Step 3: Write the implementation**

Add `import json` to the stdlib imports. After `_get_query_service_client`:

```python
    def _read_workspace_id(self) -> str:
        manifest_path = self._require_env("KBC_WORKSPACE_MANIFEST_PATH")
        with open(manifest_path, encoding="utf-8") as manifest_file:
            workspace_id = json.load(manifest_file).get("workspaceId")
        if not workspace_id:
            raise RuntimeError(f"Workspace manifest {manifest_path} does not contain workspaceId.")
        return str(workspace_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_keboola_streamlit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/keboola_streamlit/keboola_streamlit.py tests/test_keboola_streamlit.py
git commit -m "feat: read workspace ID from operator workspace manifest"
```

---

### Task 3: `query_service_execute_query` with a Storage event

**Files:**
- Modify: `src/keboola_streamlit/keboola_streamlit.py` (`create_event` signature, new public method at the end of the class)
- Test: `tests/test_keboola_streamlit.py`

**Interfaces:**
- Consumes: `_get_query_service_client()` (Task 1), `_read_workspace_id()` and `_require_env()` (Tasks 1–2), the `_mock_query_service_module()` helper and the `query_service_env` fixture (Task 1).
- Produces:
  - `KeboolaStreamlit.query_service_execute_query(self, query: str, return_df: bool = True) -> Optional[pd.DataFrame]`.
  - `KeboolaStreamlit._show_query_service_error(error: Exception) -> None`, a static method that logs the error and shows it with `st.error`.
  - `KeboolaStreamlit._fetch_query_service_results(client, job_id: str, statement_id: str) -> Tuple[List[str], List[List[Any]]]`, a static method that reads every result page (the API returns at most `page_size` rows per call) and returns column names and rows.
- Changes: the `create_event(..., job_id: Optional[Union[int, str]] = None, ...)` parameter now also accepts a string, because Query Service job IDs are strings.

- [ ] **Step 1: Write the failing tests**

```python
def test_query_service_execute_query_returns_dataframe(keboola_streamlit, query_service_env):
    fake_module, _, fake_client = _mock_query_service_module(columns=("ID", "NAME"), data=(("1", "a"), ("2", "b")))
    keboola_streamlit.create_event = MagicMock()
    query = 'SELECT "ID", "NAME" FROM "in.c-main"."users"'

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}):
        result = keboola_streamlit.query_service_execute_query(query)

    assert list(result.columns) == ["ID", "NAME"]
    assert result.values.tolist() == [["1", "a"], ["2", "b"]]
    fake_client.submit_job.assert_called_once_with(branch_id="678", workspace_id="12345", statements=[query])
    fake_client.get_job_results.assert_called_once_with("qs-job-1", "stmt-1", offset=0, page_size=1000)
    keboola_streamlit.create_event.assert_called_once_with(
        message="Streamlit App Query Service Query",
        event_type="keboola_data_app_query_service_query",
        event_data=f"Query: {query}",
        job_id="qs-job-1",
    )


def test_query_service_execute_query_reads_all_pages(keboola_streamlit, query_service_env):
    fake_module, _, fake_client = _mock_query_service_module()
    columns = [SimpleNamespace(name="N")]
    fake_client.get_job_results.side_effect = [
        SimpleNamespace(columns=columns, data=[[str(i)] for i in range(1000)]),
        SimpleNamespace(columns=columns, data=[["1000"], ["1001"]]),
    ]
    keboola_streamlit.create_event = MagicMock()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}):
        result = keboola_streamlit.query_service_execute_query("SELECT N FROM big")

    assert len(result) == 1002
    assert result["N"].iloc[-1] == "1001"
    assert fake_client.get_job_results.call_args_list[1].kwargs == {"offset": 1000, "page_size": 1000}


def test_query_service_execute_query_without_return_df(keboola_streamlit, query_service_env):
    fake_module, _, fake_client = _mock_query_service_module()
    keboola_streamlit.create_event = MagicMock()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}):
        result = keboola_streamlit.query_service_execute_query("DELETE FROM t", return_df=False)

    assert result is None
    fake_client.get_job_results.assert_not_called()
    keboola_streamlit.create_event.assert_called_once_with(
        message="Streamlit App Query Service Query Executed",
        event_type="keboola_data_app_query_service_query",
        event_data="Query executed: DELETE FROM t",
        job_id="qs-job-1",
    )


def test_query_service_execute_query_no_columns(keboola_streamlit, query_service_env):
    fake_module, _, _ = _mock_query_service_module(columns=(), data=())
    keboola_streamlit.create_event = MagicMock()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}):
        result = keboola_streamlit.query_service_execute_query("CREATE TABLE t (a INT)")

    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_query_service_execute_query_failure(keboola_streamlit, query_service_env):
    fake_module, _, fake_client = _mock_query_service_module()
    fake_client.wait_for_job.side_effect = Exception("SQL compilation error")
    keboola_streamlit.create_event = MagicMock()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}), patch("streamlit.error") as mock_error:
        result = keboola_streamlit.query_service_execute_query("SELEC 1")

    assert result is None
    assert "SQL compilation error" in mock_error.call_args[0][0]
    keboola_streamlit.create_event.assert_called_once_with(
        message="Streamlit App Query Service Query Failed",
        event_type="keboola_data_app_query_service_query_failed",
        event_data="Query: SELEC 1\nError: SQL compilation error",
        job_id="qs-job-1",
    )


def test_query_service_execute_query_submit_failure(keboola_streamlit, query_service_env):
    fake_module, _, fake_client = _mock_query_service_module()
    fake_client.submit_job.side_effect = Exception("403 Forbidden")
    keboola_streamlit.create_event = MagicMock()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}), patch("streamlit.error"):
        result = keboola_streamlit.query_service_execute_query("SELECT 1")

    assert result is None
    keboola_streamlit.create_event.assert_called_once_with(
        message="Streamlit App Query Service Query Failed",
        event_type="keboola_data_app_query_service_query_failed",
        event_data="Query: SELECT 1\nError: 403 Forbidden",
        job_id=None,
    )


def test_query_service_execute_query_without_workspace(keboola_streamlit, monkeypatch):
    monkeypatch.delenv("KBC_WORKSPACE_MANIFEST_PATH", raising=False)
    fake_module, fake_client_cls, _ = _mock_query_service_module()
    keboola_streamlit.create_event = MagicMock()

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}), patch("streamlit.error") as mock_error:
        result = keboola_streamlit.query_service_execute_query("SELECT 1")

    assert result is None
    assert "KBC_WORKSPACE_MANIFEST_PATH" in mock_error.call_args[0][0]
    fake_client_cls.assert_not_called()
    keboola_streamlit.create_event.assert_not_called()


def test_query_service_execute_query_without_extra(keboola_streamlit, query_service_env):
    with patch.dict(sys.modules, {"keboola_query_service": None}):
        with pytest.raises(ImportError, match=r"keboola-streamlit\[query-service\]"):
            keboola_streamlit.query_service_execute_query("SELECT 1")


@pytest.mark.parametrize(
    "headers, expected_user",
    [
        ({"X-Kbc-User-Email": "jane@example.com", "Origin": "https://app-123.hub.keboola.com"}, "jane@example.com"),
        ({}, "Unknown"),
    ],
)
def test_query_service_execute_query_event_contains_user(keboola_streamlit, query_service_env, headers, expected_user):
    fake_module, _, _ = _mock_query_service_module()
    keboola_streamlit.set_dev_mockup_headers(headers)

    with patch.dict(sys.modules, {"keboola_query_service": fake_module}), patch("requests.post") as mock_post:
        result = keboola_streamlit.query_service_execute_query("SELECT 1")

    assert not result.empty
    params = mock_post.call_args.kwargs["json"]["params"]
    assert params["user"] == expected_user
    assert params["event_type"] == "keboola_data_app_query_service_query"
    assert params["event_job_id"] == "qs-job-1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_keboola_streamlit.py -k query_service_execute_query -v`
Expected: FAIL with `AttributeError: ... no attribute 'query_service_execute_query'`.

- [ ] **Step 3: Write the implementation**

Add `Union` to the `typing` import. Change the `create_event` signature and docstring line:

```python
        job_id: Optional[Union[int, str]] = None,
```

```python
            job_id (Optional[Union[int, str]]): The Storage or Query Service job ID for the event.
```

Add at the end of the class:

```python
    def query_service_execute_query(self, query: str, return_df: bool = True) -> Optional[pd.DataFrame]:
        """
        Executes a SQL query through Query Service on the workspace provisioned for the data app.

        Args:
            query (str): The SQL query to execute.
            return_df (bool): Flag to indicate whether to return the result as a Pandas DataFrame. Defaults to True.

        Returns:
            Optional[pd.DataFrame]: The query results if return_df is True, otherwise None. Values are returned
            as Query Service sends them, without type conversion.
        """
        try:
            workspace_id = self._read_workspace_id()
            branch_id = self._require_env("BRANCH_ID")
            client = self._get_query_service_client()
        except ImportError:
            raise
        except Exception as e:
            self._show_query_service_error(e)
            return None

        job_id: Optional[str] = None
        try:
            job_id = client.submit_job(branch_id=branch_id, workspace_id=workspace_id, statements=[query])
            status = client.wait_for_job(job_id)
            if not return_df:
                self.create_event(
                    message="Streamlit App Query Service Query Executed",
                    event_type="keboola_data_app_query_service_query",
                    event_data=f"Query executed: {query}",
                    job_id=job_id,
                )
                return None
            columns, rows = self._fetch_query_service_results(client, job_id, status.statements[0].id)
            self.create_event(
                message="Streamlit App Query Service Query",
                event_type="keboola_data_app_query_service_query",
                event_data=f"Query: {query}",
                job_id=job_id,
            )
            return pd.DataFrame(rows, columns=columns)
        except Exception as e:
            self._show_query_service_error(e)
            self.create_event(
                message="Streamlit App Query Service Query Failed",
                event_type="keboola_data_app_query_service_query_failed",
                event_data=f"Query: {query}\nError: {e}",
                job_id=job_id,
            )
        return None

    @staticmethod
    def _fetch_query_service_results(
        client: QueryServiceClient, job_id: str, statement_id: str
    ) -> Tuple[List[str], List[List[Any]]]:
        page_size = 1000
        rows: List[List[Any]] = []
        while True:
            page = client.get_job_results(job_id, statement_id, offset=len(rows), page_size=page_size)
            rows.extend(page.data)
            if len(page.data) < page_size:
                return [column.name for column in page.columns], rows

    @staticmethod
    def _show_query_service_error(error: Exception) -> None:
        logging.error(f"An error occurred while executing Query Service query: {error}")
        st.error(f"An error occurred while executing Query Service query: {error}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_keboola_streamlit.py -v`
Expected: all PASS, including the existing `create_event` / snowflake tests.

- [ ] **Step 5: Run lint**

Run: `uv run pre-commit run --all-files`
Expected: all hooks pass.

- [ ] **Step 6: Commit**

```bash
git add src/keboola_streamlit/keboola_streamlit.py tests/test_keboola_streamlit.py
git commit -m "feat: execute Query Service queries on operator workspace with user events"
```

---

### Task 4: CI, docs and version

**Files:**
- Modify: `.github/workflows/publish.yml`
- Modify: `README.md` (new section after "Snowflake Integration")
- Modify: `CLAUDE.md` (Architecture section)
- Modify: `pyproject.toml` (`version`)

**Interfaces:**
- Consumes: `query_service_execute_query` (Task 3), extra `query-service` (Task 1).

- [ ] **Step 1: Test every extra in CI**

In `.github/workflows/publish.yml`, rename the `test-snowflake-extra` job to `test-extras` and change its install step:

```yaml
      - name: Install dependencies with all extras
        run: uv sync --all-extras
```

Replace `- test-snowflake-extra` with `- test-extras` in the `needs` lists of `publish-event-info` and `publish`.

Run: `uv sync --all-extras && uv run pytest`
Expected: all PASS with the real `keboola-query-service` installed.

- [ ] **Step 2: Document it in `README.md`**

Add the `query-service` extra to the Installation section:

````markdown
If you query the data app workspace through [Query Service](#query-service-integration), install the
`query-service` extra:

```bash
pip install keboola-streamlit[query-service]
```
````

Add a new section after "Snowflake Integration":

````markdown
### Query Service Integration

> Requires the `query-service` extra: `pip install keboola-streamlit[query-service]`

Turn on the workspace in the data app configuration (`runtime.workspace.enabled: true`). Keboola then creates a
read-only workspace for the app and passes it to the app. No Snowflake credentials are needed in secrets. The library
reads these environment variables, which the platform sets:

- `KBC_WORKSPACE_MANIFEST_PATH` – path to a JSON file with the `workspaceId`
- `QUERY_SERVICE_URL`
- `BRANCH_ID`

```python
df = keboola.query_service_execute_query(query='SELECT * FROM "in.c-main"."users" LIMIT 100')
```

Each query creates a Storage event with the user email, the query text and the Query Service job ID. The event type
is `keboola_data_app_query_service_query` for a successful query and `keboola_data_app_query_service_query_failed` for
a failed one; the failed event also holds the error message. Values in the DataFrame are returned as Query Service sends them, with no
type conversion.

For local development, write `{"workspaceId": "<id>"}` to a file and set the three variables yourself.
````

- [ ] **Step 3: Update `CLAUDE.md`**

Add this bullet to the Architecture section, after the "Optional Snowflake extra" bullet:

```markdown
- **Optional Query Service extra.** `query_service_execute_query` uses the read-only workspace that keboola-operator provisions. The workspace ID comes from the JSON file at `KBC_WORKSPACE_MANIFEST_PATH`; `QUERY_SERVICE_URL` and `BRANCH_ID` come from env vars set by sandboxes-service. The library loads `keboola_query_service` lazily and caches the client on the instance. It logs a `keboola_data_app_query_service_query` event (or `..._failed` once the query reached Query Service), because Query Service does not record the user.
```

Also change the `uv sync --extra snowflake` line in Commands to `uv sync --all-extras    # also install optional extras (snowflake, query-service)`.

- [ ] **Step 4: Bump the version**

In `pyproject.toml`: `version = "v0.3.0"`.

- [ ] **Step 5: Run the CI checks locally**

Run:
```bash
uv run pre-commit run --all-files
uv run pytest
uv build --wheel && uv run --isolated --with dist/*.whl python -c "from keboola_streamlit import KeboolaStreamlit; print('ok')"
```
Expected: hooks pass, tests pass, and `ok` is printed, which shows the base package still imports without extras.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/publish.yml README.md CLAUDE.md pyproject.toml uv.lock
git commit -m "docs: document Query Service workspace support and test all extras in CI"
```

---

## Out of scope

- A `query_service_read_table(table_id)` helper. Table quoting differs between Snowflake and BigQuery workspaces.
- Converting DataFrame column types based on `Column.type`.
- Async Query Service calls and session reuse (`session_id`).
