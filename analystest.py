#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
Mätverktyg för Signalspanarens signalanalys.

Kör analysen på IQ-klipp med känt facit och visar hur ofta den har rätt: signalfamilj,
uppmätt bandbredd och förslag ur signaldatabasen. Används för att se om en ändring gör
analysen bättre eller sämre, och som underlag för att träna klassningen.

  python3 analystest.py mat                    mät på inspelade klipp + syntetiska signaler
  python3 analystest.py mat --bara-syntetiskt  bara de syntetiska (snabbt, ingen nätåtkomst)
  python3 analystest.py mat --spara r.json     spara resultatet (för jämförelse senare)
  python3 analystest.py mat --jamfor r.json    visa vad som blivit bättre/sämre mot en tidigare körning
  python3 analystest.py samla                  spela in klipp från KiwiSDR för signalerna i facit.json
  python3 analystest.py samla "DWD RTTY 10,1008 MHz"   bara vissa
  python3 analystest.py spela-in --frekvens 7.1 --fam SSB --bredd 1800-3200 --namn "SSB 40 m"
                                               ett eget klipp med facit (t.ex. något du hört)
  python3 analystest.py morse                  mät Morse-avkodaren (teckenfel vid olika takt, brus,
                                               Farnsworth, handnyckling, fädning)

Klippen sparas som SigMF (cf32) i ~/.local/share/signalspanaren/facit-klipp/ med facit i
metadatat ("signalspanaren:facit"). Facit-listan för `samla` finns i facit.json bredvid skriptet.
"""
import argparse
import collections
import json
import os
import re
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analys        # noqa: E402
import iqfil         # noqa: E402

DATA = os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local/share"))
KLIPP_DIR = os.path.join(DATA, "signalspanaren", "facit-klipp")
CONF_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")),
                         "signalspanaren", "config.json")
FACIT_FILE = os.path.join(HERE, "facit.json")
GISSNING = 300.0       # analysen startar som om man klickat på en bärvåg: 300 Hz första gissning


# ════════════════════════════════════════════════════════════════════
# Kör analysen på ett klipp (som i programmet: kanal → analyssteg var 0,35 s)
# ════════════════════════════════════════════════════════════════════
NY_MOTOR = True


def visad_fam(f):
    """Det panelen visar som signalfamilj i steget (bevismotorn, annars den gamla klassningen)."""
    v = f.get("visad")
    if NY_MOTOR and v:
        return {"FT8": "MFSK", "FT4": "FSK4"}.get(v["fam"], v["fam"])
    return f.get("fam")


def kor_analys(z, rate, f_hz, gissning=GISSNING, steg=0.35, mitt=None):
    """f_hz = signalens frekvens (där man klickar), mitt = klippets mittfrekvens (standard samma)."""
    mitt = f_hz if mitt is None else mitt
    an = analys.Analyzer(rate)
    an.ny_motor = NY_MOTOR
    an.set_target(f_hz, gissning, auto=True)
    blk = max(256, int(rate * 0.05))
    per_steg = int(rate * steg)
    t, fed, nasta = 0.0, 0, per_steg
    feats = []
    for i in range(0, len(z) - blk + 1, blk):
        an.tap.feed(z[i:i + blk], mitt)
        fed += blk
        if fed >= nasta:
            nasta += per_steg
            t += steg
            r = an.step(now=t)
            if r is not None:
                feats.append(r[0])
    return an, feats


def sammanfatta(an, feats):
    """Resultatet efter att mätningen stabiliserats: sista halvan av analysstegen."""
    ft = an.fit or {}
    ut = {"bredd": ft.get("width"), "kanal": an.tap.bw, "steg": len(feats)}
    if not feats:
        ut.update(fam=None, klass="(inget resultat)", kandidat=None, snr=None)
        return ut
    vis = [visad_fam(f) for f in feats if f.get("fam") is not None]
    ut["visade"] = vis
    ut["byten"] = sum(1 for a, b in zip(vis, vis[1:]) if a != b)
    sista = feats[len(feats) // 2:]
    fam = collections.Counter(f.get("fam") for f in sista).most_common(1)[0][0]
    kand = collections.Counter((f.get("kandidat") or (0, {"namn": None}))[1].get("namn") for f in sista)
    ut.update(fam=fam, klass=analys.FAM_NAMN.get(fam, fam), kandidat=kand.most_common(1)[0][0],
              snr=float(np.median([f.get("snr", 0.0) for f in sista])),
              baud=float(np.median([f.get("baud") or 0.0 for f in sista])) or None)
    return ut


def bedom(facit, res):
    fam_ok = res["fam"] in facit["fam"] if facit.get("fam") else None
    b = facit.get("bredd")
    bredd_ok = (res["bredd"] is not None and b[0] <= res["bredd"] <= b[1]) if b else None
    k = facit.get("kandidat")
    kand_ok = (res["kandidat"] == k) if k else None
    vis = res.get("visade") or []
    efter = vis[min(len(vis) - 1, 9):] if vis else []          # efter ~3 s
    vis_ok = (sum(1 for x in efter if x in facit["fam"]) / len(efter)) if (facit.get("fam") and efter) else None
    return {"fam_ok": fam_ok, "bredd_ok": bredd_ok, "kand_ok": kand_ok, "vis_ok": vis_ok}


# ════════════════════════════════════════════════════════════════════
# Syntetiska signaler (fs 12 kHz som en KiwiSDR) med facit
# ════════════════════════════════════════════════════════════════════
def _lp_brus(n, fs, f_lo, f_hi, seed):
    """Bandbegränsat brus (tal-likt) mellan f_lo och f_hi Hz, reellt, rms 1."""
    rng = np.random.default_rng(seed)
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / fs)
    X[(f < f_lo) | (f > f_hi)] = 0
    X *= 1.0 / (1.0 + (f / 1500.0) ** 2) ** 0.5          # fallande spektrum som tal
    x = np.fft.irfft(X, n)
    # stavelser: amplituden varierar några gånger per sekund
    env = 0.4 + 0.6 * np.abs(np.sin(2 * np.pi * 2.7 * np.arange(n) / fs + rng.uniform(0, 6)))
    x = x * env
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-12)


def _med_brus(sig, snr_db, bredd, fs, seed=11):
    """Lägg till komplext brus så att SNR i signalens bandbredd blir snr_db."""
    rng = np.random.default_rng(seed)
    p_sig = float(np.mean(np.abs(sig) ** 2))
    # brus per Hz så att bruset i 'bredd' Hz är p_sig / snr
    n0 = p_sig / (10 ** (snr_db / 10)) / bredd
    sigma = np.sqrt(n0 * fs / 2)
    return (sig + sigma * (rng.standard_normal(len(sig)) + 1j * rng.standard_normal(len(sig)))).astype(np.complex64)


def _loop(x, n):
    reps = int(np.ceil(n / len(x)))
    return np.tile(x, reps)[:n]


def syntetiska(fs=12000.0, sek=12.0, snr=20.0):
    n = int(fs * sek)
    t = np.arange(n) / fs
    ut = []

    def lagg(namn, sig, bredd_sig, facit):
        facit = dict(facit, namn=namn)
        ut.append((namn, _med_brus(sig.astype(np.complex64), snr, bredd_sig, fs), fs, facit))

    tal = _lp_brus(n, fs, 200, 4500, 1)
    lagg("Syntetisk AM-rundradio (9 kHz)", 1.0 + 0.45 * tal, 9000,
         {"fam": ["AM"], "bredd": [6000, 10500]})
    ssb = np.fft.ifft(np.where(np.fft.fftfreq(n, 1 / fs) > 0, np.fft.fft(_lp_brus(n, fs, 300, 2700, 2)), 0))
    ssb = ssb * np.exp(-2j * np.pi * 1500 * t)          # mitt på signalen, som när man klickar på den
    lagg("Syntetisk SSB-tal (USB)", ssb, 2400, {"fam": ["SSB"], "bredd": [1800, 3200]})
    ph = 2 * np.pi * 2000 * np.cumsum(_lp_brus(n, fs, 300, 3000, 3)) / fs      # FM med tal, ±2 kHz rms
    lagg("Syntetisk FM-tal (NFM)", np.exp(1j * ph), 9000, {"fam": ["FM"], "bredd": [5000, 11000]})
    lagg("Syntetisk RTTY 50 Bd / 450 Hz", _loop(analys.make_rtty(fs, 50, 450, "RYRY CQ CQ DE TEST 1234 " * 8), n),
         600, {"fam": ["FSK2"], "bredd": [450, 1000]})
    lagg("Syntetisk 8PSK 2400 Bd", _loop(analys.make_psk(fs, 2400, 8, 6.0), n), 3240,
         {"fam": ["PSK8"], "bredd": [2400, 3800]})
    lagg("Syntetisk QPSK 1200 Bd", _loop(analys.make_psk(fs, 1200, 4, 6.0, seed=7), n), 1620,
         {"fam": ["QPSK"], "bredd": [1100, 2100]})
    lagg("Syntetisk BPSK 1200 Bd", _loop(analys.make_psk(fs, 1200, 2, 6.0, seed=5), n), 1620,
         {"fam": ["BPSK"], "bredd": [1100, 2100]})
    ale = [750 + 250 * i - 1625 for i in range(8)]
    lagg("Syntetisk 8-FSK 125 Bd (ALE-lik)", _loop(analys.make_mfsk(fs, 125, ale, 6.0), n), 2000,
         {"fam": ["MFSK"], "bredd": [1600, 2600]})
    l11 = [935 + 110 * i - 1925 for i in range(16)]
    lagg("Syntetisk 16 toner 75 Bd", _loop(analys.make_ofdm(fs, 75, l11, 6.0), n), 1800,
         {"fam": ["OFDM"], "bredd": [1500, 2300]})
    lagg("Syntetisk Morse 18 wpm", _loop(analys.make_morse(fs, "CQ CQ DE SM0ABC SM0ABC K   "), n), 100,
         {"fam": ["OOK"], "bredd": [10, 300]})
    lagg("Syntetisk omodulerad bärvåg", np.ones(n), 50, {"fam": ["BÄRVÅG"], "bredd": [5, 150]})
    rng = np.random.default_rng(9)
    brus = ((rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.01).astype(np.complex64)
    ut.append(("Bara brus", brus, fs, {"namn": "Bara brus", "fam": ["BRUS"], "bredd": None}))
    return ut


# ════════════════════════════════════════════════════════════════════
# Inspelade klipp
# ════════════════════════════════════════════════════════════════════
def las_klipp(mapp):
    ut = []
    if not os.path.isdir(mapp):
        return ut
    for fn in sorted(os.listdir(mapp)):
        if not fn.endswith(".sigmf-meta"):
            continue
        p = os.path.join(mapp, fn)
        try:
            with open(p, encoding="utf-8") as fh:
                meta = json.load(fh)
            facit = meta["global"].get("signalspanaren:facit")
            if not facit:
                continue
            r = iqfil.IQReader(p)
            z = r.read(0, r.n)
            facit = dict(facit, _mitt=r.captures[0][1] if r.captures else facit.get("frekvens"))
            ut.append((facit.get("namn") or fn, z, r.rate, facit))
        except Exception as ex:
            print(f"  kunde inte läsa {fn}: {ex}", file=sys.stderr)
    return ut


def _fmt_bredd(b):
    if b is None:
        return "–"
    return f"{b / 1000:.2f} kHz" if b >= 1000 else f"{b:.0f} Hz"


def _bock(v):
    return "–" if v is None else ("ok" if v else "FEL")


def mat(args):
    global NY_MOTOR
    NY_MOTOR = not getattr(args, "gammal", False)
    fall = []
    if not args.bara_syntetiskt:
        fall += [("inspelat",) + x for x in las_klipp(args.mapp)]
    if not args.bara_inspelat:
        fall += [("syntetiskt",) + x for x in syntetiska()]
    if not fall:
        print("Inga klipp att mäta på. Spela in med:  python3 analystest.py samla")
        return 1
    rader = []
    t0 = time.time()
    for typ, namn, z, rate, facit in fall:
        f_sig = float(facit.get("frekvens") or 5.0e6)
        an, feats = kor_analys(z, rate, f_sig, mitt=float(facit.get("_mitt") or f_sig))
        res = sammanfatta(an, feats)
        rader.append({"typ": typ, "namn": namn, "facit": facit, "res": res, **bedom(facit, res)})
    print(f"\n{'Signal':40} {'Facit':12} {'Fick':12} {'Bredd facit':>18} {'Uppmätt':>10}  Fam  Bredd Visat Byten Förslag")
    print("─" * 120)
    for r in rader:
        f, res = r["facit"], r["res"]
        bf = "–" if not f.get("bredd") else f"{_fmt_bredd(f['bredd'][0])}–{_fmt_bredd(f['bredd'][1])}"
        kand = res.get("kandidat") or "–"
        if f.get("kandidat"):
            kand += f"  [{_bock(r['kand_ok'])}]"
        vtxt = "–" if r["vis_ok"] is None else f"{100 * r['vis_ok']:.0f}%"
        print(f"{r['namn'][:40]:40} {'/'.join(f.get('fam') or ['?'])[:12]:12} {str(res['fam'])[:12]:12} "
              f"{bf:>18} {_fmt_bredd(res['bredd']):>10}  {_bock(r['fam_ok']):4} {_bock(r['bredd_ok']):5} "
              f"{vtxt:>5} {res.get('byten', 0):5} {kand[:40]}")
    print("─" * 120)

    def andel(nyckel, typ=None):
        v = [r[nyckel] for r in rader if r[nyckel] is not None and (typ is None or r["typ"] == typ)]
        return (sum(v), len(v)) if v else (0, 0)

    for typ in ("inspelat", "syntetiskt", None):
        if typ and not any(r["typ"] == typ for r in rader):
            continue
        a, b, c = andel("fam_ok", typ), andel("bredd_ok", typ), andel("kand_ok", typ)
        txt = (typ or "totalt").capitalize()
        vv = [r["vis_ok"] for r in rader if r["vis_ok"] is not None and (typ is None or r["typ"] == typ)]
        by = [r["res"].get("byten", 0) for r in rader if (typ is None or r["typ"] == typ)]
        print(f"{txt:12}  signalfamilj {a[0]}/{a[1]}   bandbredd {b[0]}/{b[1]}   förslag {c[0]}/{c[1]}   "
              f"visat rätt {100 * np.mean(vv) if vv else 0:.0f} % av stegen   byten {sum(by)}")
    # förväxlingar
    conf = collections.Counter((r["facit"]["fam"][0], r["res"]["fam"]) for r in rader
                               if r["fam_ok"] is False)
    if conf:
        print("\nFörväxlingar (facit → fick):")
        for (a, b), n in conf.most_common():
            print(f"  {a:8} → {b}  ×{n}")
    print(f"\n{len(rader)} klipp på {time.time() - t0:.0f} s")
    if args.spara:
        with open(args.spara, "w", encoding="utf-8") as fh:
            json.dump(rader, fh, ensure_ascii=False, indent=1, default=float)
        print(f"Sparat: {args.spara}")
    if args.jamfor:
        with open(args.jamfor, encoding="utf-8") as fh:
            gamla = {r["namn"]: r for r in json.load(fh)}
        print(f"\nJämfört med {args.jamfor}:")
        andrat = False
        for r in rader:
            g = gamla.get(r["namn"])
            if not g:
                continue
            for nyckel, ord_ in (("fam_ok", "signalfamilj"), ("bredd_ok", "bandbredd"), ("kand_ok", "förslag")):
                if g[nyckel] is not None and r[nyckel] is not None and g[nyckel] != r[nyckel]:
                    andrat = True
                    print(f"  {'BÄTTRE' if r[nyckel] else 'SÄMRE '}  {r['namn']}: {ord_}")
        if not andrat:
            print("  inga ändringar")
    return 0


# ════════════════════════════════════════════════════════════════════
# Inspelning från KiwiSDR
# ════════════════════════════════════════════════════════════════════
def kiwi_adress(args):
    if args.kiwi:
        return args.kiwi
    try:
        with open(CONF_FILE, encoding="utf-8") as fh:
            a = json.load(fh).get("kiwi_addr", "").strip()
        if a:
            return a
    except (OSError, ValueError):
        pass
    sys.exit("Ange en KiwiSDR med --kiwi ADRESS (eller välj en i programmet först).")


def spela_in_kiwi(addr, f_hz, sek):
    import kiwisdr
    kc = kiwisdr.KiwiClient(addr, ident="Signalspanaren-analystest")
    kc.connect()
    try:
        kc.set_gain(None)
        kc.set_freq(f_hz)
        kc.read_iq(int(kc.rate * 1.0))             # släng första sekunden (AGC ställer in sig)
        z = kc.read_iq(int(kc.rate * sek), timeout=sek + 10)
        return z, kc.rate
    finally:
        kc.close()


def spara_klipp(mapp, facit, z, rate, f_hz, addr):
    os.makedirs(mapp, exist_ok=True)
    namn = re.sub(r"[^A-Za-z0-9åäöÅÄÖ._-]+", "_", facit["namn"]).strip("_")
    base = os.path.join(mapp, f"{namn}_{time.strftime('%Y%m%d-%H%M%S')}")
    w = iqfil.SigMFWriter(base, rate, f_hz, datatype="cf32_le",
                          description=f"Facit-klipp för analystest: {facit['namn']}",
                          extra={"signalspanaren:facit": facit, "signalspanaren:kalla": addr})
    w.write(z, f_hz)
    w.close()
    return base


def kontroll(z, rate):
    """Toppens SNR mot brusgolvet i klippet – varnar om signalen verkar saknas."""
    f, P = analys._welch(z, rate, 1024)
    return 10 * np.log10(P.max() / np.median(P))


def samla(args):
    addr = kiwi_adress(args)
    with open(FACIT_FILE, encoding="utf-8") as fh:
        lista = json.load(fh)
    if args.namn:
        lista = [x for x in lista if x["namn"] in args.namn]
    for facit in lista:
        f = float(facit["frekvens"])
        print(f"{facit['namn']}: spelar in {args.sekunder:.0f} s på {f / 1e6:.4f} MHz …", flush=True)
        try:
            z, rate = spela_in_kiwi(addr, f, args.sekunder)
        except Exception as ex:
            print(f"  misslyckades: {ex}")
            continue
        snr = kontroll(z, rate)
        base = spara_klipp(args.mapp, facit, z, rate, f, addr)
        varn = "  ← VARNING: svag eller ingen signal, kontrollera innan klippet används" if snr < 12 else ""
        print(f"  sparat {os.path.basename(base)} (topp {snr:.0f} dB över bruset){varn}")
        time.sleep(1)
    return 0


def _intervall(s):
    a, b = s.replace("–", "-").split("-")
    return [float(a), float(b)]


def spela_in(args):
    addr = kiwi_adress(args)
    f = float(args.frekvens) * (1e6 if float(args.frekvens) < 1000 else 1)
    facit = {"namn": args.namn or f"{args.fam} {f / 1e6:.4f} MHz", "frekvens": f, "fam": args.fam.split(","),
             "bredd": _intervall(args.bredd) if args.bredd else None}
    if args.kandidat:
        facit["kandidat"] = args.kandidat
    print(f"Spelar in {args.sekunder:.0f} s på {f / 1e6:.4f} MHz från {addr} …", flush=True)
    z, rate = spela_in_kiwi(addr, f, args.sekunder)
    base = spara_klipp(args.mapp, facit, z, rate, f, addr)
    print(f"Sparat {base}.sigmf-meta (topp {kontroll(z, rate):.0f} dB över bruset)")
    return 0


# ════════════════════════════════════════════════════════════════════
# Morse-avkodaren: teckenfel mot facit under olika förhållanden
# ════════════════════════════════════════════════════════════════════
MORSE_TEXT = "CQ CQ DE SM0ABC SM0ABC PSE K TEST 73 THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 1234567890"
MORSE_FALL = [
    ("12 ord/min", dict(wpm=12)), ("18 ord/min", dict(wpm=18)), ("25 ord/min", dict(wpm=25)),
    ("35 ord/min", dict(wpm=35)), ("45 ord/min", dict(wpm=45)), ("8 ord/min", dict(wpm=8)),
    ("SNR 6 dB", dict(wpm=18, snr=6)), ("SNR 3 dB", dict(wpm=18, snr=3)), ("SNR 0 dB", dict(wpm=18, snr=0)),
    ("Farnsworth 20/10", dict(wpm=20, fw=10)), ("Farnsworth 25/12", dict(wpm=25, fw=12)),
    ("Handnyckel ±15 %", dict(wpm=16, jitter=0.15)), ("Handnyckel ±25 %", dict(wpm=16, jitter=0.25)),
    ("Tunga streck 1:4", dict(wpm=20, dash=4.0)), ("Lätta streck 1:2,5", dict(wpm=20, dash=2.5)),
    ("150 Hz vid sidan", dict(wpm=20, off=150)), ("−300 Hz vid sidan", dict(wpm=20, off=-300)),
    ("Fädning (QSB) 80 %", dict(wpm=20, qsb=0.8)), ("fs 48 kHz", dict(wpm=20, fs=48000)),
    ("fs 12 kHz, 0,1 s bitar", dict(wpm=22, fs=12000, chunk=0.1)),
    ("Flutter 5 Hz (som FAV22)", dict(wpm=24, fd=5, rice=1.0, snr=12)),
    ("Flutter 10 Hz, djup", dict(wpm=24, fd=10, rice=0.3, snr=12)),
]


def morse_nyckel(text, wpm, fw=None, jitter=0.0, dash=3.0, rng=None):
    """[(på/av, sekunder)] för texten. fw = Farnsworth-takt för mellanrummen, jitter = handnyckling."""
    rev = {v: k for k, v in analys.MORSE.items()}
    u = 1.2 / wpm
    uc = 1.2 / fw if fw else u

    def j():
        return max(0.2, 1 + jitter * rng.standard_normal()) if jitter else 1.0
    ut = []
    for w in text.split():
        for ch in w:
            for s in rev.get(ch, ""):
                ut += [(1, u * (1 if s == "." else dash) * j()), (0, u * j())]
            ut[-1] = (0, 3 * uc * j())
        ut[-1] = (0, 7 * uc * j())
    return ut


def flutter(n, fs, fd, rice, rng):
    """Snabb fädning som på kortvåg (flervägsutbredning): Rice-kanal med dopplerspridning fd Hz.
    rice = effekt i den fasta vägen / effekt i de spridda (lågt värde = djupa, korta avbrott)."""
    m = max(8, int(n * 2 * fd / fs) + 8)                  # spridda vägen samplas glest och interpoleras
    g = (rng.standard_normal(m) + 1j * rng.standard_normal(m)) / np.sqrt(2)
    g = analys._smooth(g.real, 2) + 1j * analys._smooth(g.imag, 2)
    g /= np.sqrt(np.mean(np.abs(g) ** 2))
    tg = np.linspace(0, n, m)
    h = np.interp(np.arange(n), tg, g.real) + 1j * np.interp(np.arange(n), tg, g.imag)
    return (np.sqrt(rice / (rice + 1)) + np.sqrt(1 / (rice + 1)) * h).astype(np.complex64)


def morse_signal(nyckel, fs, snr_db, rng, off=0.0, qsb=0.0, fd=0.0, rice=1.0):
    """Komplext basband; snr_db räknas i 500 Hz bandbredd. fd > 0 ger snabb fädning (flutter)."""
    k = np.concatenate([np.full(max(1, int(d * fs)), v, np.float32) for v, d in nyckel])
    k = analys._smooth(k, fs * 0.004)
    t = np.arange(len(k)) / fs
    z = k * np.exp(2j * np.pi * off * t) * (1 - qsb * (0.5 + 0.5 * np.sin(2 * np.pi * 0.15 * t)))
    if fd:
        z = z * flutter(len(k), fs, fd, rice, rng)
    sigma = 10 ** (-snr_db / 20) / np.sqrt(2) * np.sqrt(fs / 500)
    return (z + sigma * (rng.standard_normal(len(k)) + 1j * rng.standard_normal(len(k)))).astype(np.complex64)


def morse_fel(facit, fick):
    """Andel teckenfel: bästa matchning av facit någonstans i den avkodade texten."""
    import difflib
    best = 1.0
    for st in range(0, max(1, len(fick) - len(facit) + 1)):
        best = min(best, 1 - difflib.SequenceMatcher(None, facit, fick[st:st + len(facit)]).ratio())
    return best


def morse_kor(wpm, fs=8000, snr=20, fw=None, jitter=0.0, dash=3.0, off=0.0, qsb=0.0, chunk=0.35, seed=1,
              fd=0.0, rice=1.0):
    rng = np.random.default_rng(seed)
    # texten sänds två gånger: det andra varvet mäts, när avkodaren har lärt sig takten
    z = morse_signal(morse_nyckel(MORSE_TEXT + " " + MORSE_TEXT, wpm, fw, jitter, dash, rng), fs, snr, rng, off, qsb, fd, rice)
    d = analys.CwAvkodare()
    c = int(chunk * fs)
    ut = "".join(d.feed(z[i:i + c], fs, {"fam": "OOK"}) for i in range(0, len(z), c))
    return morse_fel(MORSE_TEXT, " ".join(ut.split())), " ".join(ut.split()), d


def morse(args):
    tot = 0.0
    print(f"{'Fall':26} {'Fel':>6}  {'Uppmätt':28} Slutet av texten")
    print("─" * 110)
    for namn, kw in MORSE_FALL:
        e, ut, d = morse_kor(**kw, seed=args.seed)
        tot += e
        v = ", ".join(x[1] for x in d.varden())
        print(f"{namn:26} {e * 100:5.1f} %  {v[:28]:28} {ut[-48:]!r}")
    print("─" * 110)
    print(f"{'Medel':26} {tot / len(MORSE_FALL) * 100:5.1f} %")
    # riktiga inspelningar med facit: testdata/cw/<namn>.wav + <namn>.txt
    mapp = os.path.join(HERE, "testdata", "cw")
    klipp = sorted(f for f in os.listdir(mapp) if f.endswith(".wav")) if os.path.isdir(mapp) else []
    if klipp:
        import cwjamfor
        import difflib
        print(f"\n{'Inspelning':26} {'Fel':>6}  Avkodat")
        print("─" * 110)
        for f in klipp:
            txt = os.path.join(mapp, f[:-4] + ".txt")
            if not os.path.exists(txt):
                continue
            with open(txt, encoding="utf-8") as fh:
                facit = " ".join(fh.read().upper().split())
            x, fs, iq = cwjamfor.las_wav(os.path.join(mapp, f))
            z, _ = cwjamfor.till_basband(x, fs, not iq)
            ut, _ = cwjamfor.signalspanaren(z, fs)
            e = 1 - difflib.SequenceMatcher(None, facit, ut, autojunk=False).ratio()
            print(f"{f[:-4][:26]:26} {e * 100:5.1f} %  {ut[:70]!r}")
    return 0


def main():
    p = argparse.ArgumentParser(prog="analystest", description="Mät Signalspanarens signalanalys mot facit.")
    p.add_argument("--mapp", default=KLIPP_DIR, help=f"mapp för facit-klipp (standard {KLIPP_DIR})")
    sub = p.add_subparsers(dest="kommando", required=True)
    m = sub.add_parser("mat", help="mät analysen på klippen")
    m.add_argument("--bara-syntetiskt", action="store_true")
    m.add_argument("--gammal", action="store_true", help="utan bevismotorn (som före 2026-11-08)")
    m.add_argument("--bara-inspelat", action="store_true")
    m.add_argument("--spara", metavar="FIL")
    m.add_argument("--jamfor", metavar="FIL")
    s = sub.add_parser("samla", help="spela in klipp för facit.json från KiwiSDR")
    s.add_argument("namn", nargs="*", help="bara dessa (namn i facit.json)")
    s.add_argument("--kiwi", metavar="ADRESS")
    s.add_argument("--sekunder", type=float, default=20.0)
    e = sub.add_parser("spela-in", help="spela in ett eget klipp med facit")
    e.add_argument("--frekvens", required=True, help="MHz (t.ex. 7.1) eller Hz")
    e.add_argument("--fam", required=True, help="signalfamilj, t.ex. AM, SSB, FSK2 (flera med komma)")
    e.add_argument("--bredd", help="förväntad bandbredd i Hz, t.ex. 1800-3200")
    e.add_argument("--kandidat", help="förväntat namn i signaldatabasen")
    e.add_argument("--namn")
    e.add_argument("--kiwi", metavar="ADRESS")
    e.add_argument("--sekunder", type=float, default=20.0)
    c = sub.add_parser("morse", help="mät Morse-avkodaren på syntetisk CW (takt, brus, Farnsworth, handnyckling)")
    c.add_argument("--seed", type=int, default=1)
    args = p.parse_args()
    return {"mat": mat, "samla": samla, "spela-in": spela_in, "morse": morse}[args.kommando](args)


if __name__ == "__main__":
    sys.exit(main())
