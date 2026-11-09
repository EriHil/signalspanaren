# Andra OS: Signalspanaren jämförd med DragonOS och SigintOS

*Analys gjord 2026-11-09 mot Signalspanaren 2.12.1 (commit 0795809). Dokumentet ändrar ingen kod.*

## Kort svar

- **De är inte samma sorts sak.** DragonOS och SigintOS är hela Linuxdistributioner som samlar andras program. Signalspanaren är ett enda program med egen kod. Därför går "deras kod" inte att jämföra rad för rad. Det mesta i DragonOS är skrivet av andra projekt (GNU Radio, SDR++, SDRangel, rtl_433 med flera).
- **Stabilitet:** De enskilda verktygen i DragonOS är mer beprövade än våra egna avkodare, eftersom tusentals använder dem på riktiga signaler. Helheten i DragonOS hänger däremot löst ihop: varje verktyg har sitt eget gränssnitt och sina egna fel. SigintOS har sämre rykte. Användare rapporterar att GUI:t inte fungerar och att LTE-delarna är låsta bakom kontakt med utvecklaren.
- **Analysverktyg:** Signalspanaren är **bättre på automatisk igenkänning** av okända signaler. Varken DragonOS eller SigintOS har en inbyggd motsvarighet till vår modulationsigenkänning med bevismotor, signaldatabas och facit-mätning. DragonOS har däremot **fler och bättre manuella verktyg** (inspectrum, Universal Radio Hacker) och **många fler avkodare**.
- **Det vi saknar mest:** stöd för fler SDR:er och bredare band, fler avkodare (VDL2, HFDL, rtl_433, satelliter, FT8) och trunking/digitalt tal. Se listan längst ner.

## 1. Vad de tre är

| | Signalspanaren | DragonOS | SigintOS |
|---|---|---|---|
| Typ | Ett GTK4-program (Python) | Färdig Linuxdistribution (Lubuntu, x86_64 och Raspberry Pi) | Färdig Linuxdistribution (Ubuntu) |
| Upphovsman | erihil | Aaron "cemaxecuter" | Ett litet team, delvis stängt |
| Senaste version | 2.12.1 (2026-11-08) | Noble R7 (aug 2025), R8 omnämnd | 2.0 Community Edition |
| Egen kod | ~21 600 rader Python, egna avkodare | Mest paketering och skript | Ett eget GUI ovanpå andras verktyg |
| Hårdvara | RTL-SDR, rtl_tcp, KiwiSDR, IQ-filer, simulator | RTL-SDR, HackRF, Airspy, SDRplay, LimeSDR, bladeRF, USRP, KrakenSDR m.fl. | RTL-SDR, HackRF, bladeRF, USRP (även sändning) |
| Språk | Svenska | Engelska | Engelska |
| Inriktning | Lyssna, logga, känna igen och avkoda | Allt inom SDR: mottagning, analys, sändning, trådlöst | "SIGINT": GSM/LTE-sökning, IMSI-fångare, GPS-/FM-sändning, störning |

## 2. Kod och stabilitet

### Signalspanaren

Det här har jag mätt själv:

- `signalspanaren.py --sjalvtest`: **OK**. ACARS, AIS, APRS, ADS-B, NAVTEX, POCSAG, ALE, DMR, PSK31, IQ-filer, skanner och KiwiSDR klarar testerna.
- `analystest.py mat --bara-syntetiskt`: **12 av 12 rätt** signalfamilj, 11 av 11 bandbredd, rätt visat 100 % av stegen.
- Starka sidor:
  - Analysen är skild från GTK (`analys.py`, `bevis.py`), så den går att testa utan fönster.
  - Det finns ett mätverktyg med facit (`analystest.py`, `facit.json`). Det har inte DragonOS eller SigintOS.
  - Bara meddelanden med rätt CRC visas.
  - API-nycklar sparas med rättigheter 600 och skrivs atomiskt (`ai.py`).
  - Inga `shell=True` och inga nakna `except:`.
- Svaga sidor jämfört med beprövade verktyg:
  - **ACARS och APRS är bara testade med syntetiska signaler.** Ingen riktig trafik har tagits emot (README). dumpvdl2/acarsdec och direwolf i DragonOS är testade av tusentals på riktiga signaler.
  - **`signalspanaren.py` är 9 000 rader i en fil.** Det gör fel svårare att hitta och ändringar riskablare.
  - **80 breda `except Exception`.** De gör programmet tåligt men kan dölja fel. Kontrollera att alla loggas.
  - **Ingen CI** (`.github/` saknas). Självtestet körs bara när någon kommer ihåg det.
  - Inga enhetstester med pytest. Självtestet är bra men ett enda stort test.
  - Webbgränssnittet saknar inloggning (dokumenterat).
  - Projektet är ungt (version 1.x–2.12 på några veckor). Mognaden kommer med tid och riktiga signaler.

### DragonOS

- Verktygen är mogna, men **integrationen är lös**. Verktygen startas var för sig från menyn och delar inte data. Det finns ingen gemensam signallogg eller analys.
- Versionskonflikter mellan GNU Radio-moduler förekommer ofta (gr-osmosdr, gr-gsm, gr-satellites). Många ärenden på SourceForge handlar om att något verktyg slutat starta efter en uppdatering.
- Hela operativsystemet är en ISO på cirka 4 GB. Det är bra för en dedikerad dator eller ett USB-minne, men det går inte att installera i ett befintligt system på samma sätt som `./installera.sh`.
- **Slutsats:** Verktygen är stabilare var för sig. Signalspanaren hänger bättre ihop.

### SigintOS

- Rapporter från användare säger att GSM-sökningen i GUI:t inte fungerar, medan `grgsm_scanner` i terminalen gör det. LTE-sökning och IMSI-fångare är låsta: man måste kontakta skaparna, troligen mot betalning.
- Koden är delvis stängd och kan inte granskas. Kommentarer på rtl-sdr.com ifrågasätter om distributionen är seriös.
- Funktioner som IMSI-fångare, GPS-sändning och störsändning är **olagliga i Sverige utan tillstånd** (lagen om elektronisk kommunikation, PTS). De ska Signalspanaren inte ha.
- **Slutsats:** SigintOS är inte stabilare och inget att efterlikna. Det enda nyttiga är idén med passiv GSM-cellsökning (se nedan).

## 3. Analysverktyg

| Förmåga | Signalspanaren | DragonOS | SigintOS |
|---|---|---|---|
| Automatisk modulationsigenkänning (FSK/PSK/QAM/OFDM …) | **Ja** (`analys.py` + bevismotorn `bevis.py`) | Nej (gr-inspector finns, men är experimentellt) | Nej |
| Signaldatabas med militära vågformer | **Ja** (`signaldb.py`, egna tillägg) | Artemis (sigidwiki, manuell jämförelse) | Nej |
| Mätning mot facit | **Ja** (`analystest.py`) | Nej | Nej |
| Konstellation, tonspektrum, envelop | Ja | Ja, i GNU Radio/SDRangel (manuellt) | Nej |
| Inspelad IQ: mäta symboltakt och klippa ut skurar för hand | Begränsat | **inspectrum** | Nej |
| Bakåtkompilera okända protokoll (bitar → fält) | Nej | **Universal Radio Hacker** | Nej |
| Bygga egen signalkedja | Nej | **GNU Radio Companion** | GNU Radio |
| Signallogg med filter | **Ja** (Wireshark-stil) | Nej (Wireshark finns för nätverk) | Wireshark |
| AI-hjälp i analysen | **Ja** (`ai.py`) | Nej | Nej |
| Värmekarta över bandaktivitet | **Ja** | Delvis (rtl_power + heatmap-skript) | Nej |
| Pejling (riktning) | Nej | **KrakenSDR** (DoA) | Nej |

**Slutsats:** När det gäller att *förstå en okänd signal automatiskt* är Signalspanaren före. När det gäller att *gräva manuellt* i en signal, och att *avkoda brett*, ligger DragonOS före.

## 4. Avkodare

| Signal | Signalspanaren | DragonOS (verktyg) |
|---|---|---|
| ACARS (VHF) | Ja (bara syntetiskt test) | acarsdec |
| **VDL Mode 2** | Bara igenkänning | **dumpvdl2** |
| **HFDL** | Bara flaggor och igenkänning | **dumphfdl** |
| AIS | Ja (verifierad) | AIS-catcher, rtl_ais |
| ADS-B | Ja (verifierad) | dump1090/readsb, tar1090 |
| APRS | Ja (bara syntetiskt test) | direwolf |
| POCSAG | Ja | multimon-ng (även FLEX) |
| **FLEX-personsökare** | Nej | multimon-ng |
| NAVTEX/SITOR-B, RTTY, CW | Ja | fldigi |
| ALE 2G | Ja | (PC-ALE, Linuxstödet är svagt) |
| Väderfax | Ja | fldigi |
| PSK31 | Ja | fldigi |
| **FT8/FT4/WSPR** | Bara igenkänning | **WSJT-X, JTDX** |
| DMR | Bara identifiering (färgkod, ID, krypterat eller inte) | **DSD-FME** (även tal), SDRTrunk |
| **P25, NXDN, dPMR, D-STAR, YSF** | Nej | DSD-FME, SDRTrunk |
| **Trunking (följa samtal)** | Nej | **SDRTrunk, trunk-recorder** |
| **ISM 433/868 MHz (väderstationer, sensorer)** | Nej | **rtl_433** |
| **Vädersatelliter (Meteor-M LRPT m.fl.)** | Bara igenkänning | **SatDump** |
| **Inmarsat (STD-C, AERO)** | Nej | JAERO, SatDump |
| **Iridium** | Nej | gr-iridium |
| **DAB/DAB+** | Nej | welle.io |
| **DRM (HF-rundradio)** | Nej | Dream, SDR++-modul |
| **GSM-cellsökning (passiv)** | Nej | gr-gsm, kalibrate-rtl |
| Wi-Fi/Bluetooth | Nej (utanför SDR-fokus) | Kismet |
| Drönare (DJI DroneID, Remote ID) | Nej | WarDragon-verktyg |

## 5. Det vi saknar, i prioritetsordning

Förslagen följer projektets inriktning: passiv mottagning, svenska, ett sammanhållet program.

1. **Fler mottagare via SoapySDR.** Airspy, SDRplay, HackRF och LimeSDR ger 2–20 MHz bandbredd i stället för cirka 1,2 MHz. Det är den största skillnaden mot DragonOS och gör att flera avkodare kan köras samtidigt.
2. **Testa ACARS och APRS mot riktig trafik.** Spela in IQ med riktiga paket, eller använd publika SigMF-exempel, och lägg klippen i `analystest.py`. Då blir avkodarna lika beprövade som AIS och ADS-B.
3. **CI på GitHub.** Kör `--sjalvtest` och `analystest.py mat --bara-syntetiskt` vid varje push. Det är billigt och fångar fel tidigt.
4. **VDL2 och HFDL.** Programmet känner redan igen dem. VDL2 är den vanligaste flygdatan i Sverige idag (136,975 MHz), oftare än ACARS.
5. **rtl_433-liknande ISM-avkodning.** Vanliga signaler och en enkel väg att få många träffar på 433/868 MHz.
6. **Manuellt analysläge à la inspectrum.** Markörer i det lilla vattenfallet för att mäta symboltakt och skurlängd på en inspelad IQ-fil, och export av rå bitström. Det kompletterar den automatiska analysen när den är osäker.
7. **FT8/WSPR-avkodning.** Igenkänningen finns redan. Avkodningen kräver LDPC och tidssynkronisering men är väldokumenterad.
8. **Valfria externa avkodare.** Om dumpvdl2, dumphfdl, rtl_433, multimon-ng eller SatDump är installerade kan Signalspanaren skicka ljud eller IQ till dem och visa resultatet i fliken Avkodat. Så får vi DragonOS bredd utan att skriva allt själva. Med det skulle `analysavkodare.py` kunna få en "extern"-typ.
9. **Trunking och digitalt tal (DMR/P25).** Det är stort och har patentfrågor kring AMBE-röstkodaren. Rimligast är att lösa det via DSD-FME som valfri extern avkodare (punkt 8).
10. **Dela upp `signalspanaren.py`** i moduler (GUI, källor, skanner, logg). Det ger ingen ny funktion men gör koden stabilare att ändra.
11. **Passiv GSM-cellsökning (som kalibrate/gr-gsm scanner).** Det är lagligt och användbart för att kalibrera dongelns frekvensfel (ppm). Avkoda inte trafik och bygg ingen IMSI-fångare.

**Inte att ta efter:** sändning, störsändning, GPS-förfalskning och IMSI-fångare från SigintOS. Det är olagligt utan tillstånd och strider mot projektets linje att krypterad och skyddad trafik inte ska läsas.

## 6. Plan: åtgärda svagheterna i analysen

Planen gäller främst testerna, inte programmet. Steg 1–4 ändrar inget i programmet som användarna kör.

### Svaghet 1: analysen och avkodarna testas mest mot sina egna signaler

Självtestet och de syntetiska testerna i `analystest.py` använder signaler som vi själva har skapat (`make_rtty`, `make_acars_burst` med flera). De bygger på samma antaganden som avkodarna. Ett fel i hur vi har tolkat standarden syns därför inte i testet. Det är troligen därför ACARS och APRS aldrig har avkodat riktig trafik.

**Åtgärd: lägg till riktiga inspelningar som facit.**

1. **Spela in egna klipp.** Använd `analystest.py spela-in` och IQ-inspelningen i programmet. De viktigaste att få in:
   - ACARS på 131,725 MHz, där det är trafik dagtid nära Arlanda och Bromma.
   - APRS på 144,800 MHz.
   - POCSAG.
   - DMR.
2. **Använd publika inspelningar.** Det finns färdiga IQ-filer med känd signaltyp, bland annat:
   - sigidwiki, med ljud och ibland IQ per signal.
   - GNU Radios och SigMF-projektets exempelfiler.
   - Testfiler som följer med acarsdec, direwolf och multimon-ng.

   Om filerna avkodas lika bra av oss som av deras verktyg vet vi att våra avkodare är rätt.
3. **Gör klippen reproducerbara.** Idag ligger facit-klippen bara lokalt i `~/.local/share/signalspanaren/facit-klipp/`. Lägg en liten uppsättning korta klipp i repot, till exempel `test/klipp/` med några sekunder per klipp i cu8 eller cf32. Då kan vem som helst köra samma mätning.

### Svaghet 2: facit är smalt och analysen tål dåligt med brus

Ändringsloggen visar att analysen har rätt typ i 98 % av stegen på facit-klippen. Med 6 dB extra brus sjunker det till 73 %. Facit innehåller 27 signaler:

- Många är OOK, AM och PSK.
- FM och 4-FSK har bara ett klipp var.
- MSK och OFDM har inga klipp i `facit.json`.
- Nästan allt ligger på HF. Det finns inget från VHF eller UHF, där de flesta med RTL-SDR lyssnar.

**Åtgärd:**

1. **Bredda facit.** Sikta på minst 3–5 klipp per signalfamilj, från olika stationer och förhållanden. Lägg till VHF/UHF-signaler: NFM-tal, ACARS, AIS, APRS, POCSAG, DMR och PMR446.
2. **Mät tålighet mot brus i varje körning.** Låt `analystest.py mat` köra varje klipp även med +3, +6 och +10 dB brus och med ett frekvensfel på några hundra Hz. Skriv ut en kurva över andelen rätt mot SNR. Då syns direkt om en ändring gör analysen skörare, vilket är det som hänt i fallen med MSK och nycklad bärvåg.
3. **Lägg till "fällor" i facit.** Det är signaler som analysen tidigare har tagit fel på: VOLMET-tal som blev STANAG, RTTY som blev MSK, fädande AM som blev OOK. Varje rättat fel bör få ett klipp, så att felet inte kommer tillbaka.

### Svaghet 3: inget skydd mot att det blir sämre

Idag kör någon testet för hand.

**Åtgärd: GitHub Actions** som kör `--sjalvtest` och `analystest.py mat` på klippen i repot vid varje push. Bygget ska stoppas om andelen rätt sjunker under en gräns, till exempel 95 % för rena klipp och 70 % med 6 dB brus. `--spara` och `--jamfor` finns redan, så det som behövs är en arbetsfil och en sparad referens.

### Svaghet 4: när analysen är osäker finns inget verktyg att mäta själv med

Analysen säger "Osäker – klassa själv", men användaren har inget sätt att mäta. Det är här inspectrum och Universal Radio Hacker i DragonOS är bättre.

**Åtgärd, i liten skala:**

- Markörer i signalanalysens lilla vattenfall som mäter tid och frekvens. Avståndet mellan markörerna ger symboltakt och shift.
- Export av den råa bitströmmen till en fil, så att man kan granska den eller köra den i URH.

### Ordning

| Steg | Arbete | Effekt | Klart |
|---|---|---|---|
| 1. Riktiga klipp för ACARS, APRS, POCSAG och DMR | Lite kod, mest inspelning | Störst. Vi får veta om avkodarna fungerar på riktiga signaler. | [ ] |
| 2. Brus- och frekvensfelstest i `analystest.py` | Liten kod, bara i testverktyget | Hög | [ ] |
| 3. Klipp i repot och CI | Liten | Hög, varaktig | [ ] |
| 4. Bredare facit (VHF, FM, 4-FSK, MSK, OFDM) | Mest inspelning | Hög | [ ] |
| 5. Markörer och bitexport | Medelstor GUI-kod | Medel | [ ] |

## 7. Där Signalspanaren redan är bäst

- Ett program där allt hänger ihop: vattenfall, skanner, logg, analys, avkodning och kartor delar samma data.
- Automatisk klassning med bevismotor, bandplan som förkunskap och signaldatabas. Det har ingen av de andra.
- Mätbar kvalitet med facit (`analystest.py`).
- KiwiSDR och cirka 1 500 webbmottagare som källa. DragonOS har bara webbläsaren och KiwiSDR-klienten.
- Svenska, en installation i det system man redan har, och Flatpak.

## Källor

- DragonOS på SourceForge: <https://sourceforge.net/projects/dragonos-focal>, <https://sourceforge.net/projects/dragonos-focal/files>, <https://sourceforge.net/p/dragonos-pi64/>
- DragonOS-artiklar: <https://www.rtl-sdr.com/tag/dragonos/>, <https://www.linuxlinks.com/dragonos-software-defined-radio-distribution/>, <https://hackaday.com/2020/04/11/software-defined-radio-made-easy>
- SigintOS: <https://www.rtl-sdr.com/sigintos-a-linux-distro-for-signal-intelligence/>, <https://www.rtl-sdr.com/tag/sigintos/>, <https://www.infosecinstitute.com/resources/penetration-testing/sigintos-signal-intelligence-via-a-single-graphical-interface/>, <https://medium.com/@tomac/sigintos-a-wireless-pentest-distro-review-a7ea93ee8f8b>
- SIGpi (närliggande projekt): <https://www.rtl-sdr.com/?p=57105>

Verktygslistorna för DragonOS bygger delvis på äldre artiklar och kan skilja sig från den senaste ISO:n (Noble R7/R8). SigintOS 2.0 saknar en publik funktionslista.
