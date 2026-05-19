import argparse
import json
import math
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests


NULL_TOKENS = frozenset({"", " ", "null", "n/a", "na", "none", "nan", "-", "--"})
TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1"})
FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0"})

INTEGER_TYPES = {"integer", "int4", "int8", "smallint", "bigint"}
NUMERIC_TYPES = {"number", "numeric", "decimal", "float", "float4", "float8", "double precision"}
BOOLEAN_TYPES = {"boolean", "bool"}
DATE_TYPES = {"date"}
DATETIME_TYPES = {
    "date-time",
    "timestamp",
    "timestamptz",
    "timestamp with time zone",
    "timestamp without time zone",
}
JSON_TYPES = {"json", "jsonb"}


def load_dotenv(dotenv_path: str) -> None:
    """
    Minimal .env loader.
    Only sets env vars that are missing.
    """
    if not os.path.exists(dotenv_path):
        return
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not os.environ.get(key):
                os.environ[key] = value


def chunked(seq: List[Any], n: int) -> Iterable[List[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def normalize_supabase_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    return value.rstrip("/")


def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    # Pandas NA types
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (pd.Timestamp,)):
        # Let requests serialize as ISO-ish string.
        return str(value)
    if isinstance(value, str):
        v = value.strip()
        if not v or v.lower() in NULL_TOKENS:
            return None
        return v
    return value


def parse_number(value: Any) -> Optional[float]:
    v = clean_scalar(value)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip()
    s = s.replace("£", "").replace("$", "").replace("€", "")
    s = s.replace(",", "").replace(" ", "")
    s_upper = s.upper()
    if s_upper.endswith("K"):
        try:
            return float(s_upper[:-1]) * 1000.0
        except ValueError:
            return None
    if s_upper.endswith("M"):
        try:
            return float(s_upper[:-1]) * 1000000.0
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def coerce_value_for_type(value: Any, pg_type: str) -> Any:
    # Keep all non-specified types as cleaned strings/values.
    v = clean_scalar(value)
    if v is None:
        return None
    t = (pg_type or "").strip().lower()
    if t in INTEGER_TYPES:
        n = parse_number(v)
        if n is None:
            return None
        return int(n)
    if t in NUMERIC_TYPES:
        return parse_number(v)
    if t in BOOLEAN_TYPES:
        if isinstance(v, bool):
            return bool(v)
        if isinstance(v, str):
            s = v.strip().lower()
            if s in TRUE_TOKENS:
                return True
            if s in FALSE_TOKENS:
                return False
        return None
    if t in DATE_TYPES or t in DATETIME_TYPES:
        # Assume CSV already uses ISO-ish strings.
        # (If not, PostgREST will reject and we want the error.)
        return v
    if t in JSON_TYPES:
        # If the CSV contains JSON text, keep it as parsed JSON.
        if isinstance(v, str):
            vv = v.strip()
            if vv.startswith("{") or vv.startswith("["):
                try:
                    return json.loads(vv)
                except Exception:
                    return None
        return v
    return v


def fetch_openapi_spec(base_url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    # Supabase exposes PostgREST OpenAPI at: <url>/rest/v1
    # Note the trailing slash; dashboard/app.py also calls `/rest/v1/`.
    url = f"{base_url}/rest/v1/"
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to fetch OpenAPI spec: {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("OpenAPI spec response was not a JSON object.")
    return data


def resolve_definition(definitions: Dict[str, Any], table_name: str) -> Dict[str, Any]:
    if table_name in definitions:
        return definitions[table_name]
    # Fallback: sometimes keys may be prefixed (e.g. public.leads).
    for k, v in definitions.items():
        if k.endswith(f".{table_name}"):
            return v if isinstance(v, dict) else {}
    raise RuntimeError(f"Table definition not found in OpenAPI spec: {table_name}")


def extract_table_props(spec: Dict[str, Any], table_name: str) -> Dict[str, Dict[str, Any]]:
    definitions = spec.get("definitions", {})
    defn = resolve_definition(definitions, table_name)
    props = defn.get("properties", {})
    if not isinstance(props, dict):
        raise RuntimeError(f"Table properties not found for {table_name}")
    return props


def build_rows_for_table_from_csv(
    *,
    df: pd.DataFrame,
    csv_to_schema_column: Dict[str, str],
    schema_props: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Build payload rows that only include columns present in the table schema.

    Returns:
      (rows, used_columns)
    """
    schema_columns = set(schema_props.keys())
    used_columns: List[str] = []
    rows: List[Dict[str, Any]] = []

    # Precompute per-column pg types for coercion
    col_type: Dict[str, str] = {}
    for col, info in schema_props.items():
        col_type[col] = str(info.get("format", info.get("type", "")) or "")

    for _, record in df.iterrows():
        out: Dict[str, Any] = {}
        for csv_col, schema_col in csv_to_schema_column.items():
            if schema_col not in schema_columns:
                continue
            if csv_col not in record:
                continue
            value = record.get(csv_col)
            out[schema_col] = coerce_value_for_type(value, col_type.get(schema_col, ""))
        # Only keep rows that have at least one non-null value.
        if any(v is not None for v in out.values()):
            rows.append(out)
            used_columns = sorted(set(used_columns) | set(out.keys()))

    return rows, used_columns


def upsert_table_rows(
    *,
    base_url: str,
    headers: Dict[str, str],
    table: str,
    rows: List[Dict[str, Any]],
    batch_size: int,
    apply: bool,
    on_conflict: Optional[str] = None,
) -> int:
    if not rows:
        return 0

    url = f"{base_url}/rest/v1/{table}"
    sent = 0

    params: Dict[str, str] = {}
    if on_conflict:
        params["on_conflict"] = on_conflict

    # For "upsert" semantics, prefer merge-duplicates.
    # If on_conflict is omitted, PostgREST may still resolve based on unique constraints.
    upsert_headers = dict(headers)
    upsert_headers["Prefer"] = "return=minimal,resolution=merge-duplicates"

    for batch in chunked(rows, batch_size):
        sent += len(batch)
        if not apply:
            continue

        resp = requests.post(url, headers=upsert_headers, params=params, json=batch, timeout=180)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Upsert failed for {table} ({resp.status_code}): {resp.text[:500]}"
            )

    return sent


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upsert missing leads and custom activities from Quotezone exports."
    )
    parser.add_argument(
        "--dotenv-path",
        default="dashboard/.env",
        help="Path to dashboard/.env (used for SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY).",
    )
    parser.add_argument(
        "--quotezone-in-db-not-dashboard-csv",
        default=r"c:\Users\Ricardo Moses\My Downloads\quotezone_in_db_not_dashboard_mapped.csv",
    )
    parser.add_argument(
        "--quotezone-missing-from-db-csv",
        default=r"c:\Users\Ricardo Moses\My Downloads\quotezone_missing_from_db_mapped.csv",
    )
    parser.add_argument(
        "--custom-activities-csv",
        default=r"c:\Users\Ricardo Moses\My Downloads\custom_activities_output (1).csv",
    )
    parser.add_argument("--apply", action="store_true", help="Actually write to Supabase.")
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    load_dotenv(args.dotenv_path)
    base_url = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if not base_url or not supabase_key:
        raise SystemExit("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }

    spec = fetch_openapi_spec(base_url, headers=headers)
    leads_props = extract_table_props(spec, "leads")
    custom_activities_props = extract_table_props(spec, "custom_activities")

    # ---------- Load CSVs ----------
    def load_csv(path: str) -> pd.DataFrame:
        if not os.path.exists(path):
            raise SystemExit(f"ERROR: CSV not found: {path}")
        return pd.read_csv(path, dtype=object, low_memory=False, keep_default_na=False)

    z1 = load_csv(args.quotezone_in_db_not_dashboard_csv)
    z2 = load_csv(args.quotezone_missing_from_db_csv)

    if "close_lead_id" not in z1.columns or "close_lead_id" not in z2.columns:
        raise SystemExit("ERROR: Quotezone CSVs must contain a 'close_lead_id' column.")

    leads_df = pd.concat([z1, z2], ignore_index=True)
    leads_df = leads_df.drop_duplicates(subset=["close_lead_id"], keep="last")

    ca_df = load_csv(args.custom_activities_csv)
    required_ca_cols = {"activity_id", "lead_id", "custom_activity_type_name", "custom_activity_type_id"}
    missing = sorted(required_ca_cols - set(ca_df.columns))
    if missing:
        raise SystemExit(f"ERROR: custom activities CSV missing columns: {missing}")

    # ---------- Build leads payload ----------
    schema_lead_cols = set(leads_props.keys())
    # Our CSV already uses close-schema column names (e.g. close_lead_id, loan_amount, ...).
    # So we map csv_col -> same schema_col when possible.
    csv_to_schema_leads: Dict[str, str] = {c: c for c in leads_df.columns if c in schema_lead_cols}

    leads_rows: List[Dict[str, Any]] = []
    # Coerce only columns that exist in schema.
    col_type: Dict[str, str] = {
        col: str(info.get("format", info.get("type", "")) or "") for col, info in leads_props.items()
    }

    for _, record in leads_df.iterrows():
        out: Dict[str, Any] = {}
        for csv_col, schema_col in csv_to_schema_leads.items():
            value = record.get(csv_col)
            out[schema_col] = coerce_value_for_type(value, col_type.get(schema_col, ""))
        if any(v is not None for v in out.values()):
            leads_rows.append(out)

    # ---------- Build custom_activities payload ----------
    schema_ca_cols = set(custom_activities_props.keys())
    ca_col_type: Dict[str, str] = {
        col: str(info.get("format", info.get("type", "")) or "") for col, info in custom_activities_props.items()
    }

    # Dedup by custom_activity_id (CSV column `activity_id`)
    ca_df = ca_df.drop_duplicates(subset=["activity_id"], keep="last")

    # Only include columns we know how to build safely.
    payload_cols_needed: Dict[str, str] = {
        "lead_id": "lead_id",
        "custom_activity_id": "activity_id",
        "source_system": "source_system",  # synthetic
    }
    # custom_activity_type_id is a jsonb object -> {"<custom_activity_type_name>": "<custom_activity_type_id>"}
    payload_cols_needed_type_id_key = "custom_activity_type_id"

    ca_rows: List[Dict[str, Any]] = []
    for _, record in ca_df.iterrows():
        lead_id = clean_scalar(record.get("lead_id"))
        caid = clean_scalar(record.get("activity_id"))
        type_name = clean_scalar(record.get("custom_activity_type_name"))
        type_id = clean_scalar(record.get("custom_activity_type_id"))

        if lead_id is None or caid is None or type_name is None or type_id is None:
            continue

        out: Dict[str, Any] = {}
        if "lead_id" in schema_ca_cols:
            out["lead_id"] = coerce_value_for_type(lead_id, ca_col_type.get("lead_id", ""))
        if "custom_activity_id" in schema_ca_cols:
            out["custom_activity_id"] = coerce_value_for_type(caid, ca_col_type.get("custom_activity_id", ""))
        if payload_cols_needed_type_id_key in schema_ca_cols:
            # jsonb column: value is a mapping of type-name -> type-id.
            out["custom_activity_type_id"] = {str(type_name): str(type_id)}
        if "source_system" in schema_ca_cols:
            out["source_system"] = coerce_value_for_type("close_crm", ca_col_type.get("source_system", ""))

        # Optional columns, included only if present in schema.
        for optional_csv_col, schema_col in [
            ("date_created", "date_created"),
            ("date_updated", "date_updated"),
            ("created_by", "created_by"),
            ("user_id", "user_id"),
            ("organization_id", "organization_id"),
            ("status", "status"),
        ]:
            if schema_col in schema_ca_cols and optional_csv_col in ca_df.columns:
                out[schema_col] = coerce_value_for_type(record.get(optional_csv_col), ca_col_type.get(schema_col, ""))

        if any(v is not None for v in out.values()):
            ca_rows.append(out)

    # ---------- Consistency checks ----------
    lead_ids_in_csv = set(str(x) for x in leads_df["close_lead_id"].tolist() if x is not None and str(x).strip() != "")
    ca_lead_ids = set(str(x) for x in ca_df["lead_id"].tolist() if x is not None and str(x).strip() != "")
    missing_lead_ids = ca_lead_ids - lead_ids_in_csv
    if missing_lead_ids:
        print(f"WARNING: {len(missing_lead_ids)} custom_activities lead_id(s) are not present in the provided leads CSVs.")
        print("WARNING: Example missing lead_id:", next(iter(missing_lead_ids)))

    # ---------- Execute ----------
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    print(f"Leads CSV rows: z1={len(z1)} z2={len(z2)} -> unique close_lead_id={len(leads_rows)} payload_rows (including null-only filtered).")
    print(f"Custom activities CSV rows: {len(ca_df)} -> payload_rows={len(ca_rows)}")

    print("\nUpserting leads...")
    upserted_leads = upsert_table_rows(
        base_url=base_url,
        headers=headers,
        table="leads",
        rows=leads_rows,
        batch_size=args.batch_size,
        apply=args.apply,
        # Your schema appears to have a unique constraint on `close_lead_id`.
        # Without this, PostgREST returns 409 "duplicate key" instead of upserting.
        on_conflict="close_lead_id",
    )
    print(f"Leads payload rows attempted: {upserted_leads}")

    print("\nUpserting custom_activities...")
    # We try on_conflict first; if the server complains, users can re-run with a different strategy.
    # (We keep it simple: custom_activity_id is assumed unique.)
    try:
        upserted_ca = upsert_table_rows(
            base_url=base_url,
            headers=headers,
            table="custom_activities",
            rows=ca_rows,
            batch_size=args.batch_size,
            apply=args.apply,
            on_conflict="custom_activity_id",
        )
    except RuntimeError as exc:
        if not args.apply:
            raise
        print(f"Primary upsert attempt failed; retrying without on_conflict. Error was: {exc}")
        upserted_ca = upsert_table_rows(
            base_url=base_url,
            headers=headers,
            table="custom_activities",
            rows=ca_rows,
            batch_size=args.batch_size,
            apply=args.apply,
            on_conflict=None,
        )
    print(f"Custom activities payload rows attempted: {upserted_ca}")

    print("\nDone.")


if __name__ == "__main__":
    main()

