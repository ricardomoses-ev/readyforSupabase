import argparse
import json
import os
import re
from typing import Dict, List, Optional

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
PARTNERS_TABLE = "partners"

PARTNERS_NAME_CANDIDATES = ["matched_partner_name", "partner_name", "name"]
PARTNERS_AUTH_USER_ID_CANDIDATES = ["auth_user_id", "user_id"]


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


if not SUPABASE_URL or not SUPABASE_KEY:
    fail(
        "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first.\n"
        '  $env:SUPABASE_URL = "https://your-project-ref.supabase.co"\n'
        '  $env:SUPABASE_SERVICE_ROLE_KEY = "your-service-role-key"'
    )


HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
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


def fetch_partners(partner_name_col: str, auth_user_id_col: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    offset = 0
    limit = 1000
    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/{PARTNERS_TABLE}",
            headers=HEADERS,
            params={
                "select": f"{partner_name_col},{auth_user_id_col}",
                "limit": limit,
                "offset": offset,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            fail(f"Failed reading partners: {resp.status_code} {resp.text[:400]}")
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return rows


def fetch_auth_user_map() -> Dict[str, Dict]:
    user_map: Dict[str, Dict] = {}
    page = 1
    per_page = 200
    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=HEADERS,
            params={"page": page, "per_page": per_page},
            timeout=60,
        )
        if resp.status_code != 200:
            fail(
                "Failed reading auth users. Make sure SUPABASE_SERVICE_ROLE_KEY is a service role key.\n"
                f"Got: {resp.status_code} {resp.text[:400]}"
            )
        payload = resp.json()
        users = payload.get("users", []) if isinstance(payload, dict) else []
        if not users:
            break
        for user in users:
            user_id = user.get("id")
            if user_id:
                user_map[str(user_id)] = user
        if len(users) < per_page:
            break
        page += 1
    return user_map


def update_auth_user_metadata(user_id: str, metadata: Dict) -> bool:
    resp = requests.put(
        f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
        headers={**HEADERS, "Content-Type": "application/json"},
        data=json.dumps({"user_metadata": metadata}),
        timeout=30,
    )
    return resp.status_code in (200, 204)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Supabase Auth user display_name from partners table."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    partner_name_col = pick_first_existing(PARTNERS_TABLE, PARTNERS_NAME_CANDIDATES)
    auth_user_id_col = pick_first_existing(PARTNERS_TABLE, PARTNERS_AUTH_USER_ID_CANDIDATES)
    if not partner_name_col:
        fail(f"Could not find partners name column. Tried: {PARTNERS_NAME_CANDIDATES}")
    if not auth_user_id_col:
        fail(f"Could not find partners auth user id column. Tried: {PARTNERS_AUTH_USER_ID_CANDIDATES}")

    partners = fetch_partners(partner_name_col, auth_user_id_col)
    auth_users = fetch_auth_user_map()

    print(f"Loaded partners: {len(partners)}")
    print(f"Loaded auth users: {len(auth_users)}")
    print(f"Mode: {'APPLY' if not dry_run else 'DRY RUN'}")

    skipped_no_user_id = 0
    skipped_user_missing = 0
    unchanged = 0
    planned = 0
    updated = 0
    failed = 0

    for row in partners:
        partner_name = (row.get(partner_name_col) or "").strip()
        auth_user_id = (row.get(auth_user_id_col) or "").strip()
        if not partner_name:
            continue
        if not auth_user_id:
            skipped_no_user_id += 1
            continue

        user = auth_users.get(auth_user_id)
        if not user:
            skipped_user_missing += 1
            continue

        current_meta = user.get("user_metadata") or {}
        next_meta = {
            **current_meta,
            "role": "partner",
            "partner_name": partner_name,
            "display_name": partner_name,
            "full_name": partner_name,
            "name": partner_name,
        }
        if next_meta == current_meta:
            unchanged += 1
            continue

        planned += 1
        if dry_run:
            continue

        ok = update_auth_user_metadata(auth_user_id, next_meta)
        if ok:
            updated += 1
        else:
            failed += 1
            if failed <= 5:
                print(f"  Failed update sample user_id={auth_user_id}")

    print("\nDone.")
    print(f"  Planned updates: {planned}")
    print(f"  Updated: {updated}")
    print(f"  Unchanged: {unchanged}")
    print(f"  Skipped (missing partner auth user id): {skipped_no_user_id}")
    print(f"  Skipped (auth user id not found): {skipped_user_missing}")
    print(f"  Failed: {failed}")
    if dry_run:
        print("\nRe-run with --apply to execute updates.")


if __name__ == "__main__":
    main()
