import os

SOURCE_FILE = "output.m3u"
OUTPUT_FILE = "ridoyiptv.m3u"

ADULT_KEYWORDS = [
    "adult", "xxx", "porn", "sex", "erotic", "erotik", "erotique",
    "dorcel", "penthouse", "playboy", "hustler", "brazzers",
    "bangbros", "mofos", "wankz", "vixen", "blacked",
    "realitykings", "reality kings", "naughtyamerica", "naughty america",
    "xvideos", "xhamster", "pornhub", "onlyfans", "boyxx", "sextreme",
    "hentai", "nude", "naked", "milf", "fetish",
    "hardcore", "softcore", "explicit", "uncensored",
    "redlight", "red light", "taboo", "18+",
    "for adults", "adults only", "hot movies", "private"
]

def is_adult(text):
    text = text.lower()
    return any(keyword in text for keyword in ADULT_KEYWORDS)

def main():
    if not os.path.exists(SOURCE_FILE):
        print(f"{SOURCE_FILE} not found")
        return

    with open(SOURCE_FILE, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    cleaned = []
    removed = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith("#EXTINF"):
            block_text = line.lower()

            if i + 1 < len(lines):
                block_text += lines[i + 1].lower()

            if is_adult(block_text):
                removed += 1
                i += 2
                continue

            cleaned.append(line)

            if i + 1 < len(lines):
                cleaned.append(lines[i + 1])

            i += 2
            continue

        cleaned.append(line)
        i += 1

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.writelines(cleaned)

    print(f"Removed channels: {removed}")
    print(f"Saved cleaned playlist to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
