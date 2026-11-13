import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from elpris import config, features as F, model, tariffs  # noqa: E402
from elpris.timeutil import hours_in_local_day, local_day_bounds_utc  # noqa: E402
from synthetic import World  # noqa: E402

CFG = config.load()


def test_dst_days_have_23_and_25_hours():
    assert hours_in_local_day(date(2026, 3, 29)) == 23
    assert hours_in_local_day(date(2026, 10, 25)) == 25
    assert hours_in_local_day(date(2026, 11, 13)) == 24


def test_tariff_bands_switch_at_6_17_21_local():
    s, e = local_day_bounds_utc(date(2026, 11, 13))
    idx = pd.date_range(s, e, freq="h", inclusive="left")
    g = tariffs.grid_fallback(idx, CFG).to_numpy()
    assert list(g[[5, 6, 16, 17, 20, 21]]) == [8.8, 26.4, 26.4, 79.2, 79.2, 26.4]


def test_tariffs_follow_local_time_on_dst_day():
    s, e = local_day_bounds_utc(date(2026, 10, 25))  # 25 timer, vintertarif
    idx = pd.date_range(s, e, freq="h", inclusive="left")
    g = tariffs.grid_fallback(idx, CFG)
    assert len(g) == 25
    assert (g == 79.2).sum() == 4  # spidslast 17-21 er stadig 4 timer


def test_pricelist_matches_fallback_and_summer_switch():
    w = World(CFG)
    pl = w.fetch_grid_tariffs("x", ["CD"])
    idx = pd.date_range("2026-03-31T00:00", "2026-04-02T00:00", freq="h", tz="UTC")
    a = tariffs.grid_from_pricelist(idx, pl)
    b = tariffs.grid_fallback(idx, CFG)
    assert np.allclose(a.to_numpy(), b.to_numpy())


def test_vat_applied_exactly_once():
    idx = pd.date_range("2026-11-13", periods=24, freq="h", tz="UTC")
    comp, _ = tariffs.components(idx, CFG, None)
    spot = pd.Series(100.0, index=idx)
    lv = tariffs.price_levels(spot, comp, 0.25)
    exp = (100 + comp["grid"] + comp["energinet"] + comp["elafgift"] + comp["addon"]) * 1.25
    assert np.allclose(lv["total"], exp)
    assert np.allclose(lv["spot"], 125.0)


def test_year_change_tariffs():
    idx = pd.DatetimeIndex(["2026-12-31T22:00", "2026-12-31T23:30"], tz="UTC").floor("h")
    comp, _ = tariffs.components(idx, CFG, None)
    # 22 UTC = 23 dansk tid 2026; 23 UTC = 00 dansk tid 2027
    assert comp["energinet"].tolist() == [11.5, 10.5]


@pytest.fixture(scope="module")
def world():
    w = World(CFG, start="2025-06-01", end="2026-01-15")
    w.now = pd.Timestamp("2025-12-10T12:30", tz="UTC")
    return w


def test_no_future_information_in_features(world):
    prices = world.fetch_prices(pd.Timestamp("2025-06-01", tz="UTC"), world.now + pd.Timedelta(days=3))
    wl, _ = world.fetch_weather_previous_runs(CFG["weather"]["points"], date(2025, 6, 1), date(2025, 12, 20))
    wide = F.wide_weather(wl, by_lead=True)
    h = 3
    tr = model.training_frame(prices, wide, h, pd.Timestamp("2025-07-01", tz="UTC"), pd.Timestamp("2025-11-30"))
    assert tr["target_date"].max() <= pd.Timestamp("2025-11-30")
    assert ((tr["target_date"] - tr["anchor"]).dt.days == h).all()
    # anker-statistik må kun komme fra dage <= anker: ændr fremtidige priser og tjek, at features er uændrede
    p2 = prices.copy()
    cut = pd.Timestamp("2025-11-20", tz="Europe/Copenhagen").tz_convert("UTC")
    p2.loc[p2.index >= cut, :] += 1000
    t = prices.index[(prices.index >= cut) & (prices.index < cut + pd.Timedelta(days=1))]
    f1 = F.build(t, h, prices, wide, by_lead=True)
    f2 = F.build(t, h, p2, wide, by_lead=True)
    cols = [c for c in F.feature_columns(f1)]
    pd.testing.assert_frame_equal(f1[cols], f2[cols])
    # vejrprognosens alder skal være mindst afstanden fra issue-tid til måltime
    iss = F.issue_time_for_anchor(pd.Series(f1["anchor"]))
    gap_days = (pd.Series(f1["target_utc"]) - iss).dt.total_seconds() / 86400
    assert (f1["lead"] >= np.minimum(np.ceil(gap_days), 7)).all()


def test_forecasts_never_overwritten(tmp_path, monkeypatch):
    from elpris import store
    monkeypatch.setattr(store, "FORECASTS", tmp_path / "f.parquet")
    base = pd.DataFrame({"issue_date": [pd.Timestamp("2026-11-01")], "kind": ["live"], "model_version": ["v"],
                         "anchor": [pd.Timestamp("2026-11-02")],
                         "target_utc": [pd.Timestamp("2026-11-04T10:00", tz="UTC")], "p50": [50.0]})
    store.append_forecasts(base)
    changed = base.assign(p50=99.0)
    out = store.append_forecasts(changed)
    assert len(out) == 1 and out["p50"].iloc[0] == 50.0


def test_price_fallback_to_energy_charts(monkeypatch):
    from elpris import sources

    class R:
        def __init__(self, js):
            self._js = js

        def json(self):
            return self._js

    def fake_get(url, params, **kw):
        if "energidataservice" in url:
            raise sources.SourceError("429")
        t0 = int(pd.Timestamp(params["start"], tz="Europe/Copenhagen").timestamp())
        return R({"unix_seconds": [t0 + 900 * i for i in range(8)], "price": [100.0] * 4 + [200.0] * 4})

    monkeypatch.setattr(sources, "http_get", fake_get)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    df = sources.fetch_prices(pd.Timestamp("2026-11-12", tz="UTC"), pd.Timestamp("2026-11-12T02:00", tz="UTC"))
    assert set(df.columns) == set(sources.AREAS)
    # 4 kvarterer à 200 EUR/MWh i timen 00 UTC -> 200 * 7,46 / 10 øre/kWh
    v = df.loc[pd.Timestamp("2026-11-11T00:00", tz="UTC"), "DK1"]
    assert abs(v - 200 * sources.EUR_DKK / 10) < 1e-6
