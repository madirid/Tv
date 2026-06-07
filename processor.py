import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    BD_KEYWORDS, INDIA_KEYWORDS, CARTOON_KEYWORDS,
    NEWS_KEYWORDS, SPORTS_KEYWORDS, MOVIES_KEYWORDS,
    MUSIC_KEYWORDS, ADULT_KEYWORDS,
    CATEGORY_ORDER, CATEGORY_LABELS
)
from logos import LOGO_DB, DEFAULT_LOGO
from utils import ffmpeg_check, clean_name


# ── Logo ──────────────────────────────────────────────────────────────────────
def get_logo(name):
    low = name.lower()
    for k, v in LOGO_DB.items():
        if k in low:
            return v
    return DEFAULT_LOGO


# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_display_name(extinf_line):
    """Pull just the channel name after the last comma in an EXTINF line."""
    if "," in extinf_line:
        return extinf_line.split(",", 1)[-1].strip()
    return extinf_line.strip()


def is_adult(text):
    t = text.lower()
    return any(word.lower() in t for word in ADULT_KEYWORDS)


# ── Category detection ────────────────────────────────────────────────────────
def detect(name):
    low = name.lower()
    if any(x.lower() in low for x in BD_KEYWORDS):      return "bd"
    if any(x.lower() in low for x in CARTOON_KEYWORDS): return "cartoon"
    if any(x.lower() in low for x in INDIA_KEYWORDS):   return "india"
    if any(x.lower() in low for x in NEWS_KEYWORDS):    return "news"
    if any(x.lower() in low for x in SPORTS_KEYWORDS):  return "sports"
    if any(x.lower() in low for x in MOVIES_KEYWORDS):  return "movies"
    if any(x.lower() in low for x in MUSIC_KEYWORDS):   return "music"
    return "other"


# ── M3U parser ────────────────────────────────────────────────────────────────
def parse_m3u(text):
    """Return list of (display_name, url) — ignores source group-title."""
    lines = text.splitlines()
    out = []
    for i in range(len(lines) - 1):
        if lines[i].startswith("#EXTINF"):
            url = lines[i + 1].strip()
            if url.startswith("http"):
                display_name = extract_display_name(lines[i])
                out.append((display_name, url))
    return out


# ── Network helpers (run in thread pools) ─────────────────────────────────────
def fetch_source(url):
    try:
        return requests.get(url, timeout=15).text
    except Exception:
        return None


def check_url(name_url):
    name, url = name_url
    try:
        return (name, url) if ffmpeg_check(url) else None
    except Exception:
        return None


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    with open("sources.txt") as f:
        sources = [x.strip() for x in f if x.strip()]

    # ── Step 1: Fetch all sources in parallel ─────────────────────────────────
    print(f"[1/4] Fetching {len(sources)} sources in parallel...")
    with ThreadPoolExecutor(max_workers=10) as ex:
        pages = list(ex.map(fetch_source, sources))

    all_channels = []
    for page in pages:
        if page:
            all_channels.extend(parse_m3u(page))

    total = len(all_channels)
    print(f"      → {total} channels found across all sources")

    # ── Step 2: Adult filter (fast, no network) ───────────────────────────────
    print(f"[2/4] Filtering adult content...")
    clean = [
        (n, u) for n, u in all_channels
        if not is_adult(n) and not is_adult(u)
    ]
    skipped_adult = total - len(clean)
    print(f"      → {skipped_adult} adult channels removed, {len(clean)} remaining")

    # ── Step 3: Check URLs in parallel ────────────────────────────────────────
    print(f"[3/4] Checking {len(clean)} URLs in parallel (50 threads)...")
    seen_urls = set()
    valid = []

    with ThreadPoolExecutor(max_workers=50) as ex:
        futures = {ex.submit(check_url, item): item for item in clean}
        for future in as_completed(futures):
            result = future.result()
            if result:
                name, url = result
                if url not in seen_urls:      # deduplicate same URL
                    seen_urls.add(url)
                    valid.append((name, url))

    print(f"      → {len(valid)} live channels confirmed")

    # ── Step 4: Categorize & build output ─────────────────────────────────────
    print(f"[4/4] Categorizing and writing output...")
    buckets = {cat: [] for cat in CATEGORY_ORDER}

    for name, url in valid:
        cat   = detect(name)
        logo  = get_logo(name)
        short = clean_name(name)
        group = CATEGORY_LABELS[cat]
        buckets[cat].append((short, url, logo, group))

    # Write M3U with clean, normalized group-title tags
    with open("output.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for cat in CATEGORY_ORDER:
            for name, url, logo, group in buckets[cat]:
                f.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group}",{name}\n')
                f.write(f"{url}\n")

    # Write stats
    final_count = sum(len(buckets[c]) for c in CATEGORY_ORDER)
    with open("stats.txt", "w") as f:
        f.write(f"Total found    : {total}\n")
        f.write(f"Adult skipped  : {skipped_adult}\n")
        f.write(f"Duplicates     : {len(clean) - skipped_adult - final_count}\n")
        f.write(f"Valid channels : {final_count}\n")
        f.write("-" * 32 + "\n")
        for cat in CATEGORY_ORDER:
            label = CATEGORY_LABELS[cat]
            f.write(f"  {label:<18}: {len(buckets[cat])}\n")

    print(f"PRO DONE — {final_count} channels in {len(CATEGORY_ORDER)} categories")


if __name__ == "__main__":
    main()
    
