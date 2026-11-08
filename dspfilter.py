"""DSP-filter för Signalspanaren.

Allt räknas på ljud-/kanalsidan efter nedblandning (48 kHz), utom störningsblankaren som arbetar på
bredbandig IQ (384 kHz) innan kanalfiltret, eftersom pulsstörningar är bredbandiga och smetas ut av
smala filter.

  ChannelFilter   – komplext kanalfilter med form SHARP/SOFT och twin PBT (kanterna var för sig)
  NoiseBlanker    – pulsblankare: nivå (tröskel), djup (dämpning), bredd (blankningstid)
  ToneControl     – RX HPF/LPF och bas/diskant (tonkontroll)
  ManualNotch     – smal IIR-notch på vald ljudfrekvens, bredd W/M/N
  SpectralNR      – brusreducering (Wiener med beslutsstyrd SNR och minimistatistik) och
                    auto-notch (tar bort stationära toner) i samma STFT
"""
import math

import numpy as np
from scipy.signal import butter, firwin, iirnotch, sosfilt, tf2sos, upfirdn
from scipy.ndimage import maximum_filter1d, median_filter


class Decimator:
    """FIR-filter + decimering med D, polyfas (räknar bara de utsampel som behålls) – samma resultat som
    lfilter(h, 1, x)[::D] med tillstånd mellan blocken, men D gånger färre multiplikationer.
    Blocklängden måste vara en multipel av D."""

    def __init__(self, h, D):
        self.D = int(D)
        self.h = np.asarray(h, np.float32)
        self.T = -(-(len(self.h) - 1) // self.D) * self.D      # historik, avrundad uppåt till multipel av D
        self.tail = np.zeros(self.T, np.complex64)

    def __call__(self, x):
        x = np.asarray(x, np.complex64)
        xin = np.concatenate((self.tail, x))
        self.tail = xin[len(xin) - self.T:]
        y = upfirdn(self.h, xin, 1, self.D)
        a = self.T // self.D
        return y[a:a + len(x) // self.D]

# ════════════════════════════════════════════════════════════════════
# Kanalfilter
# ════════════════════════════════════════════════════════════════════
SHAPES = {
    # (antal tappar vid 48 kHz, fönster) – SHARP ≈ 150 Hz övergång, SOFT ≈ 600 Hz (mjukare, mindre ringning)
    "SHARP": (1023, ("kaiser", 8.0)),
    "SOFT": (255, "hann"),
}

# Förval FIL1/FIL2/FIL3 per läge (Hz)
FIL_PRESETS = {
    "USB": (3000, 2400, 1800),
    "LSB": (3000, 2400, 1800),
    "CW": (1200, 500, 250),
    "AM": (9000, 6000, 3000),
    "NFM": (15000, 10000, 7000),
}


class FftFir:
    """Komplex FIR med overlap-save via FFT (kontinuerlig mellan block). Snabb även för långa filter."""

    def __init__(self, h):
        self.h = np.asarray(h, np.complex128)
        self.tail = np.zeros(len(self.h) - 1, np.complex128)
        self._nfft = 0
        self._H = None

    def process(self, x):
        m = len(self.h)
        xin = np.concatenate((self.tail, x))
        self.tail = xin[len(xin) - (m - 1):] if m > 1 else self.tail
        n = len(xin)
        nfft = 1 << (n - 1).bit_length()
        if nfft != self._nfft:
            self._nfft = nfft
            self._H = np.fft.fft(self.h, nfft)
        y = np.fft.ifft(np.fft.fft(xin, nfft) * self._H)
        return y[m - 1:n]


def pbt_edges(mode, bw, pbt_lo, pbt_hi):
    """Kanalens kanter i Hz relativt den inställda frekvensen (RF-sidan) med twin PBT.
    pbt_lo flyttar nedre kanten, pbt_hi övre (positivt = uppåt i frekvens). Kanten närmast bärvågen
    i SSB kan inte gå under 50 Hz från bärvågen, AM-bärvågen måste ligga kvar i passbandet."""
    if mode == "USB":
        lo, hi = 200 + pbt_lo, 200 + bw + pbt_hi
        lo = max(50.0, lo)
    elif mode == "LSB":
        lo, hi = -(200 + bw) + pbt_lo, -200 + pbt_hi
        hi = min(-50.0, hi)
    else:
        lo, hi = -bw / 2 + pbt_lo, bw / 2 + pbt_hi
        if mode == "AM":
            lo, hi = min(lo, -100.0), max(hi, 100.0)
    if hi - lo < 100:
        m = (lo + hi) / 2
        lo, hi = m - 50, m + 50
    return float(lo), float(hi)


def channel_taps(lo, hi, fs, shape, center_shift=0.0):
    """Komplext bandpass lo..hi Hz (efter att signalen flyttats center_shift Hz, t.ex. CW-tonen)."""
    n, win = SHAPES.get(shape, SHAPES["SHARP"])
    half = max(50.0, (hi - lo) / 2)
    fc = (lo + hi) / 2 + center_shift
    k = np.arange(n) - (n - 1) / 2
    return firwin(n, min(half, fs / 2 * 0.95), window=win, fs=fs) * np.exp(2j * np.pi * fc * k / fs)


# ════════════════════════════════════════════════════════════════════
# Störningsblankare (på bredbandig IQ)
# ════════════════════════════════════════════════════════════════════
class NoiseBlanker:
    """Pulsblankare: LEV (tröskel), DEP (dämpning) och WID (blankningstid).
    Tröskeln är relativ medianen av |x| i blocket – robust mot själva pulserna."""

    def __init__(self, fs):
        self.fs = float(fs)
        self.level = 50          # 1–100: högre = känsligare (lägre tröskel)
        self.depth = 8           # 1–10: dämpning 10 %…100 %
        self.width = 20          # 1–100: blankningstid 10 µs…1 ms
        self.blanked = 0.0       # andel blankade sampel (senaste blocket), för visning
        self._hold = 0           # blankning som fortsätter in i nästa block

    def threshold_factor(self):
        return 30.0 / (1.0 + self.level / 5.0)          # 1 → 25×, 50 → 2,7×, 100 → 1,4× medianen

    def process(self, x):
        mag = np.abs(x)
        med = float(np.median(mag)) + 1e-20
        mask = mag > self.threshold_factor() * med * 1.13
        w = max(1, int(round(self.width * 10e-6 * self.fs)))
        if self._hold:
            mask[:self._hold] = True
        if mask.any():
            mask = maximum_filter1d(mask.astype(np.uint8), size=2 * w + 1).astype(bool)
            last = np.nonzero(mask)[0][-1]
            self._hold = max(0, w - (len(x) - 1 - last)) if last >= len(x) - w else 0
        else:
            self._hold = 0
        self.blanked = float(mask.mean())
        if not mask.any():
            return x
        g = 1.0 - 0.1 * max(1, min(10, int(self.depth)))
        y = x.copy()
        y[mask] *= g
        return y


# ════════════════════════════════════════════════════════════════════
# Tonkontroll och manuell notch (ljud, 48 kHz)
# ════════════════════════════════════════════════════════════════════
def _shelf(fs, f0, gain_db, high):
    """Andra ordningens shelving-filter (RBJ-kokboken), S = 1."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / fs
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / 2 * math.sqrt(2)
    sa = 2 * math.sqrt(A) * alpha
    if high:
        b = [A * ((A + 1) + (A - 1) * cw + sa), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sa)]
        a = [(A + 1) - (A - 1) * cw + sa, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sa]
    else:
        b = [A * ((A + 1) - (A - 1) * cw + sa), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sa)]
        a = [(A + 1) + (A - 1) * cw + sa, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sa]
    return tf2sos(b, a)


class ToneControl:
    """RX HPF/LPF (Hz, 0 = av) och bas/diskant (−5…+5, 2 dB per steg). Bas/diskant nollas
    när HPF/LPF används. default_hp: lägets eget högpass när HPF är av (AM 60, NFM 250, övriga 100 Hz)."""

    def __init__(self, fs):
        self.fs = fs
        self.key = None
        self.sos = None
        self.zi = None

    def configure(self, hpf, lpf, bass, treble, default_hp):
        key = (hpf, lpf, bass, treble, default_hp)
        if key == self.key:
            return
        self.key = key
        parts = []
        hp = hpf if hpf and hpf > 0 else default_hp
        if hp:
            parts.append(butter(4 if hpf else 2, hp, "highpass", fs=self.fs, output="sos"))
        if lpf and lpf > 0:
            parts.append(butter(6, min(lpf, self.fs * 0.45), "lowpass", fs=self.fs, output="sos"))
        if not (hpf or lpf):
            if bass:
                parts.append(_shelf(self.fs, 300.0, 2.0 * bass, high=False))
            if treble:
                parts.append(_shelf(self.fs, 1800.0, 2.0 * treble, high=True))
        if parts:
            sos = np.vstack(parts)
            old = self.zi
            self.zi = np.zeros((len(sos), 2))
            if old is not None and old.shape == self.zi.shape:
                self.zi = old
            self.sos = sos
        else:
            self.sos, self.zi = None, None

    def process(self, a):
        if self.sos is None:
            return a
        y, self.zi = sosfilt(self.sos, a, zi=self.zi)
        return y


NOTCH_WIDTHS = {"W": 160.0, "M": 80.0, "N": 40.0}   # −3 dB-bredd i Hz


class ManualNotch:
    def __init__(self, fs):
        self.fs = fs
        self.key = None
        self.sos = None
        self.zi = None

    def configure(self, freq, width):
        key = (round(freq), width)
        if key == self.key:
            return
        self.key = key
        f = float(min(max(freq, 50.0), self.fs * 0.45))
        bw = NOTCH_WIDTHS.get(width, 80.0)
        b, a = iirnotch(f, f / bw, fs=self.fs)
        # två i kaskad: djupare och lite bredare botten
        self.sos = np.vstack((tf2sos(b, a), tf2sos(b, a)))
        self.zi = np.zeros((len(self.sos), 2))

    def process(self, a):
        y, self.zi = sosfilt(self.sos, a, zi=self.zi)
        return y


# ════════════════════════════════════════════════════════════════════
# Brusreducering och auto-notch (STFT)
# ════════════════════════════════════════════════════════════════════
class SpectralNR:
    """STFT med √Hann, 75 % överlapp (fördröjning N sampel ≈ 43 ms vid 48 kHz och N = 2048).

    NR: minimistatistik skattar bruset per frekvens (minsta utjämnade effekt över ca 1,5 s),
    Wienerförstärkning med beslutsstyrd SNR (Ephraim–Malah) som håller nere ”musikaliskt brus”.
    Nivå 1–15 styr överskattning av bruset och golvet för dämpningen (−4…−18 dB).

    Auto-notch: en ton som ligger stilla (långtidsmedel > 15 dB över omgivningen och jämn i tiden
    under ca 0,3 s) dämpas 30 dB; tal rör sig och träffas inte. Släpps när tonen försvinner."""

    def __init__(self, fs, n=2048):
        self.fs = fs
        self.n = n
        self.hop = n // 4
        self.win = np.sqrt(np.hanning(n + 1)[:n])
        self.norm = 0.5                             # √hann in och ut = hann; Σ hann vid 75 % överlapp = 2
        self.nr = False
        self.nr_level = 6
        self.notch = False
        self.reset()

    def reset(self):
        nb = self.n // 2 + 1
        self.inbuf = np.zeros(self.n)
        self.outbuf = np.zeros(self.n)
        self.pending = np.zeros(0)
        self.out_q = np.zeros(0)
        self.Ps = None
        self.noise = None
        self.mins = None
        self.sub_min = None
        self.sub_count = 0
        self.G_prev = np.ones(nb)
        self.P_prev = np.zeros(nb)
        self.PL = None
        self.steady = np.zeros(nb, np.int32)
        self.notched = np.zeros(nb, bool)
        self.release = np.zeros(nb, np.int32)
        self.frames = 0
        self.primed = False

    @property
    def active(self):
        return self.nr or self.notch

    def notched_hz(self):
        """Frekvenser (Hz) som auto-notchen dämpar just nu."""
        idx = np.nonzero(self.notched)[0]
        return [float(i * self.fs / self.n) for i in idx]

    def _frame(self, X):
        P = (X.real ** 2 + X.imag ** 2)
        nb = len(P)
        G = np.ones(nb)
        self.frames += 1
        # utjämnad effekt och minimistatistik (8 delfönster à 18 ramar ≈ 1,5 s)
        if self.Ps is None:
            self.Ps = P.copy()
            self.sub_min = P.copy()
            self.mins = np.tile(P, (8, 1))
            self.noise = P.copy()
            self.PL = P.copy()
        self.Ps = 0.75 * self.Ps + 0.25 * P
        self.sub_min = np.minimum(self.sub_min, self.Ps)
        self.sub_count += 1
        if self.sub_count >= 18:
            self.mins = np.roll(self.mins, 1, axis=0)
            self.mins[0] = self.sub_min
            self.sub_min = self.Ps.copy()
            self.sub_count = 0
        nmin = np.minimum(self.mins.min(axis=0), self.sub_min)
        # korrektion för att minimum underskattar medelvärdet (≈ 1,5–2 för dessa fönster)
        self.noise = 1.8 * nmin

        if self.notch:
            a = math.exp(-self.hop / (self.fs * 0.6))
            self.PL = a * self.PL + (1 - a) * P
            loc = median_filter(self.PL, size=41, mode="nearest") + 1e-30
            peak = self.PL > 31.6 * loc                         # 15 dB över omgivningen
            peak &= self.PL >= maximum_filter1d(self.PL, 3)     # lokalt maximum
            even = (P > 0.25 * self.PL) & (P < 4.0 * self.PL)   # jämn i tiden
            cand = peak & even
            self.steady = np.where(cand, self.steady + 1, 0)
            on = self.steady >= int(0.3 * self.fs / self.hop)
            self.release = np.where(on, 12, np.maximum(self.release - 1, 0))
            self.notched = self.release > 0
            nz = maximum_filter1d(self.notched.astype(np.uint8), 5).astype(bool)   # ±2 bin (≈ ±47 Hz)
            nz[:3] = False                                      # aldrig DC
            G[nz] = 0.03                                        # −30 dB
        else:
            self.notched[:] = False

        if self.nr:
            lvl = max(1, min(15, int(self.nr_level)))
            over = 0.6 + 0.12 * lvl                             # överskattning av bruset
            floor = 10 ** ((-3.0 - lvl) / 20)                   # −4…−18 dB
            N = self.noise * over + 1e-30
            post = P / N
            xi = 0.98 * (self.G_prev ** 2) * self.P_prev / N + 0.02 * np.maximum(post - 1.0, 0.0)
            Gw = xi / (1.0 + xi)
            Gw = np.convolve(Gw, np.ones(3) / 3, mode="same")    # lite utjämning i frekvens
            Gw = np.maximum(Gw, floor)
            self.G_prev = Gw
            self.P_prev = P
            G = G * Gw
        return X * G

    def process(self, a):
        if not self.active:
            if self.primed:
                self.reset()
            return a
        self.primed = True
        x = np.concatenate((self.pending, a))
        out = []
        pos = 0
        while pos + self.hop <= len(x):
            self.inbuf = np.concatenate((self.inbuf[self.hop:], x[pos:pos + self.hop]))
            pos += self.hop
            Y = self._frame(np.fft.rfft(self.inbuf * self.win))
            y = np.fft.irfft(Y, self.n) * self.win * self.norm
            self.outbuf += y
            out.append(self.outbuf[:self.hop].copy())
            self.outbuf = np.concatenate((self.outbuf[self.hop:], np.zeros(self.hop)))
        self.pending = x[pos:]
        q = np.concatenate([self.out_q] + out) if out else self.out_q
        # lika många sampel ut som in (fast fördröjning; början fylls med tystnad)
        if len(q) < len(a):
            q = np.concatenate((np.zeros(len(a) - len(q)), q))
        self.out_q = q[len(a):]
        return q[:len(a)]
