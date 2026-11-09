#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
POCSAG-generator: syntetiska personsökarsändningar (512/1200/2400 Bd, ±4,5 kHz FSK) med facit –
för att mäta och öva POCSAG-avkodaren. Samma upplägg som cw-, rtty- och pskgenerator.py.

Sändaren: takt, polaritet, frekvensfel, deviation, mjuka omslag. Utbredningen på VHF: brus och
fädning när mottagaren rör sig (Rayleigh, dopplerspridning 5–50 Hz). Grannkanal 12,5 kHz bort.

  python3 pocsaggenerator.py --scen fadning --ut p.wav     IQ i stereo (48 kHz) + .txt med facit
  python3 pocsaggenerator.py --lista
"""
import argparse
import os
import string
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cwgenerator as CG   # noqa: E402

FS = 48000


def sidor(rng, n=6):
    """n sidor: [(adress, funktion, text, numeriskt?)] – larm, text och nummer blandat."""
    ut = []
    for k in range(n):
        addr = int(rng.integers(100000, 2000000))
        if k % 3 == 0:
            txt = "".join(rng.choice(list(string.digits), int(rng.integers(5, 11))))
            ut.append((addr, 0, txt, True))
        else:
            txt = rng.choice(["LARM BRAND KV 12", "RING 0701234567", "LEVERANS KL 1430 PORT 4",
                              "AMBULANS PRIO 1 STORGATAN 5", "TEST TEST 123", "MOTE FLYTTAT TILL 15"])
            ut.append((addr, 3, f"{txt} {k}", False))
    return ut


@dataclass
class Station:
    sidor: list
    rate: int = 1200
    dev_hz: float = 4500.0
    omvand: bool = False
    off_hz: float = 0.0
    niva_db: float = 0.0
    fd: float = 0.0              # fädning (Rayleigh om rice litet): dopplerspridning i Hz
    rice: float = 0.3
    mjuk: float = 0.0            # omslagens längd i bitar (0 = hårda)
    start_s: float = 0.3
    taktfel_ppm: float = 0.0
    upprepa: int = 2             # sändningen går så många gånger (personsökarsändare upprepar)
    paus_s: float = 1.5


def station_bitar(st):
    """Bitarna för hela sändningen: sidorna upprepas med paus (bärvågen av) emellan; None = av."""
    from analysavkodare import pocsag_bitar
    en = pocsag_bitar(st.sidor)
    paus = [None] * int(st.paus_s * st.rate)
    ut = []
    for k in range(st.upprepa):
        ut += en + (paus if k + 1 < st.upprepa else [])
    return ut


def station_signal(st, fs, n, rng):
    bits = station_bitar(st)
    tb = 1.0 / (st.rate * (1 + st.taktfel_ppm * 1e-6))
    i0 = int(st.start_s * fs)
    idx = ((np.arange(n) - i0) / (tb * fs)).astype(np.int64)
    av = np.array([v is None for v in bits])
    bv = np.array([0 if v is None else v for v in bits], np.float32)
    ii = np.clip(idx, 0, len(bits) - 1)
    aktiv = (idx >= 0) & (idx < len(bits)) & ~av[ii]
    b = bv[ii]
    if st.mjuk:
        L = max(1, int(st.mjuk * tb * fs))
        b = np.convolve(b, np.ones(L) / L, mode="same").astype(np.float32)
    s = -1.0 if not st.omvand else 1.0               # POCSAG: 1 = lägre frekvens
    f = st.off_hz + s * (2 * b - 1) * st.dev_hz
    z = np.exp(2j * np.pi * np.cumsum(f) / fs) * aktiv
    if st.fd:
        z = z * CG._flutter(n, fs, st.fd, st.rice, rng)
    return (z * 10 ** (st.niva_db / 20)).astype(np.complex64)


@dataclass
class Band:
    stationer: list
    snr_db: float = 20.0         # för station 0, i 16 kHz bandbredd (en NFM-kanal)
    grannar: list = field(default_factory=list)


def generera(band, fs=FS, sek=None, seed=1):
    """Returnerar (z, facit = sidorna för station 0)."""
    rng = np.random.default_rng(seed)
    st0 = band.stationer[0]
    if sek is None:
        sek = st0.start_s + len(station_bitar(st0)) / st0.rate + 0.5
    n = int(fs * sek)
    z = np.zeros(n, np.complex64)
    for st in band.stationer + band.grannar:
        z += station_signal(st, fs, n, rng)
    sigma = 10 ** (-band.snr_db / 20) / np.sqrt(2) * np.sqrt(fs / 16000)
    z += (sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    return z, [(a, t) for a, f, t, num in st0.sidor]


def scenario(namn, rng):
    S = Station
    if namn == "ren":
        return Band([S(sidor(rng))], snr_db=25)
    if namn == "512":
        return Band([S(sidor(rng, 3), rate=512)], snr_db=18)
    if namn == "2400":
        return Band([S(sidor(rng), rate=2400)], snr_db=20)
    if namn == "svag":
        return Band([S(sidor(rng))], snr_db=8)
    if namn == "fadning":              # mottagaren i en bil: Rayleigh, 30 Hz doppler
        return Band([S(sidor(rng), fd=30, rice=0.3)], snr_db=18)
    if namn == "frekvensfel":          # sändaren 1,5 kHz fel, deviationen bara 3 kHz
        return Band([S(sidor(rng), off_hz=1500, dev_hz=3000)], snr_db=18)
    if namn == "omvand":
        return Band([S(sidor(rng), omvand=True)], snr_db=20)
    if namn == "mjuk":                 # mjuka omslag (förfiltrering) och 0,1 % taktfel
        return Band([S(sidor(rng), mjuk=0.6, taktfel_ppm=1000)], snr_db=18)
    if namn == "granne":               # stark station i närmaste kanal (12,5 kHz bort)
        return Band([S(sidor(rng))], snr_db=18, grannar=[S(sidor(rng, 8), off_hz=12500, niva_db=10, start_s=0)])
    raise SystemExit(f"Okänt scenario: {namn}")


SCENARIER = ["ren", "512", "2400", "svag", "fadning", "frekvensfel", "omvand", "mjuk", "granne"]


def jamfor(facit, ut):
    """facit: [(adress, text)], ut: [(adress, text)] avkodat (en sida får komma flera gånger, sändningen
    upprepas). Returnerar (rätt, fel, falska): rätt = sidor avkodade exakt minst en gång."""
    ut = [(a, " ".join(t.split())) for a, t in ut]
    ratt = sum(1 for a, t in facit if (a, t) in ut)
    falska = sum(1 for a, t in ut if a not in {x for x, _ in facit})
    return ratt, len(facit) - ratt, falska


def main():
    p = argparse.ArgumentParser(prog="pocsaggenerator", description="Syntetisk POCSAG med facit.")
    p.add_argument("--scen", default="ren")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--ut", default="pocsag.wav")
    p.add_argument("--lista", action="store_true")
    a = p.parse_args()
    if a.lista:
        print("Scenarier:", ", ".join(SCENARIER))
        return 0
    import wave
    z, facit = generera(scenario(a.scen, np.random.default_rng(a.seed)), FS, None, a.seed)
    s = z / (np.abs(z).max() + 1e-12) * 0.8
    with wave.open(a.ut, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((np.clip(np.stack((s.real, s.imag), 1), -1, 1) * 32767).astype("<i2").tobytes())
    with open(os.path.splitext(a.ut)[0] + ".txt", "w", encoding="utf-8") as fh:
        for ad, t in facit:
            fh.write(f"{ad}\t{t}\n")
    print(f"{a.ut}: {a.scen}, {len(z) / FS:.1f} s, {len(facit)} sidor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
