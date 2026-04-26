import httpx
import os
from datetime import datetime, timezone
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "NOT_SET")

print(f"RAPIDAPI_KEY set: {RAPIDAPI_KEY != 'NOT_SET'}")
print(f"RAPIDAPI_KEY length: {len(RAPIDAPI_KEY)}")
print(f"RAPIDAPI_KEY first 8 chars: {RAPIDAPI_KEY[:8]}")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def test_api():
    url = "https://propertyfinder-uae-data.p.rapidapi.com/search-buy?location_id=50&page=1"
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": "propertyfinder-uae-data.p.rapidapi.com",
    }
    print(f"Calling: {url}")
    try:
        resp = httpx.get(url, headers=headers, timeout=20)
        print(f"HTTP Status: {resp.status_code}")
        print(f"Response size: {len(resp.content)} bytes")
        if resp.status_code != 200:
            print(f"Response body: {resp.text[:500]}")
            return
        data = resp.json()
        print(f"success: {data.get('success')}")
        print(f"listings count: {len(data.get('data', []))}")
        if data.get("data"):
            first = data["data"][0]
            print(f"first property_id: {first.get('property_id')}")
            print(f"first property_url: {first.get('property_url', 'MISSING')[:80]}")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")


def log_run(total, status):
    try:
        supabase.table("scraper_runs").insert({
            "status": status,
            "listings_found": total,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"Log run failed: {e}")


test_api()
log_run(0, "success")
print("Done")
