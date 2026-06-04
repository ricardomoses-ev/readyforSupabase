import csv
import argparse
import json
import math
import os
import uuid
from datetime import datetime
from pathlib import Path
from urllib import error, request


PROJECT_ROOT = Path(__file__).resolve().parent
CSV_PATH = PROJECT_ROOT / "Funding Bay & FBX Deals - Raw data v2.csv"
ENV_PATH = PROJECT_ROOT / "dashboard" / ".env"
SUPABASE_TABLE = "dealsheet_sync_v2"
BATCH_SIZE = 200


HEADER_MAP = {
    "YYYY-QX": "yyyy_qx",
    "YYYY-MM": "yyyy_mm",
    "YYYY-WW": "yyyy_ww",
    "Date": "date",
    "Company": "company",
    "Lender": "lender",
    "FBX/Funding Bay": "fbx_funding_bay",
    "Closer": "closer",
    "Originator": "originator",
    "RSA": "rsa",
    "IF/Non-IF": "if_non_if",
    "Type": "type",
    "Facility Type": "facility_type",
    "Facility Size": "facility_size",
    "Contract end date": "contract_end_date",
    "Notice period": "notice_period",
    "Service charge": "service_charge",
    "Monthly minimums": "monthly_minimums",
    "Arrangement Fee": "arrangement_fee",
    "Success Fee %": "success_fee_percent",
    "Success Fee Amount": "success_fee_amount",
    "Lender Fee Amount": "lender_fee_amount",
    "Gross Rev": "gross_rev",
    "Partner Introducer": "partner_introducer",
    "Paid Partner?": "paid_partner",
    "Partner Owner": "partner_owner",
    "Partner Comms - Success %": "partner_comms_success_percent",
    "Partner Comms - Success Amount": "partner_comms_success_amount",
    "Partner Comms - Lender %": "partner_comms_lender_percent",
    "Partner Comms - Lender Amount": "partner_comms_lender_amount",
    "Partner Comms - Total Amount": "partner_comms_total_amount",
    "Net Rev": "net_rev",
    "Lead Source": "lead_source",
    "Campaign": "campaign",
    "Sector": "sector",
    "WW": "week",
    "MM": "month",
    "QX": "quarter",
    "YYYY": "year",
}


def load_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        value = raw_value.strip().strip('"').strip("'")
        os.environ[key] = value


def as_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    normalized = value.strip().lower()
    if normalized in {"yes", "y", "true", "1"}:
        return True
    if normalized in {"no", "n", "false", "0"}:
        return False
    return None


def as_bigint(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    stripped = value.strip()
    if stripped == "":
        return None
    try:
        return int(float(stripped))
    except ValueError:
        return None


def as_date(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(stripped, fmt).date().isoformat()
            except ValueError:
                continue
        return None
    return None


def normalize(value):
    if value is None:
        return None
    # Keep true numeric zeros as "0" so they are not sent as null.
    if isinstance(value, (int, float)) and value == 0:
        return "0"
    if isinstance(value, bool):
        return "1" if value else "0"
    if not isinstance(value, str):
        return str(value)
    stripped = value.strip()
    return stripped if stripped else None


def transform_row(raw_row: dict) -> dict:
    row = {"dealsheet_uuid": str(uuid.uuid4())}

    for source_key, target_key in HEADER_MAP.items():
        raw_value = raw_row.get(source_key)
        if target_key in {"rsa", "paid_partner"}:
            row[target_key] = as_bool(raw_value)
        elif target_key == "year":
            row[target_key] = as_bigint(raw_value)
        elif target_key == "date":
            row[target_key] = as_date(raw_value)
        else:
            row[target_key] = normalize(raw_value)

    return row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import dealsheet CSV into Supabase dealsheet_sync_v2."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate mapping and UUID generation without inserting rows.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of rows to process (0 = all rows).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV file not found: {CSV_PATH}")

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = [transform_row(r) for r in reader]

    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    total = len(rows)
    if total == 0:
        print("No rows found to process.")
        return

    if args.dry_run:
        unique_uuids = len({r["dealsheet_uuid"] for r in rows})
        print(f"[DRY RUN] Processed {total} rows from CSV.")
        print(f"[DRY RUN] Generated UUIDs: {unique_uuids}/{total} unique.")
        print("[DRY RUN] No data was inserted into Supabase.")
        print("[DRY RUN] Sample transformed row:")
        print(json.dumps(rows[0], indent=2))
        return

    if not ENV_PATH.exists():
        raise FileNotFoundError(f"Env file not found: {ENV_PATH}")

    load_env_file(ENV_PATH)
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing.")

    api_url = f"{supabase_url}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    batches = math.ceil(total / BATCH_SIZE)
    inserted = 0
    print(f"Loaded {total} rows from CSV.")
    print(f"Inserting into {SUPABASE_TABLE} in {batches} batches...")

    for i in range(batches):
        start = i * BATCH_SIZE
        end = min(start + BATCH_SIZE, total)
        payload = rows[start:end]
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(api_url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=60) as resp:
                status_code = resp.getcode()
                resp_text = resp.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            status_code = exc.code
            resp_text = exc.read().decode("utf-8", errors="replace")
        except error.URLError as exc:
            raise RuntimeError(f"Batch {i + 1}/{batches} network error: {exc}") from exc

        if status_code != 201:
            raise RuntimeError(
                f"Batch {i + 1}/{batches} failed ({status_code}): {resp_text[:600]}"
            )
        inserted += len(payload)
        print(f"Batch {i + 1}/{batches}: inserted {inserted}/{total}")

    print("Import completed.")


if __name__ == "__main__":
    main()
