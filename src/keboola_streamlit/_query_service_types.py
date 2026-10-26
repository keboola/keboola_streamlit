from __future__ import annotations

import re
import warnings
from datetime import date, datetime, timedelta, timezone
from typing import Any, List, Optional, Protocol, Sequence

import pandas as pd

INTEGER_TYPES = frozenset({"int", "integer", "bigint", "smallint", "tinyint", "byteint", "int64"})
DECIMAL_TYPES = frozenset({"fixed", "number", "numeric", "decimal", "bignumeric", "bigdecimal"})
FLOAT_TYPES = frozenset({"real", "float", "float4", "float8", "double", "float64"})
BOOLEAN_TYPES = frozenset({"boolean", "bool"})
DATE_TYPES = frozenset({"date"})
TIMESTAMP_TYPES = frozenset({"timestamp", "timestamp_ntz", "datetime"})
UTC_TIMESTAMP_TYPES = frozenset({"timestamp_ltz", "timestamp_tz"})

BOOLEAN_VALUES = {"true": True, "1": True, "false": False, "0": False}
INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
# Snowflake sends dates as days and timestamps as seconds since 1970-01-01, followed by the time zone offset
# for TIMESTAMP_TZ. BigQuery sends ISO dates and timestamps like "2026-10-12 04:05:13 +0000 UTC".
EPOCH_DAYS_PATTERN = re.compile(r"^-?\d+$")
EPOCH_SECONDS_PATTERN = re.compile(r"^-?\d+(\.\d+)?( \d+)?$")
BIGQUERY_TIMESTAMP_PATTERN = re.compile(r"^(\S+) (\S+) \+0000 UTC$")
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
NANOSECONDS_PER_DAY = 86_400 * 1_000_000_000
EPOCH_DATE = date(1970, 1, 1)
# pandas 2 guesses one format from the first value and rejects rows that differ (e.g. missing fractions).
DATETIME_OPTIONS = {"format": "ISO8601"} if int(pd.__version__.split(".")[0]) >= 2 else {}


class QueryServiceColumn(Protocol):
    name: str
    type: Optional[str]


def query_result_to_dataframe(
    columns: Sequence[QueryServiceColumn], rows: List[List[Any]], convert_types: bool = True
) -> pd.DataFrame:
    """
    Builds a DataFrame from a Query Service result. Query Service returns every value as a string,
    so columns are converted to pandas types based on the column type. A column whose values do not
    match its type stays as strings.
    """
    df = pd.DataFrame(rows, columns=[column.name for column in columns])
    if not convert_types or not columns:
        return df
    converted = pd.concat(
        [_convert_column(df.iloc[:, index], column.type) for index, column in enumerate(columns)],
        axis=1,
    )
    converted.columns = df.columns
    return converted


def _convert_column(values: pd.Series, type_name: Optional[str]) -> pd.Series:
    base_type = _base_type(type_name)
    try:
        if base_type in INTEGER_TYPES or base_type in DECIMAL_TYPES:
            return _to_number(values)
        if base_type in FLOAT_TYPES:
            return _to_float(values)
        if base_type in BOOLEAN_TYPES:
            return _to_boolean(values)
        if base_type in DATE_TYPES:
            return _to_date(values)
        if base_type in TIMESTAMP_TYPES:
            return _to_timestamp(values, utc=False)
        if base_type in UTC_TIMESTAMP_TYPES:
            return _to_timestamp(values, utc=True)
    except (ValueError, TypeError, OverflowError):
        pass
    return values


def _base_type(type_name: Optional[str]) -> str:
    """Returns the lower-case type name without size and precision, e.g. 'NUMBER(38,0)' -> 'number'."""
    parts = re.split(r"[\s(]", (type_name or "").strip().lower(), maxsplit=1)
    return parts[0]


def _all_match(values: pd.Series, pattern: re.Pattern[str]) -> bool:
    return all(pattern.match(str(value)) for value in values.dropna())


def _to_number(values: pd.Series) -> pd.Series:
    if _all_match(values, INTEGER_PATTERN):
        # Built from Python ints: pd.to_numeric goes through float64 when there are NULLs and rounds
        # integers above 2**53. Values outside the int64 range raise here and stay strings.
        integers = [pd.NA if pd.isna(value) else int(value) for value in values]
        return pd.Series(pd.array(integers, dtype="Int64"), index=values.index, name=values.name)
    return _to_float(values)


def _to_float(values: pd.Series) -> pd.Series:
    """Uses float() rather than pd.to_numeric, which rejects "NaN" and turns empty strings into NaN."""
    floats = [float("nan") if pd.isna(value) else float(str(value)) for value in values]
    return pd.Series(floats, dtype="float64", index=values.index, name=values.name)


def _to_boolean(values: pd.Series) -> pd.Series:
    def convert(value: Any) -> Any:
        if pd.isna(value):
            return pd.NA
        return BOOLEAN_VALUES[str(value).strip().lower()]

    try:
        return values.map(convert).astype("boolean")
    except KeyError as e:
        raise ValueError(f"Unexpected boolean value {e}") from e


def _to_date(values: pd.Series) -> pd.Series:
    if _all_match(values, EPOCH_DAYS_PATTERN):
        # Converted to nanoseconds, so dates get the same datetime64[ns] type as timestamps.
        nanoseconds = [pd.NA if pd.isna(value) else int(value) * NANOSECONDS_PER_DAY for value in values]
        try:
            return pd.to_datetime(
                pd.Series(nanoseconds, dtype="Int64", index=values.index, name=values.name), unit="ns"
            )
        except (OverflowError, ValueError, TypeError):
            # datetime64[ns] covers only the years 1677 to 2262, so e.g. 9999-12-31 becomes a date object.
            return values.map(lambda value: value if pd.isna(value) else EPOCH_DATE + timedelta(days=int(value)))
    return _parse_iso_datetime(values, utc=False)


def _to_timestamp(values: pd.Series, utc: bool) -> pd.Series:
    if _all_match(values, EPOCH_SECONDS_PATTERN):
        nanoseconds = [pd.NA if pd.isna(value) else _epoch_seconds_to_nanoseconds(str(value)) for value in values]
        try:
            return pd.to_datetime(
                pd.Series(nanoseconds, dtype="Int64", index=values.index, name=values.name), unit="ns", utc=utc
            )
        except (OverflowError, ValueError, TypeError):
            # Outside the datetime64[ns] range, so datetime objects with microsecond precision.
            return values.map(lambda value: value if pd.isna(value) else _epoch_seconds_to_datetime(str(value), utc))
    return _parse_iso_datetime(values.str.replace(BIGQUERY_TIMESTAMP_PATTERN, r"\1T\2Z", regex=True), utc)


def _epoch_seconds_to_nanoseconds(value: str) -> int:
    """Parses the seconds as text, because a float cannot hold nanoseconds of a current date."""
    seconds = value.split(" ")[0]
    whole, _, fraction = seconds.lstrip("-").partition(".")
    nanoseconds = int(whole) * 1_000_000_000 + int((fraction + "000000000")[:9])
    return -nanoseconds if seconds.startswith("-") else nanoseconds


def _epoch_seconds_to_datetime(value: str, utc: bool) -> datetime:
    seconds = value.split(" ")[0]
    whole, _, fraction = seconds.lstrip("-").partition(".")
    delta = timedelta(seconds=int(whole), microseconds=int((fraction + "000000")[:6]))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc if utc else None)
    return epoch - delta if seconds.startswith("-") else epoch + delta


def _parse_iso_datetime(values: pd.Series, utc: bool) -> pd.Series:
    if not _all_match(values, ISO_DATE_PATTERN):
        raise ValueError("Values are not ISO dates.")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            converted = pd.to_datetime(values, utc=utc, **DATETIME_OPTIONS)
        except ValueError:
            converted = None
        # Values with different UTC offsets give an object column in pandas 2 and an error in pandas 3.
        if converted is None or not pd.api.types.is_datetime64_any_dtype(converted):
            converted = pd.to_datetime(values, utc=True, **DATETIME_OPTIONS)
    return converted
