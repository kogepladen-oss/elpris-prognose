# Status

Læs denne fil og `notes/LEARNINGS.md` i starten af hver session. Opdater dem, når noget ændres.

## Nu

- Modelversion: `hgb-q-v1` (kvantil gradient boosting pr. prognosealder, asinh-transformeret pris).
- Bygget efter projektinstruktionernes trin 1–6 i en første version. Testet på syntetiske data; endnu ikke kørt mod de rigtige API'er.
- Første rigtige kørsel skal tjekkes: at Open-Meteo Previous Runs leverer `wind_speed_100m_previous_dayN`
  (ellers falder koden tilbage til 10 m-vind × 1,35 og skriver det i loggen og i `meta.json`).

## Kendte forenklinger

- Dagens anker K = sidste dag med komplette DK1-priser. Issue-tid i træning = kl. 13 dansk tid dagen før K.
- Vejrprognosens alder afrundes opad til hele dage og loftes ved 7. For de sidste timer af horisont 6
  er prognosen i træningen derfor op til ca. 12 timer friskere, end den ville have været.
- Nyere data vægtes ved at gentage rækker (1–3 gange), fordi `sample_weight` gør kvantiltræningen meget langsommere.
- Backtest genoptræner én gang om ugen og bruger modellen for alle 7 issue-dage i ugen.
- Energinet og elafgift ligger i `config.toml` pr. år. N1 hentes fra DatahubPricelist (`CD` og `CD R`), med reservetabel.
- DatahubPricelist-filter på Energinets GLN gav ikke de forventede rækker ved test, så Energinet hentes ikke automatisk.

## Næste skridt (i prioriteret rækkefølge)

1. Kør første gang på GitHub og tjek loggen, `docs/forecast.json` og siden.
2. Efter 30 dages live-kørsel: sammenlign live- og backtest-fejl. Er live markant dårligere, er der lækage i backtesten.
3. ENTSO-E (kræver token i GitHub Secrets som `ENTSOE_TOKEN`): vandmagasiner (A72), udfald og kapacitet.
4. Carnot som ekstern sammenligning (gratis nøgle).
5. Ensemble-spredning fra Open-Meteo som feature for usikkerhed (skal gemmes dagligt fra start).
