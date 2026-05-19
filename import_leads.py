import pandas as pd
import numpy as np
import requests
import os
import math
import json
import time

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables.")
    print('  $env:SUPABASE_URL = "https://your-project.supabase.co"')
    print('  $env:SUPABASE_SERVICE_ROLE_KEY = "your-service-role-key"')
    exit(1)

API_URL = f"{SUPABASE_URL.rstrip('/')}/rest/v1/leads"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal,resolution=merge-duplicates",
}

csv_path = os.path.join("output", "close_leads_supabase_ready.csv")
df = pd.read_csv(csv_path, low_memory=False)
print(f"Loaded {len(df)} rows, {len(df.columns)} columns")

SCHEMA_INT_COLS = {"years_of_trading"}

import re
DD_MM_YYYY = re.compile(r'^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}:\d{2}(?::\d{2})?))?(?:\s+.*)?$')
MM_DD_YY = re.compile(r'^(\d{1,2})/(\d{1,2})/(\d{2})$')


def safe_int(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


COMMA_NUMBER = re.compile(r'^-?£?\$?€?\s*[\d,]+\.?\d*$')


def fix_date_string(val):
    """Convert DD/MM/YYYY, DD/MM/YYYY HH:MM, or MM/DD/YY to ISO format."""
    if not isinstance(val, str):
        return val
    m = DD_MM_YYYY.match(val.strip())
    if m:
        d, mo, y, t = m.groups()
        base = f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
        return f"{base} {t}" if t else base
    m2 = MM_DD_YY.match(val.strip())
    if m2:
        mo, d, y = m2.groups()
        full_y = f"20{y}" if int(y) < 50 else f"19{y}"
        return f"{full_y}-{mo.zfill(2)}-{d.zfill(2)}"
    return val


def fix_comma_number(val):
    """Strip commas, currency symbols, and K/M suffixes from number-like strings."""
    if not isinstance(val, str):
        return val
    s = val.strip().replace('£', '').replace('$', '').replace('€', '').replace(',', '').replace(' ', '')
    if s.upper().endswith('K'):
        try:
            return str(float(s[:-1]) * 1000)
        except ValueError:
            return val
    if s.upper().endswith('M'):
        try:
            return str(float(s[:-1]) * 1000000)
        except ValueError:
            return val
    if ',' in val and COMMA_NUMBER.match(val.strip()):
        return s
    return val


def clean_row(row):
    cleaned = {}
    for key, val in row.items():
        if val is None:
            cleaned[key] = None
        elif isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            cleaned[key] = None
        elif key in SCHEMA_INT_COLS:
            cleaned[key] = safe_int(val)
        elif isinstance(val, (np.integer,)):
            cleaned[key] = int(val)
        elif isinstance(val, (np.floating,)):
            cleaned[key] = float(val)
        elif isinstance(val, np.bool_):
            cleaned[key] = bool(val)
        elif isinstance(val, str):
            cleaned[key] = fix_comma_number(fix_date_string(val))
        else:
            cleaned[key] = val
    return cleaned


BATCH_SIZE = 200
total = len(df)
batches = math.ceil(total / BATCH_SIZE)
inserted = 0

print(f"Inserting {total} rows in {batches} batches of {BATCH_SIZE}...\n")

for i in range(batches):
    start = i * BATCH_SIZE
    end = min(start + BATCH_SIZE, total)
    batch = df.iloc[start:end]
    rows = [clean_row(row) for _, row in batch.iterrows()]

    resp = requests.post(API_URL, headers=HEADERS, data=json.dumps(rows))

    if resp.status_code == 201:
        inserted += len(rows)
        print(f"  Batch {i+1}/{batches}: inserted rows {start+1}-{end} ({inserted}/{total})")
    else:
        error_msg = resp.text[:300]
        print(f"  Batch {i+1}/{batches}: ERROR {resp.status_code} - {error_msg}")
        print("    Trying row-by-row...")
        for j, row in enumerate(rows):
            r = requests.post(API_URL, headers=HEADERS, data=json.dumps(row))
            if r.status_code == 201:
                inserted += 1
            else:
                print(f"    Row {start+j+1} FAILED: {r.text[:150]}")
        print(f"    Row-by-row done. Total inserted so far: {inserted}")

    time.sleep(0.05)

print(f"\nDone! Inserted: {inserted}/{total}")
