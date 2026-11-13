# Elpris-prognose DK1

En prognose for elprisen i DK1 7 dage frem, så opladning af elbilen kan planlægges længere frem end
Nord Pools day-ahead-priser. Den kører hver dag på GitHub Actions og viser en graf på GitHub Pages:

- faktisk pris 7 dage tilbage og den nyeste prognose 7 dage frem, med usikkerhedsbånd (P10–P90)
- bagud også prognosen, som den så ud 2, 3, 5 eller 7 dage før, så fejlene kan ses
- tre prisniveauer: kun spot, spot + N1-nettarif eller alt inklusive (Energinet, elafgift, tillæg og moms)
- fejl pr. prognosealder for de seneste 7 og 30 dage, sammenlignet med en naiv metode
- en ladeplan: lad nu, eller vent på et billigere vindue

## Sådan kommer det i gang (ca. 10 minutter)

1. Opret et **offentligt** repo på GitHub, fx `elpris-prognose`, og upload hele denne mappe til det.
   Repoet indeholder kun offentlige markedsdata. GitHub Pages er gratis for offentlige repoer.
2. Gå til **Settings → Actions → General → Workflow permissions**, og vælg **Read and write permissions**.
3. Gå til **Actions**, vælg **Daglig elprisprognose**, og klik **Run workflow**. Første kørsel henter
   2 års historik og laver en backtest over 10 uger. Det tager typisk 10–25 minutter.
4. Gå til **Settings → Pages**. Vælg **Deploy from a branch**, branch `main`, mappe `/docs`, og gem.
   Efter et par minutter ligger siden på `https://<dit-brugernavn>.github.io/elpris-prognose/`.
5. Ret `config.toml`: elhandlerens tillæg (`spot_addon_ore_per_kwh`), ladeeffekt, typisk kWh-behov,
   og om du selv betaler nettarif og afgifter (`pays_grid_tariffs_and_tax`).

Herefter kører den selv hver dag kl. ca. 13:25 (dansk vintertid) med genforsøg kl. 14:40 og 16:10.
GitHub sender en mail, hvis en kørsel fejler.

## Sådan virker det

| Del | Fil | Hvad den gør |
| --- | --- | --- |
| Data | `elpris/sources.py`, `elpris/store.py` | Spotpriser for DK1, DK2, DE, NO2, SE3 og SE4 fra Energi Data Service. N1-tariffer fra DatahubPricelist. Vejr fra Open-Meteo for 8 punkter i DK, DE, SE og NO |
| Features | `elpris/features.py` | Vindkraft (effektkurve), sol, temperatur, kalender og prisniveauer i nabolandene. Kun det, der var kendt, da prognosen blev lavet |
| Model | `elpris/model.py` | Én kvantilmodel (gradient boosting) pr. prognosealder, P10/P50/P90 |
| Læring | `elpris/model.py` | Genoptræning hver dag med vægt på nyere data, biaskorrektion fra de seneste 21 dages fejl og kalibrering af båndet, så det rammer ca. 80 % |
| Fejlregnskab | `elpris/evaluate.py` | MAE, dækning, rangkorrelation og ekstra ladeudgift pr. prognosealder. Ugerapport i `reports/` |
| Side | `docs/index.html`, `docs/forecast.json` | Statisk side uden server |

Vigtige principper:

- **Ingen fremtidsviden.** Til træning bruges historiske vejrprognoser med den alder, de ville have haft
  (Open-Meteo Previous Runs), aldrig det faktiske vejr.
- **Prognoser fryses.** Hver dags prognose gemmes i `data/forecasts.parquet` og overskrives aldrig.
  Backtest-prognoser fra opstarten er mærket `backtest`, rigtige daglige prognoser `live`.
- **Dagens vejrprognose gemmes** i `data/weather_snapshots/`, så den kan bruges igen senere.
- **Fejl måles på den rene spotpris.** Tariffer er kendte på forhånd og ville få prognosen til at se bedre ud.

## Begrænsninger

- Prognosen bliver mindre præcis jo længere frem den rækker. Dag 6–7 er en grov retning.
- Gaspris, CO₂-pris, vandmagasiner, udfald og kabelkapacitet er ikke med endnu. Prisniveauet i NO2 og SE3
  bruges som indirekte mål for vandsituationen. ENTSO-E kan tilføjes med en gratis token (se `notes/STATE.md`).
- Energinets tariffer og elafgiften ligger i `config.toml` og skal rettes én gang om året.
- Elafgiften for 2028 er ikke fastlagt; tallet i `config.toml` skal tjekkes.

## Lokalt

```
pip install -r requirements.txt
python -m pytest -q tests                      # tests uden netadgang
python scripts/e2e_synthetic.py --days 2       # hele pipelinen på syntetiske data
python -m elpris.run_daily                     # rigtig kørsel (kræver internet)
python scripts/explain_day.py 2026-11-20 --days-before 3   # vejr- eller modelfejl?
```

Datakilder: Energi Data Service (Energinet, CC BY 4.0) og Open-Meteo (CC BY 4.0, gratis til ikke-kommerciel brug).
