"""Make empty fake libraries for testing folder mode: ROMs with real No-Intro / Redump style file names,
and movies and shows named the ways people name them.

usage: make_roms.py OUT_DIR   (OUT_DIR is deleted and rebuilt)
"""
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
shutil.rmtree(root, ignore_errors=True)
sets = {
    "N64": [
        "Legend of Zelda, The - Ocarina of Time (USA) (Rev 1).z64",
        "Legend of Zelda, The - Majora's Mask (USA).z64",
        "Mario Kart 64 (USA).z64",
        "Super Mario 64 (Europe) (En,Fr,De).z64",
        "GoldenEye 007 (USA).z64",
        "Conker's Bad Fur Day (USA).z64",
        "Banjo-Kazooie (USA) (Rev 1).z64",
        "Pokemon Stadium 2 (USA).z64",
        "Super Smash Bros. (USA).z64",
        "Paper Mario (USA).z64",
        "Some Homebrew Thing v1.2 (PD).z64",
        "readme.txt",
    ],
    "mixed": [
        "Pokemon - Emerald Version (USA, Europe).gba",
        "Tetris (World) (Rev 1).gb",
        "Super Mario World (USA).sfc",
        "Sonic The Hedgehog 2 (World).gen",
        "Final Fantasy VII (USA) (Disc 1).cue",
        "Final Fantasy VII (USA) (Disc 1).bin",
        "Final Fantasy VII (USA) (Disc 2).cue",
        "Final Fantasy VII (USA) (Disc 2).bin",
        "Crash Bandicoot (USA).cue",
        "Crash Bandicoot (USA) (Track 1).bin",
        "Mario Kart - Super Circuit (Europe).gba",
        "cover.jpg",
    ],
}
sets["Movies"] = [
    "Inception (2010)/Inception (2010).mkv",
    "The.Matrix.1999.1080p.BluRay.x264-GRP.mkv",
    "Blade Runner 2049 (2017).mp4",
    "Dune (2021) {imdb-tt1160419}/Dune.mkv",
    "Dune/Dune.mkv",
    "Spirited Away (2001).avi",
    "Breaking Bad/Season 01/Breaking.Bad.S01E01.mkv",
    "The Office (US)/Season 1/The Office - 1x01 - Pilot.mp4",
    "Our Holiday 2015.mp4",
    "notes.txt",
]
for folder, names in sets.items():
    for name in names:
        path = root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
print(root)
