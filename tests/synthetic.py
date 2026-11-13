"""Syntetiske data med realistisk struktur, så hele pipelinen kan testes uden netadgang."""
from __future__ import annotations

import numpy as np
import pandas as pd

from elpris.config import AREAS, MAX_LEAD
from elpris.features import power_curve
from elpris.timeutil import to_local

RNG_SEED = 7


class World:
    def __init__(self, cfg, start="2024-10-01", end="2026-12-01"):
        self.cfg = cfg
        rng = np.random.default_rng(RNG_SEED)
        self.idx = pd.date_range(start, end, freq="h", tz="UTC")
        n = len(self.idx)
        self.points = cfg["weather"]["points"]
        base = np.zeros(n)
        for i in range(1, n):  # fælles vejrsystem, AR(1)
            base[i] = 0.985 * base[i - 1] + rng.normal(0, 0.35)
        loc = to_local(self.idx)
        doy = loc.dayofyear.to_numpy()
        hour = loc.hour.to_numpy()
        self.truth = {}
        for j, p in enumerate(self.points):
            own = np.convolve(rng.normal(0, 1, n), np.ones(12) / 12, "same")
            wind = np.clip(8 + 1.5 * np.cos(2 * np.pi * doy / 365) + 2.2 * base + 1.2 * own, 0, 30)
            sun = np.clip(np.sin(np.pi * (hour - 5) / 15), 0, None) * (450 + 350 * np.cos(2 * np.pi * (doy - 172) / 365))
            sun = sun * np.clip(1 - 0.15 * base, 0.2, 1.3)
            temp = 9 - 8 * np.cos(2 * np.pi * (doy - 20) / 365) + 3 * np.sin(np.pi * (hour - 9) / 12) + own
            self.truth[p.name] = pd.DataFrame({"wind_speed_100m": wind, "shortwave_radiation": sun,
                                               "temperature_2m": temp}, index=self.idx)
        wdk = power_curve(self.truth["dk1_land"]["wind_speed_100m"].to_numpy())
        wde = power_curve(self.truth["de_nordvest"]["wind_speed_100m"].to_numpy())
        prof = np.array([0.6, 0.55, 0.5, 0.5, 0.55, 0.7, 0.95, 1.15, 1.2, 1.1, 1.0, 0.95,
                         0.9, 0.9, 0.95, 1.0, 1.15, 1.35, 1.45, 1.35, 1.15, 1.0, 0.85, 0.7])[hour]
        weekend = (loc.dayofweek.to_numpy() >= 5)
        level = 70 + 25 * np.cos(2 * np.pi * (doy - 15) / 365) + np.cumsum(rng.normal(0, 0.05, n))
        de = level * prof * (1.2 - 0.7 * wde) - 0.02 * sun - 10 * weekend + rng.normal(0, 6, n)
        dk1 = 0.55 * de + 0.45 * level * prof * (1.1 - 0.9 * wdk) + rng.normal(0, 5, n)
        self.prices = pd.DataFrame({
            "DK1": dk1, "DE": de, "DK2": dk1 * 1.05 + rng.normal(0, 3, n),
            "NO2": level * 0.8 + rng.normal(0, 3, n), "SE3": level * 0.7 * prof + rng.normal(0, 4, n),
            "SE4": dk1 * 0.95 + rng.normal(0, 4, n)}, index=self.idx)[AREAS]
        self.now = None

    # ------------------------------------------------ falske kilder
    def known_until(self):
        loc = self.now.tz_convert("Europe/Copenhagen")
        last_day = loc.normalize() + pd.Timedelta(days=2 if loc.hour >= 13 else 1)
        return last_day.tz_convert("UTC")

    def fetch_prices(self, start, end):
        p = self.prices[(self.prices.index >= start) & (self.prices.index < min(end, self.known_until()))]
        p = p.copy()
        p.index.name = "time_utc"
        return p

    def fetch_grid_tariffs(self, gln, codes):
        rows = []
        for vf, vt, w in [("2025-10-01", "2026-04-01", [0.088, 0.264, 0.792, 0.264]),
                          ("2026-04-01", "2026-10-01", [0.088, 0.128, 0.344, 0.128]),
                          ("2026-10-01", None, [0.088, 0.264, 0.792, 0.264])]:
            pr = [w[0]] * 6 + [w[1]] * 11 + [w[2]] * 4 + [w[3]] * 3
            rows.append({"ChargeTypeCode": "CD", "Note": "Nettarif C", "ValidFrom": vf, "ValidTo": vt,
                         **{f"Price{i + 1}": pr[i] for i in range(24)}})
        return pd.DataFrame(rows)

    def _noisy(self, name, lead, idx):
        rng = np.random.default_rng(abs(hash((name, lead))) % 2**32)
        t = self.truth[name].reindex(idx)
        s = 0.35 * lead
        return pd.DataFrame({
            "wind_speed_100m": np.clip(t["wind_speed_100m"] + rng.normal(0, 0.8 * s, len(t)), 0, None),
            "shortwave_radiation": np.clip(t["shortwave_radiation"] * (1 + rng.normal(0, 0.08 * s, len(t))), 0, None),
            "temperature_2m": t["temperature_2m"] + rng.normal(0, 0.5 * s, len(t))}, index=idx)

    def fetch_weather_previous_runs(self, points, start, end):
        idx = self.idx[(self.idx >= pd.Timestamp(start, tz="UTC")) &
                       (self.idx < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))]
        out = []
        for p in points:
            for lead in range(1, MAX_LEAD + 1):
                ok = idx <= self.now + pd.Timedelta(days=lead)
                d = self._noisy(p.name, lead, idx[ok]).reset_index(names="time_utc")
                d["point"], d["lead"] = p.name, lead
                out.append(d)
        return pd.concat(out, ignore_index=True), "wind_speed_100m"

    def fetch_weather_forecast(self, points, days=9):
        idx = self.idx[(self.idx >= self.now - pd.Timedelta(days=1)) & (self.idx < self.now + pd.Timedelta(days=days))]
        out = []
        for p in points:
            lead = np.clip(np.ceil((idx - self.now) / pd.Timedelta(days=1)), 1, 7)
            parts = [self._noisy(p.name, int(l), idx[lead == l]) for l in np.unique(lead)]
            d = pd.concat(parts).sort_index().reset_index(names="time_utc")
            d["point"] = p.name
            out.append(d)
        return pd.concat(out, ignore_index=True)


def install(world, monkeypatch_setattr):
    from elpris import sources
    for name in ("fetch_prices", "fetch_grid_tariffs", "fetch_weather_previous_runs", "fetch_weather_forecast"):
        monkeypatch_setattr(sources, name, getattr(world, name))
    monkeypatch_setattr(sources, "now_utc", lambda: world.now)
