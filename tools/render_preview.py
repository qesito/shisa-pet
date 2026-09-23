"""Render a strip of Shisa's moods to assets/preview.png (used in the README)."""
import math
import sys
from pathlib import Path

import cairo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shisa import sprite  # noqa: E402

POSES = [
    ("idle", dict()),
    ("happy", dict(eyes="happy", mouth="grin", arms=0.8)),
    ("talking", dict(mouth="open")),
    ("thinking", dict(look=(2, -3), mouth="o", brows="up")),
    ("surprised", dict(eyes="wide", mouth="o", brows="up")),
    ("sad", dict(eyes="sad", mouth="frown", brows="sad")),
    ("sleepy", dict(eyes="closed", mouth="sleep", squash_y=0.96, squash_x=1.03)),
]


def main(out="assets/preview.png", scale=1.0):
    cell = int(sprite.W * scale)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, cell * len(POSES), int(sprite.H * scale))
    cr = cairo.Context(surf)
    for i, (name, kw) in enumerate(POSES):
        cr.save()
        cr.translate(i * cell, 0)
        cr.scale(scale, scale)
        sprite.draw(cr, sprite.Pose(mane=i, **kw))
        if name == "thinking":
            sprite.thought(cr, 0.3)
        elif name == "happy":
            sprite.heart(cr, 150, 42, 12)
            sprite.sparkle(cr, 45, 50, 11)
        elif name == "sleepy":
            sprite.zzz(cr, 138, 62, 26)
            sprite.zzz(cr, 158, 36, 19, 0.7)
        cr.restore()
    surf.write_to_png(out)
    print(out)


if __name__ == "__main__":
    main(*sys.argv[1:2])
