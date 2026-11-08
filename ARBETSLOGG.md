# Arbetslogg – Signalspanaren, Morse-avkodaren

Den här grenen (`arbetslogg`) innehåller bara loggen och delar ingen historik med koden.
Koden finns på grenen `claude/determined-planck-jtw8lg`.

---

## 2026-11-08 – Morse/CW: ny avkodare, jämförelse, träning och generator

Arbetet gjordes i en Claude Code-session i molnet på uppdrag av erihil. Alla tider är UTC.

### Sammanfattning

| | Före | Efter |
|---|---|---|
| Syntetiska testfall (`analystest.py morse`) | 19,6 % teckenfel (12 fall) | 4,6 % (23 fall, fler och svårare) |
| Generatorns 16 scenarier | – | Signalspanaren ~8 %, ggmorse ~30 % |
| Riktig 40 m-QSO (facit känt) | skräp ("A I SE S&*MA …") | 97,6 % rätt ("NB ES Y ES ALWAYS GUT") |
| FAV22 (riktig, utan facit) | – | ~50 % överensstämmelse med ggmorse och fldigi |

### Vad som byggdes

**1. Ny Morse-avkodare (`analys.py`, `CwAvkodare`)**, byggd i tre steg:
- *Signalbehandling:* bärvågen spåras och blandas ner till 0 Hz. När analysen har mätt
  signalens mitt söks bärvågen bara inom ±120 Hz från den, så att en granne inte tar
  över. Det smala filtret är två glidande medel i serie, med längden en halv enhet.
  Ton- och brusnivån följs element för element.
- *Tröskel:* tröskeln ligger mitt emellan brus och ton i log-skala (så att en ton som
  fädat 10 dB ändå syns), med ett linjärt golv och hysteres. En brusspärr gör att rent
  brus inte blir text.
- *Särdrag:* nyckelläget blir en följd av ton- och pauslängder. Flankkorrigering räknar
  bort hur mycket filtret drar ut tonerna. Korta avbrott i en ton och störtoppar i en
  paus slås ihop med grannarna.
- *Klassning:* tonlängderna klustras i prick och streck, pauserna i inom tecken, mellan
  tecken och mellan ord. Det görs med k-means i log-skala, så gränserna lärs ur
  signalen (klarar Farnsworth, tunga streck och handnyckling). Takten räknas som
  (prick + kort paus) / 2. Det värdet påverkas inte av att tröskeln förlänger tonerna.
- *Träningsgrupper:* avkodaren "Morse – träningsgrupper (5 tecken)" (`CwGruppAvkodare`)
  lagar grupper med fel antal tecken: T + M blir O igen, och två delar av samma grupp
  slås ihop.

**2. Jämförelse med kända avkodare (`cwjamfor.py`)**
- fldigi 4.2.03 körs utan skärm (Xvfb, PulseAudio med en null sink, XML-RPC).
- ggmorse byggs från källkod (`ggmorse-from-file`).
- `python3 cwjamfor.py klipp.wav [--facit "…"] [--grupp 5]` och `--syntetiskt`.

**3. CW-generator (`cwgenerator.py`)**
- Stationer: takt, nyckeltyp (maskin, bug, rak), vikt, Farnsworth, takt-drift,
  chirp, klick, frekvensdrift, fladder (Rice-kanal) och QSB.
- Band: brus, åskknaster (QRN), grannar och bärvågar.
- Sändningar i turer (växel-QSO, nät, pileup) och överlappande stationer.
- Texttyper: QSO, CQ, contest, klartext, träningsgrupper, siffergrupper, fyr och bulletin.
- 16 scenarier: ren, fav22, qso, rak, contest, svag, qrn, trangt, farnsworth, bug,
  vaxel, nat, overlapp, nollstampel, pileup och blandat. Dessutom slumpade
  övningsklipp (`--dataset N`). Utdata är WAV (ljud eller IQ) plus `.txt` med facit.
- Programmets simulator har ett CW-band på 99,45 MHz med FAV22-lik station,
  växel-QSO, överlappande par, fyr, rak nyckel, Farnsworth, contest och svag station.

**4. Träning och mätning**
- `analystest.py morse`: 23 syntetiska fall och riktiga klipp i `testdata/cw/`
  (WAV + `.txt` med facit).
- `cwtrana.py`: rutnätssökning över avkodarens inställningar mot alla tre sorters prov.
  Riktiga klipp väger tre gånger så mycket.
- `testdata/cw/qso_40m_7025kHz.wav`: 9 s handsänd QSO från KiwiSDR (7,0254 MHz).

### Viktiga fynd under träningen
1. Fasta gränser (streck > 2 prickar osv.) fungerade bara på perfekt maskinsänd CW.
   Med klustring blev Farnsworth 34 % → 0 % och SNR 3 dB 70 % → 0 %.
2. Tonnivån hängde kvar efter fädningstoppar i 2 s, så svagare prickar missades.
   Lösning: tröskel i log-skala och snabbare glömska (0,7 s).
3. Låga trösklar förlänger tonerna, vilket via filterlängden blev en återkopplingsslinga
   där avkodaren skenade. Lösning: flankkorrigering och en takt som inte påverkas.
4. I ett tätt band tog en starkare granne över bärvågsspårningen (80 % fel → 0 %).
5. Ett glidande medel släppte in starka grannar via sidloberna. Två i serie gav
   tätt band 69 % → 20 %.
6. Avvägningen mellan djup fädning (vill ha låga trösklar) och störningar i pauserna
   (vill ha höga) finns kvar. Inställningarna väljs med `cwtrana.py`.

### Jämförelse (andel teckenfel)

| Prov | Signalspanaren | ggmorse | fldigi |
|---|---|---|---|
| Syntetiska testfall, medel | 0,7 %* | 20,8 % | 18,4 % |
| SNR 3 dB | 0 % | 65 % | 86 % |
| Farnsworth | 0 % | 34 % | 34 % |
| Generatorns första 10 scenarier | 10,8 % | 35,5 % | – |
| Överlapp, växel, nät, pileup, blandat | 6,2 % | 23,6 % | – |
| Riktig 40 m-QSO | 2,4 % | 0 % | 29 % |

\* Signalspanaren-värdet är från den första versionen på de ursprungliga 20 fallen.

### Kända begränsningar
- Snabbare än cirka 55 ord/min låser sig på fel takt: filtret före inlärningen är
  längre än en prick.
- Svag signal med djup QSB, mycket ojämn handnyckling (±25 %) och snabb fädning ger
  fortfarande 15–40 % fel.
- FAV22 har inget facit ännu. Klippet var en skärminspelning på 30 s med AAC-ljud.
- Pausen i "Y ES" (243 ms) i 40 m-klippet är tvetydig mellan tecken- och ordmellanrum.

### Miljön
- Molnsessionen når inte webb-SDR:er: proxyn släpper bara igenom port 443, och
  KiwiSDR använder 8073. Inspelningar måste komma från användaren.
- fldigi och ggmorse försvinner när molnmaskinen rensas. Ett uppstartsskript för en
  egen miljö föreslogs men nekades. Skriptet (apt fldigi/xvfb/pulseaudio,
  ggmorse via g++) finns i loggen nedan om det behövs.

```bash
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fldigi xvfb pulseaudio pulseaudio-utils xdotool ffmpeg
pip install -q scipy numpy
git clone -q --depth 1 https://github.com/ggerganov/ggmorse /opt/ggmorse-src
cd /opt/ggmorse-src && g++ -O2 -std=c++17 -Iinclude -Isrc -Iexamples src/*.cpp \
  examples/ggmorse-common.cpp examples/ggmorse-from-file/main.cpp \
  -o /usr/local/bin/ggmorse-from-file -lpthread
```

### Commits på `claude/determined-planck-jtw8lg`

| Commit | Tid | Innehåll |
|---|---|---|
| caecf81 | 19:32 | Ny avkodare med särdrag och adaptiv klassning |
| e19c0e6 | 19:44 | cwjamfor.py: jämförelse med fldigi |
| 8532206 | 20:12 | Tål snabb fädning: log-tröskel, flankkorrigering, takt utan bias |
| 855d329 | 20:20 | Bärvågen söks nära signalen som analysen mätt |
| 0c159ae | – | (erihil) uppladdad inspelning IMG_0934.mov |
| 2ff8786 | 20:26 | Riktig 40 m-inspelning som test; sista tecknet skrivs ut |
| 905c941 | 20:33 | Läge för träningsgrupper om fem tecken |
| 5a71656 | 20:41 | CW-generator, CW-band i simulatorn, cwtrana.py, bättre filter |
| 1314112 | 20:42 | cwjamfor.py jämför även med ggmorse |
| 09485b6 | 20:48 | Överlappande signaler och sändningar i turer; omtränad |

### Nästa steg
- Längre FAV22-inspelningar (2–4 min, 3881 och 6825 kHz) med delvis facit i `testdata/cw/`.
  Spela in med Signalspanarens "Spela in" (WAV) hellre än skärminspelning.
- Träna om med `cwtrana.py` när riktiga klipp finns.
- Bestäm om `IMG_0934.mov` (23 MB) ska tas bort ur repot. Ljudet finns i `testdata/cw/`.
- Prova CW-bandet i simulatorn i det grafiska programmet. Det är testat utanför GUI:t.
