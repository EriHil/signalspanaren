#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
CW-generator: syntetisk, realistisk telegrafi med facit – för simulatorn, för att öva
avkodaren och för att mäta den.

Varje station har en sändare (takt, nyckeltyp, vikt, chirp, klick), en text och en
utbredningsväg (fladder, QSB, frekvensdrift). Bandet får brus, åskknaster och grannar.

  python3 cwgenerator.py --scen fav22 --sek 120 --ut fav22_syn.wav     ljud (ton 600 Hz) + .txt med facit
  python3 cwgenerator.py --scen qso --iq --ut qso.wav                   IQ i stereo (som KiwiSDR:ns IQ-läge)
  python3 cwgenerator.py --dataset 40 --mapp ~/cw-ovning                40 slumpade scenarier med facit
  python3 cwgenerator.py --lista                                        visa scenarierna

Scenarier: ren, fav22, qso, rak, contest, svag, qrn, trangt, farnsworth, bug.
I programmet finns ett CW-band i simulatorn på 99,45 MHz (källa: Simulator).
"""
import argparse
import math
import os
import string
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MORSE = {".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G", "....": "H",
         "..": "I", ".---": "J", "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O", ".--.": "P",
         "--.-": "Q", ".-.": "R", "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
         "-.--": "Y", "--..": "Z", ".----": "1", "..---": "2", "...--": "3", "....-": "4", ".....": "5",
         "-....": "6", "--...": "7", "---..": "8", "----.": "9", "-----": "0", ".-.-.-": ".", "--..--": ",",
         "..--..": "?", "-..-.": "/", "-...-": "=", ".-.-.": "+", "-....-": "-"}
TILL = {v: k for k, v in MORSE.items()}


# ════════════════════════════════════════════════════════════════════
# Texter
# ════════════════════════════════════════════════════════════════════
def grupper(rng, n=20, langd=5, tecken=string.ascii_uppercase):
    """Träningsgrupper (som FAV22): n grupper om `langd` slumpade tecken."""
    return " ".join("".join(rng.choice(list(tecken), langd)) for _ in range(n))


def anrop(rng):
    pre = rng.choice(["SM", "SA", "SK", "OH", "LA", "OZ", "DL", "G", "F", "ON", "PA", "OK", "SP", "EA", "I", "K", "W"])
    return f"{pre}{rng.integers(0, 10)}{''.join(rng.choice(list(string.ascii_uppercase), int(rng.integers(1, 4))))}"


def qso(rng):
    """Ett typiskt amatör-QSO med förkortningar."""
    a, b = anrop(rng), anrop(rng)
    namn = rng.choice(["ERIK", "ANNA", "PER", "JAN", "OLE", "HANS", "TOM", "BOB", "LARS", "KARL"])
    qth = rng.choice(["STOCKHOLM", "GOTEBORG", "OSLO", "HELSINKI", "BERLIN", "PARIS", "LONDON", "MALMO"])
    rst = rng.choice(["599", "579", "559", "449", "339"])
    return (f"CQ CQ DE {a} {a} K {a} DE {b} {b} K {b} DE {a} GM OM UR RST {rst} {rst} = "
            f"NAME {namn} {namn} QTH {qth} = HW? {b} DE {a} K {a} DE {b} R TNX FER QSO "
            f"UR RST {rst} = 73 ES GL {a} DE {b} SK")


def cq(rng):
    a = anrop(rng)
    return f"CQ CQ CQ DE {a} {a} {a} PSE K"


def contest(rng):
    a = anrop(rng)
    return " ".join(f"{a} 5NN {rng.integers(1, 999):03d} TU" for _ in range(6))


def klartext(rng):
    meningar = ["THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG", "NOW IS THE TIME FOR ALL GOOD MEN",
                "WX HR IS SUNNY ES WARM", "MY RIG IS A HOMEBREW TX ES ANT IS A DIPOLE",
                "PSE QSL VIA BURO", "TNX FER NICE QSO ES HPE CUAGN", "1234567890"]
    return " ".join(rng.choice(meningar, 4))


# ════════════════════════════════════════════════════════════════════
# Sändare och nyckling
# ════════════════════════════════════════════════════════════════════
@dataclass
class Station:
    text: str
    wpm: float = 20.0
    nyckel: str = "maskin"      # maskin | bug | rak
    farnsworth: float = None    # teckentakt för mellanrummen (ord/min), None = samma som wpm
    vikt: float = 3.0           # streck/prick
    jitter: float = 0.0         # spridning på elementlängder (andel), utöver nyckeltypens egen
    drift_takt: float = 0.0     # takten vandrar ±andel under sändningen (handsändare tröttnar)
    off_hz: float = 0.0         # frekvens i förhållande till mitten
    niva_db: float = 0.0        # relativt den starkaste stationen
    chirp_hz: float = 0.0       # frekvenshopp vid nedtryckning (avklingar på ~20 ms)
    drift_hz: float = 0.0       # frekvensdrift över hela klippet
    flank_ms: float = 4.0       # stig-/falltid (1 ms = hårda klick, 8 ms = mjuk)
    fd: float = 0.0             # fladder: dopplerspridning i Hz (0 = ingen)
    rice: float = 1.0           # fast väg / spridd väg (lågt = djupa avbrott)
    qsb: float = 0.0            # långsam fädning, djup 0–1
    qsb_period: float = 8.0
    start_s: float = 0.0
    namn: str = ""


def nyckla(st, rng):
    """[(på/av, sekunder, tecken)] för stationens text med nyckeltypens egenheter. Tecknet
    (eller " " vid ordslut) står på sista pausen i tecknet, annars ""."""
    u0 = 1.2 / st.wpm
    uc = 1.2 / st.farnsworth if st.farnsworth else u0
    ut = []
    t_tot = 0.0
    vikt = st.vikt
    for w in st.text.upper().split():
        for ch in w:
            kod = TILL.get(ch)
            if not kod:
                continue
            # takten vandrar långsamt
            u = u0 * (1 + st.drift_takt * math.sin(t_tot / 20.0 + 1.3)) if st.drift_takt else u0
            for s in kod:
                if st.nyckel == "maskin":
                    p = u if s == "." else vikt * u
                    paus = u
                elif st.nyckel == "bug":       # prickarna automatiska, strecken för hand
                    p = u if s == "." else vikt * u * (1 + 0.12 * rng.standard_normal())
                    paus = u * (1 + 0.05 * rng.standard_normal())
                else:                          # rak nyckel: allt för hand, prickarna ofta korta
                    p = u * (0.85 if s == "." else vikt) * (1 + 0.15 * rng.standard_normal())
                    paus = u * (1 + 0.2 * rng.standard_normal())
                if st.jitter:
                    p *= 1 + st.jitter * rng.standard_normal()
                    paus *= 1 + st.jitter * rng.standard_normal()
                ut += [(1, max(0.25 * u, p), ""), (0, max(0.3 * u, paus), "")]
                t_tot += p + paus
            tm = 3 * uc * ((1 + 0.25 * rng.standard_normal()) if st.nyckel == "rak" else 1)
            ut[-1] = (0, max(1.5 * u, tm), ch)
        wm = 7 * uc * ((1 + 0.25 * rng.standard_normal()) if st.nyckel == "rak" else 1)
        if ut:
            ut[-1] = (0, max(3.5 * u, wm), ut[-1][2] + " ")
    return ut


def _flutter(n, fs, fd, rice, rng):
    m = max(8, int(n * 2 * fd / fs) + 8)
    g = (rng.standard_normal(m) + 1j * rng.standard_normal(m)) / np.sqrt(2)
    k = np.ones(3) / 3
    g = np.convolve(g, k, mode="same")
    g /= np.sqrt(np.mean(np.abs(g) ** 2)) + 1e-12
    tg = np.linspace(0, n, m)
    h = np.interp(np.arange(n), tg, g.real) + 1j * np.interp(np.arange(n), tg, g.imag)
    return np.sqrt(rice / (rice + 1)) + np.sqrt(1 / (rice + 1)) * h


def station_signal(st, fs, n, rng):
    """Komplext basband (längd n) för en station; nyckeln börjar efter st.start_s och slingas."""
    seq = nyckla(st, rng)
    key = np.zeros(n, np.float32)
    t = st.start_s
    i = 0
    sant = []                                  # det som hann sändas helt (facit)
    while t < n / fs and seq:
        v, d, tg = seq[i % len(seq)]
        a, b = int(t * fs), int((t + d) * fs)
        if v:
            key[a:min(b, n)] = 1.0
        elif tg and t < n / fs - 0.05:
            sant.append(tg)
        t += d
        i += 1
        if i % len(seq) == 0:
            t += 2.0                           # paus innan texten börjar om
            sant.append(" ")
    # flanker (stig-/falltid) med upphöjd cosinus
    L = max(1, int(st.flank_ms * 1e-3 * fs))
    if L > 1:
        w = np.hanning(2 * L + 1)[:L + 1]
        w /= w.sum()
        key = np.convolve(key, np.concatenate((w, w[-2::-1])) / 2, mode="same").astype(np.float32)
        key /= max(1e-6, float(np.percentile(key[key > 0.01], 95)) if np.any(key > 0.01) else 1.0)
        key = np.clip(key, 0, 1)
    tt = np.arange(n) / fs
    f = st.off_hz + st.drift_hz * tt / max(tt[-1], 1e-9)
    if st.chirp_hz:
        # chirp: frekvensen hoppar när nyckeln trycks ned och klingar av
        upp = np.flatnonzero(np.diff(key > 0.5, prepend=0) > 0)
        ch = np.zeros(n, np.float32)
        tau = int(0.02 * fs)
        kurva = np.exp(-np.arange(4 * tau) / tau).astype(np.float32)
        for u in upp:
            e = min(n, u + len(kurva))
            ch[u:e] = np.maximum(ch[u:e], kurva[:e - u])
        f = f + st.chirp_hz * ch
    fas = 2 * np.pi * np.cumsum(f) / fs
    z = key * np.exp(1j * fas) * 10 ** (st.niva_db / 20)
    if st.fd:
        z = z * _flutter(n, fs, st.fd, st.rice, rng)
    if st.qsb:
        z = z * (1 - st.qsb * (0.5 + 0.5 * np.sin(2 * np.pi * tt / st.qsb_period + rng.uniform(0, 6))))
    return z.astype(np.complex64), " ".join("".join(sant).split())


@dataclass
class Band:
    stationer: list
    snr_db: float = 20.0        # för den starkaste stationen, i 500 Hz bandbredd
    qrn: float = 0.0            # åskknaster per sekund
    barvag_hz: float = None     # en omodulerad bärvåg (störning) på denna frekvens
    barvag_db: float = -10.0
    extra: dict = field(default_factory=dict)


def generera(band, fs=12000, sek=60.0, seed=1):
    """Komplext basband med alla stationer + brus. Returnerar (z, facit för station 0)."""
    rng = np.random.default_rng(seed)
    n = int(fs * sek)
    z = np.zeros(n, np.complex64)
    facit = ""
    for k, st in enumerate(band.stationer):
        s, sant = station_signal(st, fs, n, rng)
        z += s
        if k == 0:
            facit = sant
    if band.barvag_hz is not None:
        tt = np.arange(n) / fs
        z += (10 ** (band.barvag_db / 20) * np.exp(2j * np.pi * band.barvag_hz * tt)).astype(np.complex64)
    sigma = 10 ** (-band.snr_db / 20) / np.sqrt(2) * np.sqrt(fs / 500)
    z += (sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    if band.qrn:
        # åskknaster: korta, starka, bredbandiga pulser
        for _ in range(rng.poisson(band.qrn * sek)):
            a = int(rng.uniform(0, n))
            L = int(fs * rng.uniform(0.003, 0.03))
            amp = sigma * rng.uniform(5, 30)
            e = min(n, a + L)
            z[a:e] += (amp * np.exp(-np.arange(e - a) / max(1, L / 3)) *
                       (rng.standard_normal(e - a) + 1j * rng.standard_normal(e - a))).astype(np.complex64)
    return z, facit


# ════════════════════════════════════════════════════════════════════
# Scenarier
# ════════════════════════════════════════════════════════════════════
def scenario(namn, rng):
    """Ett namngivet scenario. Station 0 är den som ska avkodas (mitt i bandet)."""
    if namn == "ren":
        return Band([Station(klartext(rng), wpm=20)], snr_db=25)
    if namn == "fav22":
        return Band([Station(grupper(rng, 25), wpm=24, fd=5, rice=1.0, namn="FAV22-lik")], snr_db=15)
    if namn == "qso":
        return Band([Station(qso(rng), wpm=rng.uniform(16, 26), nyckel="bug", qsb=0.5,
                             off_hz=rng.uniform(-30, 30)),
                     Station(cq(rng), wpm=22, off_hz=650, niva_db=-6)], snr_db=18)
    if namn == "rak":
        return Band([Station(qso(rng), wpm=rng.uniform(12, 18), nyckel="rak", chirp_hz=8,
                             vikt=rng.uniform(2.6, 3.6))], snr_db=15)
    if namn == "contest":
        return Band([Station(contest(rng), wpm=32),
                     Station(contest(rng), wpm=30, off_hz=280, niva_db=6),
                     Station(contest(rng), wpm=28, off_hz=-450, niva_db=3)], snr_db=20)
    if namn == "svag":
        return Band([Station(klartext(rng), wpm=18, qsb=0.6, qsb_period=12)], snr_db=3)
    if namn == "qrn":
        return Band([Station(qso(rng), wpm=20)], snr_db=12, qrn=3.0)
    if namn == "trangt":
        return Band([Station(qso(rng), wpm=22),
                     Station(cq(rng), wpm=25, off_hz=180, niva_db=10),
                     Station(contest(rng), wpm=30, off_hz=-220, niva_db=6)],
                    snr_db=20, barvag_hz=95, barvag_db=-6)
    if namn == "farnsworth":
        return Band([Station(klartext(rng), wpm=20, farnsworth=10)], snr_db=20)
    if namn == "bug":
        return Band([Station(qso(rng), wpm=24, nyckel="bug", vikt=3.6)], snr_db=20)
    raise SystemExit(f"Okänt scenario: {namn}")


SCENARIER = ["ren", "fav22", "qso", "rak", "contest", "svag", "qrn", "trangt", "farnsworth", "bug"]


def slumpat(rng):
    """Ett slumpat band (för träning): takt, nyckel, fädning, brus och grannar varierar."""
    text = rng.choice([qso, cq, contest, klartext, lambda r: grupper(r, 20)])(rng)
    st = Station(text, wpm=float(rng.uniform(10, 40)),
                 nyckel=str(rng.choice(["maskin", "maskin", "bug", "rak"])),
                 farnsworth=float(rng.uniform(6, 12)) if rng.random() < 0.15 else None,
                 vikt=float(rng.uniform(2.6, 3.8)),
                 off_hz=float(rng.uniform(-40, 40)),
                 chirp_hz=float(rng.choice([0, 0, 0, 5, 15])),
                 flank_ms=float(rng.choice([1.5, 4, 6])),
                 fd=float(rng.choice([0, 0, 0, 2, 5, 10])), rice=float(rng.uniform(0.5, 3)),
                 qsb=float(rng.choice([0, 0, 0.4, 0.8])), qsb_period=float(rng.uniform(4, 15)))
    stn = [st]
    for _ in range(int(rng.choice([0, 0, 1, 2]))):
        stn.append(Station(cq(rng), wpm=float(rng.uniform(15, 35)),
                           off_hz=float(rng.choice([-1, 1]) * rng.uniform(150, 900)),
                           niva_db=float(rng.uniform(-10, 10))))
    return Band(stn, snr_db=float(rng.uniform(2, 25)), qrn=float(rng.choice([0, 0, 0, 1, 4])))


# ════════════════════════════════════════════════════════════════════
# Filer
# ════════════════════════════════════════════════════════════════════
def till_ljud(z, fs, ton=600.0):
    t = np.arange(len(z)) / fs
    a = np.real(z * np.exp(2j * np.pi * ton * t))
    return a / (np.abs(a).max() + 1e-12) * 0.8


def skriv(path, z, fs, facit, iq=False, ton=600.0):
    import wave
    if iq:
        s = z / (np.abs(z).max() + 1e-12) * 0.8
        data = np.stack((s.real, s.imag), axis=1)
        ch = 2
    else:
        data = till_ljud(z, fs, ton)[:, None]
        ch = 1
    with wave.open(path, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())
    with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as fh:
        fh.write(facit + "\n")


def main():
    p = argparse.ArgumentParser(prog="cwgenerator", description="Syntetisk CW med facit.")
    p.add_argument("--scen", default="ren", help="scenario (se --lista)")
    p.add_argument("--sek", type=float, default=60.0)
    p.add_argument("--fs", type=int, default=12000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--iq", action="store_true", help="IQ i stereo i stället för ljud")
    p.add_argument("--ut", default="cw.wav")
    p.add_argument("--dataset", type=int, metavar="N", help="N slumpade scenarier")
    p.add_argument("--mapp", default="cw-ovning")
    p.add_argument("--lista", action="store_true")
    a = p.parse_args()
    if a.lista:
        print("Scenarier:", ", ".join(SCENARIER))
        return 0
    if a.dataset:
        os.makedirs(a.mapp, exist_ok=True)
        for i in range(a.dataset):
            rng = np.random.default_rng(a.seed * 1000 + i)
            z, facit = generera(slumpat(rng), a.fs, a.sek, seed=a.seed * 1000 + i)
            skriv(os.path.join(a.mapp, f"slump_{i:03d}.wav"), z, a.fs, facit, a.iq)
        print(f"{a.dataset} klipp i {a.mapp}/")
        return 0
    rng = np.random.default_rng(a.seed)
    z, facit = generera(scenario(a.scen, rng), a.fs, a.sek, seed=a.seed)
    skriv(a.ut, z, a.fs, facit, a.iq)
    print(f"{a.ut}: {a.scen}, {a.sek:.0f} s\nFacit: {facit[:100]}{'…' if len(facit) > 100 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
