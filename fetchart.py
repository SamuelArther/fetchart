"""fetchart - find and download game art, posters, album covers and paintings from the command line.

Usage:
    fetchart                     menus for everything: category, name, platform, art, folder
    fetchart <game> [platform] [-t TYPE] [-r portrait|landscape] [-q QUALITY] [-o FOLDER] [-y] [-w]
    fetchart --folder DIR [platform] [-t TYPE] [-o FOLDER] [-y]   art for every ROM in a folder
    fetchart --folder DIR movie|tv [-y]                           posters for a folder of movies or shows
    fetchart --steam [STEAM_DIR] [-y]                             Steam library art for non-Steam games
    fetchart --undo-wallpaper    put back the wallpaper from before -w
    fetchart --update            re-download the game database

Art types:
    box        box cover art, rating and all         (LaunchBox box fronts, every region)
    clean      key art without the logo              (LaunchBox fanart backgrounds, Steam library hero)
    logo       key art with the logo                 (Steam capsules, LaunchBox banners/posters, or
                                                      fanart with the game's clear logo laid on top)
    clearlogo  just the logo, see-through background (Steam library logo, LaunchBox clear logos)
    icon       dashboard icon / tile                 (Xbox 360 marketplace tiles + banners, Microsoft Store
                                                      squares, Nintendo eShop icons, Steam + LaunchBox icons)
    ach        achievement / trophy icons, by name   (Xbox, PlayStation, Steam and RetroAchievements lists
                                                      from Exophase; Xbox 360 originals from Xbox Live)

-w / --wallpaper saves the best landscape key art (or painting) and sets it as the desktop background.

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
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import xml.etree.ElementTree as ET

__version__ = "1.4.1"

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
    "clearlogo": ("Clear Logo",),
    "icon": (),  # icons mostly come from console stores, so any game qualifies
    "ach": (),
    "wallpaper": ("Fanart - Background",),
    None: (),  # art type not picked yet
}
ART_TYPES = {
    "box": "Box cover art (the retail cover, rating and all)",
    "clean": "Key art, no logo",
    "logo": "Key art, with logo",
    "clearlogo": "Clear logo (just the logo, see-through background)",
    "icon": "Dashboard icon / tile (the square on the console menu)",
    "ach": "Achievement icons (pick any or all)",
    "wallpaper": "Desktop wallpaper (the best key art, set as your background)",
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
    name = re.sub(r"\s*:\s*", " - ", name)  # "Minecraft: Xbox 360 Edition" -> "Minecraft - Xbox 360 Edition"
    return re.sub(r'[<>"/\\|?*]+', "_", name).strip(" .") or "art"


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


def images_by_type(db, gid):
    """A game's LaunchBox images: {type: [(region, url)]}, best region first."""
    by_type = {}
    for t, region, url in sorted(game_images(db, gid), key=lambda i: region_rank(i[1])):
        by_type.setdefault(t, []).append((region, url))
    return by_type


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
        # Type 0 is a game. Others are DLC and soundtracks, whose art carries a badge ("Halo 3" is MCC DLC).
        return items[0].get("assets", {}) if items and items[0].get("type", 0) == 0 else {}
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


def steam_logos(assets):
    """The library logo (transparent PNG). The store API doesn't list it, but every app has one at a fixed path."""
    m = re.search(r"apps/(\d+)/", assets.get("asset_url_format", ""))
    return [f"{STEAM_CDN}steam/apps/{m[1]}/{name}" for name in ("logo_2x.png", "logo.png")] if m else []


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


# ---------------------------------------------------------------- achievements and trophies

# Old consoles get their achievements from RetroAchievements, which Exophase lists under one "retro"
# platform with the console's name: LaunchBox platform -> that name.
RETRO_SYSTEMS = {
    "Nintendo Entertainment System": "NES", "Nintendo Famicom Disk System": "Famicom Disk System",
    "Super Nintendo Entertainment System": "SNES", "Nintendo 64": "N64", "Nintendo GameCube": "GameCube",
    "Nintendo Wii": "Wii", "Nintendo Game Boy": "Game Boy", "Nintendo Game Boy Color": "GBC",
    "Nintendo Game Boy Advance": "GBA", "Nintendo DS": "NDS", "Nintendo Virtual Boy": "Virtual Boy",
    "Nintendo Pokemon Mini": "Pokemon Mini", "Sega Genesis": "Genesis", "Sega Master System": "Master System",
    "Sega Game Gear": "Game Gear", "Sega 32X": "32X", "Sega CD": "Sega CD", "Sega Saturn": "Saturn",
    "Sega Dreamcast": "Dreamcast", "Sega SG-1000": "SG-1000", "Sony Playstation": "PS1",
    "Sony Playstation 2": "PS2", "Sony PSP": "PSP", "NEC TurboGrafx-16": "PC Engine",
    "NEC TurboGrafx-CD": "PC Engine CD", "Atari 2600": "Atari 2600", "Atari 7800": "Atari 7800",
    "Atari Jaguar": "Atari Jaguar", "Atari Lynx": "Atari Lynx", "3DO Interactive Multiplayer": "3DO",
    "Arcade": "Arcade", "SNK Neo Geo CD": "Neo Geo CD", "SNK Neo Geo Pocket": "Neo Geo Pocket",
    "SNK Neo Geo Pocket Color": "Neo Geo Pocket", "WonderSwan": "WonderSwan", "WonderSwan Color": "WonderSwan",
    "ColecoVision": "ColecoVision", "Mattel Intellivision": "Intellivision", "Microsoft MSX": "MSX",
    "Microsoft MSX2": "MSX", "Apple II": "Apple II", "Amstrad CPC": "Amstrad CPC", "NEC PC-8801": "PC-8000/8800",
}
# LaunchBox platform -> where Exophase lists its achievements: [(platform slug, console name or None)], best first.
ACH_SOURCES = {
    XBOX360: [("xbox-360", None), ("xbox-live-arcade", None)],
    "Microsoft Xbox One": [("xbox-one", None)],
    "Microsoft Xbox Series X/S": [("xbox-series", None), ("xbox-one", None)],
    "Windows": [("steam", None), ("windows-10", None), ("gog", None), ("epic", None), ("gfwl", None)],
    "Sony Playstation 3": [("ps3", None)], "Sony Playstation 4": [("ps4", None)],
    "Sony Playstation 5": [("ps5", None)], "Sony Playstation Vita": [("ps-vita", None)],
    "Android": [("android", None)], "Apple iOS": [("apple-ios", None)], "Apple Mac OS": [("apple-mac", None)],
    **{plat: [("retro", name)] for plat, name in RETRO_SYSTEMS.items()},
}
TROPHY_PLATFORMS = {"Sony Playstation 3", "Sony Playstation 4", "Sony Playstation 5", "Sony Playstation Vita"}


def ach_word(plat):
    return "trophy" if plat in TROPHY_PLATFORMS else "achievement"


def xbox360_achievement_icons(title):
    """The original icons from the Xbox Live image server: (list of image bytes, game icon bytes), or ([], None)."""
    item = xbox360_title(title)
    if not item:
        return [], None
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
    return [data for _, data in sorted(icons.items())], fetch_or_none(f"{base}icon/0/8000")


def download_achievements(title, plat, args, cfg):
    """Save a game's achievement (or trophy) icons, by name, into '<folder>/<title> [platform] achievements'."""
    word = ach_word(plat)
    icons, game_icon = [], None
    if plat == XBOX360:
        icons, game_icon = xbox360_achievement_icons(title)
    if not icons:
        print(f"Finding {word} icons for {title} [{plat}]...")
    named = exophase_achievements(title, plat)
    if not icons and not named:
        sys.exit(f"Couldn't find {word} icons for {title} [{plat}].")

    # entries: (menu label, file name, image bytes or None to download from url, url)
    entries = []
    if game_icon:
        entries.append(("Game icon (guide / mini dash)", "game icon", game_icon, None))
    if named:
        print(f"Found {len(named)} {'trophies' if word == 'trophy' else 'achievements'}.")
        width = len(str(len(named)))
        datas = match_icons(named, icons) if icons else [None] * len(named)
        for n, (ach, data) in enumerate(zip(named, datas), 1):
            badge = f" ({ach['badge']})" if ach["badge"] else ""
            desc = f" - {ach['desc']}" if ach["desc"] else ""
            entries.append((f"{ach['name']}{badge}{desc}", f"{n:0{width}d} {ach['name']}", data, ach["icon"]))
    else:
        print(f"Found {len(icons)} achievement icons (no names for this game, so they're numbered).")
        width = len(str(len(icons)))
        for n, data in enumerate(icons, 1):
            entries.append((f"Achievement icon {n}", f"achievement {n:0{width}d}", data, None))

    picked = entries if args.yes or (args.quality or "").lower() == "all" else \
        [entries[i] for i in choose_many("Which ones?", [e[0] for e in entries], preview=lambda: open_preview(
            f"{title} {word} icons", [(e[0], data_uri(e[2]) if e[2] else e[3], None) for e in entries], small=True))]
    folder_name = f"{title} [{plat}] {'trophies' if word == 'trophy' else 'achievements'}"
    folder = pick_folder(args.out, cfg, args.yes) / safe_filename(folder_name)
    folder.mkdir(parents=True, exist_ok=True)

    def get(entry):
        return entry[2] or fetch_or_none(entry[3])

    with ThreadPoolExecutor(8) as pool:
        datas = list(pool.map(get, picked))
    saved = 0
    for (_, name, _, _), data in zip(picked, datas):
        if data:
            unique_path(folder / (safe_filename(name) + image_ext(data))).write_bytes(data)
            saved += 1
    print(f"Saved {saved} icon{'s' * (saved != 1)} to {folder}")


def exophase_title(title):
    """Exophase names in a form that compares well: RetroAchievements writes "Legend of Zelda, The: ..."."""
    return re.sub(r"^(.*?), (The|A|An)\b(.*)$", r"\2 \1\3", title)


def exophase_achievements(title, plat=XBOX360):
    """A game's achievements or trophies, in the game's order, from Exophase:
    [{name, desc, badge, icon, small}], or [] when it isn't listed. badge is the gamerscore, points or
    trophy grade; icon is the biggest copy of the picture, small the 64px one."""
    sources = ACH_SOURCES.get(plat, [])

    def source_rank(game):  # which of the platform's sources lists this game, best first; None for none
        for i, (slug, name) in enumerate(sources):
            if any(p["slug"] == slug and name in (None, p.get("name")) for p in game.get("platforms", [])):
                return i
        return None

    # Exophase drops edition words that XboxUnity keeps ("Minecraft: Xbox 360 Edition" is just "Minecraft").
    short = re.sub(r"[\s:\-]*\b(for |on )?(xbox 360|x360)( edition)?\s*$", "", title, flags=re.I).strip()
    try:
        for query in dict.fromkeys((title, short)):
            found = get_json("https://api.exophase.com/public/archive/games", {"q": query}).get("games")
            # RetroAchievements also lists ROM hacks ("~Hack~ ...") and bonus sets ("... [Subset - ...]").
            games = [dict(g, plain=exophase_title(g["title"])) for g in (found or {}).get("list", [])
                     if source_rank(g) is not None and not g["title"].startswith("~") and "[Subset" not in g["title"]]
            # Japanese releases are listed separately; prefer the main one when the names tie.
            games.sort(key=lambda g: (source_rank(g), "-jp-" in g.get("endpoint_awards", "")))
            game = best_match(query, games, "plain")
            if game:
                break
        else:
            return []
        page = http_get(game["endpoint_awards"]).decode("utf-8", "replace")
    except (urllib.error.URLError, ValueError, KeyError, TypeError, AttributeError):
        return []
    return parse_exophase_awards(page, game.get("environment_slug"))


def parse_exophase_awards(page, environment):
    """The achievements on an Exophase achievements or trophies page."""
    out = []
    for block in page.split("<li ")[1:]:
        icon = re.search(r'class="award-image[^"]*" src="([^"?]+)', block)
        name = re.search(r'award-title.*?<a [^>]*>(.*?)</a>', block, re.S)
        if not (icon and name):
            continue
        desc = re.search(r'award-description[^>]*>\s*<p>(.*?)</p>', block, re.S)
        points = re.search(r'award-points.*?<span>(\d+)</span>', block, re.S)
        grade = re.search(r'exo-icon-trophy-(platinum|gold|silver|bronze)', block)
        if grade:
            badge = grade[1].title()
        elif points:
            badge = points[1] + ("G" if environment == "xbox" else " points")
        else:
            badge = ""
        out.append({"name": html.unescape(name[1]).strip(), "small": icon[1], "badge": badge,
                    "icon": icon[1].replace("/awards/s/", "/awards/o/"),  # "o" is the original size
                    "desc": html.unescape(re.sub(r"<[^>]+>", "", desc[1])).strip() if desc else ""})
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
        theirs = list(pool.map(lambda a: fetch_or_none(a["small"]), named))
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
    """One downloadable option. crop = cut to 2:3 portrait (True) or to this width/height ratio;
    logo = clear-logo URL to lay on top; scale = resize a small square icon up to this many pixels."""

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
        return crop_size(self.size, self.crop)

    def label(self):
        s = self.final_size()
        return (f"{s[0]}x{s[1]}" if s else "size ?").ljust(12) + self.source


def crop_size(size, crop):
    """The size after cutting the middle out of an image to the crop ratio (True = 2:3 portrait)."""
    w, h = size
    if not crop:
        return w, h
    ratio = 2 / 3 if crop is True else crop
    return min(w, round(h * ratio)), min(h, round(w / ratio))


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
    by_type = images_by_type(db, gid)
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
    if kind == "clearlogo":
        for url in steam_logos(steam):
            out.append(Art(url, "Steam logo (official)"))
        for region, url in by_type.get("Clear Logo", []):
            out.append(Art(url, f"clear logo, {region}" if region else "clear logo"))
        return out
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
        cw, ch = crop_size((w, h), art.crop)
        left, top = (w - cw) // 2, (h - ch) // 2
        img = img.crop((left, top, left + cw, top + ch))
        w, h = cw, ch
    if art.logo:
        logo = Image.open(io.BytesIO(http_get(art.logo))).convert("RGBA")
        scale = min(w * (0.8 if w < h else 0.45) / logo.width, h * 0.3 / logo.height)
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
         "album": "album", "music": "album", "cd": "album", "song": "album",
         "painting": "painting", "paintings": "painting", "art": "painting", "artwork": "painting"}
# Top menu when fetchart is run with no arguments: key -> (menu text, name prompt)
CATEGORIES = {
    "game": ("Video game", "Game name: "),
    "screen": ("Movie or TV show", "Movie or show name: "),
    "album": ("Music (album cover)", "Album (artist + album name works best): "),
    "painting": ("Painting / real art", "Painting name (add the artist if you know it): "),
    "folder": ("A whole folder of games (art for every ROM)", "Folder with your games (paste its path): "),
    "videos": ("A whole folder of movies or TV shows (posters for Plex, Jellyfin, Kodi)",
               "Folder with your movies or shows (paste its path): "),
    "steam": ("Steam: art for my non-Steam games", None),
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
FOLDER_SUBDIRS = {"box": "covers", "clean": "fanart", "logo": "keyart", "clearlogo": "marquees", "icon": "icons"}
VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg", ".divx"}
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


def folder_main(args, folder_text, cfg, videos=False):
    """Art for every game in a folder, saved under each ROM's own file name, as emulator frontends expect."""
    folder = Path(os.path.expandvars(os.path.expanduser(folder_text.strip().strip('"').strip("'"))))
    if not folder.is_dir():
        raise NotFound(f"There's no folder at {folder}.")
    media = MEDIA.get(re.sub(r"[^a-z]", "", (args.platform or "").lower()))
    if videos or media in ("movie", "tv") or (not args.platform and looks_like_videos(folder)):
        return video_folder_main(args, folder, media)
    if args.type and args.type not in FOLDER_SUBDIRS:
        sys.exit(f"-t {args.type} is one game at a time. For folders use: {', '.join(FOLDER_SUBDIRS)}")
    games = scan_folder(folder)
    if not games:
        raise NotFound(f"No games in {folder}.")
    db = open_index(args.update)

    # The system: the one given, else the folder's name ("N64", "PS2", "Sega Genesis"), else each file's extension.
    platform = resolve_platform(db, args.platform) if args.platform else match_platform(db, folder.name, 0.8)
    if not platform and not all(p for _, p in games) and not args.yes:
        answer = ask("\nWhich system are these games for? (n64, snes, ps2, gc, ... Enter = work it out per game): ")
        platform = resolve_platform(db, answer) if answer else None
    systems = sorted({platform} if platform else {p for _, p in games if p})
    print(f"\nFound {len(games)} games in {folder}" + (f" ({', '.join(systems)})" if systems else "") + ".")

    kinds = list(FOLDER_SUBDIRS)
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


# ---------------------------------------------------------------- whole folders of movies and shows

EPISODE = re.compile(r"\bs\d{1,2}\s?e\d{1,3}\b|\b\d{1,2}x\d{2}\b", re.I)
SEASON_DIR = re.compile(r"(season|series|staffel|saison|temporada)\s*\d+|specials|s\d{1,2}", re.I)
# Release-name junk that ends a title when a file name has no year ("Inception.1080p.BluRay.x264").
RELEASE_TAGS = re.compile(r"[\s._\-\[(]+(?:\d{3,4}p|4k|uhd|hdr|blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|dvdrip|"
                          r"x26[45]|h\.?26[45]|hevc|remux|proper|repack|extended|unrated)\b.*", re.I)


def video_title(name):
    """Title, year and IMDb ID in a movie or show's file or folder name:
    "Inception (2010) {imdb-tt1375666}" -> ("Inception", 2010, "tt1375666"),
    "The.Matrix.1999.1080p.BluRay.x264-GRP" -> ("The Matrix", 1999, None)."""
    imdb = re.search(r"\btt\d{7,}\b", name)
    name = re.sub(r"\{[^}]*\}", " ", name)
    if " " not in name.strip():
        name = re.sub(r"[._]+", " ", name)
    # A year in brackets, else the last bare one ("Blade Runner 2049 2017 1080p" is from 2017).
    m = (re.search(r"^(.+?)\s*[\[(]((?:19|20)\d\d)[\])]", name)
         or re.search(r"^(.+)[\s\-]+((?:19|20)\d\d)(?!\d)", name))
    title, year = (m[1], int(m[2])) if m else (RELEASE_TAGS.sub("", name), None)
    title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title).replace("_", " ")  # "The Office (US)"
    return re.sub(r"\s+", " ", title).strip(" -.("), year, imdb[0] if imdb else None


def has_videos(folder):
    return any(f.suffix.lower() in VIDEO_EXTS for f in folder.iterdir() if f.is_file())


def is_show(folder):
    """A show's folder has season folders, or video files named like episodes (S01E02, 1x02)."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return False
    return (any(e.is_dir() and SEASON_DIR.fullmatch(e.name) for e in entries)
            or any(e.is_file() and e.suffix.lower() in VIDEO_EXTS and EPISODE.search(e.stem) for e in entries))


def looks_like_videos(folder):
    """True when a folder holds movies or shows: video files in it, or in the folders inside it."""
    try:
        return has_videos(folder) or any(is_show(d) or has_videos(d)
                                         for d in sorted(folder.iterdir())[:30] if d.is_dir())
    except OSError:
        return False


def scan_videos(folder):
    """The movies and shows in a folder: [(name to look up, poster path, "movie" or "tv")].
    A movie or show in its own folder gets poster.jpg in it; a loose movie file gets "<file name>-poster.jpg"
    next to it. Plex, Jellyfin, Emby and Kodi all read those."""
    others = [d for d in folder.iterdir() if d.is_dir() and not SEASON_DIR.fullmatch(d.name)]
    if is_show(folder) and not any(is_show(d) or has_videos(d) for d in others):  # pointed straight at one show
        return [(folder.name, folder / "poster.jpg", "tv")]
    out = []
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            if is_show(entry):
                out.append((entry.name, entry / "poster.jpg", "tv"))
            elif has_videos(entry):
                out.append((entry.name, entry / "poster.jpg", "movie"))
        elif entry.suffix.lower() in VIDEO_EXTS and not EPISODE.search(entry.stem):
            out.append((entry.stem, folder / f"{entry.stem}-poster.jpg", "movie"))
    return out


def find_poster(name, kind):
    """IMDb matches for a movie or show's file name: (candidates best first, sure). Each candidate is an
    IMDb suggestion with a poster."""
    title, year, imdb = video_title(name)
    if not (imdb or title):
        return [], False
    items = [d for d in imdb_search(imdb or title)
             if d.get("l") and d.get("i", {}).get("imageUrl") and d.get("qid") not in (None, "videoGame")]
    if imdb:
        return items[:1], bool(items)
    same_kind = [d for d in items if (d["qid"] in TV_TYPES) == (kind == "tv")]
    exact = [d for d in same_kind if norm(d["l"]) == norm(title)]
    if year:  # release years differ by a year between countries
        dated = [d for d in exact if abs((d.get("y") or 0) - year) <= 1]
        if dated:
            return dated + [d for d in items if d not in dated], True
    elif exact:
        return exact + [d for d in items if d not in exact], len(exact) == 1
    return sorted(items, key=lambda d: -score(title, d["l"]))[:8], False


def imdb_label(d):
    return f"{d['l']} ({d.get('y', '?')}) [{'TV' if d['qid'] in TV_TYPES else d['qid']}]"


def video_folder_main(args, folder, media):
    """A poster for every movie and show in a folder, where media servers look for them."""
    entries = scan_videos(folder)
    if media in ("movie", "tv"):
        entries = [(name, dest, media) for name, dest, _ in entries]
    if not entries:
        raise NotFound(f"No movies or shows in {folder}.")
    shows = sum(kind == "tv" for _, _, kind in entries)
    print(f"\nFound {len(entries) - shows} movies and {shows} shows in {folder}.\n")
    total, width = len(entries), len(str(len(entries)))
    counts = {"saved": 0, "had it": 0, "unsure": 0, "no match": 0, "failed": 0}
    unsure = []

    def report(n, status, name, match=None):
        counts[status] += 1
        print(f"[{n:>{width}}/{total}] {status:<8} {name}" + (f"  ->  {imdb_label(match)}" if match else ""))

    def save(dest, match):
        try:
            data = http_get(match["i"]["imageUrl"], timeout=90)
        except (urllib.error.URLError, OSError):
            return False
        dest.with_suffix(image_ext(data)).write_bytes(data)
        return True

    def handle(entry):  # -> (status, matches)
        name, dest, kind = entry
        if any(dest.with_suffix(ext).exists() for ext in (".jpg", ".png", ".webp")):
            return "had it", []
        found, sure = find_poster(name, kind)
        if not found:
            return "no match", []
        if not sure:
            return "unsure", found
        return "saved" if save(dest, found[0]) else "failed", found

    with ThreadPoolExecutor(6) as pool:  # map hands results back in order, so the report reads top to bottom
        for n, ((name, dest, _), (status, found)) in enumerate(zip(entries, pool.map(handle, entries)), 1):
            report(n, status, name, found[0] if found else None)
            if status == "unsure":
                unsure.append((n, name, dest, found))

    were = "was" if len(unsure) == 1 else "were"
    if unsure and not args.yes:
        print(f"\n{len(unsure)} {were}n't a sure match. Pick the right one for each:")
        for i, (_, name, dest, found) in enumerate(unsure, 1):
            pick = choose(f'({i}/{len(unsure)}) Which one is "{name}"?',
                          [imdb_label(d) for d in found] + ["Skip this one", "Skip the rest"])
            if pick == len(found) + 1:
                break
            if pick < len(found):
                counts["unsure"] -= 1
                ok = save(dest, found[pick])
                counts["saved" if ok else "failed"] += 1
                print(f"  saved {dest.relative_to(folder)}" if ok
                      else f"  couldn't download the poster for {found[pick]['l']}")
    elif unsure:
        print(f"\n{len(unsure)} {were}n't a sure match, so {'it was' if len(unsure) == 1 else 'they were'} "
              "skipped. Run without -y to pick by hand.")
    print(f"\nDone: {counts['saved']} saved, {counts['had it']} already had a poster, {counts['unsure']} skipped, "
          f"{counts['no match']} not found, {counts['failed']} failed.")


# ---------------------------------------------------------------- Steam library art for non-Steam games

# Files in Steam's grid folder are named after the shortcut's app ID plus one of these: suffix -> what it is.
STEAM_SLOTS = {"p": "cover", "": "wide cover", "_hero": "background", "_logo": "logo"}
STEAM_ID_BASE = 76561197960265728  # SteamID64 of account 0; userdata folders use the account number


def find_steam():
    """Steam's install folder, or None."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                path = Path(winreg.QueryValueEx(key, "SteamPath")[0])
            if (path / "userdata").is_dir():
                return path
        except OSError:
            pass
        candidates = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"]
    elif sys.platform == "darwin":
        candidates = [Path.home() / "Library" / "Application Support" / "Steam"]
    else:
        candidates = [Path.home() / ".local/share/Steam", Path.home() / ".steam/steam",
                      Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    return next((c for c in candidates if (c / "userdata").is_dir()), None)


def read_binary_vdf(data):
    """Steam's binary KeyValues format (shortcuts.vdf) as nested dicts."""
    pos = 0

    def text():
        nonlocal pos
        end = data.index(b"\0", pos)
        value, pos = data[pos:end].decode("utf-8", "replace"), end + 1
        return value

    def table():
        nonlocal pos
        out = {}
        while pos < len(data):
            kind = data[pos]
            pos += 1
            if kind == 8:
                return out
            key = text()
            if kind == 0:
                out[key] = table()
            elif kind == 1:
                out[key] = text()
            elif kind in (2, 3, 7):  # int32, float32, uint64
                size = 8 if kind == 7 else 4
                out[key] = int.from_bytes(data[pos:pos + size], "little")
                pos += size
            else:
                raise ValueError(f"unknown value type {kind} in a VDF file")
        return out

    return table()


def steam_shortcuts(path):
    """The non-Steam games added to an account: [(app ID, name)]."""
    out = []
    for entry in read_binary_vdf(path.read_bytes()).get("shortcuts", {}).values():
        entry = {k.lower(): v for k, v in entry.items()}
        name = entry.get("appname", "").strip()
        # Steam has saved the ID since 2019; before that it was worked out from the program and name.
        appid = entry.get("appid") or zlib.crc32((entry.get("exe", "") + name).encode()) | 0x80000000
        if name:
            out.append((appid & 0xFFFFFFFF, name))
    return out


def steam_accounts(root):
    """Accounts that have non-Steam games: [(userdata folder, account name)], last signed in first."""
    names, recent = {}, None
    try:
        users = (root / "config" / "loginusers.vdf").read_text(encoding="utf-8", errors="replace")
        for sid, body in re.findall(r'"(\d{17})"\s*\{(.*?)\}', users, re.S):
            account = str(int(sid) - STEAM_ID_BASE)
            persona = re.search(r'"PersonaName"\s+"([^"]*)"', body)
            names[account] = persona[1] if persona else account
            if re.search(r'"MostRecent"\s+"1"', body):
                recent = account
    except OSError:
        pass
    folders = sorted(d for d in (root / "userdata").iterdir() if (d / "config" / "shortcuts.vdf").is_file())
    folders.sort(key=lambda d: d.name != recent)
    return [(d, names.get(d.name, d.name)) for d in folders]


def steam_grid_art(db, game, steam):
    """Candidates for each grid file, best first: {suffix: [Art]}. game is a LaunchBox match or None,
    steam the Steam store assets of a game with the same name ({} if none)."""
    out = {suffix: [] for suffix in STEAM_SLOTS}
    out["p"] += [Art(u, "Steam") for u in steam_urls(steam, ["library_capsule_2x", "library_capsule"])]
    out[""] += [Art(u, "Steam") for u in steam_urls(steam, ["header_2x", "header"])]
    out["_hero"] += [Art(u, "Steam") for u in steam_urls(steam, ["library_hero_2x", "library_hero"])]
    out["_logo"] += [Art(u, "Steam") for u in steam_logos(steam)]
    if game:
        by_type = images_by_type(db, game[0])
        fanart = [url for _, url in by_type.get("Fanart - Background", [])][:2]
        logos = [url for _, url in by_type.get("Clear Logo", [])]
        logo = logos[0] if logos else None
        out["p"] += [Art(url, "poster", crop=True) for _, url in by_type.get("Poster", [])]
        # Most PC and disc boxes are close to Steam's 2:3; wide cartridge boxes would lose too much.
        boxes = [Art(url, "box", crop=True) for _, url in by_type.get("Box - Front", [])[:3]]
        for box in boxes:
            probe_size(box)
        out["p"] += [b for b in boxes if b.size and b.size[0] / b.size[1] <= 0.85]
        out["p"] += [Art(url, "fanart + logo", crop=True, logo=logo) for url in fanart]
        out[""] += [Art(url, "fanart + logo", crop=920 / 430, logo=logo) for url in fanart]
        out["_hero"] += [Art(url, "fanart", crop=1920 / 620) for url in fanart]
        out["_logo"] += [Art(url, "clear logo") for url in logos[:2]]
    return out


def render_first(options):
    """Download the first option that works: (bytes, ext) or None."""
    for art in options:
        try:
            return render(art)
        except (urllib.error.URLError, OSError):
            continue
    return None


def steam_main(args, steam_text):
    """Fill in the library art Steam leaves blank for games added with "Add a Non-Steam Game"."""
    root = Path(os.path.expandvars(os.path.expanduser(steam_text.strip().strip('"')))) if steam_text else find_steam()
    if not root or not (root / "userdata").is_dir():
        sys.exit('Couldn\'t find Steam. Point fetchart at its folder: fetchart --steam "D:\\Steam"')
    accounts = steam_accounts(root)
    if not accounts:
        sys.exit("This Steam has no non-Steam games yet. Add some in Steam (Games > Add a Non-Steam Game "
                 "to My Library), then run this again.")
    user, who = accounts[choose("Which Steam account?", [a[1] for a in accounts], auto=args.yes)]
    games = steam_shortcuts(user / "config" / "shortcuts.vdf")
    grid = user / "config" / "grid"

    def missing(appid):  # never replace art someone already picked
        return [s for s in STEAM_SLOTS
                if not any((grid / f"{appid}{s}{ext}").exists() for ext in (".png", ".jpg", ".jpeg", ".webp"))]

    todo = [(appid, name, missing(appid)) for appid, name in games]
    todo = [t for t in todo if t[2]]
    print(f"\n{who} has {len(games)} non-Steam game{'s' * (len(games) != 1)}, "
          f"{len(todo)} of them missing art.")
    if not todo:
        return
    if not args.yes:
        picks = choose_many("Which games should get art?", [f"{name}  (needs: {', '.join(STEAM_SLOTS[s] for s in slots)})"
                                                            for _, name, slots in todo])
        todo = [todo[i] for i in picks]
    db = open_index(args.update)
    grid.mkdir(parents=True, exist_ok=True)

    def match(item):  # official Steam art when the game is on Steam; LaunchBox fills whatever Steam lacks
        conn = sqlite3.connect(DB_PATH)  # sqlite connections can't be shared between threads
        try:
            return steam_assets(item[1]), search_games(conn, item[1], None, None)[:8]
        finally:
            conn.close()

    with ThreadPoolExecutor(6) as pool:
        matches = list(pool.map(match, todo))

    def save(appid, name, slots, steam, game):
        art = steam_grid_art(db, game, steam)
        saved = []
        for suffix in slots:
            result = render_first(art[suffix])
            if result:
                (grid / f"{appid}{suffix}{result[1]}").write_bytes(result[0])
                saved.append(STEAM_SLOTS[suffix])
        source = " + ".join((["Steam"] if steam else []) + ([f"{game[1]} [{game[2]}]"] if game else []))
        print(f"  {name}: " + (f"saved {', '.join(saved)}  ({source})" if saved else f"no art found ({source})"))

    print()
    unsure = []
    for (appid, name, slots), (steam, found) in zip(todo, matches):
        game = found[0] if found and found[0][5] >= SURE_MATCH else None
        if steam or game:
            save(appid, name, slots, steam, game)
        elif found:
            unsure.append((appid, name, slots, found))
        else:
            print(f"  {name}: no match")
    if unsure and not args.yes:
        print(f"\n{len(unsure)} {'was' if len(unsure) == 1 else 'were'}n't a sure match. Pick the right game:")
        for appid, name, slots, found in unsure:
            pick = choose(f'Which game is "{name}"?', [f"{g[1]}  [{platform_label(g)}]" for g in found]
                          + ["Skip this one", "Skip the rest"])
            if pick == len(found) + 1:
                break
            if pick < len(found):
                save(appid, name, slots, {}, found[pick])
    elif unsure:
        print(f"Skipped {len(unsure)} that weren't a sure match. Run without -y to pick by hand.")
    print("\nDone. Restart Steam to see the new art.")


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
    if not cli_folder:  # a one-off -o shouldn't change the folder the menus suggest
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
    p.add_argument("-t", "--type", choices=list(ART_TYPES),
                   help="box, clean (no logo), logo, clearlogo, icon, ach (achievements / trophies), or wallpaper")
    p.add_argument("-r", "--orient", choices=["portrait", "landscape"], help="key art orientation")
    p.add_argument("-q", "--quality", help="low, medium, high, all, or an exact size like 1920x1080")
    p.add_argument("-o", "--out", help="folder to save into")
    p.add_argument("-y", "--yes", action="store_true", help="take the top match and defaults, no prompts")
    p.add_argument("-w", "--wallpaper", action="store_true", help="set the picture as your desktop wallpaper")
    p.add_argument("--undo-wallpaper", action="store_true",
                   help="put back the wallpaper you had before fetchart changed it")
    p.add_argument("-f", "--folder", help="get art for every game in this folder, named after each ROM file "
                                          "(or posters for a folder of movies and shows)")
    p.add_argument("--steam", nargs="?", const="", metavar="STEAM_DIR",
                   help="add library art to the non-Steam games in Steam")
    p.add_argument("--update", action="store_true", help="re-download the game database")
    p.add_argument("--version", action="version", version=f"fetchart {__version__}")
    args = p.parse_args()
    if args.undo_wallpaper:
        return undo_wallpaper(load_config())
    if args.steam is not None:
        return steam_main(args, args.steam)
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
    if category == "steam":
        return steam_main(args, "")
    while True:
        if interactive:
            args.game = ask("\n" + CATEGORIES[category][1])
            if not args.game:
                return
        query = re.sub(r"[-_]+", " ", args.game).strip()
        try:
            if category in ("folder", "videos"):
                return folder_main(args, args.game, cfg, videos=category == "videos")
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
    if args.wallpaper and args.type not in ("clean", "logo"):
        args.type = "wallpaper"
    if args.type == "ach" and platform and platform not in ACH_SOURCES:
        sys.exit(f"fetchart can't find achievements for {platform} games.")

    found = search_games(db, query, platform, args.type)
    if args.type == "ach":
        found = [g for g in found if g[2] in ACH_SOURCES]
    groups = group_games(found, split_year(query)[1])
    if not groups:
        what = ART_TYPES[args.type].lower() if args.type else "game"
        where = f" on {platform}" if platform else ""
        raise NotFound(f'No {what} found for "{query}"{where}. Try a shorter or different name.')
    group = groups[choose("Which game?", [group_label(g) for g in groups], auto=args.yes)]
    gid, title, plat = group[choose(f"Which platform is {group[0][1]} on?",
                                    [platform_label(g) for g in group], auto=args.yes)][:3]

    kinds = [k for k in ART_TYPES if k != "ach" or plat in ACH_SOURCES]
    labels = [f"{ach_word(plat).title()} icons (pick any or all)" if k == "ach" else ART_TYPES[k] for k in kinds]
    wallpaper_flag = args.wallpaper
    while True:
        kind = args.type or kinds[choose("What art do you want?", labels, auto=args.yes)]
        orient = args.orient
        args.wallpaper = wallpaper_flag or kind == "wallpaper"
        if args.wallpaper:
            kind, orient = ("clean" if kind == "wallpaper" else kind), "landscape"
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
    tag = {"box": "box art", "clean": "key art", "logo": "key art (logo)", "clearlogo": "logo", "icon": "icon"}[kind]
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
    # Biggest first, but Japanese releases last: their logo and cover text is in Japanese.
    options.sort(key=lambda o: (o.source.endswith(", Japan"),
                                -(o.final_size()[0] * o.final_size()[1] if o.final_size() else 0)))
    if args.wallpaper:  # a 600 MB museum scan makes a poor wallpaper; 4K is plenty
        options = [o for o in options if not o.final_size() or o.final_size()[0] <= 3840] or options

    picked = pick_art(options, args.quality, args.yes, name_prefix)
    folder = pick_folder(args.out, cfg, args.yes)
    print()
    saved = []
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
        saved.append(dest)
        print(f"Saved {dest}  ({len(data) // 1024} KB)")
    if args.wallpaper and saved:
        if set_wallpaper(saved[0], cfg):
            print("It's your desktop wallpaper now. To put your old one back: fetchart --undo-wallpaper")
        else:
            print("Couldn't set the wallpaper on this system, but the picture is saved.")


# ---------------------------------------------------------------- wallpaper

def run_quiet(cmd):
    """A command's output, or None if it failed or isn't installed."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def wallpaper_state():
    """The desktop background right now, in the form apply_wallpaper() takes, or None if it can't be read."""
    if os.name == "nt":
        import ctypes
        import winreg
        buf = ctypes.create_unicode_buffer(1024)
        if not ctypes.windll.user32.SystemParametersInfoW(0x73, len(buf), buf, 0):  # SPI_GETDESKWALLPAPER
            return None
        state = {"path": buf.value}  # "" for a plain colour
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
                state["style"] = winreg.QueryValueEx(key, "WallpaperStyle")[0]
                state["tile"] = winreg.QueryValueEx(key, "TileWallpaper")[0]
        except OSError:
            pass
        return state
    if sys.platform == "darwin":
        path = run_quiet(["osascript", "-e", 'tell application "System Events" to get picture of current desktop'])
        return {"path": path} if path else None
    uri = run_quiet(["gsettings", "get", "org.gnome.desktop.background", "picture-uri"])
    if uri:
        dark = run_quiet(["gsettings", "get", "org.gnome.desktop.background", "picture-uri-dark"]) or uri
        return {"uri": uri.strip("'"), "uri_dark": dark.strip("'")}
    return None  # KDE and Xfce can be set but not read back


def apply_wallpaper(state):
    """Set the desktop background from a wallpaper_state()-style dict. Returns False if nothing worked."""
    if os.name == "nt":
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE) as key:
            for name, value in (("WallpaperStyle", state.get("style")), ("TileWallpaper", state.get("tile"))):
                if value is not None:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        # SPI_SETDESKWALLPAPER, saved to the profile and announced to running programs
        return bool(ctypes.windll.user32.SystemParametersInfoW(20, 0, state["path"], 3))
    commands = []
    if sys.platform == "darwin":
        commands.append(["osascript", "-e", 'tell application "System Events" to tell every desktop to '
                                            f'set picture to {json.dumps(state["path"])}'])
    else:
        if "uri" in state:
            commands += [["gsettings", "set", "org.gnome.desktop.background", "picture-uri", state["uri"]],
                         ["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", state["uri_dark"]]]
        if "path" in state:
            commands += [["plasma-apply-wallpaperimage", state["path"]],
                         ["xfconf-query", "-c", "xfce4-desktop", "-p",
                          "/backdrop/screen0/monitor0/workspace0/last-image", "-s", state["path"]]]
    return any([run_quiet(cmd) is not None for cmd in commands])


def wallpaper_file(state):
    """The picture file behind a wallpaper state, or None."""
    if state.get("uri", "").startswith("file:"):
        return Path(urllib.request.url2pathname(urllib.parse.urlparse(state["uri"]).path))
    return Path(state["path"]) if state.get("path") else None


def set_wallpaper(path, cfg):
    """Make a picture the desktop background, first remembering the current one for --undo-wallpaper."""
    path = Path(path).resolve()
    if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp"):  # Windows won't take .webp
        Image, _ = pil()
        jpg = path.with_suffix(".jpg")
        Image.open(path).convert("RGB").save(jpg, quality=95)
        path = jpg
    before = wallpaper_state()
    # Remember what was there, unless fetchart put it there: undo goes back to the wallpaper from before
    # fetchart, however many times -w was used since.
    if before and before != cfg.get("wallpaper_ours"):
        # Keep a copy of the picture. Windows shows it from a cache file that the new wallpaper replaces,
        # and the original may get moved or deleted.
        original = wallpaper_file(before)
        if original and original.is_file():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            backup = DATA_DIR / ("wallpaper before fetchart" + (original.suffix or ".jpg"))
            shutil.copyfile(original, backup)
            before["backup"] = str(backup)
        cfg["wallpaper_before"] = before
    if os.name == "nt":
        state = {"path": str(path), "style": "10", "tile": "0"}  # style 10 fills the screen
    elif sys.platform == "darwin":
        state = {"path": str(path)}
    else:
        state = {"uri": path.as_uri(), "uri_dark": path.as_uri(), "path": str(path)}
    if not apply_wallpaper(state):
        return False
    cfg["wallpaper_ours"] = wallpaper_state()
    save_config(cfg)
    return True


def undo_wallpaper(cfg):
    before = cfg.get("wallpaper_before")
    if not before:
        sys.exit("fetchart hasn't changed your wallpaper, so there's nothing to put back.")
    state = dict(before)
    backup = state.pop("backup", None)
    original = wallpaper_file(state)
    # Use the original file if it's still there, else the copy. Windows' cache file holds fetchart's picture now.
    if backup and Path(backup).is_file() and (original is None or not original.is_file()
                                              or original.name == "TranscodedWallpaper"):
        if "uri" in state:
            state["uri"] = state["uri_dark"] = Path(backup).as_uri()
        else:
            state["path"] = backup
    if not apply_wallpaper(state):
        sys.exit("Couldn't change the wallpaper back on this system.")
    cfg.pop("wallpaper_before")
    cfg.pop("wallpaper_ours", None)
    save_config(cfg)
    print("Your old wallpaper is back.")


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
