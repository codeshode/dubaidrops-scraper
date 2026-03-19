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
    "Dubai Marina":             50,
    "Dubai Hills Estate":       105,
    "Dubai Sports City":        55,
    "Al Barari":                12,
    "Zabeel":                   100,
    "Downtown Dubai":           6,
    "Business Bay":             18,
    "Jumeirah Village Circle":  82,
    "Jumeirah Lake Towers":     77,
    "DIFC":                     28,
    "Deira":                    25,
    "Bur Dubai":                20,
    "Palm Jumeirah":            38,
    "Arabian Ranches":          4,
    "Mirdif":                   63,
    "Al Barsha":                8,
    "Dubai Silicon Oasis":      54,
    "Jumeirah Beach Residence": 46,
    "Motor City":               65,
    "Dubai South":              116,
}

LISTING_TYPES = {
    "sale":   1,
    "rental": 2,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Keywords that identify the listing type from the URL path
SALE_URL_KEYWORDS   = ["/buy/", "/for-sale/", "-for-sale-"]
RENTAL_URL_KEYWORDS = ["/rent/", "/for-rent/", "-for-rent-"]

# Non-Dubai locations to filter out (PropertyFinder injects these into Dubai results)
# PropertyFinder uses both slash-delimited (/abu-dhabi/) and hyphen-delimited (-abu-dhabi-)
# patterns in URLs, so we check for hyphen versions which match both
EXCLUDED_LOCATIONS = [
    "-abu-dhabi-", "-sharjah-", "-ajman-", "-ras-al-khaimah-",
    "-fujairah-", "-umm-al-quwain-", "-al-ain-",
]


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
    except Exception:
        return []


def safe_int(val):
    try:
        return int(str(val).strip()) if val not in [None, "", "Studio"] else (0 if val == "Studio" else None)
    except Exception:
        return None


def safe_float(val):
    try:
        f = float(str(val).replace(",", "").strip())
        return f if f < 100_000_000 else None
    except Exception:
        return None


def safe_str(val):
    if val is None:
        return None
    if isinstance(val, dict):
        return val.get("slug") or val.get("name") or None
    return str(val).strip() or None


def url_matches_listing_type(details_path, listing_type):
    """
    Validate that a listing's URL path matches the expected listing type.
    PropertyFinder injects cross-type and cross-emirate listings into search
    results pages. This filter discards those injected listings before they
    reach the database.

    Returns True if the listing should be KEPT, False if it should be discarded.
    """
    if not details_path:
        # No URL at all — discard, we cannot verify the listing
        return False

    path = details_path.lower()

    # Discard non-Dubai emirate listings injected into Dubai results
    for excluded in EXCLUDED_LOCATIONS:
        if excluded in path:
            return False

    # Validate listing type matches URL
    if listing_type == "sale":
        # Must contain a sale keyword; must NOT contain a rental keyword
        has_sale_signal   = any(kw in path for kw in SALE_URL_KEYWORDS)
        has_rental_signal = any(kw in path for kw in RENTAL_URL_KEYWORDS)
        if has_rental_signal:
            return False   # Rental URL injected into sale results
        if not has_sale_signal:
            # URL doesn't look like either — allow it through (edge case for new URL patterns)
            pass

    elif listing_type == "rental":
        # Must contain a rental keyword; must NOT contain a sale keyword
        has_rental_signal = any(kw in path for kw in RENTAL_URL_KEYWORDS)
        has_sale_signal   = any(kw in path for kw in SALE_URL_KEYWORDS)
        if has_sale_signal:
            return False   # Sale URL injected into rental results
        if not has_rental_signal:
            pass  # Allow through

    return True


def extract_listing(prop, area_name, listing_type):
    p = prop.get("property", prop)

    external_id = str(p.get("id", "")).strip()
    if not external_id:
        return None

    price_raw = p.get("price", {}).get("value", "0")
    price = safe_float(str(price_raw).replace(",", "").replace("AED", "").strip())
    if not price or price <= 0:
        return None

    details_path = p.get("details_path", "")

    # ---------------------------------------------------------------
    # CORE FIX: validate URL matches expected listing type + location
    # ---------------------------------------------------------------
    if not url_matches_listing_type(details_path, listing_type):
        return None

    # Collect up to 10 images
    raw_images = p.get("images", [])
    all_images = []
    for img in raw_images[:10]:
        url_img = img.get("medium") or img.get("large") or img.get("small")
        if url_img:
            all_images.append(url_img)

    image_url = all_images[0] if all_images else None

    completion_raw = p.get("completion_status") or p.get("is_off_plan")
    if completion_raw is True:
        completion_status = "off-plan"
    elif completion_raw is False:
        completion_status = "ready"
    else:
        completion_status = None

    return {
        "external_id":       external_id + f"_{listing_type}",
        "source":            "propertyfinder",
        "source_url":        "https://www.propertyfinder.ae" + details_path,
        "title":             safe_str(p.get("title")) or "",
        "area_name":         area_name,
        "price_aed":         price,
        "sqft":              safe_float(p.get("size", {}).get("value")),
        "beds":              safe_int(p.get("bedrooms_value")),
        "baths":             safe_int(p.get("bathrooms_value")),
        "property_type":     safe_str(p.get("property_type")),
        "furnished":         safe_str(p.get("furnished")),
        "completion_status": completion_status,
        "image_url":         image_url,
        "images":            all_images,
        "listing_type":      listing_type,
        "is_active":         True,
        "last_scraped":      datetime.now(timezone.utc).isoformat(),
    }


def scrape_area(area_name, area_id, listing_type, listing_type_code):
    print(f"Scraping {area_name} ({listing_type})...")
    all_listings = []
    skipped = 0
    page = 1

    while True:
        props = fetch_page(area_id, listing_type_code, page)
        if not props:
            break

        page_kept = 0
        for prop in props:
            listing = extract_listing(prop, area_name, listing_type)
            if listing:
                all_listings.append(listing)
                page_kept += 1
            else:
                skipped += 1

        print(f"  Page {page}: {len(props)} raw | {page_kept} kept | {skipped} skipped total")

        if len(props) < 25:
            break

        page += 1
        time.sleep(1)

    return all_listings


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
        supabase.table("listings").upsert(
            batch,
            on_conflict="external_id,source"
        ).execute()
        total += len(batch)

    return total


def mark_inactive_listings(active_external_ids):
    """
    Mark listings as inactive if they were not seen in today's scrape.
    This handles listings that have been removed from PropertyFinder.
    """
    if not active_external_ids:
        return

    try:
        # Mark all listings as inactive first
        supabase.table("listings").update(
            {"is_active": False}
        ).not_.in_("external_id", active_external_ids).execute()
        print(f"Marked listings not in today's scrape as inactive")
    except Exception as e:
        print(f"Mark inactive failed (non-fatal): {e}")


def log_run(total, status, skipped=0):
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

        # Mark any listings not seen today as inactive
        active_ids = [l["external_id"] for l in all_listings]
        mark_inactive_listings(active_ids)

        log_run(total, "success")
        print(f"Scrape finished. Total: {total} listings upserted.")

    except Exception as e:
        print(f"Error: {e}")
        log_run(len(all_listings), "failed")
        raise


if __name__ == "__main__":
    main()
