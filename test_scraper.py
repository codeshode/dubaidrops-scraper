"""
TEST SCRAPER — DubaiDrops
Scrapes 3 areas (sale + rental) with detailed logging.
Set DRY_RUN = True to test without writing to Supabase.
Set DRY_RUN = False to write to Supabase and verify upserts work.

Run locally:
  SUPABASE_URL=xxx SUPABASE_SERVICE_KEY=xxx python test_scraper.py
"""

import urllib.request
import json
import time
import os
from datetime import datetime, timezone

DRY_RUN = True  # Set False to actually write to Supabase

# Only 3 areas for testing — covers high-volume, mid-volume, low-volume
TEST_AREAS = {
    "Dubai Marina":            50,   # High volume
    "Jumeirah Village Circle": 82,   # High volume
    "Motor City":              65,   # Low volume
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

EXCLUDED_LOCATION_SLUGS = [
    "-abu-dhabi-", "-sharjah-", "-ajman-",
    "-ras-al-khaimah-", "-fujairah-", "-umm-al-quwain-", "-al-ain-",
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def fetch_page(area_id, listing_type_code, page=1):
    url = (
        f"https://www.propertyfinder.ae/en/search?"
        f"c={listing_type_code}&fu=0&ob=mr&page={page}&l={area_id}&rp=y"
    )
    log(f"  Fetching: {url}")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8")
    except Exception as e:
        log(f"  FETCH ERROR: {e}")
        return [], None

    # Check for searchResult marker
    marker = '"searchResult":'
    start = html.find(marker)
    if start == -1:
        log(f"  WARNING: 'searchResult' not found in HTML. Page may be blocked or structure changed.")
        log(f"  HTML snippet: {html[500:800]}")
        return [], None

    # Find properties array
    props_marker = '"properties":'
    props_start = html.find(props_marker, start)
    if props_start == -1:
        log(f"  WARNING: 'properties' key not found in searchResult")
        return [], None

    bracket_start = html.find("[", props_start)
    if bracket_start == -1:
        log(f"  WARNING: No array after 'properties'")
        return [], None

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
        props = json.loads(html[bracket_start:i + 1])
        log(f"  Parsed {len(props)} properties from JSON")
        return props, html
    except Exception as e:
        log(f"  JSON PARSE ERROR: {e}")
        log(f"  Raw snippet: {html[bracket_start:bracket_start+200]}")
        return [], None


def safe_int(val):
    try:
        if val in [None, "", "Studio"]:
            return 0 if val == "Studio" else None
        return int(str(val).strip())
    except Exception:
        return None


def safe_float(val):
    try:
        f = float(str(val).replace(",", "").strip())
        return f if 0 < f < 100_000_000 else None
    except Exception:
        return None


def safe_str(val):
    if val is None:
        return None
    if isinstance(val, dict):
        return val.get("slug") or val.get("name") or None
    return str(val).strip() or None


def is_valid_listing(details_path, listing_type):
    if not details_path:
        return False, "empty details_path"

    path = details_path.lower()

    for slug in EXCLUDED_LOCATION_SLUGS:
        if slug in path:
            return False, f"non-Dubai location ({slug} in path)"

    if listing_type == "sale" and ("/rent/" in path or "-for-rent-" in path):
        return False, "rental URL in sale results"

    if listing_type == "rental" and ("/buy/" in path or "-for-sale-" in path):
        return False, "sale URL in rental results"

    return True, "ok"


def extract_listing(prop, area_name, listing_type):
    p = prop.get("property", prop)

    external_id = str(p.get("id", "")).strip()
    if not external_id:
        return None, "no external_id"

    price_raw = p.get("price", {})
    if isinstance(price_raw, dict):
        price_val = price_raw.get("value", "0")
    else:
        price_val = str(price_raw)
    price = safe_float(str(price_val).replace(",", "").replace("AED", "").strip())
    if not price:
        return None, f"invalid price ({price_val})"

    details_path = p.get("details_path", "") or ""
    valid, reason = is_valid_listing(details_path, listing_type)
    if not valid:
        return None, reason

    raw_images = p.get("images", []) or []
    all_images = []
    for img in raw_images[:10]:
        if not isinstance(img, dict):
            continue
        url_img = img.get("medium") or img.get("large") or img.get("small")
        if url_img:
            all_images.append(url_img)

    return {
        "external_id":   f"{external_id}_{listing_type}",
        "source":        "propertyfinder",
        "source_url":    "https://www.propertyfinder.ae" + details_path,
        "title":         safe_str(p.get("title")) or "",
        "area_name":     area_name,
        "price_aed":     price,
        "listing_type":  listing_type,
        "is_active":     True,
        "last_scraped":  datetime.now(timezone.utc).isoformat(),
    }, "ok"


def scrape_area(area_name, area_id, listing_type, listing_type_code):
    log(f"\n{'='*60}")
    log(f"SCRAPING: {area_name} | {listing_type} | area_id={area_id} | c={listing_type_code}")
    log(f"{'='*60}")

    all_listings = []
    skip_reasons = {}
    page = 1

    while True:
        props, raw_html = fetch_page(area_id, listing_type_code, page)
        if not props:
            log(f"  Page {page}: empty, stopping")
            break

        kept = 0
        for prop in props:
            listing, reason = extract_listing(prop, area_name, listing_type)
            if listing:
                all_listings.append(listing)
                kept += 1
            else:
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

        log(f"  Page {page}: {len(props)} raw | {kept} kept | skips: {skip_reasons}")

        # Show sample of kept listings
        if page == 1 and all_listings:
            log(f"  Sample listing: {all_listings[0]['external_id']} | {all_listings[0]['source_url'][:80]}")

        if len(props) < 25:
            log(f"  Page {page}: less than 25 results, last page")
            break

        page += 1
        time.sleep(1)

    log(f"  AREA TOTAL: {len(all_listings)} listings kept")
    return all_listings


def main():
    log("=" * 60)
    log(f"TEST SCRAPER STARTED — DRY_RUN={DRY_RUN}")
    log(f"Areas: {list(TEST_AREAS.keys())}")
    log(f"Types: {list(LISTING_TYPES.keys())}")
    log("=" * 60)

    all_listings = []

    for listing_type, listing_type_code in LISTING_TYPES.items():
        log(f"\n{'#'*60}")
        log(f"LISTING TYPE: {listing_type.upper()} (c={listing_type_code})")
        log(f"{'#'*60}")
        for area_name, area_id in TEST_AREAS.items():
            listings = scrape_area(area_name, area_id, listing_type, listing_type_code)
            all_listings.extend(listings)
            time.sleep(2)

    # Summary
    log(f"\n{'='*60}")
    log(f"SCRAPE COMPLETE")
    log(f"Total listings collected: {len(all_listings)}")

    # Breakdown by type and area
    from collections import Counter
    by_type = Counter(l["listing_type"] for l in all_listings)
    by_area = Counter(l["area_name"] for l in all_listings)
    log(f"By type: {dict(by_type)}")
    log(f"By area: {dict(by_area)}")

    # Validate no bad URLs slipped through
    bad = [l for l in all_listings if
           (l["listing_type"] == "sale" and "/rent/" in l["source_url"]) or
           (l["listing_type"] == "rental" and "/buy/" in l["source_url"])]
    if bad:
        log(f"WARNING: {len(bad)} mismatched URL listings slipped through validation!")
        for b in bad[:3]:
            log(f"  {b['external_id']} | {b['source_url']}")
    else:
        log(f"URL VALIDATION: All listings have correct URLs")

    abu_dhabi = [l for l in all_listings if "-abu-dhabi-" in l["source_url"]]
    if abu_dhabi:
        log(f"WARNING: {len(abu_dhabi)} Abu Dhabi listings slipped through!")
    else:
        log(f"LOCATION VALIDATION: No non-Dubai listings found")

    if DRY_RUN:
        log(f"\nDRY RUN — no database writes. Set DRY_RUN=False to write to Supabase.")
        log(f"Sample of first 5 listings:")
        for l in all_listings[:5]:
            log(f"  {l['external_id']} | {l['area_name']} | {l['listing_type']} | AED {l['price_aed']:,.0f}")
    else:
        log(f"\nWriting to Supabase...")
        try:
            from supabase import create_client
            supabase = create_client(
                os.environ["SUPABASE_URL"],
                os.environ["SUPABASE_SERVICE_KEY"]
            )
            # Deduplicate
            seen = {}
            for l in all_listings:
                seen[l["external_id"]] = l
            unique = list(seen.values())
            log(f"After dedup: {len(unique)} unique listings")

            # Upsert in batches
            batch_size = 50
            total = 0
            for i in range(0, len(unique), batch_size):
                batch = unique[i:i + batch_size]
                supabase.table("listings").upsert(
                    batch, on_conflict="external_id,source"
                ).execute()
                total += len(batch)
            log(f"Upserted {total} listings successfully")

            # Verify
            result = supabase.table("listings").select("count", count="exact").execute()
            log(f"Total listings now in DB: {result.count}")

        except Exception as e:
            log(f"SUPABASE ERROR: {e}")
            raise

    log(f"\nTEST COMPLETE")


if __name__ == "__main__":
    main()
