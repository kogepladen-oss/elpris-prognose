"""Features uden fremtidsviden.

Begreber:
  D  = dansk kalenderdato for måltimen t
  h  = horisont i dage = D - K
  K  = sidste dag med kendte priser, da prognosen blev lavet
  issue-tid = kl. 13:00 dansk tid dagen før K (morgendagens priser er da lige frigivet)
  L  = vejrprognosens alder i hele dage = ceil((t - issue)/24 t), højst 7

Træning til horisont h bruger kun priser til og med K og vejrprognoser med alder L,
dvs. præcis det, der var kendt på issue-tidspunktet.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import MAX_LEAD
from .timeutil import TZ, is_holiday, local_date, to_local

WIND_POINTS_DK = ["dk1_land", "dk_nordsoe"]
WIND_POINTS_DE = ["de_nordvest", "de_nordoest"]
ISSUE_HOUR_LOCAL = 13


def power_curve(ws: np.ndarray) -> np.ndarray:
    """Normaliseret vindmølleeffekt (0-1) ud fra vind i navhøjde. Cut-in 3, nominel 12, cut-out 25 m/s."""
    ws = np.asarray(ws, dtype=float)
    p = np.clip((ws ** 3 - 27) / (12 ** 3 - 27), 0, 1)
    return np.where(ws > 25, 0.0, p)


def issue_time_for_anchor(k_dates: pd.Series) -> pd.Series:
    """Issue-tid (UTC) for en ankerdag K: kl. 13 dansk tid dagen før."""
    loc = (k_dates - pd.Timedelta(days=1) + pd.Timedelta(hours=ISSUE_HOUR_LOCAL))
    return pd.Series(pd.DatetimeIndex(loc).tz_localize(TZ, nonexistent="shift_forward",
                                                          ambiguous=False).tz_convert("UTC"), index=k_dates.index)


def lead_days(t: pd.Series, issue: pd.Series) -> np.ndarray:
    hours = (t - issue) / pd.Timedelta(hours=1)
    return np.clip(np.ceil(hours / 24.0), 1, MAX_LEAD).astype(int)


# ---------------------------------------------------------------- vejr

def wide_weather(long: pd.DataFrame, by_lead: bool) -> pd.DataFrame:
    keys = ["time_utc", "lead"] if by_lead else ["time_utc"]
    w = long.pivot_table(index=keys, columns="point",
                         values=["wind_speed_100m", "shortwave_radiation", "temperature_2m"])
    w.columns = [f"{v}__{p}" for v, p in w.columns]
    return w


def _weather_features(w: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=w.index)
    for c in w.columns:
        var, pt = c.split("__")
        if var == "wind_speed_100m":
            out[f"pw_{pt}"] = power_curve(w[c].to_numpy())
        elif var == "shortwave_radiation":
            out[f"rad_{pt}"] = w[c]
        else:
            out[f"temp_{pt}"] = w[c]
    dk = [f"pw_{p}" for p in WIND_POINTS_DK if f"pw_{p}" in out]
    de = [f"pw_{p}" for p in WIND_POINTS_DE if f"pw_{p}" in out]
    out["wind_dk"] = out[dk].mean(axis=1) if dk else np.nan
    out["wind_de"] = out[de].mean(axis=1) if de else np.nan
    rads = [c for c in out if c.startswith("rad_")]
    out["rad_mean"] = out[rads].mean(axis=1) if rads else np.nan
    temps = [c for c in out if c.startswith("temp_")]
    out["temp_mean"] = out[temps].mean(axis=1) if temps else np.nan
    return out


# ---------------------------------------------------------------- priser

def daily_stats(prices: pd.DataFrame) -> pd.DataFrame:
    d = local_date(prices.index)
    g = prices.groupby(d.values)
    st = pd.DataFrame({
        "dk1_mean": g["DK1"].mean(), "dk1_std": g["DK1"].std(), "dk1_min": g["DK1"].min(),
        "dk1_n": g["DK1"].count(), "de_mean": g["DE"].mean(), "dk2_mean": g["DK2"].mean(),
        "no2_mean": g["NO2"].mean(), "se3_mean": g["SE3"].mean(), "se4_mean": g["SE4"].mean(),
    })
    st.index = pd.DatetimeIndex(st.index)
    full = pd.date_range(st.index.min(), st.index.max(), freq="D")
    st = st.reindex(full)
    roll = st[["dk1_mean", "de_mean", "no2_mean", "se3_mean"]].rolling(7, min_periods=5).mean()
    for c in roll:
        st[c.replace("_mean", "_7d")] = roll[c]
    st["spread_dk1_de_7d"] = st["dk1_7d"] - st["de_7d"]
    return st


def complete_days(prices: pd.DataFrame) -> pd.Series:
    st = daily_stats(prices)
    return st["dk1_n"].fillna(0) >= 22


def last_complete_day(prices: pd.DataFrame) -> pd.Timestamp:
    ok = complete_days(prices)
    return ok[ok].index.max()


# ---------------------------------------------------------------- samlet

ANCHOR_COLS = ["dk1_mean", "dk1_std", "dk1_min", "de_mean", "dk2_mean", "se4_mean",
               "dk1_7d", "de_7d", "no2_7d", "se3_7d", "spread_dk1_de_7d"]


def build(targets: pd.DatetimeIndex, h: int | np.ndarray, prices: pd.DataFrame,
          weather: pd.DataFrame, by_lead: bool, anchor: pd.Timestamp | None = None,
          issue: pd.Timestamp | None = None) -> pd.DataFrame:
    """Featurematrix for måltimer.

    targets: UTC-timer.  h: horisont (skalar eller pr. række), bruges når anchor er None.
    weather: wide_weather(...) med (time_utc, lead) hvis by_lead, ellers kun time_utc (live-prognose).
    anchor/issue: sæt ved live-prognose; ellers udledes de af h.
    """
    t = pd.Series(targets, index=range(len(targets)))
    D = pd.Series(local_date(targets).values, index=t.index)
    if anchor is None:
        hh = pd.Series(np.broadcast_to(h, len(t)), index=t.index)
        K = D - pd.to_timedelta(hh, unit="D")
        iss = issue_time_for_anchor(K)
    else:
        K = pd.Series(anchor, index=t.index)
        hh = (D - K).dt.days
        iss = pd.Series(issue, index=t.index)
    L = lead_days(t, iss)

    st = daily_stats(prices)
    anc = st.reindex(pd.DatetimeIndex(K))[ANCHOR_COLS].reset_index(drop=True)
    anc["dk1_n_K"] = st["dk1_n"].reindex(pd.DatetimeIndex(K)).to_numpy()

    loc = to_local(targets)
    dk1 = prices["DK1"]
    same_hour_K = t - pd.to_timedelta(hh, unit="D")  # samme klokkeslæt på dag K (UTC-tilnærmelse)
    lag7 = t - pd.Timedelta(days=7)

    f = pd.DataFrame({
        "h": hh.to_numpy(), "lead": L,
        "hour": loc.hour, "dow": loc.dayofweek, "weekend": (loc.dayofweek >= 5).astype(int),
        "holiday": is_holiday(D).astype(int).to_numpy(),
        "doy_sin": np.sin(2 * np.pi * loc.dayofyear / 365.25),
        "doy_cos": np.cos(2 * np.pi * loc.dayofyear / 365.25),
        "dk1_same_hour_K": dk1.reindex(same_hour_K).to_numpy(),
        "dk1_lag7": dk1.reindex(lag7).to_numpy(),
    })
    # Sikkerhed: lag7 må kun bruges, hvis dens dato er <= K
    lag_date = pd.Series(local_date(pd.DatetimeIndex(lag7)).values)
    f.loc[(lag_date > K).to_numpy(), "dk1_lag7"] = np.nan
    f = pd.concat([f, anc], axis=1)

    if by_lead:
        keys = pd.MultiIndex.from_arrays([targets, L])
        wx = weather.reindex(keys)
    else:
        wx = weather.reindex(targets)
    wf = _weather_features(wx).reset_index(drop=True)
    # dagsgennemsnit af vind på måldagen (samme prognosealder)
    for c in ("wind_dk", "wind_de", "rad_mean"):
        wf[f"{c}_day"] = wf[c].groupby(D.to_numpy()).transform("mean").to_numpy()
    f = pd.concat([f, wf], axis=1)
    f["target_utc"] = targets
    f["anchor"] = K.to_numpy()
    f["target_date"] = D.to_numpy()
    return f


def feature_columns(f: pd.DataFrame) -> list[str]:
    drop = {"target_utc", "anchor", "target_date", "y", "dk1_n_K"}
    return [c for c in f.columns if c not in drop]
