import ctypes
import gc
import importlib.util
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.request
import wave
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

if os.name != "nt":
    sys.exit("rec.py is built for Windows")

import msvcrt

# ---- splash: the logo fades in and a shine runs through it while the heavy libraries load ----
# Only the standard library is used here, so it can start before numpy and the audio libraries are
# imported. It runs on its own thread and is driven by the clock, so a slow import never stretches it.
SPLASH_SECS = 1.5
_SP_WORD = "TIKRAR"
_SP_FONT = {
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
}
_SP_BG = (10, 10, 10)
_SP_GREEN = (0, 168, 107)
_SP_GREEN_LO = (0, 128, 84)
_SP_SHINE = (214, 255, 234)


def _sp_mix(a, b, t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _sp_cell(a, b):
    """One console cell holding two square pixels: the top one as a half block, the bottom one as its background."""
    if a is None and b is None:
        return " "
    if a is None:
        return "\x1b[38;2;%d;%d;%dm\u2584\x1b[0m" % (b[0], b[1], b[2])
    if b is None:
        return "\x1b[38;2;%d;%d;%dm\u2580\x1b[0m" % (a[0], a[1], a[2])
    return "\x1b[38;2;%d;%d;%d;48;2;%d;%d;%dm\u2580\x1b[0m" % (a[0], a[1], a[2], b[0], b[1], b[2])


class _Splash:
    def __init__(self):
        self.stop = threading.Event()
        self.thread = None
        self.t0 = time.perf_counter()

    def start(self):
        try:
            if not sys.stdout.isatty():
                return
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        except Exception:
            self.thread = None

    def _grid(self, k):
        gap = k
        width = len(_SP_WORD) * 5 * k + (len(_SP_WORD) - 1) * gap
        height = 7 * k + (7 * k) % 2
        grid = [[-1] * width for _ in range(height)]
        for li, ch in enumerate(_SP_WORD):
            x0 = li * (5 * k + gap)
            for gy, line in enumerate(_SP_FONT[ch]):
                for gx, c in enumerate(line):
                    if c == "#":
                        for dy in range(k):
                            for dx in range(k):
                                grid[gy * k + dy][x0 + gx * k + dx] = li
        return grid, width, height

    def _run(self):
        try:
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
            k32 = ctypes.windll.kernel32
            handle = k32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                k32.SetConsoleMode(handle, mode.value | 0x0004)
            cols, rows = shutil.get_terminal_size((80, 24))
            k = 2 if (cols >= 74 and rows >= 11) else 1
            grid, width, height = self._grid(k)
            if cols < width or rows < height // 2 + 1:
                return
            top = (rows - height // 2) // 2 + 1
            left = (cols - width) // 2 + 1
            total = width + 0.5 * height
            band = 0.16 * total
            out = sys.stdout
            out.write("\x1b[?25l\x1b[2J")
            while not self.stop.is_set():
                t = time.perf_counter() - self.t0
                if t >= SPLASH_SECS:
                    break
                # the shine crosses the word diagonally between 0.45 s and 1.15 s
                centre = -band + ((t - 0.45) / 0.70) * (total + 2 * band)
                lines = []
                for r in range(height // 2):
                    cells = []
                    for x in range(width):
                        pair = []
                        for y in (2 * r, 2 * r + 1):
                            li = grid[y][x]
                            if li < 0:
                                pair.append(None)
                                continue
                            a = (t - 0.07 * li) / 0.40
                            a = 0.0 if a < 0.0 else (1.0 if a > 1.0 else a)
                            a = a * a * (3.0 - 2.0 * a)
                            col = _sp_mix(_SP_BG, _sp_mix(_SP_GREEN, _SP_GREEN_LO, y / height), a)
                            d = abs(x + 0.5 * y - centre) / band
                            if d < 1.0:
                                col = _sp_mix(col, _SP_SHINE, 0.9 * (1.0 - d) ** 2 * a)
                            pair.append((int(col[0]), int(col[1]), int(col[2])))
                        cells.append(_sp_cell(pair[0], pair[1]))
                    lines.append("\x1b[%d;%dH%s" % (top + r, left, "".join(cells)))
                out.write("".join(lines))
                out.flush()
                self.stop.wait(1 / 30)
        except Exception:
            pass

    def abort(self):
        """Something failed while loading: stop drawing so the error message is readable."""
        self.stop.set()
        if self.thread is not None:
            self.thread.join(1.0)
            try:
                sys.stdout.write("\x1b[2J\x1b[H\x1b[?25h")
                sys.stdout.flush()
            except Exception:
                pass

    def finish(self):
        """Wait until the splash has had its time, then drop any keys pressed during it."""
        if self.thread is not None:
            self.thread.join()
        try:
            while msvcrt.kbhit():
                msvcrt.getwch()
        except Exception:
            pass


_SPLASH = _Splash()
_SPLASH.start()

miniaudio = None
av = None
DECODER = False


def _load_decoders():
    """Playback needs these two and `av` is slow to import, so they load while the splash plays."""
    global miniaudio, av, DECODER
    try:
        import miniaudio as _miniaudio
        miniaudio = _miniaudio
    except Exception:
        pass
    try:
        import av as _av
        av = _av
    except Exception:
        pass
    DECODER = miniaudio is not None or av is not None


_DECODER_LOADER = threading.Thread(target=_load_decoders, daemon=True)
_DECODER_LOADER.start()

try:
    import lameenc
    import numpy as np
    import sounddevice as sd
except BaseException:
    _SPLASH.abort()
    raise

APP_NAME = "Tikrar"
BITRATE = 128
FPS = 10        # while something is moving and you are looking at the window
IDLE_FPS = 6    # while the screen is just sitting there
BACK_FPS = 4    # the window is open but another window is in front of it
REC_FPS = 16    # while recording and you are looking at the window (the pictures follow your voice more closely)
TRIM_EVERY = 90.0  # seconds between memory trims while playing
BACK_FREEZE_AFTER = 4.0   # stop drawing (keep the last picture) once another window has been in front of this one this long
                          # (minimized stops drawing at once). The window is NEVER frozen while it is the one in use.
LEVEL_STEPS = 32  # how finely the mic follows the loudness (the level is rounded so frames can repeat)
LEVEL_STEPS_HQ = 256   # the same while you are LOOKING at the window: far finer, so the pictures glide
SMOOTH_FPS = 24        # frame rate of the smooth styles (see rec_styles.py, smooth=True) while you are looking at the window
SMOOTH_DUTY = 0.6      # ...but never more than this share of the time spent building and drawing: a slow console lowers the rate
MP3_RATES = (48000, 44100, 32000, 24000, 22050, 16000)

# next to the .exe when packaged, next to the script otherwise
BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "rec_error.log"
try:
    # a crash inside a native library (audio driver, decoder) can't be caught by Python;
    # this makes it leave a stack trace in rec_crash.log instead of vanishing silently
    import faulthandler

    faulthandler.enable(open(BASE_DIR / "rec_crash.log", "a"))
except Exception:
    pass
LOG_STARTED = False

RESET = "\x1b[0m"
DIM = (96, 104, 100)
JADE_LO = (6, 78, 54)
JADE_MID = (0, 168, 107)
JADE_HI = (130, 255, 196)
GOLD = (236, 196, 96)
GOLD_DIM = (150, 124, 68)

# ---- mimic styles (press C / S inside the mimic panel) ----
# accent = bright/played, dim = text, deep = unplayed, head = playhead,
# grad = colour the played wave fades toward (same as accent = flat colour)
def _t(accent, grad, dim, deep, head):
    return dict(accent=accent, grad=grad, dim=dim, deep=deep, head=head)


# accent -> grad is the gradient used across the whole mimic UI (frame, text, mic, wave, timer)
THEMES = [
    ("amber",    _t((255, 214, 100), (255, 128, 56),  (160, 120, 68),  (104, 72, 40),   (255, 244, 214))),
    ("lime",     _t((214, 255, 64),  (24, 214, 140),  (110, 160, 70),  (46, 86, 52),    (244, 255, 214))),
    ("citrus",   _t((255, 244, 72),  (110, 230, 56),  (150, 160, 64),  (76, 90, 40),    (255, 255, 220))),
    ("toxic",    _t((190, 255, 40),  (0, 200, 220),   (90, 150, 80),   (36, 80, 70),    (240, 255, 220))),
    ("brown",    _t((236, 170, 104), (122, 70, 40),   (156, 108, 72),  (76, 50, 36),    (255, 236, 214))),
    ("mocha",    _t((250, 222, 178), (140, 88, 54),   (150, 112, 84),  (74, 52, 40),    (255, 246, 232))),
    ("cherry",   _t((255, 110, 130), (150, 36, 112),  (168, 80, 100),  (96, 40, 66),    (255, 226, 234))),
    ("candy",    _t((255, 140, 224), (110, 200, 255), (150, 110, 168), (80, 62, 108),   (255, 240, 255))),
    ("violet",   _t((196, 146, 255), (255, 116, 200), (124, 90, 170),  (74, 52, 110),   (240, 226, 255))),
    ("midnight", _t((126, 156, 255), (196, 116, 255), (84, 98, 170),   (44, 52, 100),   (226, 232, 255))),
    ("ocean",    _t((96, 240, 222),  (60, 120, 255),  (60, 142, 152),  (32, 74, 92),    (226, 255, 250))),
    ("aurora",   _t((110, 255, 190), (136, 150, 255), (72, 152, 132),  (40, 82, 92),    (230, 255, 245))),
    ("forest",   _t((176, 232, 116), (40, 140, 96),   (104, 142, 80),  (50, 78, 52),    (240, 255, 226))),
    ("neon",     _t((80, 255, 240),  (255, 64, 220),  (90, 150, 160),  (48, 68, 96),    (240, 255, 255))),
    ("mono",     _t((244, 244, 244), (104, 116, 140), (130, 130, 130), (66, 70, 80),    (255, 255, 255))),
]

_PLAIN = dict(ml="╟", mr="╢", mh="─")
BORDERS = [
    ("double", dict(tl="╔", tr="╗", bl="╚", br="╝", ht="═", hb="═", vl="║", vr="║", orn=" ♦ ", **_PLAIN)),
]
STYLE_PATH = BASE_DIR / "rec_style.json"
STYLE = {
    "theme": next(i for i, (n, _) in enumerate(THEMES) if n == "lime"),
    "border": 0,
    "loop": False,
    "autoplay": False,
    "icon": 0,       # secret: 0 mic, 1 Bitzel, 2 reciter (press B to cycle)
    "mimic_icon": "cassette",   # the picture on the mimic / Quran page (press B there): kept apart from the main screen's
    "q_auto": True,  # Quran: carry on to the next surah when one ends
    "shortcut_prompted": False,
}


def load_style():
    try:
        data = json.loads(STYLE_PATH.read_text(encoding="utf-8"))
        themes = [n for n, _ in THEMES]
        borders = [n for n, _ in BORDERS]
        if data.get("theme") in themes:
            STYLE["theme"] = themes.index(data["theme"])
        if data.get("border") in borders:
            STYLE["border"] = borders.index(data["border"])
        STYLE["loop"] = bool(data.get("loop", False))
        STYLE["autoplay"] = bool(data.get("autoplay", False)) and not STYLE["loop"]
        icon = data.get("icon")
        if icon is None:  # an older style file only knew Bitzel
            icon = 1 if data.get("bitzel") else 0
        name = data.get("icon_name")
        if isinstance(name, str):
            STYLE["icon"] = ICON_NAMES.index(name) if name in ICON_NAMES else 0   # by name, so new styles never shift it
        else:
            STYLE["icon"] = icon if isinstance(icon, int) and 0 <= icon < len(ICON_NAMES) else 0
        STYLE["q_auto"] = bool(data.get("q_auto", True))
        STYLE["shortcut_prompted"] = bool(data.get("shortcut_prompted", False))
        mimic_name = data.get("mimic_icon_name")
        if isinstance(mimic_name, str) and mimic_name:   # by name, like the main picture; no entry yet: cassette
            STYLE["mimic_icon"] = mimic_name
    except Exception:
        pass


def icon_index(mim):
    """Which picture (an index into ICON_NAMES) the screen shows: the mimic / Quran page has its own."""
    if mim:
        try:
            return ICON_NAMES.index(STYLE["mimic_icon"])
        except ValueError:
            return 0      # that style is not available right now (rec_styles.py missing or broken): the mic
    return STYLE["icon"]


def save_style():
    try:
        STYLE_PATH.write_text(
            json.dumps({
                "theme": THEMES[STYLE["theme"]][0],
                "border": BORDERS[STYLE["border"]][0],
                "loop": STYLE["loop"],
                "autoplay": STYLE["autoplay"],
                "icon": STYLE["icon"],
                "icon_name": ICON_NAMES[STYLE["icon"]] if 0 <= STYLE["icon"] < len(ICON_NAMES) else "mic",
                "bitzel": STYLE["icon"] == 1,
                "mimic_icon_name": STYLE["mimic_icon"],
                "q_auto": STYLE["q_auto"],
                "shortcut_prompted": STYLE.get("shortcut_prompted", False),
            }),
            encoding="utf-8",
        )
    except Exception:
        pass


def theme():
    return THEMES[STYLE["theme"]][1]


def border():
    return BORDERS[STYLE["border"]][1]


def mimic_vis():
    """How many list rows fit in the mimic panel (rings take rows)."""
    return len(MIC) - 6 - (2 if border().get("inner") else 0)


MIC = [
    "▄▄█████████▄▄",
    "▐███████████▌",
    "▐███████████▌",
    "▐▀▀▀▀▀▀▀▀▀▀▀▌",
    "▐███████████▌",
    "▐███████████▌",
    "▐▀▀▀▀▀▀▀▀▀▀▀▌",
    "█ ▀█████████▀ █",
    "▀█▄  ▀▀▀▀▀  ▄█▀",
    "  ▀█▄▄▄▄▄▄▄█▀  ",
    "      ███      ",
    "      ███      ",
    "  ▄▄▄▄███▄▄▄▄  ",
    "  ▀▀▀▀▀▀▀▀▀▀▀  ",
]
# ---- Bitzel (easter egg: press B) ----
# 24 x 8 cells, every cell is a full block. "." is see-through.
BZ_ROWS = [
    "..bbbbbbbboooooooooooo..",
    "..bbbbbbbboooooooooooo..",
    "..nnnnnnnnrrrryyyyrrrr..",
    "bbbbbbbboooooooooooooooo",
    "..ddddddddeeeeeeeeeeee..",
    "..ddddeeeecccccccccccc..",
    ".mmmmmeeeecccccccccccc..",
    ".mbbbbmmeecccccccccccc..",
]
BZ_PAL = {
    "o": (205, 115, 30), "c": (255, 198, 112), "b": (130, 57, 22), "e": (232, 135, 63),
    "d": (46, 27, 26), "n": (73, 37, 12), "r": (127, 42, 27), "m": (122, 55, 22),
    "y": (255, 171, 47),
}
BZ_LIT = "oec"
BZ_H = len(BZ_ROWS)
BZ_W = len(BZ_ROWS[0])
BZ_STEPS = 12
# How the mic powers on/off depends on the colour theme.
#   engine: cranks, misfires, catches   sign: buzzing neon tube   tube: slow warm-up   snap: clean and quick
# kind -> (intro seconds, outro seconds)
GLOW_KINDS = {
    "engine": (2.2, 1.8),
    "sign": (1.9, 1.4),
    "tube": (3.0, 2.6),
    "snap": (0.7, 0.6),
}
THEME_KIND = {
    "neon": "sign", "candy": "sign", "cherry": "sign", "toxic": "sign",
    "amber": "tube", "brown": "tube", "mocha": "tube", "citrus": "tube",
    "mono": "snap", "midnight": "snap", "ocean": "snap",
}
SEEK_GLITCH = 0.45  # seconds the mic glitches after a , or . seek
_BZ_CACHE = {}


def bitzel_cells(step):
    """Colour of every cell (None = see-through) for a glow step 0..BZ_STEPS."""
    lv = step / BZ_STEPS
    bright = 0.62 + 0.38 * lv
    py = next(y for y, r in enumerate(BZ_ROWS) if "y" in r)
    rows = []
    for y, row in enumerate(BZ_ROWS):
        out = []
        for x, ch in enumerate(row):
            if ch == ".":
                out.append(None)
                continue
            # the whole right side of the hat is one colour; the band cells next to the slot stay red
            hat_right = y < 4 and ch == "o"
            base = BZ_PAL[ch]
            if ch == "y":
                # the glowing slot: ember when quiet, bright gold when talking
                col = lerp((112, 64, 22), base, min(1.0, 0.25 + lv * 1.3))
                if lv > 0.7:
                    col = lerp(col, (255, 238, 176), (lv - 0.7) / 0.3 * 0.65)
            else:
                col = (base[0] * bright, base[1] * bright, base[2] * bright)
                # The glow only depends on the row, and only touches the lit side of the hat
                # and face. Every cell of one colour in one row stays identical, so no
                # stripes appear between the blocks.
                if ch in BZ_LIT or hat_right:
                    # every row of the hat side gets the same glow, so no row looks different
                    halo = max(0.0, 1.0 - (1 if hat_right else abs(y - py)) / 3.0) * lv * 0.5
                    if halo:
                        col = lerp(col, (255, 196, 84), halo)
            out.append(col)
        rows.append(out)
    return rows


def bitzel_rows(step):
    step = max(0, min(BZ_STEPS, step))
    if step not in _BZ_CACHE:
        _BZ_CACHE[step] = [
            "".join(" " if c is None else bgc(c) + " " + RESET for c in row)
            for row in bitzel_cells(step)
        ]
    return _BZ_CACHE[step]


# ---- Reciter (secret icon: press B again) ----
# A round yellow emoji holding an open Quran. He is a recite buddy: he reads along while you recite, nods,
# turns the pages, and reacts to you (see reciter_key for everything he does).
#   reciting   he looks down into the Quran and reads along (right to left); the page and the line he is on
#              glow with your voice and his mouth follows it
#   quiet      he looks up at you and smiles, a moment later raises one eyebrow, then looks around, wonders
#              "hmm?", and in the end dozes off (never while recording)
# Pixels are square: every console cell holds two of them (an upper and a lower half block),
# so the picture is RC_W x RC_H pixels in RC_H // 2 rows (it must fit the height of MIC).
RC_W = 31
RC_H = 28
RC_VOICE = 0.08         # loudness (0..1) that counts as "reciting" (low on purpose: soft reciting must count)
RC_HOLD = 0.35          # seconds of quiet before he stops reading and looks at you
RC_BROW_AFTER = 1.6     # seconds of quiet before the eyebrow goes up
RC_RUN_GAP = 2.5        # a pause shorter than this does not end a stretch of reciting
RC_PROUD_RUN = 8.0      # after reciting this long (and then pausing) he is pleased
RC_PROUD_GAP = 20.0     # at least this long between two pleased moments
FLIP_EVERY = 60.0         # the page turns after about this many seconds of reciting
PACE_MIN, PACE_MAX = 1.5, 8.0   # syllables per second that his reading speed follows (slow tartil .. fast)
PACE_LINE = 32.0          # about this many syllables make one line of the page

_RC_CX, _RC_CY, _RC_RX, _RC_RY = 15.5, 11.8, 13.6, 10.2      # the face (an ellipse)
_RC_EX = (9.0, 22.0)                                          # eye centres (x)
_RC_EY = 12.0                                                 # eye centre (y)
_RC_ERX, _RC_ERY = 5.0, 4.8                                   # eye size

_RC_INK = (28, 18, 14)
_RC_OUTLINE = (122, 66, 10)
_RC_SKIN_HI = (255, 224, 78)
_RC_SKIN = (255, 204, 22)
_RC_SKIN_LO = (244, 170, 12)
_RC_MOUTH = (96, 50, 12)
_RC_WHITE = (255, 255, 255)
_RC_WHITE_LO = (230, 230, 240)
_RC_PAGE = (238, 224, 168)
_RC_PAGE_HI = (255, 252, 214)
_RC_GREEN = (36, 176, 98)
_RC_GREEN_HI = (150, 255, 200)
_RC_HALO = (255, 226, 120)
_RC_VOID = (10, 10, 10)     # what the halo fades into (the console background)

_RC_FACE = {}


def _rc_mix(a, b, t):
    return a + (b - a) * t


def _rc_face_px(dy):
    """The round face with a clean outline, soft shading and blushing cheeks, cached.
    dy = 0 or 1 (breathing). Returns (pixels, set of pixels inside the outline)."""
    hit = _RC_FACE.get(dy)
    if hit is not None:
        return hit
    cy = _RC_CY + dy
    lx, ly, lz = -0.42, -0.62, 0.66
    ln = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ln, ly / ln, lz / ln
    shape = set()
    for y in range(RC_H):
        v = (y + 0.5 - cy) / _RC_RY
        for x in range(RC_W):
            u = (x + 0.5 - _RC_CX) / _RC_RX
            if u * u + v * v <= 1.0:
                shape.add((x, y))
    grid = [[None] * RC_W for _ in range(RC_H)]
    inner = set()
    for x, y in shape:
        edge = any(n not in shape for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
        if edge:
            grid[y][x] = _RC_OUTLINE
            continue
        inner.add((x, y))
        u = (x + 0.5 - _RC_CX) / _RC_RX
        v = (y + 0.5 - cy) / _RC_RY
        z = math.sqrt(max(0.0, 1.0 - u * u - v * v))
        d = max(0.0, u * lx + v * ly + z * lz)
        if d < 0.5:
            col = lerp(_RC_SKIN_LO, _RC_SKIN, d / 0.5)
        else:
            col = lerp(_RC_SKIN, _RC_SKIN_HI, (d - 0.5) / 0.5 * 0.7)
        grid[y][x] = (int(col[0]), int(col[1]), int(col[2]))
    _RC_FACE[dy] = (grid, inner)
    return _RC_FACE[dy]


def _rc_put(px, x, y, col):
    if 0 <= x < RC_W and 0 <= y < RC_H:
        px[y][x] = col


def _rc_eye_shape(ex, ey):
    shape = set()
    for y in range(int(ey - _RC_ERY) - 1, int(ey + _RC_ERY) + 2):
        for x in range(int(ex - _RC_ERX) - 1, int(ex + _RC_ERX) + 2):
            u = (x + 0.5 - ex) / _RC_ERX
            v = (y + 0.5 - ey) / _RC_ERY
            if abs(u) ** 5.0 + abs(v) ** 5.0 <= 1.0 and 0 <= x < RC_W and 0 <= y < RC_H:   # a rounded square: roomier than an oval
                shape.add((x, y))
    n4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
    n8 = n4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))
    inner = {p for p in shape if all((p[0] + d[0], p[1] + d[1]) in shape for d in n4)}
    # "safe" = at least one white pixel away from the black outline on every side
    safe = {p for p in inner if all((p[0] + d[0], p[1] + d[1]) in inner for d in n8)}
    return shape, inner, safe


# The pupil is a fixed 4 x 4 block of pixels. It only ever moves by whole pixels, and both eyes get the
# very same shift, so they always move at the same moment and by the same amount (the two eyes are
# identical shapes, a whole number of pixels apart). It is never allowed to touch the outline or the lid.
_RC_PUPIL = [(dx, dy) for dx in (-2, -1, 0, 1) for dy in (-2, -1, 0, 1)]


def _rc_gaze_shift(gx, gy):
    sx = max(-1, min(1, int(math.floor(gx * 1.8 + 0.5))))
    sy = max(-1, min(1, int(math.floor(gy * 1.8 + 0.5))))
    return sx, sy


def _rc_eye(px, face, ex, ey, gx, gy, lid, blink, reading, lid_col=None):
    """One round eye with a clean outline. gx, gy: where the pupil looks (-1..1). lid: 0 open .. 1 shut.
    lid_col: (top, bottom, lash line) colours for the eyelid; None = the lid is the face colour (the emoji faces).
    Returns the set of pupil pixels (or None) so it can be tested."""
    if blink and lid_col is not None:                       # a closed eye: the whole eye filled by the lid
        shape, inner, _ = _rc_eye_shape(ex, ey)
        top, bot = min(y for _, y in inner), max(y for _, y in inner)
        for x, y in shape:
            px[y][x] = lerp(lid_col[0], lid_col[1], (y - top) / max(1.0, bot - top)) if (x, y) in inner else _RC_INK
        for x in range(int(ex - _RC_ERX + 1.1), int(ex + _RC_ERX - 1.1) + 1):
            low = abs(x + 0.5 - ex) < 2.4
            _rc_put(px, x, int(ey) + (1 if low else 0), lid_col[2])
        return None
    if blink:
        for x in range(int(ex - _RC_ERX + 1.1), int(ex + _RC_ERX - 1.1) + 1):
            low = abs(x + 0.5 - ex) < 2.4
            _rc_put(px, x, int(ey) + (1 if low else 0), _RC_INK)
        return None
    shape, inner, safe = _rc_eye_shape(ex, ey)
    cxp, cyp = int(ex), int(ey)
    first = {}
    for x, y in inner:
        if x not in first or y < first[x]:
            first[x] = y
    rows = int(round(lid * _RC_ERY * 2))      # how many rows of the lid are closed
    lid_cover = {(x, y) for x, y in inner if rows and y - first[x] < rows}   # lid pixels, line included
    # pick the pupil position nearest to where it wants to look that keeps a white gap all round
    pupil = None
    sx0, sy0 = _rc_gaze_shift(gx, gy)
    cands = sorted(((sx, sy) for sx in (-1, 0, 1) for sy in (-1, 0, 1)),
                   key=lambda c: (abs(c[0] - sx0) + abs(c[1] - sy0), abs(c[0]), abs(c[1])))
    for sx, sy in cands:
        pts = {(cxp + dx + sx, cyp + dy + sy) for dx, dy in _RC_PUPIL}
        if not pts <= safe:
            continue
        if lid_cover and any((x + a, y + b) in lid_cover for x, y in pts for a in (-1, 0, 1) for b in (-1, 0, 1)):
            continue
        pupil = pts
        break
    for x, y in shape:
        if (x, y) not in inner:
            px[y][x] = _RC_INK
            continue
        col = _RC_WHITE_LO if y >= int(ey + 1) + 1 else _RC_WHITE
        if pupil and (x, y) in pupil:
            col = _RC_INK
        k = y - first[x]
        if rows and k < rows:
            if lid_col is None:
                col = _RC_INK if k == rows - 1 else face[y][x]
            else:                                           # a feathered lid: lighter at the brow, a dark lash line
                col = lid_col[2] if k == rows - 1 else lerp(lid_col[0], lid_col[1], k / max(1.0, rows - 1.0))
        px[y][x] = col
    if pupil:
        tx, ty = min(x for x, _ in pupil), min(y for _, y in pupil)
        px[ty][tx] = _RC_WHITE                                  # sparkle in the pupil
        px[max(y for _, y in pupil)][max(x for x, _ in pupil)] = (170, 200, 255)
    return pupil


# Eyebrows, pixel by pixel: x -> the two rows it covers when fully raised.
_RC_BROW_L = {7: (5, 6), 8: (4, 5), 9: (4, 5), 10: (4, 5), 11: (4, 5), 12: (5, 6)}
_RC_BROW_R = {18: (6, 7), 19: (6, 7), 20: (5, 6), 21: (5, 6), 22: (5, 6), 23: (5, 6)}


def _rc_brow(px, inner, shape, shift, dy, blocked=frozenset(), col=None):
    """A thick black eyebrow, kept inside the face so its outline stays whole.
    shift: rows below the fully raised position."""
    for x, rows in shape.items():
        for r in rows:
            y = r + shift + dy
            if (x, y) in inner and (x, y) not in blocked:
                px[y][x] = col or _RC_INK


# ---- the Quran he holds ----
# A big open mushaf: cream pages with lines of script (the line he is reading lights up as he recites), a gold
# trim and a green cover, held up by two hands (the owl uses his wing tips). When he turns a page a leaf lifts
# off the right-hand page and swings over to the left.
_BK_L, _BK_R = 4, 26                       # first and last pixel column of the book; the spine is column 15
_RC_COVER = (22, 134, 82)
_RC_COVER_LO = (12, 86, 52)
_RC_COVER_HI = (70, 204, 132)
_RC_GOLD = (214, 168, 52)
_RC_GOLD_HI = (255, 232, 124)
_RC_PAGE_EDGE = (206, 188, 132)
_RC_PAGE_EDGE_HI = (240, 220, 152)
_RC_CREASE = (178, 154, 100)
_RC_SCRIPT = (118, 86, 46)
_RC_SCRIPT_HI = (86, 56, 22)
_RC_BOOK_OUT = (14, 36, 26)
_RC_TONGUE = (226, 96, 96)
_BK = {}


def _bk_top(x):
    return 21 - int(abs(x + 0.5 - 15.5) / 5.0)


def _bk_bot(x):
    return 27 - int(abs(x + 0.5 - 15.5) / 6.0)


def _bk_geo():
    """The book's pixels (kind of every pixel, which ones carry script) and its outline. Built once."""
    hit = _BK.get("geo")
    if hit is not None:
        return hit
    cells, script = {}, {}
    for x in range(_BK_L, _BK_R + 1):
        top, bot = _bk_top(x), _bk_bot(x)
        for y in range(top, bot + 1):
            if x in (_BK_L, _BK_R):
                k = "g"
            elif y == bot:
                k = "gl"
            elif y == bot - 1:
                k = "g"
            elif y == bot - 2:
                k = "gold"
            elif x == 15:
                k = "crease"
            elif y == top:
                k = "pedge"
            else:
                k = "page"
            cells[(x, y)] = k
    outline = set()
    for (x, y) in cells:
        for dx, dy in _N4:
            n = (x + dx, y + dy)
            if n not in cells and 0 <= n[0] < RC_W and 0 <= n[1] < RC_H:
                outline.add(n)
    _BK["geo"] = (cells, script, outline)
    return _BK["geo"]


# 5 x 5 (or 4 x 5) pictures that float up in the top right corner next to his head
_RC_SYMS = {
    3: (".##.", "#..#", "..#.", "....", "..#."),                           # ?
    4: ("##", "##", "##", "..", "##"),                                      # !
    5: ("###", "..#", ".#.", "#..", "###"),                                # a small z
    6: ("#####", "...#.", "..#..", ".#...", "#####"),                      # a big Z
    7: ("..#..", ".###.", "#####", ".###.", "..#.."),                      # a sparkle
}
_RC_SYM_COL = {1: (255, 226, 110), 2: (255, 104, 140), 3: (190, 226, 255), 4: (255, 214, 70),
               5: (160, 196, 255), 6: (160, 196, 255), 7: (255, 246, 176)}


def _rc_symbol(px, sym):
    """sym = kind * 16 + rise * 4 + fade (rise 0..2 lifts it, fade 0..3 how visible). Only paints empty pixels."""
    kind, rise, fade = sym // 16, (sym // 4) % 4, sym % 4
    bmp = _RC_SYMS.get(kind)
    if bmp is None or fade <= 0:
        return
    col = lerp(_RC_VOID, _RC_SYM_COL[kind], fade / 3.0)
    x0, y0 = RC_W - len(bmp[0]), 1 - min(2, rise) // 2        # right-aligned, so the body never covers it
    for j, line in enumerate(bmp):
        for i, ch in enumerate(line):
            x, y = x0 + i, y0 + j
            if ch == "#" and 0 <= x < RC_W and 0 <= y < RC_H and px[y][x] is None:
                px[y][x] = col


def _rc_book(px, lit, hl=-1, flip=-1):
    """The open Quran under his chin. lit 0..1: the pages brighten and light spills around it.
    hl: -1, or line * 100 + position (0..11): the line he is reading lights up there, right to left.
    flip: -1, or 0..3: a page is being turned."""
    cells, script, outline = _bk_geo()
    if lit > 0.03:
        R = 3.0
        for y in range(14, RC_H):
            for x in range(0, RC_W):
                if (x, y) in cells:
                    continue
                ddx = max(0.0, abs(x + 0.5 - 15.5) - 11.5)
                ddy = max(0.0, abs(y + 0.5 - 24.0) - 4.5)
                dd = math.hypot(ddx, ddy)
                if dd >= R:
                    continue
                a = lit * (1.0 - dd / R) ** 2
                cur = px[y][x]
                if cur is None:
                    if a > 0.07:
                        px[y][x] = lerp(_RC_VOID, _RC_HALO, min(1.0, a * 0.9))
                elif cur != _RC_OUTLINE:
                    px[y][x] = lerp(cur, _RC_HALO, min(1.0, a * 0.6))
    for x, y in outline:
        px[y][x] = _RC_BOOK_OUT
    page = lerp(_RC_PAGE, _RC_PAGE_HI, lit)
    col_of = {
        "page": page,
        "pedge": lerp(_RC_PAGE_EDGE, _RC_PAGE_EDGE_HI, lit),
        "crease": lerp(_RC_CREASE, _RC_PAGE_EDGE, lit),
        "g": lerp(_RC_COVER, _RC_COVER_HI, lit * 0.8),
        "gl": lerp(_RC_COVER_LO, _RC_COVER, lit * 0.8),
        "gold": lerp(_RC_GOLD, _RC_GOLD_HI, lit),
    }
    hx = 24.5 - (hl % 100) * (18.0 / 11.0) if hl >= 0 else -99.0
    rowcells = _bk_line_cells(hl // 100) if hl >= 0 else ()
    for (x, y), k in cells.items():
        c = col_of[k]
        near = abs(x - hx) if (x, y) in rowcells else 99.0
        glow = max(0.0, 1.0 - near / 3.4)
        if glow > 0.0:
            c = lerp(c, (255, 228, 130), 0.9 * glow)
        if (x, y) in script:
            c = lerp(_RC_SCRIPT, _RC_SCRIPT_HI, glow)
        px[y][x] = c
    for x in (9, 21):                                     # a gold stud on the cover under each page
        y = _bk_bot(x) - 1
        px[y][x] = _RC_GOLD_HI if lit > 0.5 else _RC_GOLD
    px[_bk_bot(15) - 1][15] = _RC_GOLD_HI if lit > 0.35 else _RC_GOLD         # the star on the spine
    if 0 <= flip <= 3:
        _rc_leaf(px, flip, page)


def _bk_line_cells(line):
    """Every page pixel on the row of a script line (so the reading light is a band, not only the dashes)."""
    hit = _BK.get(("row", line))
    if hit is None:
        cells = _bk_geo()[0]
        hit = _BK[("row", line)] = {(x, y) for (x, y), k in cells.items()
                                    if k == "page" and y == _bk_top(x) + 1 + 2 * line and (6 <= x <= 24)}
    return hit


# the leaf of a page turn: (columns, rows above the page top) for the four frames
_RC_LEAF = (((24, 25), 1), ((21, 22), 2), ((15, 16), 2), ((9, 10), 1))


def _rc_leaf(px, f, page):
    cols, up = _RC_LEAF[f]
    for i, x in enumerate(cols):
        top = _bk_top(x) - up
        for y in range(max(0, top), _bk_bot(x) - 2):
            px[y][x] = page if i == 0 else lerp(page, _RC_PAGE_EDGE, 0.55)
        if top - 1 >= 0 and px[top - 1][x] is not None:
            px[top - 1][x] = _RC_BOOK_OUT
    side = cols[0] - 1 if f < 2 else cols[1] + 1
    for y in range(max(0, _bk_top(cols[0]) - up), _bk_bot(cols[0]) - 2):
        if 0 <= side < RC_W:
            px[y][side] = _RC_BOOK_OUT


def _rc_hands(px, hand, fill, edge):
    """Two hands (or wing tips) holding the book's lower corners. hand: 0 both down, 1 left up, 2 right up, 3 both up."""
    for i, cx in enumerate((4.0, 27.0)):
        lift = 2 if (hand >> i) & 1 else 0
        cy = 24.4 - lift
        shape = {(x, y) for y in range(RC_H) for x in range(RC_W)
                 if ((x + 0.5 - cx) / 3.1) ** 2 + ((y + 0.5 - cy) / 2.7) ** 2 <= 1.0}
        for x, y in shape:
            on_edge = any((x + dx, y + dy) not in shape for dx, dy in _N4)
            px[y][x] = edge if on_edge else fill


def _rc_happy_eye(px, ex, ey, col):
    """A closed, smiling eye ^ : used when he is pleased."""
    cx, cy = int(ex), int(ey)
    for x, y in ((-3, 1), (-2, 0), (-1, -1), (0, -1), (1, -1), (2, 0), (3, 1),
                 (-2, 1), (-1, 0), (0, 0), (1, 0), (2, 1)):
        _rc_put(px, cx + x, cy + y, col)


_RC_BROW_R2 = {30 - x: r for x, r in _RC_BROW_L.items()}      # the left brow mirrored: both brows alike


_MOUTH_SHAPES = {
    "flat": ((14, 18), (15, 18), (16, 18)),
    "smile": ((14, 17), (14, 18), (15, 18), (16, 18), (16, 17)),
    "smirk": ((14, 18), (15, 18), (16, 18), (16, 17)),
    "grin": ((13, 17), (14, 17), (15, 17), (16, 17), (17, 17), (13, 18), (14, 18), (15, 18), (16, 18), (17, 18),
             (14, 19), (16, 19)),
    "t1": ((14, 18), (15, 18), (16, 18), (15, 19)),
    "t2": ((13, 18), (14, 18), (15, 18), (16, 18), (17, 18), (14, 19), (15, 19), (16, 19)),
    "o": ((14, 17), (15, 17), (16, 17), (14, 18), (16, 18), (14, 19), (15, 19), (16, 19)),
    "big": ((14, 17), (15, 17), (16, 17), (13, 18), (14, 18), (15, 18), (16, 18), (17, 18),
            (13, 19), (14, 19), (16, 19), (17, 19), (14, 20), (16, 20)),
}
_MOUTH_TONGUE = {"grin": ((15, 19),), "t2": (), "big": ((15, 19), (15, 20))}


def _rc_mouth(px, bob, shape, col):
    for x, y in _MOUTH_SHAPES[shape]:
        if y + bob <= 20:
            _rc_put(px, x, y + bob, col)
    for x, y in _MOUTH_TONGUE.get(shape, ()):
        if y + bob <= 20:
            _rc_put(px, x, y + bob, _RC_TONGUE)


def _rc_owl_beak(px, bob, talk, mood):
    """The owl's beak: the top half stays, the lower half drops as he talks."""
    for x in (14, 15, 16):
        _rc_put(px, x, 17 + bob, _OW_BEAK)
    _rc_put(px, 15, 16 + bob, _OW_BEAK)
    if mood == 4 or talk >= 3:
        for x in (14, 15, 16):
            _rc_put(px, x, 18 + bob, _OW_BEAK_IN)
            _rc_put(px, x, 19 + bob, _OW_BEAK_IN)
        for x in (14, 15, 16):
            _rc_put(px, x, 20 + bob, _OW_BEAK_LO)
    elif talk == 2 or mood == 5:
        for x in (14, 15, 16):
            _rc_put(px, x, 18 + bob, _OW_BEAK_IN)
        for x in (14, 15, 16):
            _rc_put(px, x, 19 + bob, _OW_BEAK_LO)
    elif talk == 1:
        _rc_put(px, 15, 18 + bob, _OW_BEAK_IN)
        _rc_put(px, 14, 18 + bob, _OW_BEAK_LO)
        _rc_put(px, 16, 18 + bob, _OW_BEAK_LO)
        _rc_put(px, 15, 19 + bob, _OW_BEAK_LO)
    else:
        _rc_put(px, 15, 18 + bob, _OW_BEAK_LO)


# ---- two more mascot styles: the Scholar and the Owl (press B to cycle through all the icons) ----
# Both share the reciter's brain (reciter_key): they read along with you while you recite, look up at you
# when you go quiet, react to a long silence, and get starry-eyed after a long take. They use the very same
# eyes (_rc_eye), so the pupils always move together, by whole pixels, and never touch a black border.
BASE_ICONS = ("mic", "bitzel", "reciter", "scholar", "owl", "cassette")     # drawn by this file
ICON_NAMES = list(BASE_ICONS)    # plus every style found in rec_styles.py (filled in by _styles_load)

_SC_CAP = (246, 243, 232)
_SC_CAP_LO = (224, 218, 200)
_SC_CAP_BAND = (190, 172, 126)
_SC_CAP_EDGE = (150, 138, 112)
_SC_MOUTH = (70, 30, 8)

_OW_OUT = (88, 50, 22)
_OW_HI = (226, 168, 98)
_OW_LO = (168, 112, 58)
_OW_WING = (138, 86, 44)
_OW_WING_EDGE = (104, 62, 30)
_OW_DISK = (246, 230, 194)
_OW_DISK_RING = (228, 198, 142)
_OW_BELLY = (240, 214, 164)
_OW_BELLY_MARK = (214, 178, 122)
_OW_BEAK = (255, 176, 40)
_OW_BEAK_LO = (216, 132, 16)
_OW_BEAK_IN = (120, 50, 16)
_OW_BROW = (70, 38, 16)
_OW_LID = ((224, 168, 100), (184, 126, 68), (92, 52, 24))     # eyelid: brow-coloured feathers, then a dark lash line
_N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


_OW_BASE = {}


def _ow_base(bob, ear=0):
    """The owl's body without its eyes: round body, ear tufts, wings, face disks and belly.
    ear: -1 tufts drooping, 0 normal, 1 perked, 2 straight up (startled). Cached: it only changes with the
    breathing and the ears."""
    key = (bob, ear)
    hit = _OW_BASE.get(key)
    if hit is not None:
        return hit
    cy = 12.6 + bob
    shape = set()
    for y in range(RC_H):
        v = (y + 0.5 - cy) / 10.4
        for x in range(RC_W):
            u = (x + 0.5 - 15.5) / 14.2
            if u * u + v * v <= 1.0:
                shape.add((x, y))
    h = 4 + ear                                          # ear tufts: triangles, the base row never moves
    for cx in (7, 23):
        for r in range(h):
            y = 4 + bob - (h - 1) + r
            half = max(0, r - (h - 4))
            for x in range(cx - half, cx + half + 1):
                if 0 <= x < RC_W and 0 <= y < RC_H:
                    shape.add((x, y))
    inner = {p for p in shape if all((p[0] + dx, p[1] + dy) in shape for dx, dy in _N4)}
    grid = [[None] * RC_W for _ in range(RC_H)]
    for x, y in shape:
        if (x, y) not in inner:
            grid[y][x] = _OW_OUT
            continue
        u = (x + 0.5 - 15.5) / 14.2
        v = (y + 0.5 - cy) / 10.4
        grid[y][x] = lerp(_OW_LO, _OW_HI, max(0.0, min(1.0, 0.6 - 0.45 * v - 0.2 * u)))
    for cxw in (7.0, 24.0):                              # wings, tucked in at the sides
        wing = {(x, y) for y in range(RC_H) for x in range(RC_W)
                if ((x + 0.5 - cxw) / 3.6) ** 2 + ((y + 0.5 - (19.5 + bob)) / 4.2) ** 2 <= 1.0 and (x, y) in inner}
        for x, y in wing:
            edge = any((x + dx, y + dy) not in wing for dx, dy in _N4)
            grid[y][x] = _OW_WING_EDGE if edge else _OW_WING

    def disk_d(x, y):
        return min(math.hypot(x + 0.5 - c, y + 0.5 - (12.0 + bob)) for c in (9.0, 22.0))

    for y in range(RC_H):                                # the two face disks round the eyes
        for x in range(RC_W):
            if (x, y) in inner:
                d = disk_d(x, y)
                if d <= 6.5:
                    grid[y][x] = _OW_DISK_RING if d > 5.6 else _OW_DISK
    for y in range(RC_H):                                # the belly
        for x in range(RC_W):
            if (x, y) in inner and disk_d(x, y) > 6.5 \
                    and ((x + 0.5 - 15.5) / 7.6) ** 2 + ((y + 0.5 - (19.8 + bob)) / 5.0) ** 2 <= 1.0:
                grid[y][x] = _OW_BELLY_MARK if (y >= 20 + bob and (x + 2 * y) % 5 == 0) else _OW_BELLY
    _OW_BASE[key] = (grid, inner)
    return _OW_BASE[key]


def mascot_pixels(kind, key, ph):
    """One frame of the Reciter (2), Scholar (3) or Owl (4). key (all small, so frames can repeat and be cached):
      mode   0 reading along, 1 waiting for you
      gx gy  where the pupils look (-1..1)       lid  how far the eyelids are down (0..1)
      brow   how high the eyebrows are (0..1)    blink  1 left eye shut, 2 right, 3 both
      bob    head up / down by a pixel           lit  how much the book glows (0..1)
      talk   mouth or beak opening 0..3, follows the voice
      mood   0 normal, 1 happy, 2 sleepy, 3 asleep, 4 yawning, 5 startled, 6 excited
      sym    floating picture (see _rc_symbol)   flip  page turn frame, -1 none
      hand   hands lifted (1 left, 2 right, 3 both)    ear  the owl's ear tufts (-1..2)
      hl     the line being read: -1 or line * 100 + position"""
    mode, gx, gy, lid, brow, blink, bob, lit, talk, mood, sym, flip, hand, ear, hl = key
    owl = kind == 4
    if owl:
        face, inner = _ow_base(bob, ear)
    else:
        face, inner = _rc_face_px(bob)
    px = [row[:] for row in face]
    if kind == 3:
        top = 5 + bob
        for y in range(0, top + 1):                      # the white kufi cap
            for x in range(RC_W):
                f = face[y][x]
                if f is None:
                    continue
                if f == _RC_OUTLINE:
                    px[y][x] = _SC_CAP_EDGE if y < top else _RC_OUTLINE
                elif y == top:
                    px[y][x] = _SC_CAP_BAND
                else:
                    px[y][x] = _SC_CAP if (x + y) % 2 == 0 else _SC_CAP_LO
        cy = _RC_CY + bob
        if lit > 0.05:                                   # the glow round his head
            for y in range(RC_H):
                for x in range(RC_W):
                    if px[y][x] is not None:
                        continue
                    e = math.hypot((x + 0.5 - _RC_CX) / _RC_RX, (y + 0.5 - cy) / _RC_RY)
                    if 1.0 < e < 1.22:
                        a = lit * 0.5 * (1.0 - (e - 1.0) / 0.22) ** 2
                        if a > 0.04:
                            px[y][x] = lerp(_RC_VOID, _RC_HALO, min(1.0, a))
        gate = max(0.0, min(1.0, (lit - 0.25) * 2.5))    # twinkles in the corners, fading in and out while he recites
        for i, (x, y) in enumerate(((1, 3), (0, 11), (30, 11), (1, 19), (29, 19))):
            br = math.sin(math.pi * ((ph / 24.0 + i / 5.0) % 1.0)) ** 2 * gate
            if br < 0.1 or face[y][x] is not None:
                continue
            px[y][x] = lerp(_RC_VOID, (255, 244, 160), br)
            for dx, dy in _N4:
                a, b = x + dx, y + dy
                if 0 <= a < RC_W and 0 <= b < RC_H and face[b][a] is None:
                    px[b][a] = lerp(_RC_VOID, (190, 160, 72), br)
    lid_col = _OW_LID if owl else None
    shut = mood in (3, 4)                                # asleep or yawning: eyes closed
    for i, ex in enumerate(_RC_EX):
        if mood == 1:
            _rc_happy_eye(px, ex, _RC_EY + bob, _RC_INK)
        else:
            _rc_eye(px, face, ex, _RC_EY + bob, gx, gy, lid, shut or bool(blink & (1 << i)), mode == 0, lid_col)
    if brow > 0.02 and not shut and mood != 1:
        e = brow * brow * (3.0 - 2.0 * brow)
        blocked = set()
        for ex in _RC_EX:                                # keep every brow pixel one pixel clear of the eyes
            for x, y in _rc_eye_shape(ex, _RC_EY + bob)[0]:
                blocked.update((x + a, y + b) for a in (-1, 0, 1) for b in (-1, 0, 1))
        bc = _OW_BROW if owl else None
        if mood == 0 and mode == 1:                      # one eyebrow higher than the other: "hmm?"
            _rc_brow(px, inner, _RC_BROW_L, int(round((1.0 - e) * 2)), bob, blocked, bc)
            _rc_brow(px, inner, _RC_BROW_R, int(round(1.0 - e)), bob, blocked, bc)
        else:                                            # both alike: surprised, excited
            _rc_brow(px, inner, _RC_BROW_L, int(round((1.0 - e) * 2)), bob, blocked, bc)
            _rc_brow(px, inner, _RC_BROW_R2, int(round((1.0 - e) * 2)), bob, blocked, bc)
    _rc_book(px, lit, hl, flip)
    if owl:
        _rc_hands(px, hand, _OW_WING, _OW_WING_EDGE)
        _rc_owl_beak(px, bob, talk, mood)
    else:
        _rc_hands(px, hand, _RC_SKIN, _RC_OUTLINE)
        mouth_col = _SC_MOUTH if kind == 3 else _RC_MOUTH
        if mood == 4:
            shape = "big"
        elif mood == 5:
            shape = "o"
        elif mood == 3 or mood == 2:
            shape = "t1" if (mood == 3 and bob) else "flat"
        elif mood == 1:
            shape = "grin"
        elif mode == 0 or mood == 6:
            shape = ("flat", "t1", "t2", "big")[talk]
        else:
            shape = "smirk" if brow > 0.5 else "smile"
        _rc_mouth(px, bob, shape, mouth_col)
    if sym:
        _rc_symbol(px, sym)
    return px


_MS_ROWS = {}


def mascot_rows(kind, key, ph):
    """The Reciter (2), Scholar (3) or Owl (4) as RC_H // 2 console rows (cached: a repeated frame costs nothing)."""
    if kind != 4 and key[13]:
        key = key[:13] + (0,) + key[14:]                 # only the owl has ear tufts
    ck = (kind, key, ph)
    rows = _MS_ROWS.get(ck)
    if rows is None:
        if len(_MS_ROWS) > 1500:
            _MS_ROWS.clear()
        rows = _MS_ROWS[ck] = _px_to_rows(mascot_pixels(kind, key, ph))
    return rows


# ---- the animated scene styles live in their own file: rec_styles.py, next to this script ----
# Edit that file (or add styles to it) and save: this app notices within a second and shows the change, no
# restart and no rebuilding of this file. If rec_styles.py has a mistake, the last good version keeps running
# (or just the five characters above if there never was one) and the mistake goes to rec_error.log.
STYLES_PATH = BASE_DIR / "rec_styles.py"
_STY = dict(by={}, mod=None, mtime=None, checked=0.0)
_SC_ROWS = {}            # finished pictures (as console rows), so a repeated frame costs nothing
_SC_BAD = set()          # styles that already reported an error (reported once, not on every frame)
_SC_BLANK = []


def _styles_load():
    """Read rec_styles.py if it changed. A broken file never replaces a working one."""
    try:
        mtime = STYLES_PATH.stat().st_mtime
    except OSError:
        return False                                      # no file: only the built-in characters
    if mtime == _STY["mtime"]:
        return False
    _STY["mtime"] = mtime                                 # a file with a mistake is tried once per save, not every second
    try:
        spec = importlib.util.spec_from_file_location("rec_styles_live", str(STYLES_PATH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if (int(mod.W), int(mod.H)) != (RC_W, RC_H):
            raise ValueError(f"rec_styles.py draws {mod.W} x {mod.H} pictures, this app expects {RC_W} x {RC_H}")
        by = {}
        for st in mod.STYLES:
            if st.name not in by and st.name not in BASE_ICONS:
                by[st.name] = st                          # the first style of a name wins
    except Exception:
        log_error("rec_styles.py could not be loaded; the previous version (if any) keeps running")
        return False
    here = ICON_NAMES[STYLE["icon"]] if 0 <= STYLE["icon"] < len(ICON_NAMES) else None
    _STY["mod"], _STY["by"] = mod, by
    ICON_NAMES[:] = list(BASE_ICONS) + list(by)
    STYLE["icon"] = ICON_NAMES.index(here) if here in ICON_NAMES else 0
    _SC_ROWS.clear()
    _SC_BAD.clear()
    return True


def styles_poll(now):
    """Called every frame, looks at the file once a second."""
    if now - _STY["checked"] >= 1.0:
        _STY["checked"] = now
        try:
            _styles_load()
        except Exception:
            pass
    if STYLE["icon"] >= len(ICON_NAMES):
        STYLE["icon"] = 0


def _scene_blank():
    if not _SC_BLANK:
        _SC_BLANK.append(_px_to_rows([[None] * RC_W for _ in range(RC_H)]))
    return _SC_BLANK[0]


def _scene_fail(name):
    if name not in _SC_BAD:
        _SC_BAD.add(name)
        log_error(f"style '{name}' raised an error (an empty picture is shown instead)")


def _scene_render(sty, ctx_args, key):
    """Draw a style's picture (or take it from the cache) and turn it into console rows."""
    rows = _SC_ROWS.get(key) if key is not None else None
    if rows is None:
        try:
            px = sty.draw(_STY["mod"].Ctx(*ctx_args))
            if len(px) != RC_H or any(len(r) != RC_W for r in px):
                raise ValueError(f"style '{sty.name}' must return {RC_W} x {RC_H} pixels")
            rows = _px_to_rows(px)
        except Exception:
            _scene_fail(sty.name)
            return _scene_blank()
        if key is not None:
            if len(_SC_ROWS) > 800:
                _SC_ROWS.clear()
            _SC_ROWS[key] = rows
    return rows


def scene_rows(name, now, voice, active, prog, elapsed, live=False, hq=False):
    """One frame of a scene style as RC_H // 2 console rows. voice 0..1, active: recording or playing,
    prog 0..1: how far through the take, elapsed: seconds into the take. live: recording right now, so the
    picture follows the voice twice as finely and a good deal faster (more drawing work, only while recording).
    hq: you are looking at the window. Styles marked smooth=True then get the voice unrounded, a fractional
    c.tick, a fast rise and soft fall, and are drawn fresh every frame (no cache): the picture glides with the audio."""
    if name == "cassette":
        return cassette_rows(now, voice, active, prog, elapsed)
    sty = _STY["by"].get(name)
    if sty is None:
        return _scene_blank()
    mem = sty.mem
    smooth = bool(hq and getattr(sty, "smooth", False))
    mem["_hq"] = smooth
    dt = min(0.5, max(0.0, now - mem["_t"])) if "_t" in mem else 0.0
    mem["_t"] = now
    v = max(0.0, min(1.0, voice))
    if sty.rise is not None:                              # follow the voice at the style's own, calmer pace
        cur = mem.get("_v", 0.0)
        rate = sty.rise if v > cur else (sty.fall if sty.fall is not None else sty.rise)
        if smooth:
            rate *= 2.6 if v > cur else 1.9               # quick to rise (every little change shows), soft to fall
        elif live:
            rate *= 2.4 if v > cur else 1.8
        mem["_v"] = cur + (v - cur) * (1.0 - math.exp(-dt * rate))
        v = mem["_v"]
    extra = None
    if sty.update is not None:
        try:
            extra = sty.update(mem, dt, v, bool(active), prog, elapsed, now)
        except Exception:
            _scene_fail(name)
            return _scene_blank()
    pq = int(round(max(0.0, min(1.0, prog)) * sty.prog_steps)) if sty.prog_steps else 0
    pv = pq / float(sty.prog_steps) if sty.prog_steps else 0.0
    if smooth:
        tick = (now * sty.fps) % sty.cycle                # a float: the motion is continuous, not a frame counter
        args = (v if sty.steps else 0.0, tick, pv, False, extra, mem, now)
        return _scene_render(sty, args, None)
    steps = min(32, sty.steps * 2) if (live and sty.steps) else sty.steps
    vq = int(round(v * steps)) if steps else 0
    tick = int(now * sty.fps) % sty.cycle
    args = (vq / float(steps) if steps else 0.0, tick, pv, False, extra, mem, now)
    return _scene_render(sty, args, (name, vq, tick, pq, extra, steps) if sty.cache else None)


def style_is_smooth(name):
    """True for a scene style that asked for the smooth treatment (smooth=True in rec_styles.py)."""
    sty = _STY["by"].get(name)
    return bool(sty is not None and getattr(sty, "smooth", False))


def scene_save_rows(name, p, now):
    """The saving screen: the picture itself is the progress bar (p is 0..1)."""
    if name == "cassette":
        return cassette_save_rows(p, now)
    sty = _STY["by"].get(name)
    if sty is None:
        return _scene_blank()
    p = max(0.0, min(1.0, p))
    pq = int(round(p * 40))
    vq = int(round(p * sty.steps)) if sty.steps else 0
    tick = int(now * sty.fps) % sty.cycle
    args = (vq / float(sty.steps) if sty.steps else 0.0, tick, pq / 40.0, True, None, sty.mem, now)
    return _scene_render(sty, args, ("save", name, vq, pq, tick) if sty.cache else None)


def _px_to_rows(px):
    def ci(c):
        return None if c is None else (max(0, min(255, int(c[0]))), max(0, min(255, int(c[1]))), max(0, min(255, int(c[2]))))

    rows = []
    for y in range(0, len(px) - 1, 2):
        line = []
        for a, b in zip(px[y], px[y + 1]):
            a, b = ci(a), ci(b)
            if a is None and b is None:
                line.append(" ")
            elif a is None:
                line.append(fg(b) + "▄" + RESET)
            elif b is None:
                line.append(fg(a) + "▀" + RESET)
            elif a == b:
                line.append(bgc(a) + " " + RESET)
            else:
                line.append(fg(a) + bgc(b) + "▀" + RESET)
        rows.append("".join(line))
    return rows


# ---- built-in cassette style (default for mimic & Quran playback) ----

def _px_blank():
    return [[None] * RC_W for _ in range(RC_H)]


def _px_set(px, x, y, col):
    if 0 <= x < RC_W and 0 <= y < RC_H:
        px[y][x] = col


def _px_rect(px, x0, y0, x1, y1, col):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if 0 <= x < RC_W and 0 <= y < RC_H:
                px[y][x] = col


def _px_paint(px, cols, line_col):
    for (x, y), c in cols.items():
        edge = any((x + dx, y + dy) not in cols for dx, dy in _N4)
        _px_set(px, x, y, line_col if edge else c)


def _cassette_px(voice, aq, prog):
    px = _px_blank()
    body = {(x, y) for y in range(3, 22) for x in range(1, 30)} - {(1, 3), (29, 3), (1, 21), (29, 21)}
    cols = {}
    for x, y in body:
        cols[(x, y)] = (96, 106, 134) if y == 4 else (62, 70, 92)
    _px_paint(px, cols, (18, 20, 30))
    _px_rect(px, 4, 5, 26, 10, (238, 228, 198))          # the label
    _px_rect(px, 4, 6, 26, 7, (214, 70, 58))
    _px_rect(px, 6, 9, 24, 9, (176, 166, 134))
    _px_rect(px, 6, 11, 24, 17, (20, 22, 30))            # the tape window
    ang = aq / 8.0 * (2.0 * math.pi / 3.0)
    for cx, rad in ((10, 3.2 - 1.4 * prog), (20, 1.8 + 1.4 * prog)):
        for y in range(11, 18):
            for x in range(6, 25):
                d = math.hypot(x - cx, y - 14)
                if d <= rad:
                    px[y][x] = (110, 70, 40) if d > rad - 0.9 else (146, 96, 56)
                if d <= 1.9:
                    px[y][x] = (44, 46, 60)                  # the hub: a dark gear with three bright teeth
        for k in range(3):
            a = ang + k * 2.0 * math.pi / 3.0
            _px_set(px, cx + int(round(1.5 * math.cos(a))), 14 + int(round(1.5 * math.sin(a))), (226, 226, 234))
            _px_set(px, cx + int(round(0.7 * math.cos(a))), 14 + int(round(0.7 * math.sin(a))), (150, 150, 164))
    _px_rect(px, 8, 19, 22, 20, (80, 88, 112))           # the bottom trapezoid with its holes
    for x in (11, 19):
        _px_set(px, x, 20, (20, 22, 30))
    for x, y in ((3, 4), (27, 4), (3, 20), (27, 20)):    # screws
        _px_set(px, x, y, (160, 160, 172))
    n = int(round(voice * 8))
    for i in range(8):                                   # LED level meter
        if i >= n:
            c = (34, 38, 48)
        elif i < 5:
            c = (60, 210, 110)
        elif i < 7:
            c = (240, 210, 70)
        else:
            c = (240, 80, 70)
        _px_rect(px, 8 + 2 * i, 24, 9 + 2 * i, 25, c)
    return px


def _cassette_update(mem, dt, voice, active, prog, elapsed, now):
    period = 2.0 * math.pi / 3.0                                      # the reel has three spokes
    if active:
        mem["ang"] = (mem.get("ang", 0.0) + dt * (1.5 + 4.0 * voice)) % period
    return int(mem.get("ang", 0.0) / period * 8) % 8


_CASSETTE_MEM = {}
_CASSETTE_CACHE = {}


def cassette_rows(now, voice, active, prog, elapsed):
    """Built-in cassette style: animated reels, tape window, and LED level meter."""
    mem = _CASSETTE_MEM
    dt = min(0.5, max(0.0, now - mem["_t"])) if "_t" in mem else 0.0
    mem["_t"] = now
    v = max(0.0, min(1.0, voice))
    extra = _cassette_update(mem, dt, v, bool(active), prog, elapsed, now)
    vq = int(round(v * 8))
    pq = int(round(max(0.0, min(1.0, prog)) * 10))
    key = (vq, extra, pq)
    rows = _CASSETTE_CACHE.get(key)
    if rows is None:
        if len(_CASSETTE_CACHE) > 500:
            _CASSETTE_CACHE.clear()
        px = _cassette_px(vq / 8.0, extra, pq / 10.0)
        rows = _CASSETTE_CACHE[key] = _px_to_rows(px)
    return rows


def cassette_save_rows(progress, now):
    p = max(0.0, min(1.0, progress))
    tick = int(now * 12) % 8
    px = _cassette_px(p, tick, p)
    return _px_to_rows(px)


SMALL_W, SMALL_H = 15, 12       # the small face: 15 x 12 pixels, 6 console rows (no book)


def _small_face_px(icon, big):
    """The starry-eyed face drawn directly at small size (a shrunken copy of the big one turns to mud).
    icon: 2 reciter, 3 scholar (with the cap), 4 owl (with ears and a beak). big: the sparkles' tips are lit."""
    px = [[None] * SMALL_W for _ in range(SMALL_H)]

    def put(x, y, c):
        if 0 <= x < SMALL_W and 0 <= y < SMALL_H:
            px[y][x] = c

    owl = icon == 4
    shape = {(x, y) for y in range(SMALL_H) for x in range(SMALL_W)
             if ((x + 0.5 - 7.5) / 6.9) ** 2 + ((y + 0.5 - 5.6) / 5.4) ** 2 <= 1.0}
    if owl:
        for cx in (3, 11):                                   # the two ear tufts
            for r, y in enumerate((0, 1, 2)):
                for x in range(cx - r, cx + r + 1):
                    shape.add((x, y))
    outline = _OW_OUT if owl else _RC_OUTLINE
    for x, y in shape:
        edge = any((x + dx, y + dy) not in shape for dx, dy in _N4)
        if edge:
            put(x, y, outline)
        elif owl:
            put(x, y, lerp(_OW_HI, _OW_LO, y / 10.0) if y < 8 else _OW_BELLY)
        else:
            put(x, y, lerp(_RC_SKIN_HI, _RC_SKIN_LO, y / 10.0))
    if icon == 3:                                            # the white cap
        for x, y in shape:
            if y <= 1 and px[y][x] != _RC_OUTLINE:
                put(x, y, _SC_CAP)
            elif y == 2 and px[y][x] != _RC_OUTLINE:
                put(x, y, _SC_CAP_BAND)
    for ex in (4, 10):                                       # the starry eyes: a bright plus in a dark diamond
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            put(ex + dx, 5 + dy, _RC_INK)
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            put(ex + dx, 5 + dy, (255, 240, 60) if big else _RC_INK)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            put(ex + dx, 5 + dy, (255, 240, 60))
        put(ex, 5, (255, 255, 236))
    if owl:
        put(7, 7, _OW_BEAK)
        put(7, 8, _OW_BEAK_LO)
    else:
        for x, y in ((6, 8), (7, 8), (8, 8), (5, 7), (9, 7)):  # a happy smile
            put(x, y, _SC_MOUTH)
    return px


_LF_ROWS = {}


def loading_face_rows(icon, now):
    """The starry-eyed face of the Reciter (2), Scholar (3) or Owl (4), small, for the moment a recording
    is being saved ("wow, this one is going to be good"). None for the other styles."""
    if icon not in (2, 3, 4):
        return None
    big = math.sin(now * 9.0) >= 0                           # the sparkles twinkle
    key = (icon, big)
    rows = _LF_ROWS.get(key)
    if rows is None:
        rows = _LF_ROWS[key] = _px_to_rows(_small_face_px(icon, big))
    return rows


# ---- the second saving picture: he closes the book gently and bows his head (the other is the starry-eyed face) ----
SAVE_W, SAVE_H = 15, 18          # 15 x 18 pixels, 9 console rows
_SAVE = {"variant": 0}           # which picture this save uses (chosen in save(): 0 starry eyes, 1 bow)
_BOW_ROWS = {}


def _sm(a, b, x):
    t = max(0.0, min(1.0, (x - a) / float(b - a)))
    return t * t * (3.0 - 2.0 * t)


def _bow_px(icon, p):
    """p 0..1 is the saving progress: the book closes first, then his eyes shut and he bows his head."""
    px = [[None] * SAVE_W for _ in range(SAVE_H)]

    def put(x, y, c):
        if 0 <= x < SAVE_W and 0 <= y < SAVE_H:
            px[y][x] = c

    o = 1.0 - _sm(0.16, 0.62, p)                          # 1 open .. 0 closed
    hw = 3 + int(round(3 * o))
    pages = int(round(3 * o))
    covers = 2 + int(round(2 * (1.0 - o)))
    ytop = 17 - (pages + 1 + covers)
    cells = {}
    for y in range(ytop, 17):
        r = y - ytop
        if r < pages:
            k = "page"
        elif r == pages:
            k = "gold"
        else:
            k = "cover"
        for x in range(7 - hw, 8 + hw):
            cells[(x, y)] = k
    for (x, y) in cells:
        for dx, dy in _N4:
            n = (x + dx, y + dy)
            if n not in cells and 0 <= n[0] < SAVE_W and 0 <= n[1] < SAVE_H:
                put(n[0], n[1], _RC_BOOK_OUT)
    for (x, y), k in cells.items():
        if k == "page":
            put(x, y, _RC_CREASE if x == 7 else _RC_PAGE)
        elif k == "gold":
            put(x, y, _RC_GOLD)
        else:
            put(x, y, _RC_COVER_LO if y == 16 else (_RC_COVER_HI if x == 7 - hw else _RC_COVER))
    if o < 0.2:
        put(7, ytop + 1 + covers // 2, _RC_GOLD_HI)       # the gold star on the closed cover

    b = _sm(0.50, 0.92, p)                                # the bow
    dy = int(round(2.0 * b))
    owl = icon == 4
    shape = {(x, y) for y in range(SAVE_H) for x in range(SAVE_W)
             if ((x + 0.5 - 7.5) / 6.9) ** 2 + ((y + 0.5 - (5.6 + dy)) / 5.4) ** 2 <= 1.0}
    if owl:
        for cx in (3, 11):
            for r, y in enumerate((0, 1, 2)):
                for x in range(cx - r, cx + r + 1):
                    shape.add((x, y + dy))
    outline = _OW_OUT if owl else _RC_OUTLINE
    for x, y in shape:
        k = y - dy
        if any((x + a, y + c) not in shape for a, c in _N4):
            put(x, y, outline)
        elif owl:
            put(x, y, lerp(_OW_HI, _OW_LO, k / 10.0) if k < 8 else _OW_BELLY)
        else:
            put(x, y, lerp(_RC_SKIN_HI, _RC_SKIN_LO, k / 10.0))
    if icon == 3:                                         # the white cap
        for x, y in shape:
            k = y - dy
            if px[y][x] != outline:
                if k <= 1:
                    put(x, y, _SC_CAP)
                elif k == 2:
                    put(x, y, _SC_CAP_BAND)
    ey = 5 + dy
    if p < 0.42:                                          # looking down at the book
        for x in (3, 4, 10, 11):
            put(x, ey, _RC_INK)
            put(x, ey + 1, _RC_INK)
    else:                                                 # eyes shut
        for c in (4, 10):
            for x in (c - 1, c, c + 1):
                put(x, ey + 1, _RC_INK)
    if owl:
        put(7, ey + 2, _OW_BEAK)
        put(7, ey + 3, _OW_BEAK_LO)
    else:
        if p < 0.42:
            for x in (6, 7, 8):
                put(x, ey + 3, _SC_MOUTH)
        else:                                             # a small, peaceful smile
            for x, y in ((6, ey + 3), (7, ey + 3), (8, ey + 3), (5, ey + 2), (9, ey + 2)):
                put(x, y, _SC_MOUTH)
    return px


def bow_face_rows(icon, p):
    """The book-closing, head-bowing picture for the Reciter (2), Scholar (3) or Owl (4). None for the others."""
    if icon not in (2, 3, 4):
        return None
    q = int(round(max(0.0, min(1.0, p)) * 30))
    rows = _BOW_ROWS.get((icon, q))
    if rows is None:
        rows = _BOW_ROWS[(icon, q)] = _px_to_rows(_bow_px(icon, q / 30.0))
    return rows


_RC = dict(t=0.0, last_voice=0.0, run_start=0.0, e=0.0, ph=0.0, line=0,
           gx=0.0, gy=0.0, lid=0.0, brow=0.0, lit=0.0,
           next_blink=0.0, blink_until=0.0, blink2=0.0, next_wink=0.0, wink_until=0.0,
           happy_until=0.0, happy_last=-99.0, startle_until=0.0, greet_until=0.0, exc_until=0.0,
           yawn_at=0.0, was_live=False, flip_t0=-99.0, next_flip=0.0,
           wander_at=0.0, wx=0.0, wy=0.0, read_t=0.0, flip_gap=60.0,
           pace=4.0, syl_acc=0.0, syl_t=0.0)
def _rc_ease(cur, target, dt, rate):
    return cur + (target - cur) * (1.0 - math.exp(-dt * rate))


_WANDER = ((-0.9, -0.3), (0.9, -0.3), (0.0, -0.9), (0.8, 0.6), (-0.8, 0.6), (0.0, 0.0))


def reciter_key(now, voice, live, elapsed, slow_glow=False, syl=None):
    """Advance the character by one frame and return what to draw (a hashable key for mascot_rows).
    voice: how loud it is right now (0..1). live: recording. elapsed: seconds recorded so far.
    slow_glow: the glow follows the voice gently (the Scholar), instead of snapping on.
    syl: syllables heard since the last frame (None = not recording).
    What he does:
      you recite     he reads along, calmly and with a closed mouth: his eyes run right to left over the line at
                     the speed you recite, and the page turns about once a minute of reciting
      you pause      he looks at you and smiles; a moment later one eyebrow goes up; then he looks around
      a long pause   "hmm?", then he gets sleepy, yawns and falls asleep (never while recording)
      you start again  if he was dozing, he jumps awake
      a good take    after reciting for a while and then pausing he is pleased: happy eyes and a small smile
    """
    st = _RC
    rnd = random.random
    gone = (now - st["t"]) if st["t"] else 0.0
    dt = min(0.5, max(0.0, now - st["t"])) if st["t"] else 0.0
    first = not st["t"]
    st["t"] = now
    if first or gone > 2.0:                              # not on screen for a while: he looks up fresh
        st["last_voice"] = now - RC_HOLD
        st["run_start"] = st["last_voice"]
        st["greet_until"] = now + 0.9
        st["e"] = 0.0
        st["read_t"] = 0.0
        st["flip_gap"] = FLIP_EVERY
    e = st["e"]
    e += (voice - e) * (1.0 - math.exp(-dt * (36.0 if voice > e else 9.0)))    # quick: this drives the mouth
    st["e"] = e
    if live and not st["was_live"]:                      # a recording starts: he is ready
        st["last_voice"] = now - RC_HOLD
        st["run_start"] = st["last_voice"]
        st["greet_until"] = now + 1.0
    st["was_live"] = live

    gap = now - st["last_voice"]
    if voice > RC_VOICE:
        if gap >= RC_HOLD:                               # a new burst of speech
            if gap >= 15.0 and not live:
                st["startle_until"] = now + 0.9          # he was asleep
            elif gap >= 11.0 and not live:
                st["startle_until"] = now + 0.5
            if gap >= RC_RUN_GAP:
                st["run_start"] = now
        st["last_voice"] = now
        gap = 0.0
    quiet = min(gap, 10.5) if live else gap              # while recording he never dozes off
    reading = quiet < RC_HOLD
    if syl is not None and reading:                      # how fast are you reciting? (syllables per second)
        st["syl_acc"] += syl
        st["syl_t"] += dt
        if st["syl_t"] >= 2.0:
            rate = st["syl_acc"] / st["syl_t"]
            st["syl_acc"] = st["syl_t"] = 0.0
            if rate >= 0.8:
                st["pace"] += (max(PACE_MIN, min(PACE_MAX, rate)) - st["pace"]) * 0.45
    run = st["last_voice"] - st["run_start"]
    if not reading and 0.9 <= quiet < 1.4 and run >= RC_PROUD_RUN and now - st["happy_last"] > RC_PROUD_GAP:
        st["happy_until"] = now + 2.8                    # that was a good one
        st["happy_last"] = now
    excited = False
    startled = now < st["startle_until"]
    greeting = now < st["greet_until"] and not reading
    happy = now < st["happy_until"] and not reading

    mood = 0
    tgx = tgy = tlid = tbrow = 0.0
    if startled:
        mood, tbrow = 5, 1.0
    elif reading:
        mood = 0
        st["ph"] += dt * st["pace"] / PACE_LINE         # he reads at the speed you recite
        if st["ph"] >= 1.0:
            st["ph"] -= 1.0
            st["line"] ^= 1
        tgx, tgy, tlid = 0.55 - 1.1 * st["ph"], 0.8, 0.22   # right to left, like the Arabic line
    elif happy or greeting:
        mood, tbrow = 1, 0.0
    elif quiet < RC_BROW_AFTER:
        pass                                             # looking at you, smiling
    elif quiet < 4.0:
        tbrow = 1.0
    elif quiet < 9.0:
        if now >= st["wander_at"]:                       # he looks around, waiting for you
            st["wx"], st["wy"] = _WANDER[int(rnd() * len(_WANDER)) % len(_WANDER)]
            st["wander_at"] = now + 1.1 + rnd() * 1.3
        tgx, tgy = st["wx"], st["wy"]
        tbrow = 1.0 if tgy <= 0.0 else 0.0
    elif quiet < 11.0:
        tgx, tgy, tbrow = 0.6, -0.8, 1.0                 # "hmm?" and a question mark
    elif quiet < 15.0:
        mood, tgy, tlid = 2, 0.6, 0.12 + 0.26 * (quiet - 11.0) / 4.0     # sleepy
    elif quiet < 16.6:
        mood = 4                                         # a big yawn
    else:
        mood = 3                                         # asleep
    st["gx"] = _rc_ease(st["gx"], tgx, dt, 14.0)
    st["gy"] = _rc_ease(st["gy"], tgy, dt, 14.0)
    st["lid"] = _rc_ease(st["lid"], tlid, dt, 8.0)
    st["brow"] = _rc_ease(st["brow"], tbrow, dt, 9.0 if tbrow > st["brow"] else 14.0)
    tlit = min(1.0, 0.35 + 0.4 * e) if reading else (0.12 if mood in (1, 5) else 0.0)
    if slow_glow:
        st["lit"] = _rc_ease(st["lit"], tlit, dt, 3.5 if tlit > st["lit"] else 1.6)
    else:
        st["lit"] = _rc_ease(st["lit"], tlit, dt, 16.0 if tlit > st["lit"] else 5.0)

    blink = 0
    if now >= st["next_blink"]:
        if st["next_blink"]:
            st["blink_until"] = now + (0.45 if mood == 2 else 0.18)
            st["blink2"] = st["blink_until"] + 0.2 if rnd() < 0.25 else 0.0
        st["next_blink"] = now + 2.4 + rnd() * 3.4
    if now < st["blink_until"] or (st["blink2"] and st["blink2"] <= now < st["blink2"] + 0.16):
        blink = 3

    breath = 1 if (now % 2.6) < 0.9 else 0
    if mood == 3:
        breath = 1 if (now % 4.4) < 2.2 else 0           # slow, deep sleep breathing
    bob = breath
    hand = 0
    ear = 0
    if startled:
        bob, ear, hand = -1, 2, 3
    elif mood == 6:
        bob = -1 if int(now * 5) % 2 else breath          # bouncing
        ear = 1
        hand = 1 if int(now * 5) % 2 else 2
    elif reading:
        bob = breath
    elif mood == 1:
        bob = -1 if int(now * 4) % 2 else 0
        ear, hand = 1, (3 if int(now * 4) % 2 else 0)
    elif mood in (2, 3, 4):
        ear = -1
    elif brow_up_twitch(now, quiet):
        ear = 1

    flip = -1
    if quiet < RC_RUN_GAP:
        st["read_t"] += dt                               # counts the time you are actually reciting
    if reading and st["read_t"] >= st["flip_gap"]:       # the page turns about once a minute
        st["read_t"] = 0.0
        st["flip_gap"] = FLIP_EVERY + rnd() * 10.0
        st["flip_t0"] = now
    f = int((now - st["flip_t0"]) / 0.12)
    if 0 <= f <= 3:
        flip = f
        if mood == 0 and f <= 2:
            hand = 2                                     # the right hand lifts the page

    talk = 0                                             # he listens calmly: his mouth stays closed
    hl = -1

    sym = 0
    if mood == 5:
        sym = 4 * 16 + 3
    elif mood == 1:
        if greeting and not happy:
            sym = 7 * 16 + (int(now * 3) % 3) * 4 + 3
    elif mood in (2, 3):
        tz = (now * 0.5) % 1.0
        sym = (6 if mood == 3 else 5) * 16 + min(2, int(tz * 3)) * 4 + (3 if tz < 0.75 else 1)
    elif not reading and 9.0 <= quiet < 11.0:
        sym = 3 * 16 + (int(now * 3) % 2) * 4 + 3

    return (
        0 if reading else 1,
        round(st["gx"] * 5) / 5,
        round(st["gy"] * 5) / 5,
        round(st["lid"] * 8) / 8,
        round(st["brow"] * 5) / 5,
        blink,
        bob,
        round(st["lit"] * 12) / 12,
        talk,
        mood,
        sym,
        flip,
        hand,
        ear,
        hl,
    )


def brow_up_twitch(now, quiet):
    """The owl's ears twitch now and then while he waits for you."""
    return 1.6 <= quiet < 11.0 and (now % 3.1) < 0.3


MIC_W = 15
MIC = [r.center(MIC_W) for r in MIC]


def paint_mic(row, color_at):
    """Full blocks become background-coloured cells (no hairline gaps between neighbours).
    Half blocks stay characters, they are only used on the edges."""
    out = []
    for j, ch in enumerate(row):
        if ch == " ":
            out.append(" ")
        elif ch == "█":
            out.append(bgc(color_at(j)) + " " + RESET)
        else:
            out.append(fg(color_at(j)) + ch + RESET)
    return "".join(out)
# Sound waves: 3 thin rings on each side. A ring is 2 cells wide and its bar is half a cell,
# so it can sit in 4 different places, which is what bends the bar into a curve.
RINGS = 3
ARC_ROWS = range(1, 6)
ARC_PROFILE = (1.0, 0.33, 0.0, 0.33, 1.0)


def arc_cell(row, k, bulge_out, left_side):
    """(cell index, character) of ring k on this row. Ring 0 is the one next to the mic."""
    u = round(3 * ARC_PROFILE[row - 1])          # 0 = outer edge of the ring, 3 = inner edge
    if not bulge_out:
        u = 3 - u
    if left_side:
        return ARC_W - 2 - 2 * k + u // 2, ("▌" if u % 2 == 0 else "▐")
    return 2 * k + 1 - u // 2, ("▐" if u % 2 == 0 else "▌")


ARC_W = 2 * RINGS
GAP = 2
FULL_W = ARC_W + GAP + MIC_W + GAP + ARC_W
PANEL_GAP = 3
PANEL_MIN = 24
MARK_W = 14
MARK_GAP = 3
PANEL_MAX = 46
VISIBLE = len(MIC) - 4


def log_error(where):
    global LOG_STARTED
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            if not LOG_STARTED:
                LOG_STARTED = True
                f.write(
                    f"python {sys.version.split()[0]}, numpy {np.__version__}, "
                    f"miniaudio {getattr(miniaudio, '__version__', 'missing')}, "
                    f"sounddevice {getattr(sd, '__version__', 'unknown')}\n"
                )
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}\n{traceback.format_exc()}\n")
    except Exception:
        pass


_FG = {}


def fg(c):
    key = (int(c[0]), int(c[1]), int(c[2]))
    code = _FG.get(key)
    if code is None:
        if len(_FG) > 20000:
            _FG.clear()
        code = _FG[key] = f"\x1b[38;2;{key[0]};{key[1]};{key[2]}m"
    return code


_BG = {}


def bgc(c):
    key = (int(c[0]), int(c[1]), int(c[2]))
    code = _BG.get(key)
    if code is None:
        if len(_BG) > 20000:
            _BG.clear()
        code = _BG[key] = f"\x1b[48;2;{key[0]};{key[1]};{key[2]}m"
    return code


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


_SGR = re.compile(r"\x1b\[([0-9;]*)m")


_VLEN = {}


def vlen(text):
    """Visible length of a string that contains colour codes."""
    n = _VLEN.get(text)
    if n is None:
        if len(_VLEN) > 800:
            _VLEN.clear()
        n = _VLEN[text] = len(_SGR.sub("", text))
    return n


def pad_to(text, width):
    return text + " " * max(0, width - vlen(text))


def gtext(text, t, u0, u1, mix=1.0, bold=False):
    """Colour each character along the theme gradient (u0 -> u1)."""
    n = len(text)
    out = ["\x1b[1m"] if bold else []
    for i, ch in enumerate(text):
        if ch == " ":
            out.append(" ")
            continue
        u = u0 + (u1 - u0) * (i / max(1, n - 1))
        u = max(0.0, min(1.0, u))
        out.append(fg(lerp(DIM, lerp(t["accent"], t["grad"], u), mix)) + ch)
    out.append(RESET)
    return "".join(out)


def jade(t):
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return lerp(JADE_LO, JADE_MID, t * 2)
    return lerp(JADE_MID, JADE_HI, (t - 0.5) * 2)


IN_MODE = None


def enable_vt():
    global IN_MODE
    kernel = ctypes.windll.kernel32
    handle = kernel.GetStdHandle(-11)
    mode = ctypes.c_ulong()
    if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel.SetConsoleMode(handle, mode.value | 0x0004)
    try:
        kernel.SetConsoleTitleW(APP_NAME)
    except Exception:
        pass
    # QuickEdit makes a mouse click start a text selection (square cursor + frozen output)
    try:
        hin = kernel.GetStdHandle(-10)
        imode = ctypes.c_ulong()
        if kernel.GetConsoleMode(hin, ctypes.byref(imode)):
            IN_MODE = imode.value
            kernel.SetConsoleMode(hin, (imode.value | 0x0080) & ~0x0040 & ~0x0010)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def restore_console():
    if IN_MODE is None:
        return
    try:
        kernel = ctypes.windll.kernel32
        kernel.SetConsoleMode(kernel.GetStdHandle(-10), IN_MODE)
    except Exception:
        pass


def fmt_time(seconds):
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    text = f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
    return " ".join(text)


def center(plain_len, cols):
    return " " * max(0, (cols - plain_len) // 2)


def fmt_dur(seconds):
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def fmt_size(n):
    if n >= 1048576:
        return f"{n / 1048576:.1f}MB"
    return f"{max(1, n // 1024)}KB"


def fmt_len(dur):
    return "--:--" if dur <= 0 else fmt_dur(dur)


def char_w(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def cell_w(text):
    return sum(char_w(ch) for ch in text)


def lpad(text, width):
    return " " * max(0, width - cell_w(text)) + text


_DROP_CATS = {"Mn", "Mc", "Me", "Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"}


def clean_name(text):
    """Make a title safe for a fixed-width console.

    The layout assumes every character is exactly 1 cell, but the console disagrees for
    emoji, combining marks, zero-width/bidi characters and scripts the font has no glyph
    for (they show as boxes and get a random width). So: fold fancy letters to plain ones
    (NFKC turns bold-math / fullwidth text into ASCII), drop the invisible stuff, and
    show anything else the font can't draw as a single "?".
    """
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        cp = ord(ch)
        if cp > 0xFFFF or unicodedata.category(ch) in _DROP_CATS:
            continue
        if ch.isspace():
            ch = " "
        elif not (cp < 0x0530 or 0x2010 <= cp <= 0x2027 or cp == 0x20AC):
            ch = "?"  # not Latin/Greek/Cyrillic: the console font likely has no glyph
        if ch == "?" and out and out[-1] == "?":
            continue
        out.append(ch)
    return " ".join("".join(out).split()) or "?"


def fit(text, width):
    text = clean_name(text)
    if len(text) <= width:
        return text
    out = []
    used = 0
    for ch in text:
        w = char_w(ch)
        if used + w > width - 2:
            break
        out.append(ch)
        used += w
    return "".join(out) + ".."


class UI:
    def __init__(self):
        self.which = None
        self.entries = []
        self.sel = 0
        self.scroll = 0
        self.confirm = False
        self.note = ""
        self.note_until = 0.0
        self.mfocus = False
        self.msel = 0
        self.mscroll = 0
        self.saved = None

    @property
    def list_open(self):
        return self.which is not None

    def reset_marks(self):
        self.mfocus = False
        self.msel = 0
        self.mscroll = 0


def clamp_scroll(ui):
    vis = mimic_vis() if ui.which == "mimic" else VISIBLE
    ui.scroll = max(0, min(ui.scroll, len(ui.entries) - vis))
    if ui.sel < ui.scroll:
        ui.scroll = ui.sel
    elif ui.sel >= ui.scroll + vis:
        ui.scroll = ui.sel - vis + 1


MARKS = {}


def marks_path(path):
    path = Path(path)
    return path.with_name(path.name + ".marks.json")


def get_marks(path):
    key = str(path)
    if key not in MARKS:
        try:
            data = json.loads(marks_path(path).read_text(encoding="utf-8"))
            MARKS[key] = sorted(float(x) for x in data)
        except Exception:
            MARKS[key] = []
    return MARKS[key]


def set_marks(path, marks):
    marks = sorted(marks)
    target = marks_path(path)
    if marks:
        write_atomic(target, json.dumps([round(m, 2) for m in marks]).encode("utf-8"))
    else:
        target.unlink(missing_ok=True)
    MARKS[str(path)] = marks


def scan_recordings(folder):
    MARKS.clear()
    found = []
    if folder.exists():
        for p in folder.iterdir():
            ext = p.suffix.lower()
            if ext not in (".mp3", ".wav"):
                continue
            try:
                st = p.stat()
                if ext == ".mp3":
                    dur = st.st_size * 8 / (BITRATE * 1000)
                else:
                    with wave.open(str(p), "rb") as w:
                        dur = w.getnframes() / w.getframerate()
            except Exception:
                continue
            found.append((st.st_mtime, p, p.stem, dur, st.st_size))
    found.sort(key=lambda item: item[0], reverse=True)
    return [(p, name, dur, size) for _, p, name, dur, size in found]


NATIVE_EXTS = (".mp3", ".wav", ".flac", ".ogg")
MIMIC_EXTS = NATIVE_EXTS + (
    ".oga", ".opus", ".m4a", ".m4b", ".aac", ".wma", ".webm", ".mp4", ".mka", ".aiff", ".aif", ".amr", ".3gp",
)
NAME_CAP = 22
INFO_FUNCS = {
    ".mp3": "mp3_get_info",
    ".wav": "wav_get_info",
    ".flac": "flac_get_info",
    ".ogg": "vorbis_get_info",
}
FILE_INFO_FUNCS = {
    ".mp3": "mp3_get_file_info",
    ".wav": "wav_get_file_info",
    ".flac": "flac_get_file_info",
    ".ogg": "vorbis_get_file_info",
}
DUR_CACHE = {}


def _dur_key(path, st):
    return (str(path), st.st_mtime, st.st_size)


def av_duration(path):
    if av is None:
        return 0.0
    try:
        with av.open(str(path)) as container:
            if container.duration:
                return float(container.duration) / 1000000.0
            stream = container.streams.audio[0]
            if stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
    except Exception:
        pass
    return 0.0


def probe_duration(path, st):
    key = _dur_key(path, st)
    if key not in DUR_CACHE:
        dur = 0.0
        if miniaudio is not None:
            ext = path.suffix.lower()
            try:
                dur = float(getattr(miniaudio, FILE_INFO_FUNCS[ext])(str(path)).duration)
            except Exception:
                try:
                    dur = float(getattr(miniaudio, INFO_FUNCS[ext])(path.read_bytes()).duration)
                except Exception:
                    dur = 0.0
        if dur <= 0:
            dur = av_duration(path)
        DUR_CACHE[key] = dur
    return DUR_CACHE[key]


def scan_mimic(folder):
    """Fast: uses cached durations only. probe_missing() fills in the rest in the background."""
    found = []
    if folder.exists():
        for p in folder.iterdir():
            if p.suffix.lower() not in MIMIC_EXTS:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            found.append((p.stem.lower(), p, p.stem, DUR_CACHE.get(_dur_key(p, st), 0.0), st.st_size))
    found.sort(key=lambda item: item[0])
    return [(p, name, dur, size) for _, p, name, dur, size in found]


def probe_missing(ui, entries):
    def work():
        for i in range(len(entries)):
            if ui.entries is not entries:
                return
            path, name, dur, size = entries[i]
            try:
                st = path.stat()
                if _dur_key(path, st) in DUR_CACHE:
                    continue
                dur = probe_duration(path, st)
            except Exception:
                continue
            if ui.entries is entries and i < len(entries) and entries[i][0] == path:
                entries[i] = (path, name, dur, size)

    if any(_dur_key_missing(e[0]) for e in entries):
        threading.Thread(target=work, daemon=True).start()


def _dur_key_missing(path):
    try:
        return _dur_key(path, path.stat()) not in DUR_CACHE
    except OSError:
        return False


_WIN_READY = []


def window_state():
    """'min' (minimized), 'back' (visible but not the active window), 'front', or None when
    it can't be told (for example inside Windows Terminal, where the console window is hidden)."""
    try:
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        if not _WIN_READY:
            k32.GetConsoleWindow.restype = ctypes.c_void_p
            u32.GetForegroundWindow.restype = ctypes.c_void_p
            for fn in (u32.IsIconic, u32.IsWindowVisible):
                fn.argtypes = [ctypes.c_void_p]
            _WIN_READY.append(1)
        hwnd = k32.GetConsoleWindow()
        if not hwnd:
            return None
        if u32.IsIconic(hwnd):
            return "min"
        if not u32.IsWindowVisible(hwnd):
            return None
        return "front" if u32.GetForegroundWindow() == hwnd else "back"
    except Exception:
        return None


_WAIT_READY = []


def wait_input(ms):
    """Sleep until a key arrives or `ms` pass. While Windows has the thread parked this
    costs no CPU. It can never spin: if the wait returns early without a real key (a stray
    console event, or the call failing), it sleeps out a minimum gap before returning."""
    t0 = time.perf_counter()
    try:
        k32 = ctypes.windll.kernel32
        if not _WAIT_READY:
            k32.GetStdHandle.restype = ctypes.c_void_p
            k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            _WAIT_READY.append(1)
        k32.WaitForSingleObject(k32.GetStdHandle(-10), int(ms))
    except Exception:
        pass
    if msvcrt.kbhit():  # a real key press: leave it for the main loop
        return
    spent = time.perf_counter() - t0
    if spent < 0.05:
        time.sleep(0.05 - spent)


PERF = os.environ.get("TIKRAR_PERF") == "1"   # set TIKRAR_PERF=1 to see live numbers in the window title
_PERF = {"t": 0.0, "cpu": 0.0, "n": 0, "bytes": 0, "build": 0.0, "writes": 0}


def perf_note(build_s, nbytes):
    """Once a second: how much CPU this process used, what a frame costs to build, and how many
    bytes per second the console is made to draw (that part is paid by the terminal, not by us)."""
    st = _PERF
    now = time.time()
    if not st["t"]:
        st["t"], st["cpu"] = now, time.process_time()
    st["n"] += 1
    st["build"] += build_s
    st["bytes"] += nbytes
    st["writes"] += 1 if nbytes else 0
    if now - st["t"] >= 1.0:
        dt = now - st["t"]
        cpu = time.process_time()
        title = (
            f"{APP_NAME}  py {100 * (cpu - st['cpu']) / dt:.1f}% of 1 core  "
            f"frame {1000 * st['build'] / max(1, st['n']):.1f}ms  "
            f"{st['bytes'] / dt / 1024:.1f}KB/s  {st['writes'] / dt:.0f} draws/s"
        )
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
        st.update(t=now, cpu=cpu, n=0, bytes=0, build=0.0, writes=0)


def sleep_until(deadline):
    """Wait for the next frame, or for a key press, whichever comes first. Most of the wait is
    spent parked inside Windows (no polling, no CPU). Windows only wakes a parked thread on its
    ~16 ms clock tick, which would make frames uneven, so the last 20 ms are a short precise sleep."""
    while True:
        left = deadline - time.time()
        if left <= 0.002 or msvcrt.kbhit():
            return
        if left <= 0.03:
            time.sleep(min(left, 0.01))
            continue
        t0 = time.perf_counter()
        try:
            k32 = ctypes.windll.kernel32
            if not _WAIT_READY:
                k32.GetStdHandle.restype = ctypes.c_void_p
                k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                _WAIT_READY.append(1)
            k32.WaitForSingleObject(k32.GetStdHandle(-10), int((left - 0.02) * 1000))
        except Exception:
            time.sleep(0.01)
            continue
        if time.perf_counter() - t0 < 0.002 and not msvcrt.kbhit():
            time.sleep(0.01)  # woke at once without a key (a stray console event): never spin


def lower_thread_priority():
    """Background work yields to the screen and the audio."""
    try:
        k32 = ctypes.windll.kernel32
        k32.GetCurrentThread.restype = ctypes.c_void_p
        k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        k32.SetThreadPriority(k32.GetCurrentThread(), -2)  # THREAD_PRIORITY_LOWEST
    except Exception:
        pass


def trim_memory(collect=False):
    """Give pages we are not touching back to Windows. The decoded audio is only read
    sequentially while it plays, so everything behind the playhead can leave the working
    set (Task Manager's Memory column) without costing anything: it is faulted back in
    on demand if a seek needs it."""
    try:
        if collect:
            gc.collect()
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
        k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
    except Exception:
        pass


def compute_env(data, bins=240):
    x = data[:, 0]
    edges = np.linspace(0, len(x), bins + 1).astype(int)
    vals = np.zeros(bins, dtype=np.float32)
    for i in range(bins):
        seg = x[edges[i]:edges[i + 1]]
        if len(seg):
            vals[i] = float(np.sqrt(np.mean(np.square(seg.astype(np.float32)))))
    p95 = float(np.percentile(vals, 95))
    return vals, max(p95, 1.0)


def decode_av(path):
    container = av.open(io.BytesIO(Path(path).read_bytes()))
    try:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=44100)
        buf = bytearray()  # grows in place, so the whole file is never held twice

        def take(frames):
            if frames is None:
                return
            if not isinstance(frames, (list, tuple)):
                frames = [frames]
            for fr in frames:
                arr = fr.to_ndarray().reshape(-1)[: fr.samples]
                buf.extend(arr.astype(np.int16).tobytes())

        for frame in container.decode(stream):
            take(resampler.resample(frame))
        take(resampler.resample(None))
    finally:
        container.close()
    if not buf:
        return np.zeros((0, 1), dtype=np.int16), 44100
    return np.frombuffer(buf, dtype=np.int16).reshape(-1, 1), 44100


def decode_audio(path):
    ext = Path(path).suffix.lower()
    if miniaudio is not None and ext in NATIVE_EXTS:
        try:
            decoded = miniaudio.decode(Path(path).read_bytes(), nchannels=1, sample_rate=44100)
            return np.frombuffer(decoded.samples, dtype=np.int16).reshape(-1, 1), decoded.sample_rate
        except Exception:
            if av is None:
                raise
    if av is None:
        raise RuntimeError("this format needs the av library: pip install av")
    return decode_av(path)


def wave_lines(player, width):
    """Two mirrored rows (top, bottom) drawn only with the ▄ ▀ █ blocks."""
    t = theme()
    vals = player.env
    loud = None
    if vals is not None:
        cache = player.wave_cache
        if cache is None or cache[0] is not vals or cache[1] != width:
            idx = np.linspace(0, len(vals), width + 1).astype(int)
            flags = []
            for c in range(width):
                seg = vals[idx[c]:max(idx[c] + 1, idx[c + 1])]
                v = float(seg.mean()) if len(seg) else 0.0
                flags.append(min(1.0, v / player.p95) ** 0.7 > 0.55)
            cache = player.wave_cache = (vals, width, flags)
        loud = cache[2]
        head = int(width * player.position / player.duration) if player.duration else 0
    else:
        head = -1
    wk = (id(vals), width, head, STYLE["theme"], id(loud))
    hit = player.wave_rows
    if hit is not None and hit[0] == wk:
        return hit[1]
    top, bot = [], []
    for c in range(width):
        base = lerp(t["accent"], t["grad"], c / max(1, width - 1))
        tc, bc = ("█", "█") if (loud is not None and loud[c]) else ("▄", "▀")
        if c == head:
            col = "\x1b[1m" + fg(t["head"])
        elif c < head:
            col = fg(base)
        else:
            col = fg(lerp(t["deep"], base, 0.3))
        top.append(col + tc + RESET)
        bot.append(col + bc + RESET)
    rows = ("".join(top), "".join(bot))
    player.wave_rows = (wk, rows)  # the wave only moves when the playhead crosses into the next column
    return rows


class Engine:
    """The one audio output stream, opened once and kept for as long as sound is playing.

    Seeking, pausing and resuming never open or close a stream (that is what made them lag
    and click): they only tell the audio callback where to read from, and the callback
    cross-fades over a few milliseconds so there is never a hard jump in the waveform.
    After a few quiet seconds the stream is closed so a paused app costs nothing."""

    BLOCK = 2048   # frames per audio block (46 ms): ~21 Python callbacks per second
    FADE = 256     # frames (~6 ms) cross-fade when the read position jumps
    RAMP = 1024    # frames (~23 ms) fade for pause / resume
    LINGER = 3.0   # seconds of quiet before the stream is closed

    def __init__(self):
        self.stream = None
        self.sr = 44100
        self.data = None
        self.pos = 0
        self.gain = 0.0
        self.target = 0.0
        self.pending = None
        self.release_at = 0.0

    def _callback(self, outdata, frames, time_info, status):
        try:
            self._fill(outdata, frames)
        except Exception:
            outdata.fill(0)  # never let an error escape into PortAudio

    def _fill(self, outdata, frames):
        out = outdata[:, 0]
        pend, self.pending = self.pending, None
        data, pos, gain, target = self.data, self.pos, self.gain, self.target
        if pend is None and (data is None or (gain == 0.0 and target == 0.0)):
            out[:] = 0
            return
        if pend is None and gain == 1.0 and target == 1.0:
            seg = data[pos:pos + frames, 0]  # steady playback: a plain copy
            k = len(seg)
            out[:k] = seg
            out[k:] = 0
            self.pos = pos + k
            return
        block = np.zeros(frames, np.float32)
        if pend is None:
            seg = data[pos:pos + frames, 0]
            k = len(seg)
            if k:
                block[:k] = seg
                n = min(self.RAMP, k)
                block[:n] *= np.linspace(gain, target, n, dtype=np.float32)
                block[n:k] *= target
            self.pos = pos + k
        else:
            ndata, npos = pend
            f = min(self.FADE, frames)
            if data is not None and gain > 0.0:  # the old position fades out ...
                old = data[pos:pos + f, 0]
                if len(old):
                    block[:len(old)] = old * np.linspace(gain, 0.0, f, dtype=np.float32)[:len(old)]
            new = ndata[npos:npos + frames, 0]
            k = len(new)
            if k:
                fresh = new.astype(np.float32) * target
                m = min(f, k)
                fresh[:m] *= np.linspace(0.0, 1.0, f, dtype=np.float32)[:m]  # ... as the new one fades in
                block[:k] += fresh
            self.data, self.pos = ndata, npos + k
        self.gain = target
        np.clip(block, -32768, 32767, out=block)
        out[:] = block

    def _open(self, sr):
        if self.stream is not None and sr != self.sr:
            self.close()
        if self.stream is None:
            self.sr = sr
            self.gain = 0.0
            self.stream = sd.OutputStream(
                samplerate=sr, channels=1, dtype="int16", blocksize=self.BLOCK, callback=self._callback
            )
            self.stream.start()

    def play(self, data, pos, sr):
        self._open(sr)
        self.release_at = 0.0
        self.target = 1.0
        self.pending = (data, pos)

    def pause(self):
        self.target = 0.0
        self.release_at = time.time()

    def release(self, drain=False):
        """The player let go of the track. drain: it ended by itself, let the tail play."""
        self.release_at = time.time() + (1.0 if drain else 0.0)
        if not drain:
            self.target = 0.0

    def tick(self, now):
        """Called every loop pass: close the stream once it has been quiet for a while."""
        if self.stream is None or not self.release_at or now < self.release_at:
            return
        self.target = 0.0
        if now > self.release_at + self.LINGER:
            self.close()

    def close(self):
        stream, self.stream = self.stream, None
        self.data, self.pending, self.release_at = None, None, 0.0
        self.gain = self.target = 0.0
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log_error("closing the audio stream")


class Player:
    def __init__(self):
        self.path = None
        self.data = None
        self.sr = 44100
        self.duration = 0.0
        self.offset = 0.0
        self.started = 0.0
        self.playing = False
        self.loading = False
        self.token = 0
        self.error = ""
        self.env = None
        self.p95 = 1.0
        self.engine = Engine()
        self.level = 0.0
        self._lvl = 0.0   # the exact value; .level is the same rounded to 1/16 so frames repeat
        self._lvl_t = 0.0   # when the loudness was last worked out (the smooth path follows the clock, not the frame count)
        self.loop = False
        self.paused = False
        self.pause_pos = 0.0
        self.start_at = 0.0
        self.ended_path = None
        self.wave_cache = None
        self.wave_rows = None
        # mic glow "engine": 0 = cold, 1 = fully lit (see glow_update)
        self.gate = 0.0
        self.flash = 0.0
        self._gate_on = False
        self._gate_t = 0.0
        self._gate_from = 0.0
        self._gate_kind = "engine"
        self.glitch_t = -99.0  # time of the last seek

    def glitch(self, now):
        return max(0.0, 1.0 - (now - self.glitch_t) / SEEK_GLITCH)

    def glow_update(self, now):
        """Intro/outro for the mimic mic. The style follows the colour theme (GLOW_KINDS)."""
        on = self.playing
        if on != self._gate_on:
            self._gate_on = on
            self._gate_t = now
            self._gate_from = self.gate
            self._gate_kind = THEME_KIND.get(THEMES[STYLE["theme"]][0], "engine")
        kind = self._gate_kind
        t_in, t_out = GLOW_KINDS[kind]
        t = now - self._gate_t
        lo = self._gate_from
        self.flash = 0.0
        if on:
            k = min(1.0, t / t_in)
            if kind == "sign":
                # a neon tube buzzing on: fast flicker that calms down, then a soft pop
                base = lo + (1.0 - lo) * min(1.0, k / 0.8) ** 0.8
                f = math.sin(t * 47.0) + math.sin(t * 19.3 + 0.7) + math.sin(t * 7.9)
                lit = f > (-0.4 - 1.4 * k)
                g = base if lit or k >= 1.0 else base * 0.1
                if 0.78 <= k < 0.95:
                    self.flash = math.sin((k - 0.78) / 0.17 * math.pi) * 0.6 * (1.0 - lo)
            elif kind == "tube":
                # a warming filament: slow, no misfires, just a couple of soft dips
                g = lo + (1.0 - lo) * k ** 2.2
                g *= 1.0 - 0.15 * max(0.0, math.sin(t * 6.0)) * (1.0 - k)
                if 0.8 <= k < 1.0:
                    self.flash = math.sin((k - 0.8) / 0.2 * math.pi) * 0.3 * (1.0 - lo)
            elif kind == "snap":
                # dark, then one clean flash
                g = lo if k < 0.3 else 1.0
                if 0.3 <= k < 0.75:
                    self.flash = math.sin((k - 0.3) / 0.45 * math.pi) * 0.8 * (1.0 - lo)
            else:
                if k < 0.62:
                    # cranking: hard on/off misfires, each one catching a little higher
                    crank = lo + (0.55 - lo) * (k / 0.62) ** 1.3
                    fire = (math.sin(t * 23.0) + math.sin(t * 8.7 + 1.3)) > (0.3 - 1.0 * k)
                    g = crank if fire else crank * 0.15
                elif k < 0.74:
                    # the catch: a dark beat, then everything slams on
                    g = 0.04 if k < 0.68 else 0.8
                else:
                    j = (k - 0.74) / 0.26
                    g = 0.8 + 0.2 * (j * j * (3 - 2 * j))
                if 0.68 <= k < 0.95:
                    self.flash = math.sin((k - 0.68) / 0.27 * math.pi) * (1.0 - lo)
        else:
            k = min(1.0, t / t_out)
            if kind == "tube":
                g = lo * (1.0 - k) ** 1.8  # cools slowly
            elif kind == "snap":
                g = lo if k < 0.25 else 0.0
            elif kind == "sign":
                g = lo * (1.0 - k)
                g *= 1.0 if (math.sin(t * 41.0) + math.sin(t * 13.0)) > (-1.0 + 1.6 * k) else 0.05
            else:
                g = lo * (1.0 - k) ** 1.4
                if k > 0.35:
                    # stutters as the power dies
                    g *= 1.0 if (math.sin(t * 19.0) + math.sin(t * 7.1)) > (-1.3 + 1.4 * k) else 0.12
        self.gate = max(0.0, min(1.0, g))

    def play(self, path, start=0.0):
        self.stop()
        self.path = path
        self.start_at = start
        self.loading = True
        threading.Thread(target=self._load, args=(path, self.token), daemon=True).start()

    def _load(self, path, token):
        lower_thread_priority()
        try:
            data, sr = decode_audio(path)
            if len(data) == 0:
                raise ValueError("the file has no audio in it")
            env, p95 = compute_env(data)
        except Exception as exc:
            log_error("decode " + Path(path).name)
            if token == self.token:
                self.path = None
                self.loading = False
                self.error = str(exc) or exc.__class__.__name__
            return
        if token != self.token:
            return
        self.data = data
        self.sr = sr
        self.duration = len(data) / sr
        self.env, self.p95 = env, p95
        self.level = self._lvl = 0.0
        self.loading = False
        self._start(min(self.start_at, max(0.0, self.duration - 0.05)))
        trim_memory(collect=True)

    def _start(self, offset):
        data = self.data
        if data is None:
            return
        try:
            self.engine.play(data, int(offset * self.sr), self.sr)
        except Exception as exc:
            log_error("audio output")
            self.stop()
            self.error = f"audio output failed: {exc}"
            return
        self.offset = offset
        self.started = time.time()
        self.playing = True
        self.paused = False

    def pause(self):
        if not self.playing:
            return
        self.pause_pos = self.position
        self.engine.pause()
        self.playing = False
        self.paused = True

    def resume(self):
        if self.paused and self.data is not None:
            self._start(min(self.pause_pos, max(0.0, self.duration - 0.05)))

    @property
    def position(self):
        if self.paused:
            return self.pause_pos
        if not self.playing:
            return 0.0
        return min(self.duration, self.offset + time.time() - self.started)

    def seek(self, delta):
        self.glitch_t = time.time()
        if self.paused:
            self.pause_pos = max(0.0, min(self.duration, self.pause_pos + delta))
            return
        if not self.playing:
            return
        target = self.position + delta
        if target >= self.duration:
            if self.loop:
                self._start(0.0)
            else:
                self.ended_path = self.path
                self.stop()
        else:
            self._start(max(0.0, target))

    def update(self, light=False, hq=False):
        """light: nobody is looking, skip the loudness maths. hq: you are looking at the window, so the loudness
        is measured much more finely (every little change in the recitation shows) and follows the clock."""
        now = time.time()
        dt = min(0.25, max(0.004, now - self._lvl_t)) if self._lvl_t else 0.04
        self._lvl_t = now
        self.engine.tick(now)
        if self.playing:
            pos = self.position
            if pos >= self.duration:
                if self.loop and self.data is not None:
                    self._start(0.0)
                else:
                    self.ended_path = self.path
                    self.stop(drain=True)  # it ended by itself: let the last bit of sound play out
                return
            data = self.data
            if data is None:
                self.playing = False
                return
            if light:
                # nobody is looking: only the end-of-track check above, none of the
                # loudness maths that feeds the animation
                self._lvl = 0.0
                self.level = 0.0
                return
            if hq:
                self._follow_hq(data, pos, dt)
                return
            a = int(pos * self.sr)
            seg = data[a:a + int(0.08 * self.sr), 0]
            inst = 0.0
            if len(seg):
                rms = float(np.sqrt(np.mean(np.square(seg.astype(np.float32)))))
                inst = min(1.0, rms / (1.5 * self.p95))
            self._lvl = max(inst, self._lvl * 0.82)
            self.level = round(self._lvl * LEVEL_STEPS) / LEVEL_STEPS
        else:
            if hq:
                self._lvl *= math.exp(-dt * 10.0)
                self.level = round(self._lvl * LEVEL_STEPS_HQ) / LEVEL_STEPS_HQ
            else:
                self._lvl *= 0.8
                self.level = round(self._lvl * LEVEL_STEPS) / LEVEL_STEPS

    def _follow_hq(self, data, pos, dt):
        """The fine loudness: it looks at EVERYTHING played since the last frame (so a short syllable between two frames
        is never missed), takes the loudest 10 ms of it as well as the average, lifts the quiet detail a little (the
        ear hears loudness in a curve, not a straight line), then rises fast and falls softly, by the clock."""
        sr = self.sr
        a = max(0, int((pos - dt) * sr))
        b = min(len(data), int((pos + 0.02) * sr))
        if b - a > int(0.12 * sr):
            a = b - int(0.12 * sr)
        if b - a < int(0.03 * sr):
            a = max(0, b - int(0.03 * sr))
        seg = data[a:b, 0].astype(np.float32)
        inst = 0.0
        if len(seg):
            sq = np.square(seg)
            whole = float(np.sqrt(np.mean(sq)))
            k = max(1, int(0.01 * sr))
            n = (len(sq) // k) * k
            burst = float(np.sqrt(np.max(np.mean(sq[:n].reshape(-1, k), axis=1)))) if n else whole
            inst = min(1.0, (0.5 * whole + 0.5 * burst) / (1.5 * self.p95))
            inst = inst ** 0.8
            if inst < 0.012:
                inst = 0.0
        lvl = self._lvl
        rate = 45.0 if inst > lvl else 6.0
        lvl += (inst - lvl) * (1.0 - math.exp(-dt * rate))
        self._lvl = lvl
        self.level = round(lvl * LEVEL_STEPS_HQ) / LEVEL_STEPS_HQ

    def stop(self, drain=False):
        self.token += 1
        if self.playing or self.loading:
            self.engine.release(drain)
        self.playing = False
        self.paused = False
        self.loading = False
        self.path = None
        self.data = None
        self.env = None


def panel_lines(ui, player, w):
    count = len(ui.entries)
    lines = [
        "\x1b[1m" + fg(jade(0.95)) + "RECORDINGS" + RESET + fg(GOLD_DIM) + f"  {count}" + RESET,
        fg(DIM) + "─" * w + RESET,
    ]
    body = []
    if not ui.entries:
        body.append(fg(DIM) + "no recordings yet" + RESET)
    for idx in range(ui.scroll, min(count, ui.scroll + VISIBLE)):
        path, name, dur, size = ui.entries[idx]
        sel = idx == ui.sel
        active = player.path == path and (player.playing or player.loading or player.paused)
        marker = "»" if active else (">" if sel else " ")
        right = f"{fmt_dur(dur)} {fmt_size(size):>6}"
        name_w = max(4, w - 2 - len(right) - 1)
        n_marks = len(get_marks(path))
        tag = f"♦{n_marks}" if n_marks else ""
        tag_w = len(tag) + 1 if tag else 0
        label = fit(name, name_w - tag_w).ljust(name_w - tag_w)
        tag_txt = (" " + fg(GOLD) + tag + RESET) if tag else ""
        if sel:
            body.append(
                fg(GOLD) + marker + " " + RESET
                + "\x1b[1m" + fg(jade(0.95)) + label + RESET + tag_txt
                + " " + fg(GOLD_DIM) + right + RESET
            )
        else:
            body.append(
                fg(GOLD) + marker + " " + RESET
                + fg(jade(0.55)) + label + RESET + tag_txt
                + " " + fg(DIM) + right + RESET
            )
    body += [""] * (VISIBLE - len(body))
    lines += body
    lines.append(fg(DIM) + "─" * w + RESET)
    if player.loading:
        lines.append(fg(GOLD_DIM) + "loading..." + RESET)
    elif player.playing or player.paused:
        barw = max(6, w - 14)
        filled = int(barw * player.position / player.duration) if player.duration else 0
        marks = get_marks(player.path) if player.path is not None else []
        ticks = {min(barw - 1, int(m / player.duration * barw)) for m in marks} if player.duration else set()
        cells = []
        for i in range(barw):
            if i in ticks:
                cells.append(fg(GOLD) + "♦")
            elif i < filled:
                cells.append(fg(jade(0.8)) + "═")
            else:
                cells.append(fg(DIM) + "─")
        lines.append(
            fg(GOLD) + ("║ " if player.paused else "» ") + RESET + fg(GOLD_DIM) + fmt_dur(player.position) + RESET + " "
            + "".join(cells) + RESET + " "
            + fg(GOLD_DIM) + fmt_dur(player.duration) + RESET
        )
    else:
        lines.append("")
    return lines


def clamp_marks(ui, n):
    ui.msel = max(0, min(ui.msel, n - 1)) if n else 0
    ui.mscroll = max(0, min(ui.mscroll, n - VISIBLE))
    if ui.msel < ui.mscroll:
        ui.mscroll = ui.msel
    elif ui.msel >= ui.mscroll + VISIBLE:
        ui.mscroll = ui.msel - VISIBLE + 1


def panel_marks(ui, player, w):
    path = ui.entries[ui.sel][0] if ui.entries and ui.sel < len(ui.entries) else None
    marks = get_marks(path) if path is not None else []
    n = len(marks)
    clamp_marks(ui, n)
    lines = [
        "\x1b[1m" + fg(jade(0.95)) + "MARKS" + RESET + ((fg(GOLD_DIM) + f"  {n}" + RESET) if n else ""),
        fg(DIM) + "─" * w + RESET,
    ]
    cur = -1
    if path is not None and player.path == path and (player.playing or player.paused):
        pos = player.position
        for k, m in enumerate(marks):
            if m <= pos + 0.05:
                cur = k
    body = []
    if path is not None and not marks:
        body.append(fg(DIM) + "no marks" + RESET)
    for j in range(ui.mscroll, min(n, ui.mscroll + VISIBLE)):
        text = f"♦{j + 1:>2}  {fmt_dur(marks[j])}"
        if ui.mfocus and j == ui.msel:
            body.append(fg(GOLD) + "> " + RESET + "\x1b[1m" + fg(jade(0.95)) + text + RESET)
        elif j == cur:
            body.append(fg(GOLD) + "» " + text + RESET)
        else:
            body.append("  " + fg(jade(0.55)) + text + RESET)
    body += [""] * (VISIBLE - len(body))
    lines += body
    lines.append(fg(DIM) + "─" * w + RESET)
    if ui.mfocus:
        lines.append(fg(GOLD_DIM) + "ENTER play" + RESET)
    elif n:
        lines.append(fg(GOLD_DIM) + "→ choose" + RESET)
    else:
        lines.append("")
    return lines


_ECH = {"key": None, "cells": {}}


def panel_lines_mimic(ui, player, w, quran=None):
    t = theme()
    b = border()
    inn = b.get("inner")
    side = 2 if inn else 1
    cw = w - 2 * side - 2
    H = len(MIC)
    vis = mimic_vis()
    count = len(ui.entries)
    lvl = player.level if player.playing else 0.0
    glow = 0.62 + 0.3 * (round(lvl * 8) / 8)
    loop = STYLE["loop"]
    auto = STYLE["q_auto"] if quran is not None else STYLE["autoplay"]

    def gc(u):
        return lerp(t["accent"], t["grad"], max(0.0, min(1.0, u)))

    def uu(x, y):
        return 0.5 * x / max(1, w - 1) + 0.5 * y / max(1, H - 1)

    gate = player.gate

    # The frame cells only change when the glow, gate or flash change. Those are
    # rounded to steps far too small to see (under 1/255 of a colour channel), and every
    # cell is remembered, so a steady frame costs almost nothing and its lines come out
    # identical to the last frame's (and are then not rewritten to the console at all).
    gate_q = round(gate, 3)
    flash_q = round(player.flash, 3)
    ckey = (id(t), id(b), w, H, round(glow, 3), gate_q, flash_q)
    if _ECH["key"] != ckey:
        _ECH["key"] = ckey
        _ECH["cells"] = {}
    cells = _ECH["cells"]

    def ech(ch, x, y):
        k = (ch, x, y)
        hit = cells.get(k)
        if hit is not None:
            return hit
        col = lerp(t["deep"], gc(uu(x, y)), min(1.0, glow))
        # frame powers up from the bottom with the mic, and drains with it
        rg = max(0.0, min(1.0, (gate_q - 0.5 * (1.0 - y / max(1, H - 1))) / 0.5))
        col = lerp(lerp(DIM, col, rg), t["head"], 0.7 * flash_q * rg)
        hit = cells[k] = fg(col) + ch + RESET
        return hit

    def pick(pat, i):
        return pat[i % len(pat)]

    def hrow(y, spec, l, pat, r, inset, center=None):
        rw = w - 2 * inset
        n = rw - 2
        out = []
        if inset:
            out.append(ech(pick(b["vl"], y), 0, y))
        out.append(ech(spec[l], inset, y))
        cs, cl = -1, 0
        if center:
            cl = min(center[0], n)
            cs = (n - cl) // 2
        for i in range(n):
            if cs <= i < cs + cl:
                if i == cs:
                    out.append(center[1](inset + 1 + cs))
                continue
            out.append(ech(pick(spec[pat], i), inset + 1 + i, y))
        out.append(ech(spec[r], inset + rw - 1, y))
        if inset:
            out.append(ech(pick(b["vr"], y), w - 1, y))
        return "".join(out)

    def boxed(content, plain, y):
        left = ech(pick(b["vl"], y), 0, y)
        right = ech(pick(b["vr"], y), w - 1, y)
        if inn:
            left += ech(pick(inn["vl"], y), 1, y)
            right = ech(pick(inn["vr"], y), w - 2, y) + right
        return left + " " + " " * max(0, cw - plain) + content + " " + right

    def label_center(y, text, width):
        return (width, lambda x0: gtext(text, t, uu(x0, y), uu(x0 + len(text), y), 1.0, True))

    lines = []
    title = quran["title"] if quran else f" » MIMIC  {count} « "
    lines.append(hrow(0, b, "tl", "ht", "tr", 0, label_center(0, title, len(title))))
    if inn:
        lines.append(hrow(1, inn, "tl", "ht", "tr", 1))

    body = []

    def row(name, right, sel, active, cap):
        marker = "«" if active else ("<" if sel else " ")
        name_w = min(cap, max(4, cw - len(right) - 3))
        label = lpad(fit(name, name_w), name_w)
        y = len(lines) + len(body)
        u0, u1 = uu(side + 1, y), uu(w - side - 2, y)
        colored = (
            gtext(label, t, u0, u1, 1.0 if sel else 0.6, sel)
            + " " + fg(lerp(DIM, gc(u1), 0.65)) + right + RESET
            + " " + fg(gc(u1)) + marker + RESET
        )
        return colored, name_w + len(right) + 3

    if quran is not None:
        for it in quran["items"]:
            y = len(lines) + len(body)
            if it["kind"] == "item":
                body.append(row(it["label"], it["right"], it["sel"], it["active"], cw))
            else:
                text = fit(it["label"], cw - 1)
                if it["kind"] == "input":
                    body.append((gtext(text, t, uu(side + 1, y), uu(w - side - 2, y), 1.0, True), len(text)))
                else:
                    body.append((fg(DIM) + text + RESET, len(text)))
    else:
        if not ui.entries:
            for text in ("no models yet", "press I to import", "or drop files in the", "mimic folder"):
                body.append((fg(DIM) + text + RESET, len(text)))
        for idx in range(ui.scroll, min(count, ui.scroll + vis)):
            path, name, dur, size = ui.entries[idx]
            active = player.path == path and (player.playing or player.loading or player.paused)
            body.append(row(name, fmt_len(dur), idx == ui.sel, active, NAME_CAP))
    body += [("", 0)] * (vis - len(body))
    for colored, plain in body:
        lines.append(boxed(colored, plain, len(lines)))

    orn = b["orn"]
    y = len(lines)
    lines.append(hrow(y, inn or b, "ml", "mh", "mr", 1 if inn else 0, label_center(y, orn, b.get("ow", len(orn)))))

    status = quran["status"] if quran else ""
    if status:
        y = len(lines)
        lines.append(boxed(gtext(status, t, uu(side + 1, y), uu(side + 1 + len(status), y), 0.8), len(status), y))
        lines.append(boxed("", 0, y + 1))
    elif player.loading:
        y = len(lines)
        lines.append(boxed(gtext("loading...", t, uu(side + 1, y), uu(side + 11, y), 0.7), 10, y))
        lines.append(boxed("", 0, y + 1))
    else:
        wt, wb = wave_lines(player, cw)
        lines.append(boxed(wt, cw, len(lines)))
        lines.append(boxed(wb, cw, len(lines)))

    y = len(lines)
    if player.playing or player.paused:
        total = fmt_dur(player.duration)
        pos = fmt_dur(player.position)
        barw = max(4, cw - len(total) - len(pos) - 4)
        filled = int(barw * player.position / player.duration) if player.duration else 0
        ua, ub = uu(side + 1 + len(total) + 1, y), uu(w - side - 2 - len(pos) - 3, y)
        ue = ua + (ub - ua) * (max(1, filled) - 1) / max(1, barw - 1)
        end = "∞" if loop else ("»" if auto else "«")
        colored = (
            fg(lerp(DIM, gc(ua), 0.6)) + total + RESET + " "
            + (gtext("═" * filled, t, ua, ue) if filled else "")
            + fg(DIM) + "─" * (barw - filled) + RESET + " "
            + fg(lerp(DIM, gc(ub), 0.6)) + pos + RESET + " " + fg(gc(ub)) + end + RESET
        )
        lines.append(boxed(colored, len(total) + len(pos) + barw + 4, y))
    else:
        lines.append(boxed("", 0, y))

    if inn:
        lines.append(hrow(len(lines), inn, "bl", "hb", "br", 1))
    y = len(lines)
    badge = None
    text = " ∞ LOOP " if loop else (" » AUTO " if auto else None)
    if text:
        pulse = round((math.sin(time.time() * 4.0) + 1) / 2 * 6) / 6
        badge = (len(text), lambda x0: gtext(text, t, uu(x0, y), uu(x0 + len(text), y), 0.7 + 0.3 * pulse, True))
    lines.append(hrow(y, b, "bl", "hb", "br", 0, badge))
    return lines


# ---- Quran (press U in the mimic panel) ----
# Qaris and surahs come from mp3quran.net, Hafs only. A surah is downloaded once into quran/audio
# and played from there, so the wave and mic glow work exactly like they do for a mimic model.
QURAN_DIR = BASE_DIR / "quran"
QURAN_AUDIO = QURAN_DIR / "audio"
QURAN_STATE = QURAN_DIR / "state.json"
QURAN_CACHE_MB = 1024   # saved surahs: once they add up to more than this, the one played longest ago goes first
QURAN_LIST_DAYS = 7     # the saved qari list is refreshed in the background when it is older than this
QURAN_LANG = "en"       # the qari list is saved per language (quran/reciters_<lang>.json); the terminal shows English
QURAN_API_LANG = {"en": "eng", "ar": "ar"}
QURAN_UA = {"User-Agent": "Mozilla/5.0 (Tikrar)", "Accept": "application/json"}
QURAN_MIN_BYTES = 20000

SUR_EN = ["Al-Fatihah", "Al-Baqarah", "Ali 'Imran", "An-Nisa", "Al-Ma'idah", "Al-An'am", "Al-A'raf", "Al-Anfal", "At-Tawbah", "Yunus", "Hud", "Yusuf", "Ar-Ra'd", "Ibrahim", "Al-Hijr", "An-Nahl", "Al-Isra", "Al-Kahf", "Maryam", "Ta-Ha", "Al-Anbiya", "Al-Hajj", "Al-Mu'minun", "An-Nur", "Al-Furqan", "Ash-Shu'ara", "An-Naml", "Al-Qasas", "Al-'Ankabut", "Ar-Rum", "Luqman", "As-Sajdah", "Al-Ahzab", "Saba", "Fatir", "Ya-Sin", "As-Saffat", "Sad", "Az-Zumar", "Ghafir", "Fussilat", "Ash-Shura", "Az-Zukhruf", "Ad-Dukhan", "Al-Jathiyah", "Al-Ahqaf", "Muhammad", "Al-Fath", "Al-Hujurat", "Qaf", "Adh-Dhariyat", "At-Tur", "An-Najm", "Al-Qamar", "Ar-Rahman", "Al-Waqi'ah", "Al-Hadid", "Al-Mujadila", "Al-Hashr", "Al-Mumtahanah", "As-Saff", "Al-Jumu'ah", "Al-Munafiqun", "At-Taghabun", "At-Talaq", "At-Tahrim", "Al-Mulk", "Al-Qalam", "Al-Haqqah", "Al-Ma'arij", "Nuh", "Al-Jinn", "Al-Muzzammil", "Al-Muddaththir", "Al-Qiyamah", "Al-Insan", "Al-Mursalat", "An-Naba", "An-Nazi'at", "'Abasa", "At-Takwir", "Al-Infitar", "Al-Mutaffifin", "Al-Inshiqaq", "Al-Buruj", "At-Tariq", "Al-A'la", "Al-Ghashiyah", "Al-Fajr", "Al-Balad", "Ash-Shams", "Al-Layl", "Ad-Duha", "Ash-Sharh", "At-Tin", "Al-'Alaq", "Al-Qadr", "Al-Bayyinah", "Az-Zalzalah", "Al-'Adiyat", "Al-Qari'ah", "At-Takathur", "Al-'Asr", "Al-Humazah", "Al-Fil", "Quraysh", "Al-Ma'un", "Al-Kawthar", "Al-Kafirun", "An-Nasr", "Al-Masad", "Al-Ikhlas", "Al-Falaq", "An-Nas"]
JUZ_AT = [[1, 1], [2, 142], [2, 253], [3, 93], [4, 24], [4, 148], [5, 82], [6, 111], [7, 88], [8, 41], [9, 93], [11, 6], [12, 53], [15, 1], [17, 1], [18, 75], [21, 1], [23, 1], [25, 21], [27, 56], [29, 46], [33, 31], [36, 28], [39, 32], [41, 47], [46, 1], [51, 31], [58, 1], [67, 1], [78, 1]]


def juz_surahs(j):
    a = JUZ_AT[j - 1]
    b = JUZ_AT[j] if j < 30 else None
    last = 114 if b is None else (b[0] - 1 if b[1] == 1 else b[0])
    return list(range(a[0], last + 1))


def surah_partial(j, n):
    a = JUZ_AT[j - 1]
    b = JUZ_AT[j] if j < 30 else None
    return (n == a[0] and a[1] > 1) or (b is not None and n == b[0] and b[1] > 1)


def surah_span(n):
    first = last = 1
    for j in range(1, 31):
        s0, a0 = JUZ_AT[j - 1]
        if s0 < n or (s0 == n and a0 <= 1):
            first = j
        if s0 <= n:
            last = j
    return first, last


def juz_of(n):
    for j in range(1, 31):
        if n in juz_surahs(j):
            return j
    return 30


def spill_label(j, n):
    first, last = surah_span(n)
    if first < j < last:
        return f"juz {first}-{last}"
    if last > j:
        return f"on in juz {j + 1}"
    return f"from juz {first}"


def qnorm(text):
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


# The API's moshaf objects carry no rewaya id, so Hafs is decided from the name and the server path.
NOT_HAFS_NAME = re.compile(
    r"warsh|qalon|qaloon|shu'?ba|shouba|bazzi|bizi|qunbul|qumbul|sousi|susi|doori|dorai|khalaf|khallad|"
    r"dhakwan|hisham|jammaz|ruways|rawh|yaqub|"
    r"ورش|قالون|شعبة|البزي|قنبل|السوسي|الدوري|خلف|ابن\s*ذكوان|هشام|ابن\s*جماز|رويس|روح|يعقوب", re.I)
NOT_HAFS_PATH = re.compile(r"warsh|qalon|qaloon|rewayat-(?!hafs)|riwayat-(?!hafs)|rewaya-(?!hafs)", re.I)
PIN_RES = (re.compile(r"wad[ie]+.*(yamani|yemeni|yamany|yemani)"), re.compile(r"nufais|nufays|nafis|nafees|nufis"))


def is_hafs_moshaf(m, nm, server):
    rid = m.get("rewaya_id")
    if rid is not None:
        try:
            return int(rid) == 1
        except (TypeError, ValueError):
            pass
    if NOT_HAFS_NAME.search(nm) or NOT_HAFS_PATH.search(server):
        return False
    if re.search(r"(^|\s)عن\s|A'n\s", nm, re.I) and not re.search(r"حفص|hafs", nm, re.I):
        return False  # "X from Y" naming some other riwaya
    return True


def norm_server(v):
    if not isinstance(v, str):
        return ""
    try:
        u = urlsplit(re.sub(r"^http://", "https://", v.strip(), flags=re.I))
        port = u.port
    except ValueError:
        return ""
    if u.scheme != "https" or u.username or u.password or port not in (None, 443):
        return ""
    host = (u.hostname or "").lower()
    if host != "mp3quran.net" and not host.endswith(".mp3quran.net"):
        return ""
    path = u.path or "/"
    return f"https://{host}{path}" + ("" if path.endswith("/") else "/")


def parse_set(m):
    raw = m.get("surah_list")
    if isinstance(raw, list):
        raw = ",".join(str(x) for x in raw)
    out = set()
    for part in (raw if isinstance(raw, str) else "").split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 114:
            out.add(int(part))
    if not out:
        try:
            if int(m.get("surahs_count") or m.get("surah_total") or 0) >= 114:
                out = set(range(1, 115))
        except (TypeError, ValueError):
            pass
    return out


def short_sub(nm, i):
    s = re.sub(r"Rewayat Hafs A'n Assem|Hafs A'n Assem|Rewayat|[-–]", " ", clean_name(nm), flags=re.I)
    s = " ".join(s.split())
    return s[:24] if s.strip("? ") else str(i + 1)


def pin_of(name):
    s = qnorm(name)
    for i, pat in enumerate(PIN_RES):
        if pat.search(s):
            return i
    return 99


def parse_reciters(text):
    try:
        data = json.loads(text)
    except ValueError:
        return None
    arr = data.get("reciters") if isinstance(data, dict) else None
    if not isinstance(arr, list):
        return None
    out = []
    for r in arr[:1500]:
        if not isinstance(r, dict) or not isinstance(r.get("name"), str) or not isinstance(r.get("moshaf"), list):
            continue
        ms = []
        for m in r["moshaf"][:12]:
            if not isinstance(m, dict):
                continue
            nm = m.get("name") if isinstance(m.get("name"), str) else ""
            server = norm_server(m.get("server"))
            has = parse_set(m)
            if server and has and is_hafs_moshaf(m, nm, server):
                ms.append((nm, server, has, m.get("id")))
        if not ms:
            continue
        rid = str(r.get("id"))
        name = clean_name(r["name"])
        if not name.strip("? "):
            name = f"qari {rid}"
        for i, (nm, server, has, mid) in enumerate(ms):
            key = re.sub(r"[^\w-]", "", f"{rid}_{mid if mid is not None else i}")[:40]
            if not key:
                continue
            sub = short_sub(nm, i) if len(ms) > 1 else ""
            out.append(dict(key=key, name=name, sub=sub, server=server, has=has,
                            pin=pin_of(name), nk=qnorm(name + " " + sub)))
    out.sort(key=lambda r: (r["pin"], r["name"].casefold()))
    return out


class _Cancelled(Exception):
    pass


class QuranState:
    def __init__(self):
        self.view = "juz"        # juz -> surah -> pick (the qari picker)
        self.juz = 30
        self.sel = 29
        self.scroll = 0
        self.pick_n = 0
        self.search = ""
        self.list = []
        self.by_key = {}
        self.list_state = "idle"  # idle | loading | ready | fail
        self.list_err = ""
        self.qari = ""
        self.recent = []
        self.cur = None           # (qari key, surah) that was last asked for
        self.dl = None            # download in progress: dict(key, n, got, total, err)
        self.dl_token = 0
        self.ready = None         # (path, token) handed from the download thread to the main loop
        self.msg = ""
        self.rows = []
        self.rows_sig = None
        self.loaded = False


QS = QuranState()


def quran_state_load():
    if QS.loaded:
        return
    QS.loaded = True
    try:
        data = json.loads(QURAN_STATE.read_text(encoding="utf-8"))
        key = data.get("qari")
        if isinstance(key, str) and re.fullmatch(r"[\w-]{1,40}", key):
            QS.qari = key
        recent = data.get("recent")
        if isinstance(recent, list):
            QS.recent = [k for k in recent if isinstance(k, str) and re.fullmatch(r"[\w-]{1,40}", k)][:5]
        if isinstance(data.get("juz"), int) and 1 <= data["juz"] <= 30:
            QS.juz = data["juz"]
    except Exception:
        pass
    QS.sel = QS.juz - 1
    try:
        for p in QURAN_AUDIO.glob("*.part"):
            p.unlink(missing_ok=True)  # leftovers from a download that was cut off
    except OSError:
        pass


def quran_state_save():
    try:
        QURAN_DIR.mkdir(parents=True, exist_ok=True)
        write_atomic(QURAN_STATE, json.dumps({"qari": QS.qari, "recent": QS.recent, "juz": QS.juz}).encode("utf-8"))
    except Exception:
        pass


def quran_api(lang):
    return "https://www.mp3quran.net/api/v3/reciters?language=" + QURAN_API_LANG.get(lang, "eng") + "&rewaya=1"


def quran_list_path(lang):
    return QURAN_DIR / f"reciters_{lang}.json"


def _list_fetch():
    path = quran_list_path(QURAN_LANG)
    api = quran_api(QURAN_LANG)
    why = "no answer"
    for url in (api, api.replace("://www.", "://")):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=QURAN_UA), timeout=25) as resp:
                text = resp.read().decode("utf-8")
            lst = parse_reciters(text)
            if not lst:
                why = "bad data"
                continue
            QURAN_DIR.mkdir(parents=True, exist_ok=True)
            write_atomic(path, text.encode("utf-8"))
            QS.by_key = {r["key"]: r for r in lst}
            QS.list = lst
            QS.list_state = "ready"
            return
        except Exception as exc:
            why = exc.__class__.__name__
    if QS.list:
        QS.msg = "offline: using the saved qari list"
    else:
        QS.list_state = "fail"
        QS.list_err = why


def quran_open_list(retry=False):
    """Show the saved list straight away, and refresh it in the background when it is old or missing."""
    if QS.list_state == "loading" or (QS.list_state == "ready" and not retry):
        return
    path = quran_list_path(QURAN_LANG)
    age = None
    if not QS.list:
        try:
            lst = parse_reciters(path.read_text(encoding="utf-8"))
            if lst:
                QS.by_key = {r["key"]: r for r in lst}
                QS.list = lst
                QS.list_state = "ready"
        except Exception:
            pass
    try:
        age = (time.time() - path.stat().st_mtime) / 86400
    except OSError:
        pass
    if QS.list and age is not None and age <= QURAN_LIST_DAYS and not retry:
        return
    if not QS.list:
        QS.list_state = "loading"
    threading.Thread(target=_list_fetch, daemon=True).start()


def quran_cache_path(key, n):
    return QURAN_AUDIO / f"{key}_{n:03d}.mp3"


def quran_cache_files():
    try:
        out = []
        for p in QURAN_AUDIO.glob("*.mp3"):
            st = p.stat()
            out.append((st.st_mtime, st.st_size, p))
        return out
    except OSError:
        return []


def quran_cache_bytes():
    return sum(f[1] for f in quran_cache_files())


def quran_cache_prune(keep=None):
    files = quran_cache_files()
    total = sum(f[1] for f in files)
    cap = QURAN_CACHE_MB * 1048576
    for _, size, p in sorted(files, key=lambda f: f[0]):
        if total <= cap:
            break
        if keep is not None and p == keep:
            continue
        try:
            p.unlink()
            total -= size
        except OSError:
            pass


def quran_cache_clear(player):
    freed = 0
    for _, size, p in quran_cache_files():
        if player.path == p and (player.playing or player.loading or player.paused):
            continue
        try:
            p.unlink()
            freed += size
        except OSError:
            pass
    return freed


def quran_cancel():
    QS.dl_token += 1
    QS.dl = None
    QS.ready = None


def quran_download(key, n, url, dest, tok, dl):
    part = dest.with_name(dest.name + ".part")
    try:
        QURAN_AUDIO.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": QURAN_UA["User-Agent"]}), timeout=25) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            dl["total"] = total
            with open(part, "wb") as f:
                while True:
                    if tok != QS.dl_token:
                        raise _Cancelled()
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    if dl["got"] == 0 and chunk[:1] == b"<":
                        raise OSError("not an audio file")
                    f.write(chunk)
                    dl["got"] += len(chunk)
        if (total and dl["got"] < total) or dl["got"] < QURAN_MIN_BYTES:
            raise OSError("the download was cut short")
        os.replace(part, dest)
    except _Cancelled:
        part.unlink(missing_ok=True)
        return
    except Exception as exc:
        part.unlink(missing_ok=True)
        if tok == QS.dl_token:
            code = getattr(exc, "code", None)
            if code:
                dl["err"] = f"http {code}"
            elif isinstance(exc, (urllib.error.URLError, TimeoutError)):
                dl["err"] = "no connection to the server"
            else:
                dl["err"] = str(exc) or exc.__class__.__name__
        return
    quran_cache_prune(keep=dest)
    if tok == QS.dl_token:
        QS.ready = (dest, tok)


def quran_note(ui, text, secs=3.0):
    ui.note = text
    ui.note_until = time.time() + secs


def quran_play(ui, player, key, n):
    r = QS.by_key.get(key)
    if not r or n not in r["has"]:
        quran_note(ui, "that qari does not have this surah")
        return False
    if not DECODER:
        quran_note(ui, "playback needs: pip install miniaudio av", 4)
        return False
    quran_cancel()
    player.stop()
    QS.qari = key
    if key not in QS.recent:
        QS.recent = ([key] + QS.recent)[:5]  # a new qari goes on top, one already there keeps its place
    QS.cur = (key, n)
    quran_state_save()
    dest = quran_cache_path(key, n)
    try:
        if dest.stat().st_size >= QURAN_MIN_BYTES:
            os.utime(dest, None)  # played just now, so it is the last one to be cleaned out
            player.play(dest)
            return True
    except OSError:
        pass
    QS.dl_token += 1
    dl = QS.dl = {"key": key, "n": n, "got": 0, "total": 0, "err": ""}
    threading.Thread(target=quran_download, args=(key, n, r["server"] + f"{n:03d}.mp3", dest, QS.dl_token, dl), daemon=True).start()
    return True


def quran_focus(n):
    """Point the selection at surah n (surah view)."""
    if n not in juz_surahs(QS.juz):
        QS.juz = juz_of(n)
    for i, row in enumerate(quran_rows()):
        if row.get("n") == n:
            QS.sel = i
            return


def quran_step(ui, player, d):
    cur = QS.cur
    r = QS.by_key.get(cur[0]) if cur else None
    if not r:
        quran_note(ui, "pick a surah first")
        return
    n = cur[1] + d
    while 1 <= n <= 114 and n not in r["has"]:
        n += d
    if not 1 <= n <= 114:
        quran_note(ui, "no more surahs from this qari")
        return
    if quran_play(ui, player, r["key"], n) and QS.view == "surah":
        quran_focus(n)


def quran_rows():
    sig = (QS.view, QS.juz, QS.pick_n, QS.search, id(QS.list), QS.list_state, QS.qari, tuple(QS.recent))
    if QS.rows_sig == sig:
        return QS.rows
    rows = []
    if QS.view == "juz":
        for j in range(1, 31):
            ss = juz_surahs(j)
            a, b = SUR_EN[ss[0] - 1], SUR_EN[ss[-1] - 1]
            rows.append(dict(kind="item", label=a if ss[0] == ss[-1] else f"{a} - {b}", right=f"juz {j:>2}", juz=j))
    elif QS.view == "surah":
        r = QS.by_key.get(QS.qari)
        if r:
            rows.append(dict(kind="head", label="qari: " + r["name"] + (f" ({r['sub']})" if r["sub"] else "")))
        elif QS.list_state == "loading":
            rows.append(dict(kind="head", label="loading the qari list..."))
        else:
            rows.append(dict(kind="head", label="no qari yet, ENTER on a surah picks one"))
        for n in juz_surahs(QS.juz):
            label = SUR_EN[n - 1]
            if surah_partial(QS.juz, n):
                label += f" ({spill_label(QS.juz, n)})"
            rows.append(dict(kind="item", label=label, right=f"{n:>3}", n=n))
    else:
        n, f = QS.pick_n, qnorm(QS.search)

        def ok(r):
            return n in r["has"] and (not f or f in r["nk"])

        def item(r):
            return dict(kind="item", label=r["name"] + (f" ({r['sub']})" if r["sub"] else ""), right="", key=r["key"])

        seen, rec = set(), []
        for k in QS.recent:
            r = QS.by_key.get(k)
            if r and k not in seen and ok(r):
                rec.append(r)
                seen.add(k)
        rest = [r for r in QS.list if r["key"] not in seen and ok(r)]
        if not QS.list:
            if QS.list_state == "loading":
                rows.append(dict(kind="head", label="loading the qari list..."))
            else:
                rows.append(dict(kind="head", label="no qari list yet, it needs internet once"))
                rows.append(dict(kind="head", label="(" + (QS.list_err or "offline") + ")"))
        else:
            if rec:
                rows.append(dict(kind="head", label="recent"))
                rows += [item(r) for r in rec]
            if rec and rest:
                rows.append(dict(kind="head", label="all qaris"))
            rows += [item(r) for r in rest]
            if not rec and not rest:
                rows.append(dict(kind="head", label="no qari matches"))
    QS.rows, QS.rows_sig = rows, sig
    return rows


def quran_fix_sel(rows):
    if not rows:
        QS.sel = 0
        return
    QS.sel = max(0, min(QS.sel, len(rows) - 1))
    if rows[QS.sel]["kind"] == "item":
        return
    for i in list(range(QS.sel, len(rows))) + list(range(QS.sel, -1, -1)):
        if rows[i]["kind"] == "item":
            QS.sel = i
            return


def quran_move(d):
    rows = quran_rows()
    quran_fix_sel(rows)
    items = [i for i, r in enumerate(rows) if r["kind"] == "item"]
    if items and QS.sel in items:
        QS.sel = items[(items.index(QS.sel) + d) % len(items)]


def quran_enter_view(view, sel_n=None):
    QS.view = view
    QS.scroll = 0
    QS.sel = 0
    rows = quran_rows()
    if view == "surah" and sel_n is not None:
        quran_focus(sel_n)
    else:
        quran_fix_sel(rows)


def quran_open_picker(n):
    QS.pick_n = n
    QS.search = ""
    quran_enter_view("pick")
    if QS.list_state in ("idle", "fail"):
        quran_open_list(retry=True)


def quran_open(ui, player):
    player.stop()
    quran_state_load()
    quran_open_list()
    ui.saved = (ui.entries, ui.sel, ui.scroll)
    ui.which = "quran"
    ui.confirm = False
    ui.reset_marks()
    QS.view = "juz"
    QS.scroll = 0
    QS.sel = QS.juz - 1
    quran_rows()
    mb = quran_cache_bytes() / 1048576
    quran_note(ui, f"saved surahs: {mb:.0f} MB (keeps up to {QURAN_CACHE_MB} MB)", 4)


def quran_back(ui, player):
    if QS.view == "pick":
        quran_enter_view("surah", QS.pick_n)
    elif QS.view == "surah":
        quran_enter_view("juz")
        QS.sel = QS.juz - 1
    else:
        quran_cancel()
        player.stop()
        ui.entries, ui.sel, ui.scroll = ui.saved or ([], 0, 0)
        ui.which = "mimic"
        ui.confirm = False


def quran_enter(ui, player):
    rows = quran_rows()
    quran_fix_sel(rows)
    if not rows or rows[QS.sel]["kind"] != "item":
        return
    row = rows[QS.sel]
    if QS.view == "juz":
        QS.juz = row["juz"]
        quran_state_save()
        cur_n = QS.cur[1] if QS.cur and QS.cur[1] in juz_surahs(QS.juz) else None
        quran_enter_view("surah", cur_n)
        return
    if QS.view == "pick":
        n = QS.pick_n
        if quran_play(ui, player, row["key"], n):
            quran_enter_view("surah", n)
        return
    n = row["n"]
    r = QS.by_key.get(QS.qari)
    if QS.cur == (QS.qari, n) and (player.playing or player.paused or player.loading or QS.dl):
        path = quran_cache_path(QS.qari, n)
        if player.path == path and player.paused:
            player.resume()
        elif player.path == path and player.playing:
            player.pause()
        else:
            quran_cancel()
            player.stop()
            quran_note(ui, "stopped")
        return
    if not QS.list:
        quran_open_list(retry=QS.list_state == "fail")
        quran_note(ui, "loading the qari list..." if QS.list_state == "loading" else "no qari list yet, it needs internet once", 4)
        return
    if r and n in r["has"]:
        quran_play(ui, player, QS.qari, n)
        return
    if r:
        quran_note(ui, f"{r['name']} does not have this surah, pick another qari", 4)
    quran_open_picker(n)


def quran_key(ui, player, ch, special, now):
    """Keys for the Quran screen. True means the key was used here."""
    if special is not None:
        if special in ("H", "P"):
            quran_move(-1 if special == "H" else 1)
        elif special == "M":
            quran_back(ui, player)
        elif special == "S" and QS.view == "juz":
            if ui.confirm:
                ui.confirm = False
                freed = quran_cache_clear(player)
                quran_note(ui, f"cleared {freed / 1048576:.0f} MB of saved surahs", 3)
            else:
                ui.confirm = True
        return True
    if QS.view == "pick":
        if ch == "\r":
            quran_enter(ui, player)
        elif ch == "\x08":
            if QS.search:
                QS.search = QS.search[:-1]
                QS.sel = 0
            else:
                quran_back(ui, player)
        elif ch == "\x1b":
            if QS.search:
                QS.search = ""
                QS.sel = 0
            else:
                quran_back(ui, player)
        elif ch == "\x03":
            return False
        elif ch.isprintable() and len(QS.search) < 28:
            QS.search += ch
            QS.sel = 0
            QS.scroll = 0
        return True
    if ch == "\r":
        quran_enter(ui, player)
    elif ch == "\x08":
        quran_back(ui, player)
    elif ch in ("n", "N"):
        quran_step(ui, player, 1)
    elif ch in ("p", "P"):
        quran_step(ui, player, -1)
    elif ch in ("r", "R") and QS.view == "surah":
        rows = quran_rows()
        quran_fix_sel(rows)
        if rows and rows[QS.sel].get("n"):
            quran_open_picker(rows[QS.sel]["n"])
    elif ch in ("l", "L"):
        STYLE["loop"] = not STYLE["loop"]
        extra = ""
        if STYLE["loop"] and STYLE["q_auto"]:
            STYLE["q_auto"] = False
            extra = "   (autoplay off)"
        save_style()
        quran_note(ui, "loop: " + ("on" if STYLE["loop"] else "off") + extra, 1.5)
    elif ch in ("a", "A"):
        STYLE["q_auto"] = not STYLE["q_auto"]
        extra = ""
        if STYLE["q_auto"] and STYLE["loop"]:
            STYLE["loop"] = False
            extra = "   (loop off)"
        save_style()
        quran_note(ui, "autoplay next surah: " + ("on" if STYLE["q_auto"] else "off") + extra, 1.5)
    elif ch in ("c", "C"):
        STYLE["theme"] = (STYLE["theme"] + 1) % len(THEMES)
        save_style()
        quran_note(ui, f"color: {THEMES[STYLE['theme']][0]}", 1.5)
    else:
        return False
    return True


def quran_poll(ui, player, now):
    """Runs every frame: messages and finished downloads from the worker threads."""
    if QS.msg:
        ui.note, ui.note_until, QS.msg = QS.msg, now + 4, ""
    if ui.which != "quran":
        if QS.dl or QS.ready:
            quran_cancel()
        return
    if QS.ready is not None:
        path, tok = QS.ready
        QS.ready = None
        if tok == QS.dl_token:
            QS.dl = None
            player.play(path)
    dl = QS.dl
    if dl and dl["err"]:
        QS.dl = None
        ui.note = ("download failed: " + dl["err"])[:60]
        ui.note_until = now + 6


def quran_hint():
    if QS.view == "pick":
        return "type to search  ↑↓ choose  ENTER play  BACKSPACE erase/back  ESC clear"
    if QS.view == "surah":
        return "↑↓ select  ENTER play  N next  P prev  R qari  A auto  L loop  [ ] 30s  → back"
    return "↑↓ select  ENTER open  C color  DEL clear saved surahs  → back to mimic"


def quran_panel(ui, player, pw):
    rows = quran_rows()
    quran_fix_sel(rows)
    vis = mimic_vis() - (1 if QS.view == "pick" else 0)
    if QS.sel < QS.scroll:
        QS.scroll = QS.sel
    elif QS.sel >= QS.scroll + vis:
        QS.scroll = QS.sel - vis + 1
    QS.scroll = max(0, min(QS.scroll, max(0, len(rows) - vis)))
    if rows and QS.sel == next((i for i, r in enumerate(rows) if r["kind"] == "item"), -1):
        QS.scroll = 0
    busy = player.playing or player.loading or player.paused or QS.dl is not None
    items = []
    if QS.view == "pick":
        items.append(dict(kind="input", label="find: " + QS.search + "_"))
    for i in range(QS.scroll, min(len(rows), QS.scroll + vis)):
        r = rows[i]
        if r["kind"] != "item":
            items.append(dict(kind="head", label=r["label"]))
            continue
        if QS.view == "pick":
            active = r["key"] == QS.qari
        elif not (busy and QS.cur):
            active = False
        elif QS.view == "juz":
            active = juz_of(QS.cur[1]) == r["juz"]
        else:
            active = QS.cur[1] == r["n"]
        items.append(dict(kind="item", label=r["label"], right=r["right"], sel=(i == QS.sel), active=active))
    if QS.view == "juz":
        title = " » QURAN « "
    elif QS.view == "surah":
        title = f" » JUZ {QS.juz} « "
    else:
        title = f" » QARI · {SUR_EN[QS.pick_n - 1]} « "
    status = ""
    dl = QS.dl
    if dl and not dl["err"]:
        if dl["total"]:
            status = f"downloading {int(100 * dl['got'] / dl['total'])}%  {fmt_size(dl['got'])} of {fmt_size(dl['total'])}"
        else:
            status = f"downloading {fmt_size(dl['got'])}"
    return dict(title=title, items=items, status=status[: max(1, pw - 4)])



_BLANK = (False, None, None)
_BLANK_CELL = (_BLANK, " ")
_GAP = 4   # equal cells that are cheaper to rewrite than to jump over (a cursor move is ~8 bytes)


_CODE = {}


def parse_cells(line):
    """A colour-coded line as one (style, char) per screen cell. Every character the app draws is
    one cell wide (clean_name sees to that). A space only shows its background, so its other
    attributes are dropped: two spaces that differ only in text colour count as equal."""
    cells = []
    bold, f, b = False, None, None
    parts = _SGR.split(line)  # text, params, text, params, ... text
    for idx in range(0, len(parts), 2):
        text = parts[idx]
        if text:
            st, sb = (bold, f, b), (False, None, b)
            cells.extend([(sb, ch) if ch == " " else (st, ch) for ch in text])
        if idx + 1 < len(parts):
            params = parts[idx + 1]
            if params == "0" or params == "":
                bold, f, b = False, None, None
            elif params == "1":
                bold = True
            elif params.startswith("38;2;"):
                f = _CODE.get(params) or _CODE.setdefault(params, "\x1b[" + params + "m")
            elif params.startswith("48;2;"):
                b = _CODE.get(params) or _CODE.setdefault(params, "\x1b[" + params + "m")
    return cells


def row_ops(row, old, new, cur):
    """The bytes that turn one screen row from `old` cells into `new` cells: only the cells that
    changed are written (a pulse on the frame border costs a few bytes per row instead of the
    whole row). `cur` is the console's current colour state and is kept up to date."""
    ln = max(len(old), len(new))
    nold, nnew = len(old), len(new)
    out = []
    i = 0
    while i < ln:
        if (old[i] if i < nold else _BLANK_CELL) == (new[i] if i < nnew else _BLANK_CELL):
            i += 1
            continue
        last = j = i
        while j < ln and j - last <= _GAP:
            if (old[j] if j < nold else _BLANK_CELL) != (new[j] if j < nnew else _BLANK_CELL):
                last = j
            j += 1
        out.append(f"\x1b[{row};{i + 1}H")
        for k in range(i, last + 1):
            (sbold, sf, sb), ch = new[k] if k < nnew else _BLANK_CELL
            if ch == " ":
                if cur[2] != sb:
                    if sb is None:
                        out.append(RESET)
                        cur[0], cur[1], cur[2] = False, None, None
                    else:
                        out.append(sb)
                        cur[2] = sb
            else:
                if (cur[0] and not sbold) or (cur[1] and not sf) or (cur[2] and not sb):
                    out.append(RESET)
                    cur[0], cur[1], cur[2] = False, None, None
                if sb and sb != cur[2]:
                    out.append(sb)
                    cur[2] = sb
                if sbold and not cur[0]:
                    out.append("\x1b[1m")
                    cur[0] = True
                if sf and sf != cur[1]:
                    out.append(sf)
                    cur[1] = sf
            out.append(ch)
        i = last + 1
    return "".join(out)


SCREEN = {}


def reset_screen():
    SCREEN.clear()


def build_frame(state, elapsed, now, cols, rows, ui, player, level=0.0, nosig=False, syl=None, hq=False):
    styles_poll(now)
    n = len(MIC)
    live = state == "rec"
    mim = ui.which in ("mimic", "quran")
    model = player.playing and mim
    player.glow_update(now)
    g = player.gate if (mim and not live) else 0.0
    powered = g > 0.004
    gl = player.glitch(now) if (model or powered) else 0.0
    breath = 0.08 + 0.14 * (math.sin(now * 1.7) + 1) / 2
    held = player.paused and ui.which in ("mimic", "quran", "mine")
    outward = min(RINGS, int(level * 3.6 + 0.5)) if live else 0
    inward = int(now * 6) % RINGS
    pulse = (math.sin(now * 5.0) + 1) / 2

    side = []
    marks_col = []
    lead_w = 0
    pw = 0
    if ui.which == "mine":
        pw = min(PANEL_MAX, max(PANEL_MIN, cols - FULL_W - PANEL_GAP - MARK_GAP - MARK_W - 2))
        tw = FULL_W + PANEL_GAP + pw + MARK_GAP + MARK_W
    elif mim:
        pw = min(PANEL_MAX, max(PANEL_MIN, cols - FULL_W - PANEL_GAP - 2))
        lead_w = pw + PANEL_GAP
        tw = FULL_W + lead_w
    else:
        tw = FULL_W

    if cols < tw or rows < n + 4:
        msg = "window too small, make it bigger"[: max(1, cols - 1)]
        return f"\x1b[1;1H{fg(GOLD_DIM)}{msg}{RESET}\x1b[K"

    if ui.which == "mine":
        side = panel_lines(ui, player, pw)
        marks_col = panel_marks(ui, player, MARK_W)
    elif mim:
        side = panel_lines_mimic(ui, player, pw, quran_panel(ui, player, pw) if ui.which == "quran" else None)
    side = side + [""] * max(0, n - len(side))
    marks_col = marks_col + [""] * max(0, n - len(marks_col))

    if ui.confirm:
        hint = "DEL again to clear saved surahs   any other key cancels" if ui.which == "quran" else "DEL again to delete   any other key cancels"
    elif ui.note and now < ui.note_until:
        hint = ui.note
    elif ui.which == "mine" and ui.mfocus:
        hint = "↑↓ choose a mark   ENTER play from it   DEL remove mark   ← back to list"
    elif ui.which == "mine":
        hint = "↑↓ select  ENTER play/pause  F folder  → marks  , . seek  M mark  DEL delete  ← close"
    elif ui.which == "quran":
        hint = quran_hint()
    elif ui.which == "mimic":
        hint = "↑↓ ENTER play  , . seek  L loop  A auto  C color  I import  U quran  → close"
    elif state == "idle":
        hint = "SPACE record   ← mimic   → recordings   ESC quit"
    elif state == "rec":
        hint = "SPACE pause   Q stop and save"
    else:
        hint = "SPACE resume   Q stop and save   ← mimic   → recordings"

    if len(hint) > cols - 1:
        hint = hint[: max(1, cols - 1)]
    timer = fmt_time(player.position if (model or held or (player.playing and ui.which == "mine")) else elapsed)
    if live:
        timer_col = lerp(GOLD_DIM, GOLD, 0.6 + 0.4 * pulse)
    elif model:
        tm = theme()
        timer_col = lerp(tm["dim"], tm["accent"], 0.5 + 0.5 * player.level)
    else:
        timer_col = GOLD_DIM
    margin = center(tw, cols)
    lead = margin + " " * lead_w
    out = []

    bz = None
    ic = icon_index(mim)      # the mimic / Quran page has its own picture, apart from the main screen's
    if ic:
        glow = level if live else (min(1.0, player.level * g + 0.4 * player.flash) if (model or powered) else 0.0)
        glow = max(0.0, min(1.0, glow))
        if ic == 1:
            bz = bitzel_rows(int(round(glow * BZ_STEPS)))
            bz_w, bz_h = BZ_W, BZ_H
        else:
            voice = level if live else (player.level * g if (model or powered) else 0.0)
            if ICON_NAMES[ic] == "cassette" or ic >= len(BASE_ICONS):
                # scene styles (built-in cassette or rec_styles.py): picture that follows the voice
                if (model or held) and player.duration:
                    prog, secs = player.position / player.duration, player.position
                elif live:
                    prog, secs = min(1.0, elapsed / 900.0), elapsed
                else:
                    prog, secs = 0.0, 0.0
                bz = scene_rows(ICON_NAMES[ic], now, max(0.0, min(1.0, voice)), live or model, prog, secs, live, hq)
            else:
                key = reciter_key(now, voice, live, elapsed, slow_glow=ic == 3, syl=syl)
                # slow phase (24 steps, 4 s) for the Scholar's fading twinkles
                bz = mascot_rows(ic, key, int(now * 6) % 24 if ic == 3 else 0)
            bz_w, bz_h = RC_W, len(bz)
        bz_top = (n - bz_h) // 2
        bz_pad = (FULL_W - bz_w) // 2

    for i, row in enumerate(MIC):
        if bz is not None:
            if bz_top <= i < bz_top + bz_h:
                mic = " " * bz_pad + bz[i - bz_top] + " " * (FULL_W - bz_w - bz_pad)
            else:
                mic = " " * FULL_W
        else:
            grad = 1.0 - i / (n - 1)
            wave_pos = (math.sin(now * 4.5 - i * 0.45) + 1) / 2
            row_txt = None
            if live:
                body = jade(min(1.0, 0.22 + 0.30 * grad + 0.48 * level + 0.08 * wave_pos))
                accent = jade(0.5 + 0.4 * grad)
            elif model or powered:
                lv = player.level
                tm = theme()
                inten = min(1.0, 0.16 + 0.26 * grad + 0.46 * lv + 0.12 * wave_pos * lv + 0.5 * player.flash)
                # power sweeps up from the stem on start and drains back down on stop
                rg = max(0.0, min(1.0, (g - 0.5 * grad) / 0.5))
                # seek glitch: rows drop out or flash white for a moment
                step = int(now * 30)
                if gl > 0 and (step + i * 3) % 5 == 0:
                    rg *= 0.2
                hot = 0.85 * gl if (gl > 0 and (step + i * 5) % 7 == 0) else 0.0
                white = min(1.0, 0.75 * player.flash * rg + hot)
                row_txt = paint_mic(
                    row,
                    lambda j: lerp(
                        lerp(DIM, lerp(tm["deep"], lerp(tm["accent"], tm["grad"], 0.5 * j / (MIC_W - 1) + 0.5 * i / (n - 1)), inten), rg),
                        tm["head"], white,
                    ),
                )
                accent = lerp(DIM, lerp(tm["deep"], lerp(tm["accent"], tm["grad"], i / (n - 1)), 0.35 + 0.45 * grad), rg)
            elif mim and (ui.entries or ui.which == "quran") and not held and not player.loading:
                # standby: a faint slow breath so it doesn't look dead
                body = lerp(DIM, theme()["accent"], breath * (0.5 + 0.5 * grad))
                accent = DIM
            else:
                body = DIM
                accent = DIM
            left = [" "] * ARC_W
            right = [" "] * ARC_W
            if i in ARC_ROWS:
                if live and outward:
                    for k in range(outward):
                        col = fg(lerp(DIM, accent, 1.0 - 0.3 * k))
                        li, lch = arc_cell(i, k, True, True)
                        ri, rch = arc_cell(i, k, True, False)
                        left[li] = col + lch + RESET
                        right[ri] = col + rch + RESET
                elif powered and player.flash > 0.05:
                    for k in range(min(RINGS, int(player.flash * RINGS * 1.3 + 0.5))):
                        col = fg(lerp(DIM, theme()["head"], max(0.2, 1.0 - 0.3 * k)))
                        li, lch = arc_cell(i, k, True, True)
                        ri, rch = arc_cell(i, k, True, False)
                        left[li] = col + lch + RESET
                        right[ri] = col + rch + RESET
                elif model:
                    head = RINGS - 1 - inward
                    for k, fade in ((head, 1.0), (head + 1, 0.45)):
                        if k < RINGS:
                            col = fg(lerp(DIM, accent, fade * (0.4 + 0.6 * player.level) * g))
                            li, lch = arc_cell(i, k, False, True)
                            ri, rch = arc_cell(i, k, False, False)
                            left[li] = col + lch + RESET
                            right[ri] = col + rch + RESET
            mic = (
                "".join(left)
                + " " * GAP
                + (row_txt if row_txt is not None else paint_mic(row, lambda j: body))
                + " " * GAP
                + "".join(right)
            )
        if mim:
            line = margin + side[i] + " " * PANEL_GAP + mic
        elif ui.which == "mine":
            line = margin + mic + " " * PANEL_GAP + pad_to(side[i], pw) + " " * MARK_GAP + marks_col[i]
        else:
            line = margin + mic
        out.append(line)

    if state == "pause" or held:
        tm = theme()
        lo, hi = (tm["dim"], tm["accent"]) if (held and mim) else (GOLD_DIM, GOLD)
        blink = lerp(lo, hi, (math.sin(now * 3.0) + 1) / 2)
        out.append(lead + " " * ((FULL_W - 6) // 2) + fg(blink) + "PAUSED" + RESET)
    elif live and nosig:
        warn = lerp((120, 48, 48), (255, 96, 96), (math.sin(now * 6.0) + 1) / 2)
        out.append(lead + " " * ((FULL_W - 13) // 2) + "\x1b[1m" + fg(warn) + "NO MIC SIGNAL" + RESET)
    else:
        out.append("")
    if model or (held and mim):
        timer_txt = gtext(timer, theme(), 0.0, 1.0, (0.55 + 0.45 * player.level) if model else 0.4, True)
    elif held:
        timer_txt = "\x1b[1m" + fg(GOLD_DIM) + timer + RESET
    else:
        timer_txt = "\x1b[1m" + fg(timer_col) + timer + RESET
    out.append(lead + " " * max(0, (FULL_W - len(timer)) // 2) + timer_txt)
    out.append("")
    if mim and not ui.confirm:
        hint_txt = gtext(hint, theme(), 0.0, 1.0, 0.55)
    else:
        hint_txt = fg(lerp(DIM, GOLD_DIM, 0.7)) + hint + RESET
    out.append(center(len(hint), cols) + hint_txt)

    top = max(0, (rows - len(out)) // 2)
    parts = []
    cur = [False, None, None]  # the console's colour state: bold, fg code, bg code (default between frames)
    for idx, line in enumerate(out):
        row = top + idx + 1
        old = SCREEN.get(row)
        if old is not None and old[0] == line:
            continue  # identical line: nothing to parse, nothing to write
        cells = parse_cells(line)
        parts.append(row_ops(row, old[1] if old else (), cells, cur))
        SCREEN[row] = (line, cells)
    if cur[0] or cur[1] or cur[2]:
        parts.append(RESET)
    return "".join(parts)


def default_input_rate():
    """The rate the default microphone wants right now."""
    return int(round(float(sd.query_devices(kind="input")["default_samplerate"])))


def refresh_audio(engine=None):
    """PortAudio reads the device list once, when it starts, and keeps it. If the app sits idle
    while Windows changes something (a headset is plugged in or out, the default microphone is
    switched, the device's format is changed), that list goes stale and opening the microphone
    fails with 'Invalid sample rate'. Restarting PortAudio makes it read everything again. No
    stream may be open while this runs, so the playback stream is closed first."""
    try:
        if engine is not None:
            engine.close()
        sd._terminate()
        sd._initialize()
        return True
    except Exception:
        log_error("refreshing the audio devices")
        return False


LEVEL_TOP_DB = 18.0   # how many dB louder than your usual speaking voice fills the meter (bigger = wider range)
LEVEL_CURVE = 1.4     # 1.0 = straight; higher keeps the middle of the range calmer and saves the top for loud


class LevelMeter:
    """Turns the microphone's loudness into the 0..1 level the pictures follow.

    It works in decibels (that is how the ear hears loudness) and learns two things about YOUR room and voice:
      floor  the quiet hiss of the room (it follows the quietest moments, so the hiss shows as nothing)
      ref    how loud you usually are when you speak (it follows your louder syllables, slowly, and slowly forgets)
    Normal speech lands in the lower-middle of the scale, a raised voice goes well above that, and only
    really loud speech fills it (LEVEL_TOP_DB decides how much louder). Because it learns, it behaves the same with a quiet or a hot microphone."""

    def __init__(self):
        self.floor = -60.0
        self.ref = -26.0

    def level(self, prev, pk, rms, dt):
        dt = max(0.0, min(0.25, dt))
        if pk <= 0.0 and rms <= 0.0:                     # nothing arrived this frame: just let the picture settle
            return max(0.0, prev * math.exp(-dt * 5.0))
        db = max(-90.0, 20.0 * math.log10(max(rms, 3e-5)))
        if db < self.floor:
            self.floor += (db - self.floor) * (1.0 - math.exp(-dt * 8.0))
        else:
            self.floor += 0.3 * dt                       # the hiss may drift up very slowly
        self.floor = max(-80.0, min(self.floor, self.ref - 26.0, -35.0))
        if db > self.ref:
            self.ref += (db - self.ref) * (1.0 - math.exp(-dt * 0.10))     # slow: a louder moment must not move the scale
        else:
            self.ref -= 0.2 * dt
        self.ref = max(self.floor + 26.0, min(-6.0, self.ref))
        bottom = self.floor + 8.0
        top = self.ref + LEVEL_TOP_DB
        x = max(0.0, min(1.0, (db - bottom) / max(10.0, top - bottom)))
        target = x ** LEVEL_CURVE                        # more room between a normal voice and a loud one
        if target > prev:
            return prev + (target - prev) * (1.0 - math.exp(-dt * 40.0))   # fast up: a word shows at once
        return max(target, prev * math.exp(-dt * 5.0))                    # a little slower down: no flicker


METER = LevelMeter()


def follow_mic(prev, pk, rms, dt):
    return METER.level(prev, pk, rms, dt)


class PaceMeter:
    """Counts syllables (the small bursts of sound a reciter makes) so the picture can read at your speed.
    It watches the loudness in 10 ms steps, smooths it a little, and counts every rise-and-fall that is above
    the room's hiss. Two bursts closer than 80 ms count once. Slow steady noise (a fan, hiss) does not count."""

    def __init__(self):
        self.env = 0.0
        self.hi = 0.0
        self.lo = 0.0
        self.up = False
        self.since = 99

    def feed(self, hops, gate):
        n = 0
        env, hi, lo, up, since = self.env, self.hi, self.lo, self.up, self.since
        for h in hops:
            env += (h - env) * 0.4
            since += 1
            if up:
                if env > hi:
                    hi = env
                elif env < hi * 0.7:
                    up = False
                    lo = env
                    if hi > gate and since >= 8:
                        n += 1
                        since = 0
            else:
                if env < lo:
                    lo = env
                elif env > max(gate, lo * 1.4):
                    up = True
                    hi = env
        self.env, self.hi, self.lo, self.up, self.since = env, hi, lo, up, since
        return n


class Recorder:
    def __init__(self, rate):
        self.rate = rate          # the rate of this take: what gets saved
        self.dev_rate = rate      # the rate the microphone is actually running at
        self.chunks = []
        self.samples = 0
        self.active = False
        self.stream = None
        self.peak = 0.0
        self.last_signal = time.time()
        self._lk = threading.Lock()
        self._pk = 0           # loudest sample since the screen last asked (raw 16-bit)
        self._sq = 0.0         # sum of squares of every sample since then
        self._n = 0            # how many samples that is
        self._pace = PaceMeter()
        self._carry = np.zeros(0, dtype=np.float64)   # the part of a 10 ms step that has not filled up yet
        self._syl = 0          # syllables counted since the screen last asked

    def take_levels(self):
        """(peak, rms) of everything the microphone delivered since the last call, both 0..1. The screen runs
        a few times per second and a block of sound is much shorter, so looking only at the last block missed
        most of what you said; this keeps all of it."""
        with self._lk:
            pk, sq, n = self._pk, self._sq, self._n
            self._pk, self._sq, self._n = 0, 0.0, 0
        if n <= 0:
            return 0.0, 0.0
        return pk / 32768.0, math.sqrt(sq / n) / 32768.0

    def take_syllables(self):
        """How many syllables the microphone heard since the last call."""
        with self._lk:
            k, self._syl = self._syl, 0
        return k

    def _resample(self, data):
        """Only used when the microphone could not be reopened at the rate the take started with
        (a device change in the middle of a take): the take keeps one rate from start to end."""
        x = data[:, 0].astype(np.float32)
        n = max(1, int(round(len(x) * self.rate / self.dev_rate)))
        pos = np.linspace(0, len(x) - 1, n) if len(x) > 1 else np.zeros(n)
        out = np.interp(pos, np.arange(len(x)), x)
        return np.rint(out).astype(np.int16).reshape(-1, 1)

    def _callback(self, indata, frames, time_info, status):
        if not self.active:
            return
        data = indata
        if data.shape[1] > 1:
            data = data.mean(axis=1, keepdims=True).astype(np.int16)
        if not len(data):
            return
        pk = max(int(data.max()), -int(data.min()))
        self.peak = pk / 32768.0
        if pk > 8:
            self.last_signal = time.time()
        mono = data[:, 0].astype(np.float64)
        with self._lk:
            if pk > self._pk:
                self._pk = pk
            self._sq += float(np.dot(mono, mono))
            self._n += len(mono)
        try:                                              # syllable counting: never allowed to disturb the recording
            hop = max(1, self.dev_rate // 100)
            buf = np.concatenate((self._carry, mono))
            m = len(buf) // hop
            self._carry = buf[m * hop:]
            if m:
                hops = np.sqrt((buf[:m * hop].reshape(m, hop) ** 2).mean(axis=1)) / 32768.0
                k = self._pace.feed(hops.tolist(), 10.0 ** ((METER.floor + 10.0) / 20.0))
                if k:
                    with self._lk:
                        self._syl += k
        except Exception:
            pass
        data = self._resample(data) if self.dev_rate != self.rate else data.copy()
        self.chunks.append(data)
        self.samples += len(data)

    def _try(self, rate):
        last = None
        for channels in (1, 2):
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=rate,
                    channels=channels,
                    dtype="int16",
                    callback=self._callback,
                )
                stream.start()
                return stream, None
            except Exception as exc:
                last = exc
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
        return None, last

    def _open(self, reinit=None):
        # A take that has not started yet has no rate to keep, so it follows whatever the
        # microphone wants now (never a rate remembered from earlier, which may be stale).
        fresh = self.samples == 0
        last = None
        for attempt in range(2):
            if attempt == 1:
                # first round failed: restart PortAudio so it re-reads the devices, then try again
                if reinit is None or not reinit():
                    break
            rates = []
            if fresh:
                try:
                    rates.append(default_input_rate())
                except Exception as exc:
                    last = exc
            rates.append(self.rate)
            rates.extend(MP3_RATES)   # rates the MP3 encoder also takes, so saving stays an mp3
            tried = []
            for r in rates:
                if r in tried:
                    continue
                tried.append(r)
                stream, exc = self._try(r)
                if stream is not None:
                    if fresh:
                        self.rate = r
                    self.dev_rate = r
                    self.stream = stream
                    return
                last = exc or last
        raise last or RuntimeError("no microphone found")

    def start(self, reinit=None):
        if self.stream is not None:
            try:
                dead = not self.stream.active
            except Exception:
                dead = True
            if dead:
                self.close()
        if self.stream is None:
            self._open(reinit)
        self.last_signal = time.time()
        self.active = True

    def pause(self):
        self.active = False

    def close(self):
        self.active = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    @property
    def elapsed(self):
        return self.samples / self.rate

    def pcm(self):
        if not self.chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(self.chunks).reshape(-1)


def encode_mp3(pcm, rate, job):
    try:
        enc = lameenc.Encoder()
        enc.set_bit_rate(BITRATE)
        enc.set_in_sample_rate(rate)
        enc.set_out_sample_rate(rate if rate in MP3_RATES else 44100)
        enc.set_channels(1)
        enc.set_quality(2)
        parts = []
        step = rate * 2
        total = max(1, len(pcm))
        for i in range(0, len(pcm), step):
            parts.append(enc.encode(pcm[i:i + step].tobytes()))
            job["progress"] = min(1.0, (i + step) / total)
        parts.append(enc.flush())
        job["data"] = b"".join(parts)
    except Exception as exc:
        job["error"] = exc
    job["done"] = True


def unique_path(folder, stamp, ext):
    stem = f"{stamp} rec cli"
    path = folder / f"{stem}{ext}"
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}){ext}"
        n += 1
    return path


def write_atomic(path, data):
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_wav(path, pcm, rate):
    tmp = path.with_name(path.name + ".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)


def draw_bar(progress):
    cols, rows = shutil.get_terminal_size((80, 24))
    width = 32
    filled = int(width * progress)
    bar = (
        fg(JADE_HI)
        + "█" * filled
        + fg(DIM)
        + "░" * (width - filled)
        + RESET
        + " "
        + fg(GOLD)
        + f"{int(progress * 100):3d}%"
        + RESET
    )
    plain = width + 5
    row = max(1, rows // 2)
    if (ICON_NAMES[STYLE["icon"]] == "cassette" or len(BASE_ICONS) <= STYLE["icon"] < len(ICON_NAMES)) and cols >= RC_W + 2 and rows >= RC_H // 2 + 4:
        # scene styles: the picture is the progress bar (see scene_save_rows), with the percentage below it
        pic = scene_save_rows(ICON_NAMES[STYLE["icon"]], progress, time.time())
        top = max(1, (rows - len(pic) - 2) // 2)
        left = (cols - RC_W) // 2 + 1
        sys.stdout.write("".join(f"\x1b[{top + i};{left}H{line}" for i, line in enumerate(pic)))
        pct = f"{int(progress * 100):3d}%"
        sys.stdout.write(f"\x1b[{top + len(pic) + 1};1H{center(len(pct), cols)}{fg(GOLD)}{pct}{RESET}\x1b[K")
        sys.stdout.flush()
        return
    face = bow_face_rows(STYLE["icon"], progress) if _SAVE["variant"] == 1 else None
    if face is None:
        face = loading_face_rows(STYLE["icon"], time.time())
    if face is not None and cols >= RC_W // 2 + 2 and row - len(face) - 1 >= 1:
        # the little starry-eyed face above the bar: "wow, this recording is going to be cool"
        top = row - len(face) - 1
        left = (cols - RC_W // 2) // 2 + 1
        sys.stdout.write("".join(f"\x1b[{top + i};{left}H{line}" for i, line in enumerate(face)))
    sys.stdout.write(f"\x1b[{row};1H{center(plain, cols)}{bar}\x1b[K")
    sys.stdout.flush()


def save(rec, folder, stamp):
    pcm = rec.pcm()
    job = {"progress": 0.0, "done": False}
    worker = threading.Thread(target=encode_mp3, args=(pcm, rec.rate, job), daemon=True)
    worker.start()
    last = _SAVE["variant"]          # sometimes the starry eyes, sometimes the bow (mostly taking turns)
    _SAVE["variant"] = (1 - last) if random.random() < 0.65 else last
    shown = 0.0
    sys.stdout.write("\x1b[2J")
    while True:
        target = 1.0 if job["done"] else job["progress"]
        shown = min(shown + 0.04, target)
        draw_bar(shown)
        if job["done"] and shown >= 1.0:
            break
        time.sleep(1 / FPS)
    worker.join()

    folder.mkdir(parents=True, exist_ok=True)
    label = "saved rec"
    if "error" in job:
        write_wav(unique_path(folder, stamp, ".wav"), pcm, rec.rate)
        label = "saved rec (wav)"
    else:
        write_atomic(unique_path(folder, stamp, ".mp3"), job["data"])
    return label


def pick_files():
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        patterns = " ".join("*" + e for e in MIMIC_EXTS)
        chosen = filedialog.askopenfilenames(
            parent=root,
            title=APP_NAME + ": import models into mimic",
            filetypes=[("Audio files", patterns), ("All files", "*.*")],
        )
        root.destroy()
        return [Path(c) for c in chosen]
    except Exception:
        log_error("import dialog")
        return None


def unique_dest(folder, name):
    dest = folder / name
    stem, ext = dest.stem, dest.suffix
    n = 2
    while dest.exists():
        dest = folder / f"{stem} ({n}){ext}"
        n += 1
    return dest


def import_models(paths, mimic_dir):
    mimic_dir.mkdir(exist_ok=True)
    added, skipped, failed = [], 0, 0
    for src in paths:
        if src.suffix.lower() not in MIMIC_EXTS:
            skipped += 1
            continue
        try:
            if src.resolve().parent == mimic_dir.resolve():
                skipped += 1
                continue
            dest = unique_dest(mimic_dir, src.name)
            tmp = dest.with_name(dest.name + ".part")
            shutil.copy2(src, tmp)
            os.replace(tmp, dest)
            added.append(dest)
        except Exception:
            log_error("import " + src.name)
            failed += 1
    return added, skipped, failed


def show_label(text, park=False):
    cols, rows = shutil.get_terminal_size((80, 24))
    row = max(1, rows // 2)
    sys.stdout.write("\x1b[2J" + f"\x1b[{row};1H{center(len(text), cols)}{fg(JADE_HI)}{text}{RESET}")
    if park:
        sys.stdout.write(f"\x1b[{min(rows, row + 2)};1H")
    sys.stdout.flush()


def get_desktop_dir():
    try:
        import ctypes.wintypes
        buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Desktop"


def get_exe_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    candidates = [
        BASE_DIR / f"{APP_NAME}.exe",
        BASE_DIR / "rec.exe",
        BASE_DIR / "dist" / "rec" / "rec.exe",
        BASE_DIR / "dist" / f"{APP_NAME}.exe",
        BASE_DIR.parent / "exe" / APP_NAME / f"{APP_NAME}.exe",
        BASE_DIR.parent / "exe single" / f"{APP_NAME}.exe",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return Path(sys.executable).resolve()


def create_desktop_shortcut():
    try:
        desktop = get_desktop_dir()
        if not desktop.exists():
            return False
        shortcut_path = desktop / f"{APP_NAME}.lnk"
        exe_path = get_exe_path()
        working_dir = exe_path.parent
        icon_path = BASE_DIR / "rec.ico"
        if not icon_path.exists():
            icon_path = exe_path

        def ps_quote(p):
            return str(p).replace("'", "''")

        script = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{ps_quote(shortcut_path)}'); "
            f"$s.TargetPath = '{ps_quote(exe_path)}'; "
            f"$s.WorkingDirectory = '{ps_quote(working_dir)}'; "
            f"$s.IconLocation = '{ps_quote(icon_path)}'; "
            f"$s.Description = '{APP_NAME}'; "
            f"$s.Save()"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=0x08000000,
            capture_output=True,
            timeout=5,
        )
        return res.returncode == 0 and shortcut_path.exists()
    except Exception:
        log_error("create_desktop_shortcut")
        return False


def prompt_desktop_shortcut(ui=None):
    try:
        if STYLE.get("shortcut_prompted", False):
            return
        STYLE["shortcut_prompted"] = True
        save_style()

        desktop = get_desktop_dir()
        if (desktop / f"{APP_NAME}.lnk").exists():
            return

        res = ctypes.windll.user32.MessageBoxW(
            None,
            f"Would you like to create a desktop shortcut for {APP_NAME}?",
            APP_NAME,
            0x00000004 | 0x00000020 | 0x00010000,
        )
        if res == 6:  # IDYES
            if create_desktop_shortcut() and ui is not None:
                ui.note = "desktop shortcut created"
                ui.note_until = time.time() + 3.0
        while msvcrt.kbhit():
            msvcrt.getwch()
    except Exception:
        log_error("prompt_desktop_shortcut")


def main():
    _SPLASH.finish()
    _DECODER_LOADER.join()
    enable_vt()
    _styles_load()
    load_style()
    # Everything built so far lives for the whole run: keep the collector from re-scanning it, and
    # let it run far less often (a frame makes a lot of short-lived little objects).
    gc.collect()
    gc.freeze()
    gc.set_threshold(20000, 20, 20)
    try:
        info = sd.query_devices(kind="input")
        rate = int(info["default_samplerate"])
    except Exception:
        print("no microphone found")
        return 1

    folder = BASE_DIR / "recordings"
    mimic_dir = BASE_DIR / "mimic"
    ui = UI()
    player = Player()
    rec = Recorder(rate)
    out = sys.stdout
    out.write("\x1b[?25l\x1b[2J")
    out.flush()

    prompt_desktop_shortcut(ui)

    state = "idle"
    stamp = None
    layout = None
    quit_app = False
    crashed = False
    errors = 0
    rec_level = 0.0
    last_error = 0.0
    last_trim = time.time()
    win_state, win_checked, back_since = None, 0.0, 0.0
    frozen = False  # only while another window is in front of this one: nothing is drawn until you come back
    last_key = time.time()
    lv_time = time.time()
    build_avg = 0.0   # how long a frame takes to build, smoothed (decides whether the faster recording rate is affordable)
    work_avg = 0.0    # how long building AND writing a frame takes, smoothed (decides the rate of the smooth styles)
    code = 0

    try:
        while not quit_app:
            try:
                now = time.time()
                try:
                    player.loop = STYLE["loop"] and ui.which in ("mimic", "quran")
                    # looking at the window (in front, or it can't be told): the loudness is measured finely; behind
                    # another window it is the cheap version, and minimized / frozen it is not measured at all
                    player.update(light=frozen or win_state == "min", hq=win_state != "back")
                except Exception as exc:
                    log_error("player.update")
                    player.stop()
                    player.error = str(exc) or exc.__class__.__name__
                if player.error:
                    ui.note = ("could not play: " + player.error)[:64] + "   see rec_error.log"
                    ui.note_until = now + 6
                    player.error = ""
                if player.ended_path is not None:
                    done, player.ended_path = player.ended_path, None
                    if ui.which == "quran" and QS.cur and done == quran_cache_path(*QS.cur):
                        if STYLE["q_auto"] and not STYLE["loop"]:
                            quran_step(ui, player, 1)
                    elif STYLE["autoplay"] and ui.which == "mimic" and DECODER:
                        paths = [e[0] for e in ui.entries]
                        if done in paths and paths.index(done) + 1 < len(paths):
                            ui.sel = paths.index(done) + 1
                            clamp_scroll(ui)
                            player.play(paths[ui.sel])
                        else:
                            ui.note = "autoplay: end of the list"
                            ui.note_until = now + 3
                quran_poll(ui, player, now)
                if state == "rec" and rec.stream is not None:
                    try:
                        alive = rec.stream.active
                    except Exception:
                        alive = False
                    if not alive:
                        rec.pause()
                        state = "pause"
                        ui.note = "the microphone stopped, press SPACE to reconnect"
                        ui.note_until = now + 8
                stop_take = False
                seek_total = 0.0

                while msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    special = None
                    if ch in ("\x00", "\xe0"):
                        special = msvcrt.getwch()

                    last_key = now
                    if frozen:
                        # a key means the window is being used after all: wake up, then handle the key as usual
                        frozen = False
                        layout = None
                        reset_screen()
                        out.write("\x1b[2J")

                    if ui.confirm and special != "S":
                        ui.confirm = False
                        continue

                    if ui.which == "quran" and quran_key(ui, player, ch, special, now):
                        continue

                    if special is not None:
                        if special == "M" and ui.which == "mimic":
                            ui.which = None
                            player.stop()
                        elif special == "M" and ui.which is None and state != "rec":
                            ui.entries = scan_recordings(folder)
                            ui.sel = 0
                            ui.scroll = 0
                            ui.reset_marks()
                            ui.which = "mine"
                        elif special == "M" and ui.which == "mine" and not ui.mfocus and ui.entries:
                            if get_marks(ui.entries[ui.sel][0]):
                                ui.mfocus = True
                                ui.msel = 0
                                ui.mscroll = 0
                            else:
                                ui.note = "no marks here, press M while it plays to add one"
                                ui.note_until = now + 3
                        elif special == "K" and ui.which == "mine" and ui.mfocus:
                            ui.mfocus = False
                        elif special == "K" and ui.which == "mine":
                            ui.which = None
                            player.stop()
                        elif special == "K" and ui.which is None and state != "rec":
                            mimic_dir.mkdir(exist_ok=True)
                            ui.entries = scan_mimic(mimic_dir)
                            ui.sel = 0
                            ui.scroll = 0
                            ui.which = "mimic"
                            probe_missing(ui, ui.entries)
                            if not DECODER:
                                ui.note = "playback needs: pip install miniaudio av"
                                ui.note_until = now + 6
                        elif special in ("H", "P") and ui.which == "mine" and ui.mfocus:
                            count = len(get_marks(ui.entries[ui.sel][0]))
                            if count:
                                ui.msel = (ui.msel + (-1 if special == "H" else 1)) % count
                                clamp_marks(ui, count)
                        elif special in ("H", "P") and ui.list_open and ui.entries:
                            step = -1 if special == "H" else 1
                            ui.sel = (ui.sel + step) % len(ui.entries)
                            clamp_scroll(ui)
                            ui.reset_marks()
                        elif special == "S" and ui.which == "mine" and ui.mfocus:
                            path = ui.entries[ui.sel][0]
                            marks = list(get_marks(path))
                            if marks:
                                del marks[min(ui.msel, len(marks) - 1)]
                                try:
                                    set_marks(path, marks)
                                except OSError as exc:
                                    ui.note = f"could not remove the mark: {exc.strerror}"
                                    ui.note_until = now + 3
                                clamp_marks(ui, len(marks))
                            if not marks:
                                ui.mfocus = False
                        elif special == "S" and ui.which == "mimic":
                            ui.note = "models are protected, delete them from the mimic folder"
                            ui.note_until = now + 3
                        elif special == "S" and ui.which == "mine" and ui.entries:
                            if ui.confirm:
                                ui.confirm = False
                                entry = ui.entries[ui.sel]
                                if player.path == entry[0]:
                                    player.stop()
                                try:
                                    entry[0].unlink()
                                    marks_path(entry[0]).unlink(missing_ok=True)
                                    MARKS.pop(str(entry[0]), None)
                                except OSError as exc:
                                    ui.note = f"could not delete: {exc.strerror}"
                                    ui.note_until = now + 3
                                ui.entries = scan_recordings(folder)
                                ui.sel = min(ui.sel, max(0, len(ui.entries) - 1))
                                clamp_scroll(ui)
                                ui.reset_marks()
                            else:
                                ui.confirm = True
                        continue

                    if ch == " ":
                        player.stop()
                        ui.which = None
                        if state == "rec":
                            rec.pause()
                            state = "pause"
                        else:
                            try:
                                rec.start(lambda: refresh_audio(player.engine))
                            except Exception as exc:
                                log_error("opening the microphone")
                                ui.note = f"could not open the microphone: {exc}"
                                ui.note_until = now + 4
                                continue
                            if stamp is None:
                                stamp = date.today().isoformat()
                            state = "rec"
                    elif ch in ("l", "L") and ui.which == "mimic":
                        STYLE["loop"] = not STYLE["loop"]
                        extra = ""
                        if STYLE["loop"] and STYLE["autoplay"]:
                            STYLE["autoplay"] = False
                            extra = "   (autoplay off)"
                        save_style()
                        ui.note = "loop: " + ("on" if STYLE["loop"] else "off") + extra
                        ui.note_until = now + 1.5
                    elif ch in ("a", "A") and ui.which == "mimic":
                        STYLE["autoplay"] = not STYLE["autoplay"]
                        extra = ""
                        if STYLE["autoplay"] and STYLE["loop"]:
                            STYLE["loop"] = False
                            extra = "   (loop off)"
                        save_style()
                        ui.note = "autoplay: " + ("on" if STYLE["autoplay"] else "off") + extra
                        ui.note_until = now + 1.5
                    elif ch in ("m", "M") and ui.which == "mine":
                        if (player.playing or player.paused) and player.path is not None:
                            pos = player.position
                            marks = list(get_marks(player.path))
                            near = [m for m in marks if abs(m - pos) <= 1.5]
                            if near:
                                marks = [m for m in marks if m not in near]
                                ui.note = "♦ mark removed"
                            else:
                                marks.append(pos)
                                ui.note = f"♦ mark added at {fmt_dur(pos)}"
                            try:
                                set_marks(player.path, marks)
                            except OSError as exc:
                                ui.note = f"could not save the mark: {exc.strerror}"
                        else:
                            ui.note = "play a recording, then press M to mark the spot"
                        ui.note_until = now + 2
                    elif ch in ("f", "F") and ui.which == "mine":
                        folder.mkdir(exist_ok=True)
                        try:
                            os.startfile(str(folder))
                        except Exception as exc:
                            ui.note = f"could not open folder: {exc}"
                            ui.note_until = now + 3
                    elif ch in ("i", "I") and ui.which == "mimic":
                        picked = pick_files()
                        if picked is None:
                            ui.note = "could not open the file picker, see rec_error.log"
                        elif not picked:
                            ui.note = "import cancelled"
                        else:
                            added, skipped, failed = import_models(picked, mimic_dir)
                            ui.entries = scan_mimic(mimic_dir)
                            if added:
                                names = [e[0] for e in ui.entries]
                                if added[0] in names:
                                    ui.sel = names.index(added[0])
                                    clamp_scroll(ui)
                                probe_missing(ui, ui.entries)
                            ui.note = f"imported {len(added)}"
                            if skipped:
                                ui.note += f", skipped {skipped}"
                            if failed:
                                ui.note += f", {failed} failed"
                        ui.note_until = time.time() + 4
                        while msvcrt.kbhit():
                            msvcrt.getwch()
                        layout = None
                    elif ch in ("b", "B"):
                        if ui.which in ("mimic", "quran"):   # the mimic page has its own picture
                            STYLE["mimic_icon"] = ICON_NAMES[(icon_index(True) + 1) % len(ICON_NAMES)]
                        else:
                            STYLE["icon"] = (STYLE["icon"] + 1) % len(ICON_NAMES)
                        save_style()                     # no message: the new look is the feedback
                    elif ch in ("u", "U") and ui.which == "mimic":
                        quran_open(ui, player)
                    elif ch in ("c", "C") and ui.which == "mimic":
                        STYLE["theme"] = (STYLE["theme"] + 1) % len(THEMES)
                        save_style()
                        ui.note = f"color: {THEMES[STYLE['theme']][0]}"
                        ui.note_until = now + 1.5
                    elif ch in ("q", "Q"):
                        if state != "idle":
                            stop_take = True
                    elif ch == "\r" and ui.which == "mine" and ui.mfocus and ui.entries:
                        path = ui.entries[ui.sel][0]
                        marks = get_marks(path)
                        if marks:
                            target = marks[min(ui.msel, len(marks) - 1)]
                            if not DECODER:
                                ui.note = "playback needs: pip install miniaudio av"
                                ui.note_until = now + 4
                            elif player.path == path and player.data is not None and (player.playing or player.paused):
                                player._start(target)
                            else:
                                player.play(path, target)
                    elif ch == "\r" and ui.list_open and ui.entries:
                        path = ui.entries[ui.sel][0]
                        if player.path == path and player.paused:
                            player.resume()
                        elif player.path == path and player.playing:
                            player.pause()
                        elif player.path == path and (player.playing or player.loading):
                            player.stop()
                        elif not DECODER:
                            ui.note = "playback needs: pip install miniaudio av"
                            ui.note_until = now + 4
                        else:
                            player.play(path)
                    elif ch in (",", ".") and ui.list_open:
                        seek_total += -5.0 if ch == "," else 5.0
                    elif ch in ("[", "]") and ui.which == "quran":
                        seek_total += -30.0 if ch == "[" else 30.0
                    elif ch in ("\x1b", "\x03"):
                        quit_app = True

                if seek_total and not quit_app:
                    player.seek(seek_total)

                if quit_app:
                    break

                if stop_take:
                    player.stop()
                    rec.close()
                    if rec.samples > 0:
                        try:
                            label = save(rec, folder, stamp or date.today().isoformat())
                        except Exception as exc:
                            ui.note = f"could not save: {exc}"
                            ui.note_until = time.time() + 5
                            state = "pause"
                            layout = None
                            out.write("\x1b[2J")
                            continue
                        show_label(label)
                        time.sleep(1.3)
                    rec = Recorder(rate)
                    state = "idle"
                    stamp = None
                    ui.which = None
                    ui.entries = []
                    ui.sel = 0
                    ui.scroll = 0
                    ui.reset_marks()
                    while msvcrt.kbhit():
                        msvcrt.getwch()
                    layout = None
                    out.write("\x1b[2J")
                    continue

                quiet = frozen or win_state == "min"
                cur = shutil.get_terminal_size((80, 24))
                key = (cur.columns, cur.lines, ui.which)
                if key != layout and not quiet:
                    layout = key
                    reset_screen()
                    out.write("\x1b[?25l\x1b[2J")
                if state == "rec":
                    pk_now, rms_now = rec.take_levels()
                    rec_level = follow_mic(rec_level, pk_now, rms_now, now - lv_time)
                    syl_now = rec.take_syllables()
                else:
                    rec_level *= 0.8
                    syl_now = None
                lv_time = now
                nosig = state == "rec" and now - rec.last_signal > 2.5
                # frozen or minimized: nobody is looking, so don't build or write a frame at all
                t_build = time.perf_counter()
                hq = not quiet and win_state != "back"
                frame = "" if quiet else build_frame(
                    state, rec.elapsed, now, cur.columns, cur.lines, ui, player, rec_level, nosig, syl_now, hq
                )
                if not quiet:
                    spent = time.perf_counter() - t_build
                    build_avg += (spent - build_avg) * 0.1
                    if PERF:
                        perf_note(spent, len(frame))
                if frame:
                    out.write(frame)
                    out.flush()
                    work_avg += ((time.perf_counter() - t_build) - work_avg) * 0.1
                busy = (
                    state == "rec"
                    or player.playing
                    or player.loading
                    or player.gate > 0.004
                    or player.level > 0.01
                    or rec_level > 0.01
                    or now < ui.note_until
                )
                if player.playing and not quiet and now - last_trim > TRIM_EVERY:
                    last_trim = now
                    trim_memory()
                # nothing moving: draw less often. Sleep in short slices so a key press is
                # still picked up straight away instead of waiting for the next frame.
                if now - win_checked > (0.2 if quiet else 0.4):
                    win_checked = now
                    seen = window_state()
                    if seen != win_state:
                        if seen == "back":
                            back_since = now
                        win_state = seen
                    if frozen and win_state != "back":
                        # in front again (or the window state can no longer be told): draw again at once
                        frozen = False
                        layout = None
                        reset_screen()
                        out.write("\x1b[2J")
                rate = FPS if busy else IDLE_FPS
                if state == "rec" and win_state != "back" and build_avg < 0.03:
                    rate = REC_FPS    # recording and you are looking: follow the voice more closely
                if win_state != "back" and icon_index(ui.which in ("mimic", "quran")) in (2, 3, 4):
                    rate = max(rate, FPS)     # the characters blink, wink and look around: keep them smooth even when nothing else moves
                if busy and hq:
                    try:
                        smooth_pic = style_is_smooth(ICON_NAMES[icon_index(ui.which in ("mimic", "quran"))])
                    except Exception:
                        smooth_pic = False
                    if smooth_pic:
                        # a smooth style while you are looking: as fast as the console can take it (up to SMOOTH_FPS)
                        rate = max(rate, min(float(SMOOTH_FPS), max(float(FPS), SMOOTH_DUTY / max(work_avg, 0.001))))
                if win_state == "back":
                    rate = min(rate, BACK_FPS)
                # The ONLY time it stops drawing: another window has been in front of it for a few seconds (or it is
                # minimized, handled above). Sitting in front, in use or not, it never freezes.
                if (state != "rec" and not frozen and win_state == "back"
                        and now - max(back_since, last_key) > BACK_FREEZE_AFTER):
                    frozen = True  # the screen stays as it is; nothing is drawn until you come back
                if quiet:
                    # parked in Windows until a key arrives or the timeout: ~0 CPU
                    wait_input(1000 if frozen else 500)
                    continue
                sleep_until(now + 1 / rate)
            except Exception:
                log_error("frame")
                if time.time() - last_error < 1.0:
                    errors += 1
                else:
                    errors = 1
                last_error = time.time()
                if errors >= 20:
                    crashed = True
                    break
                layout = None
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    player.stop()
    player.engine.close()
    rec.close()

    if crashed:
        code = 1
    try:
        if state != "idle" and rec.samples > 0:
            try:
                label = save(rec, folder, stamp or date.today().isoformat())
                show_label(label, park=True)
            except Exception as exc:
                out.write("\x1b[2J\x1b[H" + RESET)
                print(f"could not save the recording: {exc}")
                code = 1
        else:
            out.write("\x1b[2J\x1b[H" + RESET)
        return code
    finally:
        restore_console()
        out.write("\x1b[?25h" + RESET)
        if crashed:
            out.write("\nrec.py ran into repeated errors and closed. Your take was saved if there was one.\nSee rec_error.log next to the script.\n")
        out.flush()


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        log_error("crash")
        sys.stdout.write("\x1b[?25h" + RESET + "\x1b[2J\x1b[H")
        print("rec.py hit an error. The details are saved in rec_error.log next to the script.")
        print()
        traceback.print_exc()
        input("Press Enter to close")
        code = 1
    sys.exit(code)