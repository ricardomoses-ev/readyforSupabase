import argparse
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests


def load_dotenv_if_needed(dotenv_path: str) -> None:
    """
    Minimal .env loader (no extra deps).
    Only sets env vars that are currently missing.
    """
    required = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
    if all(os.environ.get(k) for k in required):
        return

    if not os.path.exists(dotenv_path):
        return

    with open(dotenv_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if not os.environ.get(k):
                os.environ[k] = v


def chunked(seq: List[Any], n: int) -> Iterable[List[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def clean_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, str):
        v = value.strip()
        return v if v else None
    return value


def pick_value(*values: Any) -> Any:
    for value in values:
        cleaned = clean_value(value)
        if cleaned is not None:
            return cleaned
    return None


def to_numeric(value: Any) -> Optional[float]:
    value = clean_value(value)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.replace(",", "").replace("£", "").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None
    return None


def to_int(value: Any) -> Optional[int]:
    n = to_numeric(value)
    if n is None:
        return None
    return int(n)


def req_get(url: str, headers: Dict[str, str], params: Dict[str, str], timeout_s: int = 60) -> List[Dict[str, Any]]:
    r = requests.get(url, headers=headers, params=params, timeout=timeout_s)
    if r.status_code != 200:
        raise RuntimeError(f"GET failed: {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data if isinstance(data, list) else []


def close_get_json(url: str, close_api_key: str, params: Optional[Dict[str, str]] = None, timeout_s: int = 60) -> Dict[str, Any]:
    r = requests.get(url, auth=(close_api_key, ""), params=params, timeout=timeout_s)
    if r.status_code != 200:
        raise RuntimeError(f"Close GET failed: {r.status_code}: {r.text[:500]}")
    data = r.json()
    return data if isinstance(data, dict) else {}


def fetch_close_type_field_map(close_api_key: str, activity_type_id: str) -> Dict[str, str]:
    data = close_get_json("https://api.close.com/api/v1/custom_activity", close_api_key)
    item = next((x for x in data.get("data", []) if x.get("id") == activity_type_id), None)
    if not item:
        raise RuntimeError(f"Close custom activity type not found: {activity_type_id}")
    return {f"custom.{field['id']}": field.get("name", "") for field in item.get("fields", [])}


def fetch_close_activity_for_lead(
    close_api_key: str,
    lead_id: str,
    activity_type_id: str,
    field_map: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    data = close_get_json(
        "https://api.close.com/api/v1/activity/custom/",
        close_api_key,
        params={"lead_id": lead_id, "custom_activity_type_id": activity_type_id},
    )
    rows = data.get("data", [])
    if not rows:
        return None
    rows = sorted(rows, key=lambda x: x.get("date_created") or "", reverse=True)
    activity = rows[0]

    named_fields: Dict[str, Any] = {}
    for key, value in activity.items():
        if key.startswith("custom."):
            field_name = field_map.get(key)
            if field_name:
                named_fields[field_name] = value

    return {
        "id": activity.get("id"),
        "lead_id": activity.get("lead_id"),
        "date_created": activity.get("date_created"),
        "date_updated": activity.get("date_updated"),
        "fields": named_fields,
    }


def upsert_lead_magnet_batch(
    base_url: str,
    headers: Dict[str, str],
    rows: List[Dict[str, Any]],
    batch_no: int,
    dry_run: bool,
    on_conflict_col: str = "custom_activity_uuid",
) -> int:
    if not rows:
        return 0

    if dry_run:
        print(f"  [dry-run] would upsert batch {batch_no} rows={len(rows)}")
        return len(rows)

    # PostgREST upsert: on_conflict + Prefer resolution.
    # Note: This assumes there's a unique constraint on `custom_activity_id`.
    resp = requests.post(
        f"{base_url}/rest/v1/lead_magnet",
        headers={**headers, "Prefer": "return=minimal,resolution=merge-duplicates"},
        params={"on_conflict": on_conflict_col},
        json=rows,
        timeout=120,
    )
    if resp.status_code in (200, 201):
        return len(rows)

    # If PostgREST can't resolve the conflict column (schema cache issue),
    # fall back to row-by-row patch/insert keyed by custom_activity_uuid.
    text = resp.text or ""
    if "PGRST204" in text or "Could not find the" in text and "schema cache" in text:
        done = 0
        for row in rows:
            row_key = row.get("custom_activity_uuid")
            if not row_key:
                continue

            patch_resp = requests.patch(
                f"{base_url}/rest/v1/lead_magnet",
                headers={**headers, "Prefer": "return=minimal"},
                params={"custom_activity_uuid": f"eq.{row_key}"},
                json=row,
                timeout=60,
            )
            if patch_resp.status_code in (200, 204):
                done += 1
                continue

            insert_resp = requests.post(
                f"{base_url}/rest/v1/lead_magnet",
                headers={**headers, "Prefer": "return=minimal"},
                json=row,
                timeout=60,
            )
            if insert_resp.status_code not in (200, 201):
                raise RuntimeError(
                    f"Patch/insert fallback failed: {insert_resp.status_code}: {insert_resp.text[:500]}"
                )
            done += 1

        return done

    raise RuntimeError(f"Upsert failed: {resp.status_code}: {resp.text[:500]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill public.lead_magnet from public.custom_activities (e.g. LeadMaggy)."
    )
    parser.add_argument("--activity-name", default="LeadMaggy", help="Key name inside custom_activity_type_id jsonb.")
    parser.add_argument("--activity-type-id", default="actitype_7F05YTbEK5kDTySb2WN7de", help="Value inside custom_activity_type_id jsonb.")
    parser.add_argument("--batch-size", type=int, default=200, help="Upsert batch size.")
    parser.add_argument("--page-size", type=int, default=1000, help="Custom activities page size.")
    parser.add_argument("--dry-run", action="store_true", help="Do not insert anything; just print counts.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of custom_activities to process (0 = no limit).")
    parser.add_argument("--dotenv-path", default="dashboard/.env", help="Path to dashboard/.env")
    parser.add_argument(
        "--close-leads-csv",
        default=os.path.join("output", "close_leads_supabase_ready.csv"),
        help="Optional Close leads CSV used to enrich lead_magnet values.",
    )
    args = parser.parse_args()

    load_dotenv_if_needed(args.dotenv_path)

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    close_api_key = os.environ.get("CLOSE_API_KEY", "")
    if not supabase_url or not supabase_key:
        raise SystemExit("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or provide a valid --dotenv-path).")

    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}", "Content-Type": "application/json"}

    csv_lookup: Dict[str, Dict[str, Any]] = {}
    if args.close_leads_csv and os.path.exists(args.close_leads_csv):
        csv_df = pd.read_csv(args.close_leads_csv, low_memory=False)
        if "close_lead_id" in csv_df.columns:
            for _, row in csv_df.iterrows():
                close_lead_id = clean_value(row.get("close_lead_id"))
                if close_lead_id is None:
                    continue
                csv_lookup[str(close_lead_id)] = row.to_dict()
            print(f"Loaded CSV enrichment rows: {len(csv_lookup)} from {args.close_leads_csv}")
        else:
            print(f"WARNING: CSV missing 'close_lead_id' column: {args.close_leads_csv}")
    else:
        print(f"CSV enrichment skipped; file not found: {args.close_leads_csv}")

    # Magnet columns (subset we can fill from leads).
    lead_select_cols = [
        "lead_source",
        "lead_magnet_source",
        "companies_house_url",
        "company_status",
        "company_type",
        "lender",
        "loan_amount",
        "net_assets",
        "profitability",
        "turnover",
        "years_of_trading",
        "business_model",
        "company_registration_number",
        "sic_code",
        "pay_per_lead",
        "close_lead_id",
    ]

    base = supabase_url
    close_field_map: Dict[str, str] = {}
    close_activity_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    if close_api_key:
        close_field_map = fetch_close_type_field_map(close_api_key, args.activity_type_id)
        print(f"Loaded Close field map: {len(close_field_map)} fields for {args.activity_name}")
    else:
        print("Close enrichment skipped; CLOSE_API_KEY is not set.")

    # Pagination over custom_activities.
    offset = 0
    processed_ca = 0
    matched_ca = 0
    upserted = 0

    # Cache leads so we don't fetch the same close_lead_id repeatedly.
    lead_cache: Dict[str, Dict[str, Any]] = {}

    while True:
        params = {
            "select": "lead_id,custom_activity_id,uuid,created_at,updated_at,custom_activity_type_id",
            "limit": str(args.page_size),
            "offset": str(offset),
        }
        ca_rows = req_get(f"{base}/rest/v1/custom_activities", headers=headers, params=params)
        if not ca_rows:
            break

        if args.limit and processed_ca >= args.limit:
            break

        # Filter client-side: custom_activity_type_id is jsonb like {"LeadMaggy":"actitype_...","GEN3...":"..."}
        # We'll match if the activity-name key matches AND the value matches the expected activity type id.
        wanted: List[Dict[str, Any]] = []
        for ca in ca_rows:
            processed_ca += 1
            if args.limit and processed_ca > args.limit:
                break

            ca_type_obj = ca.get("custom_activity_type_id")
            if not isinstance(ca_type_obj, dict):
                continue
            if ca_type_obj.get(args.activity_name) != args.activity_type_id:
                continue

            lead_id = ca.get("lead_id")
            if lead_id is None:
                continue

            wanted.append(ca)

        if not wanted:
            offset += args.page_size
            continue

        matched_ca += len(wanted)

        # Fetch needed leads in chunks via close_lead_id.
        lead_ids = sorted({str(ca["lead_id"]) for ca in wanted if ca.get("lead_id") is not None})
        missing = [lid for lid in lead_ids if lid not in lead_cache]

        # Try chunked IN first to reduce round-trips.
        for miss_chunk in chunked(missing, 120):
            lid_list = ",".join(miss_chunk)
            lead_params = {
                "select": ",".join(lead_select_cols),
                "close_lead_id": f"in.({lid_list})",
                "limit": "1000",
            }
            lead_rows = req_get(f"{base}/rest/v1/leads", headers=headers, params=lead_params)
            for l in lead_rows:
                cid = l.get("close_lead_id")
                if cid is not None:
                    lead_cache[str(cid)] = l

        # Build upsert payloads.
        to_upsert: List[Dict[str, Any]] = []
        for ca in wanted:
            lead_id = str(ca["lead_id"])
            l = lead_cache.get(lead_id)
            csv_row = csv_lookup.get(lead_id, {})
            close_activity = close_activity_cache.get(lead_id)
            if close_api_key and lead_id not in close_activity_cache:
                close_activity = fetch_close_activity_for_lead(
                    close_api_key=close_api_key,
                    lead_id=lead_id,
                    activity_type_id=args.activity_type_id,
                    field_map=close_field_map,
                )
                close_activity_cache[lead_id] = close_activity
            if not l:
                # If chunked IN failed due to length/format, fall back to per-lead lookup.
                # This should be rare.
                lead_params = {"select": ",".join(lead_select_cols), "close_lead_id": f"eq.{lead_id}", "limit": "1"}
                lead_rows = req_get(f"{base}/rest/v1/leads", headers=headers, params=lead_params)
                if not lead_rows:
                    continue
                l = lead_rows[0]
                lead_cache[lead_id] = l

            ca_uuid = ca.get("uuid")
            if ca_uuid is None:
                continue

            close_fields = (close_activity or {}).get("fields", {})

            row = {
                # Store both identifiers from custom_activities when available.
                "lead_id": lead_id,
                "custom_activity_id": pick_value((close_activity or {}).get("id"), ca.get("custom_activity_id")),
                "custom_activity_uuid": ca_uuid,
                "lead_source": pick_value(close_fields.get("Lead Source"), csv_row.get("lead_source"), l.get("lead_source")),
                "lead_magnet_source": pick_value(
                    close_fields.get("Lead Magnet Source"), csv_row.get("lead_magnet_source"), l.get("lead_magnet_source")
                ),
                "companies_house_url": pick_value(
                    close_fields.get("Companies House URL"), csv_row.get("companies_house_url"), l.get("companies_house_url")
                ),
                "company_status": pick_value(
                    close_fields.get("Company Status"), csv_row.get("company_status"), l.get("company_status")
                ),
                "company_type": pick_value(close_fields.get("Company Type"), csv_row.get("company_type"), l.get("company_type")),
                "lender": pick_value(close_fields.get("Lender"), csv_row.get("lender"), l.get("lender")),
                "loan_amount": pick_value(
                    to_numeric(close_fields.get("Loan Amount")),
                    to_numeric(csv_row.get("loan_amount")),
                    to_numeric(l.get("loan_amount")),
                ),
                "net_assets": pick_value(
                    to_numeric(close_fields.get("Net Assets")),
                    to_numeric(csv_row.get("net_assets")),
                    to_numeric(l.get("net_assets")),
                ),
                "profitability": pick_value(close_fields.get("Profitability"), csv_row.get("profitability"), l.get("profitability")),
                "turnover": pick_value(
                    to_numeric(close_fields.get("Turnover")),
                    to_numeric(csv_row.get("turnover")),
                    to_numeric(l.get("turnover")),
                ),
                "years_of_trading": pick_value(
                    to_int(close_fields.get("Years of trading")),
                    to_int(csv_row.get("years_of_trading")),
                    to_int(l.get("years_of_trading")),
                ),
                "business_model_use_of_funds": pick_value(
                    close_fields.get("Use of Fund"),
                    close_fields.get("Business Model"),
                    csv_row.get("business_model"),
                    l.get("business_model"),
                ),
                "company_reg": pick_value(
                    close_fields.get("Company registration number"),
                    csv_row.get("company_registration_number"),
                    l.get("company_registration_number"),
                ),
                "sic": pick_value(close_fields.get("SIC Code"), csv_row.get("sic_code"), l.get("sic_code")),
                "pay_per_lead_quotezone": pick_value(
                    to_numeric(close_fields.get("Pay Per Lead")),
                    to_numeric(csv_row.get("pay_per_lead")),
                    to_numeric(l.get("pay_per_lead")),
                ),
                "created_at": pick_value((close_activity or {}).get("date_created"), ca.get("created_at")),
                "updated_at": pick_value((close_activity or {}).get("date_updated"), ca.get("updated_at")),
            }
            to_upsert.append(row)

        # Upsert in batches.
        for i, batch in enumerate(chunked(to_upsert, args.batch_size), start=1):
            batch_no = (offset // args.page_size) * ((len(to_upsert) + args.batch_size - 1) // args.batch_size) + i
            upserted += upsert_lead_magnet_batch(
                base_url=base,
                headers=headers,
                rows=batch,
                batch_no=batch_no,
                dry_run=args.dry_run,
                on_conflict_col="custom_activity_uuid",
            )

        offset += args.page_size
        print(
            f"Progress: processed_ca={processed_ca} matched_ca={matched_ca} upserted={upserted} offset={offset}"
        )

    print("\nDone.")
    print(f"processed_custom_activities={processed_ca}")
    print(f"matched_custom_activities={matched_ca}")
    print(f"upserted_lead_magnet_rows={upserted} (including dry-run estimates)")


if __name__ == "__main__":
    main()

