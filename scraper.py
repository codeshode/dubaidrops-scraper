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
    "Dubai Marina":       50,
    "Dubai Hills Estate": 105,
    "Dubai Sports City":  55,
    "Al Barari":          12,
    "Zabeel":             100,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def fetch_page(area_id, page=1):
    url = (
        f"https://www.propertyfinder.ae/en/search?"
        f"c=2&fu=0&ob=mr&page={page}&l={area_id}&rp=y"
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

def extract_listing(prop):
    p = prop.get("property", prop)
    try:
        price = int(str(p.get("price", {}).get("value", "0")).replace(",", "").replace("AED", "").strip())
    except:
        price = 0

    if price == 0:
        return None

    images = p.get("images", [])
    image_url = images[0].get("medium") if images else None

    return {
        "external_id": str(p.get("id", "")),
        "title": p.get("title", ""),
        "price_aed": price,
        "size_sqft": p.get("size", {}).get("value"),
        "location": p.get("location", {}).get("path_name", ""),
        "listing_url": "https://www.propertyfinder.ae" + p.get("details_path", ""),
        "image_url": image_url,
        "source": "propertyfinder",
        "is_active": True,
    }

def scrape_area(area_name, area_id):
    print(f"Scraping {area_name}...")
    all_listings = []
    page = 1

    while True:
        props = fetch_page(area_id, page)
        if not props:
            break

        for prop in props:
            listing = extract_listing(prop)
            if listing:
                all_listings.append(listing)

        print(f"  Page {page}: {len(props)} listings")

        if len(props) < 25:
            break

        page += 1
        time.sleep(1)

    return all_listings

def upsert_listings(listings):
    if not listings:
        return 0

    batch_size = 50
    total = 0
    for i in range(0, len(listings), batch_size):
        batch = listings[i:i+batch_size]
        supabase.table("listings").upsert(
            batch,
            on_conflict="external_id"
        ).execute()
        total += len(batch)

    return total

def log_run(total_scraped, status):
    try:
        supabase.table("scraper_runs").insert({
            "listings_scraped": total_scraped,
            "status": status,
        }).execute()
    except Exception as e:
        print(f"Log run failed (non-fatal): {e}")

def main():
    start = datetime.now(timezone.utc)
    print(f"Scrape started at {start.isoformat()}")
    all_listings = []

    try:
        for area_name, area_id in AREAS.items():
            listings = scrape_area(area_name, area_id)
            all_listings.extend(listings)
            time.sleep(2)

        total = upsert_listings(all_listings)
        print(f"Upserted {total} listings")
        log_run(total, "success")

    except Exception as e:
        print(f"Error: {e}")
        log_run(len(all_listings), "error")
        raise

if __name__ == "__main__":
    main()
