"""The desktop pet window, its chat bubble and the pop-up terminal."""
import math
import os
import random
import shutil
import subprocess
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import sprite  # noqa: E402
from .brain import Brain, load_config, save_key, strip_mood  # noqa: E402

WM_CLASS = "Shisa-pet"
FPS = 30

CSS = b"""
#bubble, #bubble viewport, #bubble scrolledwindow { background: transparent; }
#bubble * { font-family: "Selawik", "Segoe UI", sans-serif; }
.title { font-weight: bold; font-size: 12.5pt; color: #1b5d8f; }
.close { min-width: 22px; min-height: 22px; padding: 0; border-radius: 11px;
         border: none; background: transparent; color: #5b86a8; box-shadow: none; }
.close:hover { background: rgba(255, 120, 120, 0.25); color: #b33; }
.msg { padding: 6px 10px; border-radius: 13px; font-size: 10.5pt; }
.pet { background-color: rgba(255, 255, 255, 0.92); color: #3b2a20;
       border: 1px solid rgba(150, 200, 235, 0.9); }
.me { background-image: linear-gradient(to bottom, #86cff7, #3d9ee2); color: white;
      border: 1px solid rgba(40, 120, 190, 0.6); }
.note { color: #56799a; font-style: italic; font-size: 9.5pt; }
entry { border-radius: 14px; padding: 4px 10px; min-height: 26px;
        border: 1px solid rgba(90, 160, 215, 0.8); background: rgba(255, 255, 255, 0.95);
        color: #2b2b2b; }
.send { border-radius: 14px; padding: 2px 12px; color: white; font-weight: bold;
        border: 1px solid rgba(30, 110, 180, 0.7); box-shadow: none; text-shadow: none;
        background-image: linear-gradient(to bottom, #9ad8fb, #3f9fe3); }
.send:hover { background-image: linear-gradient(to bottom, #b3e3fd, #56b0ee); }
.send:disabled { background-image: none; background-color: rgba(160, 190, 210, 0.7); }
"""

ERRORS = {
    "auth": "My key didn't work... double-check it? (right-click me → Set API key)",
    "permission": "That key isn't allowed to use this model.",
    "model": "I can't find the model in my config.json. Check the \"model\" name?",
    "ratelimit": "Too many messages at once! Give me a moment to catch my breath.",
    "credit": "My API account is out of credits. Add some at console.anthropic.com → Billing.",
    "offline": "I can't reach the internet right now...",
    "refusal": "Hmm, I can't help with that one. Ask me something else?",
    "noclaude": "I can't find Claude Code (the claude command). Install it, or set "
                "\"claude_path\" in my config.json.",
    "login": "Claude Code isn't logged in. Right-click me → Open terminal, and log in there.",
    "cclimit": "We've hit your Claude plan's usage limit... let's chat again a bit later!",
}


def setup_bspwm():
    """Float the chat bubble above everything, on every desktop, without a border.

    Shisa itself is an unmanaged window, so bspwm leaves it alone.
    """
    if not shutil.which("bspc"):
        return
    for inst in ("shisa", "shisa-chat"):   # drop rules from earlier runs
        subprocess.run(["bspc", "rule", "-r", f"{WM_CLASS}:{inst}:*"], stderr=subprocess.DEVNULL)
    subprocess.run(["bspc", "rule", "-a", f"{WM_CLASS}:shisa-chat", "state=floating",
                    "sticky=on", "layer=above", "border=off", "focus=on"],
                   stderr=subprocess.DEVNULL)


def _transparent(win):
    visual = win.get_screen().get_rgba_visual()
    if visual:
        win.set_visual(visual)
    win.set_app_paintable(True)


def _workarea(win):
    display = Gdk.Display.get_default()
    gdk_win = win.get_window()
    mon = display.get_monitor_at_window(gdk_win) if gdk_win else display.get_primary_monitor()
    return (mon or display.get_monitor(0)).get_workarea()


class Pet(Gtk.Window):
    # A POPUP (override-redirect) window: the window manager doesn't manage it,
    # so it can't grab Shisa's clicks, dim it or move it around. Shisa moves
    # itself and re-raises itself now and then to stay on top.
    def __init__(self, cfg, brain):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.cfg, self.brain = cfg, brain
        self.k = float(cfg["size"])
        self.w, self.h = int(sprite.W * self.k), int(sprite.H * self.k)

        self.set_wmclass("shisa", WM_CLASS)
        self.set_title("Shisa")
        self.set_resizable(False)
        _transparent(self)
        self.set_size_request(self.w, self.h)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("button-release-event", self.on_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("enter-notify-event", self.on_enter)
        self.connect("realize", self.on_realize)
        self.connect("configure-event", self.on_configure)
        self.connect("destroy", Gtk.main_quit)

        now = time.monotonic()
        self.t0 = now
        self.last_touch = now
        self.mood, self.mood_until = "happy", now + 2.5
        self.sleeping = False
        self.thinking = False
        self.talk_until = 0.0
        self.next_blink, self.blink_at = now + 2, -1.0
        self.look, self.look_target, self.next_look = [0.0, 0.0], (0.0, 0.0), now + 4
        self.hops = []                 # queued (dx, height, duration)
        self.hop = None                # current hop state
        self.next_wander = now + random.uniform(25, 60)
        self.next_raise = now + 2
        self.particles = []
        self.next_particle = 0.0
        self.press = None              # (root_x, root_y, win_x, win_y)
        self.dragging = False
        self.rub = []                  # recent hover positions for petting
        self.pos = (0, 0)

        wa = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        wa = wa.get_workarea()
        self.pos = (wa.x + wa.width - self.w - 60, wa.y + wa.height - self.h)
        self.move(*self.pos)

        self.chat = ChatBubble(self)
        self.menu = self._build_menu()
        GLib.timeout_add(1000 // FPS, self.tick)
        self.queue_hop(0, 26, 0.5)     # hello hop

    # --- setup -------------------------------------------------------------

    def on_realize(self, _w):
        # Only the pet's silhouette (plus hop room) catches clicks; the rest
        # of the window lets them fall through to whatever is underneath.
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
        cr = cairo.Context(surf)
        cr.scale(self.k, self.k)
        x0, x1 = sprite.CX - sprite.RX - 26, sprite.CX + sprite.RX + 26
        y0, y1 = sprite.BODY_CY - sprite.RY - 45, sprite.GROUND + 2
        r = 50
        cr.new_sub_path()
        cr.arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi)
        cr.arc(x1 - r, y0 + r, r, 1.5 * math.pi, 2 * math.pi)
        cr.arc(x1 - r, y1 - r, r, 0, 0.5 * math.pi)
        cr.arc(x0 + r, y1 - r, r, 0.5 * math.pi, math.pi)
        cr.close_path()
        cr.fill()
        self.input_shape_combine_region(Gdk.cairo_region_create_from_surface(surf))

    def on_configure(self, _w, e):
        if not self.dragging and not self.hop:
            self.pos = (e.x, e.y)
            self.chat.follow()
        return False

    def _build_menu(self):
        menu = Gtk.Menu()

        def item(label, cb):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda *_: cb())
            menu.append(mi)
            return mi

        item("💬  Chat", self.chat.toggle)
        item("🐾  Pat Shisa", self.pat)
        self.sleep_item = item("💤  Nap time", self.toggle_sleep)
        wander = Gtk.CheckMenuItem(label="Wander around")
        wander.set_active(bool(self.cfg["wander"]))
        wander.connect("toggled", lambda w: self.cfg.__setitem__("wander", w.get_active()))
        menu.append(wander)
        menu.append(Gtk.SeparatorMenuItem())
        item("🖥  Open terminal", self.open_terminal)
        if self.cfg["backend"] == "api":
            item("🔑  Set API key…", lambda: self.chat.open(key_mode=True))
        item("🧹  Forget our chats", self.forget)
        menu.append(Gtk.SeparatorMenuItem())
        item("🙈  Hide", self.toggle_hidden)
        item("👋  Goodbye", Gtk.main_quit)
        menu.show_all()
        return menu

    # --- reactions ---------------------------------------------------------

    def touch(self):
        self.last_touch = time.monotonic()
        if self.sleeping:
            self.sleeping = False
            self.set_mood("surprised", 1.2)
            self.queue_hop(0, 20, 0.4)

    def set_mood(self, mood, secs=4.0):
        self.mood, self.mood_until = mood, time.monotonic() + secs
        if mood in ("happy", "excited", "love") and not self.hop and not self.hops:
            self.queue_hop(0, 14, 0.35)

    def toggle_hidden(self):
        if self.get_visible():
            self.chat.hide()
            self.hide()
        else:
            self.move(*self.pos)
            self.show_all()
            self.touch()
            self.set_mood("happy", 2)
        return True   # keeps the SIGUSR1 handler installed

    def open_terminal(self):
        """Pop a small floating terminal next to Shisa running Claude Code."""
        w, h = int(760 * self.k), int(460 * self.k)
        wa = _workarea(self)
        px, py = self.pos
        x = px - w - 10 if px - w - 10 >= wa.x else px + self.w + 10
        x = max(wa.x, min(x, wa.x + wa.width - w))
        y = max(wa.y + 10, min(py + self.h - h, wa.y + wa.height - h))
        if shutil.which("bspc"):
            subprocess.run(["bspc", "rule", "-a", "Shisa-term", "-o", "state=floating",
                            f"rectangle={w}x{h}+{x}+{y}"], stderr=subprocess.DEVNULL)
        claude = self.brain._claude_exe()
        # run Claude Code, then leave a normal shell open when it exits
        inner = f'"{claude}"; exec "${{SHELL:-sh}}"' if claude else 'exec "${SHELL:-sh}"'
        if shutil.which("kitty"):
            cmd = ["kitty", "--class", "Shisa-term", "--title", "Shisa terminal",
                   "--directory", os.path.expanduser("~"), "sh", "-c", inner]
        else:
            cmd = ["x-terminal-emulator", "-e", "sh", "-c", inner]
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        try:
            subprocess.Popen(cmd, cwd=os.path.expanduser("~"), env=env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self.set_mood("sad", 3)
            return
        self.touch()
        self.set_mood("excited", 2.5)
        for _ in range(3):
            self.spawn("sparkle")

    def pat(self):
        self.touch()
        self.set_mood("love", 3)
        for _ in range(4):
            self.spawn("heart")

    def toggle_sleep(self):
        if self.sleeping:
            self.touch()
        else:
            self.sleeping = True
            self.last_touch = time.monotonic()

    def forget(self):
        self.brain.forget()
        self.chat.clear()
        self.set_mood("surprised", 2)

    def queue_hop(self, dx, height, dur):
        self.hops.append((dx, height, dur))

    def spawn(self, kind):
        x = sprite.CX + random.uniform(-45, 45)
        y = sprite.BODY_CY - sprite.RY - random.uniform(0, 15)
        if kind == "z":
            x, y = sprite.CX + 42, sprite.BODY_CY - sprite.RY + 5
        self.particles.append({"kind": kind, "x": x, "y": y, "t": time.monotonic(),
                               "vx": random.uniform(-8, 8) if kind != "z" else 9,
                               "vy": random.uniform(-38, -26) if kind != "z" else -22,
                               "life": 1.6 if kind != "z" else 2.4})

    # --- chat hooks --------------------------------------------------------

    def on_reply(self, kind, value):
        if kind == "mood":
            self.thinking = False
            self.set_mood(value, 6)
            if value == "love":
                for _ in range(3):
                    self.spawn("heart")
            elif value == "excited":
                for _ in range(3):
                    self.spawn("sparkle")
        elif kind == "delta":
            self.thinking = False
            self.talk_until = time.monotonic() + 0.35
        else:
            self.thinking = False
            if kind == "error":
                self.set_mood("sad", 4)

    # --- input -------------------------------------------------------------

    def on_press(self, _w, e):
        if e.type != Gdk.EventType.BUTTON_PRESS:
            return True
        if e.button == 3:
            self.sleep_item.set_label("☀️  Wake up" if self.sleeping else "💤  Nap time")
            self.menu.popup_at_pointer(e)
            return True
        if e.button == 1:
            x, y = self.get_position()
            self.press = (e.x_root, e.y_root, x, y)
            self.dragging = False
        return True

    def on_enter(self, _w, _e):
        # With focus-follows-pointer, hovering Shisa (who never takes focus)
        # would hand the keyboard to the window underneath; keep it on the chat.
        if self.chat.get_visible():
            self.chat.present()
        return False

    def on_motion(self, _w, e):
        now = time.monotonic()
        if self.press:
            rx, ry, wx, wy = self.press
            dx, dy = e.x_root - rx, e.y_root - ry
            if not self.dragging and math.hypot(dx, dy) > 6:
                self.dragging = True
                self.hops.clear()
                self.hop = None
                self.touch()
            if self.dragging:
                self.pos = (int(wx + dx), int(wy + dy))
                self.move(*self.pos)
                self.chat.follow()
            return True
        # rubbing the cursor back and forth over Shisa counts as petting
        self.rub = [(t, x) for t, x in self.rub if now - t < 1.2] + [(now, e.x)]
        travel = sum(abs(b[1] - a[1]) for a, b in zip(self.rub, self.rub[1:]))
        if travel > 350 * self.k and self.mood != "love":
            self.rub.clear()
            self.pat()
        return True

    def on_release(self, _w, e):
        if e.button != 1 or not self.press:
            return True
        was_drag = self.dragging
        self.press, self.dragging = None, False
        if was_drag:
            self.set_mood("surprised", 0.8)
            self.queue_hop(0, 10, 0.25)    # little landing bounce
        else:
            self.touch()
            self.chat.toggle()
        return True

    # --- animation ---------------------------------------------------------

    def tick(self):
        if not self.get_visible():
            return True
        now = time.monotonic()
        t = now - self.t0

        if (not self.sleeping and not self.chat.get_visible() and not self.thinking
                and now - self.last_touch > self.cfg["sleep_after"]):
            self.sleeping = True
        if self.sleeping and now > self.next_particle:
            self.spawn("z")
            self.next_particle = now + 1.3
        if self.mood == "love" and now < self.mood_until and now > self.next_particle:
            self.spawn("heart")
            self.next_particle = now + 0.45

        # blinking and glancing around
        if now > self.next_blink:
            self.blink_at = now
            self.next_blink = now + random.uniform(2.0, 6.0)
        if now > self.next_look:
            self.look_target = random.choice([(0, 0), (0, 0), (-3, 0), (3, 0), (2, 1), (-2, 1)])
            self.next_look = now + random.uniform(2.5, 7)
        tx, ty = (2.5, -3) if self.thinking else self.look_target
        self.look[0] += (tx - self.look[0]) * 0.15
        self.look[1] += (ty - self.look[1]) * 0.15

        # wandering
        if (self.cfg["wander"] and not self.sleeping and not self.dragging and not self.hop
                and not self.hops and not self.chat.get_visible() and now > self.next_wander):
            self._plan_wander()
        self._step_hop(now)

        if now > self.next_raise and not self.menu.get_visible() and not self.press:
            self.get_window().raise_()
            self.next_raise = now + 2

        self.particles = [p for p in self.particles if now - p["t"] < p["life"]]
        self.queue_draw()
        return True

    def _plan_wander(self):
        self.next_wander = time.monotonic() + random.uniform(30, 90)
        wa = _workarea(self)
        x = self.get_position()[0]
        dist = random.choice([-1, 1]) * random.uniform(60, 180) * self.k
        dist = max(wa.x - x, min(wa.x + wa.width - self.w - x, dist))
        n = max(1, int(abs(dist) // (45 * self.k)))
        for _ in range(n):
            self.queue_hop(dist / n, 16, 0.42)

    def _step_hop(self, now):
        if not self.hop and self.hops:
            dx, height, dur = self.hops.pop(0)
            x, y = self.get_position()
            self.hop = {"t": now, "dx": dx, "h": height, "dur": dur, "x": x, "y": y}
        if not self.hop:
            return
        h = self.hop
        p = min(1.0, (now - h["t"]) / h["dur"])
        if h["dx"]:
            self.pos = (int(h["x"] + h["dx"] * p), h["y"])
            self.move(*self.pos)
            self.chat.follow()
        if p >= 1.0:
            self.hop = None

    def pose(self, now):
        t = now - self.t0
        mood = self.mood if now < self.mood_until else "neutral"
        p = sprite.Pose(mane=t * 2.2)

        breath = math.sin(t * (1.4 if self.sleeping else 2.6))
        amp = 0.03 if self.sleeping else 0.018
        p.squash_y, p.squash_x = 1 + amp * breath, 1 - amp * 0.6 * breath

        if self.hop:
            q = min(1.0, (now - self.hop["t"]) / self.hop["dur"])
            p.lift = self.hop["h"] * math.sin(math.pi * q)
            s = 0.1 * math.cos(math.pi * q)          # stretch going up, squash landing
            p.squash_y *= 1 + s * 0.6
            p.squash_x *= 1 - s * 0.4
            if self.hop["dx"]:
                p.tilt = 0.08 * math.copysign(1, self.hop["dx"]) * math.sin(math.pi * q)

        if self.dragging:
            p.squash_y, p.squash_x = 1.07, 0.95
            p.feet = t * 14
            p.arms = 0.9
            p.tilt = 0.06 * math.sin(t * 6)
            p.eyes, p.mouth, p.brows = "wide", "o", "up"
            return p

        if self.sleeping:
            p.eyes, p.mouth = "closed", "sleep"
            p.squash_y *= 0.97
            p.squash_x *= 1.03
            return p

        p.look = tuple(self.look)
        if mood in ("happy", "excited", "love"):
            p.eyes, p.mouth = "happy", "grin"
            p.arms = 0.5 + 0.5 * math.sin(t * 9) if mood == "excited" else 0.35
        elif mood == "surprised":
            p.eyes, p.mouth, p.brows = "wide", "o", "up"
        elif mood == "sad":
            p.eyes, p.mouth, p.brows = "sad", "frown", "sad"
        elif mood == "sleepy":
            p.eyes, p.mouth = "closed", "smile"
        if self.thinking:
            p.mouth, p.brows = "o", "up"
            p.tilt = 0.05 * math.sin(t * 2)
        if now < self.talk_until:
            p.mouth = "open" if int(t * 9) % 2 else ("grin" if p.eyes == "happy" else "smile")
        blink = now - self.blink_at
        if 0 <= blink < 0.14 and p.eyes in ("open", "wide", "sad"):
            p.eyes = "closed"
        return p

    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.scale(self.k, self.k)
        now = time.monotonic()
        sprite.draw(cr, self.pose(now))
        if self.thinking:
            sprite.thought(cr, now - self.t0)
        for pt in self.particles:
            age = now - pt["t"]
            a = max(0.0, 1 - age / pt["life"])
            x = pt["x"] + pt["vx"] * age + (4 * math.sin(age * 5) if pt["kind"] == "z" else 0)
            y = pt["y"] + pt["vy"] * age
            if pt["kind"] == "heart":
                sprite.heart(cr, x, y, 8 + 3 * age, a)
            elif pt["kind"] == "sparkle":
                sprite.sparkle(cr, x, y, 7 + 2 * math.sin(age * 10), a)
            else:
                sprite.zzz(cr, x, y, 14 + 8 * age, a)
        return True


class ChatBubble(Gtk.Window):
    BW, BH, TAIL = 340, 330, 16

    def __init__(self, pet):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.pet = pet
        self.busy = False
        self.key_mode = False
        self.tail_x = self.BW / 2
        self.tail_down = True
        self.current = None       # label being streamed into

        self.set_wmclass("shisa-chat", WM_CLASS)
        self.set_name("bubble")
        self.set_title("Shisa chat")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.stick()
        _transparent(self)
        self.set_size_request(self.BW, self.BH)
        self.connect("draw", self.on_draw)
        self.connect("key-press-event", self.on_key)
        self.connect("delete-event", lambda *_: self.hide() or True)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._set_margins()
        self.add(self.outer)

        head = Gtk.Box(spacing=6)
        title = Gtk.Label(label="Shisa 🐾", xalign=0)
        title.get_style_context().add_class("title")
        close = Gtk.Button(label="✕")
        close.get_style_context().add_class("close")
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda *_: self.hide())
        head.pack_start(title, True, True, 0)
        head.pack_end(close, False, False, 0)
        self.outer.pack_start(head, False, False, 0)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.log = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.log.set_valign(Gtk.Align.END)
        self.scroll.add(self.log)
        self.outer.pack_start(self.scroll, True, True, 0)

        row = Gtk.Box(spacing=6)
        self.entry = Gtk.Entry()
        self.entry.connect("activate", self.on_send)
        self.send = Gtk.Button(label="Send")
        self.send.get_style_context().add_class("send")
        self.send.connect("clicked", self.on_send)
        row.pack_start(self.entry, True, True, 0)
        row.pack_end(self.send, False, False, 0)
        self.outer.pack_start(row, False, False, 0)
        self.outer.show_all()

        self._load_history()
        self._update_entry()

    # --- layout ------------------------------------------------------------

    def _set_margins(self):
        m = self.outer
        m.set_margin_start(14)
        m.set_margin_end(14)
        m.set_margin_top(10 + (0 if self.tail_down else self.TAIL))
        m.set_margin_bottom(12 + (self.TAIL if self.tail_down else 0))

    def follow(self, force=False):
        """Sit just above Shisa's head (or below if there's no room)."""
        if not force and not self.get_visible():
            return
        px, py = self.pet.pos
        k = self.pet.k
        head_top = py + int((sprite.BODY_CY - sprite.RY - 22) * k)
        cx = px + self.pet.w // 2
        wa = _workarea(self.pet)
        x = min(max(cx - self.BW // 2, wa.x + 6), wa.x + wa.width - self.BW - 6)
        y = head_top - self.BH + 4
        down = y >= wa.y + 4
        if not down:
            y = py + self.pet.h - 6
        if down != self.tail_down:
            self.tail_down = down
            self._set_margins()
        self.tail_x = min(max(cx - x, 30), self.BW - 30)
        self.move(x, y)
        self.queue_draw()

    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        w, h, T, r = self.BW, self.BH, self.TAIL, 18
        top = 2 if self.tail_down else T + 2
        bot = h - T - 2 if self.tail_down else h - 2
        l, rr = 2, w - 2
        tx = self.tail_x

        cr.new_path()
        cr.arc(l + r, top + r, r, math.pi, 1.5 * math.pi)
        if not self.tail_down:
            cr.line_to(tx - 12, top)
            cr.line_to(tx, top - T)
            cr.line_to(tx + 12, top)
        cr.arc(rr - r, top + r, r, 1.5 * math.pi, 2 * math.pi)
        cr.arc(rr - r, bot - r, r, 0, 0.5 * math.pi)
        if self.tail_down:
            cr.line_to(tx + 12, bot)
            cr.line_to(tx, bot + T)
            cr.line_to(tx - 12, bot)
        cr.arc(l + r, bot - r, r, 0.5 * math.pi, math.pi)
        cr.close_path()

        glass = cairo.LinearGradient(0, top, 0, bot)
        glass.add_color_stop_rgba(0, 0.97, 0.99, 1.0, 0.96)
        glass.add_color_stop_rgba(0.5, 0.88, 0.95, 1.0, 0.93)
        glass.add_color_stop_rgba(1, 0.80, 0.91, 0.99, 0.93)
        cr.set_source(glass)
        cr.fill_preserve()
        cr.set_source_rgba(0.35, 0.62, 0.85, 0.95)
        cr.set_line_width(1.6)
        cr.stroke()

        # Aero gloss across the top
        cr.new_path()
        cr.arc(l + r + 2, top + r + 2, r - 2, math.pi, 1.5 * math.pi)
        cr.arc(rr - r - 2, top + r + 2, r - 2, 1.5 * math.pi, 2 * math.pi)
        cr.line_to(rr - 4, top + 34)
        cr.curve_to(w * 0.6, top + 44, w * 0.4, top + 30, l + 4, top + 38)
        cr.close_path()
        gloss = cairo.LinearGradient(0, top, 0, top + 44)
        gloss.add_color_stop_rgba(0, 1, 1, 1, 0.85)
        gloss.add_color_stop_rgba(1, 1, 1, 1, 0.0)
        cr.set_source(gloss)
        cr.fill()
        return False

    # --- messages ----------------------------------------------------------

    def _add(self, text, who):
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(2)   # Pango.WrapMode.WORD_CHAR
        label.set_max_width_chars(30)
        label.set_selectable(who != "note")
        label.set_can_focus(False)
        label.get_style_context().add_class("msg" if who != "note" else "note")
        if who != "note":
            label.get_style_context().add_class(who)
        label.set_halign(Gtk.Align.END if who == "me" else Gtk.Align.START)
        label.show()
        self.log.pack_start(label, False, False, 0)
        GLib.idle_add(self._scroll_bottom)
        return label

    def _scroll_bottom(self):
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _load_history(self):
        for m in self.pet.brain.history[-12:]:
            if m["role"] == "user":
                self._add(m["content"], "me")
            else:
                self._add(strip_mood(m["content"]), "pet")
        if self.pet.brain.needs_key():
            self._key_prompt()
        elif not self.pet.brain.history:
            self._add("Haisai! I'm Shisa. Type something and I'll answer~", "pet")

    def _key_prompt(self):
        self.key_mode = True
        self._add("To talk I need a Claude API key. Make one at console.anthropic.com → "
                  "API Keys, then paste it below. It stays on this computer only.", "note")

    def clear(self):
        for child in self.log.get_children():
            child.destroy()
        self._add("...Huh? Who are you again? Haisai!", "pet")

    def _update_entry(self):
        self.entry.set_visibility(not self.key_mode)
        self.entry.set_placeholder_text("sk-ant-…" if self.key_mode else "Say something to Shisa…")
        self.entry.set_sensitive(not self.busy)
        self.send.set_sensitive(not self.busy)
        self.send.set_label("Save" if self.key_mode else "Send")

    # --- actions -----------------------------------------------------------

    def toggle(self):
        if self.get_visible():
            self.hide()
        else:
            self.open()

    def open(self, key_mode=False):
        if key_mode and not self.key_mode:
            self._key_prompt()
        self._update_entry()
        self.follow(force=True)
        self.show()
        self.present()
        self.entry.grab_focus()
        GLib.idle_add(self._scroll_bottom)

    def on_key(self, _w, e):
        if e.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def on_send(self, *_):
        text = self.entry.get_text().strip()
        if not text or self.busy:
            return
        self.entry.set_text("")
        self.pet.touch()
        if self.key_mode:
            if not text.startswith("sk-ant-"):
                self._add("That doesn't look like a Claude key (they start with sk-ant-).", "note")
                return
            save_key(text)
            self.pet.brain.reset_client()
            self.key_mode = False
            self._update_entry()
            self._add("Key saved! Yaa~ now we can talk!", "pet")
            self.pet.set_mood("excited", 3)
            return
        self._add(text, "me")
        self.busy = True
        self.current = None
        self._update_entry()
        self.pet.thinking = True
        self.pet.brain.ask(text, lambda k, v: GLib.idle_add(self._on_event, k, v))

    def _on_event(self, kind, value):
        self.pet.on_reply(kind, value)
        if kind == "delta":
            if self.current is None:
                self.current = self._add("", "pet")
            self.current.set_text(self.current.get_text() + value)
            GLib.idle_add(self._scroll_bottom)
        elif kind in ("done", "error"):
            if kind == "error":
                msg = ERRORS.get(value) or f"Oops, something went wrong: {value[4:]}"
                if value == "refusal" and self.current is not None:
                    self.current.set_text(msg)
                else:
                    self._add(msg, "note")
                if value == "auth" and self.pet.cfg["backend"] == "api":
                    self.key_mode = True
            self.busy = False
            self.current = None
            self._update_entry()
            self.entry.grab_focus()
        return False


def main():
    cfg = load_config()
    setup_bspwm()
    GLib.set_prgname("shisa-pet")
    brain = Brain(cfg)
    pet = Pet(cfg, brain)
    pet.show_all()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, Gtk.main_quit)    # SIGINT
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, Gtk.main_quit)   # SIGTERM
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 10, pet.toggle_hidden)  # SIGUSR1: shisa-pet --toggle
    if os.environ.get("SHISA_OPEN_CHAT"):
        GLib.timeout_add(800, lambda: pet.chat.open() and False)
    Gtk.main()
