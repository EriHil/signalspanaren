"""Bevismotor för signalanalysen.

Den gamla klassningen (analys.analyze) fäller en ny dom varje analyssteg (0,35–1 s signal). En kort bit
av en signal kan se ut som nästan vad som helst – en paus i talet ser ut som nyckling, en stavelse som en
ton – så domen hoppar. Bevismotorn låter i stället varje steg ge mjuka poäng (log-sannolikhet) till alla
hypoteser och summerar dem över tid med ett minne på ~10 s. Till det läggs förkunskap: bandplanen
(FT8/FT4 finns bara i sina delband, VOLMET på flygbanden …) och stationsflaggor. Visningen byter typ
först när en annan hypotes har lett tydligt i flera sekunder.

Allt här är rena funktioner/klasser utan GTK – testas med analystest.py.
"""
import math

import numpy as np

# Hypoteser = signalfamiljer (samma nycklar som analys.FAM_NAMN) + FT8/FT4 som egna typer
HYP = ("BRUS", "BÄRVÅG", "OOK", "AM", "FM", "SSB", "FSK2", "FSK4", "MFSK", "MSK",
       "BPSK", "QPSK", "PSK8", "LIN", "OFDM", "FT8", "FT4")
DIGITAL = {"FSK2", "FSK4", "MFSK", "MSK", "BPSK", "QPSK", "PSK8", "LIN", "OFDM", "FT8", "FT4"}
TAL = {"SSB", "AM", "FM"}
SLAKT = [{"FSK2", "FSK4", "MFSK", "MSK"}, {"BPSK", "QPSK", "PSK8", "LIN"}, {"FT8", "MFSK"}, {"FT4", "FSK4"}]

# Rimlig upptagen bandbredd (Hz) per hypotes: (nedre, övre). Utanför: straff per oktav.
BREDD = {"BÄRVÅG": (1, 120), "OOK": (15, 500), "AM": (3500, 14000), "FM": (4000, 25000),
         "SSB": (1300, 3600), "FSK2": (60, 1500), "FSK4": (40, 2500), "MFSK": (40, 4000),
         "MSK": (100, 25000), "BPSK": (25, 3500), "QPSK": (25, 3500), "PSK8": (1500, 3500),
         "LIN": (1500, 3600), "OFDM": (1500, 10000), "FT8": (35, 75), "FT4": (60, 110)}

# FT8/FT4: uppringningsfrekvenser (kHz); signalerna ligger 0–3 kHz ovanför (USB)
FT8_KHZ = (1840, 3573, 5357, 7074, 10136, 14074, 18100, 21074, 24915, 28074, 50313, 50323, 144174)
FT4_KHZ = (3575, 7047.5, 10140, 14080, 18104, 21140, 24919, 28180, 50318, 144170)

# Bandplan som mjuk förkunskap: (från kHz, till kHz, {hypotes: poäng}, förklaring)
BANDPLAN = [
    (148.5, 283.5, {"AM": 1.0}, "LV-rundradio"),
    (517, 519, {"FSK2": 2.0}, "NAVTEX 518 kHz"),
    (489, 491, {"FSK2": 2.0}, "NAVTEX 490 kHz"),
    (526.5, 1606.5, {"AM": 1.0}, "MV-rundradio"),
    (4209, 4210, {"FSK2": 2.0}, "NAVTEX 4209,5 kHz"),
    # flygets HF-band (MWARA/VOLMET): SSB-tal, HFDL (PSK)
    (2850, 3155, {"SSB": 0.8}, "flygband"), (3400, 3500, {"SSB": 0.8}, "flygband"),
    (4650, 4750, {"SSB": 0.8}, "flygband"), (5450, 5730, {"SSB": 0.8}, "flygband"),
    (6525, 6765, {"SSB": 0.8}, "flygband"), (8815, 9040, {"SSB": 0.8}, "flygband"),
    (10005, 10100, {"SSB": 0.8}, "flygband"), (11175, 11400, {"SSB": 0.8}, "flygband"),
    (13200, 13360, {"SSB": 0.8}, "flygband"), (17900, 18030, {"SSB": 0.8}, "flygband"),
    (21924, 22000, {"SSB": 0.8}, "flygband"),
    # KV-rundradio: AM (och DRM = OFDM)
    (2300, 2495, {"AM": 0.6}, "rundradio 120 m"), (3200, 3400, {"AM": 0.6}, "rundradio 90 m"),
    (3900, 4000, {"AM": 0.6}, "rundradio 75 m"), (4750, 5060, {"AM": 0.6}, "rundradio 60 m"),
    (5900, 6200, {"AM": 1.0, "OFDM": 0.3}, "rundradio 49 m"), (7200, 7450, {"AM": 1.0, "OFDM": 0.3}, "rundradio 41 m"),
    (9400, 9900, {"AM": 1.0, "OFDM": 0.3}, "rundradio 31 m"), (11600, 12100, {"AM": 1.0, "OFDM": 0.3}, "rundradio 25 m"),
    (13570, 13870, {"AM": 1.0, "OFDM": 0.3}, "rundradio 22 m"), (15100, 15800, {"AM": 1.0, "OFDM": 0.3}, "rundradio 19 m"),
    (17480, 17900, {"AM": 1.0, "OFDM": 0.3}, "rundradio 16 m"), (21450, 21850, {"AM": 1.0, "OFDM": 0.3}, "rundradio 13 m"),
    # amatörband: tal i de övre delarna, CW längst ned
    (1810, 1838, {"OOK": 0.6}, "amatör CW 160 m"), (3500, 3570, {"OOK": 0.6}, "amatör CW 80 m"),
    (3600, 3800, {"SSB": 0.8}, "amatör tal 80 m"), (7000, 7040, {"OOK": 0.6}, "amatör CW 40 m"),
    (7060, 7200, {"SSB": 0.8}, "amatör tal 40 m"), (10100, 10130, {"OOK": 0.6}, "amatör CW 30 m"),
    (14000, 14070, {"OOK": 0.6}, "amatör CW 20 m"), (14101, 14350, {"SSB": 0.8}, "amatör tal 20 m"),
    (18068, 18095, {"OOK": 0.6}, "amatör CW 17 m"), (18111, 18168, {"SSB": 0.8}, "amatör tal 17 m"),
    (21000, 21070, {"OOK": 0.6}, "amatör CW 15 m"), (21151, 21450, {"SSB": 0.8}, "amatör tal 15 m"),
    (28000, 28070, {"OOK": 0.6}, "amatör CW 10 m"), (28320, 29000, {"SSB": 0.8}, "amatör tal 10 m"),
    # VHF/UHF
    (118000, 137000, {"AM": 2.0}, "flygband VHF (AM)"),
    (156000, 162050, {"FM": 1.5}, "marin VHF"),
    (446000, 446200, {"FM": 1.5}, "PMR446"),
]


def ft_tillaten(namn, f_hz):
    """Sant om FT8/FT4 kan finnas på f_hz (delbandet dial … dial+3,2 kHz)."""
    lista = FT8_KHZ if namn == "FT8" else FT4_KHZ
    k = f_hz / 1e3
    return any(d - 0.1 <= k <= d + 3.2 for d in lista)


def bandplan(f_hz):
    """[(hypotes, poäng, förklaring)] för frekvensen."""
    k = f_hz / 1e3
    ft8, ft4 = ft_tillaten("FT8", f_hz), ft_tillaten("FT4", f_hz)
    if ft8 or ft4:                          # FT8/FT4-delbanden ligger inne i amatörbandens taldelar
        return [("FT8", 1.0, "FT8-delband")] if ft8 else [("FT4", 1.0, "FT4-delband")]
    ut = []
    for lo, hi, d, txt in BANDPLAN:
        if lo <= k <= hi:
            ut += [(h, v, txt) for h, v in d.items()]
    return ut


# ════════════════════════════════════════════════════════════════════
# Taldetektor (långt fönster)
# ════════════════════════════════════════════════════════════════════
def tal_matt(z, fs):
    """Hur talliknande signalen är, 0–1, mätt på ≥ 2 s.

    Tal har stavelser: kanalens energi varierar 2–8 gånger per sekund med tydliga dalar, och
    variationen är oregelbunden (inte en fast nycklingstakt). Modem har jämn effekt, och en
    bärvåg/FSK är konstant. Returnerar (mått, detaljer)."""
    z = np.asarray(z)
    fr = max(1, int(fs * 0.01))
    n = len(z) // fr
    if n < 150:
        return None, {}
    e = (np.abs(z[:n * fr]) ** 2).reshape(n, fr).mean(axis=1)
    db = 10 * np.log10(e + 1e-20)
    golv = float(np.percentile(db, 10))
    topp = float(np.percentile(db, 90))
    dyn = topp - golv
    x = db - db.mean()
    X = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
    fm = np.fft.rfftfreq(n, 0.01)
    tot = float(X[(fm >= 0.5) & (fm <= 25)].sum()) + 1e-20
    stav = float(X[(fm >= 2) & (fm <= 8)].sum()) / tot
    # fast nycklingstakt (Morse-element, tidspulser) ger en skarp topp i modulationsspektrumet
    band = X[(fm >= 1) & (fm <= 25)]
    skarp = float(band.max() / (band.mean() + 1e-20)) if band.size else 0.0
    # dynamik: tal 8–25 dB mellan stavelse och paus, modem < 3 dB
    s_dyn = 1.0 / (1.0 + math.exp(-(dyn - 6.0) / 1.5))
    s_stav = 1.0 / (1.0 + math.exp(-(stav - 0.35) / 0.07))
    s_oreg = 1.0 / (1.0 + math.exp((skarp - 12.0) / 3.0))
    m = s_dyn * s_stav * (0.4 + 0.6 * s_oreg)
    return float(m), {"dyn": dyn, "stav": stav, "skarp": skarp}


# ════════════════════════════════════════════════════════════════════
# Bevis från ett analyssteg
# ════════════════════════════════════════════════════════════════════
def _bredd_straff(h, w):
    lo, hi = BREDD.get(h, (1, 1e9))
    if w <= 0:
        return 0.0
    if w < lo:
        return -1.5 * math.log2(lo / w)
    if w > hi:
        return -1.5 * math.log2(w / hi)
    return 0.0


def steg_bevis(feat, f_abs, bredd, tal=None, ft=None):
    """{hypotes: {term: poäng}} för ett analyssteg. bredd = kanalens uppmätta bredd (stabil, över 2 s)."""
    B = {h: {} for h in HYP}

    def ge(hs, term, v):
        for h in hs:
            B[h][term] = B[h].get(term, 0.0) + v

    snr = feat.get("snr") or 0.0
    raw = feat.get("fam_ra") or feat.get("fam")
    # 1. den gamla regelklassningen är ett bra men brusigt vittne
    if raw in B:
        ge([raw], "mätning", 1.2)
        for s in SLAKT:
            if raw in s:
                ge(s - {raw}, "mätning (släkt)", 0.5)
    if snr < 1:
        ge(["BRUS"], "lågt SNR", 1.0)
    elif snr > 8:
        ge(["BRUS"], "tydlig signal", -2.0)
    # 2. bandbredd: den stabila om den finns, annars stegets
    w = bredd or feat.get("occ_bw") or 0.0
    if feat.get("occ_bw") and (feat.get("skurar") or raw in ("OOK", "BÄRVÅG")):
        w = min(w, feat["occ_bw"])          # nyckelklick gör kanalens uppmätta bredd för stor
    for h in HYP:
        if h != "BRUS":
            v = _bredd_straff(h, w)
            if v:
                ge([h], "bandbredd", 0.6 * v)
    # 3. envelop
    cv = feat.get("cv") or 0.0
    if not feat.get("skurar"):
        if cv < 0.15:
            ge(["FM", "FSK2", "FSK4", "MFSK", "BÄRVÅG", "FT8", "FT4"], "konstant envelop", 0.6)
            ge(["SSB", "OOK"], "konstant envelop", -0.8)
        elif cv > 0.35:
            ge(["SSB", "AM", "LIN", "QPSK", "PSK8", "OFDM", "BPSK"], "varierande envelop", 0.3)
            ge(["FM", "BÄRVÅG", "FSK2"], "varierande envelop", -0.6)
    else:
        ge(["OOK"], "på/av", 0.8)
        ge(["AM", "FM", "BÄRVÅG"], "på/av", -0.5)
    # 4. bärvåg
    cf = feat.get("barvag_andel") or 0.0
    if cf >= 0.4 and w >= 3000 and not feat.get("skurar"):
        ge(["AM"], "bärvåg med sidband", 1.5 if (feat.get("spegel") or 0) >= 0.45 else 1.0)
        ge(DIGITAL | {"SSB"}, "stark bärvåg", -0.8)
    if cf < 0.1 and w > 1500:
        ge(["AM"], "ingen bärvåg", -1.0)
    # 5. tal (långt fönster) – det starkaste beviset mot digitalt
    # Bara åt det positiva hållet: musik (AM-rundradio) och FM-tal (konstant envelop) saknar talrytm
    # i envelopen utan att vara digitala.
    if tal is not None and tal > 0.5:
        t = (tal - 0.5) * 2.0          # 0 … 1
        ge(TAL, "talrytm", 1.5 * t)
        ge(DIGITAL | {"BÄRVÅG"}, "talrytm", -1.5 * t)
        if w > 600:
            ge(["OOK"], "talrytm", -1.5 * t)
    # 6. FT8/FT4: bara i delbanden
    for nm in ("FT8", "FT4"):
        if not ft_tillaten(nm, f_abs):
            ge([nm], "utanför FT8/FT4-delband", -20.0)
        elif ft == nm:
            ge([nm], f"{nm}-detektor", 2.5)
    # 7. en avkodare med synk är starkt bevis för sin familj
    if feat.get("fakta_fran") and raw in B:
        ge([raw], f"avkodare {feat['fakta_fran']}", 1.5)
    return B


class Bevismotor:
    TAU = 10.0          # s: minnets tidskonstant
    SKALA = 0.35        # poäng per steg → log-sannolikhet (steg är inte oberoende)
    BYT_TID = 3.0       # s: en ny typ måste leda så här länge innan visningen byter
    BYT_MARG = 0.2

    def __init__(self):
        self.nollstall()

    def nollstall(self):
        self.A = {h: {} for h in HYP}       # ackumulerade termer per hypotes
        self.t = None
        self.visad = None
        self.utmanare, self.utm_t = None, None
        self.n = 0

    def prior(self, f_abs, kontext=()):
        P = {h: {} for h in HYP}
        for h, v, txt in bandplan(f_abs):
            P[h][txt] = P[h].get(txt, 0.0) + v
        for h, v, txt in kontext:
            if h in P:
                P[h][txt] = P[h].get(txt, 0.0) + v
        return P

    def steg(self, feat, f_abs, now, bredd=None, tal=None, ft=None, kontext=()):
        if self.t is not None:
            k = math.exp(-max(0.0, now - self.t) / self.TAU)
            for h in HYP:
                for term in self.A[h]:
                    self.A[h][term] *= k
        self.t = now
        self.n += 1
        for h, termer in steg_bevis(feat, f_abs, bredd, tal, ft).items():
            for term, v in termer.items():
                self.A[h][term] = self.A[h].get(term, 0.0) + self.SKALA * v
        P = self.prior(f_abs, kontext)
        tot = {h: sum(self.A[h].values()) + sum(P[h].values()) for h in HYP}
        m = max(tot.values())
        ex = {h: math.exp(v - m) for h, v in tot.items()}
        s = sum(ex.values())
        p = {h: v / s for h, v in ex.items()}
        bast = max(p, key=p.get)
        # visningen: byt först när en annan typ lett tydligt i BYT_TID s
        if self.visad is None:
            if p[bast] >= 0.6 or (self.n >= 12 and p[bast] >= 0.35):
                self.visad = bast
        elif bast != self.visad and p[self.visad] < 0.2 and p[bast] >= 0.6:
            self.visad, self.utmanare = bast, None        # den visade är uppenbart fel – vänta inte
        elif bast != self.visad and p[bast] - p[self.visad] >= self.BYT_MARG:
            if self.utmanare != bast:
                self.utmanare, self.utm_t = bast, now
            elif now - self.utm_t >= self.BYT_TID:
                self.visad, self.utmanare = bast, None
        else:
            self.utmanare = None
        v = self.visad
        grund = []
        if v is not None:
            termer = dict(self.A[v])
            for term, x in P[v].items():
                termer[term] = termer.get(term, 0.0) + x
            # det som skiljer den visade från de närmaste konkurrenterna
            andra = sorted((h for h in HYP if h != v), key=lambda h: -p[h])[:3]
            diff = {}
            for term, x in termer.items():
                ref = max((self.A[h].get(term, 0.0) + P[h].get(term, 0.0)) for h in andra)
                diff[term] = x - ref
            grund = [term for term, d in sorted(diff.items(), key=lambda kv: -kv[1]) if d > 0.3][:3]
        topp = sorted(p.items(), key=lambda kv: -kv[1])[:4]
        return {"fam": v, "p": p.get(v, 0.0) if v else 0.0, "bast": bast, "topp": topp, "grund": grund}
