import re
import subprocess
import requests
from requests.adapters import HTTPAdapter
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    BD_KEYWORDS, INDIA_KEYWORDS, CARTOON_KEYWORDS,
    NEWS_KEYWORDS, SPORTS_KEYWORDS, MOVIES_KEYWORDS,
    MUSIC_KEYWORDS, ADULT_KEYWORDS,
    CATEGORY_ORDER, CATEGORY_LABELS,
)
from logos import LOGO_DB, DEFAULT_LOGO
from utils import clean_name


# ── HTTP session with large pool ──────────────────────────────────────────────
_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=300, pool_maxsize=300)
_session.mount("http://",  _adapter)
_session.mount("https://", _adapter)


# ── Logo ──────────────────────────────────────────────────────────────────────
def get_logo(name):
    low = name.lower()
    for k, v in LOGO_DB.items():
        if k in low:
            return v
    return DEFAULT_LOGO


# ── Adult filter ──────────────────────────────────────────────────────────────
def is_adult(text):
    """
    Check ANY text against adult keywords.
    Called with the FULL EXTINF line so group-title / tvg-name / tvg-id
    are all included — not just the display name after the comma.
    """
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


# ── Stage 1: HTTP alive check ─────────────────────────────────────────────────
def http_alive(url, timeout=3):
    try:
        r = _session.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 405:
            r = _session.get(url, timeout=timeout, stream=True, allow_redirects=True)
            r.close()
        return r.status_code in (200, 206)
    except Exception:
        return False


# ── Stage 2: ffprobe stream validation ───────────────────────────────────────
def stream_valid(url, timeout=4):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",               "error",
                "-analyzeduration", "1000000",
                "-probesize",       "300000",
                "-timeout",         "2000000",
                url,
            ],
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except Exception:
        return False


# ── M3U parser ────────────────────────────────────────────────────────────────
def extract_display_name(extinf_line):
    if "," in extinf_line:
        return extinf_line.split(",", 1)[-1].strip()
    return extinf_line.strip()


def parse_m3u(text):
    """
    Parse channels and apply adult filter HERE against the FULL EXTINF line.
    This catches adult keywords in group-title, tvg-name, tvg-id, and display name.
    """
    lines = text.splitlines()
    out = []
    for i in range(len(lines) - 1):
        if lines[i].startswith("#EXTINF"):
            extinf = lines[i]          # full line — attributes + display name
            url    = lines[i + 1].strip()

            if not url.startswith(("http", "rtmp", "rtsp")):
                continue

            # ── Adult check on FULL extinf line AND url ───────────────────────
            if is_adult(extinf) or is_adult(url):
                continue               # skip — found adult keyword anywhere

            out.append((extract_display_name(extinf), url))
    return out


def fetch_source(url):
    try:
        return requests.get(url, timeout=15).text
    except Exception:
        return None


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    with open("sources.txt") as f:
        sources = [x.strip() for x in f if x.strip()]

    # ── Step 1: Fetch sources ─────────────────────────────────────────────────
    print(f"[1/5] Fetching {len(sources)} sources...", flush=True)
    with ThreadPoolExecutor(max_workers=10) as ex:
        pages = list(ex.map(fetch_source, sources))

    # parse_m3u already filters adult inside — nothing slips through
    all_channels = []
    skipped_adult = 0
    for page in pages:
        if page:
            before = len(all_channels)
            all_channels.extend(parse_m3u(page))
            # count filtered by comparing raw EXTINF count vs added
            raw = sum(1 for l in page.splitlines() if l.startswith("#EXTINF"))
            skipped_adult += raw - (len(all_channels) - before)

    total_raw = len(all_channels) + skipped_adult
    print(f"      → {total_raw} total | {skipped_adult} adult removed | {len(all_channels)} clean", flush=True)

    # ── Step 2: Second-pass adult filter on display name + URL (safety net) ───
    print(f"[2/5] Second-pass adult safety check...", flush=True)
    before = len(all_channels)
    all_channels = [(n, u) for n, u in all_channels if not is_adult(n) and not is_adult(u)]
    caught_second = before - len(all_channels)
    print(f"      → {caught_second} extra adult caught | {len(all_channels)} remaining", flush=True)

    # ── Step 3: HTTP alive check — 200 threads ───────────────────────────────
    print(f"[3/5] HTTP alive check | {len(all_channels)} channels | 200 threads...", flush=True)
    http_ok = []
    with ThreadPoolExecutor(max_workers=200) as ex:
        futures = {ex.submit(http_alive, u): (n, u) for n, u in all_channels}
        for future in as_completed(futures):
            n, u = futures[future]
            if future.result():
                http_ok.append((n, u))

    http_dead = len(all_channels) - len(http_ok)
    print(f"      → {len(http_ok)} alive | {http_dead} dead removed", flush=True)

    # ── Step 4: ffprobe validation — 100 threads ─────────────────────────────
    print(f"[4/5] Stream validation | {len(http_ok)} channels | 100 threads...", flush=True)
    seen_urls = set()
    valid = []
    with ThreadPoolExecutor(max_workers=100) as ex:
        futures = {ex.submit(stream_valid, u): (n, u) for n, u in http_ok}
        for future in as_completed(futures):
            n, u = futures[future]
            if future.result() and u not in seen_urls:
                seen_urls.add(u)
                valid.append((n, u))

    broken = len(http_ok) - len(valid)
    print(f"      → {len(valid)} valid | {broken} broken/duplicate removed", flush=True)

    # ── Step 5: Categorize & write ────────────────────────────────────────────
    print(f"[5/5] Writing output...", flush=True)
    buckets = {cat: [] for cat in CATEGORY_ORDER}

    for name, url in valid:
        cat   = detect(name)
        logo  = get_logo(name)
        short = clean_name(name)
        group = CATEGORY_LABELS[cat]
        buckets[cat].append((short, url, logo, group))

    with open("output.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for cat in CATEGORY_ORDER:
            for name, url, logo, group in buckets[cat]:
                f.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group}",{name}\n')
                f.write(f"{url}\n")

    final_count = sum(len(buckets[c]) for c in CATEGORY_ORDER)

    with open("stats.txt", "w") as f:
        f.write(f"Total raw      : {total_raw}\n")
        f.write(f"Adult removed  : {skipped_adult + caught_second}\n")
        f.write(f"HTTP dead      : {http_dead}\n")
        f.write(f"Stream broken  : {broken}\n")
        f.write(f"Valid channels : {final_count}\n")
        f.write("-" * 32 + "\n")
        for cat in CATEGORY_ORDER:
            f.write(f"  {CATEGORY_LABELS[cat]:<18}: {len(buckets[cat])}\n")

    print(f"\nPRO DONE — {final_count} channels in {len(CATEGORY_ORDER)} categories", flush=True)


if __name__ == "__main__":
    main()
            
