"""
Reset a Supabase Auth user's password via the Admin API (service role).

Requires:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY

Usage:
  python reset_password.py
  python reset_password.py --user-id <uuid> --password "OtherPass"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import requests

DEFAULT_USER_ID = "2bdd6e28-3a59-4668-8bc8-3e8ba545d9fd"
DEFAULT_PASSWORD = "TESTING"


def normalize_supabase_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""

    md_match = re.match(r"^\[.*\]\((https?://[^)]+)\)$", value)
    if md_match:
        value = md_match.group(1).strip()

    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()

    return value.rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset a Supabase Auth user password (admin).")
    parser.add_argument(
        "--user-id",
        default=DEFAULT_USER_ID,
        help=f"Auth user UUID (default: {DEFAULT_USER_ID})",
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help='New password (default: "TESTING")',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be sent without calling the API.",
    )
    args = parser.parse_args()

    supabase_url = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if not supabase_url or not service_key:
        print("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables.")
        print('  $env:SUPABASE_URL = "https://your-project-ref.supabase.co"')
        print('  $env:SUPABASE_SERVICE_ROLE_KEY = "your-service-role-key"')
        return 1

    if args.dry_run:
        print(f"Would PUT password for user {args.user_id} (dry run).")
        return 0

    url = f"{supabase_url}/auth/v1/admin/users/{args.user_id}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    body = {"password": args.password}

    resp = requests.put(url, headers=headers, data=json.dumps(body), timeout=30)

    if resp.status_code in (200, 204):
        print(f"OK: password updated for user {args.user_id}")
        return 0

    print(f"ERROR: {resp.status_code} {resp.text[:500]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
