import argparse
import os
from typing import Any, Dict, Iterable, List, Optional, Set

import pandas as pd
import requests


GEN1_ACTIVITY_NAME = "GEN1. Referral Upload"
GEN1_ACTIVITY_TYPE_ID = "actitype_1CKUCsigQLAPoNmDABmjcj"


def load_dotenv(dotenv_path: str) -> None:
    if not os.path.exists(dotenv_path):
        return
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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
        value = value.strip()
        return value or None
    return value


def pick_value(*values: Any) -> Any:
    for value in values:
        value = clean_value(value)
        if value is not None:
            return value
    return None


def list_to_text(value: Any) -> Any:
    value = clean_value(value)
    if isinstance(value, list):
        cleaned = [str(v).strip() for v in value if clean_value(v) is not None]
        return ", ".join(cleaned) if cleaned else None
    return value


def req_get_list(url: str, headers: Dict[str, str], params: Dict[str, str], timeout_s: int = 60) -> List[Dict[str, Any]]:
    r = requests.get(url, headers=headers, params=params, timeout=timeout_s)
    if r.status_code != 200:
        raise RuntimeError(f"GET failed: {r.status_code}: {r.text[:500]}")
    data = r.json()
    return data if isinstance(data, list) else []


def close_get_json(url: str, close_api_key: str, params: Optional[Dict[str, str]] = None, timeout_s: int = 60) -> Dict[str, Any]:
    r = requests.get(url, auth=(close_api_key, ""), params=params, timeout=timeout_s)
    if r.status_code != 200:
        raise RuntimeError(f"Close GET failed: {r.status_code}: {r.text[:500]}")
    data = r.json()
    return data if isinstance(data, dict) else {}


def fetch_close_field_map(close_api_key: str, activity_type_id: str) -> Dict[str, str]:
    data = close_get_json("https://api.close.com/api/v1/custom_activity", close_api_key)
    item = next((x for x in data.get("data", []) if x.get("id") == activity_type_id), None)
    if not item:
        raise RuntimeError(f"Close custom activity type not found: {activity_type_id}")
    return {f"custom.{field['id']}": field.get("name", "") for field in item.get("fields", [])}


def fetch_close_user_name(close_api_key: str, user_id: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    user_id = str(user_id)
    if user_id in cache:
        return cache[user_id]
    data = close_get_json(f"https://api.close.com/api/v1/user/{user_id}/", close_api_key)
    name = " ".join([part for part in [data.get("first_name"), data.get("last_name")] if clean_value(part)]) or None
    cache[user_id] = name
    return name


def fetch_close_contact_name(close_api_key: str, contact_id: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    contact_id = str(contact_id)
    if contact_id in cache:
        return cache[contact_id]
    data = close_get_json(f"https://api.close.com/api/v1/contact/{contact_id}/", close_api_key)
    name = clean_value(data.get("name")) or clean_value(data.get("display_name"))
    cache[contact_id] = name
    return name


def normalize_close_activity(activity: Dict[str, Any], field_map: Dict[str, str]) -> Dict[str, Any]:
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
        "status": activity.get("status"),
        "fields": named_fields,
    }


def fetch_close_gen1_activities(
    close_api_key: str,
    field_map: Dict[str, str],
    limit: int = 0,
    page_size: int = 100,
) -> List[Dict[str, Any]]:
    skip = 0
    matched: List[Dict[str, Any]] = []
    while True:
        data = close_get_json(
            "https://api.close.com/api/v1/activity/custom/",
            close_api_key,
            params={"_skip": str(skip), "_limit": str(page_size)},
            timeout_s=90,
        )
        rows = data.get("data", [])
        if not rows:
            break
        for activity in rows:
            if activity.get("custom_activity_type_id") != GEN1_ACTIVITY_TYPE_ID:
                continue
            matched.append(normalize_close_activity(activity, field_map))
            if limit and len(matched) >= limit:
                return matched
        if not data.get("has_more"):
            break
        skip += page_size
    return matched


def upsert_batch(base_url: str, headers: Dict[str, str], rows: List[Dict[str, Any]], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        print(f"  [dry-run] would upsert rows={len(rows)}")
        return len(rows)

    # partner_referral.custom_activity_uuid is NOT NULL.
    # We translate from Close's activity id (custom_activity_id) -> custom_activities.uuid.
    missing_uuid = [r for r in rows if clean_value(r.get("custom_activity_uuid")) is None]
    if missing_uuid:
        ids = [str(r.get("custom_activity_id")) for r in missing_uuid if clean_value(r.get("custom_activity_id")) is not None]
        if ids:
            # Fetch uuids in chunks to avoid very long IN clauses.
            id_to_uuid: Dict[str, str] = {}
            for id_chunk in chunked(sorted(set(ids)), 120):
                id_list = ",".join(id_chunk)
                existing_rows = req_get_list(
                    f"{base_url}/rest/v1/custom_activities",
                    headers,
                    {
                        "select": "custom_activity_id,uuid",
                        "custom_activity_id": f"in.({id_list})",
                    },
                    timeout_s=60,
                )
                for er in existing_rows:
                    if clean_value(er.get("custom_activity_id")) is None or clean_value(er.get("uuid")) is None:
                        continue
                    id_to_uuid[str(er["custom_activity_id"])] = str(er["uuid"])

            # Create missing custom_activities rows so we can satisfy the FK.
            missing_ids = [i for i in ids if i not in id_to_uuid]
            if missing_ids:
                insert_rows: List[Dict[str, Any]] = []
                missing_set = set(missing_ids)
                for r in rows:
                    if str(r.get("custom_activity_id")) not in missing_set:
                        continue
                    meta_lead_id = clean_value(r.get("_meta_lead_id"))
                    if meta_lead_id is None:
                        continue
                    insert_rows.append(
                        {
                            "lead_id": meta_lead_id,
                            "custom_activity_id": clean_value(r.get("custom_activity_id")),
                            "custom_activity_type_id": r.get("_meta_custom_activity_type_id", {GEN1_ACTIVITY_NAME: GEN1_ACTIVITY_TYPE_ID}),
                            "source_system": "close_crm",
                        }
                    )

                if insert_rows:
                    for batch in chunked(insert_rows, 50):
                        resp = requests.post(
                            f"{base_url}/rest/v1/custom_activities",
                            headers={**headers, "Prefer": "return=minimal"},
                            json=batch,
                            timeout=120,
                        )
                        # If bulk insert fails, retry row-by-row (still best effort).
                        if resp.status_code not in (200, 201):
                            for item in batch:
                                r2 = requests.post(
                                    f"{base_url}/rest/v1/custom_activities",
                                    headers={**headers, "Prefer": "return=minimal"},
                                    json=[item],
                                    timeout=60,
                                )
                                # Ignore failures per-row; we'll just refetch what we can.
                                _ = r2.status_code

                # Refetch UUIDs after inserts.
                id_to_uuid.clear()
                for id_chunk in chunked(sorted(set(ids)), 120):
                    id_list = ",".join(id_chunk)
                    existing_rows = req_get_list(
                        f"{base_url}/rest/v1/custom_activities",
                        headers,
                        {
                            "select": "custom_activity_id,uuid",
                            "custom_activity_id": f"in.({id_list})",
                        },
                        timeout_s=60,
                    )
                    for er in existing_rows:
                        if clean_value(er.get("custom_activity_id")) is None or clean_value(er.get("uuid")) is None:
                            continue
                        id_to_uuid[str(er["custom_activity_id"])] = str(er["uuid"])

            # Fill missing uuids, and drop rows we can't resolve.
            resolved_rows: List[Dict[str, Any]] = []
            for r in rows:
                if clean_value(r.get("custom_activity_uuid")) is not None:
                    resolved_rows.append(r)
                    continue
                caid = r.get("custom_activity_id")
                if caid is None:
                    continue
                uuid_val = id_to_uuid.get(str(caid))
                if uuid_val is None:
                    continue
                r["custom_activity_uuid"] = uuid_val
                resolved_rows.append(r)
            rows = resolved_rows

    if not rows:
        return 0

    # partner_referral table must not receive meta keys.
    for r in rows:
        r.pop("_meta_lead_id", None)
        r.pop("_meta_custom_activity_type_id", None)

    resp = requests.post(
        f"{base_url}/rest/v1/partner_referral",
        headers={**headers, "Prefer": "return=minimal,resolution=merge-duplicates"},
        params={"on_conflict": "custom_activity_uuid"},
        json=rows,
        timeout=120,
    )
    if resp.status_code in (200, 201):
        return len(rows)

    # Fallback to patch existing rows and insert missing rows by custom_activity_id.
    done = 0
    ids: List[str] = [str(row.get("custom_activity_id")) for row in rows if clean_value(row.get("custom_activity_id")) is not None]
    existing: Set[str] = set()
    if ids:
        id_list = ",".join(ids)
        existing_rows = req_get_list(
            f"{base_url}/rest/v1/partner_referral",
            headers,
            {"select": "custom_activity_id", "custom_activity_id": f"in.({id_list})"},
        )
        existing = {str(row["custom_activity_id"]) for row in existing_rows if clean_value(row.get("custom_activity_id")) is not None}

    for row in rows:
        key = row.get("custom_activity_id")
        if not key:
            continue
        if str(key) in existing:
            patch_resp = requests.patch(
                f"{base_url}/rest/v1/partner_referral",
                headers={**headers, "Prefer": "return=minimal"},
                params={"custom_activity_id": f"eq.{key}"},
                json=row,
                timeout=60,
            )
            if patch_resp.status_code not in (200, 204):
                raise RuntimeError(f"Patch failed: {patch_resp.status_code}: {patch_resp.text[:500]}")
        else:
            insert_resp = requests.post(
                f"{base_url}/rest/v1/partner_referral",
                headers={**headers, "Prefer": "return=minimal"},
                json=row,
                timeout=60,
            )
            if insert_resp.status_code not in (200, 201):
                raise RuntimeError(f"Insert failed: {insert_resp.status_code}: {insert_resp.text[:500]}")
        done += 1

    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill public.partner_referral from Close GEN1. Referral Upload activities.")
    parser.add_argument("--dotenv-path", default="dashboard/.env")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(args.dotenv_path)

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    close_api_key = os.environ.get("CLOSE_API_KEY", "")
    if not supabase_url or not supabase_key or not close_api_key:
        raise SystemExit("ERROR: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, and CLOSE_API_KEY are required.")

    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}", "Content-Type": "application/json"}
    field_map = fetch_close_field_map(close_api_key, GEN1_ACTIVITY_TYPE_ID)
    print(f"Loaded Close field map: {len(field_map)} fields for {GEN1_ACTIVITY_NAME}")

    user_name_cache: Dict[str, Optional[str]] = {}
    contact_name_cache: Dict[str, Optional[str]] = {}
    close_activity_cache: Dict[str, Optional[Dict[str, Any]]] = {}

    processed_pages = 0
    processed_ca = 0  # total custom activities scanned from Close (not just GEN1)
    matched_ca = 0   # total GEN1 activities found
    upserted = 0

    page_size = min(args.page_size, 100)
    skip = 0
    to_upsert: List[Dict[str, Any]] = []

    while True:
        processed_pages += 1
        close_data = close_get_json(
            "https://api.close.com/api/v1/activity/custom/",
            close_api_key,
            params={"_skip": str(skip), "_limit": str(page_size)},
            timeout_s=90,
        )
        rows = close_data.get("data", []) if isinstance(close_data, dict) else []
        if not rows:
            break

        for activity in rows:
            processed_ca += 1
            if activity.get("custom_activity_type_id") != GEN1_ACTIVITY_TYPE_ID:
                continue

            matched_ca += 1
            fields = {}
            # The activity already contains `custom.*` keys directly.
            for key, value in activity.items():
                if key.startswith("custom."):
                    field_name = field_map.get(key)
                    if field_name:
                        fields[field_name] = value

            partner_owner_raw = clean_value(fields.get("Partner Owner"))
            broker_raw = clean_value(fields.get("Broker to send to"))
            contact_at_partner_raw = clean_value(fields.get("Name of contact at partner"))

            partner_owner = (
                fetch_close_user_name(close_api_key, partner_owner_raw, user_name_cache)
                if isinstance(partner_owner_raw, str) and partner_owner_raw.startswith("user_")
                else partner_owner_raw
            )
            broker_to_send_to = (
                fetch_close_user_name(close_api_key, broker_raw, user_name_cache)
                if isinstance(broker_raw, str) and broker_raw.startswith("user_")
                else broker_raw
            )
            contact_at_partner = (
                fetch_close_contact_name(close_api_key, contact_at_partner_raw, contact_name_cache)
                if isinstance(contact_at_partner_raw, str) and contact_at_partner_raw.startswith("cont_")
                else contact_at_partner_raw
            )

            notes_val = fields.get("Notes")
            if isinstance(notes_val, dict):
                notes_val = str(notes_val)

            row = {
                "custom_activity_id": clean_value(activity.get("id")),
                "partner_owner": partner_owner,
                "broker_to_send_to": broker_to_send_to,
                "type_of_partner": list_to_text(fields.get("Type of Partner")),
                "company_name": clean_value(fields.get("Company Name")),
                "company_number": clean_value(fields.get("Company Number")),
                "contact_name": pick_value(clean_value(fields.get("Contact Name")), contact_at_partner),
                "contact_phone_number": clean_value(fields.get("Contact Phone Number")),
                "contact_email_address": clean_value(fields.get("Contact Email Address")),
                "fb_fbx": list_to_text(fields.get("FB/FBX")),
                "notes": clean_value(notes_val),
                "created_at": activity.get("date_created"),
                "updated_at": activity.get("date_updated"),
                # Meta needed to create the missing `custom_activities` row
                # (so we can satisfy the NOT NULL FK on partner_referral.custom_activity_uuid).
                "_meta_lead_id": clean_value(activity.get("lead_id")),
                "_meta_custom_activity_type_id": {GEN1_ACTIVITY_NAME: GEN1_ACTIVITY_TYPE_ID},
            }
            to_upsert.append(row)

            if args.limit and matched_ca >= args.limit:
                break

        if to_upsert and len(to_upsert) >= args.batch_size:
            for batch in chunked(to_upsert, args.batch_size):
                upserted += upsert_batch(supabase_url, headers, batch, args.dry_run)
            to_upsert = []

        print(f"Close progress: pages={processed_pages} skip={skip} scanned_ca={processed_ca} matched_gen1={matched_ca} upserted={upserted}")

        if args.limit and matched_ca >= args.limit:
            break
        if not close_data.get("has_more"):
            break
        skip += page_size

    if to_upsert:
        upserted += upsert_batch(supabase_url, headers, to_upsert, args.dry_run)

    print("\nDone.")
    print(f"processed_custom_activities_scanned={processed_ca}")
    print(f"matched_gen1_activities={matched_ca}")
    print(f"upserted_partner_referral_rows={upserted}")
    print("Note: Your table does not include `name_of_contact_at_partner`, so the Close value is mapped into `contact_name`.")

    print("\nDone.")
    print(f"processed_custom_activities={processed_ca}")
    print(f"matched_custom_activities={matched_ca}")
    print(f"upserted_partner_referral_rows={upserted}")
    print("Note: 'Name of contact at partner' is resolved from Close but stored in 'contact_name' because no matching snake_case column was found.")


if __name__ == "__main__":
    main()
