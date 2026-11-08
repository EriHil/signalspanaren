#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
Signalspanaren – SDR-mottagare, radioskanner och signallogg för Fedora.

Delar:
  1. Loggning och felrapport
  2. Konfiguration, bandplan, hjälpfunktioner
  3. Mottagarkällor (RTL-SDR via librtlsdr, rtl_tcp, KiwiSDR på nätet, simulator)
  4. DSP-motor (spektrum, demodulering, squelch, inspelning)
  5. Signaldetektor (signalloggen)
  6. Ljudutgång och webbgränssnitt
  7. Grafiskt gränssnitt (GTK4 / libadwaita)
  8. Kommandorad
"""

# ════════════════════════════════════════════════════════════════════
# 1. LOGGNING OCH FELRAPPORT
# ════════════════════════════════════════════════════════════════════
import os
import sys
import logging
import logging.handlers
import traceback
import threading
import atexit

APP_NAME = "Signalspanaren"
APP_ID = "se.eh.Signalspanaren"
VERSION = "2.12.1"

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.join(HOME, ".local/state")), "signalspanaren")
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config")), "signalspanaren")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(STATE_DIR, "signalspanaren.log")
STDERR_FILE = os.path.join(STATE_DIR, "stderr.log")
REPORT_FILE = os.path.join(STATE_DIR, "felrapport.txt")
CONF_FILE = os.path.join(CONF_DIR, "config.json")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.join(HOME, ".cache")), "signalspanaren")
WEBRX_CACHE = os.path.join(CACHE_DIR, "webbmottagare.json")
CSS_FILE = os.path.join(CONF_DIR, "style.css")
MANUAL_FILE = os.path.join(APP_DIR, "MANUAL.txt")

OLD_NAME = "radiospanaren"          # programmet hette Radiospanaren före 2.8.0


def migrate_old_name():
    """Flyttar inställningar, bokmärken, loggar och cache från Radiospanaren-mapparna (före 2.8.0)
    till Signalspanaren-mapparna – bara om de nya inte finns än. Returnerar vad som flyttades."""
    moved = []
    for new in (CONF_DIR, STATE_DIR, CACHE_DIR):
        old = os.path.join(os.path.dirname(new), OLD_NAME)
        if os.path.isdir(old) and not os.path.exists(new):
            try:
                os.rename(old, new)
                moved.append((old, new))
            except OSError as ex:
                print(f"Kunde inte flytta {old} till {new}: {ex}", file=sys.stderr)
    for i in ("", ".1", ".2", ".3"):
        o = os.path.join(STATE_DIR, OLD_NAME + ".log" + i)
        n = LOG_FILE + i
        if os.path.exists(o) and not os.path.exists(n):
            try:
                os.rename(o, n)
            except OSError:
                pass
    return moved


_MIGRATED = migrate_old_name()
os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(CONF_DIR, exist_ok=True)

log = logging.getLogger("signalspanaren")


def _trim_file(path, max_bytes=1_000_000, keep=200_000):
    try:
        if os.path.getsize(path) > max_bytes:
            with open(path, "rb") as f:
                f.seek(-keep, os.SEEK_END)
                data = f.read()
            with open(path, "wb") as f:
                f.write(b"[... aldre rader borttagna ...]\n" + data)
    except OSError:
        pass


def setup_logging(verbose=False):
    _trim_file(STDERR_FILE)
    log.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s: %(message)s"))
    fh.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.addHandler(fh)
    if verbose:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        log.addHandler(sh)

    def excepthook(t, v, tb):
        log.critical("Ohanterat fel:\n%s", "".join(traceback.format_exception(t, v, tb)))
        if _gui_error_hook:
            try:
                _gui_error_hook(f"{t.__name__}: {v}")
            except Exception:
                pass

    def thread_excepthook(args):
        log.critical("Ohanterat fel i tråd %s:\n%s", args.thread.name if args.thread else "?",
                     "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


_gui_error_hook = None


def _tail(path, n):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def build_report(extra=None):
    """Bygger en felrapport som kan klistras in i Claude Code."""
    import platform
    import subprocess
    import datetime
    lines = []
    lines.append(f"=== {APP_NAME} felrapport ({datetime.datetime.now():%Y-%m-%d %H:%M:%S}) ===")
    lines.append(f"Programversion: {VERSION}")
    lines.append(f"Programfil: {os.path.abspath(__file__)}")
    try:
        with open("/etc/os-release") as f:
            osr = dict(l.strip().split("=", 1) for l in f if "=" in l)
        lines.append(f"System: {osr.get('PRETTY_NAME', '?').strip(chr(34))}")
    except OSError:
        pass
    lines.append(f"Kärna: {platform.release()}")
    lines.append(f"Skrivbord: {os.environ.get('XDG_CURRENT_DESKTOP', '?')} / {os.environ.get('XDG_SESSION_TYPE', '?')}")
    lines.append(f"Python: {sys.version.split()[0]}")
    for mod in ("numpy", "scipy"):
        try:
            m = __import__(mod)
            lines.append(f"{mod}: {m.__version__}")
        except Exception as e:
            lines.append(f"{mod}: SAKNAS ({e})")
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Adw
        lines.append(f"GTK: {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}")
        lines.append(f"libadwaita: {Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}")
    except Exception as e:
        lines.append(f"GTK/libadwaita: FEL ({e})")
    try:
        out = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=3).stdout
        sdr = [l for l in out.splitlines() if any(k in l.lower() for k in ("0bda:2838", "0bda:2832", "rtl", "realtek"))]
        lines.append("SDR-enheter (lsusb): " + ("; ".join(sdr) if sdr else "inga hittade"))
    except Exception:
        lines.append("SDR-enheter (lsusb): kunde inte köras")
    try:
        out = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=3).stdout
        if "dvb_usb_rtl28xxu" in out:
            lines.append("VARNING: kärnmodulen dvb_usb_rtl28xxu är laddad (blockerar RTL-SDR)")
    except Exception:
        pass
    if extra:
        lines.append(extra)
    try:
        with open(CONF_FILE) as f:
            lines.append("--- config.json ---")
            lines.append(f.read().strip()[:3000])
    except OSError:
        pass
    lines.append("--- senaste raderna i signalspanaren.log ---")
    lines.extend(l.rstrip("\n") for l in _tail(LOG_FILE, 150))
    err = _tail(STDERR_FILE, 60)
    if err:
        lines.append("--- senaste raderna i stderr.log ---")
        lines.extend(l.rstrip("\n") for l in err)
    lines.append("=== slut på rapport ===")
    text = "\n".join(lines)
    try:
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    return text


# ════════════════════════════════════════════════════════════════════
# 2. KONFIGURATION, BANDPLAN, HJÄLPFUNKTIONER
# ════════════════════════════════════════════════════════════════════
import json
import time
import math
import re
import queue
import socket
import urllib.parse
import struct
import shutil
import subprocess
import wave
import ctypes
import ctypes.util
import datetime
import csv
from collections import deque
import ai

try:
    import numpy as np
    from scipy.signal import firwin, lfilter, butter, oaconvolve
    from scipy import ndimage
    sys.path.insert(0, APP_DIR)
    import avkodare
    import analys
    import analysavkodare
    import iqfil
    import kiwisdr
    import webbmottagare
    import dspfilter
    import flaggor
except ImportError as _e:  # pragma: no cover
    setup_logging()
    log.critical("Saknar Python-paket: %s. Installera med: sudo dnf install python3-numpy python3-scipy", _e)
    print(f"Saknar Python-paket: {_e}\nInstallera med: sudo dnf install python3-numpy python3-scipy", file=sys.stderr)
    sys.exit(1)

SR = 1_536_000          # samplingstakt från SDR:en
BLOCK = 65536           # samples per block (~43 ms)
FFT_N = 4096            # FFT-storlek (375 Hz per bin)
D1 = 4                  # första decimering -> 384 kHz
FS1 = SR // D1
D2 = 8                  # andra decimering -> 48 kHz
AF = 48000              # ljudsamplingstakt
BIN_HZ = SR / FFT_N
CW_TONE = 700.0

MODES = ["WFM", "NFM", "AM", "USB", "LSB", "CW"]
DEFAULT_BW = {"WFM": 180000, "NFM": 12500, "AM": 8000, "USB": 2700, "LSB": 2700, "CW": 500}
FILTER_STEPS = {
    "WFM": [50000, 100000, 150000, 180000, 200000, 250000],
    "NFM": [5000, 7500, 10000, 12500, 15000, 20000, 25000],
    "AM": [2400, 3000, 4000, 6000, 8000, 10000, 12000, 15000],
    "USB": [1500, 1800, 2100, 2400, 2700, 3000, 3300],
    "LSB": [1500, 1800, 2100, 2400, 2700, 3000, 3300],
    "CW": [250, 500, 750, 1000, 1500, 2400],
}
# Sidband för SSB: LSB är brukligt för tal på amatörbanden under 10 MHz (160/80/40 m), utom i
# smalbandiga data-/FT8-delar och på 60 m. Allt annat – 30 m och uppåt, flyg, sjöfart, militärt,
# VOLMET – kör USB. (start, stopp, zon) i Hz; första träffen gäller.
SSB_ZONES = [
    (1.836e6, 1.843e6, "data160", "USB"), (1.810e6, 2.000e6, "ham160", "LSB"),
    (3.570e6, 3.600e6, "data80", "USB"), (3.500e6, 3.800e6, "ham80", "LSB"),
    (7.040e6, 7.050e6, "data40", "USB"), (7.070e6, 7.080e6, "ft8_40", "USB"),
    (7.000e6, 7.200e6, "ham40", "LSB"),
]


def ssb_zone(f):
    """(sidband, zon) för frekvensen f."""
    for a, b, zon, side in SSB_ZONES:
        if a <= f < b:
            return side, zon
    return "USB", "usb"


# AGC-förval: (namn, decay i 1/s). FAST/MID/SLOW ≈ tidskonstanter 0,3/2/6 s.
AGC_PRESETS = [("SDR++ (τ 0,2 s)", 5.0), ("FAST (0,3 s)", 1 / 0.3), ("MID (2 s)", 0.5), ("SLOW (6 s)", 1 / 6),
               ("Eget", None)]

DEFAULT_STEP = {"WFM": 100000, "NFM": 12500, "AM": 5000, "USB": 100, "LSB": 100, "CW": 10}
STEPS = [0, 10, 100, 1000, 5000, 9000, 10000, 12500, 25000, 100000]
STEP_NAMES = ["Auto", "10 Hz", "100 Hz", "1 kHz", "5 kHz", "9 kHz", "10 kHz", "12,5 kHz", "25 kHz", "100 kHz"]
# Förval för intervallskanning: namn, start, stopp (MHz), kanalsteg (kHz), modulering
SCAN_PRESETS = [
    ("Eget intervall", None, None, None, None),
    ("Flyg 118–137 MHz (AM, 25 kHz)", "118.0", "136.975", 25.0, "AM"),
    ("Flyg 118–137 MHz (AM, 8,33 kHz)", "118.0", "136.99167", 25.0 / 3, "AM"),
    ("Militärflyg 225–400 MHz (AM)", "225.0", "399.975", 25.0, "AM"),
    ("Marin VHF 156–162 MHz (NFM)", "156.0", "162.025", 25.0, "NFM"),
    ("2 m amatör 144–146 MHz (NFM)", "144.0", "146.0", 12.5, "NFM"),
    ("70 cm amatör 432–438 MHz (NFM)", "432.0", "438.0", 12.5, "NFM"),
    ("PMR446 (NFM, 16 kanaler)", "446.00625", "446.19375", 12.5, "NFM"),
    ("LPD433 (NFM)", "433.075", "434.775", 25.0, "NFM"),
    ("Jakt/fritid 155,4 MHz (NFM)", "155.400", "155.525", 25.0, "NFM"),
    ("27 MHz PR/CB (NFM)", "26.965", "27.405", 10.0, "NFM"),
]

IQ_FORMATS = [("SigMF cu8 – 8 bitar, 3 MB/s", "sigmf_cu8"), ("SigMF cf32 – flyttal, 12 MB/s", "sigmf_cf32"),
              ("WAV 16 bitar (SDR++/SDR#), 6 MB/s", "wav16")]
SOURCES = ["rtl", "tcp", "kiwi", "sim", "file"]
SOURCE_NAMES = ["RTL-SDR (USB)", "rtl_tcp (nätverk)", "KiwiSDR (webb-SDR på nätet)",
                "Simulator (test utan SDR)", "IQ-fil (uppspelning)"]

# Bandplan (MHz): från, till, namn
BANDPLAN = [
    (0.1485, 0.2835, "LV-rundradio"), (0.5265, 1.6065, "MV-rundradio"), (1.81, 2.0, "160 m amatör"),
    (3.5, 3.8, "80 m amatör"), (5.9, 6.2, "49 m rundradio"), (7.0, 7.2, "40 m amatör"),
    (7.2, 7.45, "41 m rundradio"), (9.4, 9.9, "31 m rundradio"), (10.1, 10.15, "30 m amatör"),
    (11.6, 12.1, "25 m rundradio"), (13.57, 13.87, "22 m rundradio"), (14.0, 14.35, "20 m amatör"),
    (15.1, 15.8, "19 m rundradio"), (18.068, 18.168, "17 m amatör"), (21.0, 21.45, "15 m amatör"),
    (24.89, 24.99, "12 m amatör"), (26.965, 27.405, "27 MHz PR"), (28.0, 29.7, "10 m amatör"),
    (50.0, 52.0, "6 m amatör"), (87.5, 108.0, "FM-rundradio"), (108.0, 118.0, "Flygnavigering"),
    (118.0, 137.0, "Flygradio (AM)"), (137.0, 138.0, "Vädersatelliter"), (144.0, 146.0, "2 m amatör"),
    (156.0, 162.025, "Marin VHF"), (174.0, 240.0, "DAB"), (430.0, 440.0, "70 cm amatör"),
    (433.05, 434.79, "ISM 433"), (446.0, 446.2, "PMR446"), (863.0, 870.0, "SRD 868"),
    (1089.0, 1091.0, "ADS-B"), (1240.0, 1300.0, "23 cm amatör"),
]

BANDPLAN += avkodare.EXTRA_BANDS

DEFAULT_BOOKMARKS = [
    {"name": "Flyg nödfrekvens", "freq": 121.5e6, "mode": "AM"},
    {"name": "Marin VHF kanal 16", "freq": 156.8e6, "mode": "NFM"},
    {"name": "PMR446 kanal 1", "freq": 446.00625e6, "mode": "NFM"},
    {"name": "PMR446 kanal 8", "freq": 446.09375e6, "mode": "NFM"},
    {"name": "2 m anropsfrekvens", "freq": 145.5e6, "mode": "NFM"},
    {"name": "70 cm anropsfrekvens", "freq": 433.5e6, "mode": "NFM"},
    {"name": "ISM 433,92 MHz", "freq": 433.92e6, "mode": "NFM"},
    {"name": "FT8 40 m", "freq": 7.074e6, "mode": "USB"},
    {"name": "FT8 20 m", "freq": 14.074e6, "mode": "USB"},
    {"name": "Simulator: FM-station", "freq": 100.0e6, "mode": "WFM"},
    {"name": "Simulator: AM", "freq": 100.35e6, "mode": "AM"},
    {"name": "Simulator: NFM-skurar", "freq": 100.5e6, "mode": "NFM"},
] + avkodare.EXTRA_BOOKMARKS
BOOKMARK_VERSION = 2

DEFAULT_CONF = {
    "source": "rtl", "tcp_addr": "127.0.0.1:1234", "kiwi_addr": "", "webrx_own": [], "my_pos": "", "auto_gain": True, "gain": 30.0, "ppm": 0,
    "agc_attack": 50.0, "agc_decay": 5.0,
    "kiwi_auto_gain": True, "kiwi_gain": 65.0, "auto_sideband": True, "flaggor_kiwi": True, "flaggor_visa": True, "flaggor_ref": True,
    "dsp_shape": "SHARP", "pbt_lo": 0.0, "pbt_hi": 0.0, "nb_on": False, "nb_level": 50, "nb_depth": 8,
    "nb_width": 20, "nr_on": False, "nr_level": 6, "anotch_on": False, "mnotch_on": False,
    "mnotch_freq": 1000.0, "mnotch_width": "M", "tone_hpf": 0, "tone_lpf": 0, "tone_bass": 0, "tone_treble": 0,
    "direct": 0, "biast": False, "freq": 100.0e6, "mode": "WFM", "bw": None, "squelch": -150.0,
    "volume": 80, "step_idx": 0, "db_min": -100.0, "db_max": -20.0, "zoom": 1, "spec_frac": 0.35,
    "colormap": "spanare", "theme": 0, "skin": "spanare", "analys_bw_idx": 0, "analys_follow": False, "scan_mode": 0, "scan_start": "144.0", "scan_stop": "146.0",
    "scan_sq_mode": 0, "scan_snr": 10.0, "scan_prio": None, "scan_prio_s": 2.0, "scan_group": "", "scan_preset": 0,
    "scan_step_khz": 12.5, "scan_hold": 2.0, "scan_max_listen": 0.0, "scan_dwell_ms": 300, "det_thresh": 12.0,
    "det_enabled": True, "auto_rec": False, "web_enabled": False, "web_port": 8073,
    "web_public": False, "iq_format": 0, "play_file": "", "play_speed": 0, "play_loop": False,
    "play_override": {}, "lockout": [], "bookmarks": DEFAULT_BOOKMARKS, "window": None, "window_max": False, "skarm_lage": "auto", "sidebar_width": 330,
}

COLORMAPS = {
    "spanare": [(0, (2, 6, 14)), (0.2, (4, 30, 60)), (0.4, (0, 110, 130)), (0.6, (40, 220, 170)),
                (0.78, (255, 200, 70)), (0.92, (255, 90, 40)), (1, (255, 245, 230))],
    "klassisk": [(0, (0, 0, 10)), (0.15, (0, 0, 90)), (0.35, (0, 90, 220)), (0.55, (0, 220, 200)),
                 (0.7, (240, 230, 0)), (0.85, (255, 60, 0)), (1, (255, 255, 255))],
    "grå": [(0, (0, 0, 0)), (1, (255, 255, 255))],
    "grön": [(0, (0, 5, 0)), (0.5, (0, 160, 40)), (0.85, (180, 255, 120)), (1, (255, 255, 255))],
    "inferno": [(0, (0, 0, 4)), (0.25, (87, 16, 110)), (0.5, (188, 55, 84)), (0.75, (249, 142, 9)),
                (1, (252, 255, 164))],
}


# Utseenden: färger för spektrumvyn (r, g, b[, a]) och programmets CSS
SKINS = {
    "spanare": dict(namn="Spanare (mörk marin)", bg=(0.02, 0.04, 0.06), spec_bg=(0.015, 0.035, 0.05),
                    grid=(0.4, 0.9, 0.85, 0.07), text=(0.6, 0.95, 0.9, 0.6), axis_bg=(0.035, 0.065, 0.09),
                    fill_top=(0.15, 0.95, 0.75, 0.38), fill_bot=(0.05, 0.35, 0.4, 0.04), line=(0.45, 1.0, 0.85),
                    band_bg=(0.03, 0.06, 0.085), tuned=(1.0, 0.62, 0.15), chan=(1, 0.75, 0.3, 0.12),
                    analys=(0.95, 0.4, 1.0), track=(0.3, 1, 0.5, 0.85), bm=(1, 0.8, 0.3, 0.95)),
    "klassisk": dict(namn="Klassisk", bg=(0.05, 0.06, 0.08), spec_bg=(0.04, 0.05, 0.07), grid=(1, 1, 1, 0.07),
                     text=(1, 1, 1, 0.45), axis_bg=(0.09, 0.1, 0.13), fill_top=(0.35, 0.75, 1, 0.45),
                     fill_bot=(0.1, 0.3, 0.6, 0.08), line=(0.55, 0.82, 1), band_bg=(0.08, 0.09, 0.11),
                     tuned=(1, 0.25, 0.25), chan=(1, 1, 1, 0.13), analys=(1, 0.75, 0.2), track=(0.3, 1, 0.4, 0.8),
                     bm=(1, 0.85, 0.2, 0.9)),
}

SKIN_CSS = {
    "spanare": """
@define-color accent_color #ffb547;
@define-color accent_bg_color #c97d16;
@define-color accent_fg_color #ffffff;
@define-color window_bg_color #0c131b;
@define-color window_fg_color #d5e6ee;
@define-color view_bg_color #091017;
@define-color view_fg_color #d5e6ee;
@define-color headerbar_bg_color #0f1923;
@define-color headerbar_fg_color #e2f1f7;
@define-color headerbar_border_color #1c2b39;
@define-color headerbar_backdrop_color #0c131b;
@define-color sidebar_bg_color #0e1720;
@define-color card_bg_color rgba(110, 200, 220, 0.05);
@define-color card_shade_color rgba(0, 0, 0, 0.4);
@define-color popover_bg_color #121d28;
@define-color dialog_bg_color #111b25;
@define-color success_color #3ee6a8;
@define-color destructive_bg_color #c0392b;
.freq-entry { font-family: "DejaVu Sans Mono", monospace; font-size: 17pt; font-weight: bold; color: #59ffd0;
  background: #031016; border: 1px solid #13414b; border-radius: 6px; text-shadow: 0 0 6px rgba(89,255,208,0.5); }
notebook > header { background: #0a1118; border-bottom: 1px solid #1c2b39; }
notebook > header tab { padding: 4px 14px; color: alpha(@window_fg_color, 0.6); }
notebook > header tab:checked { color: @accent_color; box-shadow: inset 0 -3px @accent_color; }
levelbar > trough > block.filled { background: linear-gradient(to right, #1fd1a5, #ffb547 70%, #ff5a3c); }
columnview.data-table { font-size: 9.5pt; }
.analys-klass { font-size: 15pt; font-weight: bold; color: @accent_color; }
.analys-text { font-family: monospace; }
.analys-text text { background: #040b10; color: #9dffcf; }
.analys-ram { background: alpha(#6ec8dc, 0.04); border: 1px solid #1c2b39; border-radius: 8px; padding: 6px; }
""",
    "klassisk": """
.analys-klass { font-size: 15pt; font-weight: bold; color: @accent_color; }
.analys-text { font-family: monospace; }
.analys-ram { border: 1px solid alpha(currentColor, 0.15); border-radius: 8px; padding: 6px; }
""",
}


def load_conf():
    conf = json.loads(json.dumps(DEFAULT_CONF))
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            conf.update(json.load(f))
        if conf.get("bm_version", 1) < BOOKMARK_VERSION:
            have = {round(b["freq"]) for b in conf["bookmarks"]}
            new = [b for b in avkodare.EXTRA_BOOKMARKS if round(b["freq"]) not in have]
            conf["bookmarks"] += new
            log.info("La till %d nya standardbokmärken", len(new))
    except FileNotFoundError:
        pass
    except Exception as e:
        log.error("Kunde inte läsa %s: %s (standardinställningar används)", CONF_FILE, e)
    conf["bm_version"] = BOOKMARK_VERSION
    return conf


def save_conf(conf):
    try:
        tmp = CONF_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(conf, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONF_FILE)
    except Exception as e:
        log.error("Kunde inte spara inställningar: %s", e)


def parse_freq(s):
    """'145.5' -> 145.5 MHz, '7074k' -> 7.074 MHz, '433920000' -> Hz."""
    s = s.strip().lower().replace(" ", "").replace(",", ".")
    m = re.match(r"^([0-9]*\.?[0-9]+)(hz|khz|k|mhz|m|ghz|g)?$", s)
    if not m:
        raise ValueError(f"Ogiltig frekvens: {s!r}")
    v = float(m.group(1))
    u = m.group(2)
    if u in ("hz",):
        return v
    if u in ("k", "khz"):
        return v * 1e3
    if u in ("m", "mhz"):
        return v * 1e6
    if u in ("g", "ghz"):
        return v * 1e9
    if v <= 6000:
        return v * 1e6
    if v <= 6e6:
        return v * 1e3
    return v


def parse_bw(s):
    s = s.strip().lower().replace(",", ".")
    m = re.match(r"^([0-9]*\.?[0-9]+)(hz|k|khz|m|mhz)?$", s)
    if not m:
        raise ValueError(s)
    v = float(m.group(1))
    u = m.group(2)
    if u in ("hz",):
        return v
    if u in ("m", "mhz"):
        return v * 1e6
    return v * 1e3  # standard: kHz


def fmt_freq(hz):
    s = f"{hz / 1e6:.6f}"
    a, b = s.split(".")
    return f"{a}.{b[:3]} {b[3:]}"


def fmt_mhz(hz):
    return f"{hz / 1e6:.4f}"


def fmt_bw(hz):
    if hz < 1000:
        return f"{hz:.0f} Hz"
    return f"{hz / 1000:.1f} kHz".replace(".0 kHz", " kHz")


def band_name(hz):
    mhz = hz / 1e6
    best = ""
    width = 1e9
    for lo, hi, name in BANDPLAN:
        if lo <= mhz <= hi and hi - lo < width:
            best, width = name, hi - lo
    return best


def classify(bw):
    if bw < 800:
        return "Bärvåg/CW"
    if bw < 4000:
        return "SSB/smal data"
    if bw < 25000:
        return "NFM/AM"
    if bw < 300000:
        return "WFM/bred"
    return "Bredband/digital"


def make_lut(name):
    stops = COLORMAPS.get(name, COLORMAPS["klassisk"])
    x = np.linspace(0, 1, 256)
    pos = [s[0] for s in stops]
    r = np.interp(x, pos, [s[1][0] for s in stops]).astype(np.uint32)
    g = np.interp(x, pos, [s[1][1] for s in stops]).astype(np.uint32)
    b = np.interp(x, pos, [s[1][2] for s in stops]).astype(np.uint32)
    return (0xFF000000 | (r << 16) | (g << 8) | b).astype(np.uint32)


def rec_dir():
    base = None
    try:
        from gi.repository import GLib
        base = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
    except Exception:
        pass
    d = os.path.join(base or HOME, "Signalspanaren")
    if not os.path.exists(d):
        # inspelningar från tiden som Radiospanaren: flytta mappen (i musikmappen eller hemmappen)
        for old in (os.path.join(base or HOME, "Radiospanaren"), os.path.join(HOME, "Radiospanaren")):
            if os.path.isdir(old):
                try:
                    os.rename(old, d)
                    log.info("Flyttade inspelningsmappen %s → %s", old, d)
                    break
                except OSError as ex:
                    log.warning("Kunde inte flytta %s: %s", old, ex)
    os.makedirs(d, exist_ok=True)
    return d


# ════════════════════════════════════════════════════════════════════
# 3. MOTTAGARKÄLLOR
# ════════════════════════════════════════════════════════════════════
LUT_U8 = ((np.arange(256, dtype=np.float32) - 127.4) / 128.0).astype(np.float32)


class SourceError(Exception):
    pass


class BaseSource:
    kind = "base"
    name = "Källa"

    def __init__(self):
        self.center = 100e6
        self._pending = {}
        self._plock = threading.Lock()

    def set(self, key, val):
        with self._plock:
            self._pending[key] = val
        if key == "freq":
            self.center = float(val)

    def _take(self):
        with self._plock:
            p, self._pending = self._pending, {}
        return p

    def open(self):
        pass

    def close(self):
        pass

    def read(self):
        """Returnerar (iq complex64[BLOCK], center_hz, rå uint8 eller None)."""
        raise NotImplementedError


def rtl_finns():
    """Finns minst en RTL-SDR-dongel ansluten via USB?"""
    try:
        libname = ctypes.util.find_library("rtlsdr") or "librtlsdr.so.2"
        lib = ctypes.CDLL(libname)
        lib.rtlsdr_get_device_count.restype = ctypes.c_uint32
        return int(lib.rtlsdr_get_device_count()) > 0
    except Exception:
        return False


class RtlSource(BaseSource):
    kind = "rtl"
    name = "RTL-SDR"

    def __init__(self, index=0, rate=SR, block=BLOCK):
        super().__init__()
        self.index = index
        self.rate = int(rate)
        self.block = int(block)
        self.dev = None
        libname = ctypes.util.find_library("rtlsdr") or "librtlsdr.so.2"
        try:
            self.lib = ctypes.CDLL(libname)
        except OSError as e:
            raise SourceError(f"Kunde inte ladda librtlsdr ({e}). Installera: sudo dnf install rtl-sdr")
        L = self.lib
        vp = ctypes.c_void_p
        L.rtlsdr_get_device_count.restype = ctypes.c_uint32
        L.rtlsdr_get_device_name.argtypes = [ctypes.c_uint32]
        L.rtlsdr_get_device_name.restype = ctypes.c_char_p
        L.rtlsdr_open.argtypes = [ctypes.POINTER(vp), ctypes.c_uint32]
        L.rtlsdr_close.argtypes = [vp]
        L.rtlsdr_set_sample_rate.argtypes = [vp, ctypes.c_uint32]
        L.rtlsdr_set_center_freq.argtypes = [vp, ctypes.c_uint32]
        L.rtlsdr_set_tuner_gain_mode.argtypes = [vp, ctypes.c_int]
        L.rtlsdr_set_tuner_gain.argtypes = [vp, ctypes.c_int]
        L.rtlsdr_set_freq_correction.argtypes = [vp, ctypes.c_int]
        L.rtlsdr_set_direct_sampling.argtypes = [vp, ctypes.c_int]
        L.rtlsdr_set_agc_mode.argtypes = [vp, ctypes.c_int]
        L.rtlsdr_reset_buffer.argtypes = [vp]
        L.rtlsdr_read_sync.argtypes = [vp, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self._CB = ctypes.CFUNCTYPE(None, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint32, ctypes.c_void_p)
        L.rtlsdr_read_async.argtypes = [vp, self._CB, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        L.rtlsdr_cancel_async.argtypes = [vp]
        self.has_biast = hasattr(L, "rtlsdr_set_bias_tee")
        if self.has_biast:
            L.rtlsdr_set_bias_tee.argtypes = [vp, ctypes.c_int]

    def open(self):
        L = self.lib
        n = L.rtlsdr_get_device_count()
        if n == 0:
            raise SourceError("Ingen RTL-SDR hittades. Är dongeln inkopplad? (Testa i terminalen: rtl_test)")
        name = L.rtlsdr_get_device_name(self.index)
        self.dev = ctypes.c_void_p()
        r = L.rtlsdr_open(ctypes.byref(self.dev), self.index)
        if r < 0:
            self.dev = None
            raise SourceError(f"Kunde inte öppna RTL-SDR (felkod {r}). Används den av ett annat program "
                              f"(t.ex. SDR++ eller WaveShark), eller är DVB-drivrutinen laddad?")
        log.info("Öppnade RTL-SDR: %s", name.decode(errors="replace") if name else "?")
        L.rtlsdr_set_sample_rate(self.dev, self.rate)
        L.rtlsdr_set_center_freq(self.dev, int(self.center))
        self.center_hw = float(self.center)
        self.gen = 0
        self._apply(self._take())
        L.rtlsdr_reset_buffer(self.dev)
        # Läs i en egen tråd med librtlsdr:s asynkrona läsning (12 köade USB-överföringar). Tidigare
        # lästes dongeln synkront i DSP-tråden, och allt som kom medan ett block bearbetades gick
        # förlorat (≈ 20 % av samplen vid 10 ms bearbetning per block).
        self.q = queue.Queue(maxsize=int(2.0 * self.rate * 2 / self.CHUNK))      # ca 2 s
        self.gen = 0                  # ökas vid omstämning – äldre data kastas
        self._skip_gen = -1
        self.dropped = 0
        self.err = None
        self.pend = []
        self.pend_n = 0
        self._cb = self._CB(self._on_chunk)
        self.reader = threading.Thread(target=self._reader, name="RTL-läsare", daemon=True)
        self.reader.start()
        atexit.register(self.close)          # processen avslutas utan stopp: avbryt USB-läsningen ändå

    CHUNK = 65536                     # byte per USB-överföring (21 ms vid 1,536 MS/s)

    def _reader(self):
        r = self.lib.rtlsdr_read_async(self.dev, self._cb, None, 12, self.CHUNK)
        if r < 0 and self.dev is not None:
            self.err = r
            log.error("RTL-SDR slutade leverera data (felkod %d)", r)

    def _on_chunk(self, buf, n, _ctx):
        try:
            a = np.ctypeslib.as_array(buf, shape=(n,)).copy()
            try:
                self.q.put_nowait((self.gen, self.center_hw, a))
            except queue.Full:
                self.dropped += 1
                if self.dropped % 50 == 1:
                    log.warning("RTL-SDR: DSP hinner inte med – %d USB-block kastade", self.dropped)
        except Exception:           # undantag får aldrig gå tillbaka in i C
            pass

    def _apply(self, p):
        L, d = self.lib, self.dev
        if "direct" in p:
            L.rtlsdr_set_direct_sampling(d, int(p["direct"]))
            log.info("Direktsampling: %s", p["direct"])
        if "freq" in p:
            r = L.rtlsdr_set_center_freq(d, int(p["freq"]))
            if r < 0:
                log.warning("RTL-SDR kunde inte ställa in %.3f MHz (felkod %d)", p["freq"] / 1e6, r)
            self.center_hw = float(p["freq"])
            self.gen += 1
        if "gain" in p:
            g = p["gain"]
            if g is None:
                L.rtlsdr_set_tuner_gain_mode(d, 0)
            else:
                L.rtlsdr_set_tuner_gain_mode(d, 1)
                L.rtlsdr_set_tuner_gain(d, int(round(g * 10)))
        if "ppm" in p:
            L.rtlsdr_set_freq_correction(d, int(p["ppm"]))
        if "biast" in p and self.has_biast:
            L.rtlsdr_set_bias_tee(d, 1 if p["biast"] else 0)

    def read(self):
        p = self._take()
        if p:
            self._apply(p)
        need = self.block * 2
        while self.pend_n < need:
            try:
                g, c, a = self.q.get(timeout=1.5)
            except queue.Empty:
                if self.err is not None or not self.reader.is_alive():
                    raise SourceError(f"Läsfel från RTL-SDR (felkod {self.err}). Drogs dongeln ur?")
                log.warning("RTL-SDR: ingen data på 1,5 s")
                return None
            if g != self.gen:
                continue                          # från före omstämningen
            if self._skip_gen != g:               # första överföringen efter omstämning: delvis gammal
                self._skip_gen = g
                if g:
                    self.pend, self.pend_n = [], 0
                    continue
            self.pend.append(a)
            self.pend_n += len(a)
            self.pend_c = c
        allb = np.concatenate(self.pend) if len(self.pend) > 1 else self.pend[0]
        arr, rest = allb[:need], allb[need:]
        self.pend = [rest] if len(rest) else []
        self.pend_n = len(rest)
        return LUT_U8[arr].view(np.complex64), self.pend_c, arr

    def close(self):
        if self.dev:
            d, self.dev = self.dev, None
            self.lib.rtlsdr_cancel_async(d)
            if getattr(self, "reader", None):
                self.reader.join(timeout=3)
            self.lib.rtlsdr_close(d)
            if getattr(self, "dropped", 0):
                log.info("RTL-SDR: %d USB-block kastades totalt (DSP hann inte med)", self.dropped)
            log.info("Stängde RTL-SDR")
            try:
                atexit.unregister(self.close)
            except Exception:
                pass


class TcpSource(BaseSource):
    """Klient för rtl_tcp – lyssna på en SDR på en annan dator."""
    kind = "tcp"
    name = "rtl_tcp"

    def __init__(self, addr, rate=SR, block=BLOCK):
        super().__init__()
        self.rate = int(rate)
        host, _, port = addr.strip().rpartition(":")
        if not host:
            host, port = addr.strip(), "1234"
        self.host, self.port = host, int(port)
        self.sock = None
        self.buf = bytearray(int(block) * 2)

    def _cmd(self, c, v):
        self.sock.sendall(struct.pack(">BI", c, int(v) & 0xFFFFFFFF))

    def open(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=5)
        except OSError as e:
            raise SourceError(f"Kunde inte ansluta till rtl_tcp på {self.host}:{self.port}: {e}")
        hdr = self._recv_exact(12)
        if hdr[:4] != b"RTL0":
            raise SourceError(f"{self.host}:{self.port} svarar inte som en rtl_tcp-server")
        log.info("Ansluten till rtl_tcp %s:%d", self.host, self.port)
        self._cmd(2, self.rate)
        self._cmd(1, self.center)
        self._apply(self._take())

    def _apply(self, p):
        if "direct" in p:
            self._cmd(9, p["direct"])
        if "freq" in p:
            self._cmd(1, p["freq"])
        if "gain" in p:
            if p["gain"] is None:
                self._cmd(3, 0)
            else:
                self._cmd(3, 1)
                self._cmd(4, round(p["gain"] * 10))
        if "ppm" in p:
            self._cmd(5, p["ppm"])
        if "biast" in p:
            self._cmd(14, 1 if p["biast"] else 0)

    def _recv_exact(self, n, into=None):
        buf = into if into is not None else bytearray(n)
        mv = memoryview(buf)
        got = 0
        while got < n:
            try:
                k = self.sock.recv_into(mv[got:], n - got)
            except socket.timeout:
                raise SourceError("Tidsgräns: rtl_tcp-servern skickar ingen data")
            if k == 0:
                raise SourceError("rtl_tcp-servern stängde anslutningen")
            got += k
        return buf

    def read(self):
        p = self._take()
        if p:
            self._apply(p)
        self._recv_exact(len(self.buf), self.buf)
        arr = np.frombuffer(self.buf, np.uint8)
        return LUT_U8[arr].view(np.complex64), self.center, arr

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


class SimSource(BaseSource):
    """Testkälla utan hårdvara: brus + några signaler runt 100 MHz."""
    kind = "sim"
    name = "Simulator"
    SIGS = [(100.0e6, "WFM", 0.30), (100.35e6, "AM", 0.08), (99.8e6, "CW", 0.05),
            (100.5e6, "NFM", 0.10), (99.62e6, "USB", 0.03), (145.5e6, "NFM", 0.05),
            (131.725e6, "ACARS", 0.05), (161.975e6, "AIS", 0.08), (162.025e6, "AIS5", 0.06),
            (144.8e6, "APRS", 0.08)]

    def open(self):
        self.n0 = 0
        self.t_next = time.monotonic()
        self.rng = np.random.default_rng()
        # Morsekod "CQ" som på/av-mönster
        code = {"C": "-.-.", "Q": "--.-"}
        pat = []
        for ch in "CQ":
            for s in code[ch]:
                pat += [1] * (3 if s == "-" else 1) + [0]
            pat += [0, 0]
        pat += [0] * 7
        self.morse = np.array(pat, dtype=np.float32)
        A = avkodare
        self.bursts = {   # namn: (komplex skur, period i sekunder)
            "ACARS": (A.make_acars_burst(SR, reg=".SE-SIM", label="H1", blk="2",
                                         text="M02ASK0042TESTMEDDELANDE FRAN SIMULATORN"), 6),
            "AIS": (A.make_ais_burst(SR, A.make_ais_payload(mmsi=265999001, lat=59.3245, lon=18.0712,
                                                            sog=8.4, cog=135.0)), 1.5),
            "AIS5": (A.make_ais_burst(SR, A.make_ais5_payload(mmsi=265999001, call="SIMU", name="SIM TESTFARTYG",
                                                              ship_type=60, dest="VISBY")), 4),
            "APRS": (A.make_aprs_burst(SR, A.make_ax25_frame(src="SA0SIM-9", path=("WIDE1-1", "WIDE2-1"),
                                                              info="!5919.47N/01804.27E>Simulator-APRS")), 8),
        }
        self.burst_start = {k: int(SR * (0.5 + i * 0.37)) for i, k in enumerate(self.bursts)}
        # simulerade fartyg: (mmsi, lat, lon, fart kn, kurs, status, namn, anrop, typ, destination)
        self.ais_ships = [(265999001, 59.3245, 18.0712, 8.4, 135.0, 0, "SIM PASSAGERARE", "SIMA", 60, "VISBY"),
                          (265999002, 59.2900, 18.2600, 11.0, 60.0, 0, "SIM TANKER", "SIMB", 80, "ROTTERDAM"),
                          (265999003, 59.3550, 18.1400, 4.5, 300.0, 8, "SIM SEGLARE", "SIMC", 36, "SANDHAMN"),
                          (265999004, 59.3170, 18.0950, 0.0, 0.0, 5, "SIM FORTOJD", "SIMD", 70, "STOCKHOLM")]
        self.ais_i = 0
        self.ais_t0 = time.monotonic()
        self.low = [(99.9e6 + off, sig, amp) for off, _n, sig, amp in analys.test_signals(48000)]
        self.low.append((99.9e6, analys.make_morse(48000, "CQ CQ DE SM5SIM SM5SIM PSE K   ", 20), 0.05))
        V = analysavkodare
        silence = np.zeros(48000 * 3, np.complex64)
        self.low += [
            (99.68e6, V.make_sitor_b(48000, "ZCZC SA01\nSIMULATOR NAVTEX. NAVIGATIONAL WARNING 42. "
                                            "UNLIT BUOY 59-10N 018-40E.\nNNNN\n"), 0.05),
            (100.12e6, np.concatenate((V.make_ale(48000, [("TO", "SM5"), ("DATA", "SIM")] * 4 +
                                                  [("TIS", "HQ1")] * 2), silence,
                                       V.make_ale(48000, [("TWAS", "NET")] * 2), silence)), 0.05),
            (100.25e6, V.make_fax(48000, rows=240), 0.05),
            (100.42e6, np.concatenate((V.make_pocsag(48000, 1200, [
                (1234567, 3, "SIMULATOR: LARM BRAND KV 12", False), (200401, 0, "0701234567", True)]),
                silence)), 0.05),
            (100.62e6, np.concatenate((V.make_dmr(48000, bursts=60), silence)), 0.05),
        ]
        self._lowcache = {}
        self._take()
        log.info("Simulatorn startad")

    def read(self):
        self._take()
        self.t_next += BLOCK / SR
        d = self.t_next - time.monotonic()
        if d > 0:
            time.sleep(d)
        elif d < -0.5:
            self.t_next = time.monotonic()
        t = (self.n0 + np.arange(BLOCK)) / SR                       # absolut tid (för av/på-mönster)
        t1 = (((self.n0 + np.arange(BLOCK)) % SR) / SR).astype(np.float32)   # tid modulo 1 s (toner i hela Hz)
        w = np.float32(2 * np.pi)
        x = ((self.rng.standard_normal(BLOCK, np.float32) + 1j * self.rng.standard_normal(BLOCK, np.float32))
             * np.float32(0.006)).astype(np.complex64)

        def carrier(idx, fo, a):
            key = ("sig", idx, round(fo))
            car = self._lowcache.get(key)
            if car is None:
                car = (a * np.exp(2j * np.pi * fo * np.arange(BLOCK) / SR)).astype(np.complex64)
                self._lowcache[key] = car
            return car * np.complex64(np.exp(2j * np.pi * ((fo * self.n0 / SR) % 1.0)))
        for si, (f, kind, a) in enumerate(self.SIGS):
            fo = f - self.center
            if abs(fo) > SR / 2:
                continue
            if kind == "WFM":
                mod = -37.5 * np.cos(w * 1000 * t1) - np.float32(22500 / 440) * np.cos(w * 440 * t1)
                x += carrier(si, fo, a) * np.exp(1j * mod).astype(np.complex64)
            elif kind == "AM":
                x += carrier(si, fo, a) * (1 + np.float32(0.7) * np.sin(w * 600 * t1))
            elif kind == "CW":
                key = self.morse[(t / 0.07).astype(np.int64) % len(self.morse)]
                x += carrier(si, fo, a) * key
            elif kind == "NFM":
                on = ((t + f / 1e5) % 8.0) < 3.0
                if on.any():
                    x += carrier(si, fo, a) * on * np.exp(1j * 3 * np.sin(w * 1000 * t1)).astype(np.complex64)
            elif kind in self.bursts:
                arr, period = self.bursts[kind]
                st = self.burst_start[kind]
                new = False
                while st + len(arr) <= self.n0:          # nästa skur
                    st += int(SR * period)
                    new = True
                self.burst_start[kind] = st
                if new and kind in ("AIS", "AIS5"):      # nytt fartyg och ny position för varje skur
                    arr = self._next_ais(kind)
                    self.bursts[kind] = (arr, period)
                lo, hi = max(st, self.n0), min(st + len(arr), self.n0 + BLOCK)
                if lo < hi:
                    env = np.zeros(BLOCK, np.complex64)
                    env[lo - self.n0:hi - self.n0] = arr[lo - st:hi - st]
                    x += carrier(si, fo, a) * env
            elif kind == "USB":
                on = (t % 5.0) < 2.5
                if on.any():
                    x += carrier(si, fo, a) * on * (np.exp(1j * w * 700 * t1) +
                                                    0.7 * np.exp(1j * w * 1900 * t1)).astype(np.complex64)
        if len(self._lowcache) > 200:                   # mittfrekvensen har bytts många gånger
            self._lowcache = {k: v for k, v in self._lowcache.items() if k == "pat"}
        # analysens testsignaler (48 kHz-slingor). Interpolationsmönster och bärvåg räknas en gång
        # per mittfrekvens – blocket (65536) är en multipel av 32, så mönstret upprepas varje block.
        if "pat" not in self._lowcache:
            ar = np.arange(BLOCK)
            self._lowcache["pat"] = (ar // 32, ((ar % 32) / 32).astype(np.float32))
        bi, fr = self._lowcache["pat"]
        for k, (f, sig, a) in enumerate(self.low):
            fo = f - self.center
            if abs(fo) > SR / 2:
                continue
            key = ("low", k, round(fo))
            car = self._lowcache.get(key)
            if car is None:
                car = (a * np.exp(2j * np.pi * fo * np.arange(BLOCK) / SR)).astype(np.complex64)
                self._lowcache[key] = car
            L = len(sig)
            i = (self.n0 // 32 + bi) % L
            y = sig[i] * (1 - fr) + sig[(i + 1) % L] * fr
            ph = np.complex64(np.exp(2j * np.pi * ((fo * self.n0 / SR) % 1.0)))
            x += y * car * ph
        self.n0 += BLOCK
        return x.astype(np.complex64), self.center, None


def _sim_ship_payload(sim, kind):
    A = avkodare
    sim.ais_i += 1
    mmsi, lat, lon, sog, cog, nav, name, call, st, dest = sim.ais_ships[sim.ais_i % len(sim.ais_ships)]
    if kind == "AIS5":
        return A.make_ais_burst(SR, A.make_ais5_payload(mmsi=mmsi, call=call, name=name, ship_type=st, dest=dest))
    t = time.monotonic() - sim.ais_t0
    d = sog * 1.852 / 3600 * t / 111.0 * 20       # 20 gånger snabbare, så att rörelsen syns
    lat2 = lat + d * math.cos(math.radians(cog))
    lon2 = lon + d * math.sin(math.radians(cog)) / math.cos(math.radians(lat))
    return A.make_ais_burst(SR, A.make_ais_payload(mmsi=mmsi, lat=lat2, lon=lon2, sog=sog, cog=cog, nav=nav))


SimSource._next_ais = _sim_ship_payload


def open_iq_reader(conf):
    path = conf.get("play_file") or ""
    if not path or not os.path.exists(path):
        raise SourceError("Ingen IQ-fil vald (Mottagare → IQ-fil → Välj …)" if not path else
                          f"IQ-filen finns inte: {path}")
    extra = conf.get("play_override", {}).get(path, {})
    try:
        return iqfil.IQReader(path, rate=extra.get("rate"), center=extra.get("center"))
    except Exception as ex:
        raise SourceError(f"Kunde inte läsa IQ-filen {os.path.basename(path)}: {ex}")


class KiwiSource(kiwisdr.KiwiSource):
    """KiwiSDR-källan med programmets felklass (SourceError visas som meddelande)."""

    vanta_flaggor = None         # väntar på flagghämtningen innan ljudkanalen öppnas

    def open(self):
        try:
            if self.vanta_flaggor:
                self.vanta_flaggor()
            super().open()
        except kiwisdr.KiwiError as e:
            raise SourceError(str(e))

    def read(self):
        try:
            return super().read()
        except kiwisdr.KiwiError as e:
            raise SourceError(str(e))


def make_source(conf):
    k = conf["source"]
    if k == "kiwi":
        a = conf.get("kiwi_addr", "").strip()
        if not a:
            raise SourceError("Ange KiwiSDR:ns adress först (Mottagare → KiwiSDR-adress), "
                              "t.ex. http://sk5sm.proxy.kiwisdr.com:8073/")
        try:
            kiwisdr.parse_addr(a)
        except kiwisdr.KiwiError as e:
            raise SourceError(str(e))
        return KiwiSource(a, SR, BLOCK)
    if k == "rtl":
        return RtlSource()
    if k == "tcp":
        return TcpSource(conf["tcp_addr"])
    if k == "file":
        r = open_iq_reader(conf)
        if not r.rate:
            raise SourceError("Samplingstakten för IQ-filen är okänd – ange den när du väljer filen")
        sp = {0: 1.0, 1: 2.0, 2: 4.0, 3: 8.0, 4: 0.0}.get(conf.get("play_speed", 0), 1.0)
        log.info("Spelar upp %s (%s, %.0f S/s, %.1f s, mitt %s)", r.path, r.info, r.rate, r.seconds(),
                 f"{r.center_at(0) / 1e6:.6f} MHz" if r.center_at(0) else "okänd")
        return iqfil.FileSource(r, SR, BLOCK, speed=sp, loop=bool(conf.get("play_loop", False)))
    return SimSource()


# ════════════════════════════════════════════════════════════════════
# 4. DSP-MOTOR
# ════════════════════════════════════════════════════════════════════
def wav_header(nbytes=0x7FFFFFF0):
    return (b"RIFF" + struct.pack("<I", 36 + nbytes) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, AF, AF * 2, 2, 16) + b"data" + struct.pack("<I", nbytes))


class Engine:
    def __init__(self):
        self.mode = "WFM"
        self.bw = DEFAULT_BW["WFM"]
        self.offset = 0.0
        self.center = 100e6
        self.squelch = -150.0
        self.volume = 0.8
        self.agc_attack = 50.0      # 1/s, som SDR++ (reglage 1–200)
        self.agc_decay = 5.0        # 1/s, som SDR++ (reglage 1–20)
        self.agc_g = 1.0
        self.agc_amp = None
        # DSP-filter (dspfilter.py)
        self.shape = "SHARP"         # kanalfiltrets form: SHARP/SOFT
        self.pbt_lo = 0.0            # twin PBT: flytt av nedre/övre kanten (Hz, + = uppåt i frekvens)
        self.pbt_hi = 0.0
        self.nb_on = False
        self.nb = dspfilter.NoiseBlanker(SR // 4)
        self.tone = {"hpf": 0, "lpf": 0, "bass": 0, "treble": 0}
        self.tonec = dspfilter.ToneControl(AF)
        self.mnotch_on = False
        self.mnotch_freq = 1000.0
        self.mnotch_width = "M"
        self.mnotch = dspfilter.ManualNotch(AF)
        self.snr_proc = dspfilter.SpectralNR(AF)
        self.source = None
        self.thread = None
        self.running = False
        self.spec_q = deque(maxlen=32)
        self.decoders = None        # avkodare.DecoderHub
        self.analyzer = None        # analys.Analyzer (signalanalysfliken)
        self.view_zoom = 1          # sätts av GUI: zoomfaktor för visningen
        self.view_center = None     # sätts av GUI: mittfrekvens för zoomad visning
        self._zkey = None
        self.level = -150.0
        self.sq_open = False
        self._sq_hang = 0
        self._sq_fresh = 0
        self.act_hist = deque(maxlen=70)   # ljudeffekt per block (dB) när squelch är öppen, ca 3 s
        self.activity = None               # spridning (std, dB): tal >> 3 dB, konstant bärvåg < 2 dB
        self.sinks = []
        self.sink_lock = threading.Lock()
        self.rec_lock = threading.Lock()
        self.rec_wav = None
        self.rec_wav_path = None
        self.rec_iq = None
        self.rec_iq_path = None
        self.auto_rec = False
        self._auto_closed_blocks = 0
        self.on_error = None       # anropas från DSP-tråden
        self.on_stopped = None
        self.on_event = None
        self.detector = Detector()
        self.avg_lin = None
        self.avg_center = None
        self.avg_count = 0
        self.avg_lock = threading.Lock()
        self.peak_lin = None         # toppvärde sedan reset_peak() – skannern fångar korta skurar
        self.peak_center = None
        self.peak_count = 0
        self._peak_reset = True
        self.stats = {"blocks": 0, "proc_ms": 0.0, "overruns": 0}
        self.adc_clip = 0.0
        self.overload = False
        self._filt_key = None
        self._ph = 0.0
        self._ph_bfo = 0.0
        self.win = np.hanning(FFT_N).astype(np.float32)
        self.win_norm = float(self.win.sum() ** 2)
        self.bin_f = (np.arange(FFT_N) - FFT_N / 2) * BIN_HZ

    # ── styrning ──
    def start(self, source):
        if self.running:
            return
        self.source = source
        self.running = True
        self.thread = threading.Thread(target=self._run, name="DSP", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=6)
        self.thread = None

    def reset_squelch(self):
        """Anropas vid frekvensbyte: squelch får inte ärva läget från förra frekvensen."""
        self.sq_open = False
        self._sq_hang = 99
        self._sq_fresh = 0
        self.act_hist.clear()
        self.activity = None

    def set_center(self, c):
        if getattr(self.source, "fixed_center", False):
            return                          # uppspelning: filen styr mittfrekvensen
        c = max(1000.0, float(c))
        self.center = c
        if self.source:
            self.source.set("freq", c)

    def add_sink(self, s):
        with self.sink_lock:
            self.sinks.append(s)

    def remove_sink(self, s):
        with self.sink_lock:
            if s in self.sinks:
                self.sinks.remove(s)

    def reset_peak(self):
        with self.avg_lock:
            self._peak_reset = True
            self.peak_count = 0

    def get_peak(self):
        with self.avg_lock:
            if self.peak_lin is None or self._peak_reset:
                return None, None, 0
            return self.peak_lin.copy(), self.peak_center, self.peak_count

    def get_avg(self):
        with self.avg_lock:
            if self.avg_lin is None:
                return None, None, 0
            return self.avg_lin.copy(), self.avg_center, self.avg_count

    # ── inspelning ──
    def start_wav(self, path):
        with self.rec_lock:
            if self.rec_wav:
                return
            w = wave.open(path, "wb")
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(AF)
            self.rec_wav, self.rec_wav_path = w, path
        log.info("Ljudinspelning startad: %s", path)

    def stop_wav(self):
        with self.rec_lock:
            w, p = self.rec_wav, self.rec_wav_path
            self.rec_wav = None
        if w:
            w.close()
            log.info("Ljudinspelning sparad: %s", p)
        return p

    def start_iq(self, writer):
        """writer: iqfil.SigMFWriter eller iqfil.WavWriter."""
        with self.rec_lock:
            if self.rec_iq:
                return
            self.rec_iq, self.rec_iq_path = writer, getattr(writer, "path", "")
        log.info("IQ-inspelning startad: %s (%d S/s)", self.rec_iq_path, SR)

    def stop_iq(self):
        with self.rec_lock:
            f, p = self.rec_iq, self.rec_iq_path
            self.rec_iq = None
        if f:
            try:
                p = f.close() or p
                log.info("IQ-inspelning sparad: %s (%.1f s)", p, f.seconds())
            except Exception:
                log.exception("Kunde inte avsluta IQ-inspelningen")
        return p

    # ── filter ──
    def _filter_key(self, mode, bw):
        return (mode, bw, self.shape, self.pbt_lo, self.pbt_hi)

    def _rebuild(self, mode, bw):
        self._filt_key = self._filter_key(mode, bw)
        cut1 = float(np.clip(bw / 2 + 10e3, 30e3, 150e3)) if mode == "WFM" else 60e3
        self.h1 = firwin(63, cut1, fs=SR)
        self.z1 = np.zeros(len(self.h1) - 1, np.complex128)
        if mode == "WFM":
            self.hw = firwin(127, 15e3, fs=FS1)
            self.zw = np.zeros(len(self.hw) - 1)
            x = math.exp(-1.0 / (FS1 * 50e-6))   # 50 µs de-emfas (Europa)
            self.bde, self.ade = [1 - x], [1, -x]
            self.zd = np.zeros(1)
            self._last = 0j
        else:
            self.h2 = firwin(127, 22e3, fs=FS1)
            self.z2 = np.zeros(len(self.h2) - 1, np.complex128)
            lo, hi = dspfilter.pbt_edges(mode, min(bw, 46000), self.pbt_lo, self.pbt_hi)
            lo, hi = max(lo, -23000.0), min(hi, 23000.0)
            self.cfir = dspfilter.FftFir(dspfilter.channel_taps(
                lo, hi, AF, self.shape, CW_TONE if mode == "CW" else 0.0))
            self._default_hp = {"NFM": 250, "AM": 60}.get(mode, 100)
            self._last = 0j
        self.agc_g = 1.0
        self.agc_amp = None

    def _activity(self, a):
        """Mäter hur mycket ljudet varierar (tal/musik varierar, en ren bärvåg eller brus gör det inte)."""
        if not self.sq_open:
            return
        self.act_hist.append(10 * math.log10(float(np.mean(a * a)) + 1e-20))
        if len(self.act_hist) >= 50:
            self.activity = float(np.std(self.act_hist))

    AGC_SETPOINT = 0.1       # medelnivå |x| ut (SDR++: 1,0 med maxOutputAmp 10 – samma förhållande)
    AGC_MAX_OUT = 1.0        # topp som utlöser klippskyddet
    AGC_MAX_GAIN = 1e5
    AGC_DEC = 4              # nivåföljaren körs på 12 kHz (medel/topp av 4 sampel), förstärkningen interpoleras

    def _agc(self, a):
        """AGC som i SDR++ (dsp/loop/agc.h): nivåföljare på |x| med attack/decay i 1/s (standard 50 och 5,
        dvs. τ ≈ 20 ms upp och 200 ms ned), förstärkning = börvärde/nivå, och vid klippning sätts nivån
        till största toppen i resten av blocket. Förstärkningen ändras mjukt per sampel (inga trappsteg)."""
        D = self.AGC_DEC
        n = len(a) // D * D
        if n == 0:
            return a * self.agc_g
        ab = np.abs(a[:n]).reshape(-1, D)
        env, pk = ab.mean(axis=1), ab.max(axis=1)
        rmax = np.maximum.accumulate(pk[::-1])[::-1]          # största topp från k till blockets slut
        at = min(1.0, self.agc_attack * D / AF)
        de = min(1.0, self.agc_decay * D / AF)
        sp, mo, gmax = self.AGC_SETPOINT, self.AGC_MAX_OUT, self.AGC_MAX_GAIN
        amp = self.agc_amp
        if amp is None:                                        # första blocket: börja på rätt nivå direkt
            amp = max(float(env.mean()), sp / gmax)
        g = np.empty(len(env))
        for k in range(len(env)):
            v = env[k]
            if v > 0.0:
                amp += (v - amp) * (at if v > amp else de)
            gk = sp / amp if amp > sp / gmax else gmax
            if pk[k] * gk > mo:                                # klippskydd (look-ahead i blocket)
                amp = float(rmax[k])
                gk = sp / amp
            g[k] = gk
        self.agc_amp = amp
        # interpolera från förra blockets sista värde så att förstärkningen blir kontinuerlig
        xs = np.concatenate(([-0.5 * D], np.arange(len(g)) * D + 0.5 * D))
        gs = np.interp(np.arange(len(a)), xs, np.concatenate(([self.agc_g], g)))
        self.agc_g = float(g[-1])
        return a * gs

    def _chan_bounds(self, mode, bw, off):
        if mode != "WFM" and mode == self.mode and bw == self.bw and (self.pbt_lo or self.pbt_hi):
            lo, hi = dspfilter.pbt_edges(mode, bw, self.pbt_lo, self.pbt_hi)
            return off + lo, off + hi
        if mode == "USB":
            return off + 200, off + 200 + bw
        if mode == "LSB":
            return off - 200 - bw, off - 200
        return off - bw / 2, off + bw / 2

    def chan_power(self, p_lin, lo, hi):
        k0 = int(np.clip(np.floor(lo / BIN_HZ + FFT_N / 2), 0, FFT_N - 1))
        k1 = int(np.clip(np.ceil(hi / BIN_HZ + FFT_N / 2), k0 + 1, FFT_N))
        return 10 * math.log10(float(p_lin[k0:k1].sum()) + 1e-20)

    # ── huvudloop ──
    def _run(self):
        src = self.source
        forsok, t_upp, uppkopplad = 0, None, False
        try:
            while True:
                try:
                    src.open()
                    t_upp, uppkopplad = time.monotonic(), True
                    log.info("Mottagning startad (%s, inställd %.4f MHz, mitt %.4f MHz)", src.name,
                             (self.center + self.offset) / 1e6, self.center / 1e6)
                    if forsok:
                        self._meddela("kiwi_status", "Återansluten till KiwiSDR")
                    self._lasa(src)
                    break
                except SourceError as e:
                    if not (uppkopplad and isinstance(src, KiwiSource)) or not self.running:
                        raise
                    if t_upp is not None:
                        if time.monotonic() - t_upp > 30:
                            forsok = 0
                        t_upp = None
                    forsok += 1
                    if forsok > 3:
                        raise SourceError(f"Kunde inte återansluta till KiwiSDR efter tre försök ({e})")
                    vantan = (5, 15, 30)[forsok - 1]
                    log.warning("KiwiSDR tappades (%s) – återansluter om %d s (försök %d av 3)", e, vantan, forsok)
                    self._meddela("kiwi_status", f"KiwiSDR tappades – återansluter om {vantan} s")
                    try:
                        src.close()
                    except Exception:
                        pass
                    self._vanta_atergang(vantan)
                    if not self.running:
                        break
        except EOFError:
            log.info("Uppspelningen nådde slutet av filen")
            if self.on_event:
                self.on_event("play_end", None)
        except SourceError as e:
            log.error("%s", e)
            if self.on_error:
                self.on_error(str(e))
        except Exception as e:
            log.exception("Fel i DSP-tråden")
            if self.on_error:
                self.on_error(f"Internt fel i signalbehandlingen: {e}")
        finally:
            try:
                src.close()
            except Exception:
                log.exception("Fel när källan stängdes")
            self.running = False
            self.stop_wav()
            self.stop_iq()
            log.info("Mottagning stoppad")
            if self.on_stopped:
                self.on_stopped()

    def _lasa(self, src):
        while self.running:
            r = src.read()
            if r is None:
                continue
            t0 = time.perf_counter()
            self._process(*r)
            dt = (time.perf_counter() - t0) * 1000
            st = self.stats
            st["blocks"] += 1
            st["proc_ms"] = st["proc_ms"] * 0.95 + dt * 0.05
            if dt > BLOCK / SR * 1000:
                st["overruns"] += 1
                if st["overruns"] % 50 == 1:
                    log.warning("DSP hinner inte med (%.1f ms per block, max %.1f)", dt, BLOCK / SR * 1000)

    def _vanta_atergang(self, sek):
        slut = time.monotonic() + sek
        while self.running and time.monotonic() < slut:
            time.sleep(0.2)

    def _meddela(self, kind, val):
        if self.on_event:
            self.on_event(kind, val)

    def _zoom_line(self, x, c):
        """Zoom-FFT: blanda ner visningsfönstret, decimera och gör FFT med finare upplösning.
        Returnerar None (ingen zoom), False (inte nog data än) eller (db, mitt, spännvidd)."""
        z = int(self.view_zoom or 1)
        vc = self.view_center
        if z <= 1 or vc is None:
            self._zkey = None
            return None
        vc = float(vc)
        key = (z, round(vc), round(c))
        if key != self._zkey:
            self._zkey = key
            self._zh = firwin(64 * z + 1, 0.45 * SR / z, fs=SR).astype(np.float32)
            self._ztail = np.zeros(len(self._zh) - 1, np.complex64)
            self._zph = 0.0
            self._zbuf = np.zeros(0, np.complex64)
        w = -2 * np.pi * (vc - c) / SR
        xm = (x * np.exp(1j * (self._zph + w * np.arange(len(x))))).astype(np.complex64)
        self._zph = (self._zph + w * len(x)) % (2 * np.pi)
        xin = np.concatenate((self._ztail, xm))
        self._ztail = xm[-(len(self._zh) - 1):]
        y = oaconvolve(xin, self._zh, mode="valid")[::z].astype(np.complex64)
        self._zbuf = np.concatenate((self._zbuf, y))[-4 * FFT_N:]
        m = len(self._zbuf) // FFT_N
        if m == 0:
            return False
        seg = self._zbuf[-m * FFT_N:].reshape(m, FFT_N) * self.win
        p = np.fft.fftshift((np.abs(np.fft.fft(seg, axis=1)) ** 2).mean(axis=0)) / self.win_norm
        return (10 * np.log10(p + 1e-20)).astype(np.float32), vc, SR / z

    def _process(self, x, c, raw):
        now = time.time()
        if getattr(self.source, "fixed_center", False) and abs(c - self.center) > 1:
            # uppspelning: filen bestämmer mittfrekvensen – behåll den inställda (absoluta) frekvensen
            self.offset += self.center - c
            self.center = c
        if self.rec_iq:
            with self.rec_lock:
                if self.rec_iq:
                    try:
                        self.rec_iq.write(x, c, raw)
                    except OSError as ex:
                        log.error("IQ-inspelningen stoppades: %s", ex)
                        f, self.rec_iq = self.rec_iq, None
                        if self.on_event:
                            self.on_event("iq_error", str(ex))

        # ADC-överstyrning (8-bitars I/Q från RTL-SDR/rtl_tcp som slår i taket vid 0/255)
        if raw is not None and getattr(raw, "dtype", None) == np.uint8 and len(raw):
            cl = float(np.mean((raw[::64] <= 1) | (raw[::64] >= 254)))
            self.adc_clip = self.adc_clip * 0.8 + cl * 0.2
            self.overload = self.adc_clip > 0.005
        else:
            self.adc_clip = 0.0
            self.overload = False

        # Spektrum (Welch, 16 segment)
        seg = x.reshape(-1, FFT_N) * self.win
        p = np.fft.fftshift((np.abs(np.fft.fft(seg, axis=1)) ** 2).mean(axis=0)) / self.win_norm
        db = (10 * np.log10(p + 1e-20)).astype(np.float32)
        self.spec_q.append((db, c, self._zoom_line(x, c)))
        # uppspelning snabbare än realtid: vänta in avkodarna och analysen i stället för att kasta data
        bp = getattr(self.source, "fixed_center", False) and getattr(self.source, "speed", 1.0) != 1.0
        if self.decoders is not None:
            self.decoders.feed(x, c, wait=bp)
        if bp and self.analyzer is not None:
            an, tap = self.analyzer, self.analyzer.tap
            t_end = time.monotonic() + 3.0
            while (self.running and tap.ring is not None and tap.target is not None and not an.paused
                   and an._thr is not None and tap.total - an._ptr > 0.6 * len(tap.ring)
                   and time.monotonic() < t_end):
                time.sleep(0.01)
        if self.analyzer is not None:
            try:
                self.analyzer.tap.feed(x, c)
            except Exception:
                log.exception("Fel i signalanalysens kanalutsnitt")
                self.analyzer = None
        with self.avg_lock:
            if self.avg_center != c or self.avg_lin is None:
                self.avg_lin = p.copy()
                self.avg_center = c
                self.avg_count = 1
            else:
                self.avg_lin = self.avg_lin * 0.6 + p * 0.4
                self.avg_count += 1
            avg = self.avg_lin
            if self._peak_reset or self.peak_center != c or self.peak_lin is None:
                self.peak_lin = p.copy()
                self.peak_center = c
                self.peak_count = 1
                self._peak_reset = False
            else:
                np.maximum(self.peak_lin, p, out=self.peak_lin)
                self.peak_count += 1

        mode, bw, off = self.mode, self.bw, self.offset
        off = float(np.clip(off, -SR / 2 + 5000, SR / 2 - 5000))

        # Squelch / nivå
        lo, hi = self._chan_bounds(mode, bw, off)
        self.level = self.chan_power(p, lo, hi)
        if abs(c - self.center) > 1:
            # Block från förra mittfrekvensen (hårdvaran har inte hunnit byta än)
            self.sq_open = False
            self._sq_fresh = 0
        elif self._sq_fresh < 2:
            self._sq_fresh += 1
            self.sq_open = False
        elif self.level >= self.squelch:
            self.sq_open = True
            self._sq_hang = 0
        elif self.level < self.squelch - 2:
            self._sq_hang += 1
            # SSB/CW har ingen bärvåg: nivån faller mellan stavelser och ord – håll öppet ca 1 s
            if self._sq_hang > (23 if mode in ("USB", "LSB", "CW") else 6):
                self.sq_open = False

        # Detektor (varannan block)
        if self.detector.enabled and self.stats["blocks"] % 2 == 0:
            self.detector.update(10 * np.log10(avg + 1e-20), c, now)

        # Demodulering
        if self._filt_key != self._filter_key(mode, bw):
            self._rebuild(mode, bw)
        n = np.arange(BLOCK)
        w = -2 * np.pi * off / SR
        y = x * np.exp(1j * (self._ph + w * n))
        self._ph = (self._ph + w * BLOCK) % (2 * np.pi)
        y, self.z1 = lfilter(self.h1, 1.0, y, zi=self.z1)
        y = y[::D1]
        if self.nb_on and mode != "WFM":
            y = self.nb.process(y)                  # pulsblankare på bredbandig IQ (384 kHz)
        if mode == "WFM":
            prev = np.concatenate(([self._last], y[:-1]))
            self._last = y[-1]
            d = np.angle(y * np.conj(prev))
            d, self.zd = lfilter(self.bde, self.ade, d, zi=self.zd)
            a, self.zw = lfilter(self.hw, 1.0, d, zi=self.zw)
            a = a[::D2] * (0.7 / (2 * np.pi * 75e3 / FS1))
        else:
            y, self.z2 = lfilter(self.h2, 1.0, y, zi=self.z2)
            y = y[::D2]
            if mode == "CW":
                m = np.arange(len(y))
                wb = 2 * np.pi * CW_TONE / AF
                y = y * np.exp(1j * (self._ph_bfo + wb * m))
                self._ph_bfo = (self._ph_bfo + wb * len(y)) % (2 * np.pi)
            y = self.cfir.process(y)
            if mode == "NFM":
                prev = np.concatenate(([self._last], y[:-1]))
                self._last = y[-1]
                a = np.angle(y * np.conj(prev)) * (0.6 / (2 * np.pi * 5000 / AF))
            elif mode == "AM":
                a = np.abs(y)
            else:
                a = y.real
            t = self.tone
            self.tonec.configure(t["hpf"], t["lpf"], t["bass"], t["treble"], self._default_hp)
            a = self.tonec.process(a)
            if mode in ("NFM", "AM"):
                self._activity(a)
            if self.mnotch_on:
                self.mnotch.configure(self.mnotch_freq, self.mnotch_width)
                a = self.mnotch.process(a)
            a = self.snr_proc.process(a)            # brusreducering / auto-notch (gör inget när de är av)
            if mode != "NFM":
                a = self._agc(a)
        if not self.sq_open:
            a = a * 0.0
        pcm = (np.clip(a * self.volume, -1, 1) * 32767).astype("<i2").tobytes()

        # Automatisk inspelning
        if self.auto_rec:
            if self.sq_open:
                self._auto_closed_blocks = 0
                if not self.rec_wav:
                    path = os.path.join(rec_dir(), f"auto_{datetime.datetime.now():%Y%m%d-%H%M%S}_"
                                                   f"{(c + off) / 1e6:.4f}MHz_{mode}.wav")
                    self.start_wav(path)
                    if self.on_event:
                        self.on_event("rec_auto_start", path)
            elif self.rec_wav and self.rec_wav_path and os.path.basename(self.rec_wav_path).startswith("auto_"):
                self._auto_closed_blocks += 1
                if self._auto_closed_blocks > 47:  # ~2 s
                    p2 = self.stop_wav()
                    if self.on_event:
                        self.on_event("rec_auto_stop", p2)

        if self.rec_wav:
            with self.rec_lock:
                if self.rec_wav:
                    self.rec_wav.writeframes(pcm)
        with self.sink_lock:
            sinks = list(self.sinks)
        for s in sinks:
            s.push(pcm)


# ════════════════════════════════════════════════════════════════════
# 5. SIGNALDETEKTOR (signalloggen)
# ════════════════════════════════════════════════════════════════════
class Detector:
    def __init__(self):
        self.enabled = True
        self.thresh = 12.0
        self.min_hits = 3
        self.lock = threading.Lock()
        self.tracks = {}
        self.active = []
        self.changed = set()
        self.next_id = 1
        self.floor = -100.0

    def update(self, db, center, now):
        N = len(db)
        floor = float(np.median(db))
        self.floor = floor
        a = db.copy()
        a[N // 2 - 3:N // 2 + 4] = floor          # DC-spik
        edge = int(N * 0.06)
        a[:edge] = floor
        a[-edge:] = floor
        mask = a > floor + self.thresh
        mask = ndimage.binary_closing(mask, structure=np.ones(5, bool))
        lab, nseg = ndimage.label(mask)
        segs = []
        if nseg:
            for sl in ndimage.find_objects(lab)[:400]:
                s = sl[0]
                k = np.arange(s.start, s.stop)
                pw = 10 ** (db[s] / 10)
                f = center + float(((k - N / 2) * BIN_HZ * pw).sum() / pw.sum())
                peak = float(db[s].max())
                segs.append((f, (s.stop - s.start) * BIN_HZ, peak))
            segs.sort(key=lambda z: -z[2])
            segs = segs[:60]
        with self.lock:
            active = [t for t in self.tracks.values() if t["status"] == "Aktiv"]
            for f, bw, peak in segs:
                best = None
                for t in active:
                    if abs(t["f"] - f) < max(t["bw"], bw) / 2 + 2000:
                        best = t
                        break
                if best:
                    best["f"] = best["f"] * 0.8 + f * 0.2
                    best["bw"] = max(best["bw"] * 0.9, bw)
                    best["peak"] = max(best["peak"], peak)
                    best["snr"] = max(best["snr"], peak - floor)
                    best["last"] = now
                    best["hits"] += 1
                    best["dur"] = now - best["first"]
                    if best["hits"] >= self.min_hits:
                        self.changed.add(best["id"])
                else:
                    t = {"id": self.next_id, "first": now, "last": now, "f": f, "bw": bw, "peak": peak,
                         "snr": peak - floor, "hits": 1, "dur": 0.0, "status": "Aktiv"}
                    self.next_id += 1
                    self.tracks[t["id"]] = t
                    active.append(t)
            for t in active:
                if now - t["last"] > 1.5:
                    t["status"] = "Avslutad"
                    t["dur"] = t["last"] - t["first"]
                    if t["hits"] >= self.min_hits:
                        self.changed.add(t["id"])
                    else:
                        self.tracks.pop(t["id"], None)
            self.active = [dict(t) for t in self.tracks.values()
                           if t["status"] == "Aktiv" and t["hits"] >= self.min_hits]
            if len(self.tracks) > 3000:
                for tid in sorted(self.tracks)[:1000]:
                    if self.tracks[tid]["status"] != "Aktiv":
                        self.tracks.pop(tid, None)

    def pop_changes(self):
        with self.lock:
            out = [dict(self.tracks[i]) for i in self.changed if i in self.tracks]
            self.changed.clear()
            act = list(self.active)
        for t in out:
            t["cls"] = avkodare.identify(t["f"], t["bw"]) or classify(t["bw"])
            t["band"] = band_name(t["f"])
        return out, act

    def clear(self):
        with self.lock:
            self.tracks.clear()
            self.changed.clear()
            self.active = []


# ════════════════════════════════════════════════════════════════════
# 6. LJUDUTGÅNG OCH WEBBGRÄNSSNITT
# ════════════════════════════════════════════════════════════════════
class AudioOut:
    """Skickar ljud till PipeWire via pacat (eller pw-cat)."""

    def __init__(self):
        self.q = queue.Queue(maxsize=12)
        self.proc = None
        self.alive = False
        self.thread = None
        self._unmute_lock = threading.Lock()

    def _spawn(self):
        cmds = [["pacat", "--playback", "--raw", "--format=s16le", "--rate=48000", "--channels=1",
                 "--latency-msec=150", "--client-name=Signalspanaren", "--stream-name=Mottagare"],
                ["pw-cat", "--playback", "--format=s16", "--rate=48000", "--channels=1", "-"]]
        for c in cmds:
            if shutil.which(c[0]):
                try:
                    err = open(STDERR_FILE, "ab")
                    self.proc = subprocess.Popen(c, stdin=subprocess.PIPE, stderr=err)
                    log.info("Ljud via %s", c[0])
                    threading.Thread(target=self._unmute_own_stream, args=(self.proc.pid,),
                                     name="Ljudkontroll", daemon=True).start()
                    return True
                except OSError as e:
                    log.error("Kunde inte starta %s: %s", c[0], e)
        log.error("Hittar varken pacat eller pw-cat – inget ljud. Installera: sudo dnf install pulseaudio-utils")
        return False

    def _unmute_own_stream(self, pid):
        """PipeWire/stream-restore minns mute per program. Avmuta vår egen ström om den är tystad."""
        if not self._unmute_lock.acquire(blocking=False):
            return
        try:
            for _ in range(12):
                try:
                    r = subprocess.run(["pactl", "--format=json", "list", "sink-inputs"],
                                       capture_output=True, text=True, timeout=2)
                    if r.returncode != 0:
                        return
                    for si in json.loads(r.stdout or "[]"):
                        props = si.get("properties") or {}
                        if str(props.get("application.process.id")) != str(pid):
                            continue
                        if si.get("mute"):
                            subprocess.run(["pactl", "set-sink-input-mute", str(si["index"]), "0"],
                                           capture_output=True, timeout=2)
                            log.warning("Ljudströmmen var mutad i PipeWire – avmutade den")
                        return
                except (OSError, ValueError, subprocess.SubprocessError):
                    return
                time.sleep(0.25)
        finally:
            self._unmute_lock.release()

    def start(self):
        if self.alive:
            return
        if not self._spawn():
            return
        self.alive = True
        self.thread = threading.Thread(target=self._run, name="Ljud", daemon=True)
        self.thread.start()

    def push(self, b):
        if not self.alive:
            return
        try:
            self.q.put_nowait(b)
        except queue.Full:
            try:
                self.q.get_nowait()
                self.q.put_nowait(b)
            except (queue.Empty, queue.Full):
                pass

    def _run(self):
        restarts = 0
        while self.alive:
            try:
                b = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.proc.stdin.write(b)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, AttributeError) as e:
                log.error("Ljudutgången avbröts (%s)", e)
                restarts += 1
                if restarts > 3 or not self._spawn():
                    log.error("Ger upp ljudutgången efter flera försök")
                    self.alive = False
                    return

    def stop(self):
        self.alive = False
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None


class QueueSink:
    def __init__(self):
        self.q = queue.Queue(maxsize=60)

    def push(self, b):
        try:
            self.q.put_nowait(b)
        except queue.Full:
            pass


WEB_HTML = r"""<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Signalspanaren – webb</title>
<style>
body{margin:0;background:#111418;color:#dde3ea;font:14px system-ui,sans-serif}
#top{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 12px;background:#1b2027}
#f{font:600 22px ui-monospace,monospace;color:#7fe0d0;min-width:13ch}
canvas{display:block;width:100%;cursor:crosshair}
button{background:#2a313b;color:#dde3ea;border:1px solid #3a4350;border-radius:6px;padding:5px 10px;cursor:pointer}
button.on{background:#2f6fb0;border-color:#4a8ad0}
input{background:#0d1014;color:#dde3ea;border:1px solid #3a4350;border-radius:6px;padding:5px;width:130px}
#lvl{font-family:ui-monospace,monospace;color:#9ab}
</style></head><body>
<div id="top"><span id="f">–</span><input id="fi" placeholder="MHz, t.ex. 145.5">
<span id="modes"></span><button id="play">▶ Lyssna</button><span id="lvl"></span></div>
<canvas id="sp" height="160"></canvas><canvas id="wf" height="420"></canvas><audio id="au"></audio>
<script>
const M=["WFM","NFM","AM","USB","LSB","CW"];let st=null;
const sp=document.getElementById('sp'),wf=document.getElementById('wf'),sc=sp.getContext('2d'),wc=wf.getContext('2d');
const row=document.createElement('canvas');row.width=1024;row.height=1;const rc=row.getContext('2d');const img=rc.createImageData(1024,1);
const stops=[[0,[0,0,10]],[.15,[0,0,90]],[.35,[0,90,220]],[.55,[0,220,200]],[.7,[240,230,0]],[.85,[255,60,0]],[1,[255,255,255]]];
const cmap=[];for(let i=0;i<256;i++){const x=i/255;let j=0;while(j<stops.length-2&&x>stops[j+1][0])j++;
const a=stops[j],b=stops[j+1],t=(x-a[0])/(b[0]-a[0]);cmap.push(a[1].map((v,k)=>Math.round(v+(b[1][k]-v)*t)));}
function fit(){for(const c of [sp,wf]){if(c.width!==c.clientWidth)c.width=c.clientWidth;}}
window.addEventListener('resize',fit);fit();
const md=document.getElementById('modes');
for(const m of M){const b=document.createElement('button');b.textContent=m;b.onclick=()=>tune(st.freq,m);b.id='m'+m;md.appendChild(b);}
function tune(f,m){fetch('/tune?f='+Math.round(f)+(m?'&mode='+m:''));}
function click(e){if(!st)return;const r=e.target.getBoundingClientRect();
let f=st.center-st.sr/2+(e.clientX-r.left)/r.width*st.sr;f=Math.round(f/st.step)*st.step;tune(f);}
sp.onclick=click;wf.onclick=click;
document.getElementById('fi').onkeydown=e=>{if(e.key==='Enter'){const v=parseFloat(e.target.value.replace(',','.'));if(v>0)tune(v*1e6);}};
document.getElementById('play').onclick=()=>{const a=document.getElementById('au');
if(a.paused){a.src='/audio.wav?'+Date.now();a.play();play.textContent='■ Stoppa';}else{a.pause();a.src='';play.textContent='▶ Lyssna';}};
function draw(){const W=sp.width,H=sp.height,L=st.line;sc.fillStyle='#0b0e12';sc.fillRect(0,0,W,H);
sc.strokeStyle='#222a33';for(let i=1;i<6;i++){sc.beginPath();sc.moveTo(0,i*H/6);sc.lineTo(W,i*H/6);sc.stroke();}
sc.beginPath();sc.moveTo(0,H);for(let i=0;i<L.length;i++){sc.lineTo(i/L.length*W,H-L[i]/255*H);}sc.lineTo(W,H);
sc.fillStyle='rgba(80,180,255,.25)';sc.fill();sc.strokeStyle='#7cc8ff';sc.stroke();
const x0=(st.freq+st.lo-(st.center-st.sr/2))/st.sr*W,x1=(st.freq+st.hi-(st.center-st.sr/2))/st.sr*W;
sc.fillStyle='rgba(255,255,255,.12)';sc.fillRect(x0,0,Math.max(1,x1-x0),H);
const xf=(st.freq-(st.center-st.sr/2))/st.sr*W;sc.fillStyle='#ff4a4a';sc.fillRect(xf-1,0,2,H);
for(let i=0;i<1024;i++){const c=cmap[L[i]];img.data[i*4]=c[0];img.data[i*4+1]=c[1];img.data[i*4+2]=c[2];img.data[i*4+3]=255;}
rc.putImageData(img,0,0);wc.drawImage(wf,0,0,wf.width,wf.height-1,0,1,wf.width,wf.height-1);wc.drawImage(row,0,0,wf.width,1);
document.getElementById('f').textContent=(st.freq/1e6).toFixed(4)+' MHz';
document.getElementById('lvl').textContent=st.mode+'  '+st.level.toFixed(1)+' dB '+(st.sq?'● signal':'');
for(const m of M)document.getElementById('m'+m).className=(m===st.mode?'on':'');}
async function loop(){try{const r=await fetch('/status.json');st=await r.json();if(st.line)draw();}catch(e){}setTimeout(loop,100);}
loop();
</script></body></html>"""


class WebServer:
    """Enkel KiwiSDR-liknande webbmottagare: vattenfall, klick för att ställa in, ljudström."""

    def __init__(self, app, port, public):
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        from urllib.parse import urlparse, parse_qs
        self.app = app
        host = "0.0.0.0" if public else "127.0.0.1"
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                log.debug("webb: " + fmt, *args)

            def _send(self, code, ctype, body):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                try:
                    if u.path == "/":
                        self._send(200, "text/html; charset=utf-8", WEB_HTML.encode())
                    elif u.path == "/status.json":
                        self._send(200, "application/json", json.dumps(srv.status()).encode())
                    elif u.path == "/tune":
                        q = parse_qs(u.query)
                        f = float(q.get("f", ["0"])[0])
                        m = q.get("mode", [None])[0]
                        if m not in MODES:
                            m = None
                        if 1e4 < f < 6e9:
                            srv.app.remote_tune(f, m)
                        self._send(200, "text/plain", b"ok")
                    elif u.path == "/audio.wav":
                        self._audio()
                    else:
                        self._send(404, "text/plain", b"finns inte")
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    log.exception("Fel i webbgränssnittet")

            def _audio(self):
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(wav_header())
                sink = QueueSink()
                srv.app.engine.add_sink(sink)
                log.info("Webblyssnare ansluten: %s", self.client_address[0])
                try:
                    while True:
                        self.wfile.write(sink.q.get(timeout=5))
                except (BrokenPipeError, ConnectionResetError, queue.Empty, OSError):
                    pass
                finally:
                    srv.app.engine.remove_sink(sink)
                    log.info("Webblyssnare frånkopplad: %s", self.client_address[0])

        try:
            self.httpd = ThreadingHTTPServer((host, port), H)
        except OSError as e:
            raise RuntimeError(f"Kunde inte starta webbservern på port {port}: {e}")
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="Webb", daemon=True)
        self.thread.start()
        self.url = f"http://{'<datorns-ip>' if public else '127.0.0.1'}:{port}/"
        log.info("Webbgränssnitt startat på %s:%d", host, port)

    def status(self):
        app = self.app
        e = app.engine
        line = None
        db = app.latest_db
        if db is not None:
            v = db.reshape(1024, -1).max(axis=1)
            line = np.clip((v - app.conf["db_min"]) / max(1, app.conf["db_max"] - app.conf["db_min"]) * 255,
                           0, 255).astype(int).tolist()
        lo, hi = e._chan_bounds(e.mode, e.bw, 0)
        return {"center": app.latest_c or app.engine.center, "sr": SR, "freq": app.freq, "mode": e.mode, "bw": e.bw,
                "lo": lo, "hi": hi, "level": e.level, "sq": e.sq_open, "step": app.step(), "line": line}

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        log.info("Webbgränssnitt stoppat")


# ════════════════════════════════════════════════════════════════════
# 7. GRAFISKT GRÄNSSNITT
# ════════════════════════════════════════════════════════════════════
def on_unix_signals(cb):
    """Anropar cb vid SIGTERM/SIGINT i GTK:s huvudloop (GLibUnix i ny PyGObject, annars GLib)."""
    import signal
    from gi.repository import GLib
    add = None
    try:
        import gi
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        add = getattr(GLibUnix, "signal_add", None)       # GLib 2.84+ (t.ex. Fedora 42+)
    except (ImportError, ValueError):
        pass
    if add is None:
        add = GLib.unix_signal_add                          # äldre GLib (Ubuntu 24.04, Mint 22)
    for sig in (signal.SIGTERM, signal.SIGINT):
        add(GLib.PRIORITY_DEFAULT, sig, lambda *_: cb() or False)


MIN_GTK = (4, 12)
MIN_ADW = (1, 5)


def run_gui(args):
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Adw, Gdk, GLib, Gio, GObject, Pango
        import cairo
    except (ImportError, ValueError) as e:
        msg = (f"Saknar GTK4/libadwaita för Python ({e}).\n"
               "Fedora:          sudo dnf install python3-gobject python3-cairo gtk4 libadwaita\n"
               "Ubuntu/Mint:     sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1")
        log.critical(msg)
        print(msg, file=sys.stderr)
        return 1
    gtk_v = (Gtk.get_major_version(), Gtk.get_minor_version())
    adw_v = (Adw.get_major_version(), Adw.get_minor_version())
    if gtk_v < MIN_GTK or adw_v < MIN_ADW:
        msg = (f"För gamla versioner: GTK {gtk_v[0]}.{gtk_v[1]} och libadwaita {adw_v[0]}.{adw_v[1]}. "
               f"Signalspanaren kräver GTK {MIN_GTK[0]}.{MIN_GTK[1]}+ och libadwaita {MIN_ADW[0]}.{MIN_ADW[1]}+ "
               "(t.ex. Fedora 40+, Ubuntu 24.04+, Linux Mint 22+).")
        log.critical(msg)
        print(msg, file=sys.stderr)
        return 1

    class Row(GObject.Object):
        __gsignals__ = {"updated": (GObject.SignalFlags.RUN_FIRST, None, ())}

        def __init__(self, data):
            super().__init__()
            self.data = data

    def _bind(f, li, fn):
        lbl = li.get_child()
        row = li.get_item()
        lbl.set_text(fn(row.data))
        lbl._hid = (row, row.connect("updated", lambda r: lbl.set_text(fn(r.data))))

    def _unbind(f, li):
        lbl = li.get_child()
        h = getattr(lbl, "_hid", None)
        if h:
            h[0].disconnect(h[1])
            lbl._hid = None

    def make_table(columns, filter_func=None):
        """columns: [(rubrik, visningsfunktion, sorteringsnyckel, expandera)]"""
        store = Gio.ListStore(item_type=Row)
        cv = Gtk.ColumnView()
        cv.add_css_class("data-table")
        cv.set_show_row_separators(True)
        cols = []
        for title, fn, key, expand in columns:
            fac = Gtk.SignalListItemFactory()
            fac.connect("setup", lambda f, li: li.set_child(Gtk.Label(xalign=0)))
            fac.connect("bind", _bind, fn)
            fac.connect("unbind", _unbind)
            col = Gtk.ColumnViewColumn(title=title, factory=fac)
            col.set_resizable(True)
            col.set_expand(expand)

            def cmp(a, b, *_, key=key):
                va, vb = a.data.get(key), b.data.get(key)
                if va == vb:
                    return Gtk.Ordering.EQUAL
                try:
                    return Gtk.Ordering.SMALLER if va < vb else Gtk.Ordering.LARGER
                except TypeError:
                    return Gtk.Ordering.EQUAL
            col.set_sorter(Gtk.CustomSorter.new(cmp, None))
            cv.append_column(col)
            cols.append(col)
        cfilter = Gtk.CustomFilter.new(filter_func, None) if filter_func else None
        model = Gtk.FilterListModel(model=store, filter=cfilter) if cfilter else store
        sortm = Gtk.SortListModel(model=model, sorter=cv.get_sorter())
        sel = Gtk.SingleSelection(model=sortm)
        cv.set_model(sel)
        sw = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        sw.set_child(cv)
        return store, cv, sel, cfilter, cols, sw

    # ── Spektrum + vattenfall ──
    class SpectrumView(Gtk.DrawingArea):
        WF_ROWS = 600

        def __init__(self, win):
            super().__init__(hexpand=True, vexpand=True)
            self.win = win
            self.wf = np.zeros((self.WF_ROWS, FFT_N), np.uint8)
            self.wf_span = float(SR)
            self.disp = None
            self.mouse = None
            self.lut = make_lut(win.conf["colormap"])
            self.set_draw_func(self.on_draw)
            g = Gtk.GestureClick(button=1)
            g.connect("pressed", self.on_click)
            self.add_controller(g)
            sc = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
            sc.connect("scroll", self.on_scroll)
            self.add_controller(sc)
            mo = Gtk.EventControllerMotion()
            mo.connect("motion", lambda c, x, y: self._set_mouse((x, y)))
            mo.connect("leave", lambda c: self._set_mouse(None))
            self.add_controller(mo)
            dr = Gtk.GestureDrag(button=3)
            dr.connect("drag-begin", self.on_drag_begin)
            dr.connect("drag-update", self.on_drag_update)
            self.add_controller(dr)
            # högerklick utan att dra: flaggmeny (högerdrag flyttar fortfarande vattenfallet)
            rc = Gtk.GestureClick(button=3)
            rc.connect("released", self.on_right_click)
            self.add_controller(rc)
            self._flag_hits = []
            self._drag_moved = False
            self._drag_c = None

        def _set_mouse(self, m):
            self.mouse = m
            self.queue_draw()

        def push_line(self, db, c, span, to_waterfall=True):
            w = self.win
            if span != self.wf_span:
                self.wf[:] = 0
                self.wf_span = span
                w.wf_center = c
                self.disp = None
            if w.wf_center != c:
                delta = int(round((c - w.wf_center) / (self.wf_span / FFT_N)))
                if abs(delta) >= FFT_N:
                    self.wf[:] = 0
                elif delta > 0:
                    self.wf[:, :-delta] = self.wf[:, delta:]
                    self.wf[:, -delta:] = 0
                elif delta < 0:
                    self.wf[:, -delta:] = self.wf[:, :delta]
                    self.wf[:, :-delta] = 0
                w.wf_center = c
                self.disp = None
            lo, hi = w.conf["db_min"], w.conf["db_max"]
            q = np.clip((db - lo) * (255.0 / max(1.0, hi - lo)), 0, 255).astype(np.uint8)
            if to_waterfall:
                self.wf[1:] = self.wf[:-1]
                self.wf[0] = q
            self.disp = db.copy() if self.disp is None else self.disp * 0.5 + db * 0.5

        def view(self):
            """Visat frekvensområde = området som datan i vattenfallet täcker."""
            return self.win.wf_center - self.wf_span / 2, self.wf_span

        def requested_view(self):
            """Mittfrekvens som DSP-motorn ska zooma kring (följer inställd frekvens)."""
            w = self.win
            z = max(1, w.conf["zoom"])
            c = w.engine.center
            if z <= 1:
                return 1, None
            span = SR / z
            if w.engine.running and isinstance(w.engine.source, KiwiSource):
                return z, c                                  # Kiwins band ligger alltid mitt i
            return z, float(np.clip(w.freq, c - SR / 2 + span / 2, c + SR / 2 - span / 2))

        def x_to_f(self, x):
            lo, span = self.view()
            return lo + x / max(1, self.get_width()) * span

        def layout(self, H):
            band_h, axis_h = 18, 18
            avail = H - band_h - axis_h
            # på låga skärmar: spektrumet minst ~70 px (så långt det går) så att dB-skalan går att läsa
            spec_h = int(max(avail * self.win.conf["spec_frac"], min(70, avail * 0.6)))
            return band_h, spec_h, axis_h, band_h + spec_h + axis_h

        def flag_at(self, x, y):
            for x0, y0, x1, y1, fl in reversed(self._flag_hits):
                if x0 - 2 <= x <= x1 + 2 and y0 - 2 <= y <= y1 + 2:
                    return fl
            return None

        def on_right_click(self, g, n, x, y):
            if self._drag_moved:
                return
            self.win.flagg_meny(self, x, y, self.x_to_f(x), self.flag_at(x, y))

        def on_click(self, g, n, x, y):
            w = self.win
            fl = self.flag_at(x, y)
            if fl is not None:                   # klick på en flagga: stäm in den
                w.tune_flagga(fl)
                return
            st = w.step()
            fc = self.x_to_f(x)
            shift = bool(g.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
            w.tune(round(fc / st) * st)
            if shift:              # Skift + klick: bara ställ in, ingen analys
                return
            hit = w.carrier_at(fc)
            if hit:
                w.analyse(hit[0], hit[1], show=True)
            elif w.analys_shown():
                e = w.engine
                lo_, hi_ = e._chan_bounds(e.mode, e.bw, w.freq)
                w.analyse((lo_ + hi_) / 2, hi_ - lo_, show=False)

        def on_scroll(self, ctrl, dx, dy):
            state = ctrl.get_current_event_state()
            if state & Gdk.ModifierType.CONTROL_MASK:
                z = self.win.conf["zoom"]
                z = min(128, z * 2) if dy < 0 else max(1, z // 2)
                self.win.set_zoom(z)
            else:
                s = -dy
                acc = getattr(self, "_wheel_acc", 0.0)
                if acc * s < 0:                # bytt riktning: börja om
                    acc = 0.0
                acc += s
                k = int(acc)
                self._wheel_acc = acc - k
                if k:
                    self.win.nudge_freq(k)
            return True

        def on_drag_begin(self, g, x, y):
            self._drag_c = self.win.engine.center
            self._drag_moved = False

        def on_drag_update(self, g, dx, dy):
            if self._drag_c is None:
                return
            lo, span = self.view()
            if abs(dx) > 4 or abs(dy) > 4:
                self._drag_moved = True
            newc = self._drag_c - dx / max(1, self.get_width()) * span
            if abs(newc - self.win.engine.center) > BIN_HZ * 20:
                self.win.set_center_keep(newc)

        def on_draw(self, area, cr, W, H, *_):
            w = self.win
            conf = w.conf
            S = w.skin()
            cr.set_source_rgb(*S["bg"][:3])
            cr.paint()
            if W < 10 or H < 60:
                return
            band_h, spec_h, axis_h, wf_y = self.layout(H)
            wf_h = H - wf_y
            lo, span = self.view()
            c = w.wf_center
            xs = np.arange(W + 1)
            fpx = lo + xs * span / W
            binhz = self.wf_span / FFT_N
            kf = (fpx - c) / binhz + FFT_N / 2
            k = np.clip(np.floor(kf).astype(np.int64), 0, FFT_N - 1)
            starts = k[:-1]
            outside = (fpx[:-1] < c - self.wf_span / 2) | (fpx[:-1] > c + self.wf_span / 2)

            # Vattenfall
            rows = int(min(self.WF_ROWS, max(1, wf_h)))
            img = np.maximum.reduceat(self.wf[:rows], starts, axis=1)
            img[:, outside] = 0
            rgba = np.ascontiguousarray(self.lut[img])
            surf = cairo.ImageSurface.create_for_data(memoryview(rgba), cairo.FORMAT_ARGB32, W, rows, W * 4)
            cr.save()
            cr.rectangle(0, wf_y, W, wf_h)
            cr.clip()
            cr.translate(0, wf_y)
            cr.scale(1, wf_h / rows)
            cr.set_source_surface(surf, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST)
            cr.paint()
            cr.restore()

            # Spektrumbakgrund och dB-rutnät
            sy = band_h
            cr.set_source_rgb(*S["spec_bg"][:3])
            cr.rectangle(0, sy, W, spec_h)
            cr.fill()
            dbmin, dbmax = conf["db_min"], conf["db_max"]
            rng = max(1.0, dbmax - dbmin)
            cr.set_font_size(10)
            cr.set_line_width(1)
            # tätaste steg som ger minst 13 px mellan dB-siffrorna (annars skrivs de över varandra)
            step_db = next((st for st in (5, 10, 20, 30, 50, 100) if st / rng * spec_h >= 13 and
                            (st >= 10 or rng <= 30)), 100)
            d = math.ceil(dbmin / step_db) * step_db
            while d < dbmax:
                yy = sy + (dbmax - d) / rng * spec_h
                cr.set_source_rgba(*S["grid"])
                cr.move_to(0, int(yy) + 0.5)
                cr.line_to(W, int(yy) + 0.5)
                cr.stroke()
                cr.set_source_rgba(*S["text"])
                cr.move_to(3, yy - 2)
                cr.show_text(f"{d:.0f}")
                d += step_db

            # Frekvensaxel
            ay = sy + spec_h
            cr.set_source_rgb(*S["axis_bg"][:3])
            cr.rectangle(0, ay, W, axis_h)
            cr.fill()
            raw = span / 8
            mag = 10 ** math.floor(math.log10(raw))
            tick = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
            dec = max(0, min(6, -int(math.floor(math.log10(tick / 1e6))) if tick < 1e6 else 0))
            f0 = math.ceil(lo / tick) * tick
            ff = f0
            while ff < lo + span:
                x = (ff - lo) / span * W
                cr.set_source_rgba(*S["grid"])
                cr.move_to(int(x) + 0.5, sy)
                cr.line_to(int(x) + 0.5, sy + spec_h)
                cr.stroke()
                cr.set_source_rgba(*S["text"][:3], 0.75)
                cr.move_to(int(x) + 0.5, ay)
                cr.line_to(int(x) + 0.5, ay + 4)
                cr.stroke()
                txt = f"{ff / 1e6:.{dec}f}"
                ext = cr.text_extents(txt)
                cr.move_to(x - ext.width / 2, ay + 14)
                cr.show_text(txt)
                ff += tick

            # Spektrumkurva
            if self.disp is not None:
                v = np.maximum.reduceat(self.disp, starts)
                yv = sy + np.clip((dbmax - v) / rng, 0, 1) * spec_h
                cr.move_to(0, sy + spec_h)
                for i in range(W):
                    if not outside[i]:
                        cr.line_to(i, yv[i])
                cr.line_to(W, sy + spec_h)
                cr.close_path()
                grad = cairo.LinearGradient(0, sy, 0, sy + spec_h)
                grad.add_color_stop_rgba(0, *S["fill_top"])
                grad.add_color_stop_rgba(1, *S["fill_bot"])
                cr.set_source(grad)
                cr.fill_preserve()
                cr.set_source_rgb(*S["line"][:3])
                cr.set_line_width(1.2)
                cr.stroke()

            # Bandplan
            cr.set_source_rgb(*S["band_bg"][:3])
            cr.rectangle(0, 0, W, band_h)
            cr.fill()
            for blo, bhi, name in BANDPLAN:
                x0 = (blo * 1e6 - lo) / span * W
                x1 = (bhi * 1e6 - lo) / span * W
                if x1 < 0 or x0 > W:
                    continue
                hsh = (sum(map(ord, name)) * 47) % 360 / 360.0
                r, g, b = _hsv(hsh, 0.45, 0.55)
                cr.set_source_rgba(r, g, b, 0.85)
                cr.rectangle(max(0, x0), 2, min(W, x1) - max(0, x0), band_h - 4)
                cr.fill()
                cr.set_source_rgb(1, 1, 1)
                ext = cr.text_extents(name)
                if min(W, x1) - max(0, x0) > ext.width + 6:
                    cr.move_to(max(0, x0) + (min(W, x1) - max(0, x0) - ext.width) / 2, band_h - 5)
                    cr.show_text(name)

            # Aktiva signaler från detektorn
            for t in w.active_tracks:
                x0 = (t["f"] - t["bw"] / 2 - lo) / span * W
                x1 = (t["f"] + t["bw"] / 2 - lo) / span * W
                if x1 < 0 or x0 > W:
                    continue
                cr.set_source_rgba(*S["track"])
                cr.set_line_width(2)
                cr.move_to(x0, sy + 3)
                cr.line_to(max(x1, x0 + 2), sy + 3)
                cr.stroke()

            # Bokmärken
            cr.set_font_size(10)
            for i, bm in enumerate(conf["bookmarks"]):
                x = (bm["freq"] - lo) / span * W
                if 0 <= x <= W:
                    yy = sy + 16 + (i % 3) * 12
                    cr.set_source_rgba(*S["bm"])
                    cr.move_to(x, sy + 6)
                    cr.line_to(x - 3, sy + 1)
                    cr.line_to(x + 3, sy + 1)
                    cr.close_path()
                    cr.fill()
                    cr.move_to(x + 4, yy)
                    cr.show_text(bm["name"])

            # Flaggor (Kiwins + egna) längst ner i spektrumet, precis ovanför frekvensskalan/vattenfallet
            self._flag_hits = []
            if conf.get("flaggor_visa", True) and getattr(w, "flaggor", None) is not None:
                fls = w.flaggor.synliga(lo, lo + span, conf.get("flaggor_kiwi", True))
                if len(fls) <= 400:
                    cr.set_font_size(9.5)
                    yb = sy + spec_h - 2
                    slut = [-1e9, -1e9]                  # där texten i rad 0/1 slutar
                    for fl in fls:
                        x = (fl["f"] - lo) / span * W
                        r, g_, b_ = flaggor.farg(fl)
                        cr.set_source_rgba(r, g_, b_, 0.95)
                        cr.move_to(x, yb)
                        cr.line_to(x - 3.5, yb - 6)
                        cr.line_to(x + 3.5, yb - 6)
                        cr.close_path()
                        cr.fill()
                        txt = fl.get("namn", "")[:28] + (" ✎" if fl.get("_egen") else "")
                        ext = cr.text_extents(txt)
                        rad = 0 if x > slut[0] + 4 else (1 if x > slut[1] + 4 else None)
                        if rad is None:
                            self._flag_hits.append((x - 4, yb - 7, x + 4, yb, fl))
                            continue
                        ty = yb - 9 - rad * 12
                        cr.set_source_rgba(0, 0, 0, 0.55)
                        cr.rectangle(x - 1, ty - ext.height - 1, ext.width + 4, ext.height + 3)
                        cr.fill()
                        cr.set_source_rgba(r, g_, b_, 1.0)
                        cr.move_to(x + 1, ty)
                        cr.show_text(txt)
                        slut[rad] = x + ext.width + 4
                        self._flag_hits.append((x - 4, ty - ext.height - 1, x + ext.width + 4, yb, fl))

            # Inställd kanal
            e = w.engine
            clo, chi = e._chan_bounds(e.mode, e.bw, 0)
            x0 = (w.freq + clo - lo) / span * W
            x1 = (w.freq + chi - lo) / span * W
            cr.set_source_rgba(*S["chan"])
            cr.rectangle(x0, sy, max(2, x1 - x0), spec_h)
            cr.fill()
            cr.set_source_rgba(*S["chan"][:3], S["chan"][3] * 0.55)
            cr.rectangle(x0, wf_y, max(2, x1 - x0), wf_h)
            cr.fill()
            xf = (w.freq - lo) / span * W
            cr.set_source_rgb(*S["tuned"][:3])
            cr.set_line_width(1.5)
            cr.move_to(xf, sy)
            cr.line_to(xf, H)
            cr.stroke()

            # Analysmål (signalanalysfliken)
            ta = w.analyzer.tap.target if w.analyzer else None
            if ta is not None:
                bwa = w.analyzer.tap.bw or 1000
                xa0 = (ta - bwa / 2 - lo) / span * W
                xa1 = (ta + bwa / 2 - lo) / span * W
                if xa1 >= 0 and xa0 <= W:
                    if xa1 - xa0 < 8:
                        xa0, xa1 = (xa0 + xa1) / 2 - 4, (xa0 + xa1) / 2 + 4
                    yb = sy + spec_h - 2
                    cr.set_source_rgba(*S["analys"][:3], 0.95)
                    cr.set_line_width(2)
                    cr.move_to(xa0, yb - 8)
                    cr.line_to(xa0, yb)
                    cr.line_to(xa1, yb)
                    cr.line_to(xa1, yb - 8)
                    cr.stroke()
                    cr.set_source_rgba(*S["analys"][:3], 0.10)
                    cr.rectangle(xa0, wf_y, xa1 - xa0, wf_h)
                    cr.fill()
                    cr.set_font_size(10)
                    cr.set_source_rgba(*S["analys"][:3], 1)
                    cr.move_to(xa1 + 3, yb - 2)
                    cr.show_text("analys")

            # Muspekare
            if self.mouse:
                mx, my = self.mouse
                fm = lo + mx / W * span
                txt = f"{fm / 1e6:.4f} MHz"
                if self.disp is not None and sy <= my <= sy + spec_h:
                    kk = int(np.clip((fm - c) / binhz + FFT_N / 2, 0, FFT_N - 1))
                    txt += f"   {self.disp[kk]:.1f} dB"
                b = band_name(fm)
                if b:
                    txt += f"   {b}"
                cr.set_font_size(12)
                ext = cr.text_extents(txt)
                cr.set_source_rgba(0, 0, 0, 0.7)
                cr.rectangle(W - ext.width - 14, sy + 4, ext.width + 10, 18)
                cr.fill()
                cr.set_source_rgb(1, 1, 1)
                cr.move_to(W - ext.width - 9, sy + 17)
                cr.show_text(txt)
                cr.set_source_rgba(1, 1, 1, 0.35)
                cr.set_line_width(1)
                cr.move_to(int(mx) + 0.5, sy)
                cr.line_to(int(mx) + 0.5, H)
                cr.stroke()

            if not e.running:
                cr.set_font_size(16)
                msg = "Tryck på ▶ Starta för att börja ta emot"
                ext = cr.text_extents(msg)
                cr.set_source_rgba(1, 1, 1, 0.6)
                cr.move_to((W - ext.width) / 2, wf_y + wf_h / 2)
                cr.show_text(msg)

    def _hsv(h, s, v):
        import colorsys
        return colorsys.hsv_to_rgb(h, s, v)

    # ── Skanner ──
    class Scanner:
        """Skanner för bokmärken och frekvensintervall.

        Dongeln ser hela fönstret (±0,4·SR) på en gång, så alla kanaler i ett fönster mäts
        samtidigt ur det medelvärdesbildade spektrumet – en omstämning per fönster i stället
        för en per kanal. Tröskeln är antingen relativ till brusgolvet (median av spektrumet i
        fönstret) eller det vanliga squelch-reglaget. Prioritetskanalen kollas utan avbrott om
        den ligger i samma fönster, annars med en kort utflykt med jämna mellanrum.
        KiwiSDR (12 kHz) skannas en kanal i taget; en IQ-fil som ett enda fönster.
        """
        HALF = 0.4                  # använd ±0,4·SR av fönstret
        GUARD = 3 * BIN_HZ          # håll kanaler borta från DC-spiken

        def __init__(self, win):
            self.win = win
            self.active = False
            self.state = "idle"
            self.status = ""
            self.force_skip = False
            self.hits = {}           # nyckel (10 Hz) → träff
            self.hits_gen = 0        # ökar när träfflistan ändrats
            self.temp_lock = []      # spärrat denna session ("Hoppa över")
            self.prio = None

        # ── vad går att ta emot / är spärrat ──
        def _src(self):
            e = self.win.engine
            return e.source if e.running else None

        def _kiwi(self):
            return isinstance(self._src(), KiwiSource)

        def _file(self):
            return getattr(self._src(), "fixed_center", False)

        def receivable(self, f):
            """Direktsampling tar bara emot under 28,8 MHz; tunern (R820T m.fl.) bara över ca 24 MHz."""
            if self._kiwi():
                return f <= kiwisdr.KIWI_MAX_HZ
            if self._file():
                s = self._src()
                return abs(f - s.center) < 0.45 * min(SR, s.r.rate or SR)
            return f < 28.8e6 if self.win.conf.get("direct") else f >= 24e6

        def locked(self, f, mode=None, bw=None):
            e = self.win.engine
            lo, hi = e._chan_bounds(mode or e.mode, bw or e.bw, f)
            return any(lo - 500 <= x <= hi + 500
                       for x in self.win.conf.get("lockout", []) + self.temp_lock + self.win.suggested)

        # ── mätning ──
        @staticmethod
        def _floor(avg):
            n = FFT_N // 2
            k = int(FFT_N * 0.4)
            return float(np.median(np.concatenate((avg[n - k:n - 8], avg[n + 8:n + k])))) + 1e-20

        def _measure(self, avg, avg_c, ch, floor):
            """(nivå dB, SNR dB) för kanalen ch = {f, mode, bw} ur spektrumet."""
            e = self.win.engine
            lo, hi = e._chan_bounds(ch["mode"], ch["bw"], ch["f"] - avg_c)
            k0 = int(np.clip(np.floor(lo / BIN_HZ + FFT_N / 2), 0, FFT_N - 1))
            k1 = int(np.clip(np.ceil(hi / BIN_HZ + FFT_N / 2), k0 + 1, FFT_N))
            p = 10 * math.log10(float(avg[k0:k1].sum()) + 1e-20)
            return p, p - 10 * math.log10(floor * (k1 - k0))

        def _active(self, lvl, snr):
            if self.rel:
                return snr >= self.thr
            return lvl >= self.win.engine.squelch

        def _sq_for(self, ch, floor):
            """Relativ tröskel: sätt motorns squelch till brusgolv + tröskel för den här kanalen."""
            if not self.rel:
                return
            e = self.win.engine
            lo, hi = e._chan_bounds(ch["mode"], ch["bw"], 0)
            nb = max(1, int(math.ceil((hi - lo) / BIN_HZ)))
            e.squelch = 10 * math.log10(floor * nb) + self.thr - 1.5

        # ── start/stopp ──
        def start(self):
            w = self.win
            conf = w.conf
            e = w.engine
            if not e.running:
                w.toast("Starta mottagningen först")
                return False
            self.mode = conf["scan_mode"]
            self.hold = float(conf["scan_hold"])
            self.max_listen = float(conf.get("scan_max_listen", 0) or 0)
            self.dwell = conf["scan_dwell_ms"] / 1000.0 + (0.5 if self._kiwi() else 0.0)
            self.need = max(3, int(conf["scan_dwell_ms"] / 1000.0 / (BLOCK / SR)))   # block att toppvärdeshålla
            self.rel = conf.get("scan_sq_mode", 0) == 0
            self.thr = float(conf.get("scan_snr", 10.0))
            self.user_sq = e.squelch
            self.skip_carriers = conf.get("scan_skip_carriers", True)
            self.stops = 0
            self.t0 = time.monotonic()
            self.force_skip = False
            self.prio = self._prio_channel()
            self.prio_every = float(conf.get("scan_prio_s", 2.0))
            self.prio_next = time.monotonic() + self.prio_every
            self.prio_quiet = 0.0           # vila prioriteten till (efter maxtid/överhoppning)
            if self.mode == 0:
                grp = conf.get("scan_group", "")
                items = [b for b in conf["bookmarks"] if not grp or (band_name(b["freq"]) or "Övrigt") == grp]
                chans = [self._ch(b["freq"], b.get("mode") or e.mode, b.get("name", "")) for b in items]
                chans = [c for c in chans if self.receivable(c["f"]) and not self.locked(c["f"], c["mode"], c["bw"])]
                if not chans:
                    w.toast("Inga bokmärken att skanna" + (f" i gruppen {grp}" if grp else "") + " i nuvarande läge"
                            + (" (KiwiSDR = bara 0–30 MHz)" if self._kiwi() else
                               " (direktsampling = bara under 28,8 MHz)" if conf.get("direct") else ""))
                    return False
                self.windows = self._plan(chans)
                desc = f"bokmärken ({len(chans)} st i {len(self.windows)} fönster)"
            else:
                if self._kiwi():
                    w.toast("Intervallskanning fungerar inte med KiwiSDR (bara 12 kHz i taget) – "
                            "skanna bokmärken i stället")
                    return False
                try:
                    a = parse_freq(conf["scan_start"])
                    b = parse_freq(conf["scan_stop"])
                except ValueError as ex:
                    w.toast(str(ex))
                    return False
                if b <= a:
                    w.toast("Stoppfrekvensen måste vara högre än startfrekvensen")
                    return False
                self.a, self.b = a, b
                self.chstep = max(100.0, float(conf["scan_step_khz"]) * 1000)
                if self._file():
                    self.windows = [{"c": None, "range": (a, b)}]
                else:
                    usable = SR * 2 * self.HALF
                    self.windows = []
                    cc = a + usable / 2 + self.chstep / 2
                    while cc - usable / 2 < b:
                        self.windows.append({"c": cc, "range": (max(a, cc - usable / 2), min(b, cc + usable / 2))})
                        cc += usable
                desc = (f"intervall {fmt_mhz(a)}–{fmt_mhz(b)} MHz, steg {fmt_bw(self.chstep)}, "
                        f"{len(self.windows)} fönster")
            self.win_idx = -1
            self.active = True
            self._next_window(time.monotonic())
            if not self.rel and e.squelch <= -140:
                w.toast("Tips: höj squelch så att bruset inte stoppar skanningen")
            log.info("Skanning startad: %s, %s, tröskel %s, väntetid %.1f s%s", desc, e.mode,
                     f"{self.thr:.0f} dB över brus" if self.rel else f"squelch {e.squelch:.0f} dB", self.hold,
                     f", prioritet {self.prio['name']} {fmt_mhz(self.prio['f'])} MHz" if self.prio else "")
            return True

        def stop(self):
            if self.active:
                log.info("Skanning stoppad efter %.0f s, %d stopp på signaler",
                         time.monotonic() - self.t0, self.stops)
                self._end_hit(time.monotonic())
                if self.rel:
                    self.win.engine.squelch = self.user_sq
            self.active = False
            self.state = "idle"
            self.status = ""

        def _ch(self, f, mode, name=""):
            return {"f": float(f), "mode": mode, "bw": DEFAULT_BW.get(mode, 12500), "name": name}

        def _prio_channel(self):
            p = self.win.conf.get("scan_prio")
            if not p:
                return None
            for b in self.win.conf["bookmarks"]:
                if abs(b["freq"] - p) < 1:
                    if not self.receivable(b["freq"]):
                        return None
                    return self._ch(b["freq"], b.get("mode") or self.win.engine.mode, b.get("name", ""))
            return None

        def _plan(self, chans):
            """Dela upp kanalerna i fönster. KiwiSDR: en i taget. IQ-fil: ett enda fönster."""
            if self._kiwi():
                return [{"c": None, "chans": [c], "tune": c} for c in sorted(chans, key=lambda c: c["f"])]
            if self._file():
                return [{"c": None, "chans": chans}]
            return scan_plan(chans, SR, self.HALF, self.GUARD)

        # ── fönster och kanaler ──
        def _next_window(self, now):
            self.win_idx = (self.win_idx + 1) % len(self.windows)
            self._enter_window(now)

        def _enter_window(self, now):
            wd = self.windows[self.win_idx]
            e = self.win.engine
            if wd.get("tune"):                          # KiwiSDR: stäm in kanalen direkt
                ch = wd["tune"]
                self.win.tune(ch["f"], ch["mode"])
            elif wd["c"] is not None:
                e.set_center(wd["c"])
                # visa fönstrets första kanal (ljudet är tyst medan fönstret mäts)
                first = wd["chans"][0] if wd.get("chans") else self._ch(wd["range"][0], e.mode)
                if first["mode"] != e.mode:
                    self.win.set_mode(first["mode"])
                self.win.tune(first["f"], keep_center=True)
            self.expect_c = e.center
            self.visited = set()
            self.state = "window"
            self._mute()
            self.settle = now + 0.12 + (0.5 if self._kiwi() else 0.0)   # hårdvaran byter frekvens
            self._reset_at = self.settle
            if self.mode == 0:
                names = [ch["name"] or fmt_mhz(ch["f"]) for ch in wd["chans"]]
                self.status = "Skannar: " + (", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else ""))
            else:
                lo, hi = wd["range"]
                self.status = f"Skannar {fmt_mhz(lo)}–{fmt_mhz(hi)} MHz"

        def _range_chans(self, wd, avg, avg_c, floor):
            """Intervall: kanalrastret i fönstret, bara lokala toppar (inte sidband från grannen)."""
            e = self.win.engine
            sel_lo, sel_hi = wd["range"]
            k = max(1, int(math.ceil(e.bw / 2 / self.chstep)))
            lo = max(self.a, sel_lo - k * self.chstep, avg_c - SR * 0.48)
            hi = min(self.b, sel_hi + k * self.chstep, avg_c + SR * 0.48)
            first = math.ceil((lo - self.a) / self.chstep - 1e-9)
            last = math.floor((hi - self.a) / self.chstep + 1e-9)
            fs = self.a + np.arange(first, last + 1) * self.chstep
            fs = fs[np.abs(fs - avg_c) > e.bw / 2 + self.GUARD]
            chs = [dict(self._ch(f, e.mode), bw=e.bw) for f in fs]
            ms = [self._measure(avg, avg_c, ch, floor) for ch in chs]
            out = []
            for i, ch in enumerate(chs):
                if not (sel_lo <= ch["f"] <= sel_hi):
                    continue
                nb = [m[0] for m in ms[max(0, i - k):i + k + 1]]
                if ms[i][0] < max(nb):
                    continue
                out.append((ch, ms[i][0], ms[i][1]))
            return out

        def _candidates(self, wd, avg, avg_c, floor):
            if self.mode == 0:
                res = [(ch, *self._measure(avg, avg_c, ch, floor)) for ch in wd["chans"]]
            else:
                res = self._range_chans(wd, avg, avg_c, floor)
            return [(ch, lvl, snr) for ch, lvl, snr in res
                    if self._active(lvl, snr) and int(ch["f"]) not in self.visited
                    and not self.locked(ch["f"], ch["mode"], ch["bw"])]

        def _listen_on(self, ch, now, floor, settle=0.15, is_prio=False, snr=None):
            w = self.win
            e = w.engine
            if ch["mode"] != e.mode:
                w.set_mode(ch["mode"])
            w.tune(ch["f"], keep_center=not self._kiwi())
            self._sq_for(ch, floor)
            self.cur = ch
            self.cur_prio = is_prio
            self.floor = floor
            self.state = "listen"
            self.settle = now + settle
            self.hold_until = self.settle
            self.heard = False
            self.status = ("Prioritet: " if is_prio else "Kollar: ") + (ch["name"] or fmt_mhz(ch["f"]) + " MHz")
            self._start_hit(now, snr)

        def _leave(self, now, forced=False):
            """forced: maxtid eller överhoppning – lämna även om signalen finns kvar."""
            self._end_hit(now)
            if forced and getattr(self, "cur_prio", False):
                self.prio_quiet = now + max(10.0, 5 * self.prio_every)
            if self.heard:
                log.info("Skanner: lämnar %.4f MHz efter %.1f s", self.win.freq / 1e6, now - self.heard_t)
            self.visited.add(int(self.cur["f"]))
            if abs(self.win.engine.center - self.expect_c) > 1:      # var på prioritetsutflykt
                self._enter_window(now)
                return
            self.state = "window"            # mät fönstret igen – fler aktiva kanaler?
            self.settle = now + 0.05
            self._reset_at = self.settle
            self._mute()

        def _mute(self):
            """Relativt läge: tyst medan fönstret mäts (squelch sätts per kanal när en hittas)."""
            if self.rel:
                self.win.engine.squelch = 99.0

        # ── träfflista ──
        def _start_hit(self, now, snr):
            ch = self.cur
            k = int(round(ch["f"] / 10))
            h = self.hits.get(k)
            if h is None:
                h = self.hits[k] = {"f": ch["f"], "freq": ch["f"], "namn": ch["name"] or band_name(ch["f"]),
                                    "mode": ch["mode"], "count": 0, "dur": 0.0, "first": time.time(),
                                    "last": time.time(), "max_snr": -99.0, "prio": False}
            h["count"] += 1
            h["last"] = time.time()
            h["prio"] = h["prio"] or self.cur_prio
            if snr is not None:
                h["max_snr"] = max(h["max_snr"], snr)
            self._hit, self._hit_t = h, now
            self.hits_gen += 1

        def _end_hit(self, now):
            h = getattr(self, "_hit", None)
            if h is not None:
                h["dur"] += now - self._hit_t
                h["last"] = time.time()
                self._hit = None
                self.hits_gen += 1

        def clear_hits(self):
            self.hits.clear()
            self.hits_gen += 1

        # ── spärrar ──
        def skip(self):
            """Hoppa över aktuell frekvens resten av sessionen (permanent spärr via knappen i meddelandet)."""
            f = self.win.freq
            self.temp_lock.append(f)
            log.info("Skanner: hoppar över %.4f MHz denna session", f / 1e6)
            self.win.toast_action(f"{fmt_mhz(f)} MHz hoppas över denna session", "Spärra för alltid",
                                  lambda: self.lock_forever(f))
            self.force_skip = True

        def lock_forever(self, f):
            lst = self.win.conf.setdefault("lockout", [])
            if all(abs(f - x) > 1 for x in lst):
                lst.append(f)
            self.temp_lock = [x for x in self.temp_lock if abs(x - f) > 1]
            log.info("Skanner: spärrade %.4f MHz (%d spärrade totalt)", f / 1e6, len(lst))
            save_conf(self.win.conf)
            self.win.toast(f"{fmt_mhz(f)} MHz spärrad")

        # ── huvudloop (anropas var 33:e ms) ──
        def tick(self):
            if not self.active:
                return
            now = time.monotonic()
            e = self.win.engine
            if self.force_skip:
                self.force_skip = False
                if self.state == "listen":
                    self._leave(now, forced=True)
                return
            if now < self.settle:
                return
            if getattr(self, "_reset_at", None) is not None:
                e.reset_peak()
                self._reset_at = None
                return
            avg, avg_c, cnt = e.get_avg()
            if self.state == "listen":
                self._tick_listen(now, e, avg, avg_c, cnt)
            elif self.state == "window":
                # toppvärde under hela väntetiden: en skur i ett enda block (43 ms) räcker
                pk, pk_c, pn = e.get_peak()
                if pk is None or abs(pk_c - self.expect_c) > 1 or pn < self.need:
                    return
                floor = self._floor(pk)
                wd = self.windows[self.win_idx]
                cands = self._candidates(wd, pk, pk_c, floor)
                if self._prio_ok(now) and self._prio_in(pk_c):
                    lvl, snr = self._measure(pk, pk_c, self.prio, floor)
                    if self._active(lvl, snr):
                        self._listen_on(self.prio, now, self._floor(avg) if avg is not None else floor,
                                        snr=snr, is_prio=True)
                        return
                if cands:
                    ch, lvl, snr = max(cands, key=lambda t: t[2] if self.rel else t[1])
                    self._listen_on(ch, now, self._floor(avg) if avg is not None and abs(avg_c - pk_c) <= 1
                                    else floor, snr=snr)
                    return
                if self._prio_due(now, avg_c):
                    return
                self._next_window(now)
            elif self.state == "prio":
                pk, pk_c, pn = e.get_peak()
                if pk is None or abs(pk_c - e.center) > 1 or pn < self.need:
                    return
                floor = self._floor(pk)
                lvl, snr = self._measure(pk, pk_c, self.prio, floor)
                if self._active(lvl, snr):
                    log.info("Skanner: prioritetskanalen %s är aktiv", fmt_mhz(self.prio["f"]))
                    self._listen_on(self.prio, now, self._floor(avg) if avg is not None else floor,
                                    snr=snr, is_prio=True)
                    self.visited.discard(int(self.prio["f"]))
                else:
                    self._return_from_prio(now)

        def _tick_listen(self, now, e, avg, avg_c, cnt):
            if e.sq_open:
                if not self.heard:
                    self.heard = True
                    self.heard_t = now
                    self.stops += 1
                    log.info("Skanner: signal på %.4f MHz (%s), nivå %.1f dB%s", self.win.freq / 1e6,
                             e.mode, e.level, " (prioritet)" if self.cur_prio else "")
                    self.status = ("Prioritet: " if self.cur_prio else "Lyssnar: ") + \
                        (self.cur["name"] + " " if self.cur["name"] else "") + fmt_mhz(self.win.freq) + " MHz"
                if (self.skip_carriers and e.mode in ("AM", "NFM") and e.activity is not None
                        and e.activity < 3.0 and now - self.heard_t > 2.5):
                    f = self.win.freq
                    log.info("Skanner: %.4f MHz är en omodulerad bärvåg (ljudvariation %.1f dB) – "
                             "troligen störning, hoppar över", f / 1e6, e.activity)
                    if all(abs(f - x) > 500 for x in self.win.suggested):
                        self.win.suggested.append(f)
                    self._leave(now, forced=True)
                    return
                if self.max_listen > 0 and now - self.heard_t > self.max_listen:
                    log.info("Skanner: maxtid %.0f s nådd på %.4f MHz, fortsätter",
                             self.max_listen, self.win.freq / 1e6)
                    self._leave(now, forced=True)
                    return
                # prioritet i samma fönster: byt direkt om den blir aktiv (utan att stämma om)
                if (not self.cur_prio and self._prio_ok(now) and avg is not None and abs(avg_c - e.center) <= 1
                        and self._prio_in(avg_c) and cnt >= 2):
                    lvl, snr = self._measure(avg, avg_c, self.prio, self._floor(avg))
                    if self._active(lvl, snr):
                        log.info("Skanner: prioritetskanalen blev aktiv – byter")
                        self._end_hit(now)
                        self._listen_on(self.prio, now, self._floor(avg), is_prio=True, snr=snr)
                        return
                self.hold_until = now + self.hold
                if not self.cur_prio:
                    self._prio_due(now, avg_c)
                return
            if now < self.hold_until:
                return
            self._leave(now)

        # ── prioritetskanal ──
        def _prio_ok(self, now):
            p = self.prio
            return (p is not None and now >= self.prio_quiet and int(p["f"]) not in self.visited
                    and not self.locked(p["f"], p["mode"], p["bw"]))

        def _prio_in(self, avg_c):
            p = self.prio
            if not p or avg_c is None or self._kiwi():
                return False
            d = abs(p["f"] - avg_c)
            return d <= SR * 0.45 and d > p["bw"] / 2 + self.GUARD

        def _prio_due(self, now, avg_c):
            """Utflykt till prioritetskanalen om den ligger utanför fönstret och det är dags."""
            if not self._prio_ok(now) or now < self.prio_next or self._file():
                return False
            self.prio_next = now + self.prio_every
            if self._prio_in(avg_c):
                return False
            self._back = (self.state, getattr(self, "cur", None), self.win.freq, self.win.engine.mode,
                          getattr(self, "hold_until", 0), getattr(self, "heard", False))
            self._end_hit(now)
            w = self.win
            if self.prio["mode"] != w.engine.mode:
                w.set_mode(self.prio["mode"])
            w.tune(self.prio["f"])
            self.state = "prio"
            self.settle = now + 0.12 + (0.5 if self._kiwi() else 0.0)
            self._reset_at = self.settle
            self._mute()
            return True

        def _return_from_prio(self, now):
            st, cur, f, mode, hold_until, heard = self._back
            w = self.win
            if st == "listen" and cur is not None:
                if mode != w.engine.mode:
                    w.set_mode(mode)
                w.engine.set_center(self.expect_c) if not self._kiwi() else None
                w.tune(f, keep_center=not self._kiwi())
                self.cur = cur
                self.cur_prio = False
                self._sq_for(cur, self.floor)
                self.state = "listen"
                self.settle = now + self.dwell
                self.hold_until = max(hold_until, self.settle + self.hold)
                self.heard = False
            else:
                self._enter_window(now)

    # ── Webbmottagare ──
    class WebRxWindow(Adw.Window):
        """Lista över webbmottagare (KiwiSDR, OpenWebRX, WebSDR) – välj en KiwiSDR som källa."""
        MAX_ROWS = 300
        GROUPS = ["Alla", "Sverige", webbmottagare.SA0BMC, webbmottagare.SK6AG, "Mina mottagare"]
        SORTS = ["Avstånd", "Namn", "Lediga platser"]
        TYPES = ["Alla typer", "KiwiSDR", "OpenWebRX", "WebSDR"]

        def __init__(self, main):
            super().__init__(title="Webbmottagare – Signalspanaren", transient_for=main)
            self.main = main
            c = main.conf
            self.cat = webbmottagare.Catalog(WEBRX_CACHE, c.setdefault("webrx_own", []))
            self.set_default_size(1000, 760)
            self.set_hide_on_close(True)
            self._probed = {}
            self._timer = None
            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar()
            self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                          tooltip_text="Hämta listorna på nytt och kontrollera de som visas")
            self.refresh_btn.connect("clicked", lambda *_: self._refresh(force=True))
            hb.pack_end(self.refresh_btn)
            b = Gtk.Button(label="+ Lägg till")
            b.set_tooltip_text("Lägg till en egen mottagare (skriv in adressen manuellt)")
            b.connect("clicked", lambda *_: self._add_own())
            hb.pack_start(b)
            tv.add_top_bar(hb)

            bar = Gtk.Box(spacing=6)
            for m in ("start", "end", "top", "bottom"):
                getattr(bar, f"set_margin_{m}")(6)
            self.search = Gtk.SearchEntry(placeholder_text="Sök namn, plats, anropssignal …", hexpand=True)
            self.search.set_text(c.get("webrx_search", ""))
            self.search.connect("search-changed", lambda *_: self._queue())
            bar.append(self.search)
            self.dd_type = Gtk.DropDown.new_from_strings(self.TYPES)
            self.dd_type.set_selected(c.get("webrx_type", 0))
            self.dd_group = Gtk.DropDown.new_from_strings(self.GROUPS)
            self.dd_group.set_selected(c.get("webrx_group", 1))
            self.dd_sort = Gtk.DropDown.new_from_strings(self.SORTS)
            self.dd_sort.set_selected(c.get("webrx_sort", 0))
            for d in (self.dd_type, self.dd_group, self.dd_sort):
                d.connect("notify::selected", lambda *_: self._queue())
                bar.append(d)
            self.freq = Gtk.Entry(placeholder_text="Täcker MHz", width_chars=9,
                                  tooltip_text="Visa bara mottagare som tar emot frekvensen (t.ex. 7.074 eller 145.5)")
            self.freq.connect("changed", lambda *_: self._queue())
            bar.append(self.freq)
            self.pos = Gtk.Entry(placeholder_text="Din lokator", width_chars=10,
                                 tooltip_text="Din position för avståndet: Maidenhead-lokator (t.ex. JO99bg) "
                                              "eller lat, lon (t.ex. 59.33, 18.07)")
            self.pos.set_text(c.get("my_pos", ""))
            self.pos.connect("changed", lambda *_: self._queue())
            bar.append(self.pos)
            tv.add_top_bar(bar)

            self.lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.lb.add_css_class("boxed-list")
            for m in ("start", "end", "bottom"):
                getattr(self.lb, f"set_margin_{m}")(10)
            sc = Gtk.ScrolledWindow(vexpand=True)
            sc.set_child(self.lb)
            tv.set_content(sc)
            self.status = Gtk.Label(xalign=0, wrap=True)
            self.status.add_css_class("dim-label")
            self.status.add_css_class("caption")
            for m in ("start", "end", "top", "bottom"):
                getattr(self.status, f"set_margin_{m}")(6)
            tv.add_bottom_bar(self.status)
            self.toasts = Adw.ToastOverlay()
            self.toasts.set_child(tv)
            self.set_content(self.toasts)
            self._rebuild()
            self._refresh(force=self.cat.stale())

        # ── data ──
        def _refresh(self, force=False):
            if force and self.cat.refresh(lambda: GLib.idle_add(self._after_fetch)):
                self.refresh_btn.set_sensitive(False)
                self.status.set_text("Hämtar listor från kiwisdr.com och receiverbook.de …")
            if force:
                self._probed.clear()
            self._probe_visible(force=force)

        def _after_fetch(self):
            self.refresh_btn.set_sensitive(True)
            if self.cat.errors:
                self.toast("Kunde inte hämta: " + ", ".join(self.cat.errors))
            self._rebuild()
            return False

        def _probe_visible(self, force=False):
            """Fråga mottagarna själva om status – de svenska/egna direkt, annars när listan är kort."""
            now = time.time()
            want = [r["url"] for r in self.cat.all() if r["grupp"]]
            want += [r["url"] for r in getattr(self, "_shown", [])[:40]] if len(getattr(self, "_shown", [])) <= 40 else []
            todo = [u for u in dict.fromkeys(want) if force or now - self._probed.get(u, 0) > 600]
            if not todo:
                return
            for u in todo:
                self._probed[u] = now
            self.cat.probe(todo, each=lambda: GLib.idle_add(self._soon_rebuild))

        def _soon_rebuild(self):
            if not getattr(self, "_rb_timer", None):
                def go():
                    self._rb_timer = None
                    self._rebuild()
                    return False
                self._rb_timer = GLib.timeout_add(400, go)
            return False

        def home(self):
            try:
                return webbmottagare.parse_position(self.pos.get_text())
            except ValueError:
                return None

        def _filtered(self):
            L = self.cat.all()
            t = self.dd_type.get_selected()
            if t:
                L = [r for r in L if r["typ"] == self.TYPES[t]]
            g = self.GROUPS[self.dd_group.get_selected()]
            if g == "Sverige":
                L = [r for r in L if webbmottagare.is_swedish(r)]
            elif g == "Mina mottagare":
                L = [r for r in L if r["grupp"] == "Mina"]
            elif g != "Alla":
                L = [r for r in L if r["grupp"] == g]
            q = self.search.get_text().strip()
            if q:
                L = [r for r in L if webbmottagare.matches(r, q)]
            ft = self.freq.get_text().strip()
            if ft:
                try:
                    hz = parse_freq(ft)
                    L = [r for r in L if not r["band"] or webbmottagare.covers(r, hz)]
                except ValueError:
                    pass
            home = self.home()
            s = self.dd_sort.get_selected()
            dead = lambda r: 1 if (r.get("status") and not r["status"].get("ok")) else 0
            if s == 0 and home:
                L.sort(key=lambda r: (dead(r), webbmottagare.distance_km(home, r["pos"]) or 1e9))
            elif s == 2:
                L.sort(key=lambda r: (dead(r), -((r["users_max"] or 0) - (r["users"] or 0)) if r["users"] is not None
                                      else 1, r["namn"].lower()))
            else:
                L.sort(key=lambda r: (dead(r), r["namn"].lower()))
            return L

        # ── visning ──
        def _queue(self):
            if self._timer:
                GLib.source_remove(self._timer)
            self._timer = GLib.timeout_add(250, self._apply)

        def _apply(self):
            self._timer = None
            c = self.main.conf
            c["webrx_search"] = self.search.get_text()
            c["webrx_type"], c["webrx_group"], c["webrx_sort"] = (self.dd_type.get_selected(),
                                                                   self.dd_group.get_selected(),
                                                                   self.dd_sort.get_selected())
            if self.home() or not self.pos.get_text().strip():
                c["my_pos"] = self.pos.get_text().strip()
            self._rebuild()
            self._probe_visible()
            return False

        def _rebuild(self):
            L = self._filtered()
            self._shown = L
            while (ch := self.lb.get_first_child()) is not None:
                self.lb.remove(ch)
            home = self.home()
            cur = webbmottagare.norm_url(self.main.conf.get("kiwi_addr", "")) if self.main.conf.get("kiwi_addr") else None
            for r in L[:self.MAX_ROWS]:
                self.lb.append(self._row(r, home, cur))
            if not L:
                lab = Adw.ActionRow(title="Inga mottagare matchar",
                                    subtitle="Ändra sökningen eller filtren, eller lägg till en egen med ”+ Lägg till”")
                self.lb.append(lab)
            n_all = len(self.cat.all())
            age = time.time() - self.cat.fetched if self.cat.fetched else None
            src = (f"listor hämtade för {int(age // 3600)} h {int(age % 3600 // 60)} min sedan" if age is not None
                   else "listorna är inte hämtade ännu")
            more = f" (de första {self.MAX_ROWS} – sök för att begränsa)" if len(L) > self.MAX_ROWS else ""
            self.status.set_text(f"Visar {min(len(L), self.MAX_ROWS)} av {len(L)} som matchar, {n_all} totalt{more} · {src} · "
                                 "Källor: kiwisdr.com (via rx.linkfanel.net), receiverbook.de, SA0BMC:s länktips, SK6AG. "
                                 "Bara KiwiSDR kan tas emot i Signalspanaren – OpenWebRX och WebSDR öppnas i webbläsaren.")
            return False

        def _row(self, r, home, cur):
            esc = GLib.markup_escape_text
            row = Adw.ActionRow(title=esc(r["namn"] or r["url"]), subtitle=esc(webbmottagare.summary(r, home)))
            row.set_subtitle_lines(2)
            row.set_tooltip_text("\n".join(x for x in (r["url"], r["antenn"] and f"Antenn: {r['antenn']}",
                                                       r["snr"] and f"SNR: {r['snr']} dB",
                                                       "Från: " + ", ".join(r["källor"])) if x))
            badge = Gtk.Label(label={"KiwiSDR": "Kiwi", "OpenWebRX": "OWRX", "WebSDR": "WebSDR"}.get(r["typ"], r["typ"]),
                              width_chars=6, valign=Gtk.Align.CENTER)
            badge.add_css_class("caption")
            badge.add_css_class("accent" if r["typ"] == "KiwiSDR" else "dim-label")
            row.add_prefix(badge)
            st = r.get("status")
            if st:
                ic = Gtk.Image.new_from_icon_name("emblem-ok-symbolic" if st.get("ok") else "dialog-warning-symbolic")
                ic.set_tooltip_text(f"Svarade på {st.get('ms', 0)} ms" if st.get("ok") else st.get("fel", "svarar inte"))
                row.add_prefix(ic)
            if r["typ"] == "KiwiSDR" and not webbmottagare.kiwi_ok(r):
                lab = Gtk.Label(label="Bara webbläsare", valign=Gtk.Align.CENTER)
                lab.add_css_class("dim-label")
                lab.add_css_class("caption")
                lab.set_tooltip_text("Ägaren har stängt den här Kiwin för andra program än webbläsaren "
                                     "(ext_api=0). Öppna den med 🌐.")
                row.add_suffix(lab)
            elif r["typ"] == "KiwiSDR":
                b = Gtk.Button(label="Används" if cur == webbmottagare.norm_url(r["url"]) else "Använd",
                               valign=Gtk.Align.CENTER)
                b.add_css_class("suggested-action")
                b.set_tooltip_text("Ta emot från den här KiwiSDR:n i Signalspanaren")
                b.connect("clicked", lambda *_: self.main.use_webrx(r))
                row.add_suffix(b)
            w = Gtk.Button(label="🌐", valign=Gtk.Align.CENTER, tooltip_text="Öppna i webbläsaren")
            w.add_css_class("flat")
            w.connect("clicked", lambda *_: self.main.open_uri(r["url"]))
            row.add_suffix(w)
            own = any(webbmottagare.norm_url(o["url"]) == webbmottagare.norm_url(r["url"]) for o in self.cat.own)
            s = Gtk.Button(icon_name="user-trash-symbolic" if r["grupp"] == "Mina" else
                           ("starred-symbolic" if own else "non-starred-symbolic"), valign=Gtk.Align.CENTER,
                           tooltip_text="Ta bort från Mina mottagare" if own else "Spara i Mina mottagare")
            s.add_css_class("flat")
            s.connect("clicked", lambda *_: self._toggle_own(r))
            row.add_suffix(s)
            return row

        def _toggle_own(self, r):
            k = webbmottagare.norm_url(r["url"])
            own = self.cat.own
            hit = [o for o in own if webbmottagare.norm_url(o["url"]) == k]
            if hit:
                for o in hit:
                    own.remove(o)
                self.toast(f"Borttagen från Mina mottagare: {r['namn'] or r['url']}")
            else:
                own.append({"url": r["url"], "namn": r["namn"], "typ": r["typ"]})
                self.toast(f"Sparad i Mina mottagare: {r['namn'] or r['url']}")
            self.main.conf["webrx_own"] = own
            self._rebuild()

        def _add_own(self):
            dlg = Adw.MessageDialog(transient_for=self, heading="Lägg till egen mottagare",
                                    body="Skriv adressen du surfar in på. Typen (KiwiSDR, OpenWebRX eller WebSDR) "
                                         "känns igen automatiskt.")
            lb = Gtk.ListBox()
            lb.add_css_class("boxed-list")
            addr = Adw.EntryRow(title="Adress, t.ex. http://sk6ag1.ddns.net:8071")
            name = Adw.EntryRow(title="Namn (valfritt)")
            lb.append(addr)
            lb.append(name)
            dlg.set_extra_child(lb)
            dlg.add_response("cancel", "Avbryt")
            dlg.add_response("ok", "Lägg till")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_default_response("ok")

            def resp(_d, rid):
                u = addr.get_text().strip()
                if rid != "ok" or not u:
                    return
                url = webbmottagare.clean_url(u)
                self.cat.own.append({"url": url, "namn": name.get_text().strip(), "typ": "KiwiSDR"})
                self.main.conf["webrx_own"] = self.cat.own
                self.dd_group.set_selected(self.GROUPS.index("Mina mottagare"))
                self.toast("Tillagd – kontrollerar mottagaren …")

                def probed():
                    st = self.cat.status.get(webbmottagare.norm_url(url)) or {}
                    for o in self.cat.own:
                        if o["url"] == url:
                            if st.get("typ"):
                                o["typ"] = st["typ"]
                            if not o["namn"] and st.get("namn"):
                                o["namn"] = st["namn"]
                    self._rebuild()
                    self.toast(f"{st.get('typ') or 'Okänd typ'}: {st.get('namn') or url}" if st.get("ok")
                               else f"Svarar inte just nu: {st.get('fel', '')}")
                    return False
                self.cat.probe([url], lambda: GLib.idle_add(probed))
            dlg.connect("response", resp)
            dlg.present()

        def toast(self, msg):
            self.toasts.add_toast(Adw.Toast.new(msg))

    # ── Fartygskarta (AIS) ──
    class ShipWindow(Adw.Window):
        MAX_AGE = 30 * 60          # fartyg försvinner efter 30 min utan meddelanden

        def __init__(self, main):
            super().__init__(title="Fartyg (AIS) – Signalspanaren")
            import kartvy
            self.K = kartvy
            self.main = main
            self.rows = {}
            self.set_default_size(1250, 780)
            self.set_hide_on_close(True)
            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar()
            self.status = Gtk.Label(label="Väntar på AIS …")
            hb.set_title_widget(self.status)
            b = Gtk.Button(label="Visa alla")
            b.connect("clicked", lambda *_: self.mp.fit_all() or main.toast("Inga fartyg med position ännu"))
            hb.pack_end(b)
            b2 = Gtk.Button(label="Gå till AIS (162 MHz)")
            b2.set_tooltip_text("Ställ in mottagaren på AIS-kanalerna och slå på AIS-avkodningen")
            b2.connect("clicked", lambda *_: main._goto_decoder("AIS"))
            hb.pack_start(b2)
            tv.add_top_bar(hb)

            lat, lon, zoom = main.conf.get("ship_map_view", [59.33, 18.06, 9.0])
            self.mp = kartvy.MapPanel(center=(lat, lon), zoom=zoom or 9.0, on_select=self._select,
                                      user_agent=f"Signalspanaren/{VERSION} (+https://github.com/EriHil/signalspanaren)",
                                      toast=main.toast)
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            left.append(self.mp)
            legend = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=0,
                                 max_children_per_line=20, homogeneous=False)
            legend.set_margin_start(8)
            legend.set_margin_top(4)
            legend.set_margin_bottom(4)
            for cat in ("last", "tank", "passagerare", "fiske", "militar", "fritid", "tjanst", "okand"):
                r, g, bb = kartvy.COLORS[cat]
                lb = Gtk.Label()
                lb.set_markup(f'<span foreground="#{int(r * 255):02x}{int(g * 255):02x}{int(bb * 255):02x}">■</span> '
                              f'{GLib.markup_escape_text(kartvy.CATEGORY_NAMES[cat])}')
                lb.add_css_class("caption")
                legend.append(lb)
            lb = Gtk.Label(label="○ förtöjd/till ankars")
            lb.add_css_class("caption")
            legend.append(lb)
            left.append(legend)                  # FlowBox: radbryts när kartan är smal

            cols = [
                ("MMSI", lambda d: d["mmsi"], "mmsi", False),
                ("Namn", lambda d: d["name"] or d["special"], "name", False),
                ("Anrop", lambda d: d["call"], "call", False),
                ("Typ", lambda d: avkodare.ais_ship_type(d["shiptype"]) if d["shiptype"] is not None else "", "shiptype", False),
                ("Status", lambda d: d["status"], "status", False),
                ("Fart", lambda d: f"{d['sog']:.1f} kn" if d["sog"] is not None else "", "sog", False),
                ("Kurs", lambda d: f"{d['cog']:.0f}°" if d["cog"] is not None else "", "cog", False),
                ("Position", lambda d: f"{d['pos'][0]:.4f}, {d['pos'][1]:.4f}" if d["pos"] else "", "pos", False),
                ("Destination", lambda d: d["dest"], "dest", True),
                ("Senast", lambda d: _ago(d["last"]), "last", False),
            ]
            (self.store, self.cv, self.sel, _f, tcols, sw) = make_table(cols)
            self.cv.sort_by_column(tcols[-1], Gtk.SortType.DESCENDING)
            self.sel.set_autoselect(False)
            self.sel.set_can_unselect(True)
            self.sel.connect("notify::selected-item", self._on_row)
            self.cv.connect("activate", lambda cv, pos: self._osm(self.sel.get_item(pos)))
            right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            hint = Gtk.Label(label="Klicka: visa på kartan · Dubbelklicka: öppna i OpenStreetMap", xalign=0)
            hint.add_css_class("dim-label")
            hint.set_margin_start(8)
            hint.set_margin_top(4)
            hint.set_margin_bottom(4)
            right.append(hint)
            right.append(sw)
            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            paned.set_start_child(left)
            paned.set_end_child(right)
            paned.set_position(700)
            tv.set_content(paned)
            self.set_content(tv)
            self._fit_n = 0
            self._opened = time.time()
            self._auto_fit = "ship_map_view" not in main.conf
            self.connect("close-request", self._on_close)

        def _on_close(self, *_):
            self.main.conf["ship_map_view"] = list(self.mp.view())
            return False

        def _select(self, mmsi):
            m = self.sel.get_model()
            for i in range(m.get_n_items()):
                if m.get_item(i).data["mmsi"] == mmsi:
                    self.sel.set_selected(i)
                    return

        def _on_row(self, sel, _p):
            it = sel.get_selected_item()
            if it:
                self._auto_fit = False          # användaren styr kartan nu
                self.mp.select(it.data["mmsi"])

        def _osm(self, item):
            if item and item.data["pos"]:
                lat, lon = item.data["pos"]
                try:
                    Gio.AppInfo.launch_default_for_uri(
                        f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=13/{lat:.5f}/{lon:.5f}", None)
                except GLib.Error as ex:
                    log.error("Kunde inte öppna kartan: %s", ex)

        def refresh(self):
            K = self.K
            now = time.time()
            ships = self.main.ships
            for mmsi in [k for k, v in ships.items() if now - v["last"] > self.MAX_AGE]:
                ships.pop(mmsi)
                row = self.rows.pop(mmsi, None)
                if row:
                    ok, pos = self.store.find(row)
                    if ok:
                        self.store.remove(pos)
                self.mp.remove(mmsi)
            for mmsi, d in ships.items():
                row = self.rows.get(mmsi)
                if row is None:
                    row = Row(d)
                    self.rows[mmsi] = row
                    self.store.append(row)
                else:
                    row.data = d
                    row.emit("updated")
                if d["pos"]:
                    cat = avkodare.ais_category(d["shiptype"], mmsi)
                    still = d["nav"] in (1, 5) or (d["sog"] is not None and d["sog"] < 0.5)
                    shape = "dot" if d["special"] else ("ring" if still else "ship")
                    head = d["hdg"] if d["hdg"] is not None else (d["cog"] or 0)
                    title = d["name"] or d["special"] or mmsi
                    if d["sog"] is not None and not still and not d["special"]:
                        title += f"\n{d['sog']:.1f} kn"
                    self.mp.update(mmsi, d["pos"][0], d["pos"][1], heading=head, title=title,
                                   color=K.COLORS[cat], shape=shape, alert=d["alert"], trail=not d["special"])
            npos = sum(1 for d in ships.values() if d["pos"])
            self.status.set_text(f"{len(ships)} fartyg · {npos} med position")
            # anpassa kartan när fler fartyg dyker upp de första minuterna
            if self._auto_fit and npos > self._fit_n and time.time() - self._opened < 180:
                self._fit_n = npos
                self.mp.fit_all()

    def _ago(t):
        d = max(0, time.time() - t)
        return f"{d:.0f} s" if d < 120 else f"{d / 60:.0f} min"

    # ── Aktivitetskarta (värmekarta över ett band) ──
    class ActivityView(Gtk.DrawingArea):
        def __init__(self, win):
            super().__init__(hexpand=True, vexpand=True)
            self.win = win
            self.mon = None
            self.lut = make_lut(win.conf.get("act_colormap", "klassisk"))
            self.dmax = 35.0
            self.mouse = None
            self.set_draw_func(self.on_draw)
            g = Gtk.GestureClick(button=1)
            g.connect("pressed", self._click)
            self.add_controller(g)
            mo = Gtk.EventControllerMotion()
            mo.connect("motion", lambda c, x, y: (setattr(self, "mouse", (x, y)), self.queue_draw()))
            mo.connect("leave", lambda c: (setattr(self, "mouse", None), self.queue_draw()))
            self.add_controller(mo)

        def layout(self, H):
            occ_h, axis_h = 72, 20
            return occ_h, axis_h, max(1, H - occ_h - axis_h)

        def _x_to_freq(self, x, W):
            m = self.mon
            return m.lo + max(0.0, min(1.0, x / max(1, W))) * (m.hi - m.lo)

        def _click(self, g, n, x, y):
            if self.mon:
                self.win.tune_to(self._x_to_freq(x, self.get_width()))

        def on_draw(self, area, cr, W, H, *_):
            cr.set_source_rgb(0.05, 0.06, 0.08)
            cr.paint()
            m = self.mon
            if m is None or W < 20 or H < 60:
                cr.set_source_rgba(1, 1, 1, 0.6)
                cr.set_font_size(15)
                msg = "Välj band och tryck ▶ Svep"
                e = cr.text_extents(msg)
                cr.move_to((W - e.width) / 2, H / 2)
                cr.show_text(msg)
                return
            occ_h, axis_h, heat_h = self.layout(H)
            heat_y = occ_h
            arr, times = m.image()
            occ = m.occupancy()
            peak = m.peak_hold()

            # ── värmekarta ──
            if arr is not None and len(arr):
                rows = arr.shape[0]
                idx = np.clip(arr / self.dmax * 255.0, 0, 255).astype(np.uint8)
                rgba = np.ascontiguousarray(self.lut[idx])
                surf = cairo.ImageSurface.create_for_data(memoryview(rgba), cairo.FORMAT_ARGB32,
                                                          m.ncols, rows, m.ncols * 4)
                cr.save()
                cr.rectangle(0, heat_y, W, heat_h)
                cr.clip()
                cr.translate(0, heat_y)
                cr.scale(W / m.ncols, heat_h / rows)
                cr.set_source_surface(surf, 0, 0)
                cr.get_source().set_filter(cairo.FILTER_NEAREST)
                cr.paint()
                cr.restore()

            # ── spärrade kanaler (t.ex. WFM 96–108 MHz) ──
            if getattr(m, "excluded", None) is not None and m.excluded.any():
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.33)
                col = 0
                while col < m.ncols:
                    if m.excluded[col]:
                        c2 = col
                        while c2 < m.ncols and m.excluded[c2]:
                            c2 += 1
                        x0 = col / m.ncols * W
                        x1 = c2 / m.ncols * W
                        cr.rectangle(x0, heat_y, x1 - x0, heat_h)
                        cr.fill()
                        cx = (x0 + x1) / 2
                        if x1 - x0 > 60:
                            cr.set_source_rgba(1, 1, 1, 0.6)
                            cr.set_font_size(11)
                            t = "utesluten (WFM)"
                            e = cr.text_extents(t)
                            cr.move_to(cx - e.width / 2, heat_y + heat_h / 2)
                            cr.show_text(t)
                            cr.set_source_rgba(0.5, 0.5, 0.5, 0.33)
                        col = c2
                    else:
                        col += 1

            # ── upptagenhetsremsa ──
            cr.set_source_rgb(0.08, 0.09, 0.12)
            cr.rectangle(0, 0, W, occ_h)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.5)
            cr.set_font_size(10)
            cr.move_to(4, 12)
            cr.show_text("Upptagenhet")
            base = occ_h - 3
            for col in range(m.ncols):
                o = occ[col]
                if o <= 0:
                    continue
                x0 = col / m.ncols * W
                x1 = (col + 1) / m.ncols * W
                h = o * (occ_h - 16)
                r, g, b = (0.3 + 0.7 * o, 0.8 - 0.6 * o, 0.2)
                cr.set_source_rgb(r, g, b)
                cr.rectangle(x0, base - h, max(1, x1 - x0), h)
                cr.fill()
            # topp-håll som ljus linje
            cr.set_source_rgba(0.7, 0.85, 1, 0.5)
            cr.set_line_width(1)
            for col in range(0, m.ncols, max(1, m.ncols // W or 1)):
                x = col / m.ncols * W
                yy = base - min(1.0, peak[col] / self.dmax) * (occ_h - 16)
                cr.line_to(x, yy)
            cr.stroke()

            # ── frekvensaxel + bandplan ──
            ay = heat_y + heat_h
            cr.set_source_rgb(0.09, 0.1, 0.13)
            cr.rectangle(0, ay, W, axis_h)
            cr.fill()
            for blo, bhi, name in BANDPLAN:
                x0 = (blo * 1e6 - m.lo) / (m.hi - m.lo) * W
                x1 = (bhi * 1e6 - m.lo) / (m.hi - m.lo) * W
                if x1 < 0 or x0 > W:
                    continue
                hh = (sum(map(ord, name)) * 47) % 360 / 360.0
                import colorsys
                r, g, b = colorsys.hsv_to_rgb(hh, 0.5, 0.6)
                cr.set_source_rgba(r, g, b, 0.9)
                cr.rectangle(max(0, x0), ay, min(W, x1) - max(0, x0), 3)
                cr.fill()
            span = m.hi - m.lo
            raw = span / 9
            mag = 10 ** math.floor(math.log10(raw))
            tick = next(mm * mag for mm in (1, 2, 2.5, 5, 10) if mm * mag >= raw)
            dec = max(0, min(4, -int(math.floor(math.log10(tick / 1e6)))) if tick < 1e6 else 0)
            cr.set_font_size(10)
            ff = math.ceil(m.lo / tick) * tick
            while ff < m.hi:
                x = (ff - m.lo) / span * W
                cr.set_source_rgba(1, 1, 1, 0.07)
                cr.move_to(int(x) + 0.5, heat_y)
                cr.line_to(int(x) + 0.5, ay)
                cr.stroke()
                cr.set_source_rgba(1, 1, 1, 0.65)
                txt = f"{ff / 1e6:.{dec}f}"
                e = cr.text_extents(txt)
                cr.move_to(x - e.width / 2, ay + 14)
                cr.show_text(txt)
                ff += tick

            # ── muspekare ──
            if self.mouse:
                mx, my = self.mouse
                fm = self._x_to_freq(mx, W)
                col = m.freq_col(fm)
                txt = f"{fm / 1e6:.4f} MHz   upptagen {occ[col] * 100:.0f}%   topp {peak[col]:.0f} dB"
                b = band_name(fm)
                if b:
                    txt += f"   {b}"
                if arr is not None and len(arr) and heat_y <= my <= heat_y + heat_h:
                    ri = int((my - heat_y) / heat_h * len(arr))
                    if 0 <= ri < len(arr):
                        txt += f"   [{arr[ri][col]:.0f} dB]"
                cr.set_source_rgba(1, 1, 1, 0.3)
                cr.set_line_width(1)
                cr.move_to(int(mx) + 0.5, heat_y)
                cr.line_to(int(mx) + 0.5, heat_y + heat_h)
                cr.stroke()
                cr.set_font_size(12)
                e = cr.text_extents(txt)
                cr.set_source_rgba(0, 0, 0, 0.7)
                cr.rectangle(W - e.width - 14, heat_y + 3, e.width + 10, 18)
                cr.fill()
                cr.set_source_rgb(1, 1, 1)
                cr.move_to(W - e.width - 9, heat_y + 16)
                cr.show_text(txt)

    class ActivityWindow(Adw.Window):
        def __init__(self, main):
            super().__init__(title="Aktivitetskarta – Signalspanaren")
            import aktivitet
            self.AK = aktivitet
            self.main = main
            self.mon = None
            self.mode = "NFM"
            self.timer = None
            self.set_default_size(1180, 760)
            self.set_hide_on_close(True)

            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar()
            self.btn = Gtk.ToggleButton(label="▶ Svep")
            self.btn.add_css_class("suggested-action")
            self.btn.connect("toggled", self._on_toggle)
            hb.pack_start(self.btn)
            names = [p[0] for p in aktivitet.PRESETS] + ["Eget intervall"]
            self.preset = Gtk.DropDown.new_from_strings(names)
            self.preset.connect("notify::selected", self._on_preset)
            hb.pack_start(self.preset)
            self.status = Gtk.Label(label="Välj band och tryck ▶ Svep")
            hb.set_title_widget(self.status)
            tv.add_top_bar(hb)

            bar = Gtk.Box(spacing=6)
            bar.add_css_class("toolbar")
            self.e_lo = Gtk.Entry(width_chars=8, placeholder_text="Från MHz")
            self.e_hi = Gtk.Entry(width_chars=8, placeholder_text="Till MHz")
            bar.append(Gtk.Label(label="Band:"))
            bar.append(self.e_lo)
            bar.append(Gtk.Label(label="–"))
            bar.append(self.e_hi)
            bar.append(Gtk.Label(label="MHz"))
            self.thr = Gtk.SpinButton.new_with_range(3, 40, 1)
            self.thr.set_value(main.conf.get("act_thresh", 6))
            self.thr.connect("value-changed", self._on_thr)
            bar.append(Gtk.Label(label="   Tröskel:"))
            bar.append(self.thr)
            bar.append(Gtk.Label(label="dB   Färg:"))
            cmaps = list(COLORMAPS)
            self.cmap = Gtk.DropDown.new_from_strings(cmaps)
            self.cmap.set_selected(cmaps.index(main.conf.get("act_colormap", "klassisk"))
                                   if main.conf.get("act_colormap", "klassisk") in cmaps else 0)
            self.cmap.connect("notify::selected", self._on_cmap)
            bar.append(self.cmap)
            tv.add_top_bar(bar)

            self.view = ActivityView(main)
            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            paned.set_start_child(self.view)
            paned.set_resize_start_child(True)
            cols = [("Frekvens (MHz)", lambda d: fmt_mhz(d["f"]), "f", False),
                    ("Upptagen", lambda d: f"{d['occ'] * 100:.0f} %", "occ", False),
                    ("Topp (dB)", lambda d: f"{d['pk']:.0f}", "pk", True)]
            (self.bstore, self.bcv, self.bsel, _f, _c, bsw) = make_table(cols)
            self.bcv.sort_by_column(_c[1], Gtk.SortType.DESCENDING)
            self.bcv.connect("activate", lambda cv, pos: self.tune_to(self.bsel.get_item(pos).data["f"]))
            rbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            lbl = Gtk.Label(label="Mest aktiva kanaler", xalign=0)
            lbl.add_css_class("heading")
            lbl.set_margin_start(8)
            lbl.set_margin_top(6)
            rbox.append(lbl)
            hint = Gtk.Label(label="Klicka i kartan eller en rad för att lyssna", xalign=0)
            hint.add_css_class("dim-label")
            hint.set_margin_start(8)
            rbox.append(hint)
            rbox.append(bsw)
            paned.set_end_child(rbox)
            paned.set_position(880)
            tv.set_content(paned)
            self.set_content(tv)
            self._set_preset(0)
            self.connect("close-request", self._on_close)

        def _set_preset(self, i):
            P = self.AK.PRESETS
            if 0 <= i < len(P):
                _n, lo, hi, mode, _step = P[i]
                self.e_lo.set_text(f"{lo:g}")
                self.e_hi.set_text(f"{hi:g}")
                self.mode = mode

        def _on_preset(self, dd, _p):
            self._set_preset(dd.get_selected())

        def _on_thr(self, sb):
            self.main.conf["act_thresh"] = sb.get_value()
            if self.mon:
                self.mon.thresh = sb.get_value()
            self.view.queue_draw()

        def _on_cmap(self, dd, _p):
            name = list(COLORMAPS)[dd.get_selected()]
            self.main.conf["act_colormap"] = name
            self.view.lut = make_lut(name)
            self.view.queue_draw()

        def tune_to(self, freq):
            self.main.tune(freq, self.mode)
            self.main.toast(f"Mottagaren ställd på {fmt_mhz(freq)} MHz ({self.mode}) – "
                            "svepet pausas medan du lyssnar" if self.btn.get_active() else
                            f"Mottagaren ställd på {fmt_mhz(freq)} MHz")
            if self.btn.get_active():
                self.btn.set_active(False)     # pausa svepet så man kan lyssna

        def _on_toggle(self, btn):
            if btn.get_active():
                if not self.main.engine.running:
                    self.main.toast("Starta mottagningen först")
                    btn.set_active(False)
                    return
                try:
                    lo = parse_freq(self.e_lo.get_text())
                    hi = parse_freq(self.e_hi.get_text())
                    self.mon = self.AK.BandMonitor(lo, hi, thresh_db=self.thr.get_value())
                except ValueError as ex:
                    self.main.toast(str(ex))
                    btn.set_active(False)
                    return
                self.main.scanner.stop()
                self.main.scan_btn.set_title("Starta skanning")
                self.main.activity_active = True
                self.view.mon = self.mon
                btn.set_label("■ Stoppa")
                btn.remove_css_class("suggested-action")
                btn.add_css_class("destructive-action")
                log.info("Aktivitetskarta startad: %.3f–%.3f MHz, %d kanaler, %d fönster",
                         lo / 1e6, hi / 1e6, self.mon.ncols, len(self.mon.spans))
                if self.timer is None:
                    self.timer = GLib.timeout_add(120, self._tick)
            else:
                self.main.activity_active = False
                btn.set_label("▶ Svep")
                btn.remove_css_class("destructive-action")
                btn.add_css_class("suggested-action")

        def _tick(self):
            if not self.btn.get_active():
                self.timer = None
                return False
            if not self.main.engine.running:
                self.btn.set_active(False)
                self.timer = None
                return False
            try:
                self.mon.tick(self.main.engine)
            except Exception:
                log.exception("Fel i aktivitetskartan")
                self.btn.set_active(False)
                self.timer = None
                return False
            self.view.queue_draw()
            self._update_list()
            return True

        def _update_list(self):
            busy = self.mon.busiest(40)
            have = {round(self.bstore.get_item(i).data["f"]): self.bstore.get_item(i)
                    for i in range(self.bstore.get_n_items())}
            seen = set()
            for f, occ, pk in busy:
                seen.add(round(f))
                row = have.get(round(f))
                d = {"f": f, "occ": occ, "pk": pk}
                if row is None:
                    self.bstore.append(Row(d))
                else:
                    row.data = d
                    row.emit("updated")
            for key, row in have.items():
                if key not in seen:
                    ok, pos = self.bstore.find(row)
                    if ok:
                        self.bstore.remove(pos)
            n = self.mon.sweeps
            self.status.set_text(f"{fmt_mhz(self.mon.lo)}–{fmt_mhz(self.mon.hi)} MHz · "
                                 f"{n} svep · {self.mon.sweep_dt:.1f} s/svep")

        def _on_close(self, *_):
            if self.btn.get_active():
                self.btn.set_active(False)
            return False

    # ── Huvudfönster ──
    # ── Signalanalys ──
    ANALYS_BW = [None, 500, 1000, 3000, 6000, 12500, 25000, 50000, 200000]
    ANALYS_BW_NAMES = ["Auto (signalens bredd)", "500 Hz", "1 kHz", "3 kHz", "6 kHz", "12,5 kHz", "25 kHz",
                       "50 kHz", "200 kHz"]

    def _fmt_hz(v, signed=False):
        if v is None:
            return "–"
        a = abs(v)
        sg = ("+" if v >= 0 else "−") if signed else ("−" if v < 0 else "")
        if a >= 1e6:
            return f"{sg}{a / 1e6:.3f} MHz"
        if a >= 1e3:
            return f"{sg}{a / 1e3:.2f} kHz".replace(".", ",")
        return f"{sg}{a:.0f} Hz"

    class MiniWaterfall(Gtk.DrawingArea):
        """Litet vattenfall som täcker den analyserade kanalen."""
        ROWS = 320

        def __init__(self, panel):
            super().__init__(hexpand=True, vexpand=True)
            self.panel = panel
            self.set_size_request(240, 80)
            self.wf = np.zeros((self.ROWS, analys.ChannelTap.WF_N), np.uint8)
            self.spec = None
            self.fs = None
            self.bw = None
            self.lo_db, self.hi_db = None, None
            self.top_db = None
            self.zoom = 1.0
            self.mouse = None
            self.set_draw_func(self.on_draw)
            g = Gtk.GestureClick(button=1)
            g.connect("pressed", self.on_click)
            self.add_controller(g)
            sc = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
            sc.connect("scroll", self.on_scroll)
            self.add_controller(sc)
            mo = Gtk.EventControllerMotion()
            mo.connect("motion", lambda c, x, y: self._mouse((x, y)))
            mo.connect("leave", lambda c: self._mouse(None))
            self.add_controller(mo)

        def _mouse(self, m):
            self.mouse = m
            self.queue_draw()

        def clear(self):
            self.wf[:] = 0
            self.spec = None
            self.lo_db = self.hi_db = self.top_db = None
            self.queue_draw()

        def push(self, db, fs, bw):
            if fs != self.fs or bw != self.bw:
                self.wf[:] = 0
                self.fs, self.bw = fs, bw
                self.lo_db = None
                self.top_db = None
            fl, pk = float(np.median(db)), float(np.percentile(db, 99.5))
            if self.lo_db is None:
                self.lo_db, self.hi_db = fl - 4, max(pk, fl + 25)
            else:
                self.lo_db = self.lo_db * 0.85 + (fl - 4) * 0.15
                self.hi_db = self.hi_db * 0.85 + max(pk, fl + 25) * 0.15
            q = np.clip((db - self.lo_db) * (255.0 / max(1.0, self.hi_db - self.lo_db)), 0, 255).astype(np.uint8)
            self.wf[1:] = self.wf[:-1]
            self.wf[0] = q
            self.spec = db if self.spec is None else self.spec * 0.4 + db * 0.6
            # spektrumlinjens tak: följer högsta toppen (en smal bärvåg ligger långt över 99,5-percentilen,
            # som bara styr vattenfallets färger). Stiger direkt, sjunker långsamt så att skalan inte hoppar.
            top = float(self.spec.max()) + 3
            if self.lo_db is None or getattr(self, "top_db", None) is None or top > self.top_db:
                self.top_db = top
            else:
                self.top_db = self.top_db * 0.97 + top * 0.03

        def span(self):
            if not self.fs:
                return 1.0
            return float(min(self.fs, max(200.0, (self.bw or self.fs) * 1.3 * self.zoom)))

        def x_to_off(self, x):
            sp = self.span()
            return (x / max(1, self.get_width()) - 0.5) * sp

        def on_click(self, g, n, x, y):
            if self.fs:
                self.panel.retarget_offset(self.x_to_off(x))

        def on_scroll(self, ctrl, dx, dy):
            self.zoom = float(np.clip(self.zoom * (1.25 if dy > 0 else 0.8), 0.15, 4.0))
            self.queue_draw()
            return True

        def on_draw(self, area, cr, W, H, *_):
            S = self.panel.win.skin()
            cr.set_source_rgb(*S["bg"][:3])
            cr.paint()
            if not self.fs or W < 20 or H < 40:
                cr.set_source_rgba(*S["text"])
                cr.set_font_size(12)
                msg = self.panel.empty_msg()
                ext = cr.text_extents(msg)
                cr.move_to(max(4, (W - ext.width) / 2), H / 2)
                cr.show_text(msg)
                return
            N = self.wf.shape[1]
            sp = self.span()
            k0 = int(round(N / 2 - sp / self.fs * N / 2))
            k1 = int(round(N / 2 + sp / self.fs * N / 2))
            k0, k1 = max(0, k0), min(N, max(k0 + 2, k1))
            spec_h = int(H * 0.3)
            axis_h = 14
            wf_y = spec_h + axis_h
            wf_h = H - wf_y
            # vattenfall
            cols = np.linspace(k0, k1 - 1e-6, W).astype(np.int64)
            rows = int(min(self.ROWS, max(1, wf_h)))
            img = self.wf[:rows][:, cols]
            rgba = np.ascontiguousarray(self.panel.win.view.lut[img])
            surf = cairo.ImageSurface.create_for_data(memoryview(rgba), cairo.FORMAT_ARGB32, W, rows, W * 4)
            cr.save()
            cr.rectangle(0, wf_y, W, wf_h)
            cr.clip()
            cr.translate(0, wf_y)
            cr.scale(1, wf_h / rows)
            cr.set_source_surface(surf, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            cr.paint()
            cr.restore()
            # spektrum
            cr.set_source_rgb(*S["spec_bg"][:3])
            cr.rectangle(0, 0, W, spec_h)
            cr.fill()
            if self.spec is not None:
                v = self.spec[cols]
                top = max(self.hi_db + 3, self.top_db if self.top_db is not None else -1e9)
                rng = max(1.0, top - (self.lo_db - 3))
                yv = np.clip((top - v) / rng, 0, 1) * spec_h
                cr.move_to(0, spec_h)
                for i in range(W):
                    cr.line_to(i, yv[i])
                cr.line_to(W, spec_h)
                cr.close_path()
                grad = cairo.LinearGradient(0, 0, 0, spec_h)
                grad.add_color_stop_rgba(0, *S["fill_top"])
                grad.add_color_stop_rgba(1, *S["fill_bot"])
                cr.set_source(grad)
                cr.fill_preserve()
                cr.set_source_rgb(*S["line"][:3])
                cr.set_line_width(1.1)
                cr.stroke()
            # axel
            cr.set_source_rgb(*S["axis_bg"][:3])
            cr.rectangle(0, spec_h, W, axis_h)
            cr.fill()
            cr.set_font_size(9)
            cr.set_source_rgba(*S["text"])
            for fr in (0.0, 0.25, 0.5, 0.75, 1.0):
                off = (fr - 0.5) * sp
                txt = "0" if abs(off) < 1e-6 else _fmt_hz(off, True)
                ext = cr.text_extents(txt)
                xx = fr * W - ext.width / 2
                cr.move_to(min(max(2, xx), W - ext.width - 2), spec_h + 11)
                cr.show_text(txt)

            def xo(off):
                return (off / sp + 0.5) * W
            # kanalgränser
            if self.bw:
                cr.set_source_rgba(*S["analys"][:3], 0.8)
                cr.set_line_width(1)
                cr.set_dash([4, 3])
                for e in (-self.bw / 2, self.bw / 2):
                    cr.move_to(int(xo(e)) + 0.5, 0)
                    cr.line_to(int(xo(e)) + 0.5, H)
                cr.stroke()
                cr.set_dash([])
            # mätta toner och mittfrekvens
            r = self.panel.last
            if r and r.get("fam") not in (None, "BRUS"):
                mid = r.get("f_mid", 0.0)
                cr.set_source_rgba(*S["tuned"][:3], 0.9)
                cr.set_line_width(1.5)
                cr.move_to(xo(mid), 0)
                cr.line_to(xo(mid), spec_h)
                cr.stroke()
                tones = [mid + t for t in r.get("fi_peaks", [])] if r.get("toner") and r.get("fam") != "OFDM" \
                    else (r.get("spec_toner", []) if r.get("fam") == "OFDM" else [])
                cr.set_source_rgba(*S["track"][:3], 0.95)
                for t in tones:
                    x = xo(t)
                    cr.move_to(x, spec_h - 1)
                    cr.line_to(x - 3, spec_h - 7)
                    cr.line_to(x + 3, spec_h - 7)
                    cr.close_path()
                    cr.fill()
                if r.get("occ_bw"):
                    # alla klassningar sätter inte kanterna (t.ex. linjär modem utan spektrumkanter)
                    m0 = r.get("f_mid", 0.0) or 0.0
                    f_lo = r.get("f_lo", m0 - r["occ_bw"] / 2)
                    f_hi = r.get("f_hi", m0 + r["occ_bw"] / 2)
                    cr.set_source_rgba(*S["track"][:3], 0.35)
                    cr.rectangle(xo(f_lo), 1, max(2, xo(f_hi) - xo(f_lo)), 3)
                    cr.fill()
            if self.mouse:
                mx, my = self.mouse
                off = self.x_to_off(mx)
                txt = _fmt_hz(off, True)
                cr.set_font_size(11)
                ext = cr.text_extents(txt)
                cr.set_source_rgba(0, 0, 0, 0.7)
                cr.rectangle(W - ext.width - 12, 3, ext.width + 8, 16)
                cr.fill()
                cr.set_source_rgb(1, 1, 1)
                cr.move_to(W - ext.width - 8, 15)
                cr.show_text(txt)
                cr.set_source_rgba(1, 1, 1, 0.35)
                cr.move_to(int(mx) + 0.5, 0)
                cr.line_to(int(mx) + 0.5, H)
                cr.stroke()

    class AnalysDiagram(Gtk.DrawingArea):
        """Konstellation (PSK/QAM), frekvensfördelning (FSK/FM), envelop (AM/SSB/CW) eller tonspektrum (OFDM)."""

        NAMN = ("Automatiskt", "Konstellation", "Ramraster", "Tonspektrum", "Envelop", "Frekvensfördelning", "Faxbild")

        def __init__(self, panel):
            super().__init__()
            self.panel = panel
            self.sel = "Automatiskt"
            self.set_size_request(200, 70)
            self.set_draw_func(self.on_draw)

        def _har(self, r, kind):
            fd = r.get("bild")
            ras = r.get("ram_raster")
            if kind == "Faxbild":
                return fd is not None and fd.bild is not None and len(fd.bild) > 0
            if kind == "Konstellation":
                return r.get("konst") is not None
            if kind == "Ramraster":
                return ras is not None and len(ras) > 0
            if kind == "Tonspektrum":
                return r.get("fam") == "OFDM" and r.get("spec_f") is not None
            if kind == "Envelop":
                return r.get("kurva") is not None
            if kind == "Frekvensfördelning":
                return r.get("hist") is not None
            return False

        def on_draw(self, area, cr, W, H, *_):
            S = self.panel.win.skin()
            cr.set_source_rgb(*S["bg"][:3])
            cr.paint()
            r = self.panel.last
            cr.set_font_size(10)
            title = "Diagram"
            pad = 8
            x0, y0, w, h = pad, 18, W - 2 * pad, H - 18 - pad
            cr.set_source_rgba(*S["grid"][:3], 0.25)
            cr.rectangle(x0 + 0.5, y0 + 0.5, w, h)
            cr.set_line_width(1)
            cr.stroke()
            only = None if self.sel == "Automatiskt" else self.sel
            if not r or r.get("fam") in (None, "BRUS"):
                if only:
                    title = f"{only} – väntar på signal"
                cr.set_source_rgba(*S["text"])
                cr.move_to(pad, 12)
                cr.show_text(title)
                return
            col = S["line"][:3]
            fd = r.get("bild")
            ras = r.get("ram_raster")
            has_k = r.get("konst") is not None and only in (None, "Konstellation")
            has_r = ras is not None and len(ras) > 0 and only in (None, "Ramraster")
            if only and not self._har(r, only):
                title = f"{only} – finns inte för den här signalen"
            if (only in (None, "Faxbild")) and fd is not None and fd.bild is not None and len(fd.bild):
                title = f"Faxbild ({len(fd.rows)} rader)"
                img = fd.bild[-int(h * fd.W / max(1, w)):] if len(fd.bild) else fd.bild
                surf = gray_surface(img)
                cr.save()
                cr.rectangle(x0, y0, w, h)
                cr.clip()
                sc = w / fd.W
                cr.translate(x0, y0 + h - len(img) * sc)
                cr.scale(sc, sc)
                cr.set_source_surface(surf, 0, 0)
                cr.paint()
                cr.restore()
            elif has_k or has_r:
                if has_k and has_r:
                    title = f"Konstellation · Ramraster ({ras.shape[1]} sym × {ras.shape[0]} ramar)"
                    kw = w * 0.42
                    rx0, rw = x0 + w * 0.45, w * 0.55
                elif has_r:
                    title = f"Ramraster ({r.get('ram_ms', 0):.1f} ms, {ras.shape[0]} ramar)"
                    kw = 0
                    rx0, rw = x0, w
                else:
                    title = f"Konstellation ({r['klass'].split(' ')[0]})"
                    kw = w
                    rx0, rw = 0, 0
                if has_k:
                    s = np.asarray(r["konst"])
                    cx, cy, rad = x0 + kw / 2, y0 + h / 2, min(kw, h) / 2 * 0.62
                    cr.set_source_rgba(*S["grid"][:3], 0.35)
                    cr.arc(cx, cy, rad, 0, 2 * math.pi)
                    cr.stroke()
                    cr.move_to(x0, cy)
                    cr.line_to(x0 + kw, cy)
                    cr.move_to(cx, y0)
                    cr.line_to(cx, y0 + h)
                    cr.stroke()
                    cr.set_source_rgba(*col, 0.75)
                    for p in s:
                        cr.rectangle(cx + p.real * rad - 1, cy - p.imag * rad - 1, 2.2, 2.2)
                    cr.fill()
                if has_r:
                    surf = gray_surface(ras)
                    cr.save()
                    cr.rectangle(rx0, y0, rw, h)
                    cr.clip()
                    cr.translate(rx0, y0)
                    cr.scale(rw / max(1, ras.shape[1]), h / max(1, ras.shape[0]))
                    cr.set_source_surface(surf, 0, 0)
                    cr.get_source().set_filter(cairo.FILTER_NEAREST)
                    cr.paint()
                    cr.restore()
                    cr.set_source_rgba(*S["grid"][:3], 0.5)
                    cr.rectangle(rx0 + 0.5, y0 + 0.5, rw - 1, h - 1)
                    cr.set_line_width(1)
                    cr.stroke()
            elif only in (None, "Tonspektrum") and r.get("fam") == "OFDM" and r.get("spec_f") is not None:
                title = f"Tonspektrum ({r.get('toner') or '?'} toner)"
                f, d = np.asarray(r["spec_f"]), np.asarray(r["spec_db"])
                self._curve(cr, f, d, x0, y0, w, h, col)
                lo, hi = f[0], f[-1]
                cr.set_source_rgba(*S["track"][:3], 0.9)
                for t in r.get("spec_toner", []):
                    xx = x0 + (t - lo) / max(1e-9, hi - lo) * w
                    cr.move_to(xx, y0 + h)
                    cr.line_to(xx, y0 + h - 6)
                cr.stroke()
            elif only in (None, "Envelop") and r.get("kurva") is not None:
                title = "Envelop (senaste ~sekunden)"
                k = np.asarray(r["kurva"], float)
                self._curve(cr, np.arange(len(k)), np.clip(k, 0, 1.4), x0, y0, w, h, col, fill=False, ymin=0)
            elif only in (None, "Frekvensfördelning") and r.get("hist") is not None:
                title = "Frekvensfördelning"
                c, hs = r["hist"]
                self._curve(cr, np.asarray(c), np.asarray(hs, float), x0, y0, w, h, col, ymin=0)
                cr.set_source_rgba(*S["track"][:3], 0.95)
                lo, hi = c[0], c[-1]
                for t in r.get("fi_peaks", []):
                    xx = x0 + (t - lo) / max(1e-9, hi - lo) * w
                    cr.move_to(xx, y0)
                    cr.line_to(xx, y0 + 6)
                cr.stroke()
                span_hz = hi - lo
                step = 1000.0 if span_hz >= 2000 else 100.0
                cr.set_source_rgba(*S["text"])
                for k in range(int(np.ceil(lo / step)), int(np.floor(hi / step)) + 1):
                    xx = x0 + (k * step - lo) / max(1e-9, span_hz) * w
                    cr.move_to(xx, y0 + h - 10)
                    cr.line_to(xx, y0 + h)
                    cr.stroke()
                    cr.move_to(xx + 2, y0 + h - 2)
                    cr.show_text(str(k) if step == 1000.0 else str(int(k * step)))
                cr.move_to(x0 + w - 24, y0 + h - 2)
                cr.show_text("kHz" if step == 1000.0 else "Hz")
            cr.set_source_rgba(*S["text"])
            cr.move_to(pad, 12)
            cr.show_text(title)

        @staticmethod
        def _curve(cr, x, y, x0, y0, w, h, col, fill=True, ymin=None):
            if len(x) < 2:
                return
            lo = float(np.min(y)) if ymin is None else ymin
            hi = float(np.max(y))
            rng = max(1e-9, hi - lo)
            xs = x0 + (x - x[0]) / max(1e-9, x[-1] - x[0]) * w
            ys = y0 + h - (y - lo) / rng * (h - 4)
            cr.move_to(xs[0], ys[0])
            for a, b in zip(xs[1:], ys[1:]):
                cr.line_to(a, b)
            if fill:
                cr.line_to(xs[-1], y0 + h)
                cr.line_to(xs[0], y0 + h)
                cr.close_path()
                cr.set_source_rgba(*col, 0.25)
                cr.fill_preserve()
            cr.set_source_rgba(*col, 0.95)
            cr.set_line_width(1.2)
            cr.stroke()

    _GRAY = (0xFF000000 | (np.arange(256, dtype=np.uint32) * 0x010101)).astype(np.uint32)

    def gray_surface(img):
        img = np.ascontiguousarray(img)
        rgba = np.ascontiguousarray(_GRAY[img])
        h, w = img.shape
        return cairo.ImageSurface.create_for_data(memoryview(rgba), cairo.FORMAT_ARGB32, w, h, w * 4)

    class FaxWindow(Adw.Window):
        """Hela faxbilden med spara, ny bild och manuell justering av radstarten."""

        def __init__(self, panel):
            super().__init__(title="Väderfax – Signalspanaren", transient_for=panel.win,
                             default_width=820, default_height=900)
            self.panel = panel
            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar()
            for label, tip, cb in (("💾 Spara PNG", "Spara bilden i inspelningsmappen", self.save),
                                   ("Ny bild", "Börja om med en tom bild", self.new),
                                   ("◀", "Flytta radstarten åt vänster", lambda: self.shift(-12)),
                                   ("▶", "Flytta radstarten åt höger", lambda: self.shift(12))):
                b = Gtk.Button(label=label, tooltip_text=tip)
                b.connect("clicked", lambda *_, cb=cb: cb())
                hb.pack_start(b)
            tv.add_top_bar(hb)
            self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
            self.area.set_draw_func(self.on_draw)
            sw = Gtk.ScrolledWindow()
            sw.set_child(self.area)
            self.sw = sw
            tv.set_content(sw)
            self.set_content(tv)
            self.connect("close-request", lambda *_: self.set_visible(False) or True)

        def dec(self):
            return self.panel.fax_dec

        def on_draw(self, area, cr, W, H, *_):
            cr.set_source_rgb(0.1, 0.1, 0.1)
            cr.paint()
            d = self.dec()
            if d is None or d.bild is None or not len(d.bild):
                return
            sc = W / d.W
            need = int(len(d.bild) * sc) + 1
            if abs(area.get_content_height() - need) > 2:
                area.set_content_height(need)
            cr.scale(sc, sc)
            cr.set_source_surface(gray_surface(d.bild), 0, 0)
            cr.paint()

        def save(self):
            d = self.dec()
            if d is None or d.bild is None:
                return
            path = os.path.join(rec_dir(), f"fax_{datetime.datetime.now():%Y%m%d-%H%M%S}.png")
            try:
                gray_surface(d.bild).write_to_png(path)
                self.panel.win.toast(f"Faxbilden sparad: {path}")
                log.info("Faxbild sparad: %s", path)
            except Exception as ex:
                self.panel.win.toast(f"Kunde inte spara: {ex}")

        def new(self):
            d = self.dec()
            if d is not None:
                d._new_image()
                self.area.queue_draw()

        def shift(self, px):
            d = self.dec()
            if d is not None:
                d.offset = (d.offset + px) % d.W
                if d.rows:
                    d.bild = np.roll(np.asarray(d.rows[-1600:]), -d.offset, axis=1)
                self.area.queue_draw()

    class AnalysPanel(Gtk.Box):
        # Fasta rader i fast ordning – de visas alltid (med "–" när värdet saknas), så att rutan inte
        # byter storlek eller disposition när analysen tolkar signalen olika från steg till steg.
        ROWS = [("freq", "Frekvens (mitt)"), ("avv", "Avvikelse från mål"),
                ("snr", "SNR"), ("occ", "Upptagen bandbredd"), ("baud", "Symboltakt"), ("ram", "Ramperiod"),
                ("shift", "Shift / tonavstånd"), ("toner", "Antal toner"), ("ljud", "Ljudfrekvens"),
                ("mod", "Modulation"), ("skur", "Sändningsmönster"), ("env", "Envelopvariation"),
                ("trafik", "Trafik"), ("avk1", "Avkodare"), ("avk2", "Avkodare, mer"), ("grund", "Grund")]
        TITEL_BREDD = max(len(t) for _k, t in ROWS)

        def __init__(self, win):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.win = win
            self.an = win.analyzer
            self.last = None
            self._rid = -1
            self._kand_shown = None
            self._kand_hist = []
            self.text_t = 0
            self.set_margin_top(6)
            self.set_margin_start(6)
            self.set_margin_end(6)
            self.set_margin_bottom(6)

            tb = Gtk.Box(spacing=6)
            self.tb = tb
            self.title_row = Gtk.Box()           # rubriken flyttas hit när panelen är för smal för en rad
            self.title_row.set_visible(False)
            self.append(self.title_row)
            self.title = Gtk.Label(xalign=0, hexpand=True)
            self.title.set_markup("<b>Signalanalys</b> – klicka på en bärvåg i vattenfallet")
            self.title.set_wrap(True)
            self.title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.title.set_lines(2)
            self.title.set_ellipsize(Pango.EllipsizeMode.END)
            tb.append(self.title)
            self.bw_dd = Gtk.DropDown.new_from_strings(ANALYS_BW_NAMES)
            self.bw_dd.set_selected(int(win.conf.get("analys_bw_idx", 0)) % len(ANALYS_BW))
            self.bw_dd.set_tooltip_text("Analysbandbredd")
            self.bw_dd.connect("notify::selected", self._on_bw)
            tb.append(self.bw_dd)
            self.dec_names = [k.namn for k in analys.AVKODARE]
            self.dec_dd = Gtk.DropDown.new_from_strings(["Avkodare: automatiskt"] + self.dec_names + ["Ingen avkodare"])
            self.dec_dd.set_tooltip_text("Välj avkodare – automatiskt efter signaltyp, eller tvinga fram en viss")
            self.dec_dd.connect("notify::selected", self._on_dec)
            tb.append(self.dec_dd)
            self.fax_btn = Gtk.Button(label="🖼 Faxbild")
            self.fax_btn.set_tooltip_text("Visa den mottagna faxbilden i ett eget fönster")
            self.fax_btn.connect("clicked", lambda *_: self.open_fax())
            self.fax_btn.set_visible(False)
            self.fax_win = None
            self.fax_dec = None
            tb.append(self.fax_btn)
            self.follow = Gtk.ToggleButton(label="Följ inställd")
            self.follow.set_tooltip_text("Analysera alltid den kanal som demodulatorn är inställd på")
            self.follow.set_active(bool(win.conf.get("analys_follow", False)))
            self.follow.connect("toggled", self._on_follow)
            tb.append(self.follow)
            b = Gtk.Button(label="🎧 Lyssna")
            b.set_tooltip_text("Ställ in demodulatorn på den analyserade signalen")
            b.connect("clicked", lambda *_: self.listen())
            tb.append(b)
            b = Gtk.Button(icon_name="edit-clear-symbolic")
            b.set_tooltip_text("Rensa klartext")
            b.connect("clicked", lambda *_: self.textbuf.set_text(""))
            tb.append(b)
            b = Gtk.Button(icon_name="edit-copy-symbolic")
            b.set_tooltip_text("Kopiera mätvärden och klartext")
            b.connect("clicked", lambda *_: self.copy())
            tb.append(b)
            self.detach_btn = Gtk.Button(icon_name="window-new-symbolic")
            self.detach_btn.set_tooltip_text("Visa analysen i ett eget fönster")
            self.detach_btn.connect("clicked", lambda *_: win.toggle_analys_window())
            tb.append(self.detach_btn)
            self.append(tb)

            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, wide_handle=True)
            paned.add_css_class("delare")
            # vänster: litet vattenfall överst, mätvärden under
            left = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, wide_handle=True)
            left.add_css_class("delare")
            self.mini = MiniWaterfall(self)
            fr = Gtk.Frame()
            fr.set_child(self.mini)
            left.set_start_child(fr)
            left.set_resize_start_child(True)
            left.set_shrink_start_child(False)
            grid = Gtk.Grid(column_spacing=14, row_spacing=2)
            grid.add_css_class("analys-ram")
            self.cells = {}
            self.grid = grid
            self._cols = 0
            for key, title in self.ROWS:
                # fast bredd på både rubrik och värde: kolumnerna flyttar sig inte när texten ändras
                l1 = Gtk.Label(label=title, xalign=0, width_chars=self.TITEL_BREDD)
                l1.add_css_class("dim-label")
                l2 = Gtk.Label(label="–", xalign=0, hexpand=True, selectable=True, width_chars=12,
                               max_width_chars=12, ellipsize=Pango.EllipsizeMode.END)
                l2.add_css_class("monospace")
                self.cells[key] = (l1, l2)
            self._layout_cells(2)
            # signaltypen på en egen rad över mätvärdena – alltid en rad, hela texten i tooltip
            self.klass_lbl = Gtk.Label(label="–", xalign=0, selectable=True, ellipsize=Pango.EllipsizeMode.END)
            self.klass_lbl.add_css_class("analys-klass")
            self.klass_lbl.set_margin_bottom(4)
            sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
            sw.set_overlay_scrolling(False)      # synlig rullist när alla mätvärden inte ryms
            sw.set_size_request(-1, 60)
            sw.set_child(grid)
            grid.remove_css_class("analys-ram")
            cbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            cbox.add_css_class("analys-ram")
            cbox.append(self.klass_lbl)
            cbox.append(sw)
            self.cells_sw = sw
            self.cells_box = cbox
            left.set_end_child(cbox)
            left.set_resize_end_child(False)
            left.set_shrink_end_child(False)
            left.set_position(160)
            self.left_paned = left
            self._left_user = False
            g = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            g.connect("pressed", self._on_left_press)
            left.add_controller(g)
            left.connect("notify::max-position", lambda *_: GLib.idle_add(self._fit_cells))
            paned.set_start_child(left)
            paned.set_resize_start_child(True)
            paned.set_shrink_start_child(False)

            # höger: diagram, möjliga signaltyper och klartext
            right = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, wide_handle=True)
            right.add_css_class("delare")
            self.diag = AnalysDiagram(self)
            self.diag.set_vexpand(True)
            dbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            dhdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            dhdr.set_margin_start(6)
            dhdr.set_margin_end(6)
            dhdr.set_margin_top(4)
            dhdr.append(Gtk.Label(label="Diagram", xalign=0, hexpand=True, css_classes=["dim-label"]))
            self.diag_dd = Gtk.DropDown.new_from_strings(list(AnalysDiagram.NAMN))
            self.diag_dd.set_tooltip_text("Välj diagram – Automatiskt växlar efter signaltyp")
            vald = win.conf.get("analys_diagram", "Frekvensfördelning")
            if vald not in AnalysDiagram.NAMN:
                vald = "Frekvensfördelning"
            self.diag.sel = vald
            self.diag_dd.set_selected(AnalysDiagram.NAMN.index(vald))
            self.diag_dd.connect("notify::selected", self._on_diag)
            dhdr.append(self.diag_dd)
            dbox.append(dhdr)
            dbox.append(self.diag)
            fr = Gtk.Frame()
            fr.set_child(dbox)
            right.set_start_child(fr)
            right.set_shrink_start_child(False)
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            typbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.kand = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True, lines=4,
                                  ellipsize=Pango.EllipsizeMode.END)
            self.kand.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.kand.add_css_class("analys-ram")
            self.kand.set_markup("<b>Signaltyp</b>\n–")
            typbox.append(self.kand)
            self.klassa_btn = Gtk.Button(label="✎ Redigera …")
            self.klassa_btn.set_tooltip_text("Spara din egen bedömning som en flagga på frekvensen – den visas sedan "
                                             "som signaltyp i stället för analysens gissning")
            self.klassa_btn.add_css_class("flat")
            self.klassa_btn.connect("clicked", lambda *_: self._klassa_sjalv())
            self.klassa_btn.set_sensitive(False)
            self.alt_dd = Gtk.DropDown.new_from_strings(["Automatiskt (analysen)"])
            self.alt_dd.set_tooltip_text("Välj signaltyp – Automatiskt låter analysen bestämma")
            self.alt_dd.set_hexpand(True)
            self.alt_dd.set_sensitive(False)
            self.alt_dd.connect("notify::selected", self._on_typ)
            valrad = Gtk.Box(spacing=4)          # rullista och Redigera på samma rad, alltid synliga
            valrad.append(self.alt_dd)
            valrad.append(self.klassa_btn)
            typbox.append(valrad)
            self._typ_key, self._typ_items, self._typ_busy = None, [], False
            sw_typ = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=110)
            sw_typ.set_overlay_scrolling(False)
            sw_typ.set_child(typbox)
            info.append(sw_typ)
            kh = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            kh.append(Gtk.Label(label="Klartext från avkodaren", xalign=0, hexpand=True, css_classes=["dim-label"]))
            self._logg_fil = None
            self.logg_btn = Gtk.ToggleButton(label="⏺ Logga")
            self.logg_btn.add_css_class("flat")
            self.logg_btn.set_tooltip_text("Logga ny avkodad text till en fil fortlöpande")
            self.logg_btn.connect("toggled", self._on_logg)
            kh.append(self.logg_btn)
            spar = Gtk.Button(label="💾 Spara")
            spar.add_css_class("flat")
            spar.set_tooltip_text("Spara hela klartextrutan (inklusive symboler) till en fil")
            spar.connect("clicked", lambda *_: self._spara_klartext())
            kh.append(spar)
            self._fraga_btn = Gtk.Button()
            self._fraga_btn.add_css_class("flat")
            self._fraga_btn.connect("clicked", lambda *_: self._open_chat())
            self._ai_knapp_uppdatera()
            kh.append(self._fraga_btn)
            info.append(kh)
            self.textbuf = Gtk.TextBuffer()
            self.sym_mark = self.textbuf.create_mark("symboler", self.textbuf.get_end_iter(), False)
            tv = Gtk.TextView(buffer=self.textbuf, editable=False, wrap_mode=Gtk.WrapMode.CHAR, monospace=True)
            tv.add_css_class("analys-text")
            tv.set_left_margin(6)
            tv.set_top_margin(4)
            self.textview = tv
            sw2 = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
            sw2.set_size_request(-1, 50)
            sw2.set_child(tv)
            self.info_sw = sw2                   # bara klartexten rullas med, inte signaltypen
            info.append(sw2)
            right.set_end_child(info)
            right.set_shrink_end_child(False)
            right.set_position(150)
            right.set_size_request(300, -1)
            paned.set_end_child(right)
            paned.set_resize_end_child(False)
            paned.set_shrink_end_child(False)
            self.append(paned)
            self._update_rows(None)

        def _place_title(self):
            """Rubriken bredvid knapparna om den ryms helt, annars på en egen rad ovanför."""
            pw = self.get_width()
            key = (pw, self.title.get_label(), self.fax_btn.get_visible())
            if pw <= 0 or key == getattr(self, "_title_key", None):
                return
            self._title_key = key
            knappar, c = 0, self.tb.get_first_child()
            while c is not None:
                if c is not self.title and c.get_visible():
                    knappar += c.measure(Gtk.Orientation.HORIZONTAL, -1)[1] + 6
                c = c.get_next_sibling()
            lay = self.title.create_pango_layout(None)
            lay.set_markup(self.title.get_label(), -1)
            behov = lay.get_pixel_size()[0] + 4
            smal = knappar + behov > pw - 12
            par = self.title.get_parent()
            if smal and par is self.tb:
                self.tb.remove(self.title)
                self.title_row.append(self.title)
                self.title_row.set_visible(True)
            elif not smal and par is self.title_row and knappar + behov < pw - 40:   # lite marginal mot fladder
                self.title_row.remove(self.title)
                self.title_row.set_visible(False)
                self.tb.prepend(self.title)

        def _on_left_press(self, _g, _n, _x, y):
            if abs(y - self.left_paned.get_position() - 3) <= 8:
                self._left_user = True          # användaren tar tag i delaren – rör den inte sen

        def _fit_cells(self):
            """Ge mätvärdena den höjd de behöver (så långt det går); minivattenfallet får resten."""
            lp = self.left_paned
            mx = lp.get_property("max-position")
            if self._left_user or mx <= 0 or lp.get_width() < 50:
                return False
            _m, nat = self.klass_lbl.measure(Gtk.Orientation.VERTICAL, lp.get_width())[:2]
            nat += self.cells_sw.get_child().measure(Gtk.Orientation.VERTICAL, lp.get_width())[1] + 18
            pos = max(90, mx - nat - 2)
            if pos != lp.get_position():
                lp.set_position(pos)
            return False

        def _layout_cells(self, cols):
            """Två kolumner med mätvärden när det finns plats, annars en."""
            if cols == self._cols:
                return
            self._cols = cols
            for l1, l2 in self.cells.values():
                if l1.get_parent() is not None:
                    self.grid.remove(l1)
                    self.grid.remove(l2)
            for i, (key, _t) in enumerate(self.ROWS):
                l1, l2 = self.cells[key]
                c, r = (i % cols) * 2, i // cols
                self.grid.attach(l1, c, r, 1, 1)
                self.grid.attach(l2, c + 1, r, 1, 1)

        # ── mål ──
        def empty_msg(self):
            st = self.an.tap.status
            if self.an.tap.target is None:
                return "Klicka på en bärvåg i det stora vattenfallet"
            if not self.win.engine.running:
                return "Starta mottagningen"
            if st != "ok":
                return f"Målet ligger {st}"
            return "Väntar på data …"

        def chosen_bw(self, auto_bw):
            bw = ANALYS_BW[self.bw_dd.get_selected()] if self.bw_dd.get_selected() < len(ANALYS_BW) else None
            if bw is None:
                bw = float(np.clip(auto_bw * 1.35, 400, 250e3))
            return bw

        def _klartext_rensa(self):
            """Tömmer klartextrutan och den köade texten när målet byts till en annan signal."""
            self.textbuf.set_text("")
            self.an.text_q.clear()

        def set_target(self, f, auto_bw, fit=True):
            """fit=False: använd bredden som den är (t.ex. demodulatorns kanal i läget Följ inställd)."""
            auto = fit and self.bw_dd.get_selected() == 0
            bw = self.chosen_bw(auto_bw)
            self.auto_bw = auto_bw
            forra = getattr(self, "_klar_f", None)
            self.an.set_target(f, auto_bw if auto else bw, auto=auto)
            if forra is None or abs(f - forra) > max(500.0, bw / 2):
                self._klartext_rensa()
            self._klar_f = f
            self._title_tap = None
            self._kand_shown = None
            self._kand_hist.clear()
            self._show_symbols(None)
            self.fax_dec = None
            self.fax_btn.set_visible(False)
            self.last = None
            self._rid = -1
            self.mini.clear()
            self.mini.zoom = 1.0
            self._show_title()
            self._update_rows(None)
            self.diag.queue_draw()
            log.info("Signalanalys: %.6f MHz, bandbredd %.0f Hz", f / 1e6, bw)

        def _show_title(self):
            """Rubriken följer kanalen – den ändras när den automatiska bandbredden är uppmätt."""
            tap, ft = self.an.tap, self.an.fit
            key = (tap.target, tap.bw, ft["state"] if ft else None)
            if key == getattr(self, "_title_tap", None) or tap.target is None:
                return
            self._title_tap = key
            f = tap.target
            if ft and ft["state"] in ("prelim", "survey"):
                bwtxt = "<span alpha='60%'>mäter bandbredd …</span>"
            elif ft and ft.get("width"):
                bwtxt = f"{_fmt_hz(ft['width'])} <span alpha='60%'>(uppmätt)</span>"
            else:
                bwtxt = _fmt_hz(tap.bw)
            self.title.set_markup(f"<b>Analys:</b> {fmt_mhz(f)} MHz  ·  {bwtxt}  "
                                  f"<span alpha='60%'>{GLib.markup_escape_text(band_name(f) or '')}</span>")
            self.title.set_tooltip_markup(self.title.get_label())

        def retarget_offset(self, off):
            t = self.an.tap.target
            if t is None:
                return
            f = t + off
            snap = self.win.carrier_at(f, tol=max(200, (self.an.tap.bw or 3000) * 0.08))
            self.set_target(snap[0] if snap else f, snap[1] if snap else getattr(self, "auto_bw", self.an.tap.bw))

        def _on_bw(self, *_):
            self.win.conf["analys_bw_idx"] = int(self.bw_dd.get_selected())
            t = self.an.tap.target
            if t is not None:
                self.set_target(t, getattr(self, "auto_bw", self.an.tap.bw or 3000))

        def _on_dec(self, *_):
            i = int(self.dec_dd.get_selected())
            if i == 0:
                self.an.tvinga = None
            elif i <= len(self.dec_names):
                self.an.tvinga = self.dec_names[i - 1]
            else:
                self.an.tvinga = ""          # ingen avkodare körs
            log.info("Signalanalys: avkodare %s", self.an.tvinga if self.an.tvinga is not None else "automatiskt")

        def _on_diag(self, dd, *_):
            name = AnalysDiagram.NAMN[int(dd.get_selected())]
            self.diag.sel = name
            self.win.conf["analys_diagram"] = name
            self.diag.queue_draw()

        def open_fax(self):
            if self.fax_dec is None:
                return
            if self.fax_win is None:
                self.fax_win = FaxWindow(self)
            self.fax_win.present()

        def _on_follow(self, b):
            self.win.conf["analys_follow"] = b.get_active()
            if b.get_active():
                self._follow_key = None
                self.follow_tuned()

        def follow_tuned(self):
            e = self.win.engine
            lo, hi = e._chan_bounds(e.mode, e.bw, self.win.freq)
            f = (lo + hi) / 2
            key = (round(f), round(self.chosen_bw(hi - lo)))
            if self.an.tap.target is None or key != getattr(self, "_follow_key", None):
                self._follow_key = key
                if f < 30e6 and hi - lo > 15e3:
                    # bred kanal på kortvåg (t.ex. kvarglömt WFM): mät signalen i stället för 180 kHz brus
                    self.set_target(self.win.freq, 3000.0)
                else:
                    self.set_target(f, hi - lo, fit=False)

        def open_on_tuned(self):
            if self.an.tap.target is None:
                self.follow_tuned()

        def listen(self):
            r = self.last
            f = (r or {}).get("f_abs") or self.an.tap.target
            if f is None:
                return
            m = self.win.engine.mode
            vis = (r or {}).get("visad")
            fam = {"FT8": "MFSK", "FT4": "FSK4"}.get(vis["fam"], vis["fam"]) if vis else (r or {}).get("fam")
            if fam in ("AM",) and m not in ("AM",):
                m = "AM"
            elif fam == "FM" and m not in ("NFM", "WFM"):
                m = "NFM" if (r.get("occ_bw") or 0) < 40e3 else "WFM"
            elif fam in ("FSK2", "MFSK", "OFDM", "PSK8", "QPSK", "BPSK", "LIN", "SSB", "MSK", "FSK4") \
                    and f < 30e6 and m not in ("USB", "LSB"):
                m = "USB"
            elif fam == "OOK" and m not in ("CW", "USB", "LSB"):
                m = "CW"
            if m == "USB":
                ac = (r or {}).get("ljud_mitt") or 1500
                self.win.tune(f - ac, m)
            elif m == "LSB":
                self.win.tune(f + 1500, m)
            else:
                self.win.tune(f, m)

        # ── uppdatering ──
        def tick(self):
            an = self.an
            got = False
            while an.tap.lines:
                try:
                    db, fs, tgt, bw = an.tap.lines.popleft()
                except IndexError:
                    break
                self.mini.push(db, fs, bw)
                got = True
            if got or an.tap.target is None:
                self.mini.queue_draw()
            gw = self.grid.get_width()
            if gw > 0:
                self._layout_cells(2 if gw >= 620 else 1)
            self._show_title()
            self._place_title()
            self._fit_cells()
            if self.follow.get_active():
                self.follow_tuned()
            an.dial, an.mode = self.win.freq, self.win.engine.mode
            if an.result_id != self._rid:
                self._rid = an.result_id
                r = an.result
                if r is not None and r.get("target") == an.tap.target:
                    self.last = r
                    self._update_rows(r)
                    self._show_symbols(r)
                    self.diag.queue_draw()
                    fd = r.get("bild")
                    if fd is not None:
                        self.fax_dec = fd
                        self.fax_btn.set_visible(True)
                        if self.fax_win is not None and self.fax_win.get_visible():
                            self.fax_win.area.queue_draw()
            while an.msg_q:
                try:
                    m = an.msg_q.popleft()
                except IndexError:
                    break
                try:
                    self.win._on_decoded(m)
                except Exception:
                    log.exception("Kunde inte visa avkodat meddelande")
            while an.text_q:
                try:
                    name, txt = an.text_q.popleft()
                except IndexError:
                    break
                self.textbuf.insert(self.textbuf.get_iter_at_mark(self.sym_mark), txt)
                if self._logg_fil is not None:
                    self._logg_fil.write(f"{time.strftime('%H:%M:%S')} [{name}] {txt}\n")
                    self._logg_fil.flush()
                self.text_t = time.time()
                if self.textbuf.get_char_count() > 20000:
                    s_ = self.textbuf.get_start_iter()
                    e_ = self.textbuf.get_iter_at_offset(5000)
                    self.textbuf.delete(s_, e_)
                adj = self.info_sw.get_vadjustment()
                GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)

        def _set(self, key, val):
            """Raden står alltid kvar; saknat värde visas som ett dämpat streck."""
            _l1, l2 = self.cells[key]
            txt = str(val) if val not in (None, "", "–") else "–"
            if l2.get_text() != txt:
                l2.set_text(txt)
                l2.set_tooltip_text(txt if txt != "–" else None)
            if txt == "–":
                l2.add_css_class("dim-label")
            else:
                l2.remove_css_class("dim-label")

        def _set_klass(self, txt):
            if self.klass_lbl.get_text() != txt:
                self.klass_lbl.set_text(txt)
                self.klass_lbl.set_tooltip_text(txt)

        def traffic(self, r):
            fam = r.get("fam")
            if time.time() - self.text_t < 15:
                return "Klartext avkodas →"
            if fam in ("AM", "FM", "SSB"):
                return ("Analogt tal/ljud – okrypterat, tryck Lyssna" if r.get("ljudvariation", 0) > 2.5
                        else "Analog – ingen tydlig talaktivitet just nu")
            if fam == "OOK":
                return "Morse – klartext om avkodningen ger läsbar text"
            if fam == "BÄRVÅG":
                return "Ingen information (ren bärvåg)"
            k = r.get("kandidater") or []
            if k:
                info = k[0][1].get("info", "")
                if "rypter" in info:
                    return "Digital – ofta krypterad; kan identifieras men inte läsas"
                if "lartext" in info:
                    return "Digital – klartextprotokoll (avkodare för typen saknas än)"
            return "Digital – innehåll okänt" if fam not in ("BRUS", None) else None

        FAM_TILL_TYP = {"SSB": "Tal", "AM": "Rundradio", "FM": "Tal", "OOK": "Amatör", "BÄRVÅG": "Övrigt"}

        def _operator_flagga(self, r):
            """En egen flagga på signalens frekvens (operatörens klassning) eller None."""
            fl = getattr(self.win, "flaggor", None)
            if fl is None or not r or not r.get("f_abs"):
                return None
            tol = max(300.0, (r.get("occ_bw") or 0) / 2)
            nara = [x for x in fl.egna if x.get("kalla") != "dold" and abs(x["f"] - r["f_abs"]) <= tol]
            return min(nara, key=lambda x: abs(x["f"] - r["f_abs"])) if nara else None

        def _klassa_sjalv(self):
            r = getattr(self, "last", None) or {}
            f = r.get("f_abs") or self.win.freq
            op = self._operator_flagga(r)
            shown = getattr(self, "_kand_shown", None)
            namn = (op or {}).get("namn") or (shown or {}).get("namn") or ""
            typ = ((op or {}).get("typ") or ((shown or {}).get("e") or {}).get("kat")
                   or self.FAM_TILL_TYP.get(r.get("fam"), "Data"))
            not_ = (op or {}).get("not") or f"analys: {r.get('klass', '')}, {r.get('occ_bw') or 0:.0f} Hz"
            self.win.flagg_dialog(op, f, forslag={"namn": namn, "typ": typ, "lage": self.win.engine.mode, "not": not_})

        def _on_typ(self, dd, *_):
            if self._typ_busy:
                return
            fl = getattr(self.win, "flaggor", None)
            if fl is None:
                return
            r = self.last or {}
            op = self._operator_flagga(r)
            i = int(dd.get_selected())
            if i == 0:
                if op is not None and op.get("kalla") == "signaltyp":
                    fl.ta_bort(op["id"])
                elif op is not None:
                    self.win.toast("Klassningen är gjord med ✎ Redigera – ändra den där")
                self._kand_shown = None
                self._kand_hist.clear()
                return
            if not 0 < i <= len(self._typ_items):
                return
            namn, e = self._typ_items[i - 1]
            typ = (e or {}).get("kat", "")
            if op is not None:
                fl.andra(op["id"], namn=namn, typ=typ)
            else:
                fl.lagg_till(r.get("f_abs") or self.win.freq, namn, lage=self.win.engine.mode, typ=typ,
                             noter="Vald i signaltypen", kalla="signaltyp")
            self._kand_shown = {"namn": namn, "e": e or {"namn": namn, "kat": typ}, "sc": None, "kalla": "vald"}
            self._kand_hist.clear()

        def _typ_visa(self, r, op, shown):
            """Rullistan för signaltypen: Automatiskt, låst typ, operatörens val, kandidater och alla kända typer."""
            visa = (bool(r) and self._efam(r) not in (None, "BRUS")) or op is not None
            self._typ_busy = True
            try:
                if not visa:
                    if self._typ_key is not None:
                        self.alt_dd.set_model(Gtk.StringList.new(["Automatiskt (analysen)"]))
                        self._typ_items = []
                    self.alt_dd.set_selected(0)
                    self.alt_dd.set_sensitive(False)
                    self._typ_key = None
                    return
                key = (shown["namn"] if shown else None, op.get("namn") if op else None)
                if self._typ_key != key:
                    self._typ_key = key
                    lista = []
                    if shown is not None:
                        lista.append((shown["namn"], shown["e"]))
                    if op is not None:
                        lista.append((op.get("namn"), next((x for x in analys.signaldb.SIGNALER
                                                             if x.get("namn") == op.get("namn")), None)))
                    for _s, e, _w in (r.get("kandidater") or []):
                        lista.append((e.get("namn"), e))
                    for e in analys.signaldb.SIGNALER:
                        lista.append((e.get("namn"), e))
                    unika, sedda = [], set()
                    for n, e in lista:
                        if n and n not in sedda:
                            sedda.add(n)
                            unika.append((n, e))
                    self._typ_items = unika
                    self.alt_dd.set_model(Gtk.StringList.new(["Automatiskt (analysen)"] + [n for n, _e in unika]))
                idx = 0
                if op is not None:
                    idx = next((k + 1 for k, (n, _e) in enumerate(self._typ_items) if n == op.get("namn")), 0)
                self.alt_dd.set_selected(idx)
                self.alt_dd.set_sensitive(True)
            finally:
                self._typ_busy = False

        def _kand_visa(self, r, op):
            """Signaltyp-rutan har en fast grund: analysen låser typen när den varit stabil en stund.
            Sedan ändras rutan bara av dig (rullistan eller Redigera)."""
            esc = GLib.markup_escape_text
            shown = getattr(self, "_kand_shown", None)
            if shown is None and r and self._efam(r) not in (None, "BRUS"):
                hist = self._kand_hist
                lock = r.get("kandidat") if r.get("kandidat_las") else None
                hist.append((lock[1].get("namn"), lock) if lock is not None else None)
                del hist[:-40]
                rader = [x for x in hist if x]
                if len(hist) >= 20 and rader:
                    antal = {}
                    for nm_, _l in rader:
                        antal[nm_] = antal.get(nm_, 0) + 1
                    nm = max(antal, key=antal.get)
                    if antal[nm] >= 0.6 * len(hist):
                        _n, lock_s = next(x for x in reversed(rader) if x[0] == nm)
                        andra = max((s for s, e, _w in (r.get("kandidater") or []) if e.get("namn") != nm), default=0)
                        andel = lock_s[0] / (lock_s[0] + andra) if lock_s[0] > 0 else 0
                        if andel >= 0.8:
                            shown = {"namn": nm, "e": lock_s[1], "sc": 100 * andel, "kalla": "analys"}
                            self._kand_shown = shown
                            hist.clear()
            if op is not None:
                state = "din klassning"
                e_op = next((x for x in analys.signaldb.SIGNALER if x.get("namn") == op.get("namn")), None)
                nm_l = (f"🚩 <b>{esc(op['namn'])}</b>"
                        + (f" <span alpha='70%'>· {esc(op['typ'])}</span>" if op.get("typ") else ""))
                beskr = " – ".join(x for x in (op.get("not") or "", (e_op or {}).get("info", "")) if x)
                info_l = f"<span size='small' alpha='75%'>{esc(beskr)}</span>"
            elif shown is not None:
                e = shown["e"]
                kat = e.get("kat", "")
                colr = {"militär": "#ff7a5c", "myndighet": "#ffb547"}.get(kat, "#7fe0d0")
                state = "vald" if shown.get("kalla") == "vald" else "analys"
                procent = f" <span alpha='70%'>{shown['sc']:.0f} %</span>" if shown.get("sc") is not None else ""
                nm_l = (f"<span foreground='{colr}'>●</span> <b>{esc(shown['namn'])}</b>{procent} "
                        f"<span alpha='70%'>· {esc(kat)}</span>")
                info_l = f"<span size='small' alpha='75%'>{esc(e.get('info', ''))}</span>"
            else:
                state = "söker"
                nm_l = "<span alpha='60%'>–</span>"
                info_l = "<span size='small' alpha='60%'>väntar på en stabil signaltyp – eller välj i rullistan</span>"
            self.kand.set_markup(f"<b>Signaltyp</b> <span alpha='70%'>({state})</span>\n{nm_l}\n{info_l}")
            self.kand.set_tooltip_markup(f"{nm_l}\n{info_l}")
            self._typ_visa(r, op, shown)

        def _efam(self, r):
            """Signalfamiljen som visas: bevismotorns (stabil över tid), annars stegets egen."""
            vis = (r or {}).get("visad")
            if vis:
                return vis["fam"]
            if r and self.an.ny_motor and r.get("fam") not in (None, "BRUS"):
                return "VÄNTAR"                  # motorn har inte bestämt sig än
            return (r or {}).get("fam")

        def _update_rows(self, r):
            self.klassa_btn.set_sensitive(bool(r and self._efam(r) not in (None, "BRUS")))
            op = self._operator_flagga(r)
            if not r or self._efam(r) in (None, "BRUS"):
                for key, _t in self.ROWS:
                    self._set(key, None)
                self._set_klass("Ingen signal" if r else "–")
                if r:
                    self._set("snr", f"{r.get('snr', 0):.1f} dB")
                self._kand_visa(r, op)
                return
            fam = r["fam"]
            vis = r.get("visad")
            if vis:                              # bevismotorn: samlat över tid, byter inte typ för varje steg
                fam, klass, sak, osaker = vis["fam"], vis["klass"], vis["sakerhet"], vis["osaker"]
                if fam == "FT8" or fam == "FT4":
                    fam = "MFSK" if fam == "FT8" else "FSK4"
            elif self.an.ny_motor:
                klass, sak, osaker = None, None, False
            else:
                klass, sak, osaker = r["klass"], r.get("sakerhet"), r.get("osaker")
            if klass is None:
                self._set_klass(f"{op['namn']} (din klassning)" if op is not None else "Analyserar …")
            elif op is not None:
                self._set_klass(f"{op['namn']} (din klassning) – analysen: {klass}")
            elif osaker:
                self._set_klass(f"Osäker – kanske {klass} ({sak * 100:.0f} %)")
            else:
                self._set_klass(klass + (f" ({sak * 100:.0f} %)" if sak is not None else ""))
            if vis:
                namn_ = {"FT8": "FT8", "FT4": "FT4"}
                andra = [f"{namn_.get(h, analys.FAM_NAMN.get(h, h).split(' (')[0])} {q * 100:.0f} %"
                         for h, q in vis.get("topp", []) if h != vis["fam"] and q >= 0.05][:2]
                g = "; ".join(vis.get("grund") or [])
                self._set("grund", g + (f"  ·  annars {', '.join(andra)}" if andra else "") or None)
            else:
                self._set("grund", None)
            self._set("freq", f"{fmt_mhz(r['f_abs'])} MHz")
            self._set("avv", _fmt_hz(r.get("f_mid"), True))
            self._set("snr", f"{r.get('snr', 0):.1f} dB (topp {r.get('peak_snr', 0):.0f})")
            self._set("occ", _fmt_hz(r.get("occ_bw")))
            b = r.get("baud")
            self._set("baud", f"{b:.2f} Bd".replace(".", ",") if b else None)
            sh = r.get("shift")
            self._set("shift", _fmt_hz(sh) if sh else None)
            self._set("toner", r.get("toner") if r.get("toner") else None)
            ac = r.get("ljud_mitt")
            self._set("ljud", f"{ac:.0f} Hz" if ac else None)
            mod = []
            if fam == "FM":
                mod.append(f"dev ±{_fmt_hz(r.get('deviation'))}")
            if fam == "AM":
                mod.append(f"AM-grad {r.get('am_djup', 0) * 100:.0f} %")
            if r.get("ctcss"):
                mod.append(f"subton {r['ctcss']:.1f} Hz".replace(".", ","))
            self._set("mod", ", ".join(mod) or None)
            self._set("skur", f"skurar ({r.get('duty', 0) * 100:.0f} % på)" if r.get("skurar") else "kontinuerlig")
            self._set("env", f"{r.get('cv', 0):.2f}")
            rm = r.get("ram_ms")
            if rm:
                ns = r.get("ram_sym") or 0
                sym = f" = {round(ns)} symboler" if ns and abs(ns - round(ns)) < 0.02 * ns else ""
                self._set("ram", f"{rm:.1f} ms{sym}")
            else:
                self._set("ram", None)
            self._set("trafik", self.traffic(r))
            vals = [f"{t}: {v}" for t, v in (r.get("avk_varden") or [])]
            self._set("avk1", vals[0] if vals else None)
            self._set("avk2", " · ".join(vals[1:]) or None)
            self._kand_visa(r, op)

        def _klartext_mapp(self):
            mapp = os.path.join(STATE_DIR, "klartext")
            os.makedirs(mapp, exist_ok=True)
            return mapp

        def _spara_klartext(self):
            buf = self.textbuf
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            if not text.strip():
                self.win.toast("Klartextrutan är tom")
                return
            sokv = os.path.join(self._klartext_mapp(), f"klartext-{time.strftime('%Y%m%d-%H%M%S')}.txt")
            tg = self.an.tap.target
            with open(sokv, "w", encoding="utf-8") as fh:
                fh.write(f"Signalspanaren klartext {time.strftime('%Y-%m-%d %H:%M:%S')}"
                         + (f", {fmt_mhz(tg)} MHz" if tg else "") + "\n\n")
                fh.write(text)
            self.win.toast(f"Klartext sparad: {sokv}")

        def _on_logg(self, btn):
            if btn.get_active():
                sokv = os.path.join(self._klartext_mapp(), f"klartext-logg-{time.strftime('%Y%m%d-%H%M%S')}.txt")
                self._logg_fil = open(sokv, "a", encoding="utf-8")
                self._logg_fil.write(f"# loggning startad {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                self._logg_fil.flush()
                self.win.toast(f"Loggar klartext till {sokv}")
            else:
                if self._logg_fil is not None:
                    self._logg_fil.close()
                self._logg_fil = None
                self.win.toast("Klartextloggen stoppad")

        def _ai_cfg_sokv(self):
            return os.path.join(CONF_DIR, "ai.json")

        def _ai_knapp_uppdatera(self):
            if ai.las_config(self._ai_cfg_sokv())["aktiv"]:
                self._fraga_btn.set_label("🤖 Fråga AI")
                self._fraga_btn.set_tooltip_text("Chatta med AI:n om den aktuella analysen")
            else:
                self._fraga_btn.set_label("🤖 AI av")
                self._fraga_btn.set_tooltip_text("AI-chatten är avstängd. Klicka för att slå på den under inställningar.")

        def _analys_text(self):
            r = self.last or {}
            buf = self.textbuf
            klar = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            txt = ai.sammanfattning(r, klar)
            disp = getattr(self.win.view, "disp", None)
            if r and disp is not None and r.get("f_abs"):
                binhz = self.win.view.wf_span / FFT_N
                k = int((r["f_abs"] - self.win.wf_center) / binhz + FFT_N / 2)
                lo, hi = max(0, k - 200), min(FFT_N, k + 200)
                if hi > lo:
                    steg = np.linspace(lo, hi - 1, 64).astype(int)
                    txt += "\nSpektrum (dB, 64 punkter runt signalen): " + " ".join(f"{disp[i]:.0f}" for i in steg)
            if r.get("hist") is not None:
                c, hs = r["hist"]
                txt += "\nFrekvensfördelning (Hz:antal): " + " ".join(f"{x:.0f}:{y:.0f}" for x, y in list(zip(c, hs))[::4])
            if r.get("kurva") is not None:
                kv = np.asarray(r["kurva"], float)
                txt += "\nEnvelop (senaste): " + " ".join(f"{v:.2f}" for v in kv[::max(1, len(kv) // 40)][-40:])
            return txt

        def _open_chat(self):
            if not ai.las_config(self._ai_cfg_sokv())["aktiv"]:
                self._open_ai_settings()
                return
            if getattr(self, "_chat_win", None) is not None:
                self._chat_win.present()
                return
            self._chat_msgs, self._chat_busy = [], False
            win = Adw.Window(title="AI-chatt – Signalspanaren", transient_for=self.win,
                             default_width=560, default_height=680)
            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar()
            ins = Gtk.Button(icon_name="emblem-system-symbolic")
            ins.set_tooltip_text("Inställningar för AI")
            ins.connect("clicked", lambda *_: self._open_ai_settings())
            hb.pack_end(ins)
            tv.add_top_bar(hb)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_margin_start(8)
            box.set_margin_end(8)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            self._chat_info = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
            box.append(self._chat_info)
            self._chat_buf = Gtk.TextBuffer()
            ctv = Gtk.TextView(buffer=self._chat_buf, editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            ctv.set_left_margin(6)
            self._chat_sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            self._chat_sw.set_child(ctv)
            box.append(self._chat_sw)
            self._bifoga = Gtk.CheckButton(label="Bifoga aktuell analys", active=True)
            box.append(self._bifoga)
            rad = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self._chat_in = Gtk.Entry(hexpand=True, placeholder_text="Skriv till AI:n …")
            self._chat_in.connect("activate", lambda *_: self._chat_skicka())
            rad.append(self._chat_in)
            self._chat_send = Gtk.Button(label="Skicka")
            self._chat_send.add_css_class("suggested-action")
            self._chat_send.connect("clicked", lambda *_: self._chat_skicka())
            rad.append(self._chat_send)
            box.append(rad)
            self._chat_spara = Gtk.Button(label="Spara i analysrutan")
            self._chat_spara.add_css_class("suggested-action")
            self._chat_spara.set_visible(False)
            self._chat_spara.connect("clicked", lambda *_: self._chat_anvand())
            box.append(self._chat_spara)
            tv.set_content(box)
            win.set_content(tv)
            win.connect("close-request", lambda *_: win.set_visible(False) or True)
            self._chat_win = win
            self._chat_visa_info()
            win.present()

        def _chat_visa_info(self):
            cfg = ai.las_config(self._ai_cfg_sokv())
            if not cfg["aktiv"]:
                txt = "AI-chatten är avstängd. Slå på den under inställningar (⚙)."
            elif cfg["backend"] == "claude":
                txt = f"Claude ({cfg['claude_modell'] or 'ingen modell vald'}) med din nyckel. Analysdata skickas till Anthropic."
            else:
                txt = f"Gratis online-AI ({cfg['gratis_modell']}). Ingen nyckel krävs."
            self._chat_info.set_text(txt)

        def _chat_rad(self, text):
            self._chat_buf.insert(self._chat_buf.get_end_iter(), text + "\n\n")
            adj = self._chat_sw.get_vadjustment()
            GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)

        def _chat_skicka(self):
            text = self._chat_in.get_text().strip()
            if not text or self._chat_busy:
                return
            cfg = ai.las_config(self._ai_cfg_sokv())
            if not cfg["aktiv"]:
                self._chat_rad("(AI-chatten är avstängd. Slå på den under inställningar.)")
                return
            system = ai.systemprompt(self._analys_text() if self._bifoga.get_active() else None)
            f_ref = (self.last or {}).get("f_abs")
            self._chat_in.set_text("")
            self._chat_busy = True
            self._chat_send.set_sensitive(False)
            self._chat_msgs.append({"role": "user", "content": text})
            self._chat_rad("Du: " + text)
            meddelanden = list(self._chat_msgs)

            def arbeta():
                try:
                    svar = ai.svar(cfg, meddelanden, system)
                except Exception as ex:
                    svar = f"(fel: {ex})"
                GLib.idle_add(self._chat_klart, svar or "(tomt svar)", f_ref)

            threading.Thread(target=arbeta, daemon=True).start()

        def _chat_klart(self, svar, f_ref=None):
            self._chat_msgs.append({"role": "assistant", "content": svar})
            self._chat_rad("AI: " + svar)
            self._chat_busy = False
            self._chat_send.set_sensitive(True)
            self._chat_forslag = ai.forslag_ur(svar)
            self._chat_forslag_f = f_ref
            self._chat_spara.set_visible(bool(self._chat_forslag))
            return False

        def _chat_anvand(self):
            """Kopierar AI:ns överenskomna förslag till analysrutan och sparar det som din klassning."""
            f = getattr(self, "_chat_forslag", None)
            r = self.last or {}
            ref = getattr(self, "_chat_forslag_f", None)
            if not f or not r.get("f_abs") or ref is None:
                self.win.toast("Ingen analys att spara på ännu")
                return
            if abs(r["f_abs"] - ref) > max(500.0, (r.get("occ_bw") or 0) / 2):
                self._chat_spara.set_visible(False)
                self.win.toast("Analysen har bytts sedan frågan. Fråga AI:n igen.")
                return
            fl = getattr(self.win, "flaggor", None)
            if fl is None:
                return
            namn = str(f.get("namn", "")).strip()[:60]
            typ = str(f.get("typ", "")).strip()
            anteckning = str(f.get("not", "")).strip()
            op = self._operator_flagga(r)
            if op is not None:
                fl.andra(op["id"], namn=namn, typ=typ, **{"not": anteckning})
            else:
                fl.lagg_till(r["f_abs"], namn, lage=self.win.engine.mode, typ=typ, noter=anteckning, kalla="signaltyp")
            self._kand_shown = {"namn": namn, "e": {"namn": namn, "kat": typ, "info": anteckning},
                                "sc": None, "kalla": "vald"}
            self._kand_hist.clear()
            self._chat_spara.set_visible(False)
            self._update_rows(r)
            self._chat_rad(f"(sparat i analysrutan: {namn})")

        def _open_ai_settings(self):
            cfg = ai.las_config(self._ai_cfg_sokv())
            win = Adw.Window(title="Inställningar för AI", transient_for=self.win, default_width=460)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_margin_start(12)
            box.set_margin_end(12)
            box.set_margin_top(12)
            box.set_margin_bottom(12)
            dd = Gtk.DropDown.new_from_strings(["Gratis online-AI (standard)", "Claude (egen API-nyckel)"])
            dd.set_selected(1 if cfg["backend"] == "claude" else 0)
            gratis = Gtk.Entry(text=cfg["gratis_modell"])
            cmod = Gtk.Entry(text=cfg["claude_modell"], placeholder_text="modell-ID från ditt konto")
            key = Gtk.Entry(text=cfg["api_key"], visibility=False, placeholder_text="API-nyckel")
            info = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"],
                             label="Gratis-läget kräver ingen nyckel. Med Claude skickas analysdata till Anthropic.")
            aktiv = Gtk.Switch(active=cfg["aktiv"], halign=Gtk.Align.START)
            rad_aktiv = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            rad_aktiv.append(Gtk.Label(label="Använd AI-chatten", xalign=0, hexpand=True))
            rad_aktiv.append(aktiv)
            spara = Gtk.Button(label="Spara")
            spara.add_css_class("suggested-action")

            def _spara(*_):
                ny = {"aktiv": aktiv.get_active(), "backend": "claude" if dd.get_selected() == 1 else "gratis",
                      "gratis_modell": gratis.get_text().strip() or "openai",
                      "claude_modell": cmod.get_text().strip(), "api_key": key.get_text().strip()}
                ai.spara_config(self._ai_cfg_sokv(), ny)
                self._ai_knapp_uppdatera()
                if getattr(self, "_chat_win", None) is not None:
                    self._chat_visa_info()
                win.close()

            spara.connect("clicked", _spara)
            for w_ in (rad_aktiv, Gtk.Label(label="Leverantör", xalign=0), dd, Gtk.Label(label="Gratis modell", xalign=0), gratis,
                       Gtk.Label(label="Claude-modell", xalign=0), cmod,
                       Gtk.Label(label="API-nyckel (valfritt)", xalign=0), key, info, spara):
                box.append(w_)
            tv = Adw.ToolbarView()
            tv.add_top_bar(Adw.HeaderBar())
            tv.set_content(box)
            win.set_content(tv)
            win.present()

        def _show_symbols(self, r):
            """Ettor och nollor från senaste analyssteget, sist i klartextrutan. Ersätts vid varje steg."""
            buf = self.textbuf
            start = buf.get_iter_at_mark(self.sym_mark).get_offset()
            buf.delete(buf.get_iter_at_offset(start), buf.get_end_iter())
            sym = r.get("symboler") if r else None
            if sym:
                grupper = " ".join(sym[i:i + 8] for i in range(0, len(sym), 8))
                buf.insert(buf.get_end_iter(),
                           f"\n── symboler, senaste {len(sym)} ({(r.get('baud') or 0):.0f} Bd) ──\n{grupper}\n")
            buf.move_mark(self.sym_mark, buf.get_iter_at_offset(start))

        def copy(self):
            r = self.last or {}
            out = [f"Signalspanaren signalanalys {time.strftime('%Y-%m-%d %H:%M:%S')}"]
            out.append(f"Signaltyp: {self.klass_lbl.get_text()}")
            for key, title in self.ROWS:
                _l1, l2 = self.cells[key]
                if l2.get_text() != "–":
                    out.append(f"{title}: {l2.get_text()}")
            for sc, e, why in r.get("kandidater") or []:
                out.append(f"Kandidat: {e['namn']} ({sc:.0f} %) {why}")
            txt = self.textbuf.get_text(self.textbuf.get_start_iter(), self.textbuf.get_end_iter(), False)
            if txt:
                out.append("Klartext:\n" + txt[-2000:])
            self.get_clipboard().set("\n".join(out))
            self.win.toast("Analysen kopierad")

    class MainWindow(Adw.ApplicationWindow):
        def __init__(self, app, conf, args):
            super().__init__(application=app, title=APP_NAME)
            self.conf = conf
            self.args = args
            self.engine = Engine()
            self.audio = AudioOut()
            self.web = None
            self.wf_center = conf["freq"] + SR * 0.15
            self.freq = conf["freq"]
            self.latest_db = None
            self.latest_c = None
            self._relevel_at = 0
            self.active_tracks = []
            self.log_rows = {}
            self._updating = False
            self._auto_level_pending = True
            self.scanner = Scanner(self)
            import skarm
            self.skarm = skarm
            w, h, maxi = skarm.startstorlek(conf.get("window"))
            self.set_default_size(w, h)
            if maxi or conf.get("window_max"):
                self.maximize()
            kompakt = skarm.ar_kompakt(conf.get("skarm_lage", "auto"))
            skarm.satt_kompakt(self, kompakt)
            skarm.folj_skarm(self, lambda: self.conf.get("skarm_lage", "auto"), self._on_ny_skarm)
            log.info("Skärm: %s – fönster %d×%d%s, %s läge", skarm.beskriv(), w, h, " (maximerat)" if maxi else "",
                     "kompakt" if kompakt else "normalt")

            e = self.engine
            e.mode = conf["mode"] if conf["mode"] in MODES else "WFM"
            e.bw = conf["bw"] or DEFAULT_BW[e.mode]
            e.squelch = conf["squelch"]
            e.volume = conf["volume"] / 100
            self._apply_dsp_conf()
            if conf.get("dsp_open"):
                GLib.idle_add(lambda: self._open_dsp() and False)
            e.agc_attack = float(conf.get("agc_attack", 50.0))
            e.agc_decay = float(conf.get("agc_decay", 5.0))
            e.auto_rec = conf["auto_rec"]
            e.detector.thresh = conf["det_thresh"]
            e.detector.enabled = conf["det_enabled"]
            e.on_error = lambda msg: GLib.idle_add(self._on_engine_error, msg)
            e.on_stopped = lambda: GLib.idle_add(self._on_engine_stopped)
            e.on_event = lambda k, v: GLib.idle_add(self._on_engine_event, k, v)
            e.add_sink(self.audio)
            self.decoded_rows = 0
            self.suggested = []     # bärvågor som skannern hoppat över denna session
            self.ships = {}         # MMSI -> sammanslagen fartygsinformation (AIS)
            self.ship_win = None
            self.webrx_win = None
            self.activity_win = None
            self.activity_active = False
            self._ship_tick = 0
            self.hub = avkodare.DecoderHub(SR, lambda m: GLib.idle_add(self._on_decoded, m))
            for k in ("ACARS", "AIS", "APRS"):
                self.hub.enabled[k] = bool(conf.get(k.lower(), False))
            e.decoders = self.hub
            self.analyzer = analys.Analyzer(SR, os.path.join(CONF_DIR, "signaler.json"))
            e.analyzer = self.analyzer
            self.analyzer.start()
            self.an_win = None
            self.panel_win = None
            e.center = self.wf_center
            e.offset = self.freq - e.center

            self.toasts = Adw.ToastOverlay()
            tv = Adw.ToolbarView()
            tv.add_top_bar(self._build_header())
            outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            outer.set_start_child(self._build_sidebar())
            outer.set_resize_start_child(False)
            outer.set_shrink_start_child(False)
            outer.set_position(skarm.sidopanel_bredd(conf.get("sidebar_width")))
            self.outer = outer
            right = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, wide_handle=True)
            right.add_css_class("delare")
            right.add_css_class("nedre-delare")
            self.flaggor = flaggor.Flaggor(CONF_DIR, CACHE_DIR)
            self.flaggor.kiwi_upptagen = lambda: self.engine.running and isinstance(self.engine.source, KiwiSource)
            self.flaggor.on_change = lambda: GLib.idle_add(self._flaggor_andrade)
            self.analyzer.ny_motor = conf.get("analys_motor", "ny") != "gammal"
            self.analyzer.kontext = lambda f: flaggor.bevis_kontext(self.flaggor, f)
            self._ref_addr = conf.get("flaggor_ref_addr") or flaggor.REFERENS
            if conf.get("flaggor_ref", True):
                GLib.timeout_add(1500, lambda: self.flaggor.satt_referens(
                    webbmottagare.norm_url(self._ref_addr), self._ref_addr) and False)
            self.view = SpectrumView(self)
            overlay = Gtk.Overlay()
            overlay.set_child(self.view)
            ctrls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
            ctrls.add_css_class("wf-controls")
            ctrls.set_halign(Gtk.Align.END)
            ctrls.set_valign(Gtk.Align.END)
            ctrls.set_margin_end(8)
            ctrls.set_margin_bottom(8)
            def _btn(label, tip, cb):
                b = Gtk.Button(label=label)
                b.set_tooltip_text(tip)
                b.connect("clicked", lambda *_: cb())
                return b
            ctrls.append(_btn("−", "Zooma ut i vattenfallet", lambda: self.zoom_spectrum(0.5)))
            ctrls.append(_btn("1:1", "Återställ zoom", lambda: self.zoom_spectrum(0)))
            ctrls.append(_btn("+", "Zooma in i vattenfallet", lambda: self.zoom_spectrum(2)))
            ctrls.append(_btn("f−", "Flytta inställd frekvens ett steg ned", lambda: self.nudge_freq(-1)))
            self.wf_step_dd = Gtk.DropDown.new_from_strings(STEP_NAMES)
            self.wf_step_dd.set_selected(self.conf["step_idx"])
            self.wf_step_dd.set_tooltip_text("Frekvenssteg för f− och f+ (samma som Frekvenssteg i sidopanelen)")
            self.wf_step_dd.connect("notify::selected", lambda dd, _p: self._set_step_idx(dd.get_selected()))
            ctrls.append(self.wf_step_dd)
            ctrls.append(_btn("f+", "Flytta inställd frekvens ett steg upp", lambda: self.nudge_freq(1)))
            ctrls.append(_btn("◀", "Flytta spektrumet åt vänster", lambda: self.pan_spectrum(-1)))
            ctrls.append(_btn("▶", "Flytta spektrumet åt höger", lambda: self.pan_spectrum(1)))
            overlay.add_overlay(ctrls)
            right.set_start_child(overlay)
            # DSP-panelen svävar ovanpå den nedre delen (flikarna) – aldrig över spektrum/vattenfall
            self.bottom_overlay = Gtk.Overlay()
            self.bottom_overlay.set_child(self._build_bottom())
            right.set_end_child(self.bottom_overlay)
            right.set_resize_end_child(False)     # panelen behåller sin höjd när fönstret ändras
            right.set_shrink_end_child(False)
            right.set_shrink_start_child(False)
            right.set_position(int(h * 0.62))
            self.right_paned = right
            self._bottom_restored = False
            self._bottom_grown = False
            def _restore_bottom(*_):
                mx = right.get_property("max-position")
                bh = self.conf.get("bottom_height")
                if self._bottom_restored or mx <= 0:
                    return
                self._bottom_restored = True
                if bh:
                    self._bottom_grown = True     # användarens sparade höjd gäller
                    right.set_position(max(min(200, mx // 2), mx - int(bh)))   # vattenfallet behåller minst 200 px
                else:
                    right.set_position(int(mx * (0.55 if mx < 700 else 0.6)))
            right.connect("notify::max-position", _restore_bottom)
            self.dsp_panel = None
            outer.set_end_child(right)
            self.toasts.set_child(outer)
            tv.set_content(self.toasts)
            self.set_content(tv)

            self._load_bookmarks()
            self.connect("close-request", self._on_close)
            GLib.timeout_add(33, self._tick)
            GLib.timeout_add(400, self._slow_tick)
            self._update_freq_label()

            global _gui_error_hook
            _gui_error_hook = lambda msg: GLib.idle_add(
                self.toast, f"Fel: {msg} – se Felsökning → Kopiera felrapport")

            if conf["web_enabled"]:
                GLib.idle_add(self._start_web)
            if args.start or args.skanna:
                GLib.idle_add(lambda: self.btn_start.set_active(True) or False)
            if getattr(args, "panel", None):
                GLib.timeout_add(700, lambda: self._open_frontpanel(args.panel) or False)
            if args.kalibrera:
                GLib.timeout_add(900, lambda: self._open_calibration() or False)
            if args.aktivitetskarta:
                GLib.timeout_add(800, lambda: self._open_activity() or False)
            if args.fartygskarta:
                GLib.timeout_add(800, lambda: self._open_ship_map() or False)
            if args.position:
                self.conf["my_pos"] = args.position
            if args.webbmottagare:
                GLib.timeout_add(800, lambda: self._open_webrx() or False)
            if args.flik:
                GLib.idle_add(lambda: self.bottom_nb.set_current_page(
                    ["signallogg", "bokmarken", "skanner", "avkodat", "analys"].index(args.flik)) or False)
            if args.skarmbild:
                path, secs = args.skarmbild[0], float(args.skarmbild[1])
                GLib.timeout_add(int(secs * 1000), lambda: self.save_screenshot(path) and False)
            if args.skanna:
                GLib.timeout_add(1500, lambda: (self.scanner.active or self._toggle_scan()) and False)
            if getattr(args, "analysera", None):
                fa = parse_freq(args.analysera)

                def _start_analys():
                    hit = self.carrier_at(fa, tol=5000)
                    bw0 = DEFAULT_BW.get(self.engine.mode, 3000)
                    self.analyse(*(hit or (fa, min(bw0, 12000.0) if fa < 30e6 else bw0)))
                    return False
                GLib.timeout_add(2500, _start_analys)

        # ── delar av fönstret ──
        def _build_header(self):
            hb = Adw.HeaderBar()
            self.btn_start = Gtk.ToggleButton(label="▶ Starta")
            self.btn_start.add_css_class("suggested-action")
            self.btn_start.connect("toggled", self._on_start_toggled)
            hb.pack_start(self.btn_start)

            self.freq_entry = Gtk.Entry()
            self.freq_entry.add_css_class("freq-entry")
            self.freq_entry.set_width_chars(14)
            self.freq_entry.set_tooltip_text("Frekvens. Skriv t.ex. 145.5, 7074k eller 1090 MHz och tryck Enter")
            self.freq_entry.connect("activate", self._on_freq_entry)
            hb.pack_start(self.freq_entry)
            mhz = Gtk.Label(label="MHz")
            mhz.add_css_class("dim-label")
            hb.pack_start(mhz)

            self.mode_dd = Gtk.DropDown.new_from_strings(MODES)
            self.mode_dd.set_selected(MODES.index(self.engine.mode))
            self.mode_dd.set_tooltip_text("Modulering")
            self.mode_dd.connect("notify::selected", self._on_mode_dd)
            hb.pack_start(self.mode_dd)

            box = Gtk.Box(spacing=8)
            self.level_bar = Gtk.LevelBar(min_value=0, max_value=120)
            self.level_bar.set_size_request(160, -1)
            self.level_bar.set_valign(Gtk.Align.CENTER)
            self.level_lbl = Gtk.Label(label="– dB")
            self.level_lbl.add_css_class("monospace")
            self.level_lbl.set_width_chars(16)
            self.sq_lbl = Gtk.Label(label="")
            box.append(self.level_bar)
            box.append(self.level_lbl)
            box.append(self.sq_lbl)
            hb.set_title_widget(box)

            self.btn_skip = Gtk.Button(label="⏭ Hoppa över")
            self.btn_skip.set_tooltip_text("Spärra den här frekvensen och fortsätt skanna")
            self.btn_skip.connect("clicked", lambda *_: self.scanner.skip())
            self.btn_skip.set_visible(False)
            hb.pack_end(self.btn_skip)
            self.scan_lbl = Gtk.Label(label="")
            self.scan_lbl.add_css_class("accent")
            hb.pack_end(self.scan_lbl)
            self.btn_rec = Gtk.ToggleButton(label="● Spela in")
            self.btn_rec.set_tooltip_text("Spela in ljudet till en WAV-fil")
            self.btn_rec.connect("toggled", self._on_rec_toggled)
            hb.pack_end(self.btn_rec)
            return hb

        def _section(self, title, desc=None):
            g = Adw.PreferencesGroup(title=title)
            if desc:
                g.set_description(desc)
            return g

        def _spin(self, group, title, lo, hi, step, value, digits, cb, subtitle=None):
            r = Adw.SpinRow.new_with_range(lo, hi, step)
            r.set_title(title)
            if subtitle:
                r.set_subtitle(subtitle)
            r.set_digits(digits)
            r.set_value(value)
            r.connect("notify::value", lambda row, _p: cb(row.get_value()))
            group.add(r)
            return r

        def _combo(self, group, title, items, sel, cb):
            r = Adw.ComboRow(title=title)
            if max((len(x) for x in items), default=0) > 14:
                r.set_use_subtitle(True)        # långa val syns helt i stället för ”KiwiSDR (webb-SDR på …”
            r.set_model(Gtk.StringList.new(items))   # efter use_subtitle, annars visas inte första valet
            r.set_selected(sel)
            r.connect("notify::selected", lambda row, _p: cb(row.get_selected()))
            group.add(r)
            return r

        def _switch(self, group, title, active, cb, subtitle=None):
            r = Adw.SwitchRow(title=title)
            if subtitle:
                r.set_subtitle(subtitle)
            r.set_active(active)
            r.connect("notify::active", lambda row, _p: cb(row.get_active()))
            group.add(r)
            return r

        def _button(self, group, title, cb):
            if hasattr(Adw, "ButtonRow"):                 # libadwaita 1.6+
                r = Adw.ButtonRow(title=title)
            else:                                         # libadwaita 1.5 (Ubuntu 24.04, Linux Mint 22)
                r = Adw.ActionRow(title=title, activatable=True)
                r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            r.connect("activated", lambda *_: cb())
            group.add(r)
            return r

        def _build_sidebar(self):
            c = self.conf
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            box.set_margin_top(12)
            box.set_margin_bottom(12)
            box.set_margin_start(12)
            box.set_margin_end(12)

            g = self._section("Mottagare")
            self.source_row = self._combo(g, "Källa", SOURCE_NAMES,
                                          SOURCES.index(c["source"]) if c["source"] in SOURCES else 0, self._on_source)
            self.tcp_row = Adw.EntryRow(title="rtl_tcp-adress (värd:port)")
            self.tcp_row.set_text(c["tcp_addr"])
            self.tcp_row.set_show_apply_button(True)
            self.tcp_row.connect("apply", lambda r: self._set_conf("tcp_addr", r.get_text().strip()))
            g.add(self.tcp_row)
            self.kiwi_row = Adw.EntryRow(title="KiwiSDR-adress (klistra in webbadressen)")
            self.kiwi_row.set_text(c.get("kiwi_addr", ""))
            self.kiwi_row.set_show_apply_button(True)
            self.kiwi_row.set_tooltip_text("Adressen du surfar in på, t.ex. http://sk5sm.proxy.kiwisdr.com:8073/\n"
                                           "Lista: www.receiverbook.de/map eller rx.kiwisdr.com\n"
                                           "Lösenord (om Kiwin kräver det): lösen@värd:port\n"
                                           "Kiwin ger ca 12 kHz I/Q i taget, 0–30 MHz.")
            self.kiwi_row.connect("apply", lambda r: (self._set_conf("kiwi_addr", r.get_text().strip()),
                                                      self._refresh_ppm_row()))
            g.add(self.kiwi_row)
            self._button(g, "🌐 Webbmottagare på nätet …", self._open_webrx).set_tooltip_text(
                "KiwiSDR, OpenWebRX och WebSDR runt om i världen")
            self.agc_row = self._switch(g, "Automatisk förstärkning", c["auto_gain"], self._on_auto_gain)
            self.hw_rows = [self.agc_row]
            self.gain_row = self._spin(g, "Förstärkning (dB)", 0, 49.6, 0.5, c["gain"], 1, self._on_gain)
            self.gain_row.set_sensitive(not c["auto_gain"])
            self.ppm_row = self._spin(g, "Frekvenskorrigering (ppm)", -200, 200, 1, c["ppm"], 0, self._on_ppm)
            self.hw_rows += [self.gain_row, self.ppm_row,
                             self._button(g, "Kalibrera frekvens (ppm) …", self._open_calibration),
                             self._combo(g, "Direktsampling (HF under 28 MHz)",
                                         ["Av", "I-gren", "Q-gren (RTL-SDR v3)"], c["direct"], self._on_direct),
                             self._switch(g, "Bias-T (matar antennförstärkare)", c["biast"], self._on_biast)]
            box.append(g)

            g = self.play_group = self._section("Uppspelning av IQ-fil",
                                                "SigMF, WAV (SDR++/SDR#), rtl_sdr .cu8, .cs8, .cs16, .cf32/.cfile")
            self.play_file_row = Adw.ActionRow(title="Ingen fil vald")
            self.play_file_row.set_subtitle_lines(3)
            b = Gtk.Button(label="Välj …", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda *_: self._choose_play_file())
            self.play_file_row.add_suffix(b)
            g.add(self.play_file_row)
            pos_row = Adw.ActionRow(title="Position")
            self.play_pause_btn = Gtk.Button(icon_name="media-playback-pause-symbolic", valign=Gtk.Align.CENTER,
                                             tooltip_text="Paus / fortsätt")
            self.play_pause_btn.connect("clicked", lambda *_: self._toggle_play_pause())
            pos_row.add_prefix(self.play_pause_btn)
            self.play_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.1)
            self.play_scale.set_draw_value(False)
            self.play_scale.set_hexpand(True)
            self.play_scale.set_valign(Gtk.Align.CENTER)
            self.play_scale.set_size_request(130, -1)
            self.play_scale.connect("change-value", self._on_play_seek)
            pos_row.add_suffix(self.play_scale)
            self.play_pos_lbl = Gtk.Label(label="0:00 / 0:00", valign=Gtk.Align.CENTER)
            self.play_pos_lbl.add_css_class("numeric")
            pos_row.add_suffix(self.play_pos_lbl)
            pos_row.set_title("")
            g.add(pos_row)
            self._combo(g, "Hastighet", ["1× (realtid)", "2×", "4×", "8×", "Så fort det går"], c["play_speed"],
                        self._on_play_speed)
            self._switch(g, "Spela i slinga", c["play_loop"], self._on_play_loop)
            self._button(g, "Gå till filens mittfrekvens", self._play_goto_center)
            box.append(g)
            self._update_play_info()
            self._show_source_rows(c["source"])

            g = self._section("Demodulator")
            self.bw_row = self._spin(g, "Bandbredd (Hz)", 100, 250000, 100, self.engine.bw, 0, self._on_bw)
            self.sq_row = self._spin(g, "Squelch (dB)", -150, 0, 1, c["squelch"], 0, self._on_squelch,
                                     "-150 = alltid öppen")
            self.vol_row = self._spin(g, "Volym (%)", 0, 300, 5, c["volume"], 0, self._on_volume)
            self._spin(g, "AGC attack", 1, 200, 1, c.get("agc_attack", 50.0), 0,
                       lambda v: self._set_agc("agc_attack", v),
                       "AM/SSB/CW. Hur snabbt ljudet dras ned (SDR++: 50)")
            self.agc_preset_row = self._combo(
                g, "AGC-förval", [n for n, _ in AGC_PRESETS], self._agc_preset_idx(c.get("agc_decay", 5.0)),
                self._on_agc_preset)
            self.agc_decay_row = self._spin(g, "AGC decay", 0.1, 20, 0.1, c.get("agc_decay", 5.0), 2,
                       lambda v: self._set_agc("agc_decay", v),
                       "Hur snabbt ljudet återhämtar sig – lägre = mindre brus mellan orden (SDR++: 5)")
            self.step_row = self._combo(g, "Frekvenssteg", STEP_NAMES, c["step_idx"],
                                        lambda i: self._set_step_idx(i))
            self._button(g, "🎛 DSP-filter", self._open_dsp).set_tooltip_text(
                "Visar/döljer en panel till höger om vattenfallet med filterform, PBT, brusreducering, notch, "
                "störningsblankare och tonkontroll – går att använda medan du lyssnar")
            self._switch(g, "Automatiskt sidband", c.get("auto_sideband", True),
                         lambda v: self._set_conf("auto_sideband", v)).set_tooltip_text(
                "I USB/LSB: byt till LSB på 160/80/40 m (tal) och USB annars (30 m och uppåt, 60 m, FT8/data, "
                "flyg, sjöfart) när du stämmer till ett annat område. Ett handvalt sidband gäller tills du lämnar området.")
            box.append(g)

            g = self._section("Visning")
            self.dbmin_row = self._spin(g, "Min nivå (dB)", -160, 0, 1, c["db_min"], 0,
                                        lambda v: self._set_conf("db_min", v))
            self.dbmax_row = self._spin(g, "Max nivå (dB)", -150, 20, 1, c["db_max"], 0,
                                        lambda v: self._set_conf("db_max", v))
            self._button(g, "Automatisk nivå", self.auto_level)
            self.zoom_row = self._spin(g, "Zoom", 1, 128, 1, c["zoom"], 0, lambda v: self.set_zoom(int(v)),
                                       "Ctrl + mushjul i vattenfallet")
            cmaps = list(COLORMAPS)
            self._combo(g, "Tema", ["Mörkt", "Ljust", "Följ systemet"], c.get("theme", 0), self._on_theme)
            skins = list(SKINS)
            self._combo(g, "Utseende", [SKINS[k]["namn"] for k in skins],
                        skins.index(c.get("skin")) if c.get("skin") in skins else 0, self._on_skin)
            self._combo(g, "Färgskala", cmaps, cmaps.index(c["colormap"]) if c["colormap"] in cmaps else 0,
                        self._on_colormap)
            lagen = ["auto", "normal", "kompakt"]
            self.skarm_row = self._combo(g, "Skärmanpassning", ["Automatiskt", "Normal", "Kompakt"],
                                         lagen.index(c.get("skarm_lage")) if c.get("skarm_lage") in lagen else 0,
                                         self._on_skarm_lage)
            self._skarm_subtitle()
            box.append(g)

            g = self._section("Frontpanel", "Riktiga reglage och displayer som styr mottagaren")
            self._button(g, "🎛 Mottagare 1 – stor mottagare …", lambda: self._open_frontpanel("r9500"))
            self._button(g, "🎛 Mottagare 2 – klassisk mottagare …", lambda: self._open_frontpanel("r7100"))
            self._button(g, "🎛 Mottagare 3 – skannerpanel …", lambda: self._open_frontpanel("r2500"))
            box.append(g)

            g = self._section("Skanner")
            self.scan_mode_row = self._combo(g, "Skanna", ["Bokmärken", "Frekvensintervall"],
                                             c["scan_mode"], self._on_scan_mode)
            self.scan_group_row = Adw.ComboRow(title="Bokmärken att skanna")
            self.scan_group_row.connect("notify::selected", lambda r, _p: self._on_scan_group(r.get_selected()))
            g.add(self.scan_group_row)
            self.scan_preset_row = self._combo(g, "Band (förval)", [p[0] for p in SCAN_PRESETS],
                                               min(c.get("scan_preset", 0), len(SCAN_PRESETS) - 1), self._on_scan_preset)
            self.scan_range_rows = [self.scan_preset_row]
            self.scan_entries = {}
            for key, title in (("scan_start", "Start (MHz)"), ("scan_stop", "Stopp (MHz)")):
                r = Adw.EntryRow(title=title)
                r.set_text(str(c[key]))
                r.connect("changed", lambda row, key=key: self._set_conf(key, row.get_text()))
                g.add(r)
                self.scan_entries[key] = r
                self.scan_range_rows.append(r)
            self.scan_step_row = self._spin(g, "Kanalsteg (kHz)", 0.1, 1000, 0.5, c["scan_step_khz"], 3,
                                            lambda v: self._set_conf("scan_step_khz", v))
            self.scan_range_rows.append(self.scan_step_row)
            self.scan_sq_row = self._combo(g, "Stanna när", ["Signalen är X dB över bruset", "Squelch-reglaget öppnar"],
                                           c.get("scan_sq_mode", 0), self._on_scan_sq_mode)
            self.scan_snr_row = self._spin(g, "Över brusgolvet (dB)", 3, 40, 1, c.get("scan_snr", 10.0), 0,
                                           lambda v: self._set_conf("scan_snr", v),
                                           "Brusgolvet mäts i varje fönster – fungerar på alla band")
            self._spin(g, "Väntetid efter signal (s)", 0, 60, 0.5, c["scan_hold"], 1,
                       lambda v: self._set_conf("scan_hold", v))
            self._spin(g, "Tid per fönster (ms)", 100, 5000, 50, c["scan_dwell_ms"], 0,
                       lambda v: self._set_conf("scan_dwell_ms", int(v)),
                       "Alla kanaler inom 1,2 MHz kollas samtidigt")
            self._spin(g, "Max lyssningstid (s)", 0, 600, 5, c.get("scan_max_listen", 0), 0,
                       lambda v: self._set_conf("scan_max_listen", v), "0 = stanna så länge signalen finns")
            self.scan_prio_row = Adw.ComboRow(title="Prioritetskanal")
            self.scan_prio_row.set_use_subtitle(True)
            self.scan_prio_row.set_tooltip_text("Kollas hela tiden och tar över när den hörs")
            self.scan_prio_row.connect("notify::selected", lambda r, _p: self._on_scan_prio(r.get_selected()))
            g.add(self.scan_prio_row)
            self._spin(g, "Kolla prioritet var (s)", 0.5, 30, 0.5, c.get("scan_prio_s", 2.0), 1,
                       lambda v: self._set_conf("scan_prio_s", v),
                       "Ligger den i samma fönster kollas den utan avbrott")
            self.scan_btn = self._button(g, "Starta skanning", self._toggle_scan)
            self._switch(g, "Hoppa över omodulerade bärvågor", c.get("scan_skip_carriers", True),
                         lambda v: self._set_conf("scan_skip_carriers", v),
                         "Störningar från dongeln/datorn (gäller AM och NFM)")
            self.sugg_row = Adw.ActionRow(title="Hittade störningar", subtitle="inga än")
            self.sugg_btn = Gtk.Button(label="Spärra", valign=Gtk.Align.CENTER)
            self.sugg_btn.set_tooltip_text("Spara de hittade störningarna i spärrlistan permanent")
            self.sugg_btn.connect("clicked", lambda *_: self._lock_suggested())
            self.sugg_row.add_suffix(self.sugg_btn)
            g.add(self.sugg_row)
            self.lock_btn = self._button(g, "Spärrade frekvenser …", self._open_lockout)
            box.append(g)
            self._refresh_scan_lists()
            self._on_scan_mode(c["scan_mode"])
            self._on_scan_sq_mode(c.get("scan_sq_mode", 0))

            g = self._section("Flaggor", "Stationsetiketter ovanför vattenfallet. Klicka på en flagga för att stämma in "
                                         "den, högerklicka för att lägga till eller ändra.")
            self._switch(g, "Visa flaggor", c.get("flaggor_visa", True),
                         lambda v: (self._set_conf("flaggor_visa", v)))
            self._switch(g, "Hämta KiwiSDR:ns flaggor", c.get("flaggor_kiwi", True), self._on_flaggor_kiwi,
                         "Kiwins egna etiketter när en KiwiSDR är källa")
            self._switch(g, "Flaggor från referens-Kiwi", c.get("flaggor_ref", True), self._on_flaggor_ref,
                         "KiwiSDR:s fullständiga flagglista från Kiwin hos aprs.fi i Kuopio, oavsett mottagare. "
                         "Hämtas en gång i veckan. Analysen använder flaggorna som stöd")
            self.flagg_status_row = Adw.ActionRow(title="Status", subtitle="–")
            g.add(self.flagg_status_row)
            self._button(g, "Hämta Kiwins flaggor igen", self._flaggor_hamta_om)
            self._button(g, "🚩 Ny flagga på inställd frekvens …", lambda: self.flagg_dialog(None, self.freq))
            box.append(g)

            g = self._section("Signallogg", "Hittar signaler automatiskt i hela bandet")
            self._switch(g, "Logga signaler", c["det_enabled"], self._on_det_enabled)
            self._spin(g, "Tröskel över brus (dB)", 3, 40, 1, c["det_thresh"], 0, self._on_det_thresh)
            box.append(g)

            g = self._section("Avkodare", "Avkodade meddelanden visas i fliken Avkodat. Fönstret är ca "
                              "1,2 MHz brett, så välj ett band i taget med knapparna.")
            self._switch(g, "ACARS (flygplan, 129–137 MHz)", c.get("acars", False),
                         lambda v: self._on_decoder("ACARS", v), "AM/MSK 2400 – alla ACARS-kanaler i fönstret")
            self._switch(g, "AIS (fartyg, 161,975/162,025)", c.get("ais", False),
                         lambda v: self._on_decoder("AIS", v), "GMSK 9600 – båda AIS-kanalerna")
            self._switch(g, "APRS (144,800 MHz)", c.get("aprs", False),
                         lambda v: self._on_decoder("APRS", v), "AFSK 1200 – positioner och meddelanden")
            self.acars_row = Adw.ActionRow(title="Status", subtitle="av")
            g.add(self.acars_row)
            self._button(g, "Gå till ACARS (131,7 MHz)", lambda: self._goto_decoder("ACARS"))
            self._button(g, "Gå till AIS (162 MHz)", lambda: self._goto_decoder("AIS"))
            self._button(g, "Gå till APRS (144,800 MHz)", lambda: self._goto_decoder("APRS"))
            self._button(g, "📊 Aktivitetskarta", self._open_activity).set_tooltip_text("Värmekarta över ett band")
            self._button(g, "🚢 Visa fartyg på karta (AIS)", self._open_ship_map)
            self._button(g, "✈ Öppna Flygradar (ADS-B, 1090 MHz)", self._open_radar)
            box.append(g)

            g = self._section("Inspelning", f"Sparas i {rec_dir()}")
            self._switch(g, "Spela in automatiskt vid signal", c["auto_rec"], self._on_auto_rec)
            self._combo(g, "IQ-format", [n for n, _k in IQ_FORMATS], c["iq_format"],
                        lambda i: self._set_conf("iq_format", i))
            self.iq_btn = self._button(g, "Starta IQ-inspelning", self._toggle_iq)
            self._button(g, "Öppna inspelningsmappen", lambda: self._open_path(rec_dir()))
            box.append(g)

            g = self._section("Webbgränssnitt", "Lyssna och styr från en webbläsare")
            self._switch(g, "Aktivera webbgränssnitt", c["web_enabled"], self._on_web_enabled)
            self._spin(g, "Port", 1024, 65535, 1, c["web_port"], 0, lambda v: self._set_conf("web_port", int(v)))
            self._switch(g, "Tillåt andra datorer i nätverket", c["web_public"],
                         lambda v: self._set_conf("web_public", v), "Av = bara den här datorn")
            self.web_row = Adw.ActionRow(title="Adress", subtitle="avstängt")
            self.web_row.set_subtitle_selectable(True)
            g.add(self.web_row)
            box.append(g)

            g = self._section("Felsökning", f"Loggfil: {LOG_FILE}")
            self._button(g, "Kopiera felrapport", self.copy_report)
            self._button(g, "Öppna loggfilen", lambda: self._open_path(LOG_FILE))
            self._button(g, "Visa manualen", lambda: self._open_path(MANUAL_FILE))
            self.stats_row = Adw.ActionRow(title="Prestanda", subtitle="–")
            g.add(self.stats_row)
            box.append(g)

            def plain_text(w):
                # Äldre libadwaita tolkar rubriker som markup – "<" och "&" ska visas som text
                if isinstance(w, Adw.PreferencesRow):
                    w.set_use_markup(False)
                ch = w.get_first_child()
                while ch is not None:
                    plain_text(ch)
                    ch = ch.get_next_sibling()
            plain_text(box)
            self._make_collapsible(box)

            sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            sw.set_child(box)
            sw.set_size_request(280, -1)
            return sw

        def _make_collapsible(self, box):
            """Gör varje sektion i sidopanelen hopfällbar (klicka på rubriken). Hopfällda sparas i conf."""
            hopfallda = set(self.conf.get("sidebar_collapsed") or [])
            self._section_wraps = {}
            g = box.get_first_child()
            while g is not None:
                nxt = g.get_next_sibling()
                if isinstance(g, Adw.PreferencesGroup) and g.get_title():
                    title = g.get_title()
                    prev = g.get_prev_sibling()
                    vis = g.get_visible()
                    box.remove(g)
                    g.set_title("")
                    g.set_visible(True)
                    exp = Gtk.Expander(label=f"<b>{GLib.markup_escape_text(title)}</b>", use_markup=True,
                                       expanded=title not in hopfallda)
                    exp.set_tooltip_text("Klicka för att fälla ihop/fälla ut")
                    exp.set_child(g)
                    exp.set_visible(vis)
                    exp.connect("notify::expanded", lambda e, _p, t=title: self._on_section_toggled(t, e.get_expanded()))
                    box.insert_child_after(exp, prev)
                    self._section_wraps[g] = exp
                g = nxt

        def _on_section_toggled(self, title, expanded):
            lst = [t for t in (self.conf.get("sidebar_collapsed") or []) if t != title]
            if not expanded:
                lst.append(title)
            self.conf["sidebar_collapsed"] = lst

        def _section_visible(self, g, vis):
            """Visa/dölj en sektion (eller dess hopfällbara omslag)."""
            (getattr(self, "_section_wraps", {}).get(g) or g).set_visible(vis)

        def _build_bottom(self):
            nb = Gtk.Notebook()
            self.bottom_nb = nb

            # Signallogg
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            v.set_margin_top(6)
            v.set_margin_start(6)
            v.set_margin_end(6)
            tb = Gtk.Box(spacing=6)
            self.filter_entry = Gtk.SearchEntry(hexpand=True)
            self.filter_entry.set_placeholder_text("Filter, t.ex.  freq>144  bw<25k  typ~NFM  aktiv")
            self.filter_entry.set_tooltip_text("Filter: freq>144 freq<146  bw<25k  peak>-50  typ~NFM  aktiv")
            self.filter_entry.connect("search-changed", self._on_filter_changed)
            tb.append(self.filter_entry)
            for label, cb in (("Spara som bokmärke", self._log_to_bookmark), ("Exportera CSV", self._export_csv),
                              ("Rensa", self._clear_log)):
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda *_, cb=cb: cb())
                tb.append(b)
            self.log_count = Gtk.Label(label="0 signaler")
            self.log_count.add_css_class("dim-label")
            tb.append(self.log_count)
            v.append(tb)
            self._filter = []
            cols = [
                ("Nr", lambda d: str(d["id"]), "id", False),
                ("Först sedd", lambda d: time.strftime("%H:%M:%S", time.localtime(d["first"])), "first", False),
                ("Frekvens (MHz)", lambda d: fmt_mhz(d["f"]), "f", False),
                ("Bandbredd", lambda d: fmt_bw(d["bw"]), "bw", False),
                ("Topp (dB)", lambda d: f"{d['peak']:.1f}", "peak", False),
                ("SNR (dB)", lambda d: f"{d['snr']:.1f}", "snr", False),
                ("Längd", lambda d: f"{d['dur']:.1f} s", "dur", False),
                ("Typ (gissning)", lambda d: d["cls"], "cls", False),
                ("Band", lambda d: d["band"], "band", True),
                ("Status", lambda d: d["status"], "status", False),
            ]
            (self.log_store, self.log_cv, self.log_sel, self.log_filter, lcols, sw) = make_table(cols, self._filter_func)
            self.log_cv.connect("activate", lambda cv, pos: self._tune_row(self.log_sel.get_item(pos)))
            self.log_cv.sort_by_column(lcols[0], Gtk.SortType.DESCENDING)
            v.append(sw)
            nb.append_page(v, Gtk.Label(label="Signallogg"))

            # Bokmärken
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            v.set_margin_top(6)
            v.set_margin_start(6)
            v.set_margin_end(6)
            tb = Gtk.Box(spacing=6)
            self.bm_name = Gtk.Entry(hexpand=True, placeholder_text="Namn på nytt bokmärke")
            self.bm_name.connect("activate", lambda *_: self._add_bookmark())
            tb.append(self.bm_name)
            for label, cb in (("Lägg till aktuell frekvens", self._add_bookmark),
                              ("Ta bort markerat", self._del_bookmark)):
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda *_, cb=cb: cb())
                tb.append(b)
            v.append(tb)
            cols = [
                ("Namn", lambda d: d["name"], "name", True),
                ("Frekvens (MHz)", lambda d: fmt_freq(d["freq"]), "freq", False),
                ("Modulering", lambda d: d.get("mode", ""), "mode", False),
                ("Band", lambda d: band_name(d["freq"]), "freq", False),
            ]
            (self.bm_store, self.bm_cv, self.bm_sel, _f, _c, sw) = make_table(cols)
            self.bm_cv.connect("activate", lambda cv, pos: self._tune_row(self.bm_sel.get_item(pos)))
            v.append(sw)
            nb.append_page(v, Gtk.Label(label="Bokmärken"))

            # Skanner – träfflista
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            v.set_margin_top(6)
            v.set_margin_start(6)
            v.set_margin_end(6)
            tb = Gtk.Box(spacing=6)
            self.hit_count = Gtk.Label(label="Inga träffar än – starta skanningen", hexpand=True, xalign=0)
            self.hit_count.add_css_class("dim-label")
            tb.append(self.hit_count)
            for label, cb in (("Lägg till som bokmärke", self._hit_bookmark), ("Spärra", self._hit_lock),
                              ("Exportera CSV", self._export_hits), ("Rensa", self._clear_hits)):
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda *_, cb=cb: cb())
                tb.append(b)
            v.append(tb)
            ago = lambda t: (f"{int(time.time() - t)} s sedan" if time.time() - t < 120 else
                             datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S"))
            cols = [
                ("Frekvens (MHz)", lambda d: fmt_freq(d["f"]), "f", False),
                ("Namn / band", lambda d: ("★ " if d["prio"] else "") + (d["namn"] or ""), "namn", True),
                ("Mod", lambda d: d["mode"], "mode", False),
                ("Träffar", lambda d: str(d["count"]), "count", False),
                ("Total tid", lambda d: f"{d['dur']:.0f} s", "dur", False),
                ("Senast", lambda d: ago(d["last"]), "last", False),
                ("Max SNR", lambda d: f"{d['max_snr']:.0f} dB" if d["max_snr"] > -99 else "–", "max_snr", False),
            ]
            (self.hit_store, self.hit_cv, self.hit_sel, _f, _c, sw) = make_table(cols)
            self.hit_rows = {}
            self._hits_gen = -1
            self.hit_cv.connect("activate", lambda cv, pos: self._tune_hit(self.hit_sel.get_item(pos)))
            v.append(sw)
            nb.append_page(v, Gtk.Label(label="Skanner"))

            # Avkodat
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            v.set_margin_top(6)
            v.set_margin_start(6)
            v.set_margin_end(6)
            tb = Gtk.Box(spacing=6)
            self.dec_filter = Gtk.SearchEntry(hexpand=True)
            self.dec_filter.set_placeholder_text("Sök i avkodade meddelanden (registrering, MMSI, anropssignal, text …)")
            self.dec_filter.connect("search-changed", lambda *_: self.dec_cfilter.changed(Gtk.FilterChange.DIFFERENT))
            tb.append(self.dec_filter)
            for label, cb in (("Exportera CSV", self._export_decoded), ("Rensa", self._clear_decoded)):
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda *_, cb=cb: cb())
                tb.append(b)
            self.dec_count = Gtk.Label(label="0 meddelanden")
            self.dec_count.add_css_class("dim-label")
            tb.append(self.dec_count)
            v.append(tb)
            cols = [
                ("Tid", lambda d: time.strftime("%H:%M:%S", time.localtime(d["time"])), "time", False),
                ("Frekvens", lambda d: fmt_mhz(d["freq"]), "freq", False),
                ("Typ", lambda d: d["type"], "type", False),
                ("Avsändare", lambda d: d["src"], "src", False),
                ("Namn / flight / väg", lambda d: d["name"], "name", False),
                ("Kategori", lambda d: d["kind"], "kind", False),
                ("Position", lambda d: f"{d['pos'][0]:.4f}, {d['pos'][1]:.4f}" if d.get("pos") else "", "pos", False),
                ("Text", lambda d: d["text"][:200], "text", True),
            ]
            (self.dec_store, self.dec_cv, self.dec_sel, self.dec_cfilter, dcols, sw) = make_table(
                cols, self._dec_filter_func)
            self.dec_cv.connect("activate", lambda cv, pos: self._show_decoded(self.dec_sel.get_item(pos)))
            self.dec_cv.sort_by_column(dcols[0], Gtk.SortType.DESCENDING)
            v.append(sw)
            self.dec_tab_label = Gtk.Label(label="Avkodat")
            nb.append_page(v, self.dec_tab_label)

            # Signalanalys
            self.an_panel = AnalysPanel(self)
            self.an_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.an_holder.append(self.an_panel)
            self.an_placeholder = Adw.StatusPage(title="Signalanalysen visas i ett eget fönster",
                                                 icon_name="window-new-symbolic")
            bb = Gtk.Button(label="Hämta tillbaka hit", halign=Gtk.Align.CENTER)
            bb.add_css_class("pill")
            bb.connect("clicked", lambda *_: self.toggle_analys_window())
            self.an_placeholder.set_child(bb)
            self.an_tab_label = Gtk.Label(label="Signalanalys")
            self.an_page = nb.append_page(self.an_holder, self.an_tab_label)
            nb.connect("switch-page", self._on_bottom_page)
            return nb

        # ── signalanalys ──
        def skin(self):
            return SKINS.get(self.conf.get("skin", "spanare"), SKINS["spanare"])

        def analys_shown(self):
            return self.an_win is not None or self.bottom_nb.get_current_page() == self.an_page

        def _on_bottom_page(self, nb, page, num):
            if num == self.an_page and self.an_win is None:
                self.an_panel.open_on_tuned()

        def carrier_at(self, f, tol=None):
            """Detekterad bärvåg (eller tydlig topp i spektrumet) nära f. Returnerar (mitt, bandbredd) eller None."""
            lo, span = self.view.view()
            hz_px = span / max(1, self.view.get_width())
            tol = tol or max(hz_px * 6, 300)
            best = None
            for t in self.active_tracks:
                if t["f"] - t["bw"] / 2 - tol <= f <= t["f"] + t["bw"] / 2 + tol:
                    dd = abs(t["f"] - f)
                    if best is None or dd < best[2]:
                        best = (t["f"], max(t["bw"], 300.0), dd)
            if best:
                return best[0], best[1]
            disp = self.view.disp
            if disp is None:
                return None
            c = self.wf_center
            binhz = self.view.wf_span / FFT_N
            k = int((f - c) / binhz + FFT_N / 2)
            r = max(2, int(tol / binhz))
            k0, k1 = max(0, k - r), min(FFT_N, k + r + 1)
            if k1 - k0 < 3:
                return None
            kp = k0 + int(np.argmax(disp[k0:k1]))
            floor = float(np.median(disp))
            pk = float(disp[kp])
            if pk < floor + 8:
                return None
            thr = max(floor + 3, pk - 25)
            a = kp
            while a > 0 and disp[a - 1] > thr:
                a -= 1
            b = kp
            while b < FFT_N - 1 and disp[b + 1] > thr:
                b += 1
            kk = np.arange(a, b + 1)
            pw = 10 ** (disp[a:b + 1] / 10)
            fcen = c + float(((kk - FFT_N / 2) * binhz * pw).sum() / pw.sum())
            return fcen, max((b - a + 1) * binhz, 300.0)

        def tune_flagga(self, fl):
            """Stäm in en flagga: flaggans läge (Kiwins namn översatta), och analysen riktas mot själva signalen.
            Okänt läge under 30 MHz blir USB/LSB efter bandet – aldrig ett kvarglömt WFM/NFM på kortvåg."""
            f = float(fl["f"])
            m = flaggor.program_lage(fl.get("lage"), MODES)
            if m is None and f < 30e6 and self.engine.mode in ("WFM", "NFM"):
                m = ssb_zone(f)[0]
            self.tune(f, m)
            m = self.engine.mode
            # signalen i förhållande till flaggan: för USB/LSB står flaggan på den inställda frekvensen
            mat = True
            if m in ("USB", "LSB"):
                # flaggans kanal: talbandet 300–3000 Hz från den inställda frekvensen. Den används som den är –
                # en bandbreddsmätning i en talpaus eller svag signal hittar annars bara en smal bit brus
                c, g, mat = (f + 1650.0 if m == "USB" else f - 1650.0), 2700.0, False
            else:
                c, g = f, min(DEFAULT_BW.get(m, 3000), 12000.0 if f < 30e6 else 250e3)
            ap = self.an_panel
            if ap.follow.get_active():
                ap.follow_tuned()                # håll Följ inställd i takt
            ap.set_target(c, g, fit=mat)

        def analyse(self, f, bw, show=True):
            if self.an_panel.follow.get_active():
                self.an_panel.follow.set_active(False)
            self.an_panel.set_target(f, bw)
            if show and self.an_win is None and self.bottom_nb.get_current_page() != self.an_page:
                self.bottom_nb.set_current_page(self.an_page)
            if show and self.an_win is None:
                rp = self.right_paned
                H = rp.get_height()
                want = min(370, int(H * 0.55))
                if not self._bottom_grown and H > 400 and H - rp.get_position() < want:
                    rp.set_position(H - want)      # ge analysen plats (en gång – sen bestämmer användaren)
                self._bottom_grown = True
            if not self.engine.running:
                self.toast("Starta mottagningen för att analysera signalen")
            self.view.queue_draw()

        def toggle_analys_window(self):
            p = self.an_panel
            self.set_focus(None)
            if self.an_win is not None:
                self.an_win.set_focus(None)
            if self.an_win is None:
                self.an_holder.remove(p)
                self.an_holder.append(self.an_placeholder)
                win = Adw.Window(title="Signalanalys – Signalspanaren", transient_for=self,
                                 default_width=self.conf.get("an_win", [760, 520])[0],
                                 default_height=self.conf.get("an_win", [760, 520])[1])
                tv = Adw.ToolbarView()
                tv.add_top_bar(Adw.HeaderBar())
                tv.set_content(p)
                win.set_content(tv)
                win.connect("close-request", lambda *_: self.toggle_analys_window() or True)
                self.an_win = win
                p.detach_btn.set_icon_name("view-restore-symbolic")
                p.detach_btn.set_tooltip_text("Lägg tillbaka analysen som flik")
                win.present()
            else:
                win = self.an_win
                self.conf["an_win"] = [win.get_width(), win.get_height()]
                win.get_content().set_content(None)
                self.an_win = None
                self.an_holder.remove(self.an_placeholder)
                self.an_holder.append(p)
                p.detach_btn.set_icon_name("window-new-symbolic")
                p.detach_btn.set_tooltip_text("Visa analysen i ett eget fönster")
                win.destroy()
                self.bottom_nb.set_current_page(self.an_page)

        # ── frekvens / läge ──
        def step(self):
            s = STEPS[self.conf["step_idx"]] if 0 <= self.conf["step_idx"] < len(STEPS) else 0
            return s or DEFAULT_STEP[self.engine.mode]

        def _set_step_idx(self, i):
            self._set_conf("step_idx", i)
            for w in (getattr(self, "step_row", None), getattr(self, "wf_step_dd", None)):
                if w is not None and w.get_selected() != i:
                    w.set_selected(i)

        def nudge_freq(self, k, st=None):
            if not k:
                return
            st = st or self.step()
            q = self.freq / st
            n = round(q)
            if abs(q - n) >= 1e-6:
                n = math.floor(q) if k > 0 else math.ceil(q)
            self.tune((n + k) * st)

        def tune(self, freq, mode=None, keep_center=False):
            e = self.engine
            freq = float(max(1e4, freq))
            playing = e.running and getattr(e.source, "fixed_center", False)
            kiwi = e.running and isinstance(e.source, KiwiSource)
            if kiwi and freq > kiwisdr.KIWI_MAX_HZ:
                self.toast("KiwiSDR tar bara emot 0–30 MHz")
                return
            if playing or kiwi or self.conf.get("source") == "kiwi":
                pass                                         # uppspelning/Kiwi: direktsampling spelar ingen roll
            elif self.conf.get("direct") and freq > 28.8e6 >= self.freq:
                self.toast("Direktsampling är på – den tar bara emot under 28,8 MHz. Stäng av den för VHF/UHF.")
            elif not self.conf.get("direct") and freq < 24e6 <= self.freq:
                self.toast("Under 24 MHz behövs direktsampling (Mottagare → Direktsampling → Q-gren)")
            if not mode and e.mode in ("USB", "LSB") and self.conf.get("auto_sideband", True) \
                    and not getattr(self.scanner, "active", False):
                side, zon = ssb_zone(freq)
                if getattr(self, "_ssb_manual_zone", None) != zon:
                    self._ssb_manual_zone = None             # handvalt sidband gäller bara i sin zon
                    if side != e.mode:
                        bw = e.bw
                        mode = side
                        GLib.idle_add(lambda s=side: self.toast(
                            f"{s} – brukligt här" + (" (tal under 10 MHz)" if s == "LSB" else "")) or False)
                        self._ssb_keep_bw = bw
            if mode and mode in MODES and mode != e.mode:
                self._in_tune = True
                try:
                    self.set_mode(mode)
                    kb = getattr(self, "_ssb_keep_bw", None)
                    if kb and mode in ("USB", "LSB"):         # USB<->LSB: behåll vald bandbredd
                        e.bw = kb
                        self.conf["bw"] = kb
                        self._updating = True
                        self.bw_row.set_value(kb)
                        self._updating = False
                finally:
                    self._in_tune = False
                    self._ssb_keep_bw = None
            d = freq - e.center
            ok_offset = abs(d) <= SR * 0.42 and abs(d) >= e.bw / 2 + 3000
            if e.running and getattr(e.source, "fixed_center", False):
                if abs(d) > SR * 0.48:                      # filen styr mitten – kan inte stämma utanför den
                    self.toast(f"Utanför inspelningen ({(e.center - SR / 2) / 1e6:.3f}–"
                               f"{(e.center + SR / 2) / 1e6:.3f} MHz)")
                    freq = e.center + math.copysign(SR * 0.48, d)
                    d = freq - e.center
                ok_offset = True
            if kiwi:
                # Kiwin skickar bara ca 12 kHz runt mitten (utanför ±0,415 × takten fyller vi på med
                # brus): flytta mitten (Kiwin stämmer om) när kanalen inte längre ryms. USB/LSB ligger
                # helt på ena sidan om den inställda frekvensen, så räkna med kanalens verkliga kanter
                # och lägg kanalens mitt (inte bärvågen) i Kiwins mitt.
                lo, hi = e._chan_bounds(e.mode, e.bw, d)
                ok_offset = max(abs(lo), abs(hi)) <= e.source.narrow_rate * 0.40
                if not ok_offset:
                    mid = (lo + hi) / 2 - d                  # kanalens mitt relativt den inställda frekvensen
                    if abs(e.center - (freq + mid)) > 1:     # redan centrerad (bredare än Kiwins band)?
                        e.set_center(freq + mid)
                    d = freq - e.center
                    ok_offset = True
            if not (ok_offset or keep_center):
                newc = freq + SR * 0.15
                e.set_center(newc)
                d = freq - newc
            e.offset = d
            e.reset_squelch()
            self.freq = freq
            self.conf["freq"] = freq
            self._update_freq_label()
            self.view.queue_draw()

        # ── flaggor ──
        def _flaggor_andrade(self):
            self.view.queue_draw()
            ap = getattr(self, "an_panel", None)
            if ap is not None and getattr(ap, "last", None):
                try:
                    ap._update_rows(ap.last)
                except Exception:
                    log.exception("Kunde inte uppdatera analysen efter flaggändring")
            r = getattr(self, "flagg_status_row", None)
            if r is not None:
                fl = self.flaggor
                n_egna = sum(1 for x in fl.egna if x.get("kalla") != "dold")
                n_kiwi = len(fl.kiwi.get(fl.kiwi_aktiv, [])) if fl.kiwi_aktiv else 0
                n_ref = len(fl.kiwi.get(fl.ref, [])) if fl.ref else 0
                r.set_subtitle(f"{n_egna} egna, {n_kiwi} från Kiwin, {n_ref} från referensen"
                               + (f" – {fl.status}" if fl.status else ""))
            return False

        def _on_flaggor_kiwi(self, v):
            self.conf["flaggor_kiwi"] = v
            src = self.engine.source
            if v and self.engine.running and isinstance(src, KiwiSource):
                self.flaggor.satt_kiwi(webbmottagare.norm_url(src.addr), src.addr)
            self.view.queue_draw()

        def _on_flaggor_ref(self, v):
            self.conf["flaggor_ref"] = v
            if v:
                self.flaggor.satt_referens(webbmottagare.norm_url(self._ref_addr), self._ref_addr)
            else:
                self.flaggor.satt_referens(None, None)

        def _flaggor_hamta_om(self):
            src = self.engine.source
            if not (self.engine.running and isinstance(src, KiwiSource)):
                self.toast("Starta mottagningen med en KiwiSDR som källa först")
                return
            if not self.flaggor.hamta(webbmottagare.norm_url(src.addr), src.addr):
                self.toast("Kiwins flaggor hämtas när du stoppar lyssningen")

        def flagg_meny(self, widget, x, y, f_hz, fl):
            """Högerklick i spektrumet: lägg till/ändra/ta bort flagga."""
            pop = Gtk.Popover()
            pop.set_parent(widget)
            r = Gdk.Rectangle()
            r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
            pop.set_pointing_to(r)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            def knapp(text, cb):
                b = Gtk.Button(label=text)
                b.add_css_class("flat")
                b.connect("clicked", lambda *_: (pop.popdown(), cb()))
                box.append(b)
            st = self.step()
            f_r = round(f_hz / st) * st
            if fl is not None:
                box.append(Gtk.Label(label=f"<b>{GLib.markup_escape_text(fl.get('namn', ''))}</b>  "
                                           f"{fl['f'] / 1e3:.2f} kHz {fl.get('lage', '')}", use_markup=True))
                if fl.get("not"):
                    box.append(Gtk.Label(label=fl["not"][:80], wrap=True, xalign=0, css_classes=["dim-label"]))
                knapp("Stäm in", lambda: self.tune_flagga(fl))
                knapp("Ändra flagga …", lambda: self.flagg_dialog(fl, fl["f"]))
                if fl.get("_egen"):
                    knapp("Ta bort flagga", lambda: self.flaggor.ta_bort(fl["id"]))
                else:
                    knapp("Dölj referensens flagga" if fl.get("_ref") else "Dölj Kiwins flagga",
                          lambda: self.flaggor.dolj_kiwi(fl))
                box.append(Gtk.Separator())
            knapp(f"Lägg till flagga på {f_r / 1e6:.4f} MHz …", lambda: self.flagg_dialog(None, f_r))
            pop.set_child(box)
            pop.connect("closed", lambda p_: p_.unparent())
            pop.popup()

        def flagg_dialog(self, fl, f_hz, forslag=None):
            """Lägg till (fl=None) eller ändra en flagga. En ändrad Kiwi-flagga sparas som egen kopia.
            forslag: förifyllda värden för en ny flagga (t.ex. från signalanalysen)."""
            ny = fl is None
            egen = bool(fl and fl.get("_egen"))
            win = Adw.Window(title="Ny flagga" if ny else "Ändra flagga", modal=True, transient_for=self,
                             default_width=420)
            tv = Adw.ToolbarView()
            tv.add_top_bar(Adw.HeaderBar())
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            for m in ("top", "bottom", "start", "end"):
                getattr(box, f"set_margin_{m}")(12)
            g = Adw.PreferencesGroup()
            if fl and not egen:
                g.set_description("Kiwins flagga ändras inte på Kiwin – ändringen sparas som din egen flagga "
                                  "som visas i stället för Kiwins.")
            fv = fl or forslag or {}
            namn = Adw.EntryRow(title="Namn")
            namn.set_text(fv.get("namn", ""))
            g.add(namn)
            frek = Adw.EntryRow(title="Frekvens (kHz)")
            frek.set_text(f"{float(f_hz) / 1e3:.3f}")
            g.add(frek)
            lagen = flaggor.LAGEN
            cur_l = fv.get("lage") or self.engine.mode
            lage = Adw.ComboRow(title="Läge", model=Gtk.StringList.new(lagen))
            lage.set_selected(lagen.index(cur_l) if cur_l in lagen else 0)
            g.add(lage)
            typer = flaggor.TYPER
            cur_t = flaggor.typ_for(fv) if fv else "Övrigt"
            typ = Adw.ComboRow(title="Typ", model=Gtk.StringList.new(typer))
            typ.set_selected(typer.index(cur_t) if cur_t in typer else len(typer) - 1)
            g.add(typ)
            noter = Adw.EntryRow(title="Anteckning")
            noter.set_text(fv.get("not", ""))
            g.add(noter)
            tider = Adw.EntryRow(title="Sänder (UTC hhmm–hhmm)")
            tider.set_text(f"{int((fl or {}).get('b', 0)):04d}-{int((fl or {}).get('e', 2400)):04d}")
            g.add(tider)
            box.append(g)
            btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
            avb = Gtk.Button(label="Avbryt")
            avb.connect("clicked", lambda *_: win.close())
            spara = Gtk.Button(label="Spara")
            spara.add_css_class("suggested-action")
            btns.append(avb)
            btns.append(spara)
            box.append(btns)

            def on_spara(*_):
                try:
                    f_new = float(frek.get_text().replace(",", ".")) * 1e3
                    bt, et = (tider.get_text().replace("–", "-").split("-") + ["2400"])[:2]
                    b_, e_ = int(bt or 0), int(et or 2400)
                except ValueError:
                    self.toast("Frekvens eller tid gick inte att tolka")
                    return
                kw = dict(namn=namn.get_text().strip() or "Flagga", lage=lagen[lage.get_selected()],
                          typ=typer[typ.get_selected()], noter=noter.get_text(), b=b_, e=e_)
                if egen:
                    self.flaggor.andra(fl["id"], f=f_new, namn=kw["namn"], lage=kw["lage"], typ=kw["typ"],
                                       **{"not": kw["noter"]}, b=b_, e=e_)
                else:
                    self.flaggor.lagg_till(f_new, ersatter=flaggor.kiwi_nyckel(fl) if fl else None, **kw)
                log.info("Flagga %s: %s %.3f kHz %s", "ny" if ny else "ändrad", kw["namn"], f_new / 1e3, kw["typ"])
                win.close()
            spara.connect("clicked", on_spara)
            tv.set_content(box)
            win.set_content(tv)
            win.present()

        def _check_sideband(self):
            """Vid start: byt till brukligt sidband om det sparade läget är fel här (om inte handvalt)."""
            e = self.engine
            if e.mode in ("USB", "LSB") and self.conf.get("auto_sideband", True) \
                    and getattr(self, "_ssb_manual_zone", None) is None:
                side, _z = ssb_zone(self.freq)
                if side != e.mode:
                    self.tune(self.freq)                     # tune() byter sidband och visar en notis

        def _refit_kiwi(self):
            """Nytt läge/ny bandbredd med KiwiSDR: stäm om Kiwin om kanalen hamnat utanför dess band."""
            e = self.engine
            if e.running and isinstance(e.source, KiwiSource):
                self.tune(self.freq)

        def remote_tune(self, f, m):
            GLib.idle_add(lambda: self.tune(f, m) or False)

        def set_center_keep(self, c):
            e = self.engine
            e.set_center(c)
            c = e.center                                     # oförändrad vid uppspelning
            d = self.freq - c
            if e.running and isinstance(e.source, KiwiSource):
                lo, hi = e._chan_bounds(e.mode, e.bw, d)
                if max(abs(lo), abs(hi)) > e.source.narrow_rate * 0.40:
                    d = d - (lo + hi) / 2                    # kanalen hamnar mitt i Kiwins band
                    self.freq = c + d
            if abs(d) > SR * 0.45:
                self.freq = c + math.copysign(SR * 0.45, d)
                d = self.freq - c
            e.offset = d
            self._update_freq_label()

        def set_mode(self, mode):
            e = self.engine
            e.mode = mode
            e.bw = DEFAULT_BW[mode]
            self.conf["mode"] = mode
            self.conf["bw"] = None
            self._updating = True
            self.mode_dd.set_selected(MODES.index(mode))
            self.bw_row.set_value(e.bw)
            self._updating = False
            if not getattr(self, "_in_tune", False):
                if mode in ("USB", "LSB"):                   # användaren valde sidband själv
                    side, zon = ssb_zone(self.freq)
                    self._ssb_manual_zone = zon if mode != side else None
                    if mode != side:
                        self.toast(f"Obs: på {self.freq / 1e6:.3f} MHz används normalt {side} – "
                                   f"fel sidband låter som obegripligt tal")
                self._refit_kiwi()                           # frontpanel, skannerförval m.fl.
            self.view.queue_draw()

        def set_zoom(self, z):
            if int(z) != self.conf["zoom"] and self.engine.running:
                self._relevel_at = self.engine.stats["blocks"] + 8   # nytt brusgolv vid ny upplösning
            self.conf["zoom"] = max(1, int(z))
            if int(self.zoom_row.get_value()) != self.conf["zoom"]:
                self.zoom_row.set_value(self.conf["zoom"])
            self.view.queue_draw()

        def zoom_spectrum(self, factor):
            """factor > 1 zoomar in, < 1 zoomar ut, 0 återställer."""
            if factor == 0:
                self.set_zoom(1)
                return
            z = self.conf["zoom"]
            z = min(128, z * 2) if factor > 1 else max(1, z // 2)
            self.set_zoom(z)

        def pan_spectrum(self, direction):
            """Flytta visat frekvensområde åt vänster/höger (direction = -1/1)."""
            z = max(1, int(self.conf.get("zoom", 1)))
            span = SR / z
            try:
                self.set_center_keep(self.engine.center + direction * span * 0.25)
            except Exception:
                pass
            self.view.queue_draw()

        def auto_level(self):
            if self.latest_db is None:
                self.toast("Ingen signal ännu – starta mottagningen först")
                return
            src = self.view.disp if self.view.disp is not None else self.latest_db
            floor = float(np.median(src))
            self.dbmin_row.set_value(round(floor - 6))
            self.dbmax_row.set_value(round(floor + 40))

        def _update_freq_label(self):
            if not self.freq_entry.has_focus():
                self.freq_entry.set_text(fmt_freq(self.freq))

        # ── händelser ──
        def _set_conf(self, k, v):
            self.conf[k] = v
            self.view.queue_draw()

        def _on_freq_entry(self, entry):
            try:
                f = parse_freq(entry.get_text())
            except ValueError as ex:
                self.toast(str(ex))
                return
            self.tune(f)
            self.view.grab_focus()
            self.freq_entry.set_text(fmt_freq(self.freq))

        def _on_mode_dd(self, dd, _p):
            if self._updating:
                return
            self.set_mode(MODES[dd.get_selected()])

        def _on_bw(self, v):
            if self._updating:
                return
            self.engine.bw = float(v)
            self.conf["bw"] = float(v)
            if self.conf.get("pbt_lo") or self.conf.get("pbt_hi"):
                self._pbt_reset()                            # ny bredd nollställer PBT
            self._refit_kiwi()
            self.view.queue_draw()

        def _on_squelch(self, v):
            self.engine.squelch = v
            self.conf["squelch"] = v

        def _set_agc(self, k, v):
            self.conf[k] = float(v)
            setattr(self.engine, k, float(v))
            if k == "agc_decay" and hasattr(self, "agc_preset_row") and not getattr(self, "_agc_loading", False):
                self._agc_loading = True
                self.agc_preset_row.set_selected(self._agc_preset_idx(v))
                self._agc_loading = False

        @staticmethod
        def _agc_preset_idx(decay):
            for i, (_n, d) in enumerate(AGC_PRESETS):
                if d is not None and abs(d - float(decay)) < 0.01:
                    return i
            return len(AGC_PRESETS) - 1

        def _on_agc_preset(self, i):
            d = AGC_PRESETS[i][1]
            if d is None or getattr(self, "_agc_loading", False):
                return
            self._agc_loading = True
            self.agc_decay_row.set_value(round(d, 2))
            self._agc_loading = False

        # ── DSP-filter (dspfilter.py) ──
        def _apply_dsp_conf(self):
            """Kopierar DSP-inställningarna från conf till motorn."""
            c, e = self.conf, self.engine
            e.shape = c.get("dsp_shape", "SHARP")
            e.pbt_lo, e.pbt_hi = float(c.get("pbt_lo", 0.0)), float(c.get("pbt_hi", 0.0))
            e.nb_on = bool(c.get("nb_on"))
            e.nb.level, e.nb.depth, e.nb.width = int(c.get("nb_level", 50)), int(c.get("nb_depth", 8)), int(c.get("nb_width", 20))
            e.snr_proc.nr = bool(c.get("nr_on"))
            e.snr_proc.nr_level = int(c.get("nr_level", 6))
            e.snr_proc.notch = bool(c.get("anotch_on"))
            e.mnotch_on = bool(c.get("mnotch_on"))
            e.mnotch_freq = float(c.get("mnotch_freq", 1000.0))
            e.mnotch_width = c.get("mnotch_width", "M")
            e.tone = {"hpf": int(c.get("tone_hpf", 0)), "lpf": int(c.get("tone_lpf", 0)),
                      "bass": int(c.get("tone_bass", 0)), "treble": int(c.get("tone_treble", 0))}

        def _dsp_set(self, k, v):
            self.conf[k] = v
            self._apply_dsp_conf()
            if k in ("pbt_lo", "pbt_hi"):
                self._refit_kiwi()
            self.view.queue_draw()

        def _pbt_reset(self, *_):
            for r in (getattr(self, "pbt_lo_row", None), getattr(self, "pbt_hi_row", None)):
                if r is not None:
                    r.set_value(0)
            self.conf["pbt_lo"] = self.conf["pbt_hi"] = 0.0
            self._apply_dsp_conf()

        def _on_fil(self, i):
            if getattr(self, "_fil_loading", False):
                return
            lst = dspfilter.FIL_PRESETS.get(self.engine.mode)
            if lst:
                self.bw_row.set_value(lst[i])            # → _on_bw (nollställer PBT)

        def _open_dsp(self, *_):
            """Visar/döljer DSP-panelen. Den svävar ovanpå den nedre delen av fönstret (till höger över
            flikarna), så spektrum och vattenfall syns hela tiden, och allt annat går att använda samtidigt."""
            p = self.dsp_panel
            if p is None:
                outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                head = Gtk.Box(spacing=6)
                for m in ("top", "start", "end"):
                    getattr(head, f"set_margin_{m}")(8)
                head.append(Gtk.Label(label="<b>DSP-filter</b>", use_markup=True, xalign=0, hexpand=True))
                close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Dölj DSP-panelen")
                close.add_css_class("flat")
                close.connect("clicked", lambda *_: self._open_dsp())
                head.append(close)
                outer.append(head)
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
                for m in ("top", "bottom", "start", "end"):
                    getattr(box, f"set_margin_{m}")(10)
                self._build_dsp_rows(box, self.conf)
                sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
                sc.set_child(box)
                outer.append(sc)
                outer.set_size_request(390, -1)
                outer.set_halign(Gtk.Align.END)
                outer.set_valign(Gtk.Align.FILL)
                outer.set_margin_end(10)
                outer.set_margin_top(6)
                outer.set_margin_bottom(6)
                outer.add_css_class("card")
                outer.add_css_class("dsp-panel")
                self.dsp_panel = p = outer
                self.bottom_overlay.add_overlay(p)
                p.set_visible(False)
            show = not p.get_visible()
            p.set_visible(show)
            if show:
                self._sync_fil_row()
            self.conf["dsp_open"] = show

        def _sync_fil_row(self):
            """Visa vilket FIL-förval som motsvarar nuvarande bandbredd (om något)."""
            r = getattr(self, "fil_row", None)
            lst = dspfilter.FIL_PRESETS.get(self.engine.mode)
            if r is None or not lst:
                return
            self._fil_loading = True
            i = min(range(3), key=lambda k: abs(lst[k] - float(self.engine.bw)))
            r.set_selected(i)
            self._fil_loading = False

        def _build_dsp_rows(self, box, c):
            g = self._section(None, "Digitala mottagningsfilter för AM/SSB/CW.")
            self.fil_row = self._combo(g, "Filterförval", ["FIL1 – bred", "FIL2 – mellan", "FIL3 – smal"], 0, self._on_fil)
            self.fil_row.set_tooltip_text("SSB 3,0/2,4/1,8 kHz, CW 1,2/0,5/0,25 kHz, AM 9/6/3 kHz")
            shapes = list(dspfilter.SHAPES)
            self._combo(g, "Filterform", ["SHARP – branta kanter", "SOFT – mjukare ljud"],
                        shapes.index(c.get("dsp_shape", "SHARP")) if c.get("dsp_shape") in shapes else 0,
                        lambda i: self._dsp_set("dsp_shape", shapes[i]))
            self.pbt_lo_row = self._spin(g, "PBT nedre kant (Hz)", -1500, 1500, 50, c.get("pbt_lo", 0.0), 0,
                                         lambda v: self._dsp_set("pbt_lo", float(v)),
                                         "Flyttar filtrets nedre kant – skär bort störningar under signalen")
            self.pbt_hi_row = self._spin(g, "PBT övre kant (Hz)", -1500, 1500, 50, c.get("pbt_hi", 0.0), 0,
                                         lambda v: self._dsp_set("pbt_hi", float(v)),
                                         "Flyttar filtrets övre kant. Båda lika = flytta hela passbandet")
            self._button(g, "Nollställ PBT", self._pbt_reset)
            self._switch(g, "Brusreducering (NR)", c.get("nr_on", False), lambda v: self._dsp_set("nr_on", v))
            self._spin(g, "NR-nivå", 1, 15, 1, c.get("nr_level", 6), 0, lambda v: self._dsp_set("nr_level", int(v)),
                       "Högre = mer dämpning av bruset (men risk för ”undervattensljud”)")
            self._switch(g, "Auto-notch (AN)", c.get("anotch_on", False), lambda v: self._dsp_set("anotch_on", v),
                         "Tar bort stillastående toner (bärvågor, visslingar)")
            self._switch(g, "Manuell notch (MN)", c.get("mnotch_on", False), lambda v: self._dsp_set("mnotch_on", v))
            self._spin(g, "Notch-frekvens (Hz, i ljudet)", 50, 5000, 10, c.get("mnotch_freq", 1000.0), 0,
                       lambda v: self._dsp_set("mnotch_freq", float(v)))
            widths = list(dspfilter.NOTCH_WIDTHS)
            self._combo(g, "Notch-bredd", ["W – bred", "M – mellan", "N – smal"],
                        widths.index(c.get("mnotch_width", "M")) if c.get("mnotch_width") in widths else 1,
                        lambda i: self._dsp_set("mnotch_width", widths[i]))
            self._switch(g, "Störningsblankare (NB)", c.get("nb_on", False), lambda v: self._dsp_set("nb_on", v),
                         "Mot pulsstörningar (tändning, elstängsel). Ha den av annars")
            self._spin(g, "NB-nivå", 1, 100, 1, c.get("nb_level", 50), 0, lambda v: self._dsp_set("nb_level", int(v)),
                       "Högre = blankar svagare pulser")
            self._spin(g, "NB-djup", 1, 10, 1, c.get("nb_depth", 8), 0, lambda v: self._dsp_set("nb_depth", int(v)),
                       "Hur mycket pulsen dämpas (10 = helt)")
            self._spin(g, "NB-bredd", 1, 100, 1, c.get("nb_width", 20), 0, lambda v: self._dsp_set("nb_width", int(v)),
                       "Blankningstid, 10 µs per steg")
            self._spin(g, "RX HPF (Hz)", 0, 2000, 100, c.get("tone_hpf", 0), 0, lambda v: self._dsp_set("tone_hpf", int(v)),
                       "0 = av. När HPF/LPF används nollas bas/diskant")
            self._spin(g, "RX LPF (Hz)", 0, 5000, 100, c.get("tone_lpf", 0), 0, lambda v: self._dsp_set("tone_lpf", int(v)),
                       "0 = av")
            self._spin(g, "RX bas", -5, 5, 1, c.get("tone_bass", 0), 0, lambda v: self._dsp_set("tone_bass", int(v)),
                       "2 dB per steg")
            self._spin(g, "RX diskant", -5, 5, 1, c.get("tone_treble", 0), 0, lambda v: self._dsp_set("tone_treble", int(v)))
            box.append(g)

        def _on_volume(self, v):
            self.engine.volume = v / 100
            self.conf["volume"] = v

        def _on_source(self, i):
            self.conf["source"] = SOURCES[i]
            self._show_source_rows(SOURCES[i])
            if self.engine.running:
                self.toast("Ny källa används nästa gång du startar")
            elif SOURCES[i] == "file" and not self.conf.get("play_file"):
                self._choose_play_file()

        def _show_source_rows(self, src):
            self._section_visible(self.play_group, src == "file")
            self.tcp_row.set_visible(src == "tcp")
            self.kiwi_row.set_visible(src == "kiwi")
            for i, r in enumerate(self.hw_rows):
                # KiwiSDR: förstärkningen (Kiwins AGC eller fast) och frekvenskorrigeringen (per Kiwi)
                r.set_visible(src != "file" and (src != "kiwi" or i < 4))
            self._refresh_ppm_row()
            self._refresh_gain_rows()

        # ── uppspelning av IQ-fil ──
        def _playing(self):
            s = self.engine.source
            return s if (self.engine.running and isinstance(s, iqfil.FileSource)) else None

        def _choose_play_file(self):
            dlg = Gtk.FileDialog(title="Välj IQ-fil att spela upp")
            flt = Gtk.FileFilter()
            flt.set_name("IQ-filer")
            for pat in ("*.sigmf-meta", "*.sigmf-data", "*.wav", "*.WAV", "*.cu8", "*.raw", "*.bin", "*.iq",
                        "*.cs8", "*.ci8", "*.cs16", "*.ci16", "*.cf32", "*.cfile", "*.fc32"):
                flt.add_pattern(pat)
            alla = Gtk.FileFilter()
            alla.set_name("Alla filer")
            alla.add_pattern("*")
            fl = Gio.ListStore.new(Gtk.FileFilter)
            fl.append(flt)
            fl.append(alla)
            dlg.set_filters(fl)
            dlg.set_default_filter(flt)
            cur = self.conf.get("play_file")
            try:
                dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(cur) if cur and os.path.exists(cur)
                                                             else rec_dir()))
            except Exception:
                pass

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:
                    return
                self._set_play_file(f.get_path())
            dlg.open(self, None, done)

        def _set_play_file(self, path, ask=True):
            try:
                r = iqfil.IQReader(path)
            except Exception as ex:
                log.error("Kunde inte läsa IQ-filen %s: %s", path, ex)
                self.toast(f"Kunde inte läsa filen: {ex}", timeout=8)
                return
            ov = self.conf.setdefault("play_override", {}).get(path, {})
            if ask and (not (r.rate or ov.get("rate")) or not (r.center_at(0) or ov.get("center"))):
                self._ask_play_params(path, r, ov)
                return
            self.conf["play_file"] = path
            log.info("IQ-fil vald: %s (%s, %s S/s, %.1f s)", path, r.info, r.rate or ov.get("rate"), r.seconds())
            self._update_play_info()
            if self.engine.running and isinstance(self.engine.source, iqfil.FileSource):
                self.btn_start.set_active(False)
                self.btn_start.set_active(True)

        def _ask_play_params(self, path, r, ov):
            """Filen saknar takt och/eller mittfrekvens (vanligt för rå .cu8/.cf32) – fråga användaren."""
            gf, gr = iqfil.guess_from_name(path)
            dlg = Adw.MessageDialog(transient_for=self, heading="Uppgifter saknas för IQ-filen",
                                    body=f"{os.path.basename(path)}\n\nRåa IQ-filer innehåller inte samplingstakt "
                                         "och mittfrekvens. Ange dem (rtl_sdr använder ofta 2048000 eller 2400000 S/s).")
            lb = Gtk.ListBox()
            lb.add_css_class("boxed-list")
            rate = Adw.EntryRow(title="Samplingstakt (S/s)")
            rate.set_text(str(int(ov.get("rate") or r.rate or gr or 2048000)))
            cen = Adw.EntryRow(title="Mittfrekvens (MHz)")
            c0 = ov.get("center") or r.center_at(0) or gf
            cen.set_text(f"{c0 / 1e6:.6f}" if c0 else "")
            lb.append(rate)
            lb.append(cen)
            dlg.set_extra_child(lb)
            dlg.add_response("cancel", "Avbryt")
            dlg.add_response("ok", "Använd")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_default_response("ok")

            def resp(_d, rid):
                if rid != "ok":
                    return
                try:
                    rv = float(rate.get_text().replace(",", ".").replace(" ", ""))
                    cv = parse_freq(cen.get_text()) if cen.get_text().strip() else 0.0
                    if not 1000 <= rv <= 100e6:
                        raise ValueError("orimlig samplingstakt")
                except ValueError as ex:
                    self.toast(f"Ogiltigt värde: {ex}")
                    return
                self.conf["play_override"][path] = {"rate": rv, "center": cv or None}
                self._set_play_file(path, ask=False)
            dlg.connect("response", resp)
            dlg.present()

        def _update_play_info(self):
            path = self.conf.get("play_file") or ""
            if not path:
                self.play_file_row.set_title("Ingen fil vald")
                self.play_file_row.set_subtitle("Spela in med ”Starta IQ-inspelning” eller välj en fil från "
                                                "t.ex. SDR++, SDR#, gqrx eller rtl_sdr")
                return
            self.play_file_row.set_title(GLib.markup_escape_text(os.path.basename(path)))
            try:
                ov = self.conf.get("play_override", {}).get(path, {})
                r = iqfil.IQReader(path, rate=ov.get("rate"), center=ov.get("center"))
                c0 = r.center_at(0)
                caps = len(r.captures)
                sub = (f"{r.info}, {r.rate / 1e3:.1f} kS/s, " if r.rate else f"{r.info}, okänd takt, ") + \
                      (f"mitt {c0 / 1e6:.4f} MHz" if c0 else "okänd mittfrekvens") + \
                      (f" ({caps} frekvenser)" if caps > 1 else "") + \
                      (f", {self._fmt_t(r.seconds())}" if r.rate else "")
                if r.rate and abs(r.rate - SR) > 1 and r.rate < SR * 0.5:
                    sub += f"\nSmal inspelning – bara ±{r.rate / 2e3:.0f} kHz runt mitten har signal"
                self.play_file_row.set_subtitle(GLib.markup_escape_text(sub))
                self.play_scale.set_range(0, max(0.1, r.seconds()))
                self.play_pos_lbl.set_text(f"0:00 / {self._fmt_t(r.seconds())}")
            except Exception as ex:
                self.play_file_row.set_subtitle(GLib.markup_escape_text(f"Kan inte läsas: {ex}"))

        @staticmethod
        def _fmt_t(t):
            t = int(t)
            return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"

        def _toggle_play_pause(self):
            s = self._playing()
            if not s:
                self.toast("Starta uppspelningen med ▶ Starta")
                return
            s.paused = not s.paused
            self.play_pause_btn.set_icon_name("media-playback-start-symbolic" if s.paused
                                              else "media-playback-pause-symbolic")

        def _on_play_seek(self, _scale, _scroll, value):
            s = self._playing()
            if s:
                s.seek(value)
                self.engine.reset_squelch()
            return False

        def _on_play_speed(self, i):
            self.conf["play_speed"] = i
            s = self._playing()
            if s:
                s.speed = {0: 1.0, 1: 2.0, 2: 4.0, 3: 8.0, 4: 0.0}.get(i, 1.0)
                s.t_next = time.monotonic()

        def _on_play_loop(self, v):
            self.conf["play_loop"] = v
            s = self._playing()
            if s:
                s.loop = v

        def _play_goto_center(self):
            s = self._playing()
            c = s.center if s else None
            if c is None:
                try:
                    c = open_iq_reader(self.conf).center_at(0)
                except SourceError as ex:
                    self.toast(str(ex))
                    return
            if c:
                self.tune(c - SR * 0.15 if (not s or (s.r.rate or 0) >= SR * 0.6) else c + 1000)

        def _play_tick(self):
            s = self._playing()
            if not s:
                return
            t, tot = s.position(), s.r.seconds()
            self.play_scale.set_value(t)
            self.play_pos_lbl.set_text(f"{self._fmt_t(t)} / {self._fmt_t(tot)}")

        def _src_set(self, k, v):
            if self.engine.running and self.engine.source:
                self.engine.source.set(k, v)

        # KiwiSDR har egen förstärkning (0–120 dB, Kiwins AGC som standard) – skild från dongelns (0–49,6)
        def _gain_keys(self, kiwi=None):
            if kiwi is None:
                kiwi = self.conf.get("source") == "kiwi"
            return ("kiwi_auto_gain", "kiwi_gain") if kiwi else ("auto_gain", "gain")

        def gain_max(self):
            return 120.0 if self.conf.get("source") == "kiwi" else 49.6

        def gain_state(self):
            """(automatisk?, förstärkning i dB, max) för den valda källan."""
            ka, kg = self._gain_keys()
            return bool(self.conf.get(ka, True)), float(self.conf.get(kg, 0.0)), self.gain_max()

        def _src_gain(self, src):
            """Värdet som ska skickas till källan src (None = källans automatik)."""
            ka, kg = self._gain_keys(isinstance(src, KiwiSource))
            return None if self.conf.get(ka, True) else float(self.conf.get(kg, 0.0))

        def _refresh_gain_rows(self):
            if not hasattr(self, "gain_row"):
                return
            auto, g, mx = self.gain_state()
            self._gain_loading = True
            self.gain_row.get_adjustment().set_upper(mx)
            self.gain_row.set_value(min(g, mx))
            self.gain_row.set_sensitive(not auto)
            self.agc_row.set_active(auto)
            self.agc_row.set_subtitle("Kiwins egen AGC (rekommenderas)" if self.conf.get("source") == "kiwi" else "")
            self._gain_loading = False

        def _gain_src_matches(self):
            """Hör den körande källan till samma sort som den valda (annars väntar ändringen till nästa start)?"""
            src = self.engine.source
            return isinstance(src, KiwiSource) == (self.conf.get("source") == "kiwi")

        def _on_auto_gain(self, v):
            if getattr(self, "_gain_loading", False):
                return
            ka, kg = self._gain_keys()
            self.conf[ka] = v
            self.gain_row.set_sensitive(not v)
            if self._gain_src_matches():
                self._src_set("gain", None if v else self.conf[kg])

        def _on_gain(self, v):
            if getattr(self, "_gain_loading", False):
                return
            ka, kg = self._gain_keys()
            self.conf[kg] = v
            if not self.conf.get(ka, True) and self._gain_src_matches():
                self._src_set("gain", v)

        def _ppm_kiwi_key(self):
            """Nyckel för Kiwins egen frekvenskorrigering (None när källan inte är en KiwiSDR)."""
            src = self.engine.source
            if self.engine.running and isinstance(src, KiwiSource):
                return webbmottagare.norm_url(src.addr)      # den Kiwi som faktiskt körs
            if self.conf.get("source") != "kiwi" or not self.conf.get("kiwi_addr", "").strip():
                return None
            return webbmottagare.norm_url(self.conf["kiwi_addr"])

        def _cur_ppm(self):
            k = self._ppm_kiwi_key()
            if k:
                return float(self.conf.get("kiwi_ppm", {}).get(k, 0.0))
            return float(self.conf["ppm"])

        def _refresh_ppm_row(self):
            """Visa RTL-dongelns ppm (heltal) eller den valda Kiwins (en decimal)."""
            if not hasattr(self, "ppm_row"):
                return
            kiwi = self._ppm_kiwi_key() is not None
            self._ppm_loading = True
            self.ppm_row.set_digits(1 if kiwi else 0)
            self.ppm_row.get_adjustment().set_step_increment(0.1 if kiwi else 1)
            self.ppm_row.set_subtitle("sparas för den här KiwiSDR:n" if kiwi else "")
            self.ppm_row.set_value(self._cur_ppm())
            self._ppm_loading = False

        def _on_ppm(self, v):
            if getattr(self, "_ppm_loading", False):
                return
            k = self._ppm_kiwi_key()
            if k:
                v = round(float(v), 1)
                d = dict(self.conf.get("kiwi_ppm") or {})
                if v:
                    d[k] = v
                else:
                    d.pop(k, None)
                self.conf["kiwi_ppm"] = d
                src = self.engine.source
                if self.engine.running and isinstance(src, KiwiSource) \
                        and webbmottagare.norm_url(src.addr) == k:
                    src.set("ppm", v)
                return
            self.conf["ppm"] = int(round(v))
            if not isinstance(self.engine.source, KiwiSource):
                self._src_set("ppm", int(round(v)))

        # ── frekvenskalibrering ──
        def _calib_measure(self, true_hz):
            """Mäter var en bärvåg på true_hz faktiskt hamnar och räknar ut korrigeringen.
            Returnerar dict, "range" (utanför fönstret) eller None (ingen data än)."""
            e = self.engine
            src = e.source
            if e.running and isinstance(src, KiwiSource):
                r = src.fine_carrier(true_hz - src.center)
                if r is None:
                    if abs(true_hz - src.center) > src.narrow_rate * 0.42:
                        return "range"
                    return None
                meas = src.center + r[0]
                apparent_ppm = (meas - true_hz) / true_hz * 1e6
                return {"snr": r[1], "offset": meas - true_hz, "apparent_ppm": apparent_ppm,
                        "suggested": self._cur_ppm() - apparent_ppm}
            avg, c, cnt = e.get_avg()
            if avg is None or c is None or cnt < 4:
                return None
            N = FFT_N
            k_exp = (true_hz - c) / BIN_HZ + N / 2
            if not (5 < k_exp < N - 5):
                return "range"
            win = 60
            k0 = int(max(1, k_exp - win))
            k1 = int(min(N - 1, k_exp + win))
            kp = k0 + int(np.argmax(avg[k0:k1]))
            floor = float(np.median(avg))
            snr = 10 * math.log10(avg[kp] / (floor + 1e-20) + 1e-20)
            d = 0.0
            if 0 < kp < N - 1:
                a = math.log(avg[kp - 1] + 1e-20)
                b = math.log(avg[kp] + 1e-20)
                cc = math.log(avg[kp + 1] + 1e-20)
                den = a - 2 * b + cc
                if den:
                    d = max(-1.0, min(1.0, 0.5 * (a - cc) / den))
            meas = c + (kp + d - N / 2) * BIN_HZ
            apparent_ppm = (meas - true_hz) / true_hz * 1e6
            return {"snr": snr, "offset": meas - true_hz, "apparent_ppm": apparent_ppm,
                    "suggested": self._cur_ppm() - apparent_ppm}

        def _open_calibration(self):
            from collections import deque as _dq
            win = Adw.Window(title="Kalibrera frekvens", modal=True, transient_for=self, default_width=470)
            tv = Adw.ToolbarView()
            tv.add_top_bar(Adw.HeaderBar())
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.set_margin_top(12)
            box.set_margin_bottom(12)
            box.set_margin_start(12)
            box.set_margin_end(12)
            info = Gtk.Label(wrap=True, xalign=0)
            info.set_markup("Ställ in mottagaren på en <b>känd, stabil bärvåg</b> – t.ex. en tidssignal "
                            "(RWM 4,996/9,996/14,996 MHz via direktsampling), en fyrsändare, en repeater "
                            "eller en mellanvågssändare. Skriv den exakta frekvensen och tryck Mät.\n"
                            "Med en KiwiSDR som källa sparas korrigeringen för just den Kiwin.")
            box.append(info)
            g = Adw.PreferencesGroup()
            fr = Adw.EntryRow(title="Känd frekvens (MHz)")
            an_r = getattr(self.an_panel, "last", None) or {}
            f_init = an_r.get("f_abs") or self.freq
            # Avrunda till närmaste jämna kHz som startförslag (standardkanaler ligger nästan alltid på jämna kHz)
            fr.set_text(fmt_mhz(round(f_init / 1000.0) * 1000.0))
            g.add(fr)
            presets = Gtk.Box(spacing=6, wrap=True if hasattr(Gtk.Box, "set_wrap") else False)
            presets_lbl = Gtk.Label(label="Snabbval:", xalign=0, css_classes=["dim-label"])
            presets.append(presets_lbl)
            for lbl, f_p in (("Närmaste kHz", round(f_init / 1000.0) * 1000.0),
                             ("RWM 4,996", 4.996e6), ("RWM 9,996", 9.996e6), ("RWM 14,996", 14.996e6)):
                pb = Gtk.Button(label=lbl)
                pb.add_css_class("flat")
                pb.connect("clicked", lambda _b, hz=f_p: (
                    meas_btn.set_active(False), fr.set_text(fmt_mhz(hz)),
                    meas_btn.set_active(True) if self.engine.running else None))
                presets.append(pb)
            box.append(presets)
            sig_row = Adw.ActionRow(title="Signal", subtitle="–")
            off_row = Adw.ActionRow(title="Uppmätt avvikelse", subtitle="–")
            sug_row = Adw.ActionRow(title="Föreslagen korrigering", subtitle="–")
            for r in (sig_row, off_row, sug_row):
                g.add(r)
            box.append(g)
            btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
            meas_btn = Gtk.ToggleButton(label="Mät")
            meas_btn.add_css_class("suggested-action")
            apply_btn = Gtk.Button(label="Använd")
            apply_btn.set_sensitive(False)
            btns.append(meas_btn)
            btns.append(apply_btn)
            box.append(btns)
            tv.set_content(box)
            win.set_content(tv)

            state = {"timer": None, "hist": _dq(maxlen=10), "suggested": None, "true": None}

            def stop_timer():
                if state["timer"]:
                    GLib.source_remove(state["timer"])
                    state["timer"] = None

            def tick():
                r = self._calib_measure(state["true"])
                if r is None:
                    sig_row.set_subtitle("väntar på signal …")
                    return True
                if r == "range":
                    sig_row.set_subtitle("frekvensen är utanför mottagningsfönstret")
                    off_row.set_subtitle("–")
                    sug_row.set_subtitle("–")
                    apply_btn.set_sensitive(False)
                    return True
                sig_row.set_subtitle(f"{r['snr']:.0f} dB över brus"
                                     + ("" if r["snr"] >= 15 else "  (svag – sikta på minst 15 dB)"))
                state["hist"].append(r["apparent_ppm"])
                med_app = float(np.median(state["hist"]))
                sug = self._cur_ppm() - med_app
                off_hz = med_app * state["true"] / 1e6
                off_row.set_subtitle(f"{off_hz:+.1f} Hz  ({med_app:+.2f} ppm)  – {len(state['hist'])} mätningar")
                if self._ppm_kiwi_key():
                    state["suggested"] = round(sug, 1)
                    sug_row.set_subtitle(f"{state['suggested']:+.1f} ppm (för den här KiwiSDR:n)")
                else:
                    state["suggested"] = int(round(sug))
                    sug_row.set_subtitle(f"{state['suggested']:+d} ppm")
                apply_btn.set_sensitive(r["snr"] >= 10 and len(state["hist"]) >= 3)
                return True

            def on_meas(btn):
                if btn.get_active():
                    if not self.engine.running:
                        self.toast("Starta mottagningen först")
                        btn.set_active(False)
                        return
                    try:
                        state["true"] = parse_freq(fr.get_text())
                    except ValueError as ex:
                        self.toast(str(ex))
                        btn.set_active(False)
                        return
                    self.tune(state["true"])      # ställ mottagaren på frekvensen
                    state["hist"].clear()
                    btn.set_label("■ Mäter …")
                    fr.set_sensitive(False)
                    if state["timer"] is None:
                        state["timer"] = GLib.timeout_add(500, tick)
                else:
                    stop_timer()
                    btn.set_label("Mät")
                    fr.set_sensitive(True)

            def on_apply(_b):
                if state["suggested"] is None:
                    return
                v = max(-200, min(200, state["suggested"]))
                self.ppm_row.set_value(v)       # uppdaterar conf + source via _on_ppm
                vt = f"{v:+.1f}" if self._ppm_kiwi_key() else f"{int(v):+d}"
                log.info("Frekvenskalibrering: ppm satt till %s (mot %.4f MHz, %s)", vt, state["true"] / 1e6,
                         self._ppm_kiwi_key() or "RTL-SDR")
                self.toast(f"Frekvenskorrigering satt till {vt} ppm")
                state["hist"].clear()           # mät om för att bekräfta ~0

            meas_btn.connect("toggled", on_meas)
            apply_btn.connect("clicked", on_apply)
            win.connect("close-request", lambda *_: (stop_timer(), False)[1])
            win.present()

        def _on_direct(self, i):
            self.conf["direct"] = i
            self._src_set("direct", i)
            if i and self.freq > 28.8e6:
                self.tune(7.074e6, "USB")

        def _on_biast(self, v):
            self.conf["biast"] = v
            self._src_set("biast", v)

        def _on_colormap(self, i):
            name = list(COLORMAPS)[i]
            self.conf["colormap"] = name
            self.view.lut = make_lut(name)
            self.view.queue_draw()

        def _on_theme(self, i):
            self.conf["theme"] = i
            apply_theme(i)
            apply_skin(self.conf.get("skin", "spanare"), i)

        def _skarm_subtitle(self, kompakt=None):
            if kompakt is None:
                kompakt = self.has_css_class("kompakt")
            w, h, s = self.skarm.skarm(self)
            self.skarm_row.set_subtitle(f"{w}×{h} · {'kompakt' if kompakt else 'normal'}")
            self.skarm_row.set_tooltip_text(f"Skärmen är {w}×{h} bildpunkter (skala {s}). Automatiskt = kompakt "
                                            f"läge när skärmen är lägre än {self.skarm.KOMPAKT_HOJD} bildpunkter.")

        def _on_skarm_lage(self, i):
            lage = ["auto", "normal", "kompakt"][i]
            self.conf["skarm_lage"] = lage
            on = self.skarm.ar_kompakt(lage, self)
            self.skarm.satt_kompakt(self, on)
            self._skarm_subtitle(on)
            log.info("Skärmanpassning: %s (%s, %s läge)", lage, self.skarm.beskriv(self), "kompakt" if on else "normalt")

        def _on_ny_skarm(self, on):
            if hasattr(self, "skarm_row"):
                self._skarm_subtitle(on)

        def _on_skin(self, i):
            name = list(SKINS)[i]
            self.conf["skin"] = name
            apply_skin(name, self.conf.get("theme", 0))
            self.view.queue_draw()
            self.an_panel.mini.queue_draw()
            self.an_panel.diag.queue_draw()

        def _on_det_enabled(self, v):
            self.conf["det_enabled"] = v
            self.engine.detector.enabled = v

        def _on_det_thresh(self, v):
            self.conf["det_thresh"] = v
            self.engine.detector.thresh = v

        def _on_auto_rec(self, v):
            self.conf["auto_rec"] = v
            self.engine.auto_rec = v

        def _on_start_toggled(self, btn):
            e = self.engine
            if btn.get_active():
                if e.running:
                    return
                try:
                    src = make_source(self.conf)
                except SourceError as ex:
                    log.error("%s", ex)
                    self.toast(str(ex))
                    btn.set_active(False)
                    return
                src.set("freq", e.center)
                src.set("gain", self._src_gain(src))
                if self._cur_ppm() if isinstance(src, KiwiSource) else self.conf["ppm"]:
                    src.set("ppm", self._cur_ppm() if isinstance(src, KiwiSource) else self.conf["ppm"])
                if self.conf["direct"]:
                    src.set("direct", self.conf["direct"])
                if self.conf["biast"]:
                    src.set("biast", True)
                if isinstance(src, KiwiSource):
                    if self.freq > kiwisdr.KIWI_MAX_HZ:
                        self.toast("KiwiSDR tar bara emot 0–30 MHz – stämde till 7,100 MHz")
                        self.freq = 7.1e6
                        self.conf["freq"] = self.freq
                    e.center = self.freq
                    src.set("freq", e.center)
                    e.offset = 0.0
                    e.reset_squelch()
                    self._update_freq_label()
                    self._zoom_before_kiwi = self.conf["zoom"]
                    if self.conf["zoom"] < 64:
                        self.set_zoom(64)                  # visa Kiwins smala band (ca 12 kHz av 1,5 MHz)
                if isinstance(src, iqfil.FileSource):
                    fc = src.center
                    e.center = fc
                    span = min(SR, src.r.rate or SR) * 0.45
                    if abs(self.freq - fc) > span:          # inställd frekvens ligger utanför filen
                        self.freq = fc - SR * 0.15 if span > SR * 0.3 else fc + 1000
                        self.conf["freq"] = self.freq
                    e.offset = self.freq - fc
                    e.reset_squelch()
                    self._update_freq_label()
                    self.play_pause_btn.set_icon_name("media-playback-pause-symbolic")
                self.audio.start()
                self._auto_level_pending = True
                if isinstance(src, KiwiSource) and self.conf.get("flaggor_kiwi", True):
                    nyckel = webbmottagare.norm_url(src.addr)
                    self.flaggor.satt_kiwi(nyckel, src.addr, hamta=False)
                    if not self.flaggor.har_cache(nyckel):
                        self.flaggor.hamta(nyckel, src.addr)
                    src.vanta_flaggor = lambda: self.flaggor.vanta_pa_hamtning(nyckel)
                else:
                    self.flaggor.satt_kiwi(None, None)
                e.start(src)
                self._check_sideband()                       # sparat läge kan vara fel sidband här
                btn.set_label("■ Stoppa")
                btn.remove_css_class("suggested-action")
                btn.add_css_class("destructive-action")
            else:
                self.scanner.stop()
                was_kiwi = isinstance(e.source, KiwiSource)
                src_k = e.source
                e.stop()
                self.audio.stop()
                if was_kiwi and self.conf.get("flaggor_kiwi", True):
                    self.flaggor.efter_stopp(webbmottagare.norm_url(src_k.addr), src_k.addr, src_k)
                zb = getattr(self, "_zoom_before_kiwi", None)
                if was_kiwi and zb is not None:
                    self._zoom_before_kiwi = None
                    self.set_zoom(zb)
                btn.set_label("▶ Starta")
                btn.remove_css_class("destructive-action")
                btn.add_css_class("suggested-action")
            self.view.queue_draw()

        def _on_engine_error(self, msg):
            self.toast(msg, timeout=8)
            return False

        def _on_engine_stopped(self):
            if self.activity_active and self.activity_win:
                self.activity_win.btn.set_active(False)
            if self.btn_start.get_active():
                self.btn_start.set_active(False)
            self.btn_rec.set_active(False)
            self.iq_btn.set_title("Starta IQ-inspelning")
            return False

        def _on_engine_event(self, kind, val):
            if kind == "rec_auto_start":
                self.toast(f"Spelar in: {os.path.basename(val)}")
            elif kind == "play_end":
                self.toast("Uppspelningen är klar")
                if self.btn_start.get_active():
                    self.btn_start.set_active(False)
            elif kind == "kiwi_status":
                self.toast(val, timeout=8)
            elif kind == "iq_error":
                self.iq_btn.set_title("Starta IQ-inspelning")
                self.toast(f"IQ-inspelningen stoppades: {val}", timeout=8)
            return False

        def _on_rec_toggled(self, btn):
            e = self.engine
            if btn.get_active():
                if not e.running:
                    self.toast("Starta mottagningen först")
                    btn.set_active(False)
                    return
                path = os.path.join(rec_dir(), f"{datetime.datetime.now():%Y%m%d-%H%M%S}_"
                                               f"{self.freq / 1e6:.4f}MHz_{e.mode}.wav")
                try:
                    e.start_wav(path)
                except OSError as ex:
                    log.error("Kunde inte starta inspelning: %s", ex)
                    self.toast(f"Kunde inte spela in: {ex}")
                    btn.set_active(False)
                    return
                btn.set_label("■ Stoppa inspelning")
                btn.add_css_class("destructive-action")
            else:
                p = e.stop_wav()
                btn.set_label("● Spela in")
                btn.remove_css_class("destructive-action")
                if p:
                    self.toast(f"Sparad: {os.path.basename(p)}")

        def _toggle_iq(self):
            e = self.engine
            if e.rec_iq:
                p = e.stop_iq()
                self.iq_btn.set_title("Starta IQ-inspelning")
                self.toast(f"IQ sparad: {os.path.basename(p)}")
                return
            if not e.running:
                self.toast("Starta mottagningen först")
                return
            if isinstance(e.source, iqfil.FileSource):
                self.toast("Du spelar redan upp en IQ-fil – den behöver inte spelas in igen")
                return
            fmt = IQ_FORMATS[min(self.conf.get("iq_format", 0), len(IQ_FORMATS) - 1)][1]
            base = os.path.join(rec_dir(), f"{datetime.datetime.now():%Y%m%d-%H%M%S}_"
                                           f"{e.center / 1e6:.4f}MHz_{SR}sps")
            try:
                if fmt == "wav16":                          # SDR++-namn: baseband_<Hz>Hz_<tid>_<datum>.wav
                    w = iqfil.WavWriter(os.path.join(rec_dir(), "baseband"), SR, e.center)
                    rate_txt = "6 MB"
                else:
                    dt = "cu8" if fmt == "sigmf_cu8" else "cf32_le"
                    w = iqfil.SigMFWriter(base, SR, e.center, dt,
                                          description=f"{e.source.name}, inställd {self.freq / 1e6:.4f} MHz {e.mode}",
                                          extra={"core:hw": e.source.name})
                    rate_txt = "3 MB" if dt == "cu8" else "12 MB"
            except OSError as ex:
                log.error("Kunde inte starta IQ-inspelning: %s", ex)
                self.toast(f"Kunde inte spela in: {ex}")
                return
            e.start_iq(w)
            self.iq_btn.set_title("Stoppa IQ-inspelning")
            self.toast(f"IQ-inspelning: ca {rate_txt} per sekund")

        # ── skanner: inställningar ──
        def _on_scan_mode(self, i):
            self.conf["scan_mode"] = i
            self.scan_group_row.set_visible(i == 0)
            for r in self.scan_range_rows:
                r.set_visible(i == 1)

        def _on_scan_sq_mode(self, i):
            self.conf["scan_sq_mode"] = i
            self.scan_snr_row.set_visible(i == 0)

        def _on_scan_preset(self, i):
            self.conf["scan_preset"] = i
            name, a, b, step, mode = SCAN_PRESETS[i]
            if a is None:
                return
            self.scan_entries["scan_start"].set_text(a)
            self.scan_entries["scan_stop"].set_text(b)
            self.scan_step_row.set_value(step)
            self.conf["scan_step_khz"] = step
            if mode != self.engine.mode:
                self.set_mode(mode)

        def _scan_groups(self):
            return sorted({band_name(b["freq"]) or "Övrigt" for b in self.conf["bookmarks"]})

        def _refresh_scan_lists(self):
            """Uppdatera grupp- och prioritetslistan efter ändrade bokmärken."""
            self._scan_lists_busy = True
            groups = self._scan_groups()
            self._scan_group_vals = [""] + groups
            self.scan_group_row.set_model(Gtk.StringList.new(["Alla bokmärken"] + groups))
            cur = self.conf.get("scan_group", "")
            self.scan_group_row.set_selected(self._scan_group_vals.index(cur) if cur in self._scan_group_vals else 0)
            bms = self.conf["bookmarks"]
            self._scan_prio_vals = [None] + [b["freq"] for b in bms]
            self.scan_prio_row.set_model(Gtk.StringList.new(
                ["Ingen"] + [f"{b['name']} ({fmt_mhz(b['freq'])})" for b in bms]))
            p = self.conf.get("scan_prio")
            idx = next((i + 1 for i, b in enumerate(bms) if p and abs(b["freq"] - p) < 1), 0)
            self.scan_prio_row.set_selected(idx)
            self._scan_lists_busy = False

        def _on_scan_group(self, i):
            if getattr(self, "_scan_lists_busy", False) or i >= len(getattr(self, "_scan_group_vals", [])):
                return
            g = self._scan_group_vals[i]
            self.conf["scan_group"] = g

        def _on_scan_prio(self, i):
            if getattr(self, "_scan_lists_busy", False) or i >= len(getattr(self, "_scan_prio_vals", [])):
                return
            self.conf["scan_prio"] = self._scan_prio_vals[i]
            if self.scanner.active:
                self.scanner.prio = self.scanner._prio_channel()

        # ── skanner: träfflista ──
        def _update_hits(self):
            sc = self.scanner
            if sc.hits_gen == self._hits_gen and not sc.active:
                return
            self._hits_gen = sc.hits_gen
            for k, h in sc.hits.items():
                row = self.hit_rows.get(k)
                if row is None:
                    row = self.hit_rows[k] = Row(h)
                    self.hit_store.append(row)
                else:
                    row.data = h
                    row.emit("updated")
            for k in [k for k in self.hit_rows if k not in sc.hits]:
                self.hit_rows.pop(k)
            if not sc.hits and self.hit_store.get_n_items():
                self.hit_store.remove_all()
            n = len(sc.hits)
            if n:
                tot = sum(h["count"] for h in sc.hits.values())
                self.hit_count.set_text(f"{n} frekvenser, {tot} träffar · dubbelklicka för att lyssna")

        def _sel_hit(self):
            it = self.hit_sel.get_selected_item()
            if not it:
                self.toast("Markera en träff först")
            return it.data if it else None

        def _tune_hit(self, item):
            if item:
                if self.scanner.active:
                    self._toggle_scan()
                self.tune(item.data["f"], item.data["mode"])

        def _hit_bookmark(self):
            h = self._sel_hit()
            if not h:
                return
            if any(abs(b["freq"] - h["f"]) < 1 for b in self.conf["bookmarks"]):
                self.toast("Finns redan som bokmärke")
                return
            name = (h["namn"] + " " if h["namn"] else "") + fmt_mhz(h["f"])
            self.conf["bookmarks"].append({"name": name, "freq": h["f"], "mode": h["mode"]})
            self._load_bookmarks()
            self._refresh_scan_lists()
            save_conf(self.conf)
            self.toast(f"Bokmärke sparat: {name}")

        def _hit_lock(self):
            h = self._sel_hit()
            if h:
                self.scanner.lock_forever(h["f"])

        def _clear_hits(self):
            self.scanner.clear_hits()
            self.hit_store.remove_all()
            self.hit_rows.clear()
            self.hit_count.set_text("Inga träffar")

        def _export_hits(self):
            dlg = Gtk.FileDialog(initial_name=f"skannertraffar_{datetime.datetime.now():%Y%m%d-%H%M}.csv")

            def done(d, res):
                try:
                    path = d.save_finish(res).get_path()
                except GLib.Error:
                    return
                try:
                    with open(path, "w", newline="", encoding="utf-8") as fh:
                        wr = csv.writer(fh)
                        wr.writerow(["frekvens_hz", "namn", "modulering", "traffar", "total_tid_s", "forst", "senast",
                                     "max_snr_db", "prioritet"])
                        for h in sorted(self.scanner.hits.values(), key=lambda h: h["f"]):
                            wr.writerow([round(h["f"]), h["namn"], h["mode"], h["count"], round(h["dur"], 1),
                                         datetime.datetime.fromtimestamp(h["first"]).isoformat(timespec="seconds"),
                                         datetime.datetime.fromtimestamp(h["last"]).isoformat(timespec="seconds"),
                                         round(h["max_snr"], 1), int(h["prio"])])
                    self.toast(f"Exporterade till {path}")
                except OSError as ex:
                    self.toast(f"Kunde inte spara: {ex}")
            dlg.save(self, None, done)

        def toast_action(self, msg, label, cb, timeout=6):
            t = Adw.Toast.new(msg)
            t.set_timeout(timeout)
            t.set_button_label(label)
            t.connect("button-clicked", lambda *_: cb())
            self.toasts.add_toast(t)

        def _open_lockout(self):
            """Lista över spärrade frekvenser (permanenta och denna session) – ta bort en och en."""
            win = Adw.Window(title="Spärrade frekvenser", modal=True, transient_for=self,
                             default_width=460, default_height=520)
            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar()
            clear = Gtk.Button(label="Rensa alla")
            clear.add_css_class("destructive-action")
            hb.pack_start(clear)
            tv.add_top_bar(hb)
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lb.add_css_class("boxed-list")
            for m in ("start", "end", "top", "bottom"):
                getattr(lb, f"set_margin_{m}")(12)
            sc = Gtk.ScrolledWindow(vexpand=True)
            sc.set_child(lb)
            tv.set_content(sc)
            win.set_content(tv)
            sc_ = self.scanner

            def fill():
                while (ch := lb.get_first_child()) is not None:
                    lb.remove(ch)
                items = ([(f, "spärrad", "lockout") for f in self.conf.get("lockout", [])] +
                         [(f, "denna session", "temp") for f in sc_.temp_lock] +
                         [(f, "hittad störning (ej sparad)", "sugg") for f in self.suggested])
                if not items:
                    lb.append(Adw.ActionRow(title="Inga spärrade frekvenser"))
                for f, what, kind in sorted(items):
                    r = Adw.ActionRow(title=f"{fmt_mhz(f)} MHz", subtitle=f"{what} · {band_name(f) or ''}")
                    b = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Ta bort")
                    b.add_css_class("flat")
                    b.connect("clicked", lambda *_, f=f, kind=kind: remove(f, kind))
                    r.add_suffix(b)
                    lb.append(r)

            def remove(f, kind):
                lst = {"lockout": self.conf.setdefault("lockout", []), "temp": sc_.temp_lock,
                       "sugg": self.suggested}[kind]
                for x in [x for x in lst if abs(x - f) < 1]:
                    lst.remove(x)
                if kind == "lockout":
                    save_conf(self.conf)
                fill()

            def clear_all(*_):
                self.conf["lockout"] = []
                sc_.temp_lock.clear()
                self.suggested.clear()
                save_conf(self.conf)
                log.info("Spärrlistan rensad")
                fill()
            clear.connect("clicked", clear_all)
            fill()
            win.present()

        def _lock_suggested(self):
            if not self.suggested:
                self.toast("Inga hittade störningar att spärra")
                return
            self.conf.setdefault("lockout", []).extend(self.suggested)
            log.info("Spärrade %d störningar: %s", len(self.suggested),
                     ", ".join(fmt_mhz(f) for f in self.suggested))
            self.toast(f"{len(self.suggested)} störningar spärrade")
            self.suggested = []
            save_conf(self.conf)

        def _toggle_scan(self):
            if self.activity_active and self.activity_win:
                self.activity_win.btn.set_active(False)
            if self.scanner.active:
                self.scanner.stop()
                self.scan_btn.set_title("Starta skanning")
            elif self.scanner.start():
                self.scan_btn.set_title("Stoppa skanning")

        def _on_web_enabled(self, v):
            self.conf["web_enabled"] = v
            if v:
                self._start_web()
            elif self.web:
                self.web.stop()
                self.web = None
                self.web_row.set_subtitle("avstängt")

        def _start_web(self):
            if self.web:
                return False
            try:
                self.web = WebServer(self, int(self.conf["web_port"]), self.conf["web_public"])
                self.web_row.set_subtitle(self.web.url)
            except RuntimeError as ex:
                log.error("%s", ex)
                self.toast(str(ex))
            return False

        # ── signallogg ──
        def _parse_filter(self, text):
            terms = []
            for tok in text.split():
                m = re.match(r"^(freq|f|bw|peak|snr|dur|typ|class|band|status)(>=|<=|==|!=|>|<|~|=)(.+)$",
                             tok, re.I)
                if not m:
                    if tok.lower() in ("aktiv", "active"):
                        terms.append(("status", "==", "aktiv"))
                    else:
                        terms.append(("text", "~", tok.lower()))
                    continue
                k, op, v = m.group(1).lower(), m.group(2), m.group(3)
                try:
                    if k in ("freq", "f"):
                        terms.append(("f", op, parse_freq(v)))
                    elif k == "bw":
                        terms.append(("bw", op, parse_bw(v)))
                    elif k in ("peak", "snr", "dur"):
                        terms.append((k, op, float(v.rstrip("s"))))
                    else:
                        terms.append(({"typ": "cls", "class": "cls"}.get(k, k), op, v.lower()))
                except ValueError:
                    terms.append(("text", "~", tok.lower()))
            return terms

        def _filter_func(self, item, *_):
            d = item.data
            for k, op, v in self._filter:
                if k == "text":
                    hay = f"{fmt_mhz(d['f'])} {d['cls']} {d['band']} {d['status']}".lower()
                    if v not in hay:
                        return False
                    continue
                x = d.get(k)
                if isinstance(x, str):
                    x = x.lower()
                    ok = (v in x) if op == "~" else (x == v if op in ("==", "=") else x != v if op == "!=" else True)
                else:
                    ok = {">": x > v, "<": x < v, ">=": x >= v, "<=": x <= v, "==": abs(x - v) < 1e-6,
                          "=": abs(x - v) < 1e-6, "!=": x != v, "~": True}[op] if not isinstance(v, str) else True
                if not ok:
                    return False
            return True

        def _on_filter_changed(self, entry):
            self._filter = self._parse_filter(entry.get_text())
            self.log_filter.changed(Gtk.FilterChange.DIFFERENT)

        def _update_log(self):
            changes, active = self.engine.detector.pop_changes()
            self.active_tracks = active
            for t in changes:
                row = self.log_rows.get(t["id"])
                if row is None:
                    row = Row(t)
                    self.log_rows[t["id"]] = row
                    self.log_store.append(row)
                    if len(self.log_rows) > 5000:
                        old = self.log_store.get_item(0)
                        self.log_store.remove(0)
                        self.log_rows.pop(old.data["id"], None)
                else:
                    row.data = t
                    row.emit("updated")
            if changes and self._filter:
                self.log_filter.changed(Gtk.FilterChange.DIFFERENT)
            if changes:
                self.log_count.set_text(f"{len(self.log_rows)} signaler")

        def _clear_log(self):
            self.engine.detector.clear()
            self.log_store.remove_all()
            self.log_rows.clear()
            self.log_count.set_text("0 signaler")

        def _export_csv(self):
            dlg = Gtk.FileDialog(initial_name=f"signallogg_{datetime.datetime.now():%Y%m%d-%H%M}.csv")

            def done(d, res):
                try:
                    f = d.save_finish(res)
                except GLib.Error:
                    return
                path = f.get_path()
                try:
                    with open(path, "w", newline="", encoding="utf-8") as fh:
                        wr = csv.writer(fh)
                        wr.writerow(["nr", "forst_sedd", "frekvens_hz", "bandbredd_hz", "topp_db", "snr_db",
                                     "langd_s", "typ", "band", "status"])
                        for i in range(self.log_store.get_n_items()):
                            dd = self.log_store.get_item(i).data
                            wr.writerow([dd["id"], datetime.datetime.fromtimestamp(dd["first"]).isoformat(timespec="seconds"),
                                         round(dd["f"]), round(dd["bw"]), round(dd["peak"], 1), round(dd["snr"], 1),
                                         round(dd["dur"], 1), dd["cls"], dd["band"], dd["status"]])
                    self.toast(f"Exporterade till {path}")
                    log.info("Signallogg exporterad: %s", path)
                except OSError as ex:
                    log.error("CSV-export misslyckades: %s", ex)
                    self.toast(f"Kunde inte spara: {ex}")
            dlg.save(self, None, done)

        def _log_to_bookmark(self):
            item = self.log_sel.get_selected_item()
            if not item:
                self.toast("Markera en rad i signalloggen först")
                return
            d = item.data
            mode = "WFM" if d["bw"] > 60000 else ("NFM" if d["bw"] > 4000 else ("USB" if d["bw"] > 800 else "CW"))
            f = round(d["f"] / 500) * 500
            name = f"{d['cls']} {fmt_mhz(f)}"
            self.conf["bookmarks"].append({"name": name, "freq": f, "mode": mode})
            self._load_bookmarks()
            save_conf(self.conf)
            self.toast(f"Bokmärke sparat: {name}")

        def _tune_row(self, item):
            if not item:
                return
            d = item.data
            if "name" in d:
                self.tune(d["freq"], d.get("mode"))
            else:
                self.tune(d["f"])

        # ── avkodare ──
        DEC_TUNE = {"ACARS": (131.725e6, "AM"), "AIS": (161.975e6, "NFM"), "APRS": (144.8e6, "NFM")}

        def _on_decoder(self, kind, v):
            self.conf[kind.lower()] = v
            self.hub.enabled[kind] = v
            log.info("%s-avkodning %s", kind, "på" if v else "av")
            f, _m = self.DEC_TUNE[kind]
            if v and abs(self.engine.center - f) > SR * 0.44:
                self.toast(f"{kind} ligger utanför fönstret – tryck \"Gå till {kind}\"")

        def _open_activity(self):
            try:
                if self.activity_win is None:
                    self.activity_win = ActivityWindow(self)
                self.activity_win.present()
            except Exception as ex:
                log.exception("Kunde inte öppna aktivitetskartan")
                self.toast(f"Kunde inte öppna aktivitetskartan: {ex}")
                return
            if not self.engine.running:
                self.toast("Starta mottagningen, välj band och tryck ▶ Svep")

        # ── frontpanel ──
        def _open_frontpanel(self, model="r9500"):
            try:
                import frontpanel
            except Exception as ex:
                log.exception("Kunde inte ladda frontpanelen")
                self.toast(f"Kunde inte ladda frontpanelen: {ex}")
                return
            try:
                if self.panel_win is None:
                    self.panel_win = frontpanel.FrontPanel(self, model)
                else:
                    self.panel_win.set_model(model)
                self.panel_win.present()
            except Exception as ex:
                log.exception("Kunde inte öppna frontpanelen")
                self.toast(f"Kunde inte öppna frontpanelen: {ex}")

        # ── webbmottagare ──
        def _open_webrx(self):
            try:
                if self.webrx_win is None:
                    self.webrx_win = WebRxWindow(self)
                self.webrx_win.present()
            except Exception as ex:
                log.exception("Kunde inte öppna listan över webbmottagare")
                self.toast(f"Kunde inte öppna webbmottagarna: {ex}")

        def open_uri(self, uri):
            try:
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except GLib.Error as ex:
                self.toast(f"Kunde inte öppna {uri}: {ex.message}")

        def _ask_kiwi_password(self, r):
            parent = self.webrx_win if self.webrx_win and self.webrx_win.get_visible() else self
            dlg = Adw.MessageDialog(transient_for=parent, heading="Lösenord krävs",
                                    body=f"{r['namn'] or r['url']} är privat. Ange lösenordet du fått av ägaren.")
            pw = Adw.PasswordEntryRow(title="Lösenord")
            lb = Gtk.ListBox()
            lb.add_css_class("boxed-list")
            lb.append(pw)
            dlg.set_extra_child(lb)
            dlg.add_response("cancel", "Avbryt")
            dlg.add_response("ok", "Anslut")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_default_response("ok")
            dlg.connect("response", lambda _d, rid: rid == "ok" and pw.get_text() and
                        self.use_webrx(r, password=pw.get_text()))
            dlg.present()

        def use_webrx(self, r, password=None):
            """Välj en KiwiSDR ur listan som källa (och starta om mottagningen om den är igång)."""
            if not webbmottagare.kiwi_ok(r):
                self.open_uri(r["url"])
                return
            if r.get("auth") == "password" and not password:
                self._ask_kiwi_password(r)
                return
            url = r["url"]
            if password:
                p = urllib.parse.urlsplit(url)
                url = f"{p.scheme}://{urllib.parse.quote(password, safe='')}@{p.netloc}{p.path}"
            self.conf["kiwi_addr"] = url
            self.kiwi_row.set_text(url)
            running = self.engine.running
            if running:
                self.btn_start.set_active(False)
            self.source_row.set_selected(SOURCES.index("kiwi"))
            self._refresh_ppm_row()                          # varje Kiwi har sin egen korrigering
            if self.freq > kiwisdr.KIWI_MAX_HZ or self.freq < 10e3:
                self.tune(7.074e6, "USB")
            log.info("Webbmottagare vald: %s (%s)", r["namn"], url)
            self.toast(f"KiwiSDR: {r['namn'] or url}")
            if running:
                GLib.timeout_add(300, lambda: self.btn_start.set_active(True) or False)
            if self.webrx_win:
                self.webrx_win._rebuild()

        def _open_ship_map(self):
            try:
                if self.ship_win is None:
                    self.ship_win = ShipWindow(self)
                self.ship_win.refresh()
                self.ship_win.present()
            except Exception as ex:
                log.exception("Kunde inte öppna fartygskartan")
                self.toast(f"Kunde inte öppna fartygskartan: {ex}")
                return
            if not self.hub.enabled["AIS"]:
                self.toast("AIS-avkodningen är av – tryck \"Gå till AIS\" i fartygsfönstret")

        def _ais_update(self, msg):
            v = msg.get("ais") or {}
            mmsi = msg["src"]
            now = msg.get("time", time.time())
            d = self.ships.get(mmsi)
            if d is None:
                d = {"mmsi": mmsi, "name": "", "call": "", "shiptype": None, "status": "", "nav": None,
                     "sog": None, "cog": None, "hdg": None, "pos": None, "dest": "", "first": now, "last": now,
                     "msgs": 0, "special": "", "alert": False}
                self.ships[mmsi] = d
            for k in ("call", "shiptype", "status", "nav", "sog", "cog", "hdg", "dest"):
                if k in v and v[k] not in (None, ""):
                    d[k] = v[k]
            if msg.get("name"):
                d["name"] = msg["name"]
            if msg.get("pos"):
                d["pos"] = tuple(msg["pos"])
            if v.get("base"):
                d["special"] = "Basstation"
            elif v.get("aton"):
                d["special"] = "Fyr/boj"
            d["last"] = now
            d["msgs"] += 1
            alert = mmsi.startswith("97") or v.get("nav") == 14
            if alert and not d["alert"]:
                self.toast(f"AIS-NÖDSÄNDARE: {mmsi} {d['name']} – se fartygskartan", 15)
                log.warning("AIS-nödsändare (SART/MOB/EPIRB): %s %s %s", mmsi, d["name"], d["pos"])
            d["alert"] = alert

        def _open_radar(self):
            """Startar Flygradar (adsb_karta.py) som eget program. Dongeln kan bara användas av ett program."""
            path = os.path.join(APP_DIR, "adsb_karta.py")
            if not os.path.exists(path):
                self.toast("Hittar inte adsb_karta.py – installera om programmet")
                log.error("Saknar %s", path)
                return
            src = self.conf["source"]
            if src == "tcp":
                pass
            elif rtl_finns():
                if self.engine.running and src == "rtl":
                    self.btn_start.set_active(False)       # släpp dongeln
                    self.toast("Mottagningen stoppad – Flygradar använder dongeln")
                src = "rtl"
            else:
                src = "adsblol"
                self.toast("Ingen SDR-dongel ansluten – Flygradar hämtar data från adsb.lol")
            cmd = [sys.executable, path, "--kalla", src, "--start"]
            if src == "tcp":
                cmd += ["--tcp", self.conf["tcp_addr"]]

            def launch():
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open(STDERR_FILE, "ab"),
                                     start_new_session=True)
                    log.info("Startade Flygradar: %s", " ".join(cmd))
                except OSError as ex:
                    log.error("Kunde inte starta Flygradar: %s", ex)
                    self.toast(f"Kunde inte starta Flygradar: {ex}")
                return False
            GLib.timeout_add(700 if self.engine.running or self.engine.thread else 0, launch)

        def _goto_decoder(self, kind):
            f, m = self.DEC_TUNE[kind]
            if not self.hub.enabled[kind]:
                self.hub.enabled[kind] = True
                self.conf[kind.lower()] = True
                self.toast(f"{kind}-avkodning påslagen")
            if self.conf.get("direct"):
                self.toast("Stäng av direktsampling för VHF")
            self.tune(f, m)

        def _on_decoded(self, msg):
            if msg.get("type") == "AIS":
                self._ais_update(msg)
            self.dec_store.append(Row(msg))
            self.decoded_rows += 1
            self.dec_count.set_text(f"{self.decoded_rows} meddelanden")
            self.dec_tab_label.set_text(f"Avkodat ({self.decoded_rows})")
            try:
                with open(os.path.join(STATE_DIR, "avkodat.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            except OSError as ex:
                log.error("Kunde inte spara avkodat meddelande: %s", ex)
            return False

        def _dec_filter_func(self, item, *_):
            q = self.dec_filter.get_text().strip().lower()
            if not q:
                return True
            d = item.data
            return q in f"{d['src']} {d['name']} {d['kind']} {d['text']} {d['type']} {fmt_mhz(d['freq'])}".lower()

        def _show_decoded(self, item):
            if not item:
                return
            d = item.data
            lines = [f"{d['type']} på {fmt_mhz(d['freq'])} MHz, "
                     f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(d['time']))}", ""]
            lines += [f"{k}: {v}" for k, v in d.get("details", [])]
            if d.get("pos"):
                lines.append(f"Position: {d['pos'][0]:.5f}, {d['pos'][1]:.5f}")
            lines += [f"Nivå: {d['level']:.0f} dB över brus", "", d["text"] or "(ingen text)"]
            dlg = Adw.AlertDialog(heading="Avkodat meddelande", body="\n".join(lines))
            dlg.set_body_use_markup(False)
            if d.get("pos"):
                dlg.add_response("map", "Visa på karta")
            dlg.add_response("ok", "Stäng")
            dlg.set_default_response("ok")

            def resp(dl, r):
                if r == "map":
                    lat, lon = d["pos"]
                    uri = f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=12/{lat:.5f}/{lon:.5f}"
                    try:
                        Gio.AppInfo.launch_default_for_uri(uri, None)
                    except GLib.Error as ex:
                        log.error("Kunde inte öppna kartan: %s", ex)
            dlg.connect("response", resp)
            dlg.present(self)

        def _clear_decoded(self):
            self.dec_store.remove_all()
            self.decoded_rows = 0
            self.dec_count.set_text("0 meddelanden")
            self.dec_tab_label.set_text("Avkodat")

        def _export_decoded(self):
            dlg = Gtk.FileDialog(initial_name=f"avkodat_{datetime.datetime.now():%Y%m%d-%H%M}.csv")

            def done(d, res):
                try:
                    path = d.save_finish(res).get_path()
                except GLib.Error:
                    return
                try:
                    with open(path, "w", newline="", encoding="utf-8") as fh:
                        wr = csv.writer(fh)
                        wr.writerow(["tid", "frekvens_hz", "typ", "avsandare", "namn", "kategori", "lat", "lon",
                                     "niva_db", "text"])
                        for i in range(self.dec_store.get_n_items()):
                            dd = self.dec_store.get_item(i).data
                            pos = dd.get("pos") or ("", "")
                            wr.writerow([datetime.datetime.fromtimestamp(dd["time"]).isoformat(timespec="seconds"),
                                         round(dd["freq"]), dd["type"], dd["src"], dd["name"], dd["kind"],
                                         pos[0], pos[1], round(dd["level"], 1), dd["text"]])
                    self.toast(f"Exporterade till {path}")
                except OSError as ex:
                    log.error("CSV-export misslyckades: %s", ex)
                    self.toast(f"Kunde inte spara: {ex}")
            dlg.save(self, None, done)

        # ── bokmärken ──
        def _load_bookmarks(self):
            self.bm_store.remove_all()
            for bm in self.conf["bookmarks"]:
                self.bm_store.append(Row(bm))
            self.view.queue_draw()

        def _add_bookmark(self):
            name = self.bm_name.get_text().strip() or f"{fmt_mhz(self.freq)} MHz {self.engine.mode}"
            self.conf["bookmarks"].append({"name": name, "freq": self.freq, "mode": self.engine.mode})
            self.bm_name.set_text("")
            self._load_bookmarks()
            self._refresh_scan_lists()
            save_conf(self.conf)
            self.toast(f"Bokmärke sparat: {name}")

        def _del_bookmark(self):
            item = self.bm_sel.get_selected_item()
            if not item:
                self.toast("Markera ett bokmärke först")
                return
            try:
                self.conf["bookmarks"].remove(item.data)
            except ValueError:
                pass
            self._load_bookmarks()
            self._refresh_scan_lists()
            save_conf(self.conf)

        # ── frontpanel ──
        def panel_set_volume(self, v):
            v = max(0.0, min(300.0, float(v)))
            self._on_volume(v)
            if hasattr(self, "vol_row"):
                self.vol_row.set_value(v)

        def panel_set_squelch(self, v):
            v = max(-150.0, min(0.0, float(v)))
            self._on_squelch(v)
            self.sq_row.set_value(v)

        def panel_set_gain(self, v):
            v = max(0.0, min(self.gain_max(), float(v)))
            if self.gain_state()[0]:
                self.agc_row.set_active(False)          # stänger av AGC, slår på manuell förstärkning
            self._on_gain(v)
            self.gain_row.set_value(v)

        def panel_toggle_agc(self):
            v = not self.gain_state()[0]
            self.agc_row.set_active(v)
            self.toast("Automatisk förstärkning på" if v else "Manuell förstärkning")
            return v

        def panel_step(self, d):
            self._set_step_idx((int(self.conf.get("step_idx", 0)) + int(d)) % len(STEPS))
            self.toast(f"Frekvenssteg: {STEP_NAMES[self.conf['step_idx']]}")

        def panel_bw_step(self, d):
            e = self.engine
            lst = FILTER_STEPS.get(e.mode, FILTER_STEPS["AM"])
            i = min(range(len(lst)), key=lambda k: abs(lst[k] - float(e.bw)))
            i = max(0, min(len(lst) - 1, i + int(d)))
            e.bw = float(lst[i])
            self.conf["bw"] = float(lst[i])
            self._updating = True
            self.bw_row.set_value(e.bw)
            self._updating = False
            self._refit_kiwi()
            self.view.queue_draw()
            self.toast(f"Bandbredd: {fmt_bw(e.bw)}")

        def panel_store_bookmark(self):
            bm = {"name": f"{fmt_mhz(self.freq)} MHz {self.engine.mode}",
                  "freq": self.freq, "mode": self.engine.mode}
            self.conf["bookmarks"].append(bm)
            self._panel_bm = len(self.conf["bookmarks"]) - 1
            self._load_bookmarks()
            self._refresh_scan_lists()
            save_conf(self.conf)
            self.toast(f"Bokmärke sparat: {bm['name']}")

        def panel_recall_bookmark(self, d=1):
            bms = self.conf.get("bookmarks", [])
            if not bms:
                self.toast("Inga bokmärken sparade")
                return
            self._panel_bm = (getattr(self, "_panel_bm", -1) + int(d)) % len(bms)
            bm = bms[self._panel_bm]
            self.tune(bm["freq"], bm.get("mode"))
            self.toast(f"Bokmärke {self._panel_bm + 1}/{len(bms)}: {bm['name']}")

        def panel_clear_bookmark(self):
            bms = self.conf.get("bookmarks", [])
            if not bms:
                self.toast("Inga bokmärken att ta bort")
                return
            bm = bms.pop()
            self._load_bookmarks()
            self._refresh_scan_lists()
            save_conf(self.conf)
            self.toast(f"Tog bort bokmärket {bm['name']}")

        def panel_band_name(self, hz):
            return band_name(hz)

        def panel_band_step(self, d):
            bands = sorted({(lo, hi, name) for lo, hi, name in BANDPLAN})
            if not bands:
                return
            if d > 0:
                nxt = [b for b in bands if b[0] * 1e6 > self.freq + 1000]
                lo, hi, name = min(nxt, key=lambda b: b[0]) if nxt else bands[0]
            else:
                prv = [b for b in bands if b[1] * 1e6 < self.freq - 1000]
                lo, hi, name = max(prv, key=lambda b: b[1]) if prv else bands[-1]
            self.tune((lo + hi) / 2 * 1e6)
            self.toast(f"{name} ({lo:g}–{hi:g} MHz)")

        def panel_dwell_cycle(self):
            vals = [100, 200, 300, 500, 800, 1200, 2000]
            cur = int(self.conf.get("scan_dwell_ms", 300))
            nxt = next((v for v in vals if v > cur), vals[0])
            self.conf["scan_dwell_ms"] = nxt
            if self.scanner.active:
                self.scanner.dwell = nxt / 1000.0 + (0.5 if self.scanner._kiwi() else 0.0)
                self.scanner.need = max(3, int(nxt / 1000.0 / (BLOCK / SR)))
            self.toast(f"Tid per skanningsfönster: {nxt} ms")

        def panel_toggle_rec(self):
            self.btn_rec.set_active(not self.btn_rec.get_active())

        def _restart_scan_later(self):
            if not self.scanner.active:
                return
            self.scanner.stop()
            self.scan_btn.set_title("Starta skanning")

            def again():
                if self.scanner.start():
                    self.scan_btn.set_title("Stoppa skanning")
                return False
            GLib.timeout_add(500, again)

        def panel_scan_toggle(self):
            if self.scanner.active:
                self._toggle_scan()
                return
            if not self.engine.running:
                self.btn_start.set_active(True)
                self.toast("Startar mottagningen – skanningen börjar strax")

                def go():
                    if self.engine.running and not self.scanner.active:
                        self._toggle_scan()
                    return False
                GLib.timeout_add(900, go)
                return
            self._toggle_scan()

        def panel_scan_mode(self, i):
            i = 1 if int(i) else 0
            self.conf["scan_mode"] = i
            if hasattr(self, "scan_mode_row"):
                self.scan_mode_row.set_selected(i)
            self._restart_scan_later()
            self.toast("Skannar " + ("frekvensintervall" if i else "bokmärken"))

        def panel_scan_group(self, d=1):
            self._refresh_scan_lists()
            vals = getattr(self, "_scan_group_vals", [""])
            cur = self.conf.get("scan_group", "")
            i = vals.index(cur) if cur in vals else 0
            i = (i + int(d)) % len(vals)
            self.conf["scan_group"] = vals[i]
            self.scan_group_row.set_selected(i)
            self._restart_scan_later()
            self.toast("Skanningsgrupp: " + (vals[i] or "alla bokmärken"))

        def panel_scan_range(self, kind, value):
            key = "scan_start" if kind == "start" else "scan_stop"
            text = f"{float(value):g}"
            row = self.scan_entries.get(key)
            if row is not None:
                row.set_text(text)                  # triggar _set_conf via "changed"
            else:
                self.conf[key] = text
            if self.scanner.active:
                self._restart_scan_later()

        def panel_scan_conf(self, key, value):
            self.conf[key] = value
            s = self.scanner
            if not s.active:
                return
            if key == "scan_hold":
                s.hold = float(value)
            elif key == "scan_dwell_ms":
                s.dwell = float(value) / 1000.0 + (0.5 if s._kiwi() else 0.0)
                s.need = max(3, int(float(value) / 1000.0 / (BLOCK / SR)))
            elif key == "scan_snr":
                s.thr = float(value)
            elif key == "scan_step_khz":
                s.chstep = max(100.0, float(value) * 1000)

        # ── felsökning ──
        def copy_report(self):
            e = self.engine
            extra = (f"Mottagning igång: {e.running}, källa: {e.source.name if e.source else '-'}, "
                     f"block: {e.stats['blocks']}, DSP: {e.stats['proc_ms']:.1f} ms/block, "
                     f"överskridanden: {e.stats['overruns']}")
            text = build_report(extra)
            clip = self.get_clipboard()
            clip.set_content(Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8",
                                                               GLib.Bytes.new(text.encode("utf-8"))))
            self.toast("Felrapporten är kopierad – klistra in den i Claude Code")
            log.info("Felrapport kopierad till urklipp")

        def save_screenshot(self, path, tries=10):
            """Renderar fönstret till en PNG-fil (fungerar även under Wayland)."""
            try:
                win = self
                for w in (getattr(self, "panel_win", None), getattr(self, "webrx_win", None),
                          getattr(self, "activity_win", None), getattr(self, "ship_win", None),
                          getattr(self, "an_win", None)):
                    if w is not None and w.get_visible():
                        win = w
                        break
                w, h = win.get_width(), win.get_height()
                paint = Gtk.WidgetPaintable.new(win)
                snap = Gtk.Snapshot()
                paint.snapshot(snap, w, h)
                node = snap.to_node()
                if node is None:
                    # Fönstret är inte ritat just nu (t.ex. minimerat) – försök igen strax
                    if tries > 0:
                        GLib.timeout_add(500, lambda: self.save_screenshot(path, tries - 1) and False)
                    else:
                        log.warning("Skärmbild: fönstret ritades inte (är det minimerat?)")
                    return False
                tex = win.get_renderer().render_texture(node, None)
                tex.save_to_png(path)
                log.info("Skärmbild sparad: %s", path)
            except Exception:
                log.exception("Kunde inte spara skärmbild")
            return False

        def _open_path(self, path):
            try:
                Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(path, None), None)
            except GLib.Error as ex:
                log.error("Kunde inte öppna %s: %s", path, ex)
                self.toast(f"Kunde inte öppna {path}")

        def toast(self, msg, timeout=4):
            t = Adw.Toast.new(msg)
            t.set_timeout(timeout)
            self.toasts.add_toast(t)
            return False

        # ── timers ──
        def _tick(self):
            e = self.engine
            got = False
            e.view_zoom, e.view_center = self.view.requested_view()
            while e.spec_q:
                try:
                    db, c, zl = e.spec_q.popleft()
                except IndexError:
                    break
                self.latest_db, self.latest_c = db, c
                if zl is None:
                    zl = (db, c, float(SR))
                if zl is not False:
                    self.view.push_line(*zl, to_waterfall=not self._auto_level_pending)
                    got = True
            if got:
                if self._auto_level_pending and e.stats["blocks"] > 15:
                    self._auto_level_pending = False
                    self.auto_level()
                if self._relevel_at and e.stats["blocks"] >= self._relevel_at:
                    self._relevel_at = 0
                    self.auto_level()
                self.view.queue_draw()
            if e.running:
                self.level_bar.set_value(float(np.clip(e.level + 120, 0, 120)))
                self.level_lbl.set_text(f"{e.level:6.1f} dB")
                if e.overload:
                    self.sq_lbl.set_text(f"⚠ ÖVERSTYRD ({e.adc_clip * 100:.0f} %)")
                    now_t = time.monotonic()
                    if now_t - getattr(self, "_ovl_toast_t", 0.0) > 20.0:
                        self._ovl_toast_t = now_t
                        self.toast("⚠ Mottagaren överstyrs (ADC klipper) – sänk förstärkningen")
                else:
                    self.sq_lbl.set_text("● SIGNAL" if e.sq_open and e.squelch > -150 else "")
            self.scanner.tick()
            try:
                self.an_panel.tick()
            except Exception:
                log.exception("Fel vid uppdatering av signalanalysen")
            return True

        def _slow_tick(self):
            try:
                self._update_log()
            except Exception:
                log.exception("Fel vid uppdatering av signalloggen")
            e = self.engine
            self._play_tick()
            try:
                self._update_hits()
            except Exception:
                log.exception("Fel vid uppdatering av skannerträffarna")
            self.scan_lbl.set_text(self.scanner.status)
            self._ship_tick += 1
            if self.ship_win is not None and self.ship_win.get_visible() and self._ship_tick % 5 == 0:
                try:
                    self.ship_win.refresh()
                except Exception:
                    log.exception("Fel vid uppdatering av fartygskartan")
            h = self.hub
            h.extra = {"ACARS": self.freq if (e.mode == "AM" and 118e6 <= self.freq <= 137e6) else None,
                       "APRS": self.freq if (e.mode == "NFM" and 144e6 <= self.freq <= 146e6) else None}
            on = [k for k, v in h.enabled.items() if v]
            if on and e.running:
                lines = []
                for k in on:
                    chs = [f for kk, f in h.stats_channels if kk == k]
                    st = h.stats[k]
                    lines.append(f"{k}: " + (", ".join(f"{f / 1e6:.3f}" for f in chs) if chs else "utanför fönstret")
                                 + f" – {st['decoded']} avkodade, {st['bursts']} skurar")
                self.acars_row.set_subtitle("\n".join(lines))
            else:
                self.acars_row.set_subtitle(("på (" + ", ".join(on) + ") – starta mottagningen") if on else "av")
            self.btn_skip.set_visible(self.scanner.active)
            self.sugg_row.set_subtitle(", ".join(fmt_mhz(f) for f in self.suggested[-8:]) + " MHz"
                                       if self.suggested else "inga än")
            self.sugg_btn.set_sensitive(bool(self.suggested))
            if e.running:
                drops = getattr(e.source, "dropped", 0)
                drop_txt = f", {drops} tappade USB-block" if drops else ""
                adc_txt = f", ⚠ ADC klipper ({e.adc_clip * 100:.1f} %)" if e.overload else ""
                self.stats_row.set_subtitle(f"{e.stats['proc_ms']:.1f} ms per block (max {BLOCK / SR * 1000:.0f}), "
                                            f"{e.stats['overruns']} överskridanden{drop_txt}{adc_txt}")
            return True

        def _on_close(self, *_):
            try:
                if self.panel_win is not None:
                    self.panel_win.close()
                    self.panel_win = None
                self.conf["window_max"] = self.is_maximized()
                if not self.is_maximized():
                    self.conf["window"] = [self.get_width(), self.get_height()]
                self.conf["sidebar_width"] = self.outer.get_position()
                rp = self.right_paned
                if rp.get_height() > 0:
                    self.conf["bottom_height"] = rp.get_property("max-position") - rp.get_position()
                if self.ship_win is not None:
                    self.conf["ship_map_view"] = list(self.ship_win.mp.view())
                save_conf(self.conf)
                if self.an_win is not None:
                    self.conf["an_win"] = [self.an_win.get_width(), self.an_win.get_height()]
                self.analyzer.stop()
                self.scanner.stop()
                self.engine.stop()
                self.audio.stop()
                for k, on in self.hub.enabled.items():
                    if on:
                        st = self.hub.stats[k]
                        log.info("%s-sammanfattning: %d skurar, %d avkodade, %d ej avkodbara", k,
                                 st["bursts"], st["decoded"], st["failed"])
                self.hub.stop()
                if self.web:
                    self.web.stop()
            except Exception:
                log.exception("Fel vid avslut")
            log.info("Programmet avslutas")
            return False

    def apply_theme(i):
        schemes = [Adw.ColorScheme.FORCE_DARK, Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.DEFAULT]
        Adw.StyleManager.get_default().set_color_scheme(schemes[i if 0 <= i < 3 else 0])

    _skin_css = {}

    def apply_skin(name, theme):
        """Laddar utseendets CSS. Färgerna i 'Spanare' gäller bara när programmet visas mörkt."""
        disp = Gdk.Display.get_default()
        old = _skin_css.pop("p", None)
        if old is not None:
            Gtk.StyleContext.remove_provider_for_display(disp, old)
        css = SKIN_CSS.get(name, "")
        dark = Adw.StyleManager.get_default().get_dark()
        if name == "spanare" and not dark:
            css = SKIN_CSS["klassisk"]
        prov = Gtk.CssProvider()
        prov.load_from_string(css)
        Gtk.StyleContext.add_provider_for_display(disp, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        _skin_css["p"] = prov

    class App(Adw.Application):
        def __init__(self, args):
            # Start utan flaggor (t.ex. från ikonen): bara ett fönster, en andra start visar det
            # befintliga. Start med flaggor från terminalen: egen instans, så flaggorna gäller.
            unique = len(sys.argv) <= 1
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.DEFAULT_FLAGS if unique else Gio.ApplicationFlags.NON_UNIQUE)
            self.args = args
            self.win = None
            self.connect("activate", self.on_activate)
            self.connect("shutdown", self._on_shutdown)

        def _on_shutdown(self, _app):
            """Avslut utan att fönstret stängts (Ctrl+Q, app.quit()): släpp dongeln ordentligt."""
            w = self.win
            if w is not None and w.engine.running:
                log.info("Avslutar – stoppar mottagningen")
                try:
                    if w.panel_win is not None:
                        w.panel_win.close()
                        w.panel_win = None
                    w.scanner.stop()
                    w.engine.stop()
                    w.audio.stop()
                except Exception:
                    log.exception("Fel vid avslut")

        def on_activate(self, app):
            if self.win is not None:
                log.info("Programmet är redan öppet – visar befintligt fönster")
                self.win.present()
                return
            css = Gtk.CssProvider()
            css.load_from_string(".freq-entry { font-family: monospace; font-size: 16pt; font-weight: bold; "
                                 "color: #7fe0d0; } .map-label { background: rgba(0,0,0,0.65); color: white; "
                                 "border-radius: 4px; padding: 0 3px; font-size: 8pt; font-weight: bold; } "
                                 ".wf-controls { background: rgba(0,0,0,0.55); border-radius: 6px; padding: 2px; } "
                                 ".dsp-panel { background-color: @window_bg_color; "
                                 "box-shadow: 0 2px 10px rgba(0,0,0,0.5); } "
                                 ".wf-controls button { min-width: 28px; min-height: 22px; padding: 0 4px; "
                                 "font-size: 11pt; } "
                                 "paned.delare > separator { min-width: 6px; min-height: 6px; "
                                 "background-color: alpha(currentColor, 0.10); } "
                                 "paned.delare > separator:hover { background-color: alpha(@accent_color, 0.45); } "
                                 "paned.nedre-delare > separator { min-height: 8px; }")
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            if os.path.exists(CSS_FILE):
                user = Gtk.CssProvider()
                try:
                    user.load_from_path(CSS_FILE)
                    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), user,
                                                              Gtk.STYLE_PROVIDER_PRIORITY_USER)
                    log.info("Laddade egen stil: %s", CSS_FILE)
                except Exception as ex:
                    log.error("Fel i %s: %s", CSS_FILE, ex)
            conf = load_conf()
            import skarm
            skarm.installera()
            apply_theme(conf.get("theme", 0))
            apply_skin(conf.get("skin", "spanare"), conf.get("theme", 0))
            Adw.StyleManager.get_default().connect(
                "notify::dark", lambda sm, _p: apply_skin(self.win.conf.get("skin", "spanare") if self.win else
                                                          conf.get("skin", "spanare"), 0))
            a = self.args
            if a.source:
                conf["source"] = a.source
            if a.spela:
                pth = os.path.abspath(a.spela)
                conf["source"], conf["play_file"] = "file", pth
                if a.takt or a.mittfrekvens:
                    conf.setdefault("play_override", {})[pth] = {
                        "rate": a.takt, "center": parse_freq(a.mittfrekvens) if a.mittfrekvens else None}
            if a.tcp:
                conf["tcp_addr"] = a.tcp
                conf["source"] = "tcp"
            if a.kiwi:
                conf["kiwi_addr"] = a.kiwi
                conf["source"] = "kiwi"
            if a.freq:
                conf["freq"] = parse_freq(a.freq)
            if a.mode:
                conf["mode"] = a.mode.upper()
                conf["bw"] = None
            if a.acars:
                conf["acars"] = True
            if a.ais:
                conf["ais"] = True
            if a.aprs:
                conf["aprs"] = True
            if a.direktsampling:
                conf["direct"] = {"av": 0, "i": 1, "q": 2}[a.direktsampling]
            if a.squelch is not None:
                conf["squelch"] = a.squelch
                if a.skanna and a.snr is None:
                    conf["scan_sq_mode"] = 1          # --squelch till skannern = använd squelch-reglaget
            if a.snr is not None:
                conf["scan_sq_mode"], conf["scan_snr"] = 0, a.snr
            if a.skanna:
                conf["scan_mode"] = 0 if a.skanna == "bokmarken" else 1
            if a.intervall:
                lo_s, _, hi_s = a.intervall.partition("-")
                conf["scan_start"], conf["scan_stop"] = lo_s, hi_s
            if a.kanalsteg:
                conf["scan_step_khz"] = a.kanalsteg
            if a.max_lyssning is not None:
                conf["scan_max_listen"] = a.max_lyssning
            if a.vantetid is not None:
                conf["scan_hold"] = a.vantetid
            if a.web is not None:
                conf["web_enabled"] = True
                if a.web:
                    conf["web_port"] = a.web
            win = MainWindow(self, conf, a)
            self.win = win
            win.present()
            on_unix_signals(lambda: win.close())           # stäng snyggt (sparar inställningar, loggar)
            log.info("Fönstret öppnat")

    log.info("=== %s %s startar (Python %s) ===", APP_NAME, VERSION, sys.version.split()[0])
    app = App(args)
    return app.run([sys.argv[0]])


# ════════════════════════════════════════════════════════════════════
# 8. KOMMANDORAD
# ════════════════════════════════════════════════════════════════════
def selftest_webrx():
    """Webbmottagarlistan utan nätverk: lokator, avstånd, sammanslagning och svensk-filtret."""
    W = webbmottagare
    ok = True
    la, lo = W.locator_to_latlon("JO99bg")
    ok &= abs(la - 59.2708) < 0.001 and abs(lo - 18.125) < 0.001
    ok &= round(W.distance_km((59.33, 18.07), (57.71, 11.97))) in range(390, 400)    # Stockholm–Göteborg
    a = [W._rx("http://lodsdr.proxy.kiwisdr.com:8073", "SM5VXO", "KiwiSDR", källor=["A"], pos=(58.4, 15.6))]
    b = [W._rx("http://lodsdr.proxy.kiwisdr.com", "SM5VXO Linköping", "KiwiSDR", källor=["B"], users=1, users_max=8),
         W._rx("http://webrx.sk3gk.se:8074", "SK3GK Kungsberget", "OpenWebRX", källor=["B"])]
    c = [W._rx("http://x.example:8073", "SM5VXO", "KiwiSDR", källor=["C"], pos=(58.4, 15.6), ext_api=0)]
    m = W.merge(a, b, c)            # samma Kiwi: proxy med/utan port (A+B) och samma namn+plats (C)
    ok &= len(m) == 2 and m[0]["users_max"] == 8 and m[0]["ext_api"] == 0 and m[0]["källor"] == ["A", "B", "C"]
    ok &= W.is_swedish(m[1]) and not W.kiwi_ok(m[0])
    ok &= len(W.builtin()) == len(W.BUILTIN) and any("sk6ag" in r["url"] for r in W.builtin())
    print(f"  Webbmottagare (lokator, avstånd, sammanslagning): {'OK' if ok else 'FEL'}")
    return ok


def list_webrx(query, position=None):
    """--lista-webbmottagare: hämta listorna (eller cachen) och skriv ut de som matchar."""
    cat = webbmottagare.Catalog(WEBRX_CACHE, load_conf().get("webrx_own", []))
    if cat.stale():
        print("Hämtar listor från kiwisdr.com och receiverbook.de …", file=sys.stderr)
        ev = threading.Event()
        cat.refresh(ev.set)
        ev.wait(90)
        for k, e in cat.errors.items():
            print(f"  kunde inte hämta {k}: {e}", file=sys.stderr)
    L = cat.all()
    q = (query or "").strip()
    if q.lower() in ("sverige", "sweden"):
        L = [r for r in L if webbmottagare.is_swedish(r)]
    elif q:
        L = [r for r in L if webbmottagare.matches(r, q)]
    home = None
    try:
        home = webbmottagare.parse_position(position or load_conf().get("my_pos", ""))
    except ValueError as e:
        print(f"Ogiltig position: {e}", file=sys.stderr)
    if len(L) <= 60:
        ev = threading.Event()
        cat.probe([r["url"] for r in L], ev.set)
        ev.wait(30)
        L = [r for r in cat.all() if webbmottagare.norm_url(r["url"]) in {webbmottagare.norm_url(x["url"]) for x in L}]
    L.sort(key=lambda r: (webbmottagare.distance_km(home, r["pos"]) or 1e9) if home else r["namn"].lower())
    for r in L:
        print(f"{r['typ']:9s} {r['url']:45s} {r['namn'][:50]}")
        print(f"{'':9s} {webbmottagare.summary(r, home)}")
    print(f"{len(L)} mottagare")
    return 0


def scan_plan(chans, sr, half=0.4, guard=3 * BIN_HZ):
    """Dela upp kanaler ({f, bw, …}) i fönster om ±half·sr, så att varje fönster kräver en enda
    omstämning. Kanaler för nära fönstrets mitt (DC-spiken) flyttas till ett senare fönster."""
    rem = sorted(chans, key=lambda c: c["f"])
    wins = []
    while rem:
        c0 = rem[0]["f"] + sr * (half - 0.02)
        mem = [ch for ch in rem if abs(ch["f"] - c0) <= sr * half and abs(ch["f"] - c0) > ch["bw"] / 2 + guard]
        rem = [ch for ch in rem if ch not in mem]
        wins.append({"c": c0, "chans": mem})
    return wins


def selftest_scan():
    """Skannerns fönsterplanering: få omstämningar, alla kanaler med, ingen nära DC."""
    ch = lambda f: {"f": f * 1e6, "bw": 12500, "mode": "NFM", "name": ""}
    chans = [ch(f) for f in (144.8, 145.0, 145.5, 146.0, 131.725, 156.8, 161.975, 162.025, 118.1)]
    chans += [ch(446.00625 + i * 0.0125) for i in range(16)]            # PMR446: 16 kanaler
    w = scan_plan(chans, SR)
    flat = [c for wd in w for c in wd["chans"]]
    ok = len(flat) == len(chans) and len({id(c) for c in flat}) == len(chans)
    ok &= all(abs(c["f"] - wd["c"]) <= SR * 0.4 and abs(c["f"] - wd["c"]) > 6250 + 3 * BIN_HZ
              for wd in w for c in wd["chans"])
    ok &= len(w) == 7                    # 118,1 | 131,725 | 144,8–145,5 | 146,0 | 156,8 | 161,975–162,025 | PMR446
    print(f"  Skanner (fönsterplanering, {len(chans)} kanaler i {len(w)} fönster): {'OK' if ok else 'FEL'}")
    return ok


def selftest_iq():
    """IQ-inspelning och uppspelning: skriv SigMF (med omstämning), WAV och rå cf32 i en annan takt,
    spela upp dem med FileSource och kontrollera frekvens, mittfrekvens och nivå."""
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory(prefix="signalspanaren-iq-") as d:
        t = np.arange(BLOCK) / SR

        def tone(f, k):
            return (0.3 * np.exp(2j * np.pi * f * (t + k * BLOCK / SR))).astype(np.complex64)

        def peak(y, rate):
            S = np.abs(np.fft.fft(y))
            return np.fft.fftfreq(len(y), 1 / rate)[int(np.argmax(S))]

        def play(path, n_blocks):
            src = iqfil.FileSource(iqfil.IQReader(path), SR, BLOCK, speed=0)
            src.open()
            out = []
            try:
                for _ in range(n_blocks):
                    out.append(src.read())
            except EOFError:
                pass
            return out

        for fmt in ("cu8", "cf32_le", "wav"):
            base = os.path.join(d, f"t_{fmt}")
            w = iqfil.WavWriter(base, SR, 145e6) if fmt == "wav" else iqfil.SigMFWriter(base, SR, 145e6, fmt)
            for k in range(8):
                w.write(tone(50e3, k), 145e6)
            if fmt != "wav":
                for k in range(8):
                    w.write(tone(-80e3, k), 146e6)        # omstämning mitt i inspelningen
            path = w.close()
            if fmt == "wav":
                path = w.paths[0]
            r = play(path, 40)
            good = len(r) >= 7 and abs(peak(r[2][0], SR) - 50e3) < 30 and r[2][1] == 145e6
            if fmt != "wav":
                good &= len(r) >= 15 and abs(peak(r[12][0], SR) + 80e3) < 30 and r[12][1] == 146e6
            lvl = float(np.sqrt(np.mean(np.abs(r[2][0]) ** 2))) if r else 0
            good &= abs(lvl - 0.3) < 0.02
            ok &= good
            print(f"  IQ {fmt:7s} spela in → spela upp: {'OK' if good else 'FEL'} ({len(r)} block, nivå {lvl:.3f})")
        n = int(48000 * 3)
        x = (0.3 * np.exp(2j * np.pi * 1000 * np.arange(n) / 48000)).astype(np.complex64)
        path = os.path.join(d, "ton_48000sps_7.074MHz.cf32")
        x.tofile(path)
        r = play(path, 30)
        y = np.concatenate([b[0] for b in r[2:10]])
        f = peak(y, SR)
        lvl = float(np.sqrt(np.mean(np.abs(y) ** 2)))
        good = abs(f - 1000) < 5 and r[0][1] == 7.074e6 and abs(lvl - 0.3) < 0.02
        ok &= good
        print(f"  IQ rå cf32 48 kS/s (namn ger takt/frekvens): {'OK' if good else 'FEL'} (topp {f:.0f} Hz, nivå {lvl:.3f})")
    return ok


def selftest(seconds=4.0):
    """Kör DSP-motorn mot simulatorn utan fönster och skriver ut resultat."""
    print(f"{APP_NAME} självtest – kör simulatorn i {seconds:.0f} s ...")
    e = Engine()
    e.center = 100.0e6 + SR * 0.15
    e.mode = "WFM"
    e.bw = DEFAULT_BW["WFM"]
    e.offset = 100.0e6 - e.center
    bytes_out = [0]

    class Count:
        def push(self, b):
            bytes_out[0] += len(b)
    e.add_sink(Count())
    src = SimSource()
    src.set("freq", e.center)
    errors = []
    e.on_error = errors.append
    e.start(src)
    t_end = time.time() + seconds
    modes_ok = []
    for m in MODES:
        e.mode = m
        e.bw = DEFAULT_BW[m]
        time.sleep(seconds / len(MODES))
        modes_ok.append((m, e.level))
    e.stop()
    changes, _ = e.detector.pop_changes()
    st = e.stats
    print(f"  Block behandlade: {st['blocks']}  (förväntat ca {int(seconds * SR / BLOCK)})")
    print(f"  DSP-tid: {st['proc_ms']:.1f} ms per block (gräns {BLOCK / SR * 1000:.1f} ms), "
          f"överskridanden: {st['overruns']}")
    print(f"  Ljud producerat: {bytes_out[0] / 2 / AF:.1f} s")
    print("  Nivå per läge: " + ", ".join(f"{m} {lv:.1f} dB" for m, lv in modes_ok))
    print(f"  Signaler hittade: {len(changes)}")
    for t in sorted(changes, key=lambda t: t["f"]):
        print(f"    {t['f'] / 1e6:10.4f} MHz  {fmt_bw(t['bw']):>10}  {t['peak']:6.1f} dB  {t['cls']}")
    # ACARS: syntetiskt meddelande genom hela avkodarkedjan, båda bitkonventionerna
    acars_ok = True
    for nrzi in (False, True):
        burst = avkodare.make_acars_burst(SR, nrzi=nrzi, reg=".SE-TST", text="M01ATS0001SJALVTEST")
        total = (int(SR * 0.6) + len(burst)) // BLOCK * BLOCK + 2 * BLOCK
        sig = np.zeros(total, np.complex64)
        sig[int(SR * 0.3):int(SR * 0.3) + len(burst)] = burst * 0.05
        n = np.arange(total)
        sig = sig * np.exp(2j * np.pi * -150e3 * n / SR)
        rng = np.random.default_rng(7)
        sig += (0.003 * (rng.standard_normal(total) + 1j * rng.standard_normal(total))).astype(np.complex64)
        ch = avkodare.AcarsChannel(131.725e6, SR)
        msgs = []
        for k in range(0, total, BLOCK):
            for seg, lvl in ch.feed(sig[k:k + BLOCK].astype(np.complex64), 131.875e6):
                m, why = avkodare.decode_acars_audio(np.abs(seg))
                if m:
                    msgs.append(m)
        good = any(m["reg"] == "SE-TST" and m["flight"] == "TS0001" and m["text"] == "SJALVTEST" for m in msgs)
        acars_ok &= good
        print(f"  ACARS-avkodare ({'NRZI' if nrzi else 'direkt'}): {'OK' if good else 'FEL'}")
    # AIS och APRS genom hela kedjan
    def chain(burst, f, c, cutoff, kind):
        sig = np.concatenate((np.zeros(int(SR * 0.3), np.complex64), burst * 0.05,
                              np.zeros(int(SR * 0.3), np.complex64)))
        sig = np.concatenate((sig, np.zeros(-len(sig) % BLOCK, np.complex64)))
        sig = sig * np.exp(2j * np.pi * (f - c) * np.arange(len(sig)) / SR)
        rng2 = np.random.default_rng(11)
        sig = (sig + 0.002 * (rng2.standard_normal(len(sig)) + 1j * rng2.standard_normal(len(sig)))).astype(np.complex64)
        ch = avkodare.BurstChannel(f, SR, cutoff, kind)
        segs = []
        for k in range(0, len(sig), BLOCK):
            segs += ch.feed(sig[k:k + BLOCK], c)
        return segs
    fr = [x for sg, _l in chain(avkodare.make_ais_burst(SR), 161.975e6, 162.2e6, 7500, "AIS")
          for x in avkodare.decode_ais(sg)]
    m = avkodare.parse_ais(fr[0]) if fr else None
    ais_ok = bool(m and m["src"] == "265123456" and m["pos"] == (59.3293, 18.0686))
    print(f"  AIS-avkodare: {'OK' if ais_ok else 'FEL'}")
    fr = [x for sg, _l in chain(avkodare.make_aprs_burst(SR), 144.8e6, 145.03e6, 8000, "APRS")
          for x in avkodare.decode_aprs(sg)]
    ax = avkodare.parse_ax25(fr[0]) if fr else None
    aprs_ok = bool(ax and ax["src"] == "SA0ABC-9" and avkodare.parse_aprs(ax)[0] == "Position")
    print(f"  APRS-avkodare: {'OK' if aprs_ok else 'FEL'}")
    # Signalanalys: RTTY till klartext och igenkänning av ett militärt HF-modem
    def tap_run(sig, off, bw, secs=3.5, dial=None):
        an = analys.Analyzer(SR)
        an.set_target(14.0e6 + off, bw)
        L = len(sig)
        rng3 = np.random.default_rng(3)
        text, feat = "", None
        for k in range(int(secs * SR / BLOCK)):
            n = k * BLOCK + np.arange(BLOCK)
            pos = n / (SR / 48000)
            y = analys.loop_sample(sig, pos)
            x = 0.05 * y * np.exp(2j * np.pi * off * n / SR) + 0.006 * (rng3.standard_normal(BLOCK) +
                                                                         1j * rng3.standard_normal(BLOCK))
            an.tap.feed(x.astype(np.complex64), 14.0e6)
            if k % 8 == 7 and k * BLOCK > SR:
                r = an.step(now=k * BLOCK / SR)
                if r:
                    feat, txt = r
                    text += txt
        ac = (14.0e6 + off + feat.get("f_mid", 0) - dial) if dial else None
        return feat, text, analys.signaldb.match(feat, 14.0e6 + off, None, ac)
    feat, text, _k = tap_run(analys.make_rtty(48000, 50, 450, "RYRY CQ DE SJALVTEST 73 " * 8), 20e3, 1000, secs=6)
    rtty_ok = feat["fam"] == "FSK2" and "DE SJALVTEST" in text
    print(f"  Signalanalys RTTY 50/450: {'OK' if rtty_ok else 'FEL'} ({feat['klass']}, {feat.get('baud') or 0:.1f} Bd, "
          f"text {text.strip()[:40]!r})")
    feat, _t, kand = tap_run(analys.make_psk(48000, 2400, 8, 8.0), 51800, 3000, dial=14.05e6)
    psk_ok = feat["fam"] == "PSK8" and bool(kand) and kand[0][1]["namn"] in ("STANAG 4285", "MIL-STD-188-110A/B (seriell)")
    print(f"  Signalanalys 8PSK 2400 Bd: {'OK' if psk_ok else 'FEL'} ({feat['klass']}, "
          f"{kand[0][1]['namn'] if kand else 'ingen kandidat'})")
    V = analysavkodare
    tests = [
        ("NAVTEX", V.make_sitor_b(48000, "ZCZC SA01\nSJALVTEST NAVTEX\nNNNN\n"), 20e3, 500, 24, "SJALVTEST NAVTEX"),
        ("POCSAG", np.concatenate((V.make_pocsag(48000, 1200, [(1234567, 3, "SJALVTEST POCSAG", False)]),
                                   np.zeros(48000, np.complex64))), 25e3, 15000, 7, "SJALVTEST POCSAG"),
        ("ALE", V.make_ale(48000, [("TO", "SM5")] * 3 + [("TIS", "HQ1")] * 2), 41625, 3000, 6, "HQ1 anropar SM5"),
        ("DMR", V.make_dmr(48000, bursts=36), 25e3, 12500, 4, "grupp 2402 från 2400123"),
        ("PSK31", V.make_psk31(48000, "DE SJALVTEST PSK31 "), 20e3, 300, 7, "SJALVTEST PSK31"),
    ]
    dec_ok = True
    for name, sig, off, bw, secs, want in tests:
        feat, text, _k = tap_run(sig, off, bw, secs=secs)
        good = want in text
        dec_ok &= good
        print(f"  Analysavkodare {name}: {'OK' if good else 'FEL'} ({feat['klass'] if feat else '–'})")
    iq_ok = selftest_iq()
    webrx_ok = selftest_webrx()
    scan_ok = selftest_scan()
    try:
        iq_ok &= kiwisdr.selftest(SR, BLOCK)
    except Exception as ex:
        print(f"  KiwiSDR: FEL ({ex})")
        iq_ok = False
    try:
        import adsb
        adsb_ok = adsb.selftest(verbose=True)
    except Exception as ex:
        print(f"  ADS-B-avkodare: FEL ({ex})")
        adsb_ok = False
    ok = not errors and st["blocks"] > 0 and bytes_out[0] > 0 and acars_ok and ais_ok and aprs_ok and adsb_ok and rtty_ok and psk_ok and dec_ok and iq_ok and webrx_ok and scan_ok
    print("RESULTAT: " + ("OK" if ok else f"FEL {errors}"))
    log.info("Självtest: %s (%d block, %.1f ms/block)", "OK" if ok else "FEL", st["blocks"], st["proc_ms"])
    return 0 if ok else 1


def main():
    import argparse
    p = argparse.ArgumentParser(
        prog="signalspanaren",
        description="Signalspanaren – SDR-mottagare, radioskanner och signallogg. Se: signalspanaren --manual")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    p.add_argument("--manual", action="store_true", help="visa manualen")
    p.add_argument("--rapport", action="store_true", help="skriv ut en felrapport (för att klistra in i Claude Code)")
    p.add_argument("--logg", nargs="?", type=int, const=100, metavar="N", help="visa de senaste N raderna i loggen")
    p.add_argument("--fel", action="store_true", help="visa bara fel och varningar ur loggen")
    p.add_argument("--rensa-logg", action="store_true", help="töm loggfilerna")
    p.add_argument("--sokvagar", action="store_true", help="visa var program, inställningar och loggar ligger")
    p.add_argument("--sjalvtest", action="store_true", help="testa signalbehandlingen med simulatorn (utan fönster)")
    p.add_argument("--aterstall", action="store_true", help="återställ alla inställningar till standard")
    p.add_argument("--kalla", dest="source", choices=SOURCES, help="mottagarkälla: rtl, tcp, kiwi, sim eller file (IQ-fil, se --spela)")
    p.add_argument("--simulator", action="store_true", help="samma som --kalla sim")
    p.add_argument("--tcp", metavar="VÄRD:PORT", help="anslut till en rtl_tcp-server")
    p.add_argument("--kiwi", metavar="ADRESS", help="hämta I/Q från en KiwiSDR på nätet, "
                   "t.ex. http://sk5sm.proxy.kiwisdr.com:8073/ (0–30 MHz, ca 12 kHz bandbredd)")
    p.add_argument("--frekvens", dest="freq", metavar="FREKV", help="startfrekvens, t.ex. 145.5 eller 7074k")
    p.add_argument("--mod", dest="mode", type=str.upper, choices=MODES, help="modulering")
    p.add_argument("--start", action="store_true", help="börja ta emot direkt")
    p.add_argument("--spela", metavar="FIL", help="spela upp en IQ-fil (SigMF, WAV, .cu8, .cf32 …) som källa")
    p.add_argument("--takt", type=float, metavar="S/s", help="samplingstakt för rå IQ-fil (med --spela)")
    p.add_argument("--mittfrekvens", metavar="FREKV", help="mittfrekvens för rå IQ-fil (med --spela)")
    p.add_argument("--webb", dest="web", nargs="?", type=int, const=0, metavar="PORT",
                   help="starta webbgränssnittet (standardport 8073)")
    p.add_argument("--squelch", type=float, metavar="DB", help="squelchnivå i dB, t.ex. -8")
    p.add_argument("--snr", type=float, metavar="DB",
                   help="skannern stannar när signalen är DB över brusgolvet (standard 10)")
    p.add_argument("--skanna", choices=["bokmarken", "intervall"], help="starta skanning direkt")
    p.add_argument("--intervall", metavar="START-STOPP", help="skanningsintervall i MHz, t.ex. 87.5-108")
    p.add_argument("--kanalsteg", type=float, metavar="KHZ", help="kanalsteg vid intervallskanning, t.ex. 12.5")
    p.add_argument("--vantetid", type=float, metavar="S", help="sekunder att stanna efter att signalen försvunnit")
    p.add_argument("--max-lyssning", type=float, metavar="S",
                   help="lämna en signal efter högst S sekunder (0 = av)")
    p.add_argument("--acars", action="store_true", help="slå på ACARS-avkodning")
    p.add_argument("--ais", action="store_true", help="slå på AIS-avkodning (fartyg)")
    p.add_argument("--aprs", action="store_true", help="slå på APRS-avkodning")
    p.add_argument("--direktsampling", choices=["av", "i", "q"], help="direktsampling för HF (q för RTL-SDR v3/NESDR)")
    p.add_argument("--fartygskarta", action="store_true", help="öppna fartygskartan (AIS) direkt")
    p.add_argument("--webbmottagare", action="store_true",
                   help="öppna listan över webbmottagare (KiwiSDR, OpenWebRX, WebSDR) direkt")
    p.add_argument("--lista-webbmottagare", nargs="?", const="", metavar="SÖK",
                   help="skriv ut webbmottagare i terminalen, t.ex. --lista-webbmottagare sverige")
    p.add_argument("--position", metavar="LOKATOR", help="din position för avstånd, t.ex. JO99bg eller 59.33,18.07")
    p.add_argument("--aktivitetskarta", action="store_true", help="öppna aktivitetskartan direkt")
    p.add_argument("--kalibrera", action="store_true", help="öppna frekvenskalibreringen direkt")
    p.add_argument("--analysera", metavar="FREKVENS",
                   help="analysera signalen på FREKVENS i fliken Signalanalys (t.ex. 99.77 eller 14074k)")
    p.add_argument("--flik", choices=["signallogg", "bokmarken", "skanner", "avkodat", "analys"], help="flik att visa längst ner")
    p.add_argument("--panel", nargs="?", const="r9500", choices=["r9500", "r7100", "r2500"],
                   help="öppna frontpanelen direkt (r9500, r7100 eller r2500)")
    p.add_argument("--skarmbild", nargs=2, metavar=("FIL", "SEKUNDER"),
                   help="spara en skärmbild av fönstret (PNG) efter angivet antal sekunder")
    p.add_argument("--debug", action="store_true", help="detaljerad loggning, även i terminalen")
    args = p.parse_args()
    if args.simulator:
        args.source = "sim"

    setup_logging(verbose=args.debug)
    for old, new in _MIGRATED:
        log.info("Namnbyte till Signalspanaren: flyttade %s → %s", old, new)

    if args.manual:
        try:
            with open(MANUAL_FILE, encoding="utf-8") as f:
                txt = f.read()
        except OSError:
            print(f"Hittar inte manualen ({MANUAL_FILE})")
            return 1
        if sys.stdout.isatty() and shutil.which("less"):
            subprocess.run(["less", "-R"], input=txt, text=True)
        else:
            print(txt)
        return 0
    if args.rapport:
        print(build_report())
        print(f"\n(Rapporten är också sparad i {REPORT_FILE})")
        return 0
    if args.logg is not None or args.fel:
        lines = _tail(LOG_FILE, 100000 if args.fel else args.logg)
        if args.fel:
            out, keep = [], False
            for l in lines:
                if re.match(r"^\d{4}-\d\d-\d\d ", l):
                    keep = any(k in l for k in ("[ERROR]", "[WARNING]", "[CRITICAL]"))
                if keep:
                    out.append(l)
            lines = out[-(args.logg or 200):]
            if not lines:
                print("Inga fel eller varningar i loggen.")
        sys.stdout.write("".join(lines))
        return 0
    if args.rensa_logg:
        for f in (LOG_FILE, STDERR_FILE, REPORT_FILE):
            try:
                open(f, "w").close()
            except OSError:
                pass
        print("Loggfilerna är tömda.")
        return 0
    if args.sokvagar:
        print(f"Program:        {os.path.abspath(__file__)}")
        print(f"Manual:         {MANUAL_FILE}")
        print(f"Inställningar:  {CONF_FILE}")
        print(f"Egen stil:      {CSS_FILE}")
        print(f"Logg:           {LOG_FILE}")
        print(f"Felutskrifter:  {STDERR_FILE}")
        print(f"Felrapport:     {REPORT_FILE}")
        print(f"Inspelningar:   {rec_dir()}")
        return 0
    if args.aterstall:
        if os.path.exists(CONF_FILE):
            os.replace(CONF_FILE, CONF_FILE + ".bak")
            print(f"Inställningarna är återställda (gamla sparade som {CONF_FILE}.bak)")
        else:
            print("Inga sparade inställningar fanns.")
        return 0
    if args.lista_webbmottagare is not None:
        return list_webrx(args.lista_webbmottagare, args.position)
    if args.sjalvtest:
        return selftest()
    if args.intervall:
        try:
            lo_s, _, hi_s = args.intervall.partition("-")
            if parse_freq(hi_s) <= parse_freq(lo_s):
                raise ValueError("stopp måste vara högre än start")
        except ValueError as e:
            p.error(f"--intervall: {e} (exempel: 87.5-108)")
    if args.freq:
        try:
            parse_freq(args.freq)
        except ValueError as e:
            p.error(str(e))
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
