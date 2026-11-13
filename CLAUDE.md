# Til Claude

Dette repo er en daglig 7-dages prognose for elprisen i DK1 med N1-tariffer.
Projektinstruktionerne ligger i Claude-projektet; denne fil gentager det vigtigste.

- Læs `notes/STATE.md` og `notes/LEARNINGS.md` først, og opdater dem, når noget ændres.
- Den daglige drift kører uden Claude (`.github/workflows/daily.yml`). Claude retter kode og laver ugeanalysen.
- Kør `python -m pytest -q tests` og `python scripts/e2e_synthetic.py --days 2` før en ændring foreslås.
- Ingen fremtidsviden i træning. Frosne prognoser i `data/forecasts.parquet` må aldrig overskrives.
- En modelændring kræver ny `MODEL_VERSION` i `elpris/model.py` og walk-forward-tal før og efter.
- Svar på dansk; kode og identifiers på engelsk.
