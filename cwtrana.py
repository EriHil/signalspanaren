#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
Tränar Morse-avkodaren: söker de inställningar (klassattribut i analys.CwAvkodare) som ger
minst fel, mätt på tre sorters prov med känt facit:

  1. fallen i analystest.py morse (takt, brus, Farnsworth, handnyckling, fladder …)
  2. scenarier från cwgenerator.py (QSO, rak nyckel, contest, tätt band, åskknaster …)
  3. riktiga inspelningar i testdata/cw/ (WAV + .txt med facit) – väger tyngst

  python3 cwtrana.py                                     mät nuvarande inställningar
  python3 cwtrana.py '{"FILTER": [0.4, 0.6, 0.8]}'       rutnätssökning (alla kombinationer)
  python3 cwtrana.py '{"P_ON": [0.6, 0.7], "P_OFF": [0.5, 0.6]}' --seeds 3

Resultatet sorteras på det sammanvägda felet. Inställningarna ändras inte i koden –
för in de bästa i analys.py för hand (och kör analystest.py morse).
"""
import argparse
import difflib
import itertools
import json
import multiprocessing as mp
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analys          # noqa: E402
import analystest      # noqa: E402
import cwgenerator     # noqa: E402
import cwjamfor        # noqa: E402

VIKT_RIKTIG = 3.0      # riktiga inspelningar väger tre gånger så mycket som syntetiska


def _fel(facit, ut):
    return 1 - difflib.SequenceMatcher(None, facit, ut, autojunk=False).ratio()


def _riktiga():
    mapp = os.path.join(HERE, "testdata", "cw")
    ut = []
    for f in sorted(os.listdir(mapp)) if os.path.isdir(mapp) else []:
        txt = os.path.join(mapp, f[:-4] + ".txt")
        if f.endswith(".wav") and os.path.exists(txt):
            with open(txt, encoding="utf-8") as fh:
                facit = " ".join(fh.read().upper().split())
            x, fs, iq = cwjamfor.las_wav(os.path.join(mapp, f))
            z, _ = cwjamfor.till_basband(x, fs, not iq)
            ut.append((f[:-4], z, fs, facit))
    return ut


def mat(params, seeds=2, sek=40.0):
    """Felandel per prov med inställningarna params. Returnerar (sammanvägt, {prov: fel})."""
    for k, v in params.items():
        setattr(analys.CwAvkodare, k, v)
    fel = {}
    for namn, kw in analystest.MORSE_FALL:
        fel["fall: " + namn] = float(np.mean([analystest.morse_kor(**kw, seed=s)[0] for s in range(1, seeds + 1)]))
    for sc in cwgenerator.SCENARIER:
        e = []
        for s in range(1, seeds + 1):
            z, facit = cwgenerator.generera(cwgenerator.scenario(sc, np.random.default_rng(s)), 12000, sek, s)
            e.append(_fel(facit, cwjamfor.signalspanaren(z, 12000, mitt=0.0)[0]))
        fel["scen: " + sc] = float(np.mean(e))
    for namn, z, fs, facit in _riktiga():
        fel["riktig: " + namn] = _fel(facit, cwjamfor.signalspanaren(z, fs)[0])
    syn = [v for k, v in fel.items() if not k.startswith("riktig")]
    rik = [v for k, v in fel.items() if k.startswith("riktig")]
    tot = (sum(syn) + VIKT_RIKTIG * sum(rik)) / (len(syn) + VIKT_RIKTIG * len(rik))
    return tot, fel


def _kor(a):
    p, seeds = a
    tot, fel = mat(p, seeds)
    return tot, p, fel


def main():
    ap = argparse.ArgumentParser(prog="cwtrana", description="Tränar Morse-avkodarens inställningar.")
    ap.add_argument("rutnat", nargs="?", default="{}", help='JSON: {"ATTRIBUT": [värden], …}')
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--visa", type=int, default=5, help="antal bästa att visa")
    a = ap.parse_args()
    grid = json.loads(a.rutnat)
    for k in grid:
        if not hasattr(analys.CwAvkodare, k):
            raise SystemExit(f"CwAvkodare har inget attribut {k}")
    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())] or [{}]
    nu = {k: getattr(analys.CwAvkodare, k) for k in grid}
    print(f"{len(combos)} kombinationer, nuvarande: {nu or 'standard'}", flush=True)
    with mp.Pool(min(len(combos), os.cpu_count() or 1)) as pool:
        res = pool.map(_kor, [(c, a.seeds) for c in combos])
    res.sort(key=lambda r: r[0])
    for tot, p, fel in res[:a.visa]:
        syn = np.mean([v for k, v in fel.items() if not k.startswith("riktig")])
        rik = [v for k, v in fel.items() if k.startswith("riktig")]
        print(f"\n{tot * 100:5.2f} %  {p or 'nuvarande'}   (syntetiskt {syn * 100:.1f} %"
              + (f", riktiga {np.mean(rik) * 100:.1f} %)" if rik else ")"))
        print("   " + ", ".join(f"{k.split(': ')[1][:18]} {v * 100:.0f}" for k, v in fel.items() if v > 0.02))
    return 0


if __name__ == "__main__":
    sys.exit(main())
