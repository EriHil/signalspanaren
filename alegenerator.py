#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
ALE-generator (MIL-STD-188-141 2G): 8-FSK 125 Bd med Golay-kodade ord och facit – för att mäta och
öva ALE-avkodaren. Samma upplägg som de andra generatorerna.

Anropen: skanningsanrop (TO … upprepat), TIS-avslut, sonderingar (TWAS). Kanalen: brus, fädning
(Rice, dopplerspridning), åskknaster, frekvensfel, spegelvänd (LSB) och grannar.

  python3 alegenerator.py --lista
"""
import os
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cwgenerator as CG   # noqa: E402

FS = 12000


def anrop(rng, n=2):
    """n anrop: [(ord …)] där varje anrop är TO <mål> ×k följt av TIS <egen> ×2."""
    ut = []
    for _ in range(n):
        mal = "".join(rng.choice(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"), 3))
        egen = "".join(rng.choice(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"), 3))
        ut += [("TO", mal)] * int(rng.integers(10, 16)) + [("TIS", egen)] * 2   # skanningsanrop: TO i flera s
    return ut


@dataclass
class Station:
    ord: list
    off_hz: float = 0.0
    niva_db: float = 0.0
    lsb: bool = False
    fd: float = 0.0
    rice: float = 1.0
    start_s: float = 0.5


def station_signal(st, fs, n, rng):
    from analysavkodare import make_ale
    z = make_ale(fs, st.ord, lsb=st.lsb)
    ut = np.zeros(n, np.complex64)
    i0 = int(st.start_s * fs)
    m = min(len(z), n - i0)
    if m > 0:
        ut[i0:i0 + m] = z[:m]
    t = np.arange(n) / fs
    ut = ut * np.exp(2j * np.pi * st.off_hz * t)
    if st.fd:
        ut = ut * CG._flutter(n, fs, st.fd, st.rice, rng)
    return (ut * 10 ** (st.niva_db / 20)).astype(np.complex64)


@dataclass
class Band:
    stationer: list
    snr_db: float = 20.0          # för station 0, i 3 kHz bandbredd (en SSB-kanal)
    qrn: float = 0.0
    cw: list = field(default_factory=list)


def generera(band, fs=FS, seed=1):
    """Returnerar (z, facit = de olika orden i ordning, utan upprepningar i följd)."""
    from analysavkodare import make_ale
    rng = np.random.default_rng(seed)
    st0 = band.stationer[0]
    sek = st0.start_s + len(make_ale(fs, st0.ord)) / fs + 1.0
    n = int(fs * sek)
    z = np.zeros(n, np.complex64)
    for st in band.stationer:
        z += station_signal(st, fs, n, rng)
    for c in band.cw:
        z += CG.station_signal(c, fs, n, rng)[0]
    sigma = 10 ** (-band.snr_db / 20) / np.sqrt(2) * np.sqrt(fs / 3000)
    z += (sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    if band.qrn:
        for _ in range(rng.poisson(band.qrn * sek)):
            a = int(rng.uniform(0, n))
            L = int(fs * rng.uniform(0.003, 0.03))
            e = min(n, a + L)
            amp = sigma * rng.uniform(5, 30)
            z[a:e] += (amp * np.exp(-np.arange(e - a) / max(1, L / 3)) *
                       (rng.standard_normal(e - a) + 1j * rng.standard_normal(e - a))).astype(np.complex64)
    facit = []
    for w in st0.ord:
        if not facit or facit[-1] != w:
            facit.append(w)
    return z, facit


def scenario(namn, rng):
    S = Station
    if namn == "ren":
        return Band([S(anrop(rng))], snr_db=20)
    if namn == "svag":
        return Band([S(anrop(rng))], snr_db=3)
    if namn == "fladder":
        return Band([S(anrop(rng), fd=2, rice=0.7)], snr_db=15)
    if namn == "qrn":
        return Band([S(anrop(rng))], snr_db=12, qrn=4)
    if namn == "frekvensfel":
        return Band([S(anrop(rng), off_hz=60)], snr_db=15)
    if namn == "lsb":
        return Band([S(anrop(rng), lsb=True)], snr_db=15)
    if namn == "trangt":
        return Band([S(anrop(rng))], snr_db=15,
                    cw=[CG.Station(CG.cq(rng), wpm=22, off_hz=1450, niva_db=6),
                        CG.Station(CG.cq(rng), wpm=18, off_hz=-1500, niva_db=3)])
    raise SystemExit(f"Okänt scenario: {namn}")


SCENARIER = ["ren", "svag", "fladder", "qrn", "frekvensfel", "lsb", "trangt"]


def jamfor(facit, ut):
    """Andel av facitorden (i ordning) som finns bland de avkodade, och antal falska ord."""
    kvar = list(ut)
    ratt = 0
    for w in facit:
        if w in kvar:
            ratt += 1
            kvar.remove(w)
    falska = sum(1 for w in kvar if w not in facit)
    return ratt, len(facit), falska


if __name__ == "__main__":
    print("Scenarier:", ", ".join(SCENARIER))
