"""Hentning af priser, tariffer og vejr. Alle tider i UTC, priser i øre/kWh uden moms."""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from .config import AREAS, MAX_LEAD, WEATHER_VARS, Point

log = logging.getLogger(__name__)

EDS = "https://api.energidataservice.dk/dataset"
OM_FORECAST = "https://api.open-meteo.com/v1/forecast"
OM_PREVIOUS = "https://previous-runs-api.open-meteo.com/v1/forecast"
EUR_DKK = 7.46  # fastkurs-fallback, hvis DKK-kolonnen mangler
NEW_DATASET_FROM = pd.Timestamp("2025-10-01", tz="Europe/Copenhagen").tz_convert("UTC")
UA = {"User-Agent": "elpris-prognose/1.0 (personal forecast; github actions)"}


class SourceError(RuntimeError):
    pass


def http_get(url: str, params: dict, tries: int = 6, timeout: int = 90,
             max_wait: float = 300) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r
            if 400 <= r.status_code < 500 and r.status_code != 429:
                raise SourceError(f"{url} gav {r.status_code}: {r.text[:300]}")
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code == 429:  # grænse for antal kald ramt: vent det, serveren beder om
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.replace(".", "").isdigit() else 30 * (i + 1)
                if wait > max_wait:
                    raise SourceError(f"{url} gav 429, ventetid {wait:.0f} s: {r.text[:200]}")
                wait = min(max(wait, 5), 300)
                log.warning("429 fra %s (Retry-After=%s, body=%s). Venter %.0f s.",
                            url, ra, r.text[:200].replace("\n", " "), wait)
                time.sleep(wait)
                continue
        except requests.RequestException as e:  # netværksfejl: prøv igen
            last = str(e)
        time.sleep(2 ** i * 3)
    raise SourceError(f"{url} fejlede efter {tries} forsøg: {last}")


# ---------------------------------------------------------------- priser

def _eds_records(dataset: str, start: datetime, end: datetime, flt: dict) -> list[dict]:
    params = {
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": end.strftime("%Y-%m-%dT%H:%M"),
        "filter": json.dumps(flt),
        "limit": 0,
    }
    recs = http_get(f"{EDS}/{dataset}", params, max_wait=30).json().get("records", [])
    time.sleep(2)  # skån Energi Data Service ved første store hentning
    return recs


ENERGY_CHARTS = "https://api.energy-charts.info/price"
EC_ZONES = {"DK1": "DK1", "DK2": "DK2", "DE": "DE-LU", "NO2": "NO2", "SE3": "SE3", "SE4": "SE4"}


def fetch_prices(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Spotpriser fra Energi Data Service; ved fejl eller kaldgrænse fra Energy-Charts (Fraunhofer ISE).

    Energi Data Service begrænser kald pr. IP, og GitHub Actions deler IP-adresser med mange andre,
    så reserven bruges ofte dér. Begge kilder leverer de samme Nord Pool-priser.
    """
    try:
        return fetch_prices_eds(start, end)
    except SourceError as e:
        log.warning("Energi Data Service fejlede (%s). Henter priser fra Energy-Charts.", str(e)[:200])
        return fetch_prices_energy_charts(start, end)


def fetch_prices_energy_charts(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    d0 = (start - pd.Timedelta(days=1)).tz_convert("Europe/Copenhagen").date()
    d_end = (end + pd.Timedelta(days=1)).tz_convert("Europe/Copenhagen").date()
    for area, bzn in EC_ZONES.items():
        cur = d0
        while cur <= d_end:
            nxt = min(cur + timedelta(days=89), d_end)
            js = http_get(ENERGY_CHARTS, {"bzn": bzn, "start": cur.isoformat(), "end": nxt.isoformat()},
                          max_wait=120).json()
            time.sleep(1)
            for ts, p in zip(js.get("unix_seconds") or [], js.get("price") or []):
                if p is not None:
                    rows.append((ts, area, p * EUR_DKK))
            cur = nxt + timedelta(days=1)
    if not rows:
        raise SourceError("Energy-Charts gav ingen priser")
    df = pd.DataFrame(rows, columns=["ts", "area", "dkk_mwh"])
    df["t"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.floor("h")
    df = df.groupby(["t", "area"])["dkk_mwh"].mean().unstack("area").reindex(columns=AREAS) / 10.0
    df.index.name = "time_utc"
    return df.loc[df.index >= start - pd.Timedelta(days=1)].sort_index()


def fetch_prices_eds(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Timepriser for AREAS i øre/kWh uden moms. 15-min priser midles til timer."""
    rows = []
    # EDS filtrerer start/end i dansk tid: hent en dag ekstra i begge ender og fjern dubletter.
    cur = (start - pd.Timedelta(days=1)).tz_convert("Europe/Copenhagen").tz_localize(None).normalize()
    stop = (end + pd.Timedelta(days=1)).tz_convert("Europe/Copenhagen").tz_localize(None)
    while cur < stop:
        nxt = min(cur + pd.Timedelta(days=31), stop)
        cur_utc = cur.tz_localize("Europe/Copenhagen").tz_convert("UTC")
        if cur_utc < NEW_DATASET_FROM:
            for r in _eds_records("Elspotprices", cur, nxt, {"PriceArea": AREAS}):
                p = r.get("SpotPriceDKK")
                if p is None and r.get("SpotPriceEUR") is not None:
                    p = r["SpotPriceEUR"] * EUR_DKK
                rows.append((r["HourUTC"], r["PriceArea"], p))
        if nxt.tz_localize("Europe/Copenhagen").tz_convert("UTC") > NEW_DATASET_FROM:
            for r in _eds_records("DayAheadPrices", cur, nxt, {"PriceArea": AREAS}):
                p = r.get("DayAheadPriceDKK")
                if p is None and r.get("DayAheadPriceEUR") is not None:
                    p = r["DayAheadPriceEUR"] * EUR_DKK
                rows.append((r["TimeUTC"], r["PriceArea"], p))
        cur = nxt
    if not rows:
        return pd.DataFrame(columns=AREAS, dtype=float)
    df = pd.DataFrame(rows, columns=["t", "area", "dkk_mwh"]).dropna()
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.floor("h")
    df = df.drop_duplicates().groupby(["t", "area"])["dkk_mwh"].mean().unstack("area")
    df = df.reindex(columns=AREAS) / 10.0  # DKK/MWh -> øre/kWh
    df.index.name = "time_utc"
    return df.loc[(df.index >= start - pd.Timedelta(days=1))].sort_index()


# ---------------------------------------------------------------- tariffer

def fetch_grid_tariffs(gln: str, codes: list[str]) -> pd.DataFrame:
    params = {"filter": json.dumps({"GLN_Number": [gln], "ChargeTypeCode": codes}), "limit": 0}
    recs = http_get(f"{EDS}/DatahubPricelist", params, max_wait=60).json().get("records", [])
    if not recs:
        raise SourceError("DatahubPricelist gav ingen tariffer for netselskabet")
    df = pd.DataFrame(recs)
    keep = ["ChargeTypeCode", "Note", "ValidFrom", "ValidTo"] + [f"Price{i}" for i in range(1, 25)]
    return df[[c for c in keep if c in df.columns]]


# ---------------------------------------------------------------- vejr

def _om_frame(js: dict | list, points: list[Point], cols: list[str]) -> pd.DataFrame:
    items = js if isinstance(js, list) else [js]
    frames = []
    for p, it in zip(points, items):
        h = it["hourly"]
        f = pd.DataFrame({c: h.get(c) for c in cols})
        f["time_utc"] = pd.to_datetime(h["time"], utc=True)
        f["point"] = p.name
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


def _locs(points: list[Point]) -> dict:
    return {"latitude": ",".join(str(p.lat) for p in points),
            "longitude": ",".join(str(p.lon) for p in points)}


def fetch_weather_forecast(points: list[Point], days: int = 9) -> pd.DataFrame:
    params = {**_locs(points), "hourly": ",".join(WEATHER_VARS), "forecast_days": days,
              "past_days": 1, "timezone": "GMT", "wind_speed_unit": "ms"}
    js = http_get(OM_FORECAST, params).json()
    return _om_frame(js, points, WEATHER_VARS)


def fetch_weather_previous_runs(points: list[Point], start: date, end: date) -> tuple[pd.DataFrame, str]:
    """Historiske prognoser med fast horisont (1-7 dage). Returnerer langt format + vindkilde."""
    wind_src = "wind_speed_100m"
    out = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=90), end)
        for p in points:  # ét punkt pr. kald holder svarene små
            for attempt_var in (wind_src, "wind_speed_10m"):
                vars_ = [attempt_var, "shortwave_radiation", "temperature_2m"]
                cols = [f"{v}_previous_day{n}" for v in vars_ for n in range(1, MAX_LEAD + 1)]
                params = {"latitude": p.lat, "longitude": p.lon, "hourly": ",".join(cols),
                          "start_date": cur.isoformat(), "end_date": nxt.isoformat(),
                          "timezone": "GMT", "wind_speed_unit": "ms"}
                try:
                    js = http_get(OM_PREVIOUS, params).json()
                    time.sleep(2.5)  # hold os under Open-Meteos gratisgrænse pr. minut
                    break
                except SourceError as e:
                    if attempt_var == "wind_speed_10m":
                        raise
                    log.warning("100 m vind ikke tilgængelig i Previous Runs, bruger 10 m: %s", e)
                    wind_src = "wind_speed_10m"
            f = _om_frame(js, [p], cols)
            for n in range(1, MAX_LEAD + 1):
                part = pd.DataFrame({
                    "time_utc": f["time_utc"], "point": p.name, "lead": n,
                    "wind_speed_100m": f[f"{attempt_var}_previous_day{n}"].astype(float)
                    * (1.35 if attempt_var == "wind_speed_10m" else 1.0),
                    "shortwave_radiation": f[f"shortwave_radiation_previous_day{n}"].astype(float),
                    "temperature_2m": f[f"temperature_2m_previous_day{n}"].astype(float),
                })
                out.append(part)
        cur = nxt + timedelta(days=1)
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return df, wind_src


def now_utc() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def to_float(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.astype({c: np.float32 for c in df.select_dtypes("float").columns})
