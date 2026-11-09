#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
DMR-generator: syntetiska DMR-samtal (4FSK 4800 sym/s, Voice LC-header) med facit – för att mäta och
öva DMR-avkodaren. Samma upplägg som de andra generatorerna.

Samtalen: grupp eller enskilt, avsändare och mål, färgkod. Kanalen på VHF/UHF: brus, fädning när
mottagaren rör sig (Rayleigh), frekvensfel, tändstörningar och en stark granne i närmaste kanal.

  python3 dmrgenerator.py --lista
"""
import os
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cwgenerator as CG   # noqa: E402

FS = 48000


@dataclass
class Samtal:
    src: int
    dst: int
    group: bool = True
    enc: bool = False
    cc: int = 1
    bursts: int = 120            # ~3,3 s tal (LC-header var sjätte burst)


@dataclass
class Station:
    samtal: list
    off_hz: float = 0.0
    niva_db: float = 0.0
    fd: float = 0.0
    rice: float = 0.3
    start_s: float = 0.3
    paus_s: float = 0.8


def station_signal(st, fs, n, rng):
    from analysavkodare import make_dmr
    ut = np.zeros(n, np.complex64)
    i = int(st.start_s * fs)
    for s in st.samtal:
        z = make_dmr(fs, src=s.src, dst=s.dst, group=s.group, enc=s.enc, cc=s.cc, bursts=s.bursts)
        m = min(len(z), n - i)
        if m <= 0:
            break
        ut[i:i + m] = z[:m]
        i += len(z) + int(st.paus_s * fs)
    t = np.arange(n) / fs
    ut = ut * np.exp(2j * np.pi * st.off_hz * t)
    if st.fd:
        ut = ut * CG._flutter(n, fs, st.fd, st.rice, rng)
    return (ut * 10 ** (st.niva_db / 20)).astype(np.complex64)


@dataclass
class Band:
    stationer: list
    snr_db: float = 20.0          # för station 0, i 12,5 kHz bandbredd
    tand: float = 0.0             # tändstörningar per sekund
    grannar: list = field(default_factory=list)


def langd(st, fs=FS):
    from analysavkodare import make_dmr
    return st.start_s + sum(len(make_dmr(fs, bursts=s.bursts)) / fs + st.paus_s for s in st.samtal)


def generera(band, fs=FS, seed=1):
    """Returnerar (z, facit = [(mål, avsändare, grupp?)] för station 0)."""
    rng = np.random.default_rng(seed)
    st0 = band.stationer[0]
    n = int(fs * (langd(st0, fs) + 0.3))
    z = np.zeros(n, np.complex64)
    for st in band.stationer + band.grannar:
        z += station_signal(st, fs, n, rng)
    sigma = 10 ** (-band.snr_db / 20) / np.sqrt(2) * np.sqrt(fs / 12500)
    z += (sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    for _ in range(rng.poisson(band.tand * n / fs)):
        a = int(rng.uniform(0, n))
        e = min(n, a + int(fs * 0.0005))
        z[a:e] += (sigma * rng.uniform(20, 60) * (rng.standard_normal(e - a) + 1j * rng.standard_normal(e - a))
                   ).astype(np.complex64)
    return z, [(s.dst, s.src, s.group) for s in st0.samtal]


def samtal(rng, n=2):
    return [Samtal(src=int(rng.integers(2400000, 2409999)), dst=int(rng.integers(1, 99999)),
                   group=bool(rng.random() < 0.7), cc=int(rng.integers(1, 15))) for _ in range(n)]


def scenario(namn, rng):
    S = Station
    if namn == "ren":
        return Band([S(samtal(rng))], snr_db=25)
    if namn == "svag":
        return Band([S(samtal(rng))], snr_db=12)
    if namn == "fadning":
        return Band([S(samtal(rng), fd=30, rice=0.3)], snr_db=20)
    if namn == "frekvensfel":
        return Band([S(samtal(rng), off_hz=900)], snr_db=20)
    if namn == "tand":
        return Band([S(samtal(rng))], snr_db=18, tand=40)
    if namn == "granne":
        return Band([S(samtal(rng))], snr_db=20, grannar=[S(samtal(rng, 3), off_hz=12500, niva_db=10, start_s=0)])
    raise SystemExit(f"Okänt scenario: {namn}")


SCENARIER = ["ren", "svag", "fadning", "frekvensfel", "tand", "granne"]


def jamfor(facit, ut):
    """facit [(mål, avsändare, grupp)], ut [(mål, avsändare, grupp)]: (rätt, antal, falska)."""
    ratt = sum(1 for f in facit if f in ut)
    falska = sum(1 for u in set(ut) if u not in facit)
    return ratt, len(facit), falska


if __name__ == "__main__":
    print("Scenarier:", ", ".join(SCENARIER))
