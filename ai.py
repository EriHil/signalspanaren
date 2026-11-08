# SPDX-License-Identifier: GPL-3.0-or-later
"""AI-chatt för signalanalysen. Standard: gratis online-AI utan nyckel (Pollinations).
Valbart: Groq, Gemini och OpenRouter med egen API-nyckel. Alla val har en gratisnivå;
modeller som kräver betalning finns inte med. Nycklarna sparas med filrättigheter 600 och loggas aldrig."""
import json
import os
import re
import urllib.error
import urllib.request

GRATIS_URL = "https://text.pollinations.ai/openai"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
STANDARD = {"aktiv": True, "backend": "gratis", "gratis_modell": "openai",
            "groq_modell": "openai/gpt-oss-20b", "groq_api_key": "",
            "gemini_modell": "gemini-3.8-flash", "gemini_api_key": "",
            "openrouter_modell": "nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter_api_key": ""}
NAMN = {"gratis": "Gratis (Pollinations)", "groq": "Groq", "gemini": "Gemini",
        "openrouter": "OpenRouter"}
# Endast gratismodeller. Modeller som kräver betalning finns inte med i listan.
MODELLER = {"gratis": ["openai"],
            "groq": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
            "gemini": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
            "openrouter": ["nvidia/nemotron-3-ultra-550b-a55b:free",
                           "recursive/flint-v1.0:free",
                           "nvidia/nemotron-3.5-super-vl:free",
                           "dots-studio/dots-3-note-preview:free",
                           "cohere/north-mini-code:free",
                           "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                           "liquid/lfm-2.5-2.6b:free",
                           "nvidia/nemotron-3.5-lightning:free",
                           "liquid/lfm-3-1.7b:free",
                           "xingchen-agi/xing4.0-29b-a4b:free",
                           "google/gemma-4-26b-a4b-it:free",
                           "google/gemma-4-31b-it:free"]}
INFO = {"gratis": "Ingen nyckel krävs. Ett svar åt gången, ungefär var 30–45 s. Analysdata skickas till Pollinations.",
        "groq": "Gratis API-nyckel från console.groq.com, ingen betalmetod krävs för gratisnivån. "
                "Analysdata skickas till Groq.",
        "gemini": "Gratis API-nyckel från aistudio.google.com. Gratisnivån får användas för att förbättra Googles "
                  "produkter, så skicka inget känsligt. Analysdata skickas till Google.",
        "openrouter": "Gratis API-nyckel från openrouter.ai. Listan innehåller bara gratismodeller; en del kan "
                      "vara tillfälligt upptagna (HTTP 429). Data från gratismodellerna kan användas för att "
                      "förbättra modellerna, så skicka inget känsligt. Analysdata skickas till OpenRouter."}
SYSTEM = ("Du är en assistent i programmet Signalspanaren för radioanalys. Svara på svenska och var tydlig med "
          "vad som är mätt och vad som är en gissning. Klassa aldrig en signal som säker utan tydliga belägg. "
          "När användaren och du är överens om vilken signal det är, avsluta svaret med en egen rad som börjar med FÖRSLAG= "
          "direkt följt av ett JSON-objekt på samma rad med nycklarna namn, typ och not. Skriv ingen FÖRSLAG-rad annars. "
          "Skriv aldrig att något har sparats. Det gör användaren med knappen.")


def las_config(sokvag):
    try:
        with open(sokvag, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {}
    cfg = {**STANDARD, **{k: v for k, v in d.items() if k in STANDARD}}
    if cfg.get("backend") not in NAMN:
        cfg["backend"] = "gratis"
    return cfg


def spara_config(sokvag, cfg):
    os.makedirs(os.path.dirname(sokvag), exist_ok=True)
    tmp = sokvag + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({k: cfg.get(k, STANDARD[k]) for k in STANDARD}, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, sokvag)


def forslag_ur(text):
    m = re.search(r"FÖRSLAG[*_\s]*=[*_\s]*(\{.*\})", text or "")
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


def nyckel_falt(backend):
    """Config-nyckeln för leverantörens API-nyckel."""
    return f"{backend}_api_key"


def val_modeller(cfg):
    """Modellerna i chatten som (leverantör, modell-ID). Den inställda modellen finns alltid med."""
    val = [(b, m) for b in NAMN for m in MODELLER[b]]
    for b in NAMN:
        m = cfg.get(f"{b}_modell") or ""
        if m and (b, m) not in val:
            val.append((b, m))
    return val


def etikett(backend, modell):
    return f"{NAMN[backend]} · {modell or 'ingen modell vald'}"


def beskrivning(cfg):
    """Texten under modellvalet i chatten: vald modell och vart analysdatan skickas."""
    if not cfg.get("aktiv", True):
        return "AI-chatten är avstängd. Slå på den under inställningar (⚙)."
    b = cfg.get("backend") if cfg.get("backend") in NAMN else "gratis"
    m = cfg.get(f"{b}_modell")
    return f"{etikett(b, m)}. {INFO[b]}"


def _post(url, body, headers, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        vanta = ex.headers.get("Retry-After")
        raise RuntimeError(f"HTTP {ex.code}" + (f", försök igen om {vanta} s" if vanta else "")) from None


def svar(cfg, meddelanden, system):
    """Skickar chatten till vald backend och returnerar svaret som text."""
    if not cfg.get("aktiv", True):
        raise RuntimeError("AI-chatten är avstängd (slå på den under inställningar)")
    if cfg.get("backend") in ("groq", "gemini", "openrouter"):
        b = cfg["backend"]
        nyckel = cfg.get(nyckel_falt(b), "")
        if not nyckel:
            raise RuntimeError(f"ingen API-nyckel för {NAMN[b]} (ställ in den under inställningar)")
        d = _post({"groq": GROQ_URL, "gemini": GEMINI_URL, "openrouter": OPENROUTER_URL}[b],
                  {"model": cfg.get(f"{b}_modell") or MODELLER[b][0],
                   "messages": [{"role": "system", "content": system}] + meddelanden},
                  {"Authorization": f"Bearer {nyckel}"})
        return d.get("choices", [{}])[0].get("message", {}).get("content") or ""
    d = _post(GRATIS_URL, {"model": cfg.get("gratis_modell") or "openai",
                           "messages": [{"role": "system", "content": system}] + meddelanden}, {})
    return d.get("choices", [{}])[0].get("message", {}).get("content", "")
