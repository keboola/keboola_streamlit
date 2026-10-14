import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

# workaround to import module from parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from keboola_streamlit._query_service_types import query_result_to_dataframe  # noqa: E402


def _convert(type_name, values):
    columns = [SimpleNamespace(name="COL", type=type_name)]
    return query_result_to_dataframe(columns, [[value] for value in values])["COL"]


@pytest.mark.parametrize(
    "type_name, values, expected_dtype, expected_values",
    [
        ("FIXED", ["1", "2", None], "Int64", [1, 2, pd.NA]),
        ("NUMBER(38,0)", ["10", "-3"], "Int64", [10, -3]),
        ("INT64", ["7"], "Int64", [7]),
        ("fixed", ["1.50", "2.25"], "float64", [1.5, 2.25]),
        ("NUMERIC", ["1.5", None], "float64", [1.5, None]),
        ("REAL", ["1", "2.5"], "float64", [1.0, 2.5]),
        ("FLOAT64", ["3.25"], "float64", [3.25]),
        ("DOUBLE PRECISION", ["1e3"], "float64", [1000.0]),
        ("BOOLEAN", ["true", "FALSE", None], "boolean", [True, False, pd.NA]),
        ("BOOL", ["1", "0"], "boolean", [True, False]),
    ],
)
def test_numbers_and_booleans(type_name, values, expected_dtype, expected_values):
    result = _convert(type_name, values)

    assert str(result.dtype) == expected_dtype
    expected = pd.Series(expected_values, dtype=expected_dtype, name="COL")
    pd.testing.assert_series_equal(result, expected)


def test_snowflake_date_from_epoch_days():
    result = _convert("DATE", ["19753", "-1", None])

    assert result.dtype == "datetime64[ns]"
    assert result.iloc[0] == pd.Timestamp("2024-01-31")
    assert result.iloc[1] == pd.Timestamp("1969-12-31")
    assert pd.isna(result.iloc[2])


def test_snowflake_naive_timestamp_from_epoch_seconds_keeps_nanoseconds():
    result = _convert("TIMESTAMP_NTZ", ["1706696430.123456789", "-1.5", None])

    assert result.dtype == "datetime64[ns]"
    assert result.iloc[0] == pd.Timestamp("2024-01-31 10:20:30.123456789")
    assert result.iloc[1] == pd.Timestamp("1969-12-31 23:59:58.5")
    assert pd.isna(result.iloc[2])


@pytest.mark.parametrize(
    "type_name, value",
    [
        ("TIMESTAMP_TZ", "1706696430.000000000 1500"),
        ("TIMESTAMP_LTZ", "1706696430.000000000"),
    ],
)
def test_snowflake_timezone_timestamp_from_epoch_seconds_is_utc(type_name, value):
    result = _convert(type_name, [value])

    assert str(result.dtype) == "datetime64[ns, UTC]"
    assert result.iloc[0] == pd.Timestamp("2024-01-31 10:20:30", tz="UTC")


def test_bigquery_timestamp_is_utc():
    result = _convert("TIMESTAMP", ["2026-10-12 04:05:13 +0000 UTC", "2026-10-12 04:05:13.25 +0000 UTC", None])

    assert str(result.dtype) == "datetime64[ns, UTC]"
    assert result.iloc[0] == pd.Timestamp("2026-10-12 04:05:13", tz="UTC")
    assert result.iloc[1] == pd.Timestamp("2026-10-12 04:05:13.25", tz="UTC")
    assert pd.isna(result.iloc[2])


def test_date():
    result = _convert("DATE", ["2024-01-31", None])

    assert result.dtype == "datetime64[ns]"
    assert result.iloc[0] == pd.Timestamp("2024-01-31")
    assert pd.isna(result.iloc[1])


@pytest.mark.parametrize("type_name", ["TIMESTAMP_NTZ", "TIMESTAMP_NTZ(9)", "DATETIME", "TIMESTAMP"])
def test_naive_timestamp(type_name):
    result = _convert(type_name, ["2024-01-31 10:20:30.123", "2024-02-01 00:00:00"])

    assert result.dtype == "datetime64[ns]"
    assert result.iloc[0] == pd.Timestamp("2024-01-31 10:20:30.123")


@pytest.mark.parametrize("type_name", ["TIMESTAMP_TZ", "TIMESTAMP_LTZ"])
def test_timezone_timestamp_is_converted_to_utc(type_name):
    result = _convert(type_name, ["2024-01-31 10:00:00 +01:00", "2024-01-31 10:00:00 -02:00"])

    assert str(result.dtype) == "datetime64[ns, UTC]"
    assert result.iloc[0] == pd.Timestamp("2024-01-31 09:00:00", tz="UTC")
    assert result.iloc[1] == pd.Timestamp("2024-01-31 12:00:00", tz="UTC")


def test_timestamp_with_mixed_offsets_falls_back_to_utc():
    result = _convert("TIMESTAMP", ["2024-01-31T10:00:00Z", "2024-01-31 10:00:00 +01:00"])

    assert str(result.dtype) == "datetime64[ns, UTC]"


def test_timestamp_falls_back_to_utc_when_pandas_rejects_mixed_offsets():
    real_to_datetime = pd.to_datetime

    def to_datetime_like_pandas_3(values, utc=False, **kwargs):
        if not utc:
            raise ValueError("Mixed timezones detected. Pass utc=True in to_datetime")
        return real_to_datetime(values, utc=utc, **kwargs)

    with patch("pandas.to_datetime", side_effect=to_datetime_like_pandas_3):
        result = _convert("TIMESTAMP", ["2024-01-31T10:00:00Z", "2024-01-31 10:00:00 +01:00"])

    assert str(result.dtype) == "datetime64[ns, UTC]"


@pytest.mark.parametrize(
    "type_name, values",
    [
        ("VARCHAR", ["001", "2"]),
        ("TEXT", ["a"]),
        ("VARIANT", ['{"a": 1}']),
        ("TIME", ["10:20:30"]),
        ("BINARY", ["0A0B"]),
        ("GEOGRAPHY", ["POINT(1 2)"]),
        ("", ["1"]),
        (None, ["1"]),
    ],
)
def test_other_types_stay_strings(type_name, values):
    result = _convert(type_name, values)

    assert result.tolist() == values


@pytest.mark.parametrize(
    "type_name, values",
    [
        ("NUMBER", ["1", "not a number"]),
        ("BOOLEAN", ["true", "maybe"]),
        ("DATE", ["2024-13-45"]),
        ("DATE", ["19753", "2024-01-31"]),
        ("TIMESTAMP_NTZ", ["1706696430.5", "yesterday"]),
        ("DATE", ["99999999"]),
    ],
)
def test_unexpected_values_stay_strings(type_name, values):
    result = _convert(type_name, values)

    assert result.tolist() == values


@pytest.mark.parametrize("values", [["9007199254740993", None], ["9007199254740993", "1"]])
def test_large_integer_keeps_precision(values):
    result = _convert("NUMBER(38,0)", values)

    assert str(result.dtype) == "Int64"
    assert result.iloc[0] == 9007199254740993


def test_integer_too_large_for_int64_stays_string_to_keep_precision():
    result = _convert("NUMBER(38,0)", ["99999999999999999999"])

    assert result.tolist() == ["99999999999999999999"]


def test_duplicate_column_names_are_converted_by_position():
    columns = [SimpleNamespace(name="ID", type="NUMBER"), SimpleNamespace(name="ID", type="VARCHAR")]

    result = query_result_to_dataframe(columns, [["1", "1"], ["2", "2"]])

    assert list(result.columns) == ["ID", "ID"]
    assert str(result.dtypes.iloc[0]) == "Int64"
    assert result.iloc[:, 1].tolist() == ["1", "2"]


def test_empty_result_keeps_columns():
    columns = [SimpleNamespace(name="ID", type="NUMBER"), SimpleNamespace(name="CREATED", type="DATE")]

    result = query_result_to_dataframe(columns, [])

    assert list(result.columns) == ["ID", "CREATED"]
    assert result.empty


def test_no_columns():
    result = query_result_to_dataframe([], [])

    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_convert_types_disabled_keeps_strings():
    columns = [SimpleNamespace(name="ID", type="NUMBER")]

    result = query_result_to_dataframe(columns, [["1"]], convert_types=False)

    assert result["ID"].tolist() == ["1"]
