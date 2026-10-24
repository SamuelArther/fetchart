"""Tests for fetchart. They run offline; set FETCHART_LIVE=1 to also check the real database and websites.

    python -m unittest discover -s tests -v
"""
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fetchart as fa  # noqa: E402

LIVE = os.environ.get("FETCHART_LIVE") == "1"
X360, PS4 = "Microsoft Xbox 360", "Sony Playstation 4"

# (id, name, platform, rating votes, year, alternate names): real titles plus the near-misses that
# once won searches they shouldn't have.
GAMES = [
    (1, "Call of Duty: Modern Warfare 2", X360, 400, 2009, []),
    (2, "Call of Duty: Modern Warfare II", "Windows", 150, 2022, []),
    (3, "Call of Duty: Modern Warfare 2 Campaign Remastered", PS4, 60, 2020, []),
    (4, "Metal Warriors II", "Super Nintendo Entertainment System", 3, 1995, []),
    (5, "Super Mario World 2: Yoshi's Island", "Super Nintendo Entertainment System", 300, 1995, []),
    (6, "Halo 3", X360, 500, 2007, []),
    (7, "Halo: The Master Chief Collection", "Microsoft Xbox One", 450, 2014, ["Halo 3"]),
    (8, "Final Fantasy VII", "Sony Playstation", 600, 1997, []),
    (9, "Final Fantasy VII Remake", PS4, 300, 2020, []),
    (10, "Grand Theft Auto V", PS4, 500, 2013, []),
    (11, "The Legend of Zelda: Breath of the Wild", "Nintendo Switch", 600, 2017, []),
]


def fixture_db():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE games (id INTEGER PRIMARY KEY, name TEXT, platform TEXT, votes INTEGER, year INTEGER);
        CREATE TABLE names (id INTEGER, name TEXT, norm TEXT, abbr TEXT);
        CREATE TABLE images (id INTEGER, type TEXT, region TEXT, file TEXT);
    """)
    for gid, name, plat, votes, year, aliases in GAMES:
        db.execute("INSERT INTO games VALUES (?,?,?,?,?)", (gid, name, plat, votes, year))
        for n in [name] + aliases:
            db.execute("INSERT INTO names VALUES (?,?,?,?)", (gid, n, fa.norm(n), fa.acronyms(n)))
    return db


def vdf(entries):
    """A binary shortcuts.vdf like Steam writes, from a list of {key: str or int}."""
    def s(text):
        return text.encode() + b"\0"
    out = b"\0" + s("shortcuts")
    for i, entry in enumerate(entries):
        out += b"\0" + s(str(i))
        for key, value in entry.items():
            if isinstance(value, int):
                out += b"\x02" + s(key) + struct.pack("<i", value)
            else:
                out += b"\x01" + s(key) + s(value)
        out += b"\0" + s("tags") + b"\x08"  # an empty nested table, as every real entry has
        out += b"\x08"
    return out + b"\x08\x08"


def mp3(artist, album, v4=False):
    """The start of an MP3: an ID3v2 tag with artist and album frames (UTF-16 artist, Latin-1 album)."""
    def size(n):
        return bytes([n >> 21 & 127, n >> 14 & 127, n >> 7 & 127, n & 127]) if v4 else n.to_bytes(4, "big")

    def frame(fid, body):
        return fid + size(len(body)) + b"\0\0" + body
    frames = frame(b"TPE1", b"\x01" + artist.encode("utf-16")) + frame(b"TALB", b"\x00" + album.encode("latin-1"))
    head = b"ID3" + bytes([4 if v4 else 3, 0, 0])
    n = len(frames)
    return head + bytes([n >> 21 & 127, n >> 14 & 127, n >> 7 & 127, n & 127]) + frames + b"\xff\xfb" + b"\0" * 64


def flac(**comments):
    """The start of a FLAC file: STREAMINFO, then a Vorbis comment block."""
    body = struct.pack("<I", 4) + b"test" + struct.pack("<I", len(comments))
    for key, value in comments.items():
        entry = f"{key}={value}".encode()
        body += struct.pack("<I", len(entry)) + entry
    return (b"fLaC" + bytes([0]) + (34).to_bytes(3, "big") + b"\0" * 34
            + bytes([0x84]) + len(body).to_bytes(3, "big") + body)


class Names(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(fa.norm("Pokémon: Red & Blue (USA)"), "pokemon red and blue")

    def test_acronyms(self):
        self.assertIn("mw2", fa.acronyms("Call of Duty: Modern Warfare 2").split())
        self.assertIn("codmw2", fa.acronyms("Call of Duty: Modern Warfare 2").split())
        self.assertIn("ff7", fa.acronyms("Final Fantasy VII").split())
        self.assertIn("gta5", fa.acronyms("Grand Theft Auto V").split())
        self.assertIn("botw", fa.acronyms("The Legend of Zelda: Breath of the Wild").split())

    def test_split_year(self):
        self.assertEqual(fa.split_year("mw2 2009"), ("mw2", 2009))
        self.assertEqual(fa.split_year("halo 3"), ("halo 3", None))
        self.assertEqual(fa.split_year("1942"), ("1942", None))

    def test_safe_filename(self):
        self.assertEqual(fa.safe_filename("Minecraft: Xbox 360 Edition"), "Minecraft - Xbox 360 Edition")
        self.assertEqual(fa.safe_filename('a/b?c"d'), "a_b_c_d")

    def test_platform_short_names(self):
        self.assertEqual(fa.match_platform(None, "x360"), X360)
        self.assertEqual(fa.match_platform(None, "Series"), "Microsoft Xbox Series X/S")
        self.assertNotIn("series", fa.MEDIA)  # "series" is the Xbox, not a TV show


class Search(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = fixture_db()

    def top(self, query, platform=None):
        return fa.search_games(self.db, query, platform, None)[0][1]

    def test_short_names(self):
        self.assertEqual(self.top("mw2"), "Call of Duty: Modern Warfare 2")
        self.assertEqual(self.top("ff7"), "Final Fantasy VII")
        self.assertEqual(self.top("gta5"), "Grand Theft Auto V")
        self.assertEqual(self.top("botw"), "The Legend of Zelda: Breath of the Wild")

    def test_year_picks_the_release(self):
        self.assertEqual(self.top("mw2 2022"), "Call of Duty: Modern Warfare II")
        self.assertEqual(self.top("mw2 2009"), "Call of Duty: Modern Warfare 2")

    def test_mixed_short_name(self):
        self.assertEqual(self.top("mw2 remastered"), "Call of Duty: Modern Warfare 2 Campaign Remastered")

    def test_own_name_beats_collection_alias(self):
        self.assertEqual(self.top("halo 3"), "Halo 3")

    def test_platform_filter(self):
        self.assertEqual(self.top("final fantasy vii", PS4), "Final Fantasy VII Remake")

    def test_full_name_is_a_sure_match(self):
        self.assertGreaterEqual(fa.search_games(self.db, "Halo 3", X360, None)[0][5], fa.SURE_MATCH)

    def test_grouping(self):
        groups = fa.group_games(fa.search_games(self.db, "final fantasy vii", None, None))
        self.assertEqual([g[0][1] for g in groups][:2], ["Final Fantasy VII", "Final Fantasy VII Remake"])


class Folders(unittest.TestCase):
    def test_rom_title(self):
        self.assertEqual(fa.rom_title("Legend of Zelda, The - Ocarina of Time (USA) (Rev 1)"),
                         "The Legend of Zelda - Ocarina of Time")
        self.assertEqual(fa.rom_title("Super_Mario_64_[!]"), "Super Mario 64")

    def test_rom_region(self):
        self.assertEqual(fa.rom_region("Tetris (World) (Rev 1)"), "World")
        self.assertEqual(fa.rom_region("Pokemon - Emerald Version (USA, Europe)"), "North America")
        self.assertIsNone(fa.rom_region("Some Homebrew v1.2 (PD)"))

    def test_scan_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ["FF7 (Disc 1).cue", "FF7 (Disc 1).bin", "Crash (USA).cue", "Crash (USA) (Track 1).bin",
                         "Tetris (World).gb", "readme.txt", "cover.jpg", ".hidden.gb"]:
                (Path(tmp) / name).write_bytes(b"")
            self.assertEqual(fa.scan_folder(Path(tmp)), [("Crash (USA)", None), ("FF7 (Disc 1)", None),
                                                         ("Tetris (World)", "Nintendo Game Boy")])

    def test_video_title(self):
        cases = {
            "Inception (2010)": ("Inception", 2010, None),
            "The.Matrix.1999.1080p.BluRay.x264-GRP": ("The Matrix", 1999, None),
            "Blade Runner 2049 (2017)": ("Blade Runner 2049", 2017, None),
            "2001.A.Space.Odyssey.1968.REMUX": ("2001 A Space Odyssey", 1968, None),
            "Dune (2021) {imdb-tt1160419}": ("Dune", 2021, "tt1160419"),
            "The Office (US)": ("The Office", None, None),
            "Inception.1080p.BluRay": ("Inception", None, None),
        }
        for name, expected in cases.items():
            self.assertEqual(fa.video_title(name), expected, name)

    def test_scan_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ["Inception (2010)/Inception (2010).mkv", "Spirited Away (2001).avi",
                         "Breaking Bad/Season 01/Breaking.Bad.S01E01.mkv", "Loose.Show.S02E03.mkv", "notes.txt",
                         "Empty Folder/readme.txt"]:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(b"")
            self.assertTrue(fa.looks_like_videos(root))
            self.assertEqual(fa.scan_videos(root), [
                ("Breaking Bad", root / "Breaking Bad" / "poster.jpg", "tv"),
                ("Inception (2010)", root / "Inception (2010)" / "poster.jpg", "movie"),
                ("Spirited Away (2001)", root / "Spirited Away (2001)-poster.jpg", "movie"),
            ])
            # Pointed straight at one show's folder
            self.assertEqual(fa.scan_videos(root / "Breaking Bad"),
                             [("Breaking Bad", root / "Breaking Bad" / "poster.jpg", "tv")])

    def test_audio_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in [("a.mp3", mp3("Daft Punk", "Discovery")), ("b.mp3", mp3("Björk", "Homogenic", v4=True)),
                               ("c.flac", flac(ARTIST="Track Guy", ALBUMARTIST="Pink Floyd", ALBUM="Animals")),
                               ("d.mp3", b"\xff\xfb no tag")]:
                (Path(tmp) / name).write_bytes(data)
            tags = [fa.audio_tags(Path(tmp) / n) for n in ("a.mp3", "b.mp3", "c.flac", "d.mp3")]
            self.assertEqual(tags, [("Daft Punk", "Discovery"), ("Björk", "Homogenic"), ("Pink Floyd", "Animals"),
                                    (None, None)])

    def test_scan_music(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"Daft Punk/Discovery/01.mp3": mp3("Daft Punk", "Discovery"),
                     "Radiohead/OK Computer (1997) [FLAC]/01.flac": b"fLaC",
                     "Pink Floyd - The Wall/01.mp3": b"no tags", "Has Cover/01.mp3": mp3("A", "B")}
            for name, data in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(data)
            (root / "Has Cover" / "folder.jpg").write_bytes(b"")
            self.assertTrue(fa.looks_like_music(root))
            found = {name: (dest.relative_to(root).as_posix(), hint) for name, dest, hint in fa.scan_music(root)}
            self.assertEqual(found, {
                "Daft Punk - Discovery": ("Daft Punk/Discovery/cover.jpg", ("Daft Punk", "Discovery")),
                "Radiohead - OK Computer": ("Radiohead/OK Computer (1997) [FLAC]/cover.jpg", ("Radiohead", "OK Computer")),
                "Pink Floyd - The Wall": ("Pink Floyd - The Wall/cover.jpg", ("Pink Floyd", "The Wall")),
                "A - B": ("Has Cover/folder.jpg", ("A", "B")),
            })

    def test_rom_folder_is_not_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Mario Kart 64 (USA).z64").write_bytes(b"")
            self.assertFalse(fa.looks_like_videos(Path(tmp)))


class Art(unittest.TestCase):
    def test_crop_size(self):
        self.assertEqual(fa.crop_size((1920, 1080), True), (720, 1080))
        self.assertEqual(fa.crop_size((1920, 1080), 1920 / 620), (1920, 620))
        self.assertEqual(fa.crop_size((600, 900), True), (600, 900))
        self.assertEqual(fa.crop_size((1920, 1080), None), (1920, 1080))


class Steam(unittest.TestCase):
    def test_shortcuts(self):
        exe, name = '"C:\\Games\\Old\\old.exe"', "Old Game"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shortcuts.vdf"
            path.write_bytes(vdf([{"appid": -1234567890, "AppName": "Celeste", "Exe": '"C:\\Celeste.exe"'},
                                  {"AppName": name, "Exe": exe}]))  # saved before Steam stored the ID
            self.assertEqual(fa.steam_shortcuts(path), [
                (-1234567890 & 0xFFFFFFFF, "Celeste"),
                (zlib.crc32((exe + name).encode()) | 0x80000000, "Old Game"),
            ])

    def test_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for account in ("111", "222", "333"):
                (root / "userdata" / account / "config").mkdir(parents=True)
            for account in ("111", "222"):
                (root / "userdata" / account / "config" / "shortcuts.vdf").write_bytes(vdf([]))
            (root / "config").mkdir()
            (root / "config" / "loginusers.vdf").write_text(
                '"users"\n{\n\t"%d"\n\t{\n\t\t"PersonaName"\t\t"Sam"\n\t\t"MostRecent"\t\t"1"\n\t}\n}\n'
                % (fa.STEAM_ID_BASE + 222))
            self.assertEqual([(d.name, who) for d, who in fa.steam_accounts(root)], [("222", "Sam"), ("111", "111")])


class Wallpaper(unittest.TestCase):
    """set_wallpaper and undo_wallpaper against a pretend desktop, so the real one is never touched."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.saved = {name: getattr(fa, name) for name in ("wallpaper_state", "apply_wallpaper", "DATA_DIR", "CONFIG")}
        fa.DATA_DIR, fa.CONFIG = tmp / "data", tmp / "config.json"
        self.original = tmp / "my photo.jpg"
        self.original.write_bytes(b"photo")
        self.desktop = {"path": str(self.original), "style": "6", "tile": "0"}
        fa.wallpaper_state = lambda: dict(self.desktop)
        fa.apply_wallpaper = lambda state: self.desktop.clear() or self.desktop.update(state) or True
        self.art = []
        for name in ("halo.jpg", "elden ring.jpg"):
            (tmp / name).write_bytes(b"art")
            self.art.append(tmp / name)

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(fa, name, value)
        self.tmp.cleanup()

    def test_undo_after_two_wallpapers(self):
        cfg = {}
        fa.set_wallpaper(self.art[0], cfg)
        fa.set_wallpaper(self.art[1], cfg)
        self.assertEqual(Path(self.desktop["path"]).name, "elden ring.jpg")
        fa.undo_wallpaper(cfg)
        self.assertEqual(self.desktop, {"path": str(self.original), "style": "6", "tile": "0"})
        with self.assertRaises(SystemExit):  # nothing left to undo
            fa.undo_wallpaper(cfg)

    def test_undo_uses_the_copy_when_the_photo_is_gone(self):
        cfg = {}
        fa.set_wallpaper(self.art[0], cfg)
        self.original.unlink()
        fa.undo_wallpaper(cfg)
        self.assertEqual(Path(self.desktop["path"]).read_bytes(), b"photo")


class Daily(unittest.TestCase):
    """daily_run with the database, downloads and wallpaper faked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {n: getattr(fa, n) for n in ("open_index", "http_get", "set_wallpaper", "DATA_DIR", "CONFIG")}
        db = fixture_db()
        for gid in (6, 10):
            db.execute("INSERT INTO images VALUES (?, 'Fanart - Background', NULL, ?)", (gid, f"fanart{gid}.jpg"))
        fa.open_index = lambda *a: db
        fa.http_get = lambda url, **kw: b"\xff\xd8" + url.encode()
        self.set = []
        fa.set_wallpaper = lambda path, cfg: self.set.append(Path(path).read_bytes()) or True
        fa.DATA_DIR, fa.CONFIG = Path(self.tmp.name), Path(self.tmp.name) / "config.json"

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(fa, name, value)
        self.tmp.cleanup()

    def test_once_a_day_and_a_new_game_each_time(self):
        cfg = {"daily": {"games": [[6, "Halo 3", X360], [10, "Grand Theft Auto V", PS4]]}}
        fa.daily_run(cfg)
        fa.daily_run(cfg)  # the scheduler may start it again the same day
        self.assertEqual(len(self.set), 1)
        first = cfg["daily"]["game"]
        fa.daily_run(cfg, force=True)
        self.assertNotEqual(cfg["daily"]["game"], first)

    def test_popular_games(self):
        cfg = {"daily": {"popular": True}}
        fa.daily_run(cfg)
        self.assertIn(cfg["daily"]["game"], ("Halo 3", "Grand Theft Auto V"))  # the only games with fanart


class Exophase(unittest.TestCase):
    XBOX = """<ul><li class="award"><img class="award-image trophy-image" src="https://m.exophase.com/xbox/awards/s/ab1.png?v=1">
        <div class="award-title"><a href="#">Landfall</a></div><div class="award-description"><p>Finish &amp; win</p></div>
        <div class="award-points"><span>20</span></div></li></ul>"""
    PSN = """<li class="award"><img data-tippy-content="x" class="award-image trophy-image visible" src="https://m.exophase.com/psn/awards/s/85j51.png?a">
        <div class="award-title"><a href="#">Platinum</a></div><div class="award-description"><p>All of them</p></div>
        <div class="award-points"><i class="exo-icon exo-icon-trophy-platinum"></i></div>
        <div class="award-average"><span class="tippy">25.12%</span></div></li>"""

    def test_xbox(self):
        self.assertEqual(fa.parse_exophase_awards(self.XBOX, "xbox"), [{
            "name": "Landfall", "desc": "Finish & win", "badge": "20G",
            "small": "https://m.exophase.com/xbox/awards/s/ab1.png",
            "icon": "https://m.exophase.com/xbox/awards/o/ab1.png"}])

    def test_trophy_grade(self):
        trophy = fa.parse_exophase_awards(self.PSN, "psn")[0]
        self.assertEqual((trophy["name"], trophy["badge"]), ("Platinum", "Platinum"))
        self.assertTrue(trophy["icon"].endswith("/awards/o/85j51.png"))

    def test_retro_titles(self):
        self.assertEqual(fa.exophase_title("Legend of Zelda, The: Ocarina of Time"),
                         "The Legend of Zelda: Ocarina of Time")

    def test_every_retro_system_has_a_source(self):
        for plat in fa.RETRO_SYSTEMS:
            self.assertIn(plat, fa.ACH_SOURCES)


@unittest.skipUnless(LIVE and fa.DB_PATH.exists(), "set FETCHART_LIVE=1 (needs the downloaded database)")
class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = sqlite3.connect(fa.DB_PATH)

    def test_real_short_names(self):
        for query, name in [("mw2", "Call of Duty: Modern Warfare 2"), ("ff7", "Final Fantasy VII"),
                            ("halo 3", "Halo 3"), ("botw", "The Legend of Zelda: Breath of the Wild")]:
            self.assertEqual(fa.search_games(self.db, query, None, None)[0][1], name, query)

    def test_achievements(self):
        self.assertGreater(len(fa.exophase_achievements("Minecraft: Xbox 360 Edition", X360)), 80)
        self.assertGreater(len(fa.exophase_achievements("Uncharted 2: Among Thieves", "Sony Playstation 3")), 60)
        self.assertGreater(len(fa.exophase_achievements("Super Mario 64", "Nintendo 64")), 50)


if __name__ == "__main__":
    unittest.main()
