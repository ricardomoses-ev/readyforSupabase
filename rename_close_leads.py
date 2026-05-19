import pandas as pd
import re
import os

input_path = r"c:\Users\Ricardo Moses\My Downloads\FB(X) leads 2026-04-14 11-00.csv"
output_path = os.path.join("output", "close_leads_supabase_ready.csv")

df = pd.read_csv(input_path, low_memory=False)

print(f"Original columns: {len(df.columns)}")
print(f"Rows: {len(df)}")

# --- Direct mappings to leads schema ---
schema_map = {
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


def to_snake_case(name):
    name = name.strip()
    name = re.sub(r'^custom\.', '', name)
    name = name.replace('.id', '_id').replace('.name', '_name')
    name = name.replace('/', '_or_').replace('&', '_and_')
    name = name.replace('%', '_pct').replace('?', '').replace('>', '_gt_')
    name = re.sub(r'[()]+', '', name)
    name = re.sub(r'[^a-zA-Z0-9]+', '_', name)
    name = re.sub(r'([a-z])([A-Z])', r'\1_\2', name)
    name = name.lower()
    name = re.sub(r'_+', '_', name)
    name = name.strip('_')
    return name


rename_map = {}
for col in df.columns:
    if col in schema_map:
        rename_map[col] = schema_map[col]
    else:
        rename_map[col] = to_snake_case(col)

# Check for duplicates in the new names
seen = {}
final_map = {}
for old, new in rename_map.items():
    if new in seen:
        suffix = 2
        while f"{new}_{suffix}" in seen:
            suffix += 1
        new = f"{new}_{suffix}"
    seen[new] = old
    final_map[old] = new

df = df.rename(columns=final_map)

# Clean numeric columns that map to schema numeric types
def clean_numeric(val):
    if pd.isna(val):
        return val
    s = str(val).strip()
    s = s.replace('£', '').replace('$', '').replace('€', '').replace(',', '').replace(' ', '')
    if s.lower().endswith('k'):
        try:
            return float(s[:-1]) * 1000
        except ValueError:
            return None
    if s.lower().endswith('m'):
        try:
            return float(s[:-1]) * 1000000
        except ValueError:
            return None
    if '-' in s and not s.startswith('-'):
        parts = s.split('-')
        try:
            return (float(parts[0]) + float(parts[1])) / 2
        except (ValueError, IndexError):
            return None
    try:
        return float(s)
    except ValueError:
        return None

numeric_cols = ['loan_amount', 'net_assets', 'turnover', 'profitability']
for col in numeric_cols:
    if col in df.columns:
        before_bad = df[col].dropna().shape[0] - pd.to_numeric(df[col], errors='coerce').dropna().shape[0]
        df[col] = df[col].apply(clean_numeric)
        after_bad = df[col].dropna().shape[0] - pd.to_numeric(df[col], errors='coerce').dropna().shape[0]
        print(f"Cleaned {col}: {before_bad} bad values -> {after_bad} bad values")

# Fix integer columns — convert floats like 5.0 to 5
int_cols = ['years_of_trading']
for col in int_cols:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')
        mask = df[col].notna()
        df.loc[mask, col] = df.loc[mask, col].astype(int)
        print(f"Fixed {col}: converted to integer ({mask.sum()} values)")

# Drop unnamed columns
drop_cols = [c for c in df.columns if c.startswith("unnamed")]
if drop_cols:
    df = df.drop(columns=drop_cols)

df.to_csv(output_path, index=False)

print(f"\nOutput columns: {len(df.columns)}")
print(f"Dropped: {drop_cols}")

schema_cols = set(schema_map.values())
print("\n--- MAPPED TO SCHEMA ---")
for old, new in sorted(final_map.items(), key=lambda x: x[1]):
    if new in schema_cols:
        print(f"  {old:50s} -> {new}")

print("\n--- STANDARDIZED (not in schema) ---")
for old, new in sorted(final_map.items(), key=lambda x: x[1]):
    if new not in schema_cols and not new.startswith("unnamed"):
        print(f"  {old:50s} -> {new}")

print(f"\nSaved to: {output_path}")
