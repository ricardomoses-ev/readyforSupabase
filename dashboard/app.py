from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).parent / ".env")

SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in dashboard/.env"
    )

_SAFE_TABLE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_NULL_TOKENS = frozenset({"", " ", "null", "n/a", "na", "none", "nan", "-", "--"})
_TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1"})
_FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0"})

_CLOSE_SCHEMA_MAP = {
    "id": "close_lead_id",
    "lead_name": "lead_name",
    "primary_contact_name": "contact_name",
    "primary_contact_primary_email": "contact_email",
    "primary_contact_primary_phone": "contact_phone",
    "status_label": "status",
    "custom.Loan Amount": "loan_amount",
    "custom.Lead Source": "lead_source",
    "custom.Lead Magnet Source": "lead_magnet_source",
    "custom.Company Registration number": "company_registration_number",
    "custom.SIC Code": "sic_code",
    "custom.Years of trading": "years_of_trading",
    "custom.Companies House URL": "companies_house_url",
    "custom.Lender": "lender",
    "custom.Net Assets": "net_assets",
    "custom.Profit": "profitability",
    "custom.Business Model": "business_model",
    "custom.Turnover": "turnover",
    "custom.Company Status": "company_status",
    "date_created": "close_created_at",
    "date_updated": "close_updated_at",
}
_INTEGER_TYPES = {"integer", "int4", "int8", "smallint", "bigint"}
_NUMERIC_TYPES = {"number", "numeric", "decimal", "float", "float4", "float8", "double precision"}
_BOOLEAN_TYPES = {"boolean", "bool"}
_JSON_TYPES = {"json", "jsonb"}
_DATE_TYPES = {"date"}
_DATETIME_TYPES = {"date-time", "timestamp", "timestamptz", "timestamp with time zone", "timestamp without time zone"}

app = FastAPI(title="Supabase Dashboard")

HEADERS: dict[str, str] = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

_openapi_cache: dict[str, Any] | None = None


def _rest_url(path: str = "") -> str:
    return f"{SUPABASE_URL}/rest/v1{path}"


def _auth_url(path: str = "") -> str:
    return f"{SUPABASE_URL}/auth/v1{path}"


def _validate_table(name: str) -> str:
    if not _SAFE_TABLE_NAME.match(name):
        raise HTTPException(status_code=400, detail="Invalid table name")
    return name


async def _get_openapi_spec(force: bool = False) -> dict[str, Any]:
    """Fetch and cache the PostgREST OpenAPI spec (always available)."""
    global _openapi_cache
    if _openapi_cache and not force:
        return _openapi_cache
    url = _rest_url("/")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=HEADERS)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    _openapi_cache = resp.json()
    return _openapi_cache


def _ascii_snake(name: str) -> str:
    name = name.strip()
    name = re.sub(r"^custom\.", "", name)
    name = name.replace(".id", "_id").replace(".name", "_name")
    name = name.replace("/", "_or_").replace("&", "_and_")
    name = name.replace("%", "_pct").replace("?", "").replace(">", "_gt_")
    name = re.sub(r"[()]+", "", name)
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    name = name.lower()
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def _normalize_column_name(name: str, source_format: str) -> str:
    cleaned = name.strip()
    if source_format == "close_csv" and cleaned in _CLOSE_SCHEMA_MAP:
        return _CLOSE_SCHEMA_MAP[cleaned]
    return _ascii_snake(cleaned)


def _dedupe_column_names(columns: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    final: list[str] = []
    for col in columns:
        base = col or "column"
        counts[base] = counts.get(base, 0) + 1
        final.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return final


def _normalize_scalar(value: Any, data_type: str) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        if value.lower() in _NULL_TOKENS:
            return None
    dtype = (data_type or "").lower()

    if dtype in _BOOLEAN_TYPES:
        if isinstance(value, bool):
            return value
        token = str(value).strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        return None

    if dtype in _INTEGER_TYPES:
        token = str(value).strip().replace(",", "")
        token = token.replace("£", "").replace("$", "").replace("€", "")
        if not token:
            return None
        try:
            return int(float(token))
        except ValueError:
            return None

    if dtype in _NUMERIC_TYPES:
        token = str(value).strip().replace(",", "")
        token = token.replace("£", "").replace("$", "").replace("€", "")
        if token.lower().endswith("k"):
            token = str(float(token[:-1]) * 1000)
        elif token.lower().endswith("m"):
            token = str(float(token[:-1]) * 1_000_000)
        if "-" in token and not token.startswith("-"):
            parts = token.split("-")
            if len(parts) == 2:
                try:
                    return (float(parts[0]) + float(parts[1])) / 2
                except ValueError:
                    return None
        try:
            return float(token)
        except ValueError:
            return None

    if dtype in _DATE_TYPES:
        parsed = pd.to_datetime(value, errors="coerce", utc=False, dayfirst=False)
        if pd.isna(parsed):
            return str(value)
        return parsed.date().isoformat()

    if dtype in _DATETIME_TYPES:
        parsed = pd.to_datetime(value, errors="coerce", utc=True, dayfirst=False)
        if pd.isna(parsed):
            return str(value)
        return parsed.isoformat()

    if dtype in _JSON_TYPES:
        return value

    return str(value).strip() if isinstance(value, str) else value


def _prepare_import_frame(
    df: pd.DataFrame,
    schema: list[dict[str, Any]],
    *,
    source_format: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    original_columns = [str(col) for col in df.columns]
    normalized_columns = _dedupe_column_names(
        [_normalize_column_name(str(col), source_format) for col in original_columns]
    )
    df = df.copy()
    df.columns = normalized_columns
    df = df.dropna(axis=0, how="all")

    schema_by_name = {str(col["column_name"]): col for col in schema}
    matching_columns = [col for col in df.columns if col in schema_by_name]
    skipped_columns = [col for col in df.columns if col not in schema_by_name]
    if not matching_columns:
        raise HTTPException(
            status_code=400,
            detail="None of the uploaded CSV columns matched the selected table schema.",
        )

    trimmed = df[matching_columns].copy()
    normalized_rows: list[dict[str, Any]] = []
    for record in trimmed.to_dict(orient="records"):
        row: dict[str, Any] = {}
        for key, raw_value in record.items():
            row[key] = _normalize_scalar(raw_value, str(schema_by_name[key].get("data_type", "")))
        if any(value is not None for value in row.values()):
            normalized_rows.append(row)

    return normalized_rows, {
        "original_columns": original_columns,
        "matched_columns": matching_columns,
        "skipped_columns": skipped_columns,
        "column_mapping": {
            original_columns[idx]: normalized_columns[idx]
            for idx in range(min(len(original_columns), len(normalized_columns)))
        },
    }


async def _insert_rows_in_batches(
    table_name: str,
    rows: list[dict[str, Any]],
    *,
    batch_size: int = 500,
) -> dict[str, Any]:
    url = _rest_url(f"/{table_name}")
    headers = {**HEADERS, "Prefer": "return=minimal"}
    inserted = 0
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            resp = await client.post(url, headers=headers, json=batch)
            if resp.status_code < 400:
                inserted += len(batch)
                continue

            for row in batch:
                single = await client.post(url, headers=headers, json=row)
                if single.status_code < 400:
                    inserted += 1
                elif len(errors) < 20:
                    errors.append(single.text[:300])

    return {"inserted": inserted, "errors": errors}


# ── Tables ───────────────────────────────────────────────────────────

@app.get("/api/tables")
async def list_tables() -> list[dict[str, Any]]:
    """List all tables with row counts."""
    spec = await _get_openapi_spec()
    definitions = spec.get("definitions", {})
    table_names = sorted(definitions.keys())

    async with httpx.AsyncClient(timeout=15.0) as client:
        tables = []
        for table_name in table_names:
            url = _rest_url(f"/{table_name}?select=count&limit=0")
            headers = {**HEADERS, "Prefer": "count=exact"}
            resp = await client.get(url, headers=headers)
            row_count = _parse_content_range(
                resp.headers.get("content-range", "")
            )
            tables.append({
                "table_name": table_name,
                "row_count": row_count if row_count is not None else 0,
            })
    return tables


# ── Schema ───────────────────────────────────────────────────────────

@app.get("/api/tables/{name}/schema")
async def table_schema(name: str) -> list[dict[str, Any]]:
    """Get column details from the PostgREST OpenAPI spec."""
    _validate_table(name)
    spec = await _get_openapi_spec()
    defn = spec.get("definitions", {}).get(name)
    if not defn:
        raise HTTPException(status_code=404, detail=f"Table '{name}' not found")

    required_cols = set(defn.get("required", []))
    columns = []
    for col_name, col_info in defn.get("properties", {}).items():
        pg_type = col_info.get("format", col_info.get("type", "unknown"))
        description = col_info.get("description", "")
        is_pk = "<pk" in description
        default = col_info.get("default")
        columns.append({
            "column_name": col_name,
            "data_type": pg_type,
            "is_nullable": "NO" if col_name in required_cols else "YES",
            "column_default": str(default) if default is not None else None,
            "is_primary_key": is_pk,
            "description": description if description else None,
        })
    return columns


# ── Rows (CRUD) ─────────────────────────────────────────────────────

@app.get("/api/tables/{name}/rows")
async def get_rows(
    name: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    order: str | None = Query(None),
    filter_column: str | None = Query(None),
    filter_op: str = Query("contains"),
    filter_value: str | None = Query(None),
) -> dict[str, Any]:
    _validate_table(name)
    params: dict[str, str | int] = {"select": "*", "offset": offset, "limit": limit}
    if order:
        params["order"] = order
    if filter_column and filter_value is not None and filter_value != "":
        supported_ops = {"contains", "eq", "neq", "gt", "lt", "gte", "lte", "starts_with", "ends_with"}
        if filter_op not in supported_ops:
            raise HTTPException(status_code=400, detail="Unsupported filter operator")

        if filter_op == "contains":
            params[filter_column] = f"ilike.*{filter_value}*"
        elif filter_op == "starts_with":
            params[filter_column] = f"ilike.{filter_value}*"
        elif filter_op == "ends_with":
            params[filter_column] = f"ilike.*{filter_value}"
        else:
            params[filter_column] = f"{filter_op}.{filter_value}"

    url = _rest_url(f"/{name}")
    headers = {**HEADERS, "Prefer": "count=exact"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    total = _parse_content_range(resp.headers.get("content-range", ""))
    return {"rows": resp.json(), "total": total, "offset": offset, "limit": limit}


@app.post("/api/tables/{name}/rows")
async def insert_rows(name: str, request: Request) -> dict[str, Any]:
    _validate_table(name)
    body = await request.json()
    url = _rest_url(f"/{name}")
    headers = {**HEADERS, "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json=body)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return {"inserted": resp.json()}


@app.patch("/api/tables/{name}/rows")
async def update_rows(name: str, request: Request) -> dict[str, Any]:
    """Expects JSON: { "match": {"id": 123}, "data": {"col": "val"} }"""
    _validate_table(name)
    body = await request.json()
    match_filters = body.get("match", {})
    data = body.get("data", {})
    if not match_filters or not data:
        raise HTTPException(status_code=400, detail="Provide 'match' and 'data'")

    query = "&".join(f"{k}=eq.{v}" for k, v in match_filters.items())
    url = _rest_url(f"/{name}?{query}")
    headers = {**HEADERS, "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(url, headers=headers, json=data)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return {"updated": resp.json()}


@app.delete("/api/tables/{name}/rows")
async def delete_rows(name: str, request: Request) -> dict[str, Any]:
    """Expects JSON: { "match": {"id": 123} }"""
    _validate_table(name)
    body = await request.json()
    match_filters = body.get("match", {})
    if not match_filters:
        raise HTTPException(status_code=400, detail="Provide 'match' filters")

    query = "&".join(f"{k}=eq.{v}" for k, v in match_filters.items())
    url = _rest_url(f"/{name}?{query}")
    headers = {**HEADERS, "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(url, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return {"deleted": resp.json()}


# ── SQL Editor ───────────────────────────────────────────────────────

SQL_ENDPOINTS = [
    "/pg/query",
    "/pg-meta/default/query",
]


@app.post("/api/sql")
async def execute_sql(request: Request) -> Any:
    body = await request.json()
    sql = body.get("sql", "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="Provide 'sql' in body")

    # First try: direct RPC if the helper function exists
    rpc_result = await _try_rpc_sql(sql)
    if rpc_result is not None:
        return rpc_result

    # Second try: known pg-meta / pg endpoints
    for path in SQL_ENDPOINTS:
        url = f"{SUPABASE_URL}{path}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=HEADERS, json={"query": sql})
            if resp.status_code < 400:
                return resp.json()
        except httpx.HTTPError:
            continue

    raise HTTPException(
        status_code=501,
        detail=(
            "Raw SQL is not available via your Supabase project's public API. "
            "To enable it, create this function in your Supabase SQL Editor:\n\n"
            "CREATE OR REPLACE FUNCTION dashboard_exec_sql(query text)\n"
            "RETURNS json LANGUAGE plpgsql SECURITY DEFINER AS $$\n"
            "DECLARE result json;\n"
            "BEGIN\n"
            "  EXECUTE 'SELECT coalesce(json_agg(r), ''[]''::json) FROM (' || query || ') r'\n"
            "    INTO result;\n"
            "  RETURN result;\n"
            "END;\n"
            "$$;\n\n"
            "Then refresh the dashboard and try again."
        ),
    )


async def _try_rpc_sql(sql: str) -> Any | None:
    """Try executing SQL via the dashboard_exec_sql RPC function."""
    url = _rest_url("/rpc/dashboard_exec_sql")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=HEADERS, json={"query": sql})
        if resp.status_code < 400:
            return resp.json()
    except httpx.HTTPError:
        pass
    return None


# ── Auth Users ───────────────────────────────────────────────────────

@app.get("/api/auth/users")
async def list_auth_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    all_pages: bool = Query(False),
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        if not all_pages:
            url = _auth_url(f"/admin/users?page={page}&per_page={per_page}")
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()

        users: list[dict[str, Any]] = []
        current_page = 1
        max_pages = 200

        for _ in range(max_pages):
            url = _auth_url(f"/admin/users?page={current_page}&per_page={per_page}")
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            payload = resp.json()
            batch = payload.get("users", [])
            users.extend(batch)
            if len(batch) < per_page:
                break
            current_page += 1

    return {"users": users, "page": 1, "per_page": per_page, "pages_fetched": current_page}


@app.post("/api/auth/users/{user_id}/reset-password")
async def reset_auth_user_password(user_id: str, request: Request) -> dict[str, str]:
    body = await request.json()
    password = str(body.get("password", "")).strip()
    if not password:
        raise HTTPException(status_code=400, detail="Provide 'password' in body")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    url = _auth_url(f"/admin/users/{user_id}")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.put(url, headers=HEADERS, json={"password": password})
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return {"status": "ok", "message": f"Password updated for user {user_id}"}


@app.post("/api/cache/refresh")
async def refresh_cache() -> dict[str, str]:
    """Force-refresh the cached OpenAPI spec."""
    await _get_openapi_spec(force=True)
    return {"status": "ok"}


@app.post("/api/import/csv")
async def import_csvs(
    table_name: str = Form(...),
    source_format: str = Form("close_csv"),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    table_name = _validate_table(table_name)
    if source_format not in {"close_csv", "generic_csv"}:
        raise HTTPException(status_code=400, detail="Unsupported source format")
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one CSV file")

    schema = await table_schema(table_name)
    all_rows: list[dict[str, Any]] = []
    file_summaries: list[dict[str, Any]] = []

    for file in files:
        filename = file.filename or "upload.csv"
        if not filename.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail=f"{filename} is not a CSV file")
        content = await file.read()
        if not content:
            continue
        try:
            df = pd.read_csv(io.BytesIO(content), dtype=object, keep_default_na=False)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read {filename}: {exc}") from exc

        rows, summary = _prepare_import_frame(df, schema, source_format=source_format)
        all_rows.extend(rows)
        file_summaries.append(
            {
                "filename": filename,
                "rows_read": int(len(df)),
                "rows_ready": int(len(rows)),
                **summary,
            }
        )

    if not all_rows:
        raise HTTPException(status_code=400, detail="No importable rows were found in the uploaded files.")

    insert_result = await _insert_rows_in_batches(table_name, all_rows)
    return {
        "table_name": table_name,
        "source_format": source_format,
        "files_processed": len(file_summaries),
        "rows_ready": len(all_rows),
        "rows_inserted": insert_result["inserted"],
        "errors": insert_result["errors"],
        "files": file_summaries,
    }


# ── Helpers ──────────────────────────────────────────────────────────

def _parse_content_range(header: str) -> int | None:
    """Parse '0-49/1234' -> 1234"""
    if "/" in header:
        try:
            return int(header.split("/")[1])
        except (ValueError, IndexError):
            return None
    return None


# ── Serve frontend ───────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
