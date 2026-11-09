#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
Väderfax-generator (HF-fax 120 rader/min, FM 1500–2300 Hz): en syntetisk väderkarta med facit –
för att mäta och öva fax-avkodaren. Samma upplägg som de andra generatorerna.

Sändningen: startton (300 Hz), fasningsrader, bilden. Kanalen: brus, fädning (flervägsutbredning),
frekvensfel, klockfel i sändaren (ger en sned bild), åskknaster och en RTTY-granne.

  python3 faxgenerator.py --lista
"""
import os
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cwgenerator as CG   # noqa: E402

FS = 12000
W = 1200                     # bildpunkter per rad (som avkodaren)


def karta(rader=120, rng=None):
    """En väderkarta: isobarer (svarta linjer), några textrutor och en gråskala. 0 = svart, 1 = vitt."""
    rng = rng or np.random.default_rng(0)
    y, x = np.mgrid[0:rader, 0:W].astype(float)
    tryck = np.zeros_like(x)
    for _ in range(4):                                       # högtryck och lågtryck
        cx, cy, a = rng.uniform(0, W), rng.uniform(0, rader), rng.uniform(-1, 1)
        tryck += a * np.exp(-(((x - cx) / 300) ** 2 + ((y - cy) / 50) ** 2))
    bild = np.ones_like(x)
    bild[np.abs(np.sin(tryck * 12)) < 0.12] = 0.0             # isobarerna
    for _ in range(5):                                       # textrutor
        r0, c0 = int(rng.uniform(5, rader - 20)), int(rng.uniform(50, W - 200))
        bild[r0:r0 + 10, c0:c0 + 150] = (rng.random((10, 150)) > 0.5).astype(float)
    bild[:, W - 80:] = np.linspace(0, 1, rader)[:, None]     # gråskala i kanten
    return bild


@dataclass
class Station:
    bild: np.ndarray
    off_hz: float = 0.0
    niva_db: float = 0.0
    klockfel_ppm: float = 0.0    # sändarens radtakt avviker: bilden blir sned
    fd: float = 0.0
    rice: float = 2.0
    fasning_offset: float = 0.3  # fasningspulsens läge i raden (andel)
    start_s: float = 0.5


def station_signal(st, fs, n, rng):
    spl = fs * 0.5 * (1 + st.klockfel_ppm * 1e-6)
    delar = [np.where(np.sin(2 * np.pi * 300 * np.arange(int(fs * 3)) / fs) > 0, 1.0, 0.0)]  # startton
    m = int(round(spl))
    for _ in range(15):                                      # fasning: vit rad med svart puls (5 %)
        ln = np.ones(m)
        ln[:int(m * 0.05)] = 0
        delar.append(np.roll(ln, int(m * st.fasning_offset)))
    rad_t = 0.0
    for r in range(st.bild.shape[0]):
        a, b = int(round(rad_t)), int(round(rad_t + spl))
        rad_t += spl
        x = np.linspace(0, W - 1, b - a)
        ln = np.interp(x, np.arange(W), st.bild[r])
        ln[:int((b - a) * 0.05)] = 0.0                       # fasningsmarkeringen finns i varje rad
        delar.append(np.roll(ln, int((b - a) * st.fasning_offset)))
    lum = np.concatenate(delar)
    z = np.zeros(n, np.complex64)
    i0 = int(st.start_s * fs)
    lum = lum[:max(0, n - i0)]
    f = (lum - 0.5) * 800.0 + st.off_hz
    z[i0:i0 + len(lum)] = np.exp(2j * np.pi * np.cumsum(f) / fs)
    if st.fd:
        z = z * CG._flutter(n, fs, st.fd, st.rice, rng)
    return (z * 10 ** (st.niva_db / 20)).astype(np.complex64)


@dataclass
class Band:
    stationer: list
    snr_db: float = 20.0          # för station 0, i 1 kHz bandbredd
    qrn: float = 0.0
    rtty: list = field(default_factory=list)


def generera(band, fs=FS, seed=1):
    """Returnerar (z, facit = bilden för station 0)."""
    rng = np.random.default_rng(seed)
    st0 = band.stationer[0]
    sek = st0.start_s + 3 + 15 * 0.5 + st0.bild.shape[0] * 0.5 + 1.0
    n = int(fs * sek)
    z = np.zeros(n, np.complex64)
    for st in band.stationer:
        z += station_signal(st, fs, n, rng)
    if band.rtty:
        import rttygenerator as R
        for st in band.rtty:
            z += R.station_signal(st, fs, n, rng)[0]
    sigma = 10 ** (-band.snr_db / 20) / np.sqrt(2) * np.sqrt(fs / 1000)
    z += (sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    for _ in range(rng.poisson(band.qrn * sek)):
        a = int(rng.uniform(0, n))
        L = int(fs * rng.uniform(0.003, 0.03))
        e = min(n, a + L)
        amp = sigma * rng.uniform(5, 30)
        z[a:e] += (amp * np.exp(-np.arange(e - a) / max(1, L / 3)) *
                   (rng.standard_normal(e - a) + 1j * rng.standard_normal(e - a))).astype(np.complex64)
    facit = st0.bild.copy()
    facit[:, :int(W * 0.05)] = 0.0                       # fasningsmarkeringen (efter fasningen: vänsterkanten)
    return z, facit


def scenario(namn, rng):
    S = Station
    b = karta(100, rng)
    if namn == "ren":
        return Band([S(b)], snr_db=25)
    if namn == "svag":
        return Band([S(b)], snr_db=6)
    if namn == "fadning":
        return Band([S(b, fd=1.0, rice=1.0)], snr_db=18)
    if namn == "frekvensfel":
        return Band([S(b, off_hz=120)], snr_db=20)
    if namn == "klockfel":         # 100 ppm: bilden lutar en halv bildpunkt per rad
        return Band([S(b, klockfel_ppm=100)], snr_db=20)
    if namn == "qrn":
        return Band([S(b)], snr_db=15, qrn=4)
    if namn == "granne":
        import rttygenerator as R
        return Band([S(b)], snr_db=18, rtty=[R.Station(R.ryry(rng) * 4, off_hz=750, niva_db=0)])
    raise SystemExit(f"Okänt scenario: {namn}")


SCENARIER = ["ren", "svag", "fadning", "frekvensfel", "klockfel", "qrn", "granne"]


def likhet(facit, bild):
    """Korrelation (0–1) mellan avkodad bild och facit: bästa vertikala läge, ingen sidoförskjutning
    (fasningen ska ha satt den) – men den bästa av högst ±2 % räknas som rätt fasning."""
    if bild is None or len(bild) < 10:
        return 0.0
    B = np.asarray(bild, float) / 255.0
    F = np.asarray(facit, float)
    best = 0.0
    for dy in range(0, max(1, len(B) - len(F) + 1)):
        seg = B[dy:dy + len(F)]
        if len(seg) < len(F) * 0.8:
            break
        f = F[:len(seg)]
        for dx in range(-24, 25, 4):
            s = np.roll(seg, dx, axis=1)
            c = np.corrcoef(s.ravel(), f.ravel())[0, 1]
            best = max(best, float(c) if np.isfinite(c) else 0.0)
    return best


if __name__ == "__main__":
    print("Scenarier:", ", ".join(SCENARIER))
