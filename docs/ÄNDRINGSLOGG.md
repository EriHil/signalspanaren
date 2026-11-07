# Ändringslogg

## 2.12.1 (2026-11-07)

- **Rättat: krasch i signalanalysens spektrum** (`KeyError: 'f_lo'`). Vissa klassningar satte signalens bredd utan kantfrekvenser, bland annat linjära modem som STANAG 4539 och FT8/FT4-detektorn. Ritningen förutsatte att kanterna fanns och kraschade vid varje omritning. Nu sätts kanterna alltid, och ritningen räknar ut dem från mitt och bredd om de ändå saknas. Upptäckt på Shannon VOLMET 5505 kHz, där analysen ibland gissade STANAG 4539.
- **Tal klassas inte längre som modem eller FM.** På VOLMET blev vart fjärde analyssteg "Digital linjär" och föreslog STANAG 4539. Talets grundton (5,7 ms ≈ 175 Hz) och sökningens nedre gräns (20 ms) togs för en modemram, och sökningen godtog ett "maximum" som bara var gränsen. Nu krävs en verklig topp. Tal med stavelsevariation (minst 1,5 dB) klassas inte som LIN, varken direkt eller via ramminnet. Svagt SSB-tal i brus blev ofta "FM", men FM kräver nu konstant envelope (stavelsevariation under 2 dB). En bandbredd där bruset dominerar (SNR under 0 dB) blir "brus" i stället för FM.
- **Fyra nya facit-klipp med riktigt tal:** Shannon VOLMET 5505 kHz USB, 40 m LSB (svag), samt en tysk och en finsk station på 80 m. Signalfamiljen blir rätt på 42 av 42 klipp. STANAG 4285, PSK och FT8 känns igen som förut.

## 2.12.0 (2026-11-07)

- **DSP-filter** (ny fil `dspfilter.py`). Knappen **🎛 DSP-filter** under Demodulator visar en panel. Den svävar ovanpå den nedre delen av fönstret, så spektrum och vattenfall syns hela tiden. Allt går att ändra medan du lyssnar:
  - **Filterförval FIL1/FIL2/FIL3**: SSB 3,0/2,4/1,8 kHz, CW 1,2/0,5/0,25 kHz och AM 9/6/3 kHz.
  - **Filterform SHARP/SOFT**. SHARP har ca 150 Hz övergång, −48 dB 150 Hz utanför kanten och fel sidband helt borta. SOFT har mjukare flanker och mindre ringning.
  - **Twin PBT**: nedre och övre kanten flyttas var för sig i steg om 50 Hz. Ny bandbredd nollställer PBT.
  - **Brusreducering (NR)** nivå 1–15 (Wiener med beslutsstyrd SNR och minimistatistik). På riktig trafik ökar kontrasten mellan tal och paus med 5–9 dB vid nivå 8, och talet blir inte svagare.
  - **Auto-notch (AN)** tar bort toner som ligger still (−30 dB) men inte tal. **Manuell notch (MN)** på vald ljudfrekvens, med bredd W/M/N.
  - **Störningsblankare (NB)** med nivå, djup och bredd. Den arbetar på bredbandig IQ (384 kHz) innan kanalfiltret.
  - **Tonkontroll**: RX HPF 100–2000 Hz, LPF och bas/diskant −5…+5 (2 dB per steg).
- **AGC-förval** SDR++ / FAST / MID / SLOW. AGC decay går nu att ställa från 0,1.
- **Sidbandet kontrolleras också vid start.** Ett sparat LSB-läge på t.ex. 5,505 MHz (VOLMET, USB) blev obegripligt tal. Väljer du själv det ovanliga sidbandet visas en varning.

## 2.11.0 (2026-11-07)

- **Automatiskt sidband.** I USB/LSB byter programmet sidband när du stämmer till ett annat område. LSB används för tal på 160, 80 och 40 m. USB används på 30 m och uppåt, på 60 m, i FT8- och dataområdena (t.ex. 7,040–7,050 och 7,070–7,080 MHz) och på flyg, sjöfart och VOLMET. Ett sidband du valt själv gäller tills du lämnar området. Bokmärken och skannern behåller sitt sparade läge, och den valda bandbredden behålls. Kan stängas av under Demodulator → Automatiskt sidband.
- **KiwiSDR har egen förstärkning.** Den använder Kiwins egen AGC som standard. Manuellt går skalan 0–120 dB (förval 65), skild från dongelns 0–49,6. Förut skickades dongelns värde till Kiwin, vilket gav väldigt svag IQ (ca 0,05 % av fullt utslag mot ca 10 % med Kiwins AGC). Frontpanelens RF-ratt följer den valda källan.
- **Squelch i SSB och CW** håller öppet ca 1 s efter att signalen försvunnit, i stället för 0,25 s, så att stavelser och ord inte klipps.

## 2.10.2 (2026-11-07)

- **Ny AGC för AM, SSB och CW, som i SDR++.** Den gamla AGC:n satte förstärkningen i trappsteg var 5:e ms. Den drog ned direkt på varje topp och tog upp bruset med ca 6 dB i varje paus. Den nya följer nivån mjukt (attack τ ≈ 20 ms, decay τ ≈ 200 ms) och har klippskydd som tittar framåt i blocket. Förstärkningen ändras per sampel. Mätt på riktig LSB-trafik via KiwiSDR: skillnaden mellan tal och pauser är 13 dB, som i signalen, i stället för 6–7 dB. Klick och surr (energi över 3,5 kHz) är 11 dB lägre. Resultatet ligger inom 1 dB från en återskapad SDR++-kedja. Ljudet är ca 6 dB svagare än förut, så höj volymen vid behov.
- Nya reglage under Demodulator: **AGC attack** (1–200, standard 50) och **AGC decay** (1–20, standard 5), samma skalor som i SDR++.
- KiwiSDR: Kiwin anpassas vid alla lägesbyten, även från frontpanelen och skannerns bandförval. Att dra i vattenfallet använder samma gräns för Kiwins band som vanlig inställning. En kanal som är bredare än Kiwins band stämmer inte om Kiwin till samma mitt vid varje steg.
- Kalibreringen mäter inte gammal data direkt efter byte av frekvens eller korrigering. Den räknar och sparar mot den Kiwi som faktiskt körs.

## 2.10.1 (2026-11-07)

- **USB/LSB via KiwiSDR låter rätt igen.** Programmet räknade som om kanalen låg symmetriskt runt den inställda frekvensen, men USB och LSB ligger helt på ena sidan. Därför stämdes Kiwin inte om när en SSB-station låg 3–4 kHz från Kiwins mitt. En del av talet hamnade då utanför Kiwins ca 12 kHz breda band och byttes mot brus, så talet blev dovt och brusigt. Nu räknar programmet med kanalens verkliga kanter och lägger kanalens mitt i Kiwins mitt. Kontrollen görs också vid byte av läge eller bandbredd.
- **Frekvenskorrigering per KiwiSDR.** Med KiwiSDR som källa visas Frekvenskorrigering och Kalibrera frekvens. Värdet sparas per Kiwi med en decimal. Kalibreringen mäter med Kiwins smala IQ med upplösning under 1 Hz. Exempel: SK3JR låg −56 Hz fel på RWM 9,996 MHz och hamnade på −0,4 Hz med +5,6 ppm.

## 2.10.0 (2026-11-06)

- **Flygradar kan hämta flygplan från adsb.lol på internet**, utan dongel. Välj "adsb.lol (internet, ingen dongel)" i listan, eller starta med `flygradar --kalla adsblol`. Den hämtar flygplan inom 250 nautiska mil från kartans mitt var 5:e sekund. Det visar flygplan som andra matar in till adsb.lol, inte det din dongel hör. Mittpunkten skickas till adsb.lol vid varje anrop. Flygplansdata: adsb.lol (ODbL).
- Vid fel väntar Flygradar allt längre mellan försöken, högst 60 s, och visar felet en gång.

## 2.9.3 (2026-11-06)

- **Väderfax föreslås inte längre för vilken FM-signal som helst.** Faxavkodaren kräver faxets startton eller fasningspuls innan den sätter signaltypen.
- **Signaldatabasen** utesluter förslag med helt fel bredd (mer än 4 gånger för bred eller under en femtedel) och förslag utanför tjänstens frekvenser, till exempel marin VHF på 28 MHz. Den använder den uppmätta, stabila bandbredden.
- FT8- och FT4-områdena är den inställda frekvensen plus 4 kHz ljudband.

## 2.9.2 (2026-11-06)

- **FT8/FT4-detektor i signalanalysen.** Den tittar på upp till 4 s kring den punkt man klickade på, med ett fönster som är exakt en symbol långt (160 ms för FT8, 48 ms för FT4). FT8 och FT4 känns igen på att varje symbol ligger i en ton, att tonen byter ungefär varje symbol och att högst 8 respektive 4 intilliggande toner används. När läget har setts två gånger smalnas kanalen av till signalens egen bredd. Detektorn gav inga falska träffar på AM, CW, SSB, RTTY, STANAG eller RWM i facitklippen.

## 2.9.1 (2026-11-06)

- **SSB-tal känns igen.** Talets övertoner gav ett regelbundet "tonraster" som tolkades som OFDM. Nu kräver OFDM och "digital linjär" jämn effekt, eftersom tal varierar med stavelserna.
- **AM** kräver också att sidbanden är spegelbilder av varandra kring bärvågen. Det skiljer AM från en stark FT8-signal eller en FSK-ton.
- **Bandbredd:** en FSK-ton tolkas inte längre som AM-bärvåg. Luckan som överbryggas skalas med signalens bredd. Överblicken försöker igen om den hamnar i en paus i en signal som sänder i omgångar.
- **FT4** finns i signaldatabasen. Mätverktyget har nu 34 klipp: 22 riktiga och 12 syntetiska.

## 2.9.0 (2026-11-06)

- **Automatisk bandbredd i signalanalysen.** I läget Auto mäter analysen själv hur bred signalen är: först en bred överblick, sedan avsmalnad kanal. Den mäter mot brusgolvet, så AM-sidband räknas med. Tätt liggande signaler, till exempel modem med 3 kHz mellanrum, skiljs åt. Tidigare blev det ofta bara bärvågen (cirka 400 Hz).
- **Symboltakt och ramperiod.** För modem med en bärvåg söks symboltakten nära bandbredden, och ramperioden hittas med autokorrelation. Den visas i panelen, till exempel "106,7 ms = 256 symboler". STANAG 4285 känns igen på ramperioden 106,67 ms. Det är verifierat mot riktiga sändningar och stämmer med SigIDWikis ACF.
- **AM känns igen** på bärvågens andel av effekten. Tidigare klassades AM ofta som OFDM.
- **FT8** finns i signaldatabasen.
- **Mätverktyget `analystest.py`** kör analysen på facitklipp (inspelade från KiwiSDR, se `facit.json`) och på syntetiska signaler. Det visar träffsäkerhet och förväxlingar och jämför med en tidigare körning.
- Rättat: RTTY-avkodaren kunde krascha när symboltakten saknades. Rubriken i analysen visar "mäter bandbredd …" och sedan den uppmätta bredden.

## 2.8.1 (2026-11-06)

- **Signalanalys:** bärvågens topp kapades i det lilla spektrumet. Skalan följde 99,5-percentilen, och en smal bärvåg ligger långt över den. Spektrumlinjen har nu en egen övre gräns som följer den högsta toppen: den stiger direkt och sjunker långsamt. Testat mot en riktig AM-sändning på 7,265 MHz via KiwiSDR.

## 2.8.0 (2026-11-06)

- **Nytt namn: Signalspanaren** (tidigare Radiospanaren). Kommandot heter `signalspanaren`, och `radiospanaren` fungerar fortfarande. Inställningar, bokmärken, loggar, cache och inspelningsmappen flyttas automatiskt vid första start. Installationsskriptet tar bort den gamla installationen. Flatpak-ID:t är nu `se.eh.Signalspanaren`.
- **Skärmanpassning**: programmet känner av skärmens upplösning. Fönstren får en storlek som ryms på skärmen, och på låga skärmar används ett kompakt läge (Visning → Skärmanpassning).
- **Justerbar nedre panel**: en synlig list mellan vattenfallet och flikarna. Höjden sparas.
- **Signalanalys**: inget klipps längre. Signaltypen står kvar medan klartexten rullar, och mätvärdena får den höjd de behöver. Signalfamiljen stabiliseras: en ny typ visas när den har setts 3 av de 5 senaste gångerna, och "ingen signal" efter 2.
- Förkortade texter i sidopanelen visas nu hela. dB-skalan i spektrumet trängs inte ihop på låga skärmar, och fartygskartans färgförklaring radbryts.
- Rättat: `frontpanel.py` saknades i installationsskriptet och i flatpak-paketet.

## 2.1.1 (2026-11-05)

- **Flatpak: GNOME 51-körtid** i stället för GNOME 48, som nått slutet på sin livstid. GTK 4.24, libadwaita 1.10, Python 3.14, libshumate 1.5.5, numpy 2.5.4 och scipy 1.18.1.
- Inga funktionsändringar; samma program som 2.1.0.

## 2.1.0 – "beta v2.1" (2026-11-05)

- **Flatpak-paketering** (`flatpak/`): kör på valfri distribution oavsett systemets GTK-version, med egna bibliotek (GTK4, libadwaita, libshumate, librtlsdr, numpy, scipy). Byggt och verifierat mot GNOME 48-körtiden.
- Verifierat att programmen kör på Debian 13 (trixie).

- **Frekvenskalibrering**: "Kalibrera frekvens (ppm) …" i Mottagare (eller `--kalibrera`) mäter dongelns frekvensfel mot en känd bärvåg (t.ex. tidssignalen RWM) och sätter ppm automatiskt.
- Rättat: loggraden "Mottagning startad" visade dongelns mittfrekvens i stället för den inställda frekvensen.

## 2.0.0 – "beta v2" (2026-11-05)

Samlar funktionerna från 1.2–1.4 under en version: AIS, ADS-B och spektrumanalys.
- **Flygradar (ADS-B, 1090 MHz)** – flygplan på OpenStreetMap-karta (1.2.0).
- **Fartygskarta (AIS)** – fartyg på karta med typfärg och spår (1.3.0).
- **Aktivitetskarta (spektrumanalys)** – värmekarta över ett band, med WFM 96–108 MHz uteslutet (1.4.0).
- Avkodare för ACARS, AIS och APRS, CRC-kontrollerade.
- Fungerar på Fedora 40+, Ubuntu 24.04+, Linux Mint 22+ och Debian 13+; kartan installeras automatiskt.

Detaljerna för varje del står under respektive delversion nedan.


## 1.4.0 (2026-11-05)

### Nytt: Aktivitetskarta
- Fönstret "Aktivitetskarta" (sidopanelen → Avkodare, eller `--aktivitetskarta`) sveper ett valt band och ritar en värmekarta: frekvens i sidled, tid nedåt, färg efter signalnivå över brusgolvet.
- Upptagenhetsremsa (andel svep per kanal som var aktiv) och lista över de mest aktiva kanalerna; klicka för att lyssna.
- Färdiga band eller eget intervall, valbar tröskel och färgskala. Ömsesidigt uteslutande med skannern.
- WFM-rundradion 96–108 MHz räknas aldrig med och ritas grå.
- Ny modul `aktivitet.py` (BandMonitor, bara numpy).

## 1.3.0 (2026-11-05)

### Nytt: Fartygskarta (AIS)
- Fönstret "Fartyg (AIS)" (sidopanelen → Avkodare, eller `--fartygskarta`) med OpenStreetMap-karta och fartygstabell.
- AIS-meddelanden slås ihop per fartyg: position, fart, kurs, bäring, status, namn, anrop, typ och destination.
- Symboler i kursriktningen med färg efter fartygstyp och spår. Förtöjda och ankrade fartyg visas som en ring, basstationer och fyrar som en prick.
- Varning för AIS-nödsändare (AIS-SART/MOB/EPIRB).
- Ny gemensam kartmodul, `kartvy.py`, med reserv-radarvy och knappen "Installera kartan".
- Simulatorn har fyra fartyg som rör sig.

### Rättat
- "Visa alla" och anpassningen av kartan behåller nu zoomnivån (`go_to_full`).

## 1.2.0 (2026-11-05)

### Nytt: Flygradar (ADS-B)
- Eget program, `adsb_karta.py` / `flygradar`, med OpenStreetMap-karta (libshumate) och flygplanstabell.
- ADS-B/Mode S-avkodare (`adsb.py`): DF17/18, DF11 och DF4/5/20/21, CRC-24 med rättning av enstaka bitfel, och CPR-positioner.
- Startas med knappen "✈ Öppna Flygradar" i Radiospanaren, som först släpper dongeln. Finns också i programmenyn.
- Varning för squawk 7500, 7600 och 7700. Visar höjd i fot eller meter.
- Saknas kartan visas en enkel radarvy och knappen "Installera kartan" (pkexec med bekräftelse).
- Installationsskriptet installerar kartan (libshumate / gir1.2-shumate-1.0) automatiskt.

### Rättat
- Stängning vid SIGTERM fungerar på både ny GLib (GLibUnix) och äldre (Ubuntu 24.04 / Mint 22).
- Kompatibel med libshumate 1.2 (Ubuntu 24.04 / Mint 22).
- RTL-SDR- och rtl_tcp-källorna har valbar samplingstakt.

## 1.1.0 – "beta v1" (2026-11-05)

Första publicerade versionen.

### Mottagare
- Spektrum och vattenfall med bandplan, bokmärken och markering av aktiva signaler.
- Zoom-FFT med upplösning ner till cirka 12 Hz när man zoomar in.
- Demodulering i WFM, NFM, AM, USB, LSB och CW.
- Källor: RTL-SDR (librtlsdr via ctypes), rtl_tcp och simulator.
- Direktsampling för HF (I- och Q-gren). Testat med FT8 på 20 m.
- Ljud via PipeWire/PulseAudio (pacat/pw-cat). WAV-, auto- och IQ-inspelning.

### Skanner
- Bokmärkes- och intervallskanning, där hela fönster analyseras på en gång.
- Topp-detektering: grannkanaler till starka sändare räknas inte.
- Automatisk överhoppning av omodulerade bärvågor (dongelstörningar), med en lista över hittade störningar.
- Spärrlista, väntetid, max lyssningstid och knappen "Hoppa över".
- Squelchen nollställs vid frekvensbyte, så att den inte "ärver" läget från förra frekvensen.

### Signallogg och avkodare
- Automatisk signaldetektering med filter i stil med Wireshark och export till CSV.
- Identifiering av kända signaltyper och dongelns kristallövertoner.
- ACARS-, AIS- och APRS-avkodare med CRC-kontroll. Fliken "Avkodat" med karta.

### Övrigt
- Webbgränssnitt med vattenfall, inställning och ljudström.
- Loggfil, felrapport (`--rapport`), självtest (`--sjalvtest`) och skärmbild (`--skarmbild`).
- Installationsskript för Fedora, Ubuntu, Linux Mint och Debian.
- Kompatibelt med libadwaita 1.5 (Ubuntu 24.04 / Linux Mint 22).
