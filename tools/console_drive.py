"""Run fetchart in a hidden real console, send keys and mouse clicks into it, and print the screen.
Windows only. Used to test the menus for real and to record the README demos (see render_gif.py).

usage: console_drive.py STEPS [fetchart args...]
STEPS is comma-separated: a key name (up, down, end, enter, space, esc), waitN (seconds),
click=TEXT (left-click the screen row containing TEXT), wheeldown / wheelup, or text to type (_ = space).
Set FRAMES=dir to also save a (text, attributes) snapshot after every step for the demo GIF.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes as w

k = ctypes.WinDLL("kernel32", use_last_error=True)


class KEY(ctypes.Structure):
    _fields_ = [("bKeyDown", w.BOOL), ("wRepeatCount", w.WORD), ("wVirtualKeyCode", w.WORD),
                ("wVirtualScanCode", w.WORD), ("uChar", w.WCHAR), ("dwControlKeyState", w.DWORD)]


class COORD(ctypes.Structure):
    _fields_ = [("X", w.SHORT), ("Y", w.SHORT)]


class MOUSE(ctypes.Structure):
    _fields_ = [("pos", COORD), ("buttons", w.DWORD), ("state", w.DWORD), ("flags", w.DWORD)]


class EVENT(ctypes.Union):
    _fields_ = [("key", KEY), ("mouse", MOUSE)]


class REC(ctypes.Structure):
    _fields_ = [("EventType", w.WORD), ("Event", EVENT)]


class INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", w.WORD),
                ("srWindow", w.SHORT * 4), ("dwMaximumWindowSize", COORD)]


env = dict(os.environ, BROWSER=os.environ.get("BROWSER", "cmd /c rem %s"))  # never open a real browser
si = subprocess.STARTUPINFO()
si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
si.wShowWindow = 0  # SW_HIDE
FETCHART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fetchart.py")
proc = subprocess.Popen([sys.executable, FETCHART] + sys.argv[2:],
                        creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si, env=env)
time.sleep(2)
k.FreeConsole()
assert k.AttachConsole(proc.pid), ctypes.get_last_error()
open_ = lambda name: k.CreateFileW(name, 0xC0000000, 3, None, 3, 0, None)
cin, cout = open_("CONIN$"), open_("CONOUT$")
k.SetConsoleScreenBufferSize(cout, COORD(200, 3000))  # wide enough that long paths don't wrap

KEYS = {"up": (0x26, 0x48, "\0", 0x100), "down": (0x28, 0x50, "\0", 0x100), "end": (0x23, 0x4F, "\0", 0x100),
        "enter": (0x0D, 0x1C, "\r", 0), "space": (0x20, 0x39, " ", 0), "esc": (0x1B, 0x01, "\x1b", 0)}


def send(*records):
    arr = (REC * len(records))(*records)
    k.WriteConsoleInputW(cin, arr, len(records), ctypes.byref(w.DWORD()))


def press(vk, scan, ch, state):
    send(*(REC(1, EVENT(key=KEY(down, 1, vk, scan, ch, state))) for down in (1, 0)))


def mouse(row, buttons, flags):
    send(REC(2, EVENT(mouse=MOUSE(COORD(5, row), buttons, 0, flags))))


def screen(attrs=False):
    info = INFO()
    k.GetConsoleScreenBufferInfo(cout, ctypes.byref(info))
    width, rows = info.dwSize.X, info.dwCursorPosition.Y + 1
    buf = ctypes.create_unicode_buffer(width * rows)
    k.ReadConsoleOutputCharacterW(cout, buf, width * rows, COORD(0, 0), ctypes.byref(w.DWORD()))
    lines = [buf.value[i:i + width].rstrip() for i in range(0, width * rows, width)]
    if not attrs:
        return lines
    att = (w.WORD * (width * rows))()
    k.ReadConsoleOutputAttribute(cout, att, width * rows, COORD(0, 0), ctypes.byref(w.DWORD()))
    return lines, [list(att[i:i + width]) for i in range(0, width * rows, width)]


frames_dir = os.environ.get("FRAMES")
frames = []


def snap(label):
    if frames_dir:
        lines, att = screen(attrs=True)
        frames.append({"step": label, "lines": lines, "attrs": att})


for step in sys.argv[1].split(","):
    if step.startswith("wait"):
        time.sleep(float(step[4:]))
        snap(step)
        continue
    if step in KEYS:
        press(*KEYS[step])
    elif step.startswith("click="):
        text = step[6:].replace("_", " ")
        row = max(i for i, line in enumerate(screen()) if text in line)
        mouse(row, 1, 0)
        mouse(row, 0, 0)
    elif step in ("wheeldown", "wheelup"):
        mouse(0, (0xFF880000 if step == "wheeldown" else 0x00780000), 4)
    else:
        for ch in step.replace("_", " "):
            press(0, 0, ch, 0)
            if frames_dir:
                time.sleep(0.12)
                snap("type")
    time.sleep(0.6)
    snap(step)
time.sleep(1)
out = "\n".join(screen()).rstrip()
proc.wait(timeout=90)
k.FreeConsole()
if frames_dir:
    os.makedirs(frames_dir, exist_ok=True)
    with open(os.path.join(frames_dir, "frames.json"), "w", encoding="utf-8") as f:
        json.dump(frames, f)
print(out)
print("--- exit", proc.returncode)
