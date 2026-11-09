#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
Jämför Signalspanarens Morse-avkodare med fldigi och ggmorse (referensavkodare) på samma signal.

  python3 cwjamfor.py klipp.wav                  ljudfil (t.ex. KiwiSDR:ns "rec" i CW/USB-läge)
  python3 cwjamfor.py klipp.sigmf-meta           IQ-klipp (t.ex. från analystest.py spela-in)
  python3 cwjamfor.py klipp.wav --facit "CQ CQ DE ..."   mät båda mot känd text
  python3 cwjamfor.py --syntetiskt               fallen i analystest.py morse, båda avkodarna
  python3 cwjamfor.py fav22.wav --grupp 5        träningsgrupper om fem tecken (t.ex. FAV22)

fldigi körs utan skärm: Xvfb (virtuell skärm), PulseAudio med en "null sink" som ljudkort
och fjärrstyrning via XML-RPC. Ljudet spelas upp i realtid, så ett klipp på en minut tar
en minut. Startas automatiskt om det inte redan körs (kräver paketen fldigi, xvfb,
pulseaudio och pulseaudio-utils). ggmorse används om ggmorse-from-file finns i PATH.
Utan dem körs bara Signalspanarens avkodare.

Utan facit räknas överensstämmelsen mellan avkodarna: där fldigi och Signalspanaren
ger samma text är den sannolikt rätt, och skillnaderna visas tecken för tecken.
"""
import argparse
import difflib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
import xmlrpc.client

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analys        # noqa: E402

PORT = 7362
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")),
                     "signalspanaren", "cwjamfor")
TON = 700.0          # Hz, CW-tonens frekvens i ljudet som spelas för fldigi
LJUD_FS = 8000


# ════════════════════════════════════════════════════════════════════
# Läsa signalen: ljud (WAV) eller IQ (SigMF) → komplext basband + ljud
# ════════════════════════════════════════════════════════════════════
def las_wav(p):
    with wave.open(p, "rb") as w:
        fs, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise SystemExit("Bara 16-bitars WAV stöds")
    x = np.frombuffer(raw, "<i2").astype(np.float32).reshape(-1, ch) / 32768.0
    if ch == 2:          # KiwiSDR i IQ-läge spelar in I/Q som stereo
        return (x[:, 0] + 1j * x[:, 1]).astype(np.complex64), fs, True
    return x[:, 0], fs, False


def las_sigmf(p):
    import iqfil
    r = iqfil.IQReader(p)
    return r.read(0, r.n).astype(np.complex64), r.rate


def tonfrekvens(x, fs, lo=-1500.0, hi=1500.0):
    """Starkaste smala topp (CW-tonen) i x inom [lo, hi] Hz."""
    n = min(len(x), int(fs * 60))
    f, P = analys._welch(x[:n], fs, 1 << int(np.log2(max(256, fs // 4))))
    m = (f >= lo) & (f <= hi)
    return float(f[m][np.argmax(P[m])])


def till_basband(x, fs, reell):
    """Komplext basband med CW-tonen nära 0 Hz (det Signalspanarens avkodare får i programmet)."""
    if reell:
        X = np.fft.rfft(x)
        z = np.fft.irfft(X, len(x)) + 1j * np.fft.irfft(-1j * X * (np.arange(len(X)) > 0), len(x))
        f0 = tonfrekvens(z, fs, 150, min(3000, fs / 2 - 200))
    else:
        z = x
        f0 = tonfrekvens(z, fs)
    t = np.arange(len(z)) / fs
    return (z * np.exp(-2j * np.pi * f0 * t)).astype(np.complex64), f0


def till_ljud(z, fs):
    """Basband → ljud med tonen på TON Hz, samplat i LJUD_FS (för fldigi)."""
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(int(fs), LJUD_FS)
    y = resample_poly(z, LJUD_FS // g, int(fs) // g) if int(fs) != LJUD_FS else z
    t = np.arange(len(y)) / LJUD_FS
    a = np.real(y * np.exp(2j * np.pi * TON * t))
    return (a / (np.abs(a).max() + 1e-12) * 0.6).astype(np.float32)


def skriv_wav(p, a, fs=LJUD_FS):
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())


# ════════════════════════════════════════════════════════════════════
# Avkodarna
# ════════════════════════════════════════════════════════════════════
def signalspanaren(z, fs, bit=0.35, grupp=None, mitt=None):
    """Avkodar z. mitt = signalens frekvens i Hz (som analysen ger programmets avkodare),
    annars söker avkodaren själv kring 0 Hz."""
    # 2 s svagt brus efter klippet, så att de sista tecknen hinner skrivas ut (som ggmorse gör)
    rng = np.random.default_rng(0)
    sv = float(np.sqrt(np.mean(np.abs(z) ** 2))) * 1e-3 + 1e-9
    z = np.concatenate((z, (sv * (rng.standard_normal(2 * fs) + 1j * rng.standard_normal(2 * fs))).astype(np.complex64)))
    d = analys.CwGruppAvkodare() if grupp else analys.CwAvkodare()
    if grupp:
        d.GRUPP = grupp
    c = int(bit * fs)
    feat = {"fam": "OOK"} if mitt is None else {"fam": "OOK", "f_mid": float(mitt)}
    ut = "".join(d.feed(z[i:i + c], fs, feat) for i in range(0, len(z), c))
    return " ".join(ut.split()), d


def ggmorse(ljud):
    """Avkodar ljudet med ggmorse (ggmorse-from-file i PATH), eller None om det saknas."""
    prog = shutil.which("ggmorse-from-file")
    if not prog:
        return None
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        p = fh.name
    try:
        skriv_wav(p, ljud)
        o = subprocess.run([prog, p], capture_output=True, text=True, timeout=600).stdout
    finally:
        os.unlink(p)
    if "[+] Decoding:" not in o:
        return None
    return " ".join(o.split("[+] Decoding:")[1].split("[+] Done")[0].split())


class Fldigi:
    def __init__(self):
        self.env = dict(os.environ, XDG_RUNTIME_DIR=os.path.join(CACHE, "xdg"), DISPLAY=":97")
        self.s = xmlrpc.client.ServerProxy(f"http://127.0.0.1:{PORT}")

    def svarar(self):
        try:
            self.s.fldigi.version()
            return True
        except OSError:
            return False

    def starta(self):
        if self.svarar():
            return True
        if not shutil.which("fldigi"):
            return False
        xdg = self.env["XDG_RUNTIME_DIR"]
        os.makedirs(xdg, mode=0o700, exist_ok=True)
        konf = os.path.join(CACHE, "fldigi")
        os.makedirs(konf, exist_ok=True)
        if not os.path.exists(os.path.join(konf, "fldigi_def.xml")):
            # en minimal inställningsfil hoppar över guiden vid första start; 2 = PulseAudio
            with open(os.path.join(konf, "fldigi_def.xml"), "w") as fh:
                fh.write("<FLDIGI_DEFS>\n<AUDIOIO>2</AUDIOIO>\n<CONFIRMEXIT>0</CONFIRMEXIT>\n"
                         "<CWTRACK>1</CWTRACK>\n<CWLOWERLIMIT>5</CWLOWERLIMIT>\n"
                         "<CWUPPERLIMIT>60</CWUPPERLIMIT>\n</FLDIGI_DEFS>\n")
        lg = open(os.path.join(CACHE, "fldigi.log"), "w")
        subprocess.Popen(["Xvfb", ":97", "-screen", "0", "1024x768x24"], env=self.env,
                         stdout=lg, stderr=lg, start_new_session=True)
        subprocess.run(["pulseaudio", "-D", "--exit-idle-time=-1"], env=self.env, stdout=lg, stderr=lg)
        time.sleep(1)
        lst = subprocess.run(["pactl", "list", "short", "sinks"], env=self.env, capture_output=True, text=True)
        if "cwjamfor" not in lst.stdout:
            subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=cwjamfor"],
                           env=self.env, stdout=lg, stderr=lg)
        subprocess.run(["pactl", "set-default-source", "cwjamfor.monitor"], env=self.env, stdout=lg, stderr=lg)
        subprocess.Popen(["fldigi", "--config-dir", konf, "--xmlrpc-server-address", "127.0.0.1",
                          "--xmlrpc-server-port", str(PORT)], env=self.env, stdout=lg, stderr=lg,
                         start_new_session=True)
        for _ in range(40):
            time.sleep(0.5)
            if self.svarar():
                return True
        return False

    RTTY_BAUD = {45.0: 0, 45.45: 1, 50.0: 2, 56.0: 3, 75.0: 4, 100.0: 5}
    # 450 Hz → 425 Hz-läget: fldigis egen shift-inställning ("custom") avkodade inget i 4.2.03
    RTTY_SHIFT = {170.0: 3, 200.0: 5, 425.0: 8, 450.0: 8, 850.0: 9}

    def rtty(self, baud, shift, stopp=1.5):
        """Startar om fldigi med RTTY-inställningarna (de går inte att sätta via XML-RPC)."""
        import re
        konf = os.path.join(CACHE, "fldigi", "fldigi_def.xml")
        self.starta()                                     # skapar inställningsfilen om den saknas
        txt = open(konf, encoding="utf-8").read()
        sh = self.RTTY_SHIFT.get(float(shift), -1)
        varden = {"RTTYBAUD": self.RTTY_BAUD.get(float(baud), 1), "RTTYSHIFT": sh,
                  "RTTYCUSTOMSHIFT": int(shift), "RTTYBITS": 0, "RTTYSTOP": {1.0: 0, 1.5: 1, 2.0: 2}.get(stopp, 1)}
        for k, v in varden.items():
            if f"<{k}>" in txt:
                txt = re.sub(f"<{k}>[^<]*</{k}>", f"<{k}>{v}</{k}>", txt)
            else:
                txt = txt.replace("</FLDIGI_DEFS>", f"<{k}>{v}</{k}>\n</FLDIGI_DEFS>")
        try:
            self.s.fldigi.terminate(0)
        except Exception:
            pass
        subprocess.run(["pkill", "-f", "fldigi --config-dir"], check=False)
        # vänta tills den gamla processen är borta – annars skriver den över inställningarna när
        # den avslutas, och den nya startas aldrig (den gamla svarar fortfarande)
        for _ in range(60):
            if subprocess.run(["pgrep", "-f", "fldigi --config-dir"], capture_output=True).returncode != 0:
                break
            time.sleep(0.5)
        else:
            subprocess.run(["pkill", "-9", "-f", "fldigi --config-dir"], check=False)
            time.sleep(1)
        open(konf, "w", encoding="utf-8").write(txt)
        return self.starta()

    def avkoda(self, ljud, modem="CW", omvand=False):
        """Spelar ljudet (TON Hz) för fldigi i realtid och returnerar texten."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            p = fh.name
        skriv_wav(p, np.concatenate((np.zeros(LJUD_FS // 2, np.float32), ljud, np.zeros(LJUD_FS, np.float32))))
        try:
            self.s.modem.set_by_name(modem)
            self.s.modem.set_carrier(int(TON))
            if modem != "CW":
                self.s.main.set_reverse(bool(omvand))
            self.s.main.set_afc(True)
            self.s.main.set_squelch(modem != "CW")  # CW: av (tystar svaga signaler); RTTY: på (annars brus → skräp)
            if modem != "CW":
                self.s.main.set_squelch_level(10.0)  # standardnivån 30 tappade tecken
            self.s.text.clear_rx()
            subprocess.run(["paplay", "-d", "cwjamfor", p], env=self.env, check=True)
            time.sleep(1.0)
            n = self.s.text.get_rx_length()
            ut = self.s.text.get_rx(0, n).data.decode("utf-8", "replace") if n else ""
        finally:
            os.unlink(p)
        ut = " ".join(ut.replace("\r", " ").replace("\n", " ").split())
        # fldigi skriver förtecken (prosigns) som <BT>; Signalspanaren som tecknet de motsvarar
        for a, b in FORTECKEN.items():
            ut = ut.replace(a, b)
        return ut


FORTECKEN = {"<BT>": "=", "<AR>": "+", "<KN>": "(", "<AS>": "&"}


# ════════════════════════════════════════════════════════════════════
# Jämförelse
# ════════════════════════════════════════════════════════════════════
def likhet(a, b):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def skillnader(a, b, namn_a="Signalspanaren", namn_b="fldigi", max_rader=15):
    rader = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            ka = a[max(0, i1 - 8):i1]
            rader.append(f"  …{ka}[{a[i1:i2]}|{b[j1:j2]}]   ({namn_a}|{namn_b})")
    return rader[:max_rader]


def jamfor_fil(args):
    p = args.fil
    if p.endswith(".sigmf-meta") or p.endswith(".sigmf-data"):
        x, fs = las_sigmf(p.replace(".sigmf-data", ".sigmf-meta"))
        reell = False
    else:
        x, fs, iq = las_wav(p)
        reell = not iq
    z, f0 = till_basband(x, fs, reell)
    print(f"{os.path.basename(p)}: {len(x) / fs:.0f} s, {fs} Hz, CW-ton {f0:+.0f} Hz")
    ss, d = signalspanaren(z, fs, grupp=args.grupp)
    print(f"\nSignalspanaren ({', '.join(v for _, v in d.varden())}):\n  {ss}")
    gg = ggmorse(till_ljud(z, fs))
    if gg is not None:
        print(f"\nggmorse:\n  {gg}")
    fl = None
    if not args.utan_fldigi:
        f = Fldigi()
        if f.starta():
            print(f"\nfldigi (spelar {len(x) / fs:.0f} s i realtid) …", flush=True)
            fl = f.avkoda(till_ljud(z, fs))
            print(f"  {fl}")
        else:
            print("\nfldigi kunde inte startas (installera fldigi, xvfb, pulseaudio, pulseaudio-utils)")
    print()
    if args.facit:
        facit = " ".join(args.facit.upper().split())
        print(f"Mot facit: Signalspanaren {likhet(facit, ss) * 100:.1f} %"
              + (f", ggmorse {likhet(facit, gg) * 100:.1f} %" if gg is not None else "")
              + (f", fldigi {likhet(facit, fl) * 100:.1f} %" if fl is not None else ""))
    if fl is not None:
        print(f"Överensstämmelse Signalspanaren/fldigi: {likhet(ss, fl) * 100:.1f} %")
        for r in skillnader(ss, fl):
            print(r)
    return 0


def jamfor_syntetiskt(args):
    import analystest
    f = Fldigi()
    har_fl = (not args.utan_fldigi) and f.starta()
    if not har_fl:
        print("(utan fldigi)")
    print(f"{'Fall':26} {'Signalspanaren':>15} {'fldigi':>8}")
    print("─" * 54)
    tot_s = tot_f = 0.0
    for namn, kw in analystest.MORSE_FALL:
        kw = dict(kw)
        fs, chunk, grupp = kw.pop("fs", 8000), kw.pop("chunk", 0.35), kw.pop("grupp", None)
        text = analystest.GRUPP_TEXT if grupp else analystest.MORSE_TEXT
        rng = np.random.default_rng(args.seed)
        nyckel = analystest.morse_nyckel(text + " " + text,
                                         kw.pop("wpm"), kw.pop("fw", None), kw.pop("jitter", 0.0),
                                         kw.pop("dash", 3.0), rng)
        z = analystest.morse_signal(nyckel, fs, kw.pop("snr", 20), rng, **kw)
        ss, _ = signalspanaren(z, fs, chunk, grupp)
        es = analystest.morse_fel(text, ss)
        tot_s += es
        rad = f"{namn:26} {es * 100:13.1f} %"
        if har_fl:
            # fldigi får samma signal förskjuten till sin tonfrekvens (vid sidan-fallen behåller förskjutningen)
            fl = f.avkoda(till_ljud(z, fs))
            ef = analystest.morse_fel(text, fl)
            tot_f += ef
            rad += f" {ef * 100:6.1f} %"
            if args.visa:
                rad += f"   {fl[-50:]!r}"
        print(rad, flush=True)
    n = len(analystest.MORSE_FALL)
    print("─" * 54)
    print(f"{'Medel':26} {tot_s / n * 100:13.1f} %" + (f" {tot_f / n * 100:6.1f} %" if har_fl else ""))
    return 0


def main():
    p = argparse.ArgumentParser(prog="cwjamfor", description="Jämför Morse-avkodaren med fldigi.")
    p.add_argument("fil", nargs="?", help="WAV (ljud eller IQ i stereo) eller SigMF-klipp")
    p.add_argument("--facit", help="känd text att mäta mot")
    p.add_argument("--syntetiskt", action="store_true", help="fallen i analystest.py morse")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--visa", action="store_true", help="visa fldigis text i den syntetiska tabellen")
    p.add_argument("--utan-fldigi", action="store_true")
    p.add_argument("--grupp", type=int, metavar="N", help="träningsgrupper om N tecken (lagar delade tecken)")
    args = p.parse_args()
    if args.syntetiskt:
        return jamfor_syntetiskt(args)
    if not args.fil:
        p.error("ange en fil eller --syntetiskt")
    return jamfor_fil(args)


if __name__ == "__main__":
    sys.exit(main())
