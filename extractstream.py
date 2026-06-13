"""
IPTV Stream Extractor — Playwright Edition
==========================================
Uses headless Chromium to render JavaScript SPAs and intercepts
ALL network requests/responses to capture .m3u8 HLS stream URLs.

Target: https://stream.codecloud.bd/
No environment variables needed — URL is hardcoded below.
"""

import re
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, Page

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://stream.codecloud.bd/"

# Add individual channel watch pages here if you know them.
# The script will ALSO auto-discover channels from the main page.
MANUAL_CHANNELS = [
    # {"name": "FIFA TV",   "url": "https://stream.codecloud.bd/watch/fifa-tv"},
    # {"name": "beIN 1",    "url": "https://stream.codecloud.bd/watch/bein-1"},
]

OUTPUT_PATH = Path(__file__).parent.parent / "playlist" / "fifa_tv.m3u"

# Regex to find m3u8 URLs in rendered HTML / JS
M3U8_RE = re.compile(r'https?://[^\s\'"<>]+\.m3u8(?:[^\s\'"<>]*)?', re.I)

# Selectors to find channel card links on the listing page
CHANNEL_LINK_SELECTORS = [
    "a[href*='/watch/']",
    "a[href*='/live/']",
    "a[href*='/channel/']",
    ".channel-card a",
    ".channel-item a",
    ".stream-card a",
    "[class*='channel'] a",
    "[class*='stream'] a",
    "[class*='card'] a",
]

# ── Browser helpers ───────────────────────────────────────────────────────────

def make_context(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        extra_http_headers={
            "Referer":  BASE_URL,
            "Origin":   BASE_URL.rstrip("/"),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    return browser, context


def collect_streams(page: Page, url: str, wait_ms: int = 20_000) -> list[str]:
    """
    Navigate to `url`, intercept every network request/response,
    and return deduplicated .m3u8 URLs found in the network traffic
    AND in the rendered HTML source.
    """
    found: list[str] = []

    def on_request(req):
        if ".m3u8" in req.url:
            log.info("  → [REQ ] %s", req.url)
            found.append(req.url)

    def on_response(resp):
        if ".m3u8" in resp.url:
            log.info("  → [RESP] %s", resp.url)
            found.append(resp.url)

    page.on("request",  on_request)
    page.on("response", on_response)

    try:
        page.goto(url, wait_until="networkidle", timeout=wait_ms)
    except Exception as exc:
        log.warning("  ⚠ goto timeout/error (continuing anyway): %s", exc)

    # Extra wait — some players fire HLS requests 2–3 s after page load
    try:
        page.wait_for_timeout(4000)
    except Exception:
        pass

    # Also scan the fully-rendered DOM for m3u8 URLs
    try:
        html = page.content()
        for m in M3U8_RE.findall(html):
            if m not in found:
                log.info("  → [HTML] %s", m)
                found.append(m)
    except Exception:
        pass

    page.remove_listener("request",  on_request)
    page.remove_listener("response", on_response)
    return list(dict.fromkeys(found))   # preserve order, remove dups


def discover_channels(page: Page) -> list[dict]:
    """
    Scrape the main listing page for channel card links.
    Returns a list of {"name": ..., "url": ...} dicts.
    """
    channels = []
    seen_urls: set[str] = set()

    for sel in CHANNEL_LINK_SELECTORS:
        try:
            for el in page.query_selector_all(sel):
                href = el.get_attribute("href") or ""
                if not href:
                    continue
                full_url = urljoin(BASE_URL, href)
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                # Best-effort name extraction
                try:
                    name = (
                        el.inner_text().strip().split("\n")[0]
                        or el.get_attribute("title")
                        or el.get_attribute("aria-label")
                        or "Channel"
                    )
                except Exception:
                    name = "Channel"

                channels.append({"name": name.strip() or "Channel", "url": full_url})
        except Exception:
            continue

    return channels


# ── M3U builder ───────────────────────────────────────────────────────────────

def build_m3u(entries: list[dict]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "#EXTM3U",
        f"# Auto-updated : {ts}",
        f"# Total channels: {len(entries)}",
        f"# Source        : {BASE_URL}",
        "",
    ]
    for e in entries:
        stream = e.get("stream_url", "")
        if not stream:
            continue
        name   = e.get("name",  "Channel")
        logo   = e.get("logo",  "")
        group  = e.get("group", "BD TV")
        lines.append(
            f'#EXTINF:-1'
            f'{f" tvg-logo=\"{logo}\"" if logo else ""}'
            f' group-title="{group}"'
            f',{name}'
        )
        lines.append(stream)
        lines.append("")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    results: list[dict] = []

    with sync_playwright() as p:
        browser, ctx = make_context(p)

        # ── Step 1: Load main page ────────────────────────────────────────────
        log.info("🌐 Loading main page: %s", BASE_URL)
        main_page = ctx.new_page()
        main_streams = collect_streams(main_page, BASE_URL, wait_ms=30_000)

        if main_streams:
            log.info("✅ Found %d stream(s) on main page", len(main_streams))
            for s in main_streams:
                results.append({"name": "CodeCloudBD Live", "group": "BD TV", "stream_url": s})

        # ── Step 2: Discover channel links ────────────────────────────────────
        auto_channels = discover_channels(main_page)
        log.info("🔎 Auto-discovered %d channel link(s)", len(auto_channels))
        main_page.close()

        # Merge manual + auto channels (deduplicate by URL)
        all_channels: list[dict] = list(MANUAL_CHANNELS)
        seen_ch_urls  = {c["url"] for c in all_channels}
        for ch in auto_channels:
            if ch["url"] not in seen_ch_urls:
                all_channels.append(ch)
                seen_ch_urls.add(ch["url"])

        # ── Step 3: Visit each channel page ───────────────────────────────────
        for ch in all_channels[:30]:   # cap at 30 channels per run
            log.info("📺 [%s] → %s", ch["name"], ch["url"])
            pg = ctx.new_page()
            streams = collect_streams(pg, ch["url"], wait_ms=20_000)
            pg.close()

            if streams:
                log.info("   ✅ stream: %s", streams[0])
                results.append({**ch, "stream_url": streams[0]})
            else:
                log.warning("   ⚠ no stream found")

        browser.close()

    # ── Deduplicate results by stream URL ─────────────────────────────────────
    seen_streams: set[str] = set()
    unique: list[dict] = []
    for r in results:
        s = r.get("stream_url", "")
        if s and s not in seen_streams:
            seen_streams.add(s)
            unique.append(r)

    if not unique:
        log.error("❌ No streams found. Playlist NOT updated.")
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_m3u(unique), encoding="utf-8")
    log.info("✅ Playlist saved → %s  (%d channel(s))", OUTPUT_PATH, len(unique))


if __name__ == "__main__":
    main()
  
