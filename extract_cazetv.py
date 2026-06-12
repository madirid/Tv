#!/usr/bin/env python3
"""
extract_cazetv.py  (v3 – YouTube + yt-dlp + geo-bypass)
---------------------------------------------------------
Supports:
  • YouTube live streams  → uses yt-dlp (reliable, geo-bypass built-in)
  • Other sites           → falls back to Playwright

Geo-bypass strategy for YouTube:
  1. yt-dlp --geo-bypass        : fakes X-Forwarded-For header (works most of the time)
  2. yt-dlp --geo-bypass-country: forces a specific country's IP header if #1 fails
  3. yt-dlp player_client=ios   : iOS client often bypasses restrictions the web client can't

Usage:
  Set STREAM_URL below, or pass it as first argument:
    python extract_cazetv.py https://www.youtube.com/watch?v=CRtjePKnGvA
"""

import sys
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
STREAM_URL  = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=CRtjePKnGvA"
OUTPUT_FILE = Path("Cazetv.m3u")
MAX_RETRIES = 3

# Country code to spoof if the stream is geo-locked
# Common values: US, BR, GB, IN, FR, DE  — change to the country that CAN watch it
GEO_COUNTRY = "BR"

CHANNEL_LOGO = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/"
    "0/0f/Caze_TV_logo.svg/1200px-Caze_TV_logo.svg.png"
)

# ── M3U builder ───────────────────────────────────────────────────────────────
def build_m3u(stream_url: str, updated_at: str) -> str:
    return (
        "#EXTM3U\n"
        f"# Updated: {updated_at}\n"
        "#EXTINF:-1"
        ' tvg-id="CazeTV"'
        ' tvg-name="Caze TV"'
        f' tvg-logo="{CHANNEL_LOGO}"'
        ' group-title="Sports"'
        ",Caze TV\n"
        f"{stream_url}\n"
    )

# ── YouTube extractor (yt-dlp) ────────────────────────────────────────────────
def extract_youtube(url: str) -> str | None:
    try:
        import yt_dlp
    except ImportError:
        print("ERROR: yt-dlp not installed.  Run: pip install yt-dlp")
        return None

    # Try multiple client strategies — iOS/Android bypass most geo-locks
    client_strategies = [
        ["ios", "web"],
        ["android", "web"],
        ["web"],
    ]

    for clients in client_strategies:
        print(f"  Trying player clients: {clients} …")
        ydl_opts = {
            # ── Geo-bypass ─────────────────────────────────────────────
            "geo_bypass":         True,
            "geo_bypass_country": GEO_COUNTRY,
            # ── Format: prefer HLS (m3u8) for IPTV compatibility ───────
            "format": (
                "best[protocol=m3u8_native]"
                "/best[protocol=m3u8]"
                "/bestvideo[protocol=m3u8_native]+bestaudio"
                "/best"
            ),
            # ── Misc ────────────────────────────────────────────────────
            "skip_download":   True,
            "quiet":           False,
            "no_warnings":     False,
            "noplaylist":      True,
            "extractor_args": {
                "youtube": {
                    "player_client": clients,
                    # skip age-gate redirect
                    "skip": ["hls", "dash", "translated_subs"],
                }
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if not info:
                    continue

                # Prefer a dedicated m3u8 format entry
                for fmt in info.get("formats", []):
                    proto = fmt.get("protocol", "")
                    if "m3u8" in proto and fmt.get("url"):
                        print(f"  Found m3u8 format (protocol={proto})")
                        return fmt["url"]

                # Fallback: top-level URL
                direct = info.get("url")
                if direct:
                    print(f"  Found direct URL")
                    return direct

        except Exception as exc:
            print(f"  [warn] {exc}")

    return None

# ── Playwright fallback (non-YouTube sites) ───────────────────────────────────
async def extract_playwright(url: str) -> str | None:
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("ERROR: playwright not installed.")
        return None

    stealth_fn = None
    try:
        from playwright_stealth import stealth_async
        stealth_fn = stealth_async
    except ImportError:
        pass

    found: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--disable-web-security",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        page = await ctx.new_page()
        if stealth_fn:
            await stealth_fn(page)

        def on_req(req):
            u = req.url
            if ".m3u8" in u and u not in found:
                print(f"  [req ] {u}")
                found.append(u)

        async def on_resp(resp):
            u  = resp.url
            ct = resp.headers.get("content-type", "")
            if (".m3u8" in u or "mpegurl" in ct.lower()) and u not in found:
                print(f"  [resp] {u}")
                found.append(u)

        page.on("request",  on_req)
        page.on("response", on_resp)

        try:
            await page.goto(url, wait_until="load", timeout=30_000)
        except PWTimeout:
            print("  [warn] goto timed out — still scanning…")
        except Exception as exc:
            print(f"  [warn] {exc}")

        await asyncio.sleep(12)

        html = await page.content()
        for u in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html):
            if u not in found:
                found.append(u)

        # Also try iframes
        if not found:
            for fr in await page.query_selector_all("iframe"):
                src = await fr.get_attribute("src") or ""
                if src and src.startswith("http"):
                    p2 = await ctx.new_page()
                    try:
                        await p2.goto(src, wait_until="load", timeout=20_000)
                        await asyncio.sleep(8)
                        for u in re.findall(
                            r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
                            await p2.content()
                        ):
                            if u not in found:
                                found.append(u)
                    except Exception:
                        pass
                    finally:
                        await p2.close()
                if found:
                    break

        await browser.close()

    return found[0] if found else None


# ── Dispatcher ─────────────────────────────────────────────────────────────────
def is_youtube(url: str) -> bool:
    return any(h in url for h in ("youtube.com", "youtu.be", "m.youtube.com"))


async def extract(url: str) -> str | None:
    if is_youtube(url):
        print("  → YouTube URL detected — using yt-dlp")
        return extract_youtube(url)
    else:
        print("  → Non-YouTube URL — using Playwright")
        return await extract_playwright(url)


# ── Entry point ────────────────────────────────────────────────────────────────
async def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 55)
    print("  Stream Extractor  (v3)")
    print(f"  URL    : {STREAM_URL}")
    print(f"  Run    : {ts}")
    print(f"  Geo    : spoofing {GEO_COUNTRY}")
    print("=" * 55)

    stream_url = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n── Attempt {attempt}/{MAX_RETRIES} " + "─" * 32)
        try:
            stream_url = await extract(STREAM_URL)
            if stream_url:
                break
        except Exception as exc:
            print(f"  [error] {exc}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(attempt * 5)

    if not stream_url:
        if OUTPUT_FILE.exists():
            print(f"\n⚠️  Extraction failed — keeping existing {OUTPUT_FILE}.")
            return 1
        print(f"\n❌  Extraction failed — no {OUTPUT_FILE} written.")
        return 1

    OUTPUT_FILE.write_text(build_m3u(stream_url, ts), encoding="utf-8")
    print(f"\n✅  {OUTPUT_FILE} updated.")
    print(f"   Stream : {stream_url[:80]}…")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
  
