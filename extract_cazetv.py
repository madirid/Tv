#!/usr/bin/env python3
"""
extract_cazetv.py
-----------------
Extracts the live HLS (.m3u8) stream URL from circleplay.top/live/caze-tv
and writes / updates  Cazetv.m3u  in the repository root.

Called by GitHub Actions every 30 minutes.
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Dependency guard ──────────────────────────────────────────────────────────
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright not installed.  Run: pip install playwright playwright-stealth")
    sys.exit(1)

try:
    from playwright_stealth import stealth_async
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False
    print("WARNING: playwright-stealth not found – running without stealth mode.")

# ── Config ────────────────────────────────────────────────────────────────────
CHANNEL_URL  = "https://circleplay.top/live/caze-tv"
OUTPUT_FILE  = Path("Cazetv.m3u")        # written to repo root
MAX_RETRIES  = 3                          # retry on failure
WAIT_AFTER_LOAD = 8                       # seconds to wait after networkidle
TIMEOUT_SEC  = 45

CHANNEL_LOGO = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/"
    "0/0f/Caze_TV_logo.svg/1200px-Caze_TV_logo.svg.png"
)

# ── M3U template ──────────────────────────────────────────────────────────────
def build_m3u(stream_url: str, updated_at: str) -> str:
    return (
        f"#EXTM3U\n"
        f"# Updated: {updated_at}\n"
        f"#EXTINF:-1"
        f' tvg-id="CazeTV"'
        f' tvg-name="Caze TV"'
        f' tvg-logo="{CHANNEL_LOGO}"'
        f' group-title="Sports"'
        f",Caze TV\n"
        f"{stream_url}\n"
    )

# ── Browser extractor ─────────────────────────────────────────────────────────
async def extract_once(page_url: str) -> list[str]:
    """Launch headless Chromium, intercept requests, return .m3u8 URLs found."""
    found: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--no-first-run",
                "--no-zygote",
            ],
        )

        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            timezone_id="America/New_York",
        )

        page = await ctx.new_page()

        # Apply stealth patches to hide headless fingerprint
        if STEALTH_AVAILABLE:
            await stealth_async(page)

        # ── Hook: catch every outgoing request ───────────────────────────
        def on_request(req):
            url = req.url
            if ".m3u8" in url and url not in found:
                print(f"  [request ] {url}")
                found.append(url)

        # ── Hook: catch every response ────────────────────────────────────
        async def on_response(resp):
            url = resp.url
            ct  = resp.headers.get("content-type", "")
            if (".m3u8" in url or "mpegurl" in ct.lower()) and url not in found:
                print(f"  [response] {url}")
                found.append(url)

        page.on("request",  on_request)
        page.on("response", on_response)

        # ── Navigate ──────────────────────────────────────────────────────
        try:
            await page.goto(
                page_url,
                wait_until="networkidle",
                timeout=TIMEOUT_SEC * 1_000,
            )
        except Exception as exc:
            print(f"  [warn] goto: {exc}")

        # Give the JS video player time to boot and fire its first segment request
        print(f"  Waiting {WAIT_AFTER_LOAD}s for player …")
        await asyncio.sleep(WAIT_AFTER_LOAD)

        # ── Regex-scan rendered HTML for any remaining embedded URLs ──────
        html = await page.content()
        for url in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html):
            if url not in found:
                print(f"  [html    ] {url}")
                found.append(url)

        await browser.close()

    return found


async def extract_with_retry() -> list[str]:
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n── Attempt {attempt}/{MAX_RETRIES} ──────────────────────────────────")
        try:
            urls = await extract_once(CHANNEL_URL)
            if urls:
                return urls
            print("  No .m3u8 URLs captured this attempt.")
        except Exception as exc:
            print(f"  [error] {exc}")
        if attempt < MAX_RETRIES:
            wait = attempt * 5
            print(f"  Retrying in {wait}s …")
            await asyncio.sleep(wait)
    return []


# ── Entry point ───────────────────────────────────────────────────────────────
async def main() -> int:
    now_utc = datetime.now(timezone.utc)
    ts       = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 55)
    print("  Caze TV Stream Extractor")
    print(f"  Run    : {ts}")
    print(f"  Source : {CHANNEL_URL}")
    print("=" * 55)

    urls = await extract_with_retry()

    if not urls:
        # Keep existing file intact so IPTV apps don't lose the channel
        if OUTPUT_FILE.exists():
            print(f"\n⚠️  Extraction failed — keeping existing {OUTPUT_FILE} unchanged.")
            return 1
        else:
            print(f"\n❌  Extraction failed and no existing {OUTPUT_FILE} found.")
            return 1

    stream_url = urls[0]          # first captured = master playlist
    m3u_content = build_m3u(stream_url, ts)
    OUTPUT_FILE.write_text(m3u_content, encoding="utf-8")

    print(f"\n✅  {OUTPUT_FILE} written successfully.")
    print(f"   Stream : {stream_url}")
    print(f"   Updated: {ts}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
