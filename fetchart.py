"""fetchart - find and download game art from the command line.

Usage:
    fetchart <game> [platform] [-t box|clean|logo|icon|ach] [-r portrait|landscape] [-q QUALITY] [-o FOLDER] [-y]
    fetchart --update            re-download the game database

Art types:
    box     box cover art, rating and all            (LaunchBox box fronts, every region)
    clean   key art without the logo                 (LaunchBox fanart backgrounds, Steam library hero)
    logo    key art with the logo                    (Steam capsules, LaunchBox banners/posters, or
                                                      fanart with the game's clear logo laid on top)
    icon    dashboard icon / tile                    (Xbox 360 marketplace tiles + banners, Microsoft Store
                                                      squares, Nintendo eShop icons, Steam + LaunchBox icons)
    ach     every Xbox 360 achievement icon          (the Xbox Live image server, by title ID)

Movies, TV and music (use the platform word movie, tv or album):
    fetchart inception movie                         posters from IMDb and Wikipedia
    fetchart breaking-bad tv
    fetchart "pink floyd dark side of the moon" album   covers from Deezer, Apple Music, Cover Art Archive

The LaunchBox Games Database (gamesdb.launchbox-app.com) covers ~190k games on every platform.
Its public metadata dump is downloaded once and indexed into a local SQLite file.
"""

import argparse
import difflib
import io
import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import xml.etree.ElementTree as ET

__version__ = "1.0.0"

UA = {"User-Agent": f"fetchart/{__version__} (+https://github.com/SamuelArther/fetchart)"}
LB_DUMP = "https://gamesdb.launchbox-app.com/Metadata.zip"
LB_IMAGES = "https://images.launchbox-app.com/"
STEAM_CDN = "https://shared.akamai.steamstatic.com/store_item_assets/"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "fetchart"
DB_PATH = DATA_DIR / "launchbox.db"
CONFIG = Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "fetchart" / "config.json"
DB_MAX_AGE = 30 * 86400

KEEP_TYPES = {"Box - Front", "Box - Front - Reconstructed", "Fanart - Box - Front",
              "Fanart - Background", "Clear Logo", "Banner", "Poster", "Square", "Icon"}
DB_SCHEMA = 2  # bump when KEEP_TYPES or the table layout changes, to force a rebuild
NEEDED_TYPES = {
    "box": ("Box - Front", "Box - Front - Reconstructed", "Fanart - Box - Front"),
    "clean": ("Fanart - Background",),
    "logo": ("Fanart - Background", "Banner", "Poster"),
    "icon": (),  # icons mostly come from console stores, so any game qualifies
    "ach": (),
}
ART_TYPES = {
    "box": "Box cover art (the retail cover, rating and all)",
    "clean": "Key art, no logo",
    "logo": "Key art, with logo",
    "icon": "Dashboard icon / tile (the square on the console menu)",
    "ach": "Xbox 360 achievement icons (all of them)",
}
REGION_ORDER = ["North America", "United States", "World", None, "Canada", "Europe", "United Kingdom",
                "Australia", "Oceania", "Japan"]

# Short platform names -> LaunchBox platform names. Anything else is fuzzy-matched.
PLATFORMS = {
    "pc": "Windows", "windows": "Windows", "win": "Windows", "steam": "Windows", "dos": "MS-DOS",
    "mac": "Apple Mac OS", "linux": "Linux", "ios": "Apple iOS", "android": "Android",
    "xbox": "Microsoft Xbox", "ogxbox": "Microsoft Xbox",
    "xbox360": "Microsoft Xbox 360", "x360": "Microsoft Xbox 360", "360": "Microsoft Xbox 360",
    "xboxone": "Microsoft Xbox One", "xb1": "Microsoft Xbox One", "xone": "Microsoft Xbox One",
    "xboxseries": "Microsoft Xbox Series X/S", "xsx": "Microsoft Xbox Series X/S", "series": "Microsoft Xbox Series X/S",
    "ps1": "Sony Playstation", "psx": "Sony Playstation", "playstation": "Sony Playstation",
    "ps2": "Sony Playstation 2", "ps3": "Sony Playstation 3", "ps4": "Sony Playstation 4",
    "ps5": "Sony Playstation 5", "psp": "Sony PSP", "vita": "Sony Playstation Vita", "psvita": "Sony Playstation Vita",
    "nes": "Nintendo Entertainment System", "famicom": "Nintendo Entertainment System",
    "fds": "Nintendo Famicom Disk System",
    "snes": "Super Nintendo Entertainment System", "sfc": "Super Nintendo Entertainment System",
    "n64": "Nintendo 64", "64dd": "Nintendo 64DD",
    "gc": "Nintendo GameCube", "gcn": "Nintendo GameCube", "ngc": "Nintendo GameCube", "gamecube": "Nintendo GameCube",
    "wii": "Nintendo Wii", "wiiu": "Nintendo Wii U",
    "switch": "Nintendo Switch", "ns": "Nintendo Switch", "nsw": "Nintendo Switch",
    "switch2": "Nintendo Switch 2", "ns2": "Nintendo Switch 2",
    "gb": "Nintendo Game Boy", "gameboy": "Nintendo Game Boy",
    "gbc": "Nintendo Game Boy Color", "gba": "Nintendo Game Boy Advance",
    "ds": "Nintendo DS", "nds": "Nintendo DS", "3ds": "Nintendo 3DS",
    "vb": "Nintendo Virtual Boy", "virtualboy": "Nintendo Virtual Boy",
    "genesis": "Sega Genesis", "megadrive": "Sega Genesis", "md": "Sega Genesis",
    "sms": "Sega Master System", "mastersystem": "Sega Master System",
    "gg": "Sega Game Gear", "gamegear": "Sega Game Gear", "32x": "Sega 32X",
    "segacd": "Sega CD", "megacd": "Sega CD", "saturn": "Sega Saturn", "dc": "Sega Dreamcast", "dreamcast": "Sega Dreamcast",
    "tg16": "NEC TurboGrafx-16", "pce": "NEC TurboGrafx-16", "neogeo": "SNK Neo Geo AES",
    "ngp": "SNK Neo Geo Pocket", "ngpc": "SNK Neo Geo Pocket Color",
    "2600": "Atari 2600", "atari2600": "Atari 2600", "5200": "Atari 5200", "7800": "Atari 7800",
    "jaguar": "Atari Jaguar", "lynx": "Atari Lynx", "3do": "3DO Interactive Multiplayer", "arcade": "Arcade",
}


# ---------------------------------------------------------------- helpers

def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(url, params):
    return json.loads(http_get(url + "?" + urllib.parse.urlencode(params)))


def norm(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text.lower())
    text = text.replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def score(query, name):
    q, n = norm(query), norm(name)
    if not n:
        return 0.0
    ratio = difflib.SequenceMatcher(None, q, n).ratio()
    overlap = len(set(q.split()) & set(n.split())) / max(len(q.split()), 1)
    bonus = 0.3 if n == q else 0.15 if f" {q} " in f" {n} " else 0.0
    return ratio * 0.5 + overlap * 0.5 + bonus


def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .") or "art"


def ask(prompt, default=None):
    try:
        answer = input(prompt).strip().lstrip("﻿")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return answer or default


def choose(title, options, default=1, auto=False, allow_all=False):
    """Numbered menu over display strings. Returns the picked index, or -1 for 'all'."""
    if len(options) == 1 or auto:
        return default - 1
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    if allow_all:
        print("  a) all of them")
    hint = f"1-{len(options)}" + (", a" if allow_all else "")
    while True:
        answer = ask(f"Pick [{hint}, Enter = {default}]: ", str(default))
        if allow_all and answer.lower() in ("a", "all"):
            return -1
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("  Not an option, try again.")


def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


def pil():
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        sys.exit("This needs Pillow:  python -m pip install pillow")
    return Image, ImageFilter


# ---------------------------------------------------------------- LaunchBox database

def build_index():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DATA_DIR / "Metadata.zip"
    print("Downloading the LaunchBox game database (~110 MB, only needed once a month)...")
    req = urllib.request.Request(LB_DUMP, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(zip_path, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total}%", end="", flush=True)
    print("\nIndexing...")

    tmp = DB_PATH.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT, platform TEXT, votes INTEGER);
        CREATE TABLE names (id INTEGER, name TEXT, norm TEXT);
        CREATE TABLE images (id INTEGER, type TEXT, region TEXT, file TEXT);
    """)
    games, names, images = [], [], []

    def flush():
        db.executemany("INSERT OR IGNORE INTO games VALUES (?,?,?,?)", games)
        db.executemany("INSERT INTO names VALUES (?,?,?)", names)
        db.executemany("INSERT INTO images VALUES (?,?,?,?)", images)
        games.clear(), names.clear(), images.clear()

    with zipfile.ZipFile(zip_path) as z, z.open("Metadata.xml") as xml:
        for _, el in ET.iterparse(xml):
            tag = el.tag
            if tag == "Game":
                gid, name = el.findtext("DatabaseID"), el.findtext("Name") or ""
                if gid and name:
                    games.append((int(gid), name, el.findtext("Platform") or "",
                                  int(el.findtext("CommunityRatingCount") or 0)))
                    names.append((int(gid), name, norm(name)))
            elif tag == "GameAlternateName":
                gid, name = el.findtext("DatabaseID"), el.findtext("AlternateName") or ""
                if gid and name:
                    names.append((int(gid), name, norm(name)))
            elif tag == "GameImage":
                kind = el.findtext("Type")
                if kind in KEEP_TYPES and el.findtext("DatabaseID"):
                    images.append((int(el.findtext("DatabaseID")), kind, el.findtext("Region"), el.findtext("FileName")))
            else:
                continue
            el.clear()
            if len(images) + len(names) > 50000:
                flush()
    flush()
    db.executescript("""
        CREATE INDEX names_norm ON names(norm);
        CREATE INDEX images_id ON images(id);
        CREATE INDEX games_platform ON games(platform);
    """)
    db.execute(f"PRAGMA user_version = {DB_SCHEMA}")
    db.commit()
    db.close()
    os.replace(tmp, DB_PATH)
    zip_path.unlink(missing_ok=True)
    print("Database ready.\n")


def open_index(force_update=False):
    stale = not DB_PATH.exists() or time.time() - DB_PATH.stat().st_mtime > DB_MAX_AGE
    if not stale:
        db = sqlite3.connect(DB_PATH)
        stale = db.execute("PRAGMA user_version").fetchone()[0] != DB_SCHEMA
        db.close()
    if force_update or stale:
        try:
            build_index()
        except (urllib.error.URLError, OSError, zipfile.BadZipFile, ET.ParseError) as e:
            if not DB_PATH.exists():
                sys.exit(f"Couldn't build the game database: {e}")
            print(f"Couldn't refresh the game database ({e}), using the old one.")
    return sqlite3.connect(DB_PATH)


def resolve_platform(db, text):
    if not text or text.lower() in ("all", "any"):
        return None
    key = re.sub(r"[^a-z0-9]", "", text.lower())
    if key in PLATFORMS:
        return PLATFORMS[key]
    platforms = [row[0] for row in db.execute("SELECT DISTINCT platform FROM games")]
    ranked = sorted(platforms, key=lambda p: -score(text, p))
    if ranked and score(text, ranked[0]) > 0.45:
        return ranked[0]
    sys.exit(f"Don't know the platform '{text}'. Try: pc, xbox, x360, ps2, n64, gc, wii, switch, gba, snes...")


def search_games(db, query, platform, kind):
    """Best-matching games that actually have art of the wanted kind: [(id, name, platform)]."""
    words = sorted(norm(query).split(), key=len, reverse=True)
    if not words:
        return []
    needed = NEEDED_TYPES[kind]
    has_art = (f"AND EXISTS (SELECT 1 FROM images i WHERE i.id = g.id AND i.type IN ({','.join('?' * len(needed))}))"
               if needed else "")
    sql = f"""
        SELECT g.id, g.name, g.platform, g.votes, n.name FROM names n JOIN games g ON g.id = n.id
        WHERE n.norm LIKE ? {"AND g.platform = ?" if platform else ""}
        {has_art}
    """
    params = [f"%{words[0]}%"] + ([platform] if platform else []) + list(needed)
    best = {}
    for gid, name, plat, votes, alias in db.execute(sql, params):
        # Well-known releases outrank hacks and homebrew that share the name.
        s = score(query, alias) + min(0.2, math.log10(votes + 1) * 0.07)
        if s > best.get(gid, (0,))[0]:
            best[gid] = (s, name, plat)
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    return [(gid, name, plat) for gid, (s, name, plat) in ranked[:12] if s > 0.35]


def game_images(db, gid):
    rows = db.execute("SELECT type, region, file FROM images WHERE id = ?", (gid,)).fetchall()
    return [(t, r, LB_IMAGES + urllib.parse.quote(f)) for t, r, f in rows]


def region_rank(region):
    return REGION_ORDER.index(region) if region in REGION_ORDER else len(REGION_ORDER)


# ---------------------------------------------------------------- Steam (bonus official key art)

def steam_assets(title):
    """Steam store assets for a game whose name matches exactly, else {}."""
    try:
        items = get_json("https://store.steampowered.com/api/storesearch/",
                         {"term": title, "cc": "us", "l": "en"}).get("items", [])
        match = next((it for it in items if norm(it["name"]) == norm(title)), None)
        if not match:
            return {}
        payload = {"ids": [{"appid": match["id"]}], "context": {"language": "english", "country_code": "US"},
                   "data_request": {"include_assets": True}}
        items = get_json("https://api.steampowered.com/IStoreBrowseService/GetItems/v1",
                         {"input_json": json.dumps(payload)})["response"].get("store_items", [])
        return items[0].get("assets", {}) if items else {}
    except (urllib.error.URLError, KeyError, ValueError):
        return {}


def steam_icon(assets):
    m = re.search(r"apps/(\d+)/", assets.get("asset_url_format", ""))
    if m and assets.get("community_icon"):
        return (f"https://cdn.fastly.steamstatic.com/steamcommunity/public/images/apps/"
                f"{m.group(1)}/{assets['community_icon']}.jpg")
    return None


def steam_urls(assets, keys):
    fmt, seen, out = assets.get("asset_url_format", ""), set(), []
    for key in keys:
        name = assets.get(key)
        if name and name not in seen:
            seen.add(name)
            out.append(STEAM_CDN + fmt.replace("${FILENAME}", name))
    return out


# ---------------------------------------------------------------- console store icons

def best_match(title, items, name_key, threshold=0.8):
    ranked = sorted(items, key=lambda it: -score(title, it[name_key]))
    return ranked[0] if ranked and score(title, ranked[0][name_key]) >= threshold else None


def xbox360_title(title):
    """Look the game up on XboxUnity for its Xbox 360 title ID. Returns the Unity item or None."""
    # XboxUnity shortens names ("COD: Black Ops II", "Modern Warfare" for Call of Duty 4), so drop
    # leading words until a result matches either the full title or the shortened search.
    words = title.replace(":", " ").split()
    try:
        while words:
            query = " ".join(words)
            items = get_json("http://xboxunity.net/Resources/Lib/TitleList.php",
                             {"page": 0, "count": 10, "search": query, "sort": 3, "direction": 1,
                              "category": 0, "filter": 0}).get("Items", [])
            ranked = sorted(((max(score(title, it["Name"]), score(query, it["Name"]) - 0.1), it)
                             for it in items if it.get("TitleType") != "Xbox1"), key=lambda r: -r[0])
            if ranked and ranked[0][0] >= 0.8:
                return ranked[0][1]
            words = words[1:] if len(words) > 2 else []
    except (urllib.error.URLError, ValueError):
        pass
    return None


def xbox360_icons(title):
    """Xbox 360 dashboard art by title ID: [Art]."""
    item = xbox360_title(title)
    if not item:
        return []
    tid = item["TitleID"]
    base = f"http://download.xbox.com/content/images/66acd000-77fe-1000-9115-d802{tid.lower()}/1033/"
    out = [Art(base + "banner.png", "360 dashboard banner"), Art(base + "boxartlg.jpg", "360 dashboard box art")]
    # The game icon shown in the guide / mini dash is the title image from the game's achievement data.
    # Xbox Live serves it directly; the marketplace and XboxUnity keep copies as fallbacks
    # (XboxUnity answers unknown title IDs with a generic icon, so that one gets compared first).
    unity = f"http://xboxunity.net/Resources/Lib/Icon.php?tid={tid}"
    for url in (f"http://image.xboxlive.com/global/t.{tid.lower()}/icon/0/8000", base + "tile.png", unity):
        data = fetch_or_none(url)
        if data and not (url == unity and data == fetch_or_none(unity.replace(tid, "00000000"))):
            out += [Art(url, "360 game icon (guide / mini dash)"),
                    Art(url, "360 game icon (guide / mini dash), upscaled", scale=256)]
            break
    return out


def fetch_or_none(url):
    try:
        return http_get(url)
    except urllib.error.URLError:
        return None


def download_achievements(title, plat, args, cfg):
    """Save every achievement icon of an Xbox 360 game into '<folder>/<title> achievements'."""
    item = xbox360_title(title)
    if not item:
        sys.exit(f"Couldn't find the Xbox 360 title ID for {title}.")
    base = f"http://image.xboxlive.com/global/t.{item['TitleID'].lower()}/"
    print(f"Finding achievement icons for {title} (title ID {item['TitleID']})...")
    # Achievement image IDs are small hex numbers counting up from 1; scan in blocks until one comes back empty.
    icons, start = {}, 1
    with ThreadPoolExecutor(16) as pool:
        while start < 0x1000:
            ids = range(start, start + 64)
            hits = {i: d for i, d in zip(ids, pool.map(lambda i: fetch_or_none(f"{base}ach/0/{i:x}"), ids)) if d}
            if not hits:
                break
            icons.update(hits)
            start += 64
    if not icons:
        sys.exit(f"Xbox Live has no achievement icons for {title}.")
    print(f"Found {len(icons)} achievement icons.")

    folder = pick_folder(args.out, cfg, args.yes) / safe_filename(f"{title} [{plat}] achievements")
    folder.mkdir(parents=True, exist_ok=True)
    game_icon = fetch_or_none(f"{base}icon/0/8000")
    if game_icon:
        (folder / ("game icon" + image_ext(game_icon))).write_bytes(game_icon)
    width = len(str(len(icons)))
    for n, (_, data) in enumerate(sorted(icons.items()), 1):
        (folder / (f"achievement {n:0{width}d}" + image_ext(data))).write_bytes(data)
    print(f"Saved {len(icons)} icons to {folder}")


def ms_store_icons(title, back_compat):
    """Square art from the Microsoft Store catalog: [(url, label)].

    Xbox 360 and original Xbox games are sold on modern Xbox as back-compat listings whose tile
    carries the green console stripe; those listings have an Xbox360BackwardCompatibil package.
    """
    try:
        sets = get_json("https://www.microsoft.com/msstoreapiprod/api/autosuggest",
                        {"market": "en-us", "clientId": "7F27B536-CF6B-4C65-8638-A0F8CBDFCA65",
                         "sources": "DCatAll-Products", "filter": "+ClientType:StoreWeb", "counts": 10,
                         "query": title}).get("ResultSets", [])
        found = []
        for suggest in (s for rs in sets for s in rs.get("Suggests", [])):
            meta = {m["Key"]: m["Value"] for m in suggest.get("Metas", [])}
            if meta.get("ProductType") == "Games" and meta.get("BigCatalogId"):
                found.append({"id": meta["BigCatalogId"], "name": suggest.get("Title", "")})
        best = max((score(title, f["name"]) for f in found), default=0)
        ids = [f["id"] for f in found if best >= 0.8 and score(title, f["name"]) >= best - 0.05][:5]
        if not ids:
            return []
        products = get_json("https://displaycatalog.mp.microsoft.com/v7.0/products",
                            {"bigIds": ",".join(ids), "market": "US", "languages": "en-US"})["Products"]
    except (urllib.error.URLError, ValueError, KeyError):
        return []

    def is_bc(product):
        return product.get("Properties", {}).get("PackageFamilyName", "").startswith("Xbox360BackwardCompatib")

    wanted = [pr for pr in products if is_bc(pr) == back_compat]
    labels = {"BoxArt": "Xbox dashboard tile", "FeaturePromotionalSquareArt": "Xbox promo square",
              "Logo": "Microsoft Store icon"}
    out = []
    for product in wanted:
        suffix = " (back-compat)" if is_bc(product) else ""
        for image in product["LocalizedProperties"][0]["Images"]:
            if image["ImagePurpose"] in labels:
                out.append(("https:" + image["Uri"], labels[image["ImagePurpose"]] + suffix))
    return out


def nintendo_icons(title, platform):
    """Square eShop icon from Nintendo's public game search: [(url, label)]."""
    try:
        docs = get_json("https://searching.nintendo-europe.com/en/select",
                        {"q": title, "fq": "type:GAME", "rows": 20, "wt": "json",
                         "fl": "title,system_names_txt,image_url_sq_s"})["response"]["docs"]
    except (urllib.error.URLError, ValueError, KeyError):
        return []
    system = platform.replace("Nintendo ", "")
    docs = [d for d in docs if d.get("image_url_sq_s")]
    same_system = [d for d in docs if system in d.get("system_names_txt", [])]
    doc = best_match(title, same_system or docs, "title")
    if not doc:
        return []
    systems = ", ".join(doc.get("system_names_txt", []))
    return [(doc["image_url_sq_s"], f"eShop icon ({systems})" if systems else "eShop icon")]


# ---------------------------------------------------------------- candidates

class Art:
    """One downloadable option. crop = cut to 2:3 portrait; logo = clear-logo URL to lay on top;
    scale = resize a small square icon up to this many pixels."""

    def __init__(self, url, source, crop=False, logo=None, scale=None):
        self.url, self.source, self.crop, self.logo, self.scale = url, source, crop, logo, scale
        self.size = None
        self.fingerprint = None

    def final_size(self):
        if not self.size:
            return None
        if self.scale:
            return self.scale, self.scale
        w, h = self.size
        return (min(w, round(h * 2 / 3)), h) if self.crop else (w, h)

    def label(self):
        s = self.final_size()
        return (f"{s[0]}x{s[1]}" if s else "size ?").ljust(11) + self.source


def probe_size(art):
    Image, _ = pil()
    for span in (65535, 524287):
        try:
            head = http_get(art.url, {"Range": f"bytes=0-{span}"})
            art.size = Image.open(io.BytesIO(head)).size
            art.fingerprint = hash(head)
            return
        except (urllib.error.URLError, OSError):
            continue


def gather(db, gid, title, platform, kind, orient):
    imgs = game_images(db, gid)
    by_type = {}
    for t, region, url in sorted(imgs, key=lambda i: region_rank(i[1])):
        by_type.setdefault(t, []).append((region, url))
    fanart = [url for _, url in by_type.get("Fanart - Background", [])]
    logos = [url for _, url in by_type.get("Clear Logo", [])]
    portrait = orient == "portrait"
    out = []

    if kind == "icon":
        found = []
        if platform == "Microsoft Xbox 360":
            found += xbox360_icons(title)
        if platform in ("Microsoft Xbox", "Microsoft Xbox 360"):
            found += ms_store_icons(title, back_compat=True)
        if platform in ("Microsoft Xbox One", "Microsoft Xbox Series X/S", "Windows"):
            found += ms_store_icons(title, back_compat=False)
        if platform.startswith("Nintendo"):
            found += nintendo_icons(title, platform)
        if platform == "Windows":
            icon = steam_icon(steam_assets(title))
            if icon:
                found.append((icon, "Steam library icon"))
        found += [(url, "square art") for _, url in by_type.get("Square", [])]
        found += [(url, "icon") for _, url in by_type.get("Icon", [])]
        return [f if isinstance(f, Art) else Art(*f) for f in found]

    if kind == "box":
        for region, url in by_type.get("Box - Front", []):
            out.append(Art(url, f"box scan, {region or 'unknown region'}"))
        for region, url in by_type.get("Box - Front - Reconstructed", []):
            out.append(Art(url, f"rebuilt box, {region or 'unknown region'}"))
        for _, url in by_type.get("Fanart - Box - Front", []):
            out.append(Art(url, "fan-made box"))
        return out

    steam = steam_assets(title)
    if kind == "clean":
        for url in steam_urls(steam, ["library_hero_2x", "library_hero"]):
            out.append(Art(url, "Steam hero (official)" + (", cropped" if portrait else ""), crop=portrait))
        for i, url in enumerate(fanart, 1):
            out.append(Art(url, f"fanart #{i}" + (", cropped" if portrait else ""), crop=portrait))
        return out

    # kind == "logo"
    if portrait:
        for url in steam_urls(steam, ["library_capsule_2x", "library_capsule"]):
            out.append(Art(url, "Steam cover (official)"))
        for _, url in by_type.get("Poster", []):
            out.append(Art(url, "poster"))
    else:
        for url in steam_urls(steam, ["header_2x", "header", "main_capsule"]):
            out.append(Art(url, "Steam header (official)"))
        for _, url in by_type.get("Banner", []):
            out.append(Art(url, "banner"))
    if logos:
        for i, url in enumerate(fanart, 1):
            out.append(Art(url, f"fanart #{i} + logo" + (", cropped" if portrait else ""),
                           crop=portrait, logo=logos[0]))
    return out


def dedupe(options):
    """Stores often list the same picture under several names; keep the first copy."""
    seen, out = set(), []
    for o in options:
        key = (o.size, o.fingerprint, o.crop, o.logo, o.scale)
        if o.fingerprint is None or key not in seen:
            seen.add(key)
            out.append(o)
    return out


def image_ext(data):
    """Pick the file extension from the image bytes; many store URLs don't have one."""
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def render(art):
    """Download one option and apply its crop / logo overlay. Returns (bytes, extension)."""
    data = http_get(art.url, timeout=90)
    ext = image_ext(data)
    if not art.crop and not art.logo and not art.scale:
        return data, ext
    Image, ImageFilter = pil()
    if art.scale:
        buf = io.BytesIO()
        Image.open(io.BytesIO(data)).convert("RGBA").resize((art.scale, art.scale), Image.LANCZOS).save(buf, "PNG")
        return buf.getvalue(), ".png"
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if art.crop:
        cw = min(w, round(h * 2 / 3))
        left = (w - cw) // 2
        img = img.crop((left, 0, left + cw, h))
        w = cw
    if art.logo:
        logo = Image.open(io.BytesIO(http_get(art.logo))).convert("RGBA")
        scale = min(w * (0.8 if art.crop else 0.45) / logo.width, h * 0.3 / logo.height)
        logo = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.LANCZOS)
        x, y = (w - logo.width) // 2, round(h * 0.93) - logo.height
        shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
        shadow.putalpha(logo.getchannel("A").point(lambda a: a * 0.6))
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, logo.height // 25)))
        img.paste(shadow, (x + 3, y + 3), shadow)
        img.paste(logo, (x, y), logo)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue(), ".jpg"


# ---------------------------------------------------------------- movies, TV and albums

MEDIA = {"movie": "movie", "movies": "movie", "film": "movie", "tv": "tv", "show": "tv", "tvshow": "tv",
         "series": "tv", "album": "album", "music": "album", "cd": "album", "song": "album"}
TV_TYPES = {"tvSeries", "tvMiniSeries"}


def wiki_infobox_image(search, title):
    """Image from the infobox of the best-matching Wikipedia article, or None."""
    api = "https://en.wikipedia.org/w/api.php"
    try:
        hits = get_json(api, {"action": "query", "format": "json", "list": "search",
                              "srsearch": search, "srlimit": 5})["query"]["search"]
        for hit in sorted(hits, key=lambda h: -score(title, h["title"]))[:2]:
            text = get_json(api, {"action": "parse", "format": "json", "page": hit["title"],
                                  "prop": "wikitext", "section": 0, "redirects": 1})
            wikitext = text.get("parse", {}).get("wikitext", {}).get("*", "")
            m = re.search(r"\|\s*(?:image|cover)\s*=\s*(?:\[\[)?(?:File:|Image:)?([^\n|\]]+\.(?:jpe?g|png|webp))",
                          wikitext, re.I)
            if not m:
                continue
            pages = get_json(api, {"action": "query", "format": "json", "titles": "File:" + m.group(1).strip(),
                                   "prop": "imageinfo", "iiprop": "url"})["query"]["pages"]
            info = next(iter(pages.values())).get("imageinfo")
            if info:
                return info[0]["url"]
    except (urllib.error.URLError, ValueError, KeyError):
        pass
    return None


def imdb_search(query):
    """IMDb's public search suggestions: [{l: title, y: year, q: kind, qid, i: {imageUrl}}]."""
    q = urllib.parse.quote(query.lower().strip())
    try:
        return get_json(f"https://v2.sg.media-imdb.com/suggestion/{q[0]}/{q}.json", {}).get("d", [])
    except (urllib.error.URLError, ValueError):
        return []


def itunes_album_art(artist, album):
    try:
        results = get_json("https://itunes.apple.com/search", {"term": f"{artist} {album}", "entity": "album",
                                                               "country": "US", "limit": 25}).get("results", [])
    except (urllib.error.URLError, ValueError):
        return None
    for r in results:
        if norm(r.get("artistName", "")) == norm(artist) and score(album, r.get("collectionName", "")) >= 1.0:
            # Asking for 3000x3000 makes Apple's image server hand back the biggest copy it has.
            return r["artworkUrl100"].rsplit("/", 1)[0] + "/3000x3000bb.jpg"
    return None


def cover_art_archive(artist, album):
    """Front cover scan from the Cover Art Archive (via a MusicBrainz release-group search)."""
    params = {"query": f'releasegroup:"{album}" AND artist:"{artist}"', "fmt": "json", "limit": 5}
    groups = None
    for attempt in range(3):  # MusicBrainz allows about one request per second and answers 503 past that
        try:
            groups = get_json("https://musicbrainz.org/ws/2/release-group/", params).get("release-groups", [])
            break
        except (urllib.error.URLError, ValueError):
            time.sleep(1.5)
    if not groups:
        return None
    group = best_match(album, groups, "title", threshold=1.0)
    return f"https://coverartarchive.org/release-group/{group['id']}/front" if group else None


def media_main(args, media, cfg):
    query = re.sub(r"[-_]+", " ", args.game).strip()
    if media == "album":
        print(f'Searching albums for "{query}"...')
        try:
            albums = get_json("https://api.deezer.com/search/album", {"q": query, "limit": 12}).get("data", [])
        except (urllib.error.URLError, ValueError) as e:
            sys.exit(f"Network error: {e}")
        if not albums:
            sys.exit(f'No album found for "{query}". Try "artist album".')
        album = albums[choose("Which album?", [f"{a['title']}  -  {a['artist']['name']}" for a in albums],
                              auto=args.yes)]
        title, artist = album["title"], album["artist"]["name"]
        print(f"Finding covers for {artist} - {title}...")
        # Deezer serves covers up to 1200px when asked for more; the probe reports the real size.
        options = [Art(album["cover_xl"].replace("1000x1000", "1800x1800"), "Deezer")]
        plain = re.sub(r"\s*[(\[][^)\]]*[)\]]", "", title).strip() or title  # drop "(Remastered 2009)"
        for url, source in ((itunes_album_art(artist, plain), "Apple Music"),
                            (cover_art_archive(artist, plain), "Cover Art Archive scan")):
            if url:
                options.append(Art(url, source))
        return finish(options, f"{artist} - {title} - album cover", args, cfg, require_size=True)

    print(f'Searching IMDb for "{query}"...')
    items = [d for d in imdb_search(query) if d.get("l") and d.get("qid")]
    wanted = [d for d in items if (d["qid"] in TV_TYPES) == (media == "tv")] or items
    if not wanted:
        sys.exit(f'Nothing on IMDb for "{query}".')
    item = wanted[choose("Which one?", [f"{d['l']}  ({d.get('y', '?')})  [{d.get('q', d['qid'])}]" for d in wanted],
                         auto=args.yes)]
    title = f"{item['l']} ({item['y']})" if item.get("y") else item["l"]
    print(f"Finding posters for {title}...")
    options = []
    if item.get("i", {}).get("imageUrl"):
        options.append(Art(item["i"]["imageUrl"], "IMDb poster"))
    wiki = wiki_infobox_image(f"{item['l']} {item.get('y', '')} {'TV series' if media == 'tv' else 'film'}", item["l"])
    if wiki:
        options.append(Art(wiki, "Wikipedia poster"))
    finish(options, f"{title} - poster", args, cfg, require_size=True)


# ---------------------------------------------------------------- main

def pick_art(options, wanted, auto):
    """options are sorted best-first. Returns the list of picked options."""
    if wanted:
        w = wanted.lower()
        if w == "all":
            return options
        if w in ("low", "min", "smallest"):
            return [options[-1]]
        if w in ("medium", "med", "mid"):
            return [options[len(options) // 2]]
        if w in ("high", "max", "best", "original"):
            return [options[0]]
        for opt in options:
            if opt.label().startswith(w):
                return [opt]
        print(f"  No '{wanted}' option here, using the best one.")
        return [options[0]]
    if len(options) == 1:
        print(f"\nOnly one option: {options[0].label()}")
        return options
    i = choose("Which one? (biggest first)", [o.label() for o in options], auto=auto, allow_all=True)
    return options if i == -1 else [options[i]]


def pick_folder(cli_folder, cfg, auto):
    default = cfg.get("last_folder") or str(Path.home() / "Pictures" / "fetchart")
    folder = cli_folder or (default if auto else ask(f"\nSave to which folder? [Enter = {default}]: ", default))
    folder = Path(os.path.expandvars(os.path.expanduser(folder.strip().strip('"').strip("'"))))
    folder.mkdir(parents=True, exist_ok=True)
    cfg["last_folder"] = str(folder.resolve())
    save_config(cfg)
    return folder


def unique_path(path):
    n, candidate = 2, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def main():
    p = argparse.ArgumentParser(prog="fetchart", description="Find and download game, movie and album art.")
    p.add_argument("game", nargs="?", help='name, e.g. halo-combat-evolved or "the dark side of the moon"')
    p.add_argument("platform", nargs="?",
                   help="pc, xbox, x360, ps2, n64, gc, wii, switch, gba, ... (default: all), or movie, tv, album")
    p.add_argument("-t", "--type", choices=list(ART_TYPES), help="box, clean (no logo), logo, icon, or ach (360 achievements)")
    p.add_argument("-r", "--orient", choices=["portrait", "landscape"], help="key art orientation")
    p.add_argument("-q", "--quality", help="low, medium, high, all, or an exact size like 1920x1080")
    p.add_argument("-o", "--out", help="folder to save into")
    p.add_argument("-y", "--yes", action="store_true", help="take the top match and defaults, no prompts")
    p.add_argument("--update", action="store_true", help="re-download the game database")
    p.add_argument("--version", action="version", version=f"fetchart {__version__}")
    args = p.parse_args()
    if not args.game and not args.update:
        # Launched with no arguments (e.g. double-clicked): ask instead of erroring out.
        args.game = ask("Game, movie or album name: ")
        if not args.game:
            p.error("which game?")
        args.platform = args.platform or ask(
            "Platform (pc, xbox, x360, ps2, switch, ... or movie, tv, album. Enter = any game): ")

    media = MEDIA.get(re.sub(r"[^a-z]", "", (args.platform or "").lower()))
    if media:
        return media_main(args, media, load_config())

    db = open_index(args.update)
    if not args.game:
        return
    query = re.sub(r"[-_]+", " ", args.game).strip()
    platform = resolve_platform(db, args.platform)
    cfg = load_config()

    kind = args.type or list(ART_TYPES)[choose("What art do you want?", list(ART_TYPES.values()), auto=args.yes)]
    orient = args.orient
    if kind in ("clean", "logo") and not orient:
        orient = ["landscape", "portrait"][choose("Portrait or landscape?", ["Landscape", "Portrait"], auto=args.yes)]

    if kind == "ach":
        if platform not in (None, "Microsoft Xbox 360"):
            sys.exit("Achievement icons are only for Xbox 360 games (platform x360).")
        platform = "Microsoft Xbox 360"
    games = search_games(db, query, platform, kind)
    if not games:
        where = f" on {platform}" if platform else ""
        sys.exit(f"No {ART_TYPES[kind].lower()} found for \"{query}\"{where}. Try a shorter or different name.")
    gid, title, plat = games[choose("Which game?", [f"{n}  [{pl}]" for _, n, pl in games], auto=args.yes)]

    if kind == "ach":
        return download_achievements(title, plat, args, cfg)
    print(f"Finding art for {title} [{plat}]...")
    options = gather(db, gid, title, plat, kind, orient)
    if not options:
        sys.exit(f"{title} has no {ART_TYPES[kind].lower()}" + (f" in {orient}" if orient else "") + ".")
    tag = {"box": "box art", "clean": "key art", "logo": "key art (logo)", "icon": "icon"}[kind]
    # Store lookups guess URLs, so for icons drop the ones that didn't resolve to a real image.
    finish(options, f"{title} [{plat}] - {tag}", args, cfg, require_size=kind == "icon")


def finish(options, name_prefix, args, cfg, require_size=False):
    """Measure the options, let the user pick, and save the picks."""
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(probe_size, options))
    options = dedupe(options)
    if require_size:
        options = [o for o in options if o.size]
    if not options:
        sys.exit("Couldn't find any art for that.")
    options.sort(key=lambda o: -(o.final_size()[0] * o.final_size()[1] if o.final_size() else 0))

    picked = pick_art(options, args.quality, args.yes)
    folder = pick_folder(args.out, cfg, args.yes)
    print()
    for art in picked:
        size = art.final_size()
        name = name_prefix + (f" {size[0]}x{size[1]}" if size else "") + f" - {art.source}"
        try:
            data, ext = render(art)
        except (urllib.error.URLError, OSError) as e:
            print(f"  failed {art.label()}: {e}")
            continue
        dest = unique_path(folder / (safe_filename(name) + ext))
        dest.write_bytes(data)
        print(f"Saved {dest}  ({len(data) // 1024} KB)")


def run():
    """Entry point for the standalone build: keep the window open when it was double-clicked."""
    double_clicked = getattr(sys, "frozen", False) and len(sys.argv) == 1
    try:
        main()
    finally:
        if double_clicked:
            try:
                input("\nPress Enter to close...")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    run()
