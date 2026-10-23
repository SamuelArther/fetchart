# fetchart

Find and download game art, movie and TV posters, album covers and paintings from the command line. No API keys or accounts are needed.

For games, it covers about 190,000 titles on every platform: box covers, key art (with or without the logo), console dashboard tiles, and every Xbox 360 achievement icon.

Just type `fetchart` in PowerShell, Command Prompt or any terminal:

```
What are you looking for?
  1) Video game
  2) Movie or TV show
  3) Music (album cover)
  4) Painting / real art
```

Type the name, then pick the right one from the matches. Each game appears once, and you pick the platform after it:

```
Which game?
  1) DOOM  [23 platforms]
  2) DOOM 3  [7 platforms]
  ...
Which platform is DOOM on?
  1) Windows (2016)
  2) MS-DOS (1993)
  3) Super Nintendo Entertainment System (1995)
  ...
What art do you want?
  1) Box cover art (the retail cover, rating and all)
  2) Key art, no logo
  3) Key art, with logo
  4) Dashboard icon / tile (the square on the console menu)
```

For key art, also pick portrait or landscape. Then choose from a list of every image found, biggest first, and pick a folder. fetchart remembers the last folder you used.

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
| `-t box\|clean\|logo\|icon\|ach` | Skip the art-type menu |
| `-r portrait\|landscape` | Orientation, for key art |
| `-q low\|medium\|high\|all\|1920x1080` | Skip the "which one" menu |
| `-o FOLDER` | Where to save |
| `-y` | No prompts: take the top match and the best image |
| `--update` | Re-download the game database |

### Examples

```
fetchart super-mario-odyssey switch -t box
fetchart halo-3 x360 -t icon -q all -o "D:\Art\Halo 3"
fetchart zelda-ocarina-of-time n64 -t clean -r portrait
fetchart gears-of-war -t ach
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
| **icon** | The square tile on the console's menu | See below |
| **ach** | Xbox 360 achievement icons, listed by name, gamerscore and description. Pick one, a few (`1,4,9` or `10-20`), or all of them, plus the game icon | Xbox Live image server, names from Exophase |

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

The art comes from the LaunchBox Games Database community, Steam, the Microsoft Store, Xbox Live, XboxUnity, Exophase, Nintendo, IMDb, Wikipedia, Wikimedia Commons, Deezer, Apple Music, and MusicBrainz / the Cover Art Archive. Thanks to all of them for keeping it online.

All art belongs to its respective owners. fetchart is meant for personal use, such as emulator frontends, media servers, desktop wallpapers and collection catalogs. It is not affiliated with any of the sites it pulls from, or with any game, film or music company.

## License

[MIT](LICENSE)
