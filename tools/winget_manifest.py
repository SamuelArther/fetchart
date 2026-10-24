#!/usr/bin/env python3
"""Generate the winget manifest set for SamuelArther.fetchart.

Usage:
    python tools/winget_manifest.py 1.6.0 [--out DIR]

Downloads the release exe to hash it and writes
    <out>/manifests/s/SamuelArther/fetchart/<version>/
        SamuelArther.fetchart.yaml
        SamuelArther.fetchart.installer.yaml
        SamuelArther.fetchart.locale.en-US.yaml
<out> defaults to this script's directory. Validate with
    winget validate --manifest <that dir>
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request

PACKAGE_ID = "SamuelArther.fetchart"
REPO = "SamuelArther/fetchart"
EXE_ASSET = "fetchart-windows.exe"
MANIFEST_VERSION = "1.12.0"
HEADER = "# yaml-language-server: $schema=https://aka.ms/winget-manifest.{kind}.{ver}.schema.json\n\n"

SHORT_DESCRIPTION = "Download game art, achievements, posters, album covers and more from the command line"
DESCRIPTION = (
    "fetchart is a command-line tool that downloads artwork, including game art for every platform, "
    "Xbox 360 tiles and achievement icons, movie and TV posters, and album covers."
)
TAGS = ["art", "box-art", "cli", "cover-art", "game-art", "posters", "album-art", "achievements"]


def get(url, accept=None):
    req = urllib.request.Request(url, headers={"User-Agent": "winget-manifest-maker"})
    if accept:
        req.add_header("Accept", accept)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="fetchart version, e.g. 1.6.0 (no leading v)")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()
    version = args.version.lstrip("v")
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        raise SystemExit(f"bad version: {version}")

    release = json.loads(get(f"https://api.github.com/repos/{REPO}/releases/tags/v{version}", "application/json"))
    asset = next((a for a in release["assets"] if a["name"] == EXE_ASSET), None)
    if not asset:
        raise SystemExit(f"v{version} has no {EXE_ASSET} asset yet")
    url = asset["browser_download_url"]

    print(f"downloading {url}")
    sha = hashlib.sha256(get(url)).hexdigest().upper()
    digest = (asset.get("digest") or "").removeprefix("sha256:").upper()
    if digest and digest != sha:
        raise SystemExit(f"hash mismatch: downloaded {sha}, GitHub says {digest}")
    release_date = (release.get("published_at") or datetime.date.today().isoformat())[:10]

    files = {
        f"{PACKAGE_ID}.yaml": HEADER.format(kind="version", ver=MANIFEST_VERSION) + f"""\
PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: {MANIFEST_VERSION}
""",
        f"{PACKAGE_ID}.installer.yaml": HEADER.format(kind="installer", ver=MANIFEST_VERSION) + f"""\
PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
InstallerType: portable
Commands:
- fetchart
ReleaseDate: {release_date}
Installers:
- Architecture: x64
  InstallerUrl: {url}
  InstallerSha256: {sha}
ManifestType: installer
ManifestVersion: {MANIFEST_VERSION}
""",
        f"{PACKAGE_ID}.locale.en-US.yaml": HEADER.format(kind="defaultLocale", ver=MANIFEST_VERSION) + f"""\
PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
PackageLocale: en-US
Publisher: Samuel Arther
PublisherUrl: https://github.com/SamuelArther
PublisherSupportUrl: https://github.com/{REPO}/issues
Author: Samuel Arther
PackageName: fetchart
PackageUrl: https://github.com/{REPO}
License: MIT
LicenseUrl: https://github.com/{REPO}/blob/main/LICENSE
ShortDescription: {SHORT_DESCRIPTION}
Description: {DESCRIPTION}
Moniker: fetchart
Tags:
""" + "".join(f"- {t}\n" for t in TAGS) + f"""\
ReleaseNotesUrl: https://github.com/{REPO}/releases/tag/v{version}
ManifestType: defaultLocale
ManifestVersion: {MANIFEST_VERSION}
""",
    }

    out_dir = os.path.join(args.out, "manifests", "s", "SamuelArther", "fetchart", version)
    os.makedirs(out_dir, exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    print(f"wrote {out_dir}")
    print(f"sha256 {sha}")
    print(f'validate: winget validate --manifest "{out_dir}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
