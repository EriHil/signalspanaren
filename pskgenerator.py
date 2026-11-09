#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
PSK31/PSK63-generator: syntetisk BPSK med varicode och facit – för att mäta och öva PSK-avkodaren.
Samma upplägg som cwgenerator.py och rttygenerator.py.

Stationer: takt (31,25/62,5 Bd), frekvensfel och drift, fädning (Rice med dopplerspridning – vrider
fasen, det svåra för PSK), QSB. Bandet: brus, åskknaster och grannar (andra PSK-stationer 50–100 Hz
bort, som i PSK-delen av bandet).

  python3 pskgenerator.py --scen trangt --sek 40 --ut psk.wav      ljud (mitt 1000 Hz) + .txt med facit
  python3 pskgenerator.py --lista
"""
import argparse
import os
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cwgenerator as CG   # noqa: E402


def cq(rng):
    a = CG.anrop(rng)
    return f"CQ CQ CQ de {a} {a} {a} pse k "


def qso(rng):
    a, b = CG.anrop(rng), CG.anrop(rng)
    return (f"{a} de {b} - Good evening, thanks for the call. Your RST is 599 599. My name is "
            f"{rng.choice(['Erik', 'Anna', 'Per', 'Ole', 'Hans'])} and QTH is "
            f"{rng.choice(['Stockholm', 'Oslo', 'Berlin', 'Turku'])}. Rig is IC-7300 at 30 W. "
            f"How copy? {a} de {b} kn ")


def macro(rng):
    a = CG.anrop(rng)
    return f"{a} 599 {rng.integers(1, 999):03d} TU {a} 599 {rng.integers(1, 999):03d} TU "


def normera(text):
    return " ".join(text.upper().split())


@dataclass
class Station:
    text: str
    baud: float = 31.25
    off_hz: float = 0.0
    niva_db: float = 0.0
    drift_hz: float = 0.0
    fd: float = 0.0              # fädning: dopplerspridning (Hz) – vrider fasen
    rice: float = 1.0
    qsb: float = 0.0
    qsb_period: float = 8.0
    start_s: float = 0.0
    tomgang_bitar: int = 32      # nollor (vändningar) före texten


def station_signal(st, fs, n, rng):
    """Komplext basband för en PSK-station och facit (tecknen som hann sändas)."""
    from analysavkodare import VARICODE_ENC
    bits, slut = [0] * st.tomgang_bitar, []
    for ch in st.text:
        code = VARICODE_ENC.get(ch) or VARICODE_ENC.get(ch.upper(), "1")
        bits += [int(b) for b in code] + [0, 0]
        slut.append((len(bits), ch))
    bits += [0] * 16
    sps = fs / st.baud
    i0 = int(st.start_s * fs)
    env = np.zeros(n, np.float32)
    cur = 1.0
    for k, b in enumerate(bits):
        a, e = i0 + int(round(k * sps)), i0 + int(round((k + 1) * sps))
        if a >= n:
            break
        m = e - a
        seg = cur * np.cos(np.pi * np.arange(m) / m) if b == 0 else np.full(m, cur)
        if b == 0:
            cur = -cur
        env[a:min(e, n)] = seg[:max(0, min(e, n) - a)]
    sant = "".join(ch for k, ch in slut if i0 + k * sps < n - 0.05 * fs)
    tt = np.arange(n) / fs
    f = st.off_hz + st.drift_hz * tt / max(tt[-1], 1e-9)
    z = env * np.exp(2j * np.pi * np.cumsum(f) / fs + 1j * rng.uniform(0, 2 * np.pi))
    if st.fd:
        z = z * CG._flutter(n, fs, st.fd, st.rice, rng)
    if st.qsb:
        z = z * (1 - st.qsb * (0.5 + 0.5 * np.sin(2 * np.pi * tt / st.qsb_period + rng.uniform(0, 6))))
    return (z * 10 ** (st.niva_db / 20)).astype(np.complex64), normera(sant)


@dataclass
class Band:
    stationer: list
    snr_db: float = 20.0         # för station 0, i 500 Hz bandbredd
    qrn: float = 0.0
    cw: list = field(default_factory=list)


def generera(band, fs=12000, sek=40.0, seed=1):
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


def scenario(namn, rng):
    S = Station
    if namn == "ren":
        return Band([S(qso(rng) * 2)], snr_db=20)
    if namn == "svag":                 # PSK31 är smal: 0 dB i 500 Hz är ~9 dB i signalens bredd
        return Band([S(qso(rng) * 2)], snr_db=0)
    if namn == "qsb":
        return Band([S(qso(rng) * 2, qsb=0.8, qsb_period=6)], snr_db=12)
    if namn == "fladder":              # flervägsutbredning: fasen vrids (polarflutter)
        return Band([S(qso(rng) * 2, fd=0.5, rice=3.0)], snr_db=15)
    if namn == "drift":                # sändaren driver 15 Hz under klippet
        return Band([S(qso(rng) * 2, drift_hz=15, off_hz=-8)], snr_db=15)
    if namn == "trangt":               # PSK-delen av bandet: grannar 60 och 100 Hz bort, en starkare
        return Band([S(qso(rng) * 2), S(cq(rng) * 4, off_hz=60, niva_db=6), S(macro(rng) * 4, off_hz=-100, niva_db=3)],
                    snr_db=18)
    if namn == "qrn":
        return Band([S(qso(rng) * 2)], snr_db=12, qrn=3)
    if namn == "psk63":
        return Band([S(qso(rng) * 3, baud=62.5)], snr_db=15)
    raise SystemExit(f"Okänt scenario: {namn}")


SCENARIER = ["ren", "svag", "qsb", "fladder", "drift", "trangt", "qrn", "psk63"]


def skriv(path, z, fs, facit, mitt=1000.0):
    import wave
    t = np.arange(len(z)) / fs
    a = np.real(z * np.exp(2j * np.pi * mitt * t))
    a = a / (np.abs(a).max() + 1e-12) * 0.8
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
    with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as fh:
        fh.write(facit + "\n")


def main():
    p = argparse.ArgumentParser(prog="pskgenerator", description="Syntetisk PSK31/PSK63 med facit.")
    p.add_argument("--scen", default="ren")
    p.add_argument("--sek", type=float, default=40.0)
    p.add_argument("--fs", type=int, default=12000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--ut", default="psk.wav")
    p.add_argument("--lista", action="store_true")
    a = p.parse_args()
    if a.lista:
        print("Scenarier:", ", ".join(SCENARIER))
        return 0
    z, facit = generera(scenario(a.scen, np.random.default_rng(a.seed)), a.fs, a.sek, a.seed)
    skriv(a.ut, z, a.fs, facit)
    print(f"{a.ut}: {a.scen}, {a.sek:.0f} s\nFacit: {facit[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
