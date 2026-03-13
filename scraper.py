import urllib.request
import json
import time
import os
from datetime import datetime, timezone
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

AREAS = {
    "Dubai Marina":           50,
    "Dubai Hills Estate":     105,
    "Dubai Sports City":      55,
    "Al Barari":              12,
    "Zabeel":                 100,
    "Downtown Dubai":         6,
    "Business Bay":           18,
    "Jumeirah Village Circle": 82,
    "Jumeirah Lake Towers":   77,
    "DIFC":                   28,
    "Deira":                  25,
    "Bur Dubai":              20,
    "Palm Jumeirah":          38,
    "Arabian Ranches":        4,
    "Mirdif":                 63,
    "Al Barsha":              8,
    "Dubai Silicon Oasis":    54,
    "Jumeirah Beach Residence": 46,
    "Motor City":             65,
    "Dubai South":            116,
}

LISTING_TYPES = {
    "sale":   2,
    "rental": 1,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def fetch_page(area_id, listing_type_code, page=1):
    url = (
        f"https://www.propertyfinder.ae/en/search?"
        f"c={listing_type_code}&fu=0&ob=mr&page={page}&l={area_id}&rp=y"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8")

    marker = '"searchResult":'
    start = html.find(marker)
    if start == -1:
        return []

    props_marker = '"properties":'
    props_start = html.find(props_marker, start)
    if props_start == -1:
        return []

    bracket_start = html.find("[", props_start)
    depth = 0
    i = bracket_start
    while i < len(html):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1

    try:
        return json.loads(html[bracket_start:i+1])
    except:
        return []

def extract_listing(prop, area_name, listing_type):
    p = prop.get("property", prop)

    try:
        price = int(str(p.get("price", {}).get("value", "0")).replace(",", "").replace("AED", "").strip())
    except:
        price = 0

    if price == 0:
        return None

    external_id = str(p.get("id", ""))
    if not external_id:
        return None

    images = p.get("images", [])
    image_url = images[0].get("medium") if images else None

    size_val = p.get("size", {}).get("value")
    try:
        sqft = float(size_val) if size_val else None
    except:
        sqft = None

    try:
        beds = int(p.get("bedrooms_value")) if p.get("bedrooms_value") not in [None, "", "Studio"] else (0 if p.get("bedrooms_value") == "Studio" else None)
    except:
        beds = None

    try:
        baths = int(p.get("bathrooms_value")) if p.get("bathrooms_value") not in [None, ""] else None
    except:
        baths = None

    prop_type_raw = p.get("property_type", {})
    if isinstance(prop_type_raw, dict):
        property_type = prop_type_raw.get("slug") or prop_type_raw.get("name")
    else:
        property_type = str(prop_type_raw) if prop_type_raw else None

    furnished_raw = p.get("furnished")
    if isinstance(furnished_raw, dict):
        furnished = furnished_raw.get("slug") or furnished_raw.get("name")
    else:
        furnished = str(furnished_raw) if furnished_raw else None

    completion_raw = p.get("completion_status") or p.get("is_off_plan")
    if completion_raw is True:
        completion_status = "off-plan"
    elif completion_raw is False:
        completion_status = "ready"
    else:
        completion_status = None

    details_path = p.get("details_path", "")

    return {
        "external_id":        external_id + f"_{listing_type}",
        "source":             "propertyfinder",
        "source_url":         "https://www.propertyfinder.ae" + details_path,
        "title":              p.get("title", ""),
        "area_name":          area_name,
        "price_aed":          price,
        "sqft":               sqft,
        "beds":               beds,
        "baths":              baths,
        "property_type":      property_type,
        "furnished":          furnished,
        "completion_status":  completion_status,
        "image_url":          image_url,
        "listing_type":       listing_type,
        "is_active":          True,
        "last_scraped":       datetime.now(timezone.utc).isoformat(),
    }

def scrape_area(area_name, area_id, listing_type, listing_type_code):
    print(f"Scraping {area_name} ({listing_type})...")
    all_listings = []
    page = 1

    while True:
        props = fetch_page(area_id, listing_type_code, page)
        if not props:
            break

        for prop in props:
            listing = extract_listing(prop, area_name, listing_type)
            if listing:
                all_listings.append(listing)

        print(f"  Page {page}: {len(props)} listings")

        if len(props) < 25:
            break

        page += 1
        time.sleep(1)

    return all_listings

def deduplicate(listings):
    seen = {}
    for listing in listings:
        key = listing["external_id"]
        seen[key] = listing
    return list(seen.values())

def upsert_listings(listings):
    if not listings:
        return 0

    listings = deduplicate(listings)
    print(f"After dedup: {len(listings)} unique listings")

    batch_size = 50
    total = 0
    for i in range(0, len(listings), batch_size):
        batch = listings[i:i+batch_size]
        supabase.table("listings").upsert(
            batch,
            on_conflict="external_id,source"
        ).execute()
        total += len(batch)

    return total

def log_run(total, status):
    try:
        supabase.table("scraper_runs").insert({
            "status": status,
            "listings_found": total,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"Log run failed (non-fatal): {e}")

def main():
    start = datetime.now(timezone.utc)
    print(f"Scrape started at {start.isoformat()}")
    all_listings = []

    try:
        for listing_type, listing_type_code in LISTING_TYPES.items():
            for area_name, area_id in AREAS.items():
                listings = scrape_area(area_name, area_id, listing_type, listing_type_code)
                all_listings.extend(listings)
                time.sleep(2)

        total = upsert_listings(all_listings)
        print(f"Upserted {total} listings")
        log_run(total, "success")

    except Exception as e:
        print(f"Error: {e}")
        log_run(len(all_listings), "failed")
        raise

if __name__ == "__main__":
    main()
