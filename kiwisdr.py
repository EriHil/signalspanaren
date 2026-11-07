# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""
KiwiSDR som mottagarkälla – hämtar I/Q från en KiwiSDR på nätet (de webb-SDR:er man
surfar in på, t.ex. http://sm5ajv.example:8073/).

KiwiSDR:n skickar ett smalt I/Q-band (12 kHz, eller 20,25 kHz på "wideband"-Kiwis) runt
den frekvens man ber om, som 16-bitars heltal över en websocket. Här omsamplas bandet
till programmets samplingstakt så att resten av programmet (vattenfall, demodulering,
avkodare, analys, inspelning) fungerar som vanligt. Utanför Kiwins band fylls det på med
brus på samma nivå som Kiwins brusgolv, annars förstörs nivåautomatiken och analysens
bandbredd (samma knep som för smala IQ-filer i iqfil.py).

Protokollet (samma som kiwiclient/kiwirecorder):
  ws://värd:port/kiwi/<tid>/SND   – ljud/IQ-kanalen
  klient → "SET auth t=kiwi p=<lösen>", "SET mod=iq low_cut=… high_cut=… freq=<kHz>",
           "SET agc=…", "SET AR OK in=… out=…", "SET keepalive" (minst var 10:e s)
  server → "MSG nyckel=värde …" (text i binär ram) och "SND"-ramar:
           flaggor(1) sekvens(4, LE) smeter(2, BE) [IQ: gps(10)] data (int16 BE, I Q I Q …)

Ingen extra Python-modul behövs – websocket-klienten nedan räcker för det Kiwin gör.
"""

import base64
import logging
import os
import socket
import ssl
import struct
import threading
import time
import urllib.parse
from collections import deque

import math
import numpy as np

import iqfil

log = logging.getLogger("signalspanaren")

KIWI_MAX_HZ = 32e6          # KiwiSDR tar emot 0–30 MHz (vissa 0–32)


class KiwiError(Exception):
    pass


# ════════════════════════════════════════════════════════════════════
# Minimal websocket-klient (RFC 6455) – bara det Kiwin behöver
# ════════════════════════════════════════════════════════════════════
class WebSocket:
    def __init__(self, host, port, path, secure=False, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        hostp = host if port in (80, 443) else f"{host}:{port}"
        req = (f"GET {path} HTTP/1.1\r\nHost: {hostp}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"User-Agent: Signalspanaren\r\n\r\n")
        self.sock.sendall(req.encode())
        self.rbuf = bytearray()
        head = self._read_until(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status + " ":
            raise KiwiError(f"Servern ville inte öppna en websocket ({status.strip() or 'inget svar'})")
        self.wlock = threading.Lock()

    def _fill(self):
        d = self.sock.recv(65536)
        if not d:
            raise KiwiError("KiwiSDR:n stängde anslutningen")
        self.rbuf += d

    def _read_until(self, mark):
        while mark not in self.rbuf:
            if len(self.rbuf) > 65536:
                raise KiwiError("Konstigt svar från servern (inte en KiwiSDR?)")
            self._fill()
        i = self.rbuf.index(mark) + len(mark)
        out, self.rbuf = bytes(self.rbuf[:i]), self.rbuf[i:]
        return out

    def _read(self, n):
        while len(self.rbuf) < n:
            self._fill()
        out, self.rbuf = bytes(self.rbuf[:n]), self.rbuf[n:]
        return out

    def send(self, data, opcode=1):
        if isinstance(data, str):
            data = data.encode()
        n = len(data)
        hdr = bytes([0x80 | opcode])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        m = np.frombuffer(data, np.uint8) ^ np.resize(np.frombuffer(mask, np.uint8), n)
        with self.wlock:
            self.sock.sendall(hdr + mask + m.tobytes())

    def recv(self):
        """Returnerar nästa hela meddelande (bytes). Svarar själv på ping."""
        parts = []
        while True:
            b0, b1 = self._read(2)
            op, fin = b0 & 0x0F, b0 & 0x80
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = (np.frombuffer(data, np.uint8) ^ np.resize(np.frombuffer(mask, np.uint8), n)).tobytes()
            if op == 8:
                code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 0
                raise KiwiError(f"KiwiSDR:n stängde anslutningen (kod {code}"
                                f"{', ' + data[2:].decode(errors='replace') if len(data) > 2 else ''})")
            if op == 9:
                self.send(data, 10)
                continue
            if op == 10:
                continue
            parts.append(data)
            if fin:
                return b"".join(parts)

    def close(self):
        try:
            self.send(struct.pack(">H", 1000), 8)
        except Exception:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def parse_addr(addr):
    """'http://värd:8073/', 'värd:8073', 'värd', 'lösen@värd:port' → (värd, port, lösen, säker)."""
    a = (addr or "").strip()
    if not a:
        raise KiwiError("Ingen KiwiSDR-adress angiven")
    had_scheme = "://" in a
    if not had_scheme:
        a = "http://" + a
    u = urllib.parse.urlsplit(a)
    try:
        port = u.port
    except ValueError:
        raise KiwiError(f"Felaktigt portnummer i KiwiSDR-adressen: {addr}")
    if not u.hostname:
        raise KiwiError(f"Kan inte tolka KiwiSDR-adressen: {addr}")
    secure = u.scheme in ("https", "wss")
    if port is None:     # inklistrad webbadress utan port = webbens port, annars Kiwins standard 8073
        port = (443 if secure else 80) if had_scheme else 8073
    pw = urllib.parse.unquote(u.password or u.username or "") if "@" in u.netloc else ""
    return u.hostname, port, pw, secure


# ════════════════════════════════════════════════════════════════════
# Klient: anslutning, kommandon, mottagning av I/Q
# ════════════════════════════════════════════════════════════════════
class KiwiClient:
    """Ansluter till KiwiSDR:ns SND-kanal i IQ-läge. Läser i en egen tråd; read_iq() hämtar data."""

    def __init__(self, addr, ident="Signalspanaren"):
        self.host, self.port, self.password, self.secure = parse_addr(addr)
        self.ident = ident
        self.ws = None
        self.rate = None             # verklig samplingstakt (t.ex. 12001,135)
        self.nominal = None          # nominell (12000 eller 20250)
        self.info = {}               # MSG-värden från Kiwin
        self.rssi = None
        self.q = deque()
        self.qlen = 0
        self.cv = threading.Condition()
        self.err = None
        self.running = False
        self.freq = 10e6
        self.passband = None
        self.ppm = 0.0               # Kiwins frekvensfel: signaler syns ppm × 1e-6 × f för lågt
        self.agc = True
        self.man_gain = 50
        self.dropped = 0
        self._seq = None
        self._want = 0

    # ── anslutning ──
    def connect(self, timeout=12):
        ts = int(time.time())
        last = None
        for path in (f"/kiwi/{ts}/SND", f"/{ts}/SND"):
            try:
                self.ws = WebSocket(self.host, self.port, path, self.secure)
                break
            except KiwiError as e:
                last = e
            except OSError as e:
                raise KiwiError(f"Kunde inte ansluta till KiwiSDR på {self.host}:{self.port}: {e}")
        if self.ws is None:
            raise KiwiError(f"{self.host}:{self.port} svarar inte som en KiwiSDR ({last})")
        self.ws.sock.settimeout(timeout)
        self.ws.send(f"SET auth t=kiwi p={self.password}")
        # vänta in samplingstakten (och eventuella fel) innan vi ställer in
        t_end = time.monotonic() + timeout
        while self.rate is None:
            if time.monotonic() > t_end:
                raise KiwiError("KiwiSDR:n svarade inte med samplingstakt")
            self._handle(self.ws.recv())
        self.ws.send(f"SET AR OK in={int(round(self.rate))} out=48000")
        self.ws.send("SERVER DE CLIENT Signalspanaren SND")
        self.ws.send(f"SET ident_user={urllib.parse.quote(self.ident)}")
        self.ws.send("SET squelch=0 max=0")
        self.ws.send("SET genattn=0")
        self.ws.send("SET gen=0 mix=-1")
        self.ws.send("SET compression=0")
        self.set_freq(self.freq)
        self.set_gain(None if self.agc else self.man_gain)
        self.running = True
        threading.Thread(target=self._rx_loop, name="KiwiSDR-rx", daemon=True).start()
        threading.Thread(target=self._keepalive, name="KiwiSDR-keepalive", daemon=True).start()
        log.info("Ansluten till KiwiSDR %s:%d (%s, %.3f S/s)", self.host, self.port,
                 self.info.get("name", "?"), self.rate)

    def _msg(self, body):
        for kv in body.split(" "):
            k, _, v = kv.partition("=")
            if not k:
                continue
            v = urllib.parse.unquote(v)
            self.info[k] = v
            if k not in ("audio_adpcm_state", "keepalive"):
                log.debug("KiwiSDR MSG %s=%s", k, v[:120])
            if k == "sample_rate":
                self.rate = float(v)
                self.nominal = 20250 if self.rate > 16000 else 12000
            elif k == "too_busy":
                if v.strip() == "0":     # ägaren tillåter inga anslutningar från andra program (ext_api=0)
                    raise KiwiError("Den här KiwiSDR:n tillåter bara lyssnare i webbläsaren, inte andra program. "
                                    "Öppna den i webbläsaren eller välj en annan Kiwi (Webbmottagare-listan "
                                    "visar vilka som går).")
                raise KiwiError(f"KiwiSDR:n är fullt upptagen – alla {v} platser för andra program än "
                                "webbläsaren används. Prova en annan Kiwi eller vänta en stund.")
            elif k == "badp" and v == "1":
                raise KiwiError("KiwiSDR:n kräver lösenord (skriv adressen som lösenord@värd:port)")
            elif k == "down":
                raise KiwiError("KiwiSDR:n är tillfälligt avstängd av ägaren")
            elif k in ("inactivity_timeout", "inactivity_timeout_msg"):
                log.info("KiwiSDR: tidsgräns för inaktivitet %s", v)
            elif k == "redirect":
                raise KiwiError(f"KiwiSDR:n hänvisar vidare till {v}")

    def _handle(self, m):
        tag = m[:3]
        if tag == b"MSG":
            self._msg(m[4:].decode(errors="replace"))
        elif tag == b"SND":
            self._snd(m[3:])

    def _snd(self, body):
        if len(body) < 17:
            return
        flags, seq = struct.unpack("<BI", body[:5])
        smeter = struct.unpack(">H", body[5:7])[0]
        self.rssi = 0.1 * smeter - 127
        if self._seq is not None and seq != (self._seq + 1) & 0xFFFFFFFF:
            self.dropped += 1
        self._seq = seq
        data = body[7 + 10:]                       # IQ-läge: 10 byte GPS-tidsstämpel först
        n = len(data) // 4
        if n == 0:
            return
        s = np.frombuffer(data[:n * 4], ">i2").astype(np.float32) * np.float32(1 / 32768)
        iq = s.view(np.complex64)
        with self.cv:
            self.q.append(iq)
            self.qlen += len(iq)
            # max 1 s i kö: efter ett nätverksavbrott kommer allt på en gång – släng det äldsta
            # hellre än att ligga sekunder efter för all framtid
            while self.qlen > (self.rate or 12000) + self._want and len(self.q) > 1:
                self.qlen -= len(self.q.popleft())
                self.dropped += 1
            self.cv.notify()

    def _rx_loop(self):
        try:
            while self.running:
                self._handle(self.ws.recv())
        except socket.timeout:
            self.err = KiwiError("Tidsgräns: KiwiSDR:n skickar ingen data")
        except (KiwiError, OSError) as e:
            self.err = e if isinstance(e, KiwiError) else KiwiError(f"Nätverksfel mot KiwiSDR: {e}")
        except Exception as e:
            log.exception("Fel i KiwiSDR-mottagningen")
            self.err = KiwiError(f"Fel i KiwiSDR-mottagningen: {e}")
        finally:
            with self.cv:
                self.cv.notify_all()

    def _keepalive(self):
        while self.running:
            for _ in range(50):
                if not self.running:
                    return
                time.sleep(0.1)
            try:
                self.ws.send("SET keepalive")
            except Exception:
                return

    # ── kommandon ──
    def set_freq(self, f):
        self.freq = float(f)
        half = int((self.nominal or 12000) * 0.5 * 0.98)
        lo, hi = self.passband or (-half, half)
        if self.ws:
            # en Kiwi som visar signalerna för lågt (positivt ppm) ska be om en lite lägre frekvens
            fk = self.freq * (1 - self.ppm * 1e-6) / 1e3
            self.ws.send(f"SET mod=iq low_cut={lo} high_cut={hi} freq={fk:.4f}")
            with self.cv:                          # det som ligger i kön hör till förra frekvensen
                self.q.clear()
                self.qlen = 0

    def set_gain(self, g):
        """None = Kiwins AGC, annars fast förstärkning i dB (0–120)."""
        self.agc = g is None
        if g is not None:
            self.man_gain = int(max(0, min(120, round(g))))
        if self.ws:
            self.ws.send(f"SET agc={1 if self.agc else 0} hang=0 thresh=-100 slope=6 decay=1000 "
                         f"manGain={self.man_gain}")

    def read_iq(self, n, timeout=8):
        """Väntar tills n samples finns och returnerar dem."""
        t_end = time.monotonic() + timeout
        with self.cv:
            self._want = n
            while self.qlen < n:
                if self.err:
                    raise self.err
                left = t_end - time.monotonic()
                if left <= 0:
                    raise KiwiError("Tidsgräns: KiwiSDR:n skickar ingen data")
                self.cv.wait(left)
            out = np.empty(n, np.complex64)
            k = 0
            while k < n:
                a = self.q[0]
                take = min(len(a), n - k)
                out[k:k + take] = a[:take]
                if take == len(a):
                    self.q.popleft()
                else:
                    self.q[0] = a[take:]
                k += take
            self.qlen -= n
            return out

    def close(self):
        self.running = False
        if self.ws:
            self.ws.close()
            self.ws = None


# ════════════════════════════════════════════════════════════════════
# Mottagarkälla för Signalspanaren
# ════════════════════════════════════════════════════════════════════
class KiwiSource:
    """BaseSource-gränssnittet: set(), open(), read(), close(). Mittfrekvensen styrs av
    programmet, men bandet är bara ca 12 kHz brett (attributet narrow_rate)."""
    kind = "kiwi"
    name = "KiwiSDR"

    def __init__(self, addr, out_rate, block, ident="Signalspanaren"):
        self.addr = addr
        self.out_rate = float(out_rate)
        self.block = int(block)
        self.ident = ident
        self.center = 10e6
        self.cli = None
        self.rs = None
        self.narrow_rate = 12000.0
        self._pending = {}
        self._plock = threading.Lock()
        self._nvar = None
        self._nmask = None
        self._buf = np.zeros(0, np.complex64)

    # gränssnitt som BaseSource
    def set(self, key, val):
        with self._plock:
            self._pending[key] = val
        if key == "freq":
            self.center = float(val)

    def _take(self):
        with self._plock:
            p, self._pending = self._pending, {}
        return p

    def open(self):
        p = self._take()
        c = KiwiClient(self.addr, self.ident)
        c.freq = self.center
        if "ppm" in p:
            c.ppm = float(p["ppm"])
        if "gain" in p:
            c.agc = p["gain"] is None
            if p["gain"] is not None:
                c.man_gain = int(round(p["gain"]))
        try:
            c.connect()
        except socket.timeout:
            c.close()
            raise KiwiError("Tidsgräns: KiwiSDR:n svarar inte")
        except OSError as e:
            c.close()
            raise KiwiError(f"Nätverksfel mot KiwiSDR: {e}")
        except KiwiError:
            c.close()
            raise
        self.cli = c
        self.narrow_rate = float(c.nominal)
        self.rs = iqfil.Resampler(c.nominal, self.out_rate)
        self.chunk_in = max(self.rs.down, int(round(self.block / self.rs.ratio / self.rs.down)) * self.rs.down)
        self._buf = np.zeros(0, np.complex64)
        self._buf_center = self.center
        self._nvar = None
        self._nest = deque(maxlen=64)
        self._recent = deque(maxlen=max(4, int(4.0 * c.nominal / self.chunk_in) + 1))   # ca 4 s smal IQ
        self._rlock = threading.Lock()

    def _apply(self, p):
        c = self.cli
        if "gain" in p:
            c.set_gain(p["gain"])
            self._nest.clear()
        if "ppm" in p:
            c.ppm = float(p["ppm"] or 0.0)
            if "freq" not in p:
                c.set_freq(c.freq)
        if "freq" in p:
            c.set_freq(p["freq"])
        if "freq" in p or "ppm" in p:
            with self._rlock:
                self._recent.clear()

    def _noise_est(self, x):
        """Kiwins brusgolv (varians per insample) ur 20:e percentilen av spektret – medianen dras
        upp på fulla band. För exponentialfördelad effekt är p-percentilen −ln(1−p) × medel."""
        N = 256
        m = len(x) // N
        if m == 0:
            return None
        w = np.hanning(N).astype(np.float32)
        seg = x[:m * N].reshape(m, N) * w
        P = (np.abs(np.fft.fft(seg, axis=1)) ** 2) / np.sum(w ** 2)
        fr = np.abs(np.fft.fftfreq(N))
        return float(np.percentile(P[:, fr < 0.4], 20) / 0.2231)  # bara mitten: kanterna dämpas av Kiwins filter

    def _outband(self, n):
        if self._nmask is None or len(self._nmask) != n:
            # mjuk övergång där Kiwins eget filter rullar av (ca 0,42–0,47 × takten), så att
            # bruset inte får en grop eller puckel vid bandkanten
            fr = np.abs(np.fft.fftfreq(n, 1 / self.out_rate)) / self.narrow_rate
            t = np.clip((fr - 0.415) / (0.47 - 0.415), 0, 1)
            self._nmask = np.sqrt(0.5 - 0.5 * np.cos(np.pi * t)).astype(np.float32)
        sd = np.sqrt(max(self._nvar or 1e-12, 1e-14) * self.rs.ratio / 2)
        w = (np.random.standard_normal(2 * n) * sd).astype(np.float32).view(np.complex64)
        return np.fft.ifft(np.fft.fft(w) * self._nmask).astype(np.complex64)

    def read(self):
        p = self._take()
        if p:
            self._apply(p)
        while len(self._buf) < self.block:
            x = self.cli.read_iq(self.chunk_in)
            with self._rlock:
                self._recent.append(x)
            v = self._noise_est(x)                     # följer brusgolvet (Kiwins AGC ändrar det)
            if v:
                self._nest.append(v)
                self._nvar = float(np.median(self._nest))
            self._buf = np.concatenate((self._buf, self.rs.process(x)))
        out = self._buf[:self.block]
        self._buf = self._buf[self.block:]
        out = out + self._outband(len(out))
        return out.astype(np.complex64), self.center, None

    def fine_carrier(self, rel_hz, win=400.0):
        """Mäter en bärvåg nära rel_hz (Hz från mittfrekvensen) i de senaste ca 4 s av Kiwins smala
        IQ – bråkdelar av en Hz i upplösning, mot 375 Hz per bin i det breda spektret.
        Returnerar (uppmätt rel. frekvens, SNR i dB) eller None om data saknas."""
        if self.cli is None or getattr(self, "_rlock", None) is None:
            return None
        with self._rlock:
            parts = list(self._recent)
        rate = float(self.cli.rate or self.narrow_rate)
        x = np.concatenate(parts) if parts else np.zeros(0, np.complex64)
        if len(x) < rate * 1.5:
            return None
        n = len(x)
        P = np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(n)))) ** 2
        f = (np.arange(n) - n // 2) * rate / n
        sel = np.nonzero(np.abs(f - rel_hz) <= win)[0]
        if len(sel) < 3:
            return None
        k = int(sel[np.argmax(P[sel])])
        d = 0.0
        if 0 < k < n - 1:
            a, b, c = (math.log(P[k + i] + 1e-30) for i in (-1, 0, 1))
            den = a - 2 * b + c
            if den:
                d = max(-1.0, min(1.0, 0.5 * (a - c) / den))
        floor = float(np.median(P[np.abs(f) < rate * 0.4]))
        return f[k] + d * rate / n, 10 * math.log10(P[k] / (floor + 1e-30) + 1e-30)

    def close(self):
        if self.cli:
            self.cli.close()
            self.cli = None
            log.info("Kopplade ner från KiwiSDR")


# ════════════════════════════════════════════════════════════════════
# Självtest: en låtsas-KiwiSDR på 127.0.0.1 (ingen internet behövs)
# ════════════════════════════════════════════════════════════════════
class FakeKiwi:
    """Minimal KiwiSDR-server: svarar på websocket, skickar MSG och SND-ramar i IQ-läge med en
    ton tone_hz ovanför den frekvens klienten ber om, i realtid (12000 S/s)."""

    def __init__(self, tone_hz=1000.0, rate=12000, busy=False):
        self.tone, self.rate, self.busy = tone_hz, rate, busy
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.cmds = []
        self.freq_khz = None
        threading.Thread(target=self._serve, daemon=True).start()

    @staticmethod
    def _frame(data, op=2):
        n = len(data)
        h = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n))
        return h + data

    def _serve(self):
        c, _ = self.srv.accept()
        c.settimeout(5)
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += c.recv(4096)
        key = [ln.split(b":", 1)[1].strip() for ln in buf.split(b"\r\n") if ln.lower().startswith(b"sec-websocket-key")][0]
        import hashlib
        acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  b"Sec-WebSocket-Accept: " + acc + b"\r\n\r\n")
        rx = bytearray()
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    d = c.recv(4096)
                    if not d:
                        break
                    rx.extend(d)
                    while len(rx) >= 6:
                        n = rx[1] & 0x7F
                        o = 2
                        if n == 126:
                            n = struct.unpack(">H", rx[2:4])[0]
                            o = 4
                        if len(rx) < o + 4 + n:
                            break
                        m = rx[o:o + 4]
                        p = bytes(b ^ m[i % 4] for i, b in enumerate(rx[o + 4:o + 4 + n]))
                        del rx[:o + 4 + n]
                        t = p.decode(errors="replace")
                        self.cmds.append(t)
                        if t.startswith("SET mod=iq"):
                            self.freq_khz = float(t.split("freq=")[1].split()[0])
            except OSError:
                pass
            stop.set()
        threading.Thread(target=reader, daemon=True).start()
        try:
            if self.busy:
                c.sendall(self._frame(b"MSG too_busy=4"))
                time.sleep(0.5)
                return
            c.sendall(self._frame(f"MSG sample_rate={self.rate + 0.5:.6f} rx_chans=4".encode()))
            c.sendall(self._frame(b"MSG audio_rate=12000 name=Testkiwi"))
            n_fr, seq, ph = 512, 0, 0.0
            t_next = time.monotonic()
            while not stop.is_set():
                w = 2 * np.pi * self.tone / self.rate
                z = 0.25 * np.exp(1j * (ph + w * np.arange(n_fr)))
                ph = (ph + w * n_fr) % (2 * np.pi)
                iq = np.empty(2 * n_fr, ">i2")
                iq[0::2] = np.round(z.real * 32767)
                iq[1::2] = np.round(z.imag * 32767)
                body = (b"SND" + struct.pack("<BI", 0x08, seq) + struct.pack(">H", 470) +
                        struct.pack("<BBII", 0, 0, 0, 0) + iq.tobytes())
                c.sendall(self._frame(body))
                seq += 1
                t_next += n_fr / self.rate
                time.sleep(max(0.0, t_next - time.monotonic()))
        except OSError:
            pass
        finally:
            stop.set()
            c.close()
            self.srv.close()


def selftest(out_rate, block, verbose=True):
    """Ansluter till en låtsas-Kiwi och kontrollerar ton, frekvens, omstämning och felmeddelande."""
    ok = True
    fk = FakeKiwi(tone_hz=1000.0)
    src = KiwiSource(f"127.0.0.1:{fk.port}", out_rate, block)
    src.set("freq", 7.074e6)
    src.set("gain", None)
    try:
        src.open()
        for _ in range(4):
            src.read()
        x = np.concatenate([src.read()[0] for _ in range(4)])
        N = len(x)
        X = np.abs(np.fft.fft(x))
        f = np.fft.fftfreq(N, 1 / out_rate)[int(np.argmax(X))]
        lvl = float(np.sqrt(np.mean(np.abs(x) ** 2)))
        src.set("freq", 7.1e6)
        src.read()
        time.sleep(0.2)
        good = abs(f - 1000) < 3 and abs(lvl - 0.25) < 0.03 and fk.freq_khz == 7100.0
        if verbose:
            print(f"  KiwiSDR (låtsas-Kiwi lokalt): {'OK' if good else 'FEL'} (ton {f:.0f} Hz, nivå {lvl:.3f}, "
                  f"omstämd till {fk.freq_khz} kHz)")
        ok &= good
    except Exception as ex:
        if verbose:
            print(f"  KiwiSDR (låtsas-Kiwi lokalt): FEL ({ex})")
        ok = False
    finally:
        src.close()
    fb = FakeKiwi(busy=True)
    try:
        KiwiSource(f"127.0.0.1:{fb.port}", out_rate, block).open()
        good = False
        msg = "ingen felrapport"
    except KiwiError as ex:
        msg = str(ex)
        good = "upptagen" in msg
    if verbose:
        print(f"  KiwiSDR upptagen: {'OK' if good else 'FEL'} ({msg[:50]})")
    return ok and good
