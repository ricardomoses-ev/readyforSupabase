import requests
import os
import json

CLOSE_API_KEY = os.environ.get("CLOSE_API_KEY", "")

if not CLOSE_API_KEY:
    print("ERROR: Set CLOSE_API_KEY environment variable.")
    print('  $env:CLOSE_API_KEY = "your-close-api-key"')
    exit(1)

print("Fetching custom activity types from Close CRM...")

r = requests.get(
    "https://api.close.com/api/v1/custom_activity",
    auth=(CLOSE_API_KEY, ""),
)

if r.status_code != 200:
    print(f"Error {r.status_code}: {r.text[:300]}")
    exit(1)

data = r.json().get("data", [])
print(f"Found {len(data)} custom activity types:\n")

type_map = {}
for item in data:
    type_id = item.get("id", "")
    type_name = item.get("name", "unknown")
    type_map[type_id] = type_name
    print(f"  {type_id} -> {type_name}")

output_path = os.path.join("output", "activity_type_names.json")
with open(output_path, "w") as f:
    json.dump(type_map, f, indent=2)

print(f"\nSaved mapping to {output_path}")
