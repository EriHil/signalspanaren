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

    def hamta(self, nyckel, addr):
        if nyckel in self._hamtar:
            return
        self._hamtar.add(nyckel)
        self.status = "hämtar Kiwins flaggor …"
        self._andrat()
        threading.Thread(target=self._hamta, args=(nyckel, addr), name="KiwiFlaggor", daemon=True).start()

    def vanta_pa_hamtning(self, nyckel, timeout=70.0):
        """Väntar tills flagghämtningen för Kiwin är klar, så att ljudkanalen inte öppnas samtidigt."""
        slut = time.monotonic() + timeout
        vantade = nyckel in self._hamtar
        while nyckel in self._hamtar and time.monotonic() < slut:
            time.sleep(0.2)
        if vantade:
            time.sleep(5)     # KiwiSDR tar inte emot ljudet direkt efter flaggkanalen (sett i test)

    def _hamta(self, nyckel, addr):
        import kiwisdr
        try:
            lst = kiwisdr.hamta_flaggor(addr)
            with self._lock:
                self.kiwi[nyckel] = lst
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self._cachefil(nyckel), "w", encoding="utf-8") as fh:
                json.dump({"tid": time.time(), "addr": addr, "flaggor": lst}, fh, ensure_ascii=False)
            self.status = f"{len(lst)} flaggor från Kiwin"
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
        ut.sort(key=lambda x: x["f"])
        return ut
