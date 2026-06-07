import os

INPUT_FILE = "output.m3u"
OUTPUT_FILE = "output.m3u"

ADULT_KEYWORDS = [
    # Direct terms
    "adult", "xxx", "porn", "sex", "erotic", "erotik", "erotique",
    # Sites / brands
    "dorcel", "penthouse", "playboy", "hustler", "brazzers",
    "bangbros", "mofos", "wankz", "vixen", "blacked",
    "realitykings", "reality kings", "naughtyamerica", "naughty america",
    "xvideos", "xhamster", "pornhub", "onlyfans", "boyxx", "sextreme",
    # Content descriptors
    "hentai", "nude", "naked", "milf", "fetish",
    "hardcore", "softcore", "explicit", "uncensored",
    "redlight", "red light", "taboo", "18+",
    # Group titles commonly used
    "for adults", "adults only", "hot movies", "private",
]

def is_adult(text: str) -> bool:
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in ADULT_KEYWORDS)

def filter_m3u(lines):
    filtered = []
    skip_next = False

    for i, line in enumerate(lines):
        line = line.strip()

        # Skip URL line if previous EXTINF was adult
        if skip_next:
            skip_next = False
            if line.startswith("http"):
                continue

        if line.startswith("#EXTINF"):
            if is_adult(line):
                skip_next = True
                continue

        # also block direct URL if needed (rare case)
        if is_adult(line):
            continue

        filtered.append(lines[i])

    return filtered

def main():
    if not os.path.exists(INPUT_FILE):
        print("output.m3u not found!")
        return

    with open(INPUT_FILE, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    new_lines = filter_m3u(lines)

    if lines == new_lines:
        print("No changes needed. File already clean.")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print("Adult channels removed and output.m3u updated.")

if __name__ == "__main__":
    main()
