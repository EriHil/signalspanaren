# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
IQ-inspelning och uppspelning för Signalspanaren.

Skriva:
  SigMFWriter   .sigmf-data + .sigmf-meta (cu8 eller cf32). Varje omstämning blir en
                ny "capture" med egen mittfrekvens, så filen går att spela upp korrekt.
  WavWriter     2-kanals 16-bitars WAV (som SDR++/SDR#), mittfrekvensen i filnamnet
                och i en SDR#-kompatibel 'auxi'-del.

Läsa (IQReader):
  SigMF (.sigmf-meta/.sigmf-data), rtl_sdr (.cu8/.raw/.bin/.iq), cs8, cs16, cf32 (.cfile,
  gqrx *_fc.raw), WAV (8/16/32-bitars, SDR++/SDR# – frekvens ur 'auxi' eller filnamnet).
  Okänd takt/frekvens tas ur filnamnet (t.ex. _100.0000MHz_1536000sps, 100000000Hz) eller
  anges av användaren.

FileSource spelar upp en fil som mottagarkälla i realtid (eller snabbare), räknar om
samplingstakten till programmets takt och kan pausas, spolas och gå i slinga.
"""
import datetime
import json
import os
import re
import struct
import threading
import time
from fractions import Fraction

import numpy as np
from scipy.signal import firwin, resample_poly
import scipy.fft as sfft

DTYPES = {   # SigMF-datatyp: (numpy-typ, antal reella värden per sample, skala till ±1, offset)
    "cu8": (np.uint8, 2, 1 / 127.5, 127.5),
    "ci8": (np.int8, 2, 1 / 128.0, 0.0),
    "ci16_le": (np.int16, 2, 1 / 32768.0, 0.0),
    "cf32_le": (np.float32, 2, 1.0, 0.0),
}
EXT_TYPE = {".cu8": "cu8", ".raw": "cu8", ".bin": "cu8", ".iq": "cu8", ".cs8": "ci8", ".ci8": "ci8",
            ".cs16": "ci16_le", ".ci16": "ci16_le", ".cf32": "cf32_le", ".cfile": "cf32_le", ".fc32": "cf32_le"}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def to_cu8(x):
    v = np.empty(len(x) * 2, np.float32)
    v[0::2] = x.real
    v[1::2] = x.imag
    return np.clip(v * 127.5 + 127.5, 0, 255).astype(np.uint8)


# ════════════════════════════════════════════════════════════════════
# Skrivare
# ════════════════════════════════════════════════════════════════════
class SigMFWriter:
    def __init__(self, base, rate, center, datatype="cu8", description="", extra=None):
        self.base = base
        self.path = base + ".sigmf-data"
        self.meta_path = base + ".sigmf-meta"
        self.rate = float(rate)
        self.datatype = datatype
        self.f = open(self.path, "wb")
        self.n = 0
        self.captures = [{"core:sample_start": 0, "core:frequency": float(center), "core:datetime": now_iso()}]
        self.annotations = []
        self.glob = {"core:datatype": datatype, "core:sample_rate": self.rate, "core:version": "1.0.0",
                     "core:recorder": "Signalspanaren", "core:description": description}
        if extra:
            self.glob.update(extra)
        self._write_meta()

    def write(self, x, center, raw=None):
        if abs(center - self.captures[-1]["core:frequency"]) > 0.5:
            self.captures.append({"core:sample_start": self.n, "core:frequency": float(center),
                                  "core:datetime": now_iso()})
            self._write_meta()
        if self.datatype == "cu8":
            b = raw if raw is not None else to_cu8(x)
            self.f.write(np.asarray(b, np.uint8).tobytes())
            self.n += len(b) // 2
        else:
            self.f.write(np.asarray(x, np.complex64).tobytes())
            self.n += len(x)

    def annotate(self, start, count, label, comment="", flo=None, fhi=None):
        a = {"core:sample_start": int(start), "core:sample_count": int(count), "core:label": label}
        if comment:
            a["core:comment"] = comment
        if flo is not None:
            a["core:freq_lower_edge"] = float(flo)
            a["core:freq_upper_edge"] = float(fhi)
        self.annotations.append(a)

    def _write_meta(self):
        meta = {"global": self.glob, "captures": self.captures, "annotations": self.annotations}
        tmp = self.meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.meta_path)

    def seconds(self):
        return self.n / self.rate

    def close(self):
        self.f.close()
        self._write_meta()
        return self.path


class WavWriter:
    """2-kanals int16-WAV. Byter till en ny fil om mittfrekvensen ändras (WAV har bara en frekvens)."""

    def __init__(self, base, rate, center):
        self.dir, self.stem = os.path.split(base)
        self.rate = int(rate)
        self.paths = []
        self.n = 0
        self.total = 0
        self.f = None
        self._open(center)

    def _open(self, center):
        if self.f:
            self._finish()
        self.center = float(center)
        ts = datetime.datetime.now()
        name = f"{self.stem}_{int(round(center))}Hz_{ts:%H-%M-%S_%d-%m-%Y}.wav"
        self.path = os.path.join(self.dir, name)
        self.f = open(self.path, "wb")
        self.t0 = datetime.datetime.now(datetime.timezone.utc)
        self.f.write(self._header(0))
        self.n = 0
        self.paths.append(self.path)

    def _auxi(self, stop=None):
        def st(t):   # Windows SYSTEMTIME
            return struct.pack("<8H", t.year, t.month, t.isoweekday() % 7, t.day, t.hour, t.minute, t.second,
                               t.microsecond // 1000)
        body = st(self.t0) + st(stop or self.t0) + struct.pack("<9I", int(round(self.center)) & 0xFFFFFFFF,
                                                                self.rate, 0, 0, 0, 0, 0, 0, 0)
        return b"auxi" + struct.pack("<I", len(body)) + body

    def _header(self, nbytes, stop=None):
        aux = self._auxi(stop)
        fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, self.rate, self.rate * 4, 4, 16)
        return (b"RIFF" + struct.pack("<I", 4 + len(fmt) + len(aux) + 8 + nbytes) + b"WAVE" + fmt + aux +
                b"data" + struct.pack("<I", nbytes))

    def write(self, x, center, raw=None):
        if abs(center - self.center) > 0.5:
            self._open(center)
        v = np.empty(len(x) * 2, np.int16)
        v[0::2] = np.clip(x.real * 32767, -32768, 32767)
        v[1::2] = np.clip(x.imag * 32767, -32768, 32767)
        self.f.write(v.tobytes())
        self.n += len(x)
        self.total += len(x)

    def _finish(self):
        nbytes = self.n * 4
        self.f.seek(0)
        self.f.write(self._header(nbytes, datetime.datetime.now(datetime.timezone.utc)))
        self.f.close()
        self.f = None

    def seconds(self):
        return self.total / self.rate

    def close(self):
        if self.f:
            self._finish()
        return self.paths[0] if len(self.paths) == 1 else self.paths[-1]


# ════════════════════════════════════════════════════════════════════
# Läsare
# ════════════════════════════════════════════════════════════════════
def guess_from_name(path):
    """(mittfrekvens Hz, samplingstakt) ur filnamnet, eller None där det inte går."""
    n = os.path.basename(path)
    f = r = None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*MHz", n, re.I)
    if m:
        f = float(m.group(1).replace(",", ".")) * 1e6
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*kHz", n, re.I)
    if f is None and m:
        f = float(m.group(1).replace(",", ".")) * 1e3
    m = re.search(r"(\d{4,11})\s*Hz", n)
    if f is None and m:
        f = float(m.group(1))
    m = re.search(r"(\d{4,9})\s*(?:sps|S/s|sp)", n, re.I)
    if m:
        r = float(m.group(1))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*M(?:sps|S)", n)
    if r is None and m:
        r = float(m.group(1).replace(",", ".")) * 1e6
    m = re.match(r"gqrx_\d{8}_\d{6}_(\d+)_(\d+)_fc", n)       # gqrx: gqrx_DATUM_TID_frekvens_takt_fc.raw
    if m:
        f, r = float(m.group(1)), float(m.group(2))
    return f, r


class IQReader:
    def __init__(self, path, rate=None, center=None, datatype=None):
        self.path = path
        self.captures = []
        self.annotations = []
        self.info = ""
        base, ext = os.path.splitext(path)
        ext = ext.lower()
        if ext in (".sigmf-meta", ".sigmf-data", ".sigmf"):
            self._sigmf(base)
        elif ext == ".wav":
            self._wav()
        else:
            dt = datatype or EXT_TYPE.get(ext, "cu8")
            if "_fc" in os.path.basename(path).lower():
                dt = "cf32_le"
            self._raw(path, dt, 0)
            gf, gr = guess_from_name(path)
            self.rate = float(rate or gr or 0) or None
            self.captures = [(0, float(center or gf or 0) or None)]
            self.info = f"rå {dt}"
        if rate:
            self.rate = float(rate)
        if center:
            self.captures = [(0, float(center))] + [c for c in self.captures[1:]]

    # ── format ──
    def _raw(self, path, dt, offset):
        self.datatype = dt
        npt, k, self.scale, self.off = DTYPES[dt]
        self.mm = np.memmap(path, dtype=npt, mode="r", offset=offset)
        self.n = len(self.mm) // 2

    def _sigmf(self, base):
        with open(base + ".sigmf-meta", encoding="utf-8") as fh:
            meta = json.load(fh)
        g = meta.get("global", {})
        dt = g.get("core:datatype", "cu8")
        if dt not in DTYPES:
            raise ValueError(f"SigMF-datatypen {dt} stöds inte (stöds: {', '.join(DTYPES)})")
        self._raw(base + ".sigmf-data", dt, 0)
        self.rate = float(g.get("core:sample_rate") or 0) or None
        caps = sorted(meta.get("captures", []), key=lambda c: c.get("core:sample_start", 0))
        self.captures = [(int(c.get("core:sample_start", 0)), c.get("core:frequency")) for c in caps] or [(0, None)]
        self.annotations = meta.get("annotations", [])
        self.info = f"SigMF {dt}" + (f" – {g['core:description']}" if g.get("core:description") else "")

    def _wav(self):
        with open(self.path, "rb") as fh:
            hdr = fh.read(1 << 16)
        if hdr[:4] not in (b"RIFF", b"RF64") or hdr[8:12] != b"WAVE":
            raise ValueError("Inte en WAV-fil")
        pos = 12
        fmt = None
        center = None
        data_off = data_len = None
        while pos + 8 <= len(hdr):
            cid, ln = hdr[pos:pos + 4], struct.unpack("<I", hdr[pos + 4:pos + 8])[0]
            body = hdr[pos + 8:pos + 8 + ln]
            if cid == b"fmt ":
                fmt = struct.unpack("<HHIIHH", body[:16])
            elif cid == b"auxi" and len(body) >= 36:
                cf = struct.unpack("<I", body[32:36])[0]
                if 1000 < cf < 0xFFFFFFF0:
                    center = float(cf)
            elif cid == b"data":
                data_off, data_len = pos + 8, ln
                break
            pos += 8 + ln + (ln & 1)
        if fmt is None or data_off is None:
            raise ValueError("WAV-filen saknar fmt eller data")
        tag, ch, rate, _br, _ba, bits = fmt
        if ch != 2:
            raise ValueError(f"WAV-filen har {ch} kanal(er) – IQ kräver 2 (I och Q)")
        dt = {(1, 8): "cu8", (1, 16): "ci16_le", (3, 32): "cf32_le"}.get((tag if tag != 0xFFFE else 1, bits))
        if dt is None:
            raise ValueError(f"WAV-formatet ({bits} bitar, typ {tag}) stöds inte")
        self._raw(self.path, dt, data_off)
        if data_len and data_len != 0xFFFFFFFF:
            self.n = min(self.n, data_len // (2 * np.dtype(DTYPES[dt][0]).itemsize))
        self.rate = float(rate)
        gf, _gr = guess_from_name(self.path)
        self.captures = [(0, center or gf)]
        self.info = f"WAV {bits}-bit"

    # ── läsning ──
    def read(self, start, count):
        start = max(0, min(start, self.n))
        count = max(0, min(count, self.n - start))
        v = np.array(self.mm[2 * start:2 * (start + count)], np.float32)     # kopia (filen är skrivskyddad)
        if self.off:
            v = v - self.off
        v *= self.scale
        return v.view(np.complex64) if v.dtype == np.float32 else v.astype(np.float32).view(np.complex64)

    def center_at(self, idx):
        c = None
        for s, f in self.captures:
            if s <= idx:
                c = f
            else:
                break
        return c

    def seconds(self):
        return self.n / self.rate if self.rate else 0.0


# ════════════════════════════════════════════════════════════════════
# Omsampling (strömmande) till programmets takt
# ════════════════════════════════════════════════════════════════════
class FftUpsampler:
    """Smal IQ (t.ex. KiwiSDR:s 12 kS/s) till bred takt med en enda FFT per bit: biten FFT:as, spektret
    läggs mitt i ett större spektrum (där brus fylls i utanför det smala bandet) och en invers FFT ger
    utsamplen. Ersätter resample_poly + separat brus-FFT och är flera gånger billigare.
    Overlap-save: varje fönster har K insampel på var sida om biten, och spektret rullar av mjukt mot
    Nyquist, så kanterna inte läcker in. En bits fördröjning, precis som Resampler."""

    def __init__(self, rate_in, rate_out, chunk, noise_lo=0.415, noise_hi=0.47):
        fr = Fraction(int(round(rate_out)), int(round(rate_in))).limit_denominator(4000)
        self.up, self.down = fr.numerator, fr.denominator
        self.ratio = self.up / self.down
        self.L = int(chunk)
        assert self.L % self.down == 0
        self.K = -(-256 // self.down) * self.down
        nw = self.L + 2 * self.K
        self.nw = nw
        self.N = nw * self.up // self.down
        f = np.abs(np.fft.fftfreq(nw))                         # cykler per insampel
        t = np.clip((f - 0.44) / 0.06, 0, 1)
        self.smask = ((0.5 + 0.5 * np.cos(np.pi * t)) * self.ratio).astype(np.float32)
        fo = np.abs(np.fft.fftfreq(self.N, 1 / rate_out)) / rate_in
        t = np.clip((fo - noise_lo) / (noise_hi - noise_lo), 0, 1)
        nm = np.sqrt(0.5 - 0.5 * np.cos(np.pi * t)).astype(np.float32)
        self.nidx = np.flatnonzero(nm > 0)
        self.nmask = nm[self.nidx]
        self.rng = np.random.default_rng()
        self.reset()

    def reset(self):
        self.prev = np.zeros(self.K, np.complex64)
        self.pending = None

    def process(self, x, nvar=None):
        """x: L insampel. nvar: brusvarians per insampel (fylls i utanför bandet), None = inget brus.
        Returnerar L·up/down utsampel för förra biten."""
        x = np.asarray(x, np.complex64)
        if self.pending is None:
            self.pending = x
            return np.zeros(0, np.complex64)
        win = np.concatenate((self.prev, self.pending, x[:self.K]))
        X = sfft.fft(win) * self.smask
        h = self.nw // 2
        Y = np.zeros(self.N, np.complex64)
        Y[:h] = X[:h]
        Y[self.N - h:] = X[h:]
        if nvar is not None:
            sd = np.float32(np.sqrt(max(nvar, 1e-14) * self.ratio * self.N / 2))
            m = len(self.nidx)
            w = self.rng.standard_normal(2 * m, dtype=np.float32).view(np.complex64)
            Y[self.nidx] += w * (self.nmask * sd)
        y = sfft.ifft(Y)
        a = self.K * self.up // self.down
        out = y[a:a + self.L * self.up // self.down]
        self.prev = np.concatenate((self.prev, self.pending))[-self.K:]
        self.pending = x
        return out


class Resampler:
    """Rationell omsampling i bitar med överlapp (resample_poly), kontinuerlig mellan bitarna."""

    def __init__(self, rate_in, rate_out):
        fr = Fraction(int(round(rate_out)), int(round(rate_in))).limit_denominator(4000)
        self.up, self.down = fr.numerator, fr.denominator
        self.ratio = self.up / self.down
        m = max(self.up, self.down)
        half = 24 * m                    # längre och brantare än resample_poly:s standard (≈ −110 dB spegling)
        self.h = firwin(2 * half + 1, 0.96 / m, window=("kaiser", 11.0))     # resample_poly multiplicerar själv med up
        taps_in = int(np.ceil((half + 1) / self.up)) + 4
        self.K = int(np.ceil(taps_in / self.down)) * self.down      # överlapp i insamples, multipel av down
        self.prev = np.zeros(self.K, np.complex64)
        self.pending = None

    def process(self, x):
        """x: insamples (längd multipel av down). Returnerar utsamples för förra biten (en bits fördröjning)."""
        if self.up == self.down:
            return x
        if self.pending is None:
            self.pending = x
            return np.zeros(0, np.complex64)
        win = np.concatenate((self.prev, self.pending, x[:self.K]))
        y = resample_poly(win, self.up, self.down, window=self.h)
        a = self.K * self.up // self.down
        out = y[a:a + len(self.pending) * self.up // self.down].astype(np.complex64)
        self.prev = np.concatenate((self.prev, self.pending))[-self.K:]
        self.pending = x
        return out

    def reset(self):
        self.prev[:] = 0
        self.pending = None


# ════════════════════════════════════════════════════════════════════
# Uppspelningskälla
# ════════════════════════════════════════════════════════════════════
class FileSource:
    """Mottagarkälla som spelar upp en IQ-fil. Mittfrekvensen styrs av filen (fixed_center)."""
    kind = "file"
    name = "IQ-fil"
    fixed_center = True

    def __init__(self, reader, out_rate, block, speed=1.0, loop=False):
        self.r = reader
        self.out_rate = float(out_rate)
        self.block = int(block)
        self.speed = speed
        self.loop = loop
        self.paused = False
        self.pos = 0                       # insample-index
        self.lock = threading.Lock()
        self.ended = False
        self._seek_to = None
        rate = reader.rate or out_rate
        self.rs = Resampler(rate, out_rate)
        self.chunk_in = max(self.rs.down, int(round(self.block / self.rs.ratio / self.rs.down)) * self.rs.down)
        self.buf = np.zeros(0, np.complex64)
        self.center = reader.center_at(0) or 100e6
        self.buf_center = self.center
        self._pos_out = 0.0               # position (insamples) för första samplet i self.buf
        self._nvar = None

    # ── brus utanför en smal fils band ──
    def _noise_var(self):
        """Filens brusgolv (varians per insample) skattat med medianen av spektret på några ställen."""
        N = 8192
        est = []
        for frac in (0.1, 0.3, 0.5, 0.7, 0.9):
            st = int(max(0, self.r.n - N) * frac)
            x = self.r.read(st, N)
            if len(x) < N:
                continue
            X = np.abs(np.fft.fft(x * np.hanning(N))) ** 2 / np.sum(np.hanning(N) ** 2)
            est.append(np.median(X) / np.log(2))
        return max(float(np.median(est)) if est else 0.0, 1e-11)

    def _outband_noise(self, n):
        """Vitt brus med filens brustäthet, men bara utanför filens band. Utan det blir området
        utanför helt tomt: SNR blir "oändligt", nivåautomatiken och signalanalysens bandbredd
        tar hela filens band för en signal."""
        if self._nvar is None:
            self._nvar = self._noise_var()
            fr = np.fft.fftfreq(n, 1 / self.out_rate)
            self._nmask = (np.abs(fr) > 0.47 * self.r.rate).astype(np.float32)
        if len(self._nmask) != n:
            self._nmask = (np.abs(np.fft.fftfreq(n, 1 / self.out_rate)) > 0.47 * self.r.rate).astype(np.float32)
        sd = np.sqrt(self._nvar * self.rs.ratio / 2)
        w = (np.random.standard_normal(2 * n) * sd).astype(np.float32).view(np.complex64)
        return np.fft.ifft(np.fft.fft(w) * self._nmask).astype(np.complex64)

    # BaseSource-gränssnittet
    def set(self, key, val):
        pass                               # frekvens, förstärkning m.m. styrs av filen

    def open(self):
        self.t_next = time.monotonic()

    def close(self):
        pass

    def seek(self, seconds):
        with self.lock:
            self._seek_to = int(max(0, min(self.r.n - 1, seconds * (self.r.rate or self.out_rate))))

    def position(self):
        return self._pos_out / (self.r.rate or self.out_rate)

    def read(self):
        with self.lock:
            if self._seek_to is not None:
                self.pos = self._seek_to
                self._pos_out = float(self.pos)
                self._seek_to = None
                self.rs.reset()
                self.buf = np.zeros(0, np.complex64)
        while self.paused and not self._seek_to:
            time.sleep(0.05)
            self.t_next = time.monotonic()
            return None
        # fyll bufferten med omsamplade samples; byt inte mittfrekvens mitt i ett block
        while len(self.buf) < self.block:
            if self.pos >= self.r.n:
                if self.loop:
                    self.pos = 0
                    self.rs.reset()
                else:
                    self.ended = True
                    if len(self.buf) == 0:
                        raise EOFError
                    self.buf = np.concatenate((self.buf, np.zeros(self.block - len(self.buf), np.complex64)))
                    break
            c = self.r.center_at(self.pos)
            nxt = [s for s, _f in self.r.captures if s > self.pos]
            end = min(self.pos + self.chunk_in, self.r.n, nxt[0] if nxt else self.r.n)
            x = self.r.read(self.pos, end - self.pos)
            x = np.concatenate((x, np.zeros((-len(x)) % self.rs.down, np.complex64)))
            if c is not None and abs(c - self.buf_center) > 0.5:
                if len(self.buf):          # skicka det som finns med gamla frekvensen först
                    break
                self.buf_center = c
                self.rs.reset()
            self.pos = end
            self.buf = np.concatenate((self.buf, self.rs.process(x)))
            if self.rs.up != self.rs.down and len(self.buf) == 0 and self.pos >= self.r.n and not self.loop:
                self.buf = np.concatenate((self.buf, self.rs.process(np.zeros(self.rs.down, np.complex64))))
        out = self.buf[:self.block]
        if self.rs.ratio > 1.2:
            out = out + self._outband_noise(len(out))
        if len(out) < self.block:
            out = np.concatenate((out, np.zeros(self.block - len(out), np.complex64)))
        self.buf = self.buf[self.block:]
        self.center = self.buf_center
        self._pos_out = min(self.r.n, self._pos_out + self.block / self.rs.ratio)
        if self.loop and self._pos_out >= self.r.n:
            self._pos_out = 0.0
        # takt: realtid × hastighet (0 = så fort det går)
        if self.speed:
            self.t_next += self.block / self.out_rate / self.speed
            d = self.t_next - time.monotonic()
            if d > 0:
                time.sleep(d)
            elif d < -0.5:
                self.t_next = time.monotonic()
        raw = None
        return out.astype(np.complex64), self.center, raw
