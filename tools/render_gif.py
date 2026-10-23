"""Turn a console_drive.py recording (FRAMES=dir) into a terminal-style GIF for the README.

usage: render_gif.py FRAMES_DIR OUT.gif --prompt "fetchart" [--replace OLD=NEW ...] [--preview SCREENSHOT.png]

--prompt   the command shown at the top, as if typed at a PowerShell prompt
--replace  text swapped in every line, e.g. to hide local paths
--preview  a browser screenshot shown right after the step that pressed P
"""
import argparse
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

COLS, ROWS, FONT_SIZE, PAD = 104, 24, 15, 16
# Windows Terminal's "Campbell" scheme, in console attribute order (blue = 1, green = 2, red = 4, bright = 8).
PALETTE = ["#0C0C0C", "#0037DA", "#13A10E", "#3A96DD", "#C50F1F", "#881798", "#C19C00", "#CCCCCC",
           "#767676", "#3B78FF", "#16C60C", "#61D6D6", "#E74856", "#B4009E", "#F9F1A5", "#F2F2F2"]
BG, CHROME, HIGHLIGHT, DIM, PROMPT = "#0C0C0C", "#202020", "#2563EB", "#8A8A8A", "#3A96DD"
PS = "PS C:\\Users\\you> "
WAITS = {"type": 70}  # ms per frame by step; other steps get 650, waitN gets N seconds (capped)
MAX_WAIT = 1400  # downloads take longer for real; nobody needs to watch that in a GIF


def font():
    for name in ("CascadiaMono.ttf", "consola.ttf"):
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default()


def rows_for(frame, prompt, replace):
    """The visible rows of one frame as [(text, [attr per char])], prompt line first, long lines wrapped."""
    out = [(PS + prompt, None)]
    lines = frame["lines"]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    for text, attrs in zip(lines, frame["attrs"]):
        new = text
        for old, rep in replace:
            new = new.replace(old, rep)
        attrs = attrs[:len(text)] if new == text else [attrs[0] if attrs else 7] * len(new)
        for i, piece in enumerate(textwrap.wrap(new, COLS, drop_whitespace=False) or [""]):
            out.append((piece, attrs[i * COLS:(i + 1) * COLS]))
    return out[-ROWS:]


def draw_terminal(rows, fnt, cell_w, cell_h):
    width, height = COLS * cell_w + PAD * 2, ROWS * cell_h + PAD * 2 + 32
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, 32], fill=CHROME)
    for i, color in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        d.ellipse([14 + i * 20, 11, 26 + i * 20, 23], fill=color)
    d.text((width // 2, 16), "Windows PowerShell", fill="#BBBBBB", font=fnt, anchor="mm")
    for r, (text, attrs) in enumerate(rows):
        y = 32 + PAD + r * cell_h
        if attrs is None:  # the prompt line
            d.text((PAD, y), text[:len(PS)], fill=PROMPT, font=fnt)
            d.text((PAD + len(PS) * cell_w, y), text[len(PS):], fill="#F2F2F2", font=fnt)
            continue
        reverse = any(a & 0x4000 for a in attrs)
        if reverse:  # the highlighted menu row
            d.rectangle([PAD - 4, y, PAD + len(text.rstrip()) * cell_w + 4, y + cell_h - 1], fill=HIGHLIGHT)
            d.text((PAD, y), text, fill="#FFFFFF", font=fnt)
        elif text.startswith(("Up/Down", "Preview opened")):
            d.text((PAD, y), text, fill=DIM, font=fnt)
        else:
            for c, ch in enumerate(text):
                color = PALETTE[attrs[c] & 0xF] if c < len(attrs) else PALETTE[7]
                if ch != " ":
                    d.text((PAD + c * cell_w, y), ch, fill=color, font=fnt)
    return img


def browser_frame(shot, size):
    """The preview screenshot inside a simple browser window, the same size as the terminal frames."""
    img = Image.new("RGB", size, "#DADADA")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size[0], 32], fill="#E8E8E8")
    d.rounded_rectangle([90, 6, size[0] - 20, 26], 10, fill="#FFFFFF")
    d.text((104, 16), "fetchart preview  -  preview.html", fill="#555555", font=font(), anchor="lm")
    for i, color in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        d.ellipse([14 + i * 20, 11, 26 + i * 20, 23], fill=color)
    page = Image.open(shot).convert("RGB")
    page = page.resize((size[0], round(page.height * size[0] / page.width)), Image.LANCZOS)
    img.paste(page.crop((0, 0, size[0], size[1] - 32)), (0, 32))
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frames")
    ap.add_argument("out")
    ap.add_argument("--prompt", default="fetchart")
    ap.add_argument("--replace", action="append", default=[])
    ap.add_argument("--preview")
    a = ap.parse_args()
    replace = [tuple(r.split("=", 1)) for r in a.replace]
    frames = json.loads((Path(a.frames) / "frames.json").read_text(encoding="utf-8"))
    fnt = font()
    ascent, descent = fnt.getmetrics()
    cell_w, cell_h = round(fnt.getlength("M")), ascent + descent + 3

    # A few frames of just the prompt being "typed" before the program's own output.
    images, durations = [], []
    blank = {"lines": [], "attrs": []}
    for i in range(0, len(a.prompt) + 1, 2):
        images.append(draw_terminal([(PS + a.prompt[:i], None)], fnt, cell_w, cell_h))
        durations.append(60)
    durations[-1] = 500
    last = None
    for frame in frames:
        rows = rows_for(frame, a.prompt, replace) if frame["lines"] else rows_for(blank, a.prompt, replace)
        step = frame["step"]
        ms = WAITS.get(step, min(MAX_WAIT, int(float(step[4:]) * 1000)) if step.startswith("wait") else 650)
        if rows == last:  # nothing changed on screen: just hold the previous frame longer
            durations[-1] += ms
        else:
            images.append(draw_terminal(rows, fnt, cell_w, cell_h))
            durations.append(ms)
            last = rows
        if step == "p" and a.preview:
            images.append(browser_frame(a.preview, images[-1].size))
            durations.append(3800)
            last = None
    durations[-1] += 2500
    pal = [im.convert("P", palette=Image.ADAPTIVE, colors=64) for im in images]
    pal[0].save(a.out, save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True,
                disposal=1)
    print(f"{a.out}: {len(pal)} frames, {sum(durations) / 1000:.1f}s, {Path(a.out).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
