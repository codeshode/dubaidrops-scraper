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
    """
    Alternate sale/rental each day to stay within 700 free calls/month.
    Even day = sale, Odd day = rental.
    20 areas x 1 page = 20 calls/day = 600 calls/month.
    """
    day = datetime.now(timezone.utc).day
    return "sale" if day % 2 == 0 else "rental"


def fetch_page(area_id, listing_type, page=1):
    endpoint = "search-buy" if listing_type == "sale" else "search-rent"
    url = f"{BASE_URL}/{endpoint}?location_id={area_id}&page={page}"

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("success"):
            print(f"    API returned success=false")
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
        if val in [None, "", "Studio"]:
            return 0 if val == "Studio" else None
        return int(str(val).strip())
    except Exception:
        return None


def safe_str(val):
    if val is None:
        return None
    if isinstance(val, dict):
        return val.get("name") or val.get("slug") or None
    return str(val).strip() or None


def extract_listing(item, area_name, listing_type):
    external_id = str(item.get("property_id", "")).strip()
    if not external_id:
        return None

    price_obj = item.get("price", {})
    price_val = price_obj.get("value", 0) if isinstance(price_obj, dict) else price_obj
    price = safe_float(price_val)
    if not price:
        return None

    slug = item.get("slug") or item.get("details_path") or ""
    if slug and not slug.startswith("http"):
        source_url = "https://www.propertyfinder.ae" + slug
    elif slug:
        source_url = slug
    else:
        source_url = f"https://www.propertyfinder.ae/en/plp/{'buy' if listing_type == 'sale' else 'rent'}/{external_id}.html"

    raw_images = item.get("images", []) or []
    all_images = []
    for img in raw_images[:10]:
        if isinstance(img, dict):
            url_img = img.get("medium") or img.get("large") or img.get("small") or img.get("url")
        elif isinstance(img, str):
            url_img = img
        else:
            url_img = None
        if url_img:
            all_images.append(url_img)
    image_url = all_images[0] if all_images else None

    completion_raw = item.get("completion_status") or item.get("is_off_plan")
    if completion_raw is True or completion_raw == "off_plan":
        completion_status = "off-plan"
    elif completion_raw is False or completion_raw == "ready":
        completion_status = "ready"
    else:
        completion_status = None

    prop_type = item.get("property_type")
    if isinstance(prop_type, dict):
        prop_type = prop_type.get("name") or prop_type.get("slug")

    return {
        "external_id":       f"{external_id}_{listing_type}",
        "source":            "propertyfinder",
        "source_url":        source_url,
        "title":             safe_str(item.get("title")) or safe_str(item.get("name")) or "",
        "area_name":         area_name,
        "price_aed":         price,
        "sqft":              safe_float(item.get("size") or item.get("area")),
        "beds":              safe_int(item.get("bedrooms") or item.get("bedrooms_value")),
        "baths":             safe_int(item.get("bathrooms") or item.get("bathrooms_value")),
        "property_type":     safe_str(prop_type),
        "furnished":         safe_str(item.get("furnished")),
        "completion_status": completion_status,
        "image_url":         image_url,
        "images":            all_images,
        "listing_type":      listing_type,
        "is_active":         True,
        "last_scraped":      datetime.now(timezone.utc).isoformat(),
    }


def scrape_area(area_name, area_id, listing_type):
    print(f"  {area_name} ({listing_type})...")
    props = fetch_page(area_id, listing_type, 1)
    if not props:
        print(f"    No results")
        return []

    listings = []
    for item in props:
        listing = extract_listing(item, area_name, listing_type)
        if listing:
            listings.append(listing)

    print(f"    {len(props)} raw | {len(listings)} kept")
    return listings


def deduplicate(listings):
    seen = {}
    for listing in listings:
        seen[listing["external_id"]] = listing
    return list(seen.values())


def upsert_listings(listings):
    if not listings:
        return 0
    listings = deduplicate(listings)
    print(f"After dedup: {len(listings)} unique listings")
    batch_size = 50
    total = 0
    for i in range(0, len(listings), batch_size):
        batch = listings[i:i + batch_size]
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
    print(f"Listing type today: {listing_type.upper()}")
    print(f"Areas: {len(AREAS)} | API calls: ~{len(AREAS)}")

    all_listings = []

    try:
        for area_name, area_id in AREAS.items():
            listings = scrape_area(area_name, area_id, listing_type)
            all_listings.extend(listings)
            time.sleep(1)

        total = upsert_listings(all_listings)
        print(f"\nDone. Upserted {total} {listing_type} listings.")
        log_run(total, "success")

    except Exception as e:
        print(f"Error: {e}")
        log_run(len(all_listings), "failed")
        raise


if __name__ == "__main__":
    main()
