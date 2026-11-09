# Andra OS: Signalspanaren jämförd med DragonOS och SigintOS

*Analys gjord 2026-11-09 mot Signalspanaren 2.12.1 (commit 0795809). Dokumentet ändrar ingen kod.*

## Kort svar

- **De är inte samma sorts sak.** DragonOS och SigintOS är hela Linuxdistributioner som samlar andras program. Signalspanaren är ett enda program med egen kod. Därför går "deras kod" inte att jämföra rad för rad. Det mesta i DragonOS är skrivet av andra projekt (GNU Radio, SDR++, SDRangel, rtl_433 med flera).
- **Stabilitet:** De enskilda verktygen i DragonOS är mer beprövade än våra egna avkodare, eftersom tusentals använder dem på riktiga signaler. Helheten i DragonOS hänger däremot löst ihop: varje verktyg har sitt eget gränssnitt och sina egna fel. SigintOS har sämre rykte. Användare rapporterar att GUI:t inte fungerar och att LTE-delarna är låsta bakom kontakt med utvecklaren.
- **Analysverktyg:** Signalspanaren är **bättre på automatisk igenkänning** av okända signaler. Varken DragonOS eller SigintOS har en inbyggd motsvarighet till vår modulationsigenkänning med bevismotor, signaldatabas och facit-mätning. DragonOS har däremot **fler och bättre manuella verktyg** (inspectrum, Universal Radio Hacker) och **många fler avkodare**.
- **Det vi saknar mest:** stöd för fler SDR:er och bredare band, fler avkodare (VDL2, HFDL, rtl_433, satelliter, FT8) och trunking/digitalt tal. Se avsnitt 5.

**Innehåll:** 1–5 jämförelsen med DragonOS och SigintOS · 6 plan för analysens svagheter · 7 andra program för signalanalys och regler för att ta in kod · 8 idébank · 9 där Signalspanaren redan är bäst.

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

## 7. Andra program för signalanalys

Kontrollerat 2026-11-09. Licensen är läst ur LICENSE-filen i varje projekts källkod, och "senaste ändring" är datumet på senaste commit. Programmen är inte installerade eller provade.

### Öppen källkod

| Program | Licens | Senaste ändring | Vad det gör | Nytta för oss |
|---|---|---|---|---|
| **SigDigger** + **Suscan** (BatchDrake) | LGPL-3.0 | 2026-02-11 / 2026-08-06 | Analys i realtid på Linux. Man markerar en signal och får en "inspektör" för ASK, FSK, PSK och flerbärvåg, med skattning av symboltakt, konstellation och symbolutvinning. | Den närmaste konkurrenten till vår signalanalys. Förebild för steg 5 i planen (markörer och bitexport). |
| **IQEngine** | MIT | 2025-12-19 | Webbverktyg för att bläddra i och analysera SigMF-inspelningar, med plugins. | Källa till riktiga SigMF-klipp för steg 1 i planen. Varje inspelning kan ha egen licens, så kontrollera den innan något läggs i repot. |
| **SigMF** (specifikationen) | CC BY-SA 4.0 | 2026-01-12 | Formatet för IQ-filer med metadata som vi redan använder. | Exempelfilerna är testdata. CC BY-SA kräver att upphovet anges. |
| **TorchSig** | MIT | 2026-08-31 | Verktyg för maskininlärning som genererar signaler i 53 klasser (Sig53): FSK, MSK, PSK, QAM, OFDM, OOK med flera, vid valfri SNR. | Ett standardiserat facit med känd SNR för steg 2 i planen. MIT-licensen gör det enkelt att använda. |
| **RadioML 2016/2018** (DeepSig) | CC BY-NC-SA 4.0 (enligt DeepSigs äldre sida) | – | 2 miljoner exempel i 24 modulationstyper, SNR −20 till +30 dB. | **Bara för lokal testning.** NC (inte kommersiellt) går inte ihop med GPL, så datan får inte läggas i repot. |
| **Artemis** (AresValley) | GPL-3.0 | 2026-11-03 | Databas över signaler byggd på sigidwiki, cirka 500 typer, med ljud och vattenfallsbilder. Version 4 använder SQLite. | Jämför och utöka `signaldb.py`. Själva programmet är GPL-3.0, men licensen för sigidwikis innehåll är **inte bekräftad**. Ingen data ska föras in förrän det är kontrollerat. |
| **inspectrum** | GPL-3.0 | 2025-12-01 | Manuell analys av inspelade IQ-filer med markörer för symboltakt. | Förebild för steg 5 i planen. |
| **Universal Radio Hacker** | GPL-3.0 | 2025-12-19 | Bakåtkompilering av protokoll: från signal till bitar till fält. | Målet för bitexporten i steg 5: exportera till ett format som URH kan läsa. |
| **gr-inspector** | GPL-3.0 | 2025-03-03 | GNU Radio-moduler som hittar signaler och skattar parametrar automatiskt. | Utvecklas knappt längre. Bra att jämföra metoder mot, inget att bygga på. |
| **FISSURE** (AIS) | GPL-3.0 | 2026-11-02 | Ramverk för RF-analys: detektering, klassning, bakåtkompilering. | Idéer för hur detektering, klassning och avkodning kopplas ihop. Innehåller sändande attacker, och de ska vi inte ta efter. |
| **SDRangel**, **OpenWebRX+** | GPL-3.0 (inte kontrollerat här) | – | Mottagare med många inbyggda avkodare. | Jämför funktionslistor för avkodare. |

### Vad Suscan gör jämfört med oss

En snabb genomgång av Suscans källkod (`analyzer/estimators/`, `analyzer/spectsrcs/`) visar att den använder samma grundmetoder som `analys.py`:

- spektrum av y², y⁴ och y⁸ för PSK-ordning och bärvåg (`exp-2/4/8`; vår motsvarighet är `_power_line`)
- en cyklisk produkt med fördröjning, z·z*(t−1), för symboltakten (`cyclo`; vi använder `z[1:]·conj(z[:-1])` för momentanfrekvens)
- autokorrelation för symboltakt (`fac`)

Vi saknar alltså ingen grundmetod här. Det som är bättre i SigDigger är det **manuella** arbetssättet, alltså inspektören, och att den följer frekvensdrift (`inspector/impl/drift.c`). Att följa drift är värt att titta på för långa HF-sändningar.

### Windows-program och kommersiella referenser

| Program | Typ | Användning för oss |
|---|---|---|
| MultiPSK | Windows, gratis eller billig licens | Lista över vilka HF-lägen som finns och hur de låter och ser ut |
| SDR# med tilläggsprogram | Windows, gratis och stängd | Vilka funktioner användare efterfrågar |
| Sorcerer, Code300-32 (Hoka) | Windows, äldre och stängda | Jämförelsepunkt för klassning av HF-modem |
| PROCITEC go2MONITOR/go2DECODE | Kommersiell | Läs databladen: vilka vågformer och vilken säkerhet de anger. Sätter mål för `signaldb.py`. |
| WAVECOM W-CODE | Kommersiell | Samma som ovan |
| Rohde & Schwarz, Narda SignalShark, Keysight 89600 VSA | Kommersiella mätinstrument | Referens för hur noggrant symboltakt, EVM och konstellation kan mätas |

### Regler för att ta in kod och data

Signalspanaren är GPL-3.0-or-later.

- **GPL-3.0 och LGPL-3.0** (SigDigger, Suscan, Artemis, inspectrum, URH, gr-inspector, FISSURE): kod får tas in om upphovsrätten och licenshuvudet följer med.
- **MIT** (IQEngine, TorchSig): får tas in om licenstexten följer med.
- **CC BY-SA 4.0** (SigMF-exempel): data får tas in med upphovet angivet.
- **CC BY-NC-SA** (RadioML): bara lokalt, aldrig i repot.
- **Stängda och kommersiella program** (go2MONITOR, WAVECOM, Code300, SDR#, MultiPSK): inga dekompilerade eller bakåtkompilerade delar. Licensvillkoren förbjuder det oftast, och kod som härleds från dem skulle göra Signalspanarens GPL-licens ogiltig för de delarna. Använd i stället publika standarder (STANAG, MIL-STD, ITU-R), datablad och öppna implementationer.
- **Okänd licens** (sigidwiki-data): kontrollera först.

### Prioritet

1. **TorchSig**: generera ett facit med känd SNR och lägg in i `analystest.py`. Hör till steg 2 i planen.
2. **SigDigger/Suscan**: studera inspektören och driftföljningen innan steg 5 i planen.
3. **IQEngine och SigMF-exempel**: riktiga klipp för steg 1 i planen.
4. **Artemis**: jämför med `signaldb.py` när licensen för sigidwiki är klar.
5. **go2MONITOR och WAVECOM**: läs databladen för att sätta mål.

## 8. Idébank

Idéer som inte är beslutade eller planerade. De samlas här för att väljas ut senare. Kolumnen **Vald** bockas av när en idé flyttas till planen i avsnitt 6.

### Analys

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 1 | Följa frekvensdrift | Analysen följer en signal vars frekvens sakta glider, som SigDigger gör (`drift.c` i Suscan). Hjälper på långa HF-sändningar och billiga sändare. | [ ] |
| 2 | Kort fingeravtryck per signal | En kompakt sammanfattning av mätvärdena (symboltakt, shift, bandbredd och ramlängd). Med den kan man söka "har jag hört den här förut?" i loggen, även på en annan frekvens. | [ ] |
| 3 | Tidsschema per station | Loggen lär sig när en frekvens brukar vara aktiv, till exempel DWD-fax varje hel timme. Analysen använder det som förkunskap, på samma sätt som bandplanen i `bevis.py`. | [ ] |
| 4 | Jämföra två signaler sida vid sida | Båda konstellationerna och spektrumen bredvid varandra. | [ ] |
| 5 | Förklaring av beslutet i klartext | Raden Grund utökas till en lista: "+ bandbredd 2,7 kHz passar STANAG, − ramlängd stämmer inte". | [ ] |

### Mottagning

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 6 | Flera KiwiSDR samtidigt | Samma frekvens från två mottagare jämförs. Det skiljer en störning vid dongeln från en riktig signal och ger en grov riktning till sändaren med TDoA, som KiwiSDR själv stöder. | [ ] |
| 7 | Övervakning utan att någon tittar | Bevaka en lista med frekvenser och spara IQ-klipp med analysresultat när något nytt dyker upp. Ger också facit-klipp till steg 1 i planen. | [ ] |
| 8 | Kalibrering av frekvensfel automatiskt | Använd en känd sändare (DCF77, en GSM-cell eller en rundradiostation) och rätta dongelns ppm. | [ ] |

### Data och delning

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 9 | Export till format som andra läser | SigMF med analysens resultat som annoteringar, så att filen kan öppnas i IQEngine eller inspectrum med markeringarna kvar. | [ ] |
| 10 | Import av Artemis-databasen | Valfritt och efter kontroll av sigidwikis licens. Användaren laddar ner den själv, så att vi inte sprider data. | [ ] |
| 11 | Egna signalpaket | En användare kan dela en JSON-fil med egna signaler och flaggor, till exempel "svenska kustradiostationer". | [ ] |

### Användbarhet

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 12 | Guidat läge för nybörjare | "Vad är det här?" ger en kort förklaring av signaltypen och en länk till sigidwiki. | [ ] |
| 13 | Ljudexempel i signaldatabasen | Man kan lyssna på hur en typ ska låta. | [ ] |
| 14 | Mobilvänligt webbgränssnitt med inloggning | Lyssna på stationen hemma från telefonen. | [ ] |

### Kvalitet

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 15 | Utvärdering av AI-svaren | Spara svaren från AI-chatten tillsammans med facit och mät hur ofta AI:n har rätt. Kopplat till "AI-chatten: kortare svar" i att göra-listan i `docs/ÄNDRINGSLOGG.md`. | [ ] |
| 16 | Rapport om fel klassningar | När användaren rättar analysen med "Klassa själv" sparas klippet som ett fall till `analystest.py`, lokalt och med användarens godkännande. | [ ] |

### Ljudtjänster på nätet

De här tjänsterna sänder **färdigt ljud**, inte IQ. Det blir därför ingen mottagare med vattenfall, frekvensinställning, analys eller avkodare, utan mer som internetradio. Hantera dem som websdr.org (`webbmottagare.py`, rad 15): ingenting byggs in utan tillstånd från tjänsten.

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 17 | LiveATC (flygradio) | MP3-strömmar från frivilliga mottagare vid flygplatser världen över. **Villkor (kontrollerat 2026-11-09):** licensen gäller bara personligt, icke-kommersiellt bruk. Den som vill länka direkt till en ström ombeds fråga LiveATC först, eftersom antalet anslutningar är begränsat och sidan lever på annonser och donationer. En forumtråd från 2025 citerar startsidan med att tredje parts användning är förbjuden (inte bekräftat på sidan). **Alternativ A, utan tillstånd:** en post i listan över webbmottagare som öppnar liveatc.net i webbläsaren, som vi gör med WebSDR. **Alternativ B, med skriftligt tillstånd från LiveATC:** en ljudkälla i programmet med lista över flygplatser och frekvenser. Kontakt: liveatc.net/ct/contact.php. | [ ] |
| 18 | Broadcastify (blåljus, flyg, järnväg) | Cirka 7 000 liveflöden, nästan alla från USA. Svensk blåljusradio går på Rakel och är krypterad, så den finns inte där. **Villkor (kontrollerat 2026-11-09):** det finns två API:er. **Live Audio Feed Catalog API** godkänns inte för projekt som bygger en "parallell ingång" till deras liveljud, och Broadcastify skriver uttryckligen "No police scanner apps". **Broadcastify Calls Client API** (inspelade samtal från trunkade system) är självbetjäning via bcfy.io/dev/apply: det kräver ett konto, debiteras per användning, gäller bara appar för slutanvändare och får inte säljas vidare. Det är oklart om Premium krävs. **Alternativ A, utan tillstånd:** en länk till broadcastify.com i listan över webbmottagare. **Alternativ B:** Calls Client API med användarens egen utvecklarnyckel, sparad som AI-nycklarna i `ai.py` (rättigheter 600). Värdet för svenska användare är litet. | [ ] |
| 19 | OpenMHz (trunkad blåljusradio) | Gratis för lyssnare, finansieras med donationer. Drivs av författaren till trunk-recorder. Frivilliga kör trunk-recorder med egna SDR:er och laddar upp samtal från P25-, SmartNet- och analoga system. Över 225 system, alla som hittats i USA. Samtalen sparas i 30 dagar. **Villkor:** inte kontrollerade. openmhz.com var inte nåbar från den här miljön (2026-11-09), och ingen sökträff visade användarvillkor eller ett dokumenterat API för lyssnare. Det enda dokumenterade är uppladdning från trunk-recorder till api.openmhz.com med en nyckel per system. **Alternativ A, utan tillstånd:** en länk till openmhz.com i listan över webbmottagare. **Alternativ B:** fråga operatören innan något byggs mot api.openmhz.com. Värdet för svenska användare är litet, eftersom Rakel är krypterat. **Sidospår:** trunk-recorder (GPL-3.0, senaste ändring 2026-09-26, kontrollerat i källkoden) är den öppna motorn bakom OpenMHz och en möjlig extern avkodare för trunking (avsnitt 5, punkt 8 och 9). | [ ] |

**Svensk lag (gäller båda):** att lyssna är tillåtet. Enligt lagen om elektronisk kommunikation får innehåll i radiotrafik som inte är avsedd för en själv inte spridas eller utnyttjas. Programmet bör därför inte spela in eller dela ljudet från dessa tjänster automatiskt.

> **Två fynd som påverkar programmet redan nu (kontrollerat 2026-11-09):**
> - **adsb.lol kommer att kräva API-nyckel.** Flygradarns internetkälla (`adsb.py`, rad 467–493) använder adsb.lols öppna API. Enligt adsb.lols dokumentation kommer API:t att kräva en nyckel som man får genom att skicka data till adsb.lol, och gränserna för antal anrop ändras efter belastning. Internetkällan kan alltså sluta fungera för den som inte skickar data. Idé 27 (skicka ADS-B till adsb.lol) löser det för den som har en dongel.
> - **Flathub förbjuder AI-skrivna program sedan maj 2026.** Enligt GamingOnLinux gäller förbudet program med kod, dokumentation eller annat innehåll som är AI-genererat eller AI-assisterat, och även själva ansökan. Undantag kan ges för mogna och välskötta projekt. Signalspanaren är skriven med Claude Code (README), så idé 32 (Flathub) blir troligen nekad. Flatpak-paketet kan fortfarande byggas och installeras lokalt som idag. Kontrollera den officiella texten i Flathubs dokumentation innan en ansökan görs.

### Fler mottagare på nätet

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 20 | SpyServer-nätet (Airspy) | Publika SpyServer-mottagare världen över, många med Airspy på HF och VHF. Ger **IQ**, inte bara ljud, och täcker VHF/UHF, vilket KiwiSDR inte gör. **Villkor:** SpyServer är stängd källkod. Protokollet är inte helt dokumenterat och vissa funktioner är förbehållna SDR#. Katalogen över servrar visas bara i SDR# och har ingen publicerad specifikation. Öppna klienter finns: SDR++ och `spyserver_client` (miweber67). **Väg framåt:** utgå från SDR++:s öppna klientkod (kontrollera licensen). Bakåtkompilera inte katalogen (se reglerna i avsnitt 7). Fråga Airspy om katalogen får användas. Alternativet **SDRx.io** samlar publika rtl_tcp- och SpyServer-stationer; rtl_tcp stöds redan i programmet. | [ ] |
| 21 | SatNOGS (satelliter) | Ett öppet nätverk av markstationer. **Villkor (kontrollerat):** API:t är öppet för alla och all data är CC BY-SA. Programvaran är AGPL-3.0/GPL-3.0+. Varje observation har vattenfall (PNG) och ljud (OGG), ibland på archive.org. Rå IQ laddas normalt inte upp. **Nytta:** riktiga signaler med känd satellit och modulation, alltså facit-klipp för steg 1 i planen, och data får läggas i repot med upphovet angivet. | [ ] |

### Förkunskap till analysen (bevismotorn)

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 22 | EiBi och HFCC (rundradiotablåer) | Tablåer för rundradio på kortvåg: station, frekvens, tid, språk och sändarplats. Analysen vet då att det på 7 265 kHz klockan 18 UTC ska vara AM från en viss station. Passar idé 3. **Villkor:** EiBi beskrivs som gratis och används av många program, men ingen licenstext har hittats. HFCC:s villkor har inte hittats. Kontrollera eibispace.de och hfcc.org. Ett säkert sätt är att låta användaren ladda ner filen själv. | [ ] |
| 23 | Reverse Beacon Network, PSK Reporter, WSPRnet | Rapporter i realtid om var CW, FT8 och WSPR hörs just nu. De bekräftar analysens gissning med verkliga observationer. **Villkor:** PSK Reporter har en utvecklarsida som förbehåller sig rätten att blockera eller begränsa den som belastar tjänsten, kräver komprimering och avråder från att hämta samma data ofta. Rapporter till PSK Reporter får skickas högst var femte minut. RBN har inga hittade villkor; telnet-flödet är det loggprogram använder, med en anslutning och backoff. WSPRnet är inte kontrollerat. | [ ] |
| 24 | Priyom och UDXF (sifferstationer, utility) | Tablåer och listor för sifferstationer och utility-trafik på HF: militär, diplomati, väder. Stöd till `signaldb.py`. **Villkor:** inga hittade. Priyom har en tablå över sifferstationer på priyom.org och en community på IRC (Libera, #priyom) och Discord. Fråga innan data förs in. | [ ] |
| 25 | Rymdväder och utbredning | NOAA SWPC (Kp-index, solflöde) och VOACAP (utbredningsprognos). Förklarar varför ett band är dött och ger HF-analysen en förväntan. **Villkor (kontrollerat):** NOAA-data är allmän egendom och får användas fritt; NOAA vill att källan anges och att man inte antyder att NOAA står bakom programmet. Vissa värden är preliminära och ska märkas så. VOACAP för Linux (voacapl) har licensen "special" och CC0 enligt Debian. Läs upphovsrättsfilen innan något tas in. | [ ] |

### Jämföra och dela våra avkodningar

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 26 | airframes.io (ACARS, VDL2, HFDL) | Samlar meddelanden från många mottagare. Vi kan kontrollera att vår ACARS-avkodare ger samma meddelanden som nätverket (svaghet 1), och skicka våra egna. **Villkor:** allmänna användarvillkor finns; materiella ändringar meddelas 30 dagar i förväg. Inga särskilda API-villkor hittades. Data skickas till feed.airframes.io: ACARS UDP 5550, VDL2 UDP 5552 / TCP 5553, HFDL 5556. Det finns en API-klient (airframes-client). | [ ] |
| 27 | Skicka ADS-B till adsb.lol eller airplanes.live | Signalspanaren skickar avkodade flygplan till nätverket. **Villkor (kontrollerat):** adsb.lols historiska data, re-api och BEAST/MLAT-utgångar är ODbL 1.0. Det som delas med andra nätverk licensieras som CC0. Den som skickar data får en API-nyckel, och det löser problemet i rutan ovan. airplanes.live har inga officiella villkor hittade; en tredjepartssida anger 1 anrop/s gratis. | [ ] |
| 28 | Skicka AIS till AISHub | **Villkor (kontrollerat):** kräver minst ett eget rått AIS-flöde i NMEA från en mottagare man själv driver. Syntetiska, skrapade eller lånade data godtas inte, så simulatorn får aldrig skicka. Man fyller i ett formulär och får IP och UDP-port. Medlemmar med bra station får en API-nyckel till all data (1 anrop per minut). Data får användas även kommersiellt med texten "Data: AISHub contributors" och en länk. Rådata får inte lämnas vidare, och data får inte användas för navigering. | [ ] |
| 29 | APRS-IS som iGate | Skicka mottagna APRS-paket till APRS-IS, som aprs.fi visar. **Villkor (kontrollerat):** att läsa från APRS-IS går med lösenkoden -1. För att skicka krävs en giltig lösenkod, som bara ges till licensierade radioamatörer. Det är omstritt om en iGate som bara tar emot kräver licens, men praxis är licens. Programmet bör kräva anropssignal och lösenkod och aldrig skicka från simulatorn eller en IQ-fil. | [ ] |
| 30 | OpenSky Network och aprs.fi som facit | Kända flygplan och APRS-positioner att jämföra våra avkodare mot. **Villkor (kontrollerat):** OpenSky ger licens bara för icke-vinstdrivande forskning, utbildning, intern testning eller myndighetsbruk. Akademisk åtkomst granskas manuellt, och OpenSky ska citeras i publikationer. aprs.fi kräver eget konto och nyckel **per användare**, att aprs.fi anges som källa med synlig länk, och att svar cachas. | [ ] |

### Programmet självt

| # | Idé | Beskrivning | Vald |
|---|---|---|---|
| 31 | Raspberry Pi utan skärm | Kör på en Pi vid antennen med webbgränssnittet, som en egen fjärrmottagare. DragonOS har en Pi-version. Kräver att webbgränssnittet får inloggning (idé 14). | [ ] |
| 32 | Publicera på Flathub | Ett klick att installera på alla distributioner. **Troligen nekad** på grund av Flathubs AI-förbud (se rutan ovan). Alternativ: ett eget Flatpak-förråd på GitHub Pages eller releaser på GitHub med en `.flatpak`-fil. | [ ] |
| 33 | Vara server själv | Dela din dongel som rtl_tcp (enkelt och öppet) så att SDR++ eller andra program på en annan dator kan använda den. Att vara SpyServer kräver Airspys stängda protokoll; använd hellre rtl_tcp eller det öppna SDR-Server. | [ ] |
| 34 | Engelsk översättning | Med gettext är svenska fortfarande standard men användarbasen blir mycket större. | [ ] |
| 35 | Plugin-system för avkodare | Som SigDigger 0.3: andra kan skriva avkodare utan att ändra huvudkoden. Hör ihop med avsnitt 5, punkt 8 (externa avkodare). | [ ] |
| 36 | Samlat avsnitt om lagar och regler | Vad som får tas emot, avkodas, sparas och spridas enligt svensk lag och PTS. Idag spritt i filen. Kan bli en egen sida i manualen. | [ ] |

## 9. Där Signalspanaren redan är bäst

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
- SigDigger och Suscan: <https://github.com/BatchDrake/SigDigger>, <https://github.com/BatchDrake/suscan>
- IQEngine: <https://github.com/IQEngine/IQEngine>, SigMF: <https://github.com/sigmf/SigMF>
- TorchSig: <https://github.com/TorchDSP/torchsig>, artikel: <https://arxiv.org/pdf/2207.09918>
- RadioML (licens): <https://deepsig.squarespace.com/datasets-old>
- Artemis: <https://github.com/AresValley/Artemis>, sigidwiki: <https://sigidwiki.com/>
- inspectrum: <https://github.com/miek/inspectrum>, URH: <https://github.com/jopohl/urh>
- gr-inspector: <https://github.com/gnuradio/gr-inspector>, FISSURE: <https://github.com/ainfosec/FISSURE>, <https://fissure.readthedocs.io/>
- Avsnitt 7: licens och datum lästa ur projektens källkod (git, 2026-11-09).
- LiveATC: <https://liveatc.net/legal>, <https://liveatc.net/faq>, <https://forums.liveatc.net/listener-forum/permission-to-use-liveatc-audio-streams>
- SpyServer: <https://www.rtl-sdr.com/sdr-update-spyserver-network-browser-now-available/>, <https://github.com/projecthorus/radiosonde_auto_rx/issues/628>, <https://relatedrepos.com/gh/miweber67/spyserver_client>, <https://www.rtl-sdr.com/sdrx-io-a-public-server-network-for-rtl_tcp-and-or-spyserver-stations/>
- SatNOGS: <https://satnogs-network.readthedocs.io/en/latest/api.html>, <https://www.libre.space/projects/satnogs/>, <https://wiki.satnogs.org/Observations>
- EiBi/HFCC: <https://www.rtl-sdr.com/tag/eibi/>, <https://pypi.org/project/swtools/1.1.0>
- PSK Reporter: <https://pskreporter.info/pskdev.html>; RBN: <https://github.com/the-chrislaw/hamclock-pro/issues/399>
- Priyom: <https://priyom.org/>
- NOAA SWPC: <https://www.spaceweather.gov/content/data-access>, <https://registry.opendata.aws/noaa-space-weather>; VOACAP: <https://en.wikipedia.org/wiki/VOACAP>, <https://metadata.ftp-master.debian.org/changelogs/main/v/voacapl/stable_copyright>
- airframes.io: <https://alpha.app.airframes.io/terms>, <https://docs.airframes.io/docs/backend/aggregator>, <https://github-redirect.dependabot.com/airframesio/airframes-client>
- adsb.lol: <https://www.adsb.lol/docs/open-data/historical/>, <https://www.adsb.lol/docs/feeders-only/re-api/>, <https://github.com/adsblol/api>; airplanes.live: <https://flyitalyadsb.com/en/dati/confronto/>
- AISHub: <https://www.aishub.net/join-us>, <https://www.aishub.net/>
- APRS-IS: <https://www.aprs-is.net/Connecting.aspx>; aprs.fi API: <https://aprs.fi/page/api>
- OpenSky: <https://openskynetwork.github.io/opensky-api>
- Flathub: <https://docs.flathub.org/docs/for-app-authors/requirements>, <https://www.gamingonlinux.com/2026/05/flathub-moves-to-ban-nearly-all-apps-and-submissions-made-with-generative-ai/>
- OpenMHz och trunk-recorder: <https://github.com/robotastic/trunk-recorder/>, <https://raw.githubusercontent.com/TrunkRecorder/trunk-recorder/master/docs/OpenMHz.md>, <https://github.com/aSmig/trunk-server>, <https://apps.apple.com/us/app/6466666994>
- Broadcastify: <https://support.broadcastify.com/hc/en-us/articles/204740425-Live-Audio-Feed-Catalog-API>, <https://support.broadcastify.com/hc/en-us/articles/50488286518427-Broadcastify-Calls-Client-API>, <https://www.broadcastify.com/calls/dev/>, <https://www.broadcastify.com/>, <https://forums.radioreference.com/threads/other-apps-taking-and-reusing-broadcastify-streams.447737>

Verktygslistorna för DragonOS bygger delvis på äldre artiklar och kan skilja sig från den senaste ISO:n (Noble R7/R8). SigintOS 2.0 saknar en publik funktionslista.
