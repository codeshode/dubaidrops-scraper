import httpx
import json
import time
import os
from datetime import datetime, timezone, timedelta
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

DLD_URL = "https://gateway.dubailand.gov.ae/open-data/transactions"

# Extracted from window.apiConfig on dubailand.gov.ae's open-data page.
# This is the public consumer-id baked into the website itself, not a secret.
DLD_CONSUMER_ID = "gkb3WvEG0rY9eilwXC0P2pTz8UzvLj9F"

HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json, */*",
    "consumer-id": DLD_CONSUMER_ID,
    "Origin": "https://dubailand.gov.ae",
    "Referer": "https://dubailand.gov.ae/en/open-data/real-estate-data/",
}

# DLD-side area IDs differ from PropertyFinder's. Map them here.
AREA_MAP = [
    {"area_name": "Dubai Marina",             "dld_area_id": "C-44"},
    {"area_name": "Dubai Hills Estate",       "dld_area_id": "C-35"},
    {"area_name": "Dubai Sports City",        "dld_area_id": "C-51"},
    {"area_name": "Al Barari",                "dld_area_id": "C-3"},
    {"area_name": "Jumeirah Village Circle",  "dld_area_id": "C-82"},
    {"area_name": "Palm Jumeirah",            "dld_area_id": "C-111"},
    {"area_name": "Jumeirah Beach Residence", "dld_area_id": "C-74"},
    {"area_name": "Downtown Dubai",           "dld_area_id": "C-27"},
    {"area_name": "Business Bay",             "dld_area_id": "C-9"},
    {"area_name": "Jumeirah Lake Towers",     "dld_area_id": "C-76"},
    {"area_name": "DAMAC Hills",              "dld_area_id": "C-26"},
]


def fetch_dld_transactions(area_id_str, from_date, to_date, take=500):
    payload = {
        "P_FROM_DATE": from_date,
        "P_TO_DATE": to_date,
        "P_GROUP_ID": "",
        "P_IS_OFFPLAN": "",
        "P_IS_FREE_HOLD": "",
        "P_AREA_ID": area_id_str,
        "P_USAGE_ID": "1",
        "P_PROP_TYPE_ID": "",
        "P_TAKE": str(take),
        "P_SKIP": "0",
        "P_SORT": "INSTANCE_DATE_DESC",
    }

    try:
        resp = httpx.post(DLD_URL, headers=HEADERS, json=payload, timeout=20)
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code}: {resp.text[:300]}")
            return []
        data = resp.json()
        rc = data.get("responseCode")
        if rc != 200:
            print(f"  responseCode={rc} errors={data.get('validationErrorsList')}")
            return []

        response = data.get("response") or {}
        result = response.get("result") or []
        return result

    except Exception as e:
        print(f"  DLD fetch error: {type(e).__name__}: {e}")
        return []


def calculate_median(values):
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    return sorted_vals[mid]


def process_area(area_name, dld_area_id, from_date, to_date):
    print(f"  Fetching transactions for {area_name} ({dld_area_id})...")
    transactions = fetch_dld_transactions(dld_area_id, from_date, to_date)

    if not transactions:
        print(f"  No transactions returned")
        return None

    print(f"  Got {len(transactions)} transactions")

    price_per_sqft_values = []
    price_per_sqm_values = []

    for t in transactions:
        try:
            trans_value = float(t.get("TRANS_VALUE") or 0)
            actual_area = float(t.get("ACTUAL_AREA") or 0)
            if trans_value > 0 and actual_area > 0:
                price_per_sqm = trans_value / actual_area
                price_per_sqft = price_per_sqm / 10.764
                if 100 < price_per_sqft < 20000:
                    price_per_sqft_values.append(price_per_sqft)
                    price_per_sqm_values.append(price_per_sqm)
        except Exception:
            continue

    if not price_per_sqft_values:
        print(f"  No valid sqft data")
        return None

    median_sqft = round(calculate_median(price_per_sqft_values), 2)
    median_sqm = round(calculate_median(price_per_sqm_values), 2)
    avg_sqm = round(sum(price_per_sqm_values) / len(price_per_sqm_values), 2)
    total_value = sum(float(t.get("TRANS_VALUE") or 0) for t in transactions)

    print(f"  Median: AED {median_sqft}/sqft from {len(price_per_sqft_values)} valid txns")

    return {
        "median_sqft": median_sqft,
        "median_sqm": median_sqm,
        "avg_sqm": avg_sqm,
        "total_value": total_value,
        "transaction_count": len(transactions),
    }


def get_area_id_from_db(area_name):
    try:
        result = supabase.table("areas").select("id").eq("name", area_name).execute()
        if result.data:
            return result.data[0]["id"]
    except Exception as e:
        print(f"  DB lookup error: {e}")
    return None


def upsert_dld_benchmark(area_id, stats, today_date):
    try:
        supabase.table("dld_benchmarks").upsert({
            "area_id": area_id,
            "property_type": "Residential",
            "beds": "All",
            "transaction_date": today_date,
            "transaction_count": stats["transaction_count"],
            "median_price_sqm": stats["median_sqm"],
            "avg_price_sqm": stats["avg_sqm"],
            "total_value_aed": stats["total_value"],
        }, on_conflict="area_id,property_type,beds,transaction_date").execute()
        print(f"  Benchmark saved")
    except Exception as e:
        print(f"  Benchmark upsert failed: {e}")


def update_listings_median(area_name, median_sqft):
    try:
        result = supabase.table("listings").update({
            "dld_median_sqft": median_sqft
        }).eq("area_name", area_name).eq("is_active", True).execute()
        count = len(result.data) if result.data else 0
        print(f"  Updated {count} listings with AED {median_sqft}/sqft")
    except Exception as e:
        print(f"  Listings update failed: {e}")


def main():
    start = datetime.now(timezone.utc)
    print(f"DLD enrichment started at {start.isoformat()}")

    today = datetime.now()
    # DLD wants MM/DD/YYYY (US format), confirmed by inspecting their public JS.
    to_date = today.strftime("%m/%d/%Y")
    from_date = (today - timedelta(days=90)).strftime("%m/%d/%Y")
    today_date = today.strftime("%Y-%m-%d")

    print(f"Date range: {from_date} to {to_date}\n")

    success_count = 0
    fail_count = 0

    for area in AREA_MAP:
        area_name = area["area_name"]
        dld_area_id = area["dld_area_id"]

        print(f"\nProcessing {area_name}...")

        area_id = get_area_id_from_db(area_name)
        print(f"  DB area_id: {area_id}")

        if not area_id:
            print(f"  SKIP: area not found in DB")
            fail_count += 1
            continue

        stats = process_area(area_name, dld_area_id, from_date, to_date)

        if stats:
            upsert_dld_benchmark(area_id, stats, today_date)
            update_listings_median(area_name, stats["median_sqft"])
            success_count += 1
        else:
            fail_count += 1

        time.sleep(1)

    elapsed = (datetime.now(timezone.utc) - start).seconds
    print(f"\nComplete: {success_count} areas updated, {fail_count} failed in {elapsed}s")


if __name__ == "__main__":
    main()
