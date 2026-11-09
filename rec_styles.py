"""rec_styles.py: the animated pictures for rec.py (the ones you cycle with B after the five characters).

Put this file NEXT TO rec.py. rec.py loads it when it starts, and also reloads it while it runs: save this
file and the picture changes within a second, no restart. If this file has a mistake, rec.py keeps the last
good version (or just the five built-in characters) and writes the problem to rec_error.log, so it never
crashes the app.

HOW TO ADD A STYLE (about 10 lines):

    @style("campfire", fps=6, cycle=24, rise=1.2, fall=0.8)
    def campfire(c):
        px = blank()                       # a 31 x 28 grid of None (= empty) that you paint with colours
        h = 2 + int(c.voice * 6)           # c.voice: how loud you are, 0..1 (smoothed by rise/fall)
        rect(px, 13, 24 - h, 17, 25, (255, 160, 40))
        return px

The new style appears at the end of the B cycle automatically. What a draw function gets, in `c`:

    c.voice    loudness 0..1, already smoothed by rise/fall (rates per second; leave None for raw)
    c.tick     0..cycle-1, a looping frame counter that advances `fps` times per second
    c.prog     0..1 how far through the take (only if you set prog_steps), or the saving progress
    c.saving   True while the recording is being saved: then c.prog is the saving progress 0..1 and the
               picture is the progress bar (voice is set to the same value)
    c.extra    whatever your `update` function returned this frame (see below), None while saving
    c.mem      a dict you can keep between frames (only used by styles with an update function)
    c.t        the clock in seconds (use it only if you set cache=False)

A style is DRAWN only when something it depends on changed (voice step, tick, progress, extra), then kept in
a cache, so drawing can be generous. Make everything depend on c.tick / c.voice / c.prog / c.extra and
nothing else (no random(), no time.time()), or the picture will look frozen or flicker. For smooth loops
choose `cycle` so that your motion repeats exactly after `cycle` ticks.

Style options: fps (ticks per second), cycle (ticks in one loop), steps (how finely voice is rounded; 0 means
the picture ignores voice), prog_steps (0 = ignores progress), rise / fall (how fast c.voice follows you up /
down, per second: smaller = calmer), update (a function(mem, dt, voice, active, prog, elapsed, now) called
every frame that can keep state, like the hourglass's sand, and returns a small hashable value that goes
into c.extra), cache (False = draw every frame; for styles that cannot repeat, like the equalizer).

smooth=True (optional): while you are LOOKING at the window, rec.py draws the style at up to 24 frames a second with
the voice unrounded, a fast rise and a soft fall, and no cache, so the picture glides with every little change of the
audio. In that mode c.tick is a FLOAT (use int(c.tick) wherever you need a whole number, for example in hash01 or as
an index) and `mem.get("_hq")` is True inside your update function, so it can return fine, unrounded values. Behind
another window it falls back to the cheap stepped way automatically. Used by waves, waterfall, orb, aurora, windmill
and campfire.

SHARED "FEEL" ENGINE (see _fx_step): waves, wave ring, waterfall, aurora, orb, windmill and campfire fade their
effects in over a small range of volume instead of switching. A slow "energy" value (the average loudness of the
last couple of seconds) drives the big, slow effects (storm, corona, smoke, glowing logs, warm colours) and fades
away gently when you go quiet; sudden onsets fire one-off "peak" effects (splash, shooting star, shockwave, gust,
spark burst, pulse). No rainbows anywhere: every picture keeps to its own small palette.

Colours are (r, g, b) tuples. Helpers: blank, put, rect, disc, line, glow, blend, outline, ramp, lerp,
clamp01, smoothstep, hash01.
"""
import math

W, H = 31, 28
RC_W, RC_H = W, H
VOID = _RC_VOID = (10, 10, 10)        # the console background; glows fade into it
JADE_LO = (6, 78, 54)
JADE_MID = (0, 168, 107)
JADE_HI = (130, 255, 196)
GOLD = (236, 196, 96)
_N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
_SC_LINE = (86, 52, 16)


# ---------------------------------------------------------------- the style registry

class Style:
    def __init__(self, name, draw, fps, cycle, steps, prog_steps, rise, fall, update, cache, smooth=False):
        self.name, self.draw, self.fps, self.cycle = name, draw, fps, cycle
        self.steps, self.prog_steps, self.rise, self.fall = steps, prog_steps, rise, fall
        self.update, self.cache = update, cache
        self.smooth = smooth      # True: while you are looking at the window rec.py feeds this style the voice
                                  # unrounded, a fractional c.tick and a fast rise / soft fall, and draws it
                                  # at a high frame rate (no cache). c.tick is then a FLOAT: use int(c.tick)
                                  # wherever you need a whole number. Otherwise the old stepped way is used.
        self.mem = {}


class Ctx:
    __slots__ = ("voice", "tick", "prog", "saving", "extra", "mem", "t", "W", "H")

    def __init__(self, voice, tick, prog, saving, extra, mem, t):
        self.voice, self.tick, self.prog, self.saving = voice, tick, prog, saving
        self.extra, self.mem, self.t, self.W, self.H = extra, mem, t, W, H


STYLES = []


def style(name, fps=6, cycle=24, steps=8, prog_steps=0, rise=None, fall=None, update=None, cache=True, smooth=False):
    def register(fn):
        STYLES.append(Style(name, fn, fps, cycle, steps, prog_steps, rise, fall, update, cache, smooth))
        return fn
    return register


# ---------------------------------------------------------------- drawing helpers

def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def clamp01(v):
    return max(0.0, min(1.0, v))


def smoothstep(a, b, x):
    t = clamp01((x - a) / float(b - a))
    return t * t * (3.0 - 2.0 * t)


def hash01(x, y, k=0):
    """A repeatable 'random' number 0..1 for a cell, so textures look natural but never flicker."""
    return (((x * 73856093) ^ (y * 19349663) ^ (k * 83492791)) % 1009) / 1009.0


def ramp(cols, t):
    """Walk along a list of colours: t = 0 is the first, t = 1 is the last."""
    t = clamp01(t) * (len(cols) - 1)
    i = min(int(t), len(cols) - 2)
    return lerp(cols[i], cols[i + 1], t - i)


def blank():
    return [[None] * W for _ in range(H)]


def put(px, x, y, col):
    if 0 <= x < W and 0 <= y < H:
        px[y][x] = col


def rect(px, x0, y0, x1, y1, col):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            put(px, x, y, col)


def blend(px, x, y, col, a):
    """Mix a colour over what is there (or over the dark background) by a (0..1)."""
    if 0 <= x < W and 0 <= y < H and a > 0.0:
        base = px[y][x]
        px[y][x] = lerp(VOID if base is None else base, col, min(1.0, a))


def disc(px, cx, cy, r, col, a=1.0):
    for y in range(int(cy - r) - 1, int(cy + r) + 2):
        for x in range(int(cx - r) - 1, int(cx + r) + 2):
            if math.hypot(x + 0.5 - cx, y + 0.5 - cy) <= r:
                if a >= 1.0:
                    put(px, x, y, col)
                else:
                    blend(px, x, y, col, a)


def line(px, x0, y0, x1, y1, col):
    x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        put(px, x0, y0, col)
        if x0 == x1 and y0 == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def outline(px, cells, col):
    """A one-pixel outline just OUTSIDE a set of (x, y) cells, only where nothing is drawn yet."""
    for x, y in cells:
        for dx, dy in _N4:
            a, b = x + dx, y + dy
            if (a, b) not in cells and 0 <= a < W and 0 <= b < H and px[b][a] is None:
                px[b][a] = col


# the same helpers under the names the older styles below use
_px_blank, _px_set, _px_rect = blank, put, rect


def _px_glow(px, cx, cy, rx, ry, col, amt):
    """A soft light: over empty pixels it fades in from the background, over drawn ones it tints."""
    if amt <= 0.02:
        return
    for y in range(H):
        for x in range(W):
            d = math.hypot((x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry)
            if d >= 1.0:
                continue
            a = amt * (1.0 - d) ** 2
            cur = px[y][x]
            if cur is None:
                if a > 0.07:
                    px[y][x] = lerp(VOID, col, min(1.0, a * 0.9))
            else:
                px[y][x] = lerp(cur, col, min(1.0, a * 0.6))


glow = _px_glow


# ---------------------------------------------------------------- the shared "feel" engine
# Every scene style below fades its effects in over a small range of volume (smoothstep), so nothing ever pops.
# Two extra signals drive the big effects:
#   e                 "energy": the loudness averaged over the last couple of seconds. It drives the slow, heavy
#                     effects (storm, corona, smoke, glowing logs) and fades away gently when you go quiet.
#   pe, pa, ps, pid   "peaks": a sudden onset fires ONE event. pe is its fading flash (1 -> 0), pa the seconds since
#                     it fired, ps how strong it was (0..1), pid a counter so every event looks a little different.
#   p2, p3            free-running phases (radians) that each style advances at its own, volume-dependent speed.
# A style's update function calls _fx_step() and appends _fx_out() to what it returns; the draw function reads the
# last seven values of c.extra. While saving c.extra is None and the draw functions use _FX0 ("nothing happening").

_FX0 = (0.0, 0.0, 9.0, 0.0, 0.0, 0.0, 0)          # e, pe, pa, ps, p2, p3, pid
_TAU = 2.0 * math.pi


def _fx_step(mem, dt, voice, now, r2=1.0, r3=1.0):
    v = clamp01(voice)
    dt = max(0.0, min(0.25, dt))
    e = mem.get("e", 0.0)
    e += (v - e) * (1.0 - math.exp(-dt / (1.3 if v > e else 2.6)))          # slow average: quick-ish up, slow down
    f = mem.get("f", 0.0)
    f += (v - f) * (1.0 - math.exp(-dt / 0.08))                              # fast follower
    b = mem.get("b", 0.0)
    b += (v - b) * (1.0 - math.exp(-dt / 0.9))                               # slow baseline
    pe = mem.get("pe", 0.0) * math.exp(-dt / 0.5)
    pa = min(9.0, mem.get("pa", 9.0) + dt)
    cool = mem.get("cool", 0.0) - dt
    armed = mem.get("armed", False)
    rise = f - b
    if rise < 0.04:
        armed = True                                                         # it has settled: the next onset counts
    if armed and cool <= 0.0 and rise > 0.12 and v > 0.25:                   # a sudden jump above the baseline = a peak
        ps = clamp01(0.4 + 1.5 * rise + 0.25 * v)
        mem["ps"] = ps
        mem["pid"] = mem.get("pid", 0) + 1
        pe, pa, cool, armed = max(pe, ps), 0.0, 0.5, False
    mem["e"], mem["f"], mem["b"], mem["pe"], mem["pa"], mem["cool"], mem["armed"] = e, f, b, pe, pa, cool, armed
    mem["p2"] = (mem.get("p2", 0.0) + dt * r2) % _TAU
    mem["p3"] = (mem.get("p3", 0.0) + dt * r3) % _TAU


def _fx_out(mem, hq):
    """The seven fx values, as a hashable tuple. Fine floats when smooth, coarse steps for the cached way."""
    e, pe, pa, ps = mem.get("e", 0.0), mem.get("pe", 0.0), min(3.0, mem.get("pa", 9.0)), mem.get("ps", 0.0)
    p2, p3, pid = mem.get("p2", 0.0), mem.get("p3", 0.0), mem.get("pid", 0)
    if hq:
        return (e, pe, pa, ps, p2, p3, pid)
    q = _TAU / 24.0
    return (round(e * 10) / 10.0, round(pe * 8) / 8.0, round(pa * 8) / 8.0, round(ps * 4) / 4.0,
            round(p2 / q) * q, round(p3 / q) * q, pid)


def _fx_of(extra):
    return extra[-7:] if extra else _FX0


def _shift_x(px, dx):
    """Shake the whole picture sideways by whole pixels (the edge column repeats)."""
    if dx == 0:
        return px
    out = []
    for row in px:
        out.append(([row[0]] * dx + row[:W - dx]) if dx > 0 else (row[-dx:] + [row[-1]] * (-dx)))
    return out


def _px_paint(px, cols, line_col):
    """Paint {(x, y): colour} and give the whole shape one clean outline on its inside edge."""
    for (x, y), c in cols.items():
        edge = any((x + dx, y + dy) not in cols for dx, dy in _N4)
        put(px, x, y, line_col if edge else c)


def _row_set(rows):
    """{(y, half width)} rows centred on column 15 -> set of pixels."""
    out = set()
    for y, hw in rows:
        for x in range(15 - hw, 16 + hw):
            out.add((x, y))
    return out


def _lantern_px(voice, tq):
    px = _px_blank()
    flick = (0.0, 0.05, -0.04, 0.03, 0.0, -0.05, 0.04, 0.0)[tq % 8]
    lit = max(0.0, min(1.0, 0.45 + 0.7 * voice + flick))
    _px_glow(px, 15.5, 14.0, 15.5, 14.0, (255, 170, 60), 0.55 * lit)
    body = {(x, y) for y in range(RC_H) for x in range(RC_W)
            if abs((x + 0.5 - 15.5) / 8.5) ** 2.6 + abs((y + 0.5 - 13.5) / 6.9) ** 2.6 <= 1.0}
    metal = _row_set(((6, 7), (5, 5), (4, 3), (3, 2), (21, 5), (22, 3), (23, 2), (26, 1)))
    metal |= {(15, y) for y in (24, 25)}
    cols = {}
    for x, y in metal:
        cols[(x, y)] = lerp((255, 224, 132), (156, 104, 32), 0.35 * (y % 3) + 0.4 * abs(x - 15) / 8.0)
    for x, y in body:
        d = math.hypot((x + 0.5 - 15.5) / 9.0, (y + 0.5 - 14.5) / 8.0)
        glass = lerp((104, 44, 14), (255, 198, 92), lit * max(0.0, 1.0 - 0.6 * d))
        frame = any((x + dx, y + dy) not in body for dx, dy in _N4)
        if frame or x in (10, 15, 20) or y in (8, 19):
            cols[(x, y)] = lerp((255, 214, 120), (150, 98, 30), (y - 6) / 15.0)
        else:
            cols[(x, y)] = glass
    _px_paint(px, cols, _SC_LINE)
    for x, y in ((14, 0), (15, 0), (16, 0), (13, 1), (17, 1), (13, 2), (17, 2)):   # the hanging ring
        _px_set(px, x, y, (222, 170, 60))
    # the flame: a teardrop that sways and grows with the voice
    h = 7 + int(round(3 * voice)) + (1 if tq % 3 == 0 else 0)
    sway = (0, 0, 1, 1, 0, 0, -1, -1)[tq % 8]
    base_y = 18
    for k in range(h):
        off = sway if k >= 4 else 0     # a 3 pixel wide base, then a 1 pixel tip that sways
        y = base_y - k
        if k < 4:
            for x in (14, 15, 16):
                _px_set(px, x, y, (255, 150, 40))
        _px_set(px, 15 + off, y, (255, 238, 160) if k < h - 1 else (255, 170, 50))
    # sparks in the corners: they only come while it is loud, and each one slowly fades in and out
    # (one slow cycle every four seconds, a different moment for each spark) instead of popping on and off
    gate = max(0.0, min(1.0, (voice - 0.15) * 2.5))
    for i, (x, y) in enumerate(((3, 9), (27, 9), (2, 18), (28, 18), (5, 3), (25, 3))):
        b = math.sin(math.pi * ((tq / 24.0 + i / 6.0) % 1.0)) ** 2 * gate
        if b < 0.1 or px[y][x] is not None:
            continue
        _px_set(px, x, y, lerp(_RC_VOID, (255, 232, 140), b))
        for dx, dy in _N4:
            if px[y + dy][x + dx] is None:
                _px_set(px, x + dx, y + dy, lerp(_RC_VOID, (150, 104, 44), b))
    return px


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


def _orb_px(voice, tq, fx=_FX0, rot=0.0):
    e, pe, pa, ps, p2, p3, pid = fx
    px = _px_blank()
    cx, cy = 15.5, 14.0
    v = clamp01(voice)
    ph = tq / 40.0 * 2.0 * math.pi
    m = smoothstep(0.15, 0.45, v)                        # medium: drifting stars
    L = smoothstep(0.5, 0.85, v)                         # loud: second ring of rays, golden crescent
    S = smoothstep(0.4, 0.8, e)                          # sustained loud: a soft aurora behind the orb
    breath = 0.5 + 0.5 * math.sin(p2)                    # soft: a slow breathing glow
    if S > 0.02:                                         # jade and gold curtains, drawn first so the orb sits in front
        for x in range(RC_W):
            for band, col in ((0, JADE_MID), (1, GOLD)):
                yc = 9.0 + 4.0 * band + 2.6 * math.sin(0.28 * x + p3 + 1.7 * band)
                hg = 3.2 + 2.0 * math.sin(0.5 * x - p3 + band)
                shim = 0.7 + 0.3 * math.sin(1.3 * x + p2)
                for y in range(int(yc - hg), int(yc + hg) + 1):
                    d = abs(y - yc) / hg
                    if d < 1.0:
                        blend(px, x, y, col, S * (0.55 if band == 0 else 0.38) * (1.0 - d) ** 1.5 * shim)
    _px_glow(px, cx, cy, (13.5 + 2.5 * v) * (1.0 + 0.06 * breath), (13.0 + 2.5 * v) * (1.0 + 0.06 * breath), JADE_MID,
             0.10 + 0.42 * v + 0.10 * breath * (1.0 - v))
    ring_col = lerp(JADE_LO, JADE_MID, min(1.0, v * 1.2))
    for y in range(RC_H):                                # a faint dotted ring around the disc
        for x in range(RC_W):
            if 5.7 <= math.hypot(x + 0.5 - cx, y + 0.5 - cy) <= 6.3 and (x + y) % 2 == 0:
                px[y][x] = ring_col
    n = 32
    for i in range(n):
        a = i / n * 2.0 * math.pi
        ln = 1.2 + 0.9 * (0.5 + 0.5 * math.sin(ph + i * 0.4)) \
            + v * 5.2 * (0.55 + 0.45 * math.sin(ph + i * 0.9) * math.cos(2.0 * ph - i * 0.4))
        r = 6.9
        while r <= 6.9 + ln:
            x, y = int(math.floor(cx + math.cos(a) * r)), int(math.floor(cy + math.sin(a) * r))
            t = (r - 6.9) / 7.0
            col = lerp(JADE_MID, GOLD, min(1.0, t * 1.6))
            if v > 0.6 and r > 6.9 + ln - 0.6:
                col = (255, 244, 200)
            _px_set(px, x, y, col)
            r += 0.5
    if L > 0.02:                                         # the second ring of rays turns the other way
        n2 = 24
        for i in range(n2):
            a = i / n2 * 2.0 * math.pi - rot
            ln = (1.0 + 4.2 * v) * (0.55 + 0.45 * math.sin(ph + i * 1.3))
            r = 5.7
            while r <= 5.7 + ln:
                x, y = int(math.floor(cx + math.cos(a) * r)), int(math.floor(cy + math.sin(a) * r))
                t = (r - 5.7) / 6.0
                blend(px, x, y, lerp(JADE_HI, (255, 244, 200), min(1.0, t * 1.5)), L * 0.85 * (1.0 - 0.5 * t))
                r += 0.5
    lit = min(1.0, 0.3 + 0.9 * v + 0.2 * L)
    for y in range(RC_H):                                # the disc
        for x in range(RC_W):
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            if d <= 5.0:
                px[y][x] = lerp(JADE_MID, JADE_HI, 0.2 + 0.7 * v) if d > 4.2 \
                    else lerp((12, 22, 20), (24, 66, 54), v)
    if L > 0.02:
        _px_glow(px, 15.0, 14.0, 6.5, 6.5, (255, 205, 90), 0.45 * L)       # the crescent glows gold
    for y in range(RC_H):                                # the crescent
        for x in range(RC_W):
            if math.hypot(x + 0.5 - 15.0, y + 0.5 - 14.0) <= 3.1 and math.hypot(x + 0.5 - 16.4, y + 0.5 - 13.2) > 2.7:
                px[y][x] = lerp(lerp((150, 124, 68), (255, 232, 150), lit), (255, 206, 96), 0.55 * L)
    _px_set(px, 18, 11, lerp((150, 124, 68), (255, 240, 170), lit))
    for dx, dy in _N4:
        _px_set(px, 18 + dx, 11 + dy, lerp((90, 78, 48), (214, 190, 110), lit))
    if m > 0.02:                                         # tiny stars drifting around the crescent
        for i in range(7):
            a = i * _TAU / 7.0 + (p3 if i % 2 else -p3)
            r = 9.0 + (i % 3) * 1.6 + 0.8 * math.sin(2.0 * p3 + i)
            tw = 0.55 + 0.45 * math.sin(2.0 * p2 + i * 2.1)
            blend(px, int(cx + math.cos(a) * r), int(cy + math.sin(a) * r), (255, 238, 176), m * tw)
    if pa < 0.8 and pe > 0.02:                           # a peak: a shockwave ring flies outward
        fade = (1.0 - pa / 0.8) * (0.4 + 0.6 * ps) * smoothstep(0.0, 0.08, pa)      # it swells in, never pops
        rr = 6.5 + pa * 24.0
        col = lerp(JADE_HI, GOLD, pa / 0.8)
        for y in range(RC_H):
            for x in range(RC_W):
                d = abs(math.hypot(x + 0.5 - cx, y + 0.5 - cy) - rr)
                if d < 0.8:
                    blend(px, x, y, col, fade * (1.0 - d / 0.8))
    if pa < 1.2 and pe > 0.0:                            # ... and lantern-like sparkles float up and away
        for i in range(12):
            a = i / 12.0 * _TAU + hash01(i, 5, pid) * 0.5
            dist = 7.0 + pa * (5.0 + 8.0 * hash01(i, 1, pid))
            x = cx + math.cos(a) * dist
            y = cy + math.sin(a) * dist - pa * pa * 4.0
            al = (1.0 - pa / 1.2) * (0.5 + 0.5 * ps)
            c = (255, 226, 140) if i % 3 else (255, 250, 220)
            blend(px, int(x), int(y), c, al)
            if i % 4 == 0:
                blend(px, int(x), int(y) + 1, c, al * 0.45)
    return px


def _orb_update(mem, dt, voice, active, prog, elapsed, now):
    dt = max(0.0, min(0.25, dt))
    v = clamp01(voice)
    om = mem.get("om", 0.15)
    tgt = 0.15 + 1.5 * v
    om += (tgt - om) * (1.0 - math.exp(-dt * (2.4 if tgt > om else 0.55)))      # the ring has inertia: quick up, coasts down
    mem["om"] = om
    mem["rot"] = (mem.get("rot", 0.0) + dt * om) % _TAU
    _fx_step(mem, dt, v, now, 0.9, 0.35 + 1.1 * v)
    hq = bool(mem.get("_hq"))
    rot = mem["rot"] if hq else round(mem["rot"] / (_TAU / 48.0)) * (_TAU / 48.0)
    return (rot,) + _fx_out(mem, hq)


def _clamp01(v):
    return max(0.0, min(1.0, v))


def _hash2(x, y):
    """A tiny repeatable 'random' number 0..6 for a cell, so rocks and sand look natural but never flicker."""
    return ((x * 73856093) ^ (y * 19349663)) % 7


# ---- hourglass: classic pixel-art one (orange caps, light-blue glass, golden sand). The glass is exactly
# symmetric top to bottom, so when it is full below it can turn over and look exactly like a fresh one. ----
_HG_HW = (8, 8, 8, 7, 7, 6, 5, 4, 3, 2, 1)      # half width of the top bulb for y = 3..13; the bottom mirrors it
_HG_SAND = ((250, 186, 36), (255, 224, 104), (226, 128, 26))
_HG_CAP = ((255, 176, 64), (236, 124, 34), (150, 70, 24))


def _hg_hw(y):
    return _HG_HW[y - 3] if y <= 13 else _HG_HW[27 - y - 3]


def _hourglass_still(p, lvl, sub):
    """p 0..1: how much sand has run down. lvl 0..4: how hard the sand is running (0 = not at all). sub: 0..2,
    the phase of the falling grains."""
    px = _px_blank()
    p = _clamp01(p)
    for y0, order in ((0, (0, 1, 2)), (25, (2, 1, 0))):  # the two wooden caps, light side facing the glass
        for i in range(3):
            for x in range(5, 26):
                c = _HG_CAP[order[i]]
                if x <= 6:
                    c = lerp(c, (255, 214, 120), 0.45)   # lit end
                elif x >= 24:
                    c = lerp(c, (90, 40, 16), 0.45)      # shaded end
                _px_set(px, x, y0 + i, c)
    y_top = 13.5 - (1.0 - p) * 10.5                      # surface of the sand in the top bulb
    bottom_h = p * 10.5
    for y in range(3, 25):
        hw = _hg_hw(y)
        yy = y if y <= 13 else 27 - y                    # fold the texture so both halves match
        for x in range(15 - hw, 16 + hw):
            if x in (15 - hw, 15 + hw) or y in (3, 24):
                px[y][x] = (92, 200, 208) if x < 15 else (38, 134, 154)
                continue
            u = (x - (15 - hw)) / float(max(1, 2 * hw))
            col = lerp((182, 236, 244), (122, 198, 218), u)          # glass: lighter left, deeper right
            if x == 15 - hw + 1 and (yy % 5) != 0 and hw >= 3:
                col = (240, 252, 255)                                  # the long white glint
            sand = False
            if y <= 13:
                dip = 0.0
                if lvl and p > 0.01:                     # a little funnel opens in the sand where it drains
                    dip = (2.0, 1.0, 0.0)[abs(x - 15)] if abs(x - 15) <= 2 else 0.0
                sand = (1.0 - p) > 0.04 and y + 0.5 >= y_top + dip
            else:
                dx = abs(x - 15)
                ph = bottom_h - 0.75 * dx * (1.0 - p * p)
                sand = ph > 0.3 and (24.5 - y) <= ph
            if sand:
                col = _HG_SAND[0]
                if x <= 15 - hw + 2:
                    col = _HG_SAND[1]                                  # sunlit side
                elif x >= 15 + hw - 2:
                    col = _HG_SAND[2]                                  # shaded side
                if _hash2(x, yy) == 0:
                    col = lerp(col, (226, 128, 26), 0.6)               # a few darker grains
            px[y][x] = col
    if lvl and p < 0.985:                                # the falling stream: thicker, faster, brighter with voice
        bottom = 24
        for y in range(14, 25):
            if px[y][15] in _HG_SAND:
                bottom = y
                break
        for y in range(13, bottom):
            on = (y + sub) % 3 if lvl == 1 else True     # a soft trickle breaks into dashes, a strong one is solid
            if on:
                px[y][15] = (255, 240, 150) if (y + sub) % 3 else (252, 178, 40)
            if lvl >= 3:
                for sx in (14, 16):
                    if y >= 15 and abs(sx - 15) < _hg_hw(y) and (y + sub + (sx == 16)) % 2 == 0:
                        px[y][sx] = (255, 226, 120)
        if lvl >= 3 and bottom < 24:                     # grains splash off the top of the pile
            for k in range(3 if lvl >= 4 else 2):
                sx = 15 + ((-2, 2, -1, 1, 3, -3)[(sub * 2 + k) % 6])
                sy = bottom - 1 - (k + sub) % 2
                if abs(sx - 15) < _hg_hw(sy) - 1 and px[sy][sx] not in _HG_SAND:
                    px[sy][sx] = (255, 236, 150)
    return px


def _hourglass_px(p, lvl, sub, flip):
    """flip 0 = still; 1..15 = the glass is turning over end to end (16 would be the finished flip)."""
    if not flip:
        return _hourglass_still(p, lvl, sub)
    base = _hourglass_still(1.0, 0, 0)
    f = math.cos(math.pi * flip / 16.0)
    px = _px_blank()
    if abs(f) < 0.14:                                    # edge-on: just the two caps seen from the side
        for y in (13, 14):
            for x in range(6, 25):
                px[y][x] = lerp(_HG_CAP[0], _HG_CAP[1], 0.5) if x > 8 else (255, 214, 120)
        return px
    for y in range(RC_H):
        s = int(math.floor(14.0 + (y + 0.5 - 14.0) / f))
        if 0 <= s < RC_H:
            px[y] = list(base[s])
    if flip >= 12:                                       # a little twinkle as it settles
        for x, y in ((4, 2), (26, 25)) if flip % 2 else ((5, 25), (25, 2)):
            _px_set(px, x, y, (255, 255, 255))
    return px


def _candle_px(voice, tq, prog):
    """A candle: the flame stands tall when you recite, and leans and shrinks when you are quiet."""
    px = _px_blank()
    p = _clamp01(prog)
    top = 14 + int(p * 6)
    cols = {}
    for y in range(top, 26):
        for x in range(11, 20):
            cols[(x, y)] = (255, 246, 218) if x <= 12 else (244, 228, 192) if x <= 16 else (214, 192, 150)
    for x in range(12, 19):                              # the soft top face of the wax
        cols[(x, top + 1)] = (255, 250, 232)
    _px_paint(px, cols, (120, 90, 56))
    for x in range(7, 24):                               # the brass holder
        for y in (26, 27):
            _px_set(px, x, y, lerp((236, 190, 86), (150, 100, 36), 0.6 if y == 27 else 0.0))
    _px_set(px, 15, top - 1, (60, 40, 30))
    _px_glow(px, 15.5, top - 5, 12.0, 11.0, (255, 176, 70), 0.2 + 0.55 * voice)
    h = 5 + int(round(5 * voice))
    sway = (0, 1, 1, 0, -1, -1, 0, 1)[tq % 8]
    lean = 1.5 - voice                                   # quiet: bends a lot, loud: stands straight
    base = top - 2
    for k in range(h):
        y = base - k
        off = int(round(sway * min(2.0, max(0.0, (k - 2) / 3.0)) * lean))
        if k < 3:
            for x in (14, 15, 16):
                _px_set(px, x, y, (255, 150, 40))
            _px_set(px, 15, y, (255, 238, 160))
        else:
            _px_set(px, 15 + off, y, (255, 238, 160) if k < h - 1 else (255, 170, 50))
    return px


WV_DT = 0.04              # seconds between two stored moments of the voice (25 a second)


def _wave_cols(hist, cur, age):
    """One loudness value per pixel column (oldest left, NOW at the right edge). The stored moments slide
    left a little every frame (age 0..1 = how far the newest one has travelled) and are joined by straight
    lines, so the picture glides instead of jumping a column at a time. The right edge is the live voice."""
    pos, val = [float(W - 1)], [cur]
    for m in range(len(hist)):
        if m == 0 and age < 1e-3:
            continue
        pos.append((W - 1) - age - m)
        val.append(hist[-1 - m])
    out = [0.0] * W
    j, n = 0, len(pos)
    for x in range(W - 1, -1, -1):
        while j < n - 2 and pos[j + 1] > x:
            j += 1
        span = pos[j] - pos[j + 1]
        u = 0.0 if span < 1e-6 else clamp01((pos[j] - x) / span)
        out[x] = val[j] + (val[j + 1] - val[j]) * u
    sm = [0.0] * W                                       # a little spatial smoothing: a flowing line, not a comb
    for x in range(W):
        sm[x] = 0.25 * out[max(0, x - 1)] + 0.5 * out[x] + 0.25 * out[min(W - 1, x + 1)]
    return sm


def _wave_tone(t, W_, Wh, cap):
    """The colour of a bar at height t (0..1): jade at the bottom, pale jade higher, gold at the top, with a bright cream cap."""
    if cap:
        return (255, 244, 200)
    return lerp(JADE_MID, JADE_HI, t) if t < 0.6 else lerp(JADE_HI, GOLD, (t - 0.6) / 0.4)


def _wave_px(vals, gh=None, hd=None, fx=_FX0):
    """A scrolling equalizer: one bar per recent moment of your voice, newest on the right. A peak: a white pulse
    ripples outward from the centre."""
    e, pe, pa, ps, p2, p3, pid = fx
    px = _px_blank()
    mid = 13
    W_ = smoothstep(0.4, 0.7, e)
    Wh = smoothstep(0.72, 0.95, e)
    pulse_on = pa < 0.9 and pe > 0.02
    for i in range((W - 1) // 2):
        x = 1 + 2 * i
        pulse = 0.0
        if pulse_on:
            d = abs(x - 15) - pa * 30.0
            pulse = math.exp(-(d / 2.4) ** 2) * ps * (1.0 - pa / 0.9)
        half = min(12, 1 + int(round(_clamp01(vals[x]) * 11 + 2.4 * pulse)))
        for k in range(half):
            col = _wave_tone(k / 11.0, W_, Wh, False)
            if pulse > 0.0:
                col = lerp(col, (255, 252, 240), min(0.9, pulse * 1.2))
            for yy in (mid - k, mid + 1 + k):
                _px_set(px, x, yy, col)
        cap = _wave_tone(1.0, W_, Wh, True)
        if pulse > 0.0:
            cap = lerp(cap, (255, 252, 240), min(0.9, pulse * 1.2))
        _px_set(px, x, mid - half, cap)
        _px_set(px, x, mid + 1 + half, cap)
    return px


def _wave_update(mem, dt, voice, active, prog, elapsed, now):
    hist = mem.setdefault("hist", [0.0] * (W + 2))
    v = clamp01(voice)
    if mem.get("ht") is None or now - mem["ht"] > 1.0:
        mem["ht"], mem["pk"] = now, v
    pk = max(mem.get("pk", 0.0), v)                       # the loudest moment since the last store: no blip is missed
    n = 0
    while now - mem["ht"] >= WV_DT and n < 8:
        mem["ht"] += WV_DT
        hist.append(pk if n == 0 else v)
        del hist[0]
        pk = v
        n += 1
    if now - mem["ht"] >= WV_DT:
        mem["ht"] = now
    mem["pk"], mem["cur"] = pk, v
    mem["age"] = clamp01((now - mem["ht"]) / WV_DT)
    dt = max(0.0, min(0.25, dt))
    vals = _wave_cols(hist, v, mem["age"])
    gh = mem.setdefault("gh", [0.0] * W)                  # ghosts fall slowly
    hd = mem.setdefault("hd", [0.0] * W)                  # peak-hold dots wait a moment, then drop slowly
    ht = mem.setdefault("hdt", [0.0] * W)
    for x in range(W):
        gh[x] = max(vals[x], gh[x] - dt * 0.9)
        if vals[x] >= hd[x]:
            hd[x], ht[x] = vals[x], 0.45
        else:
            ht[x] -= dt
            if ht[x] <= 0.0:
                hd[x] = max(vals[x], hd[x] - dt * 0.30)
    mem["vals"] = vals
    _fx_step(mem, dt, v, now)
    return None


def _wave_draw(c):
    m = c.mem
    if c.saving:
        vals = [(0.3 + 0.7 * abs(math.sin(c.t * 3.0 + i * 0.35))) if (i + 1) / float(W) <= c.prog + 0.001 else 0.0
                for i in range(W)]
        gh = hd = None
        fx = _FX0
    else:
        vals = m.get("vals") or [0.0] * W
        gh, hd = m.get("gh"), m.get("hd")
        fx = _fx_out(m, True)
    return _wave_px(vals, gh, None, fx)


# ---- waterfall (12): a canyon with mossy cliffs, a fall whose width follows your voice, a pool with ripples ----
_WF_LEFT = (4, 6, 8, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9)           # right edge of the left cliff, y = 6..22
_WF_RIGHT = (26, 24, 22, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21)  # left edge of the right cliff, y = 5..21 (+1)
_WF_ROCK = {}


def _wf_rock_cells():
    cells = {}
    for i, xm in enumerate(_WF_LEFT):
        y = 6 + i
        for x in range(0, xm + 1):
            cells[(x, y)] = "L"
    for i, xm in enumerate(_WF_RIGHT):
        y = 5 + i
        for x in range(xm, RC_W):
            cells[(x, y)] = "R"
    return cells


def _wf_seeds():
    """Seeds of the rock facets: a jittered grid, so the stone breaks into irregular chunks."""
    if "seeds" not in _WF_ROCK:
        seeds = []
        for gy in range(-1, 8):
            for gx in range(-1, 9):
                jx = (_hash2(gx + 11, gy + 3) - 3) * 0.55
                jy = (_hash2(gx + 5, gy + 17) - 3) * 0.5
                seeds.append((gx * 5.4 + 2.5 + jx + (2.7 if gy % 2 else 0.0), gy * 4.6 + 2.0 + jy, _hash2(gx + 40, gy + 9)))
        _WF_ROCK["seeds"] = seeds
    return _WF_ROCK["seeds"]


def _wf_rock_colour(x, y, side):
    """One rock pixel: its facet's tone, a lit upper-left edge, a shaded lower-right edge and dark cracks."""
    seeds = _wf_seeds()
    ox = 0.0 if side == "L" else 19.0                    # the right cliff uses another patch of the pattern
    best = second = None
    for sx, sy, t in seeds:
        d = math.hypot(x - (sx + ox), y - sy)
        if best is None or d < best[0]:
            second, best = best, (d, sx + ox, sy, t)
        elif second is None or d < second[0]:
            second = (d, sx + ox, sy, t)
    d1, sx, sy, t = best
    depth = (y - 5) / 17.0
    col = lerp((150, 140, 166), (84, 76, 108), min(1.0, 0.18 + 0.45 * depth + 0.07 * t))
    s = max(-1.0, min(1.0, ((x - sx) + (y - sy)) / 3.6))
    if s < -0.35:
        col = lerp(col, (200, 192, 212), 0.35 * -s)      # the lit face
    elif s > 0.35:
        col = lerp(col, (46, 40, 70), 0.45 * s)          # the shaded face
    if second[0] - d1 < 0.55:
        col = lerp(col, (30, 26, 48), 0.55)              # a crack between two chunks
    near_water = (side == "L" and x >= 8) or (side == "R" and x <= 22)
    if near_water:
        col = lerp(col, (46, 60, 104), 0.35)             # wet and cold next to the water
    if side == "R":
        col = lerp(col, (30, 26, 48), 0.14)              # the light comes from the left
    return col


def _wf_rock_px():
    """The rocks never change, so they are painted once and copied."""
    if "px" in _WF_ROCK:
        return [row[:] for row in _WF_ROCK["px"]]
    px = _px_blank()
    cells = _wf_rock_cells()
    for y in range(7, 22):                               # the dark back wall of the canyon
        for x in range(10, 21):
            if (x, y) not in cells:
                px[y][x] = lerp((40, 38, 64), (22, 22, 42), (y - 7) / 14.0) if (x + y) % 4 else (48, 46, 76)
    painted = {}
    for (x, y), side in cells.items():
        painted[(x, y)] = _wf_rock_colour(x, y, side)
        top = (x, y - 1) not in cells
        if top and y <= 9:
            painted[(x, y)] = (112, 204, 108) if _hash2(x, y) % 2 else (72, 156, 84)
        elif y <= 10 and (x, y - 1) in cells and (x, y - 2) not in cells and _hash2(x, y) in (0, 3):
            painted[(x, y)] = (52, 124, 68)              # moss hanging down a little
    _px_paint(px, painted, (20, 18, 32))
    leaf = ((126, 216, 112), (86, 172, 92), (54, 126, 72))        # lit top, middle, shaded bottom
    for x0, ys in ((2, (2, 3, 4, 5)), (6, (4, 5)), (25, (1, 2, 3, 4)), (28, (3, 4))):   # round bushes on the cliff tops
        for k, y in enumerate(ys):
            tone = min(2, k * 3 // len(ys))
            for x in range(x0 - k // 2 - 1, x0 + 2 + k // 2):
                if (x, y) not in cells and 0 <= x < RC_W and px[y][x] is None:
                    t = tone
                    if _hash2(x, y) == 1 and t < 2:
                        t += 1                                   # a few darker leaves, not a checkerboard
                    px[y][x] = leaf[t]
    _WF_ROCK["px"] = px
    return [row[:] for row in px]


def _wf_cells_list():
    if "cl" not in _WF_ROCK:
        _WF_ROCK["cl"] = list(_wf_rock_cells().keys())
    return _WF_ROCK["cl"]


def _waterfall_px(voice, sub, ring, smooth=False, fx=_FX0):
    """voice 0..1: a thin trickle when quiet, the whole canyon roaring when you recite. sub 0..34: the falling
    water's phase (it runs faster the louder you are). ring 0..23: the pool ripples' phase. fx: see _fx_step.
    Tiers fade in with the volume: soft = thin stream + droplets, medium = mist and rippling edges, loud = white
    boulders, a shaking canyon and a bulging pool; peaks = a splash ring and droplets thrown in arcs; sustained loud
    = glistening cliffs and a shaft of sunlight through the mist."""
    e, pe, pa, ps, p2, p3, pid = fx
    px = _wf_rock_px()
    v = _clamp01(voice)
    k = _clamp01((v - 0.05) / 0.75)                      # 0 = a trickle ... 1 = full roar
    m = smoothstep(0.18, 0.5, v)                         # medium
    L = smoothstep(0.5, 0.85, v)                         # loud
    S = smoothstep(0.4, 0.75, e)                         # sustained loud
    sub = int(sub)                                       # whole pixels (the dashes and the foam texture)
    hwf = 1.0 + 4.0 * k if smooth else float(1 + int(round(4 * k)))
    cells = _wf_rock_cells()
    if S > 0.02:                                         # the cliff walls glisten: a wet sheen and twinkling highlights
        seed = int(p3 * 2.0)
        for (x, y) in _wf_cells_list():
            blend(px, x, y, (170, 212, 240), 0.10 * S)
            if hash01(x, y, seed) > 0.935:
                blend(px, x, y, (235, 250, 255), 0.85 * S)
    pool = lambda x, y: y >= 22 and abs((x + 0.5 - 15.5) / 14.0) ** 2.5 + abs((y + 0.5 - 24.8) / 3.0) ** 2.5 <= 1.0
    for y in range(22, RC_H):                            # the pool, with a bright back rim and a dark front
        for x in range(RC_W):
            if pool(x, y):
                rim = not pool(x, y - 1) or not pool(x - 1, y) or not pool(x + 1, y) or not pool(x, y + 1)
                if rim:
                    px[y][x] = (130, 206, 238) if y <= 23 else (22, 66, 128)
                else:
                    px[y][x] = lerp((64, 160, 220), (30, 96, 172), (y - 22) / 5.0)
    r1 = ring / 24.0 * 12.0                              # rings spreading across the pool: more of them, stronger, when loud
    n_rings = 2 + int(k > 0.45) + int(k > 0.8)
    for j in range(n_rings):
        r = (r1 + j * 12.0 / n_rings) % 12.0
        if r < 1.5:
            continue
        a = max(0.0, 1.0 - r / 12.0) * (0.35 + 0.65 * k)
        for y in range(22, RC_H):
            for x in range(RC_W):
                if pool(x, y) and px[y][x] is not None:
                    q = ((x + 0.5 - 15.5) / r) ** 2 + ((y + 0.5 - 24.8) / (r * 0.26)) ** 2
                    if abs(q - 1.0) < 0.22:
                        px[y][x] = lerp(px[y][x], (190, 232, 252), a)
    for y in range(7, 22):                               # the fall itself
        wob = m * (0.55 * math.sin(y * 0.85 - 2.0 * p3) + 0.3 * math.sin(y * 1.7 + 3.0 * p3))     # the edges ripple
        hwr = max(1.0, min(5.0, hwf + (wob if smooth else round(wob))))
        hwi = int(math.ceil(hwr - 1e-6))
        for x in range(15 - hwi, 16 + hwi):
            u = min(1.0, abs(x - 15) / float(max(1.0, hwr)))
            cov = clamp01(hwr - abs(x - 15) + 1.0) if smooth else 1.0
            under = px[y][x]
            col = lerp((100, 202, 246), (46, 124, 208), u ** 1.5)
            mm = (y - sub + (x * 3) % 7) % 7                # bright dashes that slide down the fall
            if mm < 2 or (k > 0.55 and mm == 3 and (x + y) % 2 == 0):
                col = (232, 248, 255)
            elif mm == 2:
                col = (172, 226, 250)
            elif (y - sub + x * 2) % 5 == 0:
                col = lerp(col, (172, 226, 250), 0.6)
            if abs(x - 15) == hwi:
                col = (30, 96, 182) if (y + sub) % 4 else (84, 160, 226)
            if y == 7:
                col = (170, 228, 252)                    # the smooth lip
            if cov < 1.0:
                col = lerp(_RC_VOID if under is None else under, col, cov)
            px[y][x] = col
    hw = int(math.ceil(hwf - 1e-6))
    if L > 0.02:                                         # boulders of white water crash down the fall
        for i in range(5):
            ph_i = sub + i * 7
            y0 = 7 + (ph_i % 35) / 35.0 * 15.0
            side = (hash01(i, int(ph_i // 35), 7) - 0.5) * 2.0
            bx = int(round(15 + side * max(0.0, hwf - 1.4)))
            for dx, dy, c in ((0, 0, (250, 253, 255)), (1, 0, (240, 250, 255)), (0, 1, (205, 232, 250)), (1, 1, (186, 222, 248))):
                x, y = bx + dx, int(y0) + dy
                if 10 <= x <= 20 and 7 <= y <= 21:
                    px[y][x] = lerp(px[y][x] if px[y][x] is not None else _RC_VOID, c, 0.9 * L)
    if k > 0.5:                                          # the water heaps over the lip when it is strong
        for x in range(15 - hw + 1, 16 + hw):
            put(px, x, 6, (190, 236, 255) if (x + sub) % 2 else (140, 214, 250))
        if k > 0.85:
            for x in range(15 - hw + 3, 16 + hw - 2):
                put(px, x, 5, (214, 244, 255))
    top = 20 - int(round(6 * k))                         # foam where it lands: it churns higher the harder it falls
    step = sub // 2
    for x in range(15 - hw - 1, 16 + hw + 1):
        for y in range(top, 23):
            if y >= 22 and not pool(x, y):
                continue
            lowness = (y - top) / float(max(1, 22 - top))
            if y >= 21 or hash01(x, y, step) < 0.25 + 0.65 * lowness:
                c = (214, 242, 255) if y in (20, 22) else (240, 252, 255)
                if y < 20 and hash01(x, y, step + 3) < 0.4:
                    c = (186, 226, 252)
                px[y][x] = c
    if L > 0.02:                                         # the pool bulges where the water hits it
        for x in range(10, 22):
            d = abs(x + 0.5 - 15.5) / 5.5
            if d < 1.0:
                for y, f in ((22, 1.0), (23, 0.5)):
                    if pool(x, y):
                        blend(px, x, y, (200, 238, 252), 0.4 * L * (1.0 - d * d) * f)
    _px_glow(px, 15.5, 19.0, 9.0 + 4.0 * k, 6.5 + 2.0 * k, (190, 232, 255), 0.2 + 0.55 * k)    # mist over the splash
    if m > 0.02:                                         # medium: spray drifts out of the fall as a pale mist
        rx = 7.0 + 5.0 * m
        for y in range(8, 23):
            for x in range(5, 27):
                d = math.hypot((x + 0.5 - 15.5) / rx, (y + 0.5 - 16.5) / 8.5)
                if d < 1.0:
                    nz = 0.65 + 0.35 * math.sin(x * 0.9 + y * 0.7 + p3)
                    blend(px, x, y, (214, 238, 252), m * 0.26 * (1.0 - d) * nz)
    if S > 0.02:                                         # sustained loud: a shaft of sunlight through the mist (no rainbow)
        for y in range(0, 22):
            cxs = 4.0 + y * 0.62
            for x in range(int(cxs - 3), int(cxs + 4)):
                d = abs(x + 0.5 - cxs) / 2.6
                if d < 1.0:
                    blend(px, x, y, (255, 244, 206), 0.15 * S * (1.0 - d) * (1.0 - y / 30.0))
    for sx0, sy0 in ((3, 23), (24, 24)):                 # two stones at the edge of the pool
        for dx, dy, c in ((0, 0, (170, 162, 184)), (1, 0, (146, 138, 164)), (2, 0, (110, 102, 134)),
                          (0, 1, (120, 112, 144)), (1, 1, (98, 90, 122)), (2, 1, (70, 62, 96))):
            px[sy0 + dy][sx0 + dx] = c
    drops = 3 + int(round(15 * k))                       # spray thrown off the foam in arcs, farther and higher when loud
    for i in range(drops):
        h = ((sub * 0.5 + i * 3.7) % 10.0) / 10.0
        side = 1 if i % 2 else -1
        reach = (2.0 + (i * 5) % 7) * (0.55 + 0.9 * k)
        x = int(round(15 + side * (hw * 0.6 + reach * h * 1.5)))
        y = int(round(21 - (3.0 + 7.0 * k) * 4.0 * h * (1.0 - h) * (0.6 + 0.4 * ((i * 3) % 5) / 4.0)))
        b = min(1.0, (1.0 - h) * 1.4) * (0.45 + 0.5 * k)
        if 0 <= x < RC_W and 0 <= y < RC_H and (px[y][x] is None or y >= 22 or (x, y) not in cells):
            cur = px[y][x]
            px[y][x] = lerp(_RC_VOID if cur is None else cur, (226, 246, 255), b)
    if pa < 1.0 and pe > 0.02:                           # a peak: a splash ring on the pool and drops thrown in arcs
        fade = (1.0 - pa) * (0.5 + 0.5 * ps)
        rr = 1.5 + pa * 15.0
        for y in range(22, RC_H):
            for x in range(RC_W):
                if pool(x, y):
                    q = ((x + 0.5 - 15.5) / rr) ** 2 + ((y + 0.5 - 24.8) / (rr * 0.3)) ** 2
                    if abs(q - 1.0) < 0.3:
                        blend(px, x, y, (240, 252, 255), fade * (1.0 - abs(q - 1.0) / 0.3))
        for i in range(8 + int(8 * ps)):
            vx = (hash01(i, 3, pid) - 0.5) * 2.0 * (7.0 + 6.0 * ps)
            vy = 14.0 + 9.0 * hash01(i, 5, pid) + 6.0 * ps
            x = 15.5 + vx * pa
            y = 21.5 - (vy * pa - 22.0 * pa * pa)
            if y < 22.0 and 0 <= y and 0 <= x < RC_W and (int(x), int(y)) not in cells:
                blend(px, int(x), int(y), (236, 250, 255), 0.95 * (1.0 - 0.6 * pa))
                if ps > 0.7 and i % 3 == 0:
                    blend(px, int(x), int(y) + 1, (200, 232, 252), 0.5 * (1.0 - pa))
    amp = 0.62 * L * (0.3 + 0.7 * e) + 0.95 * pe        # the whole canyon shakes: a little when loud, more on a peak
    dx = int(round(amp * math.sin(p2)))
    return _shift_x(px, dx)


def _wf_update(mem, dt, voice, active, prog, elapsed, now):
    k = clamp01((voice - 0.05) / 0.75)
    mem["s"] = (mem.get("s", 0.0) + dt * (2.0 + 18.0 * k)) % 35.0          # the water runs faster the louder you are
    mem["r"] = (mem.get("r", 0.0) + dt * (0.12 + 0.8 * k)) % 1.0          # and the pool ripples more
    _fx_step(mem, dt, voice, now, 26.0, 1.5 + 5.0 * k)
    hq = bool(mem.get("_hq"))
    if hq:                                                                  # smooth: the ripples move in fine steps
        return (int(mem["s"]), mem["r"] * 24.0) + _fx_out(mem, True)
    return (int(mem["s"]), int(mem["r"] * 24) % 24) + _fx_out(mem, False)


# ================================================================ the older styles, registered
@style("lantern", fps=6, cycle=24, steps=8, rise=1.4, fall=0.8)
def lantern(c):
    return _lantern_px(c.voice, c.tick)


def _cassette_update(mem, dt, voice, active, prog, elapsed, now):
    period = 2.0 * math.pi / 3.0                                      # the reel has three spokes
    if active:
        mem["ang"] = (mem.get("ang", 0.0) + dt * (1.5 + 4.0 * voice)) % period
    return int(mem.get("ang", 0.0) / period * 8) % 8


@style("cassette", fps=12, cycle=8, steps=8, prog_steps=10, update=_cassette_update)
def cassette(c):
    return _cassette_px(c.voice, c.extra if c.extra is not None else c.tick % 8, c.prog)


@style("orb", fps=10, cycle=40, steps=8, rise=4.0, fall=1.4, update=_orb_update, smooth=True)
def orb(c):
    if c.extra is None:
        return _orb_px(c.voice, c.tick)
    return _orb_px(c.voice, c.tick, _fx_of(c.extra), c.extra[0])


HG_MAX = 0.05             # fraction of the glass that runs down per second at full voice (20 s to empty)
HG_MIN = 0.006            # ... and just above the threshold (a quiet voice trickles)
HG_GATE = 0.10            # below this (smoothed) voice nothing runs
HG_TURN = 1.1             # seconds the glass takes to turn over


def _hg_update(mem, dt, voice, active, prog, elapsed, now):
    p = mem.get("p", 0.0)
    flip = mem.get("flip")
    sub = mem.get("sub", 0.0)
    lvl = 0
    if flip is not None:                                          # turning over: the sand waits
        flip += dt
        if flip >= HG_TURN:
            flip, p = None, 0.0
        mem["flip"] = flip
    elif voice > HG_GATE:
        k = clamp01((voice - HG_GATE) / (1.0 - HG_GATE))
        p = min(1.0, p + dt * (HG_MIN + (HG_MAX - HG_MIN) * k ** 1.2))
        sub += dt * (5.0 + 22.0 * k)                              # grains fall faster the louder you are
        lvl = 1 + (k > 0.22) + (k > 0.5) + (k > 0.8)
        if p >= 0.995:
            p, mem["flip"], flip, lvl = 1.0, 0.0, 0.0, 0
    elif prog < 0.001 and not active:
        p = 0.0                                                   # nothing happening: a fresh glass
    mem["p"], mem["sub"] = p, sub % 3.0
    fq = 0 if flip is None else max(1, min(15, int(flip / HG_TURN * 16.0)))
    return (int(round(p * 60)), lvl, int(sub) % 3, fq)


@style("hourglass", fps=12, cycle=3, steps=0, prog_steps=40, rise=5.0, fall=2.5, update=_hg_update)
def hourglass(c):
    if c.saving:
        return _hourglass_px(c.prog, 3 if c.prog < 0.985 else 0, c.tick % 3, 0)
    pq, lvl, sub, fq = c.extra
    return _hourglass_px(pq / 60.0, lvl, sub, fq)


@style("candle", fps=6, cycle=24, steps=8, prog_steps=20, rise=1.4, fall=0.8)
def candle(c):
    return _candle_px(c.voice, c.tick, 0.0 if c.saving else c.prog)


@style("waves", steps=0, cache=False, update=_wave_update, smooth=True)
def waves(c):
    return _wave_draw(c)


@style("waterfall", fps=8, cycle=35, steps=10, rise=4.5, fall=1.5, update=_wf_update, smooth=True)
def waterfall(c):
    if c.saving:
        return _waterfall_px(0.15 + 0.8 * c.prog, c.tick % 35, c.tick * 24 // 35)
    return _waterfall_px(c.voice, c.extra[0], c.extra[1], smooth=bool(c.mem.get("_hq")), fx=_fx_of(c.extra))


# ================================================================ TREE
# The koi pond's loading screen: a tree by the little pond goes from bare winter through blossom to full
# summer green as the recording saves. (It is drawn by the koi style; it is not a style of its own.)

_TR_LEAF = (
    ((196, 92, 128), (232, 134, 164), (250, 176, 198), (255, 222, 232)),   # spring: blossom
    ((26, 82, 50), (44, 124, 64), (74, 168, 82), (130, 212, 110)),         # summer: green
    ((150, 48, 30), (204, 88, 32), (240, 148, 46), (252, 204, 90)),        # autumn: gold and red
    ((96, 60, 40), (112, 72, 46), (130, 86, 52), (150, 100, 60)),          # winter: (no leaves anyway)
    ((196, 92, 128), (232, 134, 164), (250, 176, 198), (255, 222, 232)),
)
_TR_GROUND = ((122, 196, 92), (64, 150, 70), (176, 132, 62), (232, 240, 252), (122, 196, 92))
_TR_SUN = ((255, 238, 176), (255, 246, 196), (255, 172, 92), (214, 224, 255), (255, 238, 176))
_TR_COV = ((0.0, 0.72), (0.2, 1.0), (0.45, 1.0), (0.55, 0.8), (0.68, 0.14), (0.75, 0.0), (0.9, 0.0), (0.97, 0.35), (1.0, 0.72))
_TR_BLOBS = ((10.0, 9.6, 5.4), (20.0, 9.6, 5.4), (15.0, 6.4, 5.8), (7.0, 13.6, 3.5), (23.0, 13.6, 3.5), (15.0, 12.6, 5.0))
_TR_BRANCH = ((15, 15, 9, 9, 2), (15, 15, 21, 8, 2), (15, 14, 15, 5, 2), (12, 12, 6, 10, 1), (18, 11, 25, 10, 1),
              (15, 10, 12, 4, 1), (15, 9, 19, 4, 1))


def _tr_season_colours(s):
    f = (s % 1.0) * 4.0
    i, t = int(f), smoothstep(0.30, 0.70, f - int(f))                 # a season keeps its look, then turns quickly
    leaf = tuple(lerp(_TR_LEAF[i][k], _TR_LEAF[i + 1][k], t) for k in range(4))
    return leaf, lerp(_TR_GROUND[i], _TR_GROUND[i + 1], t), lerp(_TR_SUN[i], _TR_SUN[i + 1], t)


def _tr_cover(s):
    for (a, ca), (b, cb) in zip(_TR_COV, _TR_COV[1:]):
        if a <= s <= b:
            return ca + (cb - ca) * (s - a) / (b - a)
    return 0.0


def _tree_px(s, voice, tick):
    px = blank()
    ws = max(0.0, 1.0 - min(s, 1.0 - s) / 0.22)
    wsu = max(0.0, 1.0 - abs(s - 0.25) / 0.22)
    wa = max(0.0, 1.0 - abs(s - 0.5) / 0.22)
    ww = max(0.0, 1.0 - abs(s - 0.75) / 0.22)
    leaf, ground, sun = _tr_season_colours(s)
    cov = _tr_cover(s)

    for y in range(22, H):                                            # the grassy hill
        for x in range(W):
            top = 22 + int(round(2.0 * ((x - 15.5) / 15.5) ** 2))
            if y < top:
                continue
            depth = (y - top) / 5.0
            col = lerp(ground, lerp(ground, (24, 40, 60), 0.6), clamp01(depth))
            if ww > 0.5:
                col = lerp((240, 246, 255), (176, 196, 226), clamp01(depth * 0.9))
            elif y == top:
                col = lerp(ground, (255, 255, 220), 0.18)
            px[y][x] = col
            if y <= top + 1:
                h = hash01(x, y, 3)
                if h < 0.11 * ws:
                    px[y][x] = ((255, 180, 206), (255, 255, 250), (255, 232, 110))[int(h * 100) % 3]
                elif h < 0.09 * wsu:
                    px[y][x] = (255, 226, 96)
                elif h < 0.16 * wa:
                    px[y][x] = ((226, 112, 40), (196, 70, 36), (244, 170, 60))[int(h * 100) % 3]
    for x in range(10, 22):                                           # the tree's shadow on the grass
        y = 23 + (1 if abs(x - 15.5) > 4 else 0)
        if px[y][x] is not None:
            px[y][x] = lerp(px[y][x], (10, 24, 30), 0.30)

    for y in range(14, 22):                                           # the trunk
        for k, c in enumerate(((136, 94, 64), (104, 70, 50), (72, 46, 38))):
            put(px, 14 + k, y, c if hash01(14 + k, y, 5) > 0.12 else lerp(c, (40, 24, 24), 0.5))
    for y, (xa, xb) in ((22, (13, 17)), (23, (12, 18))):
        for x in range(xa, xb + 1):
            put(px, x, y, (136, 94, 64) if x < 15 else (104, 70, 50) if x < 17 else (72, 46, 38))
    twig = {}
    for x0, y0, x1, y1, th in _TR_BRANCH:                             # the branches
        tmp = blank()
        line(tmp, x0, y0, x1, y1, (1, 1, 1))
        if th > 1:
            line(tmp, x0 + 1, y0, x1 + 1, y1, (1, 1, 1))
        for yy in range(H):
            for xx in range(W):
                if tmp[yy][xx]:
                    twig[(xx, yy)] = True
    for (x, y) in twig:
        left_empty = (x - 1, y) not in twig
        put(px, x, y, (140, 98, 66) if left_empty else (92, 60, 42))
    if ww > 0.45:                                                     # snow resting on the branches
        for (x, y) in twig:
            if (x, y - 1) not in twig and ww > 0.55:
                put(px, x, y - 1, (244, 248, 255))

    if cov > 0.02:                                                    # the foliage
        n_shim = int(voice * 4)
        cells = {}
        for y in range(H):
            for x in range(W):
                best, bz = None, 0.0
                for bx, by, br in _TR_BLOBS:
                    nx, ny = (x + 0.5 - bx) / br, (y + 0.5 - by) / br
                    d2 = nx * nx + ny * ny
                    if d2 <= 1.0:
                        z = math.sqrt(1.0 - d2)
                        if best is None or z > bz:
                            best, bz = (nx, ny, z), z
                if best is None:
                    continue
                if (0.55 * hash01(x // 2, y // 2, 7) + 0.45 * hash01(x, y, 11)) >= cov and not (bz > 0.6 and cov > 0.6):
                    continue                                           # gaps only at the edges, never holes inside
                nx, ny, z = best
                lit = nx * -0.5 + ny * -0.7 + z * 0.5
                lvl = clamp01((lit + 0.55) / 1.6) * 3.0 + (hash01(x, y, 13) - 0.5) * 0.8
                if n_shim and ((int(hash01(x, y, 17) * 14) + tick // 4) % 14) < n_shim:
                    lvl += 1.0                                         # a leaf catching the light
                col = ramp(leaf, clamp01(lvl / 3.0))
                h = hash01(x, y, 19)
                if ws > 0.3 and h < 0.16:
                    col = (255, 236, 244)
                elif ws > 0.3 and h > 0.9:
                    col = (140, 200, 104)
                elif wa > 0.3 and 0.2 < h < 0.34:
                    col = lerp((176, 40, 38), (222, 72, 44), clamp01(lvl / 3.0))
                cells[(x, y)] = col
        dark = lerp(leaf[0], (14, 12, 20), 0.35)
        for (x, y), col in cells.items():
            edge = any((x + dx, y + dy) not in cells for dx, dy in _N4)
            px[y][x] = dark if (edge and cov > 0.55) else col

    if voice > 0.05:                                                  # the wind: the crown sways, the trunk stays rooted
        amp = min(1.0, voice) * 3.2 * (0.55 + 0.45 * math.sin(2 * math.pi * tick / 28.0))
        for y in range(0, 21):
            sh = int(round(amp * ((21 - y) / 16.0) ** 1.4))
            if sh:
                row = px[y]
                px[y] = ([None] * sh + row[:W - sh]) if sh > 0 else (row[-sh:] + [None] * (-sh))
    glow(px, 27.5, 3.5, 6.0, 6.0, sun, 0.30)                          # the sun (or the winter moon)
    disc(px, 27.5, 3.5, 2.3, sun)

    for y in range(23, 27):                                           # a little pond at the foot of the tree (the koi pond's)
        for x in range(2, 11):
            d = ((x + 0.5 - 6.5) / 4.4) ** 2 + ((y + 0.5 - 24.9) / 1.7) ** 2
            if d <= 1.0:
                rim = d > 0.62
                if ww > 0.5:
                    px[y][x] = (206, 224, 244) if rim else lerp((176, 204, 232), (150, 182, 216), (y - 23) / 3.0)
                else:
                    px[y][x] = (118, 196, 226) if rim and y < 25 else (30, 78, 128) if rim else lerp((72, 164, 212), (34, 98, 160), (y - 23) / 3.0)
    if ww <= 0.5:
        kx = 4 + int(round(2.0 * math.sin(2 * math.pi * tick / 56.0)))
        for dx, c in ((0, (255, 200, 120)), (1, (246, 152, 44)), (2, (232, 72, 40))):     # a tiny koi circling
            put(px, kx + dx, 25, c)
        put(px, 9, 24, (62, 158, 82))
        put(px, 8, 25, (62, 158, 82))
        put(px, 9, 25, (96, 190, 100))
        if ws > 0.3:
            put(px, 9, 24, (255, 188, 212))
    kind, w = max((("petal", ws), ("leaf", wa), ("snow", ww)), key=lambda k: k[1])
    if w > 0.3:                                                       # things drifting down in the wind
        n = 3 + int(voice * 14)
        col = {"petal": (255, 196, 214), "leaf": (232, 120, 40), "snow": (255, 255, 255)}[kind]
        for i in range(min(n, 17)):
            x0 = (i * 9 + 5) % 29 + 1
            y = int(((i * 7) % 28 + tick * 0.5) % 28)
            x = x0 + int(round(2.0 * math.sin(2 * math.pi * (tick * (1 + i % 2) / 56.0) + i * 1.3) + voice * 5.0 * y / 28.0))
            x = max(0, min(W - 1, x))
            if y < 25:
                put(px, x, y, col)
                if kind == "leaf" and i % 2:
                    put(px, x + 1, y, lerp(col, (160, 60, 30), 0.4))
    return px


# ================================================================ KOI POND
# Seen from above: three koi swim slow loops in a stone-ringed pond under lily pads. Your voice sends them
# faster, rings spread across the water, and the lotus opens. While saving, the lotus blooms as the bar fills.

_KOI_N = 10
_KOI_R = (1.65, 2.0, 2.1, 2.0, 1.85, 1.6, 1.3, 1.05, 0.85, 0.65)
_KOI_PATH = {}
_KOI_FISH = (
    # path number, start offset 0..1, pattern (R red, W white, K black, O orange, X dark spot on orange)
    (0, 0.00, "RRWWRRRWWW", 1.00),
    (1, 0.34, "OOOXOOOOOO", 0.92),
    (2, 0.68, "KKRRKKWWKK", 1.08),
)
_KOI_COL = {"R": (232, 72, 40), "W": (252, 246, 238), "K": (40, 38, 52), "O": (246, 152, 44), "X": (30, 26, 34)}


def _koi_point(i, u):
    """Three loops of the same shape (a slightly wobbly oval), each turned 60 degrees from the last, so the
    fish share the pond without ever piling up on one spot."""
    r = 1.0 + 0.10 * math.sin(3 * u + i)
    x, y = 6.8 * r * math.cos(u), 4.6 * r * math.sin(u)
    th = i * math.pi / 3.0
    return 15.5 + x * math.cos(th) - y * math.sin(th), 13.5 + x * math.sin(th) + y * math.cos(th)


def _koi_path(i):
    """The loop as a closed polyline with its length, so fish can be placed by distance (no stretching)."""
    if i not in _KOI_PATH:
        pts, total = [], 0.0
        n = 720
        prev = None
        for k in range(n + 1):
            p = _koi_point(i, 2 * math.pi * k / n)
            if prev is not None:
                total += math.hypot(p[0] - prev[0], p[1] - prev[1])
            pts.append((p[0], p[1], total))
            prev = p
        _KOI_PATH[i] = (pts, total)
    return _KOI_PATH[i]


def _koi_at(i, s):
    pts, total = _koi_path(i)
    s %= total
    lo, hi = 0, len(pts) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if pts[mid][2] <= s:
            lo = mid
        else:
            hi = mid
    a, b = pts[lo], pts[hi]
    t = (s - a[2]) / max(1e-9, b[2] - a[2])
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _koi_update(mem, dt, voice, active, prog, elapsed, now):
    v = clamp01(voice)
    # the fish drift when you are quiet (a loop takes over a minute) and race when you are loud (about 7 s)
    mem["ph"] = (mem.get("ph", 0.0) + dt * (0.014 + 0.14 * v)) % 1.0
    mem["wg"] = (mem.get("wg", 0.0) + dt * (0.25 + 4.0 * v)) % 1.0          # tails beat faster too
    return (int(mem["ph"] * 240) % 240, int(mem["wg"] * 24) % 24)


def _koi_px(voice, tick, phase, wagq, bloom):
    px = blank()
    cx, cy = 15.5, 13.5
    wet = set()
    for y in range(H):                                                # the water, light in the middle
        for x in range(W):
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            if d <= 11.8:
                wet.add((x, y))
                col = lerp((36, 116, 132), (12, 50, 76), (d / 11.8) ** 1.5)
                w = (math.sin(0.7 * x + 2 * math.pi * tick / 48.0) * math.sin(0.8 * y - 2 * math.pi * tick / 48.0)
                     + 0.5 * math.sin(0.45 * (x + y) + 4 * math.pi * tick / 48.0))
                if w > 0.8:
                    col = lerp(col, (110, 196, 192), 0.12)                        # soft dapples of light on the bottom
                px[y][x] = col
    pads = ((8.6, 8.4, 3.0, 0.9), (20.5, 18.8, 4.0, 4.1), (21.0, 7.0, 2.3, 2.3))
    for (x, y, r, a) in pads:                                         # the pads' shadows on the water
        disc(px, x + 1.0, y + 1.2, r, (6, 26, 38), 0.32)

    fish = []
    length = (_KOI_N - 1) * 1.25
    n_samp = 26
    for i, off, pat, mult in _KOI_FISH:
        total = _koi_path(i)[1]
        s_head = ((phase / 240.0 * mult + off) % 1.0) * total
        samples = []
        for q in range(n_samp):                                       # the body, sampled finely so it has no gaps
            t = q / float(n_samp - 1)
            sd = s_head - t * length
            x, y = _koi_at(i, sd)
            xa, ya = _koi_at(i, sd + 0.6)
            xb, yb = _koi_at(i, sd - 0.6)
            dx, dy = xa - xb, ya - yb
            n = math.hypot(dx, dy) or 1.0
            dx, dy = dx / n, dy / n
            wag = math.sin(2 * math.pi * (wagq / 24.0 - t * 2.9 + i)) * 0.45 * t ** 1.2
            ft = t * (_KOI_N - 1)
            k0 = min(int(ft), _KOI_N - 2)
            rad = _KOI_R[k0] + (_KOI_R[k0 + 1] - _KOI_R[k0]) * (ft - k0)
            samples.append((x - dy * wag, y + dx * wag, dx, dy, rad, pat[min(_KOI_N - 1, int(t * _KOI_N))]))
        fish.append((pat, samples))
    speed = clamp01((voice - 0.2) / 0.5)
    if speed > 0.05:                                                  # the faster they swim, the more wake they leave
        for pat, samples in fish:
            for q in (3, 8, 13, 18):
                x, y, dx, dy = samples[q][0], samples[q][1], samples[q][2], samples[q][3]
                for sgn in (-1, 1):
                    d = 1.7 + 0.28 * q
                    wx, wy = int(round(x - dy * sgn * d)), int(round(y + dx * sgn * d))
                    if (wx, wy) in wet:
                        blend(px, wx, wy, (196, 236, 244), speed * (0.5 - 0.02 * q))
    for pat, samples in fish:                                         # fish shadows on the bottom
        for (x, y, dx, dy, rad, ck) in samples:
            disc(px, x + 0.9, y + 1.4, rad * 0.95, (4, 22, 32), 0.22)
    for pat, samples in fish:
        for (x, y, dx, dy, rad, ck) in reversed(samples):             # tail first, so the head ends up on top
            base = _KOI_COL[ck]
            for yy in range(int(y - rad) - 1, int(y + rad) + 2):
                for xx in range(int(x - rad) - 1, int(x + rad) + 2):
                    if math.hypot(xx + 0.5 - x, yy + 0.5 - y) <= rad:
                        top = (xx + 0.5 - x) + (yy + 0.5 - y) < -rad * 0.75
                        put(px, xx, yy, lerp(base, (255, 255, 255), 0.18) if top else base)
        xt, yt, dxt, dyt = samples[-1][0], samples[-1][1], samples[-1][2], samples[-1][3]
        tail_col = lerp(_KOI_COL[pat[-1]], (255, 255, 255), 0.22)
        wag_t = math.sin(2 * math.pi * (wagq / 24.0 - 2.9))
        for j in range(1, 4):                                         # the tail fin: a small fan that widens and sways
            half = 0.4 + 0.65 * j
            m = -half
            while m <= half + 0.01:
                lat = m + wag_t * 0.4 * j
                blend(px, int(round(xt - dxt * j * 1.0 - dyt * lat)), int(round(yt - dyt * j * 1.0 + dxt * lat)), tail_col, 0.95 - 0.18 * j)
                m += 0.5
        x2, y2, dx2, dy2 = samples[6][0], samples[6][1], samples[6][2], samples[6][3]
        for sgn in (-1, 1):                                           # the side fins
            blend(px, int(round(x2 - dy2 * sgn * 2.4 - dx2 * 0.6)), int(round(y2 + dx2 * sgn * 2.4 - dy2 * 0.6)), (255, 226, 210), 0.85)
        x0, y0, dx0, dy0 = samples[0][0], samples[0][1], samples[0][2], samples[0][3]
        for sgn in (-1, 1):                                           # the eyes
            put(px, int(round(x0 - dy0 * sgn * 1.0 + dx0 * 0.3)), int(round(y0 + dx0 * sgn * 1.0 + dy0 * 0.3)), (18, 18, 28))

    for (x, y, r, a) in pads:                                         # the lily pads, over the fish
        ca = math.cos(a)
        sa = math.sin(a)
        for yy in range(int(y - r) - 1, int(y + r) + 2):
            for xx in range(int(x - r) - 1, int(x + r) + 2):
                ddx, ddy = xx + 0.5 - x, yy + 0.5 - y
                d = math.hypot(ddx, ddy)
                if d > r:
                    continue
                ang = math.atan2(ddy, ddx) - a
                ang = (ang + math.pi) % (2 * math.pi) - math.pi
                if d > 0.5 and abs(ang) < 0.2:
                    continue                                          # the notch
                t = ddx * -0.5 + ddy * -0.6
                col = lerp((52, 150, 78), (98, 190, 96), clamp01(0.5 + t / (2.2 * r)))
                if d > r - 0.9:
                    col = lerp(col, (22, 88, 58), 0.6)
                put(px, xx, yy, col)
        put(px, int(x - r * 0.35), int(y - r * 0.4), (150, 220, 126))
    lx, ly, lr = 20.5, 18.5, 4.2                                      # the lotus on the biggest pad
    o = clamp01(bloom)
    for layer, (ln, col_a, col_b, ph0) in enumerate(((1.5 + 2.3 * o, (246, 150, 190), (255, 186, 212), 0.0),
                                                     (0.9 + 1.4 * o, (255, 214, 230), (255, 234, 242), math.pi / 8))):
        for k in range(8):
            a = ph0 + k * math.pi / 4.0
            x1, y1 = lx + math.cos(a) * ln, ly + math.sin(a) * ln
            line(px, lx, ly, x1, y1, col_a if k % 2 else col_b)
    disc(px, lx, ly, 1.0, (252, 214, 84))

    for i in range(5):                                                # rings spreading when you speak
        if i >= int(round(voice * 5)):
            break
        rx, ry = ((11.5, 12.5), (18.5, 7.5), (13.5, 16.5), (10.5, 4.5), (17.5, 21.0))[i]
        r = ((tick + i * 24) % 40) / 40.0 * 6.5
        a = (1.0 - r / 6.5) * 0.65
        if r < 0.8:
            continue
        for (x, y) in wet:
            if abs(math.hypot(x + 0.5 - rx, y + 0.5 - ry) - r) < 0.55:
                blend(px, x, y, (160, 218, 228), a)

    for i in range(34):                                               # the ring of stones
        a = i * 2 * math.pi / 34.0
        sx, sy = cx + 12.9 * math.cos(a), cy + 12.9 * math.sin(a)
        h = hash01(i, 5, 31)
        tone = ((122, 120, 138), (98, 96, 116), (142, 138, 154))[int(h * 3) % 3]
        for yy in range(int(sy - 2), int(sy + 3)):
            for xx in range(int(sx - 2), int(sx + 3)):
                d = math.hypot(xx + 0.5 - sx, yy + 0.5 - sy)
                if d <= 1.4:
                    t = (xx + 0.5 - sx) + (yy + 0.5 - sy)
                    put(px, xx, yy, lerp(tone, (220, 218, 232), 0.35) if t < -0.7 else lerp(tone, (30, 28, 46), 0.4) if t > 0.8 else tone)
    return px


@style("koi", fps=12, cycle=1680, steps=5, prog_steps=40, rise=5.0, fall=1.5, update=_koi_update)
def koi(c):
    if c.saving:                                                      # saving: the tree by the pond goes through the seasons
        return _tree_px((0.75 + c.prog * 0.5) % 1.0, 0.3, c.tick // 2)
    ph, wq = c.extra if c.extra is not None else (c.tick % 240, c.tick % 24)
    return _koi_px(c.voice, c.tick, ph, wq, c.prog if c.saving else c.voice)


# ================================================================ FIREFLY JAR
# A jar of fireflies on a night-table. Quiet, they doze on the twig at the bottom, glowing faintly. Your voice
# wakes them one by one: the louder you are, the more of them lift off, swarm and glow, and the whole jar
# lights up the table. While saving, the fireflies light up one by one.

_FJ_REST = ((10, 22), (13, 23), (16, 22), (19, 23), (21, 21), (11, 19), (14, 20), (18, 19), (20, 18), (12, 16))


def _fj_hw(y):
    return {5: 5, 6: 5, 7: 6, 8: 7, 24: 6, 25: 4}.get(y, 7 if 9 <= y <= 23 else -1)


def _firefly_px(voice, tick, saving, prog):
    px = blank()
    tau = tick / 60.0
    a = 0.75 if saving else smoothstep(0.08, 0.45, voice)
    flies = []
    for i in range(10):
        ph = (i * 0.37) % 1.0
        k1, k2, k3 = 1 + i % 3, 1 + (i * 2) % 3, 2 + i % 2
        sx = 15.0 + 5.0 * math.sin(2 * math.pi * (k1 * tau + ph))
        sy = 14.5 + 6.0 * math.sin(2 * math.pi * (k2 * tau + ph * 1.7))
        rx, ry = _FJ_REST[i]
        x, y = rx + (sx - rx) * a, ry + (sy - ry) * a
        x, y = max(9.5, min(20.5, x)), max(8.5, min(22.5, y))
        if saving:
            lvl = clamp01(prog * 10.0 - i)
        else:
            lvl = clamp01(voice * 11.0 - i + 0.5)
        blink = 0.55 + 0.45 * math.sin(2 * math.pi * (k3 * tau + ph * 2.3))
        b = lvl * blink if lvl > 0.0 else 0.0
        if not saving or lvl <= 0.0:
            b = max(b, 0.10 + 0.05 * math.sin(2 * math.pi * (tau + ph)))   # asleep, but still faintly alive
        flies.append((x, y, b, lvl))
    light = {}
    for x, y, b, lvl in flies:
        for yy in range(int(y) - 6, int(y) + 7):
            for xx in range(int(x) - 6, int(x) + 7):
                d = math.hypot(xx + 0.5 - x, yy + 0.5 - y)
                if d < 6.0 and 0 <= xx < W and 0 <= yy < H:
                    light[(xx, yy)] = light.get((xx, yy), 0.0) + b * (1.0 - d / 6.0) ** 2
    total = sum(f[2] for f in flies)
    for y in range(26, H):                                            # the table, lit by the jar
        for x in range(W):
            base = (96, 62, 36) if y == 26 else (66, 42, 26)
            if (x * 7 + y * 3) % 11 == 0:
                base = lerp(base, (40, 24, 16), 0.5)
            px[y][x] = lerp(base, (150, 220, 90), clamp01(0.5 * total / 10.0 * math.exp(-((x - 15.0) / 10.0) ** 2) + light.get((x, y), 0.0) * 0.5))
    inner = {}
    for y in range(5, 26):
        hw = _fj_hw(y)
        for x in range(15 - hw, 16 + hw):
            inner[(x, y)] = True
    for (x, y) in inner:
        hw = _fj_hw(y)
        edge = x in (15 - hw, 15 + hw) or y == 25 or (y == 5)
        L = clamp01(light.get((x, y), 0.0) * 0.9)
        if edge:
            col = lerp((150, 200, 214) if x <= 15 else (66, 108, 126), (200, 255, 150), L * 0.5)
        else:
            col = lerp((14, 24, 30), (134, 214, 84), L)
        px[y][x] = col
    for x, y in ((11, 24), (12, 24), (13, 23), (14, 22), (15, 21), (16, 20), (17, 19), (18, 18)):   # a twig to sleep on
        put(px, x, y, (96, 66, 44) if (x + y) % 2 else (68, 46, 32))
    for x, y in ((12, 22), (13, 22), (17, 21), (18, 21), (18, 17), (19, 17), (15, 19)):                # its leaves
        if (x, y) in inner:
            put(px, x, y, (52, 120, 66))
    for x in range(9, 22):                                            # a few blades of grass at the bottom
        if hash01(x, 3, 5) < 0.45 and (x, 24) in inner and (x, 25) in inner:
            put(px, x, 24, (30, 82, 50))
            if hash01(x, 4, 5) < 0.4:
                put(px, x, 23, (44, 108, 60))
    for x, y, b, lvl in flies:
        xi, yi = int(x), int(y)
        body = lerp((70, 82, 44), (226, 255, 96), clamp01(b * 1.4))
        put(px, xi, yi, body)
        if a > 0.5 and lvl > 0.0:
            put(px, xi - 1, yi - 1, lerp(px[yi - 1][xi - 1] or VOID, (210, 236, 240), 0.55))     # wings
            put(px, xi + 1, yi - 1, lerp(px[yi - 1][xi + 1] or VOID, (210, 236, 240), 0.55))
        if b > 0.45:
            put(px, xi, yi + 1, lerp(px[yi + 1][xi] or VOID, (255, 255, 160), 0.7))             # the glowing tail
    for y in range(10, 17):                                           # the glint on the glass
        put(px, 15 - 7 + 2, y, lerp(px[y][15 - 7 + 2] or VOID, (220, 244, 252), 0.45))
    for y in (19, 20):
        put(px, 15 - 7 + 2, y, lerp(px[y][15 - 7 + 2] or VOID, (220, 244, 252), 0.3))
    for y, cols in ((2, ((250, 214, 120), (236, 186, 80))), (3, ((220, 166, 66), (190, 134, 48))), (4, ((140, 96, 38), (110, 76, 30)))):   # the lid
        for x in range(9, 22):
            c = cols[0] if x < 15 else cols[1]
            if y == 3 and x % 2 == 0:
                c = lerp(c, (110, 76, 30), 0.35)                  # the ridges
            put(px, x, y, c)
    for (x, y), L in light.items():                                   # the glow that escapes the jar
        if px[y][x] is None and L > 0.04:
            px[y][x] = lerp(VOID, (110, 180, 60), clamp01(L * 0.55))
    return px


@style("firefly jar", fps=10, cycle=60, steps=10, prog_steps=20, rise=4.0, fall=1.2)
def firefly(c):
    return _firefly_px(c.prog if c.saving else c.voice, c.tick, c.saving, c.prog)


# ================================================================ LIGHTHOUSE
# A lighthouse on its rock at night. Quiet, the lamp is dim, the beam creeps round slowly and the sea is calm.
# Your voice powers the lamp: the beam brightens, sweeps faster and the swell rises and breaks on the rock.
# While saving, the tower's stripes light up from the bottom.

def _lh_update(mem, dt, voice, active, prog, elapsed, now):
    v = clamp01(voice)
    mem["bp"] = (mem.get("bp", 0.0) + dt * (0.08 + 0.8 * v)) % 1.0
    mem["sp"] = (mem.get("sp", 0.0) + dt * (0.2 + 1.2 * v)) % 1.0
    return (int(mem["bp"] * 48) % 48, int(mem["sp"] * 28) % 28)


def _lighthouse_px(voice, bq, sq, tick, fill):
    px = blank()
    v = clamp01(voice)
    bl = 0.35 + 0.65 * smoothstep(0.0, 0.6, v)
    for i in range(14):                                               # stars, twinkling
        x, y = (i * 17 + 4) % 31, (i * 7 + 2) % 17
        if abs(x - 15) < 6 and y < 10:
            continue
        tw = 0.45 + 0.35 * math.sin(2 * math.pi * ((1 + i % 3) * tick / 24.0 + i * 0.37))
        px[y][x] = lerp(VOID, (210, 220, 255), tw)
    disc(px, 4.5, 4.5, 2.6, (236, 236, 214))                          # the moon, with a bite taken out of it
    disc(px, 5.9, 3.7, 2.3, (10, 10, 10))
    glow(px, 4.5, 4.5, 7.0, 7.0, (200, 210, 255), 0.12)
    for x in range(W):                                                # the sea, with a lit horizon
        for y in range(23, H):
            px[y][x] = lerp((26, 62, 118), (8, 22, 56), (y - 23) / 5.0) if y > 23 else (66, 116, 176)
    for y in range(24, H):                                            # wave crests slide along, faster near you and when loud
        n = 2 + int(3 * v)
        for k in range(n):
            drift = sq / 28.0 * 31.0 * (1 + (y - 24) % 3) * 0.5 * (-1 if y % 2 else 1)
            x0 = int(k * 31 / n + y * 5 + drift) % 31
            bob = int(round(math.sin(2 * math.pi * (sq / 28.0 * 2 + k * 0.3 + y * 0.2)) * (0.3 + 0.7 * v)))
            for dx in range(3 if (k + y) % 2 else 2):
                if 0 <= x0 + dx < W and 24 <= y + bob < H:
                    px[y + bob][x0 + dx] = (110, 170, 224) if dx == 0 else (80, 140, 200)
    for y, hw in zip(range(22, 27), (6, 8, 10, 12, 14)):              # the rock the tower stands on
        for x in range(15 - hw, 16 + hw):
            t = (x - (15 - hw)) / max(1.0, 2.0 * hw)
            col = lerp((132, 118, 138), (46, 40, 62), t * 0.85 + (y - 22) * 0.05)
            if hash01(x, y, 3) < 0.14:
                col = lerp(col, (28, 24, 42), 0.5)
            if y == 22:
                col = lerp(col, (176, 166, 176), 0.4)
            put(px, x, y, col)
    for x in range(W):                                                # surf bursting up the foot of the rock
        pass
    for side in (-1, 1):
        for k in range(3):
            h = 0.5 + 0.5 * math.sin(2 * math.pi * (sq / 28.0 * 2 + k * 0.33 + (side > 0) * 0.5))
            rise = int(h * (0.6 + 2.8 * v) + 0.4)
            bx = 15 + side * (13 - k * 3) - (1 if side < 0 else 0)
            for r in range(rise):
                for dx in (0, 1):
                    xx, yy = bx + dx - (1 if r else 0) * side, 26 - r - (k % 2)
                    if hash01(xx, yy, tick // 2) < 0.8 - 0.15 * r:
                        put(px, xx, yy, (226, 242, 255) if r < 2 else (170, 210, 242))
    for y in range(8, 23):                                            # the tower, in five bands
        hw = int(round(3.0 + (y - 8) / 14.0 * 2.0))
        band = (y - 8) // 3                                           # 0 top .. 4 bottom
        kb = 4 - band                                                 # 0 bottom
        red = band % 2 == 0
        lit = fill is None or kb < fill * 5 / 4.0
        for x in range(15 - hw, 16 + hw):
            u = (x - 15) / max(1.0, float(hw))
            if lit:
                base = (214, 52, 56) if red else (242, 236, 226)
            else:
                base = (58, 62, 86) if red else (84, 88, 112)
            col = lerp(base, (255, 255, 255), 0.25 * max(0.0, -u)) if u < 0.2 else lerp(base, (24, 20, 40), 0.42 * u)
            if x in (15 - hw, 15 + hw):
                col = lerp(col, (30, 22, 40), 0.32)
            put(px, x, y, col)
    for y in (13, 17):                                                # little windows
        put(px, 15, y, (28, 30, 52))
        put(px, 15, y + 1, (28, 30, 52))
    for y in (20, 21, 22):
        put(px, 15, y, (60, 40, 40))
        put(px, 14 if y > 20 else 15, y, (60, 40, 40))
    for x in range(10, 21):                                           # the gallery and its railing
        put(px, x, 7, (66, 70, 92))
        if x in (10, 12, 15, 18, 20):
            put(px, x, 6, (116, 120, 140))
    for y in (4, 5, 6):                                               # the lamp room
        for x in range(13, 18):
            glass = 13 < x < 17
            put(px, x, y, lerp((92, 84, 60), (255, 240, 160), bl) if glass else (70, 74, 96))
    for x0, hw, y, c in ((15, 3, 3, (160, 36, 42)), (15, 2, 2, (200, 54, 54)), (15, 1, 1, (214, 62, 62))):
        for x in range(x0 - hw, x0 + hw + 1):
            put(px, x, y, c if x <= 15 else lerp(c, (60, 14, 24), 0.4))
    put(px, 15, 0, (236, 206, 120))
    psi = bq / 48.0 * 2 * math.pi
    cosp = math.cos(psi)
    for sgn in (1, -1):
        c = cosp * sgn
        if c < 0.04:
            continue
        Lb = 17.0 * c * (0.5 + 0.5 * bl)
        for x in range(W):
            t = (x - 15.5) * sgn
            if t <= 1.0 or t > Lb:
                continue
            yc = 5.5 + 0.06 * t
            wd = 0.8 + 0.12 * t
            for y in range(int(yc - wd) - 1, int(yc + wd) + 2):
                d = abs(y + 0.5 - yc)
                if d <= wd and 0 <= y < H:
                    a = bl * 0.7 * (1.0 - t / Lb) ** 0.6 * (1.0 - 0.4 * d / wd)
                    blend(px, x, y, (255, 240, 170), a)
    glow(px, 15.5, 5.5, 5.0 + 2.0 * bl, 5.0 + 2.0 * bl, (255, 236, 150), 0.25 + 0.55 * bl)
    if abs(cosp) < 0.35:                                              # the beam swings toward you: a flare
        f = (0.35 - abs(cosp)) / 0.35
        for k in range(1, 3 + int(3 * bl)):
            for dx, dy in ((k, 0), (-k, 0), (0, k), (0, -k)):
                blend(px, 15 + dx, 5 + dy, (255, 250, 200), f * (1.0 - k / 6.0))
    return px


@style("lighthouse", fps=12, cycle=24, steps=8, prog_steps=20, rise=4.0, fall=1.5, update=_lh_update)
def lighthouse(c):
    if c.saving:
        return _lighthouse_px(0.7, (c.tick * 3) % 48, (c.tick * 28 // 24) % 28, c.tick, c.prog * 4.0)
    return _lighthouse_px(c.voice, c.extra[0], c.extra[1], c.tick, None)


# ================================================================ HOT-AIR BALLOON
# A balloon in a pale sky. Quiet, it hovers and the clouds drift by. Your voice is the burner: the flame
# roars, the balloon climbs and the clouds stream down past faster and faster. While saving, it lifts off
# the ground and climbs out of the hills.

_HB_HW = (3, 5, 6, 7, 7, 7, 7, 6, 6, 5, 4, 3)
_HB_GORE = ((222, 60, 62), (250, 230, 190), (46, 150, 164), (250, 230, 190), (222, 60, 62))


def _balloon_update(mem, dt, voice, active, prog, elapsed, now):
    mem["sc"] = (mem.get("sc", 0.0) + dt * (0.8 + 11.0 * clamp01(voice))) % 112.0
    return int(mem["sc"]) % 112


def _cloud(px, cx, cy, s, col, shade, a):
    for dx, dy, r in ((0, 0, 1.6), (1.7, 0.4, 1.3), (-1.7, 0.5, 1.2), (0.6, -0.8, 1.2), (3.0, 1.0, 0.9), (-3.0, 1.0, 0.8)):
        disc(px, cx + dx * s, cy + dy * s, r * s, col, a)
    for dx in range(-int(3.4 * s), int(3.4 * s) + 1):                 # a shaded underside
        blend(px, int(cx + dx), int(cy + 1.8 * s), shade, a * 0.7)


def _balloon_px(voice, sc, tick, y0, ground):
    px = blank()
    v = clamp01(voice)
    glow(px, 15.5, 12.0, 17.0, 15.0, (70, 100, 170), 0.22)
    for i in range(8):                                                # clouds: small far ones, big near ones
        near = i % 2
        k = 1.0 if near else 0.5
        cx = (i * 11 + 4) % 31
        y = (i * 9 + sc * k) % 56 - 14
        _cloud(px, cx, y, 1.5 if near else 0.9, (214, 228, 250) if near else (150, 172, 222), (150, 170, 214) if near else (110, 130, 182), 0.95 if near else 0.5)
    if ground is not None:                                            # the hills, falling away as it climbs
        for x in range(W):
            top = int(ground + 2.0 * math.sin(x * 0.35) + 1.2 * math.sin(x * 0.9 + 1.0))
            for y in range(max(0, top), H):
                px[y][x] = lerp((86, 170, 96), (40, 100, 70), clamp01((y - top) / 4.0)) if y > top else (140, 214, 120)
        for x in (4, 9, 24, 27):
            ty = int(ground + 2.0 * math.sin(x * 0.35) + 1.2 * math.sin(x * 0.9 + 1.0)) - 1
            put(px, x, ty, (30, 90, 60))
            put(px, x, ty - 1, (44, 120, 70))
    bob = int(round(0.7 * math.sin(2 * math.pi * tick / 24.0)))
    y0 = int(round(y0)) + bob
    fl = v
    flick = (0, 1, 0, -1)[tick % 4]
    glow(px, 15.5, y0 + 6.0, 11.0, 9.0, (255, 170, 80), 0.10 + 0.35 * fl)
    for r, hw in enumerate(_HB_HW):                                   # the envelope
        y = y0 + r
        for x in range(15 - hw, 16 + hw):
            u = (x - 15) / float(hw) if hw else 0.0
            g = max(0, min(4, int((u + 1.0) / 2.0 * 5.0)))
            col = _HB_GORE[g]
            col = lerp(col, (255, 255, 255), 0.30 * max(0.0, -u - 0.1)) if u < 0 else lerp(col, (30, 20, 40), 0.40 * u)
            if r >= 8:
                col = lerp(col, (255, 170, 70), 0.30 * fl)           # the burner lights the underside
            if r == 8:
                col = (240, 196, 90)                                  # a gold band round the middle
            if x in (15 - hw, 15 + hw) or r == 0:
                col = lerp(col, (50, 22, 40), 0.55)
            put(px, x, y, col)
    for x in range(13, 18):                                           # the open mouth of the envelope
        put(px, x, y0 + 12, (60, 26, 40))
    for k in range(4):                                                # ropes
        put(px, 12 - (k > 1), y0 + 13 + k, (210, 190, 150))
        put(px, 18 + (k > 1), y0 + 13 + k, (210, 190, 150))
    fh = 1 + int(round(3.2 * fl)) + (flick if fl > 0.1 else 0)
    for k in range(fh):                                               # the flame, between the ropes
        y = y0 + 12 - k + 2
        put(px, 15, y, (255, 250, 220) if k < 2 else (255, 214, 110))
        if k < fh - 1 and fh > 2:
            put(px, 14, y + 1 if k else y, (255, 150, 50))
            put(px, 16, y + 1 if k else y, (255, 150, 50))
    for x in (14, 15, 16):
        put(px, x, y0 + 14, (190, 150, 70))                           # the brass burner
    for y in range(4):                                                # the wicker basket
        hw = 4 if y == 0 else 3
        for x in range(15 - hw, 16 + hw):
            col = (170, 118, 66) if (x + y) % 2 else (128, 86, 48)
            if y == 0:
                col = (200, 150, 84)
            if y == 3:
                col = (84, 56, 34)
            put(px, x, y0 + 16 + y, col)
    pole = y0 - 3                                                     # a little flag on top
    put(px, 15, y0 - 1, (150, 120, 90))
    put(px, 15, y0 - 2, (150, 120, 90))
    for k in range(1, 4):
        fy = int(round(math.sin(2 * math.pi * (tick / 8.0 - k * 0.2)) * (0.5 + 1.5 * v)))
        put(px, 15 + k, y0 - 3 + fy + (1 if k == 3 else 0), (240, 80, 70))
    return px


@style("balloon", fps=10, cycle=112, steps=8, prog_steps=20, rise=4.0, fall=1.5, update=_balloon_update)
def balloon(c):
    if c.saving:
        return _balloon_px(0.8, c.tick, c.tick, 8.0 - 5.0 * c.prog, 22.0 + c.prog * 14.0)
    return _balloon_px(c.voice, c.extra, c.tick, 6.0 - 3.0 * c.voice, None)


# ================================================================ AURORA
# The northern lights over a frozen lake. Quiet, one faint green ribbon drifts low. Your voice feeds the sky:
# the curtains rise, brighten, ripple faster and turn violet and pink, and shooting stars cross the sky. The
# lake mirrors everything. While saving, the aurora spreads across the sky from left to right.

def _aurora_update(mem, dt, voice, active, prog, elapsed, now):
    v = clamp01(voice)
    mem["ph"] = (mem.get("ph", 0.0) + dt * (0.05 + 0.55 * v)) % 1.0
    S = smoothstep(0.45, 0.8, mem.get("e", 0.0))
    _fx_step(mem, dt, v, now, 0.5 + 4.5 * v, 1.2 + 6.0 * S)          # p2: the fast rays, p3: the twinkle of the stars
    hq = bool(mem.get("_hq"))
    if hq:
        return (mem["ph"] * 48.0,) + _fx_out(mem, True)                # smooth: the ribbons move in fine steps
    return (int(mem["ph"] * 48) % 48,) + _fx_out(mem, False)


def _aurora_px(voice, phq, tick, cover, fx=_FX0):
    """Soft: one faint green ribbon. Medium: a second teal ribbon and a slow shimmer. Loud: tall curtains with fast
    vertical rays and a pinkish-white fringe. Peaks: a shooting star and the whole sky brightens. Sustained loud: faster
    twinkling stars. The lake ripples more as the volume rises."""
    e, pe, pa, ps, p2, p3, pid = fx
    px = blank()
    v = clamp01(voice)
    ph = phq / 48.0 * 2 * math.pi
    a = 0.14 + 0.86 * v
    m = smoothstep(0.18, 0.42, v)
    L = smoothstep(0.5, 0.85, v)
    S = smoothstep(0.45, 0.8, e)
    for i in range(22):
        x, y = (i * 13 + 5) % 31, (i * 7 + 1) % 17
        tw = 0.3 + 0.35 * math.sin((1 + i % 3) * p3 + i * 2.3)
        px[y][x] = lerp(VOID, (220, 230, 255), tw)
    if pe > 0.02:                                                     # a peak: the whole sky brightens for a moment
        for y in range(0, 19):
            for x in range(W):
                blend(px, x, y, (60, 150, 140), 0.16 * pe * (1.0 - y / 26.0))
    for layer in (0, 1):
        fade2 = 1.0 if layer == 0 else m                              # the second curtain fades in instead of popping on
        if fade2 <= 0.0:
            continue
        for x in range(W):
            pl = ph * (1.0 if layer == 0 else -1.0) + layer * 2.0
            yb = (15.0 if layer == 0 else 11.0) + 1.8 * math.sin(0.21 * x + pl) + 1.1 * math.sin(0.47 * x - pl + layer)
            hgt = (4.0 + 10.0 * a) * (0.72 + 0.28 * math.sin(0.3 * x + pl + 1.0)) * (1.0 if layer == 0 else 0.62)
            hgt *= 1.0 + 0.22 * L
            ray = 0.72 + 0.28 * math.sin(2.3 * x + 1.5 * pl) * math.sin(0.9 * x - pl)
            rayf = 0.45 + 0.55 * abs(math.sin(1.7 * x + 2.0 * p2 + layer * 1.3))      # loud: fast vertical rays
            ray = ray * (1.0 - L) + rayf * L
            shim = 1.0 + 0.16 * m * math.sin(1.1 * x + 3.0 * ph + layer)              # medium: a slow shimmer
            mk = 1.0 if cover is None else clamp01((cover * 34.0 - x) / 3.0)
            mk *= fade2
            if mk <= 0.0:
                continue
            for y in range(max(0, int(yb - hgt) - 1), int(yb) + 3):
                t = (yb - y) / max(0.1, hgt)
                if t > 1.0:
                    continue
                streak = 1.0 - 0.18 * L * (0.5 + 0.5 * math.sin(y * 1.9 - 3.0 * p2))
                if t < 0.0:
                    inten = max(0.0, 1.0 + t * 0.9) * ray * (0.3 + 0.7 * a) * mk * 0.6     # it fades softly below its lower edge
                else:
                    inten = (1.0 - t) ** 0.8 * ray * (0.35 + 0.75 * a) * mk
                    if t < 0.15:
                        inten = min(1.0, inten * 1.35)
                inten *= shim * streak
                tc = max(0.0, t)
                if layer == 0:
                    col = lerp((90, 255, 160), (55, 225, 195), min(1.0, tc * 1.2))        # green, shading to teal
                else:
                    col = lerp((60, 225, 205), (135, 140, 255), 0.5 * tc)                 # teal, with a touch of violet on top
                col = lerp(col, (250, 190, 235), 0.5 * L * smoothstep(0.7, 1.0, tc))      # loud: a pinkish fringe ...
                col = lerp(col, (255, 255, 255), 0.25 * L * smoothstep(0.85, 1.0, tc))    # ... and white at the very top
                blend(px, x, y, col, clamp01(inten * 0.95))
    for x in range(W):                                                # two ridges of mountains, the near one with pines
        yr = int(19 + 2.2 * math.sin(0.35 * x + 1.2) + 1.5 * math.sin(0.9 * x))
        for y in range(yr, 22):
            px[y][x] = (48, 60, 104) if y == yr else (24, 30, 62)
    for x in range(W):
        yn = int(21.2 + 1.2 * math.sin(0.5 * x + 0.3))
        for y in range(yn, 22):
            px[y][x] = (8, 12, 24)
    for x0, h in ((2, 4), (5, 5), (8, 3), (23, 4), (26, 5), (29, 4)):
        for k in range(h):
            for dx in range(-(k // 2), k // 2 + 1):
                put(px, x0 + dx, 21 - h + k, (6, 12, 20))
    amp = 0.6 + 1.6 * v                                               # the lake mirrors the sky and ripples more when loud
    for y in range(22, H):
        k = y - 22
        src = 21 - 2 * k
        sh = int(round(amp * math.sin(y * 1.3 + ph)))
        for x in range(W):
            base = (6, 14, 28)
            c = px[src][min(W - 1, max(0, x + sh))] if src >= 0 else None
            px[y][x] = base if c is None else lerp(base, c, 0.5 - 0.05 * k)
    if pa < 0.9 and pe > 0.02 and ps > 0.5:                           # a peak: a shooting star streaks across
        u = pa / 0.9
        hx = 30.0 - (pid * 5) % 7 - u * 34.0
        hy = 1.0 + (pid * 3) % 4 + u * 12.0
        for k in range(7):
            blend(px, int(hx + k * 0.95), int(hy - k * 0.32), (255, 255, 255), (1.0 - k * 0.14) * (1.0 - 0.5 * u))
    return px


@style("aurora", fps=10, cycle=48, steps=8, prog_steps=31, rise=4.0, fall=1.2, update=_aurora_update, smooth=True)
def aurora(c):
    if c.saving:
        return _aurora_px(0.75, (c.tick * 1) % 48, c.tick, c.prog, (0.0, 0.0, 9.0, 0.0, 0.0, c.tick / 24.0 * _TAU, 0))
    return _aurora_px(c.voice, c.extra[0], c.tick, None, _fx_of(c.extra))


# ================================================================ WINDMILL
# A brick windmill at dusk. Quiet, the sails turn lazily. Your voice is the wind: streaks of air rush past and
# the sails whirl faster the louder you are. While saving, the mill grinds and a pile of golden grain grows.

def _windmill_update(mem, dt, voice, active, prog, elapsed, now):
    dt = max(0.0, min(0.25, dt))
    v = clamp01(voice)
    _fx_step(mem, dt, v, now, 1.0, 1.5 + 5.0 * v)                     # p3: the sway of the grass
    om = mem.get("om", 0.03)
    if mem.get("pid", 0) != mem.get("pid_om", 0):                     # a peak is a gust: the sails jolt
        mem["pid_om"] = mem.get("pid", 0)
        om += 0.30 * mem.get("ps", 0.5)
    tgt = 0.03 + 0.75 * v
    om += (tgt - om) * (1.0 - math.exp(-dt * (2.2 if tgt > om else 0.5)))     # inertia: speeds up fast, coasts down slowly
    om = max(0.0, min(1.05, om))
    mem["om"] = om
    mem["th"] = (mem.get("th", 0.0) + dt * om) % 1.0
    mem["wd"] = (mem.get("wd", 0.0) + dt * (2.0 + 70.0 * max(v, om * 0.8) ** 1.5)) % 62.0
    mem["cl"] = (mem.get("cl", 0.0) + dt * (0.6 + 26.0 * v * v)) % 50.0
    if mem.get("_hq"):                                                # smooth: the sails turn in fine steps
        return (mem["th"] * 72.0, mem["wd"], mem["cl"], om) + _fx_out(mem, True)
    return (int(mem["th"] * 72) % 72, int(mem["wd"]) % 62, int(mem["cl"]) % 50, round(om * 10) / 10.0) + _fx_out(mem, False)


def _windmill_px(voice, thq, wd, grain, cl=0.0, om=0.0, fx=_FX0):
    """Soft: slow sails. Medium: leaves blow past and the grass sways. Loud: the sails smear into motion trails
    and clouds race by. Peaks: a gust (the sails jolt, a burst of leaves). Sustained loud: the loudest peaks flash
    lightning. The sails have inertia (see _windmill_update)."""
    e, pe, pa, ps, p2, p3, pid = fx
    px = blank()
    v = clamp01(voice)
    m = smoothstep(0.18, 0.45, v)
    L = smoothstep(0.5, 0.85, v)
    S = smoothstep(0.45, 0.8, e)
    B = smoothstep(0.4, 0.75, om)                                     # how blurred the sails are (from their real speed)
    glow(px, 15.5, 27.0, 24.0, 17.0, (255, 150, 80), 0.26)
    a_c = smoothstep(0.3, 0.65, v)
    if a_c > 0.02:                                                    # clouds race by
        for x0, cy_, s_ in ((4, 3.5, 1.0), (24, 7.0, 0.8), (40, 5.0, 1.2)):
            _cloud(px, (x0 + cl) % 50 - 12.0, cy_, s_, (110, 104, 134), (74, 68, 100), a_c * 0.6)
    a_w = smoothstep(0.1, 0.4, v) * 0.7
    if a_w > 0.0:                                                     # streaks of wind: longer and more of them the faster it blows
        for i in range(12):
            if i >= 7 and L < (i - 6) / 6.0:
                continue
            y = (i * 5 + 3) % 22 + 1
            x0 = (i * 13 + wd * (1 + i % 2)) % 62 - 15
            n = 3 + i % 3 + int(10 * L)
            for dx in range(n):
                blend(px, int(x0) + dx, y, (200, 218, 248), a_w * (0.25 + 0.75 * dx / float(n)))
    if m > 0.02:                                                      # leaves and dust blowing past
        for i in range(7):
            x = (i * 13 + 7 + wd) % 62 - 15
            y = 4 + (i * 4) % 17 + 1.6 * math.sin(x * 0.45 + i)
            c = ((170, 120, 50), (120, 146, 60), (205, 150, 62))[i % 3]
            blend(px, int(x), int(y), c, m * 0.95)
            if i % 2 == 0:
                blend(px, int(x) + 1, int(y), c, m * 0.6)
    for y in range(25, H):                                            # the grassy ground
        for x in range(W):
            px[y][x] = lerp((88, 150, 70), (36, 84, 56), (y - 25) / 3.0) if y > 25 else (126, 190, 90)
    for x, y, c in ((3, 26, (244, 90, 90)), (6, 27, (255, 220, 100)), (25, 27, (244, 90, 90)), (28, 26, (255, 220, 100)), (9, 26, (255, 255, 255))):
        put(px, x, y, c)
    for y in range(13, 26):                                           # the brick tower
        hw = int(round(4.0 + (y - 13) / 12.0 * 2.0))
        for x in range(15 - hw, 16 + hw):
            u = (x - 15) / float(hw)
            brick = (194, 100, 72) if (x + (y % 2) * 2) % 4 else (150, 72, 56)
            col = lerp(brick, (255, 220, 180), 0.22 * max(0.0, -u)) if u < 0.1 else lerp(brick, (40, 20, 30), 0.42 * u)
            if x in (15 - hw, 15 + hw):
                col = lerp(col, (50, 24, 32), 0.3)
            put(px, x, y, col)
    for y in (22, 23, 24, 25):                                        # the door
        for x in (14, 15, 16):
            if not (y == 22 and x != 15):
                put(px, x, y, (58, 34, 30))
    for x, y in ((14, 17), (15, 17), (14, 18), (15, 18)):             # a lit window
        put(px, x, y, (255, 214, 110) if y == 18 else (255, 238, 160))
    for x in range(9, 22):                                            # the wooden gallery
        put(px, x, 14 if 10 < x < 20 else 15, (100, 66, 44))
    for r, (hw, c) in enumerate(((5, (120, 80, 60)), (4, (150, 100, 74)), (3, (170, 118, 84)), (2, (190, 132, 94)))):   # the cap
        for x in range(15 - hw, 16 + hw):
            put(px, x, 12 - r, lerp(c, (30, 16, 24), 0.3) if x > 15 else c)
    sw = 0.5 + 1.2 * m                                                # the grass sways in the wind
    for x in range(W):
        if px[24][x] is None and x % 2 == 0:
            d = int(round(sw * math.sin(p3 + x * 0.5)))
            if 0 <= x + d < W and px[24][x + d] is None:
                px[24][x + d] = (96, 170, 80)
                if x % 3 == 0:
                    put(px, x + d + (1 if d > 0 else 0), 23, (140, 204, 100))
    th = thq / 72.0 * 2 * math.pi
    hx, hy = 15.0, 10.0
    sa = 1.0 - 0.3 * B                                                # the faster they turn, the less clear the sails are

    def sail(ang, alpha, detail):
        cs, sn = math.cos(ang), math.sin(ang)
        seen = set()
        r = 1.5
        while r <= 9.6:
            s = -0.4
            while s <= 3.4:
                if detail == 2 and not (r >= 3.0 and 0.5 <= s <= 3.0):
                    s += 0.35
                    continue
                x = int(math.floor(hx + 0.5 + r * cs - s * sn))
                y = int(math.floor(hy + 0.5 + r * sn + s * cs))
                if detail == 2:
                    col = (240, 230, 204)
                elif not detail:
                    col = (236, 226, 200) if r >= 3.0 and 0.5 <= s <= 3.0 else (150, 106, 70)
                elif s < 0.5:
                    col = (176, 128, 84) if s > -0.4 else (86, 58, 38)
                elif r < 3.0:
                    col = (120, 84, 56)
                else:
                    col = (250, 242, 218) if s < 2.2 else (232, 220, 190)
                    if (r - 3.0) % 2.4 < 0.4:
                        col = (120, 82, 54)                           # a cross-bar of the lattice
                    if s > 3.0:
                        col = (130, 92, 62)
                if alpha >= 0.99:
                    put(px, x, y, col)
                elif (x, y) not in seen:
                    seen.add((x, y))
                    blend(px, x, y, col, alpha)
                s += 0.35
            r += 0.35

    if B > 0.02:                                                      # smear frames: ghost copies trailing behind each sail
        span = min(1.1, om * _TAU * 0.15)                              # how far the sails travel during the smear
        nt = 12
        for j in range(nt, 0, -1):                                    # oldest, faintest copy first
            back = span * j / float(nt)
            for k in range(4):
                sail(th + k * math.pi / 2.0 - back, B * 0.07 * (1.0 - j / (nt + 1.0)) ** 1.3, 2)
    for k in range(4):
        sail(th + k * math.pi / 2.0, sa, B < 0.3)
    if B > 0.02:                                                      # speed lines: thin bright arcs streaming off every sail
        sp = min(1.15, om * _TAU * 0.16)
        for k in range(4):
            base = th + k * math.pi / 2.0
            for r_, s_, f_ in ((4.2, -0.6, 0.6), (6.4, -0.6, 1.0), (8.6, -0.6, 0.8)):
                rho, phi = math.hypot(r_, s_), math.atan2(s_, r_)
                n = max(4, int(sp * f_ * rho / 0.4))
                for i in range(n):
                    u = i / float(n)
                    ang = base + phi - u * sp * f_
                    blend(px, int(math.floor(hx + 0.5 + rho * math.cos(ang))), int(math.floor(hy + 0.5 + rho * math.sin(ang))),
                          (255, 250, 236), min(1.0, B * 1.7 * (1.0 - u)))
    disc(px, hx + 0.5, hy + 0.5, 1.5, (204, 160, 76))                 # the hub
    put(px, 14, 9, (255, 232, 160))
    if grain is not None:                                             # the grain pile
        h = int(round(grain * 7))
        for y in range(h):
            hw = (y + 0.7) * 1.4
            for x in range(int(24.5 - hw), int(24.5 + hw) + 1):
                if 20 <= x <= 30:
                    put(px, x, 25 - h + y + 1, (252, 206, 80) if x < 24 else (222, 160, 50))
    if pa < 1.3 and pe > 0.02:                                        # a peak: a burst of leaves flies across
        for i in range(14):
            t = pa - hash01(i, 2, pid) * 0.3
            if t < 0:
                continue
            x = -3.0 + t * (30.0 + 14.0 * hash01(i, 4, pid))
            y = 3.0 + 18.0 * hash01(i, 6, pid) + 2.2 * math.sin(t * 9.0 + i)
            c = ((170, 120, 50), (120, 146, 60), (205, 150, 62))[i % 3]
            if x < W:
                blend(px, int(x), int(y), c, 0.95 * (1.0 - t / 1.3))
                if i % 2:
                    blend(px, int(x) + 1, int(y), c, 0.6 * (1.0 - t / 1.3))
    if S > 0.3:
        if ps > 0.8 and pa < 0.4:                         # lightning on the loudest peaks
            f = (1.0 - pa / 0.4) ** 2 * clamp01((ps - 0.75) / 0.25)
            if f > 0.15:
                bx = 20 + (pid * 7) % 8
                for y in range(0, 12):
                    bx += int(hash01(y, 3, pid) * 3.0) - 1
                    put(px, bx, y, (255, 255, 255))
                    blend(px, bx + 1, y, (200, 210, 255), 0.5)
    return px


@style("windmill", fps=12, cycle=24, steps=8, prog_steps=28, rise=4.0, fall=1.5, update=_windmill_update, smooth=True)
def windmill(c):
    if c.saving:
        return _windmill_px(0.35, (c.tick * 3) % 72, (c.tick * 2) % 62, c.prog)
    x = c.extra
    return _windmill_px(c.voice, x[0], x[1], None, x[2], x[3], _fx_of(x))


# ================================================================ CAMPFIRE
# A campfire under the stars. Quiet, it is a few embers and a low flame. Your voice feeds it: it rears up
# in several tongues, throws sparks and lights up the clearing. While saving, the fire builds from embers
# to a roaring blaze.

def _campfire_update(mem, dt, voice, active, prog, elapsed, now):
    dt = max(0.0, min(0.25, dt))
    _fx_step(mem, dt, voice, now)
    S = smoothstep(0.4, 0.8, mem.get("e", 0.0))
    mem["sm"] = (mem.get("sm", 0.0) + dt * (0.16 + 0.34 * S)) % 1.0           # the smoke rises faster when the fire stays strong
    if mem.get("_hq"):
        return (mem["sm"],) + _fx_out(mem, True)
    return (int(mem["sm"] * 24) / 24.0,) + _fx_out(mem, False)


def _campfire_px(voice, tick, fx=_FX0, sm=0.0):
    """Soft: embers and one small flame. Medium: side flames and rising sparks. Loud: a tall roaring flame with a
    hot white core, the clearing lights up. Peaks: a pop that flares the fire and a burst of sparks. Sustained
    loud: a blue-white heart, glowing logs, and thicker smoke that rises higher."""
    e, pe, pa, ps, p2, p3, pid = fx
    px = blank()
    v = clamp01(voice)
    L = smoothstep(0.5, 0.85, v)
    S = smoothstep(0.4, 0.8, e)
    flick = 0.5 + 0.5 * math.sin(2 * math.pi * tick / 6.0) * math.sin(2 * math.pi * tick / 24.0 + 1.0)
    for i in range(12):
        x, y = (i * 17 + 3) % 31, (i * 5 + 1) % 10
        tw = 0.3 + 0.3 * math.sin(2 * math.pi * ((1 + i % 3) * tick / 24.0 + i * 0.3))
        px[y][x] = lerp(VOID, (210, 220, 255), tw * (1.0 - 0.5 * v))
    glow(px, 15.5, 21.0, 12.0 + 6.0 * v, 11.0 + 5.0 * v, (255, 130, 40), 0.22 + 0.4 * v + 0.06 * flick + 0.18 * L + 0.2 * pe)
    if L > 0.02:                                                      # the whole clearing lights up
        glow(px, 15.5, 20.0, 15.0 + 4.0 * L, 13.0 + 3.0 * L, (255, 120, 40), 0.16 * L)
    for y in range(24, H):                                            # the ground
        for x in range(W):
            px[y][x] = lerp((58, 40, 36), (22, 18, 26), (y - 24) / 4.0)
    for x in range(0, W, 3):
        put(px, x, 24, (40, 76, 52))
    if L > 0.02:                                                      # firelight on the ground
        for y in range(24, H):
            for x in range(W):
                px[y][x] = lerp(px[y][x], (160, 84, 40), 0.38 * L * max(0.0, 1.0 - abs(x - 15.5) / 16.0))
    hf = (3.0 + 14.5 * v ** 0.9 + 1.2 * flick) * (1.0 + 0.3 * pe)     # a peak pops: the flame flares up
    w0 = (2.6 + 3.0 * v) * (1.0 + 0.2 * pe)
    base = 22
    if S > 0.03:                                                      # smoke: thicker and higher the longer it stays loud
        top = base - hf * 0.9
        for i in range(9):
            wgt = clamp01(S * 9.0 - i + 0.5)
            if wgt <= 0.0:
                continue
            h = (sm + i / 9.0) % 1.0
            y = top - 1.0 - h * (3.0 + 11.0 * S)
            x = 15.5 + 1.2 * math.sin(2 * math.pi * (h * 1.3 + i * 0.37)) * (1.0 + h * 2.0) + (i % 3 - 1) * (0.6 + h * 1.8)
            if y >= 0:
                disc(px, x, y, 1.0 + 1.5 * h, (104, 98, 110), math.sin(math.pi * h) * 0.36 * wgt)
    tongues = [(0.0, 1.0, 1.0, 0.0)]
    g2, g3 = smoothstep(0.28, 0.42, v), smoothstep(0.62, 0.76, v)     # the side tongues rise out of the fire, they do not pop
    if g2 > 0.0:
        tongues += [(-3.0, 0.62 * g2, 0.7, 1.7), (3.2, 0.55 * g2, 0.7, 3.3)]
    if g3 > 0.0:
        tongues += [(-5.5, 0.35 * g3, 0.55, 5.0), (5.5, 0.38 * g3, 0.55, 2.4)]
    core = 0.38 + 0.12 * L
    for dxc, hs, ws, p0 in tongues:
        H_ = hf * hs
        if H_ < 1.0:
            continue
        for k in range(int(H_) + 1):
            t = k / max(1.0, H_)
            y = base - k
            hw = w0 * ws * (1.0 - t) ** 0.85 * (0.9 + 0.1 * math.sin(2 * math.pi * (tick / 6.0 + t * 1.5 + p0)))
            cx = 15.5 + dxc + (1.4 + 1.2 * v) * math.sin(2 * math.pi * (tick / 12.0 + t * 0.8 + p0)) * t ** 1.4
            for x in range(int(cx - hw) - 1, int(cx + hw) + 2):
                rr = abs(x + 0.5 - cx) / max(0.5, hw)
                if rr > 1.0:
                    continue
                if t > 0.78 and hash01(x, y, int(tick) % 5) < (t - 0.78) * 3.5:
                    continue
                d = rr + t * 0.55
                col = (255, 250, 210) if d < core else (255, 224, 96) if d < 0.62 else (255, 148, 36) if d < 0.9 else (226, 70, 24) if d < 1.2 else (150, 30, 24)
                if S > 0.0 and dxc == 0.0 and t < 0.4 and d < 0.55:      # sustained loud: the heart turns blue-white
                    col = lerp(col, (170, 205, 255), S * 0.85 * (1.0 - t / 0.4) * (1.0 - d / 0.55))
                put(px, x, y, col)
    lg = 0.55 * S                                                     # the logs glow brighter the longer it burns
    for x0, y0, x1, y1 in ((7, 25, 22, 22), (23, 25, 8, 22)):         # two crossed logs
        for off, c in ((0, (126, 82, 50)), (1, (86, 54, 36)), (-1, (160, 108, 66))):
            line(px, x0, y0 + off - 1, x1, y1 + off - 1, lerp(c, (255, 150, 60), lg) if lg > 0 else c)
    for x, y in ((8, 22), (22, 22), (7, 25), (23, 25)):               # the sawn ends
        put(px, x, y, lerp((206, 156, 100), (255, 190, 90), lg))
    for i in range(7):                                                # embers glowing between the logs
        x, y = 11 + (i * 5) % 9, 23 + (i % 2)
        put(px, x, y, lerp((120, 30, 20), (255, 170, 50), min(1.0, 0.5 + 0.5 * math.sin(2 * math.pi * (tick / 8.0 + i * 0.4)) + 0.3 * S)))
    for i, x0 in enumerate((3, 6, 9, 21, 24, 27)):                    # a ring of stones
        y0 = 26 + i % 2
        for dx, c in ((0, (150, 144, 160)), (1, (176, 170, 186)), (2, (110, 104, 124))):
            put(px, x0 + dx, y0, lerp(c, (255, 140, 60), 0.18 + 0.3 * v))
        put(px, x0 + 1, y0 + 1, (70, 64, 84))
    for i in range(16):                                               # sparks and embers floating up (each fades in, none pops)
        wgt = clamp01(2.0 + 14.0 * v - i)
        if wgt < 0.05:
            continue
        h = ((tick + i * 7) % 24) / 24.0
        dirn = ((i * 7) % 11 - 5) / 5.0
        x = 15.5 + dirn * (2.0 + 6.0 * h) * (0.5 + v) + 1.2 * math.sin(2 * math.pi * (h * 2.0 + i * 0.3))
        y = 20.0 - h * (7.0 + 14.0 * v)
        if h > 0.92:
            continue
        col = ramp(((255, 240, 150), (255, 150, 40), (150, 40, 28)), h)
        ix, iy = int(round(x)), int(round(y))
        if 0 <= iy < H and 0 <= ix < W and (px[iy][ix] is None or hash01(i, 2, 3) < 0.4):
            px[iy][ix] = lerp(VOID, col, wgt) if px[iy][ix] is None else lerp(px[iy][ix], col, wgt)
    if pa < 1.4 and pe > 0.02:                                        # a peak: a burst of sparks thrown up and out
        life = 1.4
        for i in range(10 + int(10 * ps)):
            vx = (hash01(i, 1, pid) - 0.5) * 2.0 * (9.0 + 8.0 * ps)
            vy = 11.0 + 14.0 * hash01(i, 2, pid) + 6.0 * ps
            x = 15.5 + vx * pa
            y = 19.0 - (vy * pa - 17.0 * pa * pa)
            if 0 <= y < 24 and 0 <= x < W:
                u = pa / life
                col = ramp(((255, 250, 200), (255, 170, 50), (170, 50, 30)), u)
                blend(px, int(x), int(y), col, 1.0 - u * 0.8)
    return px


@style("campfire", fps=12, cycle=24, steps=8, prog_steps=20, rise=4.0, fall=1.2, update=_campfire_update, smooth=True)
def campfire(c):
    if c.saving:
        return _campfire_px(c.prog, c.tick)
    return _campfire_px(c.voice, c.tick, _fx_of(c.extra), c.extra[0])
