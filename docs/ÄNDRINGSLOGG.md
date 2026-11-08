# Ändringslogg

## Att göra

- [ ] **Anonym delning av klassningar.** Valbart vid installation, avstängt som standard.
  - Skickas: mätvärden och vald signaltyp. Aldrig klartext, anteckningar, plats eller tid.
  - Ingen identitet går till GitHub.
  - När majoriteten av användarna över tid är överens om en klassning, införs den i signaldatabasen automatiskt.
  - Gränser för antal användare och tid bestäms när det byggs.
  - Öppna frågor: om ändringarna granskas innan de införs, och vilken tjänst som tar emot klassningarna.
- [ ] **Låst signaltyp: beslut.** Procenten visas som den var när typen låstes och uppdateras inte. Ska den i stället följa analysen? Inget beslut ännu.
- [ ] **AI-chatten.** Verifiera analystexten (histogram och envelop) på en riktig signal. Testat bara med syntetisk data.
- [ ] **Claude-läget.** Testas med användarens egen API-nyckel och modell-ID. Modell-ID är tomt som standard.
- [ ] **AI-sök efter tjänst eller signaltyp.** Be AI:n hitta till exempel VOLMET eller STANAG 4285. Sorteringen följer KiwiSDR-flaggornas typer. Frekvenserna verifieras med analysen, flaggorna är bara en lista. Träffen visas i vattenfallet och spelas upp. Om sökordet bara är en modulation, till exempel FSK, visas i stället de signaltyper som använder den.
- [ ] **AI-chatten: kortare svar.** Gratis-AI:n skriver långa svar och ibland påhittade detaljer. Prompten behöver skärpas.

## 2.12.1 (2026-11-07, ej släppt)

- **Rättat: krasch i signalanalysens spektrum** (`KeyError: 'f_lo'`). Vissa klassningar satte signalens bredd utan kantfrekvenser, bland annat linjära modem som STANAG 4539 och FT8/FT4-detektorn. Ritningen förutsatte att kanterna fanns och kraschade vid varje omritning. Nu sätts kanterna alltid, och ritningen räknar ut dem från mitt och bredd om de ändå saknas. Upptäckt på Shannon VOLMET 5505 kHz, där analysen ibland gissade STANAG 4539.
- **Tal klassas inte längre som modem eller FM.** På VOLMET blev vart fjärde analyssteg "Digital linjär" och föreslog STANAG 4539. Talets grundton (5,7 ms ≈ 175 Hz) och sökningens nedre gräns (20 ms) togs för en modemram, och sökningen godtog ett "maximum" som bara var gränsen. Nu krävs en verklig topp. Tal med stavelsevariation (minst 1,5 dB) klassas inte som LIN, varken direkt eller via ramminnet. Svagt SSB-tal i brus blev ofta "FM", men FM kräver nu konstant envelope (stavelsevariation under 2 dB). En bandbredd där bruset dominerar (SNR under 0 dB) blir "brus" i stället för FM.
- **Testat mot KiwiSDR:ns stationsflaggor** (VOLMET, STANAG, HFDL, FAX, väder-RTTY, marin FSK, RWM, CHU). Rättat:
  - **Nycklade bärvågar** blir OOK och inte AM/SSB/okänd. Exempel är RWM:s tidspulser, vars hårda nyckling ger breda nyckelklick, och svag nyckling där av/på-detektorn ger upp i bruset.
  - **Smala signaler där en bärvåg dominerar** blir bärvåg och inte FM (CHU på långt håll). FM kräver nu minst 1,5 kHz bredd, och SSB minst 800 Hz.
  - **FSK där man klickar mellan tonerna** (850 Hz shift): bandbredden fastnade mellan tonerna och gav "brus". Nu hittas två toner på var sin sida om klicket. Två smala toner som bär nästan all effekt räknas som FSK2 även när signalen är för svag för tonhistogrammet.
  - **Förslaget byter snabbare** när det låsta förslaget inte längre passar alls, till exempel "SSB-tal" efter att mätningen visat FSK.
- **Flaggor ovanför vattenfallet** (ny fil `flaggor.py`). Med en KiwiSDR som källa hämtas Kiwins egna stationsflaggor, till exempel VOLMET, HFDL, STANAG, FAX och tidssignaler. Hämtningen sker i bakgrunden, tar några sekunder och sparas ett dygn. De ritas längst ner i spektrumet, med färg efter typ. Kiwins flaggor visas bara när de sänder enligt sin tidsangivelse.
  - **Klicka på en flagga** för att stämma in den, med frekvens och läge.
  - **Högerklicka utan att dra** för att lägga till en flagga, ändra eller ta bort den, eller dölja en av Kiwins. Högerdrag flyttar fortfarande vattenfallet.
  - **Egna flaggor** sparas i `~/.config/signalspanaren/flaggor.json` och visas oavsett källa. En ändrad Kiwi-flagga sparas som en egen kopia, eftersom Kiwins databas kräver ägarens lösenord.
  - Sektionen **Flaggor** i sidopanelen har visa/dölj, hämtning av Kiwins flaggor och "Ny flagga på inställd frekvens".
- **Analysen säger "osäker" i stället för att gissa.** Varje klassning får ett säkerhetsmått (0–100 %). Hälften väger hur stabil signalfamiljen är över de senaste mätningarna, 30 % SNR och 20 % hur väl förslaget ur databasen passar. Säkra bevis höjer måttet: FT8/FT4-detektorn, ett låst förslag med hög poäng eller text från en avkodare. Under 55 % visas "Osäker – klassa själv". Analysens bästa gissning står kvar i liten text, och mätvärdena visas som vanligt.
- **Klassa själv:** knappen "🚩 Klassa själv …" i signalanalysen sparar din bedömning som en flagga på frekvensen. Formuläret är förifyllt med frekvens, läge och analysens gissning. Därefter visar analysen din klassning som signaltyp och sin egen gissning bredvid.
- **Hopfällbara sektioner i sidopanelen:** klicka på en rubrik för att fälla ihop eller fälla ut den. Läget sparas.
- **Fyra nya facit-klipp med riktigt tal:** Shannon VOLMET 5505 kHz USB, 40 m LSB (svag), samt en tysk och en finsk station på 80 m. Signalfamiljen blir rätt på 42 av 42 klipp. STANAG 4285, PSK och FT8 känns igen som förut.
- **Signalanalysen öppnar på vald frekvens.** När du öppnar fliken Signalanalys utan analysmål analyseras den inställda kanalen direkt, i stället för att visa "Klicka på en bärvåg". Ett mål som du redan valt med klick ligger kvar.
- **Frekvensjustering i vattenfallet.** Knapparna **f−** och **f+** bredvid zoomknapparna flyttar den inställda frekvensen till nästa steg ned eller upp. Stegvalet mellan knapparna är samma som Frekvenssteg i sidopanelen, och de två följer varandra.
- **Rättat: scrollhjulet i vattenfallet** hoppade förbi ett steg när frekvensen inte låg på stegnätet. Med 100 kHz-steg gick 7,124 MHz ned till 7,000 MHz. Nu flyttas det ett steg i taget, som f− och f+. Små rörelser på styrplattan räknas ihop till ett steg, och en ny riktning börjar alltid med ett steg. Samma fel är rättat för den virtuella frontpanelens ratt, även i MHz-läget.
- **Diagram att välja.** Rutan för diagram har en lista: Automatiskt (växlar efter signaltyp, som förut) eller ett fast diagram: Konstellation, Ramraster, Tonspektrum, Envelop, Frekvensfördelning eller Faxbild. Om det valda diagrammet saknas för signalen står det i rutan. Valet sparas.
- **Symboler i klartextrutan.** Ettor och nollor från senaste analyssteget (FSK) visas sist i klartextrutan och ersätts vid varje steg. Avkodad text kommer före dem. Tidigare visades de i avkodarcellerna.
- **Spara och logga klartext.** Knappen Spara sparar hela klartextrutan, med symboler, till en fil i mappen klartext i programmets tillståndsmapp. Knappen Logga skriver ny avkodad text med tidsstämpel till en fil medan den är på. Symbolerna tas inte med i loggen.
- **Analyslogg per steg.** Varje analyssteg skriver en rad i loggen med uppmätt typ, baud, shift, bandbredd, kandidat, vilka avkodare som passar och vilka som kördes, samt antal tecken text.
- **Signaltyp-rutan är låst.** Rutan visar alltid samma tre rader: rubrik med läge, typ med säkerhet och en förklaring. Den byter bara när analysen låser en ny typ. Vid ny målfrekvens börjar den om.
- **Signaltypen väljs i rullistan.** Under signaltypen finns en rullista med Automatiskt, analysens låsta typ, övriga kandidater och alla kända typer. Ett val sparas som operatörens klassning på frekvensen och finns kvar i programmet. Väljer du Automatiskt tas den bort igen. En klassning gjord med Klassa själv ändras av ett val, men tas inte bort av Automatiskt. Rutan har en fast minimihöjd och scrollar i stället för att klippa.
- **kHz-skala i frekvensfördelningen.** Histogrammet har märken längs underkanten: 1 kHz när området är minst 2 kHz, annars 100 Hz. Då syns signalens bredd.
- **Klartextrutan töms vid byte av signal.** När målet flyttas mer än en kanalbredd töms rutan och den köade texten, så att gamla tecken inte blandas med den nya signalen. Spara och Logga behåller texten.
- **Fast signaltyp och ärlig säkerhet.** Analysen låser en typ när den varit tydligt bäst en stund, med minst 80 % av de två främsta poängen. Procenten är just den andelen, så två lika starka typer ger 50 %. Klassraden följer samma regel. Rutan ändras sedan bara av dig: rullistan väljer eller ändrar typ, och Redigera ändrar namn, typ och anteckning.
- **AI-chatt i analysen.** Knappen **🤖 Fråga AI** öppnar en chatt om den aktuella analysen. Standard är en gratis online-AI utan nyckel. Under **Inställningar för AI** kan du i stället välja Claude med egen API-nyckel och modell-ID. Nyckeln sparas i programmets inställningar med filrättigheter 600 och skrivs inte i loggen. Med Claude skickas analysdata till Anthropic. Svaret väntar i en egen tråd, så analysen och avkodningen fortsätter medan AI:n svarar. Bifogad analys: mätvärden, kandidater, spektrum, frekvensfördelning, envelop och de sista 500 tecknen klartext. AI:n ändrar aldrig signaltypen själv. När ni är överens kan du klicka **Spara i analysrutan**. Signalen sparas då som din klassning på frekvensen. En egen klassning på samma frekvens uppdateras i stället för att en ny skapas. Sparning vägras om analysen har bytt frekvens sedan frågan.
- **AI-chatten kan stängas av.** Den följer med och är på som standard. Brytaren **Använd AI-chatten** under Inställningar för AI stänger av den. Då anropas ingen AI-tjänst, och knappen visar **🤖 AI av**. Installeraren frågar vid första installationen, om du kör den i en terminal.
- **Rättat: KiwiSDR-mottagningen stannade när Kiwi stängde anslutningen.** Nu försöker appen återansluta efter 5, 15 och 30 sekunder och visar det i en notis. Kiwins DX-flaggor hämtas bara när ingen lyssnar, eftersom en samtidig hämtning kopplade ner ljudet. Flaggorna hämtas på ungefär fyra sekunder, och ljudet startar en sekund senare.
- **Snabbare analys vid klick.** Ett klick på en signal visar ett första resultat efter ungefär en halv sekund, med en gissad bandbredd. Bandbreddsmätningen förfinar resultatet efteråt, och rubriken visar ”mäter bandbredd …” under tiden.

- **Analysrutan står still.** Mätvärdesrutan visar alltid samma rader i samma ordning. Saknas ett värde visas ett dämpat streck, i stället för att raden försvinner. Signaltypen står på en egen rad överst. Raden förkortas om den inte ryms, och hela texten finns i tooltip. Kolumnerna har fast bredd. Deviation, AM-grad och subton delar raden Modulation. Avkodarens värden står på raderna Avkodare och Avkodare, mer. I signaltyp-rutan till höger står rullistan och Redigera alltid kvar, bredvid varandra.
- **Frekvensfördelning är standarddiagram** i diagramrutan i stället för Automatiskt.

- **Ny analysmotor (bevismotor, ny fil `bevis.py`).** Den gamla analysen fällde en ny dom vart 0,4 s, så signaltypen hoppade. Ett exempel är Shannon VOLMET 5505 kHz, där en paus i talet såg ut som nyckling. Nu ger varje analyssteg poäng till alla tänkbara typer, och poängen samlas över ungefär 10 s. Visningen byter typ först när en annan typ har lett tydligt i 3 s. Procenten är motorns sannolikhet. Raden **Grund** visar vad beslutet bygger på, till exempel talrytm, bandbredd, flygband eller flagga, och vilka typer som annars är möjliga.
  - **Taldetektor** över 3 s (stavelserytm 2–8 Hz och pauser). Tal ger minuspoäng till alla digitala typer och till CW.
  - **Bandplan som stöd:** flyg-, rundradio-, amatör- och NAVTEX-frekvenser. FT8/FT4 finns bara i sina delband, och FT8/FT4-detektorn körs inte utanför dem. Den gav förut falsklarm på tal och smalnade kanalen till 300 Hz.
  - **Flaggor som stöd:** KiwiSDR-flaggor och egna flaggor nära frekvensen ger lite stöd (inte facit).
  - Motorn börjar räkna först när bandbredden är färdigmätt.
  - Mätt med analystest: rätt typ visas i 98 % av stegen (förut 91 %), och visningen byter hälften så ofta (58 byten mot 116 på 46 klipp). Den gamla motorn kan väljas med `"analys_motor": "gammal"` i inställningsfilen.
- **Rättat: klick på en KiwiSDR-flagga behöll fel läge.** Kiwins lägen (CWN, NBFM, AMN, USN, LSN, SAM …) kändes inte igen, så läget du hade stod kvar, till exempel WFM. Nu översätts de till CW, NFM, AM, USB och LSB.

- **Flaggor från en referens-Kiwi.** Flaggorna hämtas nu också från Kiwin hos aprs.fi i Kuopio, oavsett vilken mottagare du lyssnar på (SK3JR, dongeln eller en annan Kiwi). Den Kiwin har KiwiSDR:s fullständiga flagglista med 866 flaggor. Kontrollerad mot 69 stationer med kända frekvenser (VOLMET, MWARA, NAVTEX, DWD, Northwood, tidssignaler och fyrar): 66 av 69 har en flagga. SK3JR har 11 av 69, och Shannon VOLMET 5505 kHz saknas där. Listan hämtas i bakgrunden en gång i veckan och sparas lokalt. Hämtningen tar ungefär 4 s och stör inte ljudet, eftersom det är en annan Kiwi. Referensens flaggor visas där varken du eller mottagarens Kiwi redan har en flagga. De kan döljas eller ändras som andra flaggor. Brytaren **Flaggor från referens-Kiwi** finns i sektionen Flaggor. Analysen använder flaggorna som stöd. Flaggans namn, typ och anteckning läses, så DWD:s väder-RTTY (DDH7, typ FSK) ger stöd för FSK.

- **Rättat: analysen följde fel bandbredd efter klick på en flagga.** Analysen tog demodulatorns kanal rakt av, och med ett kvarglömt WFM-läge blev den 180 kHz bred på kortvåg. Nu gäller följande:
  - Ett klick på en flagga riktar analysen mot själva signalen. För USB/LSB används flaggans talkanal (0,3–3 kHz från den inställda frekvensen). För andra lägen mäts bandbredden.
  - Om flaggans läge är okänt och frekvensen ligger under 30 MHz blir läget USB eller LSB efter bandet, aldrig WFM eller NFM.
  - Följ inställd och `--analysera` använder aldrig en kanal bredare än 15 kHz under 30 MHz. I stället mäter analysen signalen.
  - Provat live mot Heby: Shannon VOLMET 5505 kHz från WFM-läge ger USB och SSB 100 %, med grunden "mätning; flygband; flagga VOLMET".

- **Signaltyp-rutan säger aldrig emot rubriken.** Rutan till höger låste en typ ur signaldatabasen utan att jämföra den med analysens signalfamilj. Den kunde till exempel visa "SSB-tal 100 %" medan rubriken sa "Osäker – kanske 4-FSK". Nu låses bara en typ vars modulation passar den familj bevismotorn visar. Om motorn byter till en familj som inte passar släpper rutan typen och söker igen. Din egen klassning och typer du har valt i rullistan ändras inte. Provat live på Shannon VOLMET 5505 kHz (svag): rutan stod på "söker" medan analysen visade brus och låste SSB-tal först när analysen visade SSB.

- **Svaga signaler känns igen snabbare.** Ett svagt SSB-tal visades förut som "Ingen signal" i upp till 20 s. Det hade två orsaker:
  - **Bandbreddsmätningen** jämförde varje frekvenspunkt med bruset. Tal som bara ligger 1–3 dB över bruset syntes inte, och kanalen blev 400 Hz. Nu finns en reservmätning på ett spektrum som slätats ut över 300 Hz. Den används bara när signalen vid klicket är svag (under 6 dB) och bandet under 4 dB, så starka signaler och grannsignaler påverkas inte. Ett klick mellan två FSK-toner går fortfarande till FSK-parsökningen.
  - **Bevismotorn** tolkade lågt SNR som brus. Nu mäter den om nivån i kanalen varierar mer än brus gör (närvaro), och den spridningen mäts ur själva datan. Taldetektorn känner igen tal även i bruset, via stavelserytmen. Envelopen räknas inte som bevis under 6 dB SNR, eftersom den då är brusets.
  - Mätt med 6 dB extra brus på facitklippen: rätt typ i 49 % av stegen (förut 25 %), brus i 30 % (förut 52 %). SSB-tal blir oftast rätt, till exempel VOLMET 98 %. Rena klipp: oförändrat (98 %).
  - Live på Shannon VOLMET 5505 kHz (svag, Heby): SSB inom 1,8 s efter klick på flaggan och sedan stabilt. Förut växlade analysen mellan brus och SSB i 20 s.

- **Flaggans kanal kontrolleras med en mätning.** När du klickar på en USB/LSB-flagga börjar analysen med flaggans talkanal (0,3–3 kHz från den inställda frekvensen) och mäter samtidigt bandbredden. Mätningen tar över bara när den tydligt visar något annat:
  - en smal signal nära klicket (inom 300 Hz): under 25 % av kanalen (CW, PSK31, FT8), ett FSK-par under 45 % (RTTY), eller en smal signal med jämn nivå som fyller hela kanalen
  - en mycket bredare signal: mer än 2,5 gånger kanalen (AM)

  Talets formanter och talpauser mäts ofta smalare än talet, och då gäller kanalen. Det gäller också när inget syns. Rubriken visar "(flaggans kanal)" eller "(uppmätt)". Provat med alla facitklipp som flaggklick: bredden blir rätt i 31 av 34 (ett vanligt klick ger 26 av 34), och typen blir rätt i 97 %. Live på Shannon VOLMET (Heby) gäller flaggans kanal, och analysen visar SSB 100 %.

- **Fler svaga signaltyper känns igen.** Mätt med 6 dB extra brus på facitklippen visas rätt typ i 78 % av stegen (förra steget 49 %, från början 25 %), och "brus" bara i 7 % (52 % från början). Rena klipp: 99 %. Ändringar:
  - **AM med sidbanden i bruset:** en bärvåg vars nivå varierar räknas som modulerad, inte som omodulerad. Bandbredden används inte mot AM när bärvågen bär nästan all effekt, eftersom mätningen då bara ser bärvågen.
  - **Svaga modem (STANAG 4285):** den utslätade bandbreddsmätningen har hysteres och tappar inte bandet när det fädar. Modemramen (106,7 ms) söks över 3 s, och samma period i minst två steg ger starkt stöd för ett modem med en bärvåg. Att bandbreddsmätningen hittat signalen räknas som bevis mot brus.
  - **Svag RTTY:** FSK-parsökningen godtar svagare toner om de turas om i tiden, som FSK gör. Två CW-stationer bredvid varandra gör inte det och tas inte längre för ett FSK-par.
  - **Svag CW:** en smal topp precis under tröskeln letas i alla upplösningar, med något lägre krav nära klicket.
  - **Osäker gissning i stället för "Analyserar …":** efter ungefär 4 s visas analysens bästa gissning, märkt som osäker, även när ingen typ leder tydligt.
  - Kvar: CW 7083, RWM och PSK31 vid +6 dB ligger 1–4 dB över bruset och visas som brus. Två svaga SSB-klipp gissas som FSK (märkt osäkert).

- **MSK känns igen** (VLF-stationer som DHO38, NAA och JXN). När signalen kvadreras ger MSK två jämnstarka spektrallinjer symmetriskt kring mitten, vid ±halva symboltakten (±100 Hz vid 200 Bd). Analysen tog dem förut för BPSK/QPSK. Linjeavståndet ska vara lika med den separat uppmätta symboltakten, och det skiljer MSK från vanlig FSK (RTTY 50 Bd ger 900 Hz). Upptäckt med det nya facitbiblioteket.
- **Större facitbibliotek** (pågår, nytt verktyg `bibliotek.py`). Kandidater tas ur KiwiSDR:ns stationsflaggor och spelas in 20 s åt gången. Klipp utan synlig signal sållas bort. Resten granskas mot bilder av spektrum och vattenfall innan de blir facit, och osäkra klipp läggs i `osakra/`. Hittills har 10 nya klipp tillkommit (VLF-MSK, DCF77, DWD RTTY, PBB FSK, RWM).

- **Bandbredd på VLF/LF och för modem.** På VLF lutar brusgolvet cirka 10 dB över fönstret, och då slogs grannstationer ihop (JXN 16,4 kHz: 8,2 kHz). Under 500 kHz tas lutningen nu bort innan bandbredden mäts, och JXN känns igen som MSK. Ett starkt, platt och fyllt band på 1,5–4 kHz (ett modem) gäller före en smal bit inuti det (STANAG FUG 10186 kHz: 680 Hz → 3,4 kHz). Smala signaler under 200 Hz, som CW och bärvågar, behåller sin bredd. Facitbiblioteket med 55 riktiga klipp: familj rätt i 52, bandbredd rätt i 42 av 53, rätt typ visas i 91 % av stegen.

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
- **Flygradar ansluter automatiskt** till adsb.lol när ingen RTL-SDR-dongel finns. Pågående mottagning i Signalspanaren stoppas inte.

Analysförbättringarna nedan ingick i 2.10.0 på grenen analys-fortsattning. De ingick inte i taggen v2.10.0.

- **Bandbredd som 99 % av effekten.** Analysen mäter bredden som 99 % av effekten inom signalen, så att starka signalers kjolar inte räknas med. Det görs inte när en bärvåg bär effekten.
- **FT8/FT4 och smal BPSK.** FT8/FT4-detektorn sorterar frekvenserna i ordning, kräver sammanhängande toner och rättar falsklarmet FT4 på FSK 50/200. Signaler som bärs av en ensam bärvåg, till exempel CW och PSK31, avvisas. Smal BPSK känns igen på en undertryckt bärvåg och en symboltaktslinje.
- **PSK31/PSK63-avkodare.** Avkodar med ITU/G3PLX-varicode direkt i signalanalysen och i fliken Avkodat.
- **2D-ramraster** bredvid konstellationen när ramperioden hittas, som i SigDigger. Synkordet och pilotsymbolerna syns som lodräta ränder.
- **ADC-varning.** Programmet varnar när den 8-bitars ADC:n i RTL-SDR eller rtl_tcp klipper (värdena 0 och 255).
- **AM-rundradio.** Bandbredden tillåter formantsvackor upp till 900 Hz kring bärvågen, så att tal inte kapas vid 2,5–3 kHz.
- **RTTY och svag STANAG 4285.** Baudot-RTTY med 1,5 stoppbit halverar inte längre symboltiden. Svag STANAG 4285 känns igen på breda platåer och en ramperiod på minst 20 ms, även vid lägre SNR.
- **Snabbkalibrering av PPM.** Närmaste jämna kHz från analysen eller inställningen, samt RWM 4,996, 9,996 och 14,996 MHz.

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
