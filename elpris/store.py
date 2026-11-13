"""Lokal datalagring i parquet. Rå data overskrives aldrig; nye rækker lægges til."""
from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd

from . import sources
from .config import DATA

log = logging.getLogger(__name__)

PRICES = DATA / "prices_hourly.parquet"
WEATHER_HIST = DATA / "weather_previous_runs"  # én fil pr. måned, så git-historikken holdes lille
SNAPSHOTS = DATA / "weather_snapshots"
FORECASTS = DATA / "forecasts.parquet"
PRICELIST = DATA / "grid_pricelist.parquet"
META = DATA / "meta.json"


def read(path, **kw) -> pd.DataFrame | None:
    return pd.read_parquet(path, **kw) if path.exists() else None


def write(df: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, compression="zstd")


def update_prices(train_start: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
    old = read(PRICES)
    start = train_start if old is None or old.empty else old.index.max() - pd.Timedelta(days=3)
    new = sources.fetch_prices(start, now + pd.Timedelta(days=2))
    df = new if old is None else pd.concat([old, new])
    df = df[~df.index.duplicated(keep="last")].sort_index().astype("float32")
    write(df, PRICES)
    return df


def read_weather_history() -> pd.DataFrame | None:
    files = sorted(WEATHER_HIST.glob("*.parquet")) if WEATHER_HIST.exists() else []
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True) if files else None


def _write_weather_months(df: pd.DataFrame, months: set[str]) -> None:
    WEATHER_HIST.mkdir(parents=True, exist_ok=True)
    key = df["time_utc"].dt.strftime("%Y-%m")
    for m in months:
        part = df[key == m].reset_index(drop=True)
        if len(part):
            part.to_parquet(WEATHER_HIST / f"{m}.parquet", compression="zstd")


def update_weather_history(points, train_start: pd.Timestamp, now: pd.Timestamp,
                           budget_s: float = 35 * 60) -> pd.DataFrame:
    """Opdaterer historiske vejrprognoser. Første gang tager det lang tid; stopper efter budget_s
    sekunder og fortsætter ved næste kørsel (df.attrs["complete"] er så False)."""
    import time as _time
    t0 = _time.monotonic()
    old = read_weather_history()
    names = {p.name for p in points}
    if old is not None and set(old["point"].unique()) != names:
        log.warning("Vejrpunkter er ændret i config; henter historik forfra.")
        old = None
    start = (train_start - pd.Timedelta(days=8)).date() if old is None else \
        (old["time_utc"].max() - pd.Timedelta(days=9)).date()
    end = (now + pd.Timedelta(days=7)).date()  # Previous Runs dækker også fremtiden for korte leads
    df, wind_src = old, "wind_speed_100m"
    complete = True
    cur = start
    # Hent i bidder på 90 dage og gem efter hver bid, så en afbrudt kørsel kan fortsætte næste gang.
    while cur <= end:
        nxt = min(cur + timedelta(days=89), end)
        log.info("Henter historiske vejrprognoser %s til %s", cur, nxt)
        new, wind_src = sources.fetch_weather_previous_runs(points, cur, nxt)
        if not new.empty:
            df = new if df is None else pd.concat([df, new])
            df = df.drop_duplicates(["time_utc", "point", "lead"], keep="last")
            df = df.sort_values(["time_utc", "point", "lead"]).reset_index(drop=True)
            for c in ("wind_speed_100m", "shortwave_radiation", "temperature_2m"):
                df[c] = df[c].astype("float32")
            _write_weather_months(df, set(new["time_utc"].dt.strftime("%Y-%m")))
        cur = nxt + timedelta(days=1)
        if cur <= end and _time.monotonic() - t0 > budget_s:
            log.warning("Tidsbudget brugt; vejrhistorikken hentes færdig i næste kørsel (nået til %s).", cur)
            complete = False
            break
    if df is not None:
        df.attrs["wind_src"] = wind_src
        df.attrs["complete"] = complete
    return df


def save_snapshot(fc: pd.DataFrame, issued: pd.Timestamp) -> None:
    write(fc, SNAPSHOTS / f"{issued:%Y-%m-%d}.parquet")


def load_snapshot(day) -> pd.DataFrame | None:
    return read(SNAPSHOTS / f"{day:%Y-%m-%d}.parquet")


def append_forecasts(new: pd.DataFrame) -> pd.DataFrame:
    """Frosne prognoser lægges til. En eksisterende prognose overskrives aldrig."""
    old = read(FORECASTS)
    if old is None or old.empty:
        df = new
    else:
        key = ["issue_date", "kind", "model_version", "anchor", "target_utc"]
        merged = new.merge(old[key], on=key, how="left", indicator=True)
        df = pd.concat([old, new[(merged["_merge"] == "left_only").to_numpy()]], ignore_index=True)
    df = df.sort_values(["issue_date", "kind", "target_utc"]).reset_index(drop=True)
    write(df, FORECASTS)
    return df


def recent_window(days: int, now: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    return now - timedelta(days=days), now
