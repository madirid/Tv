"""
IPTV Stream Extractor
======================
Extracts HLS (.m3u8) stream URLs from a source webpage and writes
a valid M3U playlist to playlist/fifa_tv.m3u.

Configuration
--------------
Set these as GitHub Actions Variables (repo → Settings → Variables):
  TARGET_URL     – The webpage containing the stream  (required)
  STREAM_REFERER – Referer header to send              (optional, defaults to TARGET_URL origin)

Or create a local .env file for testing:
  TARGET_URL=https://example-stream-site.com/channel/fifa-tv
"""

import os
import re
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

# List of channels to extract. Each entry is a dict with:
#   name        – display name in the M3U
#   url         – page URL to scrape
#   logo        – logo URL (optional)
#   group       – EPG group-title (optional)
#   referer     – override Referer header for this channel (optional)
#
# You can also control TARGET_URL via env var for a single-channel setup.

CHANNELS = [
    {
        "name": "FIFA TV",
        "url": os.getenv("TARGET_URL", "https://www.totalsportek.com/live-stream/fifa-tv-live/"),
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/43/FIFA_logo_without_slogan.svg/1200px-FIFA_logo_without_slogan.svg.png",
        "group": "Sports",
    },
    # ── Add more channels here ──────────────────────────────────────────────
    # {
    #     "name": "beIN Sports 1",
    #     "url": "https://example.com/bein-sports-1",
    #     "logo": "https://example.com/bein.png",
    #     "group": "Sports",
    # },
]

OUTPUT_PATH = Path(__file__).parent.parent / "playlist" / "fifa_tv.m3u"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Regex patterns for HLS stream URLs ───────────────────────────────────────
M3U8_PATTERNS = [
    # Direct .m3u8 references
    r'https?://[^\s\'"<>]+\.m3u8(?:[^\s\'"<>]*)?',
    # src="..." or file:"..." patterns
    r'(?:file|src|source)["\']?\s*[=:]\s*["\']?(https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*)',
    # JSON-style "url":"..." patterns
    r'"url"\s*:\s*"(https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*)"',
    # jwplayer / videojs setup blocks
    r'(?:jwplayer|videojs)[^{]*\{[^}]*["\']?(?:file|src)["\']?\s*[=:]\s*["\']?(https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*)',
]

# ── Session ───────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


# ── Extraction strategies ─────────────────────────────────────────────────────

def strategy_regex(html: str, base_url: str) -> list[str]:
    """Scan raw HTML with regex patterns."""
    found = []
    for pattern in M3U8_PATTERNS:
        matches = re.findall(pattern, html, re.IGNORECASE)
        found.extend(matches)
    return list(dict.fromkeys(found))  # deduplicate, preserve order


def strategy_bs4_sources(html: str, base_url: str) -> list[str]:
    """Look inside <video>, <source>, <iframe>, and script tags via BeautifulSoup."""
    soup = BeautifulSoup(html, "lxml")
    found = []

    for tag in soup.find_all(["source", "video", "iframe"]):
        for attr in ("src", "data-src", "data-stream", "data-url"):
            val = tag.get(attr, "")
            if val and ".m3u8" in val:
                found.append(urljoin(base_url, val))

    # Also scan <script> tag bodies
    for script in soup.find_all("script"):
        if script.string and ".m3u8" in script.string:
            found.extend(strategy_regex(script.string, base_url))

    return list(dict.fromkeys(found))


def strategy_json_blobs(html: str, base_url: str) -> list[str]:
    """Try to parse embedded JSON blobs (jwplayer setup, Next.js __NEXT_DATA__, etc.)."""
    found = []

    # __NEXT_DATA__
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if match:
        try:
            data = json.loads(match.group(1))
            text = json.dumps(data)
            found.extend(re.findall(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*', text))
        except json.JSONDecodeError:
            pass

    # Generic JSON assignment: var playerConfig = {...}
    for blob in re.findall(r'(?:playerConfig|setupData|jwConfig)\s*=\s*(\{.*?\})\s*;', html, re.S):
        try:
            data = json.loads(blob)
            text = json.dumps(data)
            found.extend(re.findall(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*', text))
        except json.JSONDecodeError:
            pass

    return list(dict.fromkeys(found))


def strategy_iframe_follow(html: str, base_url: str, session: requests.Session) -> list[str]:
    """Follow <iframe> embeds one level deep and re-scan."""
    soup = BeautifulSoup(html, "lxml")
    found = []
    for iframe in soup.find_all("iframe", src=True):
        iframe_url = urljoin(base_url, iframe["src"])
        if not iframe_url.startswith("http"):
            continue
        log.info("  ↪ Following iframe: %s", iframe_url)
        try:
            r = session.get(iframe_url, timeout=15, headers={"Referer": base_url})
            r.raise_for_status()
            found.extend(strategy_regex(r.text, iframe_url))
            found.extend(strategy_bs4_sources(r.text, iframe_url))
        except Exception as exc:
            log.warning("  iframe fetch failed: %s", exc)
    return list(dict.fromkeys(found))


# ── Main extraction orchestrator ──────────────────────────────────────────────

def extract_streams(channel: dict, session: requests.Session) -> list[str]:
    url = channel["url"]
    referer = channel.get("referer") or os.getenv("STREAM_REFERER") or url
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    log.info("🔍 Fetching: %s", url)
    session.headers.update({"Referer": referer, "Origin": origin})

    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Failed to fetch %s — %s", url, exc)
        return []

    html = resp.text
    streams: list[str] = []

    # Run strategies in order; stop as soon as we find something
    for strategy in [strategy_regex, strategy_bs4_sources, strategy_json_blobs]:
        results = strategy(html, url)
        streams.extend(results)
        if results:
            log.info("  ✅ Strategy '%s' found %d URL(s)", strategy.__name__, len(results))

    # Follow iframes only if nothing found yet
    if not streams:
        log.info("  🔗 Trying iframe follow strategy…")
        streams.extend(strategy_iframe_follow(html, url, session))

    # Deduplicate
    streams = list(dict.fromkeys(streams))

    if streams:
        log.info("  🎯 %d stream(s) found for '%s'", len(streams), channel["name"])
        for s in streams:
            log.info("     → %s", s)
    else:
        log.warning("  ⚠️  No streams found for '%s'", channel["name"])

    return streams


# ── M3U writer ────────────────────────────────────────────────────────────────

def build_m3u(entries: list[dict]) -> str:
    """
    entries: list of dicts with keys: name, url, logo, group, stream_url
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "#EXTM3U",
        f"# Auto-updated: {now}",
        f"# Total channels: {len(entries)}",
        "",
    ]

    for entry in entries:
        stream_url = entry.get("stream_url", "")
        if not stream_url:
            continue

        logo  = entry.get("logo", "")
        group = entry.get("group", "IPTV")
        name  = entry.get("name", "Channel")

        logo_part  = f' tvg-logo="{logo}"'  if logo  else ""
        group_part = f' group-title="{group}"' if group else ""

        lines.append(f'#EXTINF:-1{logo_part}{group_part},{name}')
        lines.append(stream_url)
        lines.append("")

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    session = make_session()
    entries = []

    for channel in CHANNELS:
        streams = extract_streams(channel, session)
        if streams:
            # Use the first (most likely) stream URL
            entries.append({**channel, "stream_url": streams[0]})
        else:
            log.warning("Skipping '%s' — no stream URL extracted.", channel["name"])

    if not entries:
        log.error("❌ No streams extracted. Playlist will not be updated.")
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    playlist = build_m3u(entries)
    OUTPUT_PATH.write_text(playlist, encoding="utf-8")

    log.info("✅ Playlist written → %s  (%d channel(s))", OUTPUT_PATH, len(entries))


if __name__ == "__main__":
    main()
