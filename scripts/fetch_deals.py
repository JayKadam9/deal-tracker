#!/usr/bin/env python3
"""Fetch Amazon.in deals that are 10%+ below BuyHatke's historical average.

Writes data/deals.json for the static site. Uses only the Python standard
library. If the fetch fails or returns too few qualifying items, exits
non-zero and leaves any existing deals.json untouched so the site can keep
serving the last good list.

Source: BuyHatke's public deals JSON (the same feed behind buyhatke.com/deals).
No login, cookies, or API keys are required.

  GET https://buyhatke.com/api/bbd-deals
      ?sortBy=perc&sortType=DESC&type=0&page=N
      &country=India&countryCode=IN&posList=[63]
      &catId=[N]          # optional, for a top-level category

  GET https://buyhatke.com/api/getDealsCategories?countryCode=IN&countryName=India

Do not pass `per` (it is not page size and can return an empty list).

The `avg` field is BuyHatke's deal-scanner historical average, not a
documented trailing-30-day mean. `perc` is the rounded percent below that
average.

Approach (same as the 26 Nov 2026 exhaustive digest):
  1. Page the all-deals feed, sorted by biggest drop, up to 150 pages.
  2. Page each of BuyHatke's 23 top-level categories until a page is empty
     or its maximum drop falls under 10%.
  3. Union, dedupe by ASIN or PID, and assign a category name. Items that
     never appeared in a category feed are grouped as Other. Items listed
     in more than one category are filed under the more specific one.

Rules applied to every item:
  Amazon.in (pos 63), in stock, price >= Rs 1000, at least 10% below
  average, Amazon rating >= 4.0. Unrated items are excluded.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # fixed offset; GitHub runners lack tzdata
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BASE = "https://buyhatke.com"
DEALS_URL = f"{BASE}/api/bbd-deals"
CATS_URL = f"{BASE}/api/getDealsCategories"
AMAZON_POS = 63
ASIN_RE = re.compile(r"^B[0-9A-Z]{9}$")

MIN_PRICE = 1000.0
MIN_PCT = 10.0
MIN_RATING = 4.0
DEFAULT_MAX_PAGES_ALL = 150
DEFAULT_MAX_PAGES_CAT = 80
DEFAULT_SLEEP = 0.5
DEFAULT_MIN_ITEMS = 100
DEFAULT_RETRIES = 3
RETRY_STATUSES = {403, 408, 425, 429, 500, 502, 503, 504}

# Narrower first. When a product appears in more than one category feed,
# it is filed under the earliest name in this list.
SPECIFICITY_ORDER = [
    "Shoes",
    "Watches",
    "Jewellery",
    "Bags, Wallets & Luggage",
    "Beauty",
    "Video Games",
    "Musical Instruments",
    "Software",
    "Kindle Store",
    "Pet Supplies",
    "Grocery & Gourmet Foods",
    "Car & Motorbike",
    "Sports, Fitness & Outdoors",
    "Outdoor Living",
    "Home Medical Supplies & Equipment",
    "Office Products",
    "Clothing & Accessories",
    "Computers & Accessories",
    "Home & Kitchen",
    "Home Improvement",
    "Toys & Games",
    "Health & Personal Care",
    "Electronics",
]
SPECIFICITY_INDEX = {name: i for i, name in enumerate(SPECIFICITY_ORDER)}

FAKE_BRANDS = {"", "na", "n/a", "null", "none", "generic"}

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json,text/plain,*/*",
    "Referer": f"{BASE}/deals",
    "Origin": BASE,
    "Accept-Language": "en-IN,en;q=0.9",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "deals.json"


class FetchError(Exception):
    """A request failed after retries, or the API returned a bad payload."""


def ist_now() -> datetime:
    return datetime.now(IST)


def format_ist(dt: datetime | None = None) -> str:
    return (dt or ist_now()).strftime("%Y-%m-%d %I:%M %p IST")


def http_get_json(url: str, timeout: int = 25, retries: int = DEFAULT_RETRIES) -> dict:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            last_err = e
            body = b""
            try:
                body = e.read()[:200]
            except Exception:
                pass
            if e.code in RETRY_STATUSES and attempt < retries:
                wait = min(20.0, 1.5 * attempt + (2 if e.code in {403, 429} else 0))
                print(
                    f"  HTTP {e.code} from {url} (attempt {attempt}/{retries}); "
                    f"retrying in {wait:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            raise FetchError(
                f"HTTP {e.code} from {url}. "
                "BuyHatke is behind Cloudflare; a 403 usually means the "
                "request was blocked (common from GitHub-hosted runners)."
            ) from e
        except urllib.error.URLError as e:
            last_err = e
            if attempt < retries:
                wait = 1.5 * attempt
                print(
                    f"  Network error fetching {url} (attempt {attempt}/{retries}): {e}; "
                    f"retrying in {wait:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            raise FetchError(f"Network error fetching {url}: {e}") from e
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise FetchError(f"Non-JSON response from {url} (HTTP {code})") from e
        if not isinstance(data, dict):
            raise FetchError(f"Unexpected JSON type from {url}: {type(data)}")
        return data
    raise FetchError(f"Failed to fetch {url}: {last_err}")


def fetch_deals_page(page: int, cat_ids: list[int] | None = None) -> list[dict]:
    params: dict[str, str] = {
        "sortBy": "perc",
        "sortType": "DESC",
        "type": "0",
        "page": str(page),
        "country": "India",
        "countryCode": "IN",
        "posList": json.dumps([AMAZON_POS], separators=(",", ":")),
    }
    if cat_ids:
        params["catId"] = json.dumps(cat_ids, separators=(",", ":"))
    url = DEALS_URL + "?" + urllib.parse.urlencode(params)
    payload = http_get_json(url)
    if payload.get("status") != 1:
        raise FetchError(
            f"BuyHatke returned status={payload.get('status')} "
            f"msg={payload.get('msg')!r} for {url}"
        )
    data = payload.get("data") or []
    if not isinstance(data, list):
        raise FetchError(f"Unexpected deals payload shape: {type(data)}")
    return data


def amazon_url(pid: str) -> str | None:
    if ASIN_RE.match(pid or ""):
        return f"https://www.amazon.in/dp/{pid}"
    return None


def percent_below(price: float, avg: float) -> float | None:
    if not avg or avg <= 0:
        return None
    return (avg - price) / avg * 100.0


def parse_rating(row: dict) -> tuple[float | None, int | None, bool]:
    """Parse BuyHatke's Amazon star rating fields.

    Returns (rating, rating_count, found). BuyHatke uses rating=-1 and
    rating_count=0 when it has no rating for the product.
    """
    raw = row.get("rating")
    raw_count = row.get("rating_count")
    count: int | None
    try:
        count = int(raw_count) if raw_count is not None and raw_count != "" else None
    except (TypeError, ValueError):
        count = None

    if raw is None or raw == "":
        return None, count, False
    try:
        rating = float(raw)
    except (TypeError, ValueError):
        return None, count, False
    if rating < 0:
        return None, count if count and count > 0 else 0, False
    if rating == 0 and (count is None or count <= 0):
        return None, 0, False
    return rating, (count if count is not None else None), True


def extract_subcategory(prod: str, prod_search: str, brand: str | None) -> str | None:
    """Pull the trailing subcategory out of BuyHatke's prodSearch field."""
    if not prod_search:
        return None
    prod = (prod or "").strip()
    psearch = prod_search.strip()
    rest = psearch
    if prod:
        if psearch.startswith(prod):
            cut = len(prod)
        else:
            cut = 0
            for a, b in zip(prod, psearch):
                if a != b:
                    break
                cut += 1
            if cut <= 10:
                cut = 0
        # If the stored product name was truncated mid-word, back up to a
        # space so leftover title letters are not glued to the subcategory.
        if cut and cut < len(psearch) and psearch[cut] not in " \t":
            sp = psearch.rfind(" ", 0, cut)
            if sp >= 0:
                cut = sp
        if cut:
            rest = psearch[cut:].strip()
    brand = (brand or "").strip()
    if brand and brand.lower() not in FAKE_BRANDS:
        if rest.endswith(brand):
            rest = rest[: -len(brand)].strip()
        elif rest.lower().endswith(brand.lower()):
            rest = rest[: -len(brand)].strip()
    if rest.endswith(" na") or rest == "na":
        rest = rest[:-3].strip() if rest.endswith(" na") else ""
    if rest.endswith(" Feature"):
        rest = rest[:-8].strip()
    # Guard against leftover title fragments that are clearly not a category.
    if not rest or len(rest) > 60:
        return None
    return rest


def normalize(row: dict) -> dict | None:
    try:
        price = float(row["price"])
        avg = float(row["avg"])
    except (KeyError, TypeError, ValueError):
        return None
    pct = percent_below(price, avg)
    if pct is None:
        return None
    pid = str(row.get("PID") or "")
    rating, rating_count, rating_found = parse_rating(row)
    brand_raw = row.get("brand") or None
    if isinstance(brand_raw, str):
        brand_raw = brand_raw.strip() or None
        if brand_raw and brand_raw.lower() in FAKE_BRANDS:
            brand_raw = None
    name = row.get("prod") or ""
    prod_search = row.get("prodSearch") or ""
    return {
        "name": name,
        "asin": pid if ASIN_RE.match(pid) else None,
        "pid": pid,
        "current_price_inr": round(price, 2),
        "buyhatke_avg_inr": round(avg, 2),
        "percent_below_avg": round(pct, 1),
        "in_stock": row.get("oos") == 0,
        "rating": rating,
        "rating_count": rating_count,
        "rating_found": rating_found,
        "brand": brand_raw,
        "subcategory": extract_subcategory(name, prod_search, row.get("brand")),
        "amazon_url": amazon_url(pid),
        "page_perc": row.get("perc"),
    }


def qualifies(item: dict, counts: Counter) -> bool:
    if not item or not item["in_stock"]:
        counts["oos_or_bad"] += 1
        return False
    if item["percent_below_avg"] < MIN_PCT:
        counts["low_pct"] += 1
        return False
    if item["current_price_inr"] < MIN_PRICE:
        counts["low_price"] += 1
        return False
    if not item["rating_found"] or item["rating"] is None:
        counts["no_rating"] += 1
        return False
    if item["rating"] < MIN_RATING:
        counts["low_rating"] += 1
        return False
    return True


def item_key(item: dict) -> str:
    return str(item.get("asin") or item.get("pid") or "")


def page_perc_range(rows: list[dict]) -> tuple[float | None, float | None]:
    percs = []
    for row in rows:
        try:
            percs.append(float(row.get("perc")))
        except (TypeError, ValueError):
            try:
                price = float(row["price"])
                avg = float(row["avg"])
                pct = percent_below(price, avg)
                if pct is not None:
                    percs.append(pct)
            except (KeyError, TypeError, ValueError):
                continue
    if not percs:
        return None, None
    return max(percs), min(percs)


def load_top_categories() -> list[dict]:
    url = CATS_URL + "?" + urllib.parse.urlencode(
        {
            "countryCode": "IN",
            "countryName": "India",
            "rand": str(int(time.time() // 86400)),
        }
    )
    payload = http_get_json(url)
    if payload.get("status") != 1:
        raise FetchError(
            f"BuyHatke categories returned status={payload.get('status')} "
            f"msg={payload.get('msg')!r}"
        )
    data = payload.get("data") or {}
    items = data.values() if isinstance(data, dict) else data
    tops: list[dict] = []
    for item in items:
        if not isinstance(item, dict) or "name" not in item or "id" not in item:
            continue
        parent = item.get("parent_id")
        if parent in (0, None, "0"):
            tops.append(
                {
                    "id": int(item["id"]),
                    "name": str(item["name"]),
                    "n_sub": len(item.get("sub_cats") or []),
                }
            )
    if not tops:
        raise FetchError("BuyHatke returned no top-level categories")
    tops.sort(key=lambda c: (SPECIFICITY_INDEX.get(c["name"], 1000), c["id"]))
    return tops


def scan_feed(
    *,
    label: str,
    cat_ids: list[int] | None,
    max_pages: int,
    sleep_s: float,
    stop_under_min_pct: bool,
) -> dict:
    raw_count = 0
    pages = 0
    qualifying: list[dict] = []
    seen: set[str] = set()
    counts: Counter = Counter()
    last_max: float | None = None
    last_min: float | None = None
    stop_reason = "hit page cap"
    page_stats: list[dict] = []

    for page in range(1, max_pages + 1):
        batch = fetch_deals_page(page, cat_ids=cat_ids)
        pages = page
        raw_count += len(batch)
        max_p, min_p = page_perc_range(batch)
        last_max, last_min = max_p, min_p
        amazon_rows = 0
        for row in batch:
            if row.get("pos") != AMAZON_POS:
                continue
            amazon_rows += 1
            item = normalize(row)
            if not item:
                counts["oos_or_bad"] += 1
                continue
            if not qualifies(item, counts):
                continue
            key = item_key(item)
            if not key or key in seen:
                counts["dup"] += 1
                continue
            seen.add(key)
            qualifying.append(item)

        page_stats.append(
            {
                "page": page,
                "rows": len(batch),
                "amazon": amazon_rows,
                "max_perc": max_p,
                "min_perc": min_p,
            }
        )
        print(
            f"  {label} page {page}: {len(batch)} rows, "
            f"{amazon_rows} Amazon, perc {min_p}–{max_p}, "
            f"{len(qualifying)} qualifying so far",
            flush=True,
        )

        if len(batch) == 0:
            stop_reason = "empty page"
            break
        if stop_under_min_pct and max_p is not None and max_p < MIN_PCT:
            stop_reason = f"page {page} max perc {max_p} < {MIN_PCT}"
            break
        if page < max_pages:
            time.sleep(sleep_s)
    else:
        stop_reason = f"hit cap of {max_pages} pages"

    return {
        "label": label,
        "pages": pages,
        "raw_count": raw_count,
        "qualifying": qualifying,
        "counts": dict(counts),
        "stop_reason": stop_reason,
        "last_page_perc": {"max": last_max, "min": last_min},
        "page_stats": page_stats,
    }


def pick_category(names: list[str]) -> str:
    if not names:
        return "Other"
    unique = list(dict.fromkeys(names))
    unique.sort(key=lambda n: (SPECIFICITY_INDEX.get(n, 1000), n))
    return unique[0]


def site_item(item: dict, category: str, subcategory: str | None) -> dict:
    return {
        "name": item["name"],
        "asin": item["asin"],
        "pid": item["pid"],
        "current_price_inr": item["current_price_inr"],
        "buyhatke_avg_inr": item["buyhatke_avg_inr"],
        "percent_below_avg": item["percent_below_avg"],
        "rating": item["rating"],
        "rating_count": item["rating_count"] if item["rating_count"] is not None else 0,
        "brand": item.get("brand"),
        "category": category,
        "subcategory": subcategory,
        "amazon_url": item["amazon_url"]
        or (f"https://www.amazon.in/dp/{item['asin']}" if item.get("asin") else None),
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # Validate before replacing any existing file.
    parsed = json.loads(text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        raise FetchError("Refusing to write invalid deals payload")
    fd, tmp_name = tempfile.mkstemp(
        prefix="deals.", suffix=".json.tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def build_payload(
    *,
    all_scan: dict,
    cat_scans: list[dict],
    tops: list[dict],
    started: datetime,
    finished: datetime,
    sleep_s: float,
) -> dict:
    # Key -> item from all-deals, and key -> list of category names from feeds.
    all_by_key: dict[str, dict] = {}
    for item in all_scan["qualifying"]:
        all_by_key[item_key(item)] = item

    cat_by_key: dict[str, dict] = {}
    cats_for_key: dict[str, list[str]] = {}
    sub_for_key: dict[str, str] = {}
    for scan in cat_scans:
        name = scan["category_name"]
        for item in scan["qualifying"]:
            key = item_key(item)
            cats_for_key.setdefault(key, [])
            if name not in cats_for_key[key]:
                cats_for_key[key].append(name)
            # Prefer the first subcategory we saw; later scans may differ.
            if key not in cat_by_key:
                cat_by_key[key] = item
            if item.get("subcategory") and key not in sub_for_key:
                sub_for_key[key] = item["subcategory"]

    all_keys = set(all_by_key)
    cat_keys = set(cat_by_key)
    union_keys = all_keys | cat_keys

    items: list[dict] = []
    multi = 0
    for key in union_keys:
        names = cats_for_key.get(key) or []
        if len(names) > 1:
            multi += 1
        category = pick_category(names)
        source = all_by_key.get(key) or cat_by_key[key]
        # Category-feed subcategory is more reliable than all-deals (usually none).
        sub = sub_for_key.get(key)
        if category == "Other":
            sub = None
        items.append(site_item(source, category, sub))

    items.sort(
        key=lambda x: (-x["percent_below_avg"], x["current_price_inr"], x["name"] or "")
    )

    category_counts = Counter(i["category"] for i in items)
    few = sum(1 for i in items if (i.get("rating_count") or 0) < 20)
    no_asin = sum(1 for i in items if not i.get("asin"))
    only_all = len(all_keys - cat_keys)
    only_cat = len(cat_keys - all_keys)
    in_both = len(all_keys & cat_keys)

    cat_summaries = []
    for scan in cat_scans:
        cat_summaries.append(
            {
                "id": scan["category_id"],
                "name": scan["category_name"],
                "pages": scan["pages"],
                "raw_count": scan["raw_count"],
                "qualifying": len(scan["qualifying"]),
                "stop_reason": scan["stop_reason"],
                "counts": scan["counts"],
                "last_page_perc": scan["last_page_perc"],
            }
        )

    last = all_scan["last_page_perc"]
    meta = {
        "fetched_at": format_ist(finished),
        "fetched_at_range": f"{format_ist(started)} – {format_ist(finished)}",
        "timezone": "Asia/Calcutta",
        "store": "Amazon.in",
        "source_all_deals": (
            f"{DEALS_URL}?sortBy=perc&sortType=DESC&type=0"
            f"&country=India&countryCode=IN&posList=[63]"
        ),
        "source_categories": (
            f"{CATS_URL}?countryCode=IN&countryName=India"
        ),
        "rules": {
            "in_stock": True,
            "min_price_inr": MIN_PRICE,
            "min_percent_below_avg": MIN_PCT,
            "min_rating": MIN_RATING,
            "exclude_unrated": True,
            "deduped_by": "ASIN or PID",
        },
        "avg_definition": (
            "BuyHatke's history average (`avg`), not a strict 30-day mean. "
            "A drop of 80% or more can come from an inflated average rather "
            "than a typical selling price."
        ),
        "all_deals_scan": {
            "pages": all_scan["pages"],
            "raw_count": all_scan["raw_count"],
            "qualifying": len(all_scan["qualifying"]),
            "excluded_no_rating": all_scan["counts"].get("no_rating", 0),
            "excluded_low_rating": all_scan["counts"].get("low_rating", 0),
            "stop_reason": all_scan["stop_reason"],
            "last_page_perc_range": [last["min"], last["max"]],
        },
        "category_scans": cat_summaries,
        "top_categories": tops,
        "merge": {
            "union_qualifying": len(items),
            "in_both": in_both,
            "only_all_deals": only_all,
            "only_category_feeds": only_cat,
            "other_not_in_any_filter": category_counts.get("Other", 0),
            "multi_category_assigned_by_specificity": multi,
            "specificity_order": SPECIFICITY_ORDER,
        },
        "qualifying": len(items),
        "few_reviews_under_20": few,
        "no_asin": no_asin,
        "category_counts": dict(
            sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "sleep_seconds": sleep_s,
    }
    return {"meta": meta, "items": items}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Path to write deals.json (default: data/deals.json next to this repo)",
    )
    parser.add_argument("--max-pages-all", type=int, default=DEFAULT_MAX_PAGES_ALL)
    parser.add_argument("--max-pages-cat", type=int, default=DEFAULT_MAX_PAGES_CAT)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    parser.add_argument(
        "--min-items",
        type=int,
        default=DEFAULT_MIN_ITEMS,
        help=(
            "If fewer than this many items qualify, exit non-zero and do not "
            f"overwrite an existing deals.json (default {DEFAULT_MIN_ITEMS})"
        ),
    )
    args = parser.parse_args(argv)
    out = Path(args.out)

    started = ist_now()
    print(f"Starting fetch at {format_ist(started)}", flush=True)

    try:
        print("Loading BuyHatke top-level categories…", flush=True)
        tops = load_top_categories()
        print(f"  {len(tops)} top-level categories", flush=True)
        for cat in tops:
            print(f"    {cat['id']:5}  {cat['name']}", flush=True)

        print(
            f"Scanning all-deals feed (up to {args.max_pages_all} pages)…",
            flush=True,
        )
        all_scan = scan_feed(
            label="all-deals",
            cat_ids=None,
            max_pages=args.max_pages_all,
            sleep_s=args.sleep,
            stop_under_min_pct=False,
        )
        print(
            f"  all-deals done: {len(all_scan['qualifying'])} qualifying, "
            f"{all_scan['stop_reason']}",
            flush=True,
        )

        cat_scans: list[dict] = []
        for i, cat in enumerate(tops, 1):
            print(
                f"Scanning category {i}/{len(tops)}: {cat['name']} ({cat['id']})…",
                flush=True,
            )
            scan = scan_feed(
                label=cat["name"],
                cat_ids=[cat["id"]],
                max_pages=args.max_pages_cat,
                sleep_s=args.sleep,
                stop_under_min_pct=True,
            )
            scan["category_id"] = cat["id"]
            scan["category_name"] = cat["name"]
            cat_scans.append(scan)
            print(
                f"  {cat['name']} done: {len(scan['qualifying'])} qualifying, "
                f"{scan['stop_reason']}",
                flush=True,
            )
            # Small extra pause between categories.
            time.sleep(max(0.2, args.sleep * 0.5))

        finished = ist_now()
        payload = build_payload(
            all_scan=all_scan,
            cat_scans=cat_scans,
            tops=tops,
            started=started,
            finished=finished,
            sleep_s=args.sleep,
        )
    except FetchError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Existing deals.json was not modified.",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"ERROR: unexpected failure: {e}", file=sys.stderr)
        print("Existing deals.json was not modified.", file=sys.stderr)
        return 1

    n = len(payload["items"])
    if n < args.min_items:
        print(
            f"ERROR: only {n} qualifying items (minimum {args.min_items}). "
            "Existing deals.json was not modified.",
            file=sys.stderr,
        )
        return 2

    try:
        write_atomic(out, payload)
    except Exception as e:
        print(f"ERROR: failed to write {out}: {e}", file=sys.stderr)
        return 1

    elapsed = (finished - started).total_seconds()
    print(
        f"Wrote {n} deals to {out} in {elapsed:.1f}s "
        f"({payload['meta']['category_counts']})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
