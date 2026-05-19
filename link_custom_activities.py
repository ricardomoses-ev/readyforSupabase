import pandas as pd
import requests
import os
import json
import time

# Load credentials from dashboard/.env (other scripts do this too).
def load_dotenv(dotenv_path: str) -> None:
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
            os.environ.setdefault(k, v)


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_SCRIPT_DIR, "dashboard", ".env"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first")
    exit(1)

BASE = SUPABASE_URL.rstrip("/")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation,resolution=merge-duplicates",
}
HEADERS_MIN = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

# ── Step 1: Load CSV, type name mapping, and group by lead ──
print("Step 1: Loading custom activities CSV and type name mapping...")
csv_path_primary = r"c:\Users\Ricardo Moses\My Downloads\custom_activities_output (1).csv"
csv_path_fallback = r"c:\Users\Ricardo Moses\My Downloads\custom_activities_output.csv"
csv_path = csv_path_primary if os.path.exists(csv_path_primary) else csv_path_fallback
names_path = os.path.join("output", "activity_type_names.json")

df = pd.read_csv(csv_path)
with open(names_path) as f:
    type_name_map = json.load(f)

print(f"  {len(df)} activities for {df.lead_id.nunique()} unique leads")
print(f"  {len(type_name_map)} activity type names loaded")

grouped = df.sort_values("date_created", ascending=False).groupby("lead_id")
lead_activities = {}
for lead_id, group in grouped:
    activity_ids = group["activity_id"].dropna().tolist()
    type_ids = group["custom_activity_type_id"].dropna().unique().tolist()
    type_id_name_pairs = {
        type_name_map.get(tid, tid): tid for tid in type_ids
    }
    lead_activities[lead_id] = {
        "activity_ids": activity_ids,
        "type_id_name_pairs": type_id_name_pairs,
    }

print(f"  Grouped into {len(lead_activities)} lead records")

# ── Step 2: Fetch all leads from Supabase ──
print("\nStep 2: Fetching leads from Supabase...")
all_leads = []
offset = 0
PAGE = 1000
while True:
    r = requests.get(
        f"{BASE}/rest/v1/leads?select=id,close_lead_id&offset={offset}&limit={PAGE}",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    batch = r.json()
    if not batch:
        break
    all_leads.extend(batch)
    offset += PAGE
    if len(batch) < PAGE:
        break

lead_map = {l["close_lead_id"]: l["id"] for l in all_leads}
print(f"  Found {len(lead_map)} leads in Supabase")

# ── Step 3: Build custom_activity rows ──
print("\nStep 3: Building custom activity rows...")
matched = 0
unmatched = 0
rows_to_insert = []

for close_lead_id, data in lead_activities.items():
    if close_lead_id not in lead_map:
        unmatched += 1
        continue
    matched += 1

    rows_to_insert.append({
        "insert_row": {
            "lead_id": close_lead_id,
            "custom_activity_id": data["activity_ids"][0],
            "custom_activity_type_id": data["type_id_name_pairs"],
            "source_system": "close_crm",
        },
        "lead_uuid": lead_map[close_lead_id],
        "close_lead_id": close_lead_id,
    })

print(f"  Matched: {matched}, Unmatched: {unmatched}")

# ── Step 4: Insert into custom_activities ──
print(f"\nStep 4: Inserting {len(rows_to_insert)} custom activities...")
activity_uuid_map = {}
inserted = 0
failed = 0

BATCH_SIZE = 50
batches_list = [rows_to_insert[i:i+BATCH_SIZE] for i in range(0, len(rows_to_insert), BATCH_SIZE)]

for bi, batch in enumerate(batches_list):
    insert_rows = [r["insert_row"] for r in batch]

    r = requests.post(
        f"{BASE}/rest/v1/custom_activities",
        headers=HEADERS,
        data=json.dumps(insert_rows),
        params={"on_conflict": "custom_activity_id"},
    )

    if r.status_code in (200, 201):
        results = r.json()
        for j, res in enumerate(results):
            activity_uuid_map[batch[j]["close_lead_id"]] = res["uuid"]
        inserted += len(results)
        print(f"  Batch {bi+1}/{len(batches_list)}: inserted {len(results)} ({inserted}/{len(rows_to_insert)})")
    else:
        print(f"  Batch {bi+1} ERROR: {r.text[:250]}")
        print("  Trying row-by-row...")
        for j, item in enumerate(batch):
            r2 = requests.post(
                f"{BASE}/rest/v1/custom_activities",
                headers=HEADERS,
                data=json.dumps(item["insert_row"]),
            )
            if r2.status_code == 201:
                res = r2.json()
                if isinstance(res, list):
                    res = res[0]
                activity_uuid_map[item["close_lead_id"]] = res["uuid"]
                inserted += 1
            else:
                failed += 1
                if failed <= 5:
                    print(f"    FAILED ({item['close_lead_id']}): {r2.text[:150]}")
        print(f"  Row-by-row done. Inserted: {inserted}, Failed: {failed}")
    time.sleep(0.05)

print(f"\n  Total inserted: {inserted}, Failed: {failed}")

# ── Step 5: Update leads.custom_activity_uuid ──
print(f"\nStep 5: Updating {len(activity_uuid_map)} leads with custom_activity_uuid...")
updated = 0
update_failed = 0

for close_lead_id, activity_uuid in activity_uuid_map.items():
    lead_uuid = lead_map.get(close_lead_id)
    if not lead_uuid:
        continue

    r = requests.patch(
        f"{BASE}/rest/v1/leads?id=eq.{lead_uuid}",
        headers=HEADERS_MIN,
        data=json.dumps({"custom_activity_uuid": activity_uuid}),
    )
    if r.status_code in (200, 204):
        updated += 1
    else:
        update_failed += 1
        if update_failed <= 5:
            print(f"  Update failed for {close_lead_id}: {r.text[:150]}")

    if updated % 500 == 0 and updated > 0:
        print(f"  Updated {updated}/{len(activity_uuid_map)}...")
    time.sleep(0.02)

print(f"\nDone!")
print(f"  Custom activities inserted: {inserted}")
print(f"  Leads linked (custom_activity_uuid set): {updated}")
print(f"  Failures: insert={failed}, update={update_failed}")
