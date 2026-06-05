import requests
from config import BD_KEYWORDS, INDIA_KEYWORDS, CARTOON_KEYWORDS
from logos import LOGO_DB, DEFAULT_LOGO
from utils import ffmpeg_check, clean_name


def get_logo(name):
    low = name.lower()
    for k, v in LOGO_DB.items():
        if k in low:
            return v
    return DEFAULT_LOGO


def detect(name):
    low = name.lower()

    if any(x.lower() in low for x in BD_KEYWORDS):
        return "bd"
    if any(x.lower() in low for x in CARTOON_KEYWORDS):
        return "cartoon"
    if any(x.lower() in low for x in INDIA_KEYWORDS):
        return "india"
    return "other"


def parse_m3u(text):
    lines = text.splitlines()
    out = []

    for i in range(len(lines)-1):
        if lines[i].startswith("#EXTINF"):
            name = lines[i]
            url = lines[i+1].strip()

            if url.startswith("http") and ffmpeg_check(url):
                out.append((name, url))

    return out


def main():
    with open("sources.txt") as f:
        sources = [x.strip() for x in f if x.strip()]

    bd, ct, ind, oth = [], [], [], []

    total = 0

    for s in sources:
        try:
            data = requests.get(s, timeout=20).text
        except:
            continue

        channels = parse_m3u(data)
        total += len(channels)

        for name, url in channels:
            cat = detect(name)
            logo = get_logo(name)
            short = clean_name(name)

            item = (short, url, logo)

            if cat == "bd":
                bd.append(item)
            elif cat == "cartoon":
                ct.append(item)
            elif cat == "india":
                ind.append(item)
            else:
                oth.append(item)

    final = bd + ct + ind + oth

    with open("output.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for name, url, logo in final:
            f.write(f'#EXTINF:-1 tvg-logo="{logo}",{name}\n')
            f.write(f"{url}\n")

    with open("stats.txt", "w") as f:
        f.write(f"Total: {total}\n")
        f.write(f"Final: {len(final)}\n")
        f.write(f"BD: {len(bd)} | Cartoon: {len(ct)} | India: {len(ind)} | Other: {len(oth)}\n")

    print("PRO DONE")


if __name__ == "__main__":
    main()
