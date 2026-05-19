import json
import math
import os
import time
import argparse
import hashlib
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import requests

def normalize_supabase_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""

    # Handle markdown link style: [text](https://project.supabase.co/)
    md_match = re.match(r"^\[.*\]\((https?://[^)]+)\)$", value)
    if md_match:
        value = md_match.group(1).strip()

    # Handle accidental wrapping with [] or ()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()

    return value.rstrip("/")


SUPABASE_URL = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(_SCRIPT_DIR, "output", "missing_partners_upsert_ready.csv")
PARTNERS_TABLE = "partners"
LEADS_TABLE = "leads"
BATCH_SIZE = 200

CSV_LINK_COL = "lead_id"
CSV_PARTNER_NAME_CANDIDATES = ["matched_partner_name", "partner_name", "name"]
CSV_FIELD_CANDIDATES = [
    "partner_close_lead_id",
    "matched_partner_name",
    "match_method",
    "partner_introducer",
    "lead_magnet_source",
]
# Extra CSV columns copied into `partners` when the column exists in the table (slug/user_id excluded; slug is derived).
CSV_OPTIONAL_PARTNER_FIELD_CANDIDATES = [
    "is_active",
    "background_color",
    "text_color",
    "accent_color",
    "logo_url",
    "theme_colors",
    "created_at",
    "updated_at",
]
CSV_PARTNER_PK_FROM_CSV_COL = "uuid"

LEADS_UUID_CANDIDATES = ["id", "uuid"]
LEADS_CLOSE_ID_CANDIDATES = ["close_lead_id", "lead_id"]
LEADS_PARTNER_ID_CANDIDATES = ["partner_id"]
PARTNERS_LINK_ID_CANDIDATES = ["lead_id", "close_lead_id"]
PARTNERS_LINK_UUID_CANDIDATES = ["lead_uuid", "lead_id_uuid"]
PARTNERS_NAME_CANDIDATES = ["matched_partner_name", "partner_name", "name"]
PARTNERS_ID_CANDIDATES = ["uuid", "id"]
PARTNERS_AUTH_USER_ID_CANDIDATES = ["auth_user_id", "user_id"]
PARTNERS_SLUG_CANDIDATES = ["slug"]

PARTNER_AUTH_EMAIL_DOMAIN = os.environ.get("PARTNER_AUTH_EMAIL_DOMAIN", "partners.local")


def fail(msg: str) -> None:
    print(f"ERROR: {msg}")
    raise SystemExit(1)


if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables.")
    print('  $env:SUPABASE_URL = "https://your-project.supabase.co"')
    print('  $env:SUPABASE_SERVICE_ROLE_KEY = "your-service-role-key"')
    raise SystemExit(1)

parsed = urlparse(SUPABASE_URL)
if parsed.scheme not in ("http", "https") or not parsed.netloc:
    fail(
        "SUPABASE_URL is invalid. Use a plain URL like "
        "'https://your-project-ref.supabase.co' (no markdown link formatting)."
    )

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
HEADERS_MIN = {
    **HEADERS,
    "Prefer": "return=minimal,resolution=merge-duplicates",
}


def table_has_column(table: str, column: str) -> bool:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=HEADERS,
        params={"select": column, "limit": 1},
        timeout=30,
    )
    return resp.status_code == 200


def pick_first_existing(table: str, candidates: List[str]) -> Optional[str]:
    for col in candidates:
        if table_has_column(table, col):
            return col
    return None


def format_error(resp: requests.Response, max_len: int = 1200) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            code = data.get("code")
            message = data.get("message")
            details = data.get("details")
            parts = []
            if code:
                parts.append(f"code={code}")
            if message:
                parts.append(f"message={message}")
            if details:
                parts.append(f"details={details}")
            if parts:
                txt = " | ".join(parts)
                return txt[:max_len]
    except Exception:
        pass
    return (resp.text or "")[:max_len]


def clean_value(value):
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, str):
        v = value.strip()
        return v if v else None
    return value


def slugify(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "partner"


def build_partner_email(partner_name: str) -> str:
    base = slugify(partner_name)
    h = hashlib.sha1(partner_name.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{h}@{PARTNER_AUTH_EMAIL_DOMAIN}"


def build_partner_slug(partner_name: str) -> str:
    s = (partner_name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "partner"


def fetch_lead_map(lead_id_col: str, lead_uuid_col: str) -> Dict[str, str]:
    lead_map: Dict[str, str] = {}
    page = 1000
    offset = 0

    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/{LEADS_TABLE}",
            headers=HEADERS,
            params={
                "select": f"{lead_uuid_col},{lead_id_col}",
                "offset": offset,
                "limit": page,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            fail(f"Failed reading leads: {resp.status_code} {resp.text[:500]}")

        rows = resp.json()
        if not rows:
            break

        for row in rows:
            lead_id = clean_value(row.get(lead_id_col))
            lead_uuid = clean_value(row.get(lead_uuid_col))
            if lead_id is not None and lead_uuid is not None:
                lead_map[str(lead_id)] = str(lead_uuid)

        offset += page
        if len(rows) < page:
            break

    return lead_map


def fetch_existing_partners(partner_id_col: str, partner_name_col: str, partner_user_id_col: Optional[str]) -> Dict[str, Dict[str, Optional[str]]]:
    partners: Dict[str, Dict[str, Optional[str]]] = {}
    page = 1000
    offset = 0

    while True:
        select_cols = [partner_id_col, partner_name_col]
        if partner_user_id_col:
            select_cols.append(partner_user_id_col)
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/{PARTNERS_TABLE}",
            headers=HEADERS,
            params={
                "select": ",".join(select_cols),
                "offset": offset,
                "limit": page,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            fail(f"Failed reading partners: {resp.status_code} {resp.text[:500]}")

        rows = resp.json()
        if not rows:
            break

        for row in rows:
            partner_name = clean_value(row.get(partner_name_col))
            partner_id = clean_value(row.get(partner_id_col))
            if partner_name is not None and partner_id is not None:
                user_id = clean_value(row.get(partner_user_id_col)) if partner_user_id_col else None
                partners[str(partner_name).strip().lower()] = {
                    "partner_id": str(partner_id),
                    "user_id": str(user_id) if user_id else None,
                }

        offset += page
        if len(rows) < page:
            break

    return partners


def create_partner_auth_user(partner_name: str, dry_run: bool) -> Optional[str]:
    email = build_partner_email(partner_name)
    if dry_run:
        return None

    # Deterministic strong password so reruns are stable; partner can reset later.
    password = f"Tmp!{hashlib.sha1((partner_name + SUPABASE_KEY[:6]).encode('utf-8')).hexdigest()[:18]}"
    payload = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {
            "role": "partner",
            "partner_name": partner_name,
            "display_name": partner_name,
            "full_name": partner_name,
            "name": partner_name,
        },
    }
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=60,
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        return data.get("id")

    err = resp.text[:300]
    if "already" in err.lower() and "registered" in err.lower():
        return get_auth_user_id_by_email(email)

    print(f"  WARNING: failed creating auth user for '{partner_name}': {resp.status_code} {err}")
    return None


def update_auth_user_display_name(user_id: str, partner_name: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    resp = requests.put(
        f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "user_metadata": {
                    "role": "partner",
                    "partner_name": partner_name,
                    "display_name": partner_name,
                    "full_name": partner_name,
                    "name": partner_name,
                }
            }
        ),
        timeout=30,
    )
    return resp.status_code in (200, 204)


def get_auth_user_id_by_email(email: str) -> Optional[str]:
    # Admin list endpoint; we scan first pages and match exact email.
    page = 1
    per_page = 200
    max_pages = 20
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }

    for _ in range(max_pages):
        resp = requests.get(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=headers,
            params={"page": page, "per_page": per_page},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        users = data.get("users", []) if isinstance(data, dict) else []
        if not users:
            return None

        for user in users:
            if (user.get("email") or "").strip().lower() == email.strip().lower():
                return user.get("id")

        if len(users) < per_page:
            return None
        page += 1

    return None


def can_manage_auth_users() -> Tuple[bool, str]:
    resp = requests.get(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
        params={"page": 1, "per_page": 1},
        timeout=30,
    )
    if resp.status_code == 200:
        return True, ""
    return False, f"{resp.status_code} {resp.text[:300]}"


def build_unique_partner_rows(
    df: pd.DataFrame,
    lead_map: Dict[str, str],
    csv_partner_name_col: str,
    partner_name_col: str,
    partner_slug_col: Optional[str],
    partner_id_link_col: Optional[str],
    partner_uuid_link_col: Optional[str],
    partner_auth_user_id_col: Optional[str],
    supported_partner_fields: List[str],
    partners_id_col: Optional[str],
) -> Tuple[List[Dict], Dict[str, str], int, int]:
    """Build one partner row per unique partner name. Lead linking is optional.

    Returns:
        partner rows, lead_uuid -> partner_name for lead updates, count of rows
        skipped (no partner name), count of rows where CSV had lead_id but it
        was not found in the leads table (partner still imported).
    """
    partner_rows_by_name: Dict[str, Dict] = {}
    lead_uuid_to_partner_name: Dict[str, str] = {}
    unmatched_no_name = 0
    unknown_lead_id_rows = 0

    for _, row in df.iterrows():
        src_lead_id = clean_value(row.get(CSV_LINK_COL)) if CSV_LINK_COL in df.columns else None
        partner_name = clean_value(row.get(csv_partner_name_col))
        if partner_name is None:
            unmatched_no_name += 1
            continue

        partner_name_str = str(partner_name).strip()
        normalized_name = partner_name_str.lower()

        lead_uuid: Optional[str] = None
        if src_lead_id is not None:
            lead_uuid = lead_map.get(str(src_lead_id))
            if not lead_uuid:
                unknown_lead_id_rows += 1

        if lead_uuid:
            lead_uuid_to_partner_name[str(lead_uuid)] = partner_name_str

        if normalized_name not in partner_rows_by_name:
            payload = {
                partner_name_col: partner_name_str,
            }
            if partner_slug_col:
                payload[partner_slug_col] = build_partner_slug(partner_name_str)
            if partner_id_link_col and src_lead_id is not None and lead_uuid:
                payload[partner_id_link_col] = str(src_lead_id)
            if partner_uuid_link_col and lead_uuid:
                payload[partner_uuid_link_col] = str(lead_uuid)
            if partner_auth_user_id_col:
                payload[partner_auth_user_id_col] = None

            for field in supported_partner_fields:
                if field == partner_name_col:
                    continue
                payload[field] = clean_value(row.get(field))

            if partners_id_col and CSV_PARTNER_PK_FROM_CSV_COL in df.columns:
                vid = clean_value(row.get(CSV_PARTNER_PK_FROM_CSV_COL))
                if vid:
                    payload[partners_id_col] = str(vid)

            partner_rows_by_name[normalized_name] = payload
        else:
            # Same partner name on a later row: fill in lead link on the partner row if still empty.
            existing = partner_rows_by_name[normalized_name]
            if lead_uuid and partner_uuid_link_col and not existing.get(partner_uuid_link_col):
                if partner_id_link_col and src_lead_id is not None:
                    existing[partner_id_link_col] = str(src_lead_id)
                existing[partner_uuid_link_col] = str(lead_uuid)

    return (
        list(partner_rows_by_name.values()),
        lead_uuid_to_partner_name,
        unmatched_no_name,
        unknown_lead_id_rows,
    )


def normalize_batch_keys(rows: List[Dict]) -> List[Dict]:
    """PostgREST requires every object in a JSON array insert to have the same keys (PGRST102)."""
    if not rows:
        return rows
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    key_order = sorted(all_keys)
    return [{k: r.get(k) for k in key_order} for r in rows]


def upsert_batches(rows: List[Dict], on_conflict_col: Optional[str]) -> Tuple[int, int]:
    inserted = 0
    failed = 0

    total = len(rows)
    batches = math.ceil(total / BATCH_SIZE) if total else 0
    print(f"\nStep 5: Upserting {total} rows into '{PARTNERS_TABLE}' in {batches} batches...")

    for i in range(batches):
        start = i * BATCH_SIZE
        end = min(start + BATCH_SIZE, total)
        batch = normalize_batch_keys(rows[start:end])
        params = {}
        if on_conflict_col:
            params["on_conflict"] = on_conflict_col

        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/{PARTNERS_TABLE}",
            headers=HEADERS_MIN,
            params=params,
            data=json.dumps(batch),
            timeout=60,
        )

        if resp.status_code in (200, 201):
            inserted += len(batch)
            print(f"  Batch {i + 1}/{batches}: upserted {len(batch)} ({inserted}/{total})")
        else:
            print(f"  Batch {i + 1}/{batches}: ERROR {resp.status_code} - {format_error(resp)}")
            print("    Trying row-by-row...")
            for item in batch:
                r = requests.post(
                    f"{SUPABASE_URL}/rest/v1/{PARTNERS_TABLE}",
                    headers=HEADERS_MIN,
                    params=params,
                    data=json.dumps(item),
                    timeout=30,
                )
                if r.status_code in (200, 201):
                    inserted += 1
                else:
                    failed += 1
                    if failed <= 5:
                        print(f"    Failed row sample: {format_error(r)}")
            print(f"    Row-by-row done. Inserted={inserted}, Failed={failed}")

        time.sleep(0.05)

    return inserted, failed


def update_leads_partner_id(lead_partner_map: Dict[str, str], leads_uuid_col: str, leads_partner_id_col: str, dry_run: bool) -> Tuple[int, int]:
    if dry_run:
        return 0, 0

    updated = 0
    failed = 0
    total = len(lead_partner_map)
    print(f"\nStep 6: Updating leads.{leads_partner_id_col} for {total} leads...")

    for i, (lead_uuid, partner_id) in enumerate(lead_partner_map.items(), start=1):
        resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{LEADS_TABLE}",
            headers=HEADERS_MIN,
            params={leads_uuid_col: f"eq.{lead_uuid}"},
            data=json.dumps({leads_partner_id_col: partner_id}),
            timeout=30,
        )
        if resp.status_code in (200, 204):
            updated += 1
        else:
            failed += 1
            if failed <= 5:
                print(f"  Failed lead update sample: {resp.status_code} {resp.text[:200]}")

        if i % 500 == 0:
            print(f"  Progress: {i}/{total}")
        time.sleep(0.02)

    return updated, failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import partners into Supabase. Optional lead_id links to leads when present and resolvable."
    )
    parser.add_argument(
        "--csv-path",
        default=CSV_PATH,
        help="Path to partners/leads CSV file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all checks/mapping but do not insert rows into partners.",
    )
    args = parser.parse_args()

    print("Step 1: Reading CSV...")
    csv_path = args.csv_path
    if not os.path.exists(csv_path):
        fail(f"CSV not found: {csv_path}")
    df = pd.read_csv(csv_path, low_memory=False)
    print(f"  Loaded {len(df)} rows")
    print(f"  CSV columns: {list(df.columns)}")

    if CSV_LINK_COL not in df.columns:
        df[CSV_LINK_COL] = None
        print(f"  No '{CSV_LINK_COL}' column — partners will be imported without lead links.")

    print("\nStep 2: Checking table structure (leads + partners)...")
    leads_uuid_col = pick_first_existing(LEADS_TABLE, LEADS_UUID_CANDIDATES)
    leads_close_id_col = pick_first_existing(LEADS_TABLE, LEADS_CLOSE_ID_CANDIDATES)
    leads_partner_id_col = pick_first_existing(LEADS_TABLE, LEADS_PARTNER_ID_CANDIDATES)
    partners_link_id_col = pick_first_existing(PARTNERS_TABLE, PARTNERS_LINK_ID_CANDIDATES)
    partners_link_uuid_col = pick_first_existing(PARTNERS_TABLE, PARTNERS_LINK_UUID_CANDIDATES)
    partners_name_col = pick_first_existing(PARTNERS_TABLE, PARTNERS_NAME_CANDIDATES)
    partners_id_col = pick_first_existing(PARTNERS_TABLE, PARTNERS_ID_CANDIDATES)
    partners_auth_user_id_col = pick_first_existing(PARTNERS_TABLE, PARTNERS_AUTH_USER_ID_CANDIDATES)
    partners_slug_col = pick_first_existing(PARTNERS_TABLE, PARTNERS_SLUG_CANDIDATES)

    if not leads_uuid_col:
        fail(f"Could not find leads UUID column. Tried: {LEADS_UUID_CANDIDATES}")
    if not leads_close_id_col:
        fail(f"Could not find leads ID column. Tried: {LEADS_CLOSE_ID_CANDIDATES}")
    if not leads_partner_id_col:
        fail(f"Could not find leads partner link column. Tried: {LEADS_PARTNER_ID_CANDIDATES}")
    if not partners_name_col:
        fail(f"Could not find partners name column. Tried: {PARTNERS_NAME_CANDIDATES}")
    if not partners_id_col:
        fail(f"Could not find partners id column. Tried: {PARTNERS_ID_CANDIDATES}")
    csv_partner_name_col = next((c for c in CSV_PARTNER_NAME_CANDIDATES if c in df.columns), None)
    if not csv_partner_name_col:
        fail(f"Could not find CSV partner name column. Tried: {CSV_PARTNER_NAME_CANDIDATES}")

    print(f"  Leads key columns: id='{leads_close_id_col}', uuid='{leads_uuid_col}', partner_id='{leads_partner_id_col}'")
    print(f"  Partners columns: id='{partners_id_col}', name='{partners_name_col}'")
    if partners_slug_col:
        print(f"  Partners slug column: '{partners_slug_col}'")
    else:
        print("  Partners slug column not found; skipping this field.")
    print(f"  CSV partner name column: '{csv_partner_name_col}'")
    if partners_link_id_col:
        print(f"  Partners lead-id link column: '{partners_link_id_col}'")
    else:
        print("  Partners lead-id link column not found; skipping this field.")
    if partners_link_uuid_col:
        print(f"  Partners lead-uuid link column: '{partners_link_uuid_col}'")
    else:
        print("  Partners lead-uuid link column not found; skipping this field.")
    if partners_auth_user_id_col:
        print(f"  Partners auth user column: '{partners_auth_user_id_col}'")
    else:
        print("  Partners auth user column not found; auth users will still be created.")

    _csv_partner_data_candidates = CSV_FIELD_CANDIDATES + CSV_OPTIONAL_PARTNER_FIELD_CANDIDATES
    supported_partner_fields = [
        col for col in _csv_partner_data_candidates if col in df.columns and table_has_column(PARTNERS_TABLE, col)
    ]
    skipped_csv_fields = [col for col in _csv_partner_data_candidates if col in df.columns and col not in supported_partner_fields]
    if supported_partner_fields:
        print(f"  Supported partner data columns: {supported_partner_fields}")
    if skipped_csv_fields:
        print(f"  CSV columns not found in partners table (skipped): {skipped_csv_fields}")

    print("\nStep 3: Building map from leads table...")
    lead_map = fetch_lead_map(leads_close_id_col, leads_uuid_col)
    print(f"  Loaded {len(lead_map)} lead mappings")

    rows_to_insert, lead_uuid_to_partner_name, unmatched_no_name, unknown_lead_id_rows = build_unique_partner_rows(
        df=df,
        lead_map=lead_map,
        csv_partner_name_col=csv_partner_name_col,
        partner_name_col=partners_name_col,
        partner_slug_col=partners_slug_col,
        partner_id_link_col=partners_link_id_col,
        partner_uuid_link_col=partners_link_uuid_col,
        partner_auth_user_id_col=partners_auth_user_id_col,
        supported_partner_fields=supported_partner_fields,
        partners_id_col=partners_id_col,
    )

    print(f"  Unique partners to upsert: {len(rows_to_insert)}")
    print(f"  Leads to link with partner_id (after upsert): {len(lead_uuid_to_partner_name)}")
    print(f"  Rows skipped (missing partner name): {unmatched_no_name}")
    print(
        f"  CSV rows with lead_id not found in '{LEADS_TABLE}' (partner still imported): "
        f"{unknown_lead_id_rows}"
    )

    print("\nStep 4: Checking existing partners...")
    existing_partners = fetch_existing_partners(partners_id_col, partners_name_col, partners_auth_user_id_col)

    # Validate Auth Admin access once before attempting to create any users.
    # This prevents a long stream of per-partner 401 warnings.
    auth_ok = True
    auth_err = ""
    if not args.dry_run and rows_to_insert:
        auth_ok, auth_err = can_manage_auth_users()
        if not auth_ok:
            fail(
                "Cannot create Supabase Auth users via Admin API. "
                f"Got: {auth_err}. "
                "Set SUPABASE_SERVICE_ROLE_KEY to your project's service_role secret (not anon/publishable), then rerun."
            )

    existing_count = 0
    create_count = 0
    update_count = 0
    rows_to_upsert = []
    missing_user_id = 0
    for row in rows_to_insert:
        name_key = str(row.get(partners_name_col, "")).strip().lower()
        existing = existing_partners.get(name_key)
        if existing:
            existing_count += 1
            update_count += 1
            auth_user_id = existing.get("user_id")
        else:
            create_count += 1
            auth_user_id = create_partner_auth_user(str(row.get(partners_name_col, "")), args.dry_run)

        if partners_auth_user_id_col:
            row[partners_auth_user_id_col] = auth_user_id
            if not auth_user_id:
                missing_user_id += 1
            else:
                # Ensure display name is kept in sync for both new and existing auth users.
                ok = update_auth_user_display_name(auth_user_id, str(row.get(partners_name_col, "")), args.dry_run)
                if not ok:
                    print(f"  WARNING: Failed updating display_name for auth user {auth_user_id}")
        rows_to_upsert.append(row)

    print(f"  Existing partners found by unique name: {existing_count}")
    print(f"  Partners to create: {create_count}")
    print(f"  Partners to update: {update_count}")
    print(f"  Total partners to upsert: {len(rows_to_upsert)}")

    if args.dry_run:
        print("\nDry run mode: no inserts will be made.")
        sample = rows_to_upsert[:3]
        if sample:
            print("  Sample mapped rows:")
            print(json.dumps(sample, indent=2))
        else:
            print("  No matched rows to insert.")
        print("\nDone (dry run)!")
        print(f"  Would upsert partners: {len(rows_to_upsert)}")
        print(f"  Would link leads.partner_id rows: {len(lead_uuid_to_partner_name)}")
        print(f"  Unmatched (no partner name): {unmatched_no_name}")
        print(f"  Unknown lead_id in CSV (partners still imported): {unknown_lead_id_rows}")
        return

    if partners_auth_user_id_col and missing_user_id > 0:
        fail(
            f"{missing_user_id} partner rows are missing '{partners_auth_user_id_col}'. "
            "Cannot insert because this column is NOT NULL. "
            "Check service role key permissions and existing auth users."
        )

    conflict_col = partners_slug_col if partners_slug_col else partners_name_col
    inserted, failed = upsert_batches(rows_to_upsert, conflict_col)

    # Re-read partners so we can map every partner name to its final partners.id
    all_partners = fetch_existing_partners(partners_id_col, partners_name_col, partners_auth_user_id_col)
    lead_partner_id_map: Dict[str, str] = {}
    for lead_uuid, partner_name in lead_uuid_to_partner_name.items():
        info = all_partners.get(partner_name.strip().lower())
        pid = info.get("partner_id") if info else None
        if pid:
            lead_partner_id_map[lead_uuid] = pid

    linked, link_failed = update_leads_partner_id(
        lead_partner_map=lead_partner_id_map,
        leads_uuid_col=leads_uuid_col,
        leads_partner_id_col=leads_partner_id_col,
        dry_run=args.dry_run,
    )

    print("\nDone!")
    print(f"  Partners upserted: {inserted}")
    print(f"  Partner upsert failures: {failed}")
    print(f"  Leads linked to partner_id: {linked}")
    print(f"  Lead link failures: {link_failed}")
    print(f"  Skipped rows (no partner name): {unmatched_no_name}")
    print(f"  CSV rows with unknown lead_id (partners still imported): {unknown_lead_id_rows}")


if __name__ == "__main__":
    main()
