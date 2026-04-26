import urllib.request
import json
import time
import os
from datetime import datetime, timezone
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

AREAS = {
    "Dubai Marina":             50,
    "Dubai Hills Estate":       105,
    "Dubai Sports City":        55,
    "Al Barari":                12,
    "Zabeel":                   100,
    "Downtown Dubai":           6,
    "Business Bay":             119,
    "Jumeirah Village Circle":  82,
    "Jumeirah Lake Towers":     77,
    "DIFC":                     28,
    "Bur Dubai":                35,
    "Palm Jumeirah":            38,
    "Arabian Ranches":          4,
    "Mirdif":                   63,
    "Al Barsha":                13,
    "Dubai Silicon Oasis":      54,
    "Jumeirah Beach Residence": 46,
    "Motor City":               65,
    "Dubai Creek Harbour":      84,
    "Town Square":              131,
}

HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": "propertyfinder-uae-data.p.rapidapi.com",
}

BASE_URL = "https://propertyfinder-uae-data.p.rapidapi.com"


def get_today_listing_type():
    day = datetime.now(timezone.utc).day
    return "sale" if day % 2 == 0 else "rental"


def fetch_listings(area_id, listing_type):
    endpoint = "search-buy" if listing_type == "sale" else "search-rent"
    url = f"{BASE_URL}/{endpoint}?location_id={area_id}&page=1"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("success"):
            print(f"    API error")
            return []
        return data.get("data", [])
    except Exception as e:
        print(f"    Fetch error: {e}")
        return []


def safe_float(val):
    try:
        f = float(str(val).replace(",", "").strip())
        return f if 0 < f < 100_000_000 else None
    except Exception:
        return None


def safe_int(val):
    try:
        if str(val).lower() in ["none", "", "studio"]:
            return 0 if str(val).lower() == "studio" else None
        return int(str(val).strip())
    except Exception:
        return None


def extract_listing(item, area_name, listing_type):
    external_id = str(item.get("property_id", "")).strip()
    if not external_id:
        return None

    price_obj = item.get("price", {})
    price_val = price_obj.get("value", 0) if isinstance(price_obj, dict) else price_obj
    price = safe_float(price_val)
    if not price:
        return None

    source_url = item.get("property_url", "")
    if not source_url:
        return None

    raw_images = item.get("images", []) or []
    all_images = [img for img in raw_images if isinstance(img, str)][:10]
    image_url = all_images[0] if all_images else None

    size_obj = item.get("size", {})
    sqft = safe_float(size_obj.get("value") if isinstance(size_obj, dict) else size_obj)

    beds_raw = item.get("bedrooms")
    beds = 0 if str(beds_raw).lower() == "studio" else safe_int(beds_raw)

    prop_type = item.get("property_type")
    if isinstance(prop_type, dict):
        prop_type = prop_type.get("name") or prop_type.get("slug")

    return {
        "external_id":       f"{external_id}_{listing_type}",
        "source":            "propertyfinder",
        "source_url":        source_url,
        "title":             str(item.get("title", "")).strip(),
        "area_name":         area_name,
        "price_aed":         price,
        "sqft":              sqft,
        "beds":              beds,
        "baths":             safe_int(item.get("bathrooms")),
        "property_type":     str(prop_type).strip() if prop_type else None,
        "furnished":         None,
        "completion_status": None,
        "image_url":         image_url,
        "images":            all_images,
        "listing_type":      listing_type,
        "is_active":         True,
        "last_scraped":      datetime.now(timezone.utc).isoformat(),
    }


def scrape_area(area_name, area_id, listing_type):
    print(f"  {area_name}...")
    items = fetch_listings(area_id, listing_type)
    if not items:
        print(f"    No results")
        return []
    listings = []
    for item in items:
        listing = extract_listing(item, area_name, listing_type)
        if listing:
            listings.append(listing)
    print(f"    {len(items)} raw | {len(listings)} kept")
    return listings


def deduplicate(listings):
    seen = {}
    for l in listings:
        seen[l["external_id"]] = l
    return list(seen.values())


def upsert_listings(listings):
    if not listings:
        return 0
    listings = deduplicate(listings)
    print(f"After dedup: {len(listings)} unique listings")
    total = 0
    for i in range(0, len(listings), 50):
        batch = listings[i:i+50]
        try:
            supabase.table("listings").upsert(
                batch, on_conflict="external_id,source"
            ).execute()
            total += len(batch)
        except Exception as e:
            print(f"  Upsert error: {e}")
    return total


def log_run(total, status):
    try:
        supabase.table("scraper_runs").insert({
            "status":         status,
            "listings_found": total,
            "finished_at":    datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"Log run failed (non-fatal): {e}")


def main():
    start = datetime.now(timezone.utc)
    listing_type = get_today_listing_type()
    print(f"Scrape started: {start.isoformat()}")
    print(f"Today: {listing_type.upper()} | Areas: {len(AREAS)}")
    all_listings = []
    try:
        for area_name, area_id in AREAS.items():
            listings = scrape_area(area_name, area_id, listing_type)
            all_listings.extend(listings)
            time.sleep(1)
        total = upsert_listings(all_listings)
        print(f"Done. {total} {listing_type} listings upserted.")
        log_run(total, "success")
    except Exception as e:
        print(f"Error: {e}")
        log_run(len(all_listings), "failed")
        raise


if __name__ == "__main__":
    main()
