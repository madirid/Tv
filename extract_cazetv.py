#!/usr/bin/env python3
"""
extract_cazetv.py  (v2 – fixed)
---------------------------------
Key fixes vs v1:
  • wait_until="load" instead of "networkidle"  ← main fix
    (live-stream pages never reach networkidle — they loop forever)
  • Increased per-attempt wait after load (12 s)
  • Also tries iframe src URLs (circleplay often wraps a 3rd-party player)
  • playwright-stealth is optional; no crash if missing
  • Cleaner timeout handling — page.goto timeout reduced, separate wait added
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except ImportError:
    print("ERROR: playwright not installed.  Run: pip install playwright && playwright install chromium --with-deps")
    sys.exit(1)

try:
    from playwright_stealth import stealth_async
    STEALTH = True
except ImportError:
    STEALTH = False

# ── Config ────────────────────────────────────────────────────────────────────
CHANNEL_URL      = "https://circleplay.top/live/caze-tv"
OUTPUT_FILE      = Path("Cazetv.m3u")
MAX_RETRIES      = 3
GOTO_TIMEOUT_MS  = 30_000   # 30 s — just wait for page load event, not idle
PLAYER_WAIT_SEC  = 12       # seconds to watch for m3u8 after page loads
CHANNEL_LOGO     = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/"
    "0/0f/Caze_TV_logo.svg/1200px-Caze_TV_logo.svg.png"
)

CHROME_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-web-security",          # helps with cross-origin iframes
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-zygote",
]

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

# ── Core extractor for a single URL ──────────────────────────────────────────
async def watch_page(page, url: str, found: list[str], label: str = "") -> None:
    """Navigate to url and collect .m3u8 hits for PLAYER_WAIT_SEC seconds."""

    def on_req(req):
        u = req.url
        if ".m3u8" in u and u not in found:
            print(f"  [{label}req ] {u}")
            found.append(u)

    async def on_resp(resp):
        u  = resp.url
        ct = resp.headers.get("content-type", "")
        if (".m3u8" in u or "mpegurl" in ct.lower()) and u not in found:
            print(f"  [{label}resp] {u}")
            found.append(u)

    page.on("request",  on_req)
    page.on("response", on_resp)

    try:
        # ★ Use "load" not "networkidle" — live streams never become idle
        await page.goto(url, wait_until="load", timeout=GOTO_TIMEOUT_MS)
    except PWTimeout:
        print(f"  [{label}warn] goto timed out — still scanning for m3u8…")
    except Exception as exc:
        print(f"  [{label}warn] goto: {exc}")

    # Give the JS video player time to start and fire its first segment request
    print(f"  [{label}    ] Watching {PLAYER_WAIT_SEC}s for m3u8 requests…")
    await asyncio.sleep(PLAYER_WAIT_SEC)

    # Regex scan rendered HTML for inline references
    try:
        html = await page.content()
        for u in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html):
            if u not in found:
                print(f"  [{label}html] {u}")
                found.append(u)
    except Exception:
        pass

# ── Single browser attempt ────────────────────────────────────────────────────
async def extract_once() -> list[str]:
    found: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=CHROME_ARGS)
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

        # ── Main page ─────────────────────────────────────────────────────
        page = await ctx.new_page()
        if STEALTH:
            await stealth_async(page)

        await watch_page(page, CHANNEL_URL, found, label="main:")

        # ── Check iframes — many aggregators embed a 3rd-party player ────
        if not found:
            try:
                iframes = await page.query_selector_all("iframe")
                for fr in iframes:
                    src = await fr.get_attribute("src") or ""
                    if not src or src.startswith("about:"):
                        continue
                    if not src.startswith("http"):
                        src = "https://circleplay.top" + src
                    print(f"  [iframe ] {src}")
                    iframe_page = await ctx.new_page()
                    await watch_page(iframe_page, src, found, label="iframe:")
                    await iframe_page.close()
                    if found:
                        break
            except Exception as exc:
                print(f"  [iframe warn] {exc}")

        await browser.close()

    return found

# ── Retry wrapper ─────────────────────────────────────────────────────────────
async def extract_with_retry() -> list[str]:
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n── Attempt {attempt}/{MAX_RETRIES} " + "─" * 35)
        try:
            urls = await extract_once()
            if urls:
                return urls
            print("  No .m3u8 URLs captured this attempt.")
        except Exception as exc:
            print(f"  [error] {exc}")
        if attempt < MAX_RETRIES:
            delay = attempt * 5
            print(f"  Retrying in {delay}s…")
            await asyncio.sleep(delay)
    return []

# ── Entry point ───────────────────────────────────────────────────────────────
async def main() -> int:
    now_utc = datetime.now(timezone.utc)
    ts = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 55)
    print("  Caze TV Stream Extractor  (v2)")
    print(f"  Run    : {ts}")
    print(f"  Source : {CHANNEL_URL}")
    print(f"  Stealth: {'enabled' if STEALTH else 'disabled (not installed)'}")
    print("=" * 55)

    urls = await extract_with_retry()

    if not urls:
        if OUTPUT_FILE.exists():
            print(f"\n⚠️  Extraction failed — keeping existing {OUTPUT_FILE} unchanged.")
            return 1
        else:
            print(f"\n❌  Extraction failed and no existing {OUTPUT_FILE} found.")
            return 1

    stream_url = urls[0]
    OUTPUT_FILE.write_text(build_m3u(stream_url, ts), encoding="utf-8")

    print(f"\n✅  {OUTPUT_FILE} written successfully.")
    print(f"   Stream : {stream_url}")
    print(f"   Updated: {ts}")
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
    
