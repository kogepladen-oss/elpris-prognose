# Læring

Hver ændring af modellen noteres her med dato, hypotese, walk-forward-tal før og efter, og beslutning.
Én ændring ad gangen. Påstå aldrig en forbedring uden tal fra samme periode.

| Dato | Ændring | MAE før → efter (dage før 2 / 4 / 7) | Beslutning |
| --- | --- | --- | --- |
| 2026-11-13 | Første version `hgb-q-v1` på rigtige data, 30 dage | 31,1 / 40,4 / 70,4 (naiv 67) | Basislinje. Systematisk for lav (bias −18 til −59 øre), bånd for smalt (dækning 61–70 %) |
| 2026-11-13 | Forudsig afvigelse fra sidste uges DK1-niveau (alle horisonter), 6 ugers walk-forward | 28,1 → 31,0 / 35,9 → 35,9 / 67,0 → 60,0 | Bedre fra 5 dage før, dårligere kortere frem |
| 2026-11-13 | Fuld biaskorrektion (14 dage) i stedet for halv (21 dage) | 31,0 → 34,7 / 35,9 → 42,2 / 60,0 → 53,8 | Forkastet: overkorrigerer, prisniveauet skifter for hurtigt |
| 2026-11-13 | `hgb-q-v2`: afvigelse fra niveau fra 5 dage før, ellers niveau. Båndfaktor op til 4 | 28,1 / 35,9 / 60,0 (bedste af de to pr. horisont) | Indført. Følg op efter 30 dages live-kørsel |

| 2026-11-13 | `hgb-q-v2.1`: biaskorrektion og båndkalibrering bruger kun den aktuelle models egne fejl; fejlregnskabet foretrækker aktuel version | – | Rettelse: v2-backtesten blev korrigeret med v1's fejl |

Første observationer fra rigtige data:
- Efterårets prisniveau ligger over det meste af træningsperioden. Træmodeller kan ikke ekstrapolere, så de trækkes ned mod gennemsnittet.
- Usikkerheden er større end modellens rå kvantiler: båndfaktoren rammer loftet. Overvej ensemble-spredning som feature.

## Ugeanalyser

Den daglige kørsel skriver `reports/uge-ÅÅÅÅ-UU.md` hver mandag med ugens 5 dyreste fejl.
Brug `scripts/explain_day.py` til at afgøre, om fejlen kom fra vejrprognosen eller modellen.
