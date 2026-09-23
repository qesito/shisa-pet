"""Shisa, drawn with cairo.

Everything is vector paths, so there are no image files to ship and the pet
stays crisp at any size. Coordinates live on a 200x215 canvas; the app scales
that to the configured size.
"""
import math

import cairo

W, H = 200, 215
CX, GROUND = 100, 206          # centre line and where the feet touch down
RX, RY = 60, 54                # body radii
BODY_CY = GROUND - 8 - RY      # body centre

OUTLINE = (0.35, 0.22, 0.15)
CREAM = (1.0, 0.972, 0.875)
CREAM_SHADE = (0.965, 0.9, 0.76)
MANE = (0.965, 0.675, 0.47)
MANE_DARK = (0.82, 0.46, 0.29)
BLUSH = (0.99, 0.62, 0.68)
EYE = (0.22, 0.14, 0.10)
HEART = (0.98, 0.42, 0.55)
LW = 3.2


class Pose:
    """Animation parameters for one frame."""

    def __init__(self, **kw):
        self.squash_x = 1.0
        self.squash_y = 1.0
        self.lift = 0.0          # px above the ground (hops)
        self.tilt = 0.0          # radians, whole-body lean
        self.eyes = "open"       # open closed happy wide sad
        self.mouth = "smile"     # smile grin open o frown sleep
        self.look = (0.0, 0.0)   # eye offset
        self.mane = 0.0          # sway phase
        self.arms = 0.0          # 0 = down, 1 = up
        self.feet = 0.0          # dangle phase (while dragged)
        self.brows = "normal"    # normal up sad
        self.__dict__.update(kw)


def _ellipse(cr, x, y, rx, ry):
    cr.save()
    cr.translate(x, y)
    cr.scale(rx, ry)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()


def _fill_stroke(cr, fill, lw=LW):
    cr.set_source_rgb(*fill)
    cr.fill_preserve()
    cr.set_source_rgb(*OUTLINE)
    cr.set_line_width(lw)
    cr.stroke()


# --- body parts (x is relative to the centre line) -------------------------

def _body_path(cr, cy):
    top, bot = cy - RY, cy + RY
    cr.move_to(0, top)
    cr.curve_to(RX * 0.62, top, RX, cy - RY * 0.55, RX, cy + RY * 0.05)
    cr.curve_to(RX, cy + RY * 0.74, RX * 0.64, bot, 0, bot)
    cr.curve_to(-RX * 0.64, bot, -RX, cy + RY * 0.74, -RX, cy + RY * 0.05)
    cr.curve_to(-RX, cy - RY * 0.55, -RX * 0.62, top, 0, top)
    cr.close_path()


def _body(cr, cy):
    _body_path(cr, cy)
    grad = cairo.RadialGradient(-18, cy - 26, 6, 0, cy, RX * 1.15)
    grad.add_color_stop_rgb(0, *CREAM)
    grad.add_color_stop_rgb(0.7, *CREAM)
    grad.add_color_stop_rgb(1, *CREAM_SHADE)
    cr.set_source(grad)
    cr.fill_preserve()
    cr.set_source_rgb(*OUTLINE)
    cr.set_line_width(LW)
    cr.stroke()


def _tuft(cr, L, w):
    cr.move_to(0, -w)
    cr.curve_to(L * 0.45, -w * 1.4, L * 0.95, -w * 0.95, L, -w * 0.05)
    cr.curve_to(L * 1.03, w * 0.6, L * 0.72, w * 1.0, L * 0.46, w * 0.72)
    cr.curve_to(L * 0.3, w * 1.0, L * 0.1, w * 1.12, 0, w)
    cr.close_path()
    _fill_stroke(cr, MANE, 2.8)
    # inner curl line
    cr.move_to(L * 0.28, w * 0.15)
    cr.curve_to(L * 0.5, -w * 0.35, L * 0.82, -w * 0.25, L * 0.8, w * 0.28)
    cr.set_source_rgb(*MANE_DARK)
    cr.set_line_width(2.0)
    cr.stroke()


def _mane(cr, cy, phase):
    tufts = ((-36, 30, 15), (0, 34, 16), (34, 32, 15), (66, 25, 12))
    for side in (1, -1):
        cr.save()
        cr.scale(side, 1)
        for i, (deg, L, w) in enumerate(tufts):
            a = math.radians(deg)
            x = RX * 0.86 * math.cos(a)
            y = cy + RY * 0.86 * math.sin(a)
            cr.save()
            cr.translate(x, y)
            cr.rotate(a * 0.8 + 0.09 * math.sin(phase + i * 1.3 + (side > 0) * 0.7))
            _tuft(cr, L, w)
            cr.restore()
        cr.restore()


def _ears(cr, cy):
    for side in (1, -1):
        cr.save()
        cr.translate(side * 29, cy - RY + 9)
        cr.rotate(side * 0.38)
        cr.move_to(-14, 8)
        cr.curve_to(-15, -7, -8, -19, 0, -19)
        cr.curve_to(8, -19, 15, -7, 14, 8)
        cr.close_path()
        _fill_stroke(cr, CREAM)
        cr.move_to(-6, 3)
        cr.curve_to(-6, -8, 6, -8, 6, 3)
        cr.set_line_width(1.8)
        cr.stroke()
        cr.restore()


def _feet(cr, phase):
    for side in (1, -1):
        dy = 3 * math.sin(phase + (side > 0) * math.pi)
        _ellipse(cr, side * 22, GROUND - 7 + dy, 13, 8.5)
        _fill_stroke(cr, CREAM)


def _arms(cr, cy, raise_):
    for side in (1, -1):
        cr.save()
        cr.translate(side * (RX - 6), cy + RY * 0.32 - 16 * raise_)
        cr.rotate(side * (0.55 - 1.3 * raise_))
        _ellipse(cr, side * 6, 0, 12, 8.5)
        _fill_stroke(cr, CREAM)
        cr.restore()


def _brow(cr):
    cr.move_to(-7, 3)
    cr.curve_to(-9.5, -4, -2, -9.5, 5, -6.5)
    cr.curve_to(10.5, -4, 9.5, 2.5, 4, 2.5)
    cr.curve_to(6, -1, 2, -3, 0, -0.5)
    cr.curve_to(-2, 1.5, -3, 4.5, -7, 3)
    cr.close_path()
    _fill_stroke(cr, MANE, 1.8)


def _eye(cr, kind):
    cr.set_source_rgb(*EYE)
    cr.set_line_width(2.6)
    if kind in ("open", "sad", "wide"):
        rx, ry = {"open": (4.3, 6.2), "sad": (4.0, 5.2), "wide": (5.2, 7.2)}[kind]
        _ellipse(cr, 0, 0, rx, ry)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.arc(-1.2, -2.4, 1.8, 0, 2 * math.pi)
        cr.fill()
        if kind == "wide":
            cr.arc(1.6, 2.2, 1.0, 0, 2 * math.pi)
            cr.fill()
    elif kind == "happy":
        cr.move_to(-5.5, 2.5)
        cr.curve_to(-2.5, -4.5, 2.5, -4.5, 5.5, 2.5)
        cr.stroke()
    else:  # closed
        cr.move_to(-5.5, -0.5)
        cr.curve_to(-2.5, 3.5, 2.5, 3.5, 5.5, -0.5)
        cr.stroke()


def _mouth(cr, kind, y):
    cr.set_source_rgb(*EYE)
    cr.set_line_width(2.2)
    if kind == "smile":
        cr.move_to(-6, y)
        cr.curve_to(-5, y + 4, -1, y + 4, 0, y + 1)
        cr.curve_to(1, y + 4, 5, y + 4, 6, y)
        cr.stroke()
    elif kind in ("grin", "open"):
        if kind == "grin":
            cr.move_to(-6.5, y - 0.5)
            cr.curve_to(-5.5, y + 7.5, 5.5, y + 7.5, 6.5, y - 0.5)
            cr.close_path()
        else:
            _ellipse(cr, 0, y + 2.5, 4.2, 3.8)
        cr.set_source_rgb(0.45, 0.16, 0.16)
        cr.fill_preserve()
        cr.save()
        cr.clip_preserve()
        cr.set_source_rgb(*BLUSH)
        _ellipse(cr, 0, y + 6.5, 4.5, 3)
        cr.fill()
        cr.restore()
        cr.set_source_rgb(*EYE)
        cr.set_line_width(2.0)
        cr.stroke()
    elif kind == "o":
        _ellipse(cr, 0, y + 2.5, 3.2, 3.6)
        cr.stroke()
    elif kind == "frown":
        cr.move_to(-5, y + 4)
        cr.curve_to(-2, y - 0.5, 2, y - 0.5, 5, y + 4)
        cr.stroke()
    else:  # sleep: tiny relaxed mouth
        cr.move_to(-3, y + 2)
        cr.curve_to(-1, y + 3.5, 1, y + 3.5, 3, y + 2)
        cr.stroke()


def _face(cr, cy, p):
    lx, ly = p.look
    for side in (1, -1):
        # blush
        cr.set_source_rgba(*BLUSH, 0.85)
        _ellipse(cr, side * 38, cy + 9, 9.5, 5.8)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.55)
        for k in (-3.5, 0, 3.5):
            cr.move_to(side * 38 + k - 1.2, cy + 11)
            cr.line_to(side * 38 + k + 1.2, cy + 7)
        cr.set_line_width(1.1)
        cr.stroke()
        # brows
        cr.save()
        by = {"up": -26, "sad": -21}.get(p.brows, -23)
        cr.translate(side * 22 + lx * 0.4, cy + by + ly * 0.4)
        cr.scale(side * 1.15, 1.15)
        if p.brows == "sad":
            cr.rotate(0.35)
        _brow(cr)
        cr.restore()
        # eyes
        cr.save()
        cr.translate(side * 22 + lx, cy - 5 + ly)
        _eye(cr, p.eyes)
        cr.restore()
    _mouth(cr, p.mouth, cy + 5 + ly * 0.3)


def draw(cr, p):
    cr.save()
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.translate(CX, GROUND - p.lift)
    cr.rotate(p.tilt)
    cr.scale(p.squash_x, p.squash_y)
    cr.translate(0, -GROUND)
    cy = BODY_CY
    _mane(cr, cy, p.mane)
    _ears(cr, cy)
    _feet(cr, p.feet)
    _arms(cr, cy, p.arms)
    _body(cr, cy)
    _face(cr, cy, p)
    cr.restore()


# --- effects (canvas coordinates) ------------------------------------------

def heart(cr, x, y, s, alpha=1.0):
    cr.move_to(x, y + s)
    cr.curve_to(x - s * 1.3, y + s * 0.15, x - s * 0.65, y - s * 0.7, x, y - s * 0.05)
    cr.curve_to(x + s * 0.65, y - s * 0.7, x + s * 1.3, y + s * 0.15, x, y + s)
    cr.close_path()
    cr.set_source_rgba(*HEART, alpha)
    cr.fill_preserve()
    cr.set_source_rgba(*OUTLINE, alpha)
    cr.set_line_width(1.6)
    cr.stroke()


def sparkle(cr, x, y, s, alpha=1.0):
    cr.move_to(x, y - s)
    for dx, dy in ((0.25, -0.25), (s, 0), (0.25, 0.25), (0, s),
                   (-0.25, 0.25), (-s, 0), (-0.25, -0.25), (0, -s)):
        cr.line_to(x + (dx * s if abs(dx) < 1 else dx), y + (dy * s if abs(dy) < 1 else dy))
    cr.close_path()
    cr.set_source_rgba(1.0, 0.86, 0.35, alpha)
    cr.fill_preserve()
    cr.set_source_rgba(*OUTLINE, alpha * 0.8)
    cr.set_line_width(1.2)
    cr.stroke()


def zzz(cr, x, y, s, alpha=1.0):
    cr.select_font_face("Selawik", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(s)
    cr.move_to(x, y)
    cr.text_path("z")
    cr.set_source_rgba(0.55, 0.78, 0.95, alpha)
    cr.fill_preserve()
    cr.set_source_rgba(*OUTLINE, alpha)
    cr.set_line_width(1.3)
    cr.stroke()


def thought(cr, t):
    """Little thinking cloud with bouncing dots above the head."""
    x, y = CX + 38, 34
    cr.set_line_width(2.0)
    for (bx, by, r) in ((CX + 20, 66, 3.5), (CX + 28, 55, 5)):
        cr.arc(bx, by, r, 0, 2 * math.pi)
        cr.set_source_rgb(1, 1, 1)
        cr.fill_preserve()
        cr.set_source_rgb(*OUTLINE)
        cr.stroke()
    w, h, r = 46, 24, 12
    cr.new_sub_path()
    cr.arc(x - w / 2 + r, y - h / 2 + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x + w / 2 - r, y - h / 2 + r, r, 1.5 * math.pi, 2 * math.pi)
    cr.arc(x + w / 2 - r, y + h / 2 - r, r, 0, 0.5 * math.pi)
    cr.arc(x - w / 2 + r, y + h / 2 - r, r, 0.5 * math.pi, math.pi)
    cr.close_path()
    cr.set_source_rgb(1, 1, 1)
    cr.fill_preserve()
    cr.set_source_rgb(*OUTLINE)
    cr.stroke()
    for i in range(3):
        dy = -3 * max(0.0, math.sin(t * 6 - i * 0.9))
        cr.arc(x - 11 + i * 11, y + dy, 3, 0, 2 * math.pi)
        cr.set_source_rgb(*MANE_DARK)
        cr.fill()
