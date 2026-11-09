# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Signalanalys för Signalspanaren.

  ChannelTap   körs i DSP-tråden: klipper ut en kanal kring en vald bärvåg,
               decimerar och gör vattenfallsrader för den lilla vyn.
  analyze()    mäter signalens parametrar (SNR, bandbredd, envelop, momentan
               frekvens, symboltakt, PSK-ordning, toner) och klassar modulationen.
  Avkodare     insticksmoduler som demodulerar en viss signaltyp och visar
               värden/klartext (RTTY, Morse – fler läggs till i AVKODARE).
  Analyzer     arbetstråd som kopplar ihop allt; GUI:t läser .result.

Igenkänningen jämför mätvärdena mot signaldb.py.
"""
import logging
import math
import threading
import time
from collections import Counter, deque

import numpy as np
from scipy.signal import firwin, upfirdn, find_peaks
from scipy.ndimage import gaussian_filter1d, uniform_filter1d

import signaldb
import bevis

log = logging.getLogger("signalspanaren")

FAM_NAMN = {
    "BRUS": "Ingen signal / brus",
    "BÄRVÅG": "Omodulerad bärvåg",
    "OOK": "På/av-nyckling (CW/OOK)",
    "AM": "AM",
    "FM": "FM (analog)",
    "SSB": "SSB / AM utan bärvåg",
    "FSK2": "2-FSK",
    "FSK4": "4-FSK",
    "MFSK": "MFSK (flerton, en åt gången)",
    "MSK": "MSK/GMSK",
    "BPSK": "BPSK",
    "QPSK": "QPSK",
    "PSK8": "8PSK (eller π/4-DQPSK)",
    "LIN": "Digital linjär (PSK/QAM)",
    "OFDM": "Parallelltonmodem / OFDM",
    "OKÄND": "Okänd",
}

CTCSS = [67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9,
         114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3,
         173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1,
         233.6, 241.8, 250.3, 254.1]


# ════════════════════════════════════════════════════════════════════
# Kanalutsnitt (DSP-tråden)
# ════════════════════════════════════════════════════════════════════
class _Decim:
    """Tillståndsbärande FIR-decimering (polyfas via upfirdn)."""

    def __init__(self, h, D):
        self.D = D
        self.h = np.asarray(h, np.float32)
        assert (len(self.h) - 1) % D == 0
        self.tail = np.zeros(len(self.h) - 1, np.complex64)
        self.rem = np.zeros(0, np.complex64)

    def __call__(self, x):
        if len(self.rem):
            x = np.concatenate((self.rem, x))
        n = (len(x) // self.D) * self.D
        self.rem = x[n:].copy()
        if n == 0:
            return np.zeros(0, np.complex64)
        xin = np.concatenate((self.tail, x[:n]))
        self.tail = xin[-(len(self.h) - 1):]
        m = (len(self.h) - 1) // self.D
        y = upfirdn(self.h, xin, down=self.D)
        return y[m:m + n // self.D].astype(np.complex64)


def plan_decimation(sr, bw):
    need = 2.5 * max(bw, 200.0)
    for D in (256, 128, 64, 32, 16, 8, 4, 2):
        if sr % D == 0 and sr / D >= need:
            return D
    return 1


class ChannelTap:
    WF_N = 512

    def __init__(self, sr):
        self.sr = sr
        self.lock = threading.Lock()
        self.target = None
        self.bw = None
        self.gen = 0               # ökar vid varje nytt mål
        self.lines = deque(maxlen=128)
        self.status = "inget mål"
        self._key = None
        self.fs = None
        self.total = 0
        self.ring = None

    def set_target(self, f, bw):
        with self.lock:
            self.target = float(f)
            self.bw = float(bw)
            self.gen += 1
            self._key = None
            self.lines.clear()

    def clear(self):
        with self.lock:
            self.target = None
            self.gen += 1
            self._key = None
            self.lines.clear()
            self.status = "inget mål"

    def _rebuild(self, c):
        sr = self.sr
        D = plan_decimation(sr, self.bw)
        self.D = D
        self.fs = sr / D
        st = []
        if D > 8:
            st.append(_Decim(firwin(129, 0.35 * sr / 8, fs=sr), 8))
            D2 = D // 8
            st.append(_Decim(firwin(24 * D2 + 1, 0.4 * self.fs, fs=sr / 8), D2))
        elif D > 1:
            st.append(_Decim(firwin(24 * D + 1, 0.4 * self.fs, fs=sr), D))
        self.stages = st
        self.ph = 0.0
        cap = int(self.fs * 4.0)
        self.ring = np.zeros(cap, np.complex64)
        self.total = 0
        self.win = np.hanning(self.WF_N).astype(np.float32)
        self.win_norm = float((self.win ** 2).sum())
        self._key = (self.target, self.bw)

    def feed(self, x, c):
        """Anropas från DSP-tråden för varje block."""
        with self.lock:
            if self.target is None:
                return
            off = self.target - c
            if abs(off) > self.sr * 0.47:
                self.status = "utanför mottagningsfönstret"
                return
            if self._key != (self.target, self.bw):
                self._rebuild(c)
            self.status = "ok"
            n = len(x)
            w = -2 * np.pi * off / self.sr
            ph = self.ph + w * np.arange(n)
            y = x * np.exp(1j * ph).astype(np.complex64)
            self.ph = float((self.ph + w * n) % (2 * np.pi))
            for s in self.stages:
                y = s(y)
            m = len(y)
            if m == 0:
                return
            N = self.WF_N
            seg = y[-(m // N) * N:].reshape(-1, N) if m >= N else None
            cap = len(self.ring)
            yy = y
            if m > cap:
                self.total += m - cap
                yy = y[-cap:]
            i = self.total % cap
            k = min(len(yy), cap - i)
            self.ring[i:i + k] = yy[:k]
            if k < len(yy):
                self.ring[:len(yy) - k] = yy[k:]
            self.total += len(yy)
            if seg is None:
                last = self._last(N)
                if last is None or len(last) != N:
                    return
                seg = last[None, :]
            p = np.fft.fftshift((np.abs(np.fft.fft(seg * self.win, axis=1)) ** 2).mean(axis=0)) / self.win_norm
            self.lines.append((10 * np.log10(p + 1e-20).astype(np.float32), self.fs, self.target, self.bw))

    def _last(self, n):
        cap = len(self.ring)
        n = min(n, cap, self.total)
        if n <= 0:
            return None
        i = self.total % cap
        if n <= i:
            return self.ring[i - n:i].copy()
        return np.concatenate((self.ring[cap - (n - i):], self.ring[:i]))

    def snapshot(self, seconds):
        with self.lock:
            if self.target is None or self.ring is None or self.status != "ok":
                return None
            n = int(self.fs * seconds)
            z = self._last(n)
            if z is None:
                return None
            return z, self.fs, self.target, self.bw, self.gen, self.total

    def since(self, total_from):
        """Samples som tillkommit sedan räknarvärdet total_from (högst ringens längd)."""
        with self.lock:
            if self.ring is None:
                return None, self.total
            n = self.total - total_from
            if n <= 0:
                return np.zeros(0, np.complex64), self.total
            return self._last(n), self.total


# ════════════════════════════════════════════════════════════════════
# Parametrar och klassning
# ════════════════════════════════════════════════════════════════════
def _knasterspärr(z, k):
    """Nollar korta pulser (åskknaster, tändstörningar) som ligger långt över signalens normala nivå,
    så att mätningen av takt och shift inte förstörs. Bara pulser kortare än ~5 ms: talets toppar i
    SSB/AM är längre och ligger sällan k gånger över medianen."""
    if not k or len(z) < 64:
        return z
    a = np.abs(z)
    med = float(np.median(a)) + 1e-12
    hog = a > k * med
    if not hog.any() or hog.mean() > 0.02:          # många höga sampel: en stark signal, inte knaster
        return z
    # fyll i (interpolera) i stället för att nolla: nollor ser ut som på/av-nyckling för klassningen
    hog = np.convolve(hog, np.ones(5), mode="same") > 0        # ta med pulsens flanker
    i = np.arange(len(z))
    ok = ~hog
    if ok.sum() < 2:
        return z
    y = np.interp(i, i[ok], z.real[ok]) + 1j * np.interp(i, i[ok], z.imag[ok])
    return y.astype(z.dtype)


def _pow2(n):
    return 1 << int(round(math.log2(max(2, n))))


def _welch(z, fs, nfft):
    m = len(z) // nfft
    if m < 1:
        z = np.concatenate((z, np.zeros(nfft - len(z), z.dtype)))
        m = 1
    w = np.hanning(nfft).astype(np.float32)
    seg = z[:m * nfft].reshape(m, nfft) * w
    P = np.fft.fftshift((np.abs(np.fft.fft(seg, axis=1)) ** 2).mean(axis=0)) / float((w ** 2).sum())
    f = (np.arange(nfft) - nfft / 2) * fs / nfft
    return f, P


def _line(sig, fs, fmin, fmax, nfft=None):
    """Starkaste spektrallinje i [fmin, fmax]. Returnerar (frekvens, styrka i dB över omgivningen)."""
    sig = sig - sig.mean()
    n = len(sig)
    nfft = nfft or _pow2(n * 2)
    S = np.abs(np.fft.rfft(sig * np.hanning(n), nfft)) ** 2
    f = np.arange(len(S)) * fs / nfft
    k0, k1 = int(np.searchsorted(f, fmin)), int(np.searchsorted(f, fmax))
    if k1 - k0 < 8:
        return None, 0.0
    reg = S[k0:k1]
    k = int(np.argmax(reg))
    ref = float(np.median(reg)) + 1e-30
    strength = 10 * math.log10(float(reg[k]) / ref)
    kk = k0 + k
    if 0 < kk < len(S) - 1:
        a, b, c = math.log(S[kk - 1] + 1e-30), math.log(S[kk] + 1e-30), math.log(S[kk + 1] + 1e-30)
        d = a - 2 * b + c
        frac = 0.5 * (a - c) / d if d != 0 else 0.0
    else:
        frac = 0.0
    return (kk + float(np.clip(frac, -0.5, 0.5))) * fs / nfft, strength


def _power_line(y, fs, n, radius, nfft):
    """Linje i y**n nära 0 Hz (PSK-ordning / bärvåg). Returnerar (frekvens/n, styrka dB)."""
    Y = np.abs(np.fft.fftshift(np.fft.fft(y ** n, nfft))) ** 2
    f = (np.arange(nfft) - nfft / 2) * fs / nfft
    sel = np.abs(f) <= radius
    if not sel.any():
        return 0.0, 0.0
    idx = np.flatnonzero(sel)
    k = idx[int(np.argmax(Y[idx]))]
    lo, hi = max(0, k - 60), min(nfft, k + 61)
    nb = np.concatenate((Y[lo:max(lo, k - 3)], Y[min(hi, k + 4):hi]))
    ref = float(np.mean(nb)) + 1e-30 if len(nb) else 1e-30
    return float(f[k]) / n, 10 * math.log10(float(Y[k]) / ref)


def _smooth(x, n):
    n = int(max(1, n))
    return uniform_filter1d(x, n, mode="nearest") if n > 1 else x


def fit_bandwidth(z, fs, survey_bw, near_hz=300.0, lutning=False):
    """Mäter hur långt signalen nära mitten sträcker sig ovanför brusgolvet.

    Kanten bestäms mot brusgolvet, inte mot toppen: AM-sidband som ligger 30–40 dB under
    bärvågen räknas med så länge de syns över bruset. Små luckor (t.ex. mellan FSK-toner)
    överbryggas. Returnerar (mitt_hz, bredd_hz, når_kanten) eller None om ingen signal syns.
    når_kanten = signalen fortsätter utanför överblicken (den var för smal)."""
    z = np.asarray(z, np.complex64)
    N = len(z)
    if N < 2048:
        return None
    grov = int(np.clip(_pow2(fs / max(survey_bw / 400.0, 1.0)), 256, 16384))
    while grov > 256 and N // grov < 8:          # minst 8 medelvärden – annars är brusgolvet för brusigt
        grov //= 2
    r = _fit_res(z, fs, survey_bw, near_hz, grov, lutning=lutning)
    fin = grov
    while fin * 2 <= 16384 and N // (fin * 2) >= 8:
        fin *= 2
    # smal signal (t.ex. en nycklad bärvåg) syns inte i grov upplösning: försök igen finare
    if fin > grov and (r is None or r[1] < 12 * fs / grov):
        r2 = _fit_res(z, fs, survey_bw, near_hz, fin, lutning=lutning)
        if r2 is not None and r2[1] >= 2.5 * fs / fin:
            r = r2
    # svag smal signal (CW, nycklad bärvåg) precis under tröskeln: alla upplösningar däremellan, med något
    # lägre krav på toppen nära klicket (få punkter att söka i – liten risk för falsklarm i brus)
    if r is None:
        mel = grov
        while mel <= fin and r is None:
            r2 = _fit_res(z, fs, survey_bw, near_hz, mel, slapp=True, lutning=lutning)
            if r2 is not None and r2[1] >= 2.5 * fs / mel and r2[1] < 600.0:
                r = r2
            mel *= 2
    # svag bred signal (tal, modem, AM-sidband nära bruset): syns bara när spektrumet slätas ut
    rs = _fit_slat(z, fs, survey_bw, near_hz, None if r is None else r[0], lutning=lutning)
    if rs is not None and rs[4] < 4.0 and rs[5] < 6.0:
        # bara när bandet är svagt (under 4 dB över bruset): annars drar det utslätade bandet med sig
        # grannsignaler (och ett klick mellan två FSK-toner ska till _fsk_par), och den vanliga mätningen
        # är bättre
        if r is None:
            r = _fsk_par(z, fs, survey_bw) or rs[:3]      # två FSK-toner kring klicket går före
        else:
            if rs[1] >= 4 * r[1] and abs(rs[0] - r[0]) <= 0.15 * rs[1] and rs[3] >= 0.4 \
                    and _fsk_par(z, fs, survey_bw) is None:      # två FSK-toner är också spegelsymmetriska
                r = rs[:3]      # en smal topp mitt i ett brett, symmetriskt band: AM-bärvåg i brus
            elif r[1] >= 300.0 and rs[1] >= 2 * r[1] and rs[0] - rs[1] / 2 <= r[0] <= rs[0] + rs[1] / 2:
                r = rs[:3]      # en bit av ett brett band (en formant i svagt tal): hela bandet gäller
    # ett starkt, platt och fyllt band (modem, 1,5–4 kHz) där den vanliga mätningen fastnat på en smal bit
    # inuti (en störning eller ett spektralt hack): hela bandet gäller
    if rs is not None and r is not None and rs[4] >= 4.0 and rs[6] >= 0.8 and 1500.0 <= rs[1] <= 4000.0 \
            and 200.0 <= r[1] < 0.4 * rs[1] and rs[0] - rs[1] / 2 <= r[0] <= rs[0] + rs[1] / 2:
        # (under 200 Hz: en egen smal signal – CW, bärvåg – bredvid eller ovanpå ett band, inte en bit av det)
        r = rs[:3]
    # bred FSK (personsökare, ±3–5 kHz): en svag signal mäts som bara den ena tonen, långt från klicket.
    # Finns en spegelton på andra sidan och turas de om, är det ett FSK-par
    if r is not None and survey_bw >= 10000.0 and abs(r[0]) >= 1500.0 and r[1] < 1.5 * abs(r[0]):
        par = _fsk_par(z, fs, survey_bw, max_shift=min(12000.0, survey_bw), vaxling=True)
        if par is not None and abs(par[1] - 150.0 - 2 * abs(r[0])) < 0.3 * 2 * abs(r[0]):
            return par
    # tätt band: mätningen har slagit ihop klicket med grannarna till ett brett band, men vid klicket finns
    # ett äkta FSK-par (tonerna turas om) – då gäller paret
    if r is not None and r[1] >= 400.0:
        par = _fsk_par(z, fs, survey_bw, vaxling=True)
        if par is not None and r[1] >= 1.6 * par[1]:
            r = par
    return r


def _fit_slat(z, fs, survey_bw, near_hz, kring=None, slat_hz=300.0, lutning=False):
    """Bandbreddsmätning på ett utslätat spektrum (medel över slat_hz). Brusets spridning minskar med
    roten ur antalet punkter, så ett brett band som bara ligger 1–2 dB över bruset (svagt SSB-tal,
    STANAG 4285, AM-sidband) syns. Returnerar (mitt, bredd, når_kanten, spegel kring 'kring',
    bandets median i dB över bruset, nivån vid 'kring' i dB över bruset) eller None.
    Smala signaler hittas bättre av _fit_res – det här är en reserv för breda."""
    N = len(z)
    nfft = int(np.clip(_pow2(fs / 25.0), 256, 16384))
    while nfft > 256 and N // nfft < 8:
        nfft //= 2
    f, P = _welch(z, fs, nfft)
    sel = np.abs(f) <= survey_bw / 2
    f, P = f[sel], P[sel]
    res = fs / nfft
    if lutning:
        P = _utan_lutning(f, P)
    K = max(3, int(round(slat_hz / res)))
    if len(f) < 4 * K:
        return None
    Ps = uniform_filter1d(P, K, mode="nearest")
    Pd = 10 * np.log10(Ps + 1e-30)
    m = max(1, N // nfft)
    sigma = 5.57 / math.sqrt(m * K) * 1.5          # 1,5: Hann-fönstrets korrelerade grannpunkter
    floor = float(np.percentile(Pd, 10)) + 1.28 * sigma
    thr = floor + max(0.6, 3.0 * sigma)
    c = len(f) // 2
    r = max(1, int(max(near_hz, 800.0) / res))    # klicket ligger någonstans i talbandet, inte i mitten
    k0 = max(0, c - r) + int(np.argmax(Pd[max(0, c - r):c + r + 1]))
    if Pd[k0] < thr:
        return None
    # hysteres: starta vid tröskeln, väx så länge bandet ligger tydligt över bruset (ett svagt modem
    # doppar tillfälligt under tröskeln när kanalen fädar)
    above = Pd > floor + max(0.3, 1.5 * sigma)
    gap = max(2, int(round(200.0 / res)))

    def edge(step):
        k, last, miss = k0, k0, 0
        while 0 < k < len(f) - 1:
            k += step
            if above[k]:
                last, miss = k, 0
            else:
                miss += 1
                if miss > gap:
                    break
        return last

    a, b = edge(-1), edge(1)
    bredd = float(f[b] - f[a]) + res
    if bredd < 600.0:
        return None                                 # smalt: inte den här reservens sak
    kant = a <= gap or b >= len(f) - 1 - gap or (b - a + 1) > 0.75 * len(f)
    lo, hi = float(f[a]) - res / 2, float(f[b]) + res / 2
    spegel = 0.0
    if kring is not None:
        kc = int(np.argmin(np.abs(f - kring)))
        spegel = _spegel(Pd, res, kc, lo_hz=150.0, hi_hz=min(4000.0, bredd / 2))
    golv = floor - 1.28 * sigma
    over = float(np.median(Pd[a:b + 1])) - golv
    # styrkan där den vanliga mätningen hittade något (en stark signal ska inte bytas mot ett brett band)
    over_kring = over if kring is None else float(Pd[int(np.argmin(np.abs(f - kring)))]) - golv
    # hur fyllt bandet är: ett modem har en platt topp (≈ 0,9), en samling separata signaler inte
    fyllt = float(np.mean(Pd[a:b + 1] > golv + 0.5 * over)) if over > 0 else 0.0
    return (lo + hi) / 2, hi - lo, kant, spegel, over, over_kring, fyllt


def _fsk_par(z, fs, survey_bw, max_shift=1200.0, vaxling=False):
    """Två toner på var sin sida om mitten (klick mellan FSK-tonerna). Returnerar (mitt, bredd, False)
    eller None. Tonerna ska vara högst 15 dB olika starka och 100–max_shift Hz isär."""
    nfft = int(np.clip(_pow2(fs / 10.0), 256, 16384))
    while nfft > 256 and len(z) // nfft < 8:
        nfft //= 2
    f, P = _welch(np.asarray(z, np.complex64), fs, nfft)
    sel = np.abs(f) <= min(survey_bw / 2, max_shift)
    # lätt utslätning (≈ 30 Hz): en svag FSK-ton (50 Bd) är bredare än en punkt, och bruset sprider mindre
    Ps = uniform_filter1d(P, 3, mode="nearest")
    f, Pd = f[sel], 10 * np.log10(Ps[sel] + 1e-30)
    if len(f) < 16:
        return None
    floor = float(np.percentile(10 * np.log10(Ps + 1e-30), 20))
    # närmaste paret först: i ett tätt band är de starkaste topparna längre bort ofta grannar
    # (en CW-station på ena sidan, en annan RTTY på den andra), inte de egna tonerna
    for halv in (150.0, 300.0, 600.0, max_shift):
        vl, hl = (f < -20) & (f >= -halv), (f > 20) & (f <= halv)
        if not vl.any() or not hl.any():
            continue
        kl = np.flatnonzero(vl)[np.argmax(Pd[vl])]
        kh = np.flatnonzero(hl)[np.argmax(Pd[hl])]
        a, b = float(Pd[kl]), float(Pd[kh])
        sep = float(f[kh] - f[kl])
        if min(a, b) < floor + 6 or abs(a - b) > 15 or not (100.0 <= sep <= max_shift):
            continue
        if (vaxling or min(a, b) < floor + 10) and not _vaxlar(z, fs, nfft, float(f[kl]), float(f[kh])):
            continue        # svaga toner: bara ett FSK-par om de turas om (två CW-stationer gör det inte)
        # mellan tonerna ska det vara en svacka (annars är det en bred signal, inte två toner): minst 10 dB,
        # eller för svaga toner minst 60 % av vägen ned till bruset
        mellan = Pd[kl + 1:kh]
        djup = min(10.0, 0.6 * (min(a, b) - floor))
        if len(mellan) and float(np.median(mellan)) > min(a, b) - djup:
            continue
        mid = float(f[kl] + f[kh]) / 2
        return mid, sep + 150.0, False
    return None


def _vaxlar(z, fs, nfft, f1, f2):
    """Turas tonerna f1 och f2 om? FSK sänder den ena eller den andra – deras nivåer över tid är negativt
    korrelerade. Två oberoende signaler (t.ex. CW-stationer bredvid varandra) är det inte."""
    z = np.asarray(z, np.complex64)
    n = max(64, nfft // 4)                          # korta rutor: följer symbolerna (RTTY 20 ms)
    n = min(n, int(fs * 0.02)) if fs * 0.02 >= 64 else n
    m = len(z) // n
    if m < 20:
        return False
    w = np.hanning(n).astype(np.float32)
    X = np.fft.fft(z[:m * n].reshape(m, n) * w, axis=1)
    fr = np.fft.fftfreq(n, 1 / fs)
    k1, k2 = int(np.argmin(np.abs(fr - f1))), int(np.argmin(np.abs(fr - f2)))
    if k1 == k2:
        return False
    p1 = 10 * np.log10(np.abs(X[:, k1]) ** 2 + 1e-20)
    p2 = 10 * np.log10(np.abs(X[:, k2]) ** 2 + 1e-20)
    if p1.std() < 1e-6 or p2.std() < 1e-6:
        return False
    return float(np.corrcoef(p1, p2)[0, 1]) < -0.2


def _barvag_exakt(z, fs, f0, sok_hz=200.0):
    """Bärvågens frekvens exakt (≈ 1/längden Hz): starkaste toppen inom ±sok_hz från f0."""
    z = np.asarray(z, np.complex64)
    N = int(min(1 << 18, _pow2(len(z))))
    X = np.abs(np.fft.fft(z[:N] * np.hanning(min(N, len(z))).astype(np.float32), N))
    f = np.fft.fftfreq(N, 1 / fs)
    sel = np.abs(f - f0) <= sok_hz
    if not sel.any():
        return f0
    return float(f[sel][np.argmax(X[sel])])


def _nycklad(z, fs, fc, ruta_s=0.01):
    """Slås bärvågen vid fc av och på? Bärvågens effekt i rutor (smalt filter kring fc): nycklad om de
    starka rutorna ligger minst 10 dB över de svaga. En AM-bärvåg ligger kvar hela tiden.
    OBS: fc måste vara exakt (några Hz) – annars roterar fasen inom rutan och medelvärdet slår av och på."""
    z = np.asarray(z, np.complex64)
    n = len(z)
    if n < fs * 0.5:
        return False
    t = np.arange(n) / fs
    y = z * np.exp(-2j * np.pi * fc * t).astype(np.complex64)
    k = max(4, int(fs * ruta_s))
    m = n // k
    p = np.abs(y[:m * k].reshape(m, k).mean(axis=1)) ** 2      # medel över rutan ≈ smalt filter (~100 Hz)
    db = 10 * np.log10(p + 1e-30)
    if ruta_s < 0.03:
        return float(np.percentile(db, 90) - np.percentile(db, 10)) >= 10.0
    # Med brusreferens (samma filter 150 Hz bredvid bärvågen): 2 = nycklad (av-läget är brus, de svaga
    # rutorna når bruset), 1 = delvis (bärvågen ligger mest på men faller ibland ned till bruset – RWM
    # mellan pulsserierna), 0 = inte (en fädande AM-bärvåg går ned 10 dB men sällan ända ned till bruset)
    yr = z * np.exp(-2j * np.pi * (fc + 150.0) * t).astype(np.complex64)
    pr = np.abs(yr[:m * k].reshape(m, k).mean(axis=1)) ** 2
    brus = 10 * np.log10(float(np.median(pr)) + 1e-30)
    nere = int(np.count_nonzero(db - brus <= 4.0))
    if float(np.percentile(db, 90) - np.percentile(db, 10)) >= 10.0 and float(np.percentile(db, 10)) - brus <= 4.0:
        return 2
    if nere >= 2 and float(np.percentile(db, 90)) - brus >= 10.0:
        return 1
    return 0


def _utan_lutning(f, P):
    """Tar bort en lutning i brusgolvet (VLF: ~10 dB över fönstret). Annars ligger hela den höga sidan
    över ett golv som mätts i den låga, och grannsignaler slås ihop. En rak linje anpassas till de
    lägsta punkterna (bruset) i fönstrets inre 80 % – breda signaler (AM) påverkas inte, platta spektrum
    lämnas orörda (under 3 dB lutning)."""
    n = len(f)
    if n < 32:
        return P
    Pd = 10 * np.log10(P + 1e-30)
    inre = slice(n // 10, n - n // 10)
    fi, pi = f[inre], Pd[inre]
    lag = pi <= np.percentile(pi, 30)
    if lag.sum() < 8:
        return P
    k, m = np.polyfit(fi[lag], pi[lag], 1)
    if abs(k) * (fi[-1] - fi[0]) < 3.0:
        return P
    trend = k * (f - f[n // 2])
    return P / 10 ** (trend / 10)


def _fit_res(z, fs, survey_bw, near_hz, nfft, slapp=False, lutning=False):
    N = len(z)
    f, P = _welch(z, fs, nfft)
    sel = np.abs(f) <= survey_bw / 2
    f, P = f[sel], P[sel]
    if len(f) < 16:
        return None
    res = fs / nfft
    if lutning:
        P = _utan_lutning(f, P)
    Pd = 10 * np.log10(P + 1e-30)
    # Brusgolvet ur de lägsta 10 %: håller så länge minst en tiondel av överblicken är brus.
    # Spridningen i dB för ett medelvärde av m periodogram är ungefär 5,6/√m (teori, inte uppmätt –
    # den uppmätta spridningen blir för stor när signalen täcker större delen av överblicken).
    m = max(1, N // nfft)
    sigma = 5.57 / math.sqrt(m)
    p10, p20 = np.percentile(Pd, [10, 20])
    # Om fönstret når ut i mottagarfiltrets stoppband (< -25 dB) så att 10:e percentilen faller ned i stupet, utgå från 20:e.
    floor = float(p20 + 0.84 * sigma if float(Pd.min()) < p20 - 25.0 and p20 - p10 > 2.5 else p10 + 1.28 * sigma)
    thr = floor + max(3.0, 4.0 * sigma)
    c = len(f) // 2
    r = max(1, int(near_hz / res))
    k0 = max(0, c - r) + int(np.argmax(Pd[max(0, c - r):c + r + 1]))
    if Pd[k0] < (floor + max(3.0, 3.5 * sigma) + 0.3 if slapp else thr + 1.5):
        return None
    if slapp:
        thr = floor + max(2.0, 2.5 * sigma)    # kanterna för en svag topp mäts mot en lägre nivå
    above = Pd > thr
    gap = max(2, int(round(max(250.0, survey_bw * 0.02) / res)))   # t.ex. hålet kring en AM-bärvåg
    # Tydlig smal bärvåg vid k0 (AM): talets hål i sidbanden kan vara flera hundra Hz, så luckorna får
    # växa med bredden. Annars fast lucka – ett brusgap mellan två signaler ska inte överbryggas.
    nb = max(1, int(round(2000.0 / res)))
    omg = P[max(0, k0 - nb):k0 + nb + 1]
    topp_bredd = np.count_nonzero(Pd[max(0, k0 - nb):k0 + nb + 1] > Pd[k0] - 6) * res
    # bärvågens andel av effekten inom ±2 kHz (oberoende av upplösningen): AM ≈ 0,8–0,95
    andel = float(P[max(0, k0 - 2):k0 + 3].sum()) / (float(omg.sum()) + 1e-30)
    barvag = topp_bredd < 150 and andel >= 0.3 and _spegel(Pd, res, k0) >= 0.45

    def edge(step):
        k, last, miss = k0, k0, 0
        while 0 < k < len(f) - 1:
            k += step
            if above[k]:
                last, miss = k, 0
            else:
                miss += 1
                if barvag:
                    tillat = max(gap, int(round(900.0 / res)), int(0.35 * abs(last - k0)))
                else:
                    # utan bärvåg: luckan växer med signalen men högst 250 Hz – smala signaler tätt
                    # intill varandra (FT8, CW) ska inte slås ihop
                    tillat = min(gap, max(2, int(round(20.0 / res)), int(0.15 * abs(last - k0))))
                if miss > tillat:
                    break
        return last

    a, b = edge(-1), edge(1)
    if not slapp and Pd[k0] < thr + 3 and b - a < 16:
        return None
    # för smal överblick: signalen når kanten eller fyller nästan hela fönstret
    kant = a <= gap or b >= len(f) - 1 - gap or (b - a + 1) > 0.75 * len(f)
    dl, dr = float(f[k0] - f[a]), float(f[b] - f[k0])
    if barvag and min(dl, dr) < min(400.0, 0.3 * max(dl, dr, 1e-9)):
        barvag = False      # tonen ligger i kanten av signalen (FSK-ton), inte mitt i (AM-bärvåg)
    if barvag:
        # en AM-bärvåg är ensam: FSK har två (eller fler) nästan lika starka toner
        w = max(2, int(round(topp_bredd / res)))
        rest = np.r_[Pd[a:max(a, k0 - w)], Pd[k0 + w + 1:b + 1]]
        if len(rest) and float(rest.max()) > Pd[k0] - 8:
            barvag = False
    if barvag and b - a > 8 and float(np.mean(above[a:b + 1])) < 0.3:
        barvag = False      # glesa brustoppar kring en ensam bärvåg (RWM) är inga AM-sidband
    nyck = barvag and _nycklad(z, fs, float(f[k0]))
    if nyck:
        # nycklad bärvåg (RWM:s sekundpulser, CW): "sidbanden" är nyckelklick som bär mer än 1 % av
        # effekten – bredden är bärvågens, mätt där toppen fallit 20 dB (som för CW)
        # fin upplösning (~3 Hz): bärvågen samlas i en punkt och sticker upp över de utspridda klicken
        nf = int(np.clip(_pow2(fs / 3.0), nfft, 65536))
        while nf > nfft and N // nf < 4:
            nf //= 2
        ff, PP = _welch(z, fs, nf)
        Pf = 10 * np.log10(PP + 1e-30)
        rf = fs / nf
        kc = int(np.argmin(np.abs(ff - f[k0])))
        w = max(1, int(round(2 * res / rf)))
        kc = max(0, kc - w) + int(np.argmax(Pf[max(0, kc - w):kc + w + 1]))
        a2 = b2 = kc
        while a2 > 0 and Pf[a2 - 1] > Pf[kc] - 20:
            a2 -= 1
        while b2 < len(ff) - 1 and Pf[b2 + 1] > Pf[kc] - 20:
            b2 += 1
        lo2, hi2 = float(ff[a2]) - rf / 2, float(ff[b2]) + rf / 2
        return (lo2 + hi2) / 2, hi2 - lo2, False
    if barvag:
        # AM: bärvågen är mitten och sidbanden är lika breda – den bredare sidan räknas (den smalare
        # har ofta ett hål i talet), men inte bredare än vad som ryms i överblicken
        half = max(float(f[k0] - f[a]), float(f[b] - f[k0])) + res / 2
        return float(f[k0]), 2 * half, kant
    a, b = _narrow_peak(Pd, res, a, b, k0)
    a, b = _split_valleys(Pd, res, a, b, k0, thr)
    if lutning and (b - a) * res > 600.0:
        # VLF/LF: smala stationer (MSK ~230 Hz) tätt intill varandra – dela även vid svackor med bara
        # 300 Hz signal på var sida (på kortvåg skulle det klyva FSK-par)
        a, b = _split_valleys(Pd, res, a, b, k0, thr, sida_hz=300.0, am_skydd=False)
    # 99 % av effekten (över bruset) inom signalen: en stark signals svaga kjolar (fasbrus, AGC) ligger
    # långt över brusgolvet men bär nästan ingen effekt – de ska inte räknas in i bredden
    ex = np.maximum(P[a:b + 1] - 10 ** (floor / 10), 0.0)
    # inte när en bärvåg bär effekten (AM: 70–95 %) – en av två FSK-toner bär ~45 %, och då ska kjolarna
    # bort. På VLF/LF är riktig AM redan tagen ovan, och en smal MSK-huvudlob ser ut som en bärvåg.
    if ex.sum() > 0 and b - a >= 4 and (andel < 0.6 or lutning):
        cs = np.cumsum(ex) / ex.sum()
        a, b = a + int(np.searchsorted(cs, 0.005)), a + int(np.searchsorted(cs, 0.995))
    lo, hi = float(f[a]) - res / 2, float(f[b]) + res / 2
    return (lo + hi) / 2, hi - lo, kant


def _narrow_peak(Pd, res, a, b, k0, ner=20.0, smal_hz=150.0):
    """Smal signal tätt bland andra (FT8/FT4 bredvid varandra, CW): toppen inom 10 dB är smal – då slutar
    signalen där nivån fallit `ner` dB under toppen. Inte om en annan topp är nästan lika stark (FSK-toner)."""
    l = k0
    while l > a and Pd[l - 1] > Pd[k0] - 10:
        l -= 1
    r = k0
    while r < b and Pd[r + 1] > Pd[k0] - 10:
        r += 1
    if (r - l + 1) * res >= smal_hz:
        return a, b
    if _spegel(Pd, res, k0) >= 0.3:
        return a, b                    # spegelsymmetriskt kring toppen: AM-bärvåg med sidband
    w = max(2, int(round(600.0 / res)))
    near = np.r_[Pd[max(a, l - w):l], Pd[r + 1:min(b, r + w) + 1]]
    if len(near) and float(near.max()) > Pd[k0] - 15:
        return a, b                    # en till nästan lika stark ton (FSK, även fädad): låt den vara
    i = l
    while i > a and Pd[i - 1] > Pd[k0] - ner:
        i -= 1
    j = r
    while j < b and Pd[j + 1] > Pd[k0] - ner:
        j += 1
    return i, j


def _spegel(Pd, res, k, lo_hz=150.0, hi_hz=3000.0):
    """Hur lika spektrumet är på båda sidor om bin k (korrelation −1…1). AM:s sidband är spegelbilder
    av varandra (uppmätt +0,6…0,76 på riktiga AM-sändningar), FT8/CW/FSK nära 0."""
    d = np.arange(max(1, int(lo_hz / res)), int(hi_hz / res))
    d = d[(k - d >= 0) & (k + d < len(Pd))]
    if len(d) < 6:
        return 0.0
    a_, b_ = Pd[k - d], Pd[k + d]
    if a_.std() < 1e-6 or b_.std() < 1e-6:
        return 0.0
    return float(np.corrcoef(a_, b_)[0, 1])


def _split_valleys(Pd, res, a, b, k0, thr, djup=10.0, sida_hz=600.0, am_skydd=True):
    """Skiljer signaler som ligger tätt intill varandra (t.ex. modem med 3 kHz mellanrum):
    delar vid svackor som är minst `djup` dB under nivån på båda sidor, när det finns minst
    `sida_hz` bred signal på var sida. Behåller delen med k0. Görs inte om det finns en tydlig
    smal bärvåg (AM): talets hål i sidbanden ska inte klyva sändningen."""
    if b - a < 8:
        return a, b
    seg = Pd[a:b + 1]
    sm = _smooth(seg, max(3, int(round(80.0 / res))))
    plat = float(np.percentile(sm, 75))
    topp = int(np.argmax(seg))
    smal = np.count_nonzero(seg > seg[topp] - 6) * res < 150
    if am_skydd and smal and seg[topp] > plat + 10:
        return a, b                                  # bärvåg + sidband: en signal
    W = max(2, int(round(sida_hz / res)))
    snitt = []
    for i in range(1, len(sm) - 1):
        if not (sm[i] <= sm[i - 1] and sm[i] <= sm[i + 1]):
            continue
        v, h = sm[max(0, i - W):i], sm[i + 1:i + 1 + W]
        if len(v) < W // 2 or len(h) < W // 2:
            continue
        nv = min(float(np.percentile(v, 75)), float(np.percentile(h, 75)))
        if nv - sm[i] >= djup and np.count_nonzero(v > sm[i] + 6) * res >= sida_hz * 0.6 \
                and np.count_nonzero(h > sm[i] + 6) * res >= sida_hz * 0.6:
            if not snitt or i - snitt[-1] > W // 2:
                snitt.append(i)
    if not snitt:
        return a, b
    kk = k0 - a
    lo_i = max([i for i in snitt if i < kk], default=-1) + 1
    hi_i = min([i for i in snitt if i > kk], default=len(seg)) - 1
    # krymp till det som ligger över tröskeln inom delen
    inne = np.flatnonzero(seg[lo_i:hi_i + 1] > thr)
    if len(inne):
        lo_i, hi_i = lo_i + int(inne[0]), lo_i + int(inne[-1])
    return a + lo_i, a + hi_i


def analyze(z, fs, bw):
    """Mät och klassa signalen i z (komplext basband, mitt = analysens målfrekvens)."""
    t0 = time.perf_counter()
    z = np.asarray(z, np.complex64)
    N = len(z)
    out = {"fs": fs, "bw": bw, "n": N, "fam": "BRUS", "klass": FAM_NAMN["BRUS"]}
    if N < 256:
        return out

    # ── 1. Spektrum, brusgolv, upptagen bandbredd ──
    res_want = max(fs / 16384, min(bw / 500, fs / 256))
    nfft = int(np.clip(_pow2(fs / res_want), 256, 16384))
    while nfft > 256 and N // nfft < 6:
        nfft //= 2
    f, P = _welch(z, fs, nfft)
    res = fs / nfft
    floor = float(np.median(P)) + 1e-30
    inb = np.abs(f) <= bw / 2
    Pin, fin = P[inb], f[inb]
    peak_snr = 10 * math.log10(float(Pin.max()) / floor)
    out.update(res=res, floor_db=10 * math.log10(floor), peak_snr=peak_snr,
               spec_f=fin, spec_db=10 * np.log10(Pin + 1e-30))
    strong = Pin > floor * 2.5
    ex = np.where(strong, Pin - floor, 0.0)
    if peak_snr < 6 or ex.sum() <= 0:
        out["snr"] = peak_snr
        if bw >= 1500 and peak_snr >= 3.5:
            Z = np.fft.fft(z)
            fz = np.fft.fftfreq(N, 1 / fs)
            Z[np.abs(fz) > bw / 2] = 0
            rm = _ramperiod(np.fft.ifft(Z).astype(np.complex64), fs, lo_ms=20.0, min_r=0.14)
            if rm:
                out.update(fam="LIN", klass=FAM_NAMN["LIN"], ram_ms=rm[0], ram_styrka=rm[1],
                           occ_bw=bw * 0.8, f_mid=0.0, f_lo=-bw * 0.4, f_hi=bw * 0.4)
        return out
    cs = np.cumsum(ex) / ex.sum()
    lo = float(fin[min(len(fin) - 1, int(np.searchsorted(cs, 0.005)))])
    hi = float(fin[min(len(fin) - 1, int(np.searchsorted(cs, 0.995)))])
    occ = hi - lo + res
    mid = (lo + hi) / 2
    centroid = float((fin * ex).sum() / ex.sum())
    out.update(occ_bw=occ, f_mid=mid, f_centroid=centroid, f_lo=lo, f_hi=hi)

    # ── 2. Isolera signalen och flytta den till 0 Hz ──
    Z = np.fft.fft(z)
    fz = np.fft.fftfreq(N, 1 / fs)
    half = occ / 2 * 1.1 + 2 * res
    Z[np.abs(fz - mid) > half] = 0
    tt = np.arange(N) / fs
    zf = (np.fft.ifft(Z) * np.exp(-2j * np.pi * mid * tt)).astype(np.complex64)
    nb = max(1, int(np.count_nonzero(np.abs(fin - mid) <= half)))
    sig_p = float(ex[np.abs(fin - mid) <= half].sum())
    snr_lin = max(sig_p / (floor * nb), 1e-3)
    out["snr"] = 10 * math.log10(snr_lin)

    # ── 3. Envelop: på/av och variation ──
    a = np.abs(zf)
    sm = _smooth(a, max(3, fs * 0.003))
    p95 = float(np.percentile(sm, 95)) + 1e-12
    low = sm < 0.35 * p95
    high = sm > 0.6 * p95
    fl, fh = float(low.mean()), float(high.mean())
    onoff = fl > 0.12 and fh > 0.12 and (fl + fh > 0.85 or (occ < 160 and fl > 0.25 and fl + fh > 0.70))
    on = high if onoff else np.ones(N, bool)
    out["skurar"] = onoff
    out["duty"] = fh
    a_on = a[on]
    cv = float(a_on.std() / (a_on.mean() + 1e-12))
    cv_c = math.sqrt(max(cv * cv - 1 / (2 * snr_lin), 0.0))
    out["cv"] = cv_c
    out["am_djup"] = float((np.percentile(sm, 97) - np.percentile(sm, 3)) /
                           (np.percentile(sm, 97) + np.percentile(sm, 3) + 1e-12))

    # ── 4. Momentan frekvens ──
    d = np.angle(zf[1:] * np.conj(zf[:-1])) * fs / (2 * np.pi)
    v = on[1:] & on[:-1] & (a[1:] > 0.3 * np.median(a_on))
    fi = _smooth(d, fs / max(occ, 50) * 0.8)
    fi_v = fi[v] if v.sum() > 50 else fi
    span = occ / 2 + 4 * res
    hist, edges = np.histogram(np.clip(fi_v, -span, span), bins=120, range=(-span, span))
    hs = gaussian_filter1d(hist.astype(float), 1.5)
    centers = (edges[:-1] + edges[1:]) / 2
    pk, _ = find_peaks(hs, height=0.18 * hs.max(), prominence=0.12 * hs.max(), distance=4)
    tones = centers[pk]
    out["hist"] = (centers, hs)
    out["fi_peaks"] = tones.tolist()
    nlev = len(tones)
    if nlev >= 2:
        sp = np.diff(np.sort(tones))
        shift = float(np.median(sp))
        # toner som ligger alldeles för nära varandra är brus i histogrammet
        if shift < max(3 * res, occ * 0.06):
            nlev = 1
    else:
        shift = None
    trans = 0.0
    if nlev == 2:
        t0_, t1_ = np.sort(tones)[:2]
        trans = float(np.mean(np.abs(fi_v - (t0_ + t1_) / 2) < 0.25 * (t1_ - t0_)))
    out["overgang"] = trans
    dev = float(np.percentile(np.abs(fi_v - np.median(fi_v)), 99))
    out["deviation"] = dev

    # ── 5. Symboltakt (cykliska linjer) ──
    fmax = min(1.3 * occ, fs / 2.2)
    cands = []
    if fmax > 20:
        env2 = (a * a)[on] if onoff else a * a
        cands.append(("envelop", _line(env2.astype(np.float64), fs, 8, fmax)))
        tr = np.abs(np.diff(fi))[v[1:]] if v.sum() > 100 else np.abs(np.diff(fi))
        cands.append(("frekvens", _line(tr.astype(np.float64), fs, 8, fmax)))
        ph = np.abs(d - np.median(d))
        cands.append(("fas", _line(ph[v].astype(np.float64) if v.sum() > 100 else ph, fs, 8, fmax)))
    baud, bstr, bsrc = None, 0.0, ""
    for name, (fb, s) in cands:
        if fb and s > bstr:
            baud, bstr, bsrc = fb, s, name
    if baud and bstr >= 11:
        src = {"envelop": (a * a), "frekvens": np.abs(np.diff(fi)), "fas": np.abs(d - np.median(d))}[bsrc]
        fb2, s2 = _line(src.astype(np.float64), fs, baud / 2 * 0.9, baud / 2 * 1.1)
        if fb2 and s2 > bstr - 4 and s2 >= 11:
            baud, bstr = fb2, s2
        out["baud"] = float(baud)
        out["baud_styrka"] = bstr
    else:
        out["baud"] = None
        out["baud_styrka"] = bstr

    # ── 5b. Ramperiod för modem med en bärvåg ──
    # Tydlig symboltaktslinje i envelopen + varierande envelop = linjärt modem med en bärvåg (PSK/QAM med
    # pulsformning). Modemens ramar (synk/testsymboler som upprepas) syns som toppar i autokorrelationen,
    # t.ex. STANAG 4285: 256 symboler = 106,7 ms vid 2400 Bd. Görs bara här: toner, FSK och bärvågor ger
    # annars skenbara perioder.
    out["enkelbarvag"] = False
    zo = zf[on] if onoff else zf
    zo = zo / (np.sqrt(np.mean(np.abs(zo) ** 2)) + 1e-12)
    nf = _pow2(len(zo) * 2)
    lines = {}
    for n in (1, 2, 4, 8):
        lines[n] = _power_line(zo, fs, n, n * (3 * res + 15), nf)
    out["linjer"] = {n: round(s, 1) for n, (_, s) in lines.items()}
    car = lines[1][1]
    # Bärvågens andel av effekten: AM har normalt > 50 % i bärvågen, en OFDM-pilot några procent.
    # Söks i mittersta delen av signalen (inte bara exakt i kanalens mitt – osymmetriska sidband och
    # grannsignaler flyttar mitten).
    car_frac, car_f = 0.0, None
    if sel_ok := (Pin.size > 8):
        nara = np.abs(fin - mid) <= max(0.2 * occ, 4 * res)
        if nara.any():
            kc = int(np.flatnonzero(nara)[np.argmax(Pin[nara])])
            car_p = float(Pin[max(0, kc - 2):kc + 3].sum())
            tot = float(Pin[(fin >= lo - res) & (fin <= hi + res)].sum()) + 1e-30
            car_frac, car_f = car_p / tot, float(fin[kc])
            out["spegel"] = _spegel(10 * np.log10(Pin + 1e-30), res, kc)
    out["barvag_andel"] = car_frac
    # Symboltakten för pulsformad PSK/QAM ligger nära bandbredden (bw ≈ baud·(1+β), dvs baud ≈ 0,7–0,95·bw),
    # så den söks just där. Linjer kring 0,5·bw kommer från ljud/toner (tal, FSK, flertonsmodem) – uppmätt
    # med analystest på riktiga och syntetiska klipp.
    single, ob = False, None
    # Gränserna (styrka ≥ 19 dB, 0,75–1,05·bw, bw ≥ 600 Hz) skiljer riktiga modem (22–28 dB, 0,86–0,95) från
    # tal, RTTY och flertonsmodem (14–18 dB, 0,68–1,0) i analystest-klippen.
    # ojusterad cv: brusjusteringen (cv_c) blir för stor när SNR underskattas av grannsignaler i kanalen;
    # kravet på en stark symboltaktslinje skyddar mot rent brus
    if car < 14 and car_frac < 0.25 and cv > 0.15 and occ >= 600:
        env2 = (a * a)[on] if onoff else a * a
        fb, sb = _line(env2.astype(np.float64), fs, 0.75 * occ, min(1.05 * occ, fs / 2.2))
        out["enkel_linje"] = (fb, sb)          # diagnostik (analystest)
        if fb and sb >= 19:
            single, ob = True, fb
            out["baud"], out["baud_styrka"] = float(fb), sb
        elif occ >= 1200:
            rm = _ramperiod(zf, fs, lo_ms=20.0, min_r=0.14)
            if rm:
                single = True
                out["ram_ms"], out["ram_styrka"] = rm
                if fb and sb >= 12:
                    ob = fb
                    out["baud"], out["baud_styrka"] = float(fb), sb
    out["enkelbarvag"] = single
    if single and "ram_ms" not in out:
        rm = _ramperiod(zf, fs)
        if rm:
            out["ram_ms"], out["ram_styrka"] = rm
            if ob:
                out["ram_sym"] = rm[0] * ob / 1000.0
    elif single and ob and "ram_ms" in out:
        out["ram_sym"] = out["ram_ms"] * ob / 1000.0

    # ── 7. Toner i spektrum (parallellmodem/MFSK) ──
    sel = (fin >= lo - res) & (fin <= hi + res)
    sdb = 10 * np.log10(Pin[sel] + 1e-30)
    tp, _ = find_peaks(sdb, prominence=4, distance=max(2, int(20 / res)))
    tf = fin[sel][tp]
    ntones = len(tf)
    t_sp = float(np.median(np.diff(tf))) if ntones >= 3 else None
    regular = ntones >= 3 and np.std(np.diff(tf)) / (t_sp + 1e-9) < 0.3
    acf_str = 0.0
    if sel.sum() > 24:
        # periodicitet i spektrumet (autokorrelation) – hittar tonrastret även när topparna är svaga
        x = sdb - _smooth(sdb, max(3, int(len(sdb) / 6)))
        x = x - x.mean()
        ac = np.correlate(x, x, "full")[len(x) - 1:]
        ac = ac / (ac[0] + 1e-12)
        l0, l1 = max(2, int(25 / res)), min(len(ac) - 1, int(len(sdb) / 4))
        if l1 > l0 + 2:
            L = l0 + int(np.argmax(ac[l0:l1]))
            acf_str = float(ac[L])
            if acf_str > 0.35 and (not regular or ntones < 6):
                t_sp = L * res
                ntones = int(round(occ / t_sp))
                regular = True
    out["tonraster"] = acf_str
    # två smala toner som bär nästan all effekt (FSK även när momentanfrekvensen är för brusig)
    tva_toner = None
    if sel.sum() > 8:
        Ps_ = Pin[sel] - floor
        tp2, pr2 = find_peaks(sdb, prominence=8, distance=max(2, int(60 / res)))
        if len(tp2) >= 2:
            o = np.argsort(sdb[tp2])[::-1][:2]
            k1, k2 = sorted(tp2[o])
            sep2 = float(fin[sel][k2] - fin[sel][k1])
            w2 = max(2, int(round(max(40.0, 3 * res) / res)))
            lob = sum(float(np.clip(Ps_[max(0, k - w2):k + w2 + 1], 0, None).sum()) for k in (k1, k2))
            tot2 = float(np.clip(Ps_, 0, None).sum()) + 1e-30
            if 100.0 <= sep2 <= 1200.0 and lob / tot2 >= 0.7 and abs(sdb[k1] - sdb[k2]) <= 15:
                tva_toner = (sep2, float(fin[sel][k1]), float(fin[sel][k2]))
    out["tva_toner"] = tva_toner[0] if tva_toner else None
    flat = float(np.exp(np.mean(np.log(Pin[sel] + 1e-30))) / (np.mean(Pin[sel]) + 1e-30)) if sel.any() else 0.0
    out.update(spec_toner=tf.tolist(), n_spec_toner=ntones, tonavstand=t_sp, flathet=flat)

    # ── 8. Tal/ljud-aktivitet ──
    fr = max(1, int(fs * 0.02))
    nfr = N // fr
    syll = 0.0
    if nfr >= 10:
        e = (a[:nfr * fr] ** 2).reshape(nfr, fr).mean(axis=1)
        syll = float(np.std(10 * np.log10(e + 1e-20)))
    out["ljudvariation"] = syll

    # ── 9. Klassning ──
    narrow = occ < max(5 * res, 160)
    psk = None
    if car < 14 and not narrow:
        for n, nm in ((2, "BPSK"), (4, "QPSK"), (8, "PSK8")):
            if lines[n][1] >= 18:
                psk = nm
                break
    elif car < 14 and occ >= 35 and lines[2][1] >= 16 and lines[2][1] > car + 5 and cv_c >= 0.25:
        # Smal BPSK (t.ex. PSK31/PSK63): undertryckt bärvåg vid f0 men stark linje i z² vid 2·f0
        env2 = (a * a)[on] if onoff else a * a
        fb_p, sb_p = _line(env2.astype(np.float64), fs, max(15.0, 0.4 * occ), min(1.05 * occ, fs / 2.2))
        if fb_p and sb_p >= 12:
            psk = "BPSK"
            baud, bstr = float(fb_p), sb_p
            out["baud"], out["baud_styrka"] = baud, bstr
    # MSK: z² har två jämnstarka linjer symmetriskt kring mitten, ±halva symboltakten (VLF-stationer
    # 200 Bd: ±100 Hz, 38–46 dB i facit-klippen). PSK-testet ovan letar inom en radie som rymmer dem och
    # tog dem för BPSK/QPSK. BPSK har sin starkaste linje mitt i (PSK31: 59 dB mot 46 dB i sidorna).
    msk = _msk_par(zo, fs) if car < 14 and occ >= 40 else None
    # FSK med två toner ger också ett symmetriskt linjepar (avstånd = 2·shift). MSK är FSK med h = 0,5:
    # linjeavståndet är lika med symboltakten, som mäts för sig ur symbollinjerna (RTTY: 50 Bd mot 900 Hz)
    if msk and not (baud and abs(baud / msk - 1.0) < 0.05):
        # symboltakten kan vara felmätt (NAA: 62,5 Bd i första stegen): pröva i stället om frekvensbytena
        # har takten msk – för MSK ja (ett byte per symbol), för RTTY nej (50 Bd mot 900 Hz linjeavstånd)
        tr = np.abs(np.diff(fi))
        fb_m, sb_m = _line(tr.astype(np.float64), fs, 0.95 * msk, min(1.05 * msk, fs / 2.2))
        if not (fb_m and sb_m >= 15):
            msk = None
    if msk and nlev >= 2 and len(tones) >= 2:
        # den verkliga symboltakten ur tonernas omslag: MSK byter ton varje symbol (VLF: 200 Bd = linje-
        # avståndet), RTTY 50 Bd med 450 Hz shift har linjeavståndet 900 Hz men byter bara 50 gånger/s
        fb_t = _fsk_baud(fi_v, fs, tones)
        if fb_t and abs(fb_t / msk - 1.0) > 0.2:
            msk = None
    if msk:
        psk = None
        out["baud"], out["baud_styrka"] = msk, 30.0
        baud = msk
    out["msk"] = msk
    fsk_cv = 0.50 if (nlev == 2 and trans < 0.12) else 0.35
    fsk_baud = None
    if nlev >= 2 and shift and cv_c < fsk_cv and not psk:
        fsk_baud = _fsk_baud(fi_v, fs, tones)
    if msk:
        fam = "MSK"
        shift, nlev = msk / 2.0, 2
    elif not psk and onoff and narrow and cv_c < 0.3 and nlev <= 1:
        fam = "OOK"
    elif narrow and cv_c < 0.2 and not onoff:
        fam = "BÄRVÅG"
    elif onoff and car_frac >= 0.3 and nlev <= 1 and not psk:
        # nycklad bärvåg (RWM:s tidspulser, CW): hård nyckling ger breda nyckelklick som ser ut som
        # AM-sidband, men AM-rundradio har en bärvåg som är på hela tiden
        fam = "OOK"
    elif car_frac >= 0.4 and cv_c > 0.06 and not narrow and out.get("spegel", 0) >= 0.45:
        fam = "AM"                 # bärvågen bär större delen av effekten (OFDM-piloter: några procent)
    elif psk:
        fam = psk
    elif single and syll < 1.5:
        # en bärvåg med symboltakt – inte OFDM/AM/FM även om spektrumet rippar. Tal (stavelser, 2–3,5 dB
        # ljudvariation) kan ge en skenbar symbollinje/ram, modem har jämn effekt (< 0,7 dB)
        fam = "LIN"
    elif nlev >= 2 and cv_c < fsk_cv and shift and (nlev > 2 or trans < 0.18 or
                                                    (fsk_baud and abs(shift / fsk_baud - 0.5) < 0.12)):
        if fsk_baud:
            baud, bstr = fsk_baud, max(bstr, 20.0)
            out["baud"], out["baud_styrka"] = baud, bstr
        if nlev == 2:
            fam = "MSK" if baud and abs(shift / baud - 0.5) < 0.12 else "FSK2"
        elif nlev <= 4:
            fam = "FSK4"
        else:
            fam = "MFSK"
    elif ntones >= 6 and regular and cv_c > 0.3 and syll < 1.5:
        # syll: tal har stavelser (2,2–3,5 dB i analystest), modem jämn effekt (< 0,7) – talets
        # grundton ger annars ett "tonraster" som ser ut som OFDM-bärvågor
        fam = "OFDM"
    elif car >= 15 and cv_c > 0.06 and occ >= 1500 and out.get("spegel", 0) >= 0.35:
        fam = "AM"
    elif baud and bstr >= 14 and cv_c > 0.15 and syll < 1.5:
        fam = "LIN"
    elif tva_toner and not psk:
        fam = "FSK2"               # två toner i spektrumet (svag FSK där histogrammet smetats ut)
        shift = tva_toner[0]
        nlev = 2
        tones = np.array(tva_toner[1:])
    elif car_frac >= 0.5 and occ < 800:
        # smal signal där bärvågen bär effekten (CHU/RWM på långt håll: bärvåg + svaga sidband);
        # kraftigt varierande envelope = nycklad (RWM:s tidspulser, CW), annars en bärvåg
        fam = "OOK" if (onoff or cv_c >= 0.35) else "BÄRVÅG"
    elif occ < 400 and out.get("duty", 1.0) < 0.6 and cv_c >= 0.3 and car_frac >= 0.15 and nlev <= 1 and not psk:
        # svag nycklad bärvåg: av/på-detektorn ger upp i bruset, men påslagstiden och bärvågen syns
        fam = "OOK"
    elif onoff and occ < 800 and nlev <= 1:
        # smal, nyckelad signal (CW, RWM:s tidspulser) – inte SSB även om envelopen varierar mycket
        fam = "OOK"
    elif out.get("snr", 0.0) < 0.0 and cv_c < 0.3 and syll < 2.0:
        # mer brus än signal i den upptagna bandbredden och ingen envelope-/stavelsevariation:
        # brusets konstanta medel-envelope ser annars ut som FM (uppmätt i talpauser på 80 m)
        fam = "BRUS"
    elif cv_c < 0.3 and dev > max(3 * res, 0.08 * occ) and syll < 2.0 and occ >= 1500:
        # FM har konstant envelope (liten ljudvariation); svagt SSB-tal i brus kan få låg cv men behåller
        # stavelsernas nivåvariation (3–4 dB uppmätt på riktig 40/80 m-trafik)
        fam = "FM"
    elif occ >= 800 and (cv_c >= 0.3 or (syll >= 2.0 and occ >= 1000)):
        fam = "SSB"                # SSB-tal är minst ca 1,5 kHz brett
    else:
        fam = "OKÄND"
    out["fam"] = fam
    out["klass"] = FAM_NAMN[fam]
    if fam in ("BRUS", "BÄRVÅG", "OOK", "AM", "FM", "SSB", "OKÄND", "OFDM"):
        out["baud"] = None         # en ljudton är ingen symboltakt
    if fam in ("FSK2", "FSK4", "MFSK", "MSK"):
        out["shift"] = shift
        out["toner"] = nlev
    elif fam == "OFDM":
        out["shift"] = t_sp
        out["toner"] = ntones
    else:
        out["shift"] = None
        out["toner"] = None

    # ── 10. Detaljer per typ ──
    if fam == "FM":
        ct = _ctcss(fi, fs)
        if ct:
            out["ctcss"] = ct
    if fam in ("BPSK", "QPSK", "PSK8", "LIN") and out.get("baud"):
        M = {"BPSK": 2, "QPSK": 4, "PSK8": 8}.get(fam, 4)
        nl = {"BPSK": 2, "QPSK": 4, "PSK8": 8}.get(fam, 4)
        out["konst"] = _constellation(zo, fs, out["baud"], lines[nl][0], nl)
        out["psk_m"] = M
    if out.get("ram_ms"):
        out["ram_raster"] = _ram_raster(zo, fs, out["ram_ms"], out.get("baud"))
    if fam in ("FSK2", "FSK4", "MSK") and out.get("baud"):
        out["symboler"] = _fsk_symbols(fi, fs, out["baud"], tones)
    if fam in ("AM", "SSB", "OOK"):
        k = max(1, int(len(sm) / 400))
        out["kurva"] = sm[-400 * k::k] / p95
    out["ms"] = (time.perf_counter() - t0) * 1000
    return out


def _ram_raster(z, fs, ram_ms, baud=None):
    """2D-ramraster (som i SigDigger): varje rad är en ram (ram_ms), varje kolumn en symbol.
    Synkord och pilotsymboler har samma fassteg i varje ram och framträder som lodräta ränder."""
    if not ram_ms or ram_ms <= 0:
        return None
    W = int(round(ram_ms * baud / 1000.0)) if (baud and 16 <= ram_ms * baud / 1000.0 <= 1024) else 200
    frame_samps = ram_ms * 1e-3 * fs
    n_frames = int(len(z) / frame_samps)
    if n_frames < 3:
        return None
    sps = frame_samps / W
    pos = np.arange(n_frames * W) * sps
    syms = _sample(np.asarray(z, np.complex64), pos)
    if len(syms) < n_frames * W:
        n_frames = len(syms) // W
        if n_frames < 3:
            return None
        syms = syms[:n_frames * W]
    syms = syms.reshape(n_frames, W)
    dph = np.angle(syms * np.conj(np.roll(syms, 1, axis=1)))
    u = np.exp(1j * dph)
    coh = np.abs(np.mean(u[1:] * np.conj(u[:-1]), axis=0))
    kw = max(4, W // 8)
    s_coh = np.convolve(np.r_[coh, coh[:kw - 1]], np.ones(kw), mode="valid")[:W]
    shift = int(np.argmax(s_coh))
    dph = np.roll(dph, -shift, axis=1)
    return ((np.cos(dph) * 0.5 + 0.5) * 255).astype(np.uint8)


def _ramperiod(z, fs, lo_ms=5.0, hi_ms=250.0, min_r=0.12, min_kvot=6.0):
    """Starkaste periodicitet (ram) i komplexa basbandet z. Returnerar (period_ms, styrka) eller None.
    Styrka = normerad autokorrelation (0–1) vid perioden; kräver att den sticker ut mot medianen."""
    z = np.asarray(z, np.complex128)
    n = len(z)
    lo, hi = int(lo_ms / 1000 * fs), min(int(hi_ms / 1000 * fs), n // 3)
    if hi <= lo + 4:
        return None
    nf = 1 << int(math.ceil(math.log2(2 * n)))
    Z = np.fft.fft(z - z.mean(), nf)
    r = np.fft.ifft(np.abs(Z) ** 2)[:hi + 2]
    k = np.arange(len(r))
    r = np.abs(r) / (n - k) / (abs(r[0]) / n + 1e-30)      # opartisk och normerad
    seg = r[lo:hi + 1]
    i = int(np.argmax(seg))
    topp, med = float(seg[i]), float(np.median(seg)) + 1e-9
    if topp < min_r or topp < min_kvot * med:
        return None
    # en riktig ram är en topp: autokorrelationen ska stiga fram till den. Ligger maximum vid sökningens
    # början avtar den bara (t.ex. tal, vars autokorrelation sjunker långsamt) – ingen ram.
    if i < 2 or float(r[lo + i - 2:lo + i].min()) >= topp * 0.97:
        return None
    j = lo + i
    # grundperioden: en halv/tredjedels period som är nästan lika stark är den verkliga ramen
    for dv in (3, 2):
        jj = int(round(j / dv))
        if jj >= lo:
            w = r[max(lo, jj - 2):jj + 3]
            if len(w) and float(w.max()) >= 0.8 * topp:
                j = max(lo, jj - 2) + int(np.argmax(w))
                break
    frac = 0.0
    if 0 < j < len(r) - 1:
        a_, b_, c_ = r[j - 1], r[j], r[j + 1]
        d_ = a_ - 2 * b_ + c_
        frac = float(np.clip(0.5 * (a_ - c_) / d_, -0.5, 0.5)) if d_ != 0 else 0.0
    return (j + frac) / fs * 1000.0, topp


# Lågbaudiga MFSK-lägen med tonavstånd = symboltakt: (namn, symboltakt, antal toner, familj)
LAGBAUD_MFSK = [("FT8", 6.25, 8, "MFSK"), ("FT4", 20.833, 4, "FSK4")]


def lagbaud_mfsk(z, fs, f0=0.0):
    """Känner igen FT8/FT4 kring f0 Hz i basbandet z (helst ≥ 3 s).
    Fönster = en symbol: rätt läge ger EN ton per fönster, tonbyte ungefär varje symbol och högst M
    intilliggande toner. Returnerar dict(namn, baud, toner, renhet, ...) eller None."""
    z = np.asarray(z, np.complex64)
    for namn, b, M, fam in LAGBAUD_MFSK:
        N = int(round(fs / b))
        if N < 8 or len(z) < 12 * N:
            continue
        hop = max(1, N // 4)
        nfr = (len(z) - N) // hop
        idx = np.arange(N)[None, :] + hop * np.arange(nfr)[:, None]
        X = np.fft.fft(z[idx] * np.hanning(N).astype(np.float32), axis=1)
        fr = np.fft.fftfreq(N, 1 / fs)
        sel = np.flatnonzero(np.abs(fr - f0) <= (M + 1.5) * b)
        sel = sel[np.argsort(fr[sel])]
        if len(sel) < M:
            continue
        E = np.abs(X[:, sel]) ** 2
        tot = E.sum(axis=1) + 1e-30
        aktiv = tot >= 0.25 * np.percentile(tot, 90)
        if aktiv.sum() < 12:
            continue
        pk = E.argmax(axis=1)
        renhet = float(np.median((E.max(axis=1) / tot)[aktiv]))
        # tonbyte ungefär var 4:e hopp (= en symbol)
        p_ = pk[aktiv]
        byten = np.flatnonzero(np.diff(p_) != 0)
        langd = float(np.median(np.diff(byten))) if len(byten) >= 4 else 99.0
        anv = np.bincount(p_, minlength=E.shape[1]) / len(p_)
        toner = int(np.count_nonzero(anv >= 0.03))
        spann = int(np.ptp(np.flatnonzero(anv >= 0.03)) + 1) if toner else 0
        # FT8/FT4 använder intilliggande toner (en per frekvenssteg) – 2-FSK med stort skift har två
        # toner med ett tomt mellanrum och ska inte räknas (gav falsklarm på 50 Bd/200 Hz FSK och JT65).
        # En enbärvågssignal (CW/PSK31) har > 85 % av fönstren i 1–2 fack och ska heller inte räknas.
        sammanhang = toner >= 0.75 * spann if spann else False
        topp2 = float(np.sort(anv)[-2:].sum()) if len(anv) >= 2 else 1.0
        if (renhet >= 0.45 and 2.0 <= langd <= 7.0 and 3 <= toner <= M + 1 and spann <= M + 2
                and sammanhang and float(anv.max()) <= 0.65 and topp2 <= 0.85):
            return dict(namn=namn, baud=b, toner=M, fam=fam, renhet=renhet, symbol_hopp=langd,
                        toner_sedda=toner)
    return None


def _fsk_baud(fi, fs, tones):
    """Symboltakt för FSK ur runlängderna mellan tonbyten (robust mot teckenstruktur som RYRY)."""
    tones = np.sort(np.asarray(tones))
    idx = np.argmin(np.abs(fi[:, None] - tones[None, :]), axis=1)
    ch = np.flatnonzero(np.diff(idx) != 0)
    if len(ch) < 12:
        return None
    runs = np.diff(ch).astype(float)
    runs = runs[runs > max(2.0, 0.2 * float(np.percentile(runs, 90)))]
    if len(runs) < 10:
        return None
    T0 = float(np.percentile(runs, 15))
    errs = []
    for T in np.linspace(0.4 * T0, 1.3 * T0, 400):
        q = runs / T
        k = np.round(q)
        ok = (k >= 1) & (k <= 16)
        if ok.sum() < 0.8 * len(q):
            errs.append((T, np.inf))
            continue
        errs.append((T, float(np.mean((q[ok] - k[ok]) ** 2))))
    good = [T for T, e in errs if e < 0.02]
    if not good:
        return None
    # största takten som passar alla runlängder (T/2, T/3 … passar också men är fel)
    bT = max(good)
    # finjustera kring vald takt
    near = [(e, T) for T, e in errs if bT * 0.88 <= T <= bT]
    bT = min(near)[1]
    if bT < 0.7 * T0:
        bT *= 2.0                  # 1,5 stoppbit i Baudot-RTTY ger halva symbollängder (1,5·T, 2,5·T)
    b0 = fs / bT
    imp = np.zeros(len(idx))
    imp[ch] = 1.0
    fb, st = _line(imp, fs, b0 * 0.95, b0 * 1.05)
    return fb if fb and st >= 8 else b0


def _ctcss(fi, fs):
    """Subton (CTCSS) i FM-signalens momentanfrekvens."""
    k = max(1, int(fs / 1000))
    x = fi[:len(fi) // k * k].reshape(-1, k).mean(axis=1)
    if len(x) < 500:
        return None
    fl, s = _line(x.astype(np.float64), fs / k, 60, 260, nfft=_pow2(len(x) * 4))
    if not fl or s < 14:
        return None
    best = min(CTCSS, key=lambda c: abs(c - fl))
    return best if abs(best - fl) < 1.6 else None


def _sample(x, pos):
    i = np.floor(pos).astype(np.int64)
    fr = (pos - i).astype(np.float32)
    ok = (i >= 0) & (i + 1 < len(x))
    i, fr = i[ok], fr[ok]
    return x[i] * (1 - fr) + x[i + 1] * fr


def _msk_par(zo, fs, max_hz=2000.0):
    """Symboltakten (Bd) om z² har ett symmetriskt linjepar kring mitten (MSK), annars None.
    Kräver: båda linjerna minst 25 dB över medianen, högst 4 dB olika, och mitten minst 10 dB svagare."""
    zo = np.asarray(zo, np.complex64)
    n = len(zo)
    if n < 1024:
        return None
    N = int(min(1 << 17, _pow2(2 * n)))
    X = np.abs(np.fft.fftshift(np.fft.fft(zo.astype(np.complex128) ** 2, N))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(N, 1 / fs))
    sel = np.abs(f) <= min(max_hz, fs / 2.2)
    f, X = f[sel], X[sel]
    db = 10 * np.log10(X / (np.median(X) + 1e-30) + 1e-30)
    res = fs / N
    k1 = int(np.argmax(db))
    utom = np.abs(f - f[k1]) > max(10.0, 6 * res)
    if not utom.any():
        return None
    k2 = int(np.flatnonzero(utom)[np.argmax(db[utom])])
    a, b = sorted((k1, k2), key=lambda k: f[k])
    sep = float(f[b] - f[a])
    mitt = float(f[a] + f[b]) / 2
    if sep < 20.0 or min(db[a], db[b]) < 25.0 or abs(db[a] - db[b]) > 4.0 or abs(mitt) > 0.15 * sep:
        return None
    km = int(np.argmin(np.abs(f - mitt)))
    w = max(1, int(round(3.0 / res)))
    if float(db[max(0, km - w):km + w + 1].max()) > min(db[a], db[b]) - 10.0:
        return None
    return sep


def _constellation(zo, fs, baud, f_line, n):
    sps = fs / baud
    tail = zo[-int(min(len(zo), fs * 0.3)):]
    t = np.arange(len(tail)) / fs
    y = tail * np.exp(-2j * np.pi * f_line * t).astype(np.complex64)
    best, bpos = -1, None
    nsym = int(len(y) / sps) - 2
    if nsym < 20:
        return None
    for k in range(16):
        pos = (k / 16 + np.arange(nsym)) * sps
        s = _sample(y, pos)
        m = float(np.mean(np.abs(s)))
        if m > best:
            best, bpos = m, pos
    s = _sample(y, bpos)
    s = s * np.exp(-1j * np.angle(np.mean(s ** n)) / n)
    s = s / (np.sqrt(np.mean(np.abs(s) ** 2)) + 1e-12)
    return s[-500:]


def _fsk_symbols(fi, fs, baud, tones):
    sps = fs / baud
    tones = np.sort(np.asarray(tones))
    nsym = int(len(fi) / sps) - 2
    if nsym < 8:
        return ""
    best, bs = -np.inf, None
    for k in range(8):
        s = _sample(fi, (k / 8 + np.arange(nsym)) * sps)
        dist = np.min(np.abs(s[:, None] - tones[None, :]), axis=1)
        m = -float(np.mean(dist))
        if m > best:
            best, bs = m, s
    idx = np.argmin(np.abs(bs[:, None] - tones[None, :]), axis=1)
    return "".join(str(int(i)) for i in idx[-64:])


# ════════════════════════════════════════════════════════════════════
# Avkodare (insticksmoduler) – lägg till nya i AVKODARE längst ner
# ════════════════════════════════════════════════════════════════════
class Avkodare:
    """Basklass. passar(feat) avgör om avkodaren ska köras; feed() får nya samples
    (komplext basband kring analysens mål) och returnerar ny klartext."""
    namn = "?"
    PRIORITET = 0      # bland avkodare med synk körs bara de med högst prioritet

    def passar(self, feat):
        return False

    def reset(self):
        pass

    def feed(self, z, fs, feat):
        return ""

    def varden(self):
        """[(rubrik, värde)] att visa i värderutan."""
        return []

    def har_synk(self):
        """Sant när avkodaren har synk – då får den företräde framför andra."""
        return False

    def fakta(self):
        """Säkra värden från en avkodare med synk (t.ex. exakt symboltakt), ersätter mätningen."""
        return {}


ITA2_LTRS = "\0E\nA SIU\rDRJNFCKTZLWHYPQOBG\x0eMXV\x0f"
ITA2_FIGS = "\x003\n- '87\r\x054\x07,!:(5+)2#6019?&\x0e./=\x0f"


class TvatonsBas(Avkodare):
    """Gemensam grund för avkodare av tvåtons-FSK (RTTY, NAVTEX/SITOR-B):
    1. Tonerna: mitten finjusteras ur spektrumet kring klicket (det tonpar med en standard-shift
       vars toner finns båda och turas om), shiften avrundas till en standard.
    2. Störningsspärr: FSK har jämn amplitud, så korta pulser långt över den normala nivån nollas.
    3. Tvåtonsdetektor: ett filter per ton (ungefär en bit långt) i stället för frekvens-
       diskriminator – tål brus mycket bättre. Varje tons nivå normeras för sig (ATC), så
       selektiv fädning (mark och space fädar olika) inte flyttar beslutsgränsen."""
    SHIFTAR = (170.0, 200.0, 425.0, 450.0, 850.0)
    RR = 2400.0        # Hz, arbetstakt
    HIST_S = 4.0       # s historik
    ATC_S = 0.35       # s, fönster för tonernas nivå (tränat: kortare klarar selektiv fädning)
    MIN_SNR = 3.0      # dB, tonparet måste synas över bruset
    NB = 4.0           # störningsspärr: nollar sampel starkare än NB × normal nivå
    SOK_BAS = 60.0     # Hz: tonparets mitt söks så här långt från klicket (+ halva shiften)
    SOK_STEG = 1.0     # Hz, steg i sökningen

    def _tva_reset(self):
        self.acc = np.zeros(0, np.complex64)
        self.y = np.zeros(0, np.complex64)
        self.base = 0                     # absolut index för y[0]
        self.c = None                     # tonparets mitt (Hz)
        self.snr = 0.0                    # tonparets nivå över bruset (dB)
        self.nb = None                    # störningsspärrens referensnivå
        self.shpoang = {}                 # shift: (poäng i dB, mitt, energi)
        self.sh = None                    # shift (Hz)
        self.n_c = 0                      # antal skattningar av mitten

    def _tva_mata(self, z, fs, feat):
        """Tar emot nya sampel (decimerar, störningsspärr, tonerna). Sant när tonerna är kända."""
        if len(z) == 0:
            return False
        k = max(1, int(round(fs / self.RR)))
        self.RR = fs / k
        x = np.concatenate((self.acc, np.asarray(z, np.complex64)))
        n = len(x) // k
        self.acc = x[n * k:]
        if n == 0:
            return False
        ny = x[:n * k].reshape(n, k).mean(axis=1)
        a = np.abs(ny)
        med = float(np.median(a)) if len(a) else 0.0
        self.nb = med if self.nb is None else 0.9 * self.nb + 0.1 * med
        ny = np.where(a > self.NB * self.nb, 0, ny).astype(np.complex64)
        self.y = np.concatenate((self.y, ny))
        self._toner(feat)
        return self.sh is not None and len(self.y) >= self.RR * 0.5

    def _trimma(self, behovs_fran):
        """Släng historik före absolut index behovs_fran (men behåll högst HIST_S sekunder)."""
        keep = behovs_fran - int(self.ATC_S * self.RR) - 1
        keep = max(keep, len(self.y) + self.base - int(self.HIST_S * self.RR))
        cut = keep - self.base
        if cut > 0:
            self.y = self.y[cut:]
            self.base += cut


    # ── 1. tonerna ────────────────────────────────────────────────────
    def _toner(self, feat):
        RR = self.RR
        sh = feat.get("shift") if feat else None
        if feat and feat.get("fi_peaks") is not None and len(feat["fi_peaks"]) >= 2:
            p = np.sort(np.asarray(feat["fi_peaks"], float))
            sh = sh or float(p[-1] - p[0])
        c0 = float(feat.get("f_mid", 0.0)) if feat else 0.0
        if self.c is None:
            self.c = c0
        n = min(len(self.y), int(RR * 1.5))
        if n < 256:
            return
        nf = 1 << int(math.log2(n))
        P = np.abs(np.fft.fft(self.y[-nf:] * np.hanning(nf))) ** 2
        f = np.fft.fftfreq(nf, 1 / RR)
        o = np.argsort(f)
        f, P = f[o], _smooth(P[o], max(1, int(round(8.0 / (RR / nf)))))
        # alltid en standard-shift: analysens mätning väljer bara bland dem (en störd mätning, t.ex.
        # 135 Hz för en 170 Hz-signal i knaster, ska inte flytta tonfiltren)
        kand = [s for s in self.SHIFTAR if not sh or abs(s - sh) / s < 0.15] or list(self.SHIFTAR)
        # sök kring klicket (analysen kan flytta kanalens mitt mellan flera signaler i ett tätt band);
        # klicket kan ligga på en av tonerna, så mitten får ligga en halv shift bort
        if feat and feat.get("klick_off") is not None:
            mitt, extra = float(feat["klick_off"]), True
        else:
            mitt, extra = (c0 if (feat and feat.get("f_mid") is not None) else self.c), False
        for s in kand:
            w = self.SOK_BAS + (s / 2 if extra else 0.0)
            cc = np.arange(mitt - w, mitt + w + 0.1, self.SOK_STEG)
            # båda tonerna måste finnas (geometriskt medel): en stark granne på ena sidan räcker inte
            e = 2 * np.sqrt(np.interp(cc - s / 2, f, P) * np.interp(cc + s / 2, f, P))
            e = e * 10 ** (-0.01 * np.abs(cc - mitt) / 10)      # liten fördel för par nära klicket
            k = int(np.argmax(e))
            # varje shift samlar poäng över tiden, så valet inte hoppar mellan analysstegen
            p0 = self.shpoang.get(s)
            v = 10 * math.log10(float(e[k]) / (float(np.median(P)) + 1e-30) + 1e-12)
            # ett äkta FSK-par växlar: när den ena tonen hörs är den andra tyst (motsatt korrelerade
            # nivåer). Två olika signaler (t.ex. CW-granne + en ton från en annan RTTY) gör inte så.
            v += 10 * math.log10(self._vaxlar(float(cc[k]), s, n) + 0.05)
            self.shpoang[s] = (v if p0 is None else 0.8 * p0[0] + 0.2 * v, float(cc[k]), float(e[k]))
        s = max(kand, key=lambda q: self.shpoang[q][0])
        _, c, e = self.shpoang[s]
        # mitten flyttas snabbt i början och sedan långsamt: en sändare ändrar sig långsamt, och en
        # granne som stör en enstaka skattning ska inte dra iväg tonfiltren
        self.n_c = getattr(self, "n_c", 0) + 1
        a = 0.3 if self.n_c <= 8 else 0.08
        self.c = c if self.sh is None else (1 - a) * self.c + a * c
        self.sh = s
        # finns signalen kvar? tonparets energi mot bruset vid sidan om, de senaste 0,6 s
        n2 = 1 << int(math.log2(max(64, min(len(self.y), int(RR * 0.6)))))
        P2 = np.abs(np.fft.fft(self.y[-n2:] * np.hanning(n2))) ** 2
        f2 = np.fft.fftfreq(n2, 1 / RR)
        o2 = np.argsort(f2)
        f2, P2 = f2[o2], _smooth(P2[o2], 3)
        ton = 0.5 * (np.interp(self.c - s / 2, f2, P2) + np.interp(self.c + s / 2, f2, P2))
        ref = float(np.median(P2[np.abs(f2 - self.c) < max(600.0, s)])) + 1e-30
        self.snr = 10 * math.log10(max(float(ton), 1e-30) / ref)

    def _vaxlar(self, c, s, n):
        """Hur tydligt tonerna c ± s/2 växlar (0–1): minus korrelationen mellan deras nivåer."""
        y = self.y[-n:]
        t = np.arange(len(y)) / self.RR
        L = max(1, int(self.RR * 0.008))
        a = []
        for f in (c + s / 2, c - s / 2):
            m = y * np.exp(-2j * np.pi * f * t)
            a.append(np.abs(_smooth(m.real, L) + 1j * _smooth(m.imag, L)))
        if a[0].std() < 1e-12 or a[1].std() < 1e-12:
            return 0.0
        return max(0.0, -float(np.corrcoef(a[0], a[1])[0, 1]))

    # ── 2. tvåtonsdetektor med ATC ────────────────────────────────────
    def _beslut(self, baud):
        RR = self.RR
        t = (self.base + np.arange(len(self.y))) / RR
        L = max(1, int(round(0.9 * RR / baud)))
        W = max(3, int(self.ATC_S * RR))
        a = []
        for f in (self.c + self.sh / 2, self.c - self.sh / 2):     # mark = högre tonen
            m = self.y * np.exp(-2j * np.pi * f * t)
            m = _smooth(m.real, L) + 1j * _smooth(m.imag, L)
            env = np.abs(m)
            a.append((env, _smooth(env, W)))
        (am, mm), (as_, ms) = a
        mm = np.maximum(mm, 0.25 * ms) + 1e-12      # en ton kan inte vara mycket svagare än den andra
        ms = np.maximum(ms, 0.25 * mm) + 1e-12      # i längden (annars tomgång: bara mark)
        return (am / mm - as_ / ms).astype(np.float32)


class RttyAvkodare(TvatonsBas):
    """Baudot/ITA2-RTTY: 1 startbit, 5 databitar (LSB först), 1–2 stoppbitar.

    Tonerna och tvåtonsdetektorn kommer från TvatonsBas. Ramsynk körs parallellt för alla
    standardtakter och båda polariteterna; den som ramar bäst skrivs ut. Ett tillfälligt fel i
    analysens takt nollställer alltså ingenting."""
    namn = "RTTY (Baudot/ITA2)"
    STD = (45.45, 50.0, 75.0, 100.0)
    MIN_OK = 0.45      # poäng (andel godkända ramar × bitarnas tydlighet) som krävs för att skriva ut

    def __init__(self):
        self.reset()

    def reset(self):
        self._tva_reset()
        self.fr = {(b, p): self._mk() for b in self.STD for p in (0, 1)}
        self.best = None
        self.baud = None
        self.synkat = False               # har haft synk (då spelas inte gammal text upp igen)

    @staticmethod
    def _mk():
        return {"hist": deque(maxlen=30), "text": [], "figs": False, "pos": None, "emit": 0, "ok": 0, "q": 0.0}

    def passar(self, feat):
        b, s = feat.get("baud"), feat.get("shift")
        if b and (abs(b - 100) < 5 or abs(b - 50) < 2.5) and s and 130 <= s <= 230:
            return False                 # SITOR-B/NAVTEX-signatur (fasningen ser ut som 50 Bd) – sköts av NAVTEX
        return feat.get("fam") == "FSK2" and b and 40 <= b <= 115 and s and 60 <= s <= 1100

    def har_synk(self):
        return self.best is not None

    # ── 3. ramsynk ────────────────────────────────────────────────────
    def feed(self, z, fs, feat):
        if not self._tva_mata(z, fs, feat):
            return ""
        marg = int(self.ATC_S * self.RR / 2) + 1
        for b in self.STD:
            xb = self._beslut(b)
            gr = len(xb) - marg
            for p in (0, 1):
                m = self.fr[(b, p)]
                if m["pos"] is None or m["pos"] < self.base:
                    m["pos"] = self.base
                m["pos"] = self._run(m, xb if p == 0 else -xb, self.RR / b, gr)
        # välj den ramning som lyckas bäst (med hysteres)
        sc = {k2: (m["hist"].count(True) / len(m["hist"]) * min(1.0, m["q"]) if len(m["hist"]) >= 10 else 0.0)
              for k2, m in self.fr.items()}
        if self.snr < self.MIN_SNR:              # tonerna syns inte längre: skriv inget (brus ger skräp)
            sc = {k2: 0.0 for k2 in sc}
        for k2 in sc:                            # normal polaritet är vanligast (RYRY är tvetydigt)
            if k2[1] == 0 and sc[k2] > 0:
                sc[k2] += 0.02
            if sc[k2] > 0 and "RYRY" in "".join(self.fr[k2]["text"][-12:]):
                sc[k2] += 0.1                    # RYRY (standard i början) avgör polariteten
        if feat and feat.get("baud"):            # analysens takt: liten fördel – och före första synken
            for k2 in sc:                        # avgörande (RYRY ramar bra även med fel takt)
                if abs(k2[0] - feat["baud"]) / k2[0] < 0.08:
                    sc[k2] += 0.03
                elif not self.synkat:
                    sc[k2] *= 0.5
        top = max(sc, key=sc.get)
        if sc[top] < self.MIN_OK:
            top = self.best if (self.best and sc[self.best] >= self.MIN_OK - 0.1) else None
        elif self.best and top != self.best and sc[self.best] >= sc[top] - 0.1:
            top = self.best
        if top != self.best and top is not None:
            # första synken: ta med de senaste tecknen (början av sändningen); vid byte av
            # ramning fortsätt där den nya är nu, annars skrivs samma text två gånger
            m0 = self.fr[top]
            m0["emit"] = max(0, len(m0["text"]) - 20) if not self.synkat else len(m0["text"])
            self.synkat = True
        self.best = top
        self.baud = top[0] if top else self.baud
        self._trimma(min(m["pos"] for m in self.fr.values()))       # historik som ingen ramning behöver
        if top is None:
            return ""
        m = self.fr[top]
        new = "".join(m["text"][m["emit"]:])
        m["emit"] = len(m["text"])
        for mm in self.fr.values():
            if len(mm["text"]) > 5000:
                d = len(mm["text"]) - 1000
                del mm["text"][:d]
                mm["emit"] = max(0, mm["emit"] - d)
        return new

    def _run(self, m, s, sps, gr):
        """Ramning över s (absoluta index via self.base) från m["pos"] till gr; returnerar ny position."""
        b0 = self.base
        i = int(m["pos"]) - b0
        frame = int(sps * 7.6)
        mark = s > 0

        def bit(c):
            c = int(round(c))
            return float(s[c]) > 0 if 0 <= c < len(s) else False

        while i + frame < gr:
            seg = mark[i:gr - frame]
            edges = np.flatnonzero(seg[:-1] & ~seg[1:])
            if len(edges) == 0:
                i = gr - frame
                break
            j = i + int(edges[0]) + 1                      # första sampel i startbiten
            if not bit(j + 0.5 * sps) and bit(j + 6.5 * sps):
                code = 0
                q = 0.0
                for kk in range(5):
                    c = int(round(j + (1.5 + kk) * sps))
                    v = float(s[c]) if 0 <= c < len(s) else 0.0
                    q += min(abs(v), 2.0)
                    if v > 0:
                        code |= 1 << kk
                # hur tydliga bitarna är i mitten: vid fel takt hamnar avläsningen nära bitgränserna
                m["q"] = 0.9 * m["q"] + 0.1 * q / 5
                m["hist"].append(True)
                m["ok"] += 1
                ch = None
                if code == 0x1B:
                    m["figs"] = True
                elif code == 0x1F:
                    m["figs"] = False
                else:
                    ch = (ITA2_FIGS if m["figs"] else ITA2_LTRS)[code]
                    if code == 0x04:
                        m["figs"] = False      # 'unshift on space'
                if ch and ch not in "\0\r\x05\x07":
                    m["text"].append(ch)
                i = j + int(sps * 6.5)         # mitt i (första) stoppbiten: klarar 1, 1,5 och 2 stoppbitar
            else:
                m["hist"].append(False)
                i = j + 1
        return b0 + max(0, i)

    def varden(self):
        if self.best is None:
            return [("RTTY", "söker ramsynk …")]
        b, p = self.best
        m = self.fr[self.best]
        ok = m["hist"].count(True) / max(1, len(m["hist"]))
        return [("RTTY", f"{b:g} Bd, {self.sh:.0f} Hz, {'normal' if p == 0 else 'omvänd'} polaritet"),
                ("Ramar rätt", f"{ok * 100:.0f} %")]


MORSE = {".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G", "....": "H",
         "..": "I", ".---": "J", "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O", ".--.": "P",
         "--.-": "Q", ".-.": "R", "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
         "-.--": "Y", "--..": "Z", ".----": "1", "..---": "2", "...--": "3", "....-": "4", ".....": "5",
         "-....": "6", "--...": "7", "---..": "8", "----.": "9", "-----": "0", ".-.-.-": ".", "--..--": ",",
         "..--..": "?", "-..-.": "/", "-...-": "=", ".-.-.": "+", "-....-": "-", ".--.-.": "@",
         ".-...": "&", "...-.-": "<SK>", "-.--.": "(", "-.--.-": ")", "---...": ":"}


def _kmeans1d(x, init, iters=12):
    """Enkel k-means i en dimension (för tidsavstånd i log-skala). Returnerar (centra, antal)."""
    c = np.array(init, float)
    for _ in range(iters):
        lab = np.argmin(np.abs(x[:, None] - c[None, :]), axis=1)
        nc = np.array([x[lab == k].mean() if np.any(lab == k) else c[k] for k in range(len(c))])
        if np.allclose(nc, c):
            break
        c = np.sort(nc)
    lab = np.argmin(np.abs(x[:, None] - c[None, :]), axis=1)
    return c, np.bincount(lab, minlength=len(c))


def laga_grupp(tecken, n):
    """Lagar en träningsgrupp som ska ha n tecken. tecken = [[symbol, paus efter], …].

    För många tecken: ett fädningsavbrott har delat ett tecken i två (T + M = O). De två
    intilliggande tecken med kortast paus emellan vars symboler tillsammans är ett giltigt
    tecken slås ihop. För få: ett okänt tecken (två tecken där pausen försvann) delas i två
    giltiga. Returnerar listan med symboler."""
    t = [[s, g] for s, g in tecken]
    while len(t) > n:
        kand = [(t[i][1], i) for i in range(len(t) - 1) if t[i][0] + t[i + 1][0] in MORSE]
        if not kand:
            break
        _, i = min(kand)
        t[i:i + 2] = [[t[i][0] + t[i + 1][0], t[i + 1][1]]]
    while len(t) < n:
        for i, (s, g) in enumerate(t):
            if s in MORSE:
                continue
            delar = [k for k in range(1, len(s)) if s[:k] in MORSE and s[k:] in MORSE]
            if delar:
                k = min(delar, key=lambda k: abs(k - len(s) / 2))
                t[i:i + 1] = [[s[:k], 0.0], [s[k:], g]]
                break
        else:
            break
    return [s for s, _ in t]


class CwAvkodare(Avkodare):
    """Morse i tre steg:

    1. Signalbehandling: bärvågen spåras och blandas ner till 0 Hz, ett smalt filter som följer
       takten tar bort bruset och envelopen blir ett nyckelläge (på/av med hysteres).
    2. Särdrag: nyckelläget blir en följd av tidsavstånd – tonlängder och pauslängder.
       Störtoppar och korta avbrott i en ton slås ihop med grannarna.
    3. Klassning: tonlängderna klustras i två grupper (prick/streck) och pauserna i tre
       (inom tecknet / mellan tecken / mellan ord) med k-means i log-skala. Gränserna lärs
       ur signalen själv, så avkodaren klarar Farnsworth-takt, tunga eller lätta streck och
       ojämn handnyckling, inte bara de ideala förhållandena 1:3 och 1:3:7."""
    namn = "Morse (CW)"
    RATE = 500.0       # Hz, upplösning för nyckelläget
    RA = 2000.0        # Hz, mellansteg för bärvågsspårning och smalt filter
    SOK_HZ = 400.0     # bärvågen söks inom ±400 Hz från mitten …
    SOK_NARA = 120.0   # … eller ±120 Hz från signalens mitt när analysen har mätt den
    FILTER = 0.5       # det smala filtrets längd i enheter (prickar)
    START_ENHET = 0.06  # s, antagen enhet innan takten är inlärd (0,06 = 20 ord/min)
    MIN_EL = 0.012     # s; kortare toner är störningar (en prick i 100 ord/min)
    GATE = 2.6         # tonnivå/brusnivå som krävs; rent brus ger ~2,2 efter det smala filtret

    def __init__(self):
        self.reset()

    def reset(self):
        self.acc = np.zeros(0, np.complex64)       # rest till första decimeringen
        self.acc2 = np.zeros(0, np.complex64)      # rest/svans till det smala filtret
        self._dec = 0                              # fas i sista decimeringen
        self.psd = None
        self.f0 = 0.0
        self.f_hint = None           # signalens mitt enligt analysen (Hz från målet)
        self.ph = 0.0
        self.lvl_hi = None
        self.lvl_lo = None
        self.min_el = self.MIN_EL
        self.q = None                # utjämnad tonnivå/brusnivå
        self.state = False
        self.run = 0                 # sampel i pågående körning
        self.marks = deque(maxlen=80)
        self.spaces = deque(maxlen=120)
        self.backlog = []            # tidsavstånd innan takten är känd (spelas upp i efterhand)
        self.rq = []                 # körningar som väntar på nästa (för ihopslagning)
        self.pend = None             # ton som väntar på beslut (ett kort avbrott kan följa)
        self.gap = 0.0               # pågående paus i sekunder (inkl. ihopslagna störtoppar)
        self.bridge = 0.0            # kort avbrott inne i en ton
        self.sym = ""
        self.ordet = []              # tecknen i pågående grupp: [symbol, paus efter]
        self.vantar = []             # en kort grupp som kanske fortsätter i nästa (gruppläge)
        self.space_done = True
        self.dot = None
        self.dash = None
        self.unit = None             # takten (en enhet) i sekunder
        self.L_s = 0.03              # det smala filtrets längd i sekunder
        self.f_start = 0.5           # tröskelns läge vid den pågående körningens början
        self.gaps = None             # (inom tecken, mellan tecken, mellan ord) i sekunder
        self.wpm = None
        self.fw = None               # Farnsworth: teckentakt härledd ur teckenmellanrummen

    def passar(self, feat):
        return feat.get("fam") == "OOK"

    # ── 1. Signalbehandling ───────────────────────────────────────────
    def _envelope(self, z, fs):
        z = np.asarray(z, np.complex64)
        k = max(1, int(round(fs / self.RA)))
        ra = fs / k
        x = np.concatenate((self.acc, z))
        n = len(x) // k
        if n == 0:
            self.acc = x
            return np.zeros(0, np.float32)
        y = x[:n * k].reshape(n, k).mean(axis=1)
        self.acc = x[n * k:]
        # spåra bärvågen: medelvärdesbildat effektspektrum, toppen inom sökfönstret
        if len(y) >= 128:
            nf = 1 << int(math.log2(min(len(y), 2048)))
            P = np.abs(np.fft.fft(y[-nf:] * np.hanning(nf))) ** 2
            f = np.fft.fftfreq(nf, 1 / ra)
            P = np.interp(np.linspace(-ra / 2, ra / 2, 512, endpoint=False), np.fft.fftshift(f), np.fft.fftshift(P))
            self.psd = P if self.psd is None else 0.8 * self.psd + 0.2 * P
            ff = np.linspace(-ra / 2, ra / 2, 512, endpoint=False)
            if self.f_hint is not None:          # analysen har hittat signalen: sök bara nära den,
                inom = np.abs(ff - self.f_hint) <= self.SOK_NARA     # så att en granne inte tar över
            else:
                inom = np.abs(ff) <= min(self.SOK_HZ, ra / 2 * 0.8)
            Pi = self.psd[inom]
            j = int(np.argmax(Pi))
            if Pi[j] > 6 * np.median(Pi):        # tydlig topp: följ den
                self.f0 = float(ff[inom][j])
        t = np.arange(len(y))
        y = y * np.exp(-1j * (self.ph + 2 * np.pi * self.f0 / ra * t))
        self.ph = float((self.ph + 2 * np.pi * self.f0 / ra * len(y)) % (2 * np.pi))
        # smalt filter: glidande medel ~ en halv prick (5–40 ms; 20 ord/min innan takten är känd).
        # Toner kortare än filtret kan inte vara äkta – de räknas som störningar (se min_el).
        L = int(np.clip(round((self.unit or self.START_ENHET) * self.FILTER * ra), max(1, ra * 0.005), ra * 0.05))
        self.min_el = max(self.MIN_EL, 0.8 * L / ra)
        self.L_s = L / ra
        yy = np.concatenate((self.acc2, y))
        if len(yy) < L + 1:
            self.acc2 = yy
            return np.zeros(0, np.float32)
        # två glidande medel efter varandra (L1 + L2 = L): sidloberna dämpas dubbelt så mycket i dB
        # som med ett, så en stark granne några hundra Hz bort läcker inte in i pauserna
        L1 = max(1, L // 2)
        L2 = max(1, L - L1 + 1)
        cs = np.concatenate(([0], np.cumsum(yy)))
        m1 = (cs[L1:] - cs[:-L1]) / L1
        cs = np.concatenate(([0], np.cumsum(m1)))
        m = (cs[L2:] - cs[:-L2]) / L2
        Lt = L1 + L2 - 1
        self.acc2 = yy[len(yy) - (Lt - 1):] if Lt > 1 else yy[:0]
        # decimera till nyckellägets upplösning
        d = max(1, int(round(ra / self.RATE)))
        e = np.abs(m[(-self._dec) % d::d]).astype(np.float32)
        self._dec = (self._dec + len(m)) % d
        return e

    # nivåföljning och trösklar – inställda mot analystest.py morse (syntetiskt, känt facit) och en
    # riktig FAV22-inspelning (jämförd med ggmorse): djup fädning vill ha låga trösklar, störningar
    # i pauserna höga
    T_HI = 0.04        # s, tonnivån följer tonen under nedtryckning
    T_LO = 0.15        # s, brusnivån följer pauserna uppåt (störningar i pauserna höjer den snabbt)
    T_LO_NED = 0.15    # s, … och nedåt
    T_DEC = 0.7        # s, i pauser sjunker tonnivån mot bruset (glömmer gamla fädningstoppar)
    P_ON = 0.6         # tröskel för ton på: brus·(ton/brus)^P_ON – mitt emellan i log-skala, så en
    P_OFF = 0.6        # fädande ton 10 dB under den förra syns ändå; P_OFF = tröskel för ton av
    F_MIN = 0.35       # men aldrig under brus + F_MIN·(ton − brus): filtret drar ut flankerna
    LO_GRANS = 1.0     # brusnivån uppdateras bara under brus + LO_GRANS·(ton − brus)
    GOLV = 2.5         # tonen måste vara minst så här många gånger brusnivån

    def _key(self, e):
        """Envelop → nyckelläge. Ton- och brusnivån följs element för element (klarar fädning, QSB),
        tröskeln ligger mellan dem med hysteres och aldrig nära brusets egen nivå."""
        if self.lvl_hi is None:
            self.lvl_hi = float(np.percentile(e, 95))
            self.lvl_lo = float(np.percentile(e, 10))
        R = self.RATE
        a_on = 1 - math.exp(-1 / (R * self.T_HI))
        a_off = 1 - math.exp(-1 / (R * self.T_LO))
        a_ned = 1 - math.exp(-1 / (R * self.T_LO_NED))
        a_dec = 1 - math.exp(-1 / (R * self.T_DEC))
        p_on, p_off, lg, golv, fm = self.P_ON, self.P_OFF, self.LO_GRANS, self.GOLV, self.F_MIN
        hi, lo = self.lvl_hi, self.lvl_lo
        key = np.empty(len(e), bool)
        st = self.state
        self._kf = kf = []           # tröskelns läge (andel brus → ton) vid varje flank
        for i, v in enumerate(e):
            v = float(v)
            if st:
                hi += a_on * (v - hi)
            else:
                if v < lo + lg * (hi - lo):
                    lo += (a_off if v > lo else a_ned) * (v - lo)
                hi -= a_dec * (hi - lo)
                if v > hi:
                    hi = v
            q = max(hi, lo * 1.0001) / max(lo, 1e-12)
            gmin = lo + fm * (hi - lo)
            if st:
                t = max(lo * q ** p_off, 0.8 * gmin, 0.75 * golv * lo)
                if v < t:
                    st = False
                    kf.append((t - lo) / max(hi - lo, 1e-12))
            else:
                t = max(lo * q ** p_on, gmin, golv * lo)
                if v > t:
                    st = True
                    kf.append((t - lo) / max(hi - lo, 1e-12))
            key[i] = st
        self.lvl_hi, self.lvl_lo = hi, lo
        return key

    # ── 2. Särdrag: tidsavstånd ───────────────────────────────────────
    def feed(self, z, fs, feat):
        if len(z) == 0:
            return ""
        fm = feat.get("f_mid") if feat else None
        if fm is not None and feat.get("fam") == "OOK":
            self.f_hint = float(fm)
            if self.psd is None:
                self.f0 = self.f_hint
        e = self._envelope(z, fs)
        if len(e) == 0:
            return ""
        key = self._key(e)
        R = self.RATE
        q = self.lvl_hi / max(self.lvl_lo, 1e-12)
        self.q = q if self.q is None else 0.7 * self.q + 0.3 * q
        if self.q < self.GATE:
            # ingen tydlig nyckling (bara brus): lär inte takten av bruset och skriv inget nytt,
            # men ett tecken som redan var påbörjat när signalen försvann skrivs ut
            ut = ""
            if self.dot is not None and (self.rq or self.pend is not None or self.sym):
                o = []
                self._toem_ko(o)
                self._commit_mark()
                if self.sym:
                    self._tecken(self.sym, o)
                    self.sym = ""
                self._ord_slut(o, mellanslag=False)
                ut = "".join(o)
            self.state, self.run = bool(key[-1]), 0
            self.pend, self.bridge, self.gap, self.sym = None, 0.0, 0.0, ""
            self.backlog = []
            self.rq = []
            return ut
        out = []
        # körlängder ur nyckelläget (den sista körningen är öppen)
        ch = np.flatnonzero(key[1:] != key[:-1]) + 1
        starts = np.concatenate(([0], ch))
        lens = np.diff(np.concatenate((starts, [len(key)])))
        kf = iter(self._kf)

        def slut(mark, d):
            # Filtret (glidande medel, L) drar ut en ton med L·(1 − f_på − f_av), där f är tröskelns
            # läge mellan brus och ton vid flankerna; pausen krymper lika mycket. Räkna tillbaka.
            fb = min(1.0, max(0.0, next(kf, 0.5)))
            k = self.L_s * (1 - self.f_start - fb)
            self.f_start = fb
            self._run_end(mark, max(0.002, d - k if mark else d + k), out)

        if key[0] != self.state:            # ny körning direkt i början
            slut(self.state, self.run / R)
            self.run = 0
        for s, n in zip(key[starts[:-1]], lens[:-1]):
            slut(bool(s), (self.run + n) / R)
            self.run = 0
        self.state = bool(key[starts[-1]])
        self.run += int(lens[-1])
        if not self.state and self.dot is not None:
            g = self.run / R
            if g >= self.BRYGGA * self.dot:      # pausen är äkta: det som väntar kan avgöras
                if self.rq and self.rq[-1][0] is False:
                    g += self.rq.pop()[1]
                self._toem_ko(out)
                self._open_gap(self.bridge + self.gap + g, out)
        return "".join(out)

    def _run_end(self, mark, d, out):
        """En körning (ton eller paus) med längden d sekunder har tagit slut."""
        if self.dot is None:
            self.backlog.append((mark, d))
            if mark and d > self.min_el:
                self.marks.append(d)
            elif not mark and self.min_el < d < 3.0:
                self.spaces.append(d)
            if self._learn():
                bl, self.backlog = self.backlog, []
                self.marks.clear()          # fylls på igen när backloggen spelas upp
                self.spaces.clear()
                for m, dd in bl:
                    self._ko(m, dd, out)
            return
        self._ko(mark, d, out)

    BRYGGA = 0.5       # avbrott kortare än så här (i prickar) mitt i en ton slås ihop med tonen
    TOPP = 0.3         # toner kortare än så här (i prickar) mitt i en paus räknas som störning

    def _ko(self, mark, d, out):
        """Kö med en körnings framförhållning: en kort körning mellan två av motsatt sort
        (avbrott i en ton vid snabb fädning, störtopp i en paus) slås ihop med grannarna."""
        q = self.rq
        if q and q[-1][0] == mark:
            q[-1][1] += d
        else:
            q.append([mark, d])
        while len(q) >= 3:
            m1, d1 = q[1]
            # avbrott mäts mot den korta pausen, störtoppar mot pricken: om tröskeln drar ut
            # tonerna växer pricken och krymper pausen lika mycket, så gränserna följer med
            if d1 < (self.TOPP * self.dot if m1 else self.BRYGGA * (self.gaps[0] if self.gaps else self.dot)):
                q[0][1] += d1 + q[2][1]
                del q[1:3]
                continue
            m0, d0 = q.pop(0)
            self._run(m0, d0, out)

    def _toem_ko(self, out):
        while self.rq:
            m0, d0 = self.rq.pop(0)
            self._run(m0, d0, out)

    def _run(self, mark, d, out):
        dot = self.dot
        if mark:
            if d < max(self.min_el, 0.3 * dot):        # störtopp: en del av pausen
                self.gap += d
                return
            if self.pend is not None and self.bridge:  # kort avbrott inne i en ton: slå ihop
                self.pend += self.bridge + self.gap + d
            else:
                self.pend = d
            self.bridge = 0.0
            self.gap = 0.0
            return
        g = self.bridge + self.gap + d
        self.gap = self.bridge = 0.0
        if self.pend is not None and g < 0.3 * dot:   # avbrott – vänta på nästa ton
            self.bridge = g
            return
        self._commit_mark()
        if g < 4 * (self.gaps[2] if self.gaps else 7 * dot):
            self.spaces.append(g)
            self._learn()
        self._open_gap(g, out)

    def _open_gap(self, g, out):
        """Pausen pågår fortfarande: skriv tecken/mellanslag så fort gränsen passerats."""
        if self.dot is None:
            return
        if self.pend is not None and g >= 0.3 * self.dot:
            self.gap += self.bridge          # avbrottet var början på pausen
            self.bridge = 0.0
            self._commit_mark()
        c, w = self._thr()
        if self.sym and g >= c:
            self._tecken(self.sym, out)
            self.sym = ""
            self.space_done = False
        if self.ordet:
            self.ordet[-1][1] = g            # pausen efter tecknet (växer tills den tar slut)
        if not self.sym and not self.space_done and g >= w:
            self._ord_slut(out)
            self.space_done = True
        if self.GRUPP and self.vantar and g >= 2.5 * w:     # sändningen tog slut: ingen fortsättning
            out.append(self._grupp_text(self.vantar) + " ")
            self.vantar = []

    # ── tecken och ord ut ─────────────────────────────────────────────
    GRUPP = None       # träningsgrupper: antal tecken per grupp (None = vanlig text)

    def _tecken(self, sym, out):
        if self.GRUPP:
            self.ordet.append([sym, 0.0])    # skrivs när gruppen är slut (kan behöva lagas)
            if len(self.ordet) > 2 * self.GRUPP:
                self._ord_slut(out, mellanslag=False)
        else:
            out.append(MORSE.get(sym, "*"))

    def _ord_slut(self, out, mellanslag=True):
        if not self.GRUPP:
            if mellanslag:
                out.append(" ")
            return
        cur, self.ordet = self.ordet, []
        v = self.vantar
        # en grupp som fädning delat (ett teckenmellanrum som blev ett ordmellanrum): två
        # grupper som tillsammans har högst GRUPP tecken hör ihop
        if v and cur and len(v) + len(cur) <= self.GRUPP:
            self.vantar = v + cur
            cur = []
        elif cur:
            if v:
                out.append(self._grupp_text(v) + " ")
            self.vantar = cur
        if self.vantar and (len(self.vantar) >= self.GRUPP or not mellanslag):
            out.append(self._grupp_text(self.vantar) + (" " if mellanslag else ""))
            self.vantar = []

    def _grupp_text(self, t):
        return "".join(MORSE.get(x, "*") for x in laga_grupp(t, self.GRUPP))

    def _commit_mark(self):
        if self.pend is None:
            return
        d = self.pend
        self.pend = None
        self.marks.append(d)
        self._learn()
        self.sym += "." if d < self._mark_thr() else "-"
        if len(self.sym) > 8:              # orimligt långt tecken – troligen fel takt
            self.sym = self.sym[-8:]

    # ── 3. Klassning: adaptiva gränser ur tidsavstånden ───────────────
    def _mark_thr(self):
        return math.sqrt(self.dot * self.dash) if self.dash else 2 * self.dot

    def _thr(self):
        """(gräns tecken, gräns ord) för pauser, i sekunder."""
        u = self.dot
        if not self.gaps:
            return 2 * u, 5 * u
        e, c, w = self.gaps
        ct = math.sqrt(e * c) if c / e >= 1.7 else 2 * e
        ct = min(max(ct, 1.6 * u), 4.5 * u if c / e < 1.7 else ct)
        wt = math.sqrt(c * w) if w / c >= 1.5 else max(ct * 2.2, 5 * u)
        wt = max(wt, ct * 1.6)
        return ct, wt

    def _learn(self):
        """Lär om takten ur de senaste tonerna och pauserna. Sant när den är känd."""
        x = np.asarray(self.marks, float)
        x = x[x > self.min_el]
        if len(x) < 6:
            return self.dot is not None
        lx = np.log(x)
        (c1, c2), cnt = _kmeans1d(lx, (np.percentile(lx, 10), np.percentile(lx, 90)))
        sp = np.asarray(self.spaces, float)
        sp = sp[sp > self.min_el]
        if c2 - c1 > math.log(1.8) and min(cnt) >= 2:          # både prickar och streck
            dot, dash = math.exp(c1), math.exp(c2)
        else:
            # bara en sorts toner hittills: jämför med de kortaste pauserna (1 enhet)
            m = math.exp(float(np.median(lx)))
            u = float(np.percentile(sp, 20)) if len(sp) >= 4 else None
            if self.dot and self.dash:
                dot, dash = self.dot, self.dash
            elif u and m > 2 * u:
                dot, dash = m / 3, m
            else:
                dot, dash = m, None
        if self.dot is None and len(sp) < 3 and dash is None:
            return False
        self.dot, self.dash = dot, dash
        if len(sp) >= 6:
            ls = np.log(sp)
            # pauserna inom tecknet ligger nära prickens längd; de längre delas i tecken/ord
            sm, big = ls[ls <= math.log(1.8 * dot)], ls[ls > math.log(1.8 * dot)]
            e = math.exp(float(np.median(sm))) if len(sm) else dot
            if len(big) >= 3:
                (c1, c2), cnt = _kmeans1d(big, (np.percentile(big, 10), np.percentile(big, 95)))
                cc, w = math.exp(c1), math.exp(c2)
                if w / cc < 1.6 or min(cnt) == 0:     # inga ordmellanrum sedda (eller bara de)
                    cc = math.exp(float(np.median(big)))
                    w = 7 / 3 * cc
            else:
                cc, w = 3 * e, 7 * e
            self.gaps = (min(e, 1.5 * dot), cc, w)
        # en prick + en kort paus = två enheter, oberoende av om tröskeln förlänger tonerna
        u = (dot + self.gaps[0]) / 2 if self.gaps else dot
        self.unit = u
        self.wpm = 1.2 / u
        self.fw = None
        if self.gaps and self.gaps[1] > 4.5 * u:
            # Farnsworth: teckenmellanrummen motsvarar en lägre takt
            self.fw = 1.2 / (self.gaps[1] / 3)
        return True

    def varden(self):
        if self.q is not None and self.q < self.GATE:
            return [("Morse", "ingen tydlig nyckling")]
        if not self.wpm:
            return [("Morse", "lär sig takten …")]
        v = [("Morse", f"{self.wpm:.0f} ord/min" + (f" (Farnsworth ~{self.fw:.0f})" if self.fw else ""))]
        if self.dash:
            v.append(("Prick:streck", f"1:{self.dash / self.dot:.1f}"))
        return v


class CwGruppAvkodare(CwAvkodare):
    """Morse med träningsgrupper om fem tecken (t.ex. FAV22): grupper med fel antal tecken
    lagas (ett tecken som fädning delat i två slås ihop). Väljs i avkodarlistan, aldrig
    automatiskt – i vanlig text skulle lagningen göra fel."""
    namn = "Morse – träningsgrupper (5 tecken)"
    GRUPP = 5

    def passar(self, feat):
        return False


AVKODARE = [RttyAvkodare, CwAvkodare, CwGruppAvkodare]   # fler läggs till längst ner (analysavkodare.py)


# ════════════════════════════════════════════════════════════════════
# Arbetstråd
# ════════════════════════════════════════════════════════════════════
class Analyzer:
    LOCK_SCORE = 55.0          # lägsta poäng för att en kandidat får låsas
    LOCK_STEPS = 3             # antal mätningar i rad innan låsning
    LOCK_SWITCH_STEPS = 6      # en annan kandidat måste ligga högst så länge för att ta över
    LOCK_HOLD = 2.5            # sekunder som låset hålls kvar när signalen tappas
    SYNK_HALL = 30.0           # sekunder en avkodare med egen synk matas utan att klassningen passar
    KNASTER = 8.0              # mätningen: sampel över KNASTER × normal nivå (korta pulser) nollas

    def __init__(self, sr, user_db=None):
        self.tap = ChannelTap(sr)
        self.db = signaldb.load(user_db)
        self.result = None
        self.result_id = 0
        self.dial = None          # GUI: inställd frekvens och modulering (för ljudfrekvens i USB/LSB)
        self.mode = None
        self.dec = [k() for k in AVKODARE]
        self.text_q = deque(maxlen=200)
        self.msg_q = deque(maxlen=200)     # hela meddelanden till fliken Avkodat
        self.tvinga = None                 # namn på avkodare som alltid ska köras (None = automatiskt)
        self._gen = None
        self._ptr = 0
        self._last_total = -1
        self._stop = threading.Event()
        self._thr = None
        self.paused = False
        self.signal_lock = None            # stabil signaltyp: {"namn", "post", "score", "last"}
        self._lock_top = None
        self._lock_top_n = 0
        # Stabil signalfamilj: rå klassning varierar mellan mätningar (t.ex. FSK2 ↔ MFSK vid svag signal).
        # Ny familj visas först när den setts FAM_BYT av de senaste FAM_FONSTER gångerna, brus efter FAM_BRUS.
        self._fam_hist = deque(maxlen=self.FAM_FONSTER)
        self._ram_hist = deque(maxlen=8)       # ramperioder från de senaste stegen (None = ingen)
        self.fit = None                        # automatisk bandbredd, se set_target
        self._stable_fam = None
        # bevismotorn (bevis.py): samlar bevis över tid och bestämmer vad som visas
        self.motor = bevis.Bevismotor()
        self.ny_motor = True
        self.kontext = None                    # f_abs -> [(hypotes, poäng, förklaring)], t.ex. flaggor
        self._tal = None
        self._ram3 = deque(maxlen=5)           # modemram över 3 s per steg (None = ingen)

    FAM_FONSTER = 5
    FAM_BYT = 3
    FAM_BRUS = 2

    def _lagbaud_steg(self, feat, tgt):
        """FT8/FT4: kräver flera sekunder (6,25 resp. 20,8 symboler/s) – egen detektor på upp till 4 s."""
        if not hasattr(self, "_lagbaud"):
            self._lagbaud = deque(maxlen=6)
        r, provad = None, False
        klick = getattr(self, "klick", tgt)
        if self.ny_motor and not (bevis.ft_tillaten("FT8", klick) or bevis.ft_tillaten("FT4", klick)):
            return                             # FT8/FT4 sänds bara i sina delband – inga falsklarm på tal
        # även vid BRUS/BÄRVÅG: FT4/FT8 sänder i omgångar, och i en smal kanal ser en ton ut som en bärvåg
        if feat.get("fam") != "AM" and feat.get("occ_bw", 0) < 8000:
            snap = self.tap.snapshot(4.0)
            if snap is not None and len(snap[0]) >= snap[1] * 2.5:
                provad = True
                r = lagbaud_mfsk(snap[0], snap[1], getattr(self, "klick", tgt) - tgt)
        if provad:                             # för lite data (kanalen nyss bytt): historiken står kvar
            self._lagbaud.append(r["namn"] if r else None)
        namn = [x for x in self._lagbaud if x]
        if namn and (r or len(namn) >= 2):
            flest = max(set(namn), key=namn.count)     # FT8 eller FT4 efter majoritet
            best = next(dict(namn=x[0], baud=x[1], toner=x[2], fam=x[3]) for x in LAGBAUD_MFSK if x[0] == flest)
            feat.update(fam=best["fam"], klass=FAM_NAMN[best["fam"]] + f" ({best['namn']})",
                        baud=best["baud"], shift=best["baud"], toner=best["toner"], lagbaud=best["namn"])
            feat["occ_bw"] = best["baud"] * best["toner"]
            feat["bredd_uppmatt"] = feat["occ_bw"]      # detektorns bredd gäller före kanalens
            feat["f_mid"] = getattr(self, "klick", tgt) - tgt
            feat["f_lo"] = feat["f_mid"] - feat["occ_bw"] / 2      # kanterna följer detektorns bredd
            feat["f_hi"] = feat["f_mid"] + feat["occ_bw"] / 2
            # sedd minst två gånger: smalna av kanalen kring klicket (bättre SNR, rätt bredd i panelen)
            ft = self.fit
            if len(namn) >= 2 and ft is not None and ft.get("state") == "klar" and not ft.get("lagbaud"):
                bredd = best["baud"] * best["toner"]
                ft.update(lagbaud=best["namn"], width=bredd)
                self.tap.set_target(getattr(self, "klick", tgt), max(300.0, bredd * 3))
                log.info("Signalanalys: %s – kanalen smalnas till %.0f Hz", best["namn"], max(300.0, bredd * 3))

    def _stabil_ram(self, feat):
        """En ramperiod som setts minst 3 av de 8 senaste stegen gäller även i steg där den inte syns
        (svag signal, grannsignaler) – annars hoppar förslaget mellan t.ex. STANAG 4285 och något annat."""
        rm = feat.get("ram_ms")
        self._ram_hist.append(rm)
        vals = [x for x in self._ram_hist if x]
        if not vals:
            return feat
        ref = float(np.median(vals))
        lika = [x for x in vals if abs(x - ref) / ref < 0.015]
        if len(lika) >= 3:
            tal = feat.get("fam") == "SSB" and (feat.get("ljudvariation") or 0.0) >= 1.5
            if feat.get("fam") in ("FM", "BRUS", "SSB", "OKÄND") and not tal:
                feat["fam"] = "LIN"
                feat["klass"] = FAM_NAMN["LIN"]
            if feat.get("fam") in ("LIN", "PSK8", "QPSK", "BPSK"):
                feat["ram_ms"] = float(np.median(lika))
                if not rm:
                    feat["ram_minne"] = True
        return feat

    OSAKER_GRANS = 0.55

    def _sakerhet(self, feat):
        """Hur säker klassningen är (0–1): stabil familj över de senaste stegen (50 %), SNR (30 %) och hur
        väl förslaget ur databasen passar (20 %). Under OSAKER_GRANS visas "osäker" och operatören får klassa."""
        fam = feat.get("fam")
        if fam in (None, "BRUS"):
            feat["sakerhet"], feat["osaker"] = None, False
            return
        hist = list(self._fam_hist)
        stab = sum(1 for x in hist if x == fam) / max(1, len(hist))
        snr_f = min(1.0, max(0.0, ((feat.get("snr") or 0.0) - 3.0) / 12.0))
        k = feat.get("kandidat")
        # förslagets andel av de två främsta poängen: två lika starka typer ger 50 %, inte 100 %
        andra = max((x[0] for x in (feat.get("kandidater") or [])
                     if not k or x[1].get("namn") != k[1].get("namn")), default=0)
        andel = k[0] / (k[0] + andra) if k and k[0] > 0 else 0.0
        kand_f = min(1.0, andel) if k else 0.5
        s = 0.5 * stab + 0.3 * snr_f + 0.2 * kand_f
        if k and andel < 0.8:
            s = min(s, 0.8)
        # starka bevis utöver familjens stabilitet: FT8/FT4-detektorn (symboltakt och tonraster stämmer),
        # ett låst förslag med hög poäng, eller en avkodare som får ut text
        if feat.get("lagbaud"):
            s = max(s, 0.85)
        if feat.get("kandidat_las") and k and k[0] >= 80 and andel >= 0.8:
            s = max(s, 0.7)
        if feat.get("avk_varden"):
            s = max(s, 0.75)
        if fam == "OKÄND":
            s = min(s, 0.3)
        feat["sakerhet"] = round(s, 3)
        feat["osaker"] = s < self.OSAKER_GRANS

    def _stabil_familj(self, feat):
        """Byter feat["fam"]/["klass"] till den stabila familjen (hysteres mot hopp mellan mätningar)."""
        raw = feat.get("fam")
        if raw in ("FSK2", "FSK4", "MSK", "MFSK") and feat.get("shift"):
            self._fsk_mem = (raw, feat.get("baud"), feat.get("shift"), feat.get("toner"))
        self._fam_hist.append(raw)
        most, cnt = Counter(self._fam_hist).most_common(1)[0]
        if most == "BRUS":
            if cnt >= self.FAM_BRUS:
                self._stable_fam = "BRUS"
        elif most != self._stable_fam and cnt >= self.FAM_BYT:
            self._stable_fam = most
        if self._stable_fam is not None and self._stable_fam != raw:
            feat["fam_ra"] = raw                  # det den senaste mätningen själv sa
            feat["fam"] = self._stable_fam
            feat["klass"] = FAM_NAMN.get(self._stable_fam, feat["klass"])
            mem = getattr(self, "_fsk_mem", None)
            if mem and mem[0] == self._stable_fam:
                if not feat.get("baud") and mem[1]:
                    feat["baud"] = mem[1]
                if not feat.get("shift") and mem[2]:
                    feat["shift"] = mem[2]
                if not feat.get("toner") and mem[3]:
                    feat["toner"] = mem[3]
        return feat

    def start(self):
        if self._thr is None:
            self._thr = threading.Thread(target=self._loop, name="Analys", daemon=True)
            self._thr.start()

    def stop(self):
        self._stop.set()

    # Automatisk bandbredd: först en bred överblick, sedan smalnas kanalen av till signalens bredd.
    FIT_MIN_SURVEY = 10000.0
    FIT_MAX_SURVEY = 250e3
    FIT_MAX_TRIES = 3

    def set_target(self, f, bw, auto=False, flagga=False):
        """auto=True: bw är bara en första gissning – analysen mäter själv signalens bredd.
        flagga=True (med auto): f/bw är en flaggas kanal. Den gäller tills mätningen visar något
        tydligt annat (se _flagga_eller_matt)."""
        self.fit = None
        self.klick = float(f)                  # där användaren klickade (kanalens mitt kan flyttas)
        self._lagbaud = deque(maxlen=6)
        if auto:
            cap = min(self.FIT_MAX_SURVEY, self.tap.sr * 0.9)
            survey = float(np.clip(max(bw * 3.0, self.FIT_MIN_SURVEY), 1000.0, cap))
            # snabbt resultat med gissningen först; bandbreddsmätningen startar när det har visats
            self.fit = {"state": "prelim", "guess": float(bw), "survey": survey, "tries": 0,
                        "cap": cap, "result": None, "flagga": (float(f), float(bw)) if flagga else None}
        self.tap.set_target(f, bw)
        self.result = None
        self.result_id += 1
        self._reset_lock()

    def _fit_step(self, z, fs, tgt):
        """Ett steg i bandbreddsmätningen. Returnerar True medan mätningen pågår."""
        ft = self.fit
        if ft is None or ft["state"] != "survey":
            return False
        snap = self.tap.snapshot(2.0)            # 2 s: stadigare brusgolv och sidband (tal varierar)
        if snap is None or len(snap[0]) < fs * 1.8:
            return True
        z = _knasterspärr(snap[0], self.KNASTER)    # knaster flyttar annars mitten (t.ex. till en FSK-ton)
        ft["tries"] += 1
        lf = tgt < 500e3          # VLF/LF: brusgolvet lutar kraftigt (atmosfäriskt brus faller med frekvensen)
        r = fit_bandwidth(z, fs, ft["survey"], near_hz=max(200.0, ft["guess"] * 0.5), lutning=lf)
        if r is None or r[1] < 150.0:
            # klick mellan FSK-tonerna (shift upp till ca 1,2 kHz): bandbreddsmätningen delar vid svackan
            # mellan tonerna och ser ingen signal vid klicket – eller hittar bara den ena tonen (svag RTTY)
            # när en smal signal redan hittats måste tonerna turas om – annars är det en CW-station och
            # dess granne, inte ett FSK-par
            rp = _fsk_par(z, fs, ft["survey"], vaxling=r is not None)
            if rp is not None and (r is None or abs(abs(r[0] - rp[0]) - (rp[1] - 150.0) / 2) < 60.0):
                r = rp
        if ft.get("flagga"):
            # även precis vid klicket (som ett vanligt klick): en smal signal där ska inte drunkna i en
            # mätning över hela kanalen, som tar med grannsignaler
            rc = fit_bandwidth(z, fs, ft["survey"], near_hz=200.0, lutning=lf)
            par = _fsk_par(z, fs, ft["survey"])
            return self._flagga_eller_matt(ft, r, rc, par, tgt, bevis.narvaro(z, fs))
        if r is not None and r[2] and ft["survey"] < ft["cap"] and ft["tries"] < self.FIT_MAX_TRIES:
            ft["survey"] = min(ft["cap"], ft["survey"] * 3.0)      # signalen är bredare än överblicken
            self.tap.set_target(tgt, ft["survey"])
            log.info("Signalanalys: bredare överblick %.0f Hz", ft["survey"])
            return True
        if r is None and ft["tries"] < self.FIT_MAX_TRIES:
            self.tap.set_target(tgt, ft["survey"])        # ingen signal just nu (paus i en skur) – försök igen
            return True
        if r is None:
            new_f, new_bw = tgt, float(np.clip(ft["guess"] * 1.35, 400.0, ft["cap"]))
            log.info("Signalanalys: ingen tydlig signal i överblicken – %.0f Hz", new_bw)
        else:
            mid, width, _k = r
            new_f = tgt + mid
            new_bw = float(np.clip(max(width * 1.25, width + 150.0), 300.0, ft["cap"]))
            log.info("Signalanalys: uppmätt bandbredd %.0f Hz (kanal %.0f Hz, mitt %+.0f Hz)", width, new_bw, mid)
        ft.update(state="klar", result=r, width=(r[1] if r else None))
        self.tap.set_target(new_f, new_bw)
        return True

    def _flagga_eller_matt(self, ft, r, rc, par, tgt, narv=None):
        """Flaggans kanal mot mätningen. Mätningen vinner bara när den tydligt visar något annat:
          • en smal signal: under 25 % av kanalen (CW, PSK31, FT8), eller under 45 % när det är ett tydligt
            FSK-par (RTTY, 450–850 Hz shift) eller när mätningen över kanalen inte blir bredare än vid klicket
          • en mycket bredare signal: över 2,5 gånger kanalen (AM ≈ 3,7 gånger; SSB-tal med grannsignaler
            mäts ibland till 2 gånger)
        Tal mäts ofta till en formant eller en bit av bandet (0,9–2,7 kHz) – då, och när inget syns, gäller
        flaggans kanal. r = mätning över kanalen, rc = mätning vid klicket, par = FSK-par vid klicket."""
        fc, fw = ft["flagga"]
        val = None
        for kand in (par, rc, r):
            if kand is None:
                continue
            if abs(tgt + kand[0] - fc) > 300.0:
                continue            # en smal topp någonstans i kanalen (bärvåg, störning) är inte flaggans signal
            if kand[1] < 0.25 * fw or (kand is par and kand[1] < 0.45 * fw):
                val = kand
                break
            if kand is rc and kand[1] < 0.45 * fw and r is not None and r[1] <= 1.3 * kand[1] \
                    and narv is not None and narv < 1.8:
                # hela kanalen innehåller bara den smala signalen, och nivån är jämn (RTTY ≈ 1,3) – inte en
                # talpaus, där bara en formant syns (tal varierar i nivå: 2,5 och mer)
                val = kand
                break
        if val is None and r is not None and r[1] > 2.5 * fw:
            val = r
        if val is not None:
            mid, width = tgt + val[0], val[1]
            new_bw = float(np.clip(max(width * 1.25, width + 150.0), 300.0, ft["cap"]))
            log.info("Signalanalys: flaggans kanal %.0f Hz stämmer inte – uppmätt %.0f Hz (mitt %+.0f Hz)",
                     fw, width, mid - fc)
            ft.update(state="klar", result=val, width=width)
            self.tap.set_target(mid, new_bw)
        else:
            log.info("Signalanalys: flaggans kanal %.0f Hz gäller (uppmätt %s)", fw,
                     "ingenting" if r is None else f"{r[1]:.0f} Hz")
            ft.update(state="klar", result=(fc - tgt, fw, False), width=fw, bredd_fran="flagga")
            self.tap.set_target(fc, fw)
        return True

    def _reset_lock(self):
        self.signal_lock = None
        self._lock_top = None
        self._lock_top_n = 0
        self._fam_hist.clear()
        self._stable_fam = None
        if hasattr(self, "motor"):
            self.motor.nollstall()
        self._tal = None
        if hasattr(self, "_ram3"):
            self._ram3.clear()
        self._ram_hist.clear()
        self._fsk_mem = None

    def _lock_candidate(self, feat, ks, now):
        """Lås visningen till en stabil signaltyp i stället för att listan hoppar mellan likvärdiga förslag."""
        top = ks[0] if ks and ks[0][0] >= self.LOCK_SCORE else None
        name = top[1].get("namn") if top else None
        if name != self._lock_top:
            self._lock_top = name
            self._lock_top_n = 0
        if name:
            self._lock_top_n += 1

        lock = self.signal_lock
        if lock is None:
            if top and self._lock_top_n >= self.LOCK_STEPS:
                self.signal_lock = {"namn": name, "post": top[1], "score": top[0], "last": now}
                log.info("Signalanalys: låst till %s (%.0f %%)", name, top[0])
        else:
            cur = next((x for x in ks if x[1].get("namn") == lock["namn"]), None)
            if cur is not None and cur[0] >= self.LOCK_SCORE - 10:
                lock["score"] = cur[0]
                lock["last"] = now
            # den låsta typen passar inte längre alls (t.ex. "SSB-tal" när mätningen visar FSK): byt snabbare
            behov = self.LOCK_STEPS if cur is None else self.LOCK_SWITCH_STEPS
            if name and name != lock["namn"] and self._lock_top_n >= behov:
                # En ny typ har legat högst tillräckligt länge – följ den i stället.
                self.signal_lock = {"namn": name, "post": top[1], "score": top[0], "last": now}
                log.info("Signalanalys: byter lås till %s (%.0f %%)", name, top[0])
            elif now - lock["last"] > self.LOCK_HOLD:
                log.info("Signalanalys: släpper låset på %s", lock["namn"])
                self.signal_lock = None

        lock = self.signal_lock
        if lock is not None:
            cur = next((x for x in ks if x[1].get("namn") == lock["namn"]), None)
            feat["kandidat"] = cur or (lock["score"], lock["post"], "")
            feat["kandidat_las"] = True
        else:
            feat["kandidat"] = top or (ks[0] if ks else None)
            feat["kandidat_las"] = False

    def step(self, now=None):
        """Ett analyssteg: mät, jämför med databasen och kör avkodarna på nya samples.
        Returnerar (feat, ny text) eller None om inget nytt finns."""
        snap = self.tap.snapshot(1.0 if (self.tap.fs or 0) <= 100e3 else 0.4)
        if snap is None:
            return None
        z, fs, tgt, bw, gen, total = snap
        if total == self._last_total or len(z) < fs * 0.25:
            return None
        z = _knasterspärr(z, self.KNASTER)          # knaster förstör både bredden och mätningen
        if self._fit_step(z, fs, tgt):
            return None
        self._last_total = total
        now = time.monotonic() if now is None else now
        if gen != self._gen:
            self._gen = gen
            self._ptr = total
            self._reset_lock()
            for dd in self.dec:
                dd.reset()
                dd._ok_feat = None
                dd._ptr = None
        feat = analyze(z, fs, bw)
        self._stabil_familj(feat)
        self._stabil_ram(feat)
        if feat.get("ram_ms") and feat.get("ram_raster") is None:
            feat["ram_raster"] = _ram_raster(z, fs, feat["ram_ms"], feat.get("baud"))
        self._lagbaud_steg(feat, tgt)
        f_abs = tgt + feat.get("f_mid", 0.0)
        feat["f_abs"] = f_abs
        feat["target"] = tgt
        feat["klick_off"] = getattr(self, "klick", tgt) - tgt     # där användaren klickade, rel. kanalens mitt
        ac = None
        if self.dial is not None and self.mode in ("USB", "LSB") and feat.get("fam") != "BRUS":
            ac = (f_abs - self.dial) if self.mode == "USB" else (self.dial - f_abs)
            if not (150 < ac < 4000):
                ac = None
        feat["ljud_mitt"] = ac
        if self.fit and self.fit.get("width"):
            feat.setdefault("bredd_uppmatt", self.fit["width"])   # stabil (mätt över 2 s), till signaldatabasen
        feat["kandidater"] = signaldb.match(feat, f_abs, self.db, ac)
        zn, self._ptr = self.tap.since(self._ptr)
        vals, new = [], ""
        locked = [dd for dd in self.dec if dd.har_synk() and getattr(dd, "_ok_feat", None) is not None
                  and now - dd._ok_t < self.SYNK_HALL]
        if locked:
            # flera med synk: den med strängast kontroll går före (NAVTEX:s FEC och 4-av-7-kod före
            # RTTY, som ramar nästan rätt även på SITOR-B 100 Bd/170 Hz)
            pmax = max(getattr(dd, "PRIORITET", 0) for dd in locked)
            locked = [dd for dd in locked if getattr(dd, "PRIORITET", 0) == pmax]
        passar_namn, kord = [], []
        for dd in self.dec:
            try:
                if dd.passar(feat):
                    passar_namn.append(dd.namn)
            except Exception:
                pass
        for dd in self.dec:
            if locked and dd not in locked and self.tvinga is None \
                    and getattr(dd, "PRIORITET", 0) <= getattr(locked[0], "PRIORITET", 0):
                continue                 # en avkodare med högre prioritet får ändå försöka hitta synk
            use = None
            if self.tvinga is not None:
                if dd.namn != self.tvinga:
                    continue
                if feat.get("fam") != "BRUS":
                    use = feat
            elif dd.passar(feat):
                dd._ok_t, dd._ok_feat = now, feat
                use = feat
            elif getattr(dd, "_ok_feat", None) is not None and feat.get("fam") != "BRUS" and (
                    now - dd._ok_t < 4 or (dd.har_synk() and now - dd._ok_t < self.SYNK_HALL)):
                # en enstaka missad mätning ska inte bryta avkodningen – och en avkodare som har egen
                # synk matas vidare även när störningar (knaster) förvirrar klassningen en längre stund
                use = dd._ok_feat
            if use is not None and zn is not None:
                # varje avkodare har en egen läspekare: när den startar får den hela ringbufferten
                # (ca 4 s), så att början av en sändning inte går förlorad medan klassningen kommer ikapp
                if getattr(dd, "_ptr", None) is not None and self.tap.ring is not None \
                        and self.tap.total - dd._ptr > len(self.tap.ring):
                    dd.reset()                   # data har gått förlorad (lång paus): börja om, annars
                    dd._ptr = None               # läser avkodaren över hoppet i bitströmmen
                zd, dd._ptr = self.tap.since(dd._ptr if getattr(dd, "_ptr", None) is not None else -10 ** 12)
                if zd is None or not len(zd):
                    continue
                txt = dd.feed(zd, fs, use)
                kord.append(dd.namn)
                if txt:
                    self.text_q.append((dd.namn, txt))
                    new += txt
                vals += dd.varden()
                feat["avkodare"] = dd.namn
                if dd.har_synk():
                    fk = dd.fakta()
                    if fk and feat.get("msk") and fk.get("fam") in ("BPSK", "QPSK", "PSK8"):
                        fk = None            # MSK är sett i z²: en PSK-avkodares synk är falsk
                    if fk:
                        feat.update(fk)
                        feat["fakta_fran"] = dd.namn
                for m in getattr(dd, "msgs", [])[:]:
                    m["freq"] = f_abs
                    m.setdefault("level", feat.get("snr", 0.0))
                    m.setdefault("details", [("Avkodare", dd.namn), ("SNR", f"{feat.get('snr', 0):.1f} dB")])
                    self.msg_q.append(m)
                if getattr(dd, "msgs", None):
                    dd.msgs.clear()
                if getattr(dd, "bild", None) is not None:
                    feat["bild"] = dd
        feat["avk_varden"] = vals
        if feat.get("fakta_fran"):
            feat["kandidater"] = signaldb.match(feat, f_abs, self.db, ac)
        self._lock_candidate(feat, feat.get("kandidater") or [], now)
        self._sakerhet(feat)
        if self.ny_motor:
            self._motor_steg(feat, f_abs, now)
        baud, shift, bw = feat.get("baud"), feat.get("shift"), feat.get("occ_bw")
        kand = feat.get("kandidat")
        kand_namn = kand[1].get("namn") if kand and isinstance(kand[1], dict) else "–"
        vis = feat.get("visad") or {}
        if vis:
            log.info("Bevismotor: visar %s (%.0f %%), bäst nu %s, topp %s, grund %s, tal %s", vis.get("fam"),
                     100 * (vis.get("sakerhet") or 0), vis.get("bast"),
                     ", ".join(f"{h} {100 * q:.0f}" for h, q in vis.get("topp", [])),
                     "; ".join(vis.get("grund", [])) or "–",
                     f"{feat['tal']:.2f}" if feat.get("tal") is not None else "–")
        log.info("Analyssteg %.6f MHz: uppmätt %s / %s, %s Bd, shift %s Hz, bandbredd %s Hz, kandidat %s (%s), "
                 "passar %s, avkodare %s, text %d tecken",
                 f_abs / 1e6, feat.get("fam"), feat.get("klass"),
                 f"{baud:.2f}" if baud else "–", f"{shift:.0f}" if shift else "–", f"{bw:.0f}" if bw else "–",
                 kand_namn, "låst" if feat.get("kandidat_las") else "ej låst",
                 ", ".join(passar_namn) or "–", ", ".join(kord) or "–", len(new))
        self.result = feat
        self.result_id += 1
        if self.fit and self.fit.get("state") == "prelim":
            self.fit["state"] = "survey"
            self.tap.set_target(tgt, self.fit["survey"])
        return feat, new

    def _motor_steg(self, feat, f_abs, now):
        """Kör bevismotorn och lägger resultatet i feat["visad"] (det panelen visar)."""
        ft = self.fit
        if ft is not None and ft.get("state") != "klar":
            return                             # kanalen mäts fortfarande – stegen gäller fel bandbredd
        if ft is not None and not ft.get("_motor"):
            ft["_motor"] = True                # första steget på den färdiga kanalen: börja om
            self.motor.nollstall()
            self._tal = None
            self._ram3.clear()
        snap = self.tap.snapshot(3.0)
        narv = None
        if snap is not None and len(snap[0]) >= snap[1] * 2.0:
            m, _d = bevis.tal_matt(snap[0], snap[1])
            if m is not None:
                self._tal = m if self._tal is None else 0.6 * self._tal + 0.4 * m
            narv = bevis.narvaro(snap[0], snap[1], self.tap.bw)
            # nycklad bärvåg över 3 s (RWM:s sekundpulser syns sällan inom ett steg på 0,4 s)
            # 50 ms-rutor: PSK31:s korta amplituddippar (var 32:a ms) och FSK-tonernas växlingar är ingen
            # nyckling – bärvågen ska vara av i längre stunder och bära minst halva effekten
            # (inte heller när FSK nyss mätts: en fädad FSK-ton ser ut som en ensam nycklad bärvåg)
            if (feat.get("barvag_andel") or 0.0) >= 0.5 and not feat.get("shift") \
                    and not getattr(self, "_fsk_mem", None):
                nv = _nycklad(snap[0], snap[1], _barvag_exakt(snap[0], snap[1], float(feat.get("f_mid") or 0.0)),
                              ruta_s=0.05)
                feat["nycklad"], feat["delvis_nycklad"] = nv == 2, nv == 1
            # modemram över 3 s (STANAG 4285: 106,7 ms) – syns i brus där ett steg på 0,4 s inte räcker.
            # Bara ≥ 50 ms: talets grundton ger skenbara perioder på 20–35 ms
            if (self.tap.bw or 0) >= 1500:
                rm = _ramperiod(snap[0], snap[1], lo_ms=50.0, min_r=0.07, min_kvot=4.0)
                self._ram3.append(rm[0] if rm else None)
        vals = [x for x in self._ram3 if x]
        if vals:
            ref = float(np.median(vals))
            lika = [x for x in vals if abs(x - ref) / ref < 0.015]
            if len(lika) >= 2:
                feat["ram3"] = float(np.median(lika))
                if not feat.get("ram_ms"):
                    feat["ram_ms"], feat["ram_minne"] = feat["ram3"], True
        feat["tal"] = self._tal
        feat["narvaro"] = narv
        bredd = ft.get("width") if ft else None
        try:
            kontext = self.kontext(f_abs) if callable(self.kontext) else ()
        except Exception:
            kontext = ()
        sedd = bool(ft and ft.get("result") is not None and ft.get("bredd_fran") != "flagga")
        v = self.motor.steg(feat, f_abs, now, bredd=bredd, tal=self._tal, ft=feat.get("lagbaud"),
                            kontext=kontext, narv=narv, sedd=sedd)
        fam = v["fam"]
        if fam is None:
            return
        namn = {"FT8": FAM_NAMN["MFSK"] + " (FT8)", "FT4": FAM_NAMN["FSK4"] + " (FT4)"}.get(fam) or FAM_NAMN.get(fam, fam)
        feat["visad"] = {"fam": fam, "klass": namn, "sakerhet": v["p"], "osaker": v["p"] < self.OSAKER_GRANS,
                         "bast": v["bast"], "topp": v["topp"], "grund": v["grund"]}

    def _loop(self):
        import logging
        log = logging.getLogger("signalspanaren")
        while not self._stop.wait(0.35):
            if self.paused:
                continue
            try:
                self.step()
            except Exception:
                log.exception("Fel i signalanalysen")
                time.sleep(1)


# ════════════════════════════════════════════════════════════════════
# Testsignaler (simulatorn och självtestet)
# ════════════════════════════════════════════════════════════════════
def _rrc(beta, sps, span=8):
    n = np.arange(-span * sps, span * sps + 1) / sps
    h = np.zeros_like(n)
    for i, t in enumerate(n):
        if abs(t) < 1e-9:
            h[i] = 1 - beta + 4 * beta / np.pi
        elif beta and abs(abs(4 * beta * t) - 1) < 1e-9:
            h[i] = beta / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * beta)) +
                                        (1 - 2 / np.pi) * np.cos(np.pi / (4 * beta)))
        else:
            h[i] = (np.sin(np.pi * t * (1 - beta)) + 4 * beta * t * np.cos(np.pi * t * (1 + beta))) / \
                   (np.pi * t * (1 - (4 * beta * t) ** 2))
    return h / np.sqrt((h ** 2).sum())


def make_psk(fs, baud, M, seconds, beta=0.35, seed=1):
    rng = np.random.default_rng(seed)
    sps = int(round(fs / baud))
    nsym = int(seconds * baud)
    sym = np.exp(2j * np.pi * rng.integers(0, M, nsym) / M)
    up = np.zeros(nsym * sps, np.complex128)
    up[::sps] = sym
    y = np.convolve(up, _rrc(beta, sps), mode="same")
    return (y / np.sqrt(np.mean(np.abs(y) ** 2))).astype(np.complex64)


def baudot_bits(text):
    rev_l = {c: i for i, c in enumerate(ITA2_LTRS)}
    rev_f = {c: i for i, c in enumerate(ITA2_FIGS)}
    bits = [1] * 20
    figs = False
    for ch in text.upper():
        if ch in rev_l and ch not in "\0\x0e\x0f":
            if figs and ch != " ":
                bits += [0] + [(0x1F >> k) & 1 for k in range(5)] + [1, 1]
                figs = False
            code = rev_l[ch]
        elif ch in rev_f:
            if not figs:
                bits += [0] + [(0x1B >> k) & 1 for k in range(5)] + [1, 1]
                figs = True
            code = rev_f[ch]
        else:
            continue
        bits += [0] + [(code >> k) & 1 for k in range(5)] + [1, 1]
        if ch == " ":
            figs = False          # mottagaren växlar till bokstäver vid mellanslag
    return bits + [1] * 10


def make_fsk(fs, baud, shift, bits, mark_high=True):
    sps = fs / baud
    n = int(len(bits) * sps)
    idx = np.minimum((np.arange(n) / sps).astype(np.int64), len(bits) - 1)
    b = np.asarray(bits)[idx]
    f = np.where(b == 1, shift / 2, -shift / 2) * (1 if mark_high else -1)
    ph = 2 * np.pi * np.cumsum(f) / fs
    return np.exp(1j * ph).astype(np.complex64)


def make_rtty(fs, baud, shift, text, mark_high=True):
    return make_fsk(fs, baud, shift, baudot_bits(text), mark_high)


def make_mfsk(fs, baud, tones, seconds, seed=2):
    rng = np.random.default_rng(seed)
    nsym = int(seconds * baud)
    sps = fs / baud
    s = rng.integers(0, len(tones), nsym)
    n = int(nsym * sps)
    idx = np.minimum((np.arange(n) / sps).astype(np.int64), nsym - 1)
    f = np.asarray(tones, float)[s[idx]]
    return np.exp(2j * np.pi * np.cumsum(f) / fs).astype(np.complex64)


def make_ofdm(fs, baud, tones, seconds, seed=3):
    """Parallelltonmodem: DQPSK på varje ton (som Link 11 / 16-tonsmodem)."""
    rng = np.random.default_rng(seed)
    nsym = int(seconds * baud)
    sps = fs / baud
    n = int(nsym * sps)
    t = np.arange(n) / fs
    idx = np.minimum((np.arange(n) / sps).astype(np.int64), nsym - 1)
    y = np.zeros(n, np.complex128)
    for f in tones:
        ph = np.cumsum(rng.integers(0, 4, nsym) * np.pi / 2 + np.pi / 4)
        y += np.exp(1j * (2 * np.pi * f * t + ph[idx]))
    return (y / np.sqrt(np.mean(np.abs(y) ** 2))).astype(np.complex64)


def make_morse(fs, text, wpm=18):
    rev = {v: k for k, v in MORSE.items()}
    dot = 1.2 / wpm
    key = []
    for w in text.upper().split():
        for ch in w:
            for s in rev.get(ch, ""):
                key += [1] * (1 if s == "." else 3) + [0]
            key += [0, 0]
        key += [0] * 4
    k = np.repeat(np.asarray(key, np.float32), int(dot * fs))
    k = _smooth(k, fs * 0.004)
    return k.astype(np.complex64)


def loop_sample(sig, pos):
    """Linjär interpolation i en slinga (snabbare än np.interp med period)."""
    L = len(sig)
    i = np.floor(pos).astype(np.int64)
    fr = (pos - i).astype(np.float32)
    i %= L
    return sig[i] * (1 - fr) + sig[(i + 1) % L] * fr


def test_signals(fs=48000):
    """Signaler för simulatorn: [(offset i Hz från 99,9 MHz, namn, komplex slinga vid fs, amplitud)].
    99,73 RTTY · 99,77 8PSK · 99,83 8-FSK (ALE-lik) · 99,86 16 toner (Link 11-lik) · 100,2 BPSK; Morse på 99,9."""
    ale = [750 + 250 * i - 1625 for i in range(8)]
    link11 = [935 + 110 * i - 1925 for i in range(16)]
    return [
        (-170e3, "RTTY 50/450", make_rtty(fs, 50, 450, "RYRYRY CQ CQ DE SIMULATORN TEST 1234 RYRYRY " * 6), 0.05),
        (-130e3, "8PSK 2400", make_psk(fs, 2400, 8, 8.0), 0.05),
        (-70e3, "8-FSK 125", make_mfsk(fs, 125, ale, 8.0), 0.05),
        (-40e3, "16 toner 75 Bd", make_ofdm(fs, 75, link11, 8.0), 0.05),
        (300e3, "BPSK 1200", make_psk(fs, 1200, 2, 8.0, seed=5), 0.05),
    ]


# ── registrera de större avkodarna (egen modul) ──
try:
    import analysavkodare as _av        # om modulen laddas först registrerar den sig själv i slutet
    for _k in getattr(_av, "AVKODARE_NYA", []):
        if _k not in AVKODARE:
            AVKODARE.append(_k)
except ImportError:                     # pragma: no cover
    pass
