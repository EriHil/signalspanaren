#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bygger ett större facitbibliotek med riktiga signaler för analystest.

  python3 bibliotek.py kandidater FLAGGFIL [--max-per-typ N]   lista kandidater ur en Kiwis flaggcache
  python3 bibliotek.py spela FLAGGFIL --kiwi ADRESS [--sek 20]  spela in kandidaterna till inkorg/
  python3 bibliotek.py sålla                                   sortera inkorgen: utan-signal/ eller granska/
  python3 bibliotek.py bilder UTMAPP                           spektrum + vattenfall per klipp (för granskning)

Kandidaterna kommer ur KiwiSDR:ns stationsflaggor (typ, namn, läge). Flaggan är bara en kandidat: ett klipp
blir facit först när spektrumet har granskats (osäkra till osakra/). Inspelningarna läggs i
facit-klipp/inkorg/, som analystest inte läser. En anslutning åt gången – Kiwis har få platser.
"""
import argparse
import json
import os
import re
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analystest  # noqa: E402
import analys  # noqa: E402
import iqfil  # noqa: E402

MAPP = analystest.KLIPP_MAPP if hasattr(analystest, "KLIPP_MAPP") else os.path.expanduser(
    "~/.local/share/signalspanaren/facit-klipp")
INKORG = os.path.join(MAPP, "inkorg")
GRANSKA = os.path.join(MAPP, "granska")
UTAN = os.path.join(MAPP, "utan-signal")

SSB = (["SSB"], [1800, 3400])
MODEM = (["PSK8", "QPSK", "BPSK", "LIN"], [1800, 3400])


def forvantan(fl):
    """(fam, bredd, mitt_hz) för en flagga, eller None om modulationen inte är förutsägbar.
    mitt_hz = var signalens mitt väntas (för USB ligger talet ovanför den inställda frekvensen)."""
    typ = fl.get("typ") or ""
    namn = (fl.get("namn") or "").upper()
    noter = (fl.get("not") or "").upper()
    lage = (fl.get("lage") or "").upper()
    f = float(fl["f"])
    usb = lage in ("USB", "USN")
    if typ == "Aero" and usb and ("VOLMET" in namn or "MWARA" in namn):
        return SSB[0], SSB[1], f + 1650.0
    if typ == "HFDL" and usb:
        return MODEM[0], MODEM[1], f + 1440.0
    if typ == "FAX":
        return ["FM", "FSK2"], [600, 1500], (f + 1900.0 if usb else f)
    if typ == "FSK" or (typ == "Marine" and ("NAVTEX" in namn or "SITOR" in namn or "DSC" in namn)):
        return ["FSK2"], [100, 1300], f
    if typ == "Milcom" and "STANAG" in namn and usb:      # marinens sändningar: i regel STANAG 4285
        return MODEM[0], [2400, 3400], f + 1800.0
    if typ == "Milcom" and "MSK" in noter:
        return ["MSK", "FSK2"], [50, 400], f
    if typ == "Milcom" and len(namn.split()[0]) == 1 and lage in ("CW", "CWN"):
        return ["OOK", "BÄRVÅG"], [5, 300], f            # ryska marinens enbokstavsfyrar
    if typ in ("CW",) or "IBP" in namn:
        return ["OOK", "BÄRVÅG"], [5, 300], f
    if typ == "Time":
        if lage in ("AM", "AMN"):
            return ["AM", "OOK"], [300, 10500], f
        return ["OOK", "BÄRVÅG"], [5, 300], f
    if typ == "Channel" and lage in ("AM", "AMN"):
        return ["AM"], [3000, 10500], f                   # långvågsrundradio
    return None


def kandidater(flaggfil, max_per_typ=20):
    with open(flaggfil, encoding="utf-8") as fh:
        d = json.load(fh)
    fl = d.get("flaggor", d) if isinstance(d, dict) else d
    ut, per = [], {}
    for x in sorted(fl, key=lambda x: x["f"]):
        if not (10e3 <= x["f"] <= 30e6):
            continue
        v = forvantan(x)
        if v is None:
            continue
        fam, bredd, mitt = v
        grupp = (x.get("typ") or "?", fam[0])         # t.ex. Milcom: STANAG, MSK och fyrar var för sig
        if per.get(grupp, 0) >= max_per_typ:
            continue
        per[grupp] = per.get(grupp, 0) + 1
        ut.append({"namn": f"{x.get('namn', '')} {x['f'] / 1e3:g} kHz".strip(), "frekvens": round(mitt),
                   "fam": fam, "bredd": bredd, "flagga": x, "kandidat": True})
    return ut


def spela(args):
    lista = kandidater(args.flaggfil, args.max_per_typ)
    if args.typ:
        lista = [k for k in lista if k["flagga"].get("typ") in args.typ]
    os.makedirs(INKORG, exist_ok=True)
    gjorda = {json.load(open(os.path.join(INKORG, fn), encoding="utf-8"))["global"]
              .get("signalspanaren:facit", {}).get("namn") for fn in os.listdir(INKORG) if fn.endswith(".sigmf-meta")}
    print(f"{len(lista)} kandidater, {len(gjorda)} redan inspelade", flush=True)
    for i, k in enumerate(lista):
        if k["namn"] in gjorda:
            continue
        f = float(k["frekvens"])
        try:
            z, rate = analystest.spela_in_kiwi(args.kiwi, f, args.sek)
        except Exception as ex:
            print(f"[{i + 1}/{len(lista)}] {k['namn']}: misslyckades: {ex}", flush=True)
            time.sleep(5)
            continue
        k = dict(k, not_=f"kandidat från flagga, {args.kiwi}, {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
        base = analystest.spara_klipp(INKORG, k, z, rate, f, args.kiwi)
        print(f"[{i + 1}/{len(lista)}] {k['namn']}: {os.path.basename(base)} topp "
              f"{analystest.kontroll(z, rate):.0f} dB", flush=True)
        time.sleep(1.5)
    return 0


def matt(z, rate, mitt_off, bredd):
    """Syns något där signalen väntas? Returnerar (toppens dB över golvet, bandets dB över golvet)."""
    f, P = analys._welch(np.asarray(z, np.complex64), rate, 1024)
    Pd = 10 * np.log10(P + 1e-30)
    golv = float(np.percentile(Pd, 20))
    hw = max(150.0, bredd[1] / 2)
    sel = np.abs(f - mitt_off) <= hw
    if not sel.any():
        return 0.0, 0.0
    return float(Pd[sel].max() - golv), float(np.median(Pd[sel]) - golv)


def salla(args):
    os.makedirs(GRANSKA, exist_ok=True)
    os.makedirs(UTAN, exist_ok=True)
    for fn in sorted(os.listdir(INKORG)):
        if not fn.endswith(".sigmf-meta"):
            continue
        base = os.path.join(INKORG, fn[:-len(".sigmf-meta")])
        r = iqfil.SigMFReader(base + ".sigmf-meta") if hasattr(iqfil, "SigMFReader") else None
        meta = json.load(open(base + ".sigmf-meta", encoding="utf-8"))
        facit = meta["global"]["signalspanaren:facit"]
        z, rate = analystest_las(base)
        mitt = float(meta["captures"][0]["core:frequency"])
        topp, band = matt(z, rate, float(facit["frekvens"]) - mitt, facit["bredd"])
        smal = facit["bredd"][1] <= 1300
        har = (topp >= 10 if smal else (band >= 2.0 or topp >= 12))
        dst = GRANSKA if har else UTAN
        for ext in (".sigmf-meta", ".sigmf-data"):
            os.replace(base + ext, os.path.join(dst, os.path.basename(base) + ext))
        print(f"{'GRANSKA' if har else 'utan   '} topp {topp:5.1f} band {band:4.1f}  {facit['namn']}")
    return 0


def analystest_las(base):
    meta = json.load(open(base + ".sigmf-meta", encoding="utf-8"))
    dt = meta["global"]["core:datatype"]
    rate = float(meta["global"]["core:sample_rate"])
    raw = np.fromfile(base + ".sigmf-data", np.float32 if dt.startswith("cf32") else np.uint8)
    if dt.startswith("cf32"):
        z = raw.view(np.complex64)
    else:
        z = ((raw.astype(np.float32) - 127.5) / 127.5).view(np.complex64)
    return z, rate


def forslag(z, rate, off, bredd):
    """Var signalen faktiskt ligger: analysens bandbreddsmätning kring den förväntade mitten.
    Returnerar (mitt_off, bredd) eller None."""
    t = np.arange(len(z)) / rate
    zz = (np.asarray(z, np.complex64) * np.exp(-2j * np.pi * off * t)).astype(np.complex64)
    n = int(rate * 4)
    zz = zz[len(zz) // 2 - n // 2:len(zz) // 2 + n // 2] if len(zz) > n else zz
    near = max(200.0, min(1500.0, bredd[1] / 2))
    r = analys.fit_bandwidth(zz, rate, min(rate * 0.9, max(3 * bredd[1], 3000.0)), near_hz=near)
    if r is None:
        r = analys._fsk_par(zz, rate, min(rate * 0.9, max(3 * bredd[1], 3000.0)))
    return None if r is None else (off + r[0], r[1])


def _rita_klipp(ctx, x0, y0, w, h, z, rate, off, bredd, titel, forsl=None):
    """Ett klipp: spektrum (vänster) och vattenfall (höger) med förväntad signal markerad i rött."""
    import cairo
    f, P = analys._welch(np.asarray(z, np.complex64), rate, 2048)
    Pd = 10 * np.log10(P + 1e-30)
    lo, hi = float(np.percentile(Pd, 5)), float(Pd.max()) + 2
    ws = w * 0.42
    ctx.set_source_rgb(0.08, 0.1, 0.13)
    ctx.rectangle(x0, y0, w, h)
    ctx.fill()

    def fx(fr, bx, bw):
        return bx + (fr + rate / 2) / rate * bw
    # förväntad signal
    ctx.set_source_rgba(1, 0.2, 0.2, 0.18)
    ctx.rectangle(fx(off - bredd / 2, x0, ws), y0 + 14, max(1.0, bredd / rate * ws), h - 16)
    ctx.fill()
    if forsl is not None:
        ctx.set_source_rgba(1, 0.85, 0.2, 0.35)
        ctx.rectangle(fx(forsl[0] - forsl[1] / 2, x0, ws), y0 + 14, max(1.5, forsl[1] / rate * ws), h - 16)
        ctx.fill()
    ctx.set_source_rgb(0.5, 0.9, 0.85)
    ctx.set_line_width(0.8)
    for k in range(len(f)):
        xx = fx(f[k], x0, ws)
        yy = y0 + h - 2 - float(np.clip((Pd[k] - lo) / (hi - lo + 1e-9), 0, 1)) * (h - 18)
        ctx.line_to(xx, yy) if k else ctx.move_to(xx, yy)
    ctx.stroke()
    # vattenfall
    n = 512
    m = len(z) // n
    S = 10 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(z[:m * n].reshape(m, n) * np.hanning(n), axis=1),
                                             axes=1)) ** 2 + 1e-20)
    a, b = np.percentile(S, 40), np.percentile(S, 99.5)
    v = np.clip((S - a) / (b - a + 1e-9), 0, 1)
    img = np.zeros((m, n, 4), np.uint8)
    img[..., 0] = (v * 255).astype(np.uint8)                 # cairo FORMAT_RGB24: B, G, R, x
    img[..., 1] = (np.sqrt(v) * 230).astype(np.uint8)
    img[..., 2] = (v ** 2 * 255).astype(np.uint8)
    surf = cairo.ImageSurface.create_for_data(memoryview(np.ascontiguousarray(img)), cairo.FORMAT_RGB24, n, m)
    wx, ww = x0 + ws + 6, w - ws - 8
    ctx.save()
    ctx.translate(wx, y0 + 14)
    ctx.scale(ww / n, (h - 16) / m)
    ctx.set_source_surface(surf, 0, 0)
    ctx.get_source().set_filter(cairo.FILTER_NEAREST)
    ctx.paint()
    ctx.restore()
    ctx.set_source_rgb(1, 0.3, 0.3)
    ctx.set_line_width(0.7)
    for e in (off - bredd / 2, off + bredd / 2):
        ctx.move_to(fx(e, wx, ww), y0 + 14)
        ctx.line_to(fx(e, wx, ww), y0 + h - 2)
    ctx.stroke()
    ctx.set_source_rgb(0.95, 0.95, 0.95)
    ctx.select_font_face("Sans", 0, 0)
    ctx.set_font_size(11)
    ctx.move_to(x0 + 4, y0 + 11)
    ctx.show_text(titel)


def bilder(args):
    import cairo
    os.makedirs(args.utmapp, exist_ok=True)
    mapp = args.fran or GRANSKA
    filer = sorted(fn for fn in os.listdir(mapp) if fn.endswith(".sigmf-meta"))
    per, W, H = 6, 1100, 150
    index = {}
    for s in range(0, len(filer), per):
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H * per)
        ctx = cairo.Context(surf)
        for j, fn in enumerate(filer[s:s + per]):
            base = os.path.join(mapp, fn[:-len(".sigmf-meta")])
            meta = json.load(open(base + ".sigmf-meta", encoding="utf-8"))
            facit = meta["global"]["signalspanaren:facit"]
            z, rate = analystest_las(base)
            mitt = float(meta["captures"][0]["core:frequency"])
            off = float(facit["frekvens"]) - mitt
            fs_ = forslag(z, rate, off, facit["bredd"])
            fl_txt = "inget förslag" if fs_ is None else f"förslag {fs_[0] - off:+.0f} Hz, {fs_[1]:.0f} Hz bred"
            _rita_klipp(ctx, 0, j * H, W, H - 4, z, rate, off, facit["bredd"][1],
                        f"{s + j}: {facit['namn'][:40]} — väntat {'/'.join(facit['fam'])}, "
                        f"{facit['bredd'][0]}–{facit['bredd'][1]} Hz — {fl_txt}  (±{rate / 2000:.0f} kHz)", fs_)
            index[str(s + j)] = {"fil": base, "forslag": fs_, "mitt": mitt}
        surf.write_to_png(os.path.join(args.utmapp, f"granska_{s // per:03d}.png"))
    with open(os.path.join(args.utmapp, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
    print(f"{len(filer)} klipp, bilder i {args.utmapp}")
    return 0


def godkann(args):
    """Flytta granskade klipp: facit (med uppmätt mitt/bredd), osakra/ eller utan-signal/.
    args.nr = lista 'N' (förslaget gäller), 'N:mitt_off:bredd' (egna värden) – ur bilder-index."""
    with open(os.path.join(args.bildmapp, "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    mal = {"facit": MAPP, "osakra": os.path.join(MAPP, "osakra"), "utan": UTAN}[args.till]
    os.makedirs(mal, exist_ok=True)
    for spec in args.nr:
        delar = spec.split(":")
        post = index[delar[0]]
        base = post["fil"]
        if not os.path.exists(base + ".sigmf-meta"):
            print(f"{delar[0]}: redan flyttad")
            continue
        meta = json.load(open(base + ".sigmf-meta", encoding="utf-8"))
        facit = dict(meta["global"]["signalspanaren:facit"])
        if args.till == "facit":
            if len(delar) == 3:
                moff, bw = float(delar[1]), float(delar[2])
            elif post["forslag"] is not None:
                moff, bw = post["forslag"]
            else:
                print(f"{delar[0]}: inget förslag – ange N:mitt_off:bredd")
                continue
            facit["frekvens"] = round(post["mitt"] + moff)
            facit["uppmatt_bredd"] = round(bw)
            facit.pop("kandidat", None)
            facit["not"] = (facit.pop("not_", "") + f"; granskad {time.strftime('%Y-%m-%d')}").strip("; ")
            if args.fam:
                facit["fam"] = args.fam
            if args.bredd:
                facit["bredd"] = [float(x) for x in args.bredd.split("-")]
            meta["global"]["signalspanaren:facit"] = facit
            with open(base + ".sigmf-meta", "w", encoding="utf-8") as fh:
                json.dump(meta, fh, ensure_ascii=False, indent=1)
        for ext in (".sigmf-meta", ".sigmf-data"):
            os.replace(base + ext, os.path.join(mal, os.path.basename(base) + ext))
        print(f"{delar[0]}: {facit['namn']} → {args.till}"
              + (f" ({facit['frekvens'] / 1e3:.3f} kHz)" if args.till == "facit" else ""))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    k = sp.add_parser("kandidater")
    k.add_argument("flaggfil")
    k.add_argument("--max-per-typ", type=int, default=20)
    s = sp.add_parser("spela")
    s.add_argument("flaggfil")
    s.add_argument("--kiwi", required=True)
    s.add_argument("--sek", type=float, default=20.0)
    s.add_argument("--max-per-typ", type=int, default=20)
    s.add_argument("--typ", nargs="*")
    sp.add_parser("sålla")
    b = sp.add_parser("bilder")
    b.add_argument("utmapp")
    b.add_argument("--fran", help="mapp med klipp (standard granska/)")
    g = sp.add_parser("godkänn")
    g.add_argument("bildmapp")
    g.add_argument("till", choices=["facit", "osakra", "utan"])
    g.add_argument("nr", nargs="+")
    g.add_argument("--fam", nargs="*", help="byt förväntad familj")
    g.add_argument("--bredd", help="byt facitbredd, t.ex. 1800-3400")
    a = p.parse_args()
    if a.cmd == "kandidater":
        lst = kandidater(a.flaggfil, a.max_per_typ)
        import collections
        print(collections.Counter((x["flagga"]["typ"], x["fam"][0]) for x in lst).most_common())
        for x in lst:
            print(f"{x['frekvens'] / 1e3:10.1f} kHz  {'/'.join(x['fam']):22} {x['namn']}")
        return 0
    return {"spela": spela, "sålla": salla, "bilder": bilder, "godkänn": godkann}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
