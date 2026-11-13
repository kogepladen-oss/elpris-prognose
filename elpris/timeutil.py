"""Tid: alt gemmes i UTC, men tariffer og kalender følger dansk tid."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

TZ = "Europe/Copenhagen"


def to_local(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(TZ)


def local_date(idx: pd.DatetimeIndex) -> pd.Series:
    """Dansk kalenderdato for hver UTC-time (som Timestamp uden tid, tz-naiv)."""
    loc = to_local(idx)
    return pd.Series(loc.tz_localize(None).normalize(), index=idx)


def local_day_bounds_utc(d: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Start og slut (eksklusiv) i UTC for en dansk kalenderdag. Håndterer 23/25-timersdøgn."""
    start = pd.Timestamp(d).tz_localize(TZ)
    end = pd.Timestamp(d + timedelta(days=1)).tz_localize(TZ)
    return start.tz_convert("UTC"), end.tz_convert("UTC")


def hours_in_local_day(d: date) -> int:
    s, e = local_day_bounds_utc(d)
    return int((e - s) / pd.Timedelta(hours=1))


def easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def dk_holidays(year: int) -> set[date]:
    e = easter(year)
    rel = [-3, -2, 0, 1, 39, 49, 50]  # skærtorsdag .. 2. pinsedag (store bededag afskaffet 2024)
    days = {e + timedelta(days=r) for r in rel}
    days |= {date(year, 1, 1), date(year, 6, 5), date(year, 12, 24), date(year, 12, 25),
             date(year, 12, 26), date(year, 12, 31)}
    return days


def is_holiday(dates: pd.Series) -> pd.Series:
    years = sorted({d.year for d in dates})
    hol = set().union(*(dk_holidays(y) for y in years)) if years else set()
    return dates.map(lambda d: d.date() in hol)
