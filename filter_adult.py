import os

FILE = "output.m3u"

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

def is_adult(text: str) -> bool:
    text = text.lower()
    return any(keyword in text for keyword in ADULT_KEYWORDS)

def filter_m3u(lines):
    cleaned = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Only process EXTINF blocks
        if line.startswith("#EXTINF"):
            if is_adult(line):
                # skip this EXTINF + next URL line
                i += 2
                continue
            else:
                cleaned.append(lines[i])

                # keep next line if it's URL
                if i + 1 < len(lines):
                    cleaned.append(lines[i + 1])
                i += 2
                continue

        # keep everything else (headers like #EXTM3U)
        cleaned.append(lines[i])
        i += 1

    return cleaned

def main():
    if not os.path.exists(FILE):
        print("❌ output.m3u not found")
        return

    with open(FILE, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    new_data = filter_m3u(lines)

    if new_data == lines:
        print("✅ No adult channels found. No changes made.")
        return

    with open(FILE, "w", encoding="utf-8") as f:
        f.writelines(new_data)

    print("✅ Adult channels removed successfully and file updated.")

if __name__ == "__main__":
    main()
