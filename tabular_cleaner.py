#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Supabase-ready tabular file cleaner — single-file distribution.

Dependencies (install once):
  pip install pandas openpyxl xlrd python-dateutil charset-normalizer typer

Usage:
  python tabular_cleaner.py path/to/file.xlsx
  python tabular_cleaner.py path/to/data.csv --preview --schema-out

Full help:
  python tabular_cleaner.py --help
"""

from __future__ import annotations

# --- stdlib ---
import csv
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Optional

# --- third party ---
import pandas as pd
import typer
from charset_normalizer import from_bytes
from dateutil import parser as date_parser

# =============================================================================
# utils
# =============================================================================

ZERO_WIDTH_AND_BOM = (
    "\ufeff",
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\u00a0",
    "\u202f",
    "\u2007",
    "\u2009",
)

_CTRL_EXCEPT_TAB_NEWLINE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_QUOTE_MAP = str.maketrans(
    {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u2032": "'",
        "\u2033": '"',
    }
)

_DASH_CHARS = (
    "\u2010",
    "\u2011",
    "\u2012",
    "\u2013",
    "\u2014",
    "\u2015",
    "\u2212",
    "\ufe58",
    "\ufe63",
    "\uff0d",
)

RESERVED_KEYWORDS = frozenset(
    {
        "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric",
        "authorization", "binary", "both", "case", "cast", "check", "collate", "column",
        "constraint", "create", "cross", "current_catalog", "current_date", "current_role",
        "current_schema", "current_time", "current_timestamp", "current_user", "default",
        "deferrable", "desc", "distinct", "do", "else", "end", "except", "false", "fetch",
        "for", "foreign", "from", "grant", "group", "having", "in", "initially", "intersect",
        "into", "lateral", "leading", "limit", "localtime", "localtimestamp", "not", "null",
        "offset", "on", "only", "or", "order", "placing", "primary", "references", "returning",
        "select", "session_user", "some", "symmetric", "table", "then", "to", "trailing",
        "true", "union", "unique", "user", "using", "variadic", "when", "where", "window", "with",
    }
)


def strip_invisible_and_nbsp(s: str) -> str:
    for ch in ZERO_WIDTH_AND_BOM:
        s = s.replace(ch, "")
    return s


def normalize_quotes_and_dashes(s: str) -> str:
    s = s.translate(_QUOTE_MAP)
    for d in _DASH_CHARS:
        s = s.replace(d, "-")
    return s


def strip_unsafe_control_chars(s: str) -> str:
    return _CTRL_EXCEPT_TAB_NEWLINE.sub("", s)


def ascii_snake_fragment(name: str) -> str:
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.lower()
    n = re.sub(r"[^a-z0-9]+", "_", n)
    n = re.sub(r"_+", "_", n).strip("_")
    return n


def postgres_safe_identifier(name: str) -> str:
    frag = ascii_snake_fragment(name)
    if not frag:
        return "col"
    if frag[0].isdigit():
        frag = "c_" + frag
    if frag in RESERVED_KEYWORDS:
        frag = frag + "_col"
    return frag


def safe_output_stem(input_path: Path) -> str:
    stem = input_path.stem
    frag = ascii_snake_fragment(stem)
    return frag or "data"


# =============================================================================
# readers
# =============================================================================

_log = logging.getLogger("tabular_cleaner")


@dataclass
class LoadMeta:
    encoding: str
    delimiter: str | None = None


def _read_file_bytes(path: Path) -> bytes:
    return path.read_bytes()


def detect_encoding(data: bytes, override: str | None) -> str:
    if override:
        return override
    for enc in ("utf-8-sig", "utf-8"):
        try:
            data.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    match = from_bytes(data).best()
    if match is not None:
        _log.info("Detected encoding via charset-normalizer: %s", match.encoding)
        return match.encoding
    return "utf-8"


def sniff_delimiter(sample_text: str) -> str:
    if not sample_text.strip():
        return ","
    try:
        dialect = csv.Sniffer().sniff(sample_text[:65536], delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        pass
    first_line = sample_text.splitlines()[0] if sample_text else ""
    counts = {",": first_line.count(","), ";": first_line.count(";"), "\t": first_line.count("\t")}
    best = max(counts, key=counts.get)
    if counts[best] > 0:
        return best
    return ","


def load_csv_tsv(path: Path, encoding: str | None) -> tuple[pd.DataFrame, LoadMeta]:
    raw = _read_file_bytes(path)
    enc = detect_encoding(raw, encoding)
    text = raw.decode(enc, errors="replace")
    suffix = path.suffix.lower()
    sep = "\t" if suffix == ".tsv" else sniff_delimiter(text[:65536])
    df = pd.read_csv(
        StringIO(text),
        sep=sep,
        engine="python",
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        quoting=csv.QUOTE_MINIMAL,
    )
    return df, LoadMeta(encoding=enc, delimiter=sep)


def load_excel(path: Path) -> tuple[pd.DataFrame, LoadMeta]:
    suffix = path.suffix.lower()
    engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
    df = pd.read_excel(path, engine=engine, dtype=str, keep_default_na=False, na_filter=False)
    return df, LoadMeta(encoding="binary", delimiter=None)


SUPPORTED_EXTENSIONS = frozenset({".csv", ".tsv", ".xlsx", ".xls"})


def load_tabular(path: Path, encoding: str | None) -> tuple[pd.DataFrame, LoadMeta]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension {ext!r}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if not path.is_file():
        raise FileNotFoundError(f"Input path is not a file: {path}")
    if ext in (".csv", ".tsv"):
        return load_csv_tsv(path, encoding)
    return load_excel(path)


# =============================================================================
# cleaners
# =============================================================================

_NULL_TOKENS = frozenset(
    {"", " ", "null", "n/a", "na", "none", "nan", "-", "--"}
)
_TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1"})
_FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0"})


@dataclass
class CleaningStats:
    renamed_columns: dict[str, str] = field(default_factory=dict)
    blank_headers_replaced: list[str] = field(default_factory=list)
    duplicate_columns_fixed: list[str] = field(default_factory=list)
    null_values_standardized: dict[str, int] = field(default_factory=dict)
    booleans_normalised: dict[str, int] = field(default_factory=dict)
    nonstandard_boolean_tokens: dict[str, list[str]] = field(default_factory=dict)
    rows_removed_empty: int = 0
    rows_removed_duplicate: int = 0


def _strip_header_cell(h: Any) -> str:
    if h is None or (isinstance(h, float) and pd.isna(h)):
        return ""
    s = str(h)
    s = strip_invisible_and_nbsp(s)
    return s.strip()


def normalize_headers(df: pd.DataFrame, stats: CleaningStats) -> pd.DataFrame:
    originals = list(df.columns)
    stripped = [_strip_header_cell(c) for c in originals]
    bases: list[str] = []
    for i, raw in enumerate(stripped):
        if not raw:
            col = f"column_{i + 1}"
            stats.blank_headers_replaced.append(col)
        else:
            col = ascii_snake_fragment(raw)
            if not col:
                col = f"column_{i + 1}"
                stats.blank_headers_replaced.append(col)
        bases.append(col)
    counts: dict[str, int] = {}
    final_names: list[str] = []
    for base in bases:
        counts[base] = counts.get(base, 0) + 1
        n = counts[base]
        if n == 1:
            final = base
        else:
            final = f"{base}_{n}"
            stats.duplicate_columns_fixed.append(final)
        final_names.append(final)
    out = df.copy()
    out.columns = final_names
    seen_key: dict[str, int] = {}
    rc: dict[str, str] = {}
    for i, (o, n) in enumerate(zip(originals, final_names)):
        raw = _strip_header_cell(o)
        key_base = raw if raw else f"(blank_header_{i + 1})"
        seen_key[key_base] = seen_key.get(key_base, 0) + 1
        key = f"{key_base} [#{seen_key[key_base]}]" if seen_key[key_base] > 1 else key_base
        rc[key] = n
    stats.renamed_columns = rc
    return out


def clean_string_value(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    s = str(val)
    s = strip_invisible_and_nbsp(s)
    s = strip_unsafe_control_chars(s)
    s = normalize_quotes_and_dashes(s)
    return s.strip()


def _is_null_token(s: str | None) -> bool:
    if s is None:
        return True
    t = s.strip().lower()
    return t in _NULL_TOKENS or t in ("null",)


def try_bool_token(s: str) -> bool | None:
    t = s.strip().lower()
    if t in _TRUE_TOKENS:
        return True
    if t in _FALSE_TOKENS:
        return False
    return None


def clean_all_string_cells(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].map(clean_string_value)
    return out


def standardize_nulls_and_booleans(df: pd.DataFrame, stats: CleaningStats) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        null_count = 0
        bool_count = 0
        weird: list[str] = []
        series = out[col]
        new_vals: list[Any] = []
        for val in series:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                new_vals.append(pd.NA)
                continue
            if not isinstance(val, str):
                val = str(val)
            if _is_null_token(val):
                new_vals.append(pd.NA)
                null_count += 1
                continue
            b = try_bool_token(val)
            if b is not None:
                new_vals.append(b)
                bool_count += 1
                continue
            tl = val.strip().lower()
            if tl in ("on", "off", "ok", "si", "no.") or "bool" in tl:
                weird.append(val[:50])
            new_vals.append(val)
        out[col] = new_vals
        if null_count:
            stats.null_values_standardized[str(col)] = null_count
        if bool_count:
            stats.booleans_normalised[str(col)] = bool_count
        if weird:
            stats.nonstandard_boolean_tokens[str(col)] = list(dict.fromkeys(weird))[:20]
    return out


def _cell_is_empty(v: Any) -> bool:
    if pd.isna(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def remove_empty_rows(df: pd.DataFrame, stats: CleaningStats) -> pd.DataFrame:
    before = len(df)
    keep = ~df.apply(lambda row: all(_cell_is_empty(v) for v in row), axis=1)
    out = df.loc[keep].reset_index(drop=True)
    stats.rows_removed_empty = before - len(out)
    return out


def dedupe_rows(df: pd.DataFrame, stats: CleaningStats) -> pd.DataFrame:
    before = len(df)
    out = df.drop_duplicates().reset_index(drop=True)
    stats.rows_removed_duplicate = before - len(out)
    return out


# =============================================================================
# normalisers
# =============================================================================

_EXCEL_ORIGIN = pd.Timestamp("1899-12-30")


@dataclass
class NormaliseStats:
    dates_normalised: dict[str, int] = field(default_factory=dict)
    dates_ambiguous: dict[str, list[str]] = field(default_factory=dict)
    possible_excel_serial_skipped: dict[str, int] = field(default_factory=dict)


_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
_DATE_HINTS = re.compile(r"(date|time|timestamp|created|updated|dob|birth)", re.I)


def _column_hints_date(name: str) -> bool:
    return bool(_DATE_HINTS.search(str(name)))


def excel_serial_to_datetime(serial: float) -> pd.Timestamp:
    return _EXCEL_ORIGIN + pd.Timedelta(days=serial)


def _looks_like_excel_serial(val: Any, col_name: str) -> bool:
    try:
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return False
            f = float(s)
        elif isinstance(val, (int, float)):
            f = float(val)
        else:
            return False
    except (TypeError, ValueError):
        return False
    if not (1 <= f <= 100_000):
        return False
    frac = f % 1
    hinted = _column_hints_date(col_name)
    if hinted and f > 20000:
        return True
    if frac > 1e-6 and f > 20000:
        return True
    if frac > 1e-6:
        return True
    if hinted and 30000 <= f <= 60000:
        return True
    return False


def try_parse_iso_or_strict(s: str) -> tuple[datetime | None, float]:
    s = s.strip()
    if not s:
        return None, 0.0
    if _ISO_DATE_RE.match(s):
        try:
            dt = date_parser.isoparse(s.replace("Z", "+00:00"))
            return dt, 0.95
        except (ValueError, TypeError):
            return None, 0.0
    return None, 0.0


def try_parse_dateutil(s: str, dayfirst: bool = False) -> tuple[datetime | None, float]:
    try:
        dt = date_parser.parse(s, dayfirst=dayfirst, fuzzy=False)
        return dt, 0.55
    except (ValueError, TypeError, OverflowError):
        return None, 0.0


def normalise_dates_and_excel_serials(
    df: pd.DataFrame,
    stats: NormaliseStats,
    *,
    date_confidence_threshold: float = 0.75,
) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        col_name = str(col)
        converted = 0
        ambiguous_examples: list[str] = []
        skipped_serial = 0
        new_series: list[Any] = []
        for val in out[col]:
            if pd.isna(val) or val is None:
                new_series.append(pd.NA)
                continue
            if isinstance(val, bool):
                new_series.append(val)
                continue
            if isinstance(val, (pd.Timestamp, datetime)):
                dt = val if isinstance(val, datetime) else val.to_pydatetime()
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                new_series.append(dt.strftime("%Y-%m-%dT%H:%M:%S%z"))
                converted += 1
                continue
            s = str(val).strip()
            if not s:
                new_series.append(pd.NA)
                continue
            if _looks_like_excel_serial(val, col_name):
                try:
                    f = float(str(val).strip())
                    ts = excel_serial_to_datetime(f)
                    new_series.append(ts.strftime("%Y-%m-%dT%H:%M:%S"))
                    converted += 1
                    continue
                except (ValueError, OverflowError):
                    skipped_serial += 1
                    new_series.append(s)
                    continue
            dt, conf = try_parse_iso_or_strict(s)
            if dt is None:
                dt, conf2 = try_parse_dateutil(s, dayfirst=False)
                conf = max(conf, conf2)
            if dt is not None and conf >= date_confidence_threshold:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                new_series.append(dt.strftime("%Y-%m-%dT%H:%M:%S%z"))
                converted += 1
            elif dt is not None:
                ambiguous_examples.append(s[:80])
                new_series.append(s)
            else:
                new_series.append(s)
        out[col] = new_series
        if converted:
            stats.dates_normalised[col_name] = converted
        if ambiguous_examples:
            stats.dates_ambiguous[col_name] = list(dict.fromkeys(ambiguous_examples))[:15]
        if skipped_serial:
            stats.possible_excel_serial_skipped[col_name] = skipped_serial
    return out


# =============================================================================
# type_inference
# =============================================================================

_PG_INT_MAX = 2**63 - 1
_PG_INT_MIN = -(2**63)
_DIGITS_ONLY = re.compile(r"^[0-9]+$")
_LEADING_ZERO_INT = re.compile(r"^0\d+$")
_DECIMAL = re.compile(r"^-?\d+\.\d+$")
_INT_STR = re.compile(r"^-?\d+$")
_ISO_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass
class InferenceResult:
    recommended_types: dict[str, str] = field(default_factory=dict)
    type_confidence: dict[str, float] = field(default_factory=dict)
    columns_with_mixed_types: list[str] = field(default_factory=list)
    import_warnings: list[str] = field(default_factory=list)


def _sample_values(series: pd.Series, max_rows: int = 10000) -> list[Any]:
    s = series.dropna().head(max_rows)
    return [x for x in s.tolist() if not (isinstance(x, str) and x.strip() == "")]


def _ratio(pred: int, total: int) -> float:
    return 0.0 if total == 0 else pred / total


def _looks_like_id_column(name: str) -> bool:
    n = str(name).lower()
    return any(
        x in n
        for x in ("id", "uuid", "phone", "postal", "zip", "sku", "account", "iban", "ref", "code")
    )


def infer_column_type(
    col: str,
    values: list[Any],
    *,
    force_text: bool,
) -> tuple[str, float, bool]:
    if force_text:
        return "text", 1.0, False
    if not values:
        return "text", 0.5, False
    total = len(values)
    bool_ok = 0
    for v in values:
        if isinstance(v, bool):
            bool_ok += 1
        elif isinstance(v, str):
            t = v.strip().lower()
            if t in _TRUE_TOKENS or t in _FALSE_TOKENS:
                bool_ok += 1
    if _ratio(bool_ok, total) >= 0.98 and bool_ok == total:
        return "boolean", 0.92, False
    if bool_ok > 0 and bool_ok < total:
        return "text", 0.4, True
    leading_zero_hits = sum(
        1 for v in values if isinstance(v, str) and _LEADING_ZERO_INT.match(v.strip())
    )
    if leading_zero_hits > 0 and (
        _looks_like_id_column(col) or _ratio(leading_zero_hits, total) >= 0.5
    ):
        return "text", 0.9, False
    ts_ok = 0
    date_only_ok = 0
    for v in values:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if not s:
            continue
        if _ISO_LIKE.match(s) and "T" in s:
            ts_ok += 1
        elif _ISO_LIKE.match(s) and len(s) <= 10:
            date_only_ok += 1
    if ts_ok >= total * 0.9 and ts_ok > 0:
        return "timestamptz", 0.85, ts_ok < total
    if date_only_ok >= total * 0.9 and date_only_ok > 0 and ts_ok == 0:
        return "date", 0.82, date_only_ok < total
    parsed_dates = 0
    for v in values:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if _DIGITS_ONLY.match(s) and not _ISO_LIKE.match(s):
            continue
        if _LEADING_ZERO_INT.match(s):
            continue
        try:
            date_parser.parse(s, fuzzy=False)
            parsed_dates += 1
        except (ValueError, TypeError, OverflowError):
            pass
    if parsed_dates >= total * 0.85 and parsed_dates > 0:
        return "timestamptz", 0.65, parsed_dates < total
    int_ok = 0
    dec_ok = 0
    leading_zero_hits = 0
    long_digit = 0
    for v in values:
        if isinstance(v, bool):
            continue
        s = str(v).strip()
        if _LEADING_ZERO_INT.match(s):
            leading_zero_hits += 1
        if _DIGITS_ONLY.match(s) and len(s) >= 10:
            long_digit += 1
        if _DECIMAL.match(s):
            dec_ok += 1
        elif _INT_STR.match(s):
            try:
                n = int(s)
                if _PG_INT_MIN <= n <= _PG_INT_MAX:
                    int_ok += 1
            except ValueError:
                pass
    if long_digit >= total * 0.7 or (_looks_like_id_column(col) and leading_zero_hits > 0):
        return "text", 0.88, False
    if dec_ok > 0 and dec_ok < total and int_ok + dec_ok < total:
        return "text", 0.45, True
    if dec_ok >= total * 0.9 and dec_ok > 0:
        return "numeric", 0.8, dec_ok < total
    if int_ok >= total * 0.9 and int_ok > 0 and leading_zero_hits == 0:
        return "bigint", 0.78, int_ok < total
    if int_ok > 0 and int_ok < total * 0.9:
        return "text", 0.5, True
    return "text", 0.7, False


def infer_frame(df: pd.DataFrame, *, force_text: bool = False) -> InferenceResult:
    res = InferenceResult()
    for col in df.columns:
        vals = _sample_values(df[col])
        pg_t, conf, mixed = infer_column_type(str(col), vals, force_text=force_text)
        res.recommended_types[str(col)] = pg_t
        res.type_confidence[str(col)] = round(conf, 3)
        if mixed:
            res.columns_with_mixed_types.append(str(col))
            res.import_warnings.append(
                f"Column {col!r} has mixed or incompatible values; using {pg_t} for DDL."
            )
    return res


# =============================================================================
# writers
# =============================================================================


def write_cleaned_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(
        path,
        index=False,
        encoding="utf-8",
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )


def build_create_table_sql(
    table_name: str,
    columns: list[str],
    inference: InferenceResult,
    *,
    schema: str | None = None,
) -> str:
    esc_schema = postgres_safe_identifier(schema) if schema else None
    esc_table = postgres_safe_identifier(table_name)
    full_name = f"{esc_schema}.{esc_table}" if esc_schema else esc_table
    type_map = {
        "text": "text",
        "boolean": "boolean",
        "bigint": "bigint",
        "numeric": "numeric",
        "date": "date",
        "timestamptz": "timestamptz",
    }
    lines = [f"CREATE TABLE IF NOT EXISTS {full_name} ("]
    parts: list[str] = []
    for col in columns:
        safe_col = postgres_safe_identifier(str(col))
        pg = inference.recommended_types.get(str(col), "text")
        sql_type = type_map.get(pg, "text")
        parts.append(f"  {safe_col} {sql_type}")
    lines.append(",\n".join(parts))
    lines.append(");")
    lines.append("")
    lines.append("-- Review types before applying; adjust primary keys and constraints as needed.")
    return "\n".join(lines)


def write_schema_sql(
    path: Path,
    table_name: str,
    df: pd.DataFrame,
    inference: InferenceResult,
    *,
    schema: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sql = build_create_table_sql(table_name, list(df.columns), inference, schema=schema)
    path.write_text(sql, encoding="utf-8")


# =============================================================================
# report
# =============================================================================


def build_report(
    *,
    input_path: Path,
    row_count_before: int,
    row_count_after: int,
    column_count: int,
    load_meta: LoadMeta,
    cleaning: CleaningStats,
    normalise: NormaliseStats,
    inference: InferenceResult,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    w = list(warnings)
    w.extend(inference.import_warnings)
    if cleaning.nonstandard_boolean_tokens:
        for col, tok in cleaning.nonstandard_boolean_tokens.items():
            w.append(f"Nonstandard boolean-like values in column {col!r}: sample {tok[:5]}")
    return {
        "report_version": "1",
        "original_file_name": input_path.name,
        "input_path": str(input_path.resolve()),
        "encoding_detected": load_meta.encoding,
        "delimiter_detected": load_meta.delimiter,
        "row_count_before": row_count_before,
        "row_count_after": row_count_after,
        "column_count": column_count,
        "renamed_columns": cleaning.renamed_columns,
        "duplicate_columns_fixed": cleaning.duplicate_columns_fixed,
        "blank_headers_replaced": cleaning.blank_headers_replaced,
        "rows_removed_empty": cleaning.rows_removed_empty,
        "rows_removed_duplicate": cleaning.rows_removed_duplicate,
        "null_values_standardized": cleaning.null_values_standardized,
        "booleans_normalised": cleaning.booleans_normalised,
        "nonstandard_boolean_tokens": cleaning.nonstandard_boolean_tokens,
        "dates_normalised": normalise.dates_normalised,
        "dates_ambiguous": normalise.dates_ambiguous,
        "possible_excel_serial_skipped": normalise.possible_excel_serial_skipped,
        "columns_with_mixed_types": inference.columns_with_mixed_types,
        "recommended_supabase_column_types": inference.recommended_types,
        "type_confidence": inference.type_confidence,
        "warnings": w,
        "errors": errors,
    }


def report_to_json_ready(report: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(report, default=str))


# =============================================================================
# CLI
# =============================================================================

app = typer.Typer(add_completion=False, help="Clean CSV/TSV/XLS/XLSX for Supabase import.")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def process_dataframe(
    df,
    *,
    drop_empty_rows: bool,
    dedupe: bool,
    cleaning: CleaningStats,
    normalise_stats: NormaliseStats,
):
    out = normalize_headers(df, cleaning)
    out = clean_all_string_cells(out)
    out = standardize_nulls_and_booleans(out, cleaning)
    if drop_empty_rows:
        out = remove_empty_rows(out, cleaning)
    if dedupe:
        out = dedupe_rows(out, cleaning)
    out = normalise_dates_and_excel_serials(out, normalise_stats)
    return out


@app.command()
def main(
    input_path: Path = typer.Argument(..., exists=True, readable=True, help="Path to .csv, .tsv, .xlsx, .xls"),
    table_name: Optional[str] = typer.Option(
        None, "--table-name", help="Target table name for SQL suggestion (default: snake_case of file stem)."
    ),
    dedupe: bool = typer.Option(False, "--dedupe", help="Remove duplicate rows."),
    drop_empty_rows: bool = typer.Option(
        True,
        "--drop-empty-rows/--no-drop-empty-rows",
        help="Remove rows that are entirely empty.",
    ),
    output_dir: Path = typer.Option(Path("output"), "--output-dir", help="Directory for outputs."),
    report_format: str = typer.Option("json", "--report-format", help="Report format (only json supported)."),
    schema_out: bool = typer.Option(
        False,
        "--schema-out/--no-schema-out",
        help="Write suggested schema_suggestion.sql",
    ),
    encoding: Optional[str] = typer.Option(None, "--encoding", help="Force text encoding for CSV/TSV."),
    preview: bool = typer.Option(False, "--preview", help="Print summary only; do not write files."),
    force_text: bool = typer.Option(False, "--force-text", help="Treat all columns as text in inference and SQL."),
    sample_rows: int = typer.Option(5, "--sample-rows", help="Rows to show in preview mode."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    _setup_logging(verbose)
    if report_format.lower() != "json":
        typer.echo(f"Unsupported report format: {report_format!r} (only json)", err=True)
        raise typer.Exit(code=1)
    input_path = Path(input_path)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        df, load_meta = load_tabular(input_path, encoding)
    except (ValueError, FileNotFoundError, OSError) as e:
        typer.echo(f"Failed to load file: {e}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        logging.exception("Unexpected load error")
        typer.echo(f"Failed to load file: {e}", err=True)
        raise typer.Exit(code=1)
    row_before = len(df)
    col_before = len(df.columns)
    cleaning = CleaningStats()
    normalise_stats = NormaliseStats()
    try:
        out = process_dataframe(
            df,
            drop_empty_rows=drop_empty_rows,
            dedupe=dedupe,
            cleaning=cleaning,
            normalise_stats=normalise_stats,
        )
    except Exception as e:
        logging.exception("Cleaning failed")
        typer.echo(f"Cleaning failed: {e}", err=True)
        raise typer.Exit(code=1)
    inference = infer_frame(out, force_text=force_text)
    stem = safe_output_stem(input_path)
    tbl = postgres_safe_identifier(table_name or stem)
    if preview:
        typer.echo(f"Input: {input_path}")
        typer.echo(f"Rows: {row_before} -> {len(out)} | Columns: {col_before}")
        typer.echo(f"Encoding (text files): {load_meta.encoding} | Delimiter: {load_meta.delimiter!r}")
        typer.echo(f"Suggested table name: {tbl}")
        typer.echo("\nColumn renames (raw key -> final):")
        for k, v in list(cleaning.renamed_columns.items())[:50]:
            typer.echo(f"  {k!r} -> {v!r}")
        typer.echo("\nRecommended types:")
        for c, t in inference.recommended_types.items():
            conf = inference.type_confidence.get(c, 0)
            typer.echo(f"  {c}: {t} (confidence {conf})")
        typer.echo(f"\nSample ({min(sample_rows, len(out))} rows):")
        typer.echo(out.head(sample_rows).to_string())
        raise typer.Exit(code=0)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{stem}_cleaned.csv"
    report_path = output_dir / f"{stem}_report.json"
    try:
        write_cleaned_csv(out, csv_path)
    except OSError as e:
        typer.echo(f"Failed to write CSV: {e}", err=True)
        raise typer.Exit(code=1)
    rep = build_report(
        input_path=input_path,
        row_count_before=row_before,
        row_count_after=len(out),
        column_count=len(out.columns),
        load_meta=load_meta,
        cleaning=cleaning,
        normalise=normalise_stats,
        inference=inference,
        errors=errors,
        warnings=warnings,
    )
    report_path.write_text(json.dumps(report_to_json_ready(rep), indent=2), encoding="utf-8")
    if schema_out:
        sql_path = output_dir / f"{stem}_schema_suggestion.sql"
        write_schema_sql(sql_path, tbl, out, inference)
    typer.echo(f"Wrote: {csv_path.resolve()}")
    typer.echo(f"Wrote: {report_path.resolve()}")
    if schema_out:
        typer.echo(f"Wrote: {(output_dir / f'{stem}_schema_suggestion.sql').resolve()}")


if __name__ == "__main__":
    app()
