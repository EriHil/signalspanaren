# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 erihil
"""Flaggor (stationsetiketter) ovanför vattenfallet.

Två källor:
  • KiwiSDR:ns egna DX-flaggor – hämtas i bakgrunden när en Kiwi är källa (kiwisdr.hamta_flaggor) och
    sparas per Kiwi i cachen ett dygn.
  • Egna flaggor – läggs till och ändras i programmet och sparas i ~/.config/signalspanaren/flaggor.json.
    Ändrar man en Kiwi-flagga blir den en egen kopia som ersätter Kiwins (Kiwins databas kräver ägarens
    administratörslösenord).
Kiwi-flaggor visas bara när de sänder enligt sin tidsangivelse (b–e, UTC); egna visas alltid.
"""
import datetime
import json
import logging
import os
import threading
import time
import uuid

log = logging.getLogger("signalspanaren")

TYPER = ["Tal", "Rundradio", "Flyg", "Sjöfart", "Militär", "Data", "Fax/väder", "Tid", "Amatör", "Fyr/bake",
         "Spion", "HFDL", "Övrigt"]
LAGEN = ["USB", "LSB", "AM", "CW", "NFM", "WFM"]

# färg per typ (r, g, b); Kiwins typnamn mappas grovt hit
_FARG = {"Tal": (0.45, 0.85, 1.0), "Rundradio": (0.95, 0.80, 0.35), "Flyg": (0.55, 0.95, 0.55),
         "Sjöfart": (0.40, 0.70, 1.0), "Militär": (1.0, 0.50, 0.45), "Data": (0.85, 0.60, 1.0),
         "Fax/väder": (1.0, 0.65, 0.85), "Tid": (0.95, 0.95, 0.55), "Amatör": (0.65, 1.0, 0.85),
         "Fyr/bake": (0.80, 0.80, 0.80), "Spion": (1.0, 0.35, 0.75), "HFDL": (0.60, 0.95, 0.75),
         "Övrigt": (0.85, 0.85, 0.85)}
_KIWI_TYP = {"aero": "Flyg", "volmet": "Flyg", "hfdl": "HFDL", "milcom": "Militär", "marine": "Sjöfart",
             "fax": "Fax/väder", "time": "Tid", "spy": "Spion", "broadcast": "Rundradio", "bcast": "Rundradio",
             "ham": "Amatör", "voice": "Tal", "digital": "Data", "fsk": "Data", "utility": "Data", "cw": "Amatör",
             "beacon": "Fyr/bake", "beacons": "Fyr/bake", "dgps": "Fyr/bake"}
CACHE_TID = 24 * 3600
# Referens-Kiwi: flaggorna hämtas härifrån oavsett mottagare. Kiwin hos aprs.fi i Kuopio (8 platser) har
# KiwiSDR:s fullständiga flagglista (866 st, kategoriserade). Kontrollerad 2026-11-08 mot 69 stationer med
# kända frekvenser (VOLMET, MWARA, NAVTEX, DWD, Northwood, tidssignaler, fyrar): 66 av 69 har flagga.
# SK3JR: 11 av 69. Användarens val.
REFERENS = "http://kiwi-kuo.aprs.fi:8073"
REF_CACHE_TID = 7 * 24 * 3600      # listan ändras sällan


def typ_for(flagga):
    """Programmets typ för en flagga (egna har den direkt, Kiwins mappas från ägarens typnamn)."""
    t = flagga.get("typ") or ""
    if t in _FARG:
        return t
    low = t.lower()
    for k, v in _KIWI_TYP.items():
        if k in low:
            return v
    namn = (flagga.get("namn") or "").upper()
    if "VOLMET" in namn or "MWARA" in namn:
        return "Flyg"
    if "STANAG" in namn:
        return "Militär"
    if "FAX" in namn:
        return "Fax/väder"
    return "Övrigt"


def farg(flagga):
    return _FARG.get(typ_for(flagga), _FARG["Övrigt"])


def sander_nu(flagga, hhmm=None):
    if hhmm is None:
        t = datetime.datetime.now(datetime.timezone.utc)
        hhmm = t.hour * 100 + t.minute
    b, e = int(flagga.get("b", 0) or 0), int(flagga.get("e", 2400) or 2400)
    if b == 0 and e >= 2400:
        return True
    return (b <= hhmm < e) if b <= e else (hhmm >= b or hhmm < e)


def kiwi_nyckel(flagga):
    return f"{round(float(flagga['f']))}|{flagga.get('namn', '')}"


class Flaggor:
    def __init__(self, conf_dir, cache_dir):
        self.fil = os.path.join(conf_dir, "flaggor.json")
        self.cache_dir = os.path.join(cache_dir, "kiwiflaggor")
        self.egna = []
        self.kiwi = {}           # kiwi-nyckel (norm_url) -> lista
        self.kiwi_aktiv = None   # nyckel för den Kiwi som är källa just nu
        self.ref = None          # nyckel för referens-Kiwin (flaggor oavsett mottagare)
        self.ref_addr = None
        self.status = ""
        self._lock = threading.Lock()
        self._hamtar = set()
        self.on_change = None
        self._las()

    # ── egna ──
    def _las(self):
        try:
            with open(self.fil, encoding="utf-8") as fh:
                d = json.load(fh)
            self.egna = [x for x in d if isinstance(x, dict) and "f" in x]
        except FileNotFoundError:
            self.egna = []
        except (OSError, ValueError) as ex:
            log.warning("Kunde inte läsa %s: %s", self.fil, ex)
            self.egna = []

    def spara(self):
        try:
            os.makedirs(os.path.dirname(self.fil), exist_ok=True)
            tmp = self.fil + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.egna, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.fil)
        except OSError as ex:
            log.error("Kunde inte spara flaggorna: %s", ex)

    def lagg_till(self, f, namn, lage="USB", typ="Övrigt", noter="", b=0, e=2400, ersatter=None, kalla="egen"):
        x = {"id": uuid.uuid4().hex[:12], "f": float(f), "namn": namn.strip()[:60] or "Flagga", "lage": lage,
             "typ": typ, "not": noter.strip()[:200], "b": int(b), "e": int(e), "kalla": kalla,
             "skapad": time.strftime("%Y-%m-%d %H:%M")}
        if ersatter:
            x["ersatter"] = ersatter
        self.egna.append(x)
        self.spara()
        self._andrat()
        return x

    def andra(self, fid, **kw):
        for x in self.egna:
            if x.get("id") == fid:
                x.update({k: v for k, v in kw.items() if v is not None})
                self.spara()
                self._andrat()
                return x
        return None

    def ta_bort(self, fid):
        n = len(self.egna)
        self.egna = [x for x in self.egna if x.get("id") != fid]
        if len(self.egna) != n:
            self.spara()
            self._andrat()

    def dolj_kiwi(self, kiwiflagga):
        """Ta bort en Kiwi-flagga från visningen (sparas som en dold egen post som ersätter den)."""
        self.lagg_till(kiwiflagga["f"], kiwiflagga.get("namn", ""), kiwiflagga.get("lage", "USB"),
                       typ_for(kiwiflagga), kalla="dold", ersatter=kiwi_nyckel(kiwiflagga))

    # ── Kiwi ──
    kiwi_upptagen = None      # funktion som ger True när ljudet är uppkopplat (sätts av programmet)
    vantar_pa_stopp = None    # (nyckel, addr) för en hämtning som väntar på att lyssningen stoppas

    def _cachefil(self, nyckel):
        return os.path.join(self.cache_dir, nyckel.replace("/", "_").replace(":", "_") + ".json")

    def satt_kiwi(self, nyckel, addr, hamta=True):
        """Kiwin som är källa. Läser cachen och hämtar i bakgrunden om den saknas eller är äldre än ett dygn."""
        self.kiwi_aktiv = nyckel
        if nyckel is None:
            self._andrat()
            return
        p = self._cachefil(nyckel)
        alder = None
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            self.kiwi[nyckel] = d.get("flaggor", [])
            alder = time.time() - float(d.get("tid", 0))
        except (OSError, ValueError):
            pass
        self._andrat()
        if hamta and (alder is None or alder > CACHE_TID):
            self.hamta(nyckel, addr)

    def satt_referens(self, nyckel, addr):
        """Referens-Kiwin (None = av). Läser cachen och hämtar i bakgrunden om den saknas eller är äldre än en
        vecka. Hämtningen går mot en annan Kiwi än den du lyssnar på, så den stör inte ljudet."""
        self.ref, self.ref_addr = nyckel, addr
        if nyckel is None:
            self._andrat()
            return
        alder = None
        try:
            with open(self._cachefil(nyckel), encoding="utf-8") as fh:
                d = json.load(fh)
            with self._lock:
                self.kiwi[nyckel] = d.get("flaggor", [])
            alder = time.time() - float(d.get("tid", 0))
        except (OSError, ValueError):
            pass
        self._andrat()
        if alder is None or alder > REF_CACHE_TID:
            self.hamta(nyckel, addr)

    def hamta(self, nyckel, addr):
        """Hämtar Kiwins flaggor, men aldrig medan ljudet från samma Kiwi är uppkopplat. Då väntar hämtningen
        till stoppet."""
        if nyckel in self._hamtar:
            return True
        if nyckel == self.kiwi_aktiv and self.kiwi_upptagen and self.kiwi_upptagen():
            self.vantar_pa_stopp = (nyckel, addr)
            self.status = "hämtas när lyssningen stoppas"
            self._andrat()
            return False
        self.vantar_pa_stopp = None
        self._hamtar.add(nyckel)
        self.status = "hämtar Kiwins flaggor …"
        self._andrat()
        threading.Thread(target=self._hamta, args=(nyckel, addr), name="KiwiFlaggor", daemon=True).start()
        return True

    def har_cache(self, nyckel):
        return self._alder(nyckel) is not None

    def _alder(self, nyckel):
        """Cachens ålder i sekunder, eller None om den saknas."""
        try:
            with open(self._cachefil(nyckel), encoding="utf-8") as fh:
                return time.time() - float(json.load(fh).get("tid", 0))
        except (OSError, ValueError):
            return None

    def efter_stopp(self, nyckel, addr, src):
        """Körs när lyssningen stoppats. Hämtar flaggorna om de väntat på stoppet eller om cachen är äldre än ett dygn."""
        def arbeta():
            slut = time.monotonic() + 15
            while getattr(src, "cli", None) is not None and time.monotonic() < slut:
                time.sleep(0.1)
            vantar = self.vantar_pa_stopp is not None and self.vantar_pa_stopp[0] == nyckel
            alder = self._alder(nyckel)
            if vantar or alder is None or alder > CACHE_TID:
                self.hamta(nyckel, addr)
        threading.Thread(target=arbeta, name="KiwiFlaggorStopp", daemon=True).start()

    def vanta_pa_hamtning(self, nyckel, timeout=70.0):
        """Väntar tills flagghämtningen för Kiwin är klar, så att ljudkanalen inte öppnas samtidigt."""
        slut = time.monotonic() + timeout
        vantade = nyckel in self._hamtar
        while nyckel in self._hamtar and time.monotonic() < slut:
            time.sleep(0.2)
        if vantade:
            time.sleep(1)     # KiwiSDR tar inte emot ljudet direkt efter flaggkanalen (0,5 s kopplade ner)

    def _hamta(self, nyckel, addr):
        import kiwisdr
        try:
            lst = kiwisdr.hamta_flaggor(addr)
            with self._lock:
                self.kiwi[nyckel] = lst
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self._cachefil(nyckel), "w", encoding="utf-8") as fh:
                json.dump({"tid": time.time(), "addr": addr, "flaggor": lst}, fh, ensure_ascii=False)
            self.status = f"{len(lst)} flaggor från " + ("referens-Kiwin" if nyckel == self.ref else "Kiwin")
        except Exception as ex:
            log.warning("Kunde inte hämta Kiwins flaggor (%s): %s", addr, ex)
            self.status = f"kunde inte hämta Kiwins flaggor: {ex}"
        finally:
            self._hamtar.discard(nyckel)
            self._andrat()

    def _andrat(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.exception("Fel i flaggornas on_change")

    # ── visning ──
    def synliga(self, lo_hz, hi_hz, visa_kiwi=True):
        """Flaggor inom lo–hi (Hz) att rita: egna (alltid) och Kiwins (som sänder nu och inte ersatts)."""
        ut = []
        ersatta = {x.get("ersatter") for x in self.egna if x.get("ersatter")}
        for x in self.egna:
            if x.get("kalla") != "dold" and lo_hz <= x["f"] <= hi_hz:
                ut.append(dict(x, _egen=True))
        if visa_kiwi and self.kiwi_aktiv:
            with self._lock:
                lst = list(self.kiwi.get(self.kiwi_aktiv, []))
            t = datetime.datetime.now(datetime.timezone.utc)
            hhmm = t.hour * 100 + t.minute
            for x in lst:
                if lo_hz <= x["f"] <= hi_hz and kiwi_nyckel(x) not in ersatta and sander_nu(x, hhmm):
                    ut.append(dict(x, _egen=False))
        if self.ref and self.ref != (self.kiwi_aktiv if visa_kiwi else None):
            # referens-Kiwins flaggor där varken du eller mottagarens Kiwi redan har en
            with self._lock:
                lst = list(self.kiwi.get(self.ref, []))
            t = datetime.datetime.now(datetime.timezone.utc)
            hhmm = t.hour * 100 + t.minute
            upptagna = sorted(x["f"] for x in ut)
            for x in lst:
                if (lo_hz <= x["f"] <= hi_hz and kiwi_nyckel(x) not in ersatta and sander_nu(x, hhmm)
                        and not any(abs(x["f"] - f) < 600 for f in upptagna)):
                    ut.append(dict(x, _egen=False, _ref=True))
        ut.sort(key=lambda x: x["f"])
        return ut


def bevis_kontext(flaggor, f_hz, tol_hz=3500.0):
    """Mjukt stöd till bevismotorn (bevis.py) från flaggorna nära f_hz: [(hypotes, poäng, förklaring)].

    En flagga är inget facit – den säger vad som brukar sända här. Kiwi-flaggornas frekvens är ofta
    uppringningsfrekvensen (för USB ligger signalen 0–3 kHz ovanför), därav den breda toleransen."""
    if flaggor is None:
        return []
    ut = []
    for x in flaggor.synliga(f_hz - tol_hz, f_hz + tol_hz):
        lage = program_lage(x.get("lage")) or ""
        if lage == "USB" and not (x["f"] - 300 <= f_hz <= x["f"] + 3300):
            continue
        if lage == "LSB" and not (x["f"] - 3300 <= f_hz <= x["f"] + 300):
            continue
        if lage not in ("USB", "LSB") and abs(x["f"] - f_hz) > 1500:
            continue
        typ = typ_for(x)
        # namn, Kiwins typ och anteckning: t.ex. "DDH7 GER", typ "FSK", "FSK WX" (DWD:s väder-RTTY)
        namn = " ".join(str(x.get(k) or "") for k in ("namn", "typ", "not")).upper()
        txt = f"flagga {x.get('namn') or typ}"
        if typ in ("Fax/väder",) or "FAX" in namn:
            ut.append(("FSK2", 0.6, txt))
        elif any(k in namn for k in ("RTTY", "NAVTEX", "SITOR", "FSK")):
            ut.append(("FSK2", 0.8, txt))
        elif "STANAG" in namn:
            ut.append(("LIN", 0.8, txt))
        elif typ == "HFDL":
            ut.append(("BPSK", 0.4, txt))
            ut.append(("PSK8", 0.4, txt))
        elif typ in ("Data", "Militär", "Spion"):
            pass                              # för brett för att säga något om modulationen
        elif lage == "AM":
            ut.append(("AM", 0.8, txt))
        elif lage == "CW" or typ in ("Fyr/bake", "Tid"):
            ut.append(("OOK", 0.6, txt))
        elif lage in ("USB", "LSB"):
            ut.append(("SSB", 0.8, txt))
        elif lage in ("NFM", "WFM"):
            ut.append(("FM", 0.8, txt))
    # en flagga per hypotes räcker (flera flaggor på samma ställe ska inte räknas flera gånger)
    bast = {}
    for h, v, t in ut:
        if h not in bast or v > bast[h][1]:
            bast[h] = (h, v, t)
    return list(bast.values())


# KiwiSDR:ns lägesnamn (kiwisdr.DX_LAGEN) → programmets lägen
_KIWI_LAGE = {"AMN": "AM", "AMW": "AM", "SAM": "AM", "SAU": "USB", "SAL": "LSB", "SAS": "AM", "DRM": "AM",
              "QAM": "AM", "CWN": "CW", "NBFM": "NFM", "NNFM": "NFM", "USN": "USB", "LSN": "LSB", "IQ": None}


def program_lage(lage, lagen=("WFM", "NFM", "AM", "USB", "LSB", "CW")):
    """Flaggans läge som ett läge programmet har (None = okänt, behåll nuvarande)."""
    m = (lage or "").upper()
    if m in lagen:
        return m
    return _KIWI_LAGE.get(m)
