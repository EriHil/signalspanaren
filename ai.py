# SPDX-License-Identifier: GPL-3.0-or-later
"""AI-chatt för signalanalysen. Standard: gratis online-AI utan nyckel (Pollinations).
Valbart: Claude med egen API-nyckel. Nyckeln sparas med filrättigheter 600 och loggas aldrig."""
import json
import os
import re
import urllib.request

GRATIS_URL = "https://text.pollinations.ai/openai"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
STANDARD = {"aktiv": True, "backend": "gratis", "gratis_modell": "openai", "claude_modell": "", "api_key": ""}
SYSTEM = ("Du är en assistent i programmet Signalspanaren för radioanalys. Svara på svenska och var tydlig med "
          "vad som är mätt och vad som är en gissning. Klassa aldrig en signal som säker utan tydliga belägg. "
          "När ni är överens om vilken signal det är, avsluta svaret med en egen rad som börjar med FÖRSLAG= "
          "följt av ett JSON-objekt på en rad med nycklarna namn, typ och not. Skriv ingen FÖRSLAG-rad annars.")


def las_config(sokvag):
    try:
        with open(sokvag, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {}
    return {**STANDARD, **{k: v for k, v in d.items() if k in STANDARD}}


def spara_config(sokvag, cfg):
    os.makedirs(os.path.dirname(sokvag), exist_ok=True)
    tmp = sokvag + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({k: cfg.get(k, STANDARD[k]) for k in STANDARD}, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, sokvag)


def forslag_ur(text):
    m = re.search(r"FÖRSLAG=(\{.*\})", text or "")
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except ValueError:
        return None
    return d if isinstance(d, dict) and str(d.get("namn", "")).strip() else None


def sammanfattning(r, klartext=""):
    if not r:
        return "Ingen signal analyseras just nu."
    rader = [f"Frekvens: {r.get('f_abs', 0) / 1e6:.6f} MHz",
             f"Typ (mätt): {r.get('fam')} / {r.get('klass')}",
             f"Baud: {r.get('baud') or '–'}, shift: {r.get('shift') or '–'} Hz, "
             f"bandbredd: {r.get('occ_bw') or '–'} Hz",
             f"SNR: {r.get('snr') or 0:.1f} dB"]
    k = r.get("kandidater") or []
    if k:
        rader.append("Kandidater: " + ", ".join(f"{e.get('namn')} {s:.0f} %" for s, e, _w in k[:4]))
    if klartext:
        rader.append("Avkodad text (senaste):\n" + klartext[-500:])
    return "\n".join(rader)


def systemprompt(analys=None):
    return SYSTEM if not analys else SYSTEM + "\n\nAktuell analys:\n" + analys


def _post(url, body, headers, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def svar(cfg, meddelanden, system):
    """Skickar chatten till vald backend och returnerar svaret som text."""
    if not cfg.get("aktiv", True):
        raise RuntimeError("AI-chatten är avstängd (slå på den under inställningar)")
    if cfg.get("backend") == "claude":
        if not cfg.get("api_key"):
            raise RuntimeError("ingen API-nyckel för Claude (ställ in den under inställningar)")
        if not cfg.get("claude_modell"):
            raise RuntimeError("ange en Claude-modell under inställningar")
        d = _post(CLAUDE_URL, {"model": cfg["claude_modell"], "max_tokens": 1024, "system": system,
                               "messages": meddelanden},
                  {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    d = _post(GRATIS_URL, {"model": cfg.get("gratis_modell") or "openai",
                           "messages": [{"role": "system", "content": system}] + meddelanden}, {})
    return d.get("choices", [{}])[0].get("message", {}).get("content", "")
