import requests, os, json

url = os.environ.get("SUPABASE_URL", "")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not url or not key:
    print("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first")
    exit(1)

headers = {"apikey": key, "Authorization": f"Bearer {key}"}

r = requests.get(f"{url}/rest/v1/custom_activities?limit=2&select=*", headers=headers)
if r.status_code == 200:
    data = r.json()
    if data:
        print("Columns:", list(data[0].keys()))
        print(json.dumps(data[0], indent=2, default=str))
    else:
        print("Table is empty")
        rpc = requests.get(f"{url}/rest/v1/custom_activities?limit=0", headers={**headers, "Prefer": "count=exact"})
        print(f"Count header: {rpc.headers.get('content-range')}")
else:
    print(f"Error {r.status_code}: {r.text[:500]}")
