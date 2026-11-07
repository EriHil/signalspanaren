"""Frontpaneler för Signalspanaren.

Riktiga reglage och utseende från tre mottagarpaneler. Allt
som går att trycka på eller vrida på styr den körande mottagaren – ingen
dekoration utan funktion. Skannerpanelen är avsiktligt begränsad till
skannern: start/stopp, hoppa över, träfflista, intervall, grupp, tröskel och
tider.

    FrontPanel(main, "r9500")   # main = MainWindow-instansen
    FrontPanel(main, "r7100")
    FrontPanel(main, "r2500")
"""

from __future__ import annotations

import logging
import math

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import cairo  # noqa: E402
import numpy as np  # noqa: E402

log = logging.getLogger("signalspanaren")

MODELS = {
    "r9500": {
        "namn": "Mottagare 1",
        "kort": "MOTT 1",
        "undertitel": "COMMUNICATIONS RECEIVER",
        "bredd": 1180,
        "hojd": 748,
        "accent": "#5fd0ff",
        "beskrivning": "Komplett mottagarfront: rattar, huvudratt, siffertangentbord, minnen och skop.",
    },
    "r7100": {
        "namn": "Mottagare 2",
        "kort": "MOTT 2",
        "undertitel": "HF / VHF / UHF RECEIVER",
        "bredd": 1010,
        "hojd": 624,
        "accent": "#8dffa0",
        "beskrivning": "Klassisk panel med grön LCD, bandstegning och siffertangentbord.",
    },
    "r2500": {
        "namn": "Mottagare 3",
        "kort": "MOTT 3",
        "undertitel": "SCANNING RECEIVER",
        "bredd": 900,
        "hojd": 548,
        "accent": "#59c8ff",
        "beskrivning": "Skannerpanel: bara skanning, träfflista, intervall och tider.",
    },
}

# Standardfilter per modulation – samma steg som mottagaren använder.
FILTERS = {
    "WFM": [50000, 100000, 150000, 180000, 200000, 250000],
    "NFM": [5000, 7500, 10000, 12500, 15000, 20000, 25000],
    "AM": [2400, 3000, 4000, 6000, 8000, 10000, 12000, 15000],
    "USB": [1500, 1800, 2100, 2400, 2700, 3000, 3300],
    "LSB": [1500, 1800, 2100, 2400, 2700, 3000, 3300],
    "CW": [250, 500, 750, 1000, 1500, 2400],
}

_css_loaded = False


def _ensure_css():
    global _css_loaded
    if _css_loaded:
        return
    prov = Gtk.CssProvider()
    prov.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


CSS = r"""
.fp-chassis {
    background-image: linear-gradient(to bottom, #2b3036, #1a1d21 45%, #0f1114 100%);
    border: 1px solid #050607; border-radius: 10px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.10), 0 4px 14px rgba(0,0,0,0.55);
    padding: 10px 14px 12px 14px;
}
.fp-r9500 { background-image: linear-gradient(to bottom, #262b31, #171a1e 45%, #0d0f12 100%); }
.fp-r7100 { background-image: linear-gradient(to bottom, #44453f, #2a2b26 45%, #181915 100%); }
.fp-r2500 { background-image: linear-gradient(to bottom, #26282b, #17181a 46%, #0b0c0d 100%); }
.fp-brand { color: #e6eef6; font-family: "DejaVu Sans", sans-serif; font-size: 17pt; font-weight: 800; }
.fp-model { color: #ffffff; font-family: "DejaVu Sans", sans-serif; font-size: 13pt; font-weight: 600; }
.fp-sub { color: #8f9aa5; font-family: "DejaVu Sans", sans-serif; font-size: 7.5pt; }
.fp-caption { color: #9aa6b2; font-family: "DejaVu Sans", sans-serif; font-size: 7.5pt; font-weight: 600; }
.fp-plate {
    background-image: linear-gradient(to bottom, #2d333a, #1a1e23);
    border: 1px solid #080a0c; border-radius: 6px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.07);
    padding: 7px 9px;
}
.fp-r7100 .fp-plate { background-image: linear-gradient(to bottom, #4a4b45, #2d2e29); border-color: #121310; }
.fp-r2500 .fp-plate { background-image: linear-gradient(to bottom, #2b2d30, #191a1c); }
.fp-screen { background: #04060a; border: 1px solid #000000; border-radius: 4px; padding: 5px;
    box-shadow: inset 0 1px 2px rgba(255,255,255,0.06), inset 0 0 24px rgba(0,0,0,0.9); }
.fp-r7100 .fp-screen { border-radius: 3px; padding: 4px; }
button.fp-key {
    background-image: linear-gradient(to bottom, #3b424b, #1d2228);
    color: #dbe5ee; border: 1px solid #07090b; border-radius: 3px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.12), 0 1px 2px rgba(0,0,0,0.6);
    font-family: "DejaVu Sans", sans-serif; font-size: 8pt; font-weight: 600;
    padding: 2px 6px; min-height: 25px;
}
button.fp-key:hover { background-image: linear-gradient(to bottom, #4a525d, #262c33); }
button.fp-key:active { background-image: linear-gradient(to bottom, #15191e, #0d1013); box-shadow: inset 0 2px 4px rgba(0,0,0,0.8); }
button.fp-key.on {
    color: #c8f2ff; background-image: linear-gradient(to bottom, #155777, #0a2d40);
    border-color: #2d7ea6; box-shadow: inset 0 0 10px rgba(80,200,255,0.40), 0 0 6px rgba(60,180,255,0.25);
}
button.fp-key.power { background-image: linear-gradient(to bottom, #74251f, #3d100d); color: #ffd9d3; border-color: #250806; }
button.fp-key.power.on { background-image: linear-gradient(to bottom, #cf3a2b, #7d170f); color: #ffffff;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), 0 0 14px rgba(255,70,45,0.65); }
button.fp-key.mode { min-width: 46px; }
button.fp-key.fn { min-height: 30px; font-size: 7.5pt; }
button.fp-key.big { min-height: 52px; font-size: 10pt; }
button.fp-key.scan { background-image: linear-gradient(to bottom, #1e6b43, #0d3722); color: #d6ffe2; border-color: #072415; }
button.fp-key.scan.on { background-image: linear-gradient(to bottom, #2fbf6a, #12602f); color: #ffffff;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), 0 0 14px rgba(50,220,110,0.6); }
.fp-r7100 button.fp-key { background-image: linear-gradient(to bottom, #4e4f49, #2b2c27); color: #ecece2; border-color: #11120f; border-radius: 2px; }
.fp-r7100 button.fp-key:hover { background-image: linear-gradient(to bottom, #5b5c55, #363731); }
.fp-r7100 button.fp-key.on { color: #c9ffc9; background-image: linear-gradient(to bottom, #2c5a2e, #142e16); border-color: #3d7a40; }
.fp-r7100 button.fp-key.power { background-image: linear-gradient(to bottom, #6e2a22, #3a120e); color: #ffd8d0; }
.fp-r2500 button.fp-key.on { color: #bfeaff; background-image: linear-gradient(to bottom, #155a7c, #092c3e); border-color: #2a7fa6; }
.fp-r2500 button.fp-key.scan { background-image: linear-gradient(to bottom, #166b7c, #07333c); color: #d2f8ff; border-color: #06242a; }
.fp-r2500 button.fp-key.scan.on { background-image: linear-gradient(to bottom, #25b9d6, #0a5d70); color: #ffffff; }
.fp-entry { background: #04101c; color: #bfe9ff; border: 1px solid #2b5d80; border-radius: 2px;
    font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; padding: 2px 5px; min-height: 22px; }
.fp-r7100 .fp-entry { background: #0a1a0c; color: #9dffa8; border-color: #2f6b36; }
.fp-led-label { color: #919ca7; font-family: "DejaVu Sans", sans-serif; font-size: 6.8pt; font-weight: 600; }
.fp-dim1 { background: rgba(0,0,0,0.20); }
.fp-dim2 { background: rgba(0,0,0,0.42); }
.fp-hit { background: transparent; border-bottom: 1px solid rgba(255,255,255,0.05); padding: 1px 2px; }
.fp-hit:hover { background: rgba(90,190,255,0.10); }
.fp-hit-name { color: #cfe4f2; font-size: 8pt; }
.fp-hit-sub { color: #7f95a6; font-size: 7pt; font-family: "DejaVu Sans Mono", monospace; }
.fp-r7100 .fp-hit-name { color: #d8f2d5; }
.fp-r2500 .fp-hit-name { color: #c9e9f7; }
"""


# ── små ritprimitiv ───────────────────────────────────────────────────────────
def _rr(cr, x, y, w, h, r):
    """Rundad rektangel."""
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _mix(c1, c2, t):
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def _fmt_freq(hz):
    s = f"{hz / 1e6:.6f}"
    a, b = s.split(".")
    return f"{a}.{b[:3]} {b[3:]}"


def _fmt_hz(hz):
    if hz >= 1e6:
        return f"{hz / 1e6:.4f}".rstrip("0").rstrip(".") + " MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.1f}".rstrip("0").rstrip(".") + " kHz"
    return f"{hz:.0f} Hz"


class Screw(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_size_request(13, 13)
        self.set_draw_func(self._draw)

    def _draw(self, a, cr, w, h):
        cx, cy, r = w / 2, h / 2, min(w, h) / 2 - 1
        g = cairo.RadialGradient(cx - r * .35, cy - r * .35, r * .1, cx, cy, r)
        g.add_color_stop_rgb(0, .62, .65, .68)
        g.add_color_stop_rgb(.7, .30, .32, .35)
        g.add_color_stop_rgb(1, .12, .13, .14)
        cr.set_source(g)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, .75)
        cr.set_line_width(1.6)
        cr.move_to(cx - r * .62, cy - r * .18)
        cr.line_to(cx + r * .62, cy + r * .18)
        cr.stroke()


class Led(Gtk.DrawingArea):
    def __init__(self, color=(.35, 1, .55), size=11):
        super().__init__()
        self.color = color
        self.on = False
        self.set_size_request(size, size)
        self.set_draw_func(self._draw)

    def set_on(self, on):
        on = bool(on)
        if on != self.on:
            self.on = on
            self.queue_draw()

    def _draw(self, a, cr, w, h):
        cx, cy, r = w / 2, h / 2, min(w, h) / 2 - 2
        cr.set_source_rgba(0, 0, 0, .75)
        cr.arc(cx, cy + 1, r + 1.5, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(.09, .10, .10)
        cr.arc(cx, cy, r + .5, 0, 2 * math.pi)
        cr.fill()
        if self.on:
            for k, al in ((3.0, .10), (2.1, .18), (1.4, .30)):
                cr.set_source_rgba(*self.color, al)
                cr.arc(cx, cy, r + k, 0, 2 * math.pi)
                cr.fill()
            cr.set_source_rgb(*self.color)
        else:
            cr.set_source_rgb(*[c * .22 for c in self.color])
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, .55 if self.on else .12)
        cr.arc(cx - r * .28, cy - r * .3, r * .28, 0, 2 * math.pi)
        cr.fill()


class Knob(Gtk.DrawingArea):
    """Ratt som dras upp/ner eller med mushjulet. Värdet skickas till callbacken."""

    def __init__(self, label, lo, hi, value, on_change, size=72, fmt="{:.0f}",
                 step=None, accent=(.36, .80, 1.0), dark=False, suffix=""):
        super().__init__()
        self.label, self.lo, self.hi = label, float(lo), float(hi)
        self.value, self.on_change = float(value), on_change
        self.size, self.fmt, self.step = size, fmt, step
        self.accent, self.dark, self.suffix = accent, dark, suffix
        self.dragging = self.locked = False
        self._start = self.value
        self.set_size_request(size + 14, size + 39)
        self.set_draw_func(self._draw)
        self.set_cursor_from_name("ns-resize")
        self.set_tooltip_text(f"{label} – dra upp/ner eller använd mushjulet")
        dr = Gtk.GestureDrag(button=1)
        dr.connect("drag-begin", self._begin)
        dr.connect("drag-update", self._drag)
        dr.connect("drag-end", self._end)
        self.add_controller(dr)
        sc = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        sc.connect("scroll", self._scroll)
        self.add_controller(sc)

    def set_value(self, v, notify=False):
        v = max(self.lo, min(self.hi, float(v)))
        if self.step:
            v = round(v / self.step) * self.step
            v = max(self.lo, min(self.hi, v))
        changed = abs(v - self.value) > 1e-9
        self.value = v
        if changed:
            self.queue_draw()
        if notify and changed and self.on_change and not self.locked:
            self.on_change(v)

    def _begin(self, g, x, y):
        self.dragging, self._start = True, self.value

    def _drag(self, g, dx, dy):
        if not self.locked:
            self.set_value(self._start - dy * (self.hi - self.lo) / 150.0, notify=True)

    def _end(self, g, dx, dy):
        self.dragging = False

    def _scroll(self, ctrl, dx, dy):
        if self.locked:
            return True
        inc = self.step or (self.hi - self.lo) / 40.0
        self.set_value(self.value - dy * inc, notify=True)
        return True

    def _draw(self, a, cr, w, h):
        s = self.size
        cx, cy, r = w / 2, s / 2 + 2, s / 2 - 5
        a0, a1 = math.radians(135), math.radians(405)
        frac = 0 if self.hi == self.lo else (self.value - self.lo) / (self.hi - self.lo)
        ang = a0 + (a1 - a0) * max(0, min(1, frac))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        # skala
        for i in range(11):
            aa = a0 + (a1 - a0) * i / 10
            r1, r2 = r + 2, r + (7 if i % 5 == 0 else 5)
            cr.set_source_rgba(.72, .78, .84, .45 if i % 5 else .75)
            cr.set_line_width(1.6 if i % 5 == 0 else 1.0)
            cr.move_to(cx + math.cos(aa) * r1, cy + math.sin(aa) * r1)
            cr.line_to(cx + math.cos(aa) * r2, cy + math.sin(aa) * r2)
            cr.stroke()

        # spår + aktiv båge
        cr.set_source_rgba(0, 0, 0, .55)
        cr.set_line_width(4.2)
        cr.arc(cx, cy, r + 1.5, a0, a1)
        cr.stroke()
        cr.set_source_rgba(*self.accent, .85)
        cr.set_line_width(3.2)
        cr.arc(cx, cy, r + 1.5, a0, ang)
        cr.stroke()

        # kropp
        if self.dark:
            stops = ((0, (.40, .41, .40)), (.65, (.20, .21, .20)), (1, (.07, .075, .07)))
        else:
            stops = ((0, (.60, .63, .66)), (.55, (.31, .34, .37)), (1, (.10, .11, .12)))
        g = cairo.RadialGradient(cx - r * .34, cy - r * .38, r * .08, cx, cy, r * .98)
        for p, c in stops:
            g.add_color_stop_rgb(p, *c)
        cr.set_source(g)
        cr.arc(cx, cy, r * .90, 0, 2 * math.pi)
        cr.fill()

        # räfflor
        cr.set_line_width(1)
        for i in range(56):
            aa = 2 * math.pi * i / 56
            cr.set_source_rgba(0, 0, 0, .30 if i % 2 else .14)
            cr.move_to(cx + math.cos(aa) * r * .83, cy + math.sin(aa) * r * .83)
            cr.line_to(cx + math.cos(aa) * r * .90, cy + math.sin(aa) * r * .90)
            cr.stroke()

        cr.set_source_rgba(0, 0, 0, .5)
        cr.set_line_width(1)
        cr.arc(cx, cy, r * .90, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, .13)
        cr.arc(cx, cy, r * .72, math.pi * 1.05, math.pi * 1.75)
        cr.stroke()

        # pekare
        cr.set_source_rgba(*self.accent, .30)
        cr.set_line_width(7)
        cr.move_to(cx + math.cos(ang) * r * .30, cy + math.sin(ang) * r * .30)
        cr.line_to(cx + math.cos(ang) * r * .78, cy + math.sin(ang) * r * .78)
        cr.stroke()
        cr.set_source_rgb(*self.accent)
        cr.set_line_width(3)
        cr.move_to(cx + math.cos(ang) * r * .30, cy + math.sin(ang) * r * .30)
        cr.line_to(cx + math.cos(ang) * r * .78, cy + math.sin(ang) * r * .78)
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, .65)
        cr.arc(cx, cy, 2.4, 0, 2 * math.pi)
        cr.fill()

        # text
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(8.0)
        ext = cr.text_extents(self.label)
        cr.set_source_rgba(.72, .78, .84, .85)
        cr.move_to(cx - ext[2] / 2 - ext[0], s + 11)
        cr.show_text(self.label)
        txt = self.fmt.format(self.value) + self.suffix
        cr.set_font_size(8.6)
        ext = cr.text_extents(txt)
        cr.set_source_rgb(*self.accent)
        cr.move_to(cx - ext[2] / 2 - ext[0], s + 27)
        cr.show_text(txt)


class MainDial(Gtk.DrawingArea):
    """Stor huvudratt. Ett hack = ett frekvenssteg."""

    def __init__(self, on_turn, size=150, dark=False, accent=(.36, .80, 1.0)):
        super().__init__()
        self.on_turn, self.size, self.dark, self.accent = on_turn, size, dark, accent
        self.locked = False
        self._acc = 0.0
        self.set_size_request(size, size + 20)
        self.set_draw_func(self._draw)
        self.set_cursor_from_name("ew-resize")
        self.set_tooltip_text("Huvudratt – vrid med mushjulet eller dra i sidled")
        dr = Gtk.GestureDrag(button=1)
        dr.connect("drag-begin", self._begin)
        dr.connect("drag-update", self._drag)
        dr.connect("drag-end", self._end)
        self.add_controller(dr)
        sc = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        sc.connect("scroll", self._scroll)
        self.add_controller(sc)

    def _begin(self, g, x, y):
        self._acc = 0.0

    def _drag(self, g, dx, dy):
        if self.locked:
            return
        self._acc += dx
        per = 6.0
        n = int(self._acc / per)
        if n:
            self._acc -= n * per
            self.on_turn(n)

    def _end(self, g, dx, dy):
        self._acc = 0.0

    def _scroll(self, ctrl, dx, dy):
        if not self.locked:
            self.on_turn(1 if dy < 0 else -1)
        return True

    def _draw(self, a, cr, w, h):
        s = self.size
        cx, cy, r = w / 2, s / 2 + 2, s / 2 - 10
        cr.set_source_rgba(0, 0, 0, .45)
        cr.arc(cx, cy + 2, r + 3, 0, 2 * math.pi)
        cr.fill()
        base = (.34, .35, .34) if self.dark else (.22, .24, .26)
        g = cairo.RadialGradient(cx - r * .35, cy - r * .4, r * .05, cx, cy, r)
        g.add_color_stop_rgb(0, *(c * 2.0 for c in base))
        g.add_color_stop_rgb(.55, *base)
        g.add_color_stop_rgb(1, *(c * .35 for c in base))
        cr.set_source(g)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()
        # ytterkant
        cr.set_source_rgba(0, 0, 0, .65)
        cr.set_line_width(2)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        # räfflor
        for i in range(84):
            aa = 2 * math.pi * i / 84
            cr.set_source_rgba(0, 0, 0, .32 if i % 2 else .16)
            cr.set_line_width(1)
            cr.move_to(cx + math.cos(aa) * r * .92, cy + math.sin(aa) * r * .92)
            cr.line_to(cx + math.cos(aa) * r, cy + math.sin(aa) * r)
            cr.stroke()
        # inre skiva
        g = cairo.RadialGradient(cx - r * .25, cy - r * .3, r * .05, cx, cy, r * .72)
        g.add_color_stop_rgb(0, .44, .46, .48)
        g.add_color_stop_rgb(.6, .24, .26, .28)
        g.add_color_stop_rgb(1, .12, .13, .14)
        cr.set_source(g)
        cr.arc(cx, cy, r * .70, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, .45)
        cr.set_line_width(1)
        cr.arc(cx, cy, r * .70, 0, 2 * math.pi)
        cr.stroke()
        # fingergrepp
        aa = math.radians(215)
        for k in range(3):
            ox = math.cos(aa) * (r * .33 + k * 5)
            oy = math.sin(aa) * (r * .33 + k * 5)
            cr.set_source_rgba(0, 0, 0, .35)
            cr.set_line_width(4)
            cr.move_to(cx + ox - 8, cy + oy)
            cr.line_to(cx + ox + 8, cy + oy)
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, .10)
            cr.set_line_width(1.2)
            cr.move_to(cx + ox - 8, cy + oy - 1)
            cr.line_to(cx + ox + 8, cy + oy - 1)
            cr.stroke()
        # RECEIVING UNIT
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(r * .15)
        ext = cr.text_extents("RECEIVER")
        cr.set_source_rgba(.78, .82, .86, .70)
        cr.move_to(cx - ext[2] / 2 - ext[0], cy + ext[3] / 2 + r * .06)
        cr.show_text("RECEIVER")
        cr.set_font_size(7.5)
        ext = cr.text_extents("MAIN DIAL")
        cr.set_source_rgba(.72, .78, .84, .75)
        cr.move_to(cx - ext[2] / 2 - ext[0], s + 13)
        cr.show_text("MAIN DIAL")


# ── display ───────────────────────────────────────────────────────────────────
class PanelDisplay(Gtk.DrawingArea):
    """Ritar respektive modells LCD/TFT och läser läget direkt från programmet."""

    def __init__(self, panel, model):
        super().__init__()
        self.panel, self.main, self.model = panel, panel.main, model
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_size_request(520, 240)
        self.set_draw_func(self._draw)

    # -- gemensamt --
    def _level(self):
        try:
            return max(0.0, min(1.0, (float(self.main.engine.level) + 110.0) / 100.0))
        except Exception:
            return 0.0

    def _glow_text(self, cr, txt, x, y, size, color, glow=.45, align="left"):
        cr.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(size)
        ext = cr.text_extents(txt)
        if align == "center":
            x -= ext[2] / 2 + ext[0]
        elif align == "right":
            x -= ext[2] + ext[0]
        for ox, oy in ((1.3, 0), (-1.3, 0), (0, 1.3), (0, -1.3), (2.2, 2.2)):
            cr.set_source_rgba(*color, .07 * glow)
            cr.move_to(x + ox, y + oy)
            cr.show_text(txt)
        cr.set_source_rgb(*color)
        cr.move_to(x, y)
        cr.show_text(txt)
        return ext[2]

    def _meter(self, cr, x, y, w, h, color):
        n = 18
        v = self._level()
        on = int(round(v * n))
        gap = 2.0
        bw = (w - gap * (n - 1)) / n
        for i in range(n):
            xx = x + i * (bw + gap)
            cr.set_source_rgba(.10, .13, .16, .95)
            _rr(cr, xx, y, bw, h, 1.2)
            cr.fill()
            if i < on:
                cc = color if i < 12 else ((1.0, .72, .25) if i < 16 else (1.0, .32, .24))
                cr.set_source_rgb(*cc)
                _rr(cr, xx, y, bw, h, 1.2)
                cr.fill()
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(6.0, min(8.0, h * 1.2)))
        labels = (("S1", 0.00), ("3", .16), ("5", .32), ("7", .48), ("9", .66), ("+20", .79), ("+40", .89), ("+60", 1.0))
        for t, f in labels:
            ext = cr.text_extents(t)
            cr.set_source_rgba(.62, .70, .78, .8)
            cr.move_to(x + f * (w - ext[2]) - ext[0], y + h + 9)
            cr.show_text(t)

    def _scope(self, cr, x, y, w, h, accent, waterfall=True, label="SPECTRUM SCOPE"):
        w, h = max(20, int(w)), max(20, int(h))
        cr.set_source_rgba(.006, .018, .035, 1)
        _rr(cr, x, y, w, h, 2)
        cr.fill()
        # rutnät
        cr.set_line_width(1)
        for i in range(1, 10):
            cr.set_source_rgba(*accent, .08)
            xx = x + w * i / 10
            cr.move_to(xx, y)
            cr.line_to(xx, y + h)
            cr.stroke()
        for i in range(1, 4):
            yy = y + h * i / 4
            cr.set_source_rgba(*accent, .06)
            cr.move_to(x, yy)
            cr.line_to(x + w, yy)
            cr.stroke()
        disp = getattr(self.main.view, "disp", None)
        if disp is None or len(disp) < 8:
            cr.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(9)
            ext = cr.text_extents("INGEN SIGNAL ÄNNU")
            cr.set_source_rgba(*accent, .35)
            cr.move_to(x + (w - ext[2]) / 2, y + h / 2)
            cr.show_text("INGEN SIGNAL ÄNNU")
            return
        try:
            n = len(disp)
            npx = max(2, int(w))
            xs = np.linspace(0, n - 1, npx)
            vals = np.interp(xs, np.arange(n), disp)
            dbmin = float(self.main.conf.get("db_min", -100.0))
            dbmax = float(self.main.conf.get("db_max", -20.0))
            rng = max(1.0, dbmax - dbmin)
            yy = y + h * np.clip((dbmax - vals) / rng, 0, 1)
            wf_h = 0.34 * h if waterfall else 0.0
            spec_h = h - wf_h
            yy = y + spec_h * np.clip((dbmax - vals) / rng, 0, 1)
            cr.move_to(x, y + spec_h)
            for i in range(npx):
                cr.line_to(x + i, yy[i])
            cr.line_to(x + w, y + spec_h)
            cr.close_path()
            g = cairo.LinearGradient(0, y, 0, y + spec_h)
            g.add_color_stop_rgba(0, *accent, .55)
            g.add_color_stop_rgba(1, *accent, .04)
            cr.set_source(g)
            cr.fill()
            cr.set_source_rgba(*accent, .95)
            cr.set_line_width(1.4)
            cr.move_to(x, yy[0])
            for i in range(1, npx):
                cr.line_to(x + i, yy[i])
            cr.stroke()
            if waterfall:
                wf = getattr(self.main.view, "wf", None)
                lut = getattr(self.main.view, "lut", None)
                if wf is not None and lut is not None and len(wf):
                    rows = min(56, int(wf.shape[0]))
                    cols = np.linspace(0, wf.shape[1] - 1, max(2, int(w))).astype(np.int64)
                    img = np.ascontiguousarray(lut[wf[:rows][:, cols]])
                    surf = cairo.ImageSurface.create_for_data(memoryview(img), cairo.FORMAT_ARGB32,
                                                              int(w), rows, int(w) * 4)
                    cr.save()
                    cr.rectangle(x, y + spec_h, w, wf_h)
                    cr.clip()
                    cr.translate(x, y + spec_h)
                    cr.scale(1, wf_h / rows)
                    cr.set_source_surface(surf, 0, 0)
                    cr.get_source().set_filter(cairo.FILTER_NEAREST)
                    cr.paint()
                    cr.restore()
        except Exception:
            log.debug("Kunde inte rita skopet", exc_info=True)
        # markör för inställd frekvens
        c = getattr(self.main, "wf_center", self.main.freq)
        span = float(getattr(self.main.view, "wf_span", 1.0))
        if span > 0 and abs(self.main.freq - c) < span / 2:
            xx = x + (self.main.freq - c + span / 2) / span * w
            cr.set_source_rgba(1.0, .62, .15, .9)
            cr.set_line_width(1.3)
            cr.move_to(xx, y)
            cr.line_to(xx, y + h)
            cr.stroke()
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(6.8)
        cr.set_source_rgba(*accent, .40)
        cr.move_to(x + 5, y + 10)
        cr.show_text(label)
        cr.set_source_rgba(*accent, .30)
        cr.set_line_width(1)
        _rr(cr, x + .5, y + .5, w - 1, h - 1, 2)
        cr.stroke()

    def _draw(self, area, cr, w, h):
        if self.model == "r9500":
            self._r9500(cr, w, h)
        elif self.model == "r7100":
            self._r7100(cr, w, h)
        else:
            self._r2500(cr, w, h)

    # -- Panel 1: mörk TFT med cyan siffror och skop --
    def _r9500(self, cr, w, h):
        g = cairo.LinearGradient(0, 0, 0, h)
        g.add_color_stop_rgb(0, .010, .026, .048)
        g.add_color_stop_rgb(.6, .006, .016, .032)
        g.add_color_stop_rgb(1, .003, .009, .020)
        cr.set_source(g)
        cr.paint()
        accent = (.45, .84, 1.0)
        cr.set_source_rgba(*accent, .28)
        cr.set_line_width(1)
        cr.rectangle(w - 1.5, 3, 3, h - 6)
        cr.stroke()

        # statusrad
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(7.2)
        mode = self.main.engine.mode
        cr.set_source_rgba(*accent, .8)
        cr.move_to(9, 13)
        cr.show_text("VFO")
        try:
            band = self.main.panel_band_name(self.main.freq)
        except Exception:
            band = ""
        cr.set_source_rgba(.62, .78, .92, .7)
        cr.move_to(44, 13)
        cr.show_text((band or "").upper()[:26])
        # indikatorer
        flags = []
        if self.main.engine.running:
            flags.append(("RX", (.45, 1, .55)))
        if getattr(self.main.scanner, "active", False):
            flags.append(("SCAN", (.55, .95, 1)))
        if self.main.btn_rec.get_active():
            flags.append(("REC", (1, .35, .3)))
        if self.panel.locked:
            flags.append(("LOCK", (1, .75, .3)))
        if self.panel.monitor:
            flags.append(("MONI", (1, .7, .25)))
        xx = w - 8
        for txt, col in reversed(flags):
            cr.set_font_size(6.8)
            ext = cr.text_extents(txt)
            cr.set_source_rgb(*col)
            cr.move_to(xx - ext[2] - ext[0], 13)
            cr.show_text(txt)
            xx -= ext[2] + 10
        cr.set_source_rgba(*accent, .18)
        cr.set_line_width(1)
        cr.move_to(7, 18.5)
        cr.line_to(w - 7, 18.5)
        cr.stroke()

        scope = getattr(self.panel, "scope_on", True)
        left_w = w * .53 if scope else w - 18
        if self.panel.entry:
            self._entry_box(cr, 12, 36, left_w - 10, 54, accent)
        fy = 36 + 48
        txt = _fmt_freq(self.main.freq)
        self._glow_text(cr, txt, 12, fy, min(37, (w + h) * .055), (.72, .93, 1.0), glow=.55)
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(7.2)
        cr.set_source_rgba(.62, .78, .92, .72)
        cr.move_to(13, fy + 16)
        cr.show_text("MHz")
        # läge / filter / steg
        step = self.main.step()
        cr.set_font_size(8.2)
        cr.set_source_rgb(1.0, .72, .28)
        cr.move_to(13, fy + 34)
        cr.show_text(mode)
        cr.set_source_rgba(.66, .82, .95, .82)
        cr.move_to(46, fy + 34)
        cr.show_text(f"BW {self.main.engine.bw / 1000:.1f}k   STEP {step / 1000:g}k   AGC "
                     f"{'AUTO' if self.main.gain_state()[0] else 'MAN'}")
        # S-meter
        self._meter(cr, 13, fy + 46, min(left_w - 22, 330), 9, accent)

        if scope:
            sx = left_w + 6
            self._scope(cr, sx, 26, w - sx - 10, h - 50, accent)
        else:
            # informationsruta i stället för skop
            bx, by, bw, bh = 13, fy + 66, left_w - 24, h - (fy + 78)
            cr.set_source_rgba(*accent, .05)
            _rr(cr, bx, by, bw, bh, 3)
            cr.fill()
            cr.set_source_rgba(*accent, .18)
            cr.stroke()
            cr.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(8.2)
            c = self.main.conf
            lines = [
                ("Bokmärken", f"{len(c.get('bookmarks', []))} st"),
                ("Skanner", "aktiv" if getattr(self.main.scanner, "active", False) else "av"),
                ("Squelch", f"{self.main.engine.squelch:.0f} dB"),
                ("Volym", f"{c.get('volume', 80)} %"),
                ("Förstärkning", "AGC" if self.main.gain_state()[0] else f"{self.main.gain_state()[1]:.1f} dB"),
            ]
            for i, (k, v) in enumerate(lines):
                yy = by + 15 + i * 15
                cr.set_source_rgba(.55, .70, .85, .65)
                cr.move_to(bx + 10, yy)
                cr.show_text(k)
                cr.set_source_rgba(.82, .92, 1.0, .9)
                cr.move_to(bx + 100, yy)
                cr.show_text(v)

        # nederst: träffar/status
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(7.0)
        st = getattr(self.main.scanner, "status", "") or ""
        cr.set_source_rgba(.62, .82, .95, .78)
        cr.move_to(9, h - 8)
        cr.show_text(st[:90])
        if getattr(self.main.engine, "sq_open", False) and self.main.engine.squelch > -150:
            cr.set_source_rgb(.45, 1, .55)
            cr.move_to(w - 84, h - 8)
            cr.show_text("● SIGNAL")

    def _entry_box(self, cr, x, y, w, h, accent, label="INMATNING", unit="MHz"):
        cr.set_source_rgba(*accent, .10)
        _rr(cr, x, y, w, h, 3)
        cr.fill()
        cr.set_source_rgba(*accent, .55)
        cr.set_line_width(1.2)
        cr.stroke()
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(7)
        cr.set_source_rgba(*accent, .75)
        cr.move_to(x + 6, y + 11)
        cr.show_text(label)
        self._glow_text(cr, (self.panel.entry or "–"), x + 8, y + h - 11,
                        min(24, h * .48), (.78, .95, 1.0), glow=.4)
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(7)
        ext = cr.text_extents(f"{unit} · ENT")
        cr.set_source_rgba(*accent, .6)
        cr.move_to(x + w - ext[2] - 8, y + h - 8)
        cr.show_text(f"{unit} · ENT")

    # -- Panel 2: grön LCD --
    def _r7100(self, cr, w, h):
        g = cairo.LinearGradient(0, 0, 0, h)
        g.add_color_stop_rgb(0, .035, .095, .045)
        g.add_color_stop_rgb(.65, .022, .062, .028)
        g.add_color_stop_rgb(1, .012, .035, .016)
        cr.set_source(g)
        cr.paint()
        green = (.55, 1.0, .60)
        # LCD-pixlar
        cr.set_line_width(.5)
        for x in range(0, w, 3):
            cr.set_source_rgba(.55, 1, .6, .018)
            cr.move_to(x + .5, 0)
            cr.line_to(x + .5, h)
            cr.stroke()
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(7.2)
        cr.set_source_rgba(*green, .8)
        cr.move_to(8, 13)
        cr.show_text("VFO")
        try:
            band = self.main.panel_band_name(self.main.freq)
        except Exception:
            band = ""
        cr.set_source_rgba(*green, .55)
        cr.move_to(45, 13)
        cr.show_text((band or "").upper()[:28])
        cr.set_source_rgba(*green, .25)
        cr.move_to(7, 18.5)
        cr.line_to(w - 7, 18.5)
        cr.stroke()

        if self.panel.entry:
            self._entry_box(cr, 10, 26, w - 20, 48, green)
        fy = 72
        self._glow_text(cr, _fmt_freq(self.main.freq), 10, fy, min(34, w * .058), (.62, 1, .66), glow=.5)
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(7)
        cr.set_source_rgba(*green, .55)
        cr.move_to(11, fy + 14)
        cr.show_text("MHz")
        mode = self.main.engine.mode
        step = self.main.step()
        cr.set_font_size(8.4)
        cr.set_source_rgb(.78, 1, .72)
        cr.move_to(11, fy + 31)
        cr.show_text(f"{mode}   BW {self.main.engine.bw / 1000:.2f} kHz   STEP {step / 1000:g} kHz")
        cr.set_source_rgba(*green, .9)
        self._meter(cr, 11, fy + 43, min(w - 30, 420), 10, green)
        # minnesrad
        c = self.main.conf
        bms = c.get("bookmarks", [])
        cr.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(7.4)
        cr.set_source_rgba(*green, .62)
        name = ""
        for bm in bms:
            if abs(bm.get("freq", 0) - self.main.freq) < 1:
                name = bm.get("name", "")
                break
        cr.move_to(11, fy + 72)
        cr.show_text(f"MEM {len(bms):4d}   {name[:42]}")
        cr.set_source_rgba(*green, .30)
        cr.move_to(7, h - 17)
        cr.line_to(w - 7, h - 17)
        cr.stroke()
        cr.set_font_size(7.0)
        cr.set_source_rgba(*green, .70)
        st = getattr(self.main.scanner, "status", "") or ""
        cr.move_to(9, h - 6)
        cr.show_text(st[:80])
        if self.main.engine.sq_open and self.main.engine.squelch > -150:
            cr.set_source_rgb(.72, 1, .55)
            cr.move_to(w - 78, h - 6)
            cr.show_text("● SIGNAL")

    # -- Panel 3: blå skanner-LCD --
    def _r2500(self, cr, w, h):
        g = cairo.LinearGradient(0, 0, 0, h)
        g.add_color_stop_rgb(0, .025, .085, .155)
        g.add_color_stop_rgb(.6, .010, .040, .085)
        g.add_color_stop_rgb(1, .004, .018, .045)
        cr.set_source(g)
        cr.paint()
        blue = (.36, .78, 1.0)
        cr.set_line_width(.5)
        for x in range(0, w, 3):
            cr.set_source_rgba(.4, .8, 1, .018)
            cr.move_to(x + .5, 0)
            cr.line_to(x + .5, h)
            cr.stroke()

        sc = self.main.scanner
        state = "STOPPAD"
        col = (.55, .72, .88)
        if sc.active:
            if sc.state == "listen":
                state, col = "SIGNAL", (.55, 1, .55)
            elif sc.state == "prio":
                state, col = "PRIORITET", (1, .78, .3)
            else:
                state, col = "SKANNAR", (.45, .9, 1)
            if getattr(self.main, "_panel_scan_pause", False):
                state, col = "PAUS", (1, .75, .3)
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(8.2)
        cr.set_source_rgb(*col)
        cr.move_to(8, 14)
        cr.show_text(state)
        cr.set_source_rgba(*blue, .65)
        cr.set_font_size(7.0)
        cr.move_to(72, 14)
        cr.show_text("VFO")
        try:
            band = self.main.panel_band_name(self.main.freq)
        except Exception:
            band = ""
        cr.move_to(102, 14)
        cr.show_text((band or "").upper()[:30])
        if sc.active:
            cr.set_source_rgba(*blue, .8)
            cr.set_font_size(7)
            cr.move_to(w - 78, 14)
            cr.show_text(f"TRÄFFAR {len(sc.hits):3d}")
        cr.set_source_rgba(*blue, .22)
        cr.move_to(7, 19.5)
        cr.line_to(w - 7, 19.5)
        cr.stroke()

        if self.panel.entry:
            self._entry_box(cr, 10, 26, w - 20, 47, blue,
                            "INMATNING " + self.panel.num_target.upper(), self.panel.num_unit())
        fy = 74
        self._glow_text(cr, _fmt_freq(self.main.freq), 10, fy, min(30, w * .055), (.68, .92, 1.0), glow=.55)
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(6.8)
        cr.set_source_rgba(*blue, .55)
        cr.move_to(11, fy + 13)
        cr.show_text("MHz")
        mode = self.main.engine.mode
        c = self.main.conf
        cr.set_font_size(7.8)
        cr.set_source_rgba(.72, .92, 1.0, .9)
        cr.move_to(11, fy + 29)
        if int(c.get("scan_mode", 0)) == 0:
            rng = f"BOKMÄRKEN  grupp: {c.get('scan_group') or 'alla'}"
        else:
            rng = f"INTERVALL {c.get('scan_start')}–{c.get('scan_stop')} MHz  steg {c.get('scan_step_khz')} kHz"
        cr.show_text(f"{mode}   {rng}")
        cr.set_source_rgba(*blue, .9)
        self._meter(cr, 11, fy + 39, min(w - 24, 340), 8, blue)
        # skopremsa
        sz = 62 if h > 210 else 40
        self._scope(cr, 10, h - sz - 20, w - 20, sz, blue, waterfall=True, label="BAND SCOPE")
        cr.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(6.6)
        cr.set_source_rgba(*blue, .65)
        cr.move_to(9, h - 7)
        txt = (sc.status or "Starta mottagningen och tryck SCAN")[:74]
        cr.show_text(txt)


# ── fönstret ──────────────────────────────────────────────────────────────────
class FrontPanel(Adw.Window):
    """Ett fristående frontpanelfönster som styr MainWindow."""

    def __init__(self, main, model="r9500"):
        model = model if model in MODELS else "r9500"
        m = MODELS[model]
        super().__init__(title=f"{m['namn']} – frontpanel", transient_for=main,
                         default_width=m["bredd"], default_height=m["hojd"])
        self.main = main
        self.model = model
        self.entry = ""
        self.num_target = "start"
        self.locked = False
        self.dim = 0
        self.scope_on = True
        self.monitor = False
        self.mhz_mode = False
        self._monitor_saved = None
        self._timer = None
        self._closed = False
        self._knobs, self._dials, self._mode_btns = {}, [], {}
        self._hit_gen, self._hit_sig = -1, None
        self.target_btns = {}
        self.power_btn = self.scan_btn = self.rec_btn = self.agc_btn = None
        self.scope_btn = self.lock_btn = self.moni_btn = self.mhz_btn = None
        _ensure_css()
        self._build_shell()
        self._build()
        self._timer = GLib.timeout_add(90, self._tick)
        self.connect("close-request", self._on_close)
        kc = Gtk.EventControllerKey()
        kc.connect("key-pressed", self._on_key)
        self.add_controller(kc)

    # -- skal och modellval --
    def _build_shell(self):
        self.tv = Adw.ToolbarView()
        self.hb = Adw.HeaderBar()
        tb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl = Gtk.Label(label="FRONTPANEL")
        lbl.add_css_class("fp-caption")
        tb.append(lbl)
        self._model_keys = list(MODELS)
        self.model_dd = Gtk.DropDown(model=Gtk.StringList.new([MODELS[k]["namn"] for k in self._model_keys]))
        self.model_dd.set_selected(self._model_keys.index(self.model))
        self.model_dd.set_tooltip_text("Byt frontpanel – reglagen styr samma mottagare")
        self.model_dd.connect("notify::selected", self._on_model_selected)
        tb.append(self.model_dd)
        self.hb.set_title_widget(tb)
        help_btn = Gtk.Button(icon_name="help-about-symbolic")
        help_btn.set_tooltip_text("Vad gör reglagen?")
        help_btn.connect("clicked", lambda *_: self._help())
        self.hb.pack_end(help_btn)
        self.tv.add_top_bar(self.hb)
        self.overlay = Gtk.Overlay()
        self.dim_overlay = Gtk.Box()
        self.dim_overlay.set_can_target(False)
        self.dim_overlay.set_hexpand(True)
        self.dim_overlay.set_vexpand(True)
        self._dim_added = False
        self.tv.set_content(self.overlay)
        self.set_content(self.tv)

    def _on_model_selected(self, dd, _p):
        i = dd.get_selected()
        if 0 <= i < len(self._model_keys):
            self.set_model(self._model_keys[i])

    def set_model(self, model):
        if model not in MODELS:
            return
        self.model = model
        i = self._model_keys.index(model)
        if self.model_dd.get_selected() != i:
            self.model_dd.set_selected(i)
        self.set_title(f"{MODELS[model]['namn']} – frontpanel")
        self._build()
        self.set_default_size(MODELS[model]["bredd"], MODELS[model]["hojd"])
        self.present()

    def _build(self):
        self._knobs.clear()
        self._dials.clear()
        self._mode_btns.clear()
        self.target_btns.clear()
        self._hit_gen = -1
        self.hit_box = None
        if self.model == "r9500":
            chassis = self._build_r9500()
        elif self.model == "r7100":
            chassis = self._build_r7100()
        else:
            chassis = self._build_r2500()
        chassis.set_hexpand(True)
        chassis.set_vexpand(True)
        self.overlay.set_child(chassis)
        if not self._dim_added:
            self.overlay.add_overlay(self.dim_overlay)
            self._dim_added = True
        self._apply_dim()
        self._sync_buttons()

    # -- små byggstenar --
    def _key(self, label, cb, cls="", tip="", width=None, height=None):
        b = Gtk.Button(label=label)
        b.add_css_class("fp-key")
        for c in cls.split():
            if c:
                b.add_css_class(c)
        if tip:
            b.set_tooltip_text(tip)
        b.connect("clicked", lambda *_: cb())
        if width:
            b.set_size_request(width, -1)
        if height:
            b.set_size_request(-1, height)
        return b

    def _knob(self, key, label, lo, hi, value, cb, **kw):
        k = Knob(label, lo, hi, value, lambda v: self._guard() and cb(v), **kw)
        if self.locked:
            k.locked = True
        self._knobs[key] = k
        return k

    def _dial(self, cb, **kw):
        d = MainDial(lambda n: self._guard() and cb(n), **kw)
        d.locked = self.locked
        self._dials.append(d)
        return d

    @staticmethod
    def _label(text, cls="fp-caption"):
        lb = Gtk.Label(label=text, xalign=0)
        lb.add_css_class(cls)
        return lb

    def _plate(self, child, spacing=6):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        box.add_css_class("fp-plate")
        box.append(child)
        return box

    def _topline(self):
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.set_margin_bottom(8)
        brand = Gtk.Label(label="RECEIVER", xalign=0)
        brand.add_css_class("fp-brand")
        top.append(brand)
        model = Gtk.Label(label=MODELS[self.model]["kort"], xalign=0)
        model.add_css_class("fp-model")
        top.append(model)
        sub = Gtk.Label(label=MODELS[self.model]["undertitel"], xalign=0, valign=Gtk.Align.END)
        sub.add_css_class("fp-sub")
        sub.set_margin_bottom(2)
        top.append(sub)
        top.append(Gtk.Box(hexpand=True))
        self.led_power = Led((.35, 1, .55), 10)
        top.append(self.led_power)
        top.append(self._label("POWER", "fp-led-label"))
        return top

    def _entry(self, text="", width=92, tip=""):
        e = Gtk.Entry()
        e.set_text(text)
        e.add_css_class("fp-entry")
        e.set_size_request(width, -1)
        if tip:
            e.set_tooltip_text(tip)
        return e

    # -- åtgärder --
    def _guard(self):
        if self.locked:
            self.main.toast("Frontpanelen är låst – tryck LOCK först")
            return False
        return True

    def _power(self):
        if not self._guard():
            return
        b = self.main.btn_start
        b.set_active(not b.get_active())

    def _turn(self, n):
        if not self._guard():
            return
        self.main.nudge_freq(n, 1e6 if self.mhz_mode else None)

    def _mode(self, mode):
        if self._guard():
            self.main.set_mode(mode)

    def _step(self, d):
        if self._guard():
            self.main.panel_step(d)

    def _bw(self, d):
        if self._guard():
            self.main.panel_bw_step(d)

    def _scan(self):
        if self._guard():
            self.main.panel_scan_toggle()

    def _skip(self):
        if self._guard():
            self.main.scanner.skip()

    def _rec(self):
        if self._guard():
            self.main.panel_toggle_rec()

    def _agc(self):
        if self._guard():
            self.main.panel_toggle_agc()

    def _store(self):
        if self._guard():
            self.main.panel_store_bookmark()

    def _recall(self, d=1):
        if self._guard():
            self.main.panel_recall_bookmark(d)

    def _clear_bm(self):
        if self._guard():
            self.main.panel_clear_bookmark()

    def _band(self, d):
        if self._guard():
            self.main.panel_band_step(d)

    def _dwell(self):
        if self._guard():
            self.main.panel_dwell_cycle()

    def _monitor_press(self, btn):
        if not self._guard():
            return
        e = self.main.engine
        self._monitor_saved = e.squelch
        e.squelch = -150.0
        self.monitor = True
        self._sync_buttons()

    def _monitor_release(self, btn):
        if self.monitor:
            self.main.engine.squelch = (self._monitor_saved if self._monitor_saved is not None
                                        else self.main.conf["squelch"])
            self.monitor = False
            self._sync_buttons()

    def _dimmer(self):
        self.dim = (self.dim + 1) % 3
        self._apply_dim()
        self.main.toast(("Normal belysning", "Dämpad belysning", "Mörkt läge")[self.dim])

    def _apply_dim(self):
        for c in ("fp-dim1", "fp-dim2"):
            self.dim_overlay.remove_css_class(c)
        if self.dim == 1:
            self.dim_overlay.add_css_class("fp-dim1")
        elif self.dim == 2:
            self.dim_overlay.add_css_class("fp-dim2")

    def _toggle_lock(self):
        self.locked = not self.locked
        for k in self._knobs.values():
            k.locked = self.locked
        for d in self._dials:
            d.locked = self.locked
        self.main.toast("Frontpanelen är låst" if self.locked else "Frontpanelen är upplåst")
        self._sync_buttons()

    def _toggle_scope(self):
        self.scope_on = not self.scope_on
        if getattr(self, "disp", None):
            self.disp.queue_draw()
        self._sync_buttons()

    def _toggle_mhz(self):
        self.mhz_mode = not self.mhz_mode
        self.main.toast("Huvudratten stegar 1 MHz" if self.mhz_mode else "Huvudratten stegar valt frekvenssteg")
        self._sync_buttons()

    # -- numerisk inmatning --
    def num_unit(self):
        return "MHz" if self.num_target in ("start", "stop") else "kHz"

    def _num(self, ch):
        if not self._guard() or len(self.entry) >= 10:
            return
        if ch == "." and "." in self.entry:
            return
        if ch == "." and not self.entry:
            self.entry = "0."
        else:
            self.entry += ch
        if getattr(self, "disp", None):
            self.disp.queue_draw()

    def _back(self):
        self.entry = self.entry[:-1]
        if getattr(self, "disp", None):
            self.disp.queue_draw()

    def _clr(self):
        self.entry = ""
        if getattr(self, "disp", None):
            self.disp.queue_draw()

    def _commit(self):
        if not self._guard() or not self.entry:
            return
        try:
            v = float(self.entry.replace(",", "."))
        except ValueError:
            self.main.toast("Ogiltig inmatning")
            self._clr()
            return
        if self.model == "r2500":
            if self.num_target in ("start", "stop"):
                if not (0.0 < v < 3000.0):
                    self.main.toast("Ange frekvens i MHz, t.ex. 144.5")
                    return
                self.main.panel_scan_range(self.num_target, v)
            else:
                if not (0.01 <= v <= 1000.0):
                    self.main.toast("Ange kanalsteg i kHz, t.ex. 12.5")
                    return
                self.main.panel_scan_conf("scan_step_khz", v)
                self.main.toast(f"Kanalsteg {v:g} kHz")
        else:
            if not (0.01 <= v <= 3000.0):
                self.main.toast("Ange frekvens i MHz, t.ex. 145.500")
                return
            self.main.tune(round(v * 1e6))
        self.entry = ""
        if getattr(self, "disp", None):
            self.disp.queue_draw()

    def _set_target(self, target):
        self.num_target = target
        self.entry = ""
        self._sync_buttons()
        if getattr(self, "disp", None):
            self.disp.queue_draw()
        self.main.toast({"start": "Skriv skannerns startfrekvens i MHz",
                         "stop": "Skriv skannerns stoppfrekvens i MHz",
                         "step": "Skriv kanalsteget i kHz"}[target])

    def _keypad(self):
        """Numeriskt tangentbord (0–9, ., ENT) som används av alla modeller."""
        g = Gtk.Grid(column_spacing=4, row_spacing=4)
        for r, row in enumerate(("123", "456", "789", ".0")):
            for c, ch in enumerate(row):
                g.attach(self._key(ch, lambda ch=ch: self._num(ch), "fn"), c, r, 1, 1)
        g.attach(self._key("←", self._back, "fn", "Radera sista siffran"), 2, 3, 1, 1)
        g.attach(self._key("CLR", self._clr, "fn", "Rensa inmatningen"), 0, 4, 1, 1)
        g.attach(self._key("ENT", self._commit, "fn", "Verkställ inmatningen"), 1, 4, 2, 1)
        return g

    def _on_key(self, ctrl, keyval, keycode, state):
        focus = self.get_focus()
        if focus is not None and isinstance(focus, (Gtk.Entry, Gtk.DropDown, Gtk.Editable)):
            return False
        name = Gdk.keyval_name(keyval)
        if name and len(name) == 1 and name.isdigit():
            self._num(name)
            return True
        if name in ("period", "KP_Decimal", "comma"):
            self._num(".")
            return True
        if name in ("Return", "KP_Enter"):
            self._commit()
            return True
        if name == "Escape":
            self._clr()
            return True
        if name == "BackSpace":
            self._back()
            return True
        if name == "Up":
            self._step(1)
            return True
        if name == "Down":
            self._step(-1)
            return True
        if name == "Left":
            self._turn(-1)
            return True
        if name == "Right":
            self._turn(1)
            return True
        return False

    def _help(self):
        dlg = Adw.MessageDialog(transient_for=self, heading="Frontpanelen",
                                body=("Alla reglage styr den körande mottagaren direkt.\n\n"
                                      "AF – volym. SQL – squelch. RF – förstärkning (stänger av AGC).\n"
                                      "Huvudratten – ett hack = ett frekvenssteg. Mushjulet fungerar också.\n"
                                      "Siffertangentbordet – skriv MHz och tryck ENT (t.ex. 145.500).\n"
                                      "Läge, filter och steg – ändrar demodulatorn och visningen.\n"
                                      "MW – sparar aktuell frekvens som bokmärke. M-CL – tar bort det sista.\n"
                                      "M≈V / VFO-M – hämtar nästa bokmärke.\n"
                                      "SCAN – startar/stoppar skanningen. SKIP – spärrar frekvensen.\n"
                                      "LOCK – låser panelen. DIMMER – tre belysningslägen. MONI – öppnar squelchen så länge du håller.\n"
                                      "Skannerpanelen är bara för skannern: START/STOP/STEP väljer vad siffertangentbordet skriver in."))
        dlg.add_response("ok", "Stäng")
        dlg.present()

    # -- synk --
    def _sync_buttons(self):
        m = self.main
        for mode, b in self._mode_btns.items():
            if m.engine.mode == mode:
                b.add_css_class("on")
            else:
                b.remove_css_class("on")
        states = ((getattr(self, "scan_btn", None), getattr(m.scanner, "active", False)),
                  (getattr(self, "rec_btn", None), m.btn_rec.get_active()),
                  (getattr(self, "agc_btn", None), m.gain_state()[0]),
                  (getattr(self, "scope_btn", None), self.scope_on),
                  (getattr(self, "lock_btn", None), self.locked),
                  (getattr(self, "power_btn", None), m.engine.running),
                  (getattr(self, "moni_btn", None), self.monitor),
                  (getattr(self, "mhz_btn", None), self.mhz_mode))
        for b, on in states:
            if b is None:
                continue
            if on:
                b.add_css_class("on")
            else:
                b.remove_css_class("on")
        for t, b in self.target_btns.items():
            if t == self.num_target:
                b.add_css_class("on")
            else:
                b.remove_css_class("on")
        sm = getattr(self, "scanmode_btn", None)
        if sm is not None:
            sm.set_label("INTERVALL" if int(m.conf.get("scan_mode", 0)) else "BOKMÄRKEN")
        gb = getattr(self, "group_btn", None)
        if gb is not None:
            gb.set_label((m.conf.get("scan_group") or "ALLA GRUPPER").upper()[:18])
        for name, on in (("led_power", m.engine.running),
                         ("led_scan", getattr(m.scanner, "active", False)),
                         ("led_rec", m.btn_rec.get_active()),
                         ("led_lock", self.locked),
                         ("led_signal", bool(m.engine.sq_open and m.engine.squelch > -150))):
            led = getattr(self, name, None)
            if led is not None:
                led.set_on(on)

    def _tick(self):
        if self._closed:
            return False
        try:
            c = self.main.conf
            vals = {"vol": c.get("volume", 80), "sql": c.get("squelch", -150.0), "rf": self.main.gain_state()[1],
                    "dwell": c.get("scan_dwell_ms", 300), "hold": c.get("scan_hold", 2.0),
                    "snr": c.get("scan_snr", 10.0), "step": c.get("scan_step_khz", 12.5)}
            for key, k in self._knobs.items():
                if key in vals and not k.dragging:
                    k.set_value(vals[key])
            if getattr(self, "disp", None):
                self.disp.queue_draw()
            self._sync_buttons()
            if self.model == "r2500":
                self._refresh_hits()
        except Exception:
            log.debug("Frontpanelen kunde inte uppdateras", exc_info=True)
        return True

    def _refresh_hits(self):
        sc = self.main.scanner
        sig = (sc.hits_gen, len(sc.hits))
        if sig == self._hit_sig or self.hit_box is None:
            return
        self._hit_sig = sig
        while (ch := self.hit_box.get_first_child()) is not None:
            self.hit_box.remove(ch)
        hits = sorted(sc.hits.values(), key=lambda h: h.get("last", 0), reverse=True)[:12]
        if not hits:
            lb = Gtk.Label(label="Inga träffar ännu", xalign=0)
            lb.add_css_class("fp-hit-sub")
            self.hit_box.append(lb)
            return
        for h in hits:
            b = Gtk.Button()
            b.add_css_class("flat")
            b.add_css_class("fp-hit")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            name = Gtk.Label(label=(h.get("namn") or "Signal")[:22], xalign=0, hexpand=True)
            name.add_css_class("fp-hit-name")
            row.append(name)
            sub = Gtk.Label(label=f"{h.get('freq', 0) / 1e6:.5f}  {h.get('max_snr', 0):.0f} dB", xalign=1)
            sub.add_css_class("fp-hit-sub")
            row.append(sub)
            b.set_child(row)
            b.set_tooltip_text("Stanna skanningen och lyssna på frekvensen")
            b.connect("clicked", lambda _b, h=h: self._goto_hit(h))
            self.hit_box.append(b)

    def _goto_hit(self, h):
        if self.main.scanner.active:
            self.main._toggle_scan()
        self.main.tune(h["freq"], h.get("mode"))
        self.main.toast(f"{h['freq'] / 1e6:.5f} MHz – {h.get('namn') or ''}")

    def _on_close(self, *_):
        self._closed = True
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None
        if self.monitor:
            self._monitor_release(None)
        if getattr(self.main, "panel_win", None) is self:
            self.main.panel_win = None
        return False

    # -- Panel 1 --
    def _build_r9500(self):
        c = self.main.conf
        ch = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ch.add_css_class("fp-chassis")
        ch.add_css_class("fp-r9500")
        ch.append(self._topline())

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        body.set_vexpand(True)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        left.set_size_request(96, -1)
        self.power_btn = self._key("POWER", self._power, "power big",
                                   "Starta/stoppa mottagningen")
        self.power_btn.set_size_request(92, 54)
        left.append(self.power_btn)
        self.led_signal = Led((.35, 1, .55), 10)
        self.led_scan = Led((.35, .85, 1), 10)
        self.led_rec = Led((1, .32, .25), 10)
        self.led_lock = Led((1, .75, .25), 10)
        for text, led in (("SIGNAL", self.led_signal), ("SCAN", self.led_scan),
                          ("REC", self.led_rec), ("LOCK", self.led_lock)):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            row.append(led)
            row.append(self._label(text, "fp-led-label"))
            left.append(row)
        left.append(Gtk.Box(vexpand=True))
        self.moni_btn = Gtk.Button(label="MONI")
        self.moni_btn.add_css_class("fp-key")
        self.moni_btn.add_css_class("fn")
        self.moni_btn.set_tooltip_text("Håll inne för att öppna squelchen")
        gpress = Gtk.GestureClick()
        gpress.connect("pressed", lambda g, n, x, y: self._monitor_press(None))
        gpress.connect("released", lambda g, n, x, y: self._monitor_release(None))
        self.moni_btn.add_controller(gpress)
        left.append(self.moni_btn)
        self.rec_btn = self._key("REC", self._rec, "fn", "Spela in ljudet till WAV")
        left.append(self.rec_btn)
        self.scope_btn = self._key("M.SCOPE", self._toggle_scope, "fn", "Visa/dölj skopet i displayen")
        left.append(self.scope_btn)
        body.append(left)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        center.set_hexpand(True)
        bez = Gtk.Box()
        bez.add_css_class("fp-screen")
        self.disp = PanelDisplay(self, "r9500")
        self.disp.set_size_request(560, 300)
        bez.append(self.disp)
        center.append(bez)

        soft = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        soft.set_homogeneous(True)
        fkeys = (("F-1", "SKOP", self._toggle_scope), ("F-2", "SPARA", self._store),
                 ("F-3", "HÄMTA", lambda: self._recall(1)),
                 ("F-4", "NIVÅ", lambda: self._guard() and self.main.auto_level()),
                 ("F-5", "AGC", self._agc), ("F-6", "SKANNA", self._scan), ("F-7", "SPELA IN", self._rec))
        for name, sub, fn in fkeys:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            box.append(self._key(name, fn, "fn", f"{name}: {sub}"))
            sl = Gtk.Label(label=sub)
            sl.add_css_class("fp-led-label")
            box.append(sl)
            soft.append(box)
        center.append(soft)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        row.append(self._label("MODE"))
        for mode in ("WFM", "NFM", "AM", "USB", "LSB", "CW"):
            b = self._key(mode, lambda m=mode: self._mode(m), "mode", f"Modulation {mode}")
            self._mode_btns[mode] = b
            row.append(b)
        row.append(Gtk.Box(hexpand=True))
        row.append(self._key("FILTER –", lambda: self._bw(-1), "fn", "Smalkare bandbredd"))
        row.append(self._key("FILTER +", lambda: self._bw(1), "fn", "Bredare bandbredd"))
        row.append(self._key("STEP –", lambda: self._step(-1), "fn", "Mindre frekvenssteg"))
        row.append(self._key("STEP +", lambda: self._step(1), "fn", "Större frekvenssteg"))
        center.append(row)
        body.append(center)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        right.set_size_request(330, -1)
        knobs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        knobs.set_homogeneous(True)
        knobs.append(self._knob("vol", "AF", 0, 300, c.get("volume", 80),
                                lambda v: self.main.panel_set_volume(v), fmt="{:.0f}", step=5,
                                accent=(.36, .80, 1.0)))
        knobs.append(self._knob("sql", "SQL", -150, 0, c.get("squelch", -150),
                                lambda v: self.main.panel_set_squelch(v), fmt="{:.0f}", step=1,
                                accent=(.36, .80, 1.0)))
        knobs.append(self._knob("rf", "RF", 0, self.main.gain_max(), self.main.gain_state()[1],
                                lambda v: self.main.panel_set_gain(v), fmt="{:.1f}", step=.5,
                                accent=(.36, .80, 1.0)))
        right.append(knobs)
        agcrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.agc_btn = self._key("AGC", self._agc, "fn", "Automatisk förstärkning av/på")
        agcrow.append(self.agc_btn)
        agcrow.append(self._label("AUTO / MANUELL"))
        agcrow.append(Gtk.Box(hexpand=True))
        right.append(agcrow)
        right.append(self._keypad())
        mem = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        mem.append(self._key("VFO/M", lambda: self._recall(1), "fn", "Hämta nästa bokmärke"))
        mem.append(self._key("MW", self._store, "fn", "Spara aktuell frekvens som bokmärke"))
        mem.append(self._key("M-CL", self._clear_bm, "fn", "Ta bort det senaste bokmärket"))
        mem.append(self._key("M≈V", lambda: self._recall(-1), "fn", "Hämta föregående bokmärke"))
        right.append(mem)
        scanrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        self.scan_btn = self._key("SCAN", self._scan, "scan fn", "Starta/stoppa skanningen")
        scanrow.append(self.scan_btn)
        scanrow.append(self._key("SPEED/DELAY", self._dwell, "fn", "Växlar tid per skanningsfönster"))
        right.append(scanrow)
        body.append(right)
        ch.append(body)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.lock_btn = self._key("LOCK", self._toggle_lock, "fn", "Lås/lås upp frontpanelen")
        bottom.append(self.lock_btn)
        bottom.append(self._key("DIMMER", self._dimmer, "fn", "Växla belysning"))
        bottom.append(self._key("SKIP", self._skip, "fn", "Spärra frekvensen och fortsätt skanna"))
        bottom.append(self._key("SET", self._help, "fn", "Hjälp om reglagen"))
        bottom.append(self._key("EXIT", self.close, "fn", "Stäng frontpanelen"))
        bottom.append(Gtk.Box(hexpand=True))
        bottom.append(self._dial(self._turn, size=140, accent=(.36, .80, 1.0)))
        ch.append(bottom)
        return ch

    def _mode_cycle(self):
        if not self._guard():
            return
        modes = ["WFM", "NFM", "AM", "USB", "LSB", "CW"]
        i = modes.index(self.main.engine.mode) if self.main.engine.mode in modes else 0
        self._mode(modes[(i + 1) % len(modes)])

    def _scan_mode_cycle(self):
        if not self._guard():
            return
        self.main.panel_scan_mode(0 if int(self.main.conf.get("scan_mode", 0)) else 1)

    def _group_cycle(self):
        if self._guard():
            self.main.panel_scan_group(1)

    def _apply_range(self):
        if not self._guard():
            return
        try:
            a = float(self.range_start.get_text().replace(",", "."))
            b = float(self.range_stop.get_text().replace(",", "."))
        except ValueError:
            self.main.toast("Ange start och stopp i MHz, t.ex. 144.0 och 146.0")
            return
        if not (0 < a < b < 3000):
            self.main.toast("Stoppfrekvensen måste vara högre än startfrekvensen")
            return
        self.main.panel_scan_range("start", a)
        self.main.panel_scan_range("stop", b)
        self.main.toast(f"Skanningsområde {a:g}–{b:g} MHz")

    # -- Panel 2 --
    def _build_r7100(self):
        c = self.main.conf
        ch = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ch.add_css_class("fp-chassis")
        ch.add_css_class("fp-r7100")
        ch.append(self._topline())
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        body.set_vexpand(True)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        left.set_hexpand(True)
        bez = Gtk.Box()
        bez.add_css_class("fp-screen")
        self.disp = PanelDisplay(self, "r7100")
        self.disp.set_size_request(620, 272)
        bez.append(self.disp)
        left.append(bez)

        knobs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, homogeneous=True)
        knobs.append(self._knob("vol", "AF", 0, 300, c.get("volume", 80),
                                lambda v: self.main.panel_set_volume(v), fmt="{:.0f}", step=5,
                                dark=True, accent=(.55, 1.0, .60)))
        knobs.append(self._knob("sql", "SQL", -150, 0, c.get("squelch", -150),
                                lambda v: self.main.panel_set_squelch(v), fmt="{:.0f}", step=1,
                                dark=True, accent=(.55, 1.0, .60)))
        knobs.append(self._knob("rf", "RF", 0, self.main.gain_max(), self.main.gain_state()[1],
                                lambda v: self.main.panel_set_gain(v), fmt="{:.1f}", step=.5,
                                dark=True, accent=(.55, 1.0, .60)))
        knobs.append(self._knob("vol", "AF", 0, 300, c.get("volume", 80),
                                lambda v: self.main.panel_set_volume(v), fmt="{:.0f}", step=5,
                                dark=True, accent=(.55, 1.0, .60)))
        left.append(knobs)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        r1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        r1.append(self._key("MODE ↻", self._mode_cycle, "fn", "Byt modulation"))
        r1.append(self._key("STEP ↻", lambda: self._step(1), "fn", "Byt frekvenssteg"))
        r1.append(self._key("BAND ▼", lambda: self._band(-1), "fn", "Föregående band"))
        r1.append(self._key("BAND ▲", lambda: self._band(1), "fn", "Nästa band"))
        self.scan_btn = self._key("SCAN", self._scan, "scan fn", "Starta/stoppa skanningen")
        r1.append(self.scan_btn)
        self.mhz_btn = self._key("MHz", self._toggle_mhz, "fn", "Huvudratten stegar 1 MHz")
        r1.append(self.mhz_btn)
        rows.append(r1)
        r2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        r2.append(self._key("FILTER –", lambda: self._bw(-1), "fn", "Smalkare bandbredd"))
        r2.append(self._key("FILTER +", lambda: self._bw(1), "fn", "Bredare bandbredd"))
        self.agc_btn = self._key("AGC", self._agc, "fn", "Automatisk förstärkning av/på")
        r2.append(self.agc_btn)
        self.rec_btn = self._key("REC", self._rec, "fn", "Spela in ljudet")
        r2.append(self.rec_btn)
        self.lock_btn = self._key("LOCK", self._toggle_lock, "fn", "Lås frontpanelen")
        r2.append(self.lock_btn)
        r2.append(self._key("LAMP", self._dimmer, "fn", "Växla belysning"))
        rows.append(r2)
        r3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        r3.append(self._key("SKIP", self._skip, "fn", "Spärra frekvensen"))
        r3.append(self._key("DIMMER", self._dimmer, "fn", "Växla belysning"))
        r3.append(self._key("SET", self._help, "fn", "Hjälp om reglagen"))
        r3.append(self._key("EXIT", self.close, "fn", "Stäng frontpanelen"))
        rows.append(r3)
        left.append(rows)
        body.append(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        right.set_size_request(250, -1)
        right.append(self._label("SIFFERINMATNING  ·  MHz + ENT"))
        right.append(self._keypad())
        mem = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        mem.append(self._key("VFO/M", lambda: self._recall(1), "fn", "Hämta nästa bokmärke"))
        mem.append(self._key("MW", self._store, "fn", "Spara bokmärke"))
        mem.append(self._key("M-CL", self._clear_bm, "fn", "Ta bort senaste bokmärket"))
        right.append(mem)
        right.append(Gtk.Box(vexpand=True))
        right.append(self._dial(self._turn, size=150, dark=True, accent=(.55, 1.0, .60)))
        self.power_btn = self._key("POWER", self._power, "power big", "Starta/stoppa mottagningen")
        self.power_btn.set_size_request(-1, 48)
        right.append(self.power_btn)
        body.append(right)
        ch.append(body)
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.led_signal = Led((.55, 1, .6), 9)
        self.led_scan = Led((.55, .95, 1), 9)
        self.led_rec = Led((1, .35, .28), 9)
        self.led_lock = Led((1, .78, .3), 9)
        for text, led in (("SIGNAL", self.led_signal), ("SCAN", self.led_scan),
                          ("REC", self.led_rec), ("LOCK", self.led_lock)):
            foot.append(led)
            foot.append(self._label(text, "fp-led-label"))
        foot.append(Gtk.Box(hexpand=True))
        foot.append(self._label("MOTTAGARE 2 · 25–2000 MHz · AM / FM / WFM / SSB / CW"))
        ch.append(foot)
        return ch

    # -- Panel 3 (endast skannern) --
    def _build_r2500(self):
        c = self.main.conf
        ch = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ch.add_css_class("fp-chassis")
        ch.add_css_class("fp-r2500")
        ch.append(self._topline())
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        body.set_vexpand(True)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_hexpand(True)
        bez = Gtk.Box()
        bez.add_css_class("fp-screen")
        self.disp = PanelDisplay(self, "r2500")
        self.disp.set_size_request(520, 236)
        bez.append(self.disp)
        left.append(bez)

        lower = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        knobs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, homogeneous=True)
        knobs.append(self._knob("dwell", "DWELL", 100, 2000, c.get("scan_dwell_ms", 300),
                                lambda v: self.main.panel_scan_conf("scan_dwell_ms", int(v)),
                                size=64, fmt="{:.0f}", step=50, suffix=" ms"))
        knobs.append(self._knob("hold", "HOLD", 0, 15, c.get("scan_hold", 2.0),
                                lambda v: self.main.panel_scan_conf("scan_hold", float(v)),
                                size=64, fmt="{:.1f}", step=.5, suffix=" s"))
        knobs.append(self._knob("snr", "TRÖSKEL", 3, 40, c.get("scan_snr", 10.0),
                                lambda v: self.main.panel_scan_conf("scan_snr", float(v)),
                                size=64, fmt="{:.0f}", step=1, suffix=" dB"))
        lower.append(knobs)

        hits = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        hits.set_hexpand(True)
        hits.append(self._label("TRÄFFLISTA  ·  klicka för att lyssna"))
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(118)
        sw.set_vexpand(True)
        self.hit_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        sw.set_child(self.hit_box)
        hits.append(sw)
        lower.append(hits)
        left.append(lower)
        body.append(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_size_request(300, -1)
        self.scan_btn = self._key("SCAN", self._scan, "scan big", "Starta/stoppa skanningen")
        self.scan_btn.set_size_request(-1, 50)
        right.append(self.scan_btn)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        row.append(self._key("SKIP", self._skip, "fn", "Spärra frekvensen och fortsätt"))
        self.group_btn = self._key("ALLA GRUPPER", self._group_cycle, "fn", "Byt bokmärkesgrupp")
        row.append(self.group_btn)
        right.append(row)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        self.scanmode_btn = self._key("BOKMÄRKEN", self._scan_mode_cycle, "fn",
                                      "Växla mellan bokmärken och frekvensintervall")
        row.append(self.scanmode_btn)
        row.append(self._key("MW", self._store, "fn", "Spara aktuell frekvens som bokmärke"))
        right.append(row)

        plate = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        plate.add_css_class("fp-plate")
        plate.append(self._label("SKANNINGSOMRÅDE (MHz)  ·  eller använd START/STOP + siffrorna"))
        er = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.range_start = self._entry(str(c.get("scan_start", "144.0")), 76, "Startfrekvens i MHz")
        self.range_stop = self._entry(str(c.get("scan_stop", "146.0")), 76, "Stoppfrekvens i MHz")
        er.append(self._label("START"))
        er.append(self.range_start)
        er.append(self._label("STOPP"))
        er.append(self.range_stop)
        er.append(self._key("SET", self._apply_range, "fn", "Använd intervallet"))
        plate.append(er)
        right.append(plate)

        right.append(self._label("SIFFERINMATNING  ·  välj START / STOP / STEG först"))
        targets = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        for key, text, tip in (("start", "START", "Siffrorna anger startfrekvens i MHz"),
                               ("stop", "STOPP", "Siffrorna anger stoppfrekvens i MHz"),
                               ("step", "STEG", "Siffrorna anger kanalsteg i kHz")):
            b = self._key(text, lambda k=key: self._set_target(k), "fn", tip)
            self.target_btns[key] = b
            targets.append(b)
        right.append(targets)
        right.append(self._keypad())

        util = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        self.power_btn = self._key("POWER", self._power, "power fn", "Starta/stoppa mottagningen")
        util.append(self.power_btn)
        self.lock_btn = self._key("LOCK", self._toggle_lock, "fn", "Lås frontpanelen")
        util.append(self.lock_btn)
        util.append(self._key("LAMP", self._dimmer, "fn", "Växla belysning"))
        util.append(self._key("EXIT", self.close, "fn", "Stäng frontpanelen"))
        right.append(util)
        body.append(right)
        ch.append(body)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.led_signal = Led((.4, .9, 1), 9)
        self.led_scan = Led((.4, .9, 1), 9)
        self.led_lock = Led((1, .78, .3), 9)
        for text, led in (("SIGNAL", self.led_signal), ("SCAN", self.led_scan), ("LOCK", self.led_lock)):
            foot.append(led)
            foot.append(self._label(text, "fp-led-label"))
        foot.append(Gtk.Box(hexpand=True))
        foot.append(self._label("MOTTAGARE 3 · skannerpanel · siffertangentbordet matar START/STOPP/STEG"))
        ch.append(foot)
        return ch
