# fetchart

Find and download game art, movie and TV posters, album covers and paintings from the command line. No API keys or accounts are needed.

For games, it covers about 190,000 titles on every platform: box covers, key art (with or without the logo), see-through logos, console dashboard tiles, and achievement and trophy icons for Xbox, PlayStation, Steam and retro consoles. It can also fill in Steam's library art for your non-Steam games, put posters in your movie folders, and set any of it as your wallpaper.

![fetchart finding the Xbox 360 box art for Modern Warfare 2](docs/demo.gif)

Just type `fetchart` in PowerShell, Command Prompt or any terminal, and pick what you're looking for: a video game, a movie or TV show, music, a painting, a whole folder of games or movies, or art for your non-Steam games.

Type the name, then pick the right one from the matches. Each game appears once, and you pick the platform after it. Then pick the kind of art, choose from every image found (biggest first), and pick a folder. For key art, also pick portrait or landscape. fetchart remembers the last folder you used.

**Controls:** use the arrow keys or click with the mouse, and press Enter. Typing a number jumps to it. On the image and achievement lists, press **P** to see every option as a numbered picture in your browser. For achievements, Space (or a click) ticks as many as you want, and A ticks them all. Mouse clicks work in the Windows console; on macOS and Linux, use the keys.

### A whole folder of games

![fetchart getting box art for a folder of N64 games](docs/folder.gif)

Point fetchart at a folder of ROMs and it gets art for every game, saved under each ROM's own file name, as EmulationStation, RetroArch and other frontends expect:

```
fetchart --folder D:\ROMs\N64
```

- It reads No-Intro and Redump style names: `Legend of Zelda, The - Ocarina of Time (USA) (Rev 1).z64` is The Legend of Zelda: Ocarina of Time.
- The system comes from the file extension or the folder's name (`N64`, `snes`, `PS2`, `Sega Genesis`...). You can also give it: `fetchart --folder D:\ROMs\Discs ps1`.
- A `(Europe)` ROM gets the European box when there is one.
- Games that already have art are skipped, so running it again only fills the gaps.
- If a name isn't a sure match, fetchart asks you to pick the right game at the end, or skips it with `-y`.

The art goes in a `covers` folder inside the ROM folder (or `fanart`, `keyart`, `marquees` for logos, `icons` for the other kinds); `-o` picks another.

### A folder of movies or TV shows

Point it at your Plex, Jellyfin, Emby or Kodi library and every movie and show gets a poster:

```
fetchart --folder D:\Movies
```

- A movie or show in its own folder gets `poster.jpg` inside it. A loose movie file gets `<file name>-poster.jpg` next to it.
- It reads the names people actually use: `Inception (2010)`, `The.Matrix.1999.1080p.BluRay.x264`, `Dune (2021) {imdb-tt1160419}`.
- A folder with `Season 1` folders or `S01E01` files is a TV show.
- Anything that already has a poster is skipped. Anything that isn't a sure match (like `Dune` with no year) is asked about at the end.

In Plex, turn on **Use local assets** for the library so it picks the posters up.

### Steam art for non-Steam games

Games you add with **Add a Non-Steam Game** (emulators, Epic, Game Pass, old discs) show up in Steam as blank tiles. This fills them in:

```
fetchart --steam
```

Each game gets the four pictures Steam's library uses: the cover, the wide cover, the background, and the logo. Games that are also sold on Steam get the official Steam art, and everything else comes from LaunchBox. Art you already set yourself is never replaced. Restart Steam afterwards to see it.

If Steam isn't in its usual place, give its folder: `fetchart --steam "D:\Steam"`.

### Wallpaper

Add `-w` and fetchart sets the picture as your desktop background (Windows, macOS, and GNOME, KDE or Xfce on Linux):

```
fetchart halo-3 x360 -w
fetchart "the starry night" painting -w
```

For games it uses landscape key art without the logo (`-t logo` keeps the logo). For paintings it uses a 4K copy instead of the giant museum scan.

To go back to the wallpaper you had before, run `fetchart --undo-wallpaper`, even after several `-w` runs. fetchart keeps a copy of your old picture, so it works even if the original file is gone. A Windows slideshow or Spotlight comes back as the single picture that was showing.

## Install

### Windows, macOS, Linux: standalone download

Grab the file for your system from the [latest release](https://github.com/SamuelArther/fetchart/releases/latest):

| System  | File                   |
|---------|------------------------|
| Windows | `fetchart-windows.exe` |
| macOS   | `fetchart-macos`       |
| Linux   | `fetchart-linux`       |

You can double-click it and it will show the menus. To use `fetchart` as a command in any terminal, rename the file to `fetchart` (`fetchart.exe` on Windows) and put it in a folder on your `PATH`.

On macOS and Linux, make the file runnable first:

```
chmod +x fetchart-macos
```

On macOS you may also need to right-click it and choose **Open** the first time, because the build isn't signed.

### With Python (3.9+)

```
pipx install git+https://github.com/SamuelArther/fetchart
```

`pip install git+https://github.com/SamuelArther/fetchart` works too.

## Usage

Run `fetchart` with no arguments for the menus above. To skip them, put everything on one line:

```
fetchart <game> [platform] [options]
```

Game names can use dashes or quotes: `halo-3` and `"halo 3"` both work. If you leave out the platform, every platform is searched.

Short names work too: `mw2`, `bo2`, `botw`, `oot`, `gta5`, `ff7`, `csgo`, `ssbm`, or a mix like `mw2 remastered`. When a name was used twice, add the year: `mw2 2009` is the original Modern Warfare 2, and `mw2 2022` is the remake.

| Option | What it does |
|---|---|
| `-t box\|clean\|logo\|clearlogo\|icon\|ach\|wallpaper` | Skip the art-type menu |
| `-r portrait\|landscape` | Orientation, for key art |
| `-q low\|medium\|high\|all\|1920x1080` | Skip the "which one" menu |
| `-o FOLDER` | Where to save |
| `-y` | No prompts: take the top match and the best image |
| `-w` | Set the picture as your desktop wallpaper |
| `--undo-wallpaper` | Put back the wallpaper you had before |
| `-f FOLDER` | Get art for every game, movie or show in a folder (see above) |
| `--steam` | Fill in Steam's library art for your non-Steam games (see above) |
| `--update` | Re-download the game database |

### Examples

```
fetchart super-mario-odyssey switch -t box
fetchart halo-3 x360 -t icon -q all -o "D:\Art\Halo 3"
fetchart zelda-ocarina-of-time n64 -t clean -r portrait
fetchart gears-of-war -t ach
fetchart uncharted-2 ps3 -t ach
fetchart hollow-knight pc -t clearlogo
```

### Movies, TV, albums and paintings

Use `movie`, `tv`, `album` or `painting` in place of a platform:

```
fetchart inception movie
fetchart breaking-bad tv
fetchart "pink floyd dark side of the moon" album
fetchart "the starry night" painting
```

| Type | Where it comes from | Typical size |
|---|---|---|
| Movie / TV posters | IMDb, Wikipedia | up to 2000x3000 |
| Album covers | Apple Music, Deezer, Cover Art Archive | 1200–3000 px |
| Paintings | Wikimedia Commons museum scans | the full scan, plus 3840 and 1920 wide copies |

Some museum scans are huge (The Starry Night is 663 MB), so the menu shows the file size of big originals.

For albums, the artist plus the album name finds the right one fastest.

### Platforms

`pc`, `xbox`, `x360`, `xboxone`, `series`, `ps1`, `ps2`, `ps3`, `ps4`, `ps5`, `psp`, `vita`, `nes`, `snes`, `n64`, `gc`, `wii`, `wiiu`, `switch`, `switch2`, `gb`, `gbc`, `gba`, `ds`, `3ds`, `genesis`, `sms`, `gg`, `saturn`, `dc`, `segacd`, `32x`, `tg16`, `neogeo`, `2600`, `jaguar`, `lynx`, `3do`, `arcade`, `dos`, `mac`, `linux`, `android`, `ios`.

Full names work too, e.g. `"Sega Dreamcast"`.

## Art types

| Type | What you get | Where it comes from |
|---|---|---|
| **box** | The front cover from every region, rating and all | LaunchBox Games Database |
| **clean** | Key art without the logo. Landscape, or a portrait crop | LaunchBox fanart, Steam library art |
| **logo** | Key art with the logo | Steam capsules, LaunchBox banners and posters, or fanart with the game's logo laid on top |
| **clearlogo** | Just the logo, as a PNG with a see-through background | Steam library logos, LaunchBox clear logos |
| **icon** | The square tile on the console's menu | See below |
| **ach** | Achievement or trophy icons, listed by name, score and description. Pick one, a few (`1,4,9` or `10-20`), or all of them | See below |
| **wallpaper** | The best landscape key art, set as your desktop background | Same as **clean** |

Achievements and trophies, by platform:

| Platform | What you get | Where it comes from |
|---|---|---|
| Xbox 360 | Achievements with gamerscore, plus the game icon | Original icons from the Xbox Live image server, names from Exophase |
| Xbox One / Series | Achievements with gamerscore | Exophase |
| PS3 / PS4 / PS5 / Vita | Trophies with their grade (Platinum, Gold...), at 240x240 | Exophase |
| PC | Steam achievements (or Xbox PC, GOG, Epic) | Exophase |
| NES, SNES, N64, GameCube, Wii, Game Boy, DS, Genesis, PS1, PS2, PSP and other retro systems | [RetroAchievements](https://retroachievements.org/) sets | Exophase |

Where the **icon** art comes from, by platform:

| Platform | Icons |
|---|---|
| Xbox 360 / original Xbox | The back-compat tile with the green console stripe, the 360 dashboard banner, the dashboard box art, and the game icon from the guide (plus an upscaled copy) |
| Xbox One / Series / PC | The Microsoft Store square tile |
| Switch / 3DS / Wii U | The eShop icon |
| PC (Steam) | The Steam library icon |

## How it works

The first run downloads the public [LaunchBox Games Database](https://gamesdb.launchbox-app.com/) dump (~110 MB). It indexes the dump into a local SQLite file, and then refreshes it every 30 days. That takes about 20 seconds.

The index is stored at:

| System | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\fetchart` |
| macOS / Linux | `~/.cache/fetchart` |

Xbox 360 title IDs are looked up through [XboxUnity](http://xboxunity.net/).

## Credits and disclaimer

The art comes from the LaunchBox Games Database community, Steam, the Microsoft Store, Xbox Live, XboxUnity, Exophase, RetroAchievements, Nintendo, IMDb, Wikipedia, Wikimedia Commons, Deezer, Apple Music, and MusicBrainz / the Cover Art Archive. Thanks to all of them for keeping it online.

All art belongs to its respective owners. fetchart is meant for personal use, such as emulator frontends, media servers, desktop wallpapers and collection catalogs. It is not affiliated with any of the sites it pulls from, or with any game, film or music company.

## License

[MIT](LICENSE)
