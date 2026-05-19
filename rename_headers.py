import pandas as pd
import os

input_path = os.path.join("output", "dealsheet_cleaned.csv")
output_path = os.path.join("output", "dealsheet_supabase_ready.csv")

df = pd.read_csv(input_path)

rename_map = {
    "dealteam_portfolioidlookup": "dealteam_portfolio_id_lookup",
    "originatoridlookup": "originator_id_lookup",
}

df = df.rename(columns=rename_map)

drop_cols = [c for c in df.columns if c.startswith("unnamed") or c.startswith("Unnamed")]
if drop_cols:
    df = df.drop(columns=drop_cols)

df.to_csv(output_path, index=False)

print(f"Columns in output ({len(df.columns)}):")
for col in df.columns:
    print(f"  {col}")
print(f"\nDropped columns: {drop_cols}")
print(f"\nRows: {len(df)}")
print(f"Saved to: {output_path}")
