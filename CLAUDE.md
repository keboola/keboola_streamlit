# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`keboola_streamlit` is a small PyPI library (`pip install keboola-streamlit`) that wraps the Keboola Storage API (via `kbcstorage`) for use inside Streamlit data apps. It ships a single public class, `KeboolaStreamlit`, in `src/keboola_streamlit/keboola_streamlit.py`. Query Service type conversion lives in `src/keboola_streamlit/_query_service_types.py`. The README is the user-facing API doc, so update it whenever public method signatures change.

## Commands

The project uses `uv` (build backend `uv_build`), Python >=3.10. CI tests on 3.10 and 3.13.

```bash
uv sync                      # install deps + dev group
uv sync --all-extras         # also install the optional extras (snowflake, query-service)
uv run pytest                                              # all tests
uv run pytest tests/test_keboola_streamlit.py::test_read_table   # single test
uv run pre-commit run --all-files                          # flake8 + ruff + ruff-format + whitespace/yaml hooks
uv build --wheel && uv run --isolated --with dist/*.whl python -c "from keboola_streamlit import KeboolaStreamlit"  # CI smoke test
```

Line length is 120 (ruff and flake8).

## Architecture

- **One class, Streamlit-coupled.** `KeboolaStreamlit` calls `st.*` directly: errors go to `st.error` (and `logging.error`), and `auth_check` calls `st.stop()`. Most public methods swallow exceptions and return an empty `pd.DataFrame()` / `None`, and don't raise. The exceptions are `write_table`, which returns the error `repr` (or raises when `raise_on_error=True`), and `snowflake_create_session_object`, which raises `ImportError` when the extra is missing.
- **Request headers drive auth and events.** `_get_headers()` returns `st.context.headers`, or `dev_mockup_headers` when set via `set_dev_mockup_headers()` for local dev. The Keboola data-app proxy injects `X-Kbc-User-Roles` / `X-Kbc-User-Email` / `X-Kbc-User-Id`, which `auth_check`, `logout_button` (links to `/_proxy/sign_out`) and `create_event` read. Events store the email as `params.user` and the identity provider's user ID (OIDC `sub`, GitHub login; empty for password/no-auth apps) as `params.user_id`.
- **Every data operation emits a Storage event.** `read_table`, `write_table` and all `snowflake_*` methods call `create_event` (POST `/v2/storage/events`, component `keboola.data-apps`) with a `keboola_data_app_*` `event_type`. Storage read/write also look up the related job ID through `_get_event_job_id`, which scans `client.jobs.list()`. Keep this pattern when adding operations.
- **Temp files in the CWD.** `read_table` exports to `./<table_name>` and renames it to `<table_name>.csv`. `write_table` writes `<name>_<uuid>.csv.gz` and removes it in `finally`.
- **`add_table_selection`** is a multi-step Streamlit form flow (connect → bucket → table) whose state lives in `st.session_state` keys (`kbc_storage_client`, `bucket_list`, `selected_bucket`, `selected_table_id`, `tables_data` cache).
- **Optional Snowflake extra.** `snowflake.snowpark` is imported only under `TYPE_CHECKING` at module level, and lazily inside `snowflake_create_session_object`. A `ModuleNotFoundError` for `snowflake`/`cryptography` becomes an `ImportError` that tells the user to install `keboola-streamlit[snowflake]`; other missing modules are re-raised unchanged (a test covers this). The base package must stay importable without the extra, which the CI wheel smoke test checks. Credentials come from `st.secrets` (`SNOWFLAKE_*`). Key-pair auth wins over password, and the PEM key is converted to DER/PKCS8. The other `snowflake_*` methods accept `Optional[Session]` and handle `None` (a failed session creation) by logging an error.
- **Optional Query Service extra.** `query_service_execute_query` queries the workspace that keboola-operator provisions for apps with Storage Access. It reads the whole project Storage and can write only to output-mapping tables with `unload_strategy: "direct-grant"`. The workspace ID comes from the JSON file at `KBC_WORKSPACE_MANIFEST_PATH` (re-read on every call), falling back to `WORKSPACE_ID` (set by the Python/JS runtime image and by the deprecated `dataApp.secrets.WORKSPACE_ID` path). `BRANCH_ID` must be numeric. `QUERY_SERVICE_URL` falls back to the Storage API index. Query Service records no end-user identity, so Storage events are the audit record: the `..._query` event (`request_id`) is written before submit and the query is not sent if it fails; `..._query_submitted` links the `request_id` to the Query Service job ID; `..._query_completed` adds the Snowflake query ID as `backend_query_id`; failures (including a job canceled elsewhere, which the SDK's `wait_for_job` returns without raising) write `..._failed` (error and `backend_query_id` when known) after the job is cancelled; a job that finished just before the cancel follows the success path. Query Service events are written before any `st.error`, because `st.error` raises when a Streamlit rerun is pending. Submits use a client with `max_retries=0`, since an SDK retry could run a write twice; only the 403 workspace-credentials error is retried. Events never hold the SQL text (personal data); the Snowflake query ID is the link to it. `return_df=False` returns `rows_affected` (None on failure) and turns on `refresh_metadata_on_success` by default, so Storage exports see the change. Clients are cached at module level per URL, token and retry setting, because Streamlit creates a new instance on every rerun; tests clear `_QUERY_SERVICE_CLIENTS` and `_QUERY_SERVICE_URLS`. Query Service returns every value as a string; `_query_service_types.py` converts by column type. Snowflake sends `DATE` as epoch days and timestamps as epoch seconds (`TIMESTAMP_TZ` adds an offset after a space), BigQuery sends ISO dates and `2026-10-12 04:05:13 +0000 UTC` timestamps. The reference counterpart for this behavior is the React/JS data app template in keboola/ai-kit (`plugins/dataapp-developer/skills/dataapp-development/templates/react-vite-app/server/kbcQuery.ts`); the Streamlit template there is outdated.

## Tests

`tests/test_keboola_streamlit.py` puts `src/` on `sys.path` and mocks everything: the Storage client is replaced through the name-mangled `_KeboolaStreamlit__client`, Snowpark through `patch.dict(sys.modules, ...)`, and secrets through `patch("streamlit.secrets")`. No network or Keboola credentials are needed. Query Service tests live in `tests/test_query_service.py` (fake SDK), `tests/test_query_service_sdk.py` (real SDK over `httpx.MockTransport`, skipped without the extra) and `tests/test_query_service_types.py`. The suite runs both without extras and with all extras in CI.

## Release

`.github/workflows/publish.yml` runs on every push. It publishes to PyPI only for a `v*` tag on the default branch. Bump `version` in `pyproject.toml` (format `vX.Y.Z`) before tagging.
