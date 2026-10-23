"""fetchart - find and download game art, posters, album covers and paintings from the command line.

Usage:
    fetchart                     menus for everything: category, name, platform, art, folder
    fetchart <game> [platform] [-t box|clean|logo|icon|ach] [-r portrait|landscape] [-q QUALITY] [-o FOLDER] [-y]
    fetchart --folder DIR [platform] [-t TYPE] [-o FOLDER] [-y]   art for every ROM in a folder
    fetchart --update            re-download the game database

Art types:
    box     box cover art, rating and all            (LaunchBox box fronts, every region)
    clean   key art without the logo                 (LaunchBox fanart backgrounds, Steam library hero)
    logo    key art with the logo                    (Steam capsules, LaunchBox banners/posters, or
                                                      fanart with the game's clear logo laid on top)
    icon    dashboard icon / tile                    (Xbox 360 marketplace tiles + banners, Microsoft Store
                                                      squares, Nintendo eShop icons, Steam + LaunchBox icons)
    ach     Xbox 360 achievement icons, by name      (the Xbox Live image server, by title ID; names from Exophase)

Short names work: mw2, botw, gta5, ff7, "mw2 remastered". Add a year for remakes: "mw2 2009".

Movies, TV, music and paintings (use the platform word movie, tv, album or painting):
    fetchart inception movie                         posters from IMDb and Wikipedia
    fetchart breaking-bad tv
    fetchart "pink floyd dark side of the moon" album   covers from Deezer, Apple Music, Cover Art Archive
    fetchart "the starry night" painting             museum scans from Wikimedia Commons

The LaunchBox Games Database (gamesdb.launchbox-app.com) covers ~190k games on every platform.
Its public metadata dump is downloaded once and indexed into a local SQLite file.
"""

import argparse
import base64
import difflib
import html
import io
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import xml.etree.ElementTree as ET

__version__ = "1.3.0"

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
DB_SCHEMA = 4  # bump when KEEP_TYPES or the table layout changes, to force a rebuild
NEEDED_TYPES = {
    "box": ("Box - Front", "Box - Front - Reconstructed", "Fanart - Box - Front"),
    "clean": ("Fanart - Background",),
    "logo": ("Fanart - Background", "Banner", "Poster"),
    "icon": (),  # icons mostly come from console stores, so any game qualifies
    "ach": (),
    None: (),  # art type not picked yet
}
ART_TYPES = {
    "box": "Box cover art (the retail cover, rating and all)",
    "clean": "Key art, no logo",
    "logo": "Key art, with logo",
    "icon": "Dashboard icon / tile (the square on the console menu)",
    "ach": "Xbox 360 achievement icons (all of them)",
}
XBOX360 = "Microsoft Xbox 360"
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

class NotFound(Exception):
    """A search came up empty. The menus ask for another name; the command line exits."""


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


ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
         "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16"}


def acronyms(name):
    """Short forms people type for a title, for the whole name and each part after a colon:
    "Call of Duty: Modern Warfare 2" -> "codmw2 cod mw2", "Final Fantasy VII" -> "ffvii ff7".
    Numbers stay whole; Roman numerals are kept both ways."""
    keep = lambda w: w if any(c.isdigit() for c in w) else None
    out = []
    for part in [name] + re.split(r":| - ", name):
        words = norm(part).split()
        if not words:
            continue
        for numeral in (lambda w: w, lambda w: ROMAN[w]):
            a = "".join(keep(w) or (numeral(w) if w in ROMAN and i else w[0]) for i, w in enumerate(words))
            if a not in out:
                out.append(a)
    return " ".join(out)


STOPWORDS = {"the", "of", "and", "a", "an", "to", "in", "on"}


def acronym_score(tokens, alias_norm, abbr):
    """How well a typed short form matches a title's acronyms ("mw2" in "codmw2"): whole, its start or
    end, or inside it. Mixed searches like "mw2 remastered" match when every word is either part of the
    title or part of its acronym."""
    short = "".join(tokens)
    best = 0.0
    for a in abbr.split():
        if a == short:  # the whole title, or a whole part of it ("mw2" for "...: Modern Warfare 2")
            return 1.0
        if a.startswith(short):
            best = max(best, 0.95)
        elif short in a:  # somewhere in the middle ("mw2" inside "smw2"), a weaker hint
            best = max(best, 0.75)
    if not best and len(tokens) > 1:
        words = alias_norm.split()
        in_title = [t in words for t in tokens]
        if any(in_title) and all(hit or any(t in a for a in abbr.split()) for t, hit in zip(tokens, in_title)):
            best = 0.9
    return best


def split_year(query):
    """Pull a release year off the end of a search: "mw2 2009" -> ("mw2", 2009)."""
    m = re.fullmatch(r"(.+?)\s+((?:19[5-9]|20[0-4])\d)", query.strip())
    return (m[1], int(m[2])) if m else (query, None)


def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .") or "art"


def ask(prompt, default=None):
    try:
        answer = input(prompt).strip().lstrip("﻿")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return answer or default


# ---------------------------------------------------------------- menus

def arrow_keys_ok():
    """Arrow-key menus need a real console on both ends; piped input gets the numbered prompts."""
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def enable_ansi():
    """Let the classic Windows console understand the escape codes the menus redraw with."""
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle, mode = kernel.GetStdHandle(-11), ctypes.c_uint32()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


class WinConsole:
    """Raw Windows console input, so menus get mouse clicks and the scroll wheel as well as keys."""
    VK = {0x26: "up", 0x28: "down", 0x21: "pgup", 0x22: "pgdn", 0x24: "home", 0x23: "end",
          0x0D: "enter", 0x1B: "esc", 0x20: "space"}

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w

        class KEY(ctypes.Structure):
            _fields_ = [("down", w.BOOL), ("repeat", w.WORD), ("vk", w.WORD), ("scan", w.WORD),
                        ("char", w.WCHAR), ("state", w.DWORD)]

        class MOUSE(ctypes.Structure):
            _fields_ = [("x", w.SHORT), ("y", w.SHORT), ("buttons", w.DWORD), ("state", w.DWORD), ("flags", w.DWORD)]

        class EVENT(ctypes.Union):
            _fields_ = [("key", KEY), ("mouse", MOUSE), ("pad", w.BYTE * 16)]

        class RECORD(ctypes.Structure):
            _fields_ = [("type", w.WORD), ("event", EVENT)]

        class INFO(ctypes.Structure):
            _fields_ = [("size", w.SHORT * 2), ("cursor", w.SHORT * 2), ("attr", w.WORD),
                        ("window", w.SHORT * 4), ("max", w.SHORT * 2)]

        self.ctypes, self.w, self.RECORD, self.INFO = ctypes, w, RECORD, INFO
        self.kernel = ctypes.windll.kernel32
        self.stdin, self.stdout = self.kernel.GetStdHandle(-10), self.kernel.GetStdHandle(-11)
        self.saved = w.DWORD()
        self.kernel.GetConsoleMode(self.stdin, ctypes.byref(self.saved))

    def __enter__(self):
        # Mouse input on, quick-edit (which swallows clicks) and Ctrl+C-as-signal off while the menu is up.
        mode = (self.saved.value | 0x0010 | 0x0080) & ~0x0040 & ~0x0001 & ~0x0200
        self.kernel.SetConsoleMode(self.stdin, mode)
        return self

    def __exit__(self, *_):
        self.kernel.SetConsoleMode(self.stdin, self.saved)

    def cursor_row(self):
        info = self.INFO()
        self.kernel.GetConsoleScreenBufferInfo(self.stdout, self.ctypes.byref(info))
        return info.cursor[1]

    def read(self):
        """Like read_key, plus ("click", row) and wheelup / wheeldown."""
        rec, count = self.RECORD(), self.w.DWORD()
        while True:
            self.kernel.ReadConsoleInputW(self.stdin, self.ctypes.byref(rec), 1, self.ctypes.byref(count))
            if rec.type == 1 and rec.event.key.down:  # KEY_EVENT
                key = rec.event.key
                if key.vk in self.VK:
                    return self.VK[key.vk]
                if key.char == "\x03":
                    return "ctrl-c"
                if key.char and key.char != "\0":
                    return key.char
            elif rec.type == 2:  # MOUSE_EVENT
                m = rec.event.mouse
                if m.flags == 4:  # MOUSE_WHEELED: the high word of the button state is the direction
                    return "wheelup" if m.buttons & 0x80000000 == 0 else "wheeldown"
                if m.flags in (0, 2) and m.buttons & 1:  # left button pressed, or double-clicked
                    return ("click", m.y)


class PosixKeys:
    """Keyboard input for the menus on macOS and Linux."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def cursor_row(self):
        return None  # no mouse support here, so the menu doesn't need to know where it is

    def read(self):
        import select
        import termios
        import tty
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = os.read(fd, 1).decode(errors="ignore")
            if ch == "\x1b" and select.select([fd], [], [], 0.05)[0]:
                return {"[A": "up", "[B": "down", "[5~": "pgup", "[6~": "pgdn", "[H": "home", "[F": "end",
                        "OH": "home", "OF": "end"}.get(os.read(fd, 8).decode(errors="ignore"), "")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        return {"\r": "enter", "\n": "enter", " ": "space", "\x1b": "esc", "\x03": "ctrl-c"}.get(ch, ch)


def menu_input():
    return WinConsole() if os.name == "nt" else PosixKeys()


def arrow_menu(title, rows, default=0, multi=False, preview=None):
    """Menu driven by the arrow keys or the mouse (typing a number jumps to it). Returns the picked row
    index, or with multi=True the ticked indexes (just the highlighted row if none are ticked)."""
    enable_ansi()
    n, cols = len(rows), shutil.get_terminal_size().columns
    height = max(3, min(n, shutil.get_terminal_size().lines - 5))
    width = len(str(n))
    cur, top, ticked, typed, note, drawn = default, 0, set(), "", "", 0
    print()
    sys.stdout.write("\x1b[?25l")  # hide the cursor while the menu is up
    try:
        with menu_input() as keyboard:
            clicks = keyboard.cursor_row() is not None
            keys = ("Up/Down" + (" or click" if clicks else "") + ", Enter "
                    + ("done, Space tick, A all" if multi else "pick")
                    + (", P preview in browser" if preview else "") + ", Esc quit")
            while True:
                top = min(max(top, cur - height + 1), cur)
                out = [(title + (f"  ({cur + 1} of {n})" if n > height else ""))[:cols - 1]]
                for i in range(top, top + height):
                    box = ("[x] " if i in ticked else "[ ] ") if multi else ""
                    text = f"{'>' if i == cur else ' '} {i + 1:>{width}}) {box}{rows[i]}"[:cols - 1]
                    out.append(f"\x1b[7m{text}\x1b[0m" if i == cur else text)
                out.append(f"\x1b[2m{(note or keys)[:cols - 1]}\x1b[0m")
                sys.stdout.write((f"\x1b[{drawn}F\x1b[J" if drawn else "") + "\n".join(out) + "\n")
                sys.stdout.flush()
                drawn, note = len(out), ""
                first_row = keyboard.cursor_row() - drawn + 1 if clicks else 0

                key = keyboard.read()
                if isinstance(key, tuple):  # ("click", screen row)
                    row = key[1] - first_row
                    if not 0 <= row < height:
                        continue
                    cur = top + row
                    if multi:
                        ticked ^= {cur}
                        continue
                    key = "enter"
                typed = typed + key if key.isdigit() else ""
                if key.isdigit():
                    if not 1 <= int(typed) <= n:
                        typed = key
                    if 1 <= int(typed) <= n:
                        cur = int(typed) - 1
                elif key in ("up", "down"):
                    cur = (cur + (1 if key == "down" else -1)) % n
                elif key in ("wheelup", "wheeldown"):
                    cur = max(0, cur - 3) if key == "wheelup" else min(n - 1, cur + 3)
                elif key in ("pgup", "pgdn"):
                    cur = max(0, cur - height) if key == "pgup" else min(n - 1, cur + height)
                elif key in ("home", "end"):
                    cur = 0 if key == "home" else n - 1
                elif key == "space" and multi:
                    ticked ^= {cur}
                elif key.lower() == "a" and multi:
                    ticked = set() if len(ticked) == n else set(range(n))
                elif key.lower() == "p" and preview:
                    preview()
                    note = "Preview opened in your browser."
                elif key in ("esc", "ctrl-c"):
                    sys.stdout.write("\x1b[?25h\n")
                    sys.exit(1)
                elif key == "enter":
                    picked = (sorted(ticked) or [cur]) if multi else cur
                    summary = rows[cur] if not multi or picked == [cur] else f"{len(picked)} picked"
                    sys.stdout.write(f"\x1b[{drawn}F\x1b[J" + f"{title} {summary}"[:cols - 1] + "\n")
                    return picked
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def choose(title, options, default=1, auto=False, allow_all=False, preview=None):
    """Menu over display strings. Returns the picked index, or -1 for 'all'.
    preview, if given, is called when the user asks to see the options."""
    if len(options) == 1 or auto:
        return default - 1
    if arrow_keys_ok():
        i = arrow_menu(title, options + (["All of them"] if allow_all else []), default - 1, preview=preview)
        return -1 if i == len(options) else i
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    if allow_all:
        print("  a) all of them")
    hint = f"1-{len(options)}" + (", a" if allow_all else "") + (", p = preview" if preview else "")
    while True:
        answer = ask(f"Pick [{hint}, Enter = {default}]: ", str(default))
        if preview and answer.lower() == "p":
            preview()
            print("  Preview opened in your browser.")
            continue
        if allow_all and answer.lower() in ("a", "all"):
            return -1
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("  Not an option, try again.")


def choose_many(title, options, preview=None):
    """Menu where several can be picked. Typed: '3', '1,4,9', '10-20' or 'a' for all. Returns indexes."""
    if arrow_keys_ok():
        return arrow_menu(title, options, multi=True, preview=preview)
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        answer = ask("Pick one or more (e.g. 3 or 1,4,9 or 10-20" + (", p = preview" if preview else "")
                     + ") [Enter = all]: ", "a").lower().replace(" ", "")
        if preview and answer == "p":
            preview()
            print("  Preview opened in your browser.")
            continue
        if answer in ("a", "all"):
            return list(range(len(options)))
        picked = []
        for part in answer.split(","):
            lo, _, hi = part.partition("-")
            if lo.isdigit() and (hi.isdigit() or not hi):
                picked += range(int(lo), int(hi or lo) + 1)
            else:
                picked = []
                break
        if picked and all(1 <= n <= len(options) for n in picked):
            return sorted({n - 1 for n in picked})
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
    # Museum scans run past a gigapixel, which trips Pillow's decompression-bomb guard even when only
    # reading the size. Every image here comes from a known art host.
    Image.MAX_IMAGE_PIXELS = None
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
        CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT, platform TEXT, votes INTEGER, year INTEGER);
        CREATE TABLE names (id INTEGER, name TEXT, norm TEXT, abbr TEXT);
        CREATE TABLE images (id INTEGER, type TEXT, region TEXT, file TEXT);
    """)
    games, names, images = [], [], []

    def flush():
        db.executemany("INSERT OR IGNORE INTO games VALUES (?,?,?,?,?)", games)
        db.executemany("INSERT INTO names VALUES (?,?,?,?)", names)
        db.executemany("INSERT INTO images VALUES (?,?,?,?)", images)
        games.clear(), names.clear(), images.clear()

    with zipfile.ZipFile(zip_path) as z, z.open("Metadata.xml") as xml:
        for _, el in ET.iterparse(xml):
            tag = el.tag
            if tag == "Game":
                gid, name = el.findtext("DatabaseID"), el.findtext("Name") or ""
                if gid and name:
                    year = el.findtext("ReleaseYear") or (el.findtext("ReleaseDate") or "")[:4]
                    games.append((int(gid), name, el.findtext("Platform") or "",
                                  int(el.findtext("CommunityRatingCount") or 0),
                                  int(year) if year.isdigit() else None))
                    names.append((int(gid), name, norm(name), acronyms(name)))
            elif tag == "GameAlternateName":
                gid, name = el.findtext("DatabaseID"), el.findtext("AlternateName") or ""
                if gid and name:
                    names.append((int(gid), name, norm(name), acronyms(name)))
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


def match_platform(db, text, threshold=0.45):
    """LaunchBox platform name for a short name ("n64") or a near-full one ("Nintendo 64"), or None."""
    key = re.sub(r"[^a-z0-9]", "", text.lower())
    if key in PLATFORMS:
        return PLATFORMS[key]
    platforms = [row[0] for row in db.execute("SELECT DISTINCT platform FROM games")]
    ranked = sorted(platforms, key=lambda p: -score(text, p))
    return ranked[0] if ranked and score(text, ranked[0]) > threshold else None


def resolve_platform(db, text):
    if not text or text.lower() in ("all", "any"):
        return None
    return match_platform(db, text) or sys.exit(
        f"Don't know the platform '{text}'. Try: pc, xbox, x360, ps2, n64, gc, wii, switch, gba, snes...")


def search_games(db, query, platform, kind):
    """Best-matching games that have art of the wanted kind (any game if kind is None),
    best first: [(id, name, platform, year, votes, match)]. match is how well the name itself matched
    (1.3 = exactly), before the popularity and year bonuses that decide the order."""
    core, wanted_year = split_year(query)
    words = sorted(norm(core).split(), key=len, reverse=True)
    if not words:
        return []
    tokens = norm(core).split()
    short = "".join(tokens)  # "cod mw2" -> "codmw2", checked against title acronyms
    # In a mixed search ("mw2 remastered") the short words are looked up in the acronyms on their own.
    abbr_parts = [short] + [t for t in tokens if len(tokens) > 1 and t not in STOPWORDS and len(t) >= 2
                            and (len(t) <= 4 or any(c.isdigit() for c in t))]
    needed = NEEDED_TYPES[kind]
    has_art = (f"AND EXISTS (SELECT 1 FROM images i WHERE i.id = g.id AND i.type IN ({','.join('?' * len(needed))}))"
               if needed else "")
    sql = f"""
        SELECT g.id, g.name, g.platform, g.year, g.votes, n.name, n.abbr FROM names n JOIN games g ON g.id = n.id
        WHERE (n.norm LIKE ? OR n.norm LIKE ? {"OR n.abbr LIKE ? " * len(abbr_parts)})
        {"AND g.platform = ?" if platform else ""}
        {has_art}
    """
    params = ([f"%{words[0]}%", f"%{norm(query)}%"] + [f"%{a}%" for a in abbr_parts]
              + ([platform] if platform else []) + list(needed))
    best = {}
    for gid, name, plat, year, votes, alias, abbr in db.execute(sql, params):
        # Well-known releases outrank hacks and homebrew that share the name, a game's own name
        # outranks a collection that lists it as an alternate name, and a typed year picks that release.
        # Short forms are almost always meant for famous games, so popularity counts for more there.
        text = max(score(query, alias), score(core, alias))
        short_form = acronym_score(tokens, norm(alias), abbr)
        fame = math.log10(votes + 1)
        s = (max(text + min(0.2, fame * 0.07), short_form + min(0.75, fame * 0.25))
             + (0.1 if alias == name else 0) + (0.3 if wanted_year and year == wanted_year else 0))
        if s > best.get(gid, (0,))[0]:
            best[gid] = (s, name, plat, year, votes, max(text, short_form))
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    return [(gid, *rest) for gid, (s, *rest) in ranked if s > 0.35]


def group_games(games, year=None, limit=12):
    """One entry per game name, holding every platform it came out on (the asked-for year,
    then the most popular, first)."""
    groups = {}
    for game in games:  # already best match first, so the dict keeps that order
        groups.setdefault(norm(game[1]), []).append(game)
    return [sorted(g, key=lambda game: (year is not None and game[3] != year, -game[4]))
            for g in list(groups.values())[:limit]]


def group_label(group):
    name = group[0][1]
    if len(group) == 1:
        return f"{name}  [{group[0][2]}]"
    return f"{name}  [{len(group)} platforms]"


def platform_label(game):
    return f"{game[2]} ({game[3]})" if game[3] else game[2]


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
    icons = [data for _, data in sorted(icons.items())]
    named = exophase_achievements(title)
    if not icons and not named:
        sys.exit(f"Xbox Live has no achievement icons for {title}.")

    # entries: (menu label, file name, image bytes or None to download from url, url)
    entries = []
    game_icon = fetch_or_none(f"{base}icon/0/8000")
    if game_icon:
        entries.append(("Game icon (guide / mini dash)", "game icon", game_icon, None))
    if named:
        print(f"Found {len(named)} achievements.")
        width = len(str(len(named)))
        for n, (ach, data) in enumerate(zip(named, match_icons(named, icons)), 1):
            entries.append((f"{ach['name']} ({ach['points']}G) - {ach['desc']}",
                            f"{n:0{width}d} {ach['name']}", data, ach["icon"]))
    else:
        print(f"Found {len(icons)} achievement icons (no names for this game, so they're numbered).")
        width = len(str(len(icons)))
        for n, data in enumerate(icons, 1):
            entries.append((f"Achievement icon {n}", f"achievement {n:0{width}d}", data, None))

    picked = entries if args.yes or (args.quality or "").lower() == "all" else \
        [entries[i] for i in choose_many("Which ones?", [e[0] for e in entries], preview=lambda: open_preview(
            f"{title} achievements", [(e[0], data_uri(e[2]) if e[2] else e[3], None) for e in entries], small=True))]
    folder = pick_folder(args.out, cfg, args.yes) / safe_filename(f"{title} [{plat}] achievements")
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for _, name, data, url in picked:
        data = data or fetch_or_none(url)
        if data:
            unique_path(folder / (safe_filename(name) + image_ext(data))).write_bytes(data)
            saved += 1
    print(f"Saved {saved} icon{'s' * (saved != 1)} to {folder}")


def exophase_achievements(title):
    """Names of an Xbox 360 game's achievements, in the game's order, from Exophase:
    [{name, desc, points, icon}], or [] when it isn't listed."""
    # Exophase drops edition words that XboxUnity keeps ("Minecraft: Xbox 360 Edition" is just "Minecraft").
    short = re.sub(r"[\s:\-]*\b(for |on )?(xbox 360|x360)( edition)?\s*$", "", title, flags=re.I).strip()
    try:
        for query in dict.fromkeys((title, short)):
            found = get_json("https://api.exophase.com/public/archive/games", {"q": query}).get("games")
            games = [g for g in (found or {}).get("list", [])
                     if any(p["slug"] == "xbox-360" for p in g.get("platforms", []))]
            # Japanese releases are listed separately; prefer the main one when the names tie.
            games.sort(key=lambda g: "-jp-" in g.get("endpoint_awards", ""))
            game = best_match(query, games, "title")
            if game:
                break
        else:
            return []
        page = http_get(game["endpoint_awards"]).decode("utf-8", "replace")
    except (urllib.error.URLError, ValueError, KeyError, TypeError, AttributeError):
        return []
    out = []
    for block in page.split("<li ")[1:]:
        icon = re.search(r'class="award-image[^"]*" src="([^"?]+)', block)
        name = re.search(r'award-title.*?<a [^>]*>(.*?)</a>', block, re.S)
        if not (icon and name):
            continue
        desc = re.search(r'award-description[^>]*>\s*<p>(.*?)</p>', block, re.S)
        points = re.search(r'award-points.*?<span>(\d+)</span>', block, re.S)
        out.append({"name": html.unescape(name[1]).strip(), "icon": icon[1],
                    "desc": html.unescape(re.sub(r"<[^>]+>", "", desc[1])).strip() if desc else "",
                    "points": points[1] if points else "?"})
    return out


def match_icons(named, icons):
    """Pair each named achievement with its original Xbox Live icon by comparing pixels.
    Exophase keeps exact copies, so a match is an identical image; unmatched ones get None."""
    Image, _ = pil()

    def pixels(data):
        try:
            return Image.open(io.BytesIO(data)).convert("RGBA").tobytes()
        except OSError:
            return None

    with ThreadPoolExecutor(16) as pool:
        theirs = list(pool.map(lambda a: fetch_or_none(a["icon"]), named))
    ours = {}
    for data in icons:
        ours.setdefault(pixels(data), data)
    return [ours.get(pixels(data)) if data else None for data in theirs]


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

    def __init__(self, url, source, crop=False, logo=None, scale=None, preview=None):
        self.url, self.source, self.crop, self.logo, self.scale = url, source, crop, logo, scale
        self.preview = preview  # a smaller copy to show in the browser preview, when the original is huge
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
        return (f"{s[0]}x{s[1]}" if s else "size ?").ljust(12) + self.source


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
         "series": "tv", "album": "album", "music": "album", "cd": "album", "song": "album",
         "painting": "painting", "paintings": "painting", "art": "painting", "artwork": "painting"}
# Top menu when fetchart is run with no arguments: key -> (menu text, name prompt)
CATEGORIES = {
    "game": ("Video game", "Game name: "),
    "screen": ("Movie or TV show", "Movie or show name: "),
    "album": ("Music (album cover)", "Album (artist + album name works best): "),
    "painting": ("Painting / real art", "Painting name (add the artist if you know it): "),
    "folder": ("A whole folder of games (art for every ROM)", "Folder with your games (paste its path): "),
}
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


def media_main(args, query, cfg, media):
    """media is album, movie, tv, or screen (movies and TV together)."""
    if media == "album":
        print(f'Searching albums for "{query}"...')
        try:
            albums = get_json("https://api.deezer.com/search/album", {"q": query, "limit": 12}).get("data", [])
        except (urllib.error.URLError, ValueError) as e:
            sys.exit(f"Network error: {e}")
        if not albums:
            raise NotFound(f'No album found for "{query}". Try "artist album".')
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
    items = [d for d in imdb_search(query) if d.get("l") and d.get("qid") and d["qid"] != "videoGame"]
    if media != "screen":
        items = [d for d in items if (d["qid"] in TV_TYPES) == (media == "tv")] or items
    if not items:
        raise NotFound(f'Nothing on IMDb for "{query}".')
    item = items[choose("Which one?", [f"{d['l']}  ({d.get('y', '?')})  [{d.get('q', d['qid'])}]" for d in items],
                        auto=args.yes)]
    title = f"{item['l']} ({item['y']})" if item.get("y") else item["l"]
    print(f"Finding posters for {title}...")
    options = []
    if item.get("i", {}).get("imageUrl"):
        options.append(Art(item["i"]["imageUrl"], "IMDb poster"))
    kind = "TV series" if item["qid"] in TV_TYPES else "film"
    wiki = wiki_infobox_image(f"{item['l']} {item.get('y', '')} {kind}", item["l"])
    if wiki:
        options.append(Art(wiki, "Wikipedia poster"))
    finish(options, f"{title} - poster", args, cfg, require_size=True)


# ---------------------------------------------------------------- paintings

def commons_thumb(url, width):
    """Wikimedia's resized copy of an uploaded image, or None if it can't make one."""
    m = re.match(r"(https://upload\.wikimedia\.org/wikipedia/[^/]+/)(.+/([^/]+\.(?:jpe?g|png)))$", url, re.I)
    return f"{m[1]}thumb/{m[2]}/{width}px-{m[3]}" if m else None


def painting_main(args, query, cfg):
    """Paintings and other artworks, from the Wikipedia article's lead image on Wikimedia Commons."""
    print(f'Searching paintings for "{query}"...')
    pages = []
    # Articles about a single artwork carry the "Infobox artwork" template; fall back to a plain search.
    for search in (f'{query} hastemplate:"Infobox artwork"', f"{query} painting"):
        try:
            found = get_json("https://en.wikipedia.org/w/api.php", {
                "action": "query", "format": "json", "generator": "search", "gsrsearch": search,
                "gsrlimit": 12, "prop": "pageimages|description", "piprop": "original"})
        except (urllib.error.URLError, ValueError) as e:
            sys.exit(f"Network error: {e}")
        pages = sorted(found.get("query", {}).get("pages", {}).values(), key=lambda pg: pg.get("index", 0))
        pages = [pg for pg in pages if pg.get("original")]
        if pages:
            break
    if not pages:
        raise NotFound(f'No painting found for "{query}". Try adding the artist.')
    page = pages[choose("Which one?", [f"{pg['title']}  -  {pg.get('description', 'artwork')}" for pg in pages],
                        auto=args.yes)]
    original = page["original"]
    url = original["source"].split("?")[0]
    print(f"Finding scans of {page['title']}...")
    source = "Wikimedia Commons, original"
    try:  # museum scans can be hundreds of MB, so say so before anyone picks one
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            mb = int(r.headers.get("Content-Length") or 0) >> 20
        if mb >= 20:
            source += f" ({mb} MB)"
    except (urllib.error.URLError, OSError, ValueError):
        pass
    small = commons_thumb(url, 1280) if original.get("width", 0) > 1280 else None
    options = [Art(url, source, preview=small)]
    for width in (3840, 1920):
        thumb = commons_thumb(url, width)
        if thumb and width < original.get("width", 0):
            options.append(Art(thumb, f"Wikimedia Commons, resized to {width} wide", preview=small))
    finish(options, f"{page['title']} - {page.get('description') or 'artwork'}", args, cfg, require_size=True)


# ---------------------------------------------------------------- whole folders of games

# File extensions that only one system uses -> LaunchBox platform names. Disc images (.iso, .chd, .cue)
# and archives (.zip, .7z) could be anything, so those rely on the folder name or the platform given.
EXT_PLATFORMS = {
    ".nes": "Nintendo Entertainment System", ".unf": "Nintendo Entertainment System",
    ".fds": "Nintendo Famicom Disk System",
    ".sfc": "Super Nintendo Entertainment System", ".smc": "Super Nintendo Entertainment System",
    ".n64": "Nintendo 64", ".z64": "Nintendo 64", ".v64": "Nintendo 64", ".ndd": "Nintendo 64DD",
    ".gb": "Nintendo Game Boy", ".gbc": "Nintendo Game Boy Color", ".gba": "Nintendo Game Boy Advance",
    ".nds": "Nintendo DS", ".3ds": "Nintendo 3DS", ".cci": "Nintendo 3DS", ".cia": "Nintendo 3DS",
    ".gcm": "Nintendo GameCube", ".wbfs": "Nintendo Wii",
    ".wud": "Nintendo Wii U", ".wux": "Nintendo Wii U", ".wua": "Nintendo Wii U",
    ".nsp": "Nintendo Switch", ".xci": "Nintendo Switch", ".nsz": "Nintendo Switch", ".xcz": "Nintendo Switch",
    ".vb": "Nintendo Virtual Boy",
    ".gen": "Sega Genesis", ".smd": "Sega Genesis", ".sms": "Sega Master System", ".gg": "Sega Game Gear",
    ".32x": "Sega 32X", ".gdi": "Sega Dreamcast", ".cdi": "Sega Dreamcast",
    ".pce": "NEC TurboGrafx-16", ".a26": "Atari 2600", ".a52": "Atari 5200", ".a78": "Atari 7800",
    ".lnx": "Atari Lynx", ".j64": "Atari Jaguar",
    ".ngp": "SNK Neo Geo Pocket", ".ngc": "SNK Neo Geo Pocket Color",
    ".ws": "WonderSwan", ".wsc": "WonderSwan Color",
    ".cso": "Sony PSP", ".pbp": "Sony PSP", ".vpk": "Sony Playstation Vita",
}
NOT_GAMES = {".txt", ".nfo", ".diz", ".pdf", ".htm", ".html", ".url", ".lnk", ".ini", ".cfg", ".json", ".xml",
             ".dat", ".db", ".log", ".md5", ".sha1", ".sfv", ".crc", ".jpg", ".jpeg", ".png", ".gif", ".bmp",
             ".webp", ".ico", ".mp3", ".ogg", ".wav", ".flac", ".mp4", ".mkv", ".sav", ".srm", ".state", ".mcr",
             ".ips", ".bps", ".ups", ".xdelta", ".ppf", ".exe", ".dll", ".bat", ".sh", ".py"}
REGION_TAGS = {"usa": "North America", "us": "North America", "world": "World", "europe": "Europe",
               "eu": "Europe", "uk": "United Kingdom", "japan": "Japan", "jp": "Japan", "australia": "Australia",
               "canada": "Canada", "germany": "Germany", "france": "France", "spain": "Spain", "italy": "Italy",
               "korea": "Korea", "brazil": "Brazil"}
FOLDER_SUBDIRS = {"box": "covers", "clean": "fanart", "logo": "keyart", "icon": "icons"}
SURE_MATCH = 1.0  # name match needed to save without asking (1.3 = exactly the same name)


def rom_title(stem):
    """The game's name from a ROM file name:
    "Legend of Zelda, The - Ocarina of Time (USA) (Rev 1)" -> "The Legend of Zelda - Ocarina of Time"."""
    name = re.sub(r"\s*[(\[][^)\]]*[)\]]", "", stem).replace("_", " ")
    if " " not in name:
        name = name.replace(".", " ")
    name = re.sub(r"\s+", " ", name).strip(" -.")
    # No-Intro moves a leading article to the end of the first part of the name.
    return re.sub(r"^(.*?), (The|A|An)\b(.*)$", r"\2 \1\3", name) or stem


def rom_region(stem):
    """The region in a ROM file name's tags ("(USA, Europe)" -> "North America"), or None."""
    for tag in re.findall(r"\(([^)]*)\)", stem):
        for part in re.split(r",\s*", tag.lower()):
            if part in REGION_TAGS:
                return REGION_TAGS[part]
    return None


def scan_folder(folder):
    """The games in a folder: [(file name without extension, platform implied by the extension or None)]."""
    files = sorted(f for f in folder.iterdir() if f.is_file() and not f.name.startswith("."))
    cues = {f.stem.lower() for f in files if f.suffix.lower() == ".cue"}
    games = []
    for f in files:
        ext = f.suffix.lower()
        if ext in NOT_GAMES or f.stem.lower().startswith("readme"):
            continue
        if ext == ".bin" and (f.stem.lower() in cues or re.search(r"\(track \d+\)", f.stem, re.I)):
            continue  # the .cue sheet stands for the whole disc
        games.append((f.stem, EXT_PLATFORMS.get(ext)))
    if not games:  # one folder per game, as PS3, Wii U and PC dumps are kept
        games = [(d.name, None) for d in sorted(folder.iterdir()) if d.is_dir() and not d.name.startswith(".")]
    return games


def fetch_best(game, kind, orient, region, cache, lock):
    """Download the best art for one game, preferring the ROM's region. Returns (bytes, ext) or None."""
    gid, title, plat = game[:3]
    with lock:
        if gid in cache:  # discs 2 and 3 of the same game
            return cache[gid]
    db = sqlite3.connect(DB_PATH)
    try:
        options = gather(db, gid, title, plat, kind, orient)
    finally:
        db.close()
    if region:
        options.sort(key=lambda o: region.lower() not in o.source.lower())
    if kind == "icon":  # store lookups guess URLs, so keep the ones that turned out to be real images
        for o in options:
            probe_size(o)
        options = sorted((o for o in options if o.size), key=lambda o: -o.final_size()[0])
    result = None
    for art in options:
        try:
            result = render(art)
            break
        except (urllib.error.URLError, OSError):
            continue
    with lock:
        cache[gid] = result
    return result


def folder_main(args, folder_text, cfg):
    """Art for every game in a folder, saved under each ROM's own file name, as emulator frontends expect."""
    folder = Path(os.path.expandvars(os.path.expanduser(folder_text.strip().strip('"').strip("'"))))
    if not folder.is_dir():
        raise NotFound(f"There's no folder at {folder}.")
    games = scan_folder(folder)
    if not games:
        raise NotFound(f"No games in {folder}.")
    if args.type == "ach":
        sys.exit("Achievement icons are one game at a time: fetchart <game> x360 -t ach")
    db = open_index(args.update)

    # The system: the one given, else the folder's name ("N64", "PS2", "Sega Genesis"), else each file's extension.
    platform = resolve_platform(db, args.platform) if args.platform else match_platform(db, folder.name, 0.8)
    if not platform and not all(p for _, p in games) and not args.yes:
        answer = ask("\nWhich system are these games for? (n64, snes, ps2, gc, ... Enter = work it out per game): ")
        platform = resolve_platform(db, answer) if answer else None
    systems = sorted({platform} if platform else {p for _, p in games if p})
    print(f"\nFound {len(games)} games in {folder}" + (f" ({', '.join(systems)})" if systems else "") + ".")

    kinds = ["box", "clean", "logo", "icon"]
    kind = args.type or kinds[choose("What art do you want for each game?", [ART_TYPES[k] for k in kinds],
                                     auto=args.yes)]
    orient = args.orient
    if kind in ("clean", "logo") and not orient:
        orient = ["landscape", "portrait"][choose("Portrait or landscape?", ["Landscape", "Portrait"], auto=args.yes)]
    default = folder / FOLDER_SUBDIRS[kind]
    out = args.out or (str(default) if args.yes else ask(f"\nSave the art to which folder? [Enter = {default}]: ",
                                                          str(default)))
    out = Path(os.path.expandvars(os.path.expanduser(out.strip().strip('"').strip("'"))))
    out.mkdir(parents=True, exist_ok=True)
    have = {f.stem for f in out.iterdir() if f.is_file()}
    print()

    # Match every file first (quick, all local), then download the sure ones in parallel.
    lookups, plan = {}, []
    for stem, ext_platform in games:
        title, plat = rom_title(stem), platform or ext_platform
        key = (norm(title), plat)
        if key not in lookups:
            found = search_games(db, title, plat, kind)
            if not found and plat:  # LaunchBox may file it under a sister system
                found = search_games(db, title, None, kind)
            lookups[key] = found[:8]
        plan.append((stem, lookups[key]))

    total, width = len(plan), len(str(len(plan)))
    counts = {"saved": 0, "had it": 0, "unsure": 0, "no match": 0, "failed": 0}
    unsure, lock, cache = [], threading.Lock(), {}

    def report(n, status, stem, game=None):
        counts[status] += 1
        print(f"[{n:>{width}}/{total}] {status:<8} {stem}" + (f"  ->  {game[1]} [{game[2]}]" if game else ""))

    def save(n, stem, game):
        result = fetch_best(game, kind, orient, rom_region(stem), cache, lock)
        with lock:
            if result:
                (out / (stem + result[1])).write_bytes(result[0])
            report(n, "saved" if result else "failed", stem, game)

    with ThreadPoolExecutor(6) as pool:
        for n, (stem, found) in enumerate(plan, 1):
            if stem in have:
                with lock:
                    report(n, "had it", stem)
            elif not found:
                with lock:
                    report(n, "no match", stem)
            elif found[0][5] < SURE_MATCH:
                unsure.append((stem, found))
                with lock:
                    report(n, "unsure", stem, found[0])
            else:
                pool.submit(save, n, stem, found[0])

    were = "was" if len(unsure) == 1 else "were"
    if unsure and not args.yes:
        print(f"\n{len(unsure)} {were}n't a sure match. Pick the right game for each one:")
        for i, (stem, found) in enumerate(unsure, 1):
            labels = [f"{g[1]}  [{platform_label(g)}]" for g in found] + ["Skip this one", "Skip the rest"]
            pick = choose(f'({i}/{len(unsure)}) Which game is "{stem}"?', labels)
            if pick == len(found) + 1:
                break
            if pick < len(found):
                counts["unsure"] -= 1
                result = fetch_best(found[pick], kind, orient, rom_region(stem), cache, lock)
                if result:
                    (out / (stem + result[1])).write_bytes(result[0])
                    counts["saved"] += 1
                    print(f"  saved {stem}{result[1]}")
                else:
                    counts["failed"] += 1
                    print(f"  couldn't download art for {found[pick][1]}")
    elif unsure:
        print(f"\n{len(unsure)} {were}n't a sure match, so {'it was' if len(unsure) == 1 else 'they were'} "
              "skipped. Run without -y to pick by hand.")

    print(f"\nDone: {counts['saved']} saved, {counts['had it']} already had art, {counts['unsure']} skipped, "
          f"{counts['no match']} not found, {counts['failed']} failed.\nThe art is in {out}")


# ---------------------------------------------------------------- main

def pick_art(options, wanted, auto, heading):
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
    cards = [(o.label(), o.preview or o.url, o) for o in options]
    i = choose("Which one? (biggest first)", [o.label() for o in options], auto=auto, allow_all=True,
               preview=lambda: open_preview(heading, cards))
    return options if i == -1 else [options[i]]


PREVIEW_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fetchart preview</title>
<style>
  :root { --bg: #f4f4f5; --card: #ffffff; --text: #18181b; --muted: #71717a; --line: #e4e4e7; --accent: #2563eb; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #121214; --card: #1c1c20; --text: #f4f4f5; --muted: #a1a1aa; --line: #2e2e33; --accent: #60a5fa; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px 16px 48px; background: var(--bg); color: var(--text);
         font: 15px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { max-width: 1400px; margin: 0 auto 20px; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  header p { margin: 0; color: var(--muted); }
  main { max-width: 1400px; margin: 0 auto; display: grid; gap: 16px;
         grid-template-columns: repeat(auto-fill, minmax(MINpx, 1fr)); }
  figure { margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  .pic { position: relative; display: flex; align-items: center; justify-content: center; min-height: 140px;
         padding: 12px; background: repeating-conic-gradient(var(--line) 0 25%, transparent 0 50%) 0 0 / 16px 16px; }
  .pic > img:first-child { max-width: 100%; max-height: 420px; object-fit: contain; }
  .pic.crop > img:first-child { aspect-ratio: 2 / 3; height: 420px; object-fit: cover; }
  .pic.small > img:first-child { width: 128px; height: 128px; image-rendering: pixelated; }
  .logo { position: absolute; left: 50%; bottom: 10%; transform: translateX(-50%); width: 45%; max-height: 30%;
          object-fit: contain; filter: drop-shadow(2px 3px 4px rgba(0, 0, 0, .6)); }
  .crop .logo { width: 70%; }
  .num { position: absolute; top: 8px; left: 8px; min-width: 32px; padding: 3px 8px; border-radius: 999px;
         background: var(--accent); color: #fff; font-weight: 700; text-align: center; }
  figcaption { padding: 10px 12px; border-top: 1px solid var(--line); font-variant-numeric: tabular-nums;
               overflow-wrap: anywhere; }
</style></head>
<body>
<header><h1>TITLE</h1><p>Go back to the terminal and pick the number you want.</p></header>
<main>
TILES
</main>
</body></html>
"""


def data_uri(data):
    kind = {".png": "png", ".gif": "gif", ".webp": "webp"}.get(image_ext(data), "jpeg")
    return f"data:image/{kind};base64," + base64.b64encode(data).decode()


def open_preview(heading, cards, small=False):
    """Show the options as numbered pictures in the browser. cards: [(label, image src, Art or None)]."""
    tiles = []
    for n, (label, src, art) in enumerate(cards, 1):
        classes = "pic" + (" crop" if art and art.crop else "") + (" small" if small else "")
        logo = f'<img class="logo" src="{html.escape(art.logo)}" alt="">' if art and art.logo else ""
        tiles.append(f'<figure><div class="{classes}"><img src="{html.escape(src)}" alt="">{logo}'
                     f'<span class="num">{n}</span></div><figcaption>{html.escape(label)}</figcaption></figure>')
    page = (PREVIEW_PAGE.replace("MIN", "200" if small else "280").replace("TITLE", html.escape(heading))
            .replace("TILES", "\n".join(tiles)))
    path = DATA_DIR / "preview.html"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    webbrowser.open(path.as_uri())


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
    p = argparse.ArgumentParser(prog="fetchart", description="Find and download game art, posters, album covers "
                                                              "and paintings. Run it with no arguments for menus.")
    p.add_argument("game", nargs="?", help='name, e.g. halo-combat-evolved or "the dark side of the moon"')
    p.add_argument("platform", nargs="?",
                   help="pc, xbox, x360, ps2, n64, gc, wii, switch, gba, ... (default: all), or movie, tv, album, painting")
    p.add_argument("-t", "--type", choices=list(ART_TYPES), help="box, clean (no logo), logo, icon, or ach (360 achievements)")
    p.add_argument("-r", "--orient", choices=["portrait", "landscape"], help="key art orientation")
    p.add_argument("-q", "--quality", help="low, medium, high, all, or an exact size like 1920x1080")
    p.add_argument("-o", "--out", help="folder to save into")
    p.add_argument("-y", "--yes", action="store_true", help="take the top match and defaults, no prompts")
    p.add_argument("-f", "--folder", help="get art for every game in this folder, named after each ROM file")
    p.add_argument("--update", action="store_true", help="re-download the game database")
    p.add_argument("--version", action="version", version=f"fetchart {__version__}")
    args = p.parse_args()
    if args.folder:
        args.platform = args.platform or args.game  # fetchart --folder D:\ROMs n64
        try:
            return folder_main(args, args.folder, load_config())
        except NotFound as e:
            sys.exit(str(e))
    if args.update and not args.game:
        open_index(force_update=True)
        return

    # With no name given (typed alone, or double-clicked), everything is asked through menus.
    interactive = not args.game
    if interactive:
        category = list(CATEGORIES)[choose("What are you looking for?", [c[0] for c in CATEGORIES.values()])]
    else:
        category = MEDIA.get(re.sub(r"[^a-z]", "", (args.platform or "").lower()), "game")
    cfg = load_config()
    while True:
        if interactive:
            args.game = ask("\n" + CATEGORIES[category][1])
            if not args.game:
                return
        query = re.sub(r"[-_]+", " ", args.game).strip()
        try:
            if category == "folder":
                return folder_main(args, args.game, cfg)
            if category == "game":
                return game_main(args, query, cfg)
            if category == "painting":
                return painting_main(args, query, cfg)
            return media_main(args, query, cfg, category)
        except NotFound as e:
            if not interactive:
                sys.exit(str(e))
            print(f"{e} Try another name (Enter to quit).")


def game_main(args, query, cfg):
    db = open_index(args.update)
    platform = resolve_platform(db, args.platform)
    if args.type == "ach":
        if platform not in (None, XBOX360):
            sys.exit("Achievement icons are only for Xbox 360 games (platform x360).")
        platform = XBOX360

    groups = group_games(search_games(db, query, platform, args.type), split_year(query)[1])
    if not groups:
        what = ART_TYPES[args.type].lower() if args.type else "game"
        where = f" on {platform}" if platform else ""
        raise NotFound(f'No {what} found for "{query}"{where}. Try a shorter or different name.')
    group = groups[choose("Which game?", [group_label(g) for g in groups], auto=args.yes)]
    gid, title, plat = group[choose(f"Which platform is {group[0][1]} on?",
                                    [platform_label(g) for g in group], auto=args.yes)][:3]

    kinds = [k for k in ART_TYPES if k != "ach" or plat == XBOX360]
    while True:
        kind = args.type or kinds[choose("What art do you want?", [ART_TYPES[k] for k in kinds], auto=args.yes)]
        orient = args.orient
        if kind in ("clean", "logo") and not orient:
            orient = ["landscape", "portrait"][choose("Portrait or landscape?", ["Landscape", "Portrait"],
                                                      auto=args.yes)]
        if kind == "ach":
            return download_achievements(title, plat, args, cfg)
        print(f"Finding art for {title} [{plat}]...")
        options = gather(db, gid, title, plat, kind, orient)
        if options:
            break
        missing = f"{title} has no {ART_TYPES[kind].lower()}" + (f" in {orient}" if orient else "") + "."
        if args.type or args.yes:
            sys.exit(missing)
        print(f"{missing} Pick another kind.")
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

    picked = pick_art(options, args.quality, args.yes, name_prefix)
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
