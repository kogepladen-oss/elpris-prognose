# Læring

Hver ændring af modellen noteres her med dato, hypotese, walk-forward-tal før og efter, og beslutning.
Én ændring ad gangen. Påstå aldrig en forbedring uden tal fra samme periode.

| Dato | Ændring | MAE før → efter (dage før 2 / 4 / 7) | Beslutning |
| --- | --- | --- | --- |
| | Første version `hgb-q-v1` | – | Basislinje |

## Ugeanalyser

Den daglige kørsel skriver `reports/uge-ÅÅÅÅ-UU.md` hver mandag med ugens 5 dyreste fejl.
Brug `scripts/explain_day.py` til at afgøre, om fejlen kom fra vejrprognosen eller modellen.
