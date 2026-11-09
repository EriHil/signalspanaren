#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
RTTY-generator: syntetisk, realistisk Baudot/ITA2-RTTY med facit – för simulatorn, för att öva
RTTY-avkodaren och för att mäta den. Samma upplägg som cwgenerator.py.

Varje station har takt (45,45/50/75/100 Bd), shift, polaritet, stoppbitar (1/1,5/2), taktfel och
frekvensdrift. Utbredningen kan ge selektiv fädning (mark och space fädar var för sig, som på
kortvåg), QSB och flervägsutbredning; bandet får brus, åskknaster och grannar (CW och RTTY).

  python3 rttygenerator.py --scen dwd --sek 60 --ut dwd.wav       ljud (mitt 1500 Hz) + .txt med facit
  python3 rttygenerator.py --scen amator --iq --ut a.wav          IQ i stereo
  python3 rttygenerator.py --lista

NAVTEX/SITOR-B (100 Bd, 170 Hz, FEC): navtex_generera(navtex_scenario(namn, rng)) med scenarierna
ren, svag, selektiv, qrn, drift, trangt och mw518 (mellanvåg 518 kHz: svag, QSB, knaster).
"""
import argparse
import os
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cwgenerator as CG   # noqa: E402  (fladder, anrop, CW-grannar)

LTRS = "\0E\nA SIU\rDRJNFCKTZLWHYPQOBG\x0eMXV\x0f"
FIGS = "\x003\n- '87\r\x054\x07,!:(5+)2#6019?&\x0e./=\x0f"
_L = {c: i for i, c in enumerate(LTRS) if c not in "\0\x0e\x0f"}
_F = {c: i for i, c in enumerate(FIGS) if c not in "\0\x0e\x0f\x05\x07"}
LTRS_KOD, FIGS_KOD = 0x1F, 0x1B


# ════════════════════════════════════════════════════════════════════
# Texter
# ════════════════════════════════════════════════════════════════════
def ryry(rng):
    return "RYRYRYRYRYRYRYRYRYRY THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 0123456789 " * 2


def dwd(rng):
    """Väderbulletin i stil med DWD:s RTTY-sändningar (10100,8/4583/7646 kHz)."""
    t = f"{rng.integers(0, 24):02d}{rng.choice(['00', '30'])}"
    return (f"ZCZC {rng.integers(100, 999)}\r\nSXDL31 DWAV {rng.integers(10, 28)}{t}\r\n"
            f"WETTERLAGE: EIN TIEF {rng.integers(970, 1010)} HPA LIEGT UEBER DER NORDSEE.\r\n"
            f"VORHERSAGE BIS {t} UTC: WIND SW {rng.integers(3, 8)}, SPAETER NW {rng.integers(4, 9)}. "
            f"SEEGANG {rng.integers(1, 5)} M. SICHT GUT.\r\nNNNN\r\n")


def amator(rng):
    a, b = CG.anrop(rng), CG.anrop(rng)
    return (f"RYRYRY CQ CQ CQ DE {a} {a} {a} PSE K\r\n{a} DE {b} {b} K\r\n"
            f"{b} DE {a} GM OM TNX FER CALL UR RST 599 599 NAME IS {rng.choice(['ERIK', 'ANNA', 'PER'])} "
            f"QTH {rng.choice(['STOCKHOLM', 'OSLO', 'BERLIN'])} HW? {b} DE {a} K\r\n")


def contest(rng):
    a = CG.anrop(rng)
    return " ".join(f"{a} 599 {rng.integers(1, 999):03d} {rng.integers(1, 40):02d}" for _ in range(8))


def siffror(rng):
    return " ".join("".join(rng.choice(list("0123456789"), 5)) for _ in range(20))


def normera(text):
    """Facit som avkodaren bör ge: tecken som finns i ITA2, radbrytningar som mellanslag."""
    t = "".join(c if (c in _L or c in _F) else " " for c in text.upper())
    return " ".join(t.replace("\r", " ").replace("\n", " ").split())


# ════════════════════════════════════════════════════════════════════
# Sändare
# ════════════════════════════════════════════════════════════════════
def baudot(text, stoppbitar=1.5, tomgang=1.0, baud=45.45):
    """[(bit, längd i bitar, tecken eller "")]: startbit, 5 databitar (LSB först), stoppbitar.
    tomgang = sekunder mark (vila) före och efter."""
    ut = [(1, tomgang * baud, "")]
    figs = False
    for ch in text.upper():
        if ch in _L:
            if figs and ch not in " \r\n":
                ut += _tecken(LTRS_KOD, stoppbitar, "")
                figs = False
            kod = _L[ch]
        elif ch in _F:
            if not figs:
                ut += _tecken(FIGS_KOD, stoppbitar, "")
                figs = True
            kod = _F[ch]
        else:
            continue
        ut += _tecken(kod, stoppbitar, ch)
        if ch == " ":
            figs = False                           # mottagaren växlar till bokstäver vid mellanslag
    ut.append((1, tomgang * baud, ""))
    return ut


def _tecken(kod, stopp, ch):
    b = [(0, 1.0, "")] + [((kod >> k) & 1, 1.0, "") for k in range(5)]
    return b + [(1, stopp, ch)]                     # tecknet räknas som sänt när stoppbiten är klar


@dataclass
class Station:
    text: str
    baud: float = 45.45
    shift: float = 170.0
    mark_hog: bool = True        # mark på den högre tonen (normal polaritet i basbandet)
    stoppbitar: float = 1.5
    taktfel_ppm: float = 0.0     # sändarens takt avviker (t.ex. 2000 ppm = 0,2 %)
    off_hz: float = 0.0
    niva_db: float = 0.0
    drift_hz: float = 0.0        # frekvensdrift över klippet
    fd: float = 0.0              # selektiv fädning: dopplerspridning (Hz), mark och space var för sig
    rice: float = 1.0
    qsb: float = 0.0
    qsb_period: float = 8.0
    start_s: float = 0.0
    filter_hz: float = 0.0       # sändarens bandbegränsning (0 = hårda omslag)


def station_signal(st, fs, n, rng):
    """Komplext basband (längd n) för en RTTY-station och facit (det som hann sändas)."""
    seq = baudot(st.text, st.stoppbitar, baud=st.baud)
    tb = 1.0 / (st.baud * (1 + st.taktfel_ppm * 1e-6))
    bit = np.ones(n, np.float32)
    t = st.start_s
    sant = []
    for v, L, ch in seq:
        a, b = int(t * fs), int((t + L * tb) * fs)
        if a >= n:
            break
        bit[max(0, a):min(b, n)] = v
        t += L * tb
        if ch and t < n / fs:
            sant.append(ch)
    aktiv = np.zeros(n, bool)
    aktiv[max(0, int(st.start_s * fs)):min(n, int(t * fs))] = True
    return fsk_kanal(bit, aktiv, st, fs, rng), normera("".join(sant))


def fsk_kanal(bit, aktiv, st, fs, rng):
    """Tvåtons-FSK ur bit per sampel (1 = mark) genom stationens sändare och utbredning:
    bandbegränsning, frekvensfel/drift, selektiv fädning (mark och space var för sig) och QSB."""
    n = len(bit)
    if st.filter_hz:
        L = max(1, int(fs / st.filter_hz))
        bit = np.convolve(bit, np.ones(L) / L, mode="same").astype(np.float32)
    tt = np.arange(n) / fs
    df = st.shift / 2 * (1 if st.mark_hog else -1)
    f = st.off_hz + st.drift_hz * tt / max(tt[-1], 1e-9) + (2 * bit - 1) * df
    z = np.exp(2j * np.pi * np.cumsum(f) / fs) * aktiv
    if st.fd:
        # selektiv fädning: mark- och space-tonen har var sin oberoende Rice-kanal
        hm = CG._flutter(n, fs, st.fd, st.rice, rng)
        hs = CG._flutter(n, fs, st.fd, st.rice, rng)
        z = z * (bit * hm + (1 - bit) * hs)
    if st.qsb:
        z = z * (1 - st.qsb * (0.5 + 0.5 * np.sin(2 * np.pi * tt / st.qsb_period + rng.uniform(0, 6))))
    return (z * 10 ** (st.niva_db / 20)).astype(np.complex64)


# ════════════════════════════════════════════════════════════════════
# NAVTEX / SITOR-B (100 Bd, 170 Hz, FEC med repetition)
# ════════════════════════════════════════════════════════════════════
def navtex_text(rng, station="S", amne="A"):
    nr = rng.integers(1, 99)
    return (f"ZCZC {station}{amne}{nr:02d}\n{rng.integers(10, 28)}{rng.integers(0, 24):02d}00 UTC NOV\n"
            f"{rng.choice(['GOTEBORG', 'STOCKHOLM', 'BODO', 'TALLINN'])} RADIO NAVIGATIONAL WARNING "
            f"{rng.integers(100, 999)}/26\nSKAGERRAK. LIGHTBUOY {rng.integers(1, 40)} "
            f"57-{rng.integers(10, 59)}N 010-{rng.integers(10, 59)}E UNLIT.\n"
            f"WIND SW {rng.integers(4, 9)} TO {rng.integers(10, 14)} M/S.\nNNNN\n")


@dataclass
class NavtexStation(Station):
    baud: float = 100.0
    shift: float = 170.0
    fasning: int = 40            # tecken fasning före meddelandet (verklig NAVTEX: ~10 s)


def navtex_signal(st, fs, n, rng):
    """SITOR-B för en NAVTEX-station och facit (meddelandets text)."""
    from analysavkodare import sitor_b_bitar
    bits = sitor_b_bitar(st.text, fasning=st.fasning)
    tb = 1.0 / (st.baud * (1 + st.taktfel_ppm * 1e-6))
    b = np.ones(n, np.float32)
    i0 = int(st.start_s * fs)
    idx = i0 + (np.arange(len(bits)) * tb * fs).astype(np.int64)
    for k, v in enumerate(bits):
        a, e = idx[k], (idx[k + 1] if k + 1 < len(bits) else idx[k] + int(tb * fs))
        if a >= n:
            break
        b[max(0, a):min(e, n)] = v
    aktiv = np.zeros(n, bool)
    aktiv[max(0, i0):min(n, i0 + int(len(bits) * tb * fs))] = True
    return fsk_kanal(b, aktiv, st, fs, rng), normera(st.text)


def navtex_scenario(namn, rng):
    S = NavtexStation
    if namn == "ren":
        return Band([S(navtex_text(rng))], snr_db=25)
    if namn == "svag":
        return Band([S(navtex_text(rng), qsb=0.4)], snr_db=5)
    if namn == "selektiv":
        return Band([S(navtex_text(rng), fd=3, rice=0.7)], snr_db=18)
    if namn == "qrn":                  # 518 kHz på vintern: mycket knaster
        return Band([S(navtex_text(rng))], snr_db=12, qrn=6)
    if namn == "drift":
        return Band([S(navtex_text(rng), drift_hz=30, taktfel_ppm=200, off_hz=25)], snr_db=18)
    if namn == "trangt":               # en annan NAVTEX-station och CW intill
        return Band([S(navtex_text(rng)), S(navtex_text(rng, "B"), off_hz=380, niva_db=6, start_s=3)],
                    snr_db=18, cw=[CG.Station(CG.cq(rng), wpm=22, off_hz=-240, niva_db=3)])
    if namn == "mw518":                # mellanvåg: svag, QSB och knaster
        return Band([S(navtex_text(rng), qsb=0.6, qsb_period=12, fd=0.5, rice=3)], snr_db=8, qrn=3)
    raise SystemExit(f"Okänt NAVTEX-scenario: {namn}")


NAVTEX_SCENARIER = ["ren", "svag", "selektiv", "qrn", "drift", "trangt", "mw518"]


def navtex_generera(band, fs=12000, sek=None, seed=1):
    """Som generera() men stationerna är NAVTEX. sek=None: lagom lång för station 0."""
    from analysavkodare import sitor_b_bitar
    rng = np.random.default_rng(seed)
    st0 = band.stationer[0]
    if sek is None:
        sek = st0.start_s + len(sitor_b_bitar(st0.text, fasning=st0.fasning)) / st0.baud + 2.0
    n = int(fs * sek)
    z = np.zeros(n, np.complex64)
    facit = ""
    for k, st in enumerate(band.stationer):
        s, sant = navtex_signal(st, fs, n, rng)
        z += s
        if k == 0:
            facit = sant
    for c in band.cw:
        z += CG.station_signal(c, fs, n, rng)[0]
    sigma = 10 ** (-band.snr_db / 20) / np.sqrt(2) * np.sqrt(fs / 500)
    z += (sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    if band.qrn:
        for _ in range(rng.poisson(band.qrn * sek)):
            a = int(rng.uniform(0, n))
            L = int(fs * rng.uniform(0.003, 0.03))
            e = min(n, a + L)
            amp = sigma * rng.uniform(5, 30)
            z[a:e] += (amp * np.exp(-np.arange(e - a) / max(1, L / 3)) *
                       (rng.standard_normal(e - a) + 1j * rng.standard_normal(e - a))).astype(np.complex64)
    return z, facit


@dataclass
class Band:
    stationer: list
    snr_db: float = 20.0         # för station 0, i 500 Hz bandbredd
    qrn: float = 0.0
    cw: list = field(default_factory=list)       # CW-grannar (cwgenerator.Station)


def generera(band, fs=12000, sek=60.0, seed=1):
    rng = np.random.default_rng(seed)
    n = int(fs * sek)
    z = np.zeros(n, np.complex64)
    facit = ""
    for k, st in enumerate(band.stationer):
        s, sant = station_signal(st, fs, n, rng)
        z += s
        if k == 0:
            facit = sant
    for c in band.cw:
        z += CG.station_signal(c, fs, n, rng)[0]
    sigma = 10 ** (-band.snr_db / 20) / np.sqrt(2) * np.sqrt(fs / 500)
    z += (sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    if band.qrn:
        for _ in range(rng.poisson(band.qrn * sek)):
            a = int(rng.uniform(0, n))
            L = int(fs * rng.uniform(0.003, 0.03))
            e = min(n, a + L)
            amp = sigma * rng.uniform(5, 30)
            z[a:e] += (amp * np.exp(-np.arange(e - a) / max(1, L / 3)) *
                       (rng.standard_normal(e - a) + 1j * rng.standard_normal(e - a))).astype(np.complex64)
    return z, facit


# ════════════════════════════════════════════════════════════════════
# Scenarier
# ════════════════════════════════════════════════════════════════════
def scenario(namn, rng):
    S = Station
    if namn == "ren":
        return Band([S(ryry(rng))], snr_db=25)
    if namn == "dwd":                  # DWD: 50 Bd, 450 Hz, 1,5 stoppbitar, selektiv fädning
        return Band([S(dwd(rng) * 2, baud=50, shift=450, fd=1.5, rice=2.0)], snr_db=15)
    if namn == "amator":               # 45,45 Bd / 170 Hz, QSB
        return Band([S(amator(rng) * 2, qsb=0.5, off_hz=float(rng.uniform(-20, 20)))], snr_db=15)
    if namn == "svag":
        return Band([S(ryry(rng), qsb=0.4)], snr_db=4)
    if namn == "selektiv":             # djup selektiv fädning
        return Band([S(dwd(rng) * 2, baud=50, shift=450, fd=4, rice=0.5)], snr_db=18)
    if namn == "omvand":               # omvänd polaritet
        return Band([S(amator(rng) * 2, mark_hog=False)], snr_db=20)
    if namn == "drift":                # sändaren driver 40 Hz och takten är 0,3 % fel
        return Band([S(amator(rng) * 2, drift_hz=40, taktfel_ppm=3000)], snr_db=18)
    if namn == "trangt":               # CW och annan RTTY tätt intill
        return Band([S(amator(rng) * 2),
                     S(contest(rng) * 3, off_hz=420, niva_db=6)],
                    snr_db=18, cw=[CG.Station(CG.cq(rng), wpm=22, off_hz=-260, niva_db=3)])
    if namn == "qrn":
        return Band([S(dwd(rng) * 2, baud=50, shift=450)], snr_db=14, qrn=3)
    if namn == "75baud":               # 75 Bd / 850 Hz (militär/marin stil), 1 stoppbit
        return Band([S(siffror(rng) * 2, baud=75, shift=850, stoppbitar=1.0)], snr_db=18)
    if namn == "stopp2":               # 2 stoppbitar och mjuka omslag
        return Band([S(contest(rng) * 2, stoppbitar=2.0, filter_hz=60)], snr_db=18)
    raise SystemExit(f"Okänt scenario: {namn}")


SCENARIER = ["ren", "dwd", "amator", "svag", "selektiv", "omvand", "drift", "trangt", "qrn", "75baud", "stopp2"]


def slumpat(rng):
    baud, shift = [(45.45, 170), (50, 450), (50, 425), (75, 850), (100, 170), (45.45, 425)][rng.integers(0, 6)]
    st = Station(rng.choice([ryry, dwd, amator, contest, siffror])(rng) * 2, baud=baud, shift=shift,
                 mark_hog=bool(rng.random() < 0.8), stoppbitar=float(rng.choice([1.0, 1.5, 2.0])),
                 taktfel_ppm=float(rng.normal(0, 1500)), off_hz=float(rng.uniform(-30, 30)),
                 drift_hz=float(rng.choice([0, 0, 20, -30])),
                 fd=float(rng.choice([0, 0, 1, 3, 6])), rice=float(rng.uniform(0.5, 3)),
                 qsb=float(rng.choice([0, 0, 0.4, 0.8])), filter_hz=float(rng.choice([0, 0, 1.5 * baud])))
    grannar = [Station(contest(rng) * 3, off_hz=float(rng.choice([-1, 1]) * rng.uniform(350, 900)),
                       niva_db=float(rng.uniform(-10, 10)))] if rng.random() < 0.3 else []
    cw = [CG.Station(CG.cq(rng), wpm=22, off_hz=float(rng.choice([-1, 1]) * rng.uniform(200, 700)),
                     niva_db=float(rng.uniform(-10, 6)))] if rng.random() < 0.3 else []
    return Band([st] + grannar, snr_db=float(rng.uniform(3, 25)), qrn=float(rng.choice([0, 0, 1, 4])), cw=cw)


def skriv(path, z, fs, facit, iq=False, mitt=1500.0):
    import wave
    if iq:
        s = z / (np.abs(z).max() + 1e-12) * 0.8
        data, ch = np.stack((s.real, s.imag), axis=1), 2
    else:
        t = np.arange(len(z)) / fs
        a = np.real(z * np.exp(2j * np.pi * mitt * t))
        data, ch = (a / (np.abs(a).max() + 1e-12) * 0.8)[:, None], 1
    with wave.open(path, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())
    with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as fh:
        fh.write(facit + "\n")


def main():
    p = argparse.ArgumentParser(prog="rttygenerator", description="Syntetisk RTTY med facit.")
    p.add_argument("--scen", default="ren")
    p.add_argument("--sek", type=float, default=60.0)
    p.add_argument("--fs", type=int, default=12000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--iq", action="store_true")
    p.add_argument("--ut", default="rtty.wav")
    p.add_argument("--lista", action="store_true")
    a = p.parse_args()
    if a.lista:
        print("Scenarier:", ", ".join(SCENARIER))
        return 0
    z, facit = generera(scenario(a.scen, np.random.default_rng(a.seed)), a.fs, a.sek, a.seed)
    skriv(a.ut, z, a.fs, facit, a.iq)
    print(f"{a.ut}: {a.scen}, {a.sek:.0f} s\nFacit: {facit[:100]}{'…' if len(facit) > 100 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
