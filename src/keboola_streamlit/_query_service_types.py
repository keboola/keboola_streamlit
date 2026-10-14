from __future__ import annotations

import re
import warnings
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
# Dates must start with YYYY-MM-DD, so epoch numbers are never read as dates.
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
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
            return pd.to_numeric(values).astype("float64")
        if base_type in BOOLEAN_TYPES:
            return _to_boolean(values)
        if base_type in DATE_TYPES or base_type in TIMESTAMP_TYPES:
            return _to_datetime(values, utc=False)
        if base_type in UTC_TIMESTAMP_TYPES:
            return _to_datetime(values, utc=True)
    except (ValueError, TypeError, OverflowError):
        pass
    return values


def _base_type(type_name: Optional[str]) -> str:
    """Returns the lower-case type name without size and precision, e.g. 'NUMBER(38,0)' -> 'number'."""
    parts = re.split(r"[\s(]", (type_name or "").strip().lower(), maxsplit=1)
    return parts[0]


def _to_number(values: pd.Series) -> pd.Series:
    if all(INTEGER_PATTERN.match(str(value)) for value in values.dropna()):
        # Built from Python ints: pd.to_numeric goes through float64 when there are NULLs and rounds
        # integers above 2**53. Values outside the int64 range raise here and stay strings.
        integers = [pd.NA if pd.isna(value) else int(value) for value in values]
        return pd.Series(pd.array(integers, dtype="Int64"), index=values.index, name=values.name)
    return pd.to_numeric(values).astype("float64")


def _to_boolean(values: pd.Series) -> pd.Series:
    def convert(value: Any) -> Any:
        if pd.isna(value):
            return pd.NA
        return BOOLEAN_VALUES[str(value).strip().lower()]

    try:
        return values.map(convert).astype("boolean")
    except KeyError as e:
        raise ValueError(f"Unexpected boolean value {e}") from e


def _to_datetime(values: pd.Series, utc: bool) -> pd.Series:
    if not all(ISO_DATE_PATTERN.match(str(value)) for value in values.dropna()):
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
