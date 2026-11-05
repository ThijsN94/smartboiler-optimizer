# SmartBoiler Optimizer

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=thijsn94&repository=smartboiler-optimizer&category=integration)

Home Assistant-integratie die bepaalt **wanneer je warmwaterboiler het goedkoopst opgewarmd kan worden** voor een vaste deadline. Die keuze houdt rekening met de dynamische stroomprijs, de COP van je warmtepomp (uit het weer of gemeten), de zonneproductie en de buitentemperatuur.

> **English:** see the [English section](#english) below.

---

## Inhoud
- [Wat doet het?](#wat-doet-het)
- [Vereisten](#vereisten)
- [Installatie via HACS](#installatie-via-hacs)
- [Instellen](#instellen)
- [Entiteiten](#entiteiten)
- [Hoe werkt het?](#hoe-werkt-het)
- [Problemen oplossen](#problemen-oplossen)
- [Beperkingen](#beperkingen)
- [Licentie](#licentie)

---

## Wat doet het?

- Berekent het **startmoment** om de boiler op te warmen, zo dat de totale kosten voor de deadline het laagst zijn. Standaard is de deadline 07:00; die stel je zelf in.
- Herberekent dat elke **5 minuten**, zodat het moment meebeweegt met nieuwe prijzen, weer en zon.
- Kan de boiler **automatisch aan- en uitzetten** op het berekende moment. Dit staat standaard **uit**.
- **Leert** de opwarmsnelheid en de COP uit de eigen opwarmruns, en gebruikt die zodra er genoeg runs zijn.
- Kan een **backup-schakelaar** voor bijverwarming aansturen, op basis van de buitentemperatuur, alleen tijdens een run.

## Vereisten

| Wat | Waarom | Verplicht? |
|---|---|---|
| Home Assistant 2026.10 of nieuwer | Minimale versie | ja |
| Prijssensor met een `forecast`-attribuut | De dynamische prijs (zie [Beperkingen](#beperkingen)) | ja |
| Weersentiteit (`weather`) | Buitentemperatuur en COP-voorspelling | aanbevolen |
| Zonneforecast-sensor(en) met een `watts`-attribuut, zoals Open-Meteo Solar | Zonneproductie in de berekening | nee |
| Boiler als `water_heater`-entiteit | Aansturen van de modus en de boilertemperatuur | nee, anders een schakelaar |
| Boilertemperatuursensoren (boven en onder) | Opwarmsnelheid en einde van de run | aanbevolen |

## Installatie via HACS

1. Ga in Home Assistant naar **HACS > Integraties**.
2. Klik rechtsboven op de drie puntjes en kies **Aangepaste repositories**.
3. Vul in als URL: `https://github.com/thijsn94/smartboiler-optimizer` en kies als categorie **Integratie**.
4. Zoek **SmartBoiler Optimizer** en kies **Downloaden**.
5. **Herstart Home Assistant.**
6. Ga naar **Instellingen > Apparaten en diensten > Integratie toevoegen** en kies **SmartBoiler Optimizer**.

**Handmatig installeren:** kopieer de map `custom_components/smartboiler_optimizer` naar de map `custom_components` van je Home Assistant-configuratie, en herstart.

## Instellen

De instellingen zijn in secties verdeeld. Alles is later te wijzigen via **Instellingen > Apparaten en diensten > SmartBoiler Optimizer > Configureren**.

**Bronnen**
- *Dynamische prijs*: de prijssensor. Werkt met Zonneplan (kwartierprijzen) en met sensoren met een `forecast`-attribuut (uurprijzen in €/kWh).
- *Zonnestroom forecast*: één of meer zonneforecast-sensoren.
- *Weer*: de weersentiteit voor de COP-voorspelling.
- *Buitentemperatuur sensor*: een buitensensor; wordt alleen gebruikt als er geen voorspelling is.

**Boiler**
- *Boiler (water_heater)*: de boiler. Elke `water_heater` kan worden gekozen.
- *Boilertemperatuur boven/onder*: de twee temperatuursensoren van de boiler.
- *Boilerstatus*: een sensor die de status van de boiler weergeeft (bijvoorbeeld `dhw_status`).
- *Energie boiler (kWh)*: de energiemeter van de boiler. Nodig om de opwarmsnelheid en de COP te meten.
- *Boiler doeltemperatuur* en *Boiler inhoud*: voor de berekening van de opwarmtijd.
- *Modus bij aan* en *Modus bij uit*: de operatiemodi die de boiler krijgt bij het startmoment en daarna. De keuzelijst toont alleen de modi die de gekozen boiler meldt; kent de boiler ze niet, dan staan alle modi erin. **De boiler accepteert alleen de modi die hij zelf kent.**
- *Schakelaar (alleen zonder water_heater)*: als er geen `water_heater` is, zet de integratie deze schakelaar aan bij het startmoment (aan = verwarmen) en uit aan het eind (uit = niet verwarmen). Met een `water_heater` wordt dit veld niet gebruikt.
- *Statussen waarin de boiler verwarmt*: komma-gescheiden statusteksten van de boilerstatus-sensor, bijvoorbeeld `ProducingHeat, RequestingHeat`. Staat er niets in, dan gelden die twee.

**Planning**
- *Boiler klaar om (uur)*: de deadline. Standaard 7 (07:00).
- *Vermogen tijdens boiler opwarmen (W)*: het elektrisch vermogen van de boiler tijdens het opwarmen.
- *Basisverbruik huis (W)*: het verbruik van het huis zonder boiler. Hiermee wordt het zonneoverschot bepaald.
- *Boiler automatisch inschakelen op het berekende moment*: als dit aanstaat, zet de integratie de boiler zelf aan en uit. Standaard uit.

**Backup bijverwarming**
- *Backup-schakelaar*: de schakelaar voor de bijverwarming.
- *Temperatuur drempel (°C)*: de buitentemperatuur-grens.
- *Richting van de backup-schakelaar*: **Aan > Uit** of **Uit > Aan**.
  - **Aan > Uit**: boven de drempel aan, onder de drempel uit, en aan het eind van de run uit.
  - **Uit > Aan**: precies andersom.
  - De drempel geldt alleen tijdens een run. Het eind van de run schakelt alleen als de integratie zelf tijdens die run de schakelaar heeft gewijzigd. Buiten een run laat de integratie de schakelaar met rust.

**Leren en COP**
- *Gemeten COP gebruiken in plaats van de COP-tabel*: als dit aanstaat en er zijn genoeg runs, wordt de COP uit de eigen metingen gebruikt.

**COP Tabel**
- Eén regel per punt: `[buitentemperatuur in °C, COP]`, bijvoorbeeld `[5, 2.35]`.
- Tussen twee punten rekent de integratie rechtlijnig door. Bij 6 °C, tussen 5 °C (COP 2,35) en 7,5 °C (COP 2,60), wordt de COP 2,45.
- Buiten het bereik van de tabel blijft de COP gelijk aan het dichtstbijzijnde punt.
- Minimaal twee punten, en elke temperatuur maar één keer. De tabel wordt bij opslaan gesorteerd.
- De tabel wordt alleen gebruikt als *Gemeten COP gebruiken* uit staat of er nog geen gemeten COP is.

**Schakelaars voor de startberekening** (bovenaan de instellingen, standaard aan)
- *Dynamische prijs gebruiken*: uit = de prijs telt niet mee; het startmoment wordt bepaald door zon en/of COP.
- *Weer (COP) gebruiken*: uit = de COP wordt vast op 2,5 gezet in plaats van uit het weer berekend. Het opwarmtempo blijft het weer gebruiken.
- *Zonoverschot gebruiken*: uit = er wordt geen zonoverschot meegerekend; het startmoment wordt bepaald door prijs en/of COP.

**Onderhoud: opwarmruns wissen**
- Wist alle opgeslagen runs na een bevestiging. De geleerde opwarmsnelheid blijft staan.

## Entiteiten

Alle entiteiten horen bij het apparaat **SmartBoiler Optimizer**.

| Entiteit | Wat het toont |
|---|---|
| **Boiler opwarmen om** | Het startmoment voor het opwarmen, als tijdstip. |
| **Boiler opwarmen volgend blok** | Het startmoment voor het volgende blok. Blijft `unknown` tot de prijzen het hele venster tot de volgende deadline dekken. |
| **Boiler opwarmduur** | De berekende opwarmduur in minuten, met als attributen de opwarmsnelheid, de buitentemperatuurfactor en de geleerde tabellen. |
| **Huidige stroomprijs** | De huidige prijs in €/kWh. |
| **Opwarmverwachting** | De kosten per kWh warmte van het huidige kwartier. Het attribuut `verwachting` bevat per kwartier de prijs (`p`), de zon (`z`), de temperatuur (`c`), de kosten (`k`) en of het kwartier in het opwarmblok valt (`b`). `huidige_stroomprijs` en `deadline` staan ook als attribuut. |
| **Opwarmblok prijs / zon / COP / buitentemperatuur** | De waarden van het gekozen opwarmblok. |
| **COP boiler** / **Gemeten COP boiler** | De COP uit de tabel of uit de eigen metingen. |
| **Opwarmruns verzameld** | Het aantal opgeslagen runs, bijvoorbeeld `3/50`. |
| **Status dynamische prijs / weer / zonneforecast** | `Beschikbaar`, `Fout` (met reden) of `Niet ingesteld`. |
| **Herbereken boilertijd** (knop) | Wist het opgeslagen plan en berekent het startmoment opnieuw. |

## Hoe werkt het?

**Startmoment.** Voor elk kwartier tot de deadline berekent de integratie de kosten van 1 kWh warmte: de prijs, verminderd met het zonaandeel, gedeeld door de COP. Daarna kiest hij het blok van de benodigde duur met de laagste gemiddelde kosten. Een nieuw startmoment wordt pas gekozen als het minstens 5% goedkoper is dan het huidige, zodat het moment niet heen en weer springt.

**Opwarmduur.** De duur hangt af van de boilertemperatuur, de geleerde opwarmsnelheid per temperatuurband en de buitentemperatuur (als factor). Zolang er nog geen runs zijn, wordt een standaardsnelheid gebruikt.

**Leren.** Een run begint wanneer de energiemeter van de boiler stijgt en eindigt wanneer hij stilvalt. Uit de run worden de opwarmsnelheid per temperatuurband en de gemeten COP bepaald. Tappen van warm water wordt eruit gehaald. Een herstart van Home Assistant midden in een run verliest de run niet.

**Aansturing (optioneel).** Bij het startmoment zet de integratie de modus `Modus bij aan`; daarna `Modus bij uit`. Het einde van de run wordt bepaald door de boilerstatus of door de geschatte eindtijd. Is er geen `water_heater`, dan wordt de schakelaar gebruikt.

## Problemen oplossen

- **Status "Fout" bij dynamische prijs:** de prijssensor levert geen voorspelling tot de deadline. Controleer of de sensor een `forecast`-attribuut heeft met de juiste vorm (zie [Beperkingen](#beperkingen)).
- **Status "Fout" bij weer:** de weersentiteit levert geen voorspelling. Controleer de entiteit; de integratie gebruikt dan de buitensensor of 7 °C.
- **Boiler opwarmen volgend blok blijft `unknown`:** de prijzen dekken nog niet het hele venster tot de volgende deadline. Dat is normaal tot de prijzen er zijn.
- **Modi-lijst toont alles:** de boiler meldt zijn modi niet, of is niet bereikbaar. Dat is ook normaal.
- **Instellingen tonen een fout:** controleer de logboeken (Instellingen > Systeem > Logboeken). Meld het met de tekst van de fout.
- **Na het wijzigen van de boiler** zie je de nieuwe modi-lijst pas na opslaan en het opnieuw openen van de instellingen.

## Beperkingen

- **Prijsformaten:** ondersteund zijn Zonneplan (kwartierprijzen, `start_date` + `price_tax_included`) en sensoren met een `forecast` van `datetime` + `value` in €/kWh (uurprijzen). Andere integraties, zoals Nord Pool, zijn nog niet getest.
- **Weer:** de integratie rekent met de voorspelling van de weersentiteit; zonder voorspelling is de status "Fout".
- **Modi en statussen** zijn van de boiler. Een boiler accepteert alleen de modi die hij kent; de standaardstatussen komen uit de Remeha-integratie.
- **Mengfase** (de warmte die bij het mengen van het water wegvalt) wordt niet gecorrigeerd.

## Licentie

MIT. Zie [LICENSE](LICENSE).

---

# English

**SmartBoiler Optimizer** is a Home Assistant integration that determines **when your hot-water boiler can be heated most cheaply** before a fixed deadline. It takes the dynamic electricity price, the COP of your heat pump (from the weather or measured), solar production and the outdoor temperature into account.

## Contents
- [What does it do?](#what-does-it-do-en)
- [Requirements](#requirements-en)
- [Installation via HACS](#installation-via-hacs-en)
- [Configuration](#configuration-en)
- [Entities](#entities-en)
- [How does it work?](#how-does-it-work-en)
- [Troubleshooting](#troubleshooting-en)
- [Limitations](#limitations-en)
- [License](#license-en)

## What does it do? {#what-does-it-do-en}

- Calculates the **start moment** for heating the boiler, so that the total cost before the deadline is lowest. The default deadline is 07:00; you set your own.
- Recalculates that **every 5 minutes**, so the moment follows new prices, weather and sun.
- Can **turn the boiler on and off automatically** at the calculated moment. This is **off** by default.
- **Learns** the heat-up rate and the COP from its own heat-up runs, and uses them once there are enough runs.
- Can drive a **backup switch** for auxiliary heating, based on the outdoor temperature, only during a run.

## Requirements {#requirements-en}

| What | Why | Required? |
|---|---|---|
| Home Assistant 2026.10 or newer | Minimum version | yes |
| Price sensor with a `forecast` attribute | The dynamic price (see [Limitations](#limitations-en)) | yes |
| Weather entity (`weather`) | Outdoor temperature and COP forecast | recommended |
| Solar forecast sensor(s) with a `watts` attribute, such as Open-Meteo Solar | Solar production in the calculation | no |
| Boiler as a `water_heater` entity | Controlling the mode and the boiler temperature | no; otherwise a switch |
| Boiler temperature sensors (top and bottom) | Heat-up rate and end of the run | recommended |

## Installation via HACS {#installation-via-hacs-en}

1. In Home Assistant, go to **HACS > Integrations**.
2. Click the three dots at the top right and choose **Custom repositories**.
3. Enter the URL `https://github.com/thijsn94/smartboiler-optimizer` and choose the category **Integration**.
4. Search for **SmartBoiler Optimizer** and choose **Download**.
5. **Restart Home Assistant.**
6. Go to **Settings > Devices & services > Add integration** and choose **SmartBoiler Optimizer**.

**Manual installation:** copy the folder `custom_components/smartboiler_optimizer` into the `custom_components` folder of your Home Assistant configuration, and restart.

## Configuration {#configuration-en}

The settings are split into sections. You can change everything later via **Settings > Devices & services > SmartBoiler Optimizer > Configure**.

**Sources**: the price sensor, the solar forecast sensor(s), the weather entity and an outdoor sensor (only used when there is no forecast).

**Boiler**: the boiler (any `water_heater`), its top and bottom temperature sensors, the boiler status sensor, the energy meter, the target temperature and capacity, the modes *Mode when on* and *Mode when off* (the list only shows the modes the chosen boiler reports; if the boiler does not know them, all modes are shown), a switch (only used without a `water_heater`) and the *Statuses in which the boiler heats* (comma-separated; default `ProducingHeat, RequestingHeat`). **The boiler only accepts the modes it knows itself.**

**Planning**: the deadline hour (default 7), the power while heating (W), the household base load (W), and whether the boiler is turned on automatically (default off).

**Backup heating**: the backup switch, the temperature threshold (°C), and the direction:
- **On > Off**: above the threshold on, below off, and off at the end of the run.
- **Off > On**: exactly the opposite.
- The threshold only applies during a run. The end of the run only switches if the integration changed the switch during that run.

**Learning and COP**: *Use measured COP instead of the COP table*.

**COP table**: one point per line, `[outdoor temperature in °C, COP]`, for example `[5, 2.35]`. Between two points the integration interpolates linearly. Outside the range the nearest point applies. At least two points, each temperature once; sorted when you save. Used only when measured COP is off or not yet available.

**Calculation switches** (at the top of the settings, default on): *Use dynamic price*, *Use weather (COP)* (off = COP fixed at 2.5) and *Use solar surplus*.

**Maintenance: clear heat-up runs**: removes all saved runs after confirmation; the learned rate is kept.

## Entities {#entities-en}

All entities belong to the device **SmartBoiler Optimizer**.

| Entity | What it shows |
|---|---|
| **Boiler opwarmen om** (Boiler start) | The start moment for heating, as a time. |
| **Boiler opwarmen volgend blok** (next block) | The start moment of the next block. Stays `unknown` until the prices cover the whole window to the next deadline. |
| **Boiler opwarmduur** (heat-up duration) | The calculated heat-up duration in minutes, with the rate and the learned tables as attributes. |
| **Huidige stroomprijs** (current price) | The current price in €/kWh. |
| **Opwarmverwachting** (heat-up forecast) | The cost per kWh of heat for the current quarter hour. The attribute `verwachting` lists per quarter hour the price (`p`), solar (`z`), temperature (`c`), cost (`k`) and whether it falls in the heating block (`b`). |
| **Opwarmblok prijs / zon / COP / buitentemperatuur** | Values of the chosen heating block. |
| **COP boiler** / **Gemeten COP boiler** | The COP from the table or from the own measurements. |
| **Opwarmruns verzameld** | The number of saved runs, for example `3/50`. |
| **Status dynamische prijs / weer / zonneforecast** | `Beschikbaar` (available), `Fout` (error, with reason) or `Niet ingesteld` (not configured). |
| **Herbereken boilertijd** (button) | Clears the saved plan and recalculates the start moment. |

## How does it work? {#how-does-it-work-en}

**Start moment.** For each quarter hour until the deadline, the integration calculates the cost of 1 kWh of heat: the price, reduced by the solar share, divided by the COP. It then picks the block of the required length with the lowest average cost. A new start moment is only chosen if it is at least 5% cheaper than the current one, so the moment does not jump around.

**Heat-up duration.** It depends on the boiler temperature, the learned heat-up rate per temperature band, and the outdoor temperature as a factor. Until there are runs, a default rate is used.

**Learning.** A run starts when the boiler's energy meter rises and ends when it stops. From each run the heat-up rate per temperature band and the measured COP are derived. Hot water drawn off is removed from the calculation. A Home Assistant restart in the middle of a run does not lose the run.

**Control (optional).** At the start moment the integration sets *Mode when on*; afterwards *Mode when off*. The end of the run is detected from the boiler status or the estimated end time. Without a `water_heater`, the switch is used.

## Troubleshooting {#troubleshooting-en}

- **Status "Fout" for the price:** the price sensor gives no forecast up to the deadline. Check that the sensor has a `forecast` attribute in one of the supported shapes (see [Limitations](#limitations-en)).
- **Status "Fout" for the weather:** the weather entity gives no forecast. The integration then uses the outdoor sensor or 7 °C.
- **Next heat-up block stays `unknown`:** the prices do not yet cover the whole window to the next deadline. This is normal until the prices are available.
- **Mode list shows everything:** the boiler does not report its modes, or is unreachable. This is also normal.
- **Settings show an error:** check the logs (Settings > System > Logs) and report the error text.
- **After changing the boiler,** you see the new mode list only after saving and reopening the settings.

## Limitations {#limitations-en}

- **Price formats:** supported are Zonneplan (quarter-hourly prices, `start_date` + `price_tax_included`) and sensors with a `forecast` of `datetime` + `value` in €/kWh (hourly prices). Other integrations, such as Nord Pool, are not tested yet.
- **Weather:** the integration uses the forecast of the weather entity; without a forecast the status is "Fout".
- **Modes and statuses** come from the boiler. A boiler only accepts the modes it knows; the default statuses are those of the Remeha integration.
- **Mixing phase** (heat lost when water is mixed) is not corrected.

## License {#license-en}

MIT. See [LICENSE](LICENSE).
