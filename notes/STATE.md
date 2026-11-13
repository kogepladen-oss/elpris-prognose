# Status

Læs denne fil og `notes/LEARNINGS.md` i starten af hver session. Opdater dem, når noget ændres.

## Nu

- Modelversion: `hgb-q-v2` (kvantil gradient boosting pr. prognosealder; fra 5 dage før relativt til sidste uges niveau).
- Kører dagligt på GitHub Actions i `kogepladen-oss/elpris-prognose` siden 13. nov. 2026.
- Priser hentes fra Energy-Charts, fordi Energi Data Service giver 429 fra GitHub Actions (delt IP).
  Første hentning tog ca. 10 min pga. Energy-Charts' kaldgrænse.
- Open-Meteo Previous Runs leverer 100 m-vind. Hvert 2.–3. kald holdes tilbage i ca. 2 min; alle punkter hentes derfor i ét kald.
- N1-tariffer fra DatahubPricelist kan også ramme 429; så bruges den gemte prisliste eller reservetabellen.

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
