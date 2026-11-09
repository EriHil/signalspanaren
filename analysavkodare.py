# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Fler avkodare för signalanalysen i Signalspanaren.

  NAVTEX / SITOR-B   CCIR 476, 100 Bd, 170 Hz shift, FEC med repetition 5 tecken senare
  POCSAG             personsökare 512/1200/2400 Bd, BCH(31,21)
  ALE 2G             MIL-STD-188-141: 8-FSK 125 Bd, Golay(24,12), trippelredundans
  Väderfax           HF-fax 120 rader/min, IOC 576, svart 1500 Hz / vit 2300 Hz
  DMR                4FSK 4800 sym/s: synk, färgkod, burst-typ, LC-header (adresser, kryptering)
  PSK31 / PSK63      BPSK 31,25 / 62,5 Bd med ITU/G3PLX varicode-alfabet

Alla avkodare får komplext basband från analysens kanalutsnitt (mitt = analysens mål)
och returnerar ny klartext. Hela meddelanden läggs i .msgs och visas i fliken Avkodat.
Tabeller: SITOR-B från fldigi (GPL), ALE från MIL-STD-188-141B appendix A.
"""
import math
import time
from collections import deque

import numpy as np

import analys as A


# ════════════════════════════════════════════════════════════════════
# Gemensamt: diskriminator och symbolklocka
# ════════════════════════════════════════════════════════════════════
class FiAvkodare(A.Avkodare):
    """Bas för FSK-avkodare: momentan frekvens som är kontinuerlig mellan bitarna."""

    def reset(self):
        self._prev = None
        self._dtail = np.zeros(0, np.float32)
        self.msgs = []

    def _fi(self, z, fs, smooth):
        if len(z) == 0:
            return np.zeros(0, np.float32)
        if self._prev is not None:
            z = np.concatenate(([self._prev], z))
        self._prev = z[-1]
        d = (np.angle(z[1:] * np.conj(z[:-1])) * fs / (2 * np.pi)).astype(np.float32)
        K = int(max(2, smooth * 2))
        dd = np.concatenate((self._dtail, d))
        v = A._smooth(dd, smooth).astype(np.float32)[len(self._dtail):]
        self._dtail = dd[-K:]
        return v

    @staticmethod
    def levels_from(feat, n, nominal_spacing, center=None):
        """Tonernas frekvenser i kanalutsnittets ram, ur mätningen eller nominellt kring mitten."""
        mid = feat.get("f_mid", 0.0) if center is None else center
        pk = np.sort(np.asarray(feat.get("fi_peaks") or [], float)) + feat.get("f_mid", 0.0)
        if len(pk) == n and n >= 2:
            sp = np.diff(pk)
            if np.all(sp > 0.6 * nominal_spacing) and np.all(sp < 1.5 * nominal_spacing):
                return pk
        return mid + (np.arange(n) - (n - 1) / 2) * nominal_spacing

    def msg(self, typ, src, text, name="", kind=""):
        self.msgs.append({"time": time.time(), "type": typ, "src": src, "name": name, "kind": kind,
                          "text": text, "pos": None})


class SymClock:
    """Symbolbeslut med klockåterhämtning ur övergångarna mellan nivåer (2-, 4- eller 8-FSK)."""

    def __init__(self, sps):
        self.sps = float(sps)
        self.tail = np.zeros(0, np.float32)
        self.t = self.sps / 2
        self.prev = None

    def run(self, x, levels):
        levels = np.asarray(levels, float)
        sps = self.sps
        buf = np.concatenate((self.tail, x))
        t = self.t
        out = []
        n = len(buf)
        hw = max(1, int(sps * 0.2))
        while t + hw + 1 < n:
            i = int(t)
            val = float(buf[max(0, i - hw):i + hw + 1].mean())
            k = int(np.argmin(np.abs(levels - val)))
            if self.prev is not None and k != self.prev:
                mid = (levels[k] + levels[self.prev]) / 2
                a, b = max(0, int(t - sps)), i
                if b - a > 2:
                    seg = buf[a:b] - mid
                    ch = np.flatnonzero(np.signbit(seg[1:]) != np.signbit(seg[:-1]))
                    if len(ch):
                        c = a + float(ch[len(ch) // 2]) + 0.5
                        err = c - (t - sps / 2)
                        t += float(np.clip(err, -sps / 4, sps / 4)) * 0.2
            self.prev = k
            out.append(k)
            t += sps
        cut = max(0, int(t - 2 * sps))
        self.tail = buf[cut:]
        self.t = t - cut
        return out


# ════════════════════════════════════════════════════════════════════
# NAVTEX / SITOR-B
# ════════════════════════════════════════════════════════════════════
# kod → tecken (fldigi navtex.cxx code_to_ltrs / code_to_figs)
_LT = {0x17: "J", 0x1B: "F", 0x1D: "C", 0x1E: "K", 0x27: "W", 0x2B: "Y", 0x2D: "P", 0x2E: "Q", 0x35: "G",
       0x39: "M", 0x3A: "X", 0x3C: "V", 0x47: "A", 0x4B: "S", 0x4D: "I", 0x4E: "U", 0x53: "D", 0x55: "R",
       0x56: "E", 0x59: "N", 0x5C: " ", 0x63: "Z", 0x65: "L", 0x69: "H", 0x6C: "\n", 0x71: "O", 0x72: "B",
       0x74: "T", 0x78: "\r"}
_FG = {0x17: "'", 0x1B: "!", 0x1D: ":", 0x1E: "(", 0x27: "2", 0x2B: "6", 0x2D: "0", 0x2E: "1", 0x35: "&",
       0x39: ".", 0x3A: "/", 0x3C: ";", 0x47: "-", 0x4B: "\x07", 0x4D: "8", 0x4E: "7", 0x53: "$", 0x55: "4",
       0x56: "3", 0x59: ",", 0x5C: " ", 0x63: '"', 0x65: ")", 0x69: "#", 0x6C: "\n", 0x71: "9", 0x72: "?",
       0x74: "5", 0x78: "\r"}
CCIR_LTRS = "".join(_LT.get(i, "_") for i in range(128))
CCIR_FIGS = "".join(_FG.get(i, "_") for i in range(128))
C_LTRS, C_FIGS, C_ALPHA, C_BETA, C_BLK, C_REP = 0x5A, 0x36, 0x0F, 0x33, 0x6A, 0x66
CCIR_VALID = {c for c in range(128) if bin(c).count("1") == 4}
NAVTEX_AMNE = {"A": "Navigationsvarning", "B": "Meteorologisk varning", "C": "Israpport",
               "D": "Sök och räddning / pirater", "E": "Väderprognos", "F": "Lotsmeddelande",
               "G": "AIS", "H": "LORAN", "J": "Satellitnavigering", "K": "Övriga navigationshjälpmedel",
               "L": "Navigationsvarning (forts.)", "T": "Test", "V": "Fiskemeddelande", "W": "Miljö",
               "X": "Särskild tjänst", "Y": "Särskild tjänst", "Z": "Inga meddelanden"}


class NavtexAvkodare(A.TvatonsBas, FiAvkodare):
    """NAVTEX/SITOR-B: 100 Bd, 170 Hz, 7-bitars CCIR-kod med fyra ettor, FEC (repetition).
    Bitbesluten kommer från TvatonsBas (filter per ton, nivå per ton, tonsökning kring klicket,
    störningsspärr); klockan, teckensynken och FEC-avkodningen är NAVTEX-egna."""
    namn = "NAVTEX / SITOR-B"
    SHIFTAR = (170.0,)
    PRIORITET = 1      # FEC och 4-av-7-koden: säkrare synk än RTTY på samma takt och shift

    def __init__(self):
        self.reset()

    def reset(self):
        FiAvkodare.reset(self)
        self._tva_reset()
        self.ptr = None                  # absolut index: hit har bitbesluten lästs
        self.clk = None
        self.bits = []
        self.chars = []
        self.align = None
        self.valid_hist = []
        self.par = None
        self.pos = 0
        self.figs = False
        self.text = ""
        self.msgbuf = None
        self.errs = 0
        self.ok = 0

    def passar(self, feat):
        b, s = feat.get("baud"), feat.get("shift")
        # takten saknas ofta i en svag signal; 170 Hz shift räcker då (avkodaren kontrollerar själv)
        return feat.get("fam") == "FSK2" and s and 120 <= s <= 260 and (
            not b or abs(b - 100) < 5 or abs(b - 50) < 2.5)

    def har_synk(self):
        return self.align is not None and self.par is not None

    def fakta(self):
        return {"baud": 100.0, "fam": "FSK2", "klass": "2-FSK (SITOR-B 100 Bd)", "shift": 170.0, "toner": 2}

    def feed(self, z, fs, feat):
        if not self._tva_mata(z, fs, feat):
            return ""
        x = self._beslut(100.0)                        # > 0: mark (högre tonen) = 1
        marg = int(self.ATC_S * self.RR / 2) + 1
        if self.ptr is None or self.ptr < self.base:
            self.ptr = self.base
        a, b = self.ptr - self.base, len(x) - marg
        if b > a:
            self.ptr += b - a
            if self.snr >= self.MIN_SNR:               # brusspärr: inga bitar ur bara brus
                if self.clk is None:
                    self.clk = SymClock(self.RR / 100.0)
                for k in self.clk.run(x[a:b], (-1.0, 1.0)):
                    self.bits.append(1 if k == 1 else 0)
            else:
                self.clk = None
        self._trimma(self.ptr)
        out = self._frame()
        if len(self.bits) > 4000:
            drop = len(self.bits) - 1000
            if self.align is not None:
                drop = min(drop, self.align_pos)
                self.align_pos -= drop
            del self.bits[:drop]
        return out

    def _code(self, i):
        c = 0
        for k in range(7):
            c |= self.bits[i + k] << k                # första biten = b0 (som fldigi)
        return c

    def _frame(self):
        n = len(self.bits)
        if self.align is None:
            if n < 140:
                return ""

            def score(a):
                sc = 0
                for i in range(n - 140 + a, n - 7, 7):
                    c = self._code(i)
                    sc += (c in CCIR_VALID) + 2 * (c in (C_ALPHA, C_REP))
                return sc
            best = max(range(7), key=score)
            cnt = sum(self._code(i) in CCIR_VALID for i in range(n - 140 + best, n - 7, 7))
            if cnt < 17:
                return ""
            self.align = best
            self.align_pos = n - 140 + best
        out = []
        i = self.align_pos
        while i + 7 <= len(self.bits):
            c = self._code(i)
            self.chars.append(c)
            self.valid_hist.append(c in CCIR_VALID)
            i += 7
        self.align_pos = i
        self.valid_hist = self.valid_hist[-40:]
        if len(self.valid_hist) >= 30 and sum(self.valid_hist) < 20:
            self.align = None                          # tappat synk
            self.chars = []
            self.par = None
            return "".join(out)
        out.append(self._fec())
        return "".join(out)

    def _fec(self):
        ch = self.chars
        if self.par is None:
            if len(ch) < 30:
                return ""
            # fasning: rep sänds i DX-läget (första sändningen), alpha i RX-läget
            reps = [p % 2 for p, c in enumerate(ch) if c == C_REP]
            if len(reps) >= 4:
                self.par = int(round(np.mean(reps)))
            else:
                sc = [sum(1 for p in range(par, len(ch) - 5, 2) if ch[p] == ch[p + 5] and ch[p] in CCIR_VALID)
                      for par in (0, 1)]
                if max(sc) < 6:
                    return ""
                self.par = int(np.argmax(sc))
            self.pos = self.par
        out = []
        p = self.pos
        while p + 5 < len(ch):
            a, b = ch[p], ch[p + 5]
            c = a if a in CCIR_VALID else (b if b in CCIR_VALID else None)
            if a in CCIR_VALID and b in CCIR_VALID and a != b:
                c = a
            if c is None:
                self.errs += 1
                out.append(self._put("_"))
            else:
                self.ok += 1
                if c == C_LTRS:
                    self.figs = False
                elif c == C_FIGS:
                    self.figs = True
                elif c in (C_ALPHA, C_BETA, C_REP, C_BLK):
                    pass
                else:
                    t = (CCIR_FIGS if self.figs else CCIR_LTRS)[c]
                    if t != "_" and t != "\x07":
                        out.append(self._put("\n" if t == "\r" else t))
            p += 2
        self.pos = p
        if len(ch) > 2000:
            cut = (len(ch) - 200) // 2 * 2
            del ch[:cut]
            self.pos -= cut
        return "".join(s for s in out if s)

    def _put(self, t):
        if t == "\n" and self.text.endswith("\n"):
            return ""
        self.text = (self.text + t)[-4000:]
        if self.text.endswith("ZCZC"):
            self.msgbuf = "ZCZC"
        elif self.msgbuf is not None:
            self.msgbuf += t
            if self.msgbuf.endswith("NNNN") or len(self.msgbuf) > 6000:
                self._emit(self.msgbuf)
                self.msgbuf = None
        return t

    def _emit(self, m):
        lines = m[4:].strip().split("\n")
        hid = lines[0].strip()[:4] if lines else ""
        amne = NAVTEX_AMNE.get(hid[1:2], "") if len(hid) >= 2 else ""
        body = " ".join(x.strip() for x in lines[1:]).replace("NNNN", "").strip()
        self.msg("NAVTEX", hid[:1], body, name=hid, kind=amne)

    def varden(self):
        if self.align is None:
            return [("NAVTEX", "söker teckensynk …")]
        tot = self.ok + self.errs
        return [("NAVTEX", "synk, FEC " + (f"{100 * self.ok / tot:.0f} % rätt" if tot else "väntar"))]


def make_sitor_b(fs, text, shift=170.0):
    """Testsignal: SITOR-B med FEC (repetition 5 tecken senare), första biten = b0."""
    bits = sitor_b_bitar(text)
    sps = fs / 100.0
    n = int(len(bits) * sps)
    idx = np.minimum((np.arange(n) / sps).astype(np.int64), len(bits) - 1)
    f = np.where(np.asarray(bits)[idx] == 1, shift / 2, -shift / 2)
    return np.exp(2j * np.pi * np.cumsum(f) / fs).astype(np.complex64)


def sitor_b_bitar(text, fasning=60):
    """SITOR-B-bitar (100 Bd, 1 = mark) för texten: fasning, FEC-sändning och avslutning."""
    rev_l = {c: i for i, c in _LT.items()}
    rev_f = {c: i for i, c in _FG.items()}
    codes = []
    figs = False
    for ch in text.upper().replace("\n", "\r\n"):
        if ch in rev_l:
            if figs and ch not in " \r\n":
                codes.append(C_LTRS)
                figs = False
            codes.append(rev_l[ch])
        elif ch in rev_f:
            if not figs:
                codes.append(C_FIGS)
                figs = True
            codes.append(rev_f[ch])
    seq = []
    pre = [C_REP, C_ALPHA] * fasning     # fasning (verklig NAVTEX: ca 10 s)
    seq += pre
    # position 2k = första sändning (DX), 2k+5 = repetitionen (RX)
    L = len(codes) + 3
    slots = [None] * (2 * L + 10)
    for k, c in enumerate(codes):
        slots[2 * k] = c
        slots[2 * k + 5] = c
    for i, s in enumerate(slots):
        if s is None:
            slots[i] = C_ALPHA if i % 2 else C_REP
    seq += slots + [C_REP, C_ALPHA] * 6
    bits = []
    for c in seq:
        bits += [(c >> k) & 1 for k in range(7)]
    return bits


# ════════════════════════════════════════════════════════════════════
# POCSAG
# ════════════════════════════════════════════════════════════════════
POCSAG_SYNC = 0x7CD215D8
POCSAG_IDLE = 0x7A89C197
POCSAG_NUM = "0123456789*U -)("


def _bch_rem(cw31):
    g = 0x769
    v = cw31
    for i in range(30, 9, -1):
        if v & (1 << i):
            v ^= g << (i - 10)
    return v & 0x3FF


def pocsag_ok(cw):
    return _bch_rem(cw >> 1) == 0 and bin(cw).count("1") % 2 == 0


def pocsag_fix(cw):
    """Kontrollerar och rättar högst ett bitfel. Returnerar kodordet eller None."""
    if pocsag_ok(cw):
        return cw
    for i in range(32):
        c = cw ^ (1 << i)
        if pocsag_ok(c):
            return c
    return None


def pocsag_encode(data21):
    cw = data21 << 10
    cw |= _bch_rem(cw)
    cw = (cw << 1) | (bin(cw).count("1") & 1)
    return cw


class PocsagAvkodare(A.TvatonsBas, FiAvkodare):
    """POCSAG 512/1200/2400 Bd. Bitbesluten kommer från TvatonsBas (ett filter per ton, nivå per ton,
    tonsökning kring klicket) i stället för frekvensdiskriminator: klarar svaga signaler bättre, och en
    stark station i grannkanalen fångar inte avkodaren. Alla tre takterna körs parallellt; synkordet
    och BCH-kontrollen avgör (fel takt synkar inte)."""
    namn = "POCSAG"
    RATES = (512, 1200, 2400)
    SHIFTAR = (6000.0, 7000.0, 8000.0, 9000.0, 9600.0)     # deviation ±3–4,8 kHz
    RR = 24000.0
    SOK_BAS = 2000.0                                        # sändaren kan ligga ett par kHz fel
    SOK_STEG = 20.0
    HIST_S = 3.0
    MIN_SNR = -99.0    # ingen brusspärr: tonerna är breda (toppen syns dåligt), och synkordet och BCH
                       # släpper ändå inte igenom skräp

    def __init__(self):
        self.reset()

    def reset(self):
        FiAvkodare.reset(self)
        self._tva_reset()
        self.spar = {r: {"clk": None, "ptr": None, "bits": [[], []], "state": [None, None]} for r in self.RATES}
        self.rate = None
        self.n_msgs = 0
        self.pol = None

    def passar(self, feat):
        b, s = feat.get("baud"), feat.get("shift")
        if feat.get("fam") not in ("FSK2", "MSK"):
            # svag bred FSK klassas ofta som SSB/LIN: på VHF/UHF (där personsökare finns, inte AM-
            # rundradio) räcker en uppmätt bredd som ett POCSAG-par – synkordet och BCH avgör sedan
            bw = feat.get("bredd_uppmatt") or 0
            return feat.get("fam") != "BRUS" and (feat.get("f_abs") or 0) >= 25e6 and 5000 <= bw <= 14000
        # bred FSK (2–12 kHz shift) räcker: takten avgörs av synkordet (analysens takt är osäker)
        return (not s or 2000 <= s <= 12000) and (not b or any(abs(b - r) / r < 0.1 for r in self.RATES)
                                                    or (s and s >= 2000))

    def feed(self, z, fs, feat):
        if not self._tva_mata(z, fs, feat):
            return ""
        out = []
        marg = int(self.ATC_S * self.RR / 2) + 1
        for rate, sp in self.spar.items():
            x = self._beslut(float(rate))
            if sp["ptr"] is None or sp["ptr"] < self.base:
                sp["ptr"] = self.base
            a, b = sp["ptr"] - self.base, len(x) - marg
            if b <= a:
                continue
            sp["ptr"] += b - a
            if self.snr < self.MIN_SNR:
                sp["clk"] = None
                continue
            if sp["clk"] is None:
                sp["clk"] = SymClock(self.RR / rate)
            syms = sp["clk"].run(x[a:b], (-1.0, 1.0))      # 1 = högre tonen
            self.bits, self.state = sp["bits"], sp["state"]
            for pol in (0, 1):
                # POCSAG: binär 1 = lägre frekvens (normal polaritet = pol 0)
                self.bits[pol] += [(1 - k) if pol == 0 else k for k in syms]
                txt = self._scan(pol)
                if txt or (self.pol is not None and self.rate is None):
                    self.rate = rate                     # den takt som synkar
                out.append(txt)
        self._trimma(min(sp["ptr"] for sp in self.spar.values() if sp["ptr"] is not None))
        return "".join(out)

    def _scan(self, pol):
        bits = self.bits[pol]
        st = self.state[pol]
        out = []
        i = 0
        n = len(bits)
        while True:
            if st is None:
                # sök synkordet (högst 2 fel)
                found = None
                w = 0
                for j in range(i, n):
                    w = ((w << 1) | bits[j]) & 0xFFFFFFFF
                    if j - i >= 31 and bin(w ^ POCSAG_SYNC).count("1") <= 2:
                        found = j + 1
                        break
                if found is None:
                    i = max(i, n - 31)
                    break
                st = {"pos": found, "cw": 0, "msg": None}
                i = found
            if st["pos"] + 32 > n:
                break
            w = 0
            for b in bits[st["pos"]:st["pos"] + 32]:
                w = (w << 1) | b
            st["pos"] += 32
            if st["cw"] == 16:                  # efter en batch ska nästa synk komma
                st["cw"] = 0
                if bin(w ^ POCSAG_SYNC).count("1") > 3:
                    out.append(self._end(st))
                    st = None
                    i = max(0, n - 31) if n - 31 > i else i
                    continue
                continue
            idx = st["cw"]
            st["cw"] += 1
            cw = pocsag_fix(w)
            if cw is None or cw == POCSAG_IDLE:
                out.append(self._end(st))
                continue
            if self.pol is None:
                self.pol = pol
            if cw >> 31 == 0:                   # adress
                out.append(self._end(st))
                addr = (((cw >> 13) & 0x3FFFF) << 3) | (idx // 2)
                st["msg"] = {"addr": addr, "func": (cw >> 11) & 3, "data": []}
            elif st["msg"] is not None:
                st["msg"]["data"].append((cw >> 11) & 0xFFFFF)
        self.state[pol] = st
        cut = (st["pos"] if st else i) - 64
        if cut > 0:
            del bits[:cut]
            if st:
                st["pos"] -= cut
        return "".join(out)

    def _end(self, st):
        m = st.get("msg") if st else None
        if not m:
            return ""
        st["msg"] = None
        bits = []
        for d in m["data"]:
            bits += [(d >> (19 - k)) & 1 for k in range(20)]
        num = "".join(POCSAG_NUM[sum(bits[i + k] << k for k in range(4))] for i in range(0, len(bits) - 3, 4))
        alpha = ""
        for i in range(0, len(bits) - 6, 7):
            c = sum(bits[i + k] << k for k in range(7))
            if c in (0, 4, 23):     # NUL/EOT/ETB = slut
                break
            alpha += chr(c) if 32 <= c < 127 else ("\n" if c == 10 else "")
        printable = sum(ch.isalnum() or ch in " .,:-/" for ch in alpha) / max(1, len(alpha))
        if not m["data"]:
            text, kind = "(endast ton)", "ton"
        elif m["func"] == 0 or (printable < 0.7 and m["func"] != 3):
            text, kind = num.strip(), "numeriskt"
        else:
            text, kind = alpha.strip(), "text"
        self.n_msgs += 1
        self.msg("POCSAG", str(m["addr"]), text, name=f"funktion {m['func']}", kind=kind)
        return f"[{m['addr']}/{m['func']}] {text}\n"

    def har_synk(self):
        return self.pol is not None

    def fakta(self):
        if not self.rate:
            return {}
        return {"baud": float(self.rate), "fam": "FSK2", "klass": f"2-FSK (POCSAG {self.rate} Bd)"}

    def varden(self):
        return [("POCSAG", f"{self.rate} Bd, {self.n_msgs} meddelanden" if self.rate else "–")]


def make_pocsag(fs, rate, pages, shift=9000.0):
    """Testsignal: [(adress, funktion, text eller None, numeriskt?)]."""
    bits = pocsag_bitar(pages)
    sps = fs / rate
    n = int(len(bits) * sps)
    idx = np.minimum((np.arange(n) / sps).astype(np.int64), len(bits) - 1)
    f = np.where(np.asarray(bits)[idx] == 1, -shift / 2, shift / 2)
    return np.exp(2j * np.pi * np.cumsum(f) / fs).astype(np.complex64)


def pocsag_bitar(pages, ingress=288):
    """POCSAG-bitar för sidorna: ingress (1010…), batcher med synkord, adress- och meddelandekodord."""
    bits = [1, 0] * ingress
    cws = []
    for addr, func, text, numeric in pages:
        frame = addr & 7
        batch = [POCSAG_IDLE] * 16
        batch[frame * 2] = pocsag_encode(((addr >> 3) << 2 | func) & 0x1FFFFF)
        data = []
        if text:
            if numeric:
                rev = {c: i for i, c in enumerate(POCSAG_NUM)}
                for ch in text:
                    v = rev[ch]
                    data += [(v >> k) & 1 for k in range(4)]
                while len(data) % 20:                     # fyll ut med mellanslag (som riktiga sändare)
                    data += [(12 >> k) & 1 for k in range(4)]
            else:
                for ch in text:
                    data += [(ord(ch) >> k) & 1 for k in range(7)]
            data += [0] * (-len(data) % 20)
        words = [int("".join(map(str, data[i:i + 20])), 2) for i in range(0, len(data), 20)]
        pos = frame * 2 + 1
        rest = []
        for wd in words:
            cw = pocsag_encode((1 << 20) | wd)
            if pos < 16:
                batch[pos] = cw
                pos += 1
            else:
                rest.append(cw)
        cws.append(batch)
        while rest:
            b2 = [POCSAG_IDLE] * 16
            for k in range(min(16, len(rest))):
                b2[k] = rest.pop(0)
            cws.append(b2)
    for batch in cws:
        for cw in [POCSAG_SYNC] + batch:
            bits += [(cw >> (31 - k)) & 1 for k in range(32)]
    bits += [1, 0] * 32
    return bits


# ════════════════════════════════════════════════════════════════════
# ALE 2G (MIL-STD-188-141)
# ════════════════════════════════════════════════════════════════════
# Golay (24,12): paritetsmatris P ur figur A-6 (rad i = informationsbit i, W1 först)
_GOLAY_P = ["101011100011", "111110010010", "110100101011", "110001110110", "110011011001", "011001101101",
            "001100110111", "101101111000", "010110111100", "001011011110", "101110001101", "010111000111"]
GOLAY_P = [int(r, 2) for r in _GOLAY_P]
ALE_TONE_BITS = [0b000, 0b001, 0b011, 0b010, 0b110, 0b111, 0b101, 0b100]   # 750, 1000 … 2500 Hz
ALE_PRE = {0b001: "THRU", 0b010: "TO", 0b110: "CMD", 0b100: "FROM", 0b101: "TIS", 0b011: "TWAS",
           0b000: "DATA", 0b111: "REP"}
ALE_BASIC = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@?")


def golay_parity(d12):
    p = 0
    for i in range(12):
        if d12 & (1 << (11 - i)):
            p ^= GOLAY_P[i]
    return p


_GOLAY_SYN = None


def golay_decode(cw24):
    """Rättar upp till 3 fel. Returnerar (12 databitar, antal fel) eller (None, -1)."""
    global _GOLAY_SYN
    if _GOLAY_SYN is None:
        tab = {}
        import itertools
        for w in range(0, 4):
            for pos in itertools.combinations(range(24), w):
                e = 0
                for p in pos:
                    e |= 1 << p
                s = golay_parity(e >> 12) ^ (e & 0xFFF)
                tab.setdefault(s, (e, w))
        _GOLAY_SYN = tab
    d, p = cw24 >> 12, cw24 & 0xFFF
    s = golay_parity(d) ^ p
    hit = _GOLAY_SYN.get(s)
    if hit is None:
        return None, -1
    e, w = hit
    return ((cw24 ^ e) >> 12) & 0xFFF, w


def ale_encode_word(pre, chars):
    """24-bitars ALE-ord → 49 sända bitar (A1 B1 … A24 B24 S49)."""
    w = pre << 21
    for k, ch in enumerate(chars[:3].ljust(3, "@")):
        w |= (ord(ch) & 0x7F) << (14 - 7 * k)
    a, b = w >> 12, w & 0xFFF
    A_ = (a << 12) | golay_parity(a)
    B_ = (b << 12) | (golay_parity(b) ^ 0xFFF)
    bits = []
    for i in range(24):
        bits.append((A_ >> (23 - i)) & 1)
        bits.append((B_ >> (23 - i)) & 1)
    return bits + [0]


def make_ale(fs, words, lsb=False):
    """Testsignal: lista med (preamble-namn, 3 tecken); varje ord sänds tre gånger."""
    rev = {v: k for k, v in ALE_PRE.items()}
    bits = []
    for name, chars in words:
        wb = ale_encode_word(rev[name], chars)
        bits += wb * 3
    bits += [0] * (-len(bits) % 3)
    tone_of = {b: i for i, b in enumerate(ALE_TONE_BITS)}
    syms = [tone_of[(bits[i] << 2) | (bits[i + 1] << 1) | bits[i + 2]] for i in range(0, len(bits), 3)]
    syms = [0, 7] * 4 + syms + [0] * 4
    sps = fs / 125.0
    n = int(len(syms) * sps)
    idx = np.minimum((np.arange(n) / sps).astype(np.int64), len(syms) - 1)
    f = (np.asarray(syms)[idx] - 3.5) * 250.0 * (-1 if lsb else 1)
    return np.exp(2j * np.pi * np.cumsum(f) / fs).astype(np.complex64)


class AleAvkodare(FiAvkodare):
    namn = "ALE 2G (MIL-STD-188-141)"

    def __init__(self):
        self.reset()

    def reset(self):
        super().reset()
        self.clk = None
        self.bits = [[], []]       # normal och spegelvänd tonordning (USB/LSB)
        self.scanned = [0, 0]
        self.phase = [None, None]
        self.miss = [0, 0]
        self.last = [None, None]
        self.last_end = [0, 0]
        self.frame = []
        self.n_words = 0
        self.inv = None
        self.calls = []
        self.ybuf = np.zeros(0, np.complex64)
        self.ztail = np.zeros(0, np.complex64)
        self.n0 = 0
        self.c8 = None

    def passar(self, feat):
        fam, b, s, nt = feat.get("fam"), feat.get("baud"), feat.get("shift"), feat.get("toner")
        if fam != "MFSK":
            # ALE klassas ofta fel (det upprepade TO-ordet ger linjer som PSK): på kortvåg räcker en
            # signal som är lika bred som ALE (750–2500 Hz) – Golay, tripplingen och ordformatet avgör
            bw = feat.get("bredd_uppmatt") or 0
            return fam != "BRUS" and 0 < (feat.get("f_abs") or 0) < 30e6 and 1500 <= bw <= 2700
        return (b and abs(b - 125) < 6) or (s and abs(s - 250) < 30 and nt and 6 <= nt <= 8)

    def _toner8(self, z, fs, feat):
        """Ett filter per ton (8 toner, 250 Hz isär): index för den starkaste tonen per sampel.
        Tonkammens mitt söks i spektrumet kring klicket."""
        z = np.asarray(z, np.complex64)
        L = max(2, int(round(0.8 * fs / 125.0)))
        ref = feat.get("klick_off", feat.get("f_mid", 0.0)) if feat else 0.0
        self.ybuf = np.concatenate((self.ybuf, z))[-int(fs * 1.5):]
        if len(self.ybuf) >= int(fs * 0.5):
            nf = 1 << int(math.log2(len(self.ybuf)))
            P = np.abs(np.fft.fft(self.ybuf[-nf:] * np.hanning(nf))) ** 2
            f = np.fft.fftfreq(nf, 1 / fs)
            o = np.argsort(f)
            f, P = f[o], A._smooth(P[o], max(1, int(round(10.0 / (fs / nf)))))
            cc = np.arange(ref - 150, ref + 150.1, 2.0)
            k8 = (np.arange(8) - 3.5) * 250.0
            e = np.array([np.sum(np.log(np.interp(c + k8, f, P) + 1e-30)) for c in cc])
            c = float(cc[int(np.argmax(e))])
            self.c8 = c if self.c8 is None else 0.8 * self.c8 + 0.2 * c
        c = self.c8 if self.c8 is not None else ref
        x = np.concatenate((self.ztail, z))
        t = (self.n0 - len(self.ztail) + np.arange(len(x))) / fs
        env = []
        for k in range(8):
            m = x * np.exp(-2j * np.pi * (c + (k - 3.5) * 250.0) * t)
            cs = np.concatenate(([0], np.cumsum(m)))
            mm = np.abs(cs[L:] - cs[:-L])
            env.append(mm)
        env = np.asarray(env)
        idx = np.argmax(env, axis=0).astype(np.float32)
        # utjämnad position: filtret ligger L sampel efter; svansen ger kontinuitet över styckena
        self.ztail = x[-(L - 1):] if L > 1 else x[:0]
        self.n0 += len(z)
        return idx[-len(z):] if len(idx) >= len(z) else np.concatenate((np.full(len(z) - len(idx), 3.5, np.float32), idx))

    def feed(self, z, fs, feat):
        sps = fs / 125.0
        if self.clk is None:
            self.clk = SymClock(sps)
        lv = np.arange(8, dtype=float)
        v = self._toner8(z, fs, feat)
        out = []
        for k in self.clk.run(v, lv):
            for o in (0, 1):
                t = ALE_TONE_BITS[k if o == 0 else 7 - k]
                self.bits[o] += [(t >> 2) & 1, (t >> 1) & 1, t & 1]
        for o in (0, 1):
            out.append(self._scan(o))
        return "".join(out)

    def _scan(self, o):
        b = self.bits[o]
        out = []
        n = len(b)
        start = max(147, self.scanned[o] + 1)
        arr = np.asarray(b, np.int8)
        for end in range(start, n + 1):
            ph = self.phase[o]
            if ph is not None and end - self.last_end[o] > 4 * 49:
                ph = self.phase[o] = None     # inget giltigt ord på en stund – släpp låsningen
                self.last[o] = None
            if ph is not None and (end - ph) % 49 != 0:
                continue                      # ordfasen är låst: bara ordgränser (stoppar roterade ord)
            seg = arr[end - 147:end]
            c1, c2, c3 = seg[:49], seg[49:98], seg[98:147]
            maj = ((c1.astype(int) + c2 + c3) >= 2).astype(int)
            unan = int(np.sum((c1 == c2) & (c2 == c3)))
            w = self._word(maj[:48]) if unan >= 34 and not (c1[48] | c2[48] | c3[48]) else None
            if w is None:
                continue
            self.phase[o] = end % 49 if ph is None else ph
            if self.inv is None:
                self.inv = o
            if self.last[o] == w and end - self.last_end[o] <= 3 * 49:
                self.last_end[o] = end        # samma ord igen (tripplat eller skanningsanrop)
                continue
            self.last[o] = w
            self.last_end[o] = end
            out.append(self._handle(*w))
        self.scanned[o] = n
        keep = max(0, n - 200)
        if keep:
            del b[:keep]
            self.scanned[o] -= keep
            self.last_end[o] -= keep
            if self.phase[o] is not None:
                self.phase[o] = (self.phase[o] - keep) % 49
        return "".join(out)

    @staticmethod
    def _word(m48):
        A_ = 0
        B_ = 0
        for i in range(24):
            A_ = (A_ << 1) | int(m48[2 * i])
            B_ = (B_ << 1) | int(m48[2 * i + 1])
        B_ ^= 0xFFF                          # kontrollbitarna i B är inverterade
        a, ea = golay_decode(A_)
        bb, eb = golay_decode(B_)
        if a is None or bb is None or ea + eb > 3:
            return None
        w = (a << 12) | bb
        pre = ALE_PRE[w >> 21]
        chars = "".join(chr((w >> (14 - 7 * k)) & 0x7F) for k in range(3))
        if pre in ("TO", "TIS", "TWAS", "THRU", "FROM", "DATA", "REP"):
            if not all(ch in ALE_BASIC for ch in chars):
                return None
        elif not all(32 <= ord(ch) < 127 for ch in chars):
            return None
        return pre, chars

    def _handle(self, pre, txt):
        self.n_words += 1
        if pre in ("DATA", "REP") and self.frame:
            self.frame[-1][1] += txt
            # skanningsanrop: samma adress upprepas (TO SM5, DATA SIM, TO SM5 …) – slå ihop
            if len(self.frame) >= 2 and self.frame[-1] == self.frame[-2]:
                self.frame.pop()
        else:
            if self.frame and self.frame[-1][0] == pre and self.frame[-1][1].startswith(txt) and pre == "TO":
                self.frame.append([pre, txt])
            else:
                self.frame.append([pre, txt])
        out = f"[{pre}] {txt}\n"
        if pre in ("TIS", "TWAS"):
            parts = []
            for p, t in self.frame:
                e = f"{p} {t.rstrip('@')}"
                if p in ("TO", "THRU", "TIS", "TWAS", "FROM", "CMD") and e not in parts:
                    parts.append(e)
            frm = self.frame[-1][1].rstrip("@")
            to = list(dict.fromkeys(t.rstrip("@") for p, t in self.frame if p in ("TO", "THRU")))
            kind = ("Anrop" if to else "Sondering") + (" (avböjd)" if pre == "TWAS" else "")
            self.msg("ALE", frm, " · ".join(parts), name=", ".join(to), kind=kind)
            self.calls.append(f"{frm}→{','.join(to) or '–'}")
            out += f"   ⇒ {kind}: {frm}" + (f" anropar {', '.join(to)}" if to else "") + "\n"
            self.frame = []
        elif len(self.frame) > 40:
            self.frame = self.frame[-10:]
        return out

    def har_synk(self):
        return self.inv is not None and any(p is not None for p in self.phase)

    def fakta(self):
        return {"baud": 125.0, "klass": "8-FSK (ALE 2G)"}

    def varden(self):
        v = [("ALE", f"{self.n_words} ord" + ("" if self.inv is None else
                                              (" (USB)" if self.inv == 0 else " (spegelvänd/LSB)")))]
        if self.calls:
            v.append(("Senaste anrop", self.calls[-1]))
        return v


# ════════════════════════════════════════════════════════════════════
# Väderfax (HF-fax)
# ════════════════════════════════════════════════════════════════════
class FaxAvkodare(FiAvkodare):
    namn = "Väderfax (HF-fax)"
    LPM = 120
    W = 1200

    def __init__(self):
        self.reset()

    def reset(self):
        super().reset()
        self.buf = np.zeros(0, np.float32)
        self.rows = []
        self.bild = None           # uint8 (rader, W) – läses av GUI:t
        self.bild_id = 0
        self.offset = 0
        self.lo = None
        self.hi = None
        self.status = "väntar på signal"
        self.zc_hist = []
        self.fax_synk = False      # startton eller fasningspuls sedd – först då är det säkert fax

    def passar(self, feat):
        k = feat.get("kandidater") or []
        if k and k[0][1]["namn"].startswith("Väderfax"):
            return True
        occ, fam = feat.get("occ_bw") or 0, feat.get("fam")
        return fam in ("FM", "FSK2") and 650 <= occ <= 1300 and not feat.get("baud")

    def feed(self, z, fs, feat):
        spl = fs * 60.0 / self.LPM                # samples per rad
        v = self._fi(z, fs, max(1, fs / 2400))
        mid = feat.get("f_mid", 0.0)
        # IOC-start (300 Hz) och stopp (450 Hz): räkna nollgenomgångar runt mitten
        if len(v) > fs * 0.2:
            zc = np.count_nonzero(np.diff(np.signbit(v - mid))) / (len(v) / fs) / 2
            spread = np.percentile(v, 90) - np.percentile(v, 10)
            if spread > 500:
                if abs(zc - 300) < 25:
                    if self.status != "startton":
                        self._new_image()
                    self.status = "startton"
                    self.fax_synk = True
                elif abs(zc - 450) < 30:
                    self.status = "stoppton – bilden klar"
        lo_, hi_ = np.percentile(v, 1), np.percentile(v, 99)
        if hi_ - lo_ > 400:
            self.lo = lo_ if self.lo is None else self.lo * 0.9 + lo_ * 0.1
            self.hi = hi_ if self.hi is None else self.hi * 0.9 + hi_ * 0.1
        if self.lo is None:
            return ""
        c = (self.lo + self.hi) / 2
        lum = np.clip((v - (c - 400)) / 800.0, 0, 1)
        self.buf = np.concatenate((self.buf, lum.astype(np.float32)))
        new = 0
        while len(self.buf) >= spl:
            n = int(spl)
            line = self.buf[:n]
            self.buf = self.buf[n:]
            # bråkdelen av samples per rad bärs över så att bilden inte lutar
            frac = spl - n
            self._acc = getattr(self, "_acc", 0.0) + frac
            if self._acc >= 1 and len(self.buf):
                self.buf = self.buf[1:]
                self._acc -= 1
            x = np.linspace(0, n - 1, self.W)
            row = (np.interp(x, np.arange(n), line) * 255).astype(np.uint8)
            self.rows.append(row)
            new += 1
        if new:
            self._phase()
            if self.status in ("väntar på signal", "startton"):
                self.status = "tar emot bild"
            img = np.asarray(self.rows[-1600:])
            self.bild = np.roll(img, -self.offset, axis=1)
            self.bild_id += 1
        return ""

    def _phase(self):
        """Fasning: hitta den smala pulsen (ca 5 % av raden) som återkommer på samma plats."""
        if len(self.rows) < 8:
            return
        m = np.asarray(self.rows[-20:], float).mean(axis=0) / 255
        w = int(self.W * 0.05)
        k = np.ones(w) / w
        mm = np.concatenate((m, m[:w]))
        s = np.convolve(mm, k, "valid")[:self.W]
        rest = float(np.median(m))
        dev = np.abs(s - rest)
        i = int(np.argmax(dev))
        others = np.delete(m, np.arange(i, i + w) % self.W)
        if dev[i] > 0.45 and np.std(others) < 0.18:      # bara under fasningsraderna
            self.offset = int(i % self.W)
            self.fax_synk = True

    def _new_image(self):
        self.rows = []
        self.offset = 0
        self.bild = None

    def har_synk(self):
        # rader ritas av vilken FM-signal som helst – kräv faxets startton eller fasningspuls
        return self.fax_synk and len(self.rows) > 10

    def fakta(self):
        return {"klass": "Väderfax (FM 1500–2300 Hz)", "baud": None}

    def varden(self):
        return [("Fax", f"{self.status}, {len(self.rows)} rader (120 rader/min)")]


def make_fax(fs, rows=40, width=400, shift=800.0):
    """Testsignal: starttón, fasningsrader och ett schackmönster."""
    spl = int(fs * 0.5)
    parts = []
    t = np.arange(int(fs * 3)) / fs
    parts.append(np.where(np.sin(2 * np.pi * 300 * t) > 0, 1.0, 0.0))
    for _ in range(15):          # fasning: vit rad med svart puls 5 %
        ln = np.ones(spl)
        ln[:int(spl * 0.05)] = 0
        parts.append(np.roll(ln, int(spl * 0.3)))
    for r in range(rows):
        x = np.arange(spl) / spl
        ln = (((x * 8).astype(int) + (r // 5)) % 2).astype(float)
        ln[:int(spl * 0.05)] = 0.0
        parts.append(np.roll(ln, int(spl * 0.3)))
    lum = np.concatenate(parts)
    f = (lum - 0.5) * shift
    return np.exp(2j * np.pi * np.cumsum(f) / fs).astype(np.complex64)


# ════════════════════════════════════════════════════════════════════
# DMR
# ════════════════════════════════════════════════════════════════════
DMR_SYNC = {0x755FD7DF75F7: ("BS", "tal"), 0xDFF57D75DF5D: ("BS", "data"),
            0x7F7D5DD57DFD: ("MS", "tal"), 0xD5D7F77FD757: ("MS", "data"),
            0x5D577F7757FF: ("direkt TS1", "tal"), 0xF7FDD5DDFD55: ("direkt TS1", "data"),
            0x7DFFD5F55D5F: ("direkt TS2", "tal"), 0xD7557F5FF7F5: ("direkt TS2", "data")}
DMR_DT = {0: "PI-header", 1: "Voice LC-header", 2: "Terminator med LC", 3: "CSBK", 4: "MBC-header",
          5: "MBC-fortsättning", 6: "Data-header", 7: "Data 1/2", 8: "Data 3/4", 9: "Tomgång", 10: "Data 1/1"}
# dibit per nivå i stigande frekvensordning: -3, -1, +1, +3
DMR_DIBIT = [0b11, 0b10, 0b00, 0b01]

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gmul(a, b):
    return 0 if a == 0 or b == 0 else _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def rs1293_parity(data9):
    """RS(12,9) över GF(256) (0x11D), g(x) = (x+α)(x+α²)(x+α³)."""
    g = [1]
    for r in (1, 2, 3):
        a = _GF_EXP[r]
        ng = [0] * (len(g) + 1)
        for i, c in enumerate(g):
            ng[i] ^= c
            ng[i + 1] ^= _gmul(c, a)
        g = ng
    reg = [0, 0, 0]
    for d in data9:
        fb = d ^ reg[0]
        reg = [reg[1] ^ _gmul(fb, g[1]), reg[2] ^ _gmul(fb, g[2]), _gmul(fb, g[3])]
    return reg


def bptc_extract(bits196, forward=True):
    """BPTC(196,96): avinterfoliera och plocka ut de 96 databitarna (utan Hamming-rättning)."""
    d = [0] * 196
    for i in range(196):
        j = (i * 181) % 196
        if forward:
            d[i] = bits196[j]
        else:
            d[j] = bits196[i]
    m = np.asarray(d[1:], np.int8).reshape(13, 15)
    return m[:9, :11].reshape(-1)[3:].tolist()


def bptc_build(data96):
    """Bygger 196 bitar (Hamming-paritet nollad – räcker för testsignaler)."""
    m = np.zeros((13, 15), np.int8)
    flat = [0, 0, 0] + list(data96)
    m[:9, :11] = np.asarray(flat, np.int8).reshape(9, 11)
    d = [0] + m.reshape(-1).tolist()
    out = [0] * 196
    for i in range(196):
        out[(i * 181) % 196] = d[i]
    return out


class DmrAvkodare(FiAvkodare):
    namn = "DMR"
    MASK = {1: (0x96, 0x96, 0x96), 2: (0x99, 0x99, 0x99)}

    def __init__(self):
        self.reset()

    def reset(self):
        super().reset()
        self.clk = None
        self.syms = []
        self.counts = {}
        self.cc = {}
        self.dt = {}
        self.lc = None
        self.priv = None
        self.inv = None
        self.n_lc = 0
        self.sync_w = [{}, {}]

    def passar(self, feat):
        b, fam, occ = feat.get("baud"), feat.get("fam"), feat.get("occ_bw") or 0
        if not (5000 < occ < 14000) or fam not in ("FSK4", "MFSK", "FM", "LIN", "OKÄND"):
            return False
        # synkordet avgör – avkodaren är billig och ger inga falsklarm på analog FM
        return bool((b and abs(b - 4800) < 150) or fam in ("FSK4", "MFSK", "FM"))

    def feed(self, z, fs, feat):
        sps = fs / 4800.0
        if self.clk is None:
            self.clk = SymClock(sps)
        lv = self.levels_from(feat, 4, 1296)
        v = self._fi(z, fs, max(1, sps / 3))
        self.syms += self.clk.run(v, lv)
        out = self._scan()
        if len(self.syms) > 6000:
            del self.syms[:len(self.syms) - 600]
        return out

    def _scan(self):
        s = self.syms
        out = []
        start = getattr(self, "_pos", 66)
        n = len(s)
        i = max(66, start)
        while i + 24 + 66 <= n:
            w = [0, 0]
            for k in range(24):
                d = DMR_DIBIT[s[i + k]]
                w[0] = (w[0] << 2) | d
                w[1] = (w[1] << 2) | DMR_DIBIT[3 - s[i + k]]
            hit = None
            for pol in (0, 1):
                for pat, info in DMR_SYNC.items():
                    if bin(w[pol] ^ pat).count("1") <= 4:
                        hit = (pol, info)
                        break
                if hit:
                    break
            if not hit:
                i += 1
                continue
            pol, (src, kind) = hit
            if self.inv is None:
                self.inv = pol
            key = f"{src} {kind}"
            self.counts[key] = self.counts.get(key, 0) + 1
            if kind == "data":
                out.append(self._data_burst(i, pol))
            i += 24 + 66        # hoppa förbi resten av burst
        self._pos = i
        if len(s) > 6000:
            self._pos -= len(s) - 600
        return "".join(out)

    def _bits(self, a, b, pol):
        out = []
        for k in range(a, b):
            d = DMR_DIBIT[self.syms[k] if pol == 0 else 3 - self.syms[k]]
            out += [(d >> 1) & 1, d & 1]
        return out

    def _data_burst(self, i, pol):
        st = self._bits(i - 5, i, pol) + self._bits(i + 24, i + 29, pol)
        cc = int("".join(map(str, st[0:4])), 2)
        dt = int("".join(map(str, st[4:8])), 2)
        self.cc[cc] = self.cc.get(cc, 0) + 1
        self.dt[dt] = self.dt.get(dt, 0) + 1
        out = ""
        if dt == 0 and self.priv is None:
            self.priv = "ja (PI-header sänds)"
        if dt in (1, 2):
            info = self._bits(i - 54, i - 5, pol) + self._bits(i + 29, i + 78, pol)
            for fwd in (True, False):
                d = bptc_extract(info, fwd)
                by = [int("".join(map(str, d[k:k + 8])), 2) for k in range(0, 96, 8)]
                par = rs1293_parity(by[:9])
                mk = self.MASK[dt]
                if [p ^ m for p, m in zip(par, mk)] == by[9:12]:
                    out = self._lc(by[:9], dt)
                    break
        return out

    def _lc(self, b, dt):
        flco = b[0] & 0x3F
        fid = b[1]
        so = b[2]
        dst = (b[3] << 16) | (b[4] << 8) | b[5]
        src = (b[6] << 16) | (b[7] << 8) | b[8]
        group = flco == 0
        enc = bool(so & 0x40)
        if enc:
            self.priv = "ja (privacy-flagga i LC)"
        elif self.priv is None or self.priv.startswith("nej"):
            self.priv = "nej (enligt LC)"
        lc = (("grupp " if group else "enskilt ") + f"{dst} från {src}"
              + (" · NÖDLÄGE" if so & 0x80 else ""))
        new = lc != self.lc
        self.lc = lc
        self.n_lc += 1
        if new and dt == 1:
            self.msg("DMR", str(src), lc + (" · krypterat" if enc else " · okrypterat"),
                     name=("TG " if group else "ID ") + str(dst), kind="tal, " + ("grupp" if group else "enskilt"))
            return f"[DMR] {lc}{' (krypterat)' if enc else ''}\n"
        return ""

    def har_synk(self):
        return sum(self.counts.values()) >= 3

    def fakta(self):
        return {"baud": 4800.0, "fam": "FSK4", "klass": "4-FSK (DMR)", "toner": 4, "shift": 1296.0}

    def varden(self):
        if not self.counts:
            return [("DMR", "söker synk …")]
        typ = ", ".join(f"{k} {v}" for k, v in sorted(self.counts.items(), key=lambda kv: -kv[1])[:3])
        v = [("DMR-synk", typ)]
        if self.cc:
            cc = max(self.cc, key=self.cc.get)
            v.append(("Färgkod / burst", f"CC {cc} · " + ", ".join(
                DMR_DT.get(d, str(d)) for d in sorted(self.dt, key=self.dt.get, reverse=True)[:2])))
        if self.lc:
            v.append(("Samtal", self.lc))
        v.append(("Krypterat", self.priv or "okänt (ingen header hörd)"))
        return v


def _rrc_shape(sym_levels, sps, beta=0.2):
    up = np.zeros(len(sym_levels) * sps)
    up[::sps] = sym_levels
    h = A._rrc(beta, sps, 6)
    h = np.convolve(h, h)
    return np.convolve(up, h / h.max(), mode="same")


def make_dmr(fs, src=2400123, dst=2402, group=True, enc=False, cc=1, bursts=12):
    """Testsignal: BS-bursts med Voice LC-header (BPTC + RS) och tal-synk, 4FSK 4800 sym/s."""
    rng = np.random.default_rng(4)
    lev_of = {d: i for i, d in enumerate(DMR_DIBIT)}

    def dibits(bits):
        return [lev_of[(bits[k] << 1) | bits[k + 1]] for k in range(0, len(bits), 2)]

    def pat(p):
        return [(p >> (47 - k)) & 1 for k in range(48)]
    lc = [0x00 if group else 0x03, 0x00, 0x40 if enc else 0x00, (dst >> 16) & 255, (dst >> 8) & 255, dst & 255,
          (src >> 16) & 255, (src >> 8) & 255, src & 255]
    par = [p ^ 0x96 for p in rs1293_parity(lc)]
    data = []
    for byte in lc + par:
        data += [(byte >> (7 - k)) & 1 for k in range(8)]
    info = bptc_build(data)
    slot = [(cc >> (3 - k)) & 1 for k in range(4)] + [0, 0, 0, 1] + [0] * 12   # DT = 1
    syms = []
    for b in range(bursts):
        if b % 6 == 0:
            bits = info[:98] + slot[:10] + pat(0xDFF57D75DF5D) + slot[10:] + info[98:]
        else:
            vb = rng.integers(0, 2, 216).tolist()
            bits = vb[:108] + pat(0x755FD7DF75F7) + vb[108:]
        syms += dibits(bits)
    lv = np.asarray([-1944, -648, 648, 1944], float)[syms]
    sps = int(round(fs / 4800))
    f = _rrc_shape(lv, sps)
    f = np.concatenate((np.zeros(sps * 50), f, np.zeros(sps * 50)))
    return np.exp(2j * np.pi * np.cumsum(f) / fs).astype(np.complex64)


# ════════════════════════════════════════════════════════════════════
# PSK31 / PSK63 (BPSK med G3PLX Varicode)
# ════════════════════════════════════════════════════════════════════
import re as _re

_VARICODE_ASCII = [
    "1010101011", "1011011011", "1011101101", "1101110111", "1011101011", "1101011111", "1011101111", "1011111101",
    "1011111111", "11101111", "11101", "1101101111", "1011011101", "11111", "1101110101", "1110101011",
    "1011110111", "1011110101", "1110101101", "1110101111", "1101011011", "1101101011", "1101101101", "1101010111",
    "1101111011", "1101111101", "1110110111", "1101010101", "1101011101", "1110111011", "1011111011", "1101111111",
    "1", "111111111", "101011111", "111110101", "111011011", "1011010101", "1010111011", "101111111",
    "11111011", "11110111", "101101111", "111011111", "1110101", "110101", "1010111", "110101111",
    "10110111", "10111101", "11101101", "11111111", "101110111", "101011011", "101101011", "110101101",
    "110101011", "110110111", "11110101", "110111101", "111101101", "1010101", "111010111", "1010101111",
    "1010111101", "1111101", "11101011", "10101101", "10110101", "1110111", "11011011", "11111101",
    "101010101", "1111111", "111111101", "101111101", "11010111", "10111011", "11011101", "10101011",
    "11010101", "111011101", "10101111", "1101111", "1101101", "101010111", "110110101", "101011101",
    "101110101", "101111011", "1010101101", "111110111", "111101111", "111111011", "1010110111", "101101101",
    "1011011111", "1011", "1011111", "101111", "101101", "11", "111101", "1011011",
    "101011", "1101", "111101011", "10111111", "11011", "111011", "1111", "111",
    "111111", "110111111", "10101", "10111", "101", "110101", "1111011", "1101011",
    "11011111", "1011101", "111010101", "1010110101", "110111011", "1010110101", "1011010111", "1110110101",
]
VARICODE_DEC = {code: ("\n" if i in (10, 13) else chr(i))
                for i, code in enumerate(_VARICODE_ASCII) if i in (10, 13) or 32 <= i <= 126}
VARICODE_ENC = {chr(i): code for i, code in enumerate(_VARICODE_ASCII) if i in (10, 13) or 32 <= i <= 126}


def make_psk31(fs, text, baud=31.25):
    """Testsignal: BPSK31/63 med varicode och cosinusformade fasvändningar."""
    bits = [0] * 32
    for ch in text:
        code = VARICODE_ENC.get(ch) or VARICODE_ENC.get(ch.upper(), "1")
        bits += [int(b) for b in code] + [0, 0]
    bits += [0] * 16
    sps = fs / float(baud)
    n = int(len(bits) * sps)
    out = np.empty(n, np.float32)
    cur = 1.0
    for k, b in enumerate(bits):
        i0, i1 = int(round(k * sps)), int(round((k + 1) * sps))
        m = i1 - i0
        if m <= 0:
            continue
        if b == 0:
            out[i0:i1] = cur * np.cos(np.pi * np.arange(m) / m)
            cur = -cur
        else:
            out[i0:i1] = cur
    return out.astype(np.complex64)


class _PskSpar(FiAvkodare):
    """Ett avkodningsspår för BPSK med fast takt (PskAvkodare kör ett per takt)."""
    namn = "PSK31 / PSK63"

    def __init__(self, baud=31.25):
        self.fast_baud = baud
        self.reset()

    def reset(self):
        super().reset()
        self.baud = self.fast_baud
        self.senaste = deque(maxlen=30)  # giltiga/ogiltiga varicode-tecken, de senaste
        self.fc = None
        self.ph = 0.0
        self.raw = np.zeros(0, np.complex64)
        self.tail = np.zeros(0, np.complex64)
        self.t = None
        self.prev_s = None
        self.bitbuf = ""
        self.text = ""
        self.linebuf = ""
        self.ok_chars = 0
        self.bad_chars = 0
        self.lp = None                   # (fs, takt, filter, tillstånd)

    NB = 4.0           # störningsspärr: nollar sampel starkare än NB × normal nivå
    LP_SYM = 6.0       # lågpassfiltrets längd i symboler (längre = brantare mot grannar)
    LP_GRANS = 0.8     # lågpassfiltrets gräns × symboltakten

    def passar(self, feat):
        if feat.get("msk"):
            return False                 # MSK-linjerna i z² är sedda: inte PSK (falsk synk på VLF-stationer)
        k = feat.get("kandidater") or []
        if k and k[0][1]["namn"] in ("PSK31", "PSK63"):
            return True
        fam, b, occ = feat.get("fam"), feat.get("baud") or 0, feat.get("occ_bw") or 0
        if fam in ("BPSK", "QPSK", "LIN") and (26 <= b <= 37 or 56 <= b <= 69):
            return True
        return fam == "BPSK" and 35 <= occ <= 150

    def feed(self, z, fs, feat):
        if len(z) == 0:
            return ""
        sps = fs / self.baud
        # störningsspärr: korta pulser långt över signalens normala nivå (knaster) nollas
        z = np.asarray(z, np.complex64)
        a_ = np.abs(z)
        med = float(np.median(a_)) if len(a_) else 0.0      # nivån i just det här stycket: en signal
        z = np.where(a_ > self.NB * med, 0, z).astype(np.complex64)   # som kommer tillbaka efter QSB är inget knaster
        self.raw = np.concatenate((self.raw, np.asarray(z, np.complex64)))[-int(fs * 2.0):]
        if len(self.raw) >= int(fs * 0.5):
            # bärvågen söks kring klicket (analysen kan flytta kanalens mitt mellan flera stationer)
            f0 = feat.get("klick_off", feat.get("f_mid", 0.0)) if self.fc is None else self.fc
            t_r = np.arange(len(self.raw)) / fs
            zr = self.raw * np.exp(-2j * np.pi * f0 * t_r)
            Z = np.fft.fft(zr)
            fz = np.fft.fftfreq(len(zr), 1 / fs)
            Z[np.abs(fz) > self.baud * 1.4] = 0
            zr_lp = np.fft.ifft(Z)
            nf = A._pow2(len(zr_lp) * 2)
            df, s2 = A._power_line(zr_lp, fs, 2, self.baud * 0.9, nf)
            ref = feat.get("klick_off", feat.get("f_mid", 0.0))
            if s2 >= 8 and abs(f0 + df - ref) <= 10.0:        # bara bärvågar nära klicket (inte grannen)
                cand = f0 + df
                self.fc = cand if self.fc is None else self.fc * 0.7 + cand * 0.3
        if self.fc is None:
            self.fc = feat.get("klick_off", feat.get("f_mid", 0.0))
        n = len(z)
        w = -2 * np.pi * self.fc / fs
        ph = self.ph + w * np.arange(n)
        y = np.asarray(z, np.complex64) * np.exp(1j * ph).astype(np.complex64)
        self.ph = float((self.ph + w * n) % (2 * np.pi))
        # lågpassfilter med gränsen vid symboltakten (BPSK31 upptar ±31 Hz): en granne 60 Hz bort
        # dämpas, vilket det korta glidande medlet inte klarade
        if self.lp is None or self.lp[0] != fs or self.lp[1] != self.baud:
            from scipy.signal import firwin, lfilter_zi
            h = firwin(int(self.LP_SYM * sps) | 1, self.baud * self.LP_GRANS, fs=fs)
            self.lp = [fs, self.baud, h, lfilter_zi(h, 1.0).astype(np.complex128) * 0]
        from scipy.signal import lfilter
        y, self.lp[3] = lfilter(self.lp[2], 1.0, y, zi=self.lp[3])
        y = y.astype(np.complex64)
        buf = np.concatenate((self.tail, y))
        sm = buf
        if self.t is None:
            if len(sm) < int(sps * 8):
                self.tail = buf
                return ""
            step_i = max(1, int(round(sps)))
            best_k = max(range(16), key=lambda k: np.mean(np.abs(sm[int(k * sps / 16)::step_i])))
            self.t = (best_k / 16.0 + 1.0) * sps
        t = self.t
        out = []
        hw = max(1, int(sps * 0.35))
        while t + hw + 1 < len(sm):
            i = int(round(t))
            s = sm[i]
            if self.prev_s is not None:
                dot = float(s.real * self.prev_s.real + s.imag * self.prev_s.imag)
                bit = "1" if dot >= 0 else "0"
                if bit == "0":
                    a_idx = max(0, int(round(t - 0.85 * sps)))
                    b_idx = min(len(sm), int(round(t - 0.15 * sps)) + 1)
                    if b_idx - a_idx > 2:
                        dip = a_idx + int(np.argmin(np.abs(sm[a_idx:b_idx])))
                        err = dip - (t - 0.5 * sps)
                        t += float(np.clip(err, -sps / 4, sps / 4)) * 0.2
                self.bitbuf += bit
                if self.bitbuf.endswith("00"):
                    code = self.bitbuf[:-2].lstrip("0")
                    self.bitbuf = ""
                    if code:
                        ch = VARICODE_DEC.get(code)
                        self.senaste.append(bool(ch))
                        if ch:
                            self.ok_chars += 1
                            out.append(ch)
                            self.text = (self.text + ch)[-2000:]
                            self.linebuf += ch
                            if ch == "\n" or len(self.linebuf) >= 60:
                                self._emit_line()
                        else:
                            self.bad_chars += 1
                elif len(self.bitbuf) > 16:
                    self.bitbuf = self.bitbuf[-12:]
            self.prev_s = s
            t += sps
        cut = max(0, int(t - 2 * sps))
        self.tail = buf[cut:]
        self.t = t - cut
        return "".join(out)

    def _emit_line(self):
        ln = self.linebuf.strip()
        self.linebuf = ""
        if len(ln) >= 6:
            m = _re.search(r"\b([A-Z0-9]{1,3}[0-9][A-Z0-9]{1,4})\b", ln.upper())
            src = m.group(1) if m else ""
            self.msg("PSK63" if self.baud > 45 else "PSK31", src, ln, kind=f"{self.baud:g} Bd BPSK")

    def har_synk(self):
        return self.ok_chars >= 5 and self.ok_chars > self.bad_chars

    def fakta(self):
        namn = "PSK63" if self.baud > 45 else "PSK31"
        return {"baud": self.baud, "fam": "BPSK", "klass": f"BPSK ({namn})"}

    def varden(self):
        namn = "PSK63" if self.baud > 45 else "PSK31"
        if not self.ok_chars:
            return [(namn, "söker tecken …")]
        tot = self.ok_chars + self.bad_chars
        return [(namn, f"{self.ok_chars} tecken ({100 * self.ok_chars / max(1, tot):.0f} % giltiga varicode)")]


class PskAvkodare(FiAvkodare):
    """PSK31/PSK63: ett spår per takt körs parallellt och det med flest giltiga varicode-tecken
    bland de senaste vinner. Analysens takt ger bara en liten fördel – brus och grannar gör den
    uppmätta bredden större och övertonen 62 Bd syns i en PSK31-signal."""
    namn = "PSK31 / PSK63"
    TAKTER = (31.25, 62.5)
    passar = _PskSpar.passar

    def __init__(self):
        self.reset()

    def reset(self):
        super().reset()
        self.spar = {b: _PskSpar(b) for b in self.TAKTER}
        self.val = self.TAKTER[0]

    def feed(self, z, fs, feat):
        ut = {b: sp.feed(z, fs, feat) for b, sp in self.spar.items()}
        poang = {}
        for b, sp in self.spar.items():
            h = sp.senaste
            poang[b] = (sum(h) / len(h) if len(h) >= 5 else 0.0) * min(1.0, len(h) / 15)
            mb = feat.get("baud") or 0
            if mb and abs(mb - b) / b < 0.1:
                poang[b] += 0.03
        bast = max(poang, key=poang.get)
        if poang[bast] > poang[self.val] + 0.1:          # hysteres: byt bara vid tydlig skillnad
            self.val = bast
        valt = self.spar[self.val]
        self.msgs.extend(valt.msgs)
        for sp in self.spar.values():
            sp.msgs.clear()
        return ut[self.val]

    @property
    def baud(self):
        return self.val

    def har_synk(self):
        return self.spar[self.val].har_synk()

    def fakta(self):
        return self.spar[self.val].fakta()

    def varden(self):
        return self.spar[self.val].varden()


AVKODARE_NYA = [NavtexAvkodare, PocsagAvkodare, AleAvkodare, FaxAvkodare, DmrAvkodare, PskAvkodare]
for _k in AVKODARE_NYA:                 # registrera i analysen (oavsett vilken modul som laddades först)
    if _k not in A.AVKODARE:
        A.AVKODARE.append(_k)
