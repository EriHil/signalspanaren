#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
Experiment: klassning av Morse-avkodarens tidsavstånd med k-means-regler (det avkodaren gör)
jämfört med Random Forest, SVM och ett litet neuralt nät (MLP).

Signalerna kommer från cwgenerator.slumpat() med känd nyckling. Avkodarens körningar
(ton- och pauslängder) tidsjusteras mot facit och får en klass: paus inom tecken, mellan
tecken, mellan ord, prick, streck, störning (ton där det var paus) eller avbrott (paus mitt
i en ton). 80 klipp tränar, 40 andra testar. Kräver scikit-learn.

  python3 cwml.py

Resultat 2026-11-08: regler 87,5 %, Random Forest/SVM/MLP 88,7 %. Klassningen är alltså
inte flaskhalsen; störningar och avbrott uppstår redan i signalbehandlingen.
"""
import os
import sys

import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cwgenerator as G  # noqa: E402
import analys  # noqa: E402
from dataclasses import replace  # noqa: E402

FS = 8000

# Signalen byggs ur en känd nyckling (som generatorn gör för station 0)
def bygg(seed):
    rng = np.random.default_rng(seed)
    band = G.slumpat(rng)
    st = band.stationer[0]
    seq = G.nyckla(st, rng)
    u = 1.2 / st.wpm
    n = int(sum(d for _, d, _ in seq) * FS) + FS
    lab = np.full(n, -1, np.int8)
    key = np.zeros(n, np.float32)
    t = 0.5
    for v, d, tg in seq:
        a, b = int(t * FS), min(n, int((t + d) * FS))
        if v:
            key[a:b] = 1; lab[a:b] = 3 if d < 2 * u * (st.vikt / 3) ** 0.5 * 1.0 else 4
        else:
            lab[a:b] = 2 if tg.endswith(" ") else (1 if tg else 0)
        t += d
    # sätt in nycklingen i bandet i stället för station 0
    st0 = replace(st, text="")
    band.stationer[0] = st0
    zrest, _ = G.generera(band, FS, n / FS, seed=seed)
    tt = np.arange(n) / FS
    L = max(1, int(st.flank_ms * 1e-3 * FS))
    kk = np.convolve(key, np.hanning(2 * L + 1) / np.hanning(2 * L + 1).sum(), mode="same")
    zs = kk * np.exp(2j * np.pi * st.off_hz * tt)
    if st.fd:
        zs = zs * G._flutter(n, FS, st.fd, st.rice, rng)
    if st.qsb:
        zs = zs * (1 - st.qsb * (0.5 + 0.5 * np.sin(2 * np.pi * tt / st.qsb_period)))
    m_ = min(len(zs), len(zrest))
    return (zs[:m_] + zrest[:m_]).astype(np.complex64), lab[:m_], key[:m_], st

def korningar(z, off):
    d = analys.CwAvkodare(); d.GATE = 0.0; runs = []
    orig = d._run_end
    def spy(m, dd, out):
        runs.append((m, dd)); orig(m, dd, out)
    d._run_end = spy
    c = int(0.35 * FS)
    for i in range(0, len(z), c):
        d.feed(z[i:i + c], FS, {"fam": "OOK", "f_mid": off})
    return runs, d

def sarddrag_och_etiketter(seed):
    z, lab, key, st = bygg(seed)
    runs, d = korningar(z, st.off_hz)
    if d.dot is None or len(runs) < 20:
        return None
    # tidsjustera: hitta fördröjning som maximerar överensstämmelse
    t = np.cumsum([0] + [dd for _, dd in runs])
    det = np.zeros(len(key), np.int8)
    for (m, dd), a in zip(runs, t[:-1]):
        if m: det[int(a * FS):int((a + dd) * FS)] = 1
    best = max(range(0, int(0.08 * FS), 40), key=lambda s: np.mean(det[s:] == key[:len(key) - s]))
    X, Y, R = [], [], []
    u = d.unit; e = d.gaps[0] if d.gaps else u
    c_thr, w_thr = d._thr(); m_thr = d._mark_thr()
    durs = [dd for _, dd in runs]
    for k, ((m, dd), a) in enumerate(zip(runs, t[:-1])):
        mid = int((a + dd / 2) * FS) - best
        if mid < 0 or mid >= len(lab) or lab[mid] < 0:
            continue
        y = int(lab[mid])
        if m and y < 3: y = 5          # ton där det var paus = störning
        if not m and y >= 3: y = 6     # paus där det var ton = avbrott
        pv = durs[k - 1] if k else dd; nx = durs[k + 1] if k + 1 < len(durs) else dd
        X.append([int(m), np.log(dd / u), np.log(dd / e), np.log(pv / u), np.log(nx / u), np.log(u)])
        Y.append(y)
        # nuvarande regler (k-means-gränser)
        if m:
            r = 5 if dd < max(d.min_el, d.TOPP * d.dot) else (3 if dd < m_thr else 4)
        else:
            r = 6 if dd < d.BRYGGA * e else (2 if dd >= w_thr else (1 if dd >= c_thr else 0))
        R.append(r)
    return np.array(X), np.array(Y), np.array(R)

if __name__ == "__main__":
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.svm import SVC
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    tr = [x for x in (sarddrag_och_etiketter(s) for s in range(1, 81)) if x is not None]
    te = [x for x in (sarddrag_och_etiketter(s) for s in range(1001, 1041)) if x is not None]
    Xtr = np.vstack([a for a, _, _ in tr]); Ytr = np.concatenate([b for _, b, _ in tr])
    Xte = np.vstack([a for a, _, _ in te]); Yte = np.concatenate([b for _, b, _ in te]); Rte = np.concatenate([c for _, _, c in te])
    print(f"träning {len(Ytr)} körningar ur {len(tr)} klipp, test {len(Yte)} ur {len(te)} klipp")
    namn = {0: "paus inom", 1: "paus tecken", 2: "paus ord", 3: "prick", 4: "streck", 5: "störning", 6: "avbrott"}
    print("klassfördelning test:", {namn[k]: int((Yte == k).sum()) for k in sorted(set(Yte))})
    res = {"k-means-regler (nu)": Rte}
    for nm, clf in [("Random Forest", RandomForestClassifier(300, min_samples_leaf=2, random_state=0, n_jobs=-1)),
                    ("SVM (RBF)", make_pipeline(StandardScaler(), SVC(C=10, gamma="scale"))),
                    ("MLP (neuralt nät)", make_pipeline(StandardScaler(), MLPClassifier((64, 32), max_iter=800, random_state=0)))]:
        clf.fit(Xtr, Ytr); res[nm] = clf.predict(Xte)
    for nm, p in res.items():
        acc = np.mean(p == Yte)
        per = {namn[k]: f"{np.mean(p[Yte == k] == k) * 100:.0f}" for k in sorted(set(Yte))}
        print(f"{nm:22} {acc * 100:5.1f} %   {per}")
