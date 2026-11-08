#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
Experiment: neurala nät för Morse, jämförda med Signalspanarens avkodare.

  crnn   spektrogram kring bärvågen → faltningslager (CNN) → dubbelriktat LSTM → CTC → text
  cnn    samma utan LSTM (bara faltningslager → CTC)
  lstm   avkodarens ton- och pauslängder → dubbelriktat LSTM → klass per körning
         (paus inom/mellan tecken/ord, prick, streck, störning, avbrott) – jämförs med
         reglerna och Random Forest (se cwml.py)

Träningsdata kommer från cwgenerator.slumpat() (känt facit). Kräver tensorflow-cpu.

  python3 cwnn.py crnn --antal 6000 --epoker 12
  python3 cwnn.py cnn
  python3 cwnn.py lstm
"""
import argparse
import difflib
import os
import sys
from dataclasses import replace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cwgenerator as G  # noqa: E402
import cwjamfor        # noqa: E402

FS = 8000
HOP = 40                     # 5 ms per ram
NFFT = 160                   # 20 ms fönster, 50 Hz upplösning
BINS = 17                    # ±400 Hz kring bärvågen
ALFABET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/?=.,+-"
IDX = {c: i for i, c in enumerate(ALFABET)}


# ════════════════════════════════════════════════════════════════════
# Data
# ════════════════════════════════════════════════════════════════════
VARIATION = True             # eko, AGC, läckande bärvåg, nära grannar i träningsdatan


def exempel(seed, sek=8.0):
    """Ett slumpat band där station 0 (vid 0 Hz ± lite) har känd nyckling.
    Returnerar (z, [(t_start, t_slut, tecken)] för station 0)."""
    rng = np.random.default_rng(seed)
    band = G.slumpat(rng)
    st = band.stationer[0]
    seq = G.nyckla(st, rng)
    n = int(sek * FS)
    key = np.zeros(n, np.float32)
    t = float(rng.uniform(-sek / 2, 0.3))          # börja mitt i texten ibland
    tecken, t_borjan = [], None
    for v, d, tg in seq:
        if t >= sek:
            break
        a, b = int(max(0, t) * FS), int(max(0, min(sek, t + d)) * FS)
        if v:
            key[a:b] = 1.0
            if t_borjan is None:
                t_borjan = t
        elif tg:
            ch = tg.strip()
            if ch and t_borjan is not None:
                tecken.append((t_borjan, t, ch))
            if tg.endswith(" "):
                tecken.append((t, t + d, " "))
            t_borjan = None
        t += d
    band.stationer[0] = replace(st, text="")
    zrest, _ = G.generera(band, FS, sek, seed=seed)
    tt = np.arange(n) / FS
    L = max(1, int(st.flank_ms * 1e-3 * FS))
    w = np.hanning(2 * L + 1)
    kk = np.convolve(key, w / w.sum(), mode="same")
    zs = kk * np.exp(2j * np.pi * st.off_hz * tt)
    if st.fd:
        zs = zs * G._flutter(n, FS, st.fd, st.rice, rng)
    if st.qsb:
        zs = zs * (1 - st.qsb * (0.5 + 0.5 * np.sin(2 * np.pi * tt / st.qsb_period)))
    m = min(len(zs), len(zrest))
    z = zs[:m] + zrest[:m]
    # ── variation som riktiga signaler har men generatorn inte (annars generaliserar nätet inte) ──
    if VARIATION and rng.random() < 0.3:   # station nästan på samma frekvens (5–60 Hz bort)
        gr = G.Station(G.cq(rng) + " " + G.klartext(rng), wpm=float(rng.uniform(12, 35)),
                       off_hz=st.off_hz + float(rng.choice([-1, 1]) * rng.uniform(5, 60)),
                       niva_db=float(rng.uniform(-15, -3)), start_s=float(rng.uniform(-2, 2)))
        z = z + G.station_signal(gr, FS, m, rng)[0]
    if VARIATION and rng.random() < 0.3:   # kvarvarande bärvåg i pauserna (läckande nyckling)
        z = z + 10 ** (rng.uniform(-30, -12) / 20) * np.exp(2j * np.pi * st.off_hz * tt[:m])
    if VARIATION and rng.random() < 0.3:   # eko/efterklang (rum, högtalare, flervägsutbredning)
        L = int(FS * rng.uniform(0.02, 0.15))
        ir = (rng.standard_normal(L) + 1j * rng.standard_normal(L)) * np.exp(-np.arange(L) / (L / 4))
        ir[0] = 3.0
        z = np.convolve(z, ir / np.abs(ir).sum() * 3, mode="same")
    if VARIATION and rng.random() < 0.3:   # snabb AGC: nivån dras ned under tonerna
        tau = int(FS * rng.uniform(0.05, 0.5))
        env = np.sqrt(np.convolve(np.abs(z) ** 2, np.ones(tau) / tau, mode="same")) + 1e-3
        z = z / env ** rng.uniform(0.5, 1.0)
    return z.astype(np.complex64), tecken


def text_i(tecken, t0, t1):
    s = "".join(c for a, b, c in tecken if a >= t0 and b <= t1)
    return " ".join(s.split())


def spektrogram(z, f0=0.0):
    """Log-spektrogram (ramar × BINS) kring f0, normerat per klipp."""
    t = np.arange(len(z)) / FS
    z = z * np.exp(-2j * np.pi * f0 * t)
    nram = 1 + (len(z) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(nram)[:, None]
    X = np.fft.fftshift(np.fft.fft(z[idx] * np.hanning(NFFT), axis=1), axes=1)
    mid = NFFT // 2
    S = np.log(np.abs(X[:, mid - BINS // 2:mid + BINS // 2 + 1]) ** 2 + 1e-9)
    # varje kanal för sig över tiden: kontrasten ton/paus räknas, inte nivån eller bandets form
    S -= np.percentile(S, 20, axis=0, keepdims=True)
    S /= np.percentile(np.abs(S), 95) + 1e-6
    return S.astype(np.float32)


def datamangd(antal, start, sek=6.0):
    X, Y = [], []
    for i in range(antal):
        z, tec = exempel(start + i, sek)
        y = [IDX[c] for c in text_i(tec, 0, sek) if c in IDX]
        if not y:
            continue
        X.append(spektrogram(z))
        Y.append(y)
    return X, Y


# ════════════════════════════════════════════════════════════════════
# CRNN / CNN med CTC
# ════════════════════════════════════════════════════════════════════
def modell(lstm=True):
    import tensorflow as tf
    L = tf.keras.layers
    inp = L.Input((None, BINS))
    x = L.Conv1D(64, 5, padding="same", activation="relu")(inp)
    x = L.LayerNormalization()(x)
    x = L.Conv1D(64, 5, padding="same", activation="relu")(x)
    x = L.MaxPooling1D(2)(x)                                   # 10 ms
    x = L.LayerNormalization()(x)
    x = L.Conv1D(96, 5, padding="same", activation="relu")(x)
    x = L.MaxPooling1D(2)(x)                                   # 20 ms per steg (prick i 40 ord/min = 30 ms)
    x = L.LayerNormalization()(x)
    if lstm:
        x = L.Bidirectional(L.LSTM(64, return_sequences=True))(x)
        x = L.Bidirectional(L.LSTM(64, return_sequences=True))(x)
    else:
        for d in (2, 4, 8, 16):                                # vidgade faltningar ger sammanhang
            x = L.Conv1D(96, 3, padding="same", dilation_rate=d, activation="relu")(x)
            x = L.LayerNormalization()(x)
    out = L.Dense(len(ALFABET) + 1)(x)                         # sista = CTC-blank
    return tf.keras.Model(inp, out)


def trana_ctc(m, X, Y, epoker, Xv, Yv, lr=3e-3):
    import tensorflow as tf
    # hög inlärningstakt + gradientklippning behövs för att komma förbi CTC:s platå (bara blanktecken)
    opt = tf.keras.optimizers.Adam(lr, clipnorm=5.0)
    B = 32
    rng = np.random.default_rng(0)

    def batch(ix, XX, YY):
        T = max(len(XX[i]) for i in ix)
        xb = np.zeros((len(ix), T, BINS), np.float32)
        for k, i in enumerate(ix):
            xb[k, :len(XX[i])] = XX[i]
        U = max(len(YY[i]) for i in ix)
        yb = np.zeros((len(ix), U), np.int32)
        yl = np.array([len(YY[i]) for i in ix], np.int32)
        for k, i in enumerate(ix):
            yb[k, :len(YY[i])] = YY[i]
        xl = np.array([len(XX[i]) // 4 for i in ix], np.int32)
        return xb, yb, xl, yl

    @tf.function(reduce_retracing=True)
    def steg(xb, yb, xl, yl):
        with tf.GradientTape() as tp:
            lg = m(xb, training=True)
            loss = tf.reduce_mean(tf.nn.ctc_loss(yb, lg, yl, xl, logits_time_major=False,
                                                 blank_index=len(ALFABET)))
        g = tp.gradient(loss, m.trainable_variables)
        opt.apply_gradients(zip(g, m.trainable_variables))
        return loss

    for ep in range(epoker):
        perm = rng.permutation(len(X))
        ls = []
        for s in range(0, len(perm) - B + 1, B):
            ls.append(float(steg(*batch(perm[s:s + B], X, Y))))
        cer = np.mean([fel(ALFABET_text(Yv[i]), avkoda_nn(m, Xv[i])) for i in range(min(150, len(Xv)))])
        print(f"  epok {ep + 1}: förlust {np.mean(ls):.3f}, teckenfel (validering) {cer * 100:.1f} %", flush=True)
        if ep == epoker // 2:
            opt.learning_rate.assign(lr / 3)
        elif ep == (3 * epoker) // 4:
            opt.learning_rate.assign(lr / 10)


def ALFABET_text(y):
    return "".join(ALFABET[i] for i in y)


def avkoda_nn(m, S):
    import tensorflow as tf
    if not hasattr(m, "_inf"):         # kompilerad avkodning (LSTM i eager-läge är mycket långsamt)
        m._inf = tf.function(lambda x: m(x, training=False), reduce_retracing=True,
                             input_signature=[tf.TensorSpec((1, None, BINS), tf.float32)])
    lg = m._inf(tf.constant(S[None]))
    dec, _ = tf.nn.ctc_greedy_decoder(tf.transpose(lg, (1, 0, 2)), [lg.shape[1]], blank_index=len(ALFABET))
    ix = tf.sparse.to_dense(dec[0]).numpy()[0]
    return " ".join("".join(ALFABET[i] for i in ix if i < len(ALFABET)).split())


def fel(facit, ut):
    return 1 - difflib.SequenceMatcher(None, facit, ut, autojunk=False).ratio()


def utvardera(m):
    """Hela klipp: generatorns scenarier (40 s) och riktiga klipp; jämfört med Signalspanaren."""
    from math import gcd
    from scipy.signal import resample_poly
    rader = []
    for sc in G.SCENARIER:
        e_nn, e_ss = [], []
        for s in (1, 2):
            z, facit = G.generera(G.scenario(sc, np.random.default_rng(s)), 12000, 40, s)
            ss = cwjamfor.signalspanaren(z, 12000, mitt=0.0)[0]
            z8 = resample_poly(z, 2, 3).astype(np.complex64)
            nn = avkoda_nn(m, spektrogram(z8))
            e_nn.append(fel(facit, nn))
            e_ss.append(fel(facit, ss))
        rader.append((sc, np.mean(e_ss), np.mean(e_nn), nn[:60]))
    mapp = os.path.join(HERE, "testdata", "cw")
    for f in sorted(os.listdir(mapp)) if os.path.isdir(mapp) else []:
        txt = os.path.join(mapp, f[:-4] + ".txt")
        if not (f.endswith(".wav") and os.path.exists(txt)):
            continue
        facit = " ".join(open(txt, encoding="utf-8").read().upper().split())
        x, fs, iq = cwjamfor.las_wav(os.path.join(mapp, f))
        z, _ = cwjamfor.till_basband(x, fs, not iq)
        g = gcd(int(fs), FS)
        z8 = resample_poly(z, FS // g, int(fs) // g).astype(np.complex64)
        ss = cwjamfor.signalspanaren(z, fs)[0]
        nn = avkoda_nn(m, spektrogram(z8))
        rader.append(("riktig: " + f[:-4], fel(facit, ss), fel(facit, nn), nn[:60]))
    print(f"\n{'Prov':28} {'Signalspanaren':>15} {'nät':>8}")
    for sc, a, b, t in rader:
        print(f"{sc:28} {a * 100:13.1f} % {b * 100:6.1f} %   {t!r}")
    syn = [(a, b) for sc, a, b, _ in rader if not sc.startswith("riktig")]
    print(f"{'Medel scenarier':28} {np.mean([a for a, _ in syn]) * 100:13.1f} % {np.mean([b for _, b in syn]) * 100:6.1f} %")
    return rader


# ════════════════════════════════════════════════════════════════════
# LSTM på pulsföljder (tidsavstånd)
# ════════════════════════════════════════════════════════════════════
def lstm_pulser(antal_tr=80, antal_te=40):
    import cwml
    import tensorflow as tf
    L = tf.keras.layers

    def sekvenser(seeds):
        out = []
        for s in seeds:
            r = cwml.sarddrag_och_etiketter(s)
            if r is not None:
                out.append(r)
        return out
    tr = sekvenser(range(1, 1 + antal_tr))
    te = sekvenser(range(1001, 1001 + antal_te))
    mu = np.vstack([x for x, _, _ in tr]).mean(0)
    sd = np.vstack([x for x, _, _ in tr]).std(0) + 1e-6
    inp = L.Input((None, tr[0][0].shape[1]))
    x = L.Bidirectional(L.LSTM(64, return_sequences=True))(inp)
    x = L.Bidirectional(L.LSTM(64, return_sequences=True))(x)
    out = L.Dense(7, activation="softmax")(x)
    m = tf.keras.Model(inp, out)
    m.compile(tf.keras.optimizers.Adam(1e-3), "sparse_categorical_crossentropy")
    # träna på bitar om 200 körningar
    Xs, Ys = [], []
    for x, y, _ in tr:
        for a in range(0, len(y) - 50, 100):
            Xs.append((x[a:a + 200] - mu) / sd)
            Ys.append(y[a:a + 200])
    T = max(len(v) for v in Xs)
    Xp = np.zeros((len(Xs), T, Xs[0].shape[1]), np.float32)
    Yp = np.zeros((len(Xs), T), np.int32)
    W = np.zeros((len(Xs), T), np.float32)
    for i, (a, b) in enumerate(zip(Xs, Ys)):
        Xp[i, :len(a)], Yp[i, :len(b)], W[i, :len(b)] = a, b, 1
    m.fit(Xp, Yp, sample_weight=W, batch_size=32, epochs=25, verbose=0)
    P = np.concatenate([m.predict(((x - mu) / sd)[None], verbose=0)[0].argmax(1) for x, _, _ in te])
    Y = np.concatenate([y for _, y, _ in te])
    R = np.concatenate([r for _, _, r in te])
    namn = ["paus inom", "paus tecken", "paus ord", "prick", "streck", "störning", "avbrott"]
    for nm, p in (("k-means-regler (nu)", R), ("BiLSTM på pulsföljder", P)):
        per = {namn[k]: f"{np.mean(p[Y == k] == k) * 100:.0f}" for k in sorted(set(Y))}
        print(f"{nm:24} {np.mean(p == Y) * 100:5.1f} %   {per}")


def main():
    ap = argparse.ArgumentParser(prog="cwnn")
    ap.add_argument("typ", choices=["crnn", "cnn", "lstm"])
    ap.add_argument("--antal", type=int, default=6000)
    ap.add_argument("--epoker", type=int, default=12)
    ap.add_argument("--spara", help="spara modellen (.keras)")
    ap.add_argument("--fran", metavar="MODELL", help="fortsätt träna en sparad modell (.keras)")
    ap.add_argument("--lr", type=float, default=3e-3, help="inlärningstakt i början")
    ap.add_argument("--utan-variation", action="store_true", help="träningsdata utan eko/AGC/läckage/grannar")
    a = ap.parse_args()
    if a.typ == "lstm":
        lstm_pulser()
        return 0
    global VARIATION
    VARIATION = not a.utan_variation
    print(f"Skapar {a.antal} träningsklipp (6 s{'' if VARIATION else ', utan variation'}) …", flush=True)
    X, Y = datamangd(a.antal, 100000)
    Xv, Yv = datamangd(300, 900000)
    print(f"{len(X)} klipp, {sum(len(y) for y in Y)} tecken. Tränar {a.typ} …", flush=True)
    if a.fran:
        import tensorflow as tf
        k = tf.keras.models.load_model(a.fran, compile=False)
        if a.typ == "cnn" and any("lstm" in l.name for l in k.layers):
            # CNN med förtränade faltningslager från en CRNN (de första lagren är likadana)
            m = modell(lstm=False)
            n = 0
            for lm, lk in zip(m.layers, k.layers):
                if "bidirectional" in lk.name or [w.shape for w in lm.get_weights()] != [w.shape for w in lk.get_weights()]:
                    break
                lm.set_weights(lk.get_weights())
                n += 1
            print(f"CNN: {n} lager kopierade från {a.fran}", flush=True)
        else:
            m = k
            print(f"Fortsätter från {a.fran}", flush=True)
    else:
        m = modell(lstm=(a.typ == "crnn"))
    trana_ctc(m, X, Y, a.epoker, Xv, Yv, lr=a.lr)
    if a.spara:
        m.save(a.spara)
    utvardera(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
