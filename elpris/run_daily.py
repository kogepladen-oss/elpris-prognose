"""Daglig kørsel: hent data, evaluer gamle prognoser, lav ny prognose, byg siden.

    python -m elpris.run_daily            # normal kørsel
    python -m elpris.run_daily --force    # kør selvom dagens prognose findes
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

import pandas as pd

from . import config, evaluate, features as F, model, site, sources, store, tariffs
from .config import REPORTS

log = logging.getLogger("elpris")


def _load_meta() -> dict:
    return json.loads(store.META.read_text()) if store.META.exists() else {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = config.load()
    now = sources.now_utc()
    train_start = pd.Timestamp(cfg["model"]["train_start"], tz="Europe/Copenhagen").tz_convert("UTC")
    meta = _load_meta()
    warnings: list[str] = []

    # 1. priser
    try:
        prices = store.update_prices(train_start, now)
    except sources.SourceError as e:
        log.error("Priser: %s", e)
        prices = store.read(store.PRICES)
        warnings.append("Nye spotpriser kunne ikke hentes; grafen viser seneste gemte.")
        if prices is None:
            return 1
    K = F.last_complete_day(prices)
    log.info("Sidste dag med kendte priser: %s", K.date())

    if not args.force and meta.get("anchor_date") == str(K.date()) \
            and meta.get("issue_date") == str(now.tz_convert("Europe/Copenhagen").date()):
        log.info("Dagens prognose findes allerede med samme priser. Intet at gøre.")
        return 0

    # 2. tariffer
    try:
        pl = sources.fetch_grid_tariffs(cfg["pricing"]["grid_gln"], cfg["pricing"]["grid_charge_codes"])
        store.write(pl, store.PRICELIST)
    except sources.SourceError as e:
        log.warning("Tariffer: %s", e)
        pl = store.read(store.PRICELIST)

    # 3. vejr: historiske prognoser + dagens prognose (gemmes som snapshot)
    try:
        wx_long = store.update_weather_history(cfg["weather"]["points"], train_start, now)
        meta["wind_source"] = wx_long.attrs.get("wind_src", meta.get("wind_source", "wind_speed_100m"))
    except sources.SourceError as e:
        log.error("Vejrhistorik: %s", e)
        wx_long = store.read_weather_history()
        warnings.append("Historiske vejrprognoser kunne ikke opdateres.")
        if wx_long is None:
            return 1
    try:
        snap = sources.fetch_weather_forecast(cfg["weather"]["points"])
        store.save_snapshot(snap, now)
    except sources.SourceError as e:
        log.error("Vejrprognose: %s", e)
        snaps = sorted(store.SNAPSHOTS.glob("*.parquet"))
        snap = pd.read_parquet(snaps[-1]) if snaps else None
        warnings.append("Dagens vejrprognose kunne ikke hentes; bruger seneste gemte." if snaps
                        else "Ingen vejrprognose tilgængelig; ingen ny prognose i dag.")
    wx_hist = F.wide_weather(wx_long, by_lead=True)
    history_ready = wx_long.attrs.get("complete", True)
    if not history_ready:
        warnings.append("Første opsætning: historiske vejrprognoser hentes stadig. "
                        "Prognosen kommer ved næste kørsel.")

    # 4. backtest af nye uger + ny live-prognose
    existing = store.read(store.FORECASTS)
    live = pd.DataFrame()
    if history_ready:
        bt = model.backtest(prices, wx_hist, existing, cfg, K)
        if not bt.empty:
            existing = store.append_forecasts(bt)
    if history_ready and snap is not None:
        live = model.live_forecast(prices, wx_hist, F.wide_weather(snap, by_lead=False), existing, cfg, now)
        if not live.empty:
            existing = store.append_forecasts(live)

    # 5. fejlregnskab og ugerapport
    res = evaluate.resolved(existing, prices, model.MODEL_VERSION)
    m7 = evaluate.metrics(res, K, 7, cfg)
    m30 = evaluate.metrics(res, K, 30, cfg)
    if now.tz_convert("Europe/Copenhagen").dayofweek == 0 or not any(REPORTS.glob("*.md")):
        REPORTS.mkdir(exist_ok=True)
        (REPORTS / f"uge-{K:%G-%V}.md").write_text(evaluate.weekly_report(res, K, m30), encoding="utf-8")

    # 6. side
    comp_idx = pd.date_range((now - pd.Timedelta(days=8)).floor("h"), (now + pd.Timedelta(days=8)).ceil("h"), freq="h")
    comp, tw = tariffs.components(comp_idx, cfg, pl)
    warnings += tw
    if not history_ready:  # så den næste planlagte kørsel i dag fortsætter
        meta.pop("anchor_date", None)
    meta.update({"anchor_date": str(K.date()) if history_ready else None, "issue_date": str(now.tz_convert("Europe/Copenhagen").date()),
                 "model_version": model.MODEL_VERSION, "generated_at": now.isoformat()})
    site.write(site.build_json(now, prices, comp, live, res, m7, m30, warnings, cfg, meta))
    store.META.write_text(json.dumps(meta, indent=2))
    log.info("Færdig. %d advarsler.", len(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
