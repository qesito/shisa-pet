"""A lock screen with Shisa napping on a cloud, shown through i3lock-color.

`shisa-pet --lock` paints the scene to a PNG the size of the screen (cached
until the screen size or this code changes) and then becomes i3lock, so
xss-lock can wait on it like any other locker.
"""
import math
import os
import random
import shutil
import sys
from pathlib import Path

import cairo
import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402

from . import sprite  # noqa: E402
from .paths import DATA_DIR  # noqa: E402

FONT = "Selawik"
SCALE = 1.5          # Shisa's size relative to the pet's canvas, at 900px tall


def _screen():
    """Whole screen size and the primary monitor's rectangle."""
    display = Gdk.Display.get_default()
    if display is None:
        sys.exit("shisa: no display to lock")
    mons = [display.get_monitor(i).get_geometry() for i in range(display.get_n_monitors())]
    w = max(m.x + m.width for m in mons)
    h = max(m.y + m.height for m in mons)
    prim = display.get_primary_monitor()
    return w, h, prim.get_geometry() if prim else mons[0]


def _sky(cr, w, h, mon):
    g = cairo.LinearGradient(0, 0, 0, h)
    g.add_color_stop_rgb(0.0, 0.03, 0.15, 0.33)
    g.add_color_stop_rgb(0.55, 0.11, 0.42, 0.74)
    g.add_color_stop_rgb(1.0, 0.55, 0.85, 0.98)
    cr.set_source(g)
    cr.paint()

    rnd = random.Random(7)       # same scene every time
    for _ in range(int(w * h / 9000)):       # stars, fading out toward the horizon
        x, y = rnd.uniform(0, w), rnd.uniform(0, h * 0.55)
        a = rnd.uniform(0.25, 0.8) * (1 - y / (h * 0.55))
        cr.arc(x, y, rnd.uniform(0.6, 1.6), 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, a)
        cr.fill()

    # a soft moon in the top right corner of the primary monitor
    mx, my, mr = mon.x + mon.width * 0.84, mon.y + mon.height * 0.16, mon.height * 0.05
    glow = cairo.RadialGradient(mx, my, mr, mx, my, mr * 4)
    glow.add_color_stop_rgba(0, 0.85, 0.95, 1, 0.35)
    glow.add_color_stop_rgba(1, 0.85, 0.95, 1, 0)
    cr.set_source(glow)
    cr.arc(mx, my, mr * 4, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(1.0, 0.98, 0.9)
    cr.arc(mx, my, mr, 0, 2 * math.pi)
    cr.fill()

    # glossy Aero bubbles drifting up
    for _ in range(int(w * h / 80000)):
        x, y = rnd.uniform(0, w), rnd.uniform(h * 0.25, h)
        r = rnd.uniform(8, 46) * h / 900
        body = cairo.RadialGradient(x, y, r * 0.2, x, y, r)
        body.add_color_stop_rgba(0, 1, 1, 1, 0.02)
        body.add_color_stop_rgba(0.8, 0.8, 0.95, 1, 0.10)
        body.add_color_stop_rgba(1, 1, 1, 1, 0.35)
        cr.set_source(body)
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.fill()
        shine = cairo.RadialGradient(x - r * 0.35, y - r * 0.4, 0, x - r * 0.35, y - r * 0.4, r * 0.45)
        shine.add_color_stop_rgba(0, 1, 1, 1, 0.55)
        shine.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.set_source(shine)
        cr.arc(x - r * 0.35, y - r * 0.4, r * 0.45, 0, 2 * math.pi)
        cr.fill()

    # light rising from the horizon
    haze = cairo.LinearGradient(0, h * 0.7, 0, h)
    haze.add_color_stop_rgba(0, 1, 1, 1, 0)
    haze.add_color_stop_rgba(1, 1, 1, 1, 0.25)
    cr.set_source(haze)
    cr.paint()


def _cloud(cr, cx, cy, s):
    """A fluffy cloud cushion centred on (cx, cy), about 300*s wide."""
    puffs = [(-105, 8, 48), (-55, -14, 60), (5, -24, 66), (65, -12, 58), (110, 8, 44),
             (-60, 22, 50), (0, 26, 56), (60, 22, 50)]
    cr.save()
    cr.translate(cx, cy)
    cr.scale(s, s)
    glow = cairo.RadialGradient(0, 0, 60, 0, 0, 230)
    glow.add_color_stop_rgba(0, 0.8, 0.94, 1, 0.45)
    glow.add_color_stop_rgba(1, 0.8, 0.94, 1, 0)
    cr.set_source(glow)
    cr.arc(0, 0, 230, 0, 2 * math.pi)
    cr.fill()
    for x, y, r in puffs:
        cr.new_sub_path()
        cr.arc(x, y, r, 0, 2 * math.pi)
    fill = cairo.LinearGradient(0, -90, 0, 80)
    fill.add_color_stop_rgb(0, 1, 1, 1)
    fill.add_color_stop_rgb(1, 0.78, 0.89, 0.98)
    cr.set_source(fill)
    cr.fill()
    cr.restore()


def render(path, w, h, mon):
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surf)
    _sky(cr, w, h, mon)

    s = SCALE * mon.height / 900
    cx, ground = mon.x + mon.width / 2, mon.y + mon.height * 0.66
    _cloud(cr, cx, ground + 18 * s, s)

    cr.save()
    cr.translate(cx - sprite.CX * s, ground - sprite.GROUND * s)
    cr.scale(s, s)
    sprite.draw(cr, sprite.Pose(eyes="closed", mouth="sleep", squash_y=0.96, squash_x=1.04,
                                mane=0.6))
    sprite.zzz(cr, 138, 66, 34)
    sprite.zzz(cr, 162, 34, 25, 0.8)
    sprite.zzz(cr, 182, 8, 18, 0.55)
    cr.restore()

    surf.write_to_png(str(path))


def _hex(r, g, b, a=1.0):
    return "".join(f"{round(v * 255):02x}" for v in (r, g, b, a))


def lock():
    locker = shutil.which("i3lock")
    if not locker:
        sys.exit("shisa: i3lock (i3lock-color) isn't installed")
    w, h, mon = _screen()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    img = DATA_DIR / f"lockscreen-{w}x{h}.png"
    newest_code = max(Path(__file__).stat().st_mtime, Path(sprite.__file__).stat().st_mtime)
    if not img.exists() or img.stat().st_mtime < newest_code:
        render(img, w, h, mon)

    k = mon.height / 900
    cx = mon.x + mon.width // 2
    white, soft = _hex(1, 1, 1), _hex(1, 1, 1, 0.8)
    clear = "00000000"
    args = [
        locker, "--nofork", "--image", str(img), "--ignore-empty-password",
        "--show-failed-attempts", "--pass-media-keys", "--pass-volume-keys",
        "--clock", "--time-str=%H:%M", "--date-str=%A, %d %B",
        f"--time-font={FONT}", f"--date-font={FONT}", f"--greeter-font={FONT}",
        f"--verif-font={FONT}", f"--wrong-font={FONT}",
        f"--time-size={round(76 * k)}", f"--date-size={round(20 * k)}",
        f"--greeter-size={round(17 * k)}",
        f"--verif-size={round(14 * k)}", f"--wrong-size={round(14 * k)}",
        f"--time-color={white}", f"--date-color={soft}", f"--greeter-color={soft}",
        f"--verif-color={white}", f"--wrong-color={white}",
        f"--time-pos={cx}:{mon.y + round(mon.height * 0.2)}",
        f"--date-pos={cx}:{mon.y + round(mon.height * 0.2 + 34 * k)}",
        "--greeter-text=shh... Shisa is napping. Type your password to say hi",
        f"--greeter-pos={cx}:{mon.y + round(mon.height * 0.93)}",
        # the password ring: a small glossy orb under the cloud, only while typing
        f"--ind-pos={cx}:{mon.y + round(mon.height * 0.835)}",
        f"--radius={round(30 * k)}", f"--ring-width={max(3, round(5 * k))}",
        f"--inside-color={_hex(1, 1, 1, 0.18)}", f"--ring-color={_hex(1, 1, 1, 0.5)}",
        f"--insidever-color={_hex(0.6, 0.85, 0.98, 0.35)}", f"--ringver-color={_hex(0.6, 0.85, 0.98)}",
        f"--insidewrong-color={_hex(1, 0.5, 0.58, 0.35)}", f"--ringwrong-color={_hex(1, 0.48, 0.54)}",
        f"--keyhl-color={_hex(0.56, 0.86, 1)}", f"--bshl-color={_hex(1, 0.7, 0.78)}",
        f"--line-color={clear}", f"--separator-color={clear}",
        "--verif-text=...", "--wrong-text=nope!", "--noinput-text=", "--lock-text=",
        "--lockfailed-text=couldn't lock!",
    ]
    os.execv(locker, args)
