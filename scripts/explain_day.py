"""Var fejlen på en dag vejrprognosens skyld eller modellens?

Kører den samme model for en måldag to gange: med den vejrprognose, der faktisk var
kendt, og med den 1 dag gamle prognose (tæt på det faktiske vejr). Forsvinder det meste
af fejlen, var det vejrprognosen.

    python scripts/explain_day.py 2026-11-20 --days-before 3
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from elpris import config, features as F, model, store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--days-before", type=int, default=3)
    a = ap.parse_args()
    cfg = config.load()
    D = pd.Timestamp(a.date)
    h = a.days_before - 1
    K = D - pd.Timedelta(days=h)
    prices = store.read(store.PRICES)
    wide = F.wide_weather(store.read_weather_history(), by_lead=True)
    start = pd.Timestamp(cfg["model"]["train_start"], tz="UTC")
    mod = model.fit(model.training_frame(prices, wide, h, start, K), h, cfg)
    s, e = model._day_bounds(D)
    tgt = prices.index[(prices.index >= s) & (prices.index < e)]
    y = prices["DK1"].reindex(tgt).to_numpy()
    f_known = F.build(tgt, h, prices, wide, by_lead=True)
    near = wide.xs(1, level="lead")
    f_near = F.build(tgt, h, prices, near, by_lead=False, anchor=K,
                     issue=F.issue_time_for_anchor(pd.Series([K])).iloc[0])
    p_known = model.inv(mod.predict(f_known))[:, 1]
    p_near = model.inv(mod.predict(f_near))[:, 1]
    mae_k, mae_n = np.nanmean(np.abs(p_known - y)), np.nanmean(np.abs(p_near - y))
    print(f"{D:%Y-%m-%d}, prognose lavet {a.days_before} dage før")
    print(f"  MAE med kendt vejrprognose:    {mae_k:6.1f} øre/kWh")
    print(f"  MAE med næsten faktisk vejr:   {mae_n:6.1f} øre/kWh")
    share = 1 - mae_n / mae_k if mae_k > 0 else 0
    print(f"  Andel af fejlen fra vejret:    {share:6.0%}")
    if share < 0.3:
        print("  Fejlen skyldes mest modellen eller markedet (udfald, kabler, gas). Tjek ENTSO-E/UMM.")


if __name__ == "__main__":
    main()
