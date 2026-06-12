#!/usr/bin/env python3
"""
extract_cazetv.py  (v4 – Brazilian proxy auto-fetch)
-----------------------------------------------------
YouTube geo-locked to Brazil:
  • geo_bypass headers alone DON'T work (YouTube checks real IP)
  • This script fetches free Brazilian proxies, tests them,
    and passes the first working one to yt-dlp --proxy

Priority order:
  1. PROXY_URL env variable (user-supplied, most reliable)
  2. Auto-fetched free Brazilian proxies (fallback, free but may be slow)
  3. No proxy (last resort, will likely fail for Brazil-locked content)
"""

import os
import sys
import asyncio
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    os.system("pip install requests -q")
    import requests

try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp not installed.  Run: pip install yt-dlp")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────────
STREAM_URL  = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=CRtjePKnGvA"
OUTPUT_FILE = Path("Cazetv.m3u")
MAX_RETRIES = 3
CHANNEL_LOGO = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/"
    "0/0f/Caze_TV_logo.svg/1200px-Caze_TV_logo.svg.png"
)

# ── M3U builder ────────────────────────────────────────────────────────────────
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

# ── Proxy helpers ──────────────────────────────────────────────────────────────
def fetch_brazil_proxies() -> list[str]:
    """Fetch free HTTP/HTTPS proxies from Brazil via public APIs."""
    proxies = []

    apis = [
        # ProxyScrape – returns JSON list
        (
            "https://api.proxyscrape.com/v4/free-proxies/json?"
            "request=displayproxies&protocol=http&timeout=5000&country=BR&anonymity=all",
            "proxyscrape",
        ),
        # GeoNode – REST API
        (
            "https://proxylist.geonode.com/api/proxy-list?"
            "limit=50&page=1&sort_by=lastChecked&sort_type=desc"
            "&country=BR&protocols=http%2Chttps",
            "geonode",
        ),
    ]

    for url, source in apis:
        try:
            print(f"  Fetching proxies from {source}…")
            r = requests.get(url, timeout=10)
            data = r.json()

            if source == "proxyscrape":
                # {"proxies": [{"proxy": "ip:port", ...}, ...]}
                for item in data.get("proxies", []):
                    p = item.get("proxy") or f"{item.get('ip')}:{item.get('port')}"
                    if p and ":" in p:
                        proxies.append(f"http://{p}")

            elif source == "geonode":
                # {"data": [{"ip": "...", "port": "...", ...}, ...]}
                for item in data.get("data", []):
                    ip   = item.get("ip", "")
                    port = item.get("port", "")
                    if ip and port:
                        proxies.append(f"http://{ip}:{port}")

        except Exception as exc:
            print(f"  [warn] {source}: {exc}")

    print(f"  Found {len(proxies)} Brazilian proxy candidates.")
    return proxies


def test_proxy(proxy: str, timeout: int = 5) -> bool:
    """Quick TCP connect test — filters obviously dead proxies."""
    try:
        # Parse host:port
        host_port = proxy.replace("http://", "").replace("https://", "")
        host, port_str = host_port.rsplit(":", 1)
        port = int(port_str)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def get_working_proxy() -> str | None:
    """
    Return the best available Brazilian proxy.
    Priority: env variable PROXY_URL  >  auto-fetched list
    """
    # 1. User-supplied proxy via GitHub secret / env var
    env_proxy = os.environ.get("PROXY_URL", "").strip()
    if env_proxy:
        print(f"  Using PROXY_URL from env: {env_proxy}")
        return env_proxy

    # 2. Auto-fetch free proxies
    candidates = fetch_brazil_proxies()
    print(f"  Testing proxies (TCP connect)…")

    working = []
    for p in candidates[:30]:           # test up to 30 to keep it fast
        if test_proxy(p, timeout=4):
            working.append(p)
            if len(working) >= 5:       # stop early once we have 5 candidates
                break

    print(f"  {len(working)} proxies passed TCP test.")
    return working[0] if working else None

# ── YouTube extractor ──────────────────────────────────────────────────────────
def extract_youtube(url: str, proxy: str | None = None) -> str | None:
    """Extract live stream URL via yt-dlp, optionally through a proxy."""

    label = f"proxy={proxy}" if proxy else "no proxy"
    print(f"  Extracting via yt-dlp ({label})…")

    ydl_opts = {
        "geo_bypass":         True,
        "geo_bypass_country": "BR",
        "format": (
            "best[protocol=m3u8_native]"
            "/best[protocol=m3u8]"
            "/bestvideo[protocol=m3u8_native]+bestaudio"
            "/best"
        ),
        "skip_download": True,
        "quiet":         False,
        "no_warnings":   False,
        "noplaylist":    True,
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "web"],
            }
        },
    }

    if proxy:
        ydl_opts["proxy"] = proxy

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None

            # Prefer a proper m3u8 format entry
            for fmt in info.get("formats", []):
                if "m3u8" in fmt.get("protocol", "") and fmt.get("url"):
                    print(f"  ✅ m3u8 format found (protocol={fmt['protocol']})")
                    return fmt["url"]

            # Fallback to top-level URL
            direct = info.get("url")
            if direct:
                print("  ✅ direct URL found")
            return direct

    except Exception as exc:
        print(f"  [error] {exc}")
        return None


# ── Playwright fallback for non-YouTube sites ──────────────────────────────────
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
                print(f"  [req ] {u}"); found.append(u)

        async def on_resp(resp):
            u, ct = resp.url, resp.headers.get("content-type", "")
            if (".m3u8" in u or "mpegurl" in ct.lower()) and u not in found:
                print(f"  [resp] {u}"); found.append(u)

        page.on("request", on_req)
        page.on("response", on_resp)

        try:
            await page.goto(url, wait_until="load", timeout=30_000)
        except Exception as exc:
            print(f"  [warn] {exc}")

        await asyncio.sleep(12)

        html = await page.content()
        for u in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html):
            if u not in found:
                found.append(u)

        await browser.close()

    return found[0] if found else None


# ── Dispatcher ─────────────────────────────────────────────────────────────────
def is_youtube(url: str) -> bool:
    return any(h in url for h in ("youtube.com", "youtu.be", "m.youtube.com"))


async def extract(url: str) -> str | None:
    if is_youtube(url):
        print("  → YouTube URL detected — using yt-dlp")

        # Step 1: try WITHOUT proxy (sometimes works)
        result = extract_youtube(url, proxy=None)
        if result:
            return result

        # Step 2: get a Brazilian proxy and retry
        print("\n  Direct attempt failed — fetching Brazilian proxy…")
        proxy = get_working_proxy()

        if proxy:
            result = extract_youtube(url, proxy=proxy)
            if result:
                return result
            # Try next proxies if first failed yt-dlp auth
            candidates = fetch_brazil_proxies()
            for p in candidates[1:10]:
                if test_proxy(p):
                    result = extract_youtube(url, proxy=p)
                    if result:
                        return result
        else:
            print("  ⚠️  No working Brazilian proxy found.")
            print("  Tip: Add a PROXY_URL secret in your repo settings.")

        return None
    else:
        print("  → Non-YouTube URL — using Playwright")
        return await extract_playwright(url)


# ── Entry point ────────────────────────────────────────────────────────────────
async def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 55)
    print("  Stream Extractor  (v4 – BR proxy)")
    print(f"  URL    : {STREAM_URL}")
    print(f"  Run    : {ts}")
    print("=" * 55)

    stream_url = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n── Attempt {attempt}/{MAX_RETRIES} " + "─" * 30)
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
  
