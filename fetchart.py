"""fetchart - find and download game art, posters, album covers and paintings from the command line.

Usage:
    fetchart                     menus for everything: category, name, platform, art, folder
    fetchart <game> [platform] [-t TYPE] [-r portrait|landscape] [-q QUALITY] [-o FOLDER] [-y] [-w]
    fetchart --folder DIR [platform] [-t TYPE] [-o FOLDER] [-y]   art for every ROM in a folder
    fetchart --folder DIR movie|tv [-y]                           posters for a folder of movies or shows
    fetchart --folder DIR [-y]                                    cover.jpg for every album in a music folder
    fetchart --folder DIR -t info                                 a gamelist.xml with every game's details
    fetchart --missing DIR                                        what has no art yet, then get it
    fetchart --watch DIR [-t TYPE]                                art for each new game as it arrives
    fetchart --retroarch [RETROARCH_DIR] [-y]                     thumbnails for RetroArch's playlists
    fetchart --steam [STEAM_DIR] [-y]                             Steam library art for non-Steam games
    fetchart --collage DIR [-w] [-r SHAPE] [--bg COLOR] [--sort name|year|random]   a box wall poster
    fetchart --list FILE [-t TYPE] [-o FOLDER]                    art for every name in a text file
    fetchart --daily [GAMES | PLATFORM | space | off] [--every 1h] a new wallpaper every day (or hour)
    fetchart --undo-wallpaper    put back the wallpaper from before -w
    fetchart --completion powershell|bash|zsh                     Tab completion for your shell
    fetchart --update            re-download the game database
    fetchart --upgrade           update fetchart itself

Art types:
    box        box cover art, rating and all         (LaunchBox box fronts, every region)
    edition    special edition covers                (Deluxe, Collector's, GOTY...: LaunchBox, Microsoft Store)
    back, box3d, disc, screenshot, flyer             (back covers, 3D boxes, discs and cartridges,
                                                      screenshots, old magazine ads: LaunchBox)
    clean      key art without the logo              (LaunchBox fanart backgrounds, Steam library hero)
    logo       key art with the logo                 (Steam capsules, LaunchBox banners/posters, or
                                                      fanart with the game's clear logo laid on top)
    clearlogo  just the logo, see-through background (Steam library logo, LaunchBox clear logos)
    icon       dashboard icon / tile                 (Xbox 360 marketplace tiles + banners, Microsoft Store
                                                      squares, Nintendo eShop icons, Steam + LaunchBox icons)
    ach        achievement / trophy icons, by name   (Xbox, PlayStation, Steam and RetroAchievements lists
                                                      from Exophase; Xbox 360 originals from Xbox Live)
    info       description, release date, developer, genre, rating (LaunchBox)

-w / --wallpaper saves the best landscape key art (or painting) and sets it as the desktop background.
-r picks the key art shape: landscape, portrait, phone, ultrawide (21:9) or superwide (32:9).

Also: pokemon, minecraft (a player's skin), anime, manga, book, app, card (pokemon-card, mtg, yugioh),
amiibo, podcast, artist and space work in place of a platform.

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

__version__ = "1.6.0"

UA = {"User-Agent": f"fetchart/{__version__} (+https://github.com/SamuelArther/fetchart)"}
LB_DUMP = "https://gamesdb.launchbox-app.com/Metadata.zip"
LB_IMAGES = "https://images.launchbox-app.com/"
STEAM_CDN = "https://shared.akamai.steamstatic.com/store_item_assets/"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "fetchart"
DB_PATH = DATA_DIR / "launchbox.db"
CONFIG = Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "fetchart" / "config.json"
DB_MAX_AGE = 30 * 86400

SCREENSHOT_TYPES = ("Screenshot - Gameplay", "Screenshot - Game Title", "Screenshot - Game Select",
                    "Screenshot - High Scores", "Screenshot - Game Over")
BACK_TYPES = ("Box - Back", "Box - Back - Reconstructed", "Fanart - Box - Back")
MEDIA_TYPES = ("Disc", "Cart - Front", "Cart - 3D", "Cart - Back", "Fanart - Disc", "Fanart - Cart - Front")
FLYER_TYPES = ("Advertisement Flyer - Front", "Advertisement Flyer - Back")
KEEP_TYPES = {"Box - Front", "Box - Front - Reconstructed", "Fanart - Box - Front", "Box - 3D",
              "Fanart - Background", "Clear Logo", "Banner", "Poster", "Square", "Icon",
              *SCREENSHOT_TYPES, *BACK_TYPES, *MEDIA_TYPES, *FLYER_TYPES}
DB_SCHEMA = 6  # bump when KEEP_TYPES or the table layout changes, to force a rebuild
NEEDED_TYPES = {
    "box": ("Box - Front", "Box - Front - Reconstructed", "Fanart - Box - Front"),
    "back": BACK_TYPES,
    "box3d": ("Box - 3D",),
    "disc": MEDIA_TYPES,
    "screenshot": SCREENSHOT_TYPES,
    "flyer": FLYER_TYPES,
    "clean": ("Fanart - Background",),
    "logo": ("Fanart - Background", "Banner", "Poster"),
    "clearlogo": ("Clear Logo",),
    "edition": (),  # special editions are found by name, and also on the Microsoft Store
    "icon": (),  # icons mostly come from console stores, so any game qualifies
    "ach": (),
    "info": (),
    "wallpaper": ("Fanart - Background",),
    None: (),  # art type not picked yet
}
ART_TYPES = {
    "box": "Box cover art (the retail cover, rating and all)",
    "edition": "Special edition covers (Deluxe, Gold, Collector's, Game of the Year...)",
    "back": "Back of the box",
    "box3d": "3D box (the whole box at an angle)",
    "disc": "Disc or cartridge",
    "screenshot": "Screenshots (gameplay and title screen)",
    "flyer": "Old magazine / store ad flyers",
    "clean": "Key art, no logo",
    "logo": "Key art, with logo",
    "clearlogo": "Clear logo (just the logo, see-through background)",
    "icon": "Dashboard icon / tile (the square on the console menu)",
    "ach": "Achievement icons (pick any or all)",
    "info": "Game info (description, release date, developer, genre, rating)",
    "wallpaper": "Desktop wallpaper (the best key art, set as your background)",
}
# Shapes for key art: menu text -> crop (None = as is, True = 2:3 portrait, else width / height).
ORIENTS = {
    "landscape": ("Landscape", None),
    "portrait": ("Portrait", True),
    "phone": ("Phone wallpaper (tall)", 9 / 19.5),
    "ultrawide": ("Ultrawide monitor (21:9)", 21 / 9),
    "superwide": ("Super ultrawide / two monitors (32:9)", 32 / 9),
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


BIG_DOWNLOAD = 8 << 20  # bytes; bigger single downloads get a progress bar


def http_get(url, headers=None, timeout=30, progress=False):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        if not (progress and total >= BIG_DOWNLOAD):
            return r.read()
        bar, chunks, done = Progress(total, "  Downloading", megabytes=True), [], 0
        while chunk := r.read(1 << 18):
            chunks.append(chunk)
            done += len(chunk)
            bar.update(done)
        bar.close()
        return b"".join(chunks)


class Progress:
    """A progress bar on the last console line, redrawn in place. Lines printed through log() go above it.
    When the output isn't a console it draws nothing and log() just prints."""

    def __init__(self, total, label="", megabytes=False):
        self.total, self.label, self.megabytes = max(total, 1), label, megabytes
        self.done, self.extra, self.lock = 0, "", threading.Lock()
        self.live = sys.stdout.isatty()
        if self.live:
            enable_ansi()

    def _draw(self):
        if not self.live:
            return
        filled = round(24 * min(self.done, self.total) / self.total)
        count = (f"{self.done / 2 ** 20:.0f} of {self.total / 2 ** 20:.0f} MB" if self.megabytes
                 else f"{self.done} of {self.total}")
        line = f"{self.label} [{'#' * filled}{'-' * (24 - filled)}] {count}" + (f"  {self.extra}" if self.extra else "")
        sys.stdout.write("\r\x1b[2K" + line[:shutil.get_terminal_size().columns - 1])
        sys.stdout.flush()

    def update(self, done=None, extra=None, step=0):
        with self.lock:
            self.done = (self.done if done is None else done) + step
            self.extra = self.extra if extra is None else extra
            self._draw()

    def log(self, line):
        with self.lock:
            if self.live:
                sys.stdout.write("\r\x1b[2K")
            print(line)
            self._draw()

    def close(self):
        with self.lock:
            if self.live:
                sys.stdout.write("\r\x1b[2K")
                sys.stdout.flush()


def get_json(url, params):
    return json.loads(http_get(url + "?" + urllib.parse.urlencode(params), {"Accept": "application/json"}))


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


def arrow_menu(title, rows, default=0, multi=False, preview=None, sheet=None):
    """Menu driven by the arrow keys or the mouse (typing a number jumps to it). Returns the picked row
    index, or with multi=True the ticked indexes (just the highlighted row if none are ticked)."""
    enable_ansi()
    n, cols = len(rows), shutil.get_terminal_size().columns
    height = min(n, max(3, shutil.get_terminal_size().lines - 5))  # rows on screen; never more than there are
    width = len(str(n))
    cur, top, ticked, typed, note, drawn = default, 0, set(), "", "", 0
    print()
    sys.stdout.write("\x1b[?25l")  # hide the cursor while the menu is up
    try:
        with menu_input() as keyboard:
            clicks = keyboard.cursor_row() is not None
            keys = ("Up/Down" + (" or click" if clicks else "") + ", Enter "
                    + ("done, Space tick, A all" if multi else "pick")
                    + (", S contact sheet" if sheet else "") + (", P preview in browser" if preview else "")
                    + ", Esc quit")
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
                elif key.lower() == "s" and sheet:
                    sys.stdout.write(f"\x1b[{drawn}F\x1b[J" if drawn else "")
                    note = sheet()[1]
                    drawn = 0  # the menu starts again below the sheet
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


def choose(title, options, default=1, auto=False, allow_all=False, preview=None, sheet=None):
    """Menu over display strings. Returns the picked index, or -1 for 'all'.
    preview, if given, is called when the user asks to see the options."""
    if len(options) == 1 or auto:
        return default - 1
    if arrow_keys_ok():
        i = arrow_menu(title, options + (["All of them"] if allow_all else []), default - 1, preview=preview,
                       sheet=sheet)
        return -1 if i == len(options) else i
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    if allow_all:
        print("  a) all of them")
    hint = (f"1-{len(options)}" + (", a" if allow_all else "") + (", s = contact sheet" if sheet else "")
            + (", p = preview" if preview else ""))
    while True:
        answer = ask(f"Pick [{hint}, Enter = {default}]: ", str(default))
        if sheet and answer.lower() == "s":
            print("  " + sheet()[1])
            continue
        if preview and answer.lower() == "p":
            preview()
            print("  Preview opened in your browser.")
            continue
        if allow_all and answer.lower() in ("a", "all"):
            return -1
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("  Not an option, try again.")


def choose_many(title, options, preview=None, sheet=None):
    """Menu where several can be picked. Typed: '3', '1,4,9', '10-20' or 'a' for all. Returns indexes."""
    if arrow_keys_ok():
        return arrow_menu(title, options, multi=True, preview=preview, sheet=sheet)
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        answer = ask("Pick one or more (e.g. 3 or 1,4,9 or 10-20" + (", s = contact sheet" if sheet else "")
                     + (", p = preview" if preview else "") + ") [Enter = all]: ", "a").lower().replace(" ", "")
        if sheet and answer == "s":
            print("  " + sheet()[1])
            continue
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
        bar = Progress(int(r.headers.get("Content-Length") or 0), "  Downloading", megabytes=True)
        while chunk := r.read(1 << 20):
            f.write(chunk)
            bar.update(step=len(chunk))
        bar.close()
    print("Indexing (about 20 seconds)...")

    tmp = DB_PATH.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT, platform TEXT, votes INTEGER, year INTEGER);
        CREATE TABLE names (id INTEGER, name TEXT, norm TEXT, abbr TEXT);
        CREATE TABLE images (id INTEGER, type TEXT, region TEXT, file TEXT);
        CREATE TABLE info (id INTEGER PRIMARY KEY, released TEXT, overview BLOB, developer TEXT, publisher TEXT,
                           genres TEXT, players TEXT, rating REAL, esrb TEXT);
    """)
    games, names, images, info = [], [], [], []

    def flush():
        db.executemany("INSERT OR IGNORE INTO games VALUES (?,?,?,?,?)", games)
        db.executemany("INSERT INTO names VALUES (?,?,?,?)", names)
        db.executemany("INSERT INTO images VALUES (?,?,?,?)", images)
        db.executemany("INSERT OR IGNORE INTO info VALUES (?,?,?,?,?,?,?,?,?)", info)
        games.clear(), names.clear(), images.clear(), info.clear()

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
                    overview = (el.findtext("Overview") or "").strip()
                    rating = el.findtext("CommunityRating")
                    info.append((int(gid), (el.findtext("ReleaseDate") or "")[:10] or None,
                                 zlib.compress(overview.encode()) if overview else None,  # about half the size
                                 el.findtext("Developer"), el.findtext("Publisher"), el.findtext("Genres"),
                                 el.findtext("MaxPlayers"), float(rating) if rating else None, el.findtext("ESRB")))
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
            if len(images) + len(names) + len(info) > 50000:
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


def game_info(db, gid):
    """A game's details from LaunchBox: {released, overview, developer, publisher, genres, players, rating, esrb}."""
    row = db.execute("SELECT released, overview, developer, publisher, genres, players, rating, esrb FROM info "
                     "WHERE id = ?", (gid,)).fetchone()
    if not row:
        return {}
    keys = ("released", "overview", "developer", "publisher", "genres", "players", "rating", "esrb")
    info = dict(zip(keys, row))
    info["overview"] = zlib.decompress(info["overview"]).decode() if info["overview"] else None
    return info


def info_text(title, plat, info):
    """A game's details as readable text."""
    lines = [f"{title} [{plat}]", ""]
    for label, key in (("Released", "released"), ("Developer", "developer"), ("Publisher", "publisher"),
                       ("Genre", "genres"), ("Players", "players"), ("Rating", "rating"), ("ESRB", "esrb")):
        value = info.get(key)
        if value:
            lines.append(f"{label + ':':<11}{f'{value:.1f} out of 5 (LaunchBox players)' if key == 'rating' else value}")
    if info.get("overview"):
        lines += ["", info["overview"]]
    return "\n".join(lines)


EDITION_WORDS = re.compile(r"\b(edition|goty|game of the year|deluxe|gold|ultimate|complete|definitive|premium|"
                           r"collector'?s|limited|special|legendary|anniversary|essentials|greatest hits|platinum|"
                           r"players'? choice|director'?s cut|steelbook|bundle)\b", re.I)


def base_title(title):
    """A title without its edition: "Halo 3: Legendary Edition" -> "Halo 3"."""
    m = EDITION_WORDS.search(title)
    if not m:
        return title
    cut = title[:m.start()]
    if m.group(1).lower() == "edition":  # "Halo 3 Mythic Edition": drop the word before it too, unless a number
        cut = re.sub(r"\b[^\W\d]+'?s?\s*$", "", cut)
    cut = re.sub(r"[\s:\-(]*\bthe\s*$", "", cut, flags=re.I)  # "GTA IV: The Complete Edition"
    return cut.strip(" :-(") or title


def edition_covers(db, gid, title, platform):
    """Box art for a game's special editions: the LaunchBox entries named "<game> ... Edition" (this platform
    first) and the edition listings on the Microsoft Store. [Art]"""
    base = base_title(title)
    rows = db.execute("SELECT DISTINCT g.id, g.name, g.platform FROM names n JOIN games g ON g.id = n.id "
                      "WHERE n.norm LIKE ?", (norm(base) + "%",)).fetchall()
    editions = [r for r in rows if EDITION_WORDS.search(r[1][len(base):] if r[1].lower().startswith(base.lower())
                                                        else r[1]) and len(norm(r[1]).split()) - len(norm(base).split()) <= 6]
    editions.sort(key=lambda r: (r[2] != platform, r[0] == gid, r[1]))
    out = []
    for eid, name, plat in editions[:12]:
        by_type = images_by_type(db, eid)
        for image_type in ("Box - Front", "Box - Front - Reconstructed", "Box - 3D"):
            for region, url in by_type.get(image_type, []):
                kind = {"Box - 3D": ", 3D box", "Box - Front - Reconstructed": ", rebuilt"}.get(image_type, "")
                short = name[len(base):].strip(" :-") if name.lower().startswith(base.lower()) else name
                out.append(Art(url, f"{short or name} [{plat}]{kind}" + (f", {region}" if region else "")))
    out += ms_store_editions(base)
    return out


def ms_store_editions(base):
    """Cover art of a game's editions sold on the Microsoft Store (Xbox and PC): [Art]."""
    try:
        sets = get_json("https://www.microsoft.com/msstoreapiprod/api/autosuggest",
                        {"market": "en-us", "clientId": "7F27B536-CF6B-4C65-8638-A0F8CBDFCA65",
                         "sources": "DCatAll-Products", "filter": "+ClientType:StoreWeb", "counts": 20,
                         "query": base}).get("ResultSets", [])
        words = set(norm(base).split())
        ids = []
        for suggest in (s for rs in sets for s in rs.get("Suggests", [])):
            meta = {m["Key"]: m["Value"] for m in suggest.get("Metas", [])}
            name = suggest.get("Title", "")
            if (meta.get("ProductType") == "Games" and meta.get("BigCatalogId") and words <= set(norm(name).split())
                    and EDITION_WORDS.search(name)):
                ids.append(meta["BigCatalogId"])
        if not ids:
            return []
        products = get_json("https://displaycatalog.mp.microsoft.com/v7.0/products",
                            {"bigIds": ",".join(ids[:8]), "market": "US", "languages": "en-US"})["Products"]
    except (urllib.error.URLError, ValueError, KeyError):
        return []
    out = []
    for product in products:
        props = product["LocalizedProperties"][0]
        for image in props["Images"]:
            label = {"Poster": "cover", "BoxArt": "square tile"}.get(image["ImagePurpose"])
            if label:
                out.append(Art("https:" + image["Uri"], f"{props['ProductTitle']} (Microsoft Store {label})"))
    return out


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
            f"{title} {word} icons", [(e[0], data_uri(e[2]) if e[2] else e[3], None) for e in entries], small=True),
            sheet=lambda: show_sheet([Art(e[3], e[0], data=e[2], pixelated=True) for e in entries]))]
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

    def __init__(self, url, source, crop=False, logo=None, scale=None, preview=None, data=None, pixelated=False):
        self.url, self.source, self.crop, self.logo, self.scale = url, source, crop, logo, scale
        self.preview = preview  # a smaller copy to show in the browser preview, when the original is huge
        self.data = data  # the image itself, for pictures fetchart makes (url is then None)
        self.pixelated = pixelated  # scale pixel art up with hard edges
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
    if art.data:
        art.size, art.fingerprint = Image.open(io.BytesIO(art.data)).size, hash(art.data)
        return
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

    if kind == "edition":
        return edition_covers(db, gid, title, platform)
    if kind == "box":
        for region, url in by_type.get("Box - Front", []):
            out.append(Art(url, f"box scan, {region or 'unknown region'}"))
        for region, url in by_type.get("Box - Front - Reconstructed", []):
            out.append(Art(url, f"rebuilt box, {region or 'unknown region'}"))
        for _, url in by_type.get("Fanart - Box - Front", []):
            out.append(Art(url, "fan-made box"))
        return out
    labelled = {  # kind -> [(LaunchBox type, label)]
        "back": [("Box - Back", "back of the box"), ("Box - Back - Reconstructed", "rebuilt back"),
                 ("Fanart - Box - Back", "fan-made back")],
        "box3d": [("Box - 3D", "3D box")],
        "disc": [("Disc", "disc"), ("Cart - Front", "cartridge"), ("Cart - 3D", "3D cartridge"),
                 ("Cart - Back", "back of the cartridge"), ("Fanart - Disc", "fan-made disc"),
                 ("Fanart - Cart - Front", "fan-made cartridge")],
        "screenshot": [("Screenshot - Gameplay", "gameplay"), ("Screenshot - Game Title", "title screen"),
                       ("Screenshot - Game Select", "select screen"), ("Screenshot - High Scores", "high scores"),
                       ("Screenshot - Game Over", "game over")],
        "flyer": [("Advertisement Flyer - Front", "ad flyer"), ("Advertisement Flyer - Back", "back of the ad flyer")],
    }
    if kind in labelled:
        for image_type, label in labelled[kind]:
            images = by_type.get(image_type, [])
            for i, (region, url) in enumerate(images, 1):
                number = f" #{i}" if len(images) > 1 and image_type.startswith("Screenshot") else ""
                out.append(Art(url, label + number + (f", {region}" if region else "")))
        return out

    steam = steam_assets(title)
    if kind == "clearlogo":
        for url in steam_logos(steam):
            out.append(Art(url, "Steam logo (official)"))
        for region, url in by_type.get("Clear Logo", []):
            out.append(Art(url, f"clear logo, {region}" if region else "clear logo"))
        return out
    crop = ORIENTS.get(orient or "landscape", ORIENTS["landscape"])[1]
    cut = "" if not crop else ", cropped" if crop is True else f", cropped to {orient}"
    if kind == "clean":
        for url in steam_urls(steam, ["library_hero_2x", "library_hero"]):
            out.append(Art(url, "Steam hero (official)" + cut, crop=crop))
        for i, url in enumerate(fanart, 1):
            out.append(Art(url, f"fanart #{i}" + cut, crop=crop))
        return out

    # kind == "logo"
    if crop and crop is not True and crop < 1:  # phone: the tall official covers, cut down
        for url in steam_urls(steam, ["library_capsule_2x", "library_capsule"]):
            out.append(Art(url, "Steam cover (official)" + cut, crop=crop))
        for _, url in by_type.get("Poster", []):
            out.append(Art(url, "poster" + cut, crop=crop))
    elif portrait:
        for url in steam_urls(steam, ["library_capsule_2x", "library_capsule"]):
            out.append(Art(url, "Steam cover (official)"))
        for _, url in by_type.get("Poster", []):
            out.append(Art(url, "poster"))
    elif not crop:
        for url in steam_urls(steam, ["header_2x", "header", "main_capsule"]):
            out.append(Art(url, "Steam header (official)"))
        for _, url in by_type.get("Banner", []):
            out.append(Art(url, "banner"))
    if logos:
        for i, url in enumerate(fanart, 1):
            out.append(Art(url, f"fanart #{i} + logo" + cut, crop=crop, logo=logos[0]))
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


def render(art, progress=False):
    """Download one option and apply its crop / logo overlay. Returns (bytes, extension).
    progress shows a bar for big downloads (only from the main thread, one at a time)."""
    data = art.data or http_get(art.url, timeout=90, progress=progress)
    ext = image_ext(data)
    if not art.crop and not art.logo and not art.scale:
        return data, ext
    Image, ImageFilter = pil()
    if art.scale:
        buf = io.BytesIO()
        resample = Image.NEAREST if art.pixelated else Image.LANCZOS
        Image.open(io.BytesIO(data)).convert("RGBA").resize((art.scale, art.scale), resample).save(buf, "PNG")
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
         "painting": "painting", "paintings": "painting", "art": "painting", "artwork": "painting",
         "pokemon": "pokemon", "minecraft": "minecraft", "mc": "minecraft",
         "skin": "minecraft", "anime": "anime", "manga": "manga", "book": "book", "books": "book",
         "app": "app", "apps": "app", "ios": "app",
         "card": "cards", "cards": "cards", "tcg": "cards", "tradingcard": "cards", "tradingcards": "cards",
         "pokemoncard": "pokemoncard", "pokemoncards": "pokemoncard", "ptcg": "pokemoncard",
         "mtg": "mtg", "magic": "mtg", "mtgcard": "mtg", "yugioh": "yugioh", "ygo": "yugioh",
         "amiibo": "amiibo", "podcast": "podcast", "podcasts": "podcast",
         "artist": "artist", "band": "artist", "singer": "artist", "rapper": "artist",
         "space": "space", "nasa": "space", "apod": "space", "astronomy": "space"}
# Top menu when fetchart is run with no arguments: key -> (menu text, name prompt)
CATEGORIES = {
    "game": ("Video game", "Game name: "),
    "screen": ("Movie or TV show", "Movie or show name: "),
    "album": ("Music (album cover)", "Album (artist + album name works best): "),
    "painting": ("Painting / real art", "Painting name (add the artist if you know it): "),
    "pokemon": ("Pokémon", "Pokémon name: "),
    "minecraft": ("Minecraft skin (by player name)", "Minecraft player name: "),
    "anime": ("Anime or manga", "Anime or manga name: "),
    "book": ("Book cover", "Book title (add the author if you know it): "),
    "app": ("App icon (App Store, today's or any old one)", "App name: "),
    "cards": ("Trading card (Pokémon, Magic, Yu-Gi-Oh!)", "Card name: "),
    "amiibo": ("amiibo (picture and info)", "amiibo or character name: "),
    "podcast": ("Podcast cover", "Podcast name: "),
    "artist": ("Music artist or band photo", "Artist or band name: "),
    "space": ("Space picture (NASA)", "What in space? (like pillars of creation; Enter = NASA's picture of the day): "),
    "folder": ("A whole folder of games (art for every ROM)", "Folder with your games (paste its path): "),
    "videos": ("A whole folder of movies or TV shows (posters for Plex, Jellyfin, Kodi)",
               "Folder with your movies or shows (paste its path): "),
    "musicfolder": ("A whole folder of music (cover.jpg for every album)", "Folder with your music (paste its path): "),
    "collage": ("Box wall: one big poster of every cover in a folder",
                "Folder with the covers (or your ROMs) (paste its path): "),
    "retroarch": ("RetroArch: box art, screenshots and logos for my playlists", None),
    "missing": ("Check a folder for games, movies or albums with no art yet", "Folder to check (paste its path): "),
    "watch": ("Watch a ROM folder: new games get their art by themselves", "Folder to watch (paste its path): "),
    "steam": ("Steam: art for my non-Steam games", None),
    "daily": ("Daily wallpaper: a new game (or space picture) every day, or every hour",
              'Games, or a platform (like "halo 3, celeste, elden ring", "n64" or "space"; Enter = popular games): '),
    "list": ("A list of names in a text file (one per line)", "Text file (paste its path): "),
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


# ---------------------------------------------------------------- Minecraft skins

def mc_part(skin, box, scale):
    """One rectangle of a skin texture, scaled up with hard pixel edges."""
    Image, _ = pil()
    part = skin.crop(box)
    return part.resize((part.width * scale, part.height * scale), Image.NEAREST)


def mc_body(skin, slim, scale=20):
    """The whole player from the front, hat, jacket and sleeves included."""
    Image, _ = pil()
    arm = 3 if slim else 4
    legacy = skin.height == 32  # pre-1.8 skins have one arm and one leg, mirrored for the other side

    def mirrored(box):
        return skin.crop(box).transpose(Image.FLIP_LEFT_RIGHT)

    canvas = Image.new("RGBA", (16, 32), (0, 0, 0, 0))
    layers = [  # (texture box or image, position); the second layer of each part sits on top
        ((8, 8, 16, 16), (4, 0)), ((20, 20, 28, 32), (4, 8)),
        ((44, 20, 44 + arm, 32), (4 - arm, 8)), ((4, 20, 8, 32), (4, 20)),
        (mirrored((44, 20, 44 + arm, 32)) if legacy else (36, 52, 36 + arm, 64), (12, 8)),
        (mirrored((4, 20, 8, 32)) if legacy else (20, 52, 24, 64), (8, 20)),
        ((40, 8, 48, 16), (4, 0)),
    ]
    if not legacy:
        layers += [((20, 36, 28, 48), (4, 8)), ((44, 36, 44 + arm, 48), (4 - arm, 8)),
                   ((52, 52, 52 + arm, 64), (12, 8)), ((4, 36, 8, 48), (4, 20)), ((4, 52, 8, 64), (8, 20))]
    for box, pos in layers:
        part = box if isinstance(box, Image.Image) else skin.crop(box)
        canvas.alpha_composite(part, pos)
    return canvas.resize((16 * scale, 32 * scale), Image.NEAREST)


def iso_cube(top, left, right, size):
    """A cube seen from above at an angle, three square textures on its faces: (image, (top, left)).
    left is the face toward the viewer's left, right the other side; edges are size pixels long."""
    Image, _ = pil()
    from PIL import ImageEnhance
    a, b = size * math.cos(math.pi / 6), size / 2
    w, h = math.ceil(2 * a), math.ceil(2 * b + size)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    t0, t2, t3 = (a, 0), (a, 2 * b), (0, b)  # the top face's back, front and left corners
    for tex, origin, e1, e2, light in ((top, t0, (a, b), (-a, b), 1.0),
                                       (left, t3, (a, b), (0, size), 0.85),
                                       (right, t2, (a, -b), (0, size), 0.7)):
        n = tex.width
        det = e1[0] * e2[1] - e1[1] * e2[0]
        # The affine map takes each canvas point to the texture point: invert [e1 e2], scaled to the texture.
        i11, i12, i21, i22 = e2[1] / det * n, -e2[0] / det * n, -e1[1] / det * n, e1[0] / det * n
        face = tex.transform((w, h), Image.AFFINE,
                             (i11, i12, -(i11 * origin[0] + i12 * origin[1]),
                              i21, i22, -(i21 * origin[0] + i22 * origin[1])), Image.NEAREST)
        if light < 1:
            alpha = face.getchannel("A")
            face = ImageEnhance.Brightness(face).enhance(light)
            face.putalpha(alpha)
        canvas.alpha_composite(face)
    return canvas


def mc_head(skin, size=256):
    """The head as a 3D cube, the hat layer floating just outside it."""
    Image, _ = pil()
    k = size // 8

    def faces(x, y):  # the head's top, front and left side, from the texture block starting at (x, y)
        return (mc_part(skin, (x + 8, y, x + 16, y + 8), k), mc_part(skin, (x + 8, y + 8, x + 16, y + 16), k),
                mc_part(skin, (x + 16, y + 8, x + 24, y + 16), k))

    hat = iso_cube(*faces(32, 0), round(size * 1.125))
    head = iso_cube(*faces(0, 0), size)
    out = Image.new("RGBA", hat.size, (0, 0, 0, 0))
    out.alpha_composite(head, ((hat.width - head.width) // 2, (hat.height - head.height) // 2))
    out.alpha_composite(hat)
    return out


def mc_skin(raw):
    """Open a skin the way Minecraft reads it: the base layers are always solid, and an old 64x32 skin whose
    hat layer has no see-through pixel at all (most were saved without transparency) has no hat."""
    Image, _ = pil()
    skin = Image.open(io.BytesIO(raw)).convert("RGBA")
    solid = [(0, 0, 32, 16), (0, 16, 64, 32)] + ([(16, 48, 48, 64)] if skin.height == 64 else [])
    for box in solid:
        part = skin.crop(box)
        part.putalpha(255)
        skin.paste(part, box[:2])
    if skin.height == 32 and skin.crop((32, 0, 64, 16)).getchannel("A").getextrema()[0] == 255:
        skin.paste((0, 0, 0, 0), (32, 0, 64, 16))
    return skin


def png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def minecraft_main(args, query, cfg):
    """A player's skin, cape, face, full body and a 3D head, from Mojang's servers."""
    name = re.sub(r"\s+", "", query)
    print(f'Looking up the Minecraft player "{name}"...')
    try:
        profile = get_json(f"https://api.mojang.com/users/profiles/minecraft/{urllib.parse.quote(name)}", {})
        session = get_json(f"https://sessionserver.mojang.com/session/minecraft/profile/{profile['id']}", {})
        textures = json.loads(base64.b64decode(session["properties"][0]["value"]))["textures"]
    except urllib.error.HTTPError as e:
        if e.code in (204, 404):
            raise NotFound(f'No Minecraft player is called "{name}".')
        sys.exit(f"Network error: {e}")
    except (urllib.error.URLError, ValueError, KeyError) as e:
        sys.exit(f"Network error: {e}")
    if "SKIN" not in textures:
        raise NotFound(f"{profile['name']} uses a default skin, so there's no skin to download.")
    Image, _ = pil()
    raw = http_get(textures["SKIN"]["url"].replace("http://", "https://"))
    skin = mc_skin(raw)
    slim =textures["SKIN"].get("metadata", {}).get("model") == "slim"
    face = mc_part(skin, (8, 8, 16, 16), 64)
    face.alpha_composite(mc_part(skin, (40, 8, 48, 16), 64))
    options = [Art(None, "3D head", data=png_bytes(mc_head(skin))),
               Art(None, "full body, front" + (" (slim arms)" if slim else ""), data=png_bytes(mc_body(skin, slim))),
               Art(None, "face", data=png_bytes(face)),
               Art(None, "skin file (load it into Minecraft or a skin editor)", data=raw)]
    if "CAPE" in textures:
        cape = Image.open(io.BytesIO(http_get(textures["CAPE"]["url"].replace("http://", "https://")))).convert("RGBA")
        options += [Art(None, "cape", data=png_bytes(mc_part(cape, (1, 1, 11, 17), 32))),
                    Art(None, "cape file", data=png_bytes(cape))]
    finish(options, f"{profile['name']} - Minecraft", args, cfg)


# ---------------------------------------------------------------- Pokémon, anime, books and apps

def post_json(url, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {**UA, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def pokemon_names():
    """Every Pokémon and form PokéAPI knows ("pikachu", "charizard-mega-x"), cached for a month."""
    path = DATA_DIR / "pokemon.json"
    if path.exists() and time.time() - path.stat().st_mtime < DB_MAX_AGE:
        return json.loads(path.read_text(encoding="utf-8"))
    names = [p["name"] for p in get_json("https://pokeapi.co/api/v2/pokemon", {"limit": 5000})["results"]]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(names), encoding="utf-8")
    return names


def pokemon_main(args, query, cfg):
    """Official artwork, HOME renders, animated sprites and every game's sprite, shiny ones too."""
    try:
        names = pokemon_names()
    except (urllib.error.URLError, ValueError) as e:
        sys.exit(f"Network error: {e}")
    q = norm(query)
    ranked = sorted(names, key=lambda n: (norm(n) != q, not norm(n).startswith(q), -score(query, n.replace("-", " "))))
    ranked = [n for n in ranked if norm(n).startswith(q) or score(query, n.replace("-", " ")) >= 0.6][:12]
    if not ranked:
        raise NotFound(f'No Pokémon called "{query}".')
    name = ranked[choose("Which Pokémon?", [n.replace("-", " ").title() for n in ranked], auto=args.yes)]
    data = get_json(f"https://pokeapi.co/api/v2/pokemon/{name}", {})
    sprites, title = data["sprites"], name.replace("-", " ").title()
    print(f"Finding art for {title}...")
    other = sprites.get("other", {})
    options = []
    for key, label in (("official-artwork", "official artwork"), ("home", "Pokémon HOME render")):
        for shiny in ("", "_shiny"):
            url = other.get(key, {}).get("front" + (shiny or "_default"))
            if url:
                options.append(Art(url, label + (", shiny" if shiny else "")))
    for shiny in ("", "_shiny"):
        url = other.get("showdown", {}).get("front" + (shiny or "_default"))
        if url:
            options.append(Art(url, "animated (Showdown)" + (", shiny" if shiny else "")))
    for generation, games in sprites.get("versions", {}).items():
        gen = generation.replace("generation-", "Gen ").replace("-", " ").upper().replace("GEN", "Gen")
        for game, pics in games.items():
            for shiny in ("", "_shiny"):
                url = pics.get("front" + (shiny or "_default"))
                if url and not url.endswith(".gif"):
                    options.append(Art(url, f"{gen} sprite, {game.replace('-', ' ')}" + (", shiny" if shiny else ""),
                                       pixelated=True))
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(probe_size, options))
    for art in options:  # old games' sprites are tiny: 4x with hard pixel edges, still below the official art
        if art.pixelated and art.size and art.size[0] <= 128:
            art.scale = art.size[0] * 4
            art.source += ", scaled up"
    finish(options, f"{title} - Pokémon", args, cfg, require_size=True)


def anime_main(args, query, cfg, kind=None):
    """Covers and banners from AniList. kind is ANIME, MANGA or None for both."""
    print(f'Searching AniList for "{query}"...')
    gql = ("query($s:String,$t:MediaType){Page(perPage:12){media(search:$s,type:$t,sort:SEARCH_MATCH)"
           "{type format seasonYear startDate{year} title{romaji english} coverImage{extraLarge} bannerImage}}}")
    try:
        variables = {"s": query, **({"t": kind} if kind else {})}  # a null type matches nothing
        found = post_json("https://graphql.anilist.co", {"query": gql, "variables": variables})
        media = found["data"]["Page"]["media"]
    except (urllib.error.URLError, ValueError, KeyError) as e:
        sys.exit(f"Network error: {e}")
    if not media:
        raise NotFound(f'Nothing on AniList for "{query}".')

    def label(m):
        title = m["title"]["english"] or m["title"]["romaji"]
        year = m.get("seasonYear") or (m.get("startDate") or {}).get("year") or "?"
        return f"{title}  ({year})  [{m['type'].lower()}, {(m.get('format') or '').replace('_', ' ').lower()}]"

    m = media[choose("Which one?", [label(m) for m in media], auto=args.yes)]
    title = m["title"]["english"] or m["title"]["romaji"]
    options = [Art(m["coverImage"]["extraLarge"], "AniList cover")]
    if m.get("bannerImage"):
        options.append(Art(m["bannerImage"], "AniList banner"))
    finish(options, f"{title} - {m['type'].lower()}", args, cfg, require_size=True)


def book_main(args, query, cfg):
    """Book covers from Open Library: the main cover and other editions' covers."""
    print(f'Searching Open Library for "{query}"...')
    try:
        docs = get_json("https://openlibrary.org/search.json",
                        {"q": query, "limit": 12, "fields": "key,title,author_name,first_publish_year,cover_i"})["docs"]
    except (urllib.error.URLError, ValueError, KeyError) as e:
        sys.exit(f"Network error: {e}")
    docs = [d for d in docs if d.get("cover_i")]
    if not docs:
        raise NotFound(f'No book with a cover found for "{query}". Try the title and author.')
    doc = docs[choose("Which book?", [f"{d['title']}  -  {', '.join(d.get('author_name', ['?'])[:2])}"
                                      f"  ({d.get('first_publish_year', '?')})" for d in docs], auto=args.yes)]
    print(f"Finding covers for {doc['title']}...")
    ids = [doc["cover_i"]]
    try:
        ids += get_json(f"https://openlibrary.org{doc['key']}.json", {}).get("covers", [])
        for edition in get_json(f"https://openlibrary.org{doc['key']}/editions.json", {"limit": 40}).get("entries", []):
            ids += edition.get("covers", [])
    except (urllib.error.URLError, ValueError):
        pass
    ids = [i for i in dict.fromkeys(ids) if i and i > 0][:16]
    options = [Art(f"https://covers.openlibrary.org/b/id/{i}-L.jpg", "cover" if n == 0 else f"edition cover #{n}")
               for n, i in enumerate(ids)]
    author = (doc.get("author_name") or [""])[0]
    finish(options, f"{doc['title']}" + (f" - {author}" if author else "") + " - book cover", args, cfg,
           require_size=True)


def app_main(args, query, cfg):
    """App Store icons (the 1024px original) and screenshots."""
    print(f'Searching the App Store for "{query}"...')
    try:
        apps = get_json("https://itunes.apple.com/search", {"term": query, "entity": "software", "limit": 12,
                                                            "country": "US"}).get("results", [])
    except (urllib.error.URLError, ValueError) as e:
        sys.exit(f"Network error: {e}")
    if not apps:
        raise NotFound(f'No app found for "{query}".')
    app = apps[choose("Which app?", [f"{a['trackName']}  -  {a.get('sellerName', '?')}" for a in apps], auto=args.yes)]
    icon = app["artworkUrl512"].rsplit("/", 1)[0]
    options = [Art(icon + "/1024x1024bb.png", "App Store icon (PNG)"), Art(icon + "/1024x1024bb.jpg", "App Store icon (JPG)")]
    history = args.type == "icon" or (not args.yes and choose("What do you want?", [
        "The icon and screenshots it has now",
        "Every icon it has ever had (from old App Store pages, takes a minute)"]) == 1)
    if history:
        print(f"Looking through old App Store pages for {app['trackName']}'s icons...")
        options = app_icon_history(app, options[0].url)
        if len(options) == 1:
            print("The Wayback Machine has no old copies of its App Store page, so only today's icon is here.")
        return finish(options, f"{app['trackName']} - app icon", args, cfg, require_size=True, keep_order=True)
    for n, url in enumerate(app.get("screenshotUrls", []) + app.get("ipadScreenshotUrls", []), 1):
        # The listed copies are thumbnails; asking for a huge size gets the original.
        options.append(Art(re.sub(r"/\d+x\d+bb\.(png|jpg)$", "/9999x9999bb.png", url), f"screenshot #{n}"))
    finish(options, f"{app['trackName']} - app", args, cfg, require_size=True)


def mzstatic_originals(url):
    """Full-size copies to try for an App Store image, from any thumbnail address Apple has used:
    today's ".../AppIcon.png/1200x630wa.png" and 2010-era ".../us/r1000/046/Purple/../mzi.abc.175x175-75.jpg"
    both still have their original behind Apple's image server."""
    m = re.match(r"https?://[^/]*mzstatic\.com/image/thumb/(.+)/[^/]+$", url)
    if m:
        return [f"https://is1-ssl.mzstatic.com/image/thumb/{m[1]}/1024x1024bb.png"]
    m = re.match(r"https?://[^/]*(?:mzstatic\.com|phobos\.apple\.com)/us/r\d+/(?:\d+/)?(.+?)\.\d+x\d+(?:-\d+)?\.(?:jpe?g|png)$", url)
    if m:
        return [f"https://is1-ssl.mzstatic.com/image/thumb/{m[1]}.{ext}/1024x1024bb.png" for ext in ("png", "jpg")]
    return []


def icon_on_page(page, name):
    """The app's own icon on an archived App Store page (not the "customers also bought" ones), or None."""
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page) or \
        re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', page)
    if m and "/image/thumb/" in m[1]:  # not the generic App Store logo some pages used
        return html.unescape(m[1])
    for tag in re.findall(r"<img[^>]*>", page):
        src = re.search(r'src="([^"]+)"', tag)
        if src and 'class="artwork"' in tag and ("175x175" in src[1] or f'alt="{html.escape(name)}"' in tag):
            return src[1]
    return None


def app_icon_history(app, current):
    """Every icon an app has had, oldest first, from the Wayback Machine's copies of its App Store page
    (one a year: the archive only allows about 15 page loads a minute). Returns Art options labelled with
    the years each icon was up."""
    app_id, name = app["trackId"], app["trackName"]
    slug = (re.search(r"/app/([^/]+)/id\d+", app.get("trackViewUrl", "")) or [None, ""])[1]
    pages = [f"apps.apple.com/us/app/{slug}/id{app_id}", f"itunes.apple.com/us/app/{slug}/id{app_id}",
             f"itunes.apple.com/app/{slug}/id{app_id}", f"itunes.apple.com/us/app/id{app_id}"]
    snapshots = {}  # year -> (timestamp, archived address)
    for page in pages:
        try:
            rows = get_json("https://web.archive.org/cdx/search/cdx", {
                "url": page, "output": "json", "fl": "timestamp,original", "filter": "statuscode:200",
                "collapse": "timestamp:4"})
        except (urllib.error.URLError, ValueError):
            continue
        for ts, original in rows[1:]:
            snapshots.setdefault(ts[:4], (ts, original))
    years = sorted(snapshots)
    if not years:
        return []

    def look(year):
        ts, original = snapshots[year]
        for wait in (0, 30):  # a refused connection means "slow down"
            time.sleep(wait)
            try:
                page = http_get(f"https://web.archive.org/web/{ts}id_/{original}", timeout=45)
                return icon_on_page(page.decode("utf-8", "ignore"), name)
            except urllib.error.HTTPError:
                return None
            except (urllib.error.URLError, OSError):
                continue
        return None

    found = []  # [first year, last year, [full-size addresses to try], archived thumbnail address]
    bar = Progress(len(years), "  Old App Store pages")
    for n, year in enumerate(years):
        if n:
            time.sleep(4)
        thumb = look(year)
        bar.update(step=1)
        originals = mzstatic_originals(thumb) if thumb else []
        if not originals:
            continue
        if found and found[-1][2][0] == originals[0]:
            found[-1][1] = year
        else:
            found.append([year, year, originals, f"https://web.archive.org/web/{snapshots[year][0]}im_/{thumb}"])
    bar.close()
    found.append(["now", "now", [current], current])

    Image, _ = pil()
    out, last_look = [], None
    for first, last, originals, archived in found:
        for url in originals + [archived]:  # the archive's own copy is small, so it comes last
            try:
                data = http_get(url, timeout=60)
                look_alike = Image.open(io.BytesIO(data)).convert("RGB").resize((16, 16)).tobytes()
                break
            except (urllib.error.URLError, OSError):
                data = None
        if not data:
            continue
        # Apple re-uploads the same icon with every app update; only a picture that looks different is a new icon.
        if last_look and sum(abs(a - b) for a, b in zip(look_alike, last_look)) / len(look_alike) < 6:
            prev = out[-1]
            prev.last = last
            prev.source = f"icon, {prev.first}" + (f" to {last}" if last != prev.first else "")
            continue
        art = Art(url, "icon now" if first == "now" else f"icon, {first}" + (f" to {last}" if last != first else ""),
                  data=data)
        art.first, art.last = first, last
        out.append(art)
        last_look = look_alike
    return out


# ---------------------------------------------------------------- trading cards, amiibo, podcasts, artists, space

CARD_GAMES = {"pokemon": "Pokémon TCG", "mtg": "Magic: The Gathering", "yugioh": "Yu-Gi-Oh!"}


def card_names(game, query):
    """Card names matching a search in one card game, best first."""
    if game == "pokemon":
        cards = get_json("https://api.tcgdex.net/v2/en/cards", {"name": query})
        names = {c["name"] for c in cards if c.get("image")}
    elif game == "mtg":
        try:
            names = {c["name"] for c in get_json("https://api.scryfall.com/cards/search",
                                                  {"q": query, "unique": "cards"})["data"]}
        except urllib.error.HTTPError as e:
            if e.code == 404:  # Scryfall's way of saying nothing matched
                return []
            raise
    else:
        try:
            names = {c["name"] for c in get_json("https://db.ygoprodeck.com/api/v7/cardinfo.php",
                                                  {"fname": query})["data"]}
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return []
            raise
    return sorted(names, key=lambda n: (n.lower() != query.lower(), norm(n) != norm(query), -score(query, n)))[:12]


def card_prints(game, name):
    """Every printing of one card: [Art]."""
    if game == "pokemon":
        cards = [c for c in get_json("https://api.tcgdex.net/v2/en/cards", {"name": name})
                 if c["name"] == name and c.get("image")][:60]

        def detail(c):
            try:
                return get_json(f"https://api.tcgdex.net/v2/en/cards/{c['id']}", {})
            except (urllib.error.URLError, ValueError):
                return c
        with ThreadPoolExecutor(8) as pool:
            cards = list(pool.map(detail, cards))
        cards.sort(key=lambda c: (c.get("set") or {}).get("releaseDate") or "", reverse=True)
        return [Art(c["image"] + "/high.png", f"{(c.get('set') or {}).get('name', c['id'])} #{c['localId']}"
                    + (f", {c['rarity']}" if c.get("rarity") else "")) for c in cards]
    if game == "mtg":
        out = []
        found = get_json("https://api.scryfall.com/cards/search",
                         {"q": f'!"{name}"', "unique": "prints", "order": "released", "include_extras": "true"})
        for c in found["data"][:60]:
            where = f"{c['set_name']} ({c['released_at'][:4]}) #{c['collector_number']}"
            faces = [c] if c.get("image_uris") else c.get("card_faces", [])
            for face in faces:
                if face.get("image_uris"):
                    side = f", {face['name']}" if face is not c else ""
                    out.append(Art(face["image_uris"]["png"], where + side))
            if len(out) == len(faces) and faces and faces[0].get("image_uris", {}).get("art_crop"):
                out.append(Art(faces[0]["image_uris"]["art_crop"], f"just the art, {c['set_name']}"))
        return out
    card = next(c for c in get_json("https://db.ygoprodeck.com/api/v7/cardinfo.php", {"name": name})["data"])
    out = []
    for n, image in enumerate(card.get("card_images", []), 1):
        alt = f", alternate art #{n - 1}" if n > 1 else ""
        out += [Art(image["image_url"], "card" + alt), Art(image["image_url_cropped"], "just the art" + alt)]
    return out


def cards_main(args, query, cfg, game=None):
    """Trading card scans: Pokémon (TCGdex), Magic (Scryfall) or Yu-Gi-Oh! (YGOPRODeck)."""
    games = [game] if game else list(CARD_GAMES)
    print(f'Searching {" and ".join(CARD_GAMES[g] for g in games)} cards for "{query}"...')
    found = []
    for g in games:
        try:
            found += [(g, name) for name in card_names(g, query)]
        except (urllib.error.URLError, ValueError, KeyError) as e:
            print(f"  {CARD_GAMES[g]} didn't answer ({e}).")
    if not found:
        raise NotFound(f'No card called "{query}".')
    found.sort(key=lambda f: (f[1].lower() != query.lower(), norm(f[1]) != norm(query), -score(query, f[1])))
    g, name = found[choose("Which card?", [f"{n}  [{CARD_GAMES[g]}]" for g, n in found], auto=args.yes)]
    print(f"Finding every printing of {name}...")
    try:
        options = card_prints(g, name)
    except (urllib.error.URLError, ValueError, KeyError, StopIteration) as e:
        sys.exit(f"Network error: {e}")
    finish(options, f"{name} - {CARD_GAMES[g]} card", args, cfg, require_size=True, keep_order=True)


AMIIBO_API = "https://www.amiiboapi.org/api/amiibo/"


def amiibo_info(a):
    """An amiibo's details as text: series, release dates and what it unlocks in each game."""
    regions = {"na": "North America", "eu": "Europe", "jp": "Japan", "au": "Australia"}
    lines = [a["name"], "", f"Character:     {a.get('character', '?')}", f"amiibo series: {a.get('amiiboSeries', '?')}",
             f"Game series:   {a.get('gameSeries', '?')}", f"Type:          {a.get('type', '?')}", "Released:"]
    lines += [f"  {regions[r]}: {(a.get('release') or {}).get(r) or 'not released there'}" for r in regions]
    lines.append(f"amiibo ID:     {a.get('head', '')}{a.get('tail', '')}")
    consoles = [("gamesSwitch2", "Switch 2"), ("gamesSwitch", "Switch"), ("gamesWiiU", "Wii U"), ("games3DS", "3DS")]
    uses = [(console, g) for key, console in consoles for g in a.get(key) or []]
    lines += ["", f"Works with {len(uses)} game{'s' * (len(uses) != 1)}:"]
    for console, g in uses:
        lines.append(f"  {g['gameName']} ({console})")
        for use in g.get("amiiboUsage") or []:
            lines.append(f"    - {use['Usage']}" + (" (saves data to the amiibo)" if use.get("write") else ""))
    return "\n".join(lines)


def amiibo_main(args, query, cfg):
    """An amiibo's picture, plus its info: series, release dates and what it does in every game."""
    print(f'Searching amiibo for "{query}"...')
    found = []
    for key in ("name", "character", "gameseries", "amiiboSeries"):
        try:
            found = get_json(AMIIBO_API, {key: query, "showusage": ""}).get("amiibo", [])
        except urllib.error.HTTPError as e:
            if e.code != 404:
                sys.exit(f"Network error: {e}")
        except (urllib.error.URLError, ValueError) as e:
            sys.exit(f"Network error: {e}")
        if found:
            break
    if not found:
        raise NotFound(f'No amiibo called "{query}".')
    found.sort(key=lambda a: (norm(a["name"]) != norm(query), a.get("type") != "Figure", -score(query, a["name"])))
    found = found[:40]
    a = found[choose("Which amiibo?", [f"{a['name']}  [{a.get('amiiboSeries')} {a.get('type', '').lower()}]"
                                       for a in found], auto=args.yes)]
    info = amiibo_info(a)
    print("\n" + "\n".join(info.splitlines()[:14]) + ("\n  ..." if len(info.splitlines()) > 14 else ""))
    options = [Art(a["image"], "amiibo picture")]
    prefix = f"{a['name']} - amiibo"
    saved = finish(options, prefix, args, cfg, require_size=True)
    if saved:
        dest = unique_path(saved[0].parent / (safe_filename(prefix) + " info.txt"))
        dest.write_text(info + "\n", encoding="utf-8")
        print(f"Saved {dest}  (everything it does in every game)")


def podcast_main(args, query, cfg):
    """Podcast cover art at Apple's full 3000px size."""
    print(f'Searching Apple Podcasts for "{query}"...')
    try:
        shows = get_json("https://itunes.apple.com/search", {"term": query, "media": "podcast", "limit": 12,
                                                             "country": "US"}).get("results", [])
    except (urllib.error.URLError, ValueError) as e:
        sys.exit(f"Network error: {e}")
    shows = [s for s in shows if s.get("artworkUrl600")]
    if not shows:
        raise NotFound(f'No podcast found for "{query}".')
    show = shows[choose("Which podcast?", [f"{s['collectionName']}  -  {s.get('artistName', '?')}" for s in shows],
                        auto=args.yes)]
    art = show["artworkUrl600"].rsplit("/", 1)[0]
    options = [Art(art + "/3000x3000bb.jpg", "podcast cover (JPG)"), Art(art + "/3000x3000bb.png", "podcast cover (PNG)")]
    finish(options, f"{show['collectionName']} - podcast", args, cfg, require_size=True)


def artist_main(args, query, cfg):
    """Photos of a music artist or band: Deezer's artist picture and the one on Wikipedia."""
    print(f'Searching for the artist "{query}"...')
    try:
        artists = get_json("https://api.deezer.com/search/artist", {"q": query, "limit": 10}).get("data", [])
    except (urllib.error.URLError, ValueError) as e:
        sys.exit(f"Network error: {e}")
    artists = [a for a in artists if a.get("picture_xl") and "/artist//" not in a["picture_xl"]]
    if not artists:
        raise NotFound(f'No artist found for "{query}".')
    artist = artists[choose("Which artist?", [f"{a['name']}  ({a.get('nb_fan', 0):,} fans, {a.get('nb_album', 0)} albums)"
                                              for a in artists], auto=args.yes)]
    options = [Art(artist["picture_xl"].replace("1000x1000", "1900x1900"), "artist photo (Deezer)")]
    wiki = wiki_infobox_image(f"{artist['name']} band OR singer OR musician OR rapper", artist["name"])
    if wiki:
        options.append(Art(wiki, "photo from Wikipedia"))
    finish(options, f"{artist['name']} - artist", args, cfg, require_size=True)


APOD_FEED = "https://science.nasa.gov/feed/apod-basic/"


def apod_items():
    """NASA's Astronomy Picture of the Day for the last month, newest first: [(date, title, image URL)].
    Days that are videos are left out."""
    feed = http_get(APOD_FEED).decode("utf-8", "ignore")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", feed, re.S):
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        hd = re.search(r"<apod:hdurl>(.*?)</apod:hdurl>", item, re.S)
        day = re.search(r"apod-(\d{4})-([a-z]+)-(\d{1,2})-", item)
        if not (title and hd):
            continue
        url = html.unescape(hd[1].strip())
        # The feed links a resized copy; the same path under content/dam is the original.
        url = re.sub(r"/dynamicimage/assets/science/", "/content/dam/science/", url).split("?")[0]
        if not re.search(r"\.(jpe?g|png)$", url, re.I):
            continue
        date = (time.strftime("%Y-%m-%d", time.strptime(f"{day[1]} {day[2]} {day[3]}", "%Y %B %d")) if day else "")
        out.append((date, html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", title[1])).strip(), url))
    return out


def nasa_search(query):
    """NASA Image and Video Library photos for a search: [(title, date, nasa_id)]."""
    items = get_json("https://images-api.nasa.gov/search", {"q": query, "media_type": "image", "page_size": 40}
                     )["collection"]["items"]
    out = []
    for item in items:
        d = item["data"][0]
        out.append((d.get("title", d["nasa_id"]), (d.get("date_created") or "")[:10], d["nasa_id"]))
    return sorted(out, key=lambda f: -score(query, f[0]))[:20]


def space_main(args, query, cfg):
    """Space pictures from NASA: the Astronomy Picture of the Day, or a search of NASA's image library."""
    today = norm(query) in ("", "today", "apod", "space", "nasa", "latest", "picture of the day")
    try:
        apod = apod_items()
    except (urllib.error.URLError, OSError) as e:
        apod = []
        if today:
            sys.exit(f"Couldn't reach NASA: {e}")
    if today:
        picks = [("apod", a) for a in apod]
    else:
        print(f'Searching NASA\'s image library for "{query}"...')
        try:
            found = nasa_search(query)
        except (urllib.error.URLError, ValueError, KeyError) as e:
            sys.exit(f"Network error: {e}")
        picks = [("apod", a) for a in apod if score(query, a[1]) >= 0.6] + [("nasa", f) for f in found]
    if not picks:
        raise NotFound(f'NASA has no pictures for "{query}".')
    labels = [f"{a[1]}  (picture of the day, {a[0]})" if kind == "apod" else f"{a[0]}  ({a[1]})" for kind, a in picks]
    kind, pick = picks[choose("Which picture?", labels, auto=args.yes)]
    if kind == "apod":
        options, title = [Art(pick[2], "NASA picture of the day")], pick[1]
    else:
        title = pick[0]
        try:
            files = [i["href"].replace("http://", "https://")
                     for i in get_json(f"https://images-api.nasa.gov/asset/{urllib.parse.quote(pick[2])}", {}
                                       )["collection"]["items"]]
        except (urllib.error.URLError, ValueError, KeyError) as e:
            sys.exit(f"Network error: {e}")
        names = {"orig": "original", "large": "large", "medium": "medium"}
        options = [Art(f, f"NASA image library, {names[m[1]]}") for f in files
                   if (m := re.search(r"~(orig|large|medium)\.(jpe?g|png)$", f, re.I))]
    finish(options, f"{title} - NASA", args, cfg, require_size=True)


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
# Art type -> subfolder, named the way ES-DE names its media folders where it has one.
FOLDER_SUBDIRS = {"box": "covers", "back": "backcovers", "box3d": "3dboxes", "disc": "physicalmedia",
                  "screenshot": "screenshots", "clean": "fanart", "logo": "keyart", "clearlogo": "marquees",
                  "icon": "icons", "flyer": "flyers"}
# Folders frontends keep their own art and videos in, which aren't games
MEDIA_DIRS = {"images", "videos", "media", "manuals", "downloaded_images", "downloaded_media", "snap", "snaps", "wheel",
              "boxart", "thumbnails"}
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


def game_files(folder):
    """The game files in a folder, or the game folders when each game has its own (PS3, Wii U and PC dumps)."""
    files = sorted(f for f in folder.iterdir() if f.is_file() and not f.name.startswith("."))
    cues = {f.stem.lower() for f in files if f.suffix.lower() == ".cue"}
    games = []
    for f in files:
        ext = f.suffix.lower()
        if ext in NOT_GAMES or f.stem.lower().startswith("readme") or f.name.lower().startswith("gamelist."):
            continue
        if ext == ".bin" and (f.stem.lower() in cues or re.search(r"\(track \d+\)", f.stem, re.I)):
            continue  # the .cue sheet stands for the whole disc
        games.append(f)
    if not games:
        games = [d for d in sorted(folder.iterdir()) if d.is_dir() and not d.name.startswith(".")
                 and d.name not in FOLDER_SUBDIRS.values() and d.name not in MEDIA_DIRS]
    return games


def game_stem(path):
    return path.stem if path.is_file() else path.name


def scan_folder(folder):
    """The games in a folder: [(file name without extension, platform implied by the extension or None)]."""
    return [(game_stem(f), EXT_PLATFORMS.get(f.suffix.lower()) if f.is_file() else None) for f in game_files(folder)]


def match_roms(db, games, platform, kind):
    """Look every ROM up (quick, all local): [(stem, best matches)]. Each name and system is searched once."""
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
    return plan


def folder_platform(db, args, folder, games):
    """The system a folder's games are for: the one given, else the folder's name ("N64", "PS2"), else asked
    (None = work it out from each file's extension)."""
    platform = resolve_platform(db, args.platform) if args.platform else match_platform(db, folder.name, 0.8)
    if not platform and not all(p for _, p in games) and not args.yes:
        answer = ask("\nWhich system are these games for? (n64, snes, ps2, gc, ... Enter = work it out per game): ")
        platform = resolve_platform(db, answer) if answer else None
    return platform


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


def folder_main(args, folder_text, cfg, mode=None):
    """Art for every game in a folder, saved under each ROM's own file name, as emulator frontends expect.
    Folders of movies, shows or music (mode "videos" / "musicfolder", or worked out) get posters or covers."""
    folder = Path(os.path.expandvars(os.path.expanduser(folder_text.strip().strip('"').strip("'"))))
    if not folder.is_dir():
        raise NotFound(f"There's no folder at {folder}.")
    media = media_word(args.platform)
    guess = not args.platform and mode in (None, "folder")  # work out what kind of folder it is
    if mode == "videos" or media in ("movie", "tv") or (guess and looks_like_videos(folder)):
        return video_folder_main(args, folder, media)
    if (mode == "musicfolder" or media == "album"
            or (guess and looks_like_music(folder) and not any(p for _, p in scan_folder(folder)))):
        return music_folder_main(args, folder)
    if args.type and args.type not in FOLDER_SUBDIRS and args.type != "info":
        sys.exit(f"-t {args.type} is one game at a time. For folders use: {', '.join(FOLDER_SUBDIRS)}")
    games = scan_folder(folder)
    if not games:
        raise NotFound(f"No games in {folder}.")
    db = open_index(args.update)
    platform = folder_platform(db, args, folder, games)
    systems = sorted({platform} if platform else {p for _, p in games if p})
    print(f"\nFound {len(games)} games in {folder}" + (f" ({', '.join(systems)})" if systems else "") + ".")

    kinds = list(FOLDER_SUBDIRS) + ["info"]
    labels = [ART_TYPES[k] for k in FOLDER_SUBDIRS] + [GAMELIST_LABEL]
    kind = args.type or kinds[choose("What do you want for each game?", labels, auto=args.yes)]
    if kind == "info":
        return gamelist_main(args, folder, db, match_roms(db, games, platform, None))
    orient = pick_orient(args) if kind in ("clean", "logo") else args.orient
    default = folder / FOLDER_SUBDIRS[kind]
    out = args.out or (str(default) if args.yes else ask(f"\nSave the art to which folder? [Enter = {default}]: ",
                                                          str(default)))
    out = Path(os.path.expandvars(os.path.expanduser(out.strip().strip('"').strip("'"))))
    out.mkdir(parents=True, exist_ok=True)
    have = {f.stem for f in out.iterdir() if f.is_file()}
    print()

    # Match every file first, then download the sure ones in parallel.
    plan = match_roms(db, games, platform, kind)

    total, width = len(plan), len(str(len(plan)))
    counts = {"saved": 0, "had it": 0, "unsure": 0, "no match": 0, "failed": 0}
    unsure, lock, cache = [], threading.Lock(), {}
    bar = Progress(total, "Getting art")

    def report(n, status, stem, game=None):
        counts[status] += 1
        bar.log(f"[{n:>{width}}/{total}] {status:<8} {stem}" + (f"  ->  {game[1]} [{game[2]}]" if game else ""))
        bar.update(step=1, extra=f"{counts['saved']} saved")

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
    bar.close()

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


# ---------------------------------------------------------------- game info for emulator frontends

# gamelist.xml tag -> the art folders to point it at, best first (Batocera, RetroBat and Pegasus show these).
GAMELIST_MEDIA = {"image": ("screenshot", "box"), "thumbnail": ("box", "box3d"), "marquee": ("clearlogo",),
                  "fanart": ("clean",), "boxback": ("back",)}
GAMELIST_LABEL = "Game info: a gamelist.xml with descriptions, dates and ratings (ES-DE, Batocera, RetroBat, Pegasus)"


def gamelist_fields(db, folder, stem, game):
    """The gamelist.xml fields for one game."""
    info = game_info(db, game[0])
    released = (info.get("released") or "").replace("-", "")
    fields = {"name": game[1], "desc": info.get("overview"),
              "rating": f"{info['rating'] / 5:.2f}" if info.get("rating") else None,
              "releasedate": f"{released}T000000" if len(released) == 8 else None,
              "developer": info.get("developer"), "publisher": info.get("publisher"),
              "genre": (info.get("genres") or "").replace("; ", ", ") or None, "players": info.get("players")}
    for tag, kinds in GAMELIST_MEDIA.items():
        for kind in kinds:
            sub = folder / FOLDER_SUBDIRS[kind]
            art = next((f for f in sub.glob(glob_escape(stem) + ".*") if f.stem == stem), None) if sub.is_dir() else None
            if art:
                fields[tag] = f"./{sub.name}/{art.name}"
                break
    return fields


def glob_escape(text):
    return re.sub(r"([\[\]*?])", r"[\1]", text)


def gamelist_path(path_text):
    """A gamelist <path> in one form, for comparing: "./Foo.z64" and "foo.z64" are the same game."""
    return (path_text or "").strip().replace("\\", "/").removeprefix("./").lower()


def update_gamelist(path, entries):
    """Add or update games in a gamelist.xml, keeping everything else in it (play counts, favourites, games
    fetchart doesn't know). entries: [("./file name", {tag: text})]. Returns how many games were new."""
    root = None
    if path.exists():
        try:
            root = ET.parse(path).getroot()
            shutil.copyfile(path, path.with_name(path.name + ".bak"))
        except (ET.ParseError, OSError):
            root = None
    if root is None:
        root = ET.Element("gameList")
    games = {gamelist_path(g.findtext("path")): g for g in root.findall("game")}
    new = 0
    for rel, fields in entries:
        game = games.get(gamelist_path(rel))
        if game is None:
            game, new = ET.SubElement(root, "game"), new + 1
            ET.SubElement(game, "path").text = rel
            games[gamelist_path(rel)] = game
        for tag, value in fields.items():
            if value in (None, ""):
                continue
            el = game.find(tag)
            if el is None:
                el = ET.SubElement(game, tag)
            el.text = str(value)
    if hasattr(ET, "indent"):  # Python 3.9+
        ET.indent(root, "\t")
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return new


def esde_gamelist(folder):
    """Where ES-DE keeps this system's gamelist, if ES-DE is installed: ES-DE/gamelists/<system folder>/."""
    home = Path(os.environ.get("ESDE_APPDATA_DIR") or Path.home() / "ES-DE")
    return home / "gamelists" / folder.name / "gamelist.xml" if (home / "gamelists").is_dir() else None


def gamelist_main(args, folder, db, plan):
    """Write (or update) gamelist.xml with every game's name, description, release date, developer, publisher,
    genre, players and rating, plus the art fetchart saved for it."""
    files = {game_stem(f): f for f in game_files(folder)}
    picked, unsure = [], []
    for stem, found in plan:
        if found and found[0][5] >= SURE_MATCH:
            picked.append((stem, found[0]))
        elif found:
            unsure.append((stem, found))
    if unsure and not args.yes:
        print(f"\n{len(unsure)} {'was' if len(unsure) == 1 else 'were'}n't a sure match. Pick the right game for each:")
        for i, (stem, found) in enumerate(unsure, 1):
            pick = choose(f'({i}/{len(unsure)}) Which game is "{stem}"?',
                          [f"{g[1]}  [{platform_label(g)}]" for g in found] + ["Skip this one", "Skip the rest"])
            if pick == len(found) + 1:
                break
            if pick < len(found):
                picked.append((stem, found[pick]))
    entries = [(f"./{files[stem].name}", gamelist_fields(db, folder, stem, game)) for stem, game in picked
               if stem in files]
    if not entries:
        raise NotFound("None of the games could be matched, so there's nothing to write.")
    dest = folder / "gamelist.xml"
    new = update_gamelist(dest, entries)
    skipped = len(plan) - len(entries)
    print(f"\nWrote {dest}: {len(entries)} games ({new} new to it)" + (f", {skipped} left out (no sure match)"
                                                                        if skipped else "") + ".")
    esde = esde_gamelist(folder)
    if esde:
        update_gamelist(esde, entries)
        print(f"ES-DE reads its own copy, so it's in {esde} too.")
    print("Batocera, RetroBat, Recalbox and Pegasus read it from the ROM folder. Restart the frontend to see it.")


# ---------------------------------------------------------------- RetroArch thumbnails

# RetroArch's thumbnail folders -> the LaunchBox images that go in them, best first
RETROARCH_SLOTS = {"Named_Boxarts": ("Box - Front", "Box - Front - Reconstructed", "Fanart - Box - Front"),
                   "Named_Snaps": ("Screenshot - Gameplay",), "Named_Titles": ("Screenshot - Game Title",),
                   "Named_Logos": ("Clear Logo",)}
RETROARCH_LABELS = {"Named_Boxarts": "box art", "Named_Snaps": "screenshots", "Named_Titles": "title screens",
                    "Named_Logos": "logos"}
# libretro system names that don't fuzzy-match their LaunchBox name
LIBRETRO_SYSTEMS = {
    "Nintendo - Family Computer Disk System": "Nintendo Famicom Disk System",
    "Sega - Master System - Mark III": "Sega Master System", "Sega - Mega Drive - Genesis": "Sega Genesis",
    "Sega - Mega-CD - Sega CD": "Sega CD", "Sega - 32X": "Sega 32X",
    "Sony - PlayStation": "Sony Playstation", "Sony - PlayStation 2": "Sony Playstation 2",
    "Sony - PlayStation 3": "Sony Playstation 3", "Sony - PlayStation Portable": "Sony PSP",
    "Sony - PlayStation Vita": "Sony Playstation Vita",
    "NEC - PC Engine - TurboGrafx 16": "NEC TurboGrafx-16", "NEC - PC Engine CD - TurboGrafx-CD": "NEC TurboGrafx-CD",
    "NEC - PC Engine SuperGrafx": "NEC SuperGrafx", "SNK - Neo Geo": "SNK Neo Geo AES",
    "SNK - Neo Geo CD": "SNK Neo Geo CD", "FBNeo - Arcade Games": "Arcade", "MAME": "Arcade", "DOS": "MS-DOS",
    "Bandai - WonderSwan": "WonderSwan", "Bandai - WonderSwan Color": "WonderSwan Color",
    "Microsoft - MSX": "Microsoft MSX", "Microsoft - MSX2": "Microsoft MSX2",
}
PLAYLIST_SKIP = {"content_history", "content_favorites", "content_music_history", "content_video_history",
                 "content_image_history", "builtin_image_viewer", "builtin_video_player"}


def find_retroarch():
    """RetroArch's folder (the one with retroarch.cfg), or None."""
    home = Path.home()
    if os.name == "nt":
        places = [Path(os.environ.get("APPDATA", "")) / "RetroArch", Path("C:/RetroArch-Win64"), Path("C:/RetroArch"),
                  home / "scoop" / "apps" / "retroarch" / "current",
                  Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "RetroArch"]
        steam = find_steam()
        if steam:
            places.append(steam / "steamapps" / "common" / "RetroArch")
    elif sys.platform == "darwin":
        places = [home / "Library" / "Application Support" / "RetroArch"]
    else:
        places = [home / ".config" / "retroarch", home / ".var" / "app" / "org.libretro.RetroArch" / "config" / "retroarch",
                  home / "snap" / "retroarch" / "current" / ".config" / "retroarch"]
    return next((p for p in places if (p / "retroarch.cfg").is_file()), None)


def retroarch_dirs(root):
    """(playlist folder, thumbnails folder) from retroarch.cfg."""
    cfg = {}
    for line in (root / "retroarch.cfg").read_text(encoding="utf-8", errors="ignore").splitlines():
        key, eq, value = line.partition("=")
        if eq:
            cfg[key.strip()] = value.strip().strip('"')

    def where(key, default):
        value = cfg.get(key, "default")
        if value in ("", "default"):
            return root / default
        if value.startswith(":"):  # relative to RetroArch's own folder
            return root / value[1:].lstrip("\\/")
        return Path(os.path.expanduser(value))
    return where("playlist_directory", "playlists"), where("thumbnails_directory", "thumbnails")


def read_playlist(path):
    """A RetroArch playlist: [(label, system)]. Reads the JSON format and the old six-lines-per-game one."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        items = [(i.get("label") or Path(i.get("path", "")).stem, i.get("db_name") or path.name)
                 for i in json.loads(text).get("items", [])]
    except ValueError:
        lines = text.splitlines()
        items = [(lines[i + 1] or Path(lines[i]).stem, lines[i + 5] if i + 5 < len(lines) else path.name)
                 for i in range(0, len(lines) - 1, 6)]
    return [(label, Path(system).stem) for label, system in items if label]


def thumbnail_name(label):
    """RetroArch's file name for a game's thumbnail: these characters become "_"."""
    return re.sub(r'[&*/:`<>?\\|"]', "_", label) + ".png"


def libretro_platform(db, system):
    if system in LIBRETRO_SYSTEMS:
        return LIBRETRO_SYSTEMS[system]
    parts = [p.strip() for p in system.split(" - ")]
    for guess in ([" ".join(parts[:2])] if len(parts) > 1 else []) + parts[1:] + parts[:1]:
        platform = match_platform(db, guess, 0.6)
        if platform:
            return platform
    return None


def png_of(data):
    """Image bytes as PNG (RetroArch only shows .png thumbnails)."""
    if data.startswith(b"\x89PNG"):
        return data
    Image, _ = pil()
    return png_bytes(Image.open(io.BytesIO(data)).convert("RGBA"))


def retroarch_main(args, root_text):
    """Fill RetroArch's thumbnail folders (box art, screenshots, title screens, logos) for every game in its
    playlists, so they show in RetroArch's menus. Thumbnails already there are never replaced."""
    root = Path(os.path.expandvars(os.path.expanduser(root_text.strip().strip('"')))) if root_text else find_retroarch()
    if not root or not (root / "retroarch.cfg").is_file():
        sys.exit('Couldn\'t find RetroArch. Point fetchart at its folder: fetchart --retroarch "D:\\RetroArch"')
    playlists_dir, thumbs = retroarch_dirs(root)
    playlists = sorted(p for p in playlists_dir.glob("*.lpl") if p.stem not in PLAYLIST_SKIP)
    if not playlists:
        sys.exit(f"RetroArch has no playlists in {playlists_dir} yet. Scan your games in RetroArch first "
                 "(Import Content > Scan Directory), then run this again.")
    slots = list(RETROARCH_SLOTS)
    if args.type == "box":
        slots = ["Named_Boxarts"]
    elif not args.yes:
        slots = [slots[i] for i in choose_many("Which thumbnails?", [RETROARCH_LABELS[s] for s in slots])]

    todo = []  # (label, system, [missing slots])
    for playlist in playlists:
        for label, system in read_playlist(playlist):
            missing = [s for s in slots if not (thumbs / system / s / thumbnail_name(label)).exists()]
            if missing:
                todo.append((label, system, missing))
    systems = sorted({t[1] for t in todo})
    print(f"\nRetroArch at {root}: {len(playlists)} playlists, {len(todo)} games missing thumbnails.")
    if not todo:
        return
    if not args.yes and len(systems) > 1:
        picked = choose_many("Which playlists?", [f"{s}  ({sum(t[1] == s for t in todo)} games)" for s in systems])
        systems = [systems[i] for i in picked]
        todo = [t for t in todo if t[1] in systems]
    db = open_index(args.update)
    platforms = {s: libretro_platform(db, s) for s in systems}

    plan = match_roms(db, [(label, platforms[system]) for label, system, _ in todo], None, None)
    counts = {"saved": 0, "no match": 0, "unsure": 0, "failed": 0}
    bar, lock = Progress(len(todo), "Getting thumbnails"), threading.Lock()

    def save(item, game):
        label, system, missing = item
        conn = sqlite3.connect(DB_PATH)
        try:
            by_type = images_by_type(conn, game[0])
        finally:
            conn.close()
        region, done = rom_region(label), []
        for slot in missing:
            urls = [(r, u) for t in RETROARCH_SLOTS[slot] for r, u in by_type.get(t, [])]
            urls.sort(key=lambda ru: bool(region) and ru[0] != region)
            for _, url in urls:
                try:
                    data = png_of(http_get(url, timeout=90))
                except (urllib.error.URLError, OSError):
                    continue
                dest = thumbs / system / slot / thumbnail_name(label)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                done.append(RETROARCH_LABELS[slot])
                break
        return done

    def report(status, item, game=None, done=()):
        counts[status] += 1
        what = f"  ({', '.join(done)})" if done else ""
        bar.log(f"{status:<8} {item[0]}" + (f"  ->  {game[1]} [{game[2]}]{what}" if game else ""))
        bar.update(step=1, extra=f"{counts['saved']} saved")

    unsure = []

    def handle(item, found):
        if not found:
            with lock:
                report("no match", item)
        elif found[0][5] < SURE_MATCH:
            with lock:
                unsure.append((item, found))
                report("unsure", item, found[0])
        else:
            done = save(item, found[0])
            with lock:
                report("saved" if done else "failed", item, found[0], done)

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(handle, todo, [found for _, found in plan]))
    bar.close()
    if unsure and not args.yes:
        print(f"\n{len(unsure)} {'was' if len(unsure) == 1 else 'were'}n't a sure match. Pick the right game:")
        for i, (item, found) in enumerate(unsure, 1):
            pick = choose(f'({i}/{len(unsure)}) Which game is "{item[0]}"?',
                          [f"{g[1]}  [{platform_label(g)}]" for g in found] + ["Skip this one", "Skip the rest"])
            if pick == len(found) + 1:
                break
            if pick < len(found):
                done = save(item, found[pick])
                counts["unsure"] -= 1
                counts["saved" if done else "failed"] += 1
                print(f"  saved {', '.join(done)}" if done else "  couldn't download any")
    elif unsure:
        print(f"\nSkipped {len(unsure)} that weren't a sure match. Run without -y to pick by hand.")
    print(f"\nDone: {counts['saved']} games got thumbnails, {counts['unsure']} skipped, {counts['no match']} not found, "
          f"{counts['failed']} failed.\nThey show in RetroArch's playlists now (Settings > User Interface > "
          "Appearance > Thumbnails picks which ones).")


# ---------------------------------------------------------------- what's missing, and watching for new games

def missing_main(args, folder_text, cfg):
    """Report which games (or movies, or albums) in a folder have no art yet, then offer to get it."""
    folder = Path(os.path.expandvars(os.path.expanduser(folder_text.strip().strip('"').strip("'"))))
    if not folder.is_dir():
        raise NotFound(f"There's no folder at {folder}.")

    def show(what, total, missing):
        print(f"  {what:<16} {total - len(missing)} of {total} have one" + (
            f"; missing: {', '.join(missing[:8])}" + (f" and {len(missing) - 8} more" if len(missing) > 8 else "")
            if missing else ""))

    def offer():
        return args.yes or choose("Get the missing ones now?", ["Yes", "No"]) == 0

    if looks_like_videos(folder) or looks_like_music(folder) and not any(p for _, p in scan_folder(folder)):
        videos = looks_like_videos(folder)
        entries = scan_videos(folder) if videos else scan_music(folder)
        missing = [name for name, dest, _ in entries
                   if not any(dest.with_suffix(e).exists() for e in (".jpg", ".png", ".webp"))
                   and not (not videos and any((dest.parent / n).exists() for n in COVER_NAMES))]
        print(f"\n{folder}:")
        show("posters" if videos else "album covers", len(entries), missing)
        if missing and offer():
            (video_folder_main(args, folder, None) if videos else music_folder_main(args, folder))
        return
    files = game_files(folder)
    if not files:
        raise NotFound(f"No games in {folder}.")
    stems = [game_stem(f) for f in files]
    kinds = [k for k, sub in FOLDER_SUBDIRS.items() if (folder / sub).is_dir()] or ["box"]
    print(f"\n{len(stems)} games in {folder}:")
    gaps = {}
    for kind in kinds:
        sub = folder / FOLDER_SUBDIRS[kind]
        have = {f.stem for f in sub.iterdir() if f.is_file()} if sub.is_dir() else set()
        gaps[kind] = [s for s in stems if s not in have]
        show(FOLDER_SUBDIRS[kind], len(stems), gaps[kind])
    listed = folder / "gamelist.xml"
    if listed.exists():
        try:
            known = {gamelist_path(g.findtext("path")) for g in ET.parse(listed).getroot().findall("game")}
        except ET.ParseError:
            known = set()
        gaps["info"] = [game_stem(f) for f in files if gamelist_path(f"./{f.name}") not in known]
        show("gamelist.xml", len(stems), gaps["info"])
    todo = [k for k, gap in gaps.items() if gap]
    if not todo:
        print("\nEverything has art.")
        return
    if not offer():
        return
    for kind in todo:
        print(f"\n--- {GAMELIST_LABEL.split(':')[0] if kind == 'info' else FOLDER_SUBDIRS[kind]}")
        one = argparse.Namespace(**vars(args))
        one.type, one.out = kind, str(folder / FOLDER_SUBDIRS[kind]) if kind != "info" else None
        folder_main(one, str(folder), cfg, mode="folder")


def watch_main(args, folder_text, cfg):
    """Keep watching a ROM folder and get art for each new game as soon as it lands there."""
    folder = Path(os.path.expandvars(os.path.expanduser(folder_text.strip().strip('"').strip("'"))))
    if not folder.is_dir():
        raise NotFound(f"There's no folder at {folder}.")
    kinds = list(FOLDER_SUBDIRS) + ["info"]
    if args.type:
        wanted = [args.type]
    elif args.yes:
        wanted = ["box"]
    else:
        wanted = [kinds[i] for i in choose_many("What should each new game get?",
                                                [ART_TYPES[k] for k in FOLDER_SUBDIRS] + [GAMELIST_LABEL])]
    db = open_index(args.update)
    platform = folder_platform(db, args, folder, scan_folder(folder))
    seen = {f.name for f in game_files(folder)}
    names = ", ".join("gamelist.xml" if k == "info" else FOLDER_SUBDIRS[k] for k in wanted)
    print(f"\nWatching {folder} for new games ({names}). Leave this window open; Ctrl+C stops it.", flush=True)
    have_none = sum(1 for stem, _ in scan_folder(folder) if any(
        k != "info" and not any((folder / FOLDER_SUBDIRS[k]).glob(glob_escape(stem) + ".*")) for k in wanted))
    if have_none:
        print(f"({have_none} of the games already here are missing some of that. fetchart --missing gets those.)",
              flush=True)
    pending, lock = {}, threading.Lock()

    def arrived(path):
        stem = game_stem(path)
        plat = platform or (EXT_PLATFORMS.get(path.suffix.lower()) if path.is_file() else None)
        found = match_roms(db, [(stem, plat)], None, None)[0][1]
        stamp = time.strftime("%H:%M")
        if not found or found[0][5] < SURE_MATCH:
            guess = f" (best guess: {found[0][1]})" if found else ""
            print(f"[{stamp}] {path.name}: not sure which game this is{guess}. Run fetchart --folder to pick it.", flush=True)
            return
        game, saved = found[0], []
        for kind in wanted:
            if kind == "info":
                update_gamelist(folder / "gamelist.xml", [(f"./{path.name}", gamelist_fields(db, folder, stem, game))])
                saved.append("gamelist.xml")
                continue
            out = folder / FOLDER_SUBDIRS[kind]
            if any(out.glob(glob_escape(stem) + ".*")):
                continue
            result = fetch_best(game, kind, args.orient, rom_region(stem), {}, lock)
            if result:
                out.mkdir(parents=True, exist_ok=True)
                (out / (stem + result[1])).write_bytes(result[0])
                saved.append(FOLDER_SUBDIRS[kind])
        print(f"[{stamp}] {path.name}  ->  {game[1]} [{game[2]}]: "
              + (f"saved {', '.join(saved)}" if saved else "no art for it"), flush=True)

    try:
        while True:
            time.sleep(3)
            for path in game_files(folder):
                if path.name in seen:
                    continue
                try:  # wait until a copy has finished: the same size twice in a row
                    size = path.stat().st_size if path.is_file() else sum(1 for _ in path.iterdir())
                except OSError:
                    continue
                if pending.get(path.name) != size:
                    pending[path.name] = size
                    continue
                seen.add(path.name)
                pending.pop(path.name, None)
                try:
                    arrived(path)
                except (urllib.error.URLError, OSError) as e:
                    print(f"[{time.strftime('%H:%M')}] {path.name}: couldn't get art ({e})", flush=True)
    except KeyboardInterrupt:
        print("\nStopped watching.")


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
    library_main(args, folder, entries, find_poster, lambda d: d["i"]["imageUrl"], imdb_label, "poster")


# ---------------------------------------------------------------- whole folders of music

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma", ".ape", ".wv", ".aiff", ".aif"}
COVER_NAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png")


def has_audio(folder):
    try:
        return any(f.suffix.lower() in AUDIO_EXTS for f in folder.iterdir() if f.is_file())
    except OSError:
        return False


def looks_like_music(folder):
    """Audio files in the folder, or in folders one or two levels down (Artist/Album)."""
    try:
        subdirs = [d for d in sorted(folder.iterdir()) if d.is_dir()][:30]
        return has_audio(folder) or any(has_audio(d) or any(has_audio(s) for s in d.iterdir() if s.is_dir())
                                        for d in subdirs)
    except OSError:
        return False


def audio_tags(path):
    """(artist, album) from an MP3's ID3v2 tag or a FLAC file's Vorbis comments, None where missing.
    The album artist wins over the track artist, so a compilation stays one album."""
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 18)
    except OSError:
        return None, None
    tags = {}
    if head[:3] == b"ID3" and len(head) > 10 and head[3] in (3, 4):
        def syncsafe(b):
            return b[0] << 21 | b[1] << 14 | b[2] << 7 | b[3]

        v4 = head[3] == 4
        end, pos = 10 + syncsafe(head[6:10]), 10
        if head[5] & 0x40:  # an extended header comes first
            pos += syncsafe(head[10:14]) if v4 else 4 + int.from_bytes(head[10:14], "big")
        while pos + 10 <= min(end, len(head)) and head[pos] != 0:
            frame = head[pos:pos + 4].decode("latin-1")
            size = syncsafe(head[pos + 4:pos + 8]) if v4 else int.from_bytes(head[pos + 4:pos + 8], "big")
            body = head[pos + 10:pos + 10 + size]
            pos += 10 + size
            if frame in ("TPE1", "TPE2", "TALB") and body:
                encoding = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(body[0], "latin-1")
                tags[frame] = body[1:].decode(encoding, "replace").split("\x00")[0].strip()
        return tags.get("TPE2") or tags.get("TPE1") or None, tags.get("TALB") or None
    if head[:4] == b"fLaC":
        pos = 4
        while pos + 4 <= len(head):
            last, kind, size = head[pos] & 0x80, head[pos] & 0x7F, int.from_bytes(head[pos + 1:pos + 4], "big")
            body = head[pos + 4:pos + 4 + size]
            if kind == 4:  # VORBIS_COMMENT: a vendor string, then KEY=value strings, lengths little-endian
                p = 4 + int.from_bytes(body[:4], "little")
                count, p = int.from_bytes(body[p:p + 4], "little"), p + 4
                for _ in range(count):
                    n = int.from_bytes(body[p:p + 4], "little")
                    key, _, value = body[p + 4:p + 4 + n].decode("utf-8", "replace").partition("=")
                    tags.setdefault(key.upper(), value.strip())
                    p += 4 + n
                return tags.get("ALBUMARTIST") or tags.get("ARTIST") or None, tags.get("ALBUM") or None
            if last:
                break
            pos += 4 + size
    return None, None


def scan_music(folder):
    """The albums in a music folder: [(name to show, cover path, (artist, album))]. An album is a folder
    with audio files in it. Its artist and album come from the files' tags, else from the folder names
    (Artist/Album, or "Artist - Album (2003)")."""
    albums = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")) if len(Path(root).relative_to(folder).parts) < 3 else []
        here = Path(root)
        songs = sorted(here / f for f in files if Path(f).suffix.lower() in AUDIO_EXTS)
        if not songs:
            continue
        artist, album = next(((a, b) for a, b in map(audio_tags, songs[:3]) if b), (None, None))
        if not album:
            name = re.sub(r"\s*[\[(][^\])]*[\])]", "", here.name).strip()  # "(2003)", "[FLAC]"
            if " - " in name:
                artist, album = (part.strip() for part in name.split(" - ", 1))
            else:
                artist, album = (here.parent.name if here != folder else None), name
        cover = next((here / n for n in COVER_NAMES if (here / n).exists()), here / "cover.jpg")
        albums.append((f"{artist} - {album}" if artist else album, cover, (artist, album)))
    return albums


def plain_album(title):
    """An album title without "(Remastered 2009)" or "[Deluxe Edition]"."""
    return re.sub(r"\s*[(\[][^)\]]*[)\]]", "", title).strip() or title


def find_album(name, hint):
    """Deezer albums for (artist, album): (candidates best first, sure)."""
    artist, album = hint
    plain = plain_album(album)
    try:
        results = get_json("https://api.deezer.com/search/album", {"q": f"{artist or ''} {plain}".strip(),
                                                                   "limit": 10}).get("data", [])
    except (urllib.error.URLError, ValueError):
        return [], False
    same = [r for r in results if norm(plain_album(r["title"])) == norm(plain)
            and (not artist or norm(r["artist"]["name"]) == norm(artist))]
    return same + [r for r in results if r not in same], bool(same)


def album_cover_url(r):
    """Apple Music's 3000px copy when it has the album, else Deezer's."""
    return (itunes_album_art(r["artist"]["name"], plain_album(r["title"]))
            or r["cover_xl"].replace("1000x1000", "1800x1800"))


def music_folder_main(args, folder):
    """A cover.jpg in every album folder, where music players and servers look for it."""
    albums = scan_music(folder)
    if not albums:
        raise NotFound(f"No music in {folder}.")
    print(f"\nFound {len(albums)} album{'s' * (len(albums) != 1)} in {folder}.\n")
    library_main(args, folder, albums, find_album, album_cover_url,
                 lambda r: f"{r['artist']['name']} - {r['title']}", "cover")


def library_main(args, folder, entries, find, image_url, label, noun):
    """Shared by the movie and music folder modes: look every entry up, save the sure matches, then ask
    about the rest. entries: [(name, picture path, hint)]; find(name, hint) -> (candidates, sure);
    image_url(candidate) and label(candidate) describe a candidate; noun is "poster" or "cover"."""
    total, width = len(entries), len(str(len(entries)))
    counts = {"saved": 0, "had it": 0, "unsure": 0, "no match": 0, "failed": 0}
    unsure, bar = [], Progress(total, "Looking up")

    def report(n, status, name, match=None):
        counts[status] += 1
        bar.log(f"[{n:>{width}}/{total}] {status:<8} {name}" + (f"  ->  {label(match)}" if match else ""))
        bar.update(step=1, extra=f"{counts['saved']} saved")

    def save(dest, match):
        try:
            data = http_get(image_url(match), timeout=90)
        except (urllib.error.URLError, OSError):
            return False
        dest.with_suffix(image_ext(data)).write_bytes(data)
        return True

    def handle(entry):  # -> (status, candidates)
        name, dest, hint = entry
        if any(dest.with_suffix(ext).exists() for ext in (".jpg", ".png", ".webp")):
            return "had it", []
        found, sure = find(name, hint)
        if not found:
            return "no match", []
        if not sure:
            return "unsure", found
        return "saved" if save(dest, found[0]) else "failed", found

    with ThreadPoolExecutor(6) as pool:  # map hands results back in order, so the report reads top to bottom
        for n, ((name, dest, _), (status, found)) in enumerate(zip(entries, pool.map(handle, entries)), 1):
            report(n, status, name, found[0] if found else None)
            if status == "unsure":
                unsure.append((name, dest, found))
    bar.close()

    were = "was" if len(unsure) == 1 else "were"
    if unsure and not args.yes:
        print(f"\n{len(unsure)} {were}n't a sure match. Pick the right one for each:")
        for i, (name, dest, found) in enumerate(unsure, 1):
            pick = choose(f'({i}/{len(unsure)}) Which one is "{name}"?',
                          [label(d) for d in found] + ["Skip this one", "Skip the rest"])
            if pick == len(found) + 1:
                break
            if pick < len(found):
                counts["unsure"] -= 1
                ok = save(dest, found[pick])
                counts["saved" if ok else "failed"] += 1
                print(f"  saved {dest.relative_to(folder)}" if ok else f"  couldn't download that {noun}")
    elif unsure:
        print(f"\n{len(unsure)} {were}n't a sure match, so {'it was' if len(unsure) == 1 else 'they were'} "
              "skipped. Run without -y to pick by hand.")
    print(f"\nDone: {counts['saved']} saved, {counts['had it']} already had a {noun}, {counts['unsure']} skipped, "
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


# ---------------------------------------------------------------- box walls

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def pictures_in(folder):
    try:
        return sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS
                      and not f.stem.startswith("box wall"))
    except OSError:
        return []


BACKGROUNDS = {"dark": "#111114", "black": "#000000", "white": "#ffffff", "blur": "blur"}


def make_collage(paths, ratio=16 / 9, gap=12, background="#111114"):
    """Every picture in a grid on one image of exactly that shape (3840 pixels along its long side), with the
    pictures as big as they can be. Cells take the usual picture shape and each picture is fitted inside its
    cell; a short last row is centred. background is a colour, or "blur" for a blurred, darkened cover."""
    Image, ImageFilter = pil()
    from PIL import ImageEnhance
    sizes = []
    for path in paths:
        try:
            with Image.open(path) as im:
                sizes.append((path, im.size))
        except OSError:
            continue
    if not sizes:
        return None
    shapes = sorted(w / h for _, (w, h) in sizes)
    shape = shapes[len(shapes) // 2]  # the median, so one odd banner doesn't squash every cell
    width, height = (3840, round(3840 / ratio)) if ratio >= 1 else (round(3840 * ratio), 3840)
    n, best = len(sizes), None
    for cols in range(1, n + 1):  # the column count that makes the cells biggest
        rows = math.ceil(n / cols)
        cell_w = min((width - gap * (cols + 1)) / cols, (height - gap * (rows + 1)) / rows * shape)
        if cell_w > 0 and (best is None or cell_w > best[0]):
            best = (cell_w, cols, rows)
    if not best:
        return None
    cell_w, cols, rows = best
    cell_h = cell_w / shape
    left = (width - cols * cell_w - gap * (cols + 1)) / 2
    top = (height - rows * cell_h - gap * (rows + 1)) / 2
    if background == "blur":
        with Image.open(sizes[0][0]) as im:
            cover = im.convert("RGB")
        scale = max(width / cover.width, height / cover.height)
        canvas = cover.resize((round(cover.width * scale) + 1, round(cover.height * scale) + 1), Image.LANCZOS)
        canvas = canvas.crop((0, 0, width, height)).filter(ImageFilter.GaussianBlur(60))
        canvas = ImageEnhance.Brightness(canvas).enhance(0.45)
    else:
        canvas = Image.new("RGB", (width, height), background)
    for i, (path, (w, h)) in enumerate(sizes):
        row, col = divmod(i, cols)
        in_row = cols if row < rows - 1 else n - cols * (rows - 1)
        offset = (cols - in_row) * (cell_w + gap) / 2
        fit = min(cell_w / w, cell_h / h)
        with Image.open(path) as im:
            pic = im.convert("RGBA").resize((max(1, round(w * fit)), max(1, round(h * fit))), Image.LANCZOS)
        x = left + offset + gap + col * (cell_w + gap) + (cell_w - pic.width) / 2
        y = top + gap + row * (cell_h + gap) + (cell_h - pic.height) / 2
        canvas.paste(pic, (round(x), round(y)), pic)
    return canvas


def release_years(pictures, folder):
    """Each picture's game's release year, from its file name ("Super Mario 64 (USA).png" -> 1996), or None."""
    db = open_index()
    platform = match_platform(db, folder.name, 0.8) or match_platform(db, folder.parent.name, 0.8)
    years = {}
    for path in pictures:
        found = search_games(db, rom_title(path.stem), platform, None)
        years[path] = found[0][3] if found and found[0][5] >= 0.9 else None
    db.close()
    return years


def collage_main(args, folder_text, cfg):
    """A box wall: one poster of every picture in a folder. A ROM folder without art gets its box art first."""
    folder = Path(os.path.expandvars(os.path.expanduser(folder_text.strip().strip('"').strip("'"))))
    if not folder.is_dir():
        raise NotFound(f"There's no folder at {folder}.")
    pictures = pictures_in(folder) or pictures_in(folder / "covers")
    if not pictures and scan_folder(folder):
        print("No covers here yet, so first getting the box art for every game.")
        args.type = args.type if args.type in FOLDER_SUBDIRS else "box"
        art_folder = Path(args.out) if args.out else folder / FOLDER_SUBDIRS[args.type]
        args.out = str(art_folder)
        folder_main(args, str(folder), cfg)
        pictures = pictures_in(art_folder)
    if not pictures:
        raise NotFound(f"No pictures in {folder}.")
    orient = pick_orient(args, wallpaper=True) if args.wallpaper or args.orient else (
        args.orient or ["landscape", "ultrawide", "superwide", "phone", "portrait"][choose(
            "What shape should the box wall be?", ["Landscape (16:9, a normal screen)", ORIENTS["ultrawide"][0],
                                                   ORIENTS["superwide"][0], ORIENTS["phone"][0],
                                                   "Poster (2:3, for printing)"], auto=args.yes)])
    crop = ORIENTS[orient][1]
    ratio = 16 / 9 if crop is None else 2 / 3 if crop is True else crop
    background = args.bg or list(BACKGROUNDS.values())[choose("What background?", [
        "Dark grey", "Black", "White", "Blurred cover art"], auto=args.yes)]
    background = BACKGROUNDS.get(background.lower(), background)
    if background != "blur":
        try:
            from PIL import ImageColor
            ImageColor.getrgb(background)
        except ValueError:
            sys.exit(f'Don\'t know the colour "{background}". Try black, white, a name like navy, or #202030.')
    order = args.sort or ["name", "year", "random"][choose("What order?", [
        "By name", "By release year (oldest first)", "Shuffled"], auto=args.yes)]
    if order == "random":
        import random
        random.shuffle(pictures)
    elif order == "year":
        years = release_years(pictures, folder)
        pictures.sort(key=lambda p: (years[p] is None, years[p] or 0, p.name.lower()))
        unknown = sum(y is None for y in years.values())
        if unknown:
            print(f"{unknown} of them couldn't be matched to a game, so they go at the end.")
    print(f"\nMaking a box wall of {len(pictures)} pictures...")
    wall = make_collage(pictures, ratio, background=background)
    if not wall:
        raise NotFound(f"None of the pictures in {folder} could be opened.")
    dest = unique_path(folder / "box wall.jpg")
    wall.save(dest, quality=92)
    print(f"Saved {dest}  ({wall.width}x{wall.height})")
    if args.wallpaper and orient != "phone":
        print("It's your desktop wallpaper now. To put your old one back: fetchart --undo-wallpaper"
              if set_wallpaper(dest, cfg) else "Couldn't set the wallpaper on this system.")


# ---------------------------------------------------------------- daily wallpaper

DAILY_NAME = "fetchart daily wallpaper"


def daily_command():
    """How the scheduler starts fetchart: [program, script...]. pythonw keeps a console window from popping up."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return [str(windowless if windowless.exists() else exe), str(Path(__file__).resolve())]


def parse_every(text):
    """ "30m", "1h", "2 hours", "day" -> minutes (None = once a day)."""
    m = re.fullmatch(r"\s*(\d*)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)\s*", (text or "").lower())
    if not m:
        sys.exit(f'Don\'t understand --every "{text}". Try 30m, 1h or 6h.')
    minutes = int(m[1] or 1) * {"m": 1, "h": 60, "d": 1440}[m[2][0]]
    if minutes < 5:
        sys.exit("--every needs at least 5 minutes between wallpapers.")
    return None if minutes >= 1440 else minutes


def every_text(minutes):
    if not minutes:
        return "every morning (or when the computer first starts that day)"
    if minutes % 60:
        return f"every {minutes} minutes"
    return "every hour" if minutes == 60 else f"every {minutes // 60} hours"


def schedule_daily(enable=True, every=None):
    """Run "fetchart --daily-run" every morning (and as soon as possible after a morning the computer was off),
    or every `every` minutes, or remove that schedule. Returns False if this system's scheduler couldn't be used."""
    cmd = daily_command() + ["--daily-run"]
    if os.name == "nt":
        if not enable:
            return run_quiet(["schtasks", "/Delete", "/TN", DAILY_NAME, "/F"]) is not None
        program, arguments = cmd[0], subprocess.list2cmdline(cmd[1:])
        trigger = ("<CalendarTrigger><StartBoundary>2026-01-01T07:00:00</StartBoundary><ScheduleByDay>"
                   "<DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>" if not every else
                   f"<TimeTrigger><StartBoundary>2026-01-01T00:00:00</StartBoundary><Repetition><Interval>PT{every}M"
                   "</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger><LogonTrigger/>")
        xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Sets a new wallpaper {html.escape(every_text(every))}. Turn it off with: fetchart --daily off</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <StartWhenAvailable>true</StartWhenAvailable><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  </Settings>
  <Actions Context="Author"><Exec><Command>{html.escape(program)}</Command><Arguments>{html.escape(arguments)}</Arguments></Exec></Actions>
</Task>
"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        task = DATA_DIR / "daily-task.xml"
        task.write_text(xml, encoding="utf-16")
        return run_quiet(["schtasks", "/Create", "/TN", DAILY_NAME, "/XML", str(task), "/F"]) is not None
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.github.samuelarther.fetchart.daily.plist"
        run_quiet(["launchctl", "unload", str(plist)])
        if not enable:
            plist.unlink(missing_ok=True)
            return True
        program = "".join(f"<string>{html.escape(part)}</string>" for part in cmd)
        when = (f"<key>StartInterval</key><integer>{every * 60}</integer>" if every else
                "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer></dict>")
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.github.samuelarther.fetchart.daily</string>
  <key>ProgramArguments</key><array>{program}</array>
  {when}
  <key>RunAtLoad</key><true/>
</dict></plist>
""", encoding="utf-8")
        return run_quiet(["launchctl", "load", str(plist)]) is not None
    # Linux: a systemd user timer, which runs inside the desktop session so gsettings can reach it.
    units = Path.home() / ".config" / "systemd" / "user"
    if not enable:
        run_quiet(["systemctl", "--user", "disable", "--now", "fetchart-daily.timer"])
        for name in ("fetchart-daily.service", "fetchart-daily.timer"):
            (units / name).unlink(missing_ok=True)
        return True
    units.mkdir(parents=True, exist_ok=True)
    (units / "fetchart-daily.service").write_text(
        f"[Unit]\nDescription={DAILY_NAME}\n\n[Service]\nType=oneshot\nExecStart={subprocess.list2cmdline(cmd)}\n")
    (units / "fetchart-daily.timer").write_text(
        f"[Unit]\nDescription={DAILY_NAME}\n\n[Timer]\n"
        + (f"OnBootSec=2min\nOnUnitActiveSec={every}min\n" if every else "OnCalendar=*-*-* 07:00\nPersistent=true\n")
        + "\n"
        "[Install]\nWantedBy=timers.target\n")
    return (run_quiet(["systemctl", "--user", "daemon-reload"]) is not None
            and run_quiet(["systemctl", "--user", "enable", "--now", "fetchart-daily.timer"]) is not None)


def daily_main(args, text, cfg):
    """Set up (or turn off) a new game wallpaper every day."""
    text = text.strip()
    if text.lower() in ("off", "stop", "no", "none"):
        schedule_daily(enable=False)
        had = cfg.pop("daily", None)
        save_config(cfg)
        print("The daily wallpaper is off." + (" Your old wallpaper: fetchart --undo-wallpaper" if had else ""))
        return
    every = parse_every(args.every) if args.every else None
    db = open_index(args.update)
    platform = match_platform(db, text, 0.8) if text and "," not in text else None
    if media_word(text) == "space":
        pool, what = {"space": True}, "NASA's space pictures"
    elif platform:
        pool, what = {"platform": platform}, f"popular {platform} games"
    elif text:
        games = []
        for name in (t.strip() for t in text.split(",") if t.strip()):
            found = search_games(db, name, None, "wallpaper")
            if found:
                games.append(list(found[0][:3]))
                print(f"  {name}  ->  {found[0][1]} [{found[0][2]}]")
            else:
                print(f"  {name}: no key art found, so it's left out")
        if not games:
            raise NotFound("None of those games have key art for a wallpaper.")
        pool, what = {"games": games}, f"{len(games)} game{'s' * (len(games) != 1)}"
    else:
        pool, what = {"popular": True}, "popular games"
    pool["every"] = every
    cfg["daily"] = pool
    save_config(cfg)
    scheduled = schedule_daily(every=every)
    daily_run(cfg, force=True)
    if scheduled:
        print(f"\nYou'll get a new wallpaper from {what} {every_text(every)}.")
    else:
        print("\nCouldn't set up the daily schedule on this system, so this is a one-off.")
    print("Turn it off: fetchart --daily off. Your old wallpaper back: fetchart --undo-wallpaper")


def daily_run(cfg, force=False):
    """Today's wallpaper. Started by the scheduler, which may run it more than once a day."""
    pool = cfg.get("daily")
    today = time.strftime("%Y-%m-%d")
    if not pool:
        return
    every = pool.get("every")
    if not force and (time.time() - pool.get("last_time", 0) < every * 60 - 120 if every else pool.get("last") == today):
        return
    import random
    try:
        if pool.get("space"):
            pictures = apod_items()
            # Today's picture of the day; when it changes more often than daily, a random one from the last month.
            fresh = [p for p in pictures if p[1] != pool.get("game")]
            date, title, url = (pictures[0] if not every and pictures[0][1] != pool.get("game")
                                else random.choice(fresh or pictures))
            return set_daily(cfg, pool, http_get(url, timeout=90), title, "NASA")
        db = open_index()
        has_fanart = "EXISTS (SELECT 1 FROM images i WHERE i.id = g.id AND i.type = 'Fanart - Background')"
        if "games" in pool:
            games = [tuple(g) for g in pool["games"]]
        else:
            where = "g.platform = ? AND " if "platform" in pool else ""
            games = db.execute(f"SELECT g.id, g.name, g.platform FROM games g WHERE {where}{has_fanart} "
                               "ORDER BY g.votes DESC LIMIT 300", [pool["platform"]] if where else []).fetchall()
        choices = [g for g in games if g[1] != pool.get("game")] or games
        gid, title, plat = random.choice(choices)
        fanart = [url for _, url in images_by_type(db, gid).get("Fanart - Background", [])]
        set_daily(cfg, pool, http_get(random.choice(fanart), timeout=90), title, plat)
    except Exception as e:  # noqa: BLE001 - the scheduler has no console to show this, so keep a log
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / "daily.log", "a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M')} {type(e).__name__}: {e}\n")
        print(f"Couldn't change the wallpaper: {e}")


def set_daily(cfg, pool, data, title, where):
    """Make a downloaded picture the wallpaper, keeping only the newest one."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / f"daily wallpaper {time.strftime('%Y-%m-%d %H%M')}{image_ext(data)}"
    dest.write_bytes(data)
    if set_wallpaper(dest, cfg):
        for old in DATA_DIR.glob("daily wallpaper *"):
            if old.stem != dest.stem:
                old.unlink(missing_ok=True)
    pool.update(last=time.strftime("%Y-%m-%d"), last_time=time.time(), game=title)
    save_config(cfg)
    print(f"New wallpaper: {title} [{where}]")


# ---------------------------------------------------------------- lists of names

def list_main(args, path_text, cfg):
    """Art for every name in a text file. A line is a name, optionally with ", platform" (or ", movie",
    ", album", ", pokemon"...) after it; # starts a comment. Takes the top match and best picture for each."""
    path = Path(os.path.expandvars(os.path.expanduser(path_text.strip().strip('"').strip("'"))))
    try:
        lines = [line.split("#")[0].strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    except OSError:
        raise NotFound(f"Couldn't read {path}.")
    lines = [line for line in lines if line]
    if not lines:
        raise NotFound(f"{path} has no names in it.")
    if not args.type and not args.yes:
        kinds = [k for k in ART_TYPES if k not in ("ach", "wallpaper")]
        args.type = kinds[choose("What art do you want for the games?", [ART_TYPES[k] for k in kinds])]
    args.out = str(pick_folder(args.out, cfg, args.yes))
    args.yes, done, failed = True, 0, []
    for n, line in enumerate(lines, 1):
        name, comma, word = line.rpartition(",")
        name, word = (name.strip(), word.strip()) if comma else (line, None)
        print(f"\n[{n}/{len(lines)}] {line}")
        one = argparse.Namespace(**vars(args))
        one.game, one.platform = name, word
        try:
            run_category(one, name, cfg, media_word(word) or "game")
            done += 1
        except (NotFound, SystemExit) as e:
            failed.append(line)
            print(f"  skipped: {e}" if str(e) not in ("", "None", "1") else "  skipped")
    print(f"\nDone: {done} of {len(lines)} found." + (f" Not found: {'; '.join(failed)}" if failed else "")
          + f"\nThe art is in {args.out}")


# ---------------------------------------------------------------- tab completion

def completion_script(parser, shell):
    """A script that teaches the shell to Tab-complete fetchart's options, art types, shapes and platforms."""
    options = sorted(s for a in parser._actions for s in a.option_strings if a.help != argparse.SUPPRESS)
    words = sorted(set(PLATFORMS) | {k for k in MEDIA if k.isascii()})
    values = {"-t --type": list(ART_TYPES), "-r --orient": list(ORIENTS), "-q --quality": ["low", "medium", "high", "all"],
              "--completion": ["powershell", "bash", "zsh"], "--sort": ["name", "year", "random"],
              "--bg": ["black", "white", "blur"], "--every": ["30m", "1h", "2h", "6h"]}
    paths = "-o --out -f --folder --collage --list --steam --retroarch --missing --watch"
    if shell == "powershell":
        cases = "\n".join(f"        {{ $_ -in @({', '.join(repr(o) for o in opts.split())}) }} {{ @({', '.join(repr(v) for v in vals)}) }}"
                          for opts, vals in values.items())
        return f"""Register-ArgumentCompleter -Native -CommandName fetchart, fetchart.exe -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $words = @($commandAst.CommandElements | ForEach-Object {{ $_.ToString() }})
    $prev = if ($wordToComplete) {{ $words[-2] }} else {{ $words[-1] }}
    $takesValue = @({', '.join(repr(o) for o in (paths + ' ' + ' '.join(values)).split())})
    $values = switch ($prev) {{
{cases}
        {{ $_ -in @({', '.join(repr(o) for o in paths.split())}) }} {{ return }}
        default {{
            if ($wordToComplete -like '-*') {{ @({', '.join(repr(o) for o in options)}) }}
            else {{
                $given = 0
                for ($i = 1; $i -lt $words.Count; $i++) {{
                    if ($words[$i] -eq $wordToComplete -and $i -eq $words.Count - 1) {{ break }}
                    if ($words[$i] -notlike '-*' -and $takesValue -notcontains $words[$i - 1]) {{ $given++ }}
                }}
                if ($given -ge 1) {{ @({', '.join(repr(w) for w in words)}) }}
            }}
        }}
    }}
    $values | Where-Object {{ $_ -like "$wordToComplete*" }} |
        ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }}
}}
"""
    cases = "\n".join(f'        {"|".join(opts.split())}) COMPREPLY=($(compgen -W "{" ".join(vals)}" -- "$cur")); return;;'
                      for opts, vals in values.items())
    script = f"""_fetchart() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}" prev="${{COMP_WORDS[COMP_CWORD-1]}}" i given=0
    COMPREPLY=()
    case "$prev" in
{cases}
        {"|".join(paths.split())}) COMPREPLY=(); return;;
    esac
    if [[ "$cur" == -* ]]; then COMPREPLY=($(compgen -W "{" ".join(options)}" -- "$cur")); return; fi
    for ((i = 1; i < COMP_CWORD; i++)); do
        case " {paths} {" ".join(values)} " in *" ${{COMP_WORDS[i-1]}} "*) continue;; esac
        [[ "${{COMP_WORDS[i]}}" != -* ]] && given=$((given + 1))
    done
    ((given >= 1)) && COMPREPLY=($(compgen -W "{" ".join(words)}" -- "$cur"))
}}
complete -o default -F _fetchart fetchart
"""
    return ("autoload -U +X bashcompinit && bashcompinit\n" + script) if shell == "zsh" else script


# ---------------------------------------------------------------- main

def pick_art(options, wanted, auto, heading, keep_order=False):
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
    cards = [(o.label(), o.preview or o.url or data_uri(o.data), o) for o in options]
    i = choose("Which one?" + ("" if keep_order else " (biggest first)"), [o.label() for o in options], auto=auto,
               allow_all=True, preview=lambda: open_preview(heading, cards), sheet=lambda: show_sheet(options))
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
  .pic.crop > img:first-child { width: 100%; height: auto; object-fit: cover; }
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


def contact_sheet(options):
    """One picture of every option, numbered the same as the menu. Returns a Pillow image."""
    Image, _ = pil()
    from PIL import ImageDraw, ImageFont

    def thumb(art):
        try:
            if art.preview:
                data = http_get(art.preview, timeout=60)
            elif art.crop or art.logo:
                data = render(art)[0]
            else:
                data = art.data or http_get(art.url, timeout=60)
            im = Image.open(io.BytesIO(data))
            im.draft("RGB", (600, 600))  # JPEGs decode at a fraction of their size: much faster for huge scans
            im = im.convert("RGBA")
            small = max(im.size) <= 128
            if small:  # pixel art and icons: blow up with hard edges
                k = max(1, 240 // max(im.size))
                im = im.resize((im.width * k, im.height * k), Image.NEAREST)
            im.thumbnail((300, 300), Image.LANCZOS)
            return im
        except (urllib.error.URLError, OSError, ValueError):
            return None

    with ThreadPoolExecutor(8) as pool:
        thumbs = list(pool.map(thumb, options))
    n = len(options)
    cols = max(1, min(n, round(math.sqrt(n * 1.8))))
    rows, cell, pad, caption = math.ceil(n / cols), 300, 14, 30
    sheet = Image.new("RGB", (cols * (cell + pad) + pad, rows * (cell + caption + pad) + pad), "#18181b")
    draw = ImageDraw.Draw(sheet)
    try:
        font, big = ImageFont.load_default(size=18), ImageFont.load_default(size=22)
    except TypeError:  # Pillow older than 10.1 has one small built-in font
        font = big = ImageFont.load_default()
    for i, (art, im) in enumerate(zip(options, thumbs)):
        row, col = divmod(i, cols)
        x, y = pad + col * (cell + pad), pad + row * (cell + caption + pad)
        draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill="#27272a")
        if im:
            sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2), im)
        else:
            draw.text((x + 12, y + cell // 2 - 10), "couldn't load", fill="#a1a1aa", font=font)
        label = str(i + 1)
        draw.rounded_rectangle((x + 6, y + 6, x + 22 + 14 * len(label), y + 36), 15, fill="#2563eb")
        draw.text((x + 15, y + 9), label, fill="#ffffff", font=big)
        size = art.final_size()
        text = (f"{size[0]}x{size[1]}  " if size else "") + art.source
        while text and draw.textlength(text, font=font) > cell:
            text = text[:-2]
        draw.text((x, y + cell + 6), text, fill="#e4e4e7", font=font)
    return sheet


def image_protocol():
    """How this terminal shows pictures inline: "kitty", "iterm", "sixel", or None. FETCHART_IMAGES overrides it."""
    forced = os.environ.get("FETCHART_IMAGES", "").lower()
    if forced:
        return None if forced in ("off", "no", "none", "0") else forced
    if not sys.stdout.isatty():
        return None
    if os.environ.get("KITTY_WINDOW_ID") or os.environ.get("TERM") == "xterm-kitty" or os.environ.get("GHOSTTY_RESOURCES_DIR"):
        return "kitty"
    if os.environ.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm") or os.environ.get("LC_TERMINAL") == "iTerm2":
        return "iterm"
    if os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") in ("foot", "mlterm"):
        return "sixel"  # Windows Terminal draws sixel images
    return None


def sixel(img):
    """A picture as a sixel escape sequence, 255 colours."""
    img = img.convert("RGB").quantize(255)
    w, h = img.size
    palette, pixels = img.getpalette()[:255 * 3], img.tobytes()
    out = ['\x1bPq"1;1;' + f"{w};{h}"]
    for c in range(len(palette) // 3):
        r, g, b = palette[c * 3:c * 3 + 3]
        out.append(f"#{c};2;{r * 100 // 255};{g * 100 // 255};{b * 100 // 255}")
    to_sixel = bytes(range(63, 127)) + bytes(192)
    for top in range(0, h, 6):
        masks = {}
        for k in range(min(6, h - top)):
            bit, row = 1 << k, pixels[(top + k) * w:(top + k + 1) * w]
            for x, c in enumerate(row):
                mask = masks.get(c)
                if mask is None:
                    mask = masks[c] = bytearray(w)
                mask[x] |= bit
        bands = []
        for c, mask in masks.items():
            line = bytes(mask).translate(to_sixel).decode().rstrip("?")
            line = re.sub(r"(.)\1{3,}", lambda m: f"!{len(m[0])}{m[1]}", line)
            bands.append(f"#{c}{line}")
        out.append("$".join(bands) + "-")
    out.append("\x1b\\")
    return "".join(out)


def show_picture(img, name="contact sheet"):
    """Show a picture right in the terminal when it can draw one, else open it in the picture viewer.
    Returns (drawn in the terminal, message)."""
    protocol = image_protocol()
    columns = shutil.get_terminal_size().columns
    if protocol in ("kitty", "iterm", "sixel"):
        enable_ansi()
        if protocol == "sixel":
            fit = min(1.0, columns * 9 / img.width)
            if fit < 1:
                img = img.resize((round(img.width * fit), round(img.height * fit)))
            sys.stdout.write("\n" + sixel(img) + "\n")
        else:
            data = base64.b64encode(png_bytes(img.convert("RGB"))).decode()
            if protocol == "iterm":
                sys.stdout.write(f"\n\x1b]1337;File=inline=1;width={columns - 2};preserveAspectRatio=1;"
                                 f"size={len(data) * 3 // 4}:{data}\x07\n")
            else:
                chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)]
                sys.stdout.write("\n")
                for i, chunk in enumerate(chunks):
                    head = f"a=T,f=100,c={columns - 2}," if i == 0 else ""
                    sys.stdout.write(f"\x1b_G{head}m={int(i < len(chunks) - 1)};{chunk}\x1b\\")
                sys.stdout.write("\n")
        sys.stdout.flush()
        return True, "That's every option, numbered."
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{name}.png"
    img.save(path)
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False, f"Saved the contact sheet to {path}"
    return False, "Contact sheet opened in your picture viewer."


def show_sheet(options):
    print("\nMaking the contact sheet...")
    return show_picture(contact_sheet(options))


def data_uri(data):
    kind = {".png": "png", ".gif": "gif", ".webp": "webp"}.get(image_ext(data), "jpeg")
    return f"data:image/{kind};base64," + base64.b64encode(data).decode()


def open_preview(heading, cards, small=False):
    """Show the options as numbered pictures in the browser. cards: [(label, image src, Art or None)]."""
    tiles = []
    for n, (label, src, art) in enumerate(cards, 1):
        classes = "pic" + (" crop" if art and art.crop else "") + (" small" if small else "")
        logo = f'<img class="logo" src="{html.escape(art.logo)}" alt="">' if art and art.logo else ""
        ratio = (2 / 3 if art.crop is True else art.crop) if art and art.crop else None
        style = f' style="aspect-ratio: {ratio:.4f}"' if ratio else ""
        tiles.append(f'<figure><div class="{classes}"><img src="{html.escape(src)}"{style} alt="">{logo}'
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
    p.add_argument("-r", "--orient", choices=list(ORIENTS),
                   help="key art shape: landscape, portrait, phone, ultrawide (21:9) or superwide (32:9)")
    p.add_argument("-q", "--quality", help="low, medium, high, all, or an exact size like 1920x1080")
    p.add_argument("-o", "--out", help="folder to save into")
    p.add_argument("-y", "--yes", action="store_true", help="take the top match and defaults, no prompts")
    p.add_argument("-w", "--wallpaper", action="store_true", help="set the picture as your desktop wallpaper")
    p.add_argument("--undo-wallpaper", action="store_true",
                   help="put back the wallpaper you had before fetchart changed it")
    p.add_argument("-f", "--folder", help="get art for every game in this folder, named after each ROM file "
                                          "(or posters for movies and shows, covers for albums)")
    p.add_argument("--steam", nargs="?", const="", metavar="STEAM_DIR",
                   help="add library art to the non-Steam games in Steam")
    p.add_argument("--retroarch", nargs="?", const="", metavar="RETROARCH_DIR",
                   help="fill RetroArch's thumbnails (box art, screenshots, title screens, logos) for its playlists")
    p.add_argument("--missing", metavar="FOLDER", help="list the games (or movies, albums) in a folder with no art yet, "
                                                       "then get it")
    p.add_argument("--watch", metavar="FOLDER", help="keep watching a ROM folder and get art for each new game")
    p.add_argument("--collage", metavar="FOLDER", help="make one big poster (a box wall) of every cover in a folder")
    p.add_argument("--sort", choices=["name", "year", "random"], help="box wall order (default: name)")
    p.add_argument("--bg", metavar="COLOR", help='box wall background: a colour ("black", "#202030") or "blur"')
    p.add_argument("--list", metavar="FILE", help="get art for every name in a text file, one per line "
                                                  '(add ", platform" or ", movie" etc. after a name)')
    p.add_argument("--daily", nargs="?", const="", metavar="GAMES",
                   help='a new game wallpaper every day: game names ("halo 3, celeste"), a platform, '
                        "space for NASA's picture of the day, nothing for popular games, or off")
    p.add_argument("--every", metavar="TIME", help="with --daily: change it more often, like 30m, 1h or 6h")
    p.add_argument("--daily-run", action="store_true", help=argparse.SUPPRESS)  # what the scheduler starts
    p.add_argument("--completion", choices=["powershell", "bash", "zsh"],
                   help="print the tab-completion script for your shell")
    p.add_argument("--update", action="store_true", help="re-download the game database")
    p.add_argument("--upgrade", action="store_true", help="update fetchart itself to the newest version")
    p.add_argument("--version", action="version", version=f"fetchart {__version__}")
    args = p.parse_args()
    if args.completion:
        return print(completion_script(p, args.completion))
    if args.daily_run:
        return daily_run(load_config())
    if args.undo_wallpaper:
        return undo_wallpaper(load_config())
    if args.upgrade:
        return upgrade()
    if args.update and not (args.game or args.folder or args.list or args.collage or args.steam is not None
                            or args.retroarch is not None or args.missing or args.watch):
        open_index(force_update=True)
        return
    # Options that name their own target, then a name on the command line, else the menus.
    for category, value in (("steam", args.steam), ("retroarch", args.retroarch), ("folder", args.folder),
                            ("missing", args.missing), ("watch", args.watch), ("collage", args.collage),
                            ("list", args.list), ("daily", args.daily)):
        if value is not None:
            if category in ("folder", "missing", "watch"):
                args.platform = args.platform or args.game  # fetchart --folder D:\ROMs n64
            try:
                return run_category(args, value, load_config(), category)
            except NotFound as e:
                sys.exit(str(e))

    # With no name given (typed alone, or double-clicked), everything is asked through menus.
    interactive = not args.game
    if interactive:
        category = list(CATEGORIES)[choose("What are you looking for?", [c[0] for c in CATEGORIES.values()])]
    elif not args.platform and media_word(args.game) == "space":  # fetchart space: today's picture
        category, args.game = "space", ""
    else:
        category = media_word(args.platform) or "game"
    cfg = load_config()
    while True:
        if interactive and CATEGORIES[category][1]:
            args.game = ask("\n" + CATEGORIES[category][1])
            if not args.game and category not in ("daily", "space"):
                return
        try:
            return run_category(args, args.game or "", cfg, category)
        except NotFound as e:
            if not interactive:
                sys.exit(str(e))
            print(f"{e} Try another name (Enter to quit).")


def media_word(text):
    """The category a platform word stands for ("movie", "Pokémon", "manga"), or None."""
    return MEDIA.get(re.sub(r"[^a-z]", "", norm(text or "")))


def run_category(args, text, cfg, category):
    """Do one search or job. text is the name typed (or the folder / file / list of games)."""
    query = re.sub(r"[-_]+", " ", text).strip()
    if category in ("folder", "videos", "musicfolder"):
        return folder_main(args, text, cfg, mode=category)
    handlers = {
        "steam": lambda: steam_main(args, text),
        "retroarch": lambda: retroarch_main(args, text),
        "missing": lambda: missing_main(args, text, cfg),
        "watch": lambda: watch_main(args, text, cfg),
        "collage": lambda: collage_main(args, text, cfg),
        "list": lambda: list_main(args, text, cfg),
        "daily": lambda: daily_main(args, text, cfg),
        "game": lambda: game_main(args, query, cfg),
        "painting": lambda: painting_main(args, query, cfg),
        "pokemon": lambda: pokemon_main(args, query, cfg),
        "minecraft": lambda: minecraft_main(args, text, cfg),
        # The menu's "Anime or manga" searches both; the word "anime" on the command line means anime.
        "anime": lambda: anime_main(args, query, cfg, "ANIME" if media_word(args.platform) == "anime" else None),
        "manga": lambda: anime_main(args, query, cfg, "MANGA"),
        "book": lambda: book_main(args, query, cfg),
        "app": lambda: app_main(args, query, cfg),
        "cards": lambda: cards_main(args, query, cfg),
        "pokemoncard": lambda: cards_main(args, query, cfg, "pokemon"),
        "mtg": lambda: cards_main(args, query, cfg, "mtg"),
        "yugioh": lambda: cards_main(args, query, cfg, "yugioh"),
        "amiibo": lambda: amiibo_main(args, query, cfg),
        "podcast": lambda: podcast_main(args, query, cfg),
        "artist": lambda: artist_main(args, query, cfg),
        "space": lambda: space_main(args, query, cfg),
    }
    return handlers.get(category, lambda: media_main(args, query, cfg, category))()


def pick_orient(args, wallpaper=False):
    """The shape for key art: from -r, else asked (landscape with -y)."""
    keys = ["landscape", "ultrawide", "superwide", "phone"] if wallpaper else list(ORIENTS)
    if args.orient in keys:
        return args.orient
    title = "Which screen is it for?" if wallpaper else "Which shape?"
    return keys[choose(title, [ORIENTS[k][0] for k in keys], auto=args.yes)]


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
    if args.yes and found and found[0][5] < 0.5:  # nobody's there to say a far-off guess is wrong
        found = []
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
            kind = "clean" if kind == "wallpaper" else kind
            orient = pick_orient(args, wallpaper=True)
            args.wallpaper = orient != "phone"  # a phone wallpaper is only saved, for copying to the phone
        elif kind in ("clean", "logo"):
            orient = pick_orient(args)
        if kind == "ach":
            return download_achievements(title, plat, args, cfg)
        if kind == "info":
            return save_info(db, gid, title, plat, args, cfg)
        print(f"Finding art for {title} [{plat}]...")
        options = gather(db, gid, title, plat, kind, orient)
        if options:
            break
        missing = f"{title} has no {ART_TYPES[kind].lower()}" + (f" in {orient}" if orient else "") + "."
        if args.type or args.yes:
            sys.exit(missing)
        print(f"{missing} Pick another kind.")
    tag = {"box": "box art", "edition": "edition cover", "back": "back cover", "box3d": "3D box", "disc": "disc", "screenshot": "screenshot",
           "flyer": "ad flyer", "clean": "key art", "logo": "key art (logo)", "clearlogo": "logo", "icon": "icon"}[kind]
    # Store lookups guess URLs, so for icons drop the ones that didn't resolve to a real image.
    finish(options, f"{title} [{plat}] - {tag}", args, cfg, require_size=kind == "icon")


def save_info(db, gid, title, plat, args, cfg):
    """Show a game's details and save them as a text file."""
    info = game_info(db, gid)
    if not info:
        sys.exit(f"LaunchBox has no details for {title}.")
    text = info_text(title, plat, info)
    print("\n" + text)
    folder = pick_folder(args.out, cfg, args.yes)
    dest = unique_path(folder / (safe_filename(f"{title} [{plat}] - info") + ".txt"))
    dest.write_text(text + "\n", encoding="utf-8")
    print(f"\nSaved {dest}")


def finish(options, name_prefix, args, cfg, require_size=False, keep_order=False):
    """Measure the options, let the user pick, and save the picks. Returns the saved files.
    keep_order keeps the options in the order given (oldest icon first, newest card first)
    instead of biggest first."""
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(probe_size, options))
    options = dedupe(options)
    if require_size:
        options = [o for o in options if o.size]
    if not options:
        sys.exit("Couldn't find any art for that.")
    # Biggest first, but Japanese releases last: their logo and cover text is in Japanese.
    if not keep_order:
        options.sort(key=lambda o: (o.source.endswith(", Japan"),
                                    -(o.final_size()[0] * o.final_size()[1] if o.final_size() else 0)))
    if args.wallpaper:  # a 600 MB museum scan makes a poor wallpaper; 4K is plenty
        options = [o for o in options if not o.final_size() or o.final_size()[0] <= 3840] or options

    picked = pick_art(options, args.quality, args.yes, name_prefix, keep_order)
    folder = pick_folder(args.out, cfg, args.yes)
    print()
    saved = []
    for art in picked:
        size = art.final_size()
        name = name_prefix + (f" {size[0]}x{size[1]}" if size else "") + f" - {art.source}"
        try:
            data, ext = render(art, progress=True)
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
    return saved


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
    daily = cfg.pop("daily", None)
    if daily:  # otherwise tomorrow's run would change it again
        schedule_daily(enable=False)
    save_config(cfg)
    print("Your old wallpaper is back." + (" The daily wallpaper is off too." if daily else ""))


# ---------------------------------------------------------------- updating fetchart itself

RELEASES = "https://github.com/SamuelArther/fetchart/releases"
RELEASE_ASSETS = {"nt": "fetchart-windows.exe", "darwin": "fetchart-macos", "linux": "fetchart-linux"}


def version_tuple(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def latest_version():
    """The newest fetchart on PyPI (every release goes there), or None."""
    try:
        return get_json("https://pypi.org/pypi/fetchart/json", {})["info"]["version"]
    except (urllib.error.URLError, ValueError, KeyError, OSError):
        return None


def install_kind():
    """How this copy of fetchart was installed: (kind, detail)."""
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    text = str(here).replace("\\", "/").lower()
    if "/cellar/" in text or "/homebrew/" in text:
        return "brew", None
    if "/scoop/apps/" in text:
        return "scoop", None
    if "/winget/packages/" in text:
        return "winget", None
    if getattr(sys, "frozen", False):
        return "exe", here
    if (here.parent / ".git").exists():
        return "git", here.parent
    if "/pipx/" in text:
        return "pipx", None
    return "pip", None


def upgrade():
    """Update fetchart the same way it was installed."""
    latest = latest_version()
    if not latest:
        sys.exit("Couldn't reach PyPI to see what the newest fetchart is.")
    if version_tuple(latest) <= version_tuple(__version__):
        return print(f"You have the newest fetchart ({__version__}).")
    print(f"Updating fetchart {__version__} -> {latest}...")
    kind, where = install_kind()
    commands = {"brew": ["brew", "upgrade", "fetchart"], "scoop": ["scoop", "update", "fetchart"],
                "winget": ["winget", "upgrade", "--id", "SamuelArther.fetchart"],
                "pipx": ["pipx", "upgrade", "fetchart"],
                "pip": [sys.executable, "-m", "pip", "install", "--upgrade", "fetchart"],
                "git": ["git", "-C", str(where), "pull", "--ff-only"]}
    if kind in commands:
        print("Running: " + subprocess.list2cmdline(commands[kind]))
        try:
            code = subprocess.call(commands[kind])
        except OSError as e:
            sys.exit(f"Couldn't run it: {e}")
        sys.exit(code)
    # The standalone program replaces itself. A running .exe can't be overwritten on Windows, but it can be renamed.
    asset = RELEASE_ASSETS.get(os.name if os.name == "nt" else sys.platform)
    if not asset:
        sys.exit(f"There's no standalone build for this system. Get it from {RELEASES}")
    new = where.with_name(where.name + ".new")
    try:
        new.write_bytes(http_get(f"{RELEASES}/download/v{latest}/{asset}", timeout=300, progress=True))
        if os.name != "nt":
            new.chmod(0o755)
        old = where.with_name(where.name + ".old")
        old.unlink(missing_ok=True)
        where.rename(old)
        new.rename(where)
    except (urllib.error.URLError, OSError) as e:
        new.unlink(missing_ok=True)
        sys.exit(f"Couldn't update: {e}\nDownload it yourself from {RELEASES}")
    print(f"Updated to fetchart {latest}.")


def clean_old_exe():
    """Delete the copy a self-update left behind (it was still running at the time)."""
    if getattr(sys, "frozen", False):
        try:
            Path(sys.executable + ".old").unlink(missing_ok=True)
        except OSError:
            pass


def check_for_update(cfg):
    """Look for a newer fetchart at most once a day, in the background. Returns a function that prints a
    one-line notice if there is one."""
    found = []
    asked = time.time() - cfg.get("update_checked", 0) >= 86400 and sys.stdout.isatty()
    if not asked:
        latest = cfg.get("update_latest")
        found.append(latest)
    else:
        threading.Thread(target=lambda: found.append(latest_version()), daemon=True).start()

    def notice():
        latest = found[0] if found else None
        if latest and asked:  # saved here, not in the thread, so it can't clash with fetchart's own saves
            fresh = load_config()
            fresh.update(update_checked=time.time(), update_latest=latest)
            save_config(fresh)
        if latest and version_tuple(latest) > version_tuple(__version__) and sys.stdout.isatty():
            print(f"\nfetchart {latest} is out (you have {__version__}). Update: fetchart --upgrade")
    return notice


def run():
    """Entry point for the standalone build: keep the window open when it was double-clicked."""
    double_clicked = getattr(sys, "frozen", False) and len(sys.argv) == 1
    clean_old_exe()
    quiet = len(sys.argv) > 1 and sys.argv[1] in ("--completion", "--daily-run", "--upgrade", "--version", "-h", "--help")
    notice = (lambda: None) if quiet else check_for_update(load_config())
    try:
        main()
        notice()
    finally:
        if double_clicked:
            try:
                input("\nPress Enter to close...")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    run()
