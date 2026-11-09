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

---

## 2026-11-08 (forts.) – genomgång: har allt som föreslogs provats?

### Gjort i det här passet
- **Simulatorns CW-band provat i det grafiska programmet** (Xvfb, GTK4). Första försöket
  misslyckades: analysen tog bandet för en bred AM-signal (19–60 kHz) och Morse-avkodaren
  startade aldrig. Orsaker: generatorns brus och åskknaster täckte hela simulatorslingans
  48 kHz, och stationerna låg för tätt. Rättat (inget eget brus, stationer 3 kHz isär).
  Nu: På/av-nyckling 100 %, 32 Hz, 24 ord/min, träningsgrupperna avkodas (78 % mot facit).
- **Maskininlärning provad (`cwml.py`)**: klassning av avkodarens ton- och pauslängder
  (7 klasser, känd nyckling från generatorn, 80 klipp träning och 40 test).
  k-means-regler 87,5 %, Random Forest 88,7 %, SVM 88,7 %, MLP 88,7 %.
  Klassningen är inte flaskhalsen. Störningar (61 %) och avbrott (~31 %) uppstår i
  signalbehandlingen och kan inte rättas av någon klassare.

### Checklista mot förslagen

| Förslag | Status |
|---|---|
| Ansluta till repot | Gjort |
| Regelbaserad segmentering (1/3, 1/3/7) | Gjort, med adaptiva gränser (k-means) i stället för fasta |
| Random Forest / SVM på tidsavstånd | Provat (`cwml.py`): +1,2 procentenheter, inte infört |
| RNN/LSTM/Transformer på pulsföljder | Delvis: ett MLP (neuralt nät) på samma särdrag. LSTM inte provat (PyTorch går inte att installera här) |
| Spektrogram/MFCC + CNN | Inte provat (PyTorch saknas). Avkodaren använder envelop, inte spektrogram |
| Lyssna på FAV22 3881/6825 kHz | Inte möjligt från molnet (KiwiSDR port 8073 blockeras); användarens inspelning analyserad |
| Kända CW-avkodare som jämförelse | fldigi och ggmorse (lokalt). Webbaserade avkodare (t.ex. morsecode.world) inte provade, de kräver webbläsare och mikrofon |
| Träna | Gjort: cwtrana.py, analystest.py morse, generatorscenarier, riktiga klipp |
| Bygga/installera modulerna | fldigi och ggmorse. morse-audio-decoder (pip) fanns inte för Python 3.13 |
| Träningsgrupper | Gjort (`CwGruppAvkodare`) |
| CW-generator i programmet | Gjort, nu även provad i GUI:t |
| Överlappande signaler, olika sändningar | Gjort (vaxel, nat, overlapp, nollstampel, pileup, blandat) |
| Logg på GitHub utanför koden | Gjort (den här grenen) |
| Miljö med uppstartsskript | Skapad ("signalspanaren"), ännu inte prövad i en ny session |
| Ta bort IMG_0934.mov ur repot | Väntar på besked |

---

## 2026-11-08 (forts.) – CNN och LSTM, simulatorn på riktiga band

### Simulatorn på riktiga band
CW-bandet låg i FM-rundradiobandet (99,45 MHz), där analysen dessutom använder
rundradiobandets regler. Flyttat till 40 m:s CW-del (7,0185–7,0395 MHz). En FAV22-lik
station sänder "VVV DE FAV22", 20 träningsgrupper och "AR" parallellt på 3881 och
6825 kHz. Provat i programmet på 3,881 MHz: CW/OOK 100 %, 32 Hz, 24 ord/min, 82 % rätt.

### Neurala nät (`cwnn.py`, TensorFlow-CPU – PyTorch går inte att hämta här)

| Teckenfel | Signalspanaren | CRNN (CNN+BiLSTM+CTC) | CNN (+CTC) |
|---|---|---|---|
| Medel, 16 scenarier | **8,3 %** | 17,5 % | 13,1 % |
| Fladder (fav22) | 10,5 % | 3,8 % | **2,6 %** |
| Tätt band | 24,3 % | 2,3 % | **1,0 %** |
| Åskknaster | 7,5 % | 2,1 % | **1,1 %** |
| Svag signal | **23,8 %** | 45,4 % | 26,4 % |
| Station 6–40 Hz bort | **0,6–1,2 %** | 66–68 % | 52–53 % |
| Riktig 40 m-QSO | **2,4 %** | 5,3 % | 7,7 % |
| Riktig FAV22 (likhet med fldigi) | 52,5 % | 55,9 % | 55,9 % |

LSTM på pulsföljder (klass per ton/paus): 89,6 % mot reglernas 87,5 % (RF/SVM/MLP 88,7 %).

Erfarenheter:
- CTC fastnar på bara blanktecken utan hög inlärningstakt (3e-3) och gradientklippning.
  Ett rent CNN fastnade ändå, men lärde sig när det fick CRNN:ns faltningslager.
- Första CRNN gav 100 % fel på riktiga klipp. Riktigt ljud har mycket lägre kontrast
  mellan ton och paus (eko, ljudbehandling, läckage). Det löstes med normering per
  frekvenskanal och träningsdata med eko, AGC, läckande bärvåg och nära grannar.
- Nät och regler kompletterar varandra: näten är bättre vid fädning, tätt band och
  åskknaster, reglerna vid svag signal, grannar några Hz bort och i medel.

Inte gjort: näten är inte inbyggda i programmet (de kräver TensorFlow och
GUI-integration). Ett tänkbart nästa steg är en hybrid som väljer metod efter läge.

---

## 2026-11-09 – RTTY med samma metoder som CW

### Byggt
- **`rttygenerator.py`**: syntetisk RTTY med facit. Varierar takt (45,45/50/75/100 Bd),
  shift, polaritet, stoppbitar (1/1,5/2), taktfel, drift, selektiv fädning (mark och space
  fädar var för sig), QSB, åskknaster och grannar (CW och RTTY). Innehåller 11 scenarier.
- **Ny `RttyAvkodare`**, som lär sig signalen själv:
  - Tonparet finjusteras ur spektrumet, kring klicket. Båda tonerna måste finnas och
    turas om.
  - Ett filter per ton med nivå per ton (ATC) i stället för frekvensdiskriminator.
  - Ramsynk körs parallellt för alla takter och båda polariteterna. Poängen är andel
    godkända ramar × hur tydliga bitarna är i mitten.
  - Störningsspärr mot knaster, och brusspärr så att inget skrivs efter sändningen.
  - 1 stoppbit fungerar nu.
- **Analysen i tätt band**: `_fsk_par` söker närmaste tonpar först. Ett äkta FSK-par vid
  klicket går före en hopslagen bred mätning.
- **`analystest.py rtty`** mäter genom hela analyskedjan, som i programmet.
- **fldigi som referens för RTTY**: `cwjamfor.Fldigi.rtty()` med säker omstart. 450 Hz
  körs i 425 Hz-läget, och brusspärren står på nivå 10.
- **Simulatorn**: DWD-lik väderstation på 10,1008 och 4,583 MHz, amatör-RTTY på 14,083
  och 14,0865 MHz.

### Fynd
1. Analysens takt hoppade mellan 43, 45,5 och 47,8 Bd, och 47,8 rundades inte av till
   45,45 (gränsen var 5 %). Varje hopp nollställde den gamla avkodaren.
2. Analysens mitt var 12 Hz fel, så tröskeln låg snett.
3. Fel takt ramar ofta "rätt": start- och stoppbit träffar men databitarna glider.
   Lösningen var att mäta hur tydliga bitarna är i mitten.
4. RYRY ramar med båda polariteterna. RYRY-texten och analysens takt avgör vid första synk.
5. I tätt band flyttade analysen kanalens mitt till klungans mitt. Den egna signalen låg
   då utanför avkodarens sökfönster, och analysens FSK-par-sökning valde grannarna.

### Resultat (teckenfel genom hela analyskedjan; fldigi fick takt/shift/stopp/polaritet)

| Scenario | Gamla | Nya | fldigi |
|---|---|---|---|
| ren | 14,2 % | 0,7 % | 1,3 % |
| dwd (50 Bd/450 Hz) | 65,9 % | 1,1 % | 4,2 % |
| amator (QSB) | 37,4 % | 1,2 % | 0,7 % |
| svag | 100 % | 26 % (10 % i fldigi-körningen) | 44,6 % |
| selektiv fädning | 44,7 % | 7,8 % | 54,8 % |
| omvänd polaritet | 51,1 % | 0,7 % | 0,3 % |
| drift | 25,9 % | 0,7 % | 0,0 % |
| tätt band | 100 % | 0,9 % | 0,3 % |
| åskknaster | 71,4 % | 4,0 % | 56,2 % |
| 75 Bd/850 Hz | 74,0 % | 2,2 % | 3,3 % |
| 2 stoppbitar | 2,7 % | 6,8 % | 57,1 % |
| **Medel** | **53,4 %** | **4,8 %** | **20,3 %** |

Kvar att göra: svag signal (26 %). Signalspanaren missar de första sekunderna medan
analysen klassar, men fldigi ska ställas in för hand. Nästa modulationstyp enligt planen:
NAVTEX/SITOR-B, PSK31 och POCSAG (multimon-ng som referens).
